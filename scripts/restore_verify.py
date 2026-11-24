#!/usr/bin/env python3
"""W3 part 4 — restore a backup into a scratch folder and verify it
(run/specs/W3.md D5; RUN_WORKLIST W3: restore verification is a product
feature, not ops hygiene).

    python scripts/restore_verify.py BACKUP_FOLDER [--into SCRATCH]

BACKUP_FOLDER is one folder `scripts/backup.py` wrote (it holds
MANIFEST.json). The backup itself is never written: it is copied into
SCRATCH (a fresh folder under the system's temporary directory unless
--into names one), and every check runs on the copy. Each check prints
PASS or FAIL with what it saw:

  R1  every file the manifest names is present and hashes to its sha256,
      and no file is present that the manifest does not name (a restored
      copy that differs from the backup is not a restore);
  R2  the row counts per table of kb.db and archive.db equal the
      manifest's, read through the read-only open (scripts/_readonly.py);
  R3  the chain's block count and head equal the manifest's, and the chain
      verifies read-only (start.verify_chain_read_only: the chain check on
      a temp copy, the restored archive itself never opened read-write);
  R4  the product's own verifiers pass on a SECOND copy of the two
      databases (they open read-write and migrate older schemas, so the
      restored copy is not what they are given): verify_chain,
      verify_sealed_lineage (every record's three layers), verify_recall_index,
      verify_request_bodies (reported: `none kept` is a true state, not a
      failure) and verify_governance_records;
  R5  the restored databases' bytes are unchanged by the checks above
      (hashed before and after).

Snapshot files are verified by the manifest's hashes (R1), not by
`audit_snapshots()`: snapshot paths are sealed as given, so on a restored
copy the audit reads the live instance's files (W3's establish; a register
line).

A HOSTED BACKUP (W3b part 5, D11: the manifest's `store` names postgres)
needs `--database-url URL`. The URL names a server and a role holding
`CREATEDB` (the backup role, declared). The dump is restored into two scratch
databases on that server, made with `createdb`, filled with `pg_restore
--no-owner --no-privileges --exit-on-error` and dropped afterwards, whatever
the checks found. They are the twins of the two folder copies above:
  R1  as above; the dump is one of the files.
  R2  the counts on the first scratch database, read on a read-only
      session, equal the manifest's.
  R3  the chain's count and head there equal the manifest's, and
      `verify_chain` passes on the second.
  R4  the product's verifiers, on the second.
  R5  the first database's rows are unchanged by the checks: a digest per
      table (scripts/_store_copy.py), before R2 and after R4.
The password reaches the client tools in their environment, never on a
command line: their URLs carry no secret libpq reads, and a URL carrying
one libpq has no environment variable for (sslpassword) is refused by name
before anything runs (W3b close, F6; `scripts/backup.py`'s
url_without_password).
A hosted backup taken of a data root that also held kb.db or archive.db did
not copy them (backup.py: one backup holds one store), and its manifest
lists them (`data_root_sqlite_files_not_in_this_backup`). They are named on
a `not in this backup:` line beside the verdict, which is about the hosted
store alone (W3b close, the delta check's D6).

THE DROP (the close's F19; `drop_scratch`). A plain `dropdb` first: the
server ends an autovacuum worker on the database itself. If that is
refused, this role's own sessions on the database are ended and the plain
drop runs again. `dropdb --force` is never used: as the backup role on
PostgreSQL 16 it is refused outright while an autovacuum worker is on the
database, which a freshly restored one draws (measured). Every exit status
is read. A scratch database still on the server afterwards is a failed
result of its own, after the five: `FAIL cleanup: the scratch database
<name> is still on <server>: <the client's message>`. The five checks'
lines stand as printed, and the run fails: it never reads N of N with a
copy of the store left behind. With both dropped, a `cleanup:` line says so.

Exit codes: 0 every check passed (and, for a hosted backup, neither scratch
database is left); 1 a check failed or a scratch database is left; 2 bad
arguments or a folder with no manifest (or a hosted backup without
--database-url).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                                    # noqa: E402
from scripts._env import load_env                                                # noqa: E402
from scripts._readonly import open_read_only, redacted                           # noqa: E402
from scripts.backup import MANIFEST, NOT_IN_BACKUP, chain_head, copy_database, sha256, table_counts  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_ARGS = 0, 1, 2


def say(check: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'} {check}: {detail}", flush=True)
    return ok


def restore(backup: Path, scratch: Path) -> Path:
    """The backup copied into `scratch/restored`; the backup is read only."""
    target = scratch / "restored"
    shutil.copytree(backup, target)
    return target


def r1_files(restored: Path, manifest: dict) -> bool:
    files = {p.relative_to(restored).as_posix() for p in restored.rglob("*") if p.is_file()} - {MANIFEST}
    named = set(manifest["files"])
    missing = sorted(named - files)
    extra = sorted(files - named)
    differ = sorted(k for k in named & files if sha256(restored / k) != manifest["files"][k])
    return say("R1 files", not (missing or extra or differ),
               f"{len(named)} named; missing {missing or 'none'}; unnamed {extra or 'none'}; differing {differ or 'none'}")


def r2_counts(restored: Path, manifest: dict) -> bool:
    ok, seen = True, {}
    for name, want in manifest["counts"].items():
        conn = open_read_only(restored / name)
        try:
            got = table_counts(conn)
        finally:
            conn.close()
        seen[name] = {t: (got.get(t), want.get(t)) for t in set(got) | set(want) if got.get(t) != want.get(t)}
        ok = ok and not seen[name]
    return say("R2 counts", ok, "every table's count equals the manifest's" if ok else f"differences (got, manifest): {seen}")


def r3_chain(restored: Path, manifest: dict) -> bool:
    import start
    archive = restored / "archive.db"
    conn = open_read_only(archive)
    try:
        head = chain_head(conn)
    finally:
        conn.close()
    state, text = start.verify_chain_read_only(archive)
    ok = head == manifest["chain"] and state == "valid"
    return say("R3 chain", ok, f"head {head['head_index']} {str(head['head_hash'])[:12]} ({head['blocks']} blocks) "
                               f"{'equals' if head == manifest['chain'] else 'DIFFERS FROM'} the manifest's; "
                               f"verify_chain_read_only: {state} ({text})")


def r4_verifiers(restored: Path, scratch: Path) -> bool:
    from backend.kb.recalibration import RecalibrationStore
    from backend.memory.chain import NexusArchiveChain
    from backend.memory.lineage import LineageStore, verify_sealed_lineage
    second = scratch / "verify-copy"
    second.mkdir()
    for name in ("kb.db", "archive.db"):
        copy_database(restored / name, second / name)
    chain = NexusArchiveChain(second / "archive.db")
    lineage = LineageStore(second / "kb.db")
    try:
        vc = chain.verify_chain()
        sealed = verify_sealed_lineage(lineage, chain)
        recall = lineage.verify_recall_index()
        bodies = lineage.verify_request_bodies()
        gov = RecalibrationStore(second / "kb.db").verify_governance_records(chain)
    finally:
        try:
            chain.close()
        except Exception:                                   # noqa: BLE001
            pass
        try:
            lineage.conn.close()
        except Exception:                                   # noqa: BLE001
            pass
    results = {
        "verify_chain": (bool(vc.valid), f"{vc.n_blocks} blocks" + (f", broken at {vc.broken_at}: {vc.reason}" if not vc.valid else "")),
        "verify_sealed_lineage": (sealed.get("verdict") == "intact", f"{sealed.get('verdict')} ({sealed.get('records_checked')} records)"),
        "verify_recall_index": (bool(recall.get("consistent")), f"consistent={recall.get('consistent')} ({recall.get('pairs')} pairs)"),
        "verify_request_bodies": (bodies.get("verdict") in ("intact", "none kept"), f"{bodies.get('verdict')} ({bodies.get('checked')} checked, {bodies.get('failed')} failed)"),
        "verify_governance_records": (gov.get("verdict") == "intact", f"{gov.get('verdict')}"),
    }
    ok = all(v[0] for v in results.values())
    return say("R4 verifiers (on a second copy)", ok, "; ".join(f"{k}: {v[1]}" for k, v in results.items()))


# ---------------------------------------------------------------------------
# a hosted backup (W3b part 5, D11)
# ---------------------------------------------------------------------------

def pg_url(url: str, database: str, schema: str) -> str:
    """`url`'s server and role on `database`, the session's search_path set to
    `schema` through libpq's options (the restored store's schema keeps the
    name it had). Built on libpq's reading of the URL (`backend.store.target`,
    W3b close F6), not urlsplit's: libpq's userinfo may hold a raw `?` or `#`
    and its query has no fragment, so urlsplit cut a valid URL short."""
    from backend.store.target import replace_database
    return replace_database(url, database, options=f"-c%20search_path%3D{quote(schema)}")


def pg_tool(tool: str, url: str, *args: str) -> subprocess.CompletedProcess:
    """A client tool against `url`'s server: the URL without its password on
    the command line, the password in PGPASSWORD."""
    from scripts.backup import pg_client, url_without_password
    path, _major, _line = pg_client(tool)
    plain, password = url_without_password(url)
    env = dict(os.environ)
    if password is not None:
        env["PGPASSWORD"] = password
    conn = f"--maintenance-db={plain}" if tool in ("createdb", "dropdb") else f"--dbname={plain}"
    return subprocess.run([path, conn, *args], env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def pg_restore_into(url: str, name: str, dump: Path, schema: str) -> None:
    """A fresh database `name` on `url`'s server holding the dump. The fresh
    database's own schema of the dump's name is dropped first: a new
    database has its own empty `public`, and the store's `public` (the
    deployment's default) is the one the dump creates, so `pg_restore`
    stopped on "schema public already exists" (found by the deploy smoke's
    hosted run, W3b part 5). A database this script just made holds nothing
    else to lose."""
    from scripts.backup import pg_client, url_without_password
    from backend.store.target import redacted_text
    made = pg_tool("createdb", url, name)
    if made.returncode != 0:
        # A tool's text is printed by the FAIL line: redacted, though the tool
        # was never given a secret (its URL carries none: F6).
        raise RuntimeError(f"createdb {name}: {redacted_text(made.stderr.strip(), url)[:300]}")
    plain, password = url_without_password(pg_url(url, name, "public"))
    env = dict(os.environ)
    if password is not None:
        env["PGPASSWORD"] = password
    psql, _major, _line = pg_client("psql")
    cleared = subprocess.run([psql, "-v", "ON_ERROR_STOP=1", "-X", "-q", f"--dbname={plain}",
                              "-c", f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'], env=env, capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
    if cleared.returncode != 0:
        raise RuntimeError(f"clearing the fresh database {name}'s own schema {schema}: "
                           f"{redacted_text(cleared.stderr.strip(), url)[:300]}")
    path, _major, _line = pg_client("pg_restore")
    done = subprocess.run([path, "--no-owner", "--no-privileges", "--exit-on-error", f"--dbname={plain}", str(dump)],
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise RuntimeError(f"pg_restore into {name}: {redacted_text(done.stderr.strip(), url)[:300]}")


def pg_digests(target) -> dict:
    """Every data table's rows as one digest each (scripts/_store_copy.py),
    read on a read-only session."""
    from backend.store import connect
    from scripts._store_copy import data_tables, table_digest
    db = connect(target, read_only=True)
    try:
        return {t: table_digest(db, t) for t in data_tables(db)}
    finally:
        db.close()


def r2_pg_counts(target, manifest: dict) -> bool:
    from backend.store import connect
    from scripts.backup import db_table_counts
    db = connect(target, read_only=True)
    try:
        got = db_table_counts(db)
    finally:
        db.close()
    want = manifest["counts"]["store"]
    seen = {t: (got.get(t), want.get(t)) for t in set(got) | set(want) if got.get(t) != want.get(t)}
    return say("R2 counts", not seen, "every table's count equals the manifest's" if not seen
               else f"differences (got, manifest): {seen}")


def r3_pg_chain(first, second, manifest: dict) -> bool:
    from backend.memory.chain import NexusArchiveChain
    from backend.store import connect
    db = connect(first, read_only=True)
    try:
        head = chain_head(db)
    finally:
        db.close()
    chain = NexusArchiveChain(second)
    try:
        vc = chain.verify_chain()
    finally:
        chain.close()
    ok = head == manifest["chain"] and bool(vc.valid)
    return say("R3 chain", ok, f"head {head['head_index']} {str(head['head_hash'])[:12]} ({head['blocks']} blocks) "
                               f"{'equals' if head == manifest['chain'] else 'DIFFERS FROM'} the manifest's; "
                               f"verify_chain on the second database: {'valid' if vc.valid else 'BROKEN'}"
                               + ("" if vc.valid else f" at {vc.broken_at}: {vc.reason}"))


def r4_pg_verifiers(second) -> bool:
    from backend.kb.recalibration import RecalibrationStore
    from backend.memory.chain import NexusArchiveChain
    from backend.memory.lineage import LineageStore, verify_sealed_lineage
    # Every store built so far is closed below, even when a later one's
    # constructor raises: the session each holds on the second database
    # must not outlive this check into the drop (F19).
    opened = []
    try:
        chain = NexusArchiveChain(second)
        opened.append(chain)
        lineage = LineageStore(second)
        opened.append(lineage)
        recal = RecalibrationStore(second)
        opened.append(recal)
        vc = chain.verify_chain()
        sealed = verify_sealed_lineage(lineage, chain)
        recall = lineage.verify_recall_index()
        bodies = lineage.verify_request_bodies()
        gov = recal.verify_governance_records(chain)
    finally:
        for s in opened:
            try:
                s.close()
            except Exception:                               # noqa: BLE001
                pass
    results = {
        "verify_chain": (bool(vc.valid), f"{vc.n_blocks} blocks" + (f", broken at {vc.broken_at}: {vc.reason}" if not vc.valid else "")),
        "verify_sealed_lineage": (sealed.get("verdict") == "intact", f"{sealed.get('verdict')} ({sealed.get('records_checked')} records)"),
        "verify_recall_index": (bool(recall.get("consistent")), f"consistent={recall.get('consistent')} ({recall.get('pairs')} pairs)"),
        "verify_request_bodies": (bodies.get("verdict") in ("intact", "none kept"), f"{bodies.get('verdict')} ({bodies.get('checked')} checked, {bodies.get('failed')} failed)"),
        "verify_governance_records": (gov.get("verdict") == "intact", f"{gov.get('verdict')}"),
    }
    ok = all(v[0] for v in results.values())
    return say("R4 verifiers (on the second database)", ok, "; ".join(f"{k}: {v[1]}" for k, v in results.items()))


def one_line(text) -> str:
    """A client's message on one line (dropdb's ERROR and DETAIL lines
    joined), bounded."""
    return " ".join(str(text).split())[:400]


def run_dropdb(url: str, name: str) -> tuple[bool, str]:
    """A plain `dropdb --if-exists name` on `url`'s server: (dropped, what the
    client said). With --if-exists an exit of 0 means the database is not
    there now; any other exit is a refusal, given in the client's words."""
    try:
        done = pg_tool("dropdb", url, "--if-exists", name)
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:     # dropdb absent (refused by name) or not runnable
        return False, f"dropdb could not run: {one_line(e)}"
    if done.returncode == 0:
        return True, ""
    return False, one_line(done.stderr) or f"dropdb exited {done.returncode} and said nothing"


def server_session(url: str):
    """A read-only session on `url`'s own database: the one dropdb names as
    its maintenance database, never a scratch database."""
    from backend.store import StoreTarget, connect
    return connect(StoreTarget.postgres(url), read_only=True)


def end_own_sessions(url: str, name: str) -> tuple["int | None", str]:
    """`pg_terminate_backend` on every session on the database `name` whose
    role is this one: (how many were ended, "") or (None, why not). A role
    may end its own sessions. The selection names no other, so an
    autovacuum worker (which has no role) and another role's session are
    left alone: the plain drop ends the first, and the cleanup's line names
    the database the second keeps."""
    try:
        db = server_session(url)
        try:
            rows = db.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                              "WHERE datname = ? AND usename = current_user", (name,)).fetchall()
        finally:
            db.close()
    except Exception as e:                                  # noqa: BLE001 - said on the cleanup's line, never raised
        return None, f"{type(e).__name__}: {one_line(e)}"
    return sum(1 for r in rows if r[0]), ""


def on_server(url: str, name: str) -> tuple["bool | None", str]:
    """Whether the database `name` is on `url`'s server, read from
    pg_database (which every role may read): (True or False, "") or (None,
    why it could not be read). Unknown is said, never taken for absent."""
    try:
        db = server_session(url)
        try:
            return db.execute("SELECT 1 FROM pg_database WHERE datname = ?", (name,)).fetchone() is not None, ""
        finally:
            db.close()
    except Exception as e:                                  # noqa: BLE001 - said on the cleanup's line, never raised
        return None, f"{type(e).__name__}: {one_line(e)}"


def drop_scratch(url: str, name: str) -> tuple["bool | None", str]:
    """Drop the scratch database `name` (the close's F19). Returns (gone,
    said): gone is True when it is not on the server, False when it still
    is, None when neither could be established. said is what the cleanup
    reports ("" when the first drop dropped it).

    1. A plain `dropdb --if-exists`. The server signals an autovacuum
       worker on the database itself and waits up to 5 s for every session
       on it to end (CountOtherDBBackends).
    2. Refused: this role's own sessions on the database are ended
       (`end_own_sessions`), and the plain drop runs again. A connection that
       starts while a drop waits, an autovacuum worker's included, waits for
       the drop's lock and connects the moment the drop fails; the second
       plain drop ends such a worker as the first would have.
    3. Refused again: pg_database says whether the database is still there.

    Never `dropdb --force`. On PostgreSQL 16, FORCE first checks the right
    to end EVERY other session on the database, and refuses before ending
    any (TerminateOtherDBBackends). An autovacuum worker has no role, and
    the backup role (CREATEDB, not pg_signal_backend) has the privileges of
    no role such a worker carries, so FORCE is refused outright: "permission
    denied to terminate process". With an autovacuum worker on the database,
    FORCE fails where a plain drop succeeds. Seen on 16.15: the old code's
    FORCE refused on a scratch database (the server log of the close's F2
    run, 10 November). Measured there: a plain drop then FORCE, against a
    session of the dropping role's own, refused at the FORCE in 2 of 10
    rounds, each time with an autovacuum worker connected in between."""
    dropped, refused = run_dropdb(url, name)
    if dropped:
        return True, ""
    ended, why = end_own_sessions(url, name)
    own = (f"{ended} session{'' if ended == 1 else 's'} of this role's own on it ended" if ended is not None
           else f"this role's own sessions on it could not be ended: {why}")
    dropped, again = run_dropdb(url, name)
    if dropped:
        return True, f"{name}: the plain drop was refused ({refused}); {own}; the second plain drop dropped it"
    there, unread = on_server(url, name)
    if there is False:
        return True, f"{name}: not on the server, though the plain drop was refused twice ({again})"
    said = f"{again} (the plain drop was refused twice; between the two, {own})"
    if there is None:
        return None, f"{said}; pg_database could not be read: {unread}"
    return False, said


def verify_hosted(restored: Path, manifest: dict, url: str) -> list[bool]:
    """The five checks of a hosted backup, on two scratch databases restored
    from its dump. Then both are dropped (`drop_scratch`), whatever the
    checks found. A scratch database left on the server is a failed result
    of its own, `FAIL cleanup`, after the five.

    THE SESSIONS ON A SCRATCH DATABASE, AND WHEN (F19). This script's own
    are each closed by the step that opened them, before the next starts:
      - `createdb` runs on the URL's own database and opens none on a
        scratch database.
      - psql (clearing the fresh database's own schema) and pg_restore hold
        one session each while they run.
      - R5's first digest and R2 hold one read-only session each on the
        first database. R3 holds one on the first, then the chain store's on
        the second.
      - R4 holds the chain, lineage and recalibration stores' sessions on
        the second. They are closed together, a store built before a later
        one raised included.
      - R5's second digest holds one on the first.
    So none is open when the drop starts, with three exceptions. A close
    may have failed. An exception may still hold a store's session: a store
    whose constructor raised, or any store while an error other than a
    failed restore is on its way out through this function. And the
    server's end of a closed session can outlive the client by a moment.
    The plain drop waits for the last; the drop's second step ends the
    first two.
    Two kinds are not this script's. An autovacuum worker, which the server
    may start on a scratch database at any time once its tables are
    written, holds no role; the plain drop ends it. Another role's session
    is one the backup role may not end; the cleanup names the database it
    keeps."""
    from backend.store import StoreTarget
    schema = manifest["store"]["schema"]
    stem = f"nexus_restore_{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}_{os.getpid()}"
    names = (f"{stem}_a", f"{stem}_b")
    print(f"scratch databases: {', '.join(names)} on {redacted(url)} (dropped afterwards)", flush=True)
    results = [r1_files(restored, manifest)]
    made = []
    try:
        for name in names:
            made.append(name)
            pg_restore_into(url, name, restored / manifest["store"]["dump"], schema)
        first, second = (StoreTarget.postgres(pg_url(url, n, schema)) for n in names)
        before = pg_digests(first)
        results += [r2_pg_counts(first, manifest), r3_pg_chain(first, second, manifest), r4_pg_verifiers(second)]
        after = pg_digests(first)
        results.append(say("R5 restored rows unchanged by the checks", before == after,
                           f"{len(before)} tables digest as restored" if before == after
                           else f"changed: {[t for t in before if before[t] != after.get(t)]}"))
    except RuntimeError as e:
        results.append(say("restore", False, str(e)))
    finally:
        left = False
        for name in made + [n for n in names if n not in made]:
            gone, said = drop_scratch(url, name)
            if gone:
                if said:
                    print(f"cleanup: {said}", flush=True)
                continue
            left = True
            where = "is still on" if gone is False else "may still be on"
            results.append(say("cleanup", False, f"the scratch database {name} {where} {redacted(url)}: {said}; "
                                                 "the checks above stand; drop it as a role that may end its sessions"))
        if not left:
            print(f"cleanup: neither scratch database is left on {redacted(url)}", flush=True)
    return results


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("backup", nargs="?", default=None,
                    help="one backup folder scripts/backup.py wrote (holds MANIFEST.json)")
    ap.add_argument("--latest-under", default=None,
                    help="instead of a folder: the newest backup folder (by name, the UTC stamp) under this directory")
    ap.add_argument("--into", default=None, help="the scratch folder to restore into (default: a fresh temporary one)")
    ap.add_argument("--keep-scratch", action="store_true", help="leave the scratch folder in place afterwards")
    ap.add_argument("--database-url", default=None,
                    help="a hosted backup's server: a postgresql:// URL for a role with CREATEDB (the backup role); "
                         "the dump is restored into two scratch databases there and they are dropped afterwards")
    args = ap.parse_args(argv)
    if bool(args.backup) == bool(args.latest_under):
        print("REFUSED: name one backup folder, or --latest-under a directory of them (not both, not neither)")
        return EXIT_ARGS
    if args.latest_under:
        under = Path(args.latest_under).resolve()
        folders = sorted(p for p in under.iterdir() if p.is_dir() and (p / MANIFEST).is_file()) if under.is_dir() else []
        if not folders:
            print(f"REFUSED: no backup folder with a {MANIFEST} under {under}")
            return EXIT_ARGS
        backup = folders[-1]
    else:
        backup = Path(args.backup).resolve()
    print(f"backup: {backup.as_posix()} (read only: copied into a scratch folder, never opened in place)", flush=True)
    if not (backup / MANIFEST).is_file():
        print(f"REFUSED: {backup} holds no {MANIFEST}: not a folder scripts/backup.py wrote")
        return EXIT_ARGS
    manifest = json.loads((backup / MANIFEST).read_text(encoding="utf-8"))
    hosted = (manifest.get("store") or {}).get("backend") == "postgres"
    if hosted and not args.database_url:
        # The compose backup service carries the backup role's URL in its
        # environment (NEXUS_BACKUP_DATABASE_URL), the one it dumped with.
        args.database_url = (os.environ.get("NEXUS_BACKUP_DATABASE_URL") or "").strip() or None
    if hosted and not args.database_url:
        print("REFUSED: this is a hosted store's backup (its manifest names postgres); --database-url URL (or "
              "NEXUS_BACKUP_DATABASE_URL) names the server and a role with CREATEDB to restore it into")
        return EXIT_ARGS
    if not hosted and args.database_url:
        print("REFUSED: --database-url restores a hosted store's backup; this one holds SQLite files")
        return EXIT_ARGS
    if hosted:
        # Before anything is copied, made or run (W3b close, F6): a URL whose
        # secret could reach the client tools only on their command line
        # (sslpassword, which libpq has no environment variable for), or that
        # libpq would read otherwise, is refused by name.
        from scripts.backup import BackupRefused, url_without_password
        try:
            url_without_password(args.database_url)
        except BackupRefused as e:
            print(f"REFUSED: {str(e).rstrip('.')}. Nothing was restored.")
            return EXIT_ARGS
    scratch = Path(args.into).resolve() if args.into else Path(tempfile.mkdtemp(prefix="nexus_restore_"))
    scratch.mkdir(parents=True, exist_ok=True)
    print(f"scratch: {scratch.as_posix()}")
    restored = restore(backup, scratch)
    if hosted:
        results = verify_hosted(restored, manifest, args.database_url)
    else:
        before = {name: sha256(restored / name) for name in ("kb.db", "archive.db") if (restored / name).is_file()}
        results = [r1_files(restored, manifest), r2_counts(restored, manifest), r3_chain(restored, manifest),
                   r4_verifiers(restored, scratch)]
        after = {name: sha256(restored / name) for name in before}
        results.append(say("R5 restored bytes unchanged by the checks", before == after,
                           "kb.db and archive.db hash as restored" if before == after else f"changed: {[k for k in before if before[k] != after[k]]}"))
    passed = sum(results)
    if manifest.get(NOT_IN_BACKUP):
        # A hosted backup of a data root that also held kb.db or archive.db
        # (backup.py's D6): said beside the verdict, which is about the
        # hosted store alone.
        print(f"not in this backup: {' and '.join(manifest[NOT_IN_BACKUP])}, SQLite store files its data root held "
              f"when it was taken. A hosted backup does not copy them (the manifest's {NOT_IN_BACKUP}), and the "
              f"checks above are about the hosted store alone")
    print(f"restore verification: {passed} of {len(results)} checks passed; taken {manifest.get('taken_at')} from {manifest.get('source')}")
    if not args.keep_scratch and not args.into:
        shutil.rmtree(scratch, ignore_errors=True)
    return EXIT_OK if passed == len(results) else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
