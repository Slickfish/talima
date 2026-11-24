"""The backend's import graph, read from source (W10c; run/specs/W10c.md S2).

`graph(texts)` takes `{tracked path: source}` and gives `{module: the tracked backend modules it imports}`. A plant
is a changed entry in `texts`; nothing is imported and nothing on disk is read here.

WHAT IS AN EDGE
  - every `import x` and `from x import y`, wherever it stands: module level, a function's body, a class's body, a
    `try`, a `with`. A function-level import is an edge because calling the function runs it; the interceptor's
    guard is about what a process CAN load, not what it loads at start;
  - `from pkg import name` is an edge to `pkg` and, when `pkg.name` is a tracked module, to `pkg.name`;
  - a relative import, resolved against the importing module's package;
  - a dynamic import in its one readable form, `importlib.import_module("an.absolute.name")`: one literal
    argument and nothing else (and, read liberally where nothing is refused, any `import_module` or `__import__`
    call's absolute literal first argument);
  - importing `a.b.c` runs `a/__init__.py` and `a/b/__init__.py` first, so `a` and `a.b` are edges too.

WHAT IS NOT
  - the body of `if TYPE_CHECKING:`, when the name is `typing`'s and nothing this reading knows has set it: the
    module imports it from `typing`, binds it no other way and takes no table of its own names, and no module in
    the texts assigns an attribute of that name or spells the name as a string (either could set typing's own
    flag, which is every module's). Then the body is not read; its `else` is. Otherwise the body is read like any
    other. A name built at run time (`setattr(typing, 'TYPE_' + 'CHECKING', True)`) is not seen here;
  - a dynamic import in any other form (a computed name, a relative name with its package, a keyword, a second
    argument, `__import__` at all): `dynamic_imports(texts)` lists every call, with None for those, so a guard
    can refuse them where it must see everything;
  - any other way to load code by name. `unreadable_loads(texts)` lists every use of one it knows, so a guard can
    refuse them too: the name `importlib` in any use but its readable one, and the attributes `.importlib` and
    `.import_module` anywhere else; `from x import *` (it loads whatever the package's `__all__` names);
    `__import__`, `exec`, `eval`, `compile`; `sys` under another name, anything imported from it, and
    `.modules` and the importers' own lists on whatever they are read from; the tables of names (`globals()`,
    `vars()`, `.__dict__`, `.__globals__`, a frame's) and a module's own loader; the name of any of those
    attributes written as a string; and a LIST of other loaders (LOADER_MODULES).

THIS IS A READING OF SOURCE, AND IT IS NOT COMPLETE. It refuses the names, attributes and strings it lists. A name
built at run time (`getattr(sys, 'mod' + 'ules')`: `getattr` is used all through the reach and is not refused), a loader
reached through an object it does not list, a C extension's own import (and `ctypes`, which the lock uses and
which can call the interpreter's own importer) are past it. That is why two other
readings stand beside it: a fresh interpreter that runs an exchange and is read for what it loaded, and
PyInstaller's own list of what the released binary holds.
"""
from __future__ import annotations

import ast
from collections import deque


def module_of(rel: str) -> str:
    parts = rel[:-3].split("/")
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def modules(texts: dict) -> dict:
    """{module name: tracked path} for the Python files of `texts`."""
    return {module_of(rel): rel for rel in texts if rel.endswith(".py")}


def _typings(tree) -> tuple:
    """(whether `TYPE_CHECKING` alone is typing's here, whether `typing.TYPE_CHECKING` is): each only when the
    module binds the name once, by importing it from the standard library, and never gives it another value."""
    bound = {"TYPE_CHECKING": [], "typing": []}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                if name in bound:
                    bound[name].append(name == "TYPE_CHECKING" and alias.name == "TYPE_CHECKING" and not node.level
                                       and node.module in ("typing", "typing_extensions"))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                if name in bound:
                    bound[name].append(name == "typing" and alias.name == "typing")
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load) and node.id in bound:
            bound[node.id].append(False)
        elif isinstance(node, ast.arg) and node.arg in bound:
            bound[node.arg].append(False)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in bound:
            bound[node.name].append(False)
        elif isinstance(node, ast.ExceptHandler) and node.name in bound:
            bound[node.name].append(False)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name in bound:
            bound[node.name].append(False)
    if _sets_typings_flag(tree) or any(isinstance(node, ast.Name) and node.id in ("globals", "locals", "vars")
                                       for node in ast.walk(tree)):
        return (False, False)            # a table of names taken: this module's own names can be set through it
    return tuple(bool(how) and all(how) for how in (bound["TYPE_CHECKING"], bound["typing"]))


def _sets_typings_flag(tree) -> bool:
    """Whether a module can give `TYPE_CHECKING` a value without assigning it by name: an attribute assigned
    (`typing.TYPE_CHECKING = True`) or the name spelled as a string (`setattr(typing, 'TYPE_CHECKING', ...)`,
    `globals()['TYPE_CHECKING']`). Either can set typing's own flag, which is every module's."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING" and not isinstance(node.ctx, ast.Load):
            return True
        if isinstance(node, ast.Constant) and node.value == "TYPE_CHECKING":
            return True
    return False


def _type_checking(test, typings: tuple) -> bool:
    return (typings[0] and isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or \
           (typings[1] and isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            and isinstance(test.value, ast.Name) and test.value.id == "typing")


def _is_dynamic_import(call) -> bool:
    f = call.func
    return (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")) in ("import_module", "__import__")


def _readable_load(call) -> "str | None":
    """The module a call loads when it is the ONE readable form, `importlib.import_module("an.absolute.name")`:
    one positional literal, no keyword, no leading dot. None for every other call."""
    f = call.func
    if isinstance(f, ast.Attribute) and f.attr == "import_module" and isinstance(f.value, ast.Name) \
            and f.value.id == "importlib" and len(call.args) == 1 and not call.keywords \
            and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str) \
            and call.args[0].value and not call.args[0].value.startswith("."):
        return call.args[0].value
    return None


# Set by `graph` and `dynamic_imports` for the texts they are given: whether ANY of them sets typing's flag. One module that does has set it for all, and no module's `if TYPE_CHECKING:` is skipped then.
_FLAG_SET_SOMEWHERE = [False]


def _flag_set_somewhere(texts: dict) -> bool:
    return any(_sets_typings_flag(ast.parse(source)) for rel, source in texts.items() if rel.endswith(".py"))


def _walk(node, typings: "tuple | None" = None):
    """Every node that can run, in source order: the body of an `if TYPE_CHECKING:` that is typing's is skipped,
    its `else` is not."""
    if typings is None:
        typings = (False, False) if _FLAG_SET_SOMEWHERE[0] else _typings(node)
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.If) and _type_checking(child.test, typings):
            for other in child.orelse:
                yield other
                yield from _walk(other, typings)
            continue
        yield child
        yield from _walk(child, typings)


def imports_of(module: str, rel: str, source: str, known: dict, *, dynamic: bool = True) -> set:
    """The tracked modules `module` imports. With `dynamic=False`, by `import` statements only."""
    package = module if rel.endswith("__init__.py") else module.rpartition(".")[0]
    found = set()

    def add(name: str):
        parts = name.split(".")
        found.update(c for c in (".".join(parts[:i]) for i in range(1, len(parts) + 1)) if c in known)

    for node in _walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[:len(base) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            add(target)
            for alias in node.names:
                add(f"{target}.{alias.name}")
        elif dynamic and isinstance(node, ast.Call) and _is_dynamic_import(node) and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) \
                and not node.args[0].value.startswith("."):
            add(node.args[0].value)             # read liberally: an edge more is a module more in a reach
    found.discard(module)
    return found


def graph(texts: dict, *, dynamic: bool = True) -> dict:
    known = modules(texts)
    _FLAG_SET_SOMEWHERE[0] = _flag_set_somewhere(texts)
    try:
        return {m: imports_of(m, rel, texts[rel], known, dynamic=dynamic) for m, rel in known.items()}
    finally:
        _FLAG_SET_SOMEWHERE[0] = False


def dynamic_imports(texts: dict) -> list:
    """(tracked path, line, the module or None) for every `import_module(...)` / `__import__(...)` call that can
    run: the module when the call is the one readable form (`_readable_load`), None for any other."""
    out = []
    _FLAG_SET_SOMEWHERE[0] = _flag_set_somewhere(texts)
    try:
        for rel in sorted(t for t in texts if t.endswith(".py")):
            for node in _walk(ast.parse(texts[rel])):
                if isinstance(node, ast.Call) and _is_dynamic_import(node):
                    out.append((rel, node.lineno, _readable_load(node)))
    finally:
        _FLAG_SET_SOMEWHERE[0] = False
    return out


# Other ways to load code by a name: a LIST (what it does not name is not seen here), and the built-ins that run
# or import what a string names. A dotted entry is that module and what is under it.
LOADER_MODULES = ("runpy", "pkgutil", "imp", "zipimport", "builtins", "pydoc", "pickle", "shelve", "marshal",
                  "unittest", "logging.config", "uvicorn.importer",
                  # these run a string as code
                  "code", "codeop", "timeit", "cProfile", "profile", "pdb", "bdb", "trace", "doctest")
LOADER_BUILTINS = ("exec", "eval", "compile", "__import__", "__builtins__", "__loader__", "__spec__", "globals",
                   "locals", "vars")
# An attribute by one of these names reaches a loader or a table of names, whatever it is read from.
LOADER_ATTRIBUTES = ("load_module", "exec_module", "__import__", "__builtins__", "__loader__", "__spec__",
                     "__dict__", "__globals__", "f_globals", "f_builtins", "__subclasses__",
                     # the table of loaded modules and the importers' own lists, on whatever they are read from
                     # (`os.sys.modules`); `sys` reached as an attribute; and the reader that takes a name as text
                     "modules", "meta_path", "path_hooks", "path_importer_cache", "sys", "__getattribute__")
# The importer itself, read as an attribute of something (another module that imported it): refused wherever it is
# not the one readable load's own `importlib.import_module`.
IMPORTLIB_ATTRIBUTES = ("importlib", "import_module")


def _is_loader(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in LOADER_MODULES)


def unreadable_loads(texts: dict) -> list:
    """(tracked path, line, what) for every use, in code that can run, of a way to load code that `graph` cannot
    follow. `importlib` has ONE readable form (`_readable_load`); every other appearance of the NAME is listed:
    an alias, a `from` import, a submodule, any other attribute of it, a second argument, a relative name, the
    name passed along; and so is the importer read as an attribute of something else (`x.importlib`,
    `x.import_module`). So is a star import, `sys.modules`, an import of a listed loader, a listed attribute
    (LOADER_ATTRIBUTES), and a built-in that runs or imports what a string names (`__import__` in every form: its
    from-list and its level load what its first argument does not name), and the name of a listed attribute
    written as a string (what `getattr` would be handed). What is listed is names, attributes and strings as they
    are written; a name put together at run time is not seen."""
    out = []
    for rel in sorted(t for t in texts if t.endswith(".py")):
        nodes = list(ast.walk(ast.parse(texts[rel])))    # every node: a refused form is refused wherever it stands
        readable = {id(node.func.value) for node in nodes
                    if isinstance(node, ast.Call) and _readable_load(node) is not None}
        readable_loads = {id(node.func) for node in nodes
                          if isinstance(node, ast.Call) and _readable_load(node) is not None}
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in ("importlib", "sys") and (alias.name != top or alias.asname):
                        out.append((rel, node.lineno, f"import {alias.name}"
                                    + (f" as {alias.asname}" if alias.asname else "")))
                    elif _is_loader(alias.name):
                        out.append((rel, node.lineno, f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom):
                module = "." * node.level + (node.module or "")
                if any(alias.name == "*" for alias in node.names):
                    out.append((rel, node.lineno, f"from {module} import *"))
                elif not node.level and ((node.module or "").split(".")[0] in ("importlib", "sys")
                                         or _is_loader(node.module or "")
                                         or any(_is_loader(f"{node.module}.{a.name}") for a in node.names)):
                    out.append((rel, node.lineno, f"from {module} import ..."))
            elif isinstance(node, ast.Name) and id(node) not in readable \
                    and (node.id == "importlib" or node.id in LOADER_BUILTINS):
                out.append((rel, node.lineno, f"{node.id}, used other than to import one module named by a literal"))
            elif isinstance(node, ast.Attribute) and node.attr in LOADER_ATTRIBUTES:
                out.append((rel, node.lineno, f"an attribute .{node.attr}"))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value in LOADER_ATTRIBUTES + IMPORTLIB_ATTRIBUTES:
                # a refused attribute's name, written as a string: what `getattr`, `attrgetter` and
                # `__getattribute__` are handed
                out.append((rel, node.lineno, f"the name {node.value!r} as a string"))
            elif isinstance(node, ast.Attribute) and node.attr in IMPORTLIB_ATTRIBUTES \
                    and id(node) not in readable_loads:
                out.append((rel, node.lineno, f"an attribute .{node.attr}, other than the one readable load's"))
    return out


def closure(g: dict, roots, *, without=()) -> dict:
    """{module: the module it was first reached from (None for a root)} for everything `roots` can reach. An edge
    into a module of `without` is not followed and the module is not in the result."""
    seen = {r: None for r in sorted(roots)}
    queue = deque(seen)
    while queue:
        m = queue.popleft()
        for n in sorted(g.get(m, ())):
            if n not in seen and n not in without:
                seen[n] = m
                queue.append(n)
    return seen


def chain(seen: dict, module: str) -> str:
    out = [module]
    while seen[out[-1]] is not None:
        out.append(seen[out[-1]])
    return " -> ".join(reversed(out))
