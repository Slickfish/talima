"""W1 part 3 — every read-only open of a store goes through one function.

W0b fixed the read-only URI in `scripts/cp_b49_tally.py` and registered that
"five other scripts" built it by hand. W1's establish counted 36. Built by
hand (`f"file:{path}?mode=ro"`), a `#` or `?` in a folder name ended the path
early, so SQLite created a stray file beside that folder and the connection
could write it. A `%HH` was percent-decoded, so another existing database
was read. Through the real scripts that meant false verdicts about the
operator's store ("no `triples` table — is this a NEXUS kb.db?"), a report
on 0 triples written over a tracked file, and a `--selftest` that printed
"write refused" on the wrong file. The authorizer the three helpers set was
no barrier either: it allowed every pragma, so with `mode=ro` lost a query
could switch `query_only` off and write.

W1 part 5f (the verifier's lens 4) gave each barrier and each fix the case
that fails without it, and widened the guard from the URI's spelling to the
class of calls that open a connection.

U   `scripts/_readonly.py` on its own, barrier by barrier:
    - with no `mode=ro`, the writing pragmas are refused by barriers 2+3 and
      by the authorizer alone, and the file's bytes do not change;
    - barrier 2 (`query_only`) refuses the two writes the authorizer never
      sees, an incremental-blob write and `BEGIN IMMEDIATE`; the companion
      runs the authorizer alone and the blob write lands;
    - `ANALYZE` is refused on a store whose `sqlite_stat1` already exists,
      through the authorizer alone; the companion removes `SQLITE_ANALYZE`
      from the set and the file changes;
    - the selftest accepts only the refusal itself, and tells a wrong file
      apart: on an empty file, or a store without the probed table, it
      raises "no such table" instead of printing a refusal;
    - a missing file is an error, never a new database;
    - the caller's row type is kept;
    - a relative path names the file under the CWD (the `abspath` step);
    - the URI carries no authority for a UNC or an extended-length Windows
      path, checked against SQLite's own URI parser on every OS.
G   THE GUARD, two rules over the tracked population, each asserted through
    one function the companions share (`assert_rule_1`, `assert_rule_2`):
    - no tracked backend, script or root `.py` builds a `file:` URI (a
      literal, an f-string, a constant concatenation), passes `uri=` to any
      call, or passes `uri` to a SQLite open positionally (its 8th argument
      in 3.11, read from the signature) or inside `**` keywords, anywhere
      but `scripts/_readonly.py`;
    - no tracked script or root `.py` calls `sqlite3.connect`,
      `sqlite3.Connection` or `apsw.Connection` — through any alias — outside
      `scripts/_readonly.py` and `ANSWERED_FOR_READ_WRITE`, the deliberate
      read-write opens of temp copies, each with its reason, frozen both
      ways (a site gone from the code fails too).
    Companions plant what the guard's source does not spell: a positional
    `uri`, a `**` mapping, a `**NAME`, a scheme split as `"file" + ":"` with
    no `uri` keyword, a URI string with no connect at all, a plain connect
    of a store, a connect through `import sqlite3 as s` and through
    `from sqlite3 import connect as c`, `sqlite3.Connection(...)`, and an
    answered-for site that no longer exists; each asserts the rule's own
    assertion fails naming the plant.
B   THE BEHAVIOUR, on three OS: under folders named `C#`, `a?b` (not on
    Windows, which forbids `?`), `x%20y` beside a decoy `x y`, and
    `sp ace`, four migrated scripts give exactly the result they give under
    a plain folder, on stdout and stderr, and the plain result is first held
    to the fixture's known facts (`triples: 5`; `live rows : 4`; the exact
    selftest line) so a run that read no store or attempted no write cannot
    pass. They create nothing beside the folder and leave the store's bytes
    as they were. The four are a resolving script, a temp-copy script, a
    selftest script, and a relative-default script run from the awkward
    folder as its CWD.
S   The two instance scripts whose selftest was never run (`wc_window_count`,
    `cp_b49_tally`): on a planted store each prints the exact refusal line,
    and with the opener replaced by a read-write connect in the child each
    prints `SELFTEST FAILED` and exits 3.
C   `coactivation_eligibility.py` (a read-write open of the instance before
    W1) runs its forecast path on a planted store that holds co-activation
    rows, through the shared opener, whose connection refuses a write; the
    store's bytes are unchanged.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import os
import re
import shutil
import sqlite3
from tests import _proc
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

import pytest

from scripts import _readonly as RO
from tests._readonly_opens import CONNECT, enclosing_function, open_calls, sqlite_opens
from tests._tracked import tracked

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "pre_run_data" / "kb.db"
THE_OPENER = "scripts/_readonly.py"
REFUSAL_LINE = "selftest: write refused (DatabaseError: not authorized)"
WRITING_PRAGMAS = ("PRAGMA query_only = 0", "PRAGMA user_version = 7",
                   "PRAGMA journal_mode = WAL", "INSERT INTO t VALUES (1)", "ANALYZE")


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def _copy_fixture(where: Path) -> Path:
    where.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FIXTURE, where)
    return where


def _authorizer_alone(db: Path) -> sqlite3.Connection:
    """Barrier 3 and nothing else: no mode=ro, no query_only."""
    conn = sqlite3.connect(db)
    conn.set_authorizer(RO._authorize)
    return conn


def _analyze_first(db: Path) -> str:
    """ANALYZE on a read-write connection, so `sqlite_stat1` exists and is
    populated; returns the md5 of the file in that state."""
    c = sqlite3.connect(db)
    c.execute("ANALYZE")
    c.commit()
    rows = c.execute("SELECT COUNT(*) FROM sqlite_stat1").fetchone()[0]
    c.close()
    assert rows > 0, "ANALYZE wrote no statistics: the case the fix is for is not planted"
    return _md5(db)


# ---------------------------------------------------------------------------
# U — the opener, barrier by barrier
# ---------------------------------------------------------------------------

def test_u_without_mode_ro_the_writing_pragmas_are_refused_and_the_file_is_unchanged(tmp_path):
    """Barriers 2+3 (`refuse_writes`) and the authorizer alone, each on a
    connection with no mode=ro. The pre-W1 authorizer allowed every pragma:
    `PRAGMA query_only = 0` then `PRAGMA user_version = 7` ran and the file
    changed (W1 part 5f, N1: the pre-fix run printed in the evidence)."""
    db = tmp_path / "plain.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE t (x)")
    before = _md5(db)
    for label, conn in (("barriers 2+3", RO.refuse_writes(sqlite3.connect(db))),
                        ("authorizer alone", _authorizer_alone(db))):
        for sql in WRITING_PRAGMAS:
            with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                conn.execute(sql)
        assert conn.execute("PRAGMA table_info(t)").fetchall(), label
        assert conn.execute("PRAGMA user_version").fetchone() == (0,), label
        conn.close()
        assert _md5(db) == before, f"{label}: a refused pragma still wrote the file"


def test_u_barrier_2_refuses_the_writes_the_authorizer_cannot_see(tmp_path):
    """An incremental-blob write and BEGIN IMMEDIATE reach SQLite with no
    statement the authorizer classes as a write. `PRAGMA query_only = 1`
    refuses both, on a connection with no mode=ro, and the bytes hold."""
    db = tmp_path / "blob.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE t (x BLOB)")
        c.execute("INSERT INTO t VALUES (zeroblob(8))")
    before = _md5(db)
    conn = RO.refuse_writes(sqlite3.connect(db))          # NO mode=ro: barrier 1 absent
    if not hasattr(conn, "blobopen"):
        pytest.skip(f"Connection.blobopen is Python 3.11+; this is {sys.version.split()[0]}")
    with pytest.raises(sqlite3.OperationalError, match="attempt to write a readonly database"):
        blob = conn.blobopen("t", "x", 1, readonly=False)
        blob.write(b"12345678")
    with pytest.raises(sqlite3.OperationalError, match="attempt to write a readonly database"):
        conn.execute("BEGIN IMMEDIATE")
    assert conn.execute("SELECT x FROM t").fetchone() == (bytes(8),)
    conn.close()
    assert _md5(db) == before, "barrier 2 let a write through"


def test_u_companion_the_authorizer_alone_lets_a_blob_write_land(tmp_path):
    """COMPANION (the consequence): barrier 3 without barrier 2. The blob
    write lands and the file changes; BEGIN IMMEDIATE runs."""
    db = tmp_path / "blob.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE t (x BLOB)")
        c.execute("INSERT INTO t VALUES (zeroblob(8))")
    before = _md5(db)
    conn = _authorizer_alone(db)
    if not hasattr(conn, "blobopen"):
        pytest.skip(f"Connection.blobopen is Python 3.11+; this is {sys.version.split()[0]}")
    blob = conn.blobopen("t", "x", 1, readonly=False)
    blob.write(b"12345678")
    blob.close()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("ROLLBACK")
    conn.close()
    assert _md5(db) != before, "the companion did not reach the file: the blob write did not land"
    assert sqlite3.connect(db).execute("SELECT x FROM t").fetchone() == (b"12345678",)


def test_u_analyze_is_refused_where_the_statistics_table_exists(tmp_path):
    """On a fresh store SQLite itself refuses ANALYZE under query_only, so the
    first test of it proved nothing (MF1 b). Here `sqlite_stat1` exists and
    is populated before the authorizer, alone, is asked."""
    db = _copy_fixture(tmp_path / "kb.db")
    before = _analyze_first(db)
    conn = _authorizer_alone(db)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        conn.execute("ANALYZE")
    conn.close()
    assert _md5(db) == before, "a refused ANALYZE still wrote sqlite_stat1"


def test_u_companion_without_sqlite_analyze_in_the_set_analyze_runs_and_the_file_changes(tmp_path):
    """COMPANION: the pre-W1 set, SQLITE_ANALYZE removed, otherwise the same
    authorizer. ANALYZE runs and the store's bytes change."""
    db = _copy_fixture(tmp_path / "kb.db")
    before = _analyze_first(db)
    without = RO.WRITE_ACTIONS - {sqlite3.SQLITE_ANALYZE}

    def pre_w1_set(action, arg1, arg2, db_, trigger):
        if action in without:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA:          # the pragma rule, unchanged
            return RO._authorize(action, arg1, arg2, db_, trigger)
        return sqlite3.SQLITE_OK
    conn = sqlite3.connect(db)
    conn.set_authorizer(pre_w1_set)
    conn.execute("ANALYZE")
    conn.commit()
    conn.close()
    assert _md5(db) != before, "the companion did not reach the file: ANALYZE did not run"


def test_u_the_selftest_accepts_only_the_refusal(tmp_path):
    db = _copy_fixture(tmp_path / "kb.db")
    ro = RO.open_read_only(db)
    assert RO.assert_write_refused(ro, table="lineage_records") == "DatabaseError: not authorized"
    ro.close()
    barrier_1_only = sqlite3.connect(RO.read_only_uri(db), uri=True)   # no authorizer: SQLite's own refusal
    assert RO.assert_write_refused(barrier_1_only, table="lineage_records") == \
        "OperationalError: attempt to write a readonly database"
    barrier_1_only.close()
    rw = sqlite3.connect(db)
    with pytest.raises(AssertionError, match="a write succeeded"):
        RO.assert_write_refused(rw, table="lineage_records")
    rw.close()
    ro = RO.open_read_only(db)
    with pytest.raises(ValueError, match="bare identifier"):
        RO.assert_write_refused(ro, table="lineage_records; DROP TABLE t")
    ro.close()
    assert _md5(db) == _md5(FIXTURE), "the probe changed the store"


def test_u_the_selftest_tells_a_wrong_file_apart(tmp_path):
    """The probe resolves its table before the authorizer is consulted, so a
    misopened file (an empty one; a store without the table) raises instead
    of printing a refusal. The CREATE TABLE probe this replaces was refused
    on any file at all (SF6)."""
    empty = tmp_path / "C"                       # the stray file a hand-built URI under C# created
    empty.write_bytes(b"")
    conn = RO.refuse_writes(sqlite3.connect(empty))
    with pytest.raises(AssertionError, match="not as a refusal") as e:
        RO.assert_write_refused(conn, table="lineage_records")
    assert "no such table: lineage_records" in str(e.value)
    conn.close()
    other = tmp_path / "other.db"
    with sqlite3.connect(other) as c:
        c.execute("CREATE TABLE t (x)")
    with pytest.raises(AssertionError, match="no such table: lineage_records"):
        RO.assert_write_refused(RO.open_read_only(other), table="lineage_records")
    assert empty.read_bytes() == b"", "the probe wrote the empty file"


def test_u_a_missing_file_is_an_error_never_a_new_database(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        RO.open_read_only(tmp_path / "absent.db")
    assert not (tmp_path / "absent.db").exists()


def test_u_the_callers_row_type_is_kept(tmp_path):
    db = tmp_path / "r.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE t (x)")
        c.execute("INSERT INTO t VALUES (1)")
    a = RO.open_read_only(db)
    assert type(a.execute("SELECT x FROM t").fetchone()) is sqlite3.Row
    a.close()
    b = RO.open_read_only(db, row_factory=None)
    assert b.execute("SELECT x FROM t").fetchone() == (1,)
    b.close()


def test_u_a_relative_path_names_the_file_under_the_cwd(tmp_path, monkeypatch):
    """The `abspath` step (SF3's `resolve()` witness, M9): `data/kb.db` is the
    store under the CWD, the file it names everywhere else."""
    db = _copy_fixture(tmp_path / "data" / "kb.db")
    monkeypatch.chdir(tmp_path)
    assert RO.read_only_uri(Path("data") / "kb.db") == RO.uri_for_absolute(db) + "?mode=ro"
    conn = RO.open_read_only(Path("data") / "kb.db")
    assert conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0] == 5
    conn.close()
    # `uri_for_absolute` itself takes an absolute path only, as `as_uri()` does.
    with pytest.raises(ValueError, match="relative path"):
        RO.uri_for_absolute(PureWindowsPath(r"data\kb.db"))
    with pytest.raises(ValueError, match="relative path"):
        RO.uri_for_absolute(PurePosixPath("data/kb.db"))


WINDOWS_SHAPES = [
    # (the path, the URI it must give)
    (PureWindowsPath(r"C:\Users\op\NEXUS\data\kb.db"), "file:///C:/Users/op/NEXUS/data/kb.db"),
    (PureWindowsPath(r"Z:\x y\C#\kb.db"), "file:///Z:/x%20y/C%23/kb.db"),            # a mapped drive, kept a drive
    (PureWindowsPath(r"\\nas\share\NEXUS\data\kb.db"), "file:////nas/share/NEXUS/data/kb.db"),
    (PureWindowsPath(r"\\?\C:\Users\op\NEXUS\data\kb.db"), "file:///C:/Users/op/NEXUS/data/kb.db"),
    (PureWindowsPath(r"\\?\UNC\nas\share\NEXUS\data\kb.db"), "file:////nas/share/NEXUS/data/kb.db"),
]


@pytest.mark.parametrize("path,expected", WINDOWS_SHAPES, ids=[str(p) for p, _ in WINDOWS_SHAPES])
def test_u_the_uri_carries_no_authority_for_every_windows_path_shape(path, expected):
    """SF7. `as_uri()` puts the host of a UNC path, and `%3F` of an
    extended-length path, in the URI's authority, which SQLite refuses by
    name. `uri_for_absolute` keeps the authority empty on every shape and is
    byte-identical to `as_uri()` where that already had none."""
    uri = RO.uri_for_absolute(path)
    assert uri == expected
    assert urlsplit(uri).netloc == "", uri
    if len(path.drive) == 2:
        assert uri == path.as_uri(), "the established drive-letter form moved"
    else:
        assert urlsplit(path.as_uri()).netloc != "", "as_uri() grew no authority here: the case is moot"
    assert RO.uri_for_absolute(PurePosixPath("/home/op/C#/kb.db")) == PurePosixPath("/home/op/C#/kb.db").as_uri()


def test_u_sqlite_refuses_a_planted_authority_and_parses_the_fixed_forms():
    """SQLite's own URI parser, on every OS: an authority is refused by name
    before any file is touched; the forms `uri_for_absolute` builds are
    parsed as paths (the error is then about the absent file, never the
    authority). The UNC form is opened off Windows only: on Windows the path
    `//nas/share/...` is a network lookup, said here rather than run."""
    with pytest.raises(sqlite3.OperationalError) as e:
        sqlite3.connect("file://nas/share/NEXUS/data/kb.db?mode=ro", uri=True)
    assert str(e.value) == "invalid uri authority: nas"
    with pytest.raises(sqlite3.OperationalError) as e:
        sqlite3.connect(PureWindowsPath(r"\\?\C:\x\kb.db").as_uri() + "?mode=ro", uri=True)
    assert str(e.value) == "invalid uri authority: %3F"
    fixed = [RO.uri_for_absolute(PureWindowsPath(r"\\?\C:\nexus-w1-absent-5f\kb.db")) + "?mode=ro"]
    if sys.platform != "win32":
        fixed.append(RO.uri_for_absolute(PureWindowsPath(r"\\nas\share\NEXUS\data\kb.db")) + "?mode=ro")
    else:
        print("SF7: the UNC form is not opened on win32 (a network lookup); its parse is asserted by urlsplit")
    for uri in fixed:
        with pytest.raises(sqlite3.OperationalError) as e:
            sqlite3.connect(uri, uri=True)
        assert str(e.value) == "unable to open database file", (uri, str(e.value))
        assert "authority" not in str(e.value)


# ---------------------------------------------------------------------------
# G — one place builds the URI, and one place opens a connection
# ---------------------------------------------------------------------------

def _leading_constant(node) -> "str | None":
    """The string a URI expression begins with: a constant, an f-string's
    first part, or the folded left spine of a `+` chain of constants
    (`"file" + ":" + str(p)` -> `file:`)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        return _leading_constant(node.values[0])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _leading_constant(node.left)
        if left is None:
            return None
        right = _leading_constant(node.right)
        return left + (right or "")
    return None


def _starts_uri(node) -> bool:
    lead = _leading_constant(node)
    return lead is not None and lead.startswith("file:")


def _is_false(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def uri_position() -> int:
    """Where `uri` sits among `sqlite3.connect`'s positional parameters, from
    the signature this interpreter ships (1-based: the 8th in 3.11, after
    database, timeout, detect_types, isolation_level, check_same_thread,
    factory and cached_statements; `sqlite3.Connection` takes the same).
    Read, not listed, so a build that moves it moves the rule with it.

    Read from `__text_signature__`, the Argument Clinic text, because
    `inspect.signature` refuses the function: its default `factory=
    ConnectionType` is no literal ("builtin has invalid signature")."""
    text = sqlite3.connect.__text_signature__
    assert text, "sqlite3.connect carries no text signature in this interpreter; re-derive this rule"
    positional = []
    for part in text.strip("()").split(","):
        name = part.strip().split("=")[0].strip()
        if name in ("$module", "/", ""):
            continue
        if name.startswith("*"):              # `*` or `*args`: every later name is keyword-only
            break
        positional.append(name)
    assert "uri" in positional, (
        f"uri is not a positional parameter of this interpreter's sqlite3.connect ({text}): "
        f"the positional form cannot be written, so this rule has no object; re-derive it")
    return positional.index("uri") + 1


def uri_violations(sources: dict[str, str]) -> list[str]:
    """Rule 1: no `file:` URI is built, no call carries `uri=`, and no SQLite
    open passes `uri` positionally or inside `**` keywords, outside the
    opener. Over backend, scripts and root."""
    found = []
    position = uri_position()
    for path, src in sorted(sources.items()):
        if path == THE_OPENER:
            continue
        tree = ast.parse(src)
        connects = {node for kind, node, _spelled in open_calls(tree) if kind == CONNECT}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and _starts_uri(node):
                found.append(f"{path}:{node.lineno} a file: URI literal")
            elif isinstance(node, ast.JoinedStr) and _starts_uri(node):
                found.append(f"{path}:{node.lineno} a file: URI f-string")
            elif isinstance(node, ast.BinOp) and _starts_uri(node):
                found.append(f"{path}:{node.lineno} a file: URI built by concatenation")
            elif isinstance(node, ast.Call):
                if any(k.arg == "uri" and not _is_false(k.value) for k in node.keywords):
                    found.append(f"{path}:{node.lineno} a call with uri= (a hand-built URI open)")
                if node in connects:
                    if any(k.arg is None for k in node.keywords):
                        found.append(f"{path}:{node.lineno} a SQLite open with ** keywords "
                                     f"(uri= cannot be resolved)")
                    if len(node.args) >= position and not _is_false(node.args[position - 1]):
                        found.append(f"{path}:{node.lineno} a SQLite open with uri passed "
                                     f"positionally (argument {position})")
    # An f-string's first part is also a Constant: keep one finding per line.
    return sorted(set(found))


def assert_rule_1(sources: dict[str, str]) -> None:
    """The guard's assertion, shared with its companions."""
    found = uri_violations(sources)
    assert not found, "a file: URI or a uri argument outside the opener:\n  " + "\n  ".join(found)


# The deliberate read-write opens, by (file, enclosing function), each with
# its reason. Frozen both ways: a site here that the code no longer has
# fails the guard too.
ANSWERED_FOR_READ_WRITE = {
    ("scripts/verify_packet_f.py", "convergence_and_integrity"):
        "a disposable temp copy of the store, migrated and rebalanced ten times on "
        "purpose; the original is never opened by this function",
    ("start.py", "verify_chain_read_only"):
        "the destination of `src.backup(dst)`: a temp copy of the archive that the "
        "chain check reads and removes; the archive itself is opened through open_read_only",
    # W3 part 4 — the backup: the source is opened through open_read_only and
    # copied with SQLite's online backup API into a fresh file the script
    # owns; the two opens below are of that copy.
    ("scripts/backup.py", "copy_database"):
        "the destination of `source.backup(destination)`: the backup copy being written, a file "
        "this script creates; the source database is opened through open_read_only",
    ("scripts/backup.py", "integrity_check"):
        "PRAGMA integrity_check on the backup copy this script just wrote (the read-only open "
        "refuses the pragma on a source, by design); never the source",
    # W3b part 4 — the migration to Postgres: the source is opened through
    # open_read_only and copied into the script's scratch folder; the three
    # opens below are of that copy and of the migration's TARGET.
    ("scripts/migrate_sqlite_to_postgres.py", "snapshot"):
        "the destination of `source.backup(destination)`: the scratch copy this script creates and owns; "
        "the source database is opened through open_read_only",
    ("scripts/migrate_sqlite_to_postgres.py", "check_target"):
        "the migration's target counted on a read-only session (read_only=True) before anything is "
        "written: the script's destination, never its source",
    ("scripts/migrate_sqlite_to_postgres.py", "copy_into"):
        "the migration's target written in one transaction under its single-writer lock: the script's "
        "destination, never its source",
    # W3b part 5 — the hosted backup's restore verification: read-only
    # sessions (read_only=True) on the scratch databases this script restored
    # the dump into and drops afterwards; never the instance's store, which
    # the script never opens.
    ("scripts/restore_verify.py", "pg_digests"):
        "R5's digests of a scratch database this script restored, on a read-only session",
    ("scripts/restore_verify.py", "r2_pg_counts"):
        "R2's counts on a scratch database this script restored, on a read-only session",
    ("scripts/restore_verify.py", "r3_pg_chain"):
        "R3's chain head on a scratch database this script restored, on a read-only session",
    # The W3b close's F19 — the drop's own session, read-only (read_only=True),
    # on the URL's own database: the one dropdb names as its maintenance
    # database (under compose the instance's `nexus`, as the backup role). It
    # reads pg_database and pg_stat_activity and ends this role's own sessions
    # on a scratch database (pg_terminate_backend); no store table is read.
    ("scripts/restore_verify.py", "server_session"):
        "the drop's catalogue reads, and pg_terminate_backend of this role's own sessions on a scratch "
        "database, on a read-only session on the URL's own database; no store table is read or written",
}


def open_violations(sources: dict[str, str],
                    answered_for: dict = ANSWERED_FOR_READ_WRITE) -> list[str]:
    """Rule 2: no call that opens a SQLite connection (`sqlite3.connect`,
    `sqlite3.Connection`, `apsw.Connection`, through any import alias) in
    the tracked scripts and root files, outside the opener and the
    answered-for list. Over scripts and root only: the backend opens its
    stores read-write by design."""
    found = []
    seen_keys = set()
    for path, src in sorted(sources.items()):
        if path == THE_OPENER:
            continue
        tree = ast.parse(src)
        for kind, node, spelled in open_calls(tree):
            if kind != CONNECT:
                continue
            key = (path, enclosing_function(tree, node))
            if key in answered_for:
                seen_keys.add(key)
                continue
            found.append(f"{path}:{node.lineno} a direct SQLite open ({spelled}) in "
                         f"{key[1]}: not open_read_only and not answered for")
    for key in sorted(set(answered_for) - seen_keys):
        found.append(f"{key[0]}: the answered-for read-write open in {key[1]} no longer "
                     f"exists; remove its entry")
    return sorted(set(found))


def assert_rule_2(sources: dict[str, str], answered_for: dict = ANSWERED_FOR_READ_WRITE) -> None:
    """The guard's assertion, shared with its companions."""
    found = open_violations(sources, answered_for)
    assert not found, "a SQLite open outside the opener and the answered-for list:\n  " + "\n  ".join(found)


def _population() -> dict[str, str]:
    files = [p for p in tracked("backend/**/*.py", "backend/*.py", "scripts/*.py", "*.py")
             if not p.relative_to(ROOT).as_posix().startswith("tests/")]
    return {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in files}


def _scripts_and_root(sources: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in sources.items() if k.startswith("scripts/") or "/" not in k}


def test_g_only_the_shared_opener_builds_a_read_only_uri():
    sources = _population()
    print(f"G population: {len(sources)} files (backend, scripts, root); uri is "
          f"sqlite3.connect's positional argument {uri_position()} on {sys.version.split()[0]}")
    assert len(sources) > 140 and THE_OPENER in sources and "start.py" in sources
    assert_rule_1(sources)


def test_g_only_the_shared_opener_opens_a_connection_in_scripts_and_root():
    sources = _scripts_and_root(_population())
    print(f"G connect population: {len(sources)} files (scripts, root); answered for: "
          f"{sorted(ANSWERED_FOR_READ_WRITE)}")
    assert len(sources) > 50 and THE_OPENER in sources and "start.py" in sources
    assert "scripts/coactivation_eligibility.py" in sources
    assert_rule_2(sources)


PLANTED = "scripts/planted_probe.py"

# (the planted source, what rule 1's failure must name)
URI_COMPANIONS = [
    ('conn = sqlite3.connect("file:{}?mode=ro".format(p), uri=True)\n', "a file: URI literal"),
    ('conn = sqlite3.connect("file:" + str(p) + "?mode=ro", uri=True)\n', "built by concatenation"),
    ('conn = sqlite3.connect("file:%s?mode=ro" % p, uri=True)\n', "a file: URI literal"),
    ('conn = sqlite3.connect(r"file:data/kb.db?mode=ro", uri=True)\n', "a file: URI literal"),
    ('conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)\n', "a file: URI f-string"),
    ('uri = build(p)\nconn = sqlite3.connect(uri, uri=True)\n', "a call with uri="),
    # part 5f: the forms the old companions never planted
    ('URI = "file:data/kb.db?mode=ro"\n', "a file: URI literal"),                              # no connect at all
    ('conn = sqlite3.connect("file" + ":" + str(p) + "?mode=ro")\n', "built by concatenation"),  # split scheme, no uri keyword
    ('import sqlite3\nconn = sqlite3.connect(str(p), **{"uri": True})\n', "** keywords"),      # a ** mapping
    ('import sqlite3\nconn = sqlite3.connect(str(p), **OPTS)\n', "** keywords"),               # a ** name
    ('import sqlite3\nconn = sqlite3.connect(uri, 5.0, 0, None, True, None, 128, True)\n',    # uri positional
     "uri passed positionally"),
    ('import sqlite3 as s\nconn = s.Connection(uri, 5.0, 0, None, True, None, 128, True)\n',  # ...on the constructor, aliased
     "uri passed positionally"),
]


@pytest.mark.parametrize("planted,named", URI_COMPANIONS, ids=[p.strip().splitlines()[-1] for p, _ in URI_COMPANIONS])
def test_g_companions_every_uri_form_fails_rule_1_by_name(planted, named):
    with pytest.raises(AssertionError, match=re.escape(f"{PLANTED}:")) as e:
        assert_rule_1({PLANTED: planted})
    assert named in str(e.value), str(e.value)


def test_g_companion_rule_1_passes_uri_false_and_a_full_positional_call_without_it():
    """The rule's edge, so the positional check is known to read the argument
    it claims: `uri=False` and the 8th argument `False` are the default."""
    assert_rule_1({PLANTED: 'import sqlite3\nconn = sqlite3.connect(p, 5.0, 0, None, True, None, 128, False)\n'})
    assert_rule_1({PLANTED: 'import sqlite3\nconn = sqlite3.connect(p, uri=False)\n'})
    assert_rule_1({PLANTED: 'import sqlite3\nconn = sqlite3.connect(p, 5.0, 0, None, True, None, 128)\n'})


OPEN_COMPANIONS = [
    'import sqlite3\nconn = sqlite3.connect(db)\n',                                          # M23's shape
    'import sqlite3\nconn = sqlite3.connect(uri, 5.0, 0, None, True, None, 128, True)\n',    # uri positional
    'import sqlite3\nconn = sqlite3.connect(str(p), **{"uri": True})\n',
    'import sqlite3\nconn = sqlite3.connect("file" + ":" + str(p) + "?mode=ro")\n',
    'import sqlite3 as s\nconn = s.connect(db)\n',
    'from sqlite3 import connect as c\nconn = c(db)\n',
    'from sqlite3 import Connection\nconn = Connection(db)\n',
    'import sqlite3\nconn = sqlite3.Connection(db)\n',
    'import sqlite3.dbapi2 as d\nconn = d.connect(db)\n',
    'import sqlite3.dbapi2\nconn = sqlite3.dbapi2.connect(db)\n',
    'import apsw\nconn = apsw.Connection(db)\n',
    'def f(db):\n    import sqlite3\n    return sqlite3.connect(db)\n',                      # inside a function
    # W3b part 4: the store interface's connect, under the spellings the guard's table does not write
    'from backend import store as s\nconn = s.connect(db)\n',
    'import backend.store as bs\nconn = bs.connect(db)\n',
    'from backend.store import connect as open_store\nconn = open_store(db)\n',
    'def f(db):\n    import backend.kb.schema\n    return backend.kb.schema.connect(db)\n',
]


@pytest.mark.parametrize("planted", OPEN_COMPANIONS, ids=[p.strip().splitlines()[-1] for p in OPEN_COMPANIONS])
def test_g_companions_every_open_form_fails_rule_2_by_name(planted):
    # answered_for is empty here: the planted population has none of the
    # real list's files, and the freeze (a listed site with no code) is the
    # next companion's.
    with pytest.raises(AssertionError, match=re.escape(f"{PLANTED}:")) as e:
        assert_rule_2({PLANTED: planted}, {})
    assert "a direct SQLite open" in str(e.value), str(e.value)
    # ...and the same open through the shared opener passes the rule.
    assert_rule_2({PLANTED: "from scripts._readonly import open_read_only\nconn = open_read_only(db)\n"}, {})


def test_g_companion_an_answered_for_site_that_vanished_is_named():
    """Frozen both ways: the list cannot rot into fiction."""
    with pytest.raises(AssertionError, match=re.escape(
            f"{PLANTED}: the answered-for read-write open in gone no longer exists; remove its entry")):
        assert_rule_2({PLANTED: "x = 1\n"}, {(PLANTED, "gone"): "it was here once"})
    # ...and an answered-for site that IS there passes, under its own function only.
    src = "import sqlite3\ndef copy_work(p):\n    return sqlite3.connect(p)\ndef other(p):\n    return sqlite3.connect(p)\n"
    assert_rule_2({PLANTED: src.split("def other")[0]}, {(PLANTED, "copy_work"): "a temp copy"})
    with pytest.raises(AssertionError, match="in other:"):
        assert_rule_2({PLANTED: src}, {(PLANTED, "copy_work"): "a temp copy"})


def test_g_the_opener_itself_is_the_one_connect_and_is_exempt():
    src = (ROOT / THE_OPENER).read_text(encoding="utf-8")
    opens = sqlite_opens(src)
    # The one SQLite connect (the read-only URI), and the store interface's
    # connect in the URL forms (W3b part 3: `read_only=True`, the hosted
    # store's read-only session), each by its function.
    tree = ast.parse(src)
    by_function = sorted((enclosing_function(tree, node), spelled) for kind, node, spelled in open_calls(tree))
    assert by_function == [("open_read_only", "sqlite3.connect"), ("open_read_only_postgres", "connect"),
                           ("store_exists", "connect")], by_function
    assert all(kind == CONNECT for kind, _line, _spelled in opens)
    assert_rule_1({THE_OPENER: src})
    assert_rule_2({THE_OPENER: src}, {})
    # ...and the same source under any other name is named by both rules.
    with pytest.raises(AssertionError, match="a call with uri="):
        assert_rule_1({"scripts/copy_of_opener.py": src})
    with pytest.raises(AssertionError, match="a direct SQLite open"):
        assert_rule_2({"scripts/copy_of_opener.py": src}, {})


# ---------------------------------------------------------------------------
# B — awkward folders, through real scripts, on every platform
# ---------------------------------------------------------------------------

AWKWARD = ["C#", "x%20y", "sp ace"] + ([] if sys.platform == "win32" else ["a?b"])

# (script, its args given the store path, the plain run's exit code, the
# facts its plain output must carry): a resolving script, a temp-copy script
# (its copy lands under TMPDIR, which is awkward too), a selftest one.
# predicate_distribution exits 1 by contract on this fixture: "nothing to
# cluster" (every claim predicate appears once), a verdict, not a failure.
SCRIPTS = [
    ("verify_packet_k.py", lambda db: ["--kb", str(db)], 0,
     ["triples: 5", "lineage records: 5 (5 sealed)", "stored rows the gate would reject : 0 / 5"]),
    ("predicate_distribution.py", lambda db: ["--db", str(db)], 1,
     ["live rows                  : 4", "claim rows                 : 4", "UNCHANGED"]),
    ("as2_instance_windows.py", lambda db: ["--db", str(db), "--selftest"], 0,
     [REFUSAL_LINE, "(records: 5)"]),
]


def _run(script, args, cwd, tmpdir):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               TMPDIR=str(tmpdir), TEMP=str(tmpdir), TMP=str(tmpdir),
               OLLAMA_URL="http://127.0.0.1:9", NEXUS_ENV_ROOT=str(cwd))
    proc = _proc.run([sys.executable, str(ROOT / "scripts" / script), *args],
                          cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    return proc.returncode, proc.stdout, proc.stderr


def _setup(base: Path, folder: str) -> tuple[Path, Path]:
    where = base / folder
    where.mkdir(parents=True)
    shutil.copy2(FIXTURE, where / "kb.db")
    tmpdir = base / f"t-{folder}"
    tmpdir.mkdir()
    return where / "kb.db", tmpdir


def _normalise(text: str, store_dir: Path, tmpdir: Path) -> list[str]:
    out = text.replace(store_dir.resolve().as_posix(), "<STORE>").replace(
        str(store_dir), "<STORE>").replace(str(tmpdir), "<TMP>")
    return [l for l in out.splitlines() if "<STORE>" not in l and "<TMP>" not in l]


@pytest.mark.parametrize("script,args,rc_expected,facts", SCRIPTS, ids=[s[0] for s in SCRIPTS])
def test_b_an_awkward_folder_gives_the_plain_folders_result(tmp_path, script, args, rc_expected, facts):
    plain_db, plain_tmp = _setup(tmp_path / "plain", "store")
    rc_plain, out_plain, err_plain = _run(script, args(plain_db), tmp_path / "plain", plain_tmp)
    # The absolute anchor first (SF4): the plain run read THIS store and did
    # what the script claims, or the equality below compares two failures.
    assert rc_plain == rc_expected, f"{script} plain run exited {rc_plain}:\n{out_plain}\n{err_plain}"
    for fact in facts:
        assert fact in out_plain, f"{script} plain run does not carry {fact!r}:\n{out_plain}"
    if script.startswith("as2"):
        assert out_plain.splitlines()[1] == REFUSAL_LINE, out_plain.splitlines()[:3]
    for folder in AWKWARD:
        base = tmp_path / f"case{AWKWARD.index(folder)}"
        db, tmpdir = _setup(base, folder)
        if folder == "x%20y":                     # what a decoded %20 would name
            decoy = base / "x y"
            decoy.mkdir()
            sqlite3.connect(decoy / "kb.db").close()
        md5 = _md5(db)
        before = sorted(p.name for p in base.iterdir())
        rc, out, err = _run(script, args(db), base, tmpdir)
        after = sorted(p.name for p in base.iterdir())
        print(f"B ({sys.platform}) {script} under {folder!r}: exit {rc} (plain {rc_plain}); "
              f"new beside it: {sorted(set(after) - set(before))}")
        assert after == before, (
            f"{script} under {folder!r} created {sorted(set(after) - set(before))} beside "
            f"the folder: the URI lost mode=ro and SQLite opened another file")
        assert _md5(db) == md5, f"{script} changed the store's bytes"
        assert rc == rc_plain, (
            f"{script} under {folder!r} exited {rc}, under a plain folder {rc_plain}:\n{out}\n{err}")
        assert _normalise(out, db.parent, tmpdir) == _normalise(out_plain, plain_db.parent, plain_tmp), (
            f"{script} under {folder!r} reported something else than under a plain folder")
        assert _normalise(err, db.parent, tmpdir) == _normalise(err_plain, plain_db.parent, plain_tmp), (
            f"{script} under {folder!r} wrote something else to stderr than under a plain folder:\n{err}")


def test_b_the_relative_default_script_reads_the_store_under_an_awkward_cwd(tmp_path):
    """The fourth migrated script S3.7 names (SF3): `count_traces.py` opens
    `data/kb.db` relative to its CWD. Run from each awkward folder as the
    CWD, it reads that folder's store (`lineage records: 5`), creates nothing
    beside it and leaves the bytes alone; under `x%20y` a decoy `x y/data/kb.db`
    with no records would read `lineage records: 0`."""
    plain = tmp_path / "plain"
    _copy_fixture(plain / "data" / "kb.db")
    rc_plain, out_plain, err_plain = _run("count_traces.py", [], plain, plain)
    assert rc_plain == 0, out_plain + err_plain
    assert "lineage records: 5" in out_plain, out_plain
    for folder in AWKWARD:
        base = tmp_path / f"rel{AWKWARD.index(folder)}"
        cwd = base / folder
        db = _copy_fixture(cwd / "data" / "kb.db")
        if folder == "x%20y":
            decoy = base / "x y" / "data"
            decoy.mkdir(parents=True)
            with sqlite3.connect(decoy / "kb.db") as c:
                c.execute("CREATE TABLE lineage_records (lineage_id TEXT, injected_json TEXT)")
                c.execute("CREATE TABLE triples (id INTEGER, relation TEXT)")
        md5 = _md5(db)
        before = sorted(p.name for p in cwd.iterdir()) + sorted(p.name for p in (cwd / "data").iterdir())
        rc, out, err = _run("count_traces.py", [], cwd, cwd)
        after = sorted(p.name for p in cwd.iterdir()) + sorted(p.name for p in (cwd / "data").iterdir())
        print(f"B4 ({sys.platform}) count_traces.py from cwd {folder!r}: exit {rc}; {out.splitlines()[:1]}")
        assert after == before, f"count_traces.py under {folder!r} created {sorted(set(after) - set(before))}"
        assert _md5(db) == md5, "count_traces.py changed the store's bytes"
        assert rc == 0 and "lineage records: 5" in out, (folder, rc, out, err)
        # W4 part 4 — law 15's first line names the store each run opened, the
        # awkward folder's own (never a decoy's); the rest is the plain run's.
        first, head, tail = out.splitlines()[0], "database: ", " (opened read-only: mode=ro + write authorizer)"
        assert first.startswith(head) and first.endswith(tail) \
            and first[len(head):-len(tail)] in {db.as_posix(), db.resolve().as_posix()}, (folder, first)
        assert out.splitlines()[1:] == out_plain.splitlines()[1:] and err.splitlines() == err_plain.splitlines(), (
            folder, out, err)


# ---------------------------------------------------------------------------
# S — the two selftests nobody ran (MF1 c), and their must-fail
# ---------------------------------------------------------------------------

# The two nobody ran (M19, M20), and as2, whose B anchor is the printed line
# and whose must-fail is therefore this (M21).
SELFTEST_SCRIPTS = ["wc_window_count.py", "cp_b49_tally.py", "as2_instance_windows.py"]

# A child that runs a script's main with a plain read-write connect bound to
# the opener's name before the script imports it (the companion's condition:
# a connection that refuses nothing).
CHILD = (
    "import runpy, sqlite3, sys\n"
    "sys.path.insert(0, {root!r})\n"
    "import scripts._readonly as R\n"
    "if {writable!r}:\n"
    "    def plain(path, *, row_factory=sqlite3.Row, url_ok=False):\n"   # W3b part 3: the opener grew `url_ok`
    "        conn = sqlite3.connect(path)\n"
    "        conn.row_factory = row_factory\n"
    "        return conn\n"
    "    R.open_read_only = plain\n"
    "sys.argv = [{script!r}, '--db', {db!r}, '--selftest']\n"
    "try:\n"
    "    runpy.run_path({script!r}, run_name='__main__')\n"
    "except SystemExit as e:\n"
    "    sys.exit(e.code or 0)\n"
)


def _child(tmp_path: Path, script: str, db: Path, *, writable: bool):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               OLLAMA_URL="http://127.0.0.1:9", NEXUS_ENV_ROOT=str(tmp_path))
    code = CHILD.format(root=str(ROOT), writable=writable,
                        script=str(ROOT / "scripts" / script), db=str(db))
    return _proc.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300)


@pytest.mark.parametrize("script", SELFTEST_SCRIPTS)
def test_s_the_selftest_prints_the_exact_refusal_on_a_planted_store(tmp_path, script):
    """The script itself, as the operator runs it, on a copy of the pre-run
    fixture: exit 0, the exact refusal line on its second line (after the
    `database:` line), and the store's bytes unchanged."""
    db = _copy_fixture(tmp_path / "kb.db")
    rc, out, err = _run(script, ["--db", str(db), "--selftest"], tmp_path, tmp_path)
    lines = out.splitlines()
    print(f"S {script}: exit {rc}; {lines[:3]}")
    assert rc == 0, out + err
    assert lines[0].startswith("database: "), lines[:2]
    assert REFUSAL_LINE in lines, lines
    assert not any("SELFTEST FAILED" in l for l in lines)
    assert _md5(db) == _md5(FIXTURE)


@pytest.mark.parametrize("script", SELFTEST_SCRIPTS)
def test_s_companion_an_unrefused_write_prints_selftest_failed_and_exits_3(tmp_path, script):
    """COMPANION (the consequence): the opener replaced by a plain read-write
    connect in the child. The selftest's UPDATE runs unrefused, the script
    prints SELFTEST FAILED and exits 3; a selftest that printed the refusal
    without attempting a write (M19, M20) would exit 0 here."""
    db = _copy_fixture(tmp_path / "kb.db")
    proc = _child(tmp_path, script, db, writable=True)
    lines = proc.stdout.splitlines()
    print(f"S companion {script}: exit {proc.returncode}; {lines[:3]}")
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert any(l.startswith("SELFTEST FAILED: a write succeeded") for l in lines), lines
    assert REFUSAL_LINE not in lines
    assert _md5(db) == _md5(FIXTURE), "the probe matches no row; the bytes must hold even unrefused"


# ---------------------------------------------------------------------------
# C — coactivation_eligibility runs its forecast through the shared opener
# ---------------------------------------------------------------------------

def _plant_coactivation_rows(db: Path, n: int = 3) -> None:
    """Rows the forecast selects: relation co_activated, source
    hippocampus_consolidation (the fixture has none, so the script's
    pre-W1 run never left `No rows match`)."""
    import time
    from backend.kb.granularity import Granularity
    now = time.time()
    c = sqlite3.connect(db)
    for i in range(n):
        c.execute("INSERT INTO triples (subject, relation, object, weight, tier, granularity, source, "
                  "fire_count, created_at, last_fired) VALUES (?, 'co_activated', ?, 0.97, 0, ?, "
                  "'hippocampus_consolidation', 3, ?, ?)",
                  (f"term{i}", "query", Granularity.SESSION.value, now - 30 * 86400, now - (i + 1) * 86400))
    c.commit()
    c.close()


COACT_CHILD = (
    "import runpy, sys\n"
    "sys.path.insert(0, {root!r}); sys.path.insert(0, {scripts!r})\n"
    "import _readonly as R\n"
    "real = R.open_read_only\n"
    "def probed(path, *, row_factory=__import__('sqlite3').Row):\n"
    "    conn = real(path, row_factory=row_factory)\n"
    "    print('PROBE ' + R.assert_write_refused(conn, table='triples'))\n"
    "    return conn\n"
    "R.open_read_only = probed\n"
    "sys.argv = [{script!r}, '--kb', {db!r}]\n"
    "try:\n"
    "    runpy.run_path({script!r}, run_name='__main__')\n"
    "except SystemExit as e:\n"
    "    sys.exit(e.code or 0)\n"
)


def test_c_coactivation_eligibility_forecasts_through_a_connection_that_refuses_a_write(tmp_path):
    """MF1 (a). The script imports the opener as `from _readonly import
    open_read_only`; the child binds a probing wrapper to that name before
    the script runs, so the connection the script receives is the real
    read-only one, probed once on open. A script back on `sqlite3.connect(db)`
    never calls the opener: no PROBE line, and the test fails."""
    db = _copy_fixture(tmp_path / "kb.db")
    _plant_coactivation_rows(db)
    md5 = _md5(db)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               OLLAMA_URL="http://127.0.0.1:9", NEXUS_ENV_ROOT=str(tmp_path))
    code = COACT_CHILD.format(root=str(ROOT), scripts=str(ROOT / "scripts"),
                              script=str(ROOT / "scripts" / "coactivation_eligibility.py"), db=str(db))
    proc = _proc.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300)
    out = proc.stdout
    print(f"C coactivation_eligibility: exit {proc.returncode}; "
          f"{[l for l in out.splitlines() if l.startswith(('PROBE', 'matched rows', 'DRY RUN'))]}")
    assert proc.returncode == 0, out + proc.stderr
    assert "matched rows      : 3" in out and "RATCHET FORECAST" in out, "the forecast path did not run:\n" + out
    assert "DRY RUN — nothing written" in out
    assert "PROBE DatabaseError: not authorized" in out.splitlines(), (
        "the script did not open its store through the shared opener, or the connection it got "
        "did not refuse a write:\n" + out)
    assert _md5(db) == md5, "the forecast changed the store's bytes"
