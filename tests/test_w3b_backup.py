"""W3b part 5 — backup and restore of a hosted store (run/specs/W3b.md D11;
S3: T11). A NEXUS store is loaded into a fresh schema by the part 4
migration (the committed pre-run fixture), and a backup role is made the way
the deployment makes it: SELECT on the store's tables and CREATEDB, nothing
that writes. The scripts run as the operator runs them, as processes, and
in this process where a plant needs to reach inside.

K1  THE BACKUP: `backup.py --database-url` as the backup role writes a
    folder with `store.pgdump`, a `format: 2` manifest naming the store
    (the URL redacted, the database, the schema, both versions), the counts,
    the chain head and the lineage digest; the dump lists (`pg_restore
    --list`); no line prints the role's password. The lineage digest equals
    the one the SQLite backup of the same fixture records: `ORDER BY
    record_hash` is byte order on both (COLLATE "C").
K2  THE RESTORE: `restore_verify.py --database-url` restores the dump into
    two scratch databases and passes all five checks; neither is left.
K3  COMPANION: one byte of the dump changed: R1 names `store.pgdump`, the
    run fails, and no scratch database is left.
K4  THE REFUSALS, each writing nothing: a role that could write (the
    superuser); a hosted backup verified without --database-url; a SQLite
    backup verified with one; a pg_dump older than the server (planted in
    this process).
K5  THE COUNTS IN THE SNAPSHOT: a claim written by another session after
    pg_dump has returned and before the manifest's counts are read (planted
    inside db_table_counts) is not counted, because the counts are read in
    the transaction that exported the snapshot, so the restore verification
    passes. The dump had finished before the write, so K5 says nothing about
    the dump's own snapshot: K5b does. The companion reads the counts on a
    fresh session outside the snapshot, and R2 names the table,
    `'triples': (5, 6)`.
K5b THE DUMP IN THE SNAPSHOT: two changes by another session after the
    snapshot is exported and before pg_dump starts (planted as backup.py
    invokes pg_dump to dump, not for its --version probe): a new claim,
    which moves the triples count, and an existing claim re-asserted at a
    higher weight (add()'s MAX upsert: an UPDATE that moves no count and
    that no figure in the manifest covers). The live store holds both. The
    manifest counts the export-time rows, the dump's own data (read with
    `pg_restore --data-only`, not through the restore under test) holds
    exactly the export-time claims and weights, and the restore
    verification passes, 5 of 5. Companion: the same plant with --snapshot
    stripped from pg_dump's argv. The dump then holds both changes: R2
    names the insert, `'triples': (6, 5)`, and is the one check that fails
    (4 of 5). No restore check sees the update; only the dump's data shows
    it, the weight named by the test.
K7  THE PUBLIC SCHEMA (the deployment's shape, found by the deploy smoke's
    hosted run): a store in a database's own `public` schema backs up and
    restores, 5 of 5; before the fix pg_restore stopped on "schema public
    already exists".
K6  THE IMAGE'S CLIENT AND COMPOSE'S SERVER: the major of
    `postgresql-client-N` in deploy/Dockerfile equals the major of the
    `postgres:N…` image in docker-compose.yml (pg_dump must be at least the
    server's major). Companion: a planted compose naming 17 is named. Runs
    everywhere.
K8  A SCRATCH DATABASE THE BACKUP ROLE CANNOT DROP (the close's F19): a
    third role (LOGIN, not a superuser, nothing the backup role is a member
    of; CONNECT is PUBLIC's on a new database) opens a session on the first
    scratch database just before restore_verify's first `dropdb` runs
    (restore_verify's own main() in this process, its pg_tool wrapped). The
    run names the database it leaves, `FAIL cleanup: the scratch database
    <name> is still on <server>: …`, reads 5 of 6 rather than 5 of 5 and
    exits 1, the five checks' own lines as they were; the database is on
    the server, as the line says, and the plant's session survived. The test
    then ends its plant and drops the database itself, by name, as the
    cluster's superuser. Before the fix: `5 of 5 checks passed`, exit 0, and
    the database still on the server (dropdb's exit status was ignored).
K8b A SESSION OF THE BACKUP ROLE'S OWN left open on the first scratch
    database (the same plant, as the backup role): the plain drop is
    refused, the role's own session is ended, the second plain drop drops
    the database, and the run passes 5 of 5 with neither scratch database
    left; the plant's session was ended by the server. This holds before the
    fix too (`dropdb --force` alone ends a session of the role's own when no
    autovacuum worker is there); it fails without the fallback (a plain drop
    alone leaves the database, named).
K8c UNKNOWN IS NEVER TAKEN FOR ABSENT (the delta check's D7: three
    behaviours the drop declares that K8 and K8b did not pin; its mutants
    of the first two passed both). Both plain drops are refused (K8's
    plant) and pg_database cannot be read (the session `on_server` asks for
    is refused: planted in `server_session`, for that one caller). The run
    says the database `may still be on` the server and why it could not be
    read, reads 5 of 6 and exits 1; and the database is in fact there. With
    unknown read as absent, in `drop_scratch` or in `on_server`, the run
    reads `not on the server`, 5 of 5, exit 0.
K8d `end_own_sessions` ENDS THIS ROLE'S SESSIONS ON THIS DATABASE AND NO
    OTHER (D7). Three sessions are open when the first drop is refused, and
    the backup role holds pg_signal_backend here, so it MAY end any of
    them: the selection (`datname = ? AND usename = current_user`) is all
    that leaves two alone. Afterwards, by pg_stat_activity as the
    superuser, the role's own on the scratch database is gone, and its own
    on the URL's database and another role's on the scratch database are
    alive. Without the second condition the other role's session is ended
    too. Without the first the role's sessions on every database are
    selected, the one running the statement among them: it ends itself at
    whatever row it reaches (measured both ways: the session on the URL's
    database ended; or itself first, and none of the three), so the three
    are never left as above.
K8e GONE, THOUGH BOTH DROPS WERE REFUSED (D7): the client's report is lost
    after the server has dropped the database (planted in `run_dropdb`:
    the real drop runs, and a refusal is reported in its place), and
    pg_database says it is not there. Said on a `cleanup:` line with the
    client's words; the run passes 5 of 5, and the database is in fact
    gone. With that branch removed the run fails, naming a database that is
    not on the server as still there.
K9  ONE BACKUP HOLDS ONE STORE (the delta check's D6). The backup role's
    URL is in NEXUS_BACKUP_DATABASE_URL (in the environment, or in the
    `.env` the script loads) and the data root holds kb.db and archive.db,
    or either alone: refused by name, exit 2, both stores and the ways out
    named, the URL's password blanked, nothing written, and nothing asked
    of the hosted store (the port its URL names takes no connection). The
    way out it names is then taken: with the setting gone the same command
    backs up the SQLite store, its files in the folder; a data root with no
    kb.db has no SQLite store to back up, and the refusal says that
    instead. No server: this runs on every platform.
K9b The same where the hosted store answers, on a migrated host's data root
    (the migration leaves its source files in place). Before the fix:
    exit 0 and a folder holding store.pgdump and neither kb.db nor
    archive.db, a backup that reports success and does not hold the store
    its data root holds (law 5's false pass).
K10 `--database-url` given explicitly on that data root: the hosted backup
    is taken and what it leaves out is declared, on a `not in this backup:`
    line and in the manifest (`data_root_sqlite_files_not_in_this_backup`),
    and restore_verify.py says it again beside its verdict (5 of 5).
K11 The setting and a data root with no SQLite store (compose's hosted
    shape): the hosted backup as before, by either route; the manifest's
    field is an empty list, never absent.
K12 (the delta check's D9) A value that is not a postgres URL is refused
    naming where it was given: the setting and where it was read, or
    `--database-url`. It read `--database-url must be…` for a value nobody
    gave as an option. No server.

THE AUTOVACUUM PATH IS NOT PLANTED BY A TEST (declared limit, F19). A
scratch database's restored tables draw an autovacuum worker, which holds
no role. On PostgreSQL 16, `DROP DATABASE … WITH (FORCE)` checks the right
to end every other session on the database before it ends any, and the
backup role (CREATEDB, no pg_signal_backend) has the privileges of no role
such a worker carries, so FORCE is refused outright: "permission denied to
terminate process". The server log of the close's F2 run (10 November)
holds it: dropdb's FORCE statement refused on
`nexus_restore_20261110T060351_1912_a` in K3's run, and the database left
without a word. That run failed anyway, on its planted R1; a passing run
would have read 5 of 5 with exit 0, as K8 shows before the fix. A plain
`DROP DATABASE` signals an
autovacuum worker itself and waits up to 5 s. restore_verify therefore
drops plainly first, and, if that is refused, ends the role's own sessions
(`pg_terminate_backend`) and drops plainly again: never FORCE, because an
autovacuum worker that the first drop kept waiting connects the moment it
fails. Measured on 16.15 outside the suite, as a CREATEDB-only owner:
- an autovacuum worker on a fresh database (`usename` NULL): FORCE refused
  with that DETAIL, and the plain drop dropped it in 0.16 s, the server
  logging "terminating autovacuum process due to administrator command";
- plain then FORCE against a session of the owner's own: refused at the
  FORCE in 2 of 10 rounds, each time with an autovacuum worker connected
  between the two;
- the fixed drop (`drop_scratch`) with a worker on the database: the old
  `dropdb --force` refused, then the fixed drop dropped it at its first
  plain drop (0.47 s and 0.19 s, two runs), the termination logged each
  time;
- the fixed drop against a session of the owner's own: 30 of 30 dropped,
  in 3 of them with an autovacuum worker on the database between the two
  plain drops (each had left before the second drop's check, so those
  three show the arrival does not defeat the fallback; the line above
  shows a present worker ended).
No test waits for the server's launcher to send a worker, so the suite
proves the drop's fallback (K8b) and its failure (K8), not the autovacuum
case itself.

K6, K9 and K12 need no server and run everywhere. Every other test skips
by name without `NEXUS_TEST_DATABASE_URL`.

restore_verify prints its two scratch databases' names before it makes
either. The server may be shared (CI runs one session; a development
cluster may serve several at once), so the checks that none is left, and
the fixtures' teardowns, count and drop only the names this module's runs
printed: another session's scratch databases are not this module's to see
or to drop.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
from tests import _proc
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from tests.conftest import POSTGRES_TEST_URL as PG_URL  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "pre_run_data"
NO_PG = "no Postgres named: NEXUS_TEST_DATABASE_URL is unset (the Postgres job sets it; windows and macos run SQLite)"
BK_PASSWORD = "Pw" + hashlib.sha1(b"w3b part 5 backup role").hexdigest()[:20]


def _url(schema: str, role: "str | None" = None, password: "str | None" = None, database: "str | None" = None) -> str:
    parts = urlsplit(PG_URL)
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host
    user = role or parts.username or ""
    secret = password if role else parts.password
    netloc = (f"{user}:{secret}@" if secret else (f"{user}@" if user else "")) + host + \
        (f":{parts.port}" if parts.port else "")
    query = (parts.query + "&" if parts.query else "") + f"options=-c%20search_path%3D{schema}"
    return urlunsplit((parts.scheme, netloc, "/" + database if database else parts.path, query, parts.fragment))


SCRATCH_LINE = "scratch databases: "        # restore_verify's line, printed before it makes either
_SCRATCH_NAMED: set[str] = set()             # every scratch database this module's runs named


def _scratch_named(stdout: str) -> list[str]:
    """The scratch databases a restore_verify run named, from its line
    `scratch databases: A, B on <server> (dropped afterwards)`."""
    return [name for line in stdout.splitlines() if line.startswith(SCRATCH_LINE)
            for name in line[len(SCRATCH_LINE):].split(" on ", 1)[0].split(", ")]


def _run(script: str, args: list[str], tmp_path: Path, *, env: "dict | None" = None,
         env_root: "Path | None" = None) -> subprocess.CompletedProcess:
    """A script as the operator runs it, as a process. The store settings are
    taken out of its environment, and its one `.env` location is `env_root`
    (tmp_path unless given: NEXUS_ENV_ROOT, so the repository's own `.env` is
    never read). `env` is what a test plants in its environment."""
    child = {k: v for k, v in os.environ.items()
             if k not in ("NEXUS_STORE", "NEXUS_DATABASE_URL", "NEXUS_DATA_ROOT", "NEXUS_BACKUP_DATABASE_URL")}
    child.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", NEXUS_ENV_ROOT=str(env_root or tmp_path),
                 PYTHONPATH=os.pathsep.join(filter(None, [str(REPO), os.environ.get("PYTHONPATH")])))
    child.update(env or {})
    proc = _proc.run([sys.executable, str(REPO / "scripts" / script), *args], cwd=tmp_path, env=child,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=900)
    _SCRATCH_NAMED.update(_scratch_named(proc.stdout))
    return proc


def _scratch_databases() -> list[str]:
    """This module's scratch databases still on the server: the names its
    restore_verify runs printed. Another session's are neither counted here
    nor dropped by the fixtures' teardowns (the server may be shared)."""
    if not _SCRATCH_NAMED:
        return []
    import psycopg
    with psycopg.connect(PG_URL, autocommit=True) as c:
        return [r[0] for r in c.execute("SELECT datname FROM pg_database WHERE datname = ANY(%s) ORDER BY datname",
                                        (sorted(_SCRATCH_NAMED),))]


@pytest.fixture
def hosted(tmp_path):
    """The pre-run fixture migrated into a fresh schema, a backup role on it
    (SELECT on every table, CREATEDB), an empty data root beside it."""
    if not PG_URL:
        pytest.skip(NO_PG)
    if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None:
        pytest.skip("the PostgreSQL client tools (pg_dump, pg_restore) are not on PATH")
    import psycopg
    from psycopg import sql
    h = hashlib.sha1(str(tmp_path).encode()).hexdigest()[:16]
    schema, role = f"k_{h}", f"bk_{h}"
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    made_role = False
    try:
        src = tmp_path / "source"
        shutil.copytree(FIXTURE, src)
        migrated = _run("migrate_sqlite_to_postgres.py", ["--data-root", str(src), "--to", _url(schema), "--confirm"],
                        tmp_path)
        assert migrated.returncode == 0, migrated.stdout + migrated.stderr
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(sql.SQL("CREATE ROLE {} LOGIN CREATEDB PASSWORD {}").format(sql.Identifier(role),
                                                                                sql.Literal(BK_PASSWORD)))
            made_role = True
            c.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
            c.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(sql.Identifier(schema),
                                                                                     sql.Identifier(role)))
        root = tmp_path / "dataroot"
        (root / "snapshots").mkdir(parents=True)
        (root / "turn_metrics.jsonl").write_text("{}\n", encoding="utf-8")
        yield {"schema": schema, "role": role, "url": _url(schema, role, BK_PASSWORD), "su": _url(schema),
               "server": _url(schema, role, BK_PASSWORD, database=urlsplit(PG_URL).path.lstrip("/")),
               "root": root, "out": tmp_path / "backups", "source": src}
    finally:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            for name in _scratch_databases():              # restore_verify's own, should it not have dropped them
                c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
            if made_role:
                c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s", (role,))
                c.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(role)))
                c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
            c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _backup(hosted, tmp_path) -> tuple[subprocess.CompletedProcess, "Path | None"]:
    proc = _run("backup.py", ["--data-root", str(hosted["root"]), "--out", str(hosted["out"]),
                              "--database-url", hosted["url"]], tmp_path)
    folders = sorted(hosted["out"].iterdir()) if hosted["out"].is_dir() else []
    return proc, (folders[-1] if folders else None)


def _sqlite_lineage_digest(kb: Path) -> dict:
    from scripts._readonly import open_read_only
    from scripts.backup import lineage_digest
    conn = open_read_only(kb)
    try:
        return lineage_digest(conn)
    finally:
        conn.close()


def test_k1_the_backup_dumps_the_store_and_describes_the_dump(hosted, tmp_path):
    proc, folder = _backup(hosted, tmp_path)
    out = proc.stdout + proc.stderr
    print(f"K1: exit {proc.returncode}; {[l for l in proc.stdout.splitlines() if l.startswith(('store', 'chain', 'lineage'))]}")
    assert proc.returncode == 0, out
    assert proc.stdout.splitlines()[0].startswith("data root: ") and "postgresql://" in proc.stdout.splitlines()[0]
    assert BK_PASSWORD not in out
    manifest = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
    store = manifest["store"]
    assert manifest["format"] == 2 and store["backend"] == "postgres" and store["schema"] == hosted["schema"]
    assert BK_PASSWORD not in json.dumps(manifest) and "***" in store["target"]
    assert manifest["integrity_check"] == {"store.pgdump": "ok"}
    assert "store.pgdump" in manifest["files"] and "turn_metrics.jsonl" in manifest["files"]
    assert manifest["files"]["store.pgdump"] == hashlib.sha256((folder / "store.pgdump").read_bytes()).hexdigest()
    assert manifest["counts"]["store"]["lineage_records"] == 5 and manifest["chain"]["blocks"] == 2
    assert manifest["lineage"] == _sqlite_lineage_digest(hosted["source"] / "kb.db"), \
        "the lineage digest differs from the SQLite backup's: the record hashes' order is not byte order"


def test_k2_restore_verify_restores_into_two_scratch_databases_and_passes(hosted, tmp_path):
    _proc, folder = _backup(hosted, tmp_path)
    proc = _run("restore_verify.py", [str(folder), "--database-url", hosted["server"]], tmp_path)
    print(f"K2: exit {proc.returncode}; {[l for l in proc.stdout.splitlines() if l.startswith(('PASS', 'FAIL'))]}")
    assert proc.returncode == 0 and "5 of 5 checks passed" in proc.stdout, proc.stdout + proc.stderr
    assert BK_PASSWORD not in proc.stdout + proc.stderr
    assert len(_scratch_named(proc.stdout)) == 2, \
        f"restore_verify did not name its two scratch databases, so the check below would count none:\n{proc.stdout}"
    assert _scratch_databases() == [], "a scratch database was left behind"


def test_k3_companion_a_changed_byte_in_the_dump_is_named(hosted, tmp_path):
    _proc, folder = _backup(hosted, tmp_path)
    dump = folder / "store.pgdump"
    data = bytearray(dump.read_bytes())
    data[len(data) // 2] ^= 0xFF
    dump.write_bytes(bytes(data))
    proc = _run("restore_verify.py", [str(folder), "--database-url", hosted["server"]], tmp_path)
    r1 = next(l for l in proc.stdout.splitlines() if l.startswith(("PASS R1", "FAIL R1")))
    print(f"K3: exit {proc.returncode}; {r1}")
    assert proc.returncode == 1 and r1.startswith("FAIL R1") and "differing ['store.pgdump']" in r1, proc.stdout
    assert _scratch_databases() == [], "a scratch database was left behind"


def test_k4_the_refusals_each_write_nothing(hosted, tmp_path):
    writer = _run("backup.py", ["--data-root", str(hosted["root"]), "--out", str(hosted["out"]),
                                "--database-url", hosted["su"]], tmp_path)
    assert writer.returncode == 2 and "is a superuser" in writer.stdout and "Nothing was written" in writer.stdout, \
        writer.stdout + writer.stderr
    assert not hosted["out"].exists(), "the refused backup wrote a folder"
    _proc, folder = _backup(hosted, tmp_path)
    bare = _run("restore_verify.py", [str(folder)], tmp_path)
    assert bare.returncode == 2 and "names the server and a role with CREATEDB" in bare.stdout, bare.stdout
    lite_root = tmp_path / "lite"
    shutil.copytree(FIXTURE, lite_root)
    lite = _run("backup.py", ["--data-root", str(lite_root), "--out", str(tmp_path / "lite-backups")], tmp_path)
    assert lite.returncode == 0, lite.stdout + lite.stderr
    lite_folder = sorted((tmp_path / "lite-backups").iterdir())[-1]
    crossed = _run("restore_verify.py", [str(lite_folder), "--database-url", hosted["server"]], tmp_path)
    assert crossed.returncode == 2 and "this one holds SQLite files" in crossed.stdout, crossed.stdout


def test_k4b_a_pg_dump_older_than_the_server_is_refused_by_name(hosted, tmp_path, monkeypatch):
    from scripts import backup
    real = backup.pg_client
    monkeypatch.setattr(backup, "pg_client", lambda tool: (*real(tool)[:1], 9, "pg_dump (PostgreSQL) 9.6")
                        if tool == "pg_dump" else real(tool))
    with pytest.raises(backup.BackupRefused, match="pg_dump 9 is older than the server"):
        backup.take_pg_backup(hosted["root"], hosted["url"], hosted["out"])
    assert not hosted["out"].exists()


def _plant_a_claim_after_the_dump(hosted, monkeypatch, *, outside_the_snapshot: bool):
    """A claim written by another session once the dump has run, then the
    counts read: inside the snapshot (the script) or on a fresh session (the
    companion)."""
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    from backend.store import StoreTarget, connect
    from scripts import backup
    real = backup.db_table_counts

    def counts_after_a_write(db):
        writer = TripleStore(StoreTarget.postgres(hosted["su"]))
        try:
            writer.add(subject="callisto", relation="orbits", object="jupiter", weight=0.6, tier=1,
                       granularity=Granularity.TOPIC, source="asserted")
        finally:
            writer.close()
        if not outside_the_snapshot:
            return real(db)
        fresh = connect(StoreTarget.postgres(hosted["url"]), read_only=True)
        try:
            return real(fresh)
        finally:
            fresh.close()

    monkeypatch.setattr(backup, "db_table_counts", counts_after_a_write)
    folder, manifest, failures = backup.take_pg_backup(hosted["root"], hosted["url"], hosted["out"])
    assert failures == [], failures
    return folder, manifest


def test_k5_a_claim_written_after_the_dump_is_not_counted_by_the_manifest(hosted, tmp_path, monkeypatch):
    """The manifest's counts are read in the transaction that exported the
    snapshot: a claim written once pg_dump has returned is not counted, and
    R2 passes. The dump had already finished, so the claim's absence from
    it is not the snapshot's doing (K5b plants before the dump starts)."""
    folder, manifest = _plant_a_claim_after_the_dump(hosted, monkeypatch, outside_the_snapshot=False)
    assert manifest["counts"]["store"]["triples"] == 5, manifest["counts"]["store"]["triples"]
    proc = _run("restore_verify.py", [str(folder), "--database-url", hosted["server"]], tmp_path)
    print(f"K5: {[l for l in proc.stdout.splitlines() if l.startswith(('PASS R2', 'FAIL R2'))]}")
    assert proc.returncode == 0 and "5 of 5 checks passed" in proc.stdout, proc.stdout + proc.stderr


def test_k5_companion_counts_read_outside_the_snapshot_are_named_by_r2(hosted, tmp_path, monkeypatch):
    """The counts read on a fresh session outside the snapshot count the
    claim the dump does not hold: R2 names the table, `'triples': (5, 6)`."""
    folder, manifest = _plant_a_claim_after_the_dump(hosted, monkeypatch, outside_the_snapshot=True)
    assert manifest["counts"]["store"]["triples"] == 6
    proc = _run("restore_verify.py", [str(folder), "--database-url", hosted["server"]], tmp_path)
    r2 = next(l for l in proc.stdout.splitlines() if l.startswith(("PASS R2", "FAIL R2")))
    print(f"K5 companion: {r2}")
    assert proc.returncode == 1 and r2.startswith("FAIL R2") and "'triples': (5, 6)" in r2, proc.stdout


# K5b's plant: two changes by another session after the snapshot is exported
# and before pg_dump starts. The new claim moves the triples count. The
# re-asserted one is an UPDATE of a row the export already held (add()'s
# monotone MAX upsert, which is what a running console does on a repeated
# claim): it moves no count, and no figure in the manifest covers it.
NEW_CLAIM = {"subject": "callisto", "relation": "orbits", "object": "jupiter", "source": "human_assertion"}
REASSERTED = {"subject": "ganymede", "relation": "orbits", "object": "jupiter", "source": "wikidata"}
RAISED_WEIGHT = 0.97
COPY_TRIPLES = re.compile(r'^COPY (?:"?[^".\s]+"?\.)?"?triples"? \((.+)\) FROM stdin;$')
COPY_ESCAPES = {"\\": "\\", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}


def _claim_key(claim: dict) -> tuple:
    return claim["subject"], claim["relation"], claim["object"], claim["source"]


class _BackupSubprocess:
    """backup.py's `subprocess` with `run` replaced and every other name the
    module's own, so the wrap reaches the backup's calls and nothing else in
    this process (restore_verify runs in a child of its own)."""

    def __init__(self, run):
        self.run = run

    def __getattr__(self, name):
        return getattr(subprocess, name)


def _live_claims(hosted) -> dict:
    """{(subject, relation, object, source): weight} for every row the live
    store holds, read on a fresh session."""
    import psycopg
    with psycopg.connect(hosted["su"], autocommit=True) as c:
        return {(s, r, o, src): float(w) for s, r, o, src, w in
                c.execute("SELECT subject, relation, object, source, weight FROM triples ORDER BY id")}


def _copy_field(text: str) -> "str | None":
    """One field of COPY's text format: `\\N` is NULL, and a backslash
    escapes the characters the format must escape (COPY TO writes no other)."""
    if text == "\\N":
        return None
    return re.sub(r"\\(.)", lambda m: COPY_ESCAPES.get(m.group(1), m.group(1)), text)


def _dump_claims(dump: Path) -> dict:
    """{(subject, relation, object, source): weight} for every `triples` row
    the dump itself holds, read with the client tools (`pg_restore
    --data-only --table=triples`), never through the restore under test."""
    proc = _proc.run([shutil.which("pg_restore"), "--data-only", "--table=triples", "--file=-", str(dump)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    heads = [i for i, line in enumerate(lines) if COPY_TRIPLES.match(line)]
    assert len(heads) == 1, f"{len(heads)} COPY blocks for triples in the dump's data:\n{proc.stdout[-2000:]}"
    columns = [c.strip().strip('"') for c in COPY_TRIPLES.match(lines[heads[0]]).group(1).split(",")]
    rows = []
    for line in lines[heads[0] + 1:]:
        if line == "\\.":
            break
        rows.append(dict(zip(columns, map(_copy_field, line.split("\t")), strict=True)))
    else:
        raise AssertionError("the dump's triples data has no end marker")
    return {(r["subject"], r["relation"], r["object"], r["source"]): float(r["weight"]) for r in rows}


def _differences(export_time: dict, got: dict) -> str:
    added = {k: got[k] for k in sorted(got.keys() - export_time.keys())}
    missing = {k: export_time[k] for k in sorted(export_time.keys() - got.keys())}
    moved = {k: (export_time[k], got[k]) for k in sorted(export_time.keys() & got.keys()) if export_time[k] != got[k]}
    return (f"added {added or 'none'}; missing {missing or 'none'}; "
            f"weight moved (export time, here) {moved or 'none'}")


def _plant_between_the_export_and_the_dump(hosted, monkeypatch, *, strip_the_snapshot: bool) -> dict:
    """K5b's plant. Another session writes as backup.py invokes pg_dump to
    dump (the wrap sees that call, not the `--version` probe before it and
    not the `pg_restore --list` after it), so the writes land after the
    snapshot was exported, while the backup's transaction is open, and
    before pg_dump starts. `strip_the_snapshot` also takes --snapshot out of
    pg_dump's argv (the companion). Returns the claims at export time, in
    the live store afterwards and in the dump, the manifest's triples count,
    the folder and what was stripped."""
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    from backend.store import StoreTarget
    from scripts import backup
    export_time = _live_claims(hosted)       # nothing else writes this schema before the plant
    assert _claim_key(NEW_CLAIM) not in export_time and export_time[_claim_key(REASSERTED)] < RAISED_WEIGHT, \
        export_time
    seen = {"dumps": 0, "stripped": []}

    def run(cmd, *args, **kwargs):
        if Path(str(cmd[0])).stem == "pg_dump" and "--version" not in cmd:
            seen["dumps"] += 1
            writer = TripleStore(StoreTarget.postgres(hosted["su"]))
            try:
                writer.add(**NEW_CLAIM, weight=0.6, tier=1, granularity=Granularity.TOPIC)
                writer.add(**REASSERTED, weight=RAISED_WEIGHT, tier=0, granularity=Granularity.TOPIC)
            finally:
                writer.close()
            if strip_the_snapshot:
                seen["stripped"] = [a for a in cmd if str(a).startswith("--snapshot")]
                cmd = [a for a in cmd if not str(a).startswith("--snapshot")]
        return _proc.run(cmd, *args, **kwargs, timeout=600)

    monkeypatch.setattr(backup, "subprocess", _BackupSubprocess(run))
    folder, manifest, failures = backup.take_pg_backup(hosted["root"], hosted["url"], hosted["out"])
    assert failures == [], failures
    assert seen["dumps"] == 1, f"backup.py invoked pg_dump to dump {seen['dumps']} times, and the plant ran as often"
    return {"export_time": export_time, "live": _live_claims(hosted),
            "dump": _dump_claims(folder / backup.PG_DUMP_FILE),
            "manifest_triples": manifest["counts"]["store"]["triples"], "folder": folder,
            "stripped": seen["stripped"]}


def _k5b_line(seen: dict) -> str:
    new, old = _claim_key(NEW_CLAIM), _claim_key(REASSERTED)

    def claims(c: dict) -> str:
        return (f"{len(c)} rows, {NEW_CLAIM['subject']} {'present' if new in c else 'absent'}, "
                f"{REASSERTED['subject']} weight {c.get(old)}")
    return (f"export time: {claims(seen['export_time'])}; live after: {claims(seen['live'])}; "
            f"manifest triples {seen['manifest_triples']}; dump: {claims(seen['dump'])}; "
            f"--snapshot stripped: {seen['stripped'] or 'none'}")


def test_k5b_changes_between_the_export_and_the_dump_are_in_neither_the_dump_nor_the_manifest(
        hosted, tmp_path, monkeypatch):
    """The dump reads the exported snapshot (`pg_dump --snapshot`): a new
    claim and an UPDATE that moves no count, written by another session after
    the export and before pg_dump starts, are in the live store and in
    neither the dump's own data nor the manifest, and the restore
    verification passes, 5 of 5."""
    seen = _plant_between_the_export_and_the_dump(hosted, monkeypatch, strip_the_snapshot=False)
    proc = _run("restore_verify.py", [str(seen["folder"]), "--database-url", hosted["server"]], tmp_path)
    r2 = next((l for l in proc.stdout.splitlines() if l.startswith(("PASS R2", "FAIL R2"))), None)
    print(f"K5b: {_k5b_line(seen)}; restore_verify exit {proc.returncode}: {r2}")
    export_time, new, old = seen["export_time"], _claim_key(NEW_CLAIM), _claim_key(REASSERTED)
    assert new in seen["live"] and seen["live"][old] == RAISED_WEIGHT and len(seen["live"]) == len(export_time) + 1, \
        f"the live store does not hold both changes: {_differences(export_time, seen['live'])}"
    assert seen["manifest_triples"] == len(export_time), seen["manifest_triples"]
    assert seen["dump"] == export_time, ("the dump holds what another session wrote after the snapshot was "
                                         f"exported: {_differences(export_time, seen['dump'])}")
    assert proc.returncode == 0 and "5 of 5 checks passed" in proc.stdout, proc.stdout + proc.stderr


def test_k5b_companion_without_the_snapshot_the_dump_holds_both_changes_and_r2_names_only_the_insert(
        hosted, tmp_path, monkeypatch):
    """The same plant with --snapshot stripped from pg_dump's argv: pg_dump
    takes its own snapshot after the writes, so the dump holds both changes
    while the manifest (read in the exported snapshot, as before) holds
    neither. R2 names the insert's count, `'triples': (6, 5)`, and is the one
    check that fails: the update moves no count and no manifest figure
    covers it, so no restore check sees it, and only the dump's data shows
    it (the weight, named here)."""
    seen = _plant_between_the_export_and_the_dump(hosted, monkeypatch, strip_the_snapshot=True)
    proc = _run("restore_verify.py", [str(seen["folder"]), "--database-url", hosted["server"]], tmp_path)
    r2 = next((l for l in proc.stdout.splitlines() if l.startswith(("PASS R2", "FAIL R2"))), None)
    print(f"K5b companion: {_k5b_line(seen)}; restore_verify exit {proc.returncode}: {r2}")
    export_time, new, old = seen["export_time"], _claim_key(NEW_CLAIM), _claim_key(REASSERTED)
    n = len(export_time)
    assert new in seen["live"] and seen["live"][old] == RAISED_WEIGHT and len(seen["live"]) == n + 1, \
        f"the live store does not hold both changes: {_differences(export_time, seen['live'])}"
    assert seen["manifest_triples"] == n, seen["manifest_triples"]
    assert new in seen["dump"] and len(seen["dump"]) == n + 1, \
        f"the insert is not in the dump: {_differences(export_time, seen['dump'])}"
    assert seen["dump"][old] == RAISED_WEIGHT, (
        f"the dump's {' '.join(_claim_key(REASSERTED))} row holds weight {seen['dump'][old]}, not the "
        f"update's {RAISED_WEIGHT}")
    named = f"FAIL R2 counts: differences (got, manifest): {{'triples': ({n + 1}, {n})}}"
    assert proc.returncode == 1 and r2 == named, proc.stdout
    assert "restore verification: 4 of 5 checks passed" in proc.stdout, proc.stdout


CLIENT = re.compile(r"\bpostgresql-client-(\d+)\b")
SERVER = re.compile(r"^\s*image:\s*postgres:(\d+)(?:[.\-][^\s]*)?\s*$", re.M)


def client_server_majors(dockerfile: str, compose: str) -> tuple[set, set]:
    return {int(m) for m in CLIENT.findall(dockerfile)}, {int(m) for m in SERVER.findall(compose)}


def test_k6_the_images_client_is_the_compose_servers_major():
    client, server = client_server_majors((REPO / "deploy" / "Dockerfile").read_text(encoding="utf-8"),
                                          (REPO / "docker-compose.yml").read_text(encoding="utf-8"))
    print(f"K6: the image's client {sorted(client)}, compose's server {sorted(server)}")
    assert len(client) == 1 and client == server, (client, server)


def test_k6_companion_a_compose_server_of_another_major_is_named():
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    planted = SERVER.sub(lambda m: m.group(0).replace(f"postgres:{m.group(1)}", "postgres:17.2"), compose)
    client, server = client_server_majors((REPO / "deploy" / "Dockerfile").read_text(encoding="utf-8"), planted)
    assert server == {17} and client != server


@pytest.fixture
def hosted_public(tmp_path):
    """The deployment's shape: the store in a database's own `public` schema
    (compose's `nexus` database), in a database made for this test, with the
    backup role on it."""
    if not PG_URL:
        pytest.skip(NO_PG)
    if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None or shutil.which("psql") is None:
        pytest.skip("the PostgreSQL client tools (pg_dump, pg_restore, psql) are not on PATH")
    import psycopg
    from psycopg import sql
    h = hashlib.sha1(str(tmp_path).encode()).hexdigest()[:16]
    database, role = f"kp_{h}", f"bkp_{h}"
    parts = urlsplit(PG_URL)
    owner_url = urlunsplit((parts.scheme, parts.netloc, "/" + database, parts.query, parts.fragment))
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    made_role = False
    try:
        src = tmp_path / "source"
        shutil.copytree(FIXTURE, src)
        migrated = _run("migrate_sqlite_to_postgres.py", ["--data-root", str(src), "--to", owner_url, "--confirm"], tmp_path)
        assert migrated.returncode == 0, migrated.stdout + migrated.stderr
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(sql.SQL("CREATE ROLE {} LOGIN CREATEDB PASSWORD {}").format(sql.Identifier(role),
                                                                                sql.Literal(BK_PASSWORD)))
            made_role = True
        with psycopg.connect(owner_url, autocommit=True) as c:
            c.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            c.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(role)))
        host = parts.hostname or ""
        host = f"[{host}]" if ":" in host else host
        netloc = f"{role}:{BK_PASSWORD}@{host}" + (f":{parts.port}" if parts.port else "")
        url = urlunsplit((parts.scheme, netloc, "/" + database, parts.query, parts.fragment))
        root = tmp_path / "dataroot"
        root.mkdir()
        yield {"url": url, "root": root, "out": tmp_path / "backups"}
    finally:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            for name in _scratch_databases():
                c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
            c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
            if made_role:
                c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_k7_a_store_in_the_public_schema_restores(hosted_public, tmp_path):
    """The deploy smoke's hosted run found it: a fresh scratch database has
    its own empty `public`, and a dump of the store's `public` creates it, so
    pg_restore stopped on "schema public already exists". The scratch
    database's own schema of that name is dropped first."""
    proc = _run("backup.py", ["--data-root", str(hosted_public["root"]), "--out", str(hosted_public["out"]),
                              "--database-url", hosted_public["url"]], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    folder = sorted(hosted_public["out"].iterdir())[-1]
    assert json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))["store"]["schema"] == "public"
    proc = _run("restore_verify.py", [str(folder), "--database-url", hosted_public["url"]], tmp_path)
    print(f"K7: exit {proc.returncode}; {[l for l in proc.stdout.splitlines() if l.startswith(('PASS', 'FAIL'))]}")
    assert proc.returncode == 0 and "5 of 5 checks passed" in proc.stdout, proc.stdout + proc.stderr
    assert _scratch_databases() == []


# ---------------------------------------------------------------------------
# K8 and K8b: the scratch databases' drop (the close's F19)
# ---------------------------------------------------------------------------

HOLDER_PASSWORD = "Pw" + hashlib.sha1(b"w3b close F19 holder role").hexdigest()[:20]
FIVE_CHECKS = ["PASS R1 files", "PASS R2 counts", "PASS R3 chain", "PASS R4 verifiers (on the second database)",
               "PASS R5 restored rows unchanged by the checks"]


def _on_server(name: str) -> bool:
    """Whether the database `name` is on the server, read as the cluster's
    superuser (not through anything restore_verify uses)."""
    import psycopg
    with psycopg.connect(PG_URL, autocommit=True) as c:
        return c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is not None


def _drop_as_superuser(name: str) -> None:
    """The test's own drop of a database it planted on, by name."""
    import psycopg
    from psycopg import sql
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def holder(hosted, tmp_path):
    """K8's third role: LOGIN, not a superuser, and nothing the backup role
    is a member of. CONNECT on a new database is PUBLIC's by default, so it
    can hold a session on a scratch database the backup role made."""
    import psycopg
    from psycopg import sql
    role = f"k8h_{hashlib.sha1(str(tmp_path).encode()).hexdigest()[:16]}"
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role),
                                                                     sql.Literal(HOLDER_PASSWORD)))
    try:
        yield role
    finally:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s", (role,))
            c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def _restore_verify_here(folder: Path, server: str, capsys) -> tuple[int, str]:
    """restore_verify's own main() in this process, so a test's wraps of the
    module's functions are the ones it runs. Returns the exit status and what
    the run printed."""
    from scripts import restore_verify
    try:
        rc = restore_verify.main([str(folder), "--database-url", server])
    finally:
        out = capsys.readouterr().out
        _SCRATCH_NAMED.update(_scratch_named(out))      # the fixtures' teardowns drop what the run named
        print(out, end="")                              # and the test's own report carries the run
    return rc, out


def _restore_verify_with_a_plant(folder: Path, server: str, plant, monkeypatch, capsys) -> tuple[int, str, dict]:
    """restore_verify's own main() in this process, its pg_tool wrapped: just
    before the first `dropdb` runs, `plant(name)` opens a session on the
    database that dropdb is about to drop. Returns the exit status, what the
    run printed and the plant ({"name", "session"})."""
    from scripts import restore_verify
    real = restore_verify.pg_tool
    planted: dict = {}

    def pg_tool(tool, url, *args):
        if tool == "dropdb" and not planted:
            planted["name"] = args[-1]
            planted["session"] = plant(args[-1])
        return real(tool, url, *args)

    monkeypatch.setattr(restore_verify, "pg_tool", pg_tool)
    rc, out = _restore_verify_here(folder, server, capsys)
    assert planted, f"restore_verify ran no dropdb, so nothing was planted:\n{out}"
    return rc, out, planted


def _summary(out: str) -> "str | None":
    return next((l for l in out.splitlines() if l.startswith("restore verification: ")), None)


def test_k8_a_scratch_database_another_role_holds_is_named_and_the_run_fails(hosted, holder, tmp_path,
                                                                            monkeypatch, capsys):
    """A session the backup role may not end (a third role's) is on the first
    scratch database when it is dropped. The run names the database it
    leaves and fails, 5 of 6: the cleanup is a failed result of its own, the
    five checks' lines unchanged. Before the fix the run read 5 of 5 and
    exited 0 with the database still on the server."""
    import psycopg
    from scripts._readonly import redacted
    _proc, folder = _backup(hosted, tmp_path)
    with psycopg.connect(PG_URL, autocommit=True) as c:
        may_not_end = c.execute(
            "SELECT NOT r.rolsuper AND NOT pg_has_role(%s, r.oid, 'USAGE') "
            "AND NOT pg_has_role(%s, 'pg_signal_backend', 'USAGE') FROM pg_roles r WHERE r.rolname = %s",
            (hosted["role"], hosted["role"], holder)).fetchone()[0]
    assert may_not_end, f"the backup role {hosted['role']} may end {holder}'s sessions: no plant"

    def plant(name):
        session = psycopg.connect(_url(hosted["schema"], holder, HOLDER_PASSWORD, database=name), autocommit=True)
        session.execute("SELECT 1")
        return session

    rc, out, planted = _restore_verify_with_a_plant(folder, hosted["server"], plant, monkeypatch, capsys)
    name, session = planted["name"], planted["session"]
    try:
        lines = out.splitlines()
        cleanup = [l for l in lines if l.startswith("FAIL cleanup")]
        left = _on_server(name)
        print(f"K8: exit {rc}; {_summary(out)}; {cleanup or 'no cleanup line'}; "
              f"{name} on the server after the run: {left}")
        assert rc == 1 and "5 of 5 checks passed" not in out, out
        assert len(cleanup) == 1 and cleanup[0].startswith(
            f"FAIL cleanup: the scratch database {name} is still on {redacted(hosted['server'])}: "), out
        assert cleanup[0].endswith("; the checks above stand; drop it as a role that may end its sessions"), cleanup
        assert [l.split(": ", 1)[0] for l in lines if l.startswith(("PASS ", "FAIL R"))] == FIVE_CHECKS, out
        assert (_summary(out) or "").startswith("restore verification: 5 of 6 checks passed"), out
        assert left, f"the run says {name} is still on the server, and it is not"
        assert session.execute("SELECT current_database()").fetchone()[0] == name, "the plant's session is gone"
        assert [n for n in _scratch_databases() if n != name] == [], "the other scratch database was left too"
        assert BK_PASSWORD not in out and HOLDER_PASSWORD not in out
    finally:
        session.close()
        _drop_as_superuser(name)
    assert not _on_server(name)


def test_k8b_a_session_of_the_backup_roles_own_is_ended_and_the_database_dropped(hosted, tmp_path, monkeypatch,
                                                                                  capsys):
    """A session of the backup role's own is on the first scratch database
    when it is dropped (as one of restore_verify's would be, had a close
    failed): the plain drop is refused, the role's own session is ended, the
    second plain drop drops it, and the run passes 5 of 5."""
    import psycopg

    _proc, folder = _backup(hosted, tmp_path)

    def plant(name):
        session = psycopg.connect(_url(hosted["schema"], hosted["role"], BK_PASSWORD, database=name),
                                  autocommit=True)
        session.execute("SELECT 1")
        return session

    rc, out, planted = _restore_verify_with_a_plant(folder, hosted["server"], plant, monkeypatch, capsys)
    name, session = planted["name"], planted["session"]
    try:
        lines = out.splitlines()
        print(f"K8b: exit {rc}; {_summary(out)}; {[l for l in lines if l.startswith(('cleanup', 'FAIL'))]}; "
              f"{name} on the server after the run: {_on_server(name)}")
        assert rc == 0 and "restore verification: 5 of 5 checks passed" in out, out
        assert not [l for l in lines if l.startswith("FAIL")], out
        assert _scratch_databases() == [], "a scratch database was left behind"
        with pytest.raises(psycopg.OperationalError):
            session.execute("SELECT 1")                 # the server ended the plant's session
    finally:
        session.close()
        if _on_server(name):
            _drop_as_superuser(name)


# K8c to K8e: three behaviours `drop_scratch` and `end_own_sessions` declare
# and K8 and K8b do not pin (the delta check's D7: its mutants of the first
# two passed both).

PLANTED_UNREADABLE = "planted: the server does not answer"
PLANTED_LOST_REPORT = "planted: dropdb exited 1 after the server had dropped the database"
OWN_ON_SCRATCH = "the backup role's own, on the scratch database"
OWN_ELSEWHERE = "the backup role's own, on the URL's database"
OTHERS_ON_SCRATCH = "another role's, on the scratch database"


def test_k8c_a_scratch_database_whose_presence_cannot_be_read_may_still_be_there_and_the_run_fails(
        hosted, holder, tmp_path, monkeypatch, capsys):
    """Unknown is never taken for absent. Both plain drops are refused
    (K8's plant: a third role's session on the first scratch database), and
    then pg_database cannot be read: the session `on_server` asks for is
    refused (planted in `server_session`, for that one caller; the session
    `end_own_sessions` asks for is the real one). The run says the database
    `may still be on` the server, with why it could not be read, reads 5 of
    6 and exits 1, the five checks' lines as they were; and the database is
    in fact there. With unknown read as absent (`if not there:` in
    `drop_scratch`, or `on_server` answering False for a server it could not
    ask) the run reads `not on the server`, 5 of 5 and exit 0, with a full
    copy of the store left behind."""
    import psycopg
    from scripts import restore_verify
    from scripts._readonly import redacted
    _proc, folder = _backup(hosted, tmp_path)
    real_on_server, real_session = restore_verify.on_server, restore_verify.server_session
    asking = {"pg_database": False, "refused": 0}

    def on_server(url, name):
        asking["pg_database"] = True
        try:
            return real_on_server(url, name)
        finally:
            asking["pg_database"] = False

    def server_session(url):
        if asking["pg_database"]:
            asking["refused"] += 1
            raise psycopg.OperationalError(PLANTED_UNREADABLE)
        return real_session(url)

    monkeypatch.setattr(restore_verify, "on_server", on_server)
    monkeypatch.setattr(restore_verify, "server_session", server_session)

    def plant(name):
        session = psycopg.connect(_url(hosted["schema"], holder, HOLDER_PASSWORD, database=name), autocommit=True)
        session.execute("SELECT 1")
        return session

    rc, out, planted = _restore_verify_with_a_plant(folder, hosted["server"], plant, monkeypatch, capsys)
    name, session = planted["name"], planted["session"]
    try:
        lines = out.splitlines()
        said = [l for l in lines if l.startswith(("FAIL cleanup", "cleanup"))]
        cleanup = [l for l in said if l.startswith("FAIL cleanup")]
        left = _on_server(name)
        print(f"K8c: exit {rc}; {_summary(out)}; {said or 'no cleanup line'}; pg_database refused to the run "
              f"{asking['refused']} time(s); {name} on the server after the run: {left}")
        assert asking["refused"] == 1, f"the plant did not run once: {asking}"
        assert left, f"no plant: {name} is not on the server, so both drops were not refused"
        assert rc == 1 and "5 of 5 checks passed" not in out, \
            f"a scratch database whose presence could not be read was taken for dropped: exit {rc}; {said}"
        assert len(cleanup) == 1 and cleanup[0].startswith(
            f"FAIL cleanup: the scratch database {name} may still be on {redacted(hosted['server'])}: "), said
        assert f"pg_database could not be read: OperationalError: {PLANTED_UNREADABLE}" in cleanup[0], cleanup
        assert [l.split(": ", 1)[0] for l in lines if l.startswith(("PASS ", "FAIL R"))] == FIVE_CHECKS, out
        assert (_summary(out) or "").startswith("restore verification: 5 of 6 checks passed"), out
        assert session.execute("SELECT current_database()").fetchone()[0] == name, "the plant's session is gone"
        assert BK_PASSWORD not in out and HOLDER_PASSWORD not in out
    finally:
        session.close()
        _drop_as_superuser(name)
    assert not _on_server(name)


def _backend_of(session) -> tuple:
    """(pid, backend_start) of a session's own backend: the pair names one
    backend for good, where a pid alone is reused."""
    return tuple(session.execute("SELECT pid, backend_start FROM pg_stat_activity "
                                 "WHERE pid = pg_backend_pid()").fetchone())


def _backends(planted: dict) -> dict:
    """{label: "<database> as <role>", or None when that backend is gone}, by
    pg_stat_activity as the cluster's superuser (not through anything
    restore_verify uses)."""
    import psycopg
    with psycopg.connect(PG_URL, autocommit=True) as c:
        seen = {(pid, started): f"{datname} as {usename}" for pid, started, datname, usename in c.execute(
            "SELECT pid, backend_start, datname, usename FROM pg_stat_activity WHERE pid = ANY(%s)",
            (sorted(pid for pid, _started in planted.values()),))}
    return {label: seen.get(backend) for label, backend in planted.items()}


def test_k8d_only_the_backup_roles_own_sessions_on_the_scratch_database_are_ended(hosted, holder, tmp_path,
                                                                                 monkeypatch, capsys):
    """`end_own_sessions` ends this role's sessions on this database, and no
    other session. Three are open when the first plain drop is refused: the
    backup role's own on the scratch database, the backup role's own on
    ANOTHER database (the URL's own), and another role's on the scratch
    database. Here the backup role holds `pg_signal_backend`, so it MAY end
    the other role's session (a superuser backup role, which the official
    image's POSTGRES_USER is, may too): the selection is all that leaves it
    alone. Afterwards, by pg_stat_activity as the superuser, only the first
    is gone. The other role's session keeps the database, so the run names
    it and fails 5 of 6, as K8, saying one session of the role's own was
    ended. Without `usename = current_user` the other role's session is
    ended too. Without `datname = ?` the role's sessions on every database
    are selected, the one running the statement among them, which ends
    itself at whatever row it reaches: the session on the URL's database is
    ended, or none of the three is; never exactly the first."""
    import psycopg
    from psycopg import sql
    from scripts._readonly import redacted
    _proc, folder = _backup(hosted, tmp_path)
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(sql.SQL("GRANT pg_signal_backend TO {}").format(sql.Identifier(hosted["role"])))
        may_end = c.execute("SELECT NOT r.rolsuper AND pg_has_role(%s, 'pg_signal_backend', 'USAGE') "
                            "FROM pg_roles r WHERE r.rolname = %s", (hosted["role"], holder)).fetchone()[0]
    assert may_end, f"the backup role {hosted['role']} may not end {holder}'s sessions: the selection is not tested"

    def plant(name):
        sessions = {
            OWN_ON_SCRATCH: psycopg.connect(_url(hosted["schema"], hosted["role"], BK_PASSWORD, database=name),
                                            autocommit=True),
            OWN_ELSEWHERE: psycopg.connect(hosted["server"], autocommit=True),
            OTHERS_ON_SCRATCH: psycopg.connect(_url(hosted["schema"], holder, HOLDER_PASSWORD, database=name),
                                               autocommit=True),
        }
        return {"sessions": sessions, "backends": {label: _backend_of(s) for label, s in sessions.items()}}

    rc, out, planted = _restore_verify_with_a_plant(folder, hosted["server"], plant, monkeypatch, capsys)
    name, sessions, backends = planted["name"], planted["session"]["sessions"], planted["session"]["backends"]
    try:
        before = {OWN_ON_SCRATCH: f"{name} as {hosted['role']}",
                  OWN_ELSEWHERE: f"{urlsplit(PG_URL).path.lstrip('/')} as {hosted['role']}",
                  OTHERS_ON_SCRATCH: f"{name} as {holder}"}
        after = _backends(backends)
        lines = out.splitlines()
        said = [l for l in lines if l.startswith(("FAIL cleanup", "cleanup"))]
        cleanup = [l for l in said if l.startswith("FAIL cleanup")]
        print(f"K8d: exit {rc}; {_summary(out)}; {said or 'no cleanup line'}")
        for label in before:
            print(f"K8d:   {label} (pid {backends[label][0]}, {before[label]}): "
                  f"{'alive' if after[label] else 'GONE'} after the run")
        assert after == {**before, OWN_ON_SCRATCH: None}, \
            f"the sessions the fallback ended are not exactly the backup role's own on {name}: after the run {after}"
        assert sessions[OWN_ELSEWHERE].execute("SELECT 1").fetchone() == (1,)
        assert sessions[OTHERS_ON_SCRATCH].execute("SELECT current_database()").fetchone()[0] == name
        with pytest.raises(psycopg.OperationalError):
            sessions[OWN_ON_SCRATCH].execute("SELECT 1")
        assert len(cleanup) == 1 and cleanup[0].startswith(
            f"FAIL cleanup: the scratch database {name} is still on {redacted(hosted['server'])}: "), said
        assert "between the two, 1 session of this role's own on it ended" in cleanup[0], cleanup
        assert rc == 1 and (_summary(out) or "").startswith("restore verification: 5 of 6 checks passed"), out
        assert _on_server(name), f"the run says {name} is still on the server, and it is not"
    finally:
        for s in sessions.values():
            s.close()
        _drop_as_superuser(name)
    assert not _on_server(name)


def test_k8e_a_scratch_database_that_is_gone_though_both_drops_were_refused_is_said_and_the_run_passes(
        hosted, tmp_path, monkeypatch, capsys):
    """Both plain drops report a refusal and pg_database says the database is
    not there: as when the client's report is lost after the server has
    dropped it (planted in `run_dropdb` for the first scratch database: the
    real drop runs and succeeds, and a refusal is reported in its place).
    The cleanup says so on its own line, `not on the server, though the
    plain drop was refused twice`, with the client's words; the run passes 5
    of 5, and the database is in fact gone. With that branch taken out, a
    database that is not there is reported as still on the server and the
    run fails."""
    from scripts import restore_verify
    _proc, folder = _backup(hosted, tmp_path)
    real = restore_verify.run_dropdb
    lost: dict = {"name": None, "really": []}

    def run_dropdb(url, name):
        dropped, said = real(url, name)
        if lost["name"] in (None, name):
            lost["name"] = name
            lost["really"].append((dropped, said))
            return False, PLANTED_LOST_REPORT
        return dropped, said

    monkeypatch.setattr(restore_verify, "run_dropdb", run_dropdb)
    rc, out = _restore_verify_here(folder, hosted["server"], capsys)
    name = lost["name"]
    lines = out.splitlines()
    cleanup = [l for l in lines if l.startswith(("FAIL cleanup", "cleanup"))]
    print(f"K8e: exit {rc}; {_summary(out)}; {cleanup}; the real drops of {name}: {lost['really']}; "
          f"on the server after the run: {_on_server(name) if name else None}")
    assert len(lost["really"]) == 2 and any(dropped for dropped, _said in lost["really"]) and not _on_server(name), \
        f"no plant: two drops of {name} were not reported refused with the database in fact dropped: {lost}"
    assert rc == 0 and "restore verification: 5 of 5 checks passed" in out, \
        f"a scratch database that is not on the server failed the run: exit {rc}; {cleanup}"
    assert cleanup[0] == (f"cleanup: {name}: not on the server, though the plain drop was refused twice "
                          f"({PLANTED_LOST_REPORT})"), cleanup
    assert not [l for l in lines if l.startswith("FAIL")], out
    assert _scratch_databases() == [], "a scratch database was left behind"


# ---------------------------------------------------------------------------
# K9 to K12: one backup holds one store (the delta check's D6 and D9)
# ---------------------------------------------------------------------------

URL_SETTING = "NEXUS_BACKUP_DATABASE_URL"
LEFT_OUT = "data_root_sqlite_files_not_in_this_backup"     # the manifest's field, as a reader of one finds it
LEFT_OUT_LINE = "not in this backup: "                      # the line backup.py and restore_verify.py print for it
SQLITE_STORE_FILES = ["archive.db", "kb.db"]                # the fixture's two, in the manifest's (sorted) order
ROUTES = ("environment", "dotenv")                          # the two ways the setting reaches backup.py


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sqlite_root(tmp_path: Path, holds=tuple(SQLITE_STORE_FILES)) -> Path:
    """A SQLite deployment's data root: the pre-run fixture's snapshot and,
    of its kb.db and archive.db, the ones in `holds` (the fixture's own
    MANIFEST.json is not an instance's)."""
    root = tmp_path / "sqlite-root"
    shutil.copytree(FIXTURE, root)
    for name in ["MANIFEST.json", *(n for n in SQLITE_STORE_FILES if n not in holds)]:
        (root / name).unlink()
    return root


def _backup_folders(out: Path) -> list[Path]:
    return sorted(p for p in out.iterdir() if p.is_dir()) if out.is_dir() else []


def _held(folder: Path) -> list[str]:
    """What a backup folder holds, as the manifest names files."""
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def _backup_by_the_setting(route: str, value: str, root: Path, out: Path, tmp_path: Path, *,
                           store: "str | None" = None) -> tuple[subprocess.CompletedProcess, str]:
    """`backup.py --data-root ROOT --out OUT` with NEXUS_BACKUP_DATABASE_URL
    planted and no --database-url:
    - "environment": in the child's environment, as backup.sh hands it over;
    - "dotenv": in a `.env` in a folder of its own, which NEXUS_ENV_ROOT
      names. That folder is then the script's one `.env` location
      (backend/env.py; tests/test_dotenv_settings_take_effect.py isolates
      the same way), the variable is absent from the child's environment,
      and the repository's own `.env` is neither read nor touched.
    `store` plants NEXUS_STORE beside it, the same way. Returns the process
    and what a refusal names as the place the setting was read."""
    args = ["--data-root", str(root), "--out", str(out)]
    planted = {**({"NEXUS_STORE": store} if store else {}), URL_SETTING: value}
    if route == "environment":
        return _run("backup.py", args, tmp_path, env=planted), "the environment"
    env_root = tmp_path / f"{out.name}-env-root"
    env_root.mkdir()
    (env_root / ".env").write_text("".join(f"{k}={v}\n" for k, v in planted.items()), encoding="utf-8")
    return _run("backup.py", args, tmp_path, env_root=env_root), (env_root / ".env").as_posix()


def _refusal(stdout: str) -> str:
    """A run's refusal: its REFUSED line and the lines after it (the ways out)."""
    lines = stdout.splitlines()
    at = next((i for i, line in enumerate(lines) if line.startswith("REFUSED: ")), None)
    return "" if at is None else "\n".join(lines[at:])


def _not_named_by_the_refusal(stdout: str, *, url: str, root: Path, read_from: str,
                              holds=tuple(SQLITE_STORE_FILES)) -> list[str]:
    """What the two-stores refusal must name and does not. On its REFUSED
    line, both stores: the setting, where it was read and its URL as a line
    may show it; the data root and the SQLite files it holds, and no file it
    does not hold; and that nothing was written. On the lines after it, the
    ways out: the explicit one, the migrated host's, and the SQLite store's
    where the data root holds one to back up (kb.db), or else that it holds
    none."""
    from scripts._readonly import redacted
    head, _newline, ways = _refusal(stdout).partition("\n")
    stores = {"the setting": URL_SETTING, "where the setting was read": read_from,
              "the hosted store, its secrets blanked": redacted(url), "the data root": root.resolve().as_posix(),
              **{f"the SQLite file {name}": name for name in holds},
              "that nothing was written": "Nothing was written"}
    out = {"the explicit way out": "--database-url URL", "the migrated host's way out": "migrate_sqlite_to_postgres.py",
           **({"the SQLite store's way out": f"unset {URL_SETTING}"} if "kb.db" in holds
              else {"that there is no SQLite store to back up in its place": "no kb.db"})}
    return [what for what, text in stores.items() if text not in head] + \
        [what for what, text in out.items() if text not in ways] + \
        [f"only the files the data root holds (it says it holds {name})" for name in SQLITE_STORE_FILES
         if name not in holds and name in head]


@contextlib.contextmanager
def _a_port_that_never_answers():
    """(port, connections): a local port that takes a connection and never
    answers it, and a count of the connections it has taken. A script that
    goes to a store named there is seen, on every platform (a listening port
    accepts at once: nothing here depends on how a closed one refuses), and
    libpq's connect_timeout in the URL bounds the wait of one that does."""
    taken: list = []
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        listener.setblocking(False)

        def connections() -> int:
            while True:
                try:
                    conn, _peer = listener.accept()
                except BlockingIOError:
                    return len(taken)
                taken.append(conn)

        yield listener.getsockname()[1], connections
    finally:
        for conn in taken:
            conn.close()
        listener.close()


@pytest.mark.parametrize("holds", [("archive.db", "kb.db"), ("kb.db",), ("archive.db",)], ids="+".join)
@pytest.mark.parametrize("route", ROUTES)
def test_k9_the_setting_and_a_sqlite_data_root_are_refused_by_name_before_anything_connects(route, holds, tmp_path):
    """One backup holds one store. NEXUS_BACKUP_DATABASE_URL names a hosted
    store (in the environment, or in the `.env` the script loads; NEXUS_STORE
    is sqlite beside it, as the delta check's probe had it: the script reads
    the data root, not that setting) and the data root holds kb.db and
    archive.db, or either alone (a half-cleared migrated host): taken as a
    hosted backup, the folder would not hold them. Refused by name, exit 2:
    both stores, the ways out and the migrated host's, the URL's password
    blanked; nothing is written, and nothing is asked of the hosted store
    (the port the URL names takes no connection).

    Then the way out the refusal names is taken. With the setting gone (set
    and empty, as compose hands it to a SQLite deployment; or absent) the
    same command backs up the SQLite store, and the folder holds its files.
    Where the data root holds no kb.db there is no SQLite store to back up:
    the refusal says that instead, and that run is refused by name.

    No server: this runs everywhere. Before the fix the script went to the
    hosted store for its backup (here the port takes its connection and
    never answers: `does not answer`); K9b is the same where it answers."""
    root = _sqlite_root(tmp_path, holds)
    before = {name: _sha256(root / name) for name in holds}
    out = tmp_path / "backups"
    tag = hashlib.sha1(f"k9 {route} {holds} {tmp_path}".encode()).hexdigest()[:12]
    with _a_port_that_never_answers() as (port, connections):
        url = f"postgresql://nexus_backup:Bk{tag}@127.0.0.1:{port}/nexus?connect_timeout=3"
        proc, read_from = _backup_by_the_setting(route, url, root, out, tmp_path, store="sqlite")
        asked = connections()
    said = proc.stdout + proc.stderr
    print(f"K9 [{route}; the data root holds {list(holds)}]: exit {proc.returncode}; connections the hosted "
          f"store's port took: {asked}; wrote {[p.name for p in _backup_folders(out)] or 'nothing'}\n"
          f"{_refusal(proc.stdout) or said}")
    assert proc.returncode == 2 and not out.exists(), said
    missing = _not_named_by_the_refusal(proc.stdout, url=url, root=root, read_from=read_from, holds=holds)
    assert not missing, f"the refusal does not name {missing}:\n{said}"
    assert asked == 0, f"the script went to the hosted store before it refused ({asked} connection(s))"
    assert tag not in said, "the URL's password is on a line"
    # The way out the refusal names, taken: the same command with the setting gone.
    unset = tmp_path / "backups-with-the-setting-gone"
    gone = _run("backup.py", ["--data-root", str(root), "--out", str(unset)], tmp_path,
                env={URL_SETTING: ""} if route == "environment" else None)
    folders = _backup_folders(unset)
    print(f"K9 with the setting {'set and empty' if route == 'environment' else 'absent'}: exit {gone.returncode}; "
          f"the backup folder holds {_held(folders[-1]) if folders else 'nothing (none was written)'}")
    if "kb.db" in holds:
        assert gone.returncode == 0 and len(folders) == 1 and set(holds) <= set(_held(folders[0])), \
            gone.stdout + gone.stderr
    else:
        assert gone.returncode == 2 and "kb.db is not a file" in gone.stdout and not unset.exists(), \
            gone.stdout + gone.stderr
    assert {name: _sha256(root / name) for name in holds} == before, "the data root was written"
    assert sorted(p.name for p in root.iterdir() if p.suffix == ".db") == sorted(holds), "the data root was written"


@pytest.mark.parametrize("route", ROUTES)
def test_k9b_the_setting_and_a_migrated_hosts_data_root_are_refused_where_the_hosted_store_answers(route, hosted,
                                                                                                tmp_path):
    """K9 against a hosted store that answers, which is where the defect was.
    The data root is the migrated host's: `migrate_sqlite_to_postgres.py`
    left kb.db and archive.db in place, and the backup service carries the
    setting. Refused by name, exit 2, nothing written, by either route.
    Before the fix: exit 0 and a folder holding MANIFEST.json, the snapshot
    and store.pgdump, and neither kb.db nor archive.db: a backup that reports
    success and does not hold the store its data root holds (law 5)."""
    root = hosted["source"]
    assert [name for name in SQLITE_STORE_FILES if (root / name).is_file()] == SQLITE_STORE_FILES
    before = {name: _sha256(root / name) for name in SQLITE_STORE_FILES}
    out = tmp_path / "backups"
    proc, read_from = _backup_by_the_setting(route, hosted["url"], root, out, tmp_path)
    said = proc.stdout + proc.stderr
    folders = _backup_folders(out)
    held = _held(folders[-1]) if folders else []
    print(f"K9b [{route}]: exit {proc.returncode}; the data root holds {SQLITE_STORE_FILES}; the backup folder "
          f"holds {held or 'nothing (none was written)'}\n{_refusal(proc.stdout) or proc.stdout.splitlines()[0]}")
    assert proc.returncode == 2 and not out.exists(), (
        f"a backup was taken that does not hold the store its data root holds: exit {proc.returncode}; the data "
        f"root holds {SQLITE_STORE_FILES}; the backup folder holds {held}")
    missing = _not_named_by_the_refusal(proc.stdout, url=hosted["url"], root=root, read_from=read_from)
    assert not missing, f"the refusal does not name {missing}:\n{said}"
    assert BK_PASSWORD not in said
    assert {name: _sha256(root / name) for name in SQLITE_STORE_FILES} == before, "the data root was written"


def test_k10_an_explicit_url_backs_up_the_hosted_store_and_declares_the_sqlite_files_it_leaves_out(hosted, tmp_path):
    """`--database-url` given explicitly on the same data root says which
    store is meant: the hosted backup is taken, and what it leaves out is
    declared, never silent. A line names kb.db and archive.db and the data
    root; the manifest carries them (`data_root_sqlite_files_not_in_this_backup`);
    the folder holds store.pgdump and neither file; and restore_verify.py
    says so again beside its verdict, which passes 5 of 5 on the hosted
    store."""
    root = hosted["source"]
    proc = _run("backup.py", ["--data-root", str(root), "--out", str(hosted["out"]),
                              "--database-url", hosted["url"]], tmp_path)
    said = proc.stdout + proc.stderr
    assert proc.returncode == 0, said
    (folder,) = _backup_folders(hosted["out"])
    manifest = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
    held = _held(folder)
    declared = [line for line in proc.stdout.splitlines() if line.startswith(LEFT_OUT_LINE)]
    print(f"K10: exit {proc.returncode}; the folder holds {held}; manifest {LEFT_OUT}: {manifest.get(LEFT_OUT)!r}; "
          f"declared: {declared or 'on no line'}")
    assert manifest["store"]["backend"] == "postgres" and "store.pgdump" in held, held
    assert not set(SQLITE_STORE_FILES) & set(held), held
    assert manifest.get(LEFT_OUT) == SQLITE_STORE_FILES, (
        f"the backup left {SQLITE_STORE_FILES} out and its manifest does not say so: {LEFT_OUT} is "
        f"{manifest.get(LEFT_OUT)!r}")
    assert len(declared) == 1 and all(name in declared[0] for name in SQLITE_STORE_FILES) \
        and root.resolve().as_posix() in declared[0] and LEFT_OUT in declared[0], (
        f"the backup left {SQLITE_STORE_FILES} out and no line says so:\n{proc.stdout}")
    assert BK_PASSWORD not in said
    verified = _run("restore_verify.py", [str(folder), "--database-url", hosted["server"]], tmp_path)
    again = [line for line in verified.stdout.splitlines() if line.startswith(LEFT_OUT_LINE)]
    print(f"K10 restore_verify: exit {verified.returncode}; {again or 'no line'}; {_summary(verified.stdout)}")
    assert verified.returncode == 0 and "5 of 5 checks passed" in verified.stdout, verified.stdout + verified.stderr
    assert len(again) == 1 and all(name in again[0] for name in SQLITE_STORE_FILES), (
        f"restore_verify.py does not say what the backup left out:\n{verified.stdout}")
    assert _scratch_databases() == [], "a scratch database was left behind"


def test_k11_the_setting_and_a_data_root_with_no_sqlite_store_is_the_hosted_backup_as_before(hosted, tmp_path):
    """Compose's hosted shape: the setting names the store and the data root
    holds chroma/, snapshots/ and the logs, no kb.db and no archive.db.
    Nothing is ambiguous: the hosted backup is taken by either route, the
    manifest says the data root held no SQLite store file (an empty list,
    never an absent field) and no line declares an omission."""
    root = hosted["root"]
    assert not [name for name in SQLITE_STORE_FILES if (root / name).exists()]
    for route in ROUTES:
        out = tmp_path / f"backups-by-{route}"
        proc, _read_from = _backup_by_the_setting(route, hosted["url"], root, out, tmp_path)
        said = proc.stdout + proc.stderr
        assert proc.returncode == 0, f"[{route}] {said}"
        (folder,) = _backup_folders(out)
        manifest = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
        held = _held(folder)
        print(f"K11 [{route}]: exit {proc.returncode}; the folder holds {held}; manifest {LEFT_OUT}: "
              f"{manifest.get(LEFT_OUT)!r}")
        assert manifest["store"]["backend"] == "postgres" and manifest["integrity_check"] == {"store.pgdump": "ok"}
        assert "store.pgdump" in held and "turn_metrics.jsonl" in held, held
        assert manifest.get(LEFT_OUT, "absent") == [], (
            f"[{route}] the manifest does not say the data root held no SQLite store file: {LEFT_OUT} is "
            f"{manifest.get(LEFT_OUT, 'absent')!r}")
        assert not [line for line in proc.stdout.splitlines() if line.startswith(LEFT_OUT_LINE)], proc.stdout
        assert BK_PASSWORD not in said


@pytest.mark.parametrize("given", [*ROUTES, "--database-url"])
def test_k12_a_value_that_is_not_a_postgres_url_is_refused_naming_where_it_was_given(given, tmp_path):
    """The delta check's D9: `REFUSED: --database-url must be a
    postgresql:// URL` was printed for a value read from
    NEXUS_BACKUP_DATABASE_URL, naming an option that was never given. The
    refusal names the setting and where it was read, or the option; none of
    the value is shown (a value that is not a URL can begin with its
    password); exit 2, nothing written. No server."""
    root = tmp_path / "dataroot"
    root.mkdir()
    out = tmp_path / "backups"
    tag = hashlib.sha1(f"k12 {given} {tmp_path}".encode()).hexdigest()[:12]
    value = f"mysql://nexus_backup:Bk{tag}@127.0.0.1:1/nexus"
    if given == "--database-url":
        proc = _run("backup.py", ["--data-root", str(root), "--out", str(out), "--database-url", value], tmp_path)
        named = "--database-url"
    else:
        proc, read_from = _backup_by_the_setting(given, value, root, out, tmp_path)
        named = f"{URL_SETTING} (read from {read_from})"
    said = proc.stdout + proc.stderr
    refused = [line for line in proc.stdout.splitlines() if line.startswith("REFUSED")]
    print(f"K12 [{given}]: exit {proc.returncode}; {refused}")
    assert proc.returncode == 2 and not out.exists(), said
    assert refused == [f"REFUSED: {named} must be a postgresql:// URL"], said
    assert tag not in said, "the value is on a line"
