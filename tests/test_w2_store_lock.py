"""W2 part 1 — the single-writer lock (`backend/store_lock.py`; run/specs/W2.md S2, D4).

Declared behaviour, each driven through real processes where the claim is
about processes:

L1  A second process on a store another NEXUS process holds is refused by
    name, in MCP mode (`python -m backend.mcp`) and at the console's factory
    (`create_app()`, uvicorn's call), with the named exit 7 and the holder's
    pid and start in the message.
L2  The refusal opens nothing: on a fresh data root whose lock another
    process holds, the refused process creates no store file.
L3  One process may open its own store again (the suite does, 855 times):
    the lock is counted, not refused, and a clean release marks it released.
L4  A stale lock (its text names a holder that never released it) is taken
    over, and the takeover is appended to `kb.db.lock.log` and logged; a
    cleanly released lock is taken without a takeover line.
L5  A holder killed outright leaves a lock the next process takes over (the
    OS frees a dead holder's lock), recorded as L4 says.
L6  The two scripts that write the operator's store under `--confirm` take
    the lock first: while NEXUS holds the store they refuse by name (exit 7)
    and leave its bytes unchanged; their dry runs, which open read-only, are
    not refused.

Guard:
G   Every construction of a store class in the tracked backend, scripts and
    root files is answered for: inside `create_services` (which takes the
    lock first), inside a class `create_services` builds, after the lock in
    the two `--confirm` scripts, or on a copy or temporary store the script
    made itself. A planted construction the guard's table does not name, a
    `--confirm` write without the lock, is named.

Every child runs with its working directory in `tmp_path` and NEXUS_ENV_ROOT
there, so no `.env` is read and nothing lands in the repo's `data/`.
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import shutil
import subprocess
import warnings
from tests import _proc
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tests._tracked import REPO, tracked

FIXTURE = REPO / "tests" / "fixtures" / "pre_run_data"
EXIT_STORE_LOCKED = 7          # the documented literal, never the module's own constant


def _env(tmp_path: Path, **extra: str) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("NEXUS_DATA_ROOT", "NEXUS_MCP_DATA_ROOT", "NEXUS_MCP_OWNER")}
    env.update(PYTHONDONTWRITEBYTECODE="1", NEXUS_ENV_ROOT=str(tmp_path), PYTHONIOENCODING="utf-8")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPO), env.get("PYTHONPATH")]))
    env.update(extra)
    return env


HOLDER = textwrap.dedent("""
    import sys
    from pathlib import Path
    {body}
    print("HOLDER READY", flush=True)
    sys.stdin.read()          # held until the test closes stdin (or kills us)
""")

# The holders use only what existed before W2 (`create_services` with its
# old arguments), or take the lock alone where it exists, so the same tests
# run on the pre-fix code and fail there on their consequence (law 16), not
# on a missing name.
BY_SERVICES = ("from backend.data_root import paths_under\n"
               "from backend.server import create_services\n"
               "services = create_services(**paths_under(Path(sys.argv[1])))")
BY_LOCK_ONLY = ("try:\n"
                "    from backend.store_lock import acquire\n"
                "except ImportError:           # before W2 there is no lock to hold\n"
                "    acquire = None\n"
                "lock = acquire(Path(sys.argv[1]) / 'kb.db', mode='console') if acquire else None")


HOLDER_READY_S = 180.0      # create_services on a cold Windows runner, with every import


class _Holder:
    """A child process holding the store lock under `root` until stopped.

    W4b: started by tests/_proc.py. The operator's merge gate hung here for
    1 h 45 min (14 November): the readiness wait was `readline()` with no
    bound, and its failure message called `read()`, which returns only when
    the child exits, while the holder waited on its stdin as designed. Now
    the readiness, the stop and the kill are bounded, a holder that does not
    say ready is killed and named with what it printed, and `proc.pid` is
    the holder's own pid under a Windows venv too (part 2)."""

    def __init__(self, tmp_path: Path, root: Path, body: str = BY_SERVICES):
        root.mkdir(parents=True, exist_ok=True)
        self.proc = _proc.Child([sys.executable, "-c", HOLDER.format(body=body), str(root)], label="the W2 holder",
                                cwd=tmp_path, env=_env(tmp_path), stdin=_proc.PIPE, stdout=_proc.PIPE,
                                stderr=_proc.STDOUT, text=True, encoding="utf-8", errors="replace")
        try:
            seen = self.proc.expect("HOLDER READY", timeout=HOLDER_READY_S)
            before = seen.splitlines()[:-1]
            if before:
                # Not the lock's business, but never silent: a line before the
                # holder's own is what turned the pre-W4b helper's failure
                # message into a wait with no end. pytest's warnings summary
                # shows it.
                warnings.warn(f"W4b: the W2 holder printed before HOLDER READY: {before[:5]!r}", stacklevel=2)
        except BaseException:
            self.proc.kill()                     # a holder that will not be returned is never left running
            raise

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.stop(timeout=60)

    def kill(self) -> None:
        self.proc.kill()


def _run(tmp_path: Path, argv: list[str], **extra: str) -> subprocess.CompletedProcess:
    return _proc.run(argv, cwd=tmp_path, env=_env(tmp_path, **extra), stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=240)


def _console_factory(root: Path) -> list[str]:
    code = ("import os, sys; os.environ['NEXUS_DATA_ROOT'] = sys.argv[1]\n"
            "from backend.server import create_app\n"
            "create_app()\nprint('OPENED')")
    return [sys.executable, "-c", code, str(root)]


# ---------------------------------------------------------------------------
# L1, L2 — a second process is refused by name and opens nothing
# ---------------------------------------------------------------------------

def test_l1_a_second_mcp_process_is_refused_by_name(tmp_path):
    """Pre-fix (law 16): the second MCP process opened the store and served
    (stdin at EOF, it exited 0)."""
    root = tmp_path / "root"
    holder = _Holder(tmp_path, root)
    try:
        proc = _run(tmp_path, [sys.executable, "-m", "backend.mcp"], NEXUS_MCP_DATA_ROOT=str(root))
    finally:
        holder.stop()
    print(f"L1 mcp ({sys.platform}) exit {proc.returncode}; stderr tail: {proc.stderr[-400:]}")
    assert proc.returncode == EXIT_STORE_LOCKED, (proc.returncode, proc.stderr[-1200:])
    assert f"this store is open in another NEXUS process (pid {holder.proc.pid}, since " in proc.stderr
    assert proc.stdout == "", "the refusal wrote to the protocol channel"


def test_l1_a_second_console_is_refused_by_name_at_the_factory(tmp_path):
    """uvicorn's `--factory` call. Pre-fix: create_app() opened the store
    beside the holder's and printed OPENED."""
    root = tmp_path / "root"
    holder = _Holder(tmp_path, root)
    try:
        proc = _run(tmp_path, _console_factory(root))
    finally:
        holder.stop()
    print(f"L1 console ({sys.platform}) exit {proc.returncode}; stderr tail: {proc.stderr[-400:]}")
    assert proc.returncode == EXIT_STORE_LOCKED, (proc.returncode, proc.stderr[-1200:])
    assert "OPENED" not in proc.stdout
    assert f"this store is open in another NEXUS process (pid {holder.proc.pid}" in proc.stderr


def test_l1_w4b_a_line_before_ready_is_a_warning_and_the_holder_still_holds(tmp_path):
    """W4b D8 (the verifier's S9): a holder whose output begins with a line of
    its own (an import's warning on the operator's machine, the line the
    pre-W4b helper's failure message waited on forever) is not a failure of
    the lock's tests: the helper warns with the line, in pytest's warnings
    summary, and the holder holds the lock (a second process is refused)."""
    root = tmp_path / "root"
    early = "print('a planted early line', flush=True)\n" + BY_LOCK_ONLY
    holder = None
    try:
        # A machine whose holder prints lines of its own first, the case this
        # behaviour exists for, must not fail this test: the warning's display
        # shows the first five lines only (the second delta check planted five
        # startup lines and the planted one fell off it), so the warning is
        # matched by its prefix and the planted line is read from the holder's
        # whole early output.
        with pytest.warns(UserWarning, match=r"W4b: the W2 holder printed before HOLDER READY: \["):
            holder = _Holder(tmp_path, root, body=early)
        early_output = holder.proc.output().split("HOLDER READY")[0]
        assert "a planted early line" in early_output, early_output
        proc = _run(tmp_path, _console_factory(root))
    finally:
        if holder is not None:                   # stopped even when the warning or the refusal check fails
            holder.stop()
    assert proc.returncode == EXIT_STORE_LOCKED, (proc.returncode, proc.stderr[-1200:])


def test_l2_the_refused_process_opens_nothing(tmp_path):
    """The lock is taken before the first connect: on a fresh root held by a
    process that took only the lock, the refused process creates no store
    (pre-fix: kb.db, archive.db and chroma/ appeared)."""
    root = tmp_path / "root"
    holder = _Holder(tmp_path, root, body=BY_LOCK_ONLY)
    try:
        proc = _run(tmp_path, [sys.executable, "-m", "backend.mcp"], NEXUS_MCP_DATA_ROOT=str(root))
    finally:
        holder.stop()
    left = sorted(p.name for p in root.iterdir())
    print(f"L2 ({sys.platform}) exit {proc.returncode}; root holds {left}")
    assert proc.returncode == EXIT_STORE_LOCKED, proc.stderr[-800:]
    assert left == ["kb.db.lock"], f"the refused process opened something: {left}"


# ---------------------------------------------------------------------------
# L3 — the same process may open its own store again
# ---------------------------------------------------------------------------

def test_l3_one_process_reopens_its_own_store_and_a_clean_release_says_so(tmp_path):
    from backend.data_root import paths_under
    from backend.server import create_services
    from backend.store_lock import held_here, read_holder
    root = tmp_path / "root"
    first = create_services(**paths_under(root))
    second = create_services(**paths_under(root))           # the suite's restart shape
    kb = root / "kb.db"
    assert held_here(kb) == 2
    assert read_holder(kb)["pid"] == os.getpid() and read_holder(kb)["released"] is False
    first.store_lock.release()
    assert held_here(kb) == 1
    second.store_lock.release()
    assert held_here(kb) == 0 and read_holder(kb)["released"] is True
    second.store_lock.release()                               # once only
    assert held_here(kb) == 0


# ---------------------------------------------------------------------------
# L4, L5 — a stale lock is taken over and recorded
# ---------------------------------------------------------------------------

def _plant_holder(root: Path, **holder) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "kb.db.lock").write_text(json.dumps(holder) + "\n", encoding="utf-8")


def test_l4_a_stale_lock_is_taken_over_and_recorded(tmp_path, caplog):
    """Pre-fix: no lock, so no takeover was ever recorded."""
    from backend.data_root import paths_under
    from backend.server import create_services
    root = tmp_path / "root"
    _plant_holder(root, nexus_store_lock=1, pid=999999, mode="mcp", host="gone", since="2026-11-01T00:00:00Z",
                  released=False)
    with caplog.at_level(logging.WARNING, logger="backend.store_lock"):
        services = create_services(**paths_under(root))
    getattr(services, "store_lock", None) and services.store_lock.release()
    log = root / "kb.db.lock.log"
    assert log.is_file(), "no takeover was recorded"
    lines = log.read_text(encoding="utf-8").splitlines()
    print(f"L4 takeover log: {lines}")
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event"] == "stale_lock_taken_over" and event["previous"]["pid"] == 999999
    assert event["by"]["pid"] == os.getpid()
    assert any("took over a stale lock" in r.getMessage() and "999999" in r.getMessage() for r in caplog.records)


def test_l4_a_cleanly_released_lock_is_taken_without_a_takeover(tmp_path):
    from backend.data_root import paths_under
    from backend.server import create_services
    root = tmp_path / "root"
    _plant_holder(root, nexus_store_lock=1, pid=999999, mode="mcp", host="gone", since="2026-11-01T00:00:00Z",
                  released=True)
    services = create_services(**paths_under(root))
    lock = getattr(services, "store_lock", None)
    assert lock is not None, "create_services took no lock"
    lock.release()
    assert not (root / "kb.db.lock.log").exists(), "a released lock was recorded as a takeover"


def test_l5_a_holder_killed_outright_leaves_a_lock_the_next_process_takes_over(tmp_path):
    """The OS frees a dead holder's lock (on Windows after a delay the lock
    module retries through). Pre-fix: the second process opened the store
    and nothing was recorded."""
    root = tmp_path / "root"
    holder = _Holder(tmp_path, root)
    killed = holder.proc.pid
    holder.kill()
    proc = _run(tmp_path, [sys.executable, "-c",
                           "import sys\nfrom pathlib import Path\n"
                           "from backend.data_root import paths_under\n"
                           "from backend.server import create_services\n"
                           "create_services(**paths_under(Path(sys.argv[1])))\n"
                           "print('OPENED')", str(root)])
    print(f"L5 ({sys.platform}) killed {killed}; exit {proc.returncode}; {proc.stdout.strip()!r}")
    assert proc.returncode == 0 and "OPENED" in proc.stdout, proc.stderr[-1200:]
    log = root / "kb.db.lock.log"
    assert log.is_file(), "the killed holder's lock was taken without a record"
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["previous"]["pid"] == killed, lines


# ---------------------------------------------------------------------------
# L6 — the two --confirm writers take the lock first
# ---------------------------------------------------------------------------

def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("script,args", [
    ("archive_pregate.py", []),
    ("archive_consolidation.py", ["--source", "hippocampus_consolidation"]),
])
def test_l6_a_confirmed_archive_while_nexus_holds_the_store_is_refused(tmp_path, script, args):
    """Pre-fix: the script opened the held store read-write and archived
    (exit 0)."""
    root = tmp_path / "root"
    root.mkdir()
    shutil.copy2(FIXTURE / "kb.db", root / "kb.db")
    holder = _Holder(tmp_path, root)          # NEXUS serving this store
    before = _md5(root / "kb.db")             # after the holder's own schema writes
    try:
        confirmed = _run(tmp_path, [sys.executable, str(REPO / "scripts" / script), "--kb", str(root / "kb.db"),
                                    *args, "--report", str(tmp_path / "r.json"), "--confirm"])
        dry = _run(tmp_path, [sys.executable, str(REPO / "scripts" / script), "--kb", str(root / "kb.db"),
                              *args, "--report", str(tmp_path / "d.json")])
    finally:
        holder.stop()
    print(f"L6 {script} ({sys.platform}): confirmed exit {confirmed.returncode}, dry exit {dry.returncode}; "
          f"{confirmed.stderr.strip()[-200:]!r}")
    assert confirmed.returncode == EXIT_STORE_LOCKED, (confirmed.returncode, confirmed.stderr[-800:])
    assert "this store is open in another NEXUS process" in confirmed.stderr
    assert _md5(root / "kb.db") == before, "a refused run changed the store"
    assert dry.returncode == 0, ("the read-only dry run was refused", dry.stderr[-800:])


# ---------------------------------------------------------------------------
# G — every store opened for writing is answered for
# ---------------------------------------------------------------------------

STORE_CLASSES = {"TripleStore", "Neocortex", "LineageStore", "RecalibrationStore", "HebbianGraphStore",
                 "TrailStore", "NexusArchiveChain", "VectorStore"}
# (file, enclosing function) -> why its store-class constructions need no lock of their own.
ANSWERED = {
    ("backend/server.py", "create_services"): "takes the lock before its first store (L1 to L5)",
    ("backend/memory/neocortex.py", "__init__"): "Neocortex's own TripleStore, built by create_services",
    # W10c — the interceptor's own store: the lock (mode `intercept`) is taken first, before either constructor.
    ("backend/intercept/recorder.py", "open_at"): "takes the lock before its first store (the interceptor's own root)",
    ("scripts/archive_pregate.py", "main"): "takes the lock before TripleStore under --confirm (L6)",
    ("scripts/archive_consolidation.py", "main"): "takes the lock before TripleStore under --confirm (L6)",
    ("start.py", "verify_chain_read_only"): "a temporary copy of the archive, removed after",
    ("scripts/history_shape_harness.py", "_build_store"): "a store the harness builds in its own temp directory",
    ("scripts/history_shape_harness.py", "_run_conversation"): "a store the harness builds in its own temp directory",
    ("scripts/history_shape_replay.py", "_turn"): "a store the replay builds in its own temp directory",
    ("scripts/measure_cycle_cost.py", "main"): "a synthetic store in a temp directory",
    ("scripts/measure_retrieval.py", "main_async"): "a copy of the store it measures",
    ("scripts/measure_trails.py", "main"): "a copy of the store it measures",
    ("scripts/pairing_exclusion_probe.py", "main"): "a copy of the store it probes",
    ("scripts/render_block_probe.py", "main"): "a copy of the store it probes",
    ("scripts/v31_phase0_probe.py", "fixture"): "a fixture store it builds in a temp directory",
    ("scripts/verify_packet_f.py", "convergence_and_integrity"): "a temp copy (its ten rebalances)",
    ("scripts/verify_packet_f.py", "retrieval_probe"): "a temp copy",
    ("scripts/verify_packet_f.py", "citation_dry_run"): "a temp copy",
    ("scripts/who_outranks.py", "run"): "a copy of the store it ranks",
    # W3 part 4 — the restore verification's R4: the product's verifiers on a
    # SECOND copy of the restored backup, in the script's own scratch folder
    # (they open read-write and migrate); never an instance's store, which
    # the script never opens in place.
    ("scripts/restore_verify.py", "r4_verifiers"): "a second copy of the restored backup in the scratch folder",
    # W3b part 4 — the migration's one opener of the store classes. Its
    # callers hand it a store the script made itself (the migration's
    # scratch copy of the source, the export's scratch folder, a temporary
    # store) or the migration's target after the lock is taken; the lock's
    # order is proved by behaviour (tests/test_w3b_migration.py T5: a held
    # lock refuses with exit 7 and the target holds no store table).
    ("scripts/_store_copy.py", "open_schema"): "a store the script made itself, or the target after its lock",
    # W3b part 5 — the hosted backup's R3 and R4: the product's verifiers on the
    # SECOND scratch database the script restored the dump into (they open
    # read-write and migrate), dropped afterwards; never the instance's.
    ("scripts/restore_verify.py", "r3_pg_chain"): "the second scratch database restored from the dump",
    ("scripts/restore_verify.py", "r4_pg_verifiers"): "the second scratch database restored from the dump",
}


def store_constructions(files: "dict[str, str]") -> list[tuple[str, str, int, str]]:
    """(file, enclosing function, line, class) for every call of a store
    class, by AST, under any spelling: a bare name, an attribute, an alias."""
    out = []
    for rel, source in files.items():
        tree = ast.parse(source)
        aliases = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                for a in n.names:
                    if a.name in STORE_CLASSES:
                        aliases[a.asname or a.name] = a.name
            elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Name) and n.value.id in STORE_CLASSES:
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        aliases[t.id] = n.value.id

        class V(ast.NodeVisitor):
            stack = ["<module>"]

            def _fn(self, node):
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            visit_FunctionDef = visit_AsyncFunctionDef = _fn

            def visit_Call(self, node):
                f = node.func
                name = (aliases.get(f.id) or (f.id if f.id in STORE_CLASSES else None)) if isinstance(f, ast.Name) \
                    else (f.attr if isinstance(f, ast.Attribute) and f.attr in STORE_CLASSES else None)
                if name:
                    out.append((rel, self.stack[-1], node.lineno, name))
                self.generic_visit(node)

        V().visit(tree)
    return out


def _population() -> "dict[str, str]":
    files = [p for p in tracked("backend", "scripts", "*.py") if p.suffix == ".py"
             and not p.relative_to(REPO).as_posix().startswith("tests/")]
    return {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8") for p in files}


def unanswered(files: "dict[str, str]") -> list[str]:
    return [f"{rel}:{line} {cls}(...) in {fn}" for rel, fn, line, cls in store_constructions(files)
            if (rel, fn) not in ANSWERED]


def test_g_every_store_construction_is_answered_for():
    files = _population()
    sites = store_constructions(files)
    print(f"G {len(sites)} store-class constructions in {len({s[0] for s in sites})} files")
    assert len(sites) >= 20, "non-vacuity: the population collapsed"
    assert not unanswered(files), unanswered(files)
    used = {(rel, fn) for rel, fn, _l, _c in sites}
    stale = sorted(set(ANSWERED) - used)
    assert not stale, f"answered-for sites that construct no store any more: {stale}"


# The answered sites whose reason is the lock itself: each must call the
# lock's `acquire` (under any name it is imported as) before its first
# store-class construction, by line.
LOCK_TAKERS = {("backend/server.py", "create_services"), ("scripts/archive_pregate.py", "main"),
               ("scripts/archive_consolidation.py", "main")}


def lock_order_violations(files: "dict[str, str]") -> list[str]:
    found = []
    for rel, fn in sorted(LOCK_TAKERS):
        tree = ast.parse(files[rel])
        names = {"acquire"} | {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                               and n.module == "backend.store_lock" for a in n.names if a.name == "acquire"}
        func = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == fn), None)
        if func is None:
            found.append(f"{rel}: {fn} is gone")
            continue
        calls = [n for n in ast.walk(func) if isinstance(n, ast.Call)]
        takes = [c.lineno for c in calls if isinstance(c.func, ast.Name) and c.func.id in names]
        builds = [c.lineno for c in calls if (isinstance(c.func, ast.Name) and c.func.id in STORE_CLASSES)
                  or (isinstance(c.func, ast.Attribute) and c.func.attr in STORE_CLASSES)]
        if not builds:
            found.append(f"{rel}: {fn} builds no store any more")
        elif not takes or min(takes) > min(builds):
            found.append(f"{rel}:{min(builds)} {fn} opens a store before it takes the lock "
                         f"(acquire at {takes or 'none'})")
    return found


def test_g_the_lock_comes_before_the_first_store_at_every_lock_taking_site():
    """Pre-fix (law 16): no site took a lock, so all three are named."""
    found = lock_order_violations(_population())
    assert not found, found


def test_g_companion_a_lock_taken_after_the_store_opens_is_named():
    """COMPANION: archive_pregate's acquire moved below its TripleStore,
    the order the guard's source does not spell out."""
    files = _population()
    src = files["scripts/archive_pregate.py"]
    store_line = "        store = TripleStore(store_target(kb))\n"     # W3b part 3: the store on a path or a URL
    assert src.count(store_line) == 1
    take = next(l for l in src.splitlines(keepends=True) if "acquire_store_lock(store_target(kb)" in l)
    indent = take[:len(take) - len(take.lstrip())]
    moved = (src.replace(take, f"{indent}pass\n")                     # the try body stays valid
             .replace(store_line, store_line + "        " + take.lstrip()))
    files["scripts/archive_pregate.py"] = moved
    assert any("archive_pregate.py" in v and "before it takes the lock" in v
               for v in lock_order_violations(files)), lock_order_violations(files)


def test_g_companion_a_confirmed_write_without_the_lock_is_named():
    """COMPANION (OQ-16 rules 1 and 3): a script that opens the store
    read-write under --confirm through an alias, with no lock; neither its
    file nor its function is in the table."""
    files = _population()
    files["scripts/planted_archiver.py"] = textwrap.dedent("""
        from backend.kb.tiered_store import TripleStore as TS
        def archive(kb, confirm):
            if confirm:
                store = TS(kb)
                store.archive_ids([1], reason="planted")
    """)
    found = unanswered(files)
    assert any("scripts/planted_archiver.py" in v and "TripleStore" in v for v in found), found
