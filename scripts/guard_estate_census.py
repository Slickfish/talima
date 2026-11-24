"""Packet BD-1 — the guard estate, censused by AST. Read-only over the repository.

    python scripts/guard_estate_census.py [--out guard_estate.json] [--print e1|e2|e3|e4|all]

Four mechanical censuses; the hand-checked columns the packet asks for are
applied to these listings in the report, not computed here.

  E.1  POPULATION of guard modules: every tracked test module that imports
       `ast`, or that reads `PRAGMA table_info`, `app.routes`, CLAUDE.md or
       the tracked enumerators. For each module: its test functions, which of
       them are GUARDS (their body, or a module helper they call, walks an
       AST / reads a schema / reads the router / reads the constitution) and
       which are COMPANION CANDIDATES (their body carries a source-shaped
       string constant, or calls `pytest.raises`, or their name says
       must-fail / flags / catches / fires / trips / synthetic / planted /
       vacuous). Column 2's mechanical half: the string constants a
       companion plants that also appear verbatim in a guard assertion of
       the same module — the "proves the literal" signal.

  E.2  Every `assert` in the tracked test tree whose test is a membership,
       set-equality, subset or set-difference comparison between a LITERAL
       side and a DERIVED side, classified by shape:
         denylist       literal NOT IN derived
         presence       literal IN derived (a required member; not a denylist)
         allowlist      derived == literal-set | derived <= NAME | not (derived - NAME)
         value_pin      derived compared with a number
       Denylist hits are printed with their source line for the human question.

  E.3  A named population of fields — `NexusLoopState`, the compaction
       report / event / result / violation dataclasses, `KairosCycleResult`,
       `record_turn`'s parameters and the `done` frame's keys — and every
       read site of each in the tracked backend, classified:
         control     inside an if/while/ifexp test, a comparison, a boolean
                     op, an assert, or assigned to a name that feeds one of
                     those within the same function (one hop)
         serialise   inside a dict literal, a call to sse_event/json/logger/
                     print/str, an f-string, or a `to_dict`/`asdict` body
         passthrough a keyword argument to next_state/replace/a dataclass
         ambiguous   anything else (reported, never resolved)
       Fields whose backend reads are all serialise/passthrough are the hits.

  E.4  Every sentence of CLAUDE.md and of the I-1…I-9 list containing an
       invariant word (must, never, exactly one, only, always, cannot),
       numbered, with any `tests/test_*.py` the same sentence or paragraph
       names — the mechanical half of the proof column.

Nothing is written but the report. `backend.server` is never imported.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402
from tests._tracked import tracked, tracked_backend              # noqa: E402

INVARIANT_WORDS = re.compile(r"\b(must|never|exactly one|only|always|cannot|MUST|NEVER)\b")
COMPANION_NAME = re.compile(
    r"must_fail|flags|catches|fires|trips|synthetic|planted|plants|vacuous|"
    r"old_shape|old_vacuous|can_fail|can_fire|can_still|deliberately|impure|"
    r"refuses|refused|rejects|caught|forbidden|wrong|altered|tamper|spoof|"
    r"non_vacuity|nonvacuity|detected|ratchet_fails|fails_the")
# "CONTRACTS.md" since W11c moved the documented contracts there and repointed their tests: without it the tests
# that hold those contracts stopped counting as guards (374 guard functions read 355, none removed; W11c-B5).
STRUCTURAL_MARKERS = ("table_info", "app.routes", "CLAUDE_MD", "CLAUDE.md", "CONTRACTS.md",
                      "tracked(", "tracked_backend", "tracked_scripts", "tracked_frontends")


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _parents(tree: ast.AST) -> dict:
    par = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            par[child] = node
    return par


# ---------------------------------------------------------------------------
# E.1 — guard modules, guards, companion candidates
# ---------------------------------------------------------------------------

def _calls_in(fn: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def _is_guard_body(fn: ast.AST, src_lines: list[str]) -> bool:
    text = "\n".join(src_lines[fn.lineno - 1: fn.end_lineno])
    if any(m in text for m in STRUCTURAL_MARKERS):
        return True
    return any(c in ("walk", "parse") for c in _calls_in(fn)) and "ast" in text


def _plants_source(fn: ast.AST) -> list[str]:
    """String constants that look like planted source or planted names."""
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            v = n.value
            if ("def " in v or "\n" in v.strip() or "import " in v) and len(v) > 12:
                out.append(v.strip().splitlines()[0][:60] + " …")
    return out


def e1(tests: list[Path]) -> dict:
    modules = {}
    for p in tests:
        src = _src(p)
        tree = ast.parse(src)
        imports_ast = any(isinstance(n, (ast.Import, ast.ImportFrom))
                          and any((a.name == "ast") for a in n.names) for n in ast.walk(tree)
                          if isinstance(n, ast.Import)) or "import ast" in src
        structural = any(m in src for m in STRUCTURAL_MARKERS)
        if not (imports_ast or structural):
            continue
        lines = src.splitlines()
        helpers = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and not n.name.startswith("test_")}
        helper_is_guard = {name: _is_guard_body(fn, lines) for name, fn in helpers.items()}
        guard_literals: set[str] = set()
        entries = []
        for fn in tree.body:
            if not (isinstance(fn, ast.FunctionDef) and fn.name.startswith("test_")):
                continue
            calls = _calls_in(fn)
            guard = _is_guard_body(fn, lines) or any(helper_is_guard.get(c) for c in calls)
            raises = "raises" in calls
            planted = _plants_source(fn)
            companion = bool(COMPANION_NAME.search(fn.name)) or raises or bool(planted)
            if guard and not companion:
                for n in ast.walk(fn):
                    if isinstance(n, ast.Assert):
                        for c in ast.walk(n):
                            if isinstance(c, ast.Constant) and isinstance(c.value, str) \
                                    and 2 <= len(c.value) <= 40:
                                guard_literals.add(c.value)
            entries.append({"name": fn.name, "line": fn.lineno, "guard": guard,
                            "companion_candidate": companion, "raises": raises,
                            "plants": planted})
        # column 2 mechanical half: a companion's short literals that a guard asserts
        for e in entries:
            if e["companion_candidate"]:
                fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                          and n.name == e["name"])
                lits = {c.value for c in ast.walk(fn)
                        if isinstance(c, ast.Constant) and isinstance(c.value, str)
                        and 2 <= len(c.value) <= 40}
                e["shares_literal_with_a_guard"] = sorted(lits & guard_literals)
        modules[p.relative_to(ROOT).as_posix()] = {
            "imports_ast": imports_ast, "structural": structural,
            "tests": entries,
            "guards": sum(1 for e in entries if e["guard"]),
            "companion_candidates": sum(1 for e in entries if e["companion_candidate"]),
        }
    return modules


# ---------------------------------------------------------------------------
# E.2 — assertion shapes
# ---------------------------------------------------------------------------

def _is_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_literal(e) for e in node.elts)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("set", "frozenset", "tuple", "list") and node.args:
        return _is_literal(node.args[0])
    if isinstance(node, ast.Dict):
        return all(_is_literal(k) for k in node.keys if k is not None)
    return False


def _is_upper_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id.isupper()


def _shape(test: ast.AST) -> str | None:
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = test.operand
        if isinstance(inner, ast.BinOp) and isinstance(inner.op, (ast.Sub, ast.BitAnd)):
            if _is_upper_name(inner.right) or _is_literal(inner.right):
                return "allowlist"
        return None
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return None
    left, op, right = test.left, test.ops[0], test.comparators[0]
    lit_l, lit_r = _is_literal(left), _is_literal(right)
    if isinstance(op, ast.NotIn) and lit_l and not lit_r:
        return "denylist"
    if isinstance(op, ast.In) and lit_l and not lit_r:
        return "presence"
    if isinstance(op, (ast.Eq, ast.NotEq)):
        num = lambda n: isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) \
            and not isinstance(n.value, bool)
        if (num(left) and not lit_r) or (num(right) and not lit_l):
            return "value_pin"
        coll = lambda n: isinstance(n, (ast.Set, ast.Dict, ast.List, ast.Tuple)) or (
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id in ("set", "frozenset"))
        if (lit_l and coll(left) and not lit_r) or (lit_r and coll(right) and not lit_l):
            return "allowlist"
        if (_is_upper_name(left) and not lit_r) or (_is_upper_name(right) and not lit_l):
            return "allowlist"
        return None
    if isinstance(op, (ast.LtE, ast.Lt, ast.GtE, ast.Gt)):
        num = lambda n: isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
        if num(left) or num(right):
            return "value_pin"
        if isinstance(op, ast.LtE) and (_is_upper_name(right) or lit_r):
            return "allowlist"
    return None


def e2(tests: list[Path]) -> dict:
    counts: Counter = Counter()
    hits = defaultdict(list)
    for p in tests:
        src = _src(p)
        lines = src.splitlines()
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Assert):
                shape = _shape(n.test)
                if shape is None and isinstance(n.test, ast.Call) and \
                        isinstance(n.test.func, ast.Attribute) and n.test.func.attr == "issubset":
                    shape = "allowlist"
                if shape:
                    counts[shape] += 1
                    if shape in ("denylist",):
                        hits[shape].append({"file": p.relative_to(ROOT).as_posix(), "line": n.lineno,
                                            "src": lines[n.lineno - 1].strip()[:140]})
    return {"counts": dict(counts), "denylist": hits["denylist"]}


# ---------------------------------------------------------------------------
# E.3 — set-and-reported-but-never-read, over a named population
# ---------------------------------------------------------------------------

SERIALISERS = {"sse_event", "dumps", "dump", "info", "warning", "debug", "error",
               "print", "str", "repr", "round", "float", "int", "len", "list", "tuple",
               "JSONResponse", "format", "log", "exception", "getattr", "asdict"}
PASSTHROUGH = {"next_state", "replace", "NexusLoopState", "CompactionResult",
               "KairosCycleResult", "CompactionReport", "LineageRecord"}


def _dataclass_fields(path: Path, cls: str) -> list[str]:
    tree = ast.parse(_src(path))
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == cls:
            return [s.target.id for s in n.body if isinstance(s, ast.AnnAssign)
                    and isinstance(s.target, ast.Name)]
    return []


def _fn_params(path: Path, fn_name: str) -> list[str]:
    tree = ast.parse(_src(path))
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == fn_name:
            return [a.arg for a in n.args.kwonlyargs + n.args.args if a.arg != "self"]
    return []


def _done_keys(path: Path) -> list[str]:
    tree = ast.parse(_src(path))
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "sse_event" \
                and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "done":
            d = n.args[1]
            if isinstance(d, ast.Dict):
                return [k.value for k in d.keys if isinstance(k, ast.Constant)]
    return []


def _enclosing_function(node, parents):
    n = node
    while n in parents:
        n = parents[n]
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return n
    return None


def _classify_read(node, parents) -> str:
    """Walk up from a read site to the statement and name what it feeds."""
    n, prev = node, None
    while n in parents:
        prev, n = n, parents[n]
        if isinstance(n, (ast.If, ast.While)) and prev is n.test:
            return "control"
        if isinstance(n, ast.IfExp) and prev is n.test:
            return "control"
        if isinstance(n, (ast.Compare, ast.BoolOp, ast.Assert)):
            return "control"
        if isinstance(n, ast.comprehension) and prev in n.ifs:
            return "control"
        if isinstance(n, ast.JoinedStr):
            return "serialise"
        if isinstance(n, ast.Dict):
            return "serialise"
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            if name in PASSTHROUGH:
                return "passthrough"
            if name in SERIALISERS:
                return "serialise"
        if isinstance(n, ast.Return):
            fn = _enclosing_function(n, parents)
            if fn is not None and fn.name in ("to_dict", "as_dict", "__dict__"):
                return "serialise"
            return "ambiguous"
        if isinstance(n, ast.Assign):
            # one hop: does the assigned name feed a control test in this function?
            fn = _enclosing_function(n, parents)
            targets = {t.id for t in n.targets if isinstance(t, ast.Name)}
            if fn is not None and targets:
                for m in ast.walk(fn):
                    if isinstance(m, ast.Name) and m.id in targets and isinstance(m.ctx, ast.Load):
                        c = _classify_read(m, parents) if m is not node else "ambiguous"
                        if c == "control":
                            return "control"
            return "ambiguous"
        if isinstance(n, ast.stmt):
            return "ambiguous"
    return "ambiguous"


def e3(backend: list[Path]) -> dict:
    pop = {
        "NexusLoopState": _dataclass_fields(ROOT / "backend/loop/state.py", "NexusLoopState"),
        "CompactionReport": _dataclass_fields(ROOT / "backend/loop/compaction.py", "CompactionReport"),
        "IntegrityViolation": _dataclass_fields(ROOT / "backend/loop/compaction.py", "IntegrityViolation"),
        "CompactionEvent": _dataclass_fields(ROOT / "backend/loop/compaction.py", "CompactionEvent"),
        "CompactionResult": _dataclass_fields(ROOT / "backend/loop/compaction.py", "CompactionResult"),
        "KairosCycleResult": _dataclass_fields(ROOT / "backend/memory/kairos.py", "KairosCycleResult"),
        "record_turn params": _fn_params(ROOT / "backend/memory/lineage.py", "record_turn"),
        "done keys": _done_keys(ROOT / "backend/loop/agentic_loop.py"),
    }
    trees = {p: ast.parse(_src(p)) for p in backend}
    parents = {p: _parents(t) for p, t in trees.items()}
    results = {}
    for group, fields in pop.items():
        for field in fields:
            sites = []
            for p, t in trees.items():
                for n in ast.walk(t):
                    hit = None
                    if group == "done keys":
                        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) \
                                and n.slice.value == field and isinstance(n.ctx, ast.Load):
                            hit = n
                    elif isinstance(n, ast.Attribute) and n.attr == field and isinstance(n.ctx, ast.Load):
                        hit = n
                    if hit is not None:
                        sites.append({"file": p.relative_to(ROOT).as_posix(), "line": hit.lineno,
                                      "class": _classify_read(hit, parents[p])})
            classes = Counter(s["class"] for s in sites)
            results[f"{group}.{field}"] = {
                "reads": len(sites), "by_class": dict(classes),
                "hit": len(sites) > 0 and classes.get("control", 0) == 0
                       and classes.get("ambiguous", 0) == 0,
                "no_backend_read": len(sites) == 0,
                "sites": sites,
            }
    return {"population": pop, "fields": results}


# ---------------------------------------------------------------------------
# E.4 — invariant sentences
# ---------------------------------------------------------------------------

def _sentences(text: str) -> list[tuple[str, str]]:
    """(sentence, paragraph) pairs; code blocks are kept as their own lines."""
    out = []
    for para in re.split(r"\n\s*\n", text):
        p = para.strip()
        if not p:
            continue
        if p.startswith("```"):
            for line in p.splitlines():
                if line.strip() and not line.startswith("```"):
                    out.append((line.strip(), p))
            continue
        flat = re.sub(r"\s+", " ", p)
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z`*\"'(])", flat):
            s = s.strip()
            if s:
                out.append((s, p))
    return out


def e4() -> list[dict]:
    rows = []
    # W11c: the sections the tests read moved to docs/CONTRACTS.md; the census reads both where each exists.
    sources = [(name, _src(ROOT / name)) for name in ("CLAUDE.md", "docs/CONTRACTS.md") if (ROOT / name).is_file()]
    pe = ROOT / "PACKET_E_PERSISTENCE.md"
    if pe.is_file():
        txt = _src(pe)
        start = txt.find("### Non-negotiable")
        end = txt.find("**Scope discipline", start)
        if start >= 0:
            sources.append(("PACKET_E_PERSISTENCE.md (I-list)", txt[start:end if end > 0 else None]))
    n = 0
    for name, text in sources:
        for sent, para in _sentences(text):
            if INVARIANT_WORDS.search(sent):
                n += 1
                named = sorted(set(re.findall(r"tests?/(?:test_[a-z0-9_]+\.py|_[a-z_]+\.py)", para)))
                rows.append({"n": n, "source": name, "sentence": sent[:400],
                             "tests_named_nearby": named})
    return rows


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="guard_estate.json")
    ap.add_argument("--print", dest="which", default="all",
                    choices=("e1", "e2", "e3", "e4", "all"))
    args = ap.parse_args()

    tests = [p for p in tracked("tests/*.py") if p.name != "__init__.py"]
    backend = tracked_backend()
    report = {"population_tests": len(tests), "population_backend": len(backend)}
    report["e1"] = e1(tests)
    report["e2"] = e2(tests)
    report["e3"] = e3(backend)
    report["e4"] = e4()
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    w = args.which
    if w in ("e1", "all"):
        mods = report["e1"]
        print(f"E.1 population: {len(mods)} guard modules of {len(tests)} tracked test files; "
              f"{sum(m['guards'] for m in mods.values())} guard tests, "
              f"{sum(m['companion_candidates'] for m in mods.values())} companion candidates")
        for f, m in sorted(mods.items()):
            print(f"  {f}: guards={m['guards']} companions={m['companion_candidates']}")
    if w in ("e2", "all"):
        print(f"\nE.2 assertion shapes: {report['e2']['counts']}")
        for h in report["e2"]["denylist"]:
            print(f"  DENYLIST {h['file']}:{h['line']}  {h['src']}")
    if w in ("e3", "all"):
        print("\nE.3 population:")
        for g, fs in report["e3"]["population"].items():
            print(f"  {g}: {fs}")
        print("E.3 hits (backend reads only serialise/passthrough):")
        for k, v in report["e3"]["fields"].items():
            if v["hit"]:
                print(f"  {k}: reads={v['reads']} {v['by_class']}")
        print("E.3 no backend read at all:")
        print("  " + ", ".join(k for k, v in report["e3"]["fields"].items() if v["no_backend_read"]))
    if w in ("e4", "all"):
        print(f"\nE.4 invariant sentences: {len(report['e4'])}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
