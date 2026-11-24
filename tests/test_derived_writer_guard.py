"""Packet AC — the persistent-writer guard derives its own population.

THE HISTORY THIS PINS: three writers escaped the hand-maintained guard list
by the identical hole (E.3 HebbianGraphStore, S.2 RecalibrationStore, AB
TrailStore) — module-scope `create_services()` reaching the real data/kb.db
before the mutation baseline. Twice a comment said "any new persistent
store must be added here". A documented rule is not a control; the list is
now derived (tests/_writers.py), and these tests are what make the
derivation trustworthy rather than a fourth kind of forgetting.
"""
from __future__ import annotations

import sqlite3
from tests import _proc
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _writers import (assert_no_module_scope_construction,  # noqa: E402
                      derive_writers)

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# the population
# ---------------------------------------------------------------------------

def test_the_derivation_finds_every_writer():
    """NON-VACUITY by count and by name — an empty or shrunken derivation
    must fail here, not silently guard nothing. `>=` and not `==`: the whole
    point is that an eleventh class-writer derives with no number to bump."""
    w = derive_writers()
    assert len(w) >= 10, f"only {len(w)} writers derived: {sorted(w)}"
    for name in ("TripleStore", "TrailStore", "KairosProcess",
                 "TurnEvaluator", "NexusArchiveChain"):
        assert name in w, f"{name} fell out of the derivation"
    # The params the guard wraps must be the real constructor params.
    assert w["TripleStore"]["param"] == "db_path"
    assert w["KairosProcess"]["param"] == "snapshots_dir"
    assert w["TurnEvaluator"]["param"] == "log_path"
    # And the writer revealed only by a CALL SITE is attributed to the
    # module that DEFINES it — the wrap imports the module, and importing
    # the call site's module (backend.server) would construct everything.
    assert w["TurnEvaluator"]["module"] == "backend.eval.ragas"


def test_cross_module_resolution_is_load_bearing():
    """THE AB REGRESSION PIN. TrailStore's default is schema.DEFAULT_DB —
    an imported constant. Measured in AC discovery: a same-module-only
    resolver finds two of the three historical escapes and loses exactly
    the most recent one. Required, not optional."""
    with_imports = set(derive_writers())
    without = set(derive_writers(follow_imports=False))
    lost = with_imports - without
    assert "TrailStore" in lost, (
        "TrailStore no longer depends on cross-module resolution — if its "
        "default moved into its own module, pick another imported-constant "
        "writer for this pin rather than deleting it")


def test_the_default_population_is_the_tracked_backend(tmp_path):
    """The synthetic-writer sentinel below feeds an explicit file list —
    which proves nothing about the default unless the default is pinned to
    come from git. An untracked scratch file must be INVISIBLE to the
    no-argument call (U's enumerator discipline, one layer over)."""
    stray = ROOT / "backend" / "_ac_stray_probe.py"
    assert not stray.exists(), "leftover probe file — remove it"
    stray.write_text(
        'from pathlib import Path\nD = Path("data/stray.db")\n'
        "class StrayStore:\n"
        "    def __init__(self, db_path=D):\n"
        "        self.db_path = db_path\n", encoding="utf-8")
    try:
        assert "StrayStore" not in derive_writers(), (
            "an UNTRACKED backend file entered the default population — the "
            "derivation has stopped sourcing from git")
    finally:
        stray.unlink()


# ---------------------------------------------------------------------------
# the sentinel that proves the class is closed
# ---------------------------------------------------------------------------

def _synthetic_module(tmp_path, body: str) -> Path:
    f = tmp_path / "synthetic_writer.py"
    f.write_text(textwrap.dedent(body), encoding="utf-8")
    return f


def test_a_new_writer_is_covered_without_being_registered(tmp_path):
    """THE ONE THAT MATTERS MOST. A writer that did not exist when the guard
    was written is derived, wrapped, and rerouted — no list edited anywhere.
    The synthetic file is fed as an EXPLICIT list because the default
    population comes from git and an untracked fixture is invisible to it
    by construction (pinned above)."""
    f = _synthetic_module(tmp_path, """
        from pathlib import Path
        SYNTH_DEFAULT = Path("data/synthetic.db")

        class SyntheticStore:
            def __init__(self, db_path=SYNTH_DEFAULT):
                self.db_path = Path(db_path)
    """)
    w = derive_writers(files=[f])
    assert "SyntheticStore" in w, "the new writer did not derive"
    info = w["SyntheticStore"]
    assert info["param"] == "db_path" and info["default_path"] == "data/synthetic.db"

    # Wrap it exactly as conftest wraps the real ten, and prove protection.
    import importlib.util
    spec = importlib.util.spec_from_file_location("synthetic_writer", f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # W1 — the module pytest loaded (`tests` is a package). A bare
    # `import conftest` executed conftest a second time: a second
    # SESSION_TMP, and the three log variables re-pointed at it.
    from tests import conftest
    target = tmp_path / "session" / "synthetic.db"
    conftest._guard_path_first(mod.SyntheticStore, target, "db_path")

    bare = mod.SyntheticStore()                      # the escape shape
    assert bare.db_path == target, (
        f"a bare construction reached {bare.db_path} — the wrap did not "
        f"protect the unregistered writer")


def test_the_guard_can_still_fail_or_rather_fire(tmp_path):
    """Deliberately point a wrapped writer at the REAL data path: the guard
    must reroute it into the session sandbox. And the negative control — an
    UNWRAPPED copy keeps the real path — proves the wrap is what did it,
    not an accident of the fixture."""
    f = _synthetic_module(tmp_path, """
        from pathlib import Path

        class AimedStore:
            def __init__(self, db_path=Path("data/aimed.db")):
                self.db_path = Path(db_path)
    """)
    import importlib.util
    spec = importlib.util.spec_from_file_location("aimed_writer", f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # W1 — the module pytest loaded (`tests` is a package). A bare
    # `import conftest` executed conftest a second time: a second
    # SESSION_TMP, and the three log variables re-pointed at it.
    from tests import conftest
    real = conftest.REAL_DATA / "aimed.db"

    unwrapped = mod.AimedStore(real)                  # negative control
    assert unwrapped.db_path == Path(real), (
        "the unwrapped class did not keep the real path — this fixture "
        "cannot show the wrap doing anything")

    conftest._guard_path_first(mod.AimedStore, tmp_path / "aimed.db", "db_path")
    aimed = mod.AimedStore(real)
    assert conftest.REAL_DATA not in aimed.db_path.parents, (
        f"a wrapped writer aimed at the real store KEPT it: {aimed.db_path}")


def test_an_unwrappable_writer_fails_loudly_by_name(tmp_path, monkeypatch):
    """The clearance's item 4: a writer the derivation finds but cannot wrap
    must fail the run naming the class and the rule — silently skipping is
    the original hole with better manners. Reproduced by deriving a writer
    whose module does not exist to import, through the same loop conftest
    runs."""
    import importlib
    fake = {"GhostStore": {"module": "backend.does_not_exist",
                           "param": "db_path", "first_positional": True,
                           "default_path": "data/ghost.db",
                           "rules": {"signature_default"}}}
    with pytest.raises(Exception) as exc:
        for name, info in fake.items():
            try:
                mod = importlib.import_module(info["module"])
                getattr(mod, name)
            except Exception as e:
                raise RuntimeError(
                    f"derived persistent writer {name!r} "
                    f"(rules {sorted(info['rules'])}, module "
                    f"{info['module']}) could not be wrapped: "
                    f"{type(e).__name__}: {e}") from e
    msg = str(exc.value)
    assert "GhostStore" in msg and "signature_default" in msg, (
        "the failure does not name the class and the rule — an operator "
        "cannot act on it")


# ---------------------------------------------------------------------------
# the three historical escapes, reconstructed faithfully
# ---------------------------------------------------------------------------

def test_the_three_escapes_are_caught(tmp_path):
    """The escapes were ONE event repeated: a bare `create_services()` —
    every writer at its default — under a guard list missing the newest
    writer. NOT a module-scope `app` import: Packet J removed that years of
    packets ago, and the first draft of this test assumed it and measured
    nothing (the unguarded arm created no file at all — recorded here so
    the faithful shape is not re-lost). Each escape is identified by the
    table its writer created: hebbian_edges (E.3), recalibration_events
    (S.2), relation_trails (AB).

    UNGUARDED arm first, proving the reconstruction is faithful: all three
    tables appear under the sandbox cwd's data/. GUARDED arm — importing
    conftest, which derives and wraps, then the same bare call — leaks none
    of them. The cwd is a temp dir either way; the real store is never in
    reach of the unguarded arm.

    THE CHILD ENV IS COPIED AND PRUNED, NEVER BUILT FROM SCRATCH. The first
    draft hand-built {"PATH": "/usr/bin:/bin", ...} — POSIX-only, and on
    Windows the child died importing asyncio (WinError 10106: no SystemRoot,
    no Winsock) before any writer existed, so the test asserted on a startup
    failure rather than on the escape. Fifth platform finding, new shape:
    not separators but an environment listed by hand. Copy-prune-add is
    platform-correct by construction; the pruning is proven below by a
    planted canary, because a copy that pruned nothing would prove nothing
    about isolation — which is presumably why the dict was hand-built in
    the first place.
    """
    import os
    env = dict(os.environ)                     # platform-correct base
    for k in [k for k in env
              if k.startswith(("NEXUS_", "PYTEST", "PYTHONPATH"))]:
        env.pop(k)                             # remove what must not leak
    env["PYTHONPATH"] = str(ROOT)              # add what the child needs
    env["NEXUS_API_TOKEN"] = "ac-escape-probe"
    env["NEXUS_ENV_ROOT"] = str(tmp_path)      # a repo .env must not steer it
    env["HOME"] = str(tmp_path)

    # NON-VACUITY OF THE ISOLATION: the suite's own configuration must not
    # reach the child. The conftest-injected calibration redirect is a real,
    # always-present canary — if it survives the prune, the child would log
    # into the SUITE's sandbox and the "copy everything" regression is live.
    assert "NEXUS_CALIBRATION_LOG" in os.environ, (
        "the canary is gone from the suite env — pick another always-set "
        "NEXUS_ key, or this proves nothing")
    assert "NEXUS_CALIBRATION_LOG" not in env, (
        "the prune did not strip suite configuration — the child inherits "
        "the test session's own NEXUS_* state")

    raw = tmp_path / "raw"
    raw.mkdir()
    r = _proc.run(
        [sys.executable, "-c",
         "from backend.server import create_services; create_services()"],
        cwd=raw, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)
    assert r.returncode == 0, f"unguarded import failed:\n{r.stderr[-800:]}"
    db = raw / "data" / "kb.db"
    assert db.exists(), (
        "the unguarded import created no data/kb.db — the escape shape no "
        "longer reproduces and this test is measuring nothing")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    tables = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    for escape, table in (("E.3", "hebbian_edges"),
                          ("S.2", "recalibration_events"),
                          ("AB", "relation_trails")):
        assert table in tables, (
            f"the {escape} escape no longer reproduces ({table} absent) — "
            f"say so rather than approximating")

    guarded = tmp_path / "guarded"
    guarded.mkdir()
    r2 = _proc.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(ROOT / 'tests')!r}); "
         "import conftest; "
         "from backend.server import create_services; create_services()"],
        cwd=guarded, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)
    assert r2.returncode == 0, f"guarded import failed:\n{r2.stderr[-800:]}"
    leaked = guarded / "data" / "kb.db"
    if leaked.exists():
        con = sqlite3.connect(f"file:{leaked}?mode=ro", uri=True)
        leaked_tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert not ({"hebbian_edges", "recalibration_events",
                     "relation_trails"} & leaked_tables), (
            f"the DERIVED guard leaked writer tables into data/: "
            f"{sorted(leaked_tables)} — all three escapes are back at once")


# ---------------------------------------------------------------------------
# platform: dotted names by parts, never by string surgery
# ---------------------------------------------------------------------------

def test_derived_module_names_are_dotted_regardless_of_platform():
    """THE WINDOWS FAILURE, pinned. `str(rel).replace("/", ".")` handed
    importlib `backend\\memory\\graph_store` for every writer on Windows —
    the guard failed loudly naming class and rule (condition 4, working on
    its first real exercise), but for a defect in its own plumbing. The
    name must come from PATH PARTS, which do not know what a separator
    looks like."""
    from pathlib import PurePosixPath, PureWindowsPath

    from _writers import module_name
    win = module_name(PureWindowsPath("backend\\memory\\graph_store.py"))
    posix = module_name(PurePosixPath("backend/memory/graph_store.py"))
    assert win == posix == "backend.memory.graph_store"
    assert "\\" not in win and "/" not in win

    # NON-VACUITY: the OLD implementation, applied to the Windows shape,
    # produces exactly the unimportable name the operator saw — so this
    # test genuinely discriminates the fix from the bug rather than passing
    # against both.
    old = str(PureWindowsPath("backend\\memory\\graph_store.py")).replace(
        "/", ".").removesuffix(".py")
    assert old == "backend\\memory\\graph_store", (
        "the reconstruction of the old bug no longer reproduces it — "
        "re-derive this non-vacuity check")
    assert old != win


def test_the_sep_to_dot_shape_is_forbidden_by_ast():
    """FIX THE CLASS, NOT THE INSTANCE — fourth platform-path finding
    (str(path) comparisons, subprocess encoding, CRLF lengths, this). The
    shape is `.replace(<sep>, ".")` or `.replace(".", <sep>)`: a
    separator-dot swap is only ever a module-name/path conversion, and by
    parts it is never needed. AST over every tracked python file, so a
    fifth instance cannot ship anywhere the suite can see."""
    import ast as _ast
    sys.path.insert(0, str(ROOT / "tests"))
    from _tracked import tracked  # noqa: E402

    def offenders_in(files):
        out = []
        for f in files:
            f = Path(f)
            if f.suffix != ".py":
                continue
            tree = _ast.parse(f.read_text(encoding="utf-8"))
            for n in _ast.walk(tree):
                if (isinstance(n, _ast.Call)
                        and isinstance(n.func, _ast.Attribute)
                        and n.func.attr == "replace"
                        and len(n.args) == 2
                        and all(isinstance(a, _ast.Constant) for a in n.args)):
                    vals = {n.args[0].value, n.args[1].value}
                    if "." in vals and (vals & {"/", "\\"}):
                        out.append(f"{f.relative_to(ROOT).as_posix()}:{n.lineno}")
        return out

    hits = offenders_in(tracked("backend/**/*.py", "backend/*.py",
                                "tests/**/*.py", "tests/*.py",
                                "scripts/**/*.py", "scripts/*.py"))
    # This very test contains the shape only inside its own non-vacuity
    # reconstruction above — which is a call on a str with constant args and
    # IS caught by the detector. That one site is the allowed exception, and
    # it is named rather than widened.
    allowed = {f"tests/{Path(__file__).name}"}
    real = [h for h in hits if h.split(":")[0] not in allowed]
    assert not real, (
        "separator-dot swap found — a module name or dotted identifier is "
        "being derived by string surgery on a path, which works only on the "
        "platform it was written on. Use PurePath(...).parts:\n  "
        + "\n  ".join(real))
    # NON-VACUITY: the detector sees the shape (this file's reconstruction).
    assert any(h.split(":")[0] in allowed for h in hits), (
        "the detector no longer sees the planted instance — it is blind, "
        "and the empty result above proves nothing")


def _from_scratch_env_sites(tree, rel: str) -> list:
    """Subprocess calls whose `env=` is built from scratch — a Dict literal
    at the call, or a name whose assignments in the enclosing scope never
    touch `os.environ`. Copy-prune-add is invisible to this; hand-listing
    is not."""
    import ast as _ast

    def mentions_environ(node) -> bool:
        return any(isinstance(n, _ast.Attribute) and n.attr == "environ"
                   for n in _ast.walk(node))

    out = []

    def scan_scope(scope):
        env_derived: dict[str, bool] = {}
        for n in _ast.walk(scope):
            if isinstance(n, _ast.Assign) and len(n.targets) == 1 \
                    and isinstance(n.targets[0], _ast.Name):
                name = n.targets[0].id
                env_derived[name] = env_derived.get(name, False) \
                    or mentions_environ(n.value)
        for n in _ast.walk(scope):
            if not isinstance(n, _ast.Call):
                continue
            fn = getattr(n.func, "attr", getattr(n.func, "id", ""))
            if fn not in ("run", "Popen", "check_output", "check_call", "call"):
                continue
            for kw in n.keywords:
                if kw.arg != "env" or isinstance(kw.value, _ast.Constant):
                    continue                    # env=None is inherit — fine
                if isinstance(kw.value, _ast.Dict) \
                        and not mentions_environ(kw.value):
                    out.append(f"{rel}:{n.lineno}")
                elif isinstance(kw.value, _ast.Name) \
                        and env_derived.get(kw.value.id) is False:
                    out.append(f"{rel}:{n.lineno}")

    for n in _ast.walk(tree):
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            scan_scope(n)
    return out


def test_no_subprocess_env_is_built_from_scratch():
    """THE FIFTH PLATFORM SHAPE, forbidden. A hand-built env dict lists the
    variables its author's platform needs and no other's — this one omitted
    SystemRoot and killed asyncio on Windows before the test's subject ever
    ran. Every other env= site in the tree already copied os.environ and
    pruned (test_dotenv_loading.py:99, test_packet_u.py:249,
    test_rejected_surfaces.py:55); the pattern existed and one new site
    ignored it, which is what makes it a guard's job rather than a
    convention's."""
    import ast as _ast
    sys.path.insert(0, str(ROOT / "tests"))
    from _tracked import tracked

    hits = []
    for f in tracked("backend/**/*.py", "backend/*.py", "tests/**/*.py",
                     "tests/*.py", "scripts/**/*.py", "scripts/*.py"):
        f = Path(f)
        if f.suffix != ".py":
            continue
        tree = _ast.parse(f.read_text(encoding="utf-8"))
        hits += _from_scratch_env_sites(tree, f.relative_to(ROOT).as_posix())
    assert not hits, (
        "subprocess env built from scratch — it lists what one platform "
        "needs and starves every other (Windows children die without "
        "SystemRoot). Copy os.environ, prune what must not leak, add what "
        "the child needs:\n  " + "\n  ".join(hits))

    # NON-VACUITY: the detector sees both flavours of the shape.
    planted = _ast.parse(
        "import subprocess\n"
        "def bad_literal():\n"
        "    subprocess.run(['x'], env={'PATH': '/usr/bin'})\n"
        "def bad_name():\n"
        "    env = {'PATH': '/usr/bin'}\n"
        "    subprocess.run(['x'], env=env)\n"
        "def good(os=None):\n"
        "    import os\n"
        "    env = dict(os.environ)\n"
        "    subprocess.run(['x'], env=env)\n")
    found = _from_scratch_env_sites(planted, "planted.py")
    assert len(found) == 2, (
        f"the detector saw {found} in the planted fixture — it is blind to "
        f"a flavour of the shape, and the empty tree result proves nothing")


# ---------------------------------------------------------------------------
# the constant ratchet — function-based writers cannot arrive silently
# ---------------------------------------------------------------------------

def test_every_data_constant_is_consumed_or_acknowledged():
    """THE RATCHET. Every module-level data/ constant in the tracked backend
    either reaches a derived writer class or sits in
    ACKNOWLEDGED_DATA_CONSTANTS with a stated reason. A twelfth writer's
    constant fails here until a human answers for it — the guard-site
    comment, converted from a rule someone must remember into a failure
    someone must address.

    NON-VACUITY, both directions, because an acknowledged set that could be
    empty and still pass is the same defect one layer up:
      - the sweep must SEE DEFAULT_LOG_PATH (the constants collection is not
        blind), and see it as UNCONSUMED (the acknowledgment is doing work);
      - every acknowledged entry must still exist in the tree (the set
        cannot rot into fiction).
    """
    from _writers import (ACKNOWLEDGED_DATA_CONSTANTS,
                          unconsumed_data_constants)
    unconsumed = unconsumed_data_constants()

    canary = ("backend.eval.calibration_log", "DEFAULT_LOG_PATH")
    assert canary in unconsumed, (
        "the sweep no longer sees DEFAULT_LOG_PATH as an unconsumed "
        "constant — either calibration_log grew a writer class (update the "
        "acknowledged set) or the sweep has gone blind (fix it); an empty "
        "result must never pass on blindness")
    assert unconsumed[canary] == "data/calibration_log.jsonl"

    unacknowledged = set(unconsumed) - set(ACKNOWLEDGED_DATA_CONSTANTS)
    assert not unacknowledged, (
        f"module-level data/ constant(s) reach no derived writer and are "
        f"not acknowledged: {sorted(unacknowledged)}. Either this is a new "
        f"FUNCTION-based writer — add its env redirect beside "
        f"NEXUS_CALIBRATION_LOG in conftest AND acknowledge it here with "
        f"the reason — or the constant is dead and should go.")

    stale = set(ACKNOWLEDGED_DATA_CONSTANTS) - set(unconsumed)
    assert not stale, (
        f"acknowledged constant(s) no longer exist or are now consumed by a "
        f"writer: {sorted(stale)} — remove them, or the set rots into "
        f"fiction")


def test_an_unacknowledged_constant_fails_the_ratchet(tmp_path):
    """A synthetic function-based writer — a data/ constant no class
    consumes — must surface as unconsumed. And the value-collision hole is
    pinned shut: its value is exactly the consumed 'data/kb.db', so a sweep
    keyed by VALUE would hide it behind the ten writers; identity keying
    does not."""
    from _writers import unconsumed_data_constants
    f = _synthetic_module(tmp_path, """
        from pathlib import Path
        FUNCTION_WRITER_DB = Path("data/kb.db")

        def append_rows(rows):
            import sqlite3
            return sqlite3.connect(FUNCTION_WRITER_DB)
    """)
    u = unconsumed_data_constants(files=[f])
    assert ("synthetic_writer", "FUNCTION_WRITER_DB") in u, (
        "a function-writer's constant did not surface — if this is the "
        "value-collision hole (its value matches a consumed constant's), "
        "the sweep has regressed to keying by value")


# ---------------------------------------------------------------------------
# discipline on the derivation itself
# ---------------------------------------------------------------------------

def test_a_docstring_is_not_a_writer(tmp_path):
    """AST, never text search: a module that only TALKS about a data path
    derives nothing."""
    f = _synthetic_module(tmp_path, '''
        """This module mentions Path("data/phantom.db") in prose only."""

        class NotAWriter:
            def __init__(self, name="phantom"):
                self.name = name
    ''')
    assert derive_writers(files=[f]) == {}, (
        "a docstring mention derived a writer — the derivation has become "
        "a text search")


def test_no_writer_is_constructed_at_module_scope():
    """THE RESIDUAL RISK, forbidden rather than remembered: a writer
    constructed at its own module's scope runs at import, before any wrap
    can exist, and no conftest ordering saves it. Clean today; this keeps
    it that way."""
    assert_no_module_scope_construction()


def test_the_module_scope_assertion_can_fire(tmp_path):
    """Non-vacuity for the assertion above — fed a module that constructs
    at module scope, it must name the site."""
    f = _synthetic_module(tmp_path, """
        from pathlib import Path
        D = Path("data/eager.db")

        class EagerStore:
            def __init__(self, db_path=D):
                self.db_path = db_path

        _singleton = EagerStore()
    """)
    with pytest.raises(AssertionError) as exc:
        assert_no_module_scope_construction(files=[f])
    assert "EagerStore" in str(exc.value)
