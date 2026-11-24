"""W10c (run/specs/W10c.md S1, S2) — the interceptor is a standalone recorder: no memory engine within its reach.

THE CLAIM (Amendment 10, the operator's): the interceptor "runs with no memory engine (no KB, cortex, hippocampus,
KAIROS or learning), depending only on the record, seal, chain, store and lock modules", held by "a guard derived
from the import graph [that] fails if the interceptor reaches any memory-engine module, with a companion that
plants such an import".

HOW IT IS HELD. The backend's import graph is read from source (`tests/_import_graph.py`: every import that can run,
function bodies included, literal dynamic imports, each module's parent packages). The interceptor's reach is the
closure of its own modules (`backend/intercept/`) and of the standalone set, STANDALONE below, each member with the
reason it is there. The verdict fails when that closure holds anything outside the standalone set and the
interceptor's own modules. The engine is not listed here: it is derived, as everything the console (`backend.server`)
can reach that the standalone set does not hold, and G2 shows the derivation finds the five things the operator named.

    G1  the verdict on the tree is empty.
    G2  the derived engine is not empty and holds a module for each of the operator's five words.
    C1  a function-level import of an engine module, planted in a standalone module, fails the verdict by name.
    C2  the same reached through two hops, and through a package's `__init__`.
    C3  a literal `importlib.import_module`, and a dynamic import whose name is not a literal.
    C4  an import under `if TYPE_CHECKING:` is not an edge; the same import in its `else` is.
    C7  a way to load code the graph cannot follow, inside the reach, fails the verdict: the name `importlib` in
        any use but its one readable one (an alias, a `from` import, a submodule, another attribute), another
        loader of the standard library, a built-in that runs or imports what a string names, a table of names.
    C7b the lists C7's refusals are read from: every entry is refused when used, and none has left.
    C8  the function that holds the optional link's one load begins by refusing unless the configuration it is
        given says injection is on.
    C9  `if TYPE_CHECKING:` is skipped only where nothing this reading knows has set the flag.
    C5  a stale name in STANDALONE (no such tracked module) fails the verdict.
    C6  the optional link (`backend/intercept/inject.py`, NEXUS's retrieval added to a request, off by default)
        does reach the engine, is left out of the reach, and is entered one way only: by name, once, from the
        interceptor's own code. Imported, statically or in a function, or loaded by name anywhere else, it fails.
    R1  a fresh interpreter that imports every standalone module holds no backend module outside the set; the
        same interpreter made to import the console's seal host as well shows what the witness would see.
    R2  a fresh interpreter that RUNS the interceptor (one exchange with a tool use, recorded, sealed, its
        credential built, its store verified) holds no backend module outside the standalone set and the
        interceptor's own: the function bodies ran, and loaded no engine. The same run with the optional link
        switched on holds the engine, and the upstream got the retrieval block: the witness can see.
    M1-M4  the moves W10c made to get here changed nothing: the record-side DDL by its sha256 and by identity with
        `backend.kb.schema`'s names, the tombstone hash and the archival hash by their pre-move literals, the
        capture settings by identity, and the governance seal's payload by its pre-move literal.

WHAT IT DOES NOT SEE: a way to load code that is none of the above (a name put together at run time and handed to
`getattr`, a C extension's own import, another process). The reading refuses the names and attributes it lists; it
is not complete, and no page says it is. R1 and R2 are the runtime half, for the
paths they drive; the release workflow's reading of the binary's own archive is the third (a module that is not in
the archive cannot be loaded from it, by any means).
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys

from tests import _proc
from tests._import_graph import chain, closure, dynamic_imports, graph, module_of, modules, unreadable_loads
from tests._tracked import REPO, tracked_backend

# The standalone set: what a recorder with no memory engine may load, and why each is there.
STANDALONE = {
    "backend": "the package (a docstring)",
    "backend.data_root": "where a store's files live under a root; reads the environment, imports nothing of ours",
    "backend.env": "the .env loader",
    "backend.identity": "W4's identity boundary (who a request is taken to be from); standard library only",
    "backend.mcp": "the package that homes the scope vocabulary (its __init__ is a docstring)",
    "backend.mcp.scope": "what a record's scope declares, field by field; imports the lineage store alone",
    "backend.memory": "the package (an empty __init__)",
    "backend.memory.actions": "the action recorder: a row before a request leaves, its outcome once",
    "backend.memory.capture_settings": "the replay capture's two switches, with no provider behind them",
    "backend.memory.chain": "the archive chain: blocks, their hashes, roots and signatures",
    "backend.memory.commitments": "salted field commitments",
    "backend.memory.credentials": "decision credentials and receipts",
    "backend.memory.http_signatures": "RFC 9421 signatures over an action's request",
    "backend.memory.instance_key": "the instance's signing key",
    "backend.memory.lineage": "the record: the lineage store and its verifier",
    "backend.memory.merkle": "Merkle roots and inclusion proofs",
    "backend.memory.proofs": "a record's proof of inclusion in its block",
    "backend.memory.record_schema": "the record's own tables and the tombstone's hashed body",
    "backend.memory.seal": "the governance seal: what a block seals, and one pass that appends it",
    "backend.store": "the store interface",
    "backend.store.base": "the store interface",
    "backend.store.dialect": "the store's SQL dialect",
    "backend.store.postgres_backend": "the Postgres backend",
    "backend.store.sqlite_backend": "the SQLite backend",
    "backend.store.target": "what names a store",
    "backend.store_lock": "the single-writer lock",
}
INTERCEPT = "backend.intercept"          # the interceptor's own package
OPTIONAL_LINK = "backend.intercept.inject"      # the one optional link to the engine: loaded by name, when asked for
CONSOLE = "backend.server"               # what the engine is derived from
THE_OPERATORS_FIVE = ("kb", "cortex", "hippocampus", "kairos", "learning")


def tracked_texts() -> dict:
    return {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8") for p in tracked_backend()}


def _own(module: str) -> bool:
    return module == INTERCEPT or module.startswith(INTERCEPT + ".")


def engine(texts: dict) -> set:
    """Everything the console can reach that the standalone set does not hold and the interceptor does not own."""
    return {m for m in closure(graph(texts), [CONSOLE]) if m not in STANDALONE and not _own(m)}


def reach(texts: dict) -> dict:
    """What the interceptor can load without its optional link: the link is no root and no edge into it is
    followed. That it is entered one way only, by name and when asked for, is the verdict's to hold."""
    g = graph(texts)
    return closure(g, [m for m in g if (m in STANDALONE or _own(m)) and m != OPTIONAL_LINK], without={OPTIONAL_LINK})


def verdict(texts: dict) -> list:
    """Every way the interceptor's reach leaves the standalone set; empty when it does not."""
    known = modules(texts)
    problems = [f"STANDALONE names {m}, which is not a tracked backend module" for m in sorted(STANDALONE)
                if m not in known]
    seen = reach(texts)
    eng = engine(texts)
    for m in sorted(seen):
        if m not in STANDALONE and not _own(m):
            what = "the memory engine or the console" if m in eng else "outside the standalone set"
            problems.append(f"the interceptor reaches {m} ({what}): {chain(seen, m)}")
    inside = {known[m] for m in seen if m in known}
    for rel, line, literal in dynamic_imports(texts):
        if rel in inside and literal is None:
            problems.append(f"{rel}:{line}: a dynamic import that is not one absolute literal name, inside the "
                            "interceptor's reach: the graph cannot say what it loads")
    for rel, line, what in unreadable_loads(texts):
        if rel in inside:
            problems.append(f"{rel}:{line}: {what}, inside the interceptor's reach: a way to load code the graph "
                            "cannot follow")
    # the optional link: entered once, by name, from the interceptor's own code, and imported by nothing
    if OPTIONAL_LINK in known:
        static = graph(texts, dynamic=False)
        for m in sorted(seen):
            if OPTIONAL_LINK in static.get(m, ()):
                problems.append(f"{m} imports {OPTIONAL_LINK}: the optional link is loaded by name when it is "
                                "asked for, never imported")
        by_name = [(rel, line) for rel, line, literal in dynamic_imports(texts) if literal == OPTIONAL_LINK]
        if len(by_name) != 1 or not _own(module_of(by_name[0][0])):
            problems.append(f"{OPTIONAL_LINK} is loaded by name at {by_name}: one place, in the interceptor's own "
                            "code, is where it comes in")
        else:
            why = _holder_problem(texts[by_name[0][0]], by_name[0][1])
            if why:
                problems.append(f"{by_name[0][0]}:{by_name[0][1]}: the function that loads {OPTIONAL_LINK} does not "
                                f"begin by refusing unless the setting is on: {why}")
    return problems


SETTING = "inject"                       # the configuration's field for NEXUS_INTERCEPT_INJECT


def _holder_problem(source: str, line: int) -> "str | None":
    """Why the function that holds the link's one load does not refuse by itself; None when it does: it takes the
    configuration as its first parameter, and its first statement (after a docstring) is
    `if <that parameter>.inject is not True: raise ...`, with the load after it. What it holds is that statement
    and that order. A caller that hands it a configuration made to say injection is on has turned injection on;
    no reading of source stops that."""
    holders = [n for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.lineno <= line <= n.end_lineno]
    if not holders:
        return "the load is at module level"
    holder = max(holders, key=lambda n: n.lineno)
    params = [a.arg for a in holder.args.posonlyargs + holder.args.args]
    if not params:
        return f"{holder.name} takes no configuration"
    body = holder.body[1:] if holder.body and isinstance(holder.body[0], ast.Expr) \
        and isinstance(getattr(holder.body[0], "value", None), ast.Constant) else holder.body
    first = body[0] if body else None
    test = first.test if isinstance(first, ast.If) else None
    refuses = (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.IsNot)
               and isinstance(test.left, ast.Attribute) and test.left.attr == SETTING
               and isinstance(test.left.value, ast.Name) and test.left.value.id == params[0]
               and len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Constant)
               and test.comparators[0].value is True
               and first.body and isinstance(first.body[0], ast.Raise) and not first.orelse)
    if not refuses:
        return f"{holder.name}'s first statement is not `if {params[0]}.{SETTING} is not True: raise ...`"
    # the load must stand after the refusal: not in the function's own defaults or decorators (they run when the
    # module is imported), and not inside the refusal (it would run because the setting is off)
    return None if first.end_lineno < line else "the load does not come after the refusal"


def _planted(texts: dict, module: str, addition: str) -> dict:
    """`texts` with `addition` appended to a module's source. The module is named; its tracked path is the graph's."""
    rel = modules(texts)[module]
    return {**texts, rel: texts[rel] + "\n\n" + addition + "\n"}


# ---------------------------------------------------------------- G1, G2

def test_g1_the_interceptors_reach_is_the_standalone_set():
    texts = tracked_texts()
    problems = verdict(texts)
    print(f"reach: {len(reach(texts))} modules; standalone set: {len(STANDALONE)}; engine (derived): {len(engine(texts))}")
    assert problems == [], "\n".join(problems)


def test_g2_the_engine_is_derived_and_holds_the_operators_five():
    eng = engine(tracked_texts())
    found = {word: sorted(m for m in eng if word in m.split(".") or any(part.startswith(word) for part in m.split(".")))
             for word in THE_OPERATORS_FIVE}
    print({word: len(ms) for word, ms in found.items()}, "of", len(eng))
    assert all(found.values()), f"the derived engine holds nothing for {[w for w, ms in found.items() if not ms]}"
    assert len(eng) >= 40, f"the derived engine is {len(eng)} modules: the derivation lost the console"


# ---------------------------------------------------------------- the companions

def _an_engine_module(texts: dict, word: str) -> str:
    """A derived engine module under the operator's word, never spelled here."""
    return sorted(m for m in engine(texts) if word in m.split(".") and m.count(".") >= 2)[0]


def test_c1_a_function_level_import_of_an_engine_module_fails_the_verdict():
    texts = tracked_texts()
    for word, home in (("cortex", "backend.memory.lineage"), ("kb", "backend.memory.chain"),
                       ("learning", "backend.store_lock")):
        target = _an_engine_module(texts, word) if word != "learning" else "backend.learning"
        assert target in engine(texts), target
        plant = f"def _planted():\n    import {target}\n    return {target}"
        problems = verdict(_planted(texts, home, plant))
        assert any(f"reaches {target} (the memory engine or the console)" in p for p in problems), (target, problems)


def test_c2_an_engine_module_two_hops_away_and_through_a_package_init_fails_the_verdict():
    texts = tracked_texts()
    target = _an_engine_module(texts, "cortex")
    # two hops: merkle is imported by chain, which the lineage store's callers import
    two_hops = verdict(_planted(texts, "backend.memory.merkle", f"def _planted():\n    from {target} import x"))
    assert any(f"reaches {target} " in p and "backend.memory.merkle" in p for p in two_hops), two_hops
    # a package __init__: every module of the package runs it first
    kairos = sorted(m for m in engine(texts) if m.endswith(".kairos"))[0]
    through_init = verdict(_planted(texts, "backend.memory", f"from {kairos} import KairosProcess"))
    assert any(f"reaches {kairos} " in p and "backend.memory ->" in p for p in through_init), through_init


def test_c3_a_dynamic_import_is_read_when_literal_and_refused_when_not():
    texts = tracked_texts()
    target = _an_engine_module(texts, "kb")
    literal = verdict(_planted(texts, "backend.memory.proofs",
                               f"def _planted():\n    import importlib\n    return importlib.import_module({target!r})"))
    assert any(f"reaches {target} " in p for p in literal), literal
    computed = verdict(_planted(texts, "backend.memory.proofs",
                                "def _planted(name):\n    import importlib\n    return importlib.import_module(name)"))
    proofs = modules(texts)["backend.memory.proofs"]
    assert any("a dynamic import that is not one absolute literal name" in p and p.startswith(proofs + ":")
               for p in computed), computed
    # outside the reach, the same call is not this guard's business
    elsewhere = sorted(m for m in engine(texts) if m.endswith(".curiosity"))[0]
    assert verdict(_planted(texts, elsewhere, "def _planted(name):\n    import importlib\n"
                                              "    return importlib.import_module(name)")) == []


def test_c4_type_checking_imports_are_not_edges_and_their_else_is():
    texts = tracked_texts()
    target = _an_engine_module(texts, "cortex")
    head = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
    assert verdict(_planted(texts, "backend.memory.commitments", head + f"    import {target}")) == []
    in_else = verdict(_planted(texts, "backend.memory.commitments",
                               head + f"    pass\nelse:\n    import {target}"))
    assert any(f"reaches {target} " in p for p in in_else), in_else


def test_c7_a_way_to_load_code_the_graph_cannot_follow_fails_the_verdict_inside_the_reach():
    texts = tracked_texts()
    target = _an_engine_module(texts, "cortex")
    package, _, leaf = target.rpartition(".")
    home = "backend.memory.merkle"
    rel = modules(texts)[home]
    plants = {
        "an alias of the loader": f"from importlib import import_module as _load\n\n\ndef _planted():\n"
                                  f"    return _load({target!r})",
        "a submodule of importlib": "import importlib.util as _u\n\n\ndef _planted(path):\n"
                                    "    return _u.spec_from_file_location('m', path)",
        "another attribute of importlib": "import importlib\n\n\ndef _planted(m):\n    return importlib.reload(m)",
        "importlib handed along": "import importlib\n_loader = importlib",
        "a string run as code": f"def _planted():\n    exec('import ' + {target!r})",
        "the built-in importer under another name": "_imp = __import__",
        "another loader of the standard library": f"import runpy\n\n\ndef _planted():\n"
                                                  f"    return runpy.run_module({target!r})",
        # the verifier's V8: forms that read as a literal load and load something else
        "a relative name with its package": f"import importlib\n\n\ndef _planted():\n"
                                            f"    return importlib.import_module({'.' + leaf!r}, {package!r})",
        "the built-in importer with a from-list": f"def _planted():\n"
                                                  f"    return __import__({package!r}, fromlist=[{leaf!r}])",
        "the built-in importer with a level": f"def _planted():\n"
                                              f"    return __import__({leaf!r}, globals(), None, ['x'], 1)",
        "a star import": f"from {package} import *",
        # the verifier's V10: three more of the standard library's ways
        "pydoc's locate": f"import pydoc\n\n\ndef _planted():\n    return pydoc.locate({target!r})",
        "a pickle": "import pickle\n\n\ndef _planted(data):\n    return pickle.loads(data)",
        "the table of loaded modules": "import sys\n\n\ndef _planted():\n"
                                       "    return getattr(sys.modules['importlib'], 'import_' + 'module')",
        # delta check 1's D5: the same table and the same importer, reached another way
        "sys under another name": "import sys as _s\n\n\ndef _planted(n):\n"
                                  "    return getattr(_s.modules['importlib'], 'import_' + 'module')(n)",
        "the table imported by itself": "from sys import modules as _m\n\n\ndef _planted(n):\n"
                                        "    return _m['importlib']",
        "the built-ins through the module's own names": "def _planted(n):\n"
                                                        "    return globals()['__builtins__']['__imp' + 'ort__'](n)",
        "the module's own loader": "def _planted(n, p):\n    return type(__loader__)(n, p).load_module()",
        "a module's names as a table": "import json\n\n\ndef _planted():\n    return json.__dict__",
        # and three more of the standard library's ways to run a string as code
        "a statement timed": f"import timeit\n\n\ndef _planted():\n"
                             f"    return timeit.timeit('import ' + {target!r}, number=1)",
        "an interpreter of its own": f"import code\n\n\ndef _planted():\n"
                                     f"    return code.InteractiveInterpreter().runsource('import ' + {target!r})",
        "a statement profiled": f"import cProfile\n\n\ndef _planted():\n"
                                f"    return cProfile.run('import ' + {target!r})",
        # delta check 2's E18: the same importer and the same tables, reached through an attribute
        "a function's own table of names": "def _planted(n):\n"
                                           "    return _planted.__globals__['__built' + 'ins__']['__imp' + 'ort__'](n)",
        "the loader through another module's name": "from backend.intercept import config as _c\n\n\n"
                                                    "def _planted(n):\n"
                                                    "    _load = _c.importlib.import_module\n    return _load(n)",
        "a frame's table of names": "import sys\n\n\ndef _planted():\n    return sys._getframe().f_globals",
        # delta check 3's F12: nothing built at run time, and none of it was seen
        "sys through another module": "import os\n\n\ndef _planted():\n    return os.sys.modules",
        "the table by a literal name": "import sys\n\n\ndef _planted(n):\n"
                                       "    return getattr(getattr(sys, 'modules')['json'], 'loads')(n)",
        "the importer by a literal name": "import json\n\n\ndef _planted(n):\n"
                                          "    return getattr(json, 'import_module', None)",
        "an attribute getter": "import operator\nimport sys\n\n\ndef _planted():\n"
                               "    return operator.attrgetter('modules')(sys)",
        "the object's own attribute reader": "import sys\n\n\ndef _planted():\n"
                                             "    return sys.__getattribute__('meta_path')",
        "the importers' own list": "import sys\n\n\ndef _planted():\n    return sys.meta_path",
    }
    for what, plant in plants.items():
        problems = verdict(_planted(texts, home, plant))
        assert any(p.startswith(rel + ":") and "a way to load code the graph cannot follow" in p
                   for p in problems), (what, problems)
        assert not any(f"reaches {target} " in p for p in problems), \
            f"{what}: the graph followed it after all, so this plant is no witness for the rule"
    # a literal with a keyword beside it is followed (it does load that module) AND refused as a form
    both = verdict(_planted(texts, home, f"import importlib\n\n\ndef _planted():\n"
                                         f"    return importlib.import_module({target!r}, package=None)"))
    assert any(f"reaches {target} " in p for p in both) and any("not one absolute literal name" in p for p in both)
    # outside the reach, the same code is not this guard's business
    elsewhere = sorted(m for m in engine(texts) if m.endswith(".curiosity"))[0]
    assert verdict(_planted(texts, elsewhere, plants["a string run as code"])) == []


# The names the guard refuses inside the reach, as they stood when this was written. A name leaves a list only
# by a decision recorded here: with one removed and nothing else changed, every other test of this file passed
# (delta check 2's E19).
REFUSED_MODULES = {"runpy", "pkgutil", "imp", "zipimport", "builtins", "pydoc", "pickle", "shelve", "marshal",
                   "unittest", "logging.config", "uvicorn.importer", "code", "codeop", "timeit", "cProfile",
                   "profile", "pdb", "bdb", "trace", "doctest"}
REFUSED_NAMES = {"exec", "eval", "compile", "__import__", "__builtins__", "__loader__", "__spec__", "globals",
                 "locals", "vars"}
REFUSED_ATTRIBUTES = {"load_module", "exec_module", "__import__", "__builtins__", "__loader__", "__spec__",
                      "__dict__", "__globals__", "f_globals", "f_builtins", "__subclasses__", "importlib",
                      "import_module", "modules", "meta_path", "path_hooks", "path_importer_cache",
                      "__getattribute__", "sys"}


def test_c7b_every_name_the_guard_refuses_is_refused_and_none_has_left_the_lists():
    """C7 plants forms. This holds the lists themselves: every name on one is refused when it is used, alone, in
    a module (so no entry is dead), and every name that was on one still is (so none leaves unnoticed)."""
    from tests import _import_graph as ig
    assert REFUSED_MODULES <= set(ig.LOADER_MODULES), REFUSED_MODULES - set(ig.LOADER_MODULES)
    assert REFUSED_NAMES <= set(ig.LOADER_BUILTINS), REFUSED_NAMES - set(ig.LOADER_BUILTINS)
    assert REFUSED_ATTRIBUTES <= set(ig.LOADER_ATTRIBUTES) | set(ig.IMPORTLIB_ATTRIBUTES), \
        REFUSED_ATTRIBUTES - set(ig.LOADER_ATTRIBUTES) - set(ig.IMPORTLIB_ATTRIBUTES)
    plants = {f"import {m}": f"import {m}\n" for m in ig.LOADER_MODULES}
    plants.update({f"from {m} import x": f"from {m} import x\n" for m in ig.LOADER_MODULES})
    plants.update({f"the name {n}": f"_kept = {n}\n" for n in ig.LOADER_BUILTINS})
    plants.update({f"the attribute .{a}": f"import json\n_kept = json.{a}\n"
                   for a in ig.LOADER_ATTRIBUTES + ig.IMPORTLIB_ATTRIBUTES})
    plants.update({f"the attribute's name {a!r}, as a string": f"import json\n_kept = getattr(json, {a!r}, None)\n"
                   for a in ig.LOADER_ATTRIBUTES + ig.IMPORTLIB_ATTRIBUTES})
    assert len(plants) == 2 * len(ig.LOADER_MODULES) + len(ig.LOADER_BUILTINS) + 2 * len(ig.LOADER_ATTRIBUTES) \
        + 2 * len(ig.IMPORTLIB_ATTRIBUTES)
    for what, source in plants.items():
        assert unreadable_loads({"backend/planted.py": source}), f"{what} is on a list and is not refused"
    assert unreadable_loads({"backend/planted.py": "import json\n_kept = getattr(json, 'dumps')\n"}) == []


def test_c9_a_module_that_sets_type_checking_itself_is_read_under_it():
    """`if TYPE_CHECKING:` is skipped because the name is `typing`'s, which is False when the program runs. A
    module that gives the name a value of its own has made no such promise (the verifier's V9)."""
    texts = tracked_texts()
    target = _an_engine_module(texts, "cortex")
    for head in ("TYPE_CHECKING = True\n", "from typing import TYPE_CHECKING\nTYPE_CHECKING = True\n",
                 "import backend.env as typing\n",
                 # delta check 1's D12: the name given a value without being assigned by name
                 "import typing\nfrom typing import TYPE_CHECKING\ntyping.TYPE_CHECKING = True\n",
                 "from typing import TYPE_CHECKING\nglobals()['TYPE_CHECKING'] = True\n",
                 "from typing import TYPE_CHECKING\ntry:\n    pass\nexcept ValueError as TYPE_CHECKING:\n    pass\n",
                 "from typing import TYPE_CHECKING\nimport typing\nsetattr(typing, 'TYPE_CHECKING', True)\n"):
        guard = "typing.TYPE_CHECKING" if head.startswith("import backend") else "TYPE_CHECKING"
        problems = verdict(_planted(texts, "backend.memory.commitments", f"{head}if {guard}:\n    import {target}"))
        assert any(f"reaches {target} " in p for p in problems), (head, problems)
    honest = "import typing\nif typing.TYPE_CHECKING:\n    import " + target
    assert verdict(_planted(texts, "backend.memory.commitments", honest)) == []
    # typing's flag set in ONE module is set for all: another module's honest `if TYPE_CHECKING:` is then read
    elsewhere = _planted(_planted(texts, "backend.memory.commitments", honest), "backend.memory.merkle",
                         "import typing\ntyping.TYPE_CHECKING = True")
    assert any(f"reaches {target} " in p for p in verdict(elsewhere)), verdict(elsewhere)


def test_c8_the_function_that_loads_the_optional_link_refuses_unless_the_setting_is_on(monkeypatch):
    """The link is loaded by one function, and that function begins by refusing unless the configuration it is
    given has injection on. So however it is called (delta check 1's D11: an alias, `getattr`, a call from
    another module, a test that only looks true), the engine is loaded only for a configuration that says
    injection is on. Held two ways: the verdict reads the function's first statement, and the function is called
    with the setting off."""
    texts = tracked_texts()
    app = modules(texts)[INTERCEPT + ".app"]
    refusal = "    if config.inject is not True:\n"
    load = f'importlib.import_module("{OPTIONAL_LINK}")'
    assert texts[app].count(refusal) == 1 and texts[app].count(load) == 1
    for what, planted in {
        "the refusal gone": texts[app].replace(refusal, "    if False:\n"),
        "a weaker test": texts[app].replace(refusal, "    if config.inject is None:\n"),
        "a test of something else": texts[app].replace(refusal, "    if config.owner is not True:\n"),
        # the load moved to where it runs before the refusal, or because of it (delta check 3's F9)
        "the load as a default of the function's own": texts[app].replace(
            "def link(config):", f"def link(config, _engine={load}):").replace(f"        return {load}",
                                                                              "        return _engine"),
        "the load inside the refusal itself": texts[app].replace(f"        return {load}", "        return None").replace(
            "        raise ConfigRefused(f\"inject_off: ", f"        raise ConfigRefused(str({load}) + f\"inject_off: "),
    }.items():
        assert planted != texts[app] and planted.count(load) == 1, what
        problems = verdict({**texts, app: planted})
        assert any("does not begin by refusing" in p for p in problems), (what, problems)
    # at run time: the setting off, or anything that is not exactly on, loads nothing
    import types

    from backend.intercept import app as iapp
    from backend.intercept.config import ConfigRefused
    loaded = []
    monkeypatch.setattr(iapp, "importlib", types.SimpleNamespace(import_module=loaded.append))
    for setting in (False, None, 1, "on", iapp.link):
        try:
            iapp.link(types.SimpleNamespace(inject=setting))
        except ConfigRefused as e:
            assert str(e).startswith("inject_off: "), e
        else:
            raise AssertionError(f"the link was loaded with injection {setting!r}")
    assert loaded == []
    iapp.link(types.SimpleNamespace(inject=True))
    assert loaded == [OPTIONAL_LINK]


def test_a1_the_archive_reading_names_every_engine_module_and_nothing_of_the_reach():
    """The release reads PyInstaller's own list of what it bundled (`tests/_archive_reach.py`). Derived: every
    module of the derived engine, named in a listing, is named by the reading (the verifier's V19: a pattern of
    six package names missed 37 of 83); the reach itself, listed whole, is not."""
    from tests import _archive_reach
    texts = tracked_texts()
    listing = lambda names: "\n".join(f" ('{n}', '/x/module.py', 'PYMODULE')," for n in names)   # noqa: E731
    reach_names = sorted(reach(texts))
    assert _archive_reach.outside(listing(reach_names)) == [], _archive_reach.outside(listing(reach_names))
    eng = sorted(engine(texts))
    missed = [m for m in eng if _archive_reach.outside(listing(reach_names + [m])) != [m]]
    assert len(eng) >= 40 and missed == [], f"the archive reading does not name {len(missed)} of {len(eng)}: {missed[:8]}"
    assert _archive_reach.outside(listing(reach_names + [OPTIONAL_LINK])) == [OPTIONAL_LINK]
    assert _archive_reach.problems(listing([m for m in reach_names if not m.endswith(".recorder")])) != [], \
        "a listing without the interceptor's own module is a reading of nothing"
    assert _archive_reach.problems(listing(reach_names)) == []


def test_c6_the_optional_link_reaches_the_engine_and_is_entered_one_way_only():
    texts = tracked_texts()
    g = graph(texts)
    # non-vacuity: the link is where the engine comes in, so leaving it out of the reach is what the verdict rests on
    through_link = closure(g, [OPTIONAL_LINK])
    assert set(through_link) & engine(texts), "the optional link reaches no engine module: the exclusion holds nothing"
    assert OPTIONAL_LINK not in reach(texts)
    imported = verdict(_planted(texts, "backend.intercept.recorder", "from backend.intercept import inject"))
    assert any(f"backend.intercept.recorder imports {OPTIONAL_LINK}" in p for p in imported), imported
    lazily = verdict(_planted(texts, "backend.intercept.protocols",
                              "def _planted():\n    from backend.intercept.inject import Injector\n    return Injector"))
    assert any(f"backend.intercept.protocols imports {OPTIONAL_LINK}" in p for p in lazily), lazily
    twice = verdict(_planted(texts, "backend.intercept.recorder",
                             f"def _planted():\n    import importlib\n    return importlib.import_module({OPTIONAL_LINK!r})"))
    assert any("is loaded by name at" in p for p in twice), twice
    elsewhere = verdict(_planted(texts, "backend.memory.seal",
                                 f"def _planted():\n    import importlib\n    return importlib.import_module({OPTIONAL_LINK!r})"))
    assert any("is loaded by name at" in p for p in elsewhere), elsewhere


def test_c5_a_stale_name_in_the_standalone_set_fails_the_verdict():
    texts = tracked_texts()
    problems = verdict({rel: text for rel, text in texts.items()
                        if not rel.endswith(".py") or module_of(rel) != "backend.memory.proofs"})
    assert any("STANDALONE names backend.memory.proofs, which is not a tracked backend module" in p
               for p in problems), problems


# ---------------------------------------------------------------- R1, the runtime half

_PROBE = """
import importlib, json, sys
for name in json.loads(sys.argv[1]):
    importlib.import_module(name)
print(json.dumps(sorted(m for m in sys.modules if m == "backend" or m.startswith("backend."))))
"""


def _loaded(names: list) -> set:
    done = _proc.run([sys.executable, "-c", _PROBE, json.dumps(names)], capture_output=True, text=True,
                     encoding="utf-8", errors="replace", cwd=REPO, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    return set(json.loads(done.stdout.strip().splitlines()[-1]))


def test_r1_importing_the_standalone_set_loads_nothing_outside_it():
    loaded = _loaded(sorted(STANDALONE))
    assert loaded == set(STANDALONE), sorted(loaded ^ set(STANDALONE))
    # the witness can see: the console's seal host, imported as well, brings the engine with it
    kairos = sorted(m for m in engine(tracked_texts()) if m.endswith(".kairos"))[0]
    with_engine = _loaded(sorted(STANDALONE) + [kairos])
    assert kairos in with_engine and with_engine - set(STANDALONE), "the witness would not see an engine import"


_EXCHANGE = """
import asyncio, json, sys
import httpx
from backend.intercept import app as iapp, config as iconfig
from backend.intercept.forward import Forwarder
from backend.intercept.recorder import Recorder
from backend.intercept.stub_upstream import StubUpstream


async def main(root, inject):
    config = iconfig.load({"NEXUS_INTERCEPT_UPSTREAM": "http://127.0.0.1:9", "NEXUS_INTERCEPT_DATA_ROOT": root,
                           "NEXUS_INTERCEPT_INJECT": inject})
    recorder = Recorder.open_at(config.data_root)
    stub = StubUpstream()
    app = iapp.build(config, recorder, Forwarder(transport=httpx.ASGITransport(app=stub)))
    await app.startup()
    if inject == "on":                                   # a claim for the link's retrieval to find
        from backend.kb.granularity import Granularity
        app._inject.services.store.add(subject="qtc change at 600mg", relation="was", object="11.4ms", weight=0.8,
                                       tier=1, granularity=Granularity.TOPIC, source="human_assertion")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8790") as harness:
        doc = {"model": "m", "max_tokens": 8, "stream": True,
               "messages": [{"role": "user", "content": "what was the QTc change at 600mg?"}],
               "tools": [{"name": "get_weather", "input_schema": {"type": "object"}}]}
        response = await harness.post("/v1/messages", json=doc, headers={"x-api-key": "not-a-key"})
    assert response.status_code == 200, response.text
    app.seal_once()
    record = recorder.lineage.recent(1)[0]
    credential = recorder.credential(record.lineage_id, disclose=("record_scope",))
    verdict = recorder.verify()
    await app.shutdown()
    return {"scope": record.record_scope, "ok": verdict["ok"], "kind": credential["credential"],
            "actions": verdict["actions"]["checked"], "block_sent": b"nexus-block" in stub.requests[0]["body"],
            "injected": len(record.injected)}


done = asyncio.run(main(sys.argv[1], sys.argv[2]))
print(json.dumps({"done": done, "loaded": sorted(m for m in sys.modules if m == "backend" or m.startswith("backend."))}))
"""


def _exchange(root, inject: str) -> dict:
    done = _proc.run([sys.executable, "-c", _EXCHANGE, str(root), inject], capture_output=True, text=True,
                     encoding="utf-8", errors="replace", cwd=REPO, timeout=300)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_r2_running_an_exchange_through_the_interceptor_loads_no_engine_module(tmp_path):
    out = _exchange(tmp_path / "root", "off")
    assert out["done"]["scope"] == "intercepted" and out["done"]["ok"] and out["done"]["actions"] == 1, out["done"]
    assert not out["done"]["block_sent"] and out["done"]["injected"] == 0
    loaded = set(out["loaded"])
    outside = sorted(m for m in loaded if m not in STANDALONE and not _own(m))
    assert not outside, f"running the interceptor loaded {outside}"
    assert {m for m in loaded if _own(m)} >= {"backend.intercept.app", "backend.intercept.recorder",
                                             "backend.intercept.forward", "backend.intercept.protocols"}
    eng = engine(tracked_texts())
    assert not loaded & eng and len(eng) >= 40
    # the witness can see: with the optional link on, the engine is there, and so is its block in the request
    linked = _exchange(tmp_path / "linked", "on")
    assert OPTIONAL_LINK in linked["loaded"] and set(linked["loaded"]) & eng, "the link loaded no engine"
    assert linked["done"]["block_sent"] and linked["done"]["injected"] >= 1 and linked["done"]["ok"], linked["done"]


# ---------------------------------------------------------------- M1 to M4, the moves changed nothing

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_m1_the_record_side_ddl_moved_verbatim_and_kb_schema_names_the_same_objects():
    from backend.kb import schema
    from backend.memory import record_schema
    pre_move = {"HELD_COMMITMENTS_DDL": "e12b898a7b54343b3d2aa88a49fd03f04d08d010ec7b08248cf01fe6e304d52c",
                "ACTION_RECORDS_DDL": "f858d096ade16a26802031009c82a54084422d367ab0d753f1416f1196d0b5ad",
                "RECORD_TOMBSTONES_DDL": "c9866e1026ce5325e4d17cad60d0792d4dc6ecad1a99f35a77305390e774339d"}
    for name, sha in pre_move.items():
        assert _sha(getattr(record_schema, name)) == sha, name
        assert getattr(schema, name) is getattr(record_schema, name), name
    assert schema.record_tombstone_body is record_schema.record_tombstone_body
    assert schema.record_tombstone_hash is record_schema.record_tombstone_hash


def test_m2_the_tombstone_and_archival_hashes_are_their_pre_move_literals():
    from backend.kb import schema
    body = schema.record_tombstone_body("e1", 1.5, "lid", "op", "role", "local", "why", ["a", "b"])
    assert schema.record_tombstone_hash(body) == "aa32a9318aee12421f70cc733ac985f52565e8a73e08c3ee676ee804dda96e70"
    archival = schema.archival_event_body("e1", 1.5, 7, "archived", "r", "op", "role", "local")
    assert schema.archival_event_hash(archival) == "ca8fe94dac0f0c26786fd5b8703706a140ab173f803559b28077bd5bd6208efe"


def test_m3_the_capture_settings_are_one_object_under_both_names():
    from backend.memory import capture_settings
    from backend.providers import replay_capture
    for name in ("KEEP_SETTING", "SEED_SETTING", "keep_requests_enabled", "seed_enabled"):
        assert getattr(replay_capture, name) is getattr(capture_settings, name), name
    assert (capture_settings.KEEP_SETTING, capture_settings.SEED_SETTING) == ("NEXUS_KEEP_REQUESTS",
                                                                              "NEXUS_INFERENCE_SEED")


def test_m4_the_governance_seals_payload_is_its_pre_move_literal_from_both_hosts():
    from backend.kb.recalibration import RecalibrationStore
    from backend.memory import seal
    from backend.memory.kairos import KairosProcess
    from backend.memory.lineage import LineageStore
    h = lambda i: hashlib.sha256(str(i).encode()).hexdigest()
    args = ([("lid-b", h(2)), ("lid-a", h(1)), ("lid-c", h(3))], [("recal:2", h(12)), ("recal:1", h(11))],
            [h(1) + h(21), h(2) + h(22), h(3) + h(23)], [("lid-y", h(32), h(42), 7), ("lid-x", h(31), h(41), 5)],
            [("c-1", h(51)), ("c-2", h(52))], [("a-1", h(61)), ("a-2", h(62))])
    pre_move = "c1ca9696ad7eac411575d220606767ec9301b3c873d07801868ca6a86af15a1a"
    for build in (KairosProcess._seal_payload, seal.seal_payload):
        payload = build(*args)
        assert sorted(payload) == ["action_seal", "binding_seal", "commit_seal", "lineage_seal", "recalibration_seal"]
        assert _sha(json.dumps(payload, sort_keys=True, separators=(",", ":"))) == pre_move, build
        assert build([], []) == {}
    # the one digest: the recalibration store's is the lineage store's construction
    for hashes in ([], [h(1)], [h(3), h(1), h(2)]):
        assert RecalibrationStore.seal_digest(hashes) == LineageStore.seal_digest(hashes)
