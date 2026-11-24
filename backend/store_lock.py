"""W2 — the single-writer lock: one NEXUS process per store.

A process that opens a store for writing first takes ``<kb.db>.lock``, the
file beside the store's ``kb.db``. One lock guards everything
``create_services`` opens (kb.db, archive.db, the vector store's folder) and
the two scripts that write the operator's store under ``--confirm``. A second
process is refused by name, and every caller turns the refusal into the
named exit ``EXIT_STORE_LOCKED``.

The rules, each measured in W2's establish (``run/specs/W2.md`` S1, item 4):

- **The OS lock is the truth.** POSIX: ``fcntl.flock(LOCK_EX | LOCK_NB)`` on
  the lock file. ``flock`` belongs to the open file, so the kernel frees it
  the moment its holder dies, whatever the file's text says. (``lockf`` is
  per process and dropped by closing any descriptor on the file: not used.)
  Windows: ``msvcrt.locking(LK_NBLCK)`` on one byte at ``LOCK_BYTE_OFFSET``,
  past the holder text, because a locked range is unreadable to other
  processes and a refused process must be able to read who holds it.
- **The lock is taken before the first connect**, because every store
  constructor writes its schema: a refused process creates nothing.
- **One process may take its own lock again.** Two descriptors on one file
  conflict even inside one process, and the suite re-opens a live store 855
  times in one process; a registry keyed by the lock file's device and inode
  counts the takers, and the OS lock is taken once per process per store.
- **The file is never deleted.** A deleted lock file would let a second
  process lock a new file at the same path while the first still holds the
  old one.
- **The holder text** (one JSON line, padded) names the pid, the mode
  (``console``, ``mcp``, ``script:<name>``, ``services``), the host and
  when it was taken; a clean release marks it released. A lock whose text
  names a holder that never released it was left by a process that died
  (the OS lock was free, or the taker could not have taken it): the taker
  appends the takeover to ``<kb.db>.lock.log`` and logs a WARNING. Under
  ``uvicorn --reload`` every reload takes over its predecessor's lock this
  way, by design.
- **Windows only:** a crashed holder's lock is freed "depending upon
  available system resources", so a refusal whose recorded holder is no
  longer running is retried for ``WINDOWS_DEAD_HOLDER_RETRY_S``. Liveness is
  read with ``OpenProcess`` and ``WaitForSingleObject``; ``os.kill(pid, 0)``
  is never called there (``CTRL_C_EVENT`` is 0, and on a process with no
  console CPython 3.11 falls through to ``TerminateProcess``).
- **A file system that cannot lock** (``flock`` raising anything but "held")
  is refused by name, ``StoreLockUnavailable``, never opened unlocked.

Release happens at interpreter exit (``atexit``), when the services object
that took the lock is collected, or by ``StoreLock.release()``; the count
reaching zero marks the text released and frees the OS lock.
"""
from __future__ import annotations

import atexit
import errno
import json
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

LOCK_SUFFIX = ".lock"
LOG_SUFFIX = ".lock.log"
EXIT_STORE_LOCKED = 7                 # the named exit: MCP mode, the console's factory, the scripts
HOLDER_BYTES = 1024                   # the holder text's fixed width (padded, so a rewrite covers the old)
LOCK_BYTE_OFFSET = 1 << 16            # Windows: the one byte locked, past the holder text
WINDOWS_DEAD_HOLDER_RETRY_S = 5.0
LOCK_FORMAT = 1


class StoreLocked(RuntimeError):
    """Another process holds this store's lock."""

    def __init__(self, db_path: "str | os.PathLike", holder: "dict | None"):
        self.db_path = Path(db_path)
        self.holder = dict(holder or {})
        pid = self.holder.get("pid", "unknown")
        since = self.holder.get("since", "unknown")
        super().__init__(f"this store is open in another NEXUS process (pid {pid}, since {since}): "
                         f"{self.db_path}")


class StoreLockUnavailable(RuntimeError):
    """The lock file's file system refused to lock it (not because it is held)."""


@dataclass
class _Entry:
    fd: int
    lock_file: Path
    db_path: Path
    holder: dict
    count: int = 1


_registry: "dict[tuple[int, int], _Entry]" = {}
_mutex = threading.RLock()


def lock_path(db_path: "str | os.PathLike") -> Path:
    """``<db>.lock``, beside the store it guards."""
    return Path(f"{os.fspath(db_path)}{LOCK_SUFFIX}")


def takeover_log_path(db_path: "str | os.PathLike") -> Path:
    """``<db>.lock.log``: one JSON line per stale lock taken over."""
    return Path(f"{os.fspath(db_path)}{LOG_SUFFIX}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _holder(mode: str) -> dict:
    return {"nexus_store_lock": LOCK_FORMAT, "pid": os.getpid(), "mode": mode,
            "host": socket.gethostname(), "since": _utc_now(), "released": False}


def _read_holder(fd: int) -> "dict | None":
    """The holder text at the head of the file: a dict, None for an empty
    file, or ``{"unreadable": <text>}`` for text that is not a holder."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, HOLDER_BYTES)
    except OSError as e:
        return {"unreadable": f"{type(e).__name__}: {e}"}
    text = raw.decode("utf-8", "replace").strip().splitlines()
    if not text or not text[0].strip():
        return None
    try:
        value = json.loads(text[0])
    except ValueError:
        return {"unreadable": text[0][:200]}
    return value if isinstance(value, dict) else {"unreadable": text[0][:200]}


def _write_holder(fd: int, holder: dict) -> None:
    line = (json.dumps(holder, sort_keys=True) + "\n").encode("utf-8")
    if len(line) > HOLDER_BYTES:
        raise ValueError(f"holder text is {len(line)} bytes, over {HOLDER_BYTES}")
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, line.ljust(HOLDER_BYTES, b" "))
    os.fsync(fd)


def _windows_pid_running(pid) -> bool:
    """True when `pid` names a running process (Windows). Access denied
    means it exists. Never os.kill (see the module docstring)."""
    import ctypes
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION, SYNCHRONIZE = 0x1000, 0x00100000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5          # ERROR_ACCESS_DENIED: it exists
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0x102   # WAIT_TIMEOUT: still running
    finally:
        kernel32.CloseHandle(handle)


def _os_lock(fd: int, db_path: Path) -> None:
    """Take the OS lock on `fd` without blocking, or raise StoreLocked
    (held) or StoreLockUnavailable (the file system cannot lock)."""
    if os.name == "nt":
        import msvcrt
        deadline = time.monotonic() + WINDOWS_DEAD_HOLDER_RETRY_S
        while True:
            os.lseek(fd, LOCK_BYTE_OFFSET, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError as e:
                if e.errno not in (errno.EACCES, errno.EDEADLK):
                    raise StoreLockUnavailable(f"cannot lock {lock_path(db_path)}: {e}") from None
                holder = _read_holder(fd)
                pid = (holder or {}).get("pid")
                if pid is not None and not _windows_pid_running(pid) and time.monotonic() < deadline:
                    time.sleep(0.25)          # a dead holder's lock, not yet freed by the OS
                    continue
                raise StoreLocked(db_path, holder) from None
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise StoreLocked(db_path, _read_holder(fd)) from None
    except OSError as e:
        raise StoreLockUnavailable(f"cannot lock {lock_path(db_path)}: {e}") from None


def _os_unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, LOCK_BYTE_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def _record_takeover(db_path: Path, previous: dict, holder: dict) -> None:
    event = {"event": "stale_lock_taken_over", "at": holder["since"], "store": os.fspath(db_path),
             "previous": previous,
             "by": {k: holder[k] for k in ("pid", "mode", "host")}}
    with open(takeover_log_path(db_path), "a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")
    logger.warning("store lock: took over a stale lock on %s, left by pid %s (%s, since %s) without a "
                   "release; recorded in %s", db_path, previous.get("pid", "unknown"),
                   previous.get("mode", "unknown"), previous.get("since", "unknown"),
                   takeover_log_path(db_path))


@dataclass
class StoreLock:
    """One taker's count on its process's lock for a store. `release()`
    gives that count back, once. `key[0] == "postgres"` names a lock held
    on a Postgres session (W3b part 3); every other key is a file's."""
    key: tuple
    db_path: Path
    released: bool = field(default=False)

    def release(self) -> None:
        with _mutex:
            if self.released:
                return
            self.released = True
            if self.key and self.key[0] == POSTGRES_KEY:
                _release_postgres(self.key[1:])
                return
            entry = _registry.get(self.key)
            if entry is None:
                return
            entry.count -= 1
            if entry.count > 0:
                return
            del _registry[self.key]
            try:
                _write_holder(entry.fd, dict(entry.holder, released=True, released_at=_utc_now()))
            except (OSError, ValueError):
                pass
            _os_unlock(entry.fd)
            os.close(entry.fd)

    @property
    def lock_file(self) -> Path:
        return lock_path(self.db_path)


# ---------------------------------------------------------------------------
# W3b part 3 (run/specs/W3b.md D8) — the same lock where the store is a
# Postgres database. The OS lock's twin is a session-level advisory lock,
# `pg_try_advisory_lock(hashtext('nexus-store:<database>/<schema>'))`, taken
# on a DEDICATED connection kept open for the process's life: refused at once
# to a second session, freed the moment the holder's session ends (measured
# about 3 ms after a SIGKILL of the client; a holder whose HOST vanished
# keeps it until TCP's keepalives notice, which the connection asks for and
# the runbook says). The holder text and the takeover log are two tables in
# the store's schema (`nexus_store_lock`, `nexus_store_lock_log`: the twins
# of the file and its `.log`, written on the lock session), so a refused
# process names the holder as a file holder is named. One process may take
# its own lock again: a registry keyed by a digest of the URL and the schema
# counts the takers, as the file registry does by device and inode. `pg_terminate_backend`
# is never used (the NEXUS role must not be able to signal backends).
# ---------------------------------------------------------------------------

POSTGRES_KEY = "postgres"
PG_LOCK_PREFIX = "nexus-store:"
# The two tables are support DDL in every schema (`backend/store/dialect.py`,
# SUPPORT_DDL), so a refused process can read the holder before any store
# opened; the SQL below is spelled for the store interface (`?`, the
# dialect's spellings), which is the one place that knows the driver.
PG_LOCK_TABLE, PG_LOCK_LOG_TABLE = "nexus_store_lock", "nexus_store_lock_log"


@dataclass
class _PgEntry:
    db: object                  # the lock's own `Database`, kept for the process's life
    text_key: str
    store: str
    holder: dict
    count: int = 1


_pg_registry: "dict[tuple[str, str], _PgEntry]" = {}


def is_postgres_target(value) -> bool:
    return getattr(value, "backend", None) == POSTGRES_KEY and bool(getattr(value, "url", None))


def _pg_registry_key(target) -> tuple[str, str]:
    """The registry's key for a target: a digest of its URL, never the URL,
    because the key is a `StoreLock`'s and a lock's repr reaches log lines
    and assertion output (W3b close, F6)."""
    import hashlib
    return (hashlib.sha256(target.url.encode("utf-8")).hexdigest(), target.schema or "")


def _pg_read_holder(db, text_key: str) -> "dict | None":
    """The holder row for `text_key`, None when there is none yet."""
    row = db.execute("SELECT holder FROM nexus_store_lock WHERE lock_key = ?", (text_key,)).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0])
    except ValueError:
        return {"unreadable": str(row[0])[:200]}
    return value if isinstance(value, dict) else {"unreadable": str(row[0])[:200]}


def _pg_text_key(db) -> tuple[str, str]:
    """(the lock's text key, the schema) for the session: the database and
    the schema the session resolves names to, never a guess from the URL."""
    database, schema = db.execute("SELECT current_database(), current_schema()").fetchone()
    return f"{PG_LOCK_PREFIX}{database}/{schema}", schema


def _acquire_postgres(target, mode: str) -> StoreLock:
    from backend.store import connect
    from backend.store.target import redacted_url
    reg_key = _pg_registry_key(target)
    with _mutex:
        entry = _pg_registry.get(reg_key)
        if entry is not None:
            entry.count += 1
            return StoreLock(key=(POSTGRES_KEY,) + reg_key, db_path=Path(entry.store))
        db = connect(target)                         # a dedicated session: the advisory lock is the session's
        try:
            text_key, schema = _pg_text_key(db)
            store = f"postgres {redacted_url(target.url)} schema {schema}"
            got = db.execute("SELECT pg_try_advisory_lock(hashtext(?))", (text_key,)).fetchone()[0]
            if not got:
                raise StoreLocked(store, _pg_read_holder(db, text_key))
            previous = _pg_read_holder(db, text_key)
            holder = dict(_holder(mode), store=text_key)
            db.execute("INSERT INTO nexus_store_lock (lock_key, holder) VALUES (?, ?) "
                       "ON CONFLICT (lock_key) DO UPDATE SET holder = excluded.holder",
                       (text_key, json.dumps(holder, sort_keys=True)))
            if previous is not None and not previous.get("released", False):
                event = {"event": "stale_lock_taken_over", "at": holder["since"], "store": text_key,
                         "previous": previous, "by": {k: holder[k] for k in ("pid", "mode", "host")}}
                db.execute("INSERT INTO nexus_store_lock_log (at, event) VALUES (?, ?)",
                           (holder["since"], json.dumps(event, sort_keys=True)))
                logger.warning("store lock: took over a stale lock on %s, left by pid %s (%s, since %s) without "
                               "a release; recorded in %s", store, previous.get("pid", "unknown"),
                               previous.get("mode", "unknown"), previous.get("since", "unknown"), PG_LOCK_LOG_TABLE)
            db.commit()
        except BaseException:
            db.close()
            raise
        _pg_registry[reg_key] = _PgEntry(db=db, text_key=text_key, store=store, holder=holder)
        return StoreLock(key=(POSTGRES_KEY,) + reg_key, db_path=Path(store))


def _release_postgres(reg_key: tuple) -> None:
    entry = _pg_registry.get(reg_key)
    if entry is None:
        return
    entry.count -= 1
    if entry.count > 0:
        return
    del _pg_registry[reg_key]
    try:
        entry.db.execute("UPDATE nexus_store_lock SET holder = ? WHERE lock_key = ?",
                         (json.dumps(dict(entry.holder, released=True, released_at=_utc_now()), sort_keys=True),
                          entry.text_key))
        entry.db.commit()
        entry.db.execute("SELECT pg_advisory_unlock(hashtext(?))", (entry.text_key,))
    except Exception:
        pass
    entry.db.close()


def acquire(db_path: "str | os.PathLike", *, mode: str) -> StoreLock:
    """Take the single-writer lock for the store at `db_path` (its kb.db), or
    for the Postgres `StoreTarget` given (W3b part 3), before anything
    connects to it. Raises StoreLocked when another process holds it,
    StoreLockUnavailable when the file system cannot lock."""
    if is_postgres_target(db_path):
        return _acquire_postgres(db_path, mode)
    db = Path(db_path)
    lf = lock_path(db)
    lf.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lf, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        st = os.fstat(fd)
        key = (st.st_dev, st.st_ino)
        with _mutex:
            entry = _registry.get(key)
            if entry is not None:
                entry.count += 1
                return StoreLock(key=key, db_path=db)
            _os_lock(fd, db)
            previous = _read_holder(fd)
            holder = _holder(mode)
            _write_holder(fd, holder)
            _registry[key] = _Entry(fd=fd, lock_file=lf, db_path=db, holder=holder)
            fd = -1                                   # the registry owns it now
        if previous is not None and not previous.get("released", False):
            _record_takeover(db, previous, holder)
        return StoreLock(key=key, db_path=db)
    finally:
        if fd >= 0:
            os.close(fd)


def held_here(db_path: "str | os.PathLike") -> int:
    """How many takers this process has on `db_path`'s lock (0: none); a
    Postgres target counts its session's takers."""
    if is_postgres_target(db_path):
        with _mutex:
            entry = _pg_registry.get(_pg_registry_key(db_path))
            return entry.count if entry is not None else 0
    lf = lock_path(db_path)
    try:
        st = os.stat(lf)
    except OSError:
        return 0
    with _mutex:
        entry = _registry.get((st.st_dev, st.st_ino))
        return entry.count if entry is not None else 0


def read_holder(db_path: "str | os.PathLike") -> "dict | None":
    """The holder text of `db_path`'s lock file, read without locking; for
    a Postgres target the holder row, read on a short connection that takes
    no lock."""
    if is_postgres_target(db_path):
        from backend.store import connect
        db = connect(db_path)
        try:
            return _pg_read_holder(db, _pg_text_key(db)[0])
        finally:
            db.close()
    try:
        fd = os.open(lock_path(db_path), os.O_RDONLY)
    except OSError:
        return None
    try:
        return _read_holder(fd)
    finally:
        os.close(fd)


def _release_all_at_exit() -> None:
    with _mutex:
        for key, entry in list(_registry.items()):
            entry.count = 1
            StoreLock(key=key, db_path=entry.db_path).release()
        for reg_key, entry in list(_pg_registry.items()):
            entry.count = 1
            _release_postgres(reg_key)


atexit.register(_release_all_at_exit)

__all__ = ["EXIT_STORE_LOCKED", "PG_LOCK_LOG_TABLE", "PG_LOCK_PREFIX", "PG_LOCK_TABLE", "StoreLock", "StoreLocked",
           "StoreLockUnavailable", "acquire", "held_here", "is_postgres_target", "lock_path", "read_holder",
           "takeover_log_path"]
