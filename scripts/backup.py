#!/usr/bin/env python3
"""W3 part 4 — a consistent backup of a NEXUS data root, running or not
(run/specs/W3.md D5; RUN_WORKLIST W3: restore verification is a product
feature).

    python scripts/backup.py --data-root /var/lib/nexus --out /var/backups/nexus [--keep N]

Instance-side and read-only on the source (RUN_CHARTER law 15): the first
line printed is the data root, and every source database is opened through
`scripts/_readonly.py` (mode=ro, query_only, a write-refusing authorizer)
and copied with SQLite's online backup API in one step, one read
transaction each, so a running console holding W2's lock is never blocked
and never written (W3's establish measured kb.db in 2.4 to 3.3 ms and
archive.db in 0.9 to 1.3 ms under a busy writer, no lock contention). kb.db
is copied before archive.db, because records name archive blocks, and the
pair is copied again while the archive's head moved across it (a block
appended between the copies would seal an act the copy of kb.db lacks);
chroma's chroma.sqlite3 the same way, chroma's other files, the snapshots
and the logs as plain copies; the lock file itself is never copied (it is
the running process's), but the holder at backup time is recorded.

One folder per backup, `<out>/<UTC stamp>/`, with MANIFEST.json beside the
files: when it was taken, the source, the lock holder, the row counts per
table of both databases, the chain's block count and head, the lineage
count with the sha256 of its sorted record hashes, the integrity_check of
each copied database (run on the COPY, which this script owns), and every
file's sha256. `scripts/restore_verify.py` reads the manifest back.

`--keep N` removes the oldest backup folders beyond N AFTER a successful
backup, naming each one removed; without it nothing is removed. A backup
folder is removed only when it holds a MANIFEST.json (never a folder this
script did not write).

THE HOSTED STORE (W3b part 5, run/specs/W3b.md D11). Under
`NEXUS_STORE=postgres` the store's tables live in a database, and the data
root keeps chroma/, snapshots/ and the logs:

    python scripts/backup.py --data-root /var/lib/nexus --database-url postgresql://… --out /var/backups/nexus

The data root's files are copied as above. The store itself:
- is read through the one read-only open (`scripts/_readonly.py`'s URL form:
  a read-only session and a role that cannot write; a role that could is
  refused by name before anything is written);
- is dumped with `pg_dump -Fc` into `store.pgdump`, from a snapshot this
  script exports inside one REPEATABLE READ, READ ONLY transaction. The
  manifest's counts, chain head, lineage digest and lock holder are read in
  that same transaction, so they describe exactly the dump.
`pg_dump` must be at least the server's major (refused by name otherwise),
and the password reaches it in its environment, never on a command line:
the URL pg_dump is given carries no secret libpq reads (W3b close, F6: the
userinfo's and the query's `password=` taken out, the password the one
libpq would use, percent-decoded), and a URL carrying a secret libpq has no
environment variable for (`sslpassword` and libpq 18's three) is refused
by name before anything runs. Without --database-url the URL is read from
NEXUS_BACKUP_DATABASE_URL (the compose backup service's), so backup.sh
never puts it on this script's command line.
The manifest says `format: 2`, names the store (the URL redacted, the
database, the schema, both versions), and checks the dump with
`pg_restore --list` where a SQLite backup runs integrity_check.
`scripts/restore_verify.py` restores it into scratch databases.

ONE BACKUP HOLDS ONE STORE (W3b close, the delta check's D6). A hosted
backup copies the data root's side files and never kb.db or archive.db. So
when a URL is given and the data root holds either file, which store is
meant has to have been said:
- The URL came from NEXUS_BACKUP_DATABASE_URL (the environment, or a `.env`
  this script loads): REFUSED by name, exit 2, before anything connects and
  with nothing written. The setting is easily left behind, or set for
  another deployment, and it turned a SQLite deployment's backup into a
  hosted one that reported success without the store its data root holds.
  The refusal names both stores and the ways out: --database-url given
  explicitly; the setting unset (offered where the data root holds kb.db:
  archive.db alone is no store this script backs up); or, on a host that
  was migrated (migrate_sqlite_to_postgres.py leaves its source files in
  place), the old files moved out of the data root once the migration is
  verified.
- The URL was given as --database-url: the hosted backup is taken, and what
  it leaves out is declared: a `not in this backup:` line names the files,
  and the manifest lists them in `data_root_sqlite_files_not_in_this_backup`
  (an empty list when the data root held none; `restore_verify.py` prints
  the list beside its verdict).
A data root with neither file is the hosted shape, by either route.

Exit codes: 0 done; 2 bad arguments, a data root or database that is not a
NEXUS store, two stores named (the setting's URL and a data root that holds
SQLite store files), a database that does not answer, a role that could
write, a URL whose secret cannot be kept off a command line, or a pg_dump
older than the server;
5 a copied database fails integrity_check, or the dump fails or does not
list (the backup folder is kept and named, so it can be inspected; it is not
a backup to restore from); 6 no consistent pair of the SQLite stores: the
archive's head moved during each of PAIR_ATTEMPTS copies, and the folder is
removed, so nothing restorable is left.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402
from scripts._readonly import ReadOnlyRefused, is_url, open_read_only, redacted, table_exists  # noqa: E402
from backend.store import BackendUnavailable, Error as StoreError, redacted_text  # noqa: E402

EXIT_OK, EXIT_ARGS, EXIT_INTEGRITY, EXIT_NO_PAIR = 0, 2, 5, 6
#: The compose backup service's setting: the backup role's URL, read when
#: --database-url is not given (restore_verify.py reads it the same way).
BACKUP_URL_SETTING = "NEXUS_BACKUP_DATABASE_URL"
PG_DUMP_FILE = "store.pgdump"
DATABASES = ("kb.db", "archive.db")            # in this order: records name archive blocks
#: A hosted backup's manifest field: the SQLite store files its data root
#: held and it did not copy (D6). Always present in a manifest this script
#: writes for a hosted store, an empty list when there were none; absent
#: from a manifest written before it, where nothing was looked for.
NOT_IN_BACKUP = "data_root_sqlite_files_not_in_this_backup"
CHROMA_DB = Path("chroma") / "chroma.sqlite3"
COPIED_FILES = ("turn_metrics.jsonl", "calibration_log.jsonl", "citation_capture.jsonl", "fire_capture.jsonl",
                "kb.db.lock.log", "probe_log.jsonl")
COPIED_DIRS = ("snapshots",)
LOCK_FILE = "kb.db.lock"
MANIFEST = "MANIFEST.json"
STAMP = "%Y%m%dT%H%M%SZ"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}


def chain_head(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT idx, block_hash, kind, timestamp FROM archive_blocks ORDER BY idx DESC LIMIT 1").fetchone()
    return {"blocks": conn.execute("SELECT count(*) FROM archive_blocks").fetchone()[0],
            "head_index": row[0] if row else None, "head_hash": row[1] if row else None,
            "head_kind": row[2] if row else None}


#: How many times the two stores are copied, at most, to take a consistent
#: pair (W6's delta checks, D2): kb.db is copied before archive.db, so a block
#: appended between the two copies may seal an act the copy of kb.db lacks,
#: and a store restored from such a pair names that block for good.
PAIR_ATTEMPTS = 5


def archive_head(path: Path) -> int | None:
    """The archive's highest block index; None when it holds no block."""
    if not path.is_file():
        return None
    conn = open_read_only(path)
    try:
        if not table_exists(conn, "archive_blocks"):
            return None
        head = conn.execute("SELECT MAX(idx) FROM archive_blocks").fetchone()[0]
    finally:
        conn.close()
    return None if head is None else int(head)


def lineage_digest(conn: sqlite3.Connection) -> dict:
    hashes = [r[0] for r in conn.execute("SELECT record_hash FROM lineage_records ORDER BY record_hash")]
    return {"count": len(hashes), "record_hashes_sha256": hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()}


def copy_database(src: Path, dst: Path) -> float:
    """SQLite's online backup, one step, from a read-only source connection
    into a fresh file this script owns. Returns the seconds it took."""
    source = open_read_only(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    destination = sqlite3.connect(dst)            # the copy being written: ours, by construction
    t0 = time.perf_counter()
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return time.perf_counter() - t0


def integrity_check(copy: Path) -> str:
    """PRAGMA integrity_check on the COPY (the read-only open refuses the
    pragma on a source, by design: W3's establish). `ok` or SQLite's text."""
    conn = sqlite3.connect(copy)                  # the copy this script wrote
    try:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conn.close()


def lock_holder(data_root: Path) -> dict | None:
    lock = data_root / LOCK_FILE
    if not lock.is_file():
        return None
    try:
        return json.loads(lock.read_text(encoding="utf-8").strip("\0 \n"))
    except (OSError, ValueError):
        return {"unreadable": True}


def is_nexus_store(data_root: Path) -> tuple[bool, str]:
    kb = data_root / "kb.db"
    if not kb.is_file():
        return False, f"{kb} is not a file"
    try:
        conn = open_read_only(kb)
    except Exception as e:                        # noqa: BLE001
        return False, f"{kb} cannot be opened read-only: {type(e).__name__}: {e}"
    try:
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'lineage_records'").fetchone()
    finally:
        conn.close()
    return (True, "") if has else (False, f"{kb} has no lineage_records table: not a NEXUS store")


def sqlite_store_files(data_root: Path) -> list[str]:
    """The SQLite store files a data root holds, by name and sorted: the
    members of DATABASES that are files there, which is what `take_backup`
    copies as the store and a hosted backup does not copy at all. Presence,
    not validity: a kb.db this script could not read is still a file a
    backup of the hosted store would leave out."""
    return sorted(name for name in DATABASES if (data_root / name).is_file())


def setting_source(name: str, in_environment: bool, env_files: list[Path]) -> str:
    """Where a setting's value was read, for a refusal that asks for it to be
    unset: `the environment`, or the `.env` file `load_env()` took it from.
    A variable in the process's environment beats every file, and among the
    files the first that gives the setting a value wins (backend/env.py), so
    that is the file named."""
    if in_environment:
        return "the environment"
    try:
        from dotenv import dotenv_values
        for path in env_files:
            if dotenv_values(path).get(name) is not None:
                return path.as_posix()
    except Exception:                             # noqa: BLE001 - the files read are still named, below
        pass
    return "a .env file" + (f" ({', '.join(p.as_posix() for p in env_files)})" if env_files else "")


def take_backup(data_root: Path, out: Path, *, now: float | None = None) -> tuple[Path, dict, list[str]]:
    """One backup folder under `out`. Returns (folder, manifest, the
    integrity failures)."""
    stamp = time.strftime(STAMP, time.gmtime(now if now is not None else time.time()))
    folder = out / stamp
    n = 1
    while folder.exists():                        # two backups in one second: a suffix, never an overwrite
        n += 1
        folder = out / f"{stamp}-{n}"
    folder.mkdir(parents=True)
    holder = lock_holder(data_root)
    timings: dict[str, float] = {}
    integrity: dict[str, str] = {}
    pair_attempts = copy_pair(data_root, folder, timings)
    if pair_attempts is None:
        # No consistent pair: nothing restorable is left behind. The folder is
        # this call's own (made fresh above), so restore verification, which
        # takes only a folder with a manifest, and `prune` never see it.
        shutil.rmtree(folder)
        return folder, {"pair_attempts": None}, [
            f"no consistent pair: the archive's head moved during each of {PAIR_ATTEMPTS} copies of kb.db and "
            "archive.db (a block was appended while they were copied); nothing was written"]
    for name in DATABASES:
        if (folder / name).is_file():
            integrity[name] = integrity_check(folder / name)
    copy_side_files(data_root, folder, timings, integrity)
    counts: dict[str, dict] = {}
    for name in DATABASES:
        if (folder / name).is_file():
            conn = open_read_only(folder / name)
            try:
                counts[name] = table_counts(conn)
                if name == "archive.db":
                    chain = chain_head(conn)
                if name == "kb.db":
                    lineage = lineage_digest(conn)
            finally:
                conn.close()
    manifest = {
        "format": 1,
        "taken_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time())),
        "source": str(data_root),
        "lock_holder_at_backup": holder,
        "copied_in_seconds": {k: round(v, 4) for k, v in timings.items()},
        "integrity_check": integrity,
        "counts": counts,
        "chain": chain if "archive.db" in counts else None,
        "lineage": lineage if "kb.db" in counts else None,
        "pair_attempts": pair_attempts,
        "files": {p.relative_to(folder).as_posix(): sha256(p) for p in sorted(folder.rglob("*")) if p.is_file()},
    }
    (folder / MANIFEST).write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    failures = [f"{name}: {verdict}" for name, verdict in integrity.items() if verdict != "ok"]
    return folder, manifest, failures


def copy_pair(data_root: Path, folder: Path, timings: dict) -> int | None:
    """The two stores copied in order, again while the archive's head moved
    across the pair: equal heads before kb.db's copy and in archive.db's copy
    mean no block was appended between them, so every block the copy holds
    seals rows the copy of kb.db holds, and every row it marks sealed names a
    block the copy holds. Returns the attempts it took, or None when the head
    moved across every one of PAIR_ATTEMPTS."""
    for attempt in range(1, PAIR_ATTEMPTS + 1):
        head = archive_head(data_root / "archive.db")
        for name in DATABASES:
            src, dst = data_root / name, folder / name
            if dst.exists():
                dst.unlink()                      # an earlier attempt's copy: ours, never the source
            if src.is_file():
                timings[name] = copy_database(src, dst)
        if archive_head(folder / "archive.db") == head:
            return attempt
    return None


def copy_side_files(data_root: Path, folder: Path, timings: dict, integrity: dict) -> None:
    """The data root's files beside the store: chroma's database through the
    backup API (checked), chroma's other files, the snapshots and the logs
    as plain copies."""
    if (data_root / CHROMA_DB).is_file():
        timings[CHROMA_DB.as_posix()] = copy_database(data_root / CHROMA_DB, folder / CHROMA_DB)
        integrity[CHROMA_DB.as_posix()] = integrity_check(folder / CHROMA_DB)
        for p in sorted((data_root / "chroma").rglob("*")):
            if p.is_file() and p != data_root / CHROMA_DB:
                rel = p.relative_to(data_root)
                (folder / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, folder / rel)
    for sub in COPIED_DIRS:
        if (data_root / sub).is_dir():
            shutil.copytree(data_root / sub, folder / sub, dirs_exist_ok=True)
    for name in COPIED_FILES:
        if (data_root / name).is_file():
            shutil.copy2(data_root / name, folder / name)


# ---------------------------------------------------------------------------
# the hosted store (W3b part 5, D11)
# ---------------------------------------------------------------------------

class BackupRefused(RuntimeError):
    """The hosted store cannot be backed up as asked; the message names why."""


def pg_client(tool: str) -> tuple[str, int, str]:
    """(path, major, version line) of a PostgreSQL client tool on PATH;
    refused by name when it is absent (the image installs
    postgresql-client-16, D11)."""
    path = shutil.which(tool)
    if path is None:
        raise BackupRefused(f"{tool} is not on PATH: the hosted store's backup needs the PostgreSQL client tools "
                            f"(postgresql-client-16; the NEXUS image carries them)")
    line = subprocess.run([path, "--version"], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout.strip()
    m = re.search(r"\(PostgreSQL\)\s+(\d+)", line)
    if m is None:
        raise BackupRefused(f"{tool} --version said {line!r}, which names no PostgreSQL major")
    return path, int(m.group(1)), line


def url_without_password(url: str) -> tuple[str, "str | None"]:
    """(the URL a client tool is given, the password it needs): the first
    on its command line, carrying no secret libpq reads; the second in its
    environment (PGPASSWORD), never on a command line another user's `ps`
    reads. The password is the one libpq would use: the query's `password=`
    when given (the last one), else the userinfo's, percent-decoded as libpq
    decodes it (W3b close, F6: an encoded password reached PGPASSWORD still
    encoded, and the dump failed authenticating, exit 5; a query password
    reached pg_dump's command line). `backend.store.target` is the one place
    that knows a URL's secrets; a URL it cannot pass (sslpassword, which
    has no environment variable; a password libpq cannot decode; a URL libpq
    and RFC 3986 read differently) is refused by name, BackupRefused."""
    from backend.store.target import SecretNotPassable, url_without_secrets
    try:
        return url_without_secrets(url)
    except SecretNotPassable as e:
        raise BackupRefused(str(e)) from None


def db_table_counts(db) -> dict[str, int]:
    """Every data table's row count (the store interface's `tables()`: the
    support tables, the lock and the sequence, are the process's, not data)."""
    return {t: int(db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]) for t in db.tables()}


def db_lock_holder(db) -> "dict | None":
    """The single-writer lock's holder row at backup time (W3b D8), if any."""
    if not table_exists(db, "nexus_store_lock"):
        return None
    row = db.execute("SELECT holder FROM nexus_store_lock ORDER BY lock_key LIMIT 1").fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return {"unreadable": True}


def take_pg_backup(data_root: Path, url: str, out: Path, *, now: float | None = None) -> tuple[Path, dict, list[str]]:
    """One backup folder for a hosted store: the data root's files beside the
    store, and the store as `pg_dump -Fc` from a snapshot the manifest's
    figures are read in. A kb.db or archive.db in the data root is not
    copied (one backup holds one store), and the manifest names each one
    left out (NOT_IN_BACKUP; `main` decides whether such a data root is
    backed up this way at all). Raises BackupRefused (before any folder is
    made) for a URL whose secret cannot be kept off a command line (before
    anything runs or connects), a database that does not answer, a role
    that could write, a store that is not NEXUS's or a pg_dump older than
    the server."""
    plain, password = url_without_password(url)
    try:
        db = open_read_only(url, url_ok=True)
    except ReadOnlyRefused as e:
        raise BackupRefused(str(e)) from None
    except StoreError + (BackendUnavailable,) as e:              # the drivers' classes, a tuple
        # The driver's text is redacted where NEXUS connects (backend/store); named here, not a traceback.
        raise BackupRefused(f"{redacted(url)} does not answer: {type(e).__name__}: "
                            f"{redacted_text(str(e).strip(), url)}") from None
    try:
        if not all(table_exists(db, t) for t in ("triples", "lineage_records")):
            raise BackupRefused(f"{redacted(url)} holds no triples or lineage_records table: not a NEXUS store")
        server_major = int(db.execute("SHOW server_version_num").fetchone()[0]) // 10000
        server_version = str(db.execute("SHOW server_version").fetchone()[0])
        dump_tool, dump_major, dump_version = pg_client("pg_dump")
        list_tool, _list_major, _ = pg_client("pg_restore")
        if dump_major < server_major:
            raise BackupRefused(f"pg_dump {dump_major} is older than the server ({server_major}): pg_dump must be at "
                                f"least the server's major ({dump_version})")
        stamp = time.strftime(STAMP, time.gmtime(now if now is not None else time.time()))
        folder = out / stamp
        n = 1
        while folder.exists():
            n += 1
            folder = out / f"{stamp}-{n}"
        folder.mkdir(parents=True)
        timings: dict[str, float] = {}
        integrity: dict[str, str] = {}
        copy_side_files(data_root, folder, timings, integrity)
        left_out = sqlite_store_files(data_root)      # read as the side files are copied: what this folder lacks
        schema, database = db.schema, str(db.execute("SELECT current_database()").fetchone()[0])
        db.execute("BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        try:
            snapshot = str(db.execute("SELECT pg_export_snapshot()").fetchone()[0])
            env = dict(os.environ)
            if password is not None:
                env["PGPASSWORD"] = password
            t0 = time.perf_counter()
            dump = subprocess.run([dump_tool, "--format=custom", f"--schema={schema}", f"--snapshot={snapshot}",
                                   f"--file={folder / PG_DUMP_FILE}", "--no-password", f"--dbname={plain}"],
                                  env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            timings[PG_DUMP_FILE] = time.perf_counter() - t0
            counts = {"store": db_table_counts(db)}
            chain = chain_head(db) if table_exists(db, "archive_blocks") else None
            lineage = lineage_digest(db)
            holder = db_lock_holder(db)
        finally:
            db.execute("COMMIT")
    finally:
        db.close()
    if dump.returncode != 0:
        # The tool never had a secret (its URL carries none), and its text is
        # recorded in the manifest, so it is redacted all the same (F6).
        integrity[PG_DUMP_FILE] = f"pg_dump exited {dump.returncode}: {redacted_text(dump.stderr.strip(), url)[:400]}"
    else:
        listed = subprocess.run([list_tool, "--list", str(folder / PG_DUMP_FILE)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        integrity[PG_DUMP_FILE] = ("ok" if listed.returncode == 0
                                   else f"pg_restore --list: {redacted_text(listed.stderr.strip(), url)[:400]}")
    manifest = {
        "format": 2,
        "taken_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time())),
        "source": str(data_root),
        "store": {"backend": "postgres", "target": redacted(url), "database": database, "schema": schema,
                  "server_version": server_version, "pg_dump": dump_version, "dump": PG_DUMP_FILE},
        NOT_IN_BACKUP: left_out,
        "lock_holder_at_backup": holder,
        "copied_in_seconds": {k: round(v, 4) for k, v in timings.items()},
        "integrity_check": integrity,
        "counts": counts,
        "chain": chain,
        "lineage": lineage,
        "files": {p.relative_to(folder).as_posix(): sha256(p) for p in sorted(folder.rglob("*")) if p.is_file()},
    }
    (folder / MANIFEST).write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    failures = [f"{name}: {verdict}" for name, verdict in integrity.items() if verdict != "ok"]
    return folder, manifest, failures


def prune(out: Path, keep: int) -> list[Path]:
    """The oldest backup folders beyond `keep`, removed; a folder without a
    manifest is never touched."""
    folders = sorted(p for p in out.iterdir() if p.is_dir() and (p / MANIFEST).is_file())
    removed = []
    for p in folders[:max(0, len(folders) - keep)]:
        shutil.rmtree(p)
        removed.append(p)
    return removed


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    # Read before the `.env` files are loaded: a variable already in the
    # environment beats every file, so this says which of the two the
    # setting's value came from (`setting_source`).
    in_environment = bool((os.environ.get(BACKUP_URL_SETTING) or "").strip())
    env_files = load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", required=True, help="the data root to back up (read-only)")
    ap.add_argument("--out", required=True, help="where the backup folders go")
    ap.add_argument("--keep", type=int, default=None, help="remove the oldest backups beyond N (default: remove none)")
    ap.add_argument("--database-url", default=None,
                    help="the hosted store (NEXUS_STORE=postgres): a postgresql:// URL for a role that cannot write; "
                         f"dumped beside the data root's files (default: {BACKUP_URL_SETTING}, so the URL need not "
                         "be on a command line; read from the setting, it is refused while the data root holds "
                         "kb.db or archive.db: one backup holds one store)")
    args = ap.parse_args(argv)
    read_from = None                  # where the setting was read, when --database-url did not name the store
    if args.database_url is None:
        # backup.sh (the compose backup service's entrypoint) hands the backup
        # role's URL over in this environment, never on the command line,
        # which another user's `ps` reads (W3b close, F6).
        args.database_url = (os.environ.get(BACKUP_URL_SETTING) or "").strip() or None
        if args.database_url is not None:
            read_from = setting_source(BACKUP_URL_SETTING, in_environment, env_files)
    if is_url(args.data_root):
        # The data root is a folder; a hosted store is named by --database-url (W3b part 5).
        print("REFUSED: --data-root is a folder; a database URL was given there. A hosted store is backed up "
              "with --database-url URL beside the data root's files.")
        return EXIT_ARGS
    data_root, out = Path(args.data_root).resolve(), Path(args.out).resolve()
    if args.database_url is not None:
        print(f"data root: {data_root.as_posix()} (read only: the files beside the hosted store); database: postgres "
              f"{redacted(args.database_url)} (read only: a read-only session and a role that cannot write)", flush=True)
        if not is_url(args.database_url):
            # Named by where the value was given (the delta check's D9: a value
            # read from the setting was refused as an option nobody gave).
            named = "--database-url" if read_from is None else f"{BACKUP_URL_SETTING} (read from {read_from})"
            print(f"REFUSED: {named} must be a postgresql:// URL")
            return EXIT_ARGS
        if not data_root.is_dir():
            print(f"REFUSED: {data_root} is not a folder")
            return EXIT_ARGS
        held = sqlite_store_files(data_root)
        if held and read_from is not None:
            # One backup holds one store (the delta check's D6). The setting
            # names a hosted store and the data root holds a SQLite one's
            # files, and nothing says which is meant: a hosted backup would
            # report success without them. Decided here, before anything
            # connects, runs or is written. The SQLite store is offered as a
            # way out only where there is one to back up (`is_nexus_store`
            # wants kb.db): archive.db alone is refused without the setting too.
            files = " and ".join(held)
            sqlite_way = (f"To back up the SQLite store, unset {BACKUP_URL_SETTING} where it was read ({read_from})."
                          if DATABASES[0] in held else
                          f"There is no SQLite store here to back up in its place: the data root holds no "
                          f"{DATABASES[0]}, and with {BACKUP_URL_SETTING} unset this script refuses it "
                          f"({DATABASES[0]} is not a file).")
            print(f"REFUSED: one backup holds one store, and two are named. {BACKUP_URL_SETTING} (read from "
                  f"{read_from}) names the hosted store {redacted(args.database_url)}, and the data root "
                  f"{data_root.as_posix()} holds SQLite store files ({files}), which a backup of the hosted store "
                  f"does not copy. Nothing was written.\n"
                  f"  To back up the hosted store, name it explicitly: --database-url URL. The backup then names "
                  f"{files} as left out, on a line and in its manifest.\n"
                  f"  {sqlite_way}\n"
                  f"  On a host that was migrated (scripts/migrate_sqlite_to_postgres.py leaves its source files in "
                  f"place): once the migration is verified, move {files} out of the data root. The hosted store's "
                  f"backup then runs with the setting as it is.")
            return EXIT_ARGS
    else:
        print(f"data root: {data_root.as_posix()} (read only: mode=ro, query_only and a write-refusing authorizer on every source database)", flush=True)
        ok, why = is_nexus_store(data_root)
        if not ok:
            print(f"REFUSED: {why}")
            return EXIT_ARGS
    if args.keep is not None and args.keep < 1:
        print(f"REFUSED: --keep {args.keep}: at least one backup is kept")
        return EXIT_ARGS
    if args.database_url is not None:
        try:
            folder, manifest, failures = take_pg_backup(data_root, args.database_url, out)
        except BackupRefused as e:
            print(f"REFUSED: {str(e).rstrip('.')}. Nothing was written.")
            return EXIT_ARGS
    else:
        folder, manifest, failures = take_backup(data_root, out)
        if manifest.get("pair_attempts", 1) is None:
            print(f"FAIL: {failures[0]}. Run the backup again.")
            return EXIT_NO_PAIR
    print(f"backup: {folder.as_posix()}")
    if manifest.get("store"):
        s = manifest["store"]
        print(f"store: postgres database {s['database']} schema {s['schema']} (server {s['server_version']}; "
              f"{s['pg_dump']}) dumped to {s['dump']}")
        if manifest.get(NOT_IN_BACKUP):
            # Declared, never silent (D6): reached only with --database-url
            # given, since the setting's URL is refused on such a data root.
            print(f"not in this backup: {' and '.join(manifest[NOT_IN_BACKUP])}, the SQLite store files in the data "
                  f"root {data_root.as_posix()}. --database-url names the hosted store, and one backup holds one "
                  f"store; the manifest lists them ({NOT_IN_BACKUP}).")
    print(f"copied: " + ", ".join(f"{k} in {v * 1000:.1f} ms" for k, v in manifest["copied_in_seconds"].items()))
    print(f"integrity_check: {manifest['integrity_check']}")
    if manifest["chain"]:
        c = manifest["chain"]
        print(f"chain: {c['blocks']} blocks, head {c['head_index']} {str(c['head_hash'])[:12]} ({c['head_kind']})")
    if manifest["lineage"]:
        print(f"lineage: {manifest['lineage']['count']} records")
    print(f"files: {len(manifest['files'])}; manifest: {(folder / MANIFEST).as_posix()}")
    holder = manifest["lock_holder_at_backup"]
    print("lock holder at backup: " + (f"pid {holder.get('pid')} ({holder.get('mode')}), released={holder.get('released')}"
                                        if isinstance(holder, dict) and "pid" in holder
                                        else ("none (no holder row)" if manifest.get("store") else "none (no lock file)")))
    if failures:
        print(f"FAIL: a copied database does not pass integrity_check: {failures}. The folder is kept for "
              f"inspection and is NOT a backup to restore from.")
        return EXIT_INTEGRITY
    if args.keep is not None:
        removed = prune(out, args.keep)
        print(f"kept the newest {args.keep}; removed: " + (", ".join(p.name for p in removed) or "none"))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
