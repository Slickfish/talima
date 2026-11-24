"""Derive the persistent-writer population from the code. One implementation.

WHY THIS EXISTS (Packet AC)
    conftest's default-path guard listed its classes by hand, and three
    writers escaped it by the identical hole — E.3's HebbianGraphStore,
    S.2's RecalibrationStore, AB's TrailStore — each time a module-scope
    `create_services()` reaching the real `data/kb.db` before the mutation
    baseline was taken. Twice a comment said "any new persistent store must
    be added here"; a documented rule is not a control. Packet U ended the
    same class for enumerators by deriving the population from git
    (`tests/_tracked.py`); this is the same move one layer over.

THE DEFINITION, measured before it was built (AC discovery): a class is a
persistent writer iff the AST finds a `data/`-rooted path reaching its
constructor by any of four routes —

    signature_default   a parameter default that is a `data/` literal or a
                        module-level constant resolving to one, INCLUDING
                        constants imported from another module. The
                        cross-module resolution is REQUIRED, not optional:
                        without it the derivation finds two of the three
                        historical escapes and misses AB's TrailStore, whose
                        default is `schema.DEFAULT_DB`. Pinned by a
                        regression test.
    body_fallback       `X if param else Path("data/...")` inside __init__
                        (KairosProcess's shape — signature default is None).
    callsite            a keyword argument at any tracked call site whose
                        value is, or falls back to (`x or CONST`), a `data/`
                        constant (TurnEvaluator's shape — its own module
                        contains no `data/` reference at all; the path is
                        entirely the caller's).

    Together the four rules find exactly the ten writers with no false
    positives — verified against the hand list they replace.

WHAT THIS CANNOT SEE, said plainly: a FUNCTION-based writer. calibration_log
writes `data/calibration_log.jsonl` through module functions and is guarded
by an env var in conftest, by hand. That residual is documented at the guard
site, not here, so the person adding the eleventh function-writer meets the
warning where they would type.

AST, never text search: constants are collected from Assign nodes only, so a
docstring mentioning `Path("data/x.db")` derives nothing — pinned by test.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def module_name(rel) -> str:
    """Dotted module name from a repo-relative path — by PARTS, never by
    string replacement.

    The first implementation was `str(rel).replace("/", ".")`, which works
    on Linux by accident: on Windows `str(rel)` yields backslashes, the
    replace matches nothing, and importlib was handed
    `backend\\memory\\graph_store` for EVERY writer — the guard failed
    loudly naming class and rule, which is its job, but for a defect in its
    own plumbing. Fourth instance of the platform-path class (str(path)
    comparisons, subprocess encoding, CRLF lengths, now this); the
    sep-to-dot string swap is forbidden by AST in
    tests/test_derived_writer_guard.py so the SHAPE cannot return.
    """
    from pathlib import PurePath
    parts = PurePath(rel).with_suffix("").parts
    return ".".join(parts)


def _tracked_backend() -> list[Path]:
    """The population, from git — U's discipline, via the one existing
    implementation."""
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    from _tracked import tracked_backend
    return [Path(p) for p in tracked_backend()]


def _data_literal(node) -> "str | None":
    """The `data/...` string this expression IS, or None."""
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Path":
        a = node.args[0] if node.args else None
        if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                and a.value.startswith("data/"):
            return a.value
    if isinstance(node, ast.Constant) and isinstance(node.value, str) \
            and node.value.startswith("data/"):
        return node.value
    return None


# The ratchet's acknowledged set: module-level data/ constants that reach NO
# derived writer class, each with the reason it is allowed to. Keyed by
# IDENTITY (defining module, name), never by value — a value key would let a
# twelfth writer whose constant happens to be "data/kb.db" hide behind the
# consumed ones. Adding an entry here is a deliberate act with a stated
# reason, exactly like EXPECTED_WRITERS; the stale-entry check fails if a
# member stops existing, so the set cannot rot into fiction.
ACKNOWLEDGED_DATA_CONSTANTS = {
    ("backend.eval.calibration_log", "DEFAULT_LOG_PATH"):
        "function-based writer — no class for the derivation to see; "
        "redirected by the NEXUS_CALIBRATION_LOG env line in conftest",
    ("backend.eval.citation_capture", "DEFAULT_CAPTURE_PATH"):
        "function-based writer (AO), off by default so nothing consumes the "
        "constant at runtime; redirected by the NEXUS_CITATION_CAPTURE_PATH "
        "env line in conftest",
    ("backend.eval.fire_capture", "DEFAULT_FIRE_CAPTURE_PATH"):
        "function-based writer (CP, AO's shape), off by default so nothing "
        "consumes the constant at runtime; redirected by the "
        "NEXUS_FIRE_CAPTURE_PATH env line in conftest",
    ("backend.memory.instance_key", "DEFAULT_KEY_PATH"):
        "function-based writer (W9: load_or_create makes the private key "
        "beside kb.db at first use); the path follows kb.db, so the suite's "
        "services on temporary roots keep it there; named so the ignore "
        "rules and the data/ sentinel watch where it lands by default",
}


def derive_writers(files: "list[Path] | None" = None, *,
                   follow_imports: bool = True) -> dict:
    """{class_name: {module, param, first_positional, default_path, rules}}.

    `files` defaults to the tracked backend population; the parameter exists
    so the synthetic-writer sentinel can feed an explicit list — an untracked
    fixture file is invisible to the git-derived default BY CONSTRUCTION,
    and a sentinel that forgot that would prove nothing (pinned).

    `follow_imports=False` exists only for the AB regression test: it
    disables cross-module constant resolution so the test can show the
    derivation LOSING TrailStore, which is the measured reason the
    resolution is mandatory.
    """
    return _derive(files, follow_imports)[0]


def unconsumed_data_constants(files: "list[Path] | None" = None) -> dict:
    """{(module, NAME): "data/..."} for every module-level data/ constant no
    derived writer consumes — THE RATCHET's raw material. A new
    function-based writer announces itself here as an unacknowledged
    constant, and the test fails until a human adds it to
    ACKNOWLEDGED_DATA_CONSTANTS with a reason and a redirect — converting
    the guard-site comment from a rule someone must remember into a failure
    someone must answer."""
    _writers_map, all_consts, consumed = _derive(files, True)
    return {key: val for key, val in all_consts.items()
            if key not in consumed}


def _derive(files, follow_imports):
    files = [Path(f) for f in (files if files is not None
                               else _tracked_backend())]
    trees: dict[Path, ast.Module] = {}
    mod_consts: dict[str, dict[str, str]] = {}   # module -> {NAME: "data/..."}

    def modname(f: Path) -> str:
        rel = f.resolve().relative_to(ROOT) if f.resolve().is_relative_to(ROOT) \
            else Path(f.name)
        return module_name(rel)

    all_consts: dict[tuple, str] = {}    # (module, NAME) -> "data/..."
    consumed: set[tuple] = set()         # identities a writer path resolved

    for f in files:
        t = ast.parse(f.read_text(encoding="utf-8"))
        trees[f] = t
        consts = {}
        for n in t.body:                          # Assign nodes ONLY — a
            if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                    and isinstance(n.targets[0], ast.Name):
                lit = _data_literal(n.value)      # docstring cannot land here
                if lit:
                    consts[n.targets[0].id] = lit
                    all_consts[(modname(f), n.targets[0].id)] = lit
        mod_consts[modname(f)] = consts

    def visible_consts(f: Path, t: ast.Module) -> dict:
        """local name -> (value, (defining module, NAME)). Identity travels
        with the value so consumption is recorded against the ORIGIN — an
        import does not launder a constant into a new one."""
        me = modname(f)
        vis = {name: (val, (me, name))
               for name, val in mod_consts.get(me, {}).items()}
        if follow_imports:
            for n in ast.walk(t):
                if isinstance(n, ast.ImportFrom) and n.module:
                    for alias in n.names:
                        src = mod_consts.get(n.module, {})
                        if alias.name in src:
                            vis[alias.asname or alias.name] = (
                                src[alias.name], (n.module, alias.name))
        return vis

    def resolve(node, vis) -> "str | None":
        """The data/ path this expression reaches, consumption recorded: a
        Name that resolves marks its ORIGIN consumed, because every resolve
        on the rule paths below feeds a writer record."""
        lit = _data_literal(node)
        if lit:
            return lit
        if isinstance(node, ast.Name) and node.id in vis:
            val, origin = vis[node.id]
            consumed.add(origin)
            return val
        return None

    writers: dict[str, dict] = {}
    class_files: dict[str, Path] = {}
    # Where every class is DEFINED — rule 3 must attribute a writer to its
    # defining module, not to the call site that revealed it (TurnEvaluator
    # lives in backend.eval.ragas; the data/ path that makes it a writer
    # lives in backend.server, and the wrap must import the former).
    class_defs: dict[str, Path] = {}
    for f, t in trees.items():
        for n in ast.walk(t):
            if isinstance(n, ast.ClassDef):
                class_defs.setdefault(n.name, f)

    def record(cls: str, f: Path, param: str, first: bool,
               default_path: str, rule: str) -> None:
        seen = class_files.setdefault(cls, f)
        if seen != f:
            raise RuntimeError(
                f"two classes named {cls!r} in {seen} and {f} — the "
                f"derivation keys by class name and cannot wrap this "
                f"ambiguously; rename one")
        info = writers.setdefault(cls, {
            "module": modname(f), "param": param,
            "first_positional": first, "default_path": default_path,
            "rules": set()})
        info["rules"].add(rule)

    # ---- rules 1+2: signature defaults / body fallbacks -------------------
    for f, t in trees.items():
        vis = visible_consts(f, t)
        for cls in [n for n in ast.walk(t) if isinstance(n, ast.ClassDef)]:
            init = next((m for m in cls.body if isinstance(m, ast.FunctionDef)
                         and m.name == "__init__"), None)
            if init is None:
                continue
            pos = init.args.args[1:]              # skip self
            pos_defaults = ([None] * (len(pos) - len(init.args.defaults))
                            + list(init.args.defaults))
            params = ([(p, d, i == 0, False) for i, (p, d) in
                       enumerate(zip(pos, pos_defaults))]
                      + [(p, d, False, True) for p, d in
                         zip(init.args.kwonlyargs, init.args.kw_defaults)])
            names = {p.arg for p, *_ in params}
            for p, d, first, _kw in params:
                path = resolve(d, vis) if d is not None else None
                if path:
                    record(cls.name, f, p.arg, first, path,
                           "signature_default")
            for n in ast.walk(init):
                if isinstance(n, ast.IfExp) and isinstance(n.test, ast.Name) \
                        and n.test.id in names:
                    path = resolve(n.orelse, vis)
                    if path:
                        first = bool(pos) and pos[0].arg == n.test.id
                        record(cls.name, f, n.test.id, first, path,
                               "body_fallback")

    # ---- rule 3: call sites -----------------------------------------------
    all_classes = set(class_defs)
    for f, t in trees.items():
        vis = visible_consts(f, t)
        for n in ast.walk(t):
            if not isinstance(n, ast.Call):
                continue
            name = getattr(n.func, "id", getattr(n.func, "attr", ""))
            if name not in all_classes:
                continue
            for kw in n.keywords:
                if kw.arg is None:
                    continue
                path = resolve(kw.value, vis)
                if path is None and isinstance(kw.value, ast.BoolOp):
                    for v in kw.value.values:
                        path = path or resolve(v, vis)
                if path:
                    record(name, class_defs[name], kw.arg,
                           False, path, "callsite")
    return writers, all_consts, consumed


def assert_no_module_scope_construction(files: "list[Path] | None" = None) -> None:
    """The residual risk, forbidden by AST (AC clearance, item 5).

    A wrapper-based guard patches `__init__` AFTER importing the writer's
    module — so a writer constructed AT ITS OWN MODULE'S SCOPE would run
    before any wrap could exist, and no conftest ordering can save it. The
    shape does not exist today; this keeps it that way. Module scope means
    the module body, including inside module-level `if`/`try` — anything
    outside a function or class body.
    """
    files = [Path(f) for f in (files if files is not None
                               else _tracked_backend())]
    writer_names = set(derive_writers(files))
    offenders = []
    for f in files:
        t = ast.parse(f.read_text(encoding="utf-8"))

        def scan(node, in_module_scope: bool, f=f):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef, ast.Lambda)):
                    scan(child, False)
                    continue
                if in_module_scope and isinstance(child, ast.Call):
                    name = getattr(child.func, "id",
                                   getattr(child.func, "attr", ""))
                    if name in writer_names:
                        offenders.append(f"{f}:{child.lineno} {name}(...)")
                scan(child, in_module_scope)

        scan(t, True)
    assert not offenders, (
        "persistent writer constructed at module scope — it runs at IMPORT, "
        "before any guard can wrap it, and would reach the real data/ in "
        "every context:\n  " + "\n  ".join(offenders))
