"""W1 — what a test may assert about how a script opens a store.

Before W1 three tests asserted the TEXT `mode=ro` inside a script's source,
or a `.connect(...)` whose literal carried it. Text is a claim, and one of
them was already satisfied by a docstring (`scripts/predicate_distribution.py`
mentions `mode=ro` in prose). After W1 every read-only open goes through
`scripts/_readonly.py`'s `open_read_only`, so the text vanished from the
scripts and those tests would have failed, or, for the close guard keyed on
`.connect`, gone silent. These helpers say the same thing by AST: the script
opens stores through the one read-only open, and calls `sqlite3.connect`
nowhere itself.

The class, not the spelling (W1 part 5f, SF2/SF5): an open is a call that
resolves, through the module's own imports, to `sqlite3.connect`,
`sqlite3.Connection` or `apsw.Connection` (kind `connect`), or to
`scripts/_readonly.py`'s `open_read_only` (kind `open_read_only`). So
`import sqlite3 as s; s.connect(p)`, `from sqlite3 import connect as c; c(p)`
and `from scripts._readonly import open_read_only as ro; ro(p)` are seen for
what they are. A bare `sqlite3.connect(...)` or `open_read_only(...)` with no
import in the source (a planted snippet) is counted too. Every helper takes
the source text or an already parsed `ast.AST`.

W3b part 4: the store interface's `connect` (`backend.store.connect`, and
the kb schema's `backend.kb.schema.connect` over it) is kind `connect` too.
Given a path it opens the SQLite file read-write, so a script calling it is
making exactly the open rule 2 answers for. The migration script was the
first script to call it, and before this line the guard could not see it.
"""
from __future__ import annotations

import ast

OPENER = "open_read_only"
OPENER_MODULES = frozenset({"_readonly", "scripts._readonly"})
# module -> the names in it that open a store connection (a SQLite one, given a path)
SQLITE_OPENERS = {"sqlite3": frozenset({"connect", "Connection"}),
                  "sqlite3.dbapi2": frozenset({"connect", "Connection"}),
                  "apsw": frozenset({"Connection"}),
                  "backend.store": frozenset({"connect"}),
                  "backend.kb.schema": frozenset({"connect"})}
CONNECT = "connect"


def _tree(source: "str | ast.AST") -> ast.AST:
    return source if isinstance(source, ast.AST) else ast.parse(source)


def _bindings(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    """What the imports bind: {local name: module} for a module bound by
    `import` or `from pkg import module`, and {local name: kind} for a
    function bound by `from module import name [as alias]`."""
    modules: dict[str, str] = {}
    functions: dict[str, str] = {}
    known_modules = set(SQLITE_OPENERS) | set(OPENER_MODULES)
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name in known_modules:
                    if a.asname:                      # `import sqlite3 as s` binds `s`
                        modules[a.asname] = a.name
                    else:                             # `import scripts._readonly` binds `scripts`
                        top = a.name.split(".")[0]
                        modules[top] = top
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            for a in n.names:
                dotted = f"{base}.{a.name}" if base else a.name
                bound = a.asname or a.name
                if dotted in known_modules:
                    modules[bound] = dotted
                elif base in SQLITE_OPENERS and a.name in SQLITE_OPENERS[base]:
                    functions[bound] = CONNECT
                elif base in OPENER_MODULES and a.name == OPENER:
                    functions[bound] = OPENER
    return modules, functions


def _kind(dotted: str) -> "str | None":
    """`sqlite3.connect` -> connect; `scripts._readonly.open_read_only` ->
    open_read_only; anything else -> None."""
    module, _, name = dotted.rpartition(".")
    if module in SQLITE_OPENERS and name in SQLITE_OPENERS[module]:
        return CONNECT
    if module in OPENER_MODULES and name == OPENER:
        return OPENER
    return None


def _opens_in(node: ast.AST, modules: dict[str, str],
              functions: dict[str, str]) -> list[tuple[str, ast.Call, str]]:
    """The opens under `node`, resolved through `_bindings` of the whole
    module (the imports sit at module level; the calls sit in functions):
    (kind, the call node, the call as spelled)."""
    found = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name):
            kind = functions.get(f.id) or (OPENER if f.id == OPENER else None)
        elif isinstance(f, ast.Attribute):
            spelled = ast.unparse(f)
            head, _, rest = spelled.partition(".")
            # The head either names a module the imports bound, or IS the module.
            dotted = f"{modules[head]}.{rest}" if head in modules else spelled
            kind = _kind(dotted)
        else:
            continue
        if kind:
            found.append((kind, n, ast.unparse(n.func)))
    return found


def open_calls(source: "str | ast.AST") -> list[tuple[str, ast.Call, str]]:
    """(kind, the call node, the call as spelled) of every store open in
    `source`: the node, for a guard that reads the call's arguments."""
    tree = _tree(source)
    return _opens_in(tree, *_bindings(tree))


def sqlite_opens(source: "str | ast.AST") -> list[tuple[str, int, str]]:
    """(kind, line, the call as spelled) of every store open in `source`."""
    return [(kind, node.lineno, spelled) for kind, node, spelled in open_calls(source)]


def store_opens(source: "str | ast.AST") -> list[tuple[str, int]]:
    """(kind, line) of every store open: `connect` for a direct SQLite
    connect (any spelling the imports bind), `open_read_only` for the shared
    read-only open (any alias)."""
    return [(kind, line) for kind, line, _spelled in sqlite_opens(source)]


def read_only_problems(source: "str | ast.AST") -> list[str]:
    """Why `source` does not open its stores through the one read-only open
    (empty: it does)."""
    opens = sqlite_opens(source)
    problems = [f"line {line}: a direct connect ({spelled}), not {OPENER}"
                for kind, line, spelled in opens if kind == CONNECT]
    if not any(kind == OPENER for kind, _line, _spelled in opens):
        problems.append(f"the script never calls {OPENER}")
    return problems


def unclosed_opens(source: "str | ast.AST") -> list[str]:
    """The functions that open a store, by either kind, and call no
    `.close()` inside their own body: `name (line N)` each. The Windows lock
    guard's key, which went silent when it was `.connect` alone and the
    scripts moved to `open_read_only` (W1 part 3)."""
    tree = _tree(source)
    modules, functions = _bindings(tree)
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _opens_in(fn, modules, functions):
            continue
        closes = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute) and n.func.attr == "close"]
        if not closes:
            out.append(f"{fn.name} (line {fn.lineno})")
    return out


def enclosing_function(tree: ast.AST, node: ast.AST) -> str:
    """The name of the innermost function `node` sits in, or `<module>`."""
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
    return "<module>"


__all__ = ["CONNECT", "OPENER", "OPENER_MODULES", "SQLITE_OPENERS", "enclosing_function",
           "open_calls", "read_only_problems", "sqlite_opens", "store_opens", "unclosed_opens"]
