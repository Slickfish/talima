"""W1 part 2 — the legacy `backend/ollama_client.py` shim is retired.

Fix 0 moved the Ollama client into `backend/providers/ollama.py` and left
this module behind "so legacy imports keep working"; the Fix 0 spec itself
said to delete it once both providers worked (FIX_0_PROVIDER_OVERRIDE.md,
"Delete after migration"). W1's establish found it had no run-time caller:
AST and bytecode over every tracked `.py`, and a runtime import of the 86
other backend modules, which never loaded it. Its only importers were two
tests. It also carried the AV defect: its `stream_chat` supplied Law 5's
ceiling as a default `num_ctx`, which AV's guard could not see because it
looked only under `backend/providers/`.

The retirement is recorded on its own register line (law 5: a retired
feature has a recorded decision).

S1  WHAT THIS READS, exactly. No tracked Python file imports the shim: no
    `import` or `from ... import` binds the name `ollama_client` or names
    `backend.ollama_client` (absolute, relative from a backend subpackage,
    or bare after a path hack). And no string a program can act on names
    it: every string constant, every `+` of constants and every f-string's
    literal parts, wherever it stands, matched as the module — dotted
    (`backend.ollama_client`, alone or with a `.member` after it),
    relative (`.ollama_client`), bare (`ollama_client`) or in pkgutil's
    colon form (`backend:ollama_client`) — or as its file
    (`ollama_client.py`, in any path). W1's verifier (N4) found six
    dynamic forms the first call rule missed; the second verifier (N3)
    found the path-shaped and bare forms outside the enumeration. One
    exclusion: a docstring or a bare string statement is prose, nothing
    imports by it, and it may name the shim as history (tests/_tracked.py's
    docstring does; the witness below finds it with the guard's own reader
    and the exclusion off). Comments are not read: nothing imports by a
    comment. Outside the class by its shape, recorded (the review's R7): a
    text computed at run time — a `%` format, `bytes.decode()`, a `join`
    of fragments, `.lower()`, a name split across an f-string's formatted
    value (`f"backend.{'ollama'}_client"`: the fold joins literal parts
    only) — and a docstring read back through `__doc__`; `find_spec`
    fails every one of them at run time while no copy of the module
    exists. The import system cannot find the shim
    either. This file names the shim to forbid it and is not scanned.
S1c Companions: each of those forms, planted, is named; the prose forms,
    planted, are not.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path, PurePosixPath

import pytest

from tests._tracked import tracked

ROOT = Path(__file__).resolve().parent.parent
SHIM = "backend.ollama_client"
SHIM_NAME = SHIM.rsplit(".", 1)[1]
# The path the shim had, by parts (the `.as_posix()` key the population uses).
SHIM_FILE = PurePosixPath(*SHIM.split(".")).with_suffix(".py").as_posix()
THIS_FILE = "tests/test_w1_shim_retired.py"
# A string that names the shim: the module as a token — dotted, relative,
# bare or in pkgutil's colon form, followed by nothing, a `.member` or any
# non-word character — or its file, anywhere in a path.
SHIM_SHAPED = re.compile(r"(?<![\w.])\.?(?:backend[.:])?ollama_client(?![\w])|ollama_client\.py(?![\w])")


def _folded(node) -> str | None:
    """The text a string expression spells with its literal parts: a
    literal, a `+` of literals, or an f-string's literal parts."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _folded(node.left), _folded(node.right)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant))
    return None


def _prose(tree) -> set[int]:
    """ids of the string expressions that are prose — a docstring or a bare
    string statement, the value of an `Expr` statement — which nothing
    imports by."""
    return {id(s.value) for s in ast.walk(tree) if isinstance(s, ast.Expr)}


def shim_references(sources: dict[str, str], read_prose: bool = False) -> list[str]:
    """Every reference to the shim in `sources`, as S1 states. `read_prose`
    turns the one exclusion off, so the witness below can show, with this
    reader, which prose the exclusion covers."""
    hits = []
    for path, src in sorted(sources.items()):
        package = list(Path(path).parent.parts)
        tree = ast.parse(src)
        prose = set() if read_prose else _prose(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                hits += [f"{path}:{node.lineno} import {a.name}" for a in node.names
                         if a.name == SHIM or a.name.startswith(SHIM + ".") or a.name.split(".")[-1] == SHIM_NAME]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package[:len(package) - node.level + 1]
                    module = ".".join(base + ([node.module] if node.module else []))
                else:
                    module = node.module or ""
                if module == SHIM or module.startswith(SHIM + ".") or module.split(".")[-1] == SHIM_NAME:
                    hits.append(f"{path}:{node.lineno} from {module} import ...")
                hits += [f"{path}:{node.lineno} from {module} import {a.name}"
                         for a in node.names if a.name == SHIM_NAME]
            if id(node) in prose:
                continue
            text = _folded(node)
            if text is not None and SHIM_SHAPED.search(text):
                hits.append(f"{path}:{node.lineno} a string naming the shim: {text.strip()[:60]!r}")
    return hits


def _tracked_python() -> dict[str, str]:
    return {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
            for p in tracked("*.py") if p.relative_to(ROOT).as_posix() != THIS_FILE}


def test_s1_nothing_imports_the_retired_shim_and_it_is_gone():
    sources = _tracked_python()
    print(f"S1 tracked Python files scanned: {len(sources)}")
    assert len(sources) > 300, "the population collapsed"
    assert not shim_references(sources), shim_references(sources)
    assert SHIM_FILE not in sources
    assert importlib.util.find_spec(SHIM) is None, (
        "the import system still finds the shim (an untracked copy?)")


@pytest.mark.parametrize("path,planted", [
    ("backend/loop/planted.py", "from .. import ollama_client\n"),
    ("backend/loop/planted.py", "from ..ollama_client import NUM_CTX\n"),
    ("scripts/planted.py", "from backend import ollama_client\n"),
    ("tests/planted.py", "import backend.ollama_client as legacy\n"),
    ("tests/planted.py", "import importlib\nm = importlib.import_module('backend.ollama_client')\n"),
    # N4: the dynamic forms the call rule missed
    ("tests/planted.py", "import importlib\nm = importlib.import_module('.ollama_client', 'backend')\n"),
    ("tests/planted.py", "import importlib\nm = importlib.import_module('backend.' + 'ollama_client')\n"),
    ("tests/planted.py", "LEGACY = 'backend.ollama_client'\n"),
    ("tests/planted.py", "pkg = 'backend'\nname = f'{pkg}.ollama_client'\n"),
    ("tests/planted.py", "import sys\nm = sys.modules['backend.ollama_client']\n"),
    ("tests/planted.py", "m = __import__('backend', fromlist=['ollama_client'])\n"),
    # N3 (the second verifier): the path-shaped and bare forms
    ("tests/planted.py", "import importlib.util\ns = importlib.util.spec_from_file_location('legacy', 'backend/ollama_client.py')\n"),
    ("tests/planted.py", "from pathlib import Path\np = Path('backend') / 'ollama_client.py'\n"),
    ("tests/planted.py", "import runpy\nrunpy.run_path('backend/ollama_client.py')\n"),
    ("tests/planted.py", "import pkgutil\nm = pkgutil.resolve_name('backend:ollama_client')\n"),
    ("tests/planted.py", "TARGET = 'backend.ollama_client.stream_chat'\n"),
    ("scripts/planted.py", "import sys\nsys.path.insert(0, 'backend')\nimport ollama_client\n"),
    ("scripts/planted.py", "from ollama_client import NUM_CTX\n"),
])
def test_s1c_companions_every_import_form_is_named(path, planted):
    hits = shim_references({path: planted})
    assert hits and hits[0].startswith(path), (path, planted, hits)


def test_s1_prose_naming_the_shim_is_not_a_reference():
    """Declared: a docstring, a bare string statement and a comment are
    prose — nothing imports by them — and may name the shim as history.
    Every string a program can act on is read; this is the one exclusion,
    and tests/_tracked.py's docstring is the live instance. The witness is
    the guard's own reader with the exclusion off (the review's R6: a
    substring search over the file could not tell a docstring from code):
    it names tests/_tracked.py's docstring, and with the exclusion on it
    names nothing — so the instance is prose and the exclusion is not
    vacuous. The other prose that names the shim is printed, not pinned:
    history may be written."""
    prose = ('"""The retired backend/ollama_client.py shim, backend.ollama_client, is history."""\n'
             'def f():\n'
             '    """see backend.ollama_client"""\n'
             '    "backend/ollama_client.py"\n'
             '    # import backend.ollama_client\n'
             '    return 1\n')
    assert shim_references({"tests/planted.py": prose}) == []
    sources = _tracked_python()
    with_prose = shim_references(sources, read_prose=True)
    print(f"S1 prose naming the shim, by the guard's reader with the exclusion off: {with_prose}")
    assert shim_references(sources) == []
    assert any(h.startswith("tests/_tracked.py:") and "a string naming the shim" in h for h in with_prose), (
        "the live instance of the exclusion is gone; the exclusion still stands, but say so here", with_prose)
