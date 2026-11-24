"""W11d (run/specs/W11d.md) — the specification is enough to build from, and each of its rules is one a verifier holds.

THE CLAIM (Amendment 10, the operator's): `docs/TALIMA_SPEC.md` states every rule normatively; "conformance means
talima-verify accepts an implementation's records"; "a second, minimal producer written from the spec alone (a
short script that imports nothing from backend/, enforced by a guard) creates a record and chain that
talima-verify accepts, and a planted deviation from each MUST rule is refused".

WHAT IS HELD HERE
    A1  Every document `conformance/minimal_producer.py` makes is accepted by `talima-verify`, run as a stranger
        runs it (isolated, from a folder holding the verifier and the documents and nothing else).
    A2  The producer is standard library only, by AST: it imports nothing of this project and no package.
        Companions plant an import of the backend, of a script, of a package, a relative one and a dynamic one.
    R1  The rules are DERIVED from the document: every `M-` rule has one row in section 13's table and every row
        one rule; the failure each row names is one the verifier documents; and a sentence that says MUST outside
        a rule (and outside section 1, which defines the word) fails.
    R2  For EVERY rule so derived, one deviation is planted in the producer (one function or constant replaced, so
        everything else it makes is recomputed and consistent) and `talima-verify` must refuse the document with
        the failure the table names. A rule with no deviation fails R2 by name: the table below is the test's, the
        rule list is the document's.
    R3  Companions for R2 itself: a rule added to the document with no deviation is named; a deviation that
        changes nothing is named, because its document verifies.
    V1  The document's test vectors: each file is the bytes its row names (SHA-256) and gets the verdict its row
        shows, given to the verifier as the row says. A changed digest and a row for a file that is not there
        are named.
    X1  The document's worked examples hold for both implementations: the second producer's functions and the
        verifier's give the encodings, roots, commitment and signature the document prints.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from tests import _proc
from tests._tracked import REPO

SPEC = REPO / "docs" / "TALIMA_SPEC.md"
PRODUCER = REPO / "conformance" / "minimal_producer.py"
VERIFIER = ("talima_verify.py", "nexus_verify.py")
SEED = "00112233"
RULE = re.compile(r"^- \*\*(M-[A-Z]+-\d+)\.\*\* ", re.M)
ROW = re.compile(r"^\| (M-[A-Z]+-\d+) \| [^|]+ \| `([a-z_]+)` \|$", re.M)


def producer():
    """The producer as a fresh module: a rule replaced in one test is whole again in the next."""
    spec = importlib.util.spec_from_file_location("minimal_producer_under_test", PRODUCER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rules(text: str) -> list:
    return RULE.findall(text)


def table(text: str) -> dict:
    return dict(ROW.findall(text))


def stranger(folder: Path, args: list) -> dict:
    """`talima-verify --json` as a stranger runs it, in `folder`: its exit and its verdict."""
    for name in VERIFIER:
        shutil.copy2(REPO / "scripts" / name, folder / name)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    proc = _proc.run([sys.executable, "-I", VERIFIER[0], *args, "--json"], cwd=folder, env=env, timeout=120,
                     capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        verdict = json.loads(proc.stdout)
    except ValueError:
        verdict = {"verified": False, "failures": [f"no verdict: {proc.stdout[-300:]} {proc.stderr[-300:]}"]}
    return {"exit": proc.returncode, **verdict}


def arguments(folder: Path) -> dict:
    """How each of the producer's documents is given to the verifier."""
    pin = ["--identity", "identity.json"]
    salt = (folder / "commitment_salt.txt").read_text(encoding="utf-8").strip()
    return {"credential": ["credential.json", *pin], "full": ["credential_full.json", *pin],
            "reveal": ["--reveal", "commitment_receipt.json", "--content", "commitment_content.txt", "--salt", salt,
                       *pin],
            "action": ["--action", "action_receipt.json", *pin], "chain": ["--chain", "chain.json", *pin]}


def made(folder: Path, plant=None):
    """The producer's documents in `folder`, after `plant(module)` replaced one of its rules."""
    module = producer()
    if plant is not None:
        plant(module)
    folder.mkdir(parents=True, exist_ok=True)
    assert module.main([str(folder), "--seed", SEED]) == 0
    return module


# ---------------------------------------------------------------- A1, A2

def test_a1_every_document_the_second_producer_makes_is_accepted(tmp_path):
    made(tmp_path / "out")
    for name, args in arguments(tmp_path / "out").items():
        verdict = stranger(tmp_path / "out", args)
        assert verdict["exit"] == 0 and verdict["verified"] and verdict["failures"] == [], (name, verdict)
    full = stranger(tmp_path / "out", arguments(tmp_path / "out")["full"])
    assert any(n.startswith("record_recomputed:") for n in full["notes"]), full["notes"]
    chain = stranger(tmp_path / "out", arguments(tmp_path / "out")["chain"])
    assert chain["blocks"] == 3 and chain["first_index"] == 0, chain


# What the second producer imports, what it asks of each module and which built-in names it uses: allowlists. "Is in
# the standard library" let through the standard library's own ways to load and run other code (the first
# verifier's V6: importlib under an alias, runpy, pkgutil, builtins, subprocess), and each list of what to refuse
# let through the next thing (delta check 2's G10: `getattr(argparse, '_os')`, `json.codecs.builtins`; delta check
# 3's H5: a frame's names, `breakpoint()`, a module's thing stored over).
ASKS = {"argparse": {"ArgumentParser"}, "hashlib": {"sha256", "sha512"}, "json": {"dumps"}, "os": {"urandom"},
        "sys": {"exit"}}                           # each imported whole and under its own name
NAMES_FROM = {"__future__": {"annotations"}, "pathlib": {"Path"}}      # the only names it imports out of a module
BUILT_IN = frozenset({"__name__", "bytes", "dict", "int", "isinstance", "len", "list", "open", "pow", "print", "set",
                      "sorted", "str"})            # the built-in names it uses
MAY_BIND = frozenset({"__init__"})                 # the one built-in's name it binds: a method's
INTERNALS = ("f_", "gi_", "cr_", "ag_", "tb_", "co_")      # a frame's, a generator's, a traceback's, a code object's


def _bound(tree) -> set:
    """Every name the source binds, anywhere: the reading does not follow scopes, so it asks for more."""
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.alias):
            bound.add((node.asname or node.name).split(".")[0])
        else:
            bound |= {getattr(node, slot) for slot in ("name", "arg", "rest")
                      if isinstance(getattr(node, slot, None), str) and not isinstance(node, ast.keyword)}
    return bound


def import_problems(source: str) -> list:
    """Why a producer's source is more than these modules, asked for these things, with these built-in names.
    Every import is one of the five, whole and under its own name, or one of the two names taken out of a
    module. A module's name stands in the source one way only, read as `module.thing` for a thing the table
    allows: handed along, renamed, rebound, stored through or asked for anything else, it is refused. A name the
    source does not bind is one of the built-in names listed, and the source binds no built-in's name. No
    attribute begins with an underscore (the way from any object to its class, its globals and the modules it
    keeps for itself), is a frame's or a generator's own, or is named like a module of the standard library
    (`.os`, `.sys`, `.codecs`: how one module is read out of another). And nothing is a way to load or run code
    the source does not spell (the forms the interceptor's guard refuses, `tests/_import_graph.py`). A reading
    of names as written, like that guard, and not complete: a name put together as the code runs is past it."""
    import builtins
    from tests._import_graph import unreadable_loads
    problems, tree = [], ast.parse(source)
    bound = _bound(tree)
    asked = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load)
             and isinstance(n.value, ast.Name) and n.attr in ASKS.get(n.value.id, ())}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            problems += [f"line {node.lineno}: imports {a.name}{' as ' + a.asname if a.asname else ''}"
                         for a in node.names if a.name not in ASKS or a.asname]
        elif isinstance(node, ast.ImportFrom):
            allowed = set() if node.level else NAMES_FROM.get(node.module, set())
            problems += [f"line {node.lineno}: imports {a.name} from {'.' * node.level}{node.module or ''}"
                         for a in node.names if a.name not in allowed or a.asname]
        elif isinstance(node, ast.Name) and node.id in ASKS and id(node) not in asked:
            problems.append(f"line {node.lineno}: uses {node.id} other than for {', '.join(sorted(ASKS[node.id]))}")
        elif isinstance(node, ast.Name) and node.id not in bound and node.id not in BUILT_IN:
            problems.append(f"line {node.lineno}: uses the built-in name {node.id}, which the producer has no use for")
        elif isinstance(node, ast.Attribute) and (node.attr.startswith(("_",) + INTERNALS)
                                                  or node.attr in sys.stdlib_module_names):
            problems.append(f"line {node.lineno}: reaches through .{node.attr}")
        elif not isinstance(node, (ast.alias, ast.keyword)) and any(getattr(node, slot, None) in ASKS
                                                                    for slot in ("name", "arg", "rest")):
            problems.append(f"line {node.lineno}: binds a module's name to something else")
    problems += [f"binds the built-in name {name}" for name in sorted(bound - MAY_BIND) if hasattr(builtins, name)]
    problems += [f"line {line}: {what}" for _rel, line, what in unreadable_loads({"conformance/producer.py": source})]
    return problems


def test_a2_the_second_producer_is_the_standard_library_alone():
    source = PRODUCER.read_text(encoding="utf-8")
    assert import_problems(source) == []
    assert "from __future__ import annotations" in source and len(source.splitlines()) < 450, "a short script"


@pytest.mark.parametrize("plant", [
    "from backend.memory import merkle", "import backend.memory.chain as chain", "from scripts import nexus_verify",
    "import cryptography", "from . import helper", "from tests._tracked import REPO",
    "import importlib\nmerkle = importlib.import_module('backend.memory.merkle')",
    "merkle = __import__('backend.memory.merkle')",
    # the first verifier's nine (V6): each loaded or ran the project's code, and the guard said nothing
    "from importlib import import_module as load\nmerkle = load('backend.memory.merkle')",
    "import importlib.util as u\nspec = u.spec_from_file_location('m', 'backend/memory/merkle.py')",
    "import pkgutil\nmerkle = pkgutil.resolve_name('backend.memory.merkle')",
    "import builtins\nmerkle = builtins.__import__('backend.memory.merkle')",
    "from runpy import run_path as rp\nrp('scripts/nexus_verify.py')",
    "from importlib.machinery import SourceFileLoader\nm = SourceFileLoader('m', 'scripts/nexus_verify.py')",
    "merkle = sys.modules.get('backend.memory.merkle')",
    "import subprocess\nsubprocess.run([sys.executable, 'scripts/nexus_verify.py'])",
    "import builtins\ngetattr(builtins, 'ex' + 'ec')('import backend')",
    # and their neighbours, which name nothing the nine do
    "os.system('python scripts/nexus_verify.py')", "import os as o\no.popen('python -m backend.mcp')",
    "from os import execv", "import json as j", "import shutil", "merkle = __builtins__['__import__']('backend')",
    "import hashlib.blake2 as b",
    # delta check 1's D15: `os` under another name, by a literal name, through another module, through itself
    "o = os\no.system('x')", "(o,) = (os,)\no.system('x')", "(o := os).system('x')", "print(os)",
    "getattr(os, 'system')('x')", "getattr(os, 'popen')('x')", "from pathlib import os as o\no.system('x')",
    "import pathlib\npathlib.os.system('x')", "from argparse import _os as o", "argparse._os.system('x')",
    "os.urandom.__self__.system('x')", "from hashlib import sha256 as s",
    # delta check 2's G10: a module handed along by its name, read out of another by a literal name, reached
    # through a module's own imports; and neighbours that name nothing those three do
    "getattr(argparse, '_os').system('x')", "import pathlib\ngetattr(pathlib, 'os').system('x')",
    "json.codecs.builtins.exec('import backend')", "h = hashlib\nh.new('md5')", "print([json][0])",
    "sys.settrace(print)", "json.loads('1')", "hashlib.sha256.__self__", "().__class__.__base__",
    "setattr(sys, 'argv', [])", "def f(json):\n    return json", "f = lambda sys: sys",
    "for hashlib in ():\n    pass", "argparse.ArgumentParser.__init__.__defaults__", "delattr(Path, 'home')",
    # one plant for each line of the reading that the others do not reach: a module read out of an object by its
    # own name, a private attribute, and a module's name bound to something else with no use of it after
    "Path('.').parser.os.system('x')", "Path('.')._flavour.sep", "def os():\n    pass", "f = lambda sys: 0",
    "class json:\n    pass", "try:\n    pass\nexcept ValueError as hashlib:\n    pass",
    # delta check 3's H5: a module read out of a frame's names, a debugger, a module's thing stored over or
    # deleted, a module's name bound by a pattern; and built-in names the producer has no use for
    "def g():\n    yield it.gi_frame.f_back.f_locals\nit = g()\nnext(it)['os'].system('x')", "breakpoint()",
    "os.urandom = print", "del hashlib.sha512", "sys.exit = print", "json.dumps = sorted",
    "hashlib.sha256 = hashlib.sha512", "match {}:\n    case {**sys}:\n        pass", "help()", "t = type(print)",
    "x = memoryview(b'')", "c = classmethod(print)",
    "x = (i for i in ()).gi_frame", "def g(breakpoint):\n    pass\nbreakpoint()",
])
def test_a2_companions_an_import_that_is_not_the_standard_librarys_is_named(plant):
    source = PRODUCER.read_text(encoding="utf-8")
    assert import_problems(source + "\n" + plant + "\n") != [], plant


# ---------------------------------------------------------------- R1: the rules, derived

def unruled_musts(text: str) -> list:
    """Sentences that say MUST outside a rule: every block of the document (a paragraph, a list item) that holds
    the word and does not open as a rule, section 1 excepted (it defines the word)."""
    body = text.split("\n## 2. ", 1)[1]
    blocks = re.split(r"\n(?=- )|\n\s*\n", body)
    return [b.strip()[:90] for b in blocks if re.search(r"\bMUST\b", b) and not RULE.match(b.strip())
            and not b.strip().startswith("|")]


def verifier_failures() -> set:
    """The failure names `nexus_verify.py` documents: the head of its docstring's list."""
    doc = ast.get_docstring(ast.parse((REPO / "scripts" / "nexus_verify.py").read_text(encoding="utf-8")))
    listed = doc.split("Checked, and the name each failure carries:")[1].split("Said in a note")[0]
    return set(re.findall(r"^\s{2}([a-z_]+)\s{2,}", listed, re.M)) | set(re.findall(r"\b([a-z]+_[a-z_]+)\b", listed))


def test_r1_every_rule_has_one_row_and_every_row_a_failure_the_verifier_names():
    text = SPEC.read_text(encoding="utf-8")
    listed, rows = rules(text), table(text)
    assert len(listed) >= 40 and len(listed) == len(set(listed)), "the derivation lost the rules, or one is twice"
    assert set(listed) == set(rows), (sorted(set(listed) ^ set(rows)))
    assert unruled_musts(text) == []
    unknown = {rule: failure for rule, failure in rows.items() if failure not in verifier_failures()}
    assert unknown == {}, f"the table names failures the verifier does not document: {unknown}"


SAID = re.compile(r"([a-z]+(?:_[a-z]+)*): ")


def unlisted_names(source: str) -> set:
    """The names the verifier's code says (a message opens `name: `) that its own page, the docstring at its
    head, lists neither as a failure nor as a note. A proof refusal the binding repeats is listed once, as
    `binding_*`."""
    tree = ast.parse(source)
    page = ast.get_docstring(tree)
    listed = page.split("Checked, and the name each failure carries:")[1]
    known = set(re.findall(r"^\s{2}([a-z_]+)\s{2,}", listed, re.M)) | set(re.findall(r"\b([a-z]+_[a-z_]+)\b", listed))
    said = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value != page:
            found = SAID.match(node.value)
            if found and not node.value.startswith(("http", "error: ")):
                said.add(found.group(1))
    return {name for name in said if name not in known and not (name.startswith("binding_") and "binding_*" in listed)}


def unlisted_in(names: list) -> list:
    """The failure names of a real verdict that the verifier's own page does not list (`binding_*` stands there
    for each proof refusal a binding repeats)."""
    listed = verifier_failures()
    return [name for name in names if name not in listed and not name.startswith("binding_")]


def test_r1c_the_verifiers_own_page_lists_every_name_it_says():
    """Its docstring's list went stale twice (delta check 2's G17: `bare_hash_leaves` named nowhere). The list is
    held to the code: every name a message opens with is one the page lists. That reads a lower-case name that
    opens a string as written; a name built as the code runs is past it (delta check 3's H11), so every failure
    name R2 and R4 see in a real verdict is held to the page as well (`unlisted_in`)."""
    source = (REPO / "scripts" / "nexus_verify.py").read_text(encoding="utf-8")
    assert unlisted_names(source) == set()
    page = ast.get_docstring(ast.parse(source))
    assert "Said in a note" in page and not {"bare_hash_leaves", "partial_chain"} & verifier_failures(), \
        "a note is listed apart: it is never a failure"
    planted = source.replace('failures.append("commitments_mismatch: ', 'failures.append("a_new_refusal: ', 1)
    assert planted != source and unlisted_names(planted) == {"a_new_refusal"}
    # what that reading cannot see (a name built as the code runs) is held where verdicts are read: R2 and R4
    assert unlisted_in(["malformed", "binding_root_mismatch", "a_name_built_at_run_time"]) == \
        ["a_name_built_at_run_time"]


def test_r1b_the_pages_that_count_the_rules_count_the_documents():
    """The changelog said 45 when the document had 44 (the first verifier's V3). Wherever it gives the number, it
    gives the number of rules the document has now, and wherever it counts the clauses planted, the number planted.
    It gives the first at least once in every tree: the public changelog is the part above its marker, which
    says it once and does not count the clauses (the trial public snapshot failed here on "twice")."""
    n = len(rules(SPEC.read_text(encoding="utf-8")))
    log = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    stated = re.findall(r"(?:each of its|`docs/TALIMA_SPEC.md`,) (\d+) rules", log)
    assert stated and all(int(s) == n for s in stated), (stated, n)
    clauses = re.findall(r"a deviation for (\d+) clauses", log)
    assert all(int(c) == len(CLAUSES) for c in clauses), (clauses, len(CLAUSES))
    public = log.split("<!-- The public changelog is everything above this line")[0]
    assert re.findall(r"(?:each of its|`docs/TALIMA_SPEC.md`,) (\d+) rules", public) == [str(n)], "the public part"


def test_r1_companions_a_must_outside_a_rule_and_a_rule_with_no_row_are_named():
    text = SPEC.read_text(encoding="utf-8")
    loose = text.replace("\n## 15. ", "\nA producer MUST also keep its promises.\n\n## 15. ", 1)
    assert unruled_musts(loose) == ["A producer MUST also keep its promises."]
    more = text.replace("\n## 4. ", "\n- **M-NEW-1.** A thing MUST be so.\n\n## 4. ", 1)
    assert set(rules(more)) - set(table(more)) == {"M-NEW-1"}


# ---------------------------------------------------------------- R2: a deviation for every rule

def _replaced(name: str, make):
    """Replace one module-level function or constant: `make(module, original)` gives the new one."""
    def plant(m):
        setattr(m, name, make(m, getattr(m, name)))
    return plant


def _body(change):
    """Change each record's body before it is committed and hashed."""
    def plant(m):
        original = m.Producer.record

        def record(self, body):
            body = dict(body)
            change(body)
            return original(self, body)
        m.Producer.record = record
    return plant


def _document(name: str, change):
    """Change one document as its builder returns it (the builder's own rule is what is replaced)."""
    def plant(m):
        original = getattr(m, name)

        def build(*args, **kwargs):
            document = original(*args, **kwargs)
            change(m, document)
            return document
        setattr(m, name, build)
    return plant


def _both(*plants):
    def plant(m):
        for p in plants:
            p(m)
    return plant


def _method(name: str, make):
    def plant(m):
        setattr(m.Producer, name, make(m, getattr(m.Producer, name)))
    return plant


def _no_commitment_for(field: str):
    def plant(m):
        original = m.Producer.record

        def record(self, body):
            rec = original(self, body)
            del rec["commitments"][field]
            rec["root"] = m.commitment_root(rec["commitments"])
            return rec
        m.Producer.record = record
    return plant


def _dump(m, **options):
    return lambda value: json.dumps(value, **options).encode("utf-8")


def _other_records_commitments(m, document):
    document["commitments"] = {name: m.H(c.encode()).hex() for name, c in document["commitments"].items()}
    document["commitment_root"] = m.commitment_root({n: bytes.fromhex(c)
                                                     for n, c in document["commitments"].items()}).hex()
    document["disclosed"] = {}


def _action_field_added(m, original):
    def an_action(*args, **kwargs):
        action = original(*args, **kwargs)
        action["fields"]["note"] = "a nineteenth member"
        action["hash"] = m.action_hash(action["fields"])
        return action
    return an_action


def _another_person(m, original):
    def an_action(*args, **kwargs):
        action = original(*args, **kwargs)
        action["person"] = "someone-else@example.org"
        return action
    return an_action


# rule -> (the document the verifier is given, the one change planted in the producer)
DEVIATIONS = {
    "M-CAN-1": ("credential", _replaced("canonical", lambda m, o: _dump(m, separators=(",", ":")))),
    "M-CAN-2": ("credential", _replaced("canonical", lambda m, o: _dump(m, sort_keys=True))),
    "M-CAN-3": ("credential", _replaced("canonical", lambda m, o: _dump(m, sort_keys=True, separators=(",", ":"),
                                                                        ensure_ascii=False))),
    "M-CAN-4": ("credential", _replaced("canonical", lambda m, o: (
        lambda v: o(v).replace(b"1700000010.5", b"1.7000000105e+09")))),
    "M-CAN-5": ("full", _both(_replaced("canonical", lambda m, o: _dump(m, sort_keys=True, separators=(",", ":"))),
                              _body(lambda body: body.__setitem__("forecast", float("nan"))))),
    "M-CAN-6": ("credential", ("credential.json", lambda text: text.replace(
        '"version": ', '"version": 2,\n "version": ', 1))),
    "M-CAN-7": ("full", _body(lambda body: body.__setitem__("model_id", "x\ud800y"))),
    "M-CAN-8": ("credential", _document("credential", lambda m, d: d["disclosed"]["model_id"].__setitem__(
        "salt", d["disclosed"]["model_id"]["salt"].upper()))),
    "M-REC-1": ("credential", _body(lambda body: body.pop("contradictions"))),
    "M-REC-2": ("full", _body(lambda body: body.__setitem__("ts", str(body["ts"])))),
    "M-REC-3": ("full", _replaced("record_hash", lambda m, o: (
        lambda body: m.H(m.canonical({k: v for k, v in body.items() if k != "lineage_id"}))))),
    "M-REC-4": ("full", _body(lambda body: body.__setitem__("record_scope", "full_turn"))),
    "M-COM-1": ("credential", _replaced("commitment", lambda m, o: (lambda salt, value: m.H(m.canonical(value) + salt)))),
    "M-COM-2": ("credential", _replaced("SALT_BYTES", lambda m, o: 16)),
    "M-COM-3": ("credential", _no_commitment_for("model_id")),
    "M-COM-4": ("credential", _replaced("commitment_leaf", lambda m, o: (lambda name, c: name.encode("utf-8") + c))),
    "M-MRK-1": ("credential", _replaced("leaf_hash", lambda m, o: (lambda leaf: m.H(leaf)))),
    "M-MRK-2": ("credential", _replaced("audit_path", lambda m, o: (lambda leaves, i: list(reversed(o(leaves, i)))))),
    "M-BLK-1": ("credential", _replaced("block_hash", lambda m, o: (
        lambda header: m.H(m.canonical({k: v for k, v in header.items() if k != "roots"})).hex()))),
    "M-BLK-2": ("credential", _replaced("header_of", lambda m, o: (
        lambda *a: {k: v for k, v in o(*a).items() if k != "snapshot_hash"}))),
    "M-BLK-3": ("credential", _replaced("header_of", lambda m, o: (lambda *a: {**o(*a), "kind": "snapshot"}))),
    "M-BLK-4": ("credential", _replaced("lineage_leaf", lambda m, o: (lambda rec: rec["hash"]))),
    "M-BLK-5": ("credential", _replaced("root_entry", lambda m, o: (
        lambda leaves: {**o(leaves), "size": len(leaves) + 1}))),
    "M-CHN-1": ("chain", _method("next_index", lambda m, o: (lambda self: 2 * len(self.blocks)))),
    "M-CHN-2": ("chain", _method("previous_hash", lambda m, o: (
        lambda self: m.H(b"another block").hex() if self.blocks else m.GENESIS))),
    "M-CHN-3": ("chain", _replaced("GENESIS", lambda m, o: "11" * 32)),
    "M-KEY-1": ("credential", _replaced("key_id", lambda m, o: (lambda public: m.H(b"not the key" + public).hex()))),
    "M-KEY-2": ("credential", _method("keys", lambda m, o: (
        lambda self: [{**k, "announced_in": 5} for k in o(self)]))),
    "M-SIG-1": ("credential", _replaced("statement", lambda m, o: (lambda index, block_hash: bytes.fromhex(block_hash)))),
    "M-SIG-2": ("credential", _document("credential", lambda m, d: d.__setitem__("signatures", []))),
    "M-SIG-3": ("credential", _document("credential", lambda m, d: d["signatures"][0].__setitem__(
        "block_index", d["signatures"][0]["block_index"] + 1))),
    "M-DOC-1": ("credential", _replaced("CREDENTIAL", lambda m, o: "talima.credential")),
    "M-DOC-2": ("credential", _document("credential", lambda m, d: d["proof"].__setitem__("state", "unsealed"))),
    "M-DOC-3": ("credential", _document("credential", _other_records_commitments)),
    "M-DOC-4": ("credential", _document("credential", lambda m, d: d["disclosed"]["model_id"].__setitem__(
        "value", "another-model"))),
    "M-DOC-5": ("credential", _document("credential", lambda m, d: d.__setitem__("decision_fields", ["forecast"]))),
    "M-DOC-6": ("full", _document("credential", lambda m, d: d["proof"].__setitem__("lineage_id", "another-record"))),
    "M-RCP-1": ("reveal", _replaced("COMMITMENT_RECEIPT", lambda m, o: "talima.commitment")),
    "M-RCP-2": ("reveal", _replaced("held_commitment", lambda m, o: (
        lambda salt, content: m.H(salt + content.encode("utf-8"))))),
    "M-RCP-3": ("reveal", _replaced("commit_leaf", lambda m, o: (lambda held: m.H(held["commitment"])))),
    "M-ACT-1": ("action", _replaced("ACTION_RECEIPT", lambda m, o: "talima.action")),
    "M-ACT-2": ("action", _replaced("an_action", _action_field_added)),
    "M-ACT-3": ("action", _replaced("action_hash", lambda m, o: (
        lambda fields: m.H(m.canonical({k: v for k, v in fields.items() if k != "status"}))))),
    "M-ACT-4": ("action", _replaced("action_leaf", lambda m, o: (lambda action: m.H(action["hash"])))),
    "M-ACT-5": ("action", _replaced("an_action", _another_person)),
    "M-CHD-1": ("chain", _replaced("CHAIN", lambda m, o: "talima.chain")),
    "M-CHD-2": ("chain", _document("chain_document", lambda m, d: d["blocks"][0]["leaves"]["lineage"].__setitem__(
        0, m.H(b"another leaf").hex() * 2))),
    "M-ANC-1": ("credential", _document("credential", lambda m, d: d.__setitem__("anchors", [{
        "block_index": d["proof"]["block"]["index"], "method": "rfc3161", "authority": "https://tsa.example.org",
        "token": base64.b64encode(b"not a time-stamp").decode("ascii"), "anchored_at": 1700000050}]))),
}


def deviation_refused(rule: str, folder: Path, deviations=None) -> "str | None":
    """None when the rule's deviation is refused with the failure the document's table names; else why not."""
    deviations = DEVIATIONS if deviations is None else deviations
    expected = table(SPEC.read_text(encoding="utf-8")).get(rule)
    if rule not in deviations:
        return f"{rule} has no deviation: a rule nothing breaks is a rule nothing shows the verifier holds"
    which, plant = deviations[rule]
    edit = None
    if isinstance(plant, tuple):                    # a change no producer's object can hold: made in the file's text
        (name, edit), plant = plant, None
    made(folder, plant)
    if edit is not None:
        (folder / name).write_text(edit((folder / name).read_text(encoding="utf-8")), encoding="utf-8", newline="")
    verdict = stranger(folder, arguments(folder)[which])
    names = [f.split(":")[0] for f in verdict.get("failures", [])]
    if verdict["exit"] == 0 or verdict.get("verified"):
        return f"{rule}: the deviation verifies ({which}), so it broke nothing the verifier checks"
    if unlisted_in(names):
        return f"{rule}: the verifier said {unlisted_in(names)}, which its own page does not list"
    if expected not in names:
        return f"{rule}: the verifier refused it as {names}, and the document's table says {expected!r}"
    return None


@pytest.mark.parametrize("rule", rules(SPEC.read_text(encoding="utf-8")))
def test_r2_a_deviation_from_each_rule_is_refused_with_the_failure_the_document_names(tmp_path, rule):
    assert deviation_refused(rule, tmp_path / "out") is None


def test_r2_there_is_no_deviation_for_a_rule_the_document_does_not_have():
    assert set(DEVIATIONS) == set(rules(SPEC.read_text(encoding="utf-8")))


def test_r3_companions_a_rule_with_no_deviation_and_a_deviation_that_changes_nothing_are_named(tmp_path):
    some = rules(SPEC.read_text(encoding="utf-8"))[0]
    assert "has no deviation" in deviation_refused(some, tmp_path / "none", deviations={})
    nothing = {some: ("credential", lambda m: None)}
    assert "the deviation verifies" in deviation_refused(some, tmp_path / "nothing", deviations=nothing)
    other = {some: ("credential", DEVIATIONS["M-SIG-2"][1])}
    assert "the document's table says" in deviation_refused(some, tmp_path / "other", deviations=other)


# ---------------------------------------------------------------- R4: the clauses one deviation a rule leaves out

def _set(builder: str, path: tuple, make):
    """Set one member of one document, by its path, to `make(old value)`."""
    def change(m, document):
        at = document
        for key in path[:-1]:
            at = at[key]
        at[path[-1]] = make(at[path[-1]]) if path[-1] in at or isinstance(at, list) else make(None)
    return _document(builder, change)


def _header(**members):
    return _replaced("header_of", lambda m, o: (lambda *a: {**o(*a), **members}))


def _loose_canonical():
    return _replaced("canonical", lambda m, o: _dump(m, sort_keys=True, separators=(",", ":")))


def _an_empty_tree(m):
    """The third block seals one tree with no leaves, and the producer gives it a root that is not the empty one."""
    append, entry = m.Producer.append, m.root_entry
    m.Producer.append = lambda self, timestamp, leaves: append(self, timestamp, leaves or {"commit": []})
    m.root_entry = lambda leaves: entry(leaves) if leaves else {**entry(leaves), "root": m.H(b"not empty").hex()}


def _a_chain_from_block_one_that_claims_the_genesis(m):
    m.Producer.previous_hash = lambda self: m.GENESIS
    build = m.chain_document

    def chain(producer):
        document = build(producer)
        document["blocks"] = document["blocks"][1:2]
        document["signatures"] = document["signatures"][1:2]
        return document
    m.chain_document = chain


def _twice(name: str):
    """The file's text with one member given twice: a forged value first, the real one after it."""
    def edit(text: str) -> str:
        needle = f'"{name}": '
        assert text.count(needle) >= 1
        return text.replace(needle, f'"{name}": 2,\n "{name}": ', 1)
    return edit


def _two_leaves_under_each_root(m):
    """Every tree the producer seals over one leaf gets a second, so a receipt's path is not empty."""
    append = m.Producer.append
    m.Producer.append = lambda self, timestamp, leaves: append(self, timestamp, {
        name: ls + [m.H(b"another leaf under " + name.encode())] if len(ls) == 1 else ls
        for name, ls in leaves.items()})


def _upper(path: list) -> list:
    assert path and any(x != x.upper() for x in path), "the plant needs a path with a letter in it"
    return [x.upper() for x in path]


def _a_binding_leaf_of_a_bare_hash(m):
    """The third block seals a `binding` root (the reference producer's own) over one leaf of 32 bytes."""
    append = m.Producer.append
    m.Producer.append = lambda self, timestamp, leaves: append(self, timestamp,
                                                               leaves or {"binding": [m.H(b"a record")]})


def _an_identity_commitment_in_upper_case(m, original):
    def an_action(*args, **kwargs):
        action = original(*args, **kwargs)
        action["fields"]["identity_commitment"] = action["fields"]["identity_commitment"].upper()
        action["hash"] = m.action_hash(action["fields"])
        return action
    return an_action


def _roots_in_upper_case(m):
    """Every root the producer seals is written in upper case, and hashed and signed so."""
    entry = m.root_entry
    m.root_entry = lambda leaves: {**entry(leaves), "root": entry(leaves)["root"].upper()}


def _by_hand(**period):
    """The producer's key as a person pins one by hand: its id and its public key, and the period given."""
    return _method("keys", lambda m, o: (lambda self: [{"key_id": k["key_id"], "public_key": k["public_key"],
                                                        **period} for k in o(self)]))


def _an_anchor(index):
    return {"block_index": index, "method": "rfc3161", "authority": "https://tsa.example.org",
            "token": base64.b64encode(b"x").decode("ascii"), "anchored_at": 1700000050}


# (what is broken, the rule it is a clause of, the document, the plant, the failure the verifier names)
CLAUSES = [
    # the first verifier's V1: a value JSON cannot carry, where a block header is hashed
    ("a NaN in a block header, in a chain", "M-CAN-5", "chain",
     _both(_loose_canonical(), _header(timestamp=float("nan"))), "malformed"),
    ("a NaN in a block header, under a credential", "M-CAN-5", "credential",
     _both(_loose_canonical(), _header(timestamp=float("nan"))), "malformed"),
    ("a NaN in a member no hash covers", "M-CAN-5", "credential",
     _set("credential", ("scope", "note"), lambda old: float("nan")), "malformed"),
    ("a number past every float, in a member no hash covers", "M-CAN-5", "credential",
     ("credential.json", lambda text: text.replace('"anchors": [],', '"anchors": [],\n "weight": 1e400,', 1)), "malformed"),
    # V4: an integer is a number written with no fraction and no exponent; `true` and `1.0` are not one
    ("a credential's version as true", "M-DOC-1", "credential", _set("credential", ("version",), lambda v: True), "malformed"),
    ("a credential's version as 1.0", "M-DOC-1", "credential", _set("credential", ("version",), float), "malformed"),
    ("a commitment receipt's version as true", "M-RCP-1", "reveal",
     _set("commitment_receipt", ("version",), lambda v: True), "malformed"),
    ("an action receipt's version as 1.0", "M-ACT-1", "action", _set("action_receipt", ("version",), float), "malformed"),
    ("a chain document's version as true", "M-CHD-1", "chain", _set("chain_document", ("version",), lambda v: True), "malformed"),
    ("a signature's block index as a float", "M-SIG-3", "credential",
     _set("credential", ("signatures", 0, "block_index"), float), "malformed"),
    ("a leaf index as true", "M-MRK-2", "credential", _set("credential", ("proof", "leaf_index"), lambda v: True), "malformed"),
    ("a tree size as a float", "M-MRK-2", "credential", _set("credential", ("proof", "tree_size"), float), "malformed"),
    ("the proof's block index as false", "M-CHN-1", "credential",
     _set("credential", ("proof", "block", "index"), lambda v: False), "malformed"),
    ("a commitment receipt's tree size as true", "M-RCP-3", "reveal",
     _set("commitment_receipt", ("tree_size",), lambda v: True), "malformed"),
    ("an action receipt's leaf index as false", "M-ACT-4", "action",
     _set("action_receipt", ("leaf_index",), lambda v: False), "malformed"),
    ("a chain block's index as a float", "M-CHN-1", "chain", _set("chain_document", ("blocks", 1, "index"), float), "malformed"),
    # V9, V10, V11: what a header and a chain may not say
    ("a kind stated and empty", "M-BLK-3", "credential", _header(kind=""), "block_malformed"),
    ("an empty tree whose root is not the empty root", "M-MRK-1", "chain", _an_empty_tree, "block_malformed"),
    ("lineage leaves that are neither a record hash and a commitment root nor a bare record hash", "M-BLK-4", "chain",
     _replaced("lineage_leaf", lambda m, o: (lambda rec: rec["hash"] + rec["root"][:16])), "leaf_malformed"),
    ("a commit leaf that is not 32 bytes", "M-RCP-3", "chain",
     _replaced("commit_leaf", lambda m, o: (lambda held: o(held)[:31])), "leaf_malformed"),
    ("a first block after the genesis that claims the genesis", "M-CHN-3", "chain",
     _a_chain_from_block_one_that_claims_the_genesis, "chain_link_broken"),
    # V7: a proof names a record only where the credential discloses that name
    ("a proof that names another record", "M-DOC-6", "full",
     _set("credential", ("proof", "lineage_id"), lambda v: "another-record"), "lineage_id_mismatch"),
    ("a proof that names a record its credential does not", "M-DOC-6", "credential",
     _set("credential", ("proof", "lineage_id"), lambda v: "00" * 16), "lineage_id_mismatch"),
    # V13, V14, V15, V17
    ("an unpaired surrogate in a string", "M-CAN-7", "full",
     _body(lambda body: body.__setitem__("model_id", "x\ud800y")), "malformed"),
    ("a salt in upper case", "M-CAN-8", "credential",
     _set("credential", ("disclosed", "model_id", "salt"), str.upper), "malformed"),
    ("a signature in upper case", "M-CAN-8", "credential",
     _set("credential", ("signatures", 0, "value"), str.upper), "malformed"),
    ("a chain's leaves in upper case", "M-CAN-8", "chain",
     _set("chain_document", ("blocks", 0, "leaves", "lineage"), lambda leaves: [x.upper() for x in leaves]), "malformed"),
    ("a name given twice in one object", "M-CAN-6", "credential", ("credential.json", _twice("version")), "malformed"),
    ("an action receipt whose id is not its fields'", "M-ACT-2", "action",
     _set("action_receipt", ("action_id",), lambda v: "another-action"), "malformed"),
    # the clauses that were held and had no deviation (the first verifier removed each check and no test failed)
    ("a header member of another type", "M-BLK-2", "credential", _header(snapshot_path=5), "block_malformed"),
    ("a header whose layout is another", "M-BLK-2", "credential", _header(layout=3), "block_malformed"),
    ("snapshot_missing stated and false", "M-BLK-3", "credential", _header(snapshot_missing=False), "block_malformed"),
    ("a root's size as text", "M-BLK-2", "credential",
     _replaced("root_entry", lambda m, o: (lambda leaves: {**o(leaves), "size": str(len(leaves))})), "block_malformed"),
    ("a commitment receipt's size that is not the header's", "M-RCP-3", "reveal",
     _set("commitment_receipt", ("tree_size",), lambda v: v + 1), "size_mismatch"),
    ("an action receipt's size that is not the header's", "M-ACT-4", "action",
     _set("action_receipt", ("tree_size",), lambda v: v + 1), "size_mismatch"),
    ("an anchor for a block the document does not rest on", "M-ANC-1", "credential",
     _set("credential", ("anchors",), lambda v: [{"block_index": 7, "method": "rfc3161", "authority": "https://tsa.example.org",
                                                 "token": base64.b64encode(b"x").decode("ascii"), "anchored_at": 1700000050}]),
     "anchor_invalid"),
    ("scope stated at its default", "M-REC-4", "full", _body(lambda b: b.__setitem__("scope", "default")), "record_malformed"),
    ("learning_mode stated at its default", "M-REC-4", "full",
     _body(lambda b: b.__setitem__("learning_mode", "live")), "record_malformed"),
    ("identity_mechanism stated at its default", "M-REC-4", "full",
     _body(lambda b: b.__setitem__("identity_mechanism", "asserted")), "record_malformed"),
    ("record_scope stated and empty", "M-REC-4", "full", _body(lambda b: b.__setitem__("record_scope", "")), "record_malformed"),
    ("record_scope stated as null", "M-REC-4", "full", _body(lambda b: b.__setitem__("record_scope", None)), "record_malformed"),
    ("injected that is not an array", "M-REC-2", "full", _body(lambda b: b.__setitem__("injected", "x")), "record_malformed"),
    ("model_id that is not a string", "M-REC-2", "full", _body(lambda b: b.__setitem__("model_id", 5)), "record_malformed"),
    ("a hash field in upper case", "M-REC-2", "full",
     _body(lambda b: b.__setitem__("query_hash", b["query_hash"].upper())), "record_malformed"),
    ("a key whose period closed before the block", "M-KEY-2", "credential",
     _method("keys", lambda m, o: (lambda self: [{**k, "until_block": 0} for k in o(self)])), "signature_outside_key_period"),
    ("a leaf dropped from a chain's list", "M-CHD-2", "chain",
     _set("chain_document", ("blocks", 0, "leaves", "lineage"), lambda leaves: leaves[:-1]), "size_mismatch"),
    # delta check 1: the identity's own keys (D3), a receipt on a block that calls itself layout 1 (D4), a list
    # where an object belongs (D17). The rules of a chain (D13) are held where a chain is shown, and R5 says so.
    ("a pinned key whose period's end is a float", "M-KEY-2", "credential",
     ("identity.json", lambda text: text.replace('"until_block": null', '"until_block": 5.0', 1)), "malformed"),
    ("a pinned key announced in false", "M-KEY-2", "credential",
     ("identity.json", lambda text: text.replace('"announced_in": 0', '"announced_in": false', 1)), "malformed"),
    ("a commitment receipt on a block that calls itself layout 1", "M-BLK-2", "reveal",
     _set("commitment_receipt", ("block", "layout"), lambda v: 1), "block_malformed"),
    ("an action receipt on a block that calls itself layout 1", "M-BLK-2", "action",
     _set("action_receipt", ("block", "layout"), lambda v: 1), "block_malformed"),
    ("a list where the disclosures belong", "M-DOC-4", "credential",
     _set("credential", ("disclosed",), lambda v: [5]), "malformed"),
    # delta check 1's D16: checks that no deviation reached
    ("a credential's version as 2", "M-DOC-1", "credential", _set("credential", ("version",), lambda v: 2), "malformed"),
    ("a commitment receipt's version as 2", "M-RCP-1", "reveal", _set("commitment_receipt", ("version",), lambda v: 2), "malformed"),
    ("an action receipt's version as 2", "M-ACT-1", "action", _set("action_receipt", ("version",), lambda v: 2), "malformed"),
    ("a chain document's version as 2", "M-CHD-1", "chain", _set("chain_document", ("version",), lambda v: 2), "malformed"),
    ("the proof's block index that is not its header's", "M-BLK-1", "credential",
     _set("credential", ("proof", "block", "index"), lambda v: v + 1), "block_hash_mismatch"),
    ("a receipt's block index that is not its header's", "M-BLK-1", "reveal",
     _set("commitment_receipt", ("block", "index"), lambda v: v + 1), "block_hash_mismatch"),
    ("a block's layout as a float", "M-BLK-2", "credential",
     _set("credential", ("proof", "block", "layout"), float), "malformed"),
    ("a kind that is not a string", "M-BLK-3", "credential", _header(kind=5), "block_malformed"),
    ("a root that is not a hash", "M-BLK-2", "credential",
     _replaced("root_entry", lambda m, o: (lambda leaves: {**o(leaves), "root": "zz"})), "block_malformed"),
    ("a path element in upper case", "M-CAN-8", "credential",
     _set("credential", ("proof", "path"), lambda path: [x.upper() for x in path]), "malformed"),
    ("a commitment in upper case", "M-CAN-8", "credential",
     _set("credential", ("commitments", "model_id"), str.upper), "malformed"),
    ("a held commitment in upper case", "M-CAN-8", "reveal",
     _set("commitment_receipt", ("commitment",), str.upper), "malformed"),
    ("a pinned public key in upper case", "M-CAN-8", "credential",
     ("identity.json", lambda text: __import__("re").sub(r'("public_key": ")([0-9a-f]{64})(")',
                                                         lambda m: m.group(1) + m.group(2).upper() + m.group(3), text)), "malformed"),
    ("a name that holds half a character pair", "M-CAN-7", "credential",
     ("credential.json", lambda text: text.replace('"anchors": [],', '"anchors": [],\n "\\ud800": 1,', 1)), "malformed"),
    ("minus infinity", "M-CAN-5", "credential",
     ("credential.json", lambda text: text.replace('"anchors": [],', '"anchors": [],\n "weight": -Infinity,', 1)), "malformed"),
    ("a number below every float", "M-CAN-5", "credential",
     ("credential.json", lambda text: text.replace('"anchors": [],', '"anchors": [],\n "weight": -1e400,', 1)), "malformed"),
    # delta check 2. G7: the layout a block is labelled with is held to what its document rests on
    ("the proof's block labelled layout 3", "M-BLK-2", "credential",
     _set("credential", ("proof", "block", "layout"), lambda v: 3), "block_malformed"),
    ("the proof's block labelled layout 0", "M-BLK-2", "credential",
     _set("credential", ("proof", "block", "layout"), lambda v: 0), "block_malformed"),
    # G2: one key pinned by hand states no period, or it states both ends
    ("a key pinned with an end and no start", "M-KEY-2", "credential", _by_hand(until_block=5),
     "signature_outside_key_period"),
    # G8: upper case where the verifier compares and does not decode is named by the comparison
    ("a credential's commitment root in upper case", "M-CAN-8", "credential",
     _set("credential", ("commitment_root",), str.upper), "commitments_mismatch"),
    ("a proof's commitment root in upper case", "M-CAN-8", "credential",
     _set("credential", ("proof", "commitment_root"), str.upper), "leaf_mismatch"),
    ("a proof's record hash in upper case", "M-CAN-8", "credential",
     _set("credential", ("proof", "record_hash"), str.upper), "leaf_mismatch"),
    ("a signature's block hash in upper case", "M-CAN-8", "credential",
     _set("credential", ("signatures", 0, "block_hash"), str.upper), "signature_invalid"),
    ("a signature's key id in upper case", "M-CAN-8", "credential",
     _set("credential", ("signatures", 0, "key_id"), str.upper), "signature_invalid"),
    ("every key id in upper case, the pinned one too", "M-CAN-8", "credential",
     _replaced("key_id", lambda m, o: (lambda public: o(public).upper())), "key_id_mismatch"),
    ("a chain block's hash in upper case", "M-CAN-8", "chain",
     _set("chain_document", ("blocks", 0, "block_hash"), str.upper), "block_hash_mismatch"),
    ("an action's record hash in upper case", "M-CAN-8", "action",
     _set("action_receipt", ("record_hash",), str.upper), "action_mismatch"),
    ("an action's identity commitment in upper case, hashed so", "M-CAN-8", "action",
     _replaced("an_action", _an_identity_commitment_in_upper_case), "disclosure_mismatch"),
    ("a header's hash member in upper case", "M-CAN-8", "credential", _header(snapshot_hash="AB" * 32),
     "block_malformed"),
    # G11: checks no deviation reached (each was removed alone, and every test of this file passed)
    ("an action leaf that is not 32 bytes", "M-ACT-4", "chain",
     _replaced("action_leaf", lambda m, o: (lambda action: o(action)[:31])), "leaf_malformed"),
    ("a binding leaf that is not 64 bytes", "M-BLK-4", "chain", _a_binding_leaf_of_a_bare_hash, "leaf_malformed"),
    ("an anchor's block index as a float", "M-ANC-1", "credential", _document("credential", lambda m, d: d.__setitem__(
        "anchors", [_an_anchor(float(d["proof"]["block"]["index"]))])), "malformed"),
    ("a document's own key whose period's end is a float", "M-KEY-2", "credential",
     _set("credential", ("keys", 0, "until_block"), lambda v: 5.0), "malformed"),
    ("a key's from_block as a float", "M-KEY-2", "credential",
     _method("keys", lambda m, o: (lambda self: [{**k, "from_block": 0.0} for k in o(self)])), "malformed"),
    ("a chain block's layout as a float", "M-BLK-2", "chain",
     _set("chain_document", ("blocks", 1, "layout"), float), "malformed"),
    ("a header whose index is negative", "M-BLK-2", "credential", _header(index=-1), "block_malformed"),
    ("an action receipt's block index that is not its header's", "M-BLK-1", "action",
     _set("action_receipt", ("block", "index"), lambda v: v + 1), "block_hash_mismatch"),
    ("a chain block's index that is not its header's", "M-BLK-1", "chain",
     _set("chain_document", ("blocks", 1, "index"), lambda v: v + 1), "block_hash_mismatch"),
    ("an action's identity salt in upper case", "M-CAN-8", "action",
     _set("action_receipt", ("disclosed", "accountable_identity", "salt"), str.upper), "malformed"),
    ("a commitment receipt's path in upper case", "M-CAN-8", "reveal",
     _both(_two_leaves_under_each_root, _set("commitment_receipt", ("path",), _upper)), "malformed"),
    ("an action receipt's path in upper case", "M-CAN-8", "action",
     _both(_two_leaves_under_each_root, _set("action_receipt", ("path",), _upper)), "malformed"),
    # delta check 3. H2: a root inside a header is the header's, in a receipt as in a credential and a chain
    ("a commitment receipt on a block whose roots are in upper case", "M-CAN-8", "reveal", _roots_in_upper_case,
     "block_malformed"),
    ("an action receipt on a block whose roots are in upper case", "M-CAN-8", "action", _roots_in_upper_case,
     "block_malformed"),
    ("a commitment receipt on a block whose root is not a hash", "M-BLK-2", "reveal",
     _replaced("root_entry", lambda m, o: (lambda leaves: {**o(leaves), "root": "zz"})), "block_malformed"),
    ("an action receipt on a block whose root is not a hash", "M-BLK-2", "action",
     _replaced("root_entry", lambda m, o: (lambda leaves: {**o(leaves), "root": "zz"})), "block_malformed"),
    # H4: lines with no witness (each removed alone, and both files passed)
    ("a commitment receipt's block index as true", "M-CHN-1", "reveal",
     _set("commitment_receipt", ("block", "index"), lambda v: True), "malformed"),
    ("an action receipt's block layout as a float", "M-BLK-2", "action",
     _set("action_receipt", ("block", "layout"), float), "malformed"),
    ("a chain block labelled layout 3", "M-BLK-2", "chain",
     _set("chain_document", ("blocks", 1, "layout"), lambda v: 3), "block_malformed"),
    ("a chain block with no layout", "M-BLK-2", "chain",
     _document("chain_document", lambda m, d: d["blocks"][1].pop("layout")), "block_malformed"),
]


def clause_refused(which: str, plant, expected: str, folder: Path) -> "str | None":
    """None when the clause's deviation is refused with the failure named; else why not."""
    edit = None
    if isinstance(plant, tuple):
        (name, edit), plant = plant, None
    made(folder, plant)
    if edit is not None:
        path = folder / name
        path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8", newline="")
    verdict = stranger(folder, arguments(folder)[which])
    names = [f.split(":")[0] for f in verdict.get("failures", [])]
    if verdict["exit"] == 0 or verdict.get("verified"):
        return f"the deviation verifies ({which}), so it broke nothing the verifier checks"
    if unlisted_in(names):
        return f"the verifier said {unlisted_in(names)}, which its own page does not list"
    if expected not in names:
        return f"the verifier refused it as {names}, not as {expected!r}"
    return None


def test_r4b_a_key_with_an_end_and_no_start_is_refused_for_the_reason_the_document_gives(tmp_path):
    """Section 8: such a key "has no period a `seal` signature can be held to: nothing says where it opened". The
    refusal said the block was past the period's end, which it is not (delta check 3's H7)."""
    made(tmp_path / "out", _by_hand(until_block=5))
    verdict = stranger(tmp_path / "out", arguments(tmp_path / "out")["credential"])
    said = [f for f in verdict["failures"] if f.startswith("signature_outside_key_period")]
    assert len(said) == 1 and "where it opened" in said[0] and "an end at block 5 and no start" in said[0], said
    assert "over a later block" not in said[0] and "blocks before 5" not in said[0], said


def test_r4c_a_failure_name_the_verifiers_page_does_not_list_is_named(tmp_path, monkeypatch):
    """The companion of `unlisted_in` where it is used: with a page that lists one name only, a refusal R2 and R4
    would otherwise take as the one expected is named for what the page lacks."""
    monkeypatch.setattr(sys.modules[__name__], "verifier_failures", lambda: {"block_malformed"})
    plant = _set("credential", ("disclosed", "model_id", "salt"), str.upper)
    assert "its own page does not list" in clause_refused("credential", plant, "malformed", tmp_path / "clause")
    assert "its own page does not list" in deviation_refused("M-CAN-8", tmp_path / "rule")


# What a conforming producer MAY do, and the verifier must go on accepting (delta check 1: the first fix round
# refused the first three). Each is planted like a deviation, and the document must still verify.
ALLOWED = [
    ("a record whose fields are named like the document's own typed members", "full", _body(lambda b: b.update(
        {"index": "x", "size": 2.5, "version": None, "layout": [1], "until_block": "never", "leaf_index": True,
         "tree_size": {"a": 1}, "block_index": 1.0, "announced_in": "0", "from_block": [], "proof_version": "x"}))),
    ("the same record with one field disclosed", "credential", _body(lambda b: b.update(
        {"index": "x", "size": 2.5, "version": None, "until_block": "never"}))),
    ("a key entry with a member of the producer's own", "credential",
     _method("keys", lambda m, o: (lambda self: [{**k, "version": "x", "size": 1.5} for k in o(self)]))),
    ("an action receipt with a statement of the producer's own beside it", "action",
     _set("action_receipt", ("per_person",), lambda v: {"version": "x", "index": 2.5})),
    ("a chain whose lineage leaves are bare record hashes, the older form", "chain",
     _replaced("lineage_leaf", lambda m, o: (lambda rec: rec["hash"]))),
    ("a proof version and a seal of the producer's own", "credential",
     _set("credential", ("proof", "proof_version"), lambda v: "x")),
    # a credential or a receipt shows one block: what its header says of the block before it is a chain's rule,
    # held where a chain is shown (the specification says so; delta check 1's D13)
    ("a receipt on a block whose header claims the genesis", "reveal",
     _method("previous_hash", lambda m, o: (lambda self: m.GENESIS))),
    # delta check 2's G6: a member of the producer's own is typed by where it stands in the document it stands in.
    # What one kind of document types, another kind may name as it likes.
    ("a credential with members of its producer's own named like a receipt's and a chain's", "credential",
     _document("credential", lambda m, d: d.update({"leaf_index": "x", "tree_size": 1.5, "block": {"layout": "x"},
                                                    "blocks": [{"index": "x", "layout": 1.5}]}))),
    ("a commitment receipt with members named like a credential's and a chain's", "reveal",
     _document("commitment_receipt", lambda m, d: d.update({"proof": {"leaf_index": "x", "block": {"layout": "x"}},
                                                            "blocks": [{"index": "x"}]}))),
    ("an action receipt with members named like a credential's and a chain's", "action",
     _document("action_receipt", lambda m, d: d.update({"proof": {"tree_size": "x", "binding": {"leaf_index": 1.5}},
                                                        "blocks": [{"layout": "x"}]}))),
    ("a chain with members named like a credential's and a receipt's", "chain",
     _document("chain_document", lambda m, d: d.update({"leaf_index": "x", "tree_size": 1.5, "block": {"index": "x"},
                                                        "proof": {"leaf_index": "x", "binding": {"tree_size": "x"}}}))),
    # G2: one public key pinned by hand, stating no period, is active at every block
    ("a key pinned by hand that states no period", "credential", _by_hand()),
]


@pytest.mark.parametrize("case", ALLOWED, ids=[c[0] for c in ALLOWED])
def test_r5_what_the_document_leaves_to_a_producer_still_verifies(tmp_path, case):
    what, which, plant = case
    made(tmp_path / "out", plant)
    verdict = stranger(tmp_path / "out", arguments(tmp_path / "out")[which])
    assert verdict["exit"] == 0 and verdict["verified"] and verdict["failures"] == [], (what, verdict)


def test_r5b_a_key_and_a_salt_typed_in_upper_case_on_the_command_line_are_taken(tmp_path):
    """The document's rule about case is a document's. What a person types after `--key` or `--salt` is not one:
    a credential that verified against a key typed in upper case still does (delta check 1's D11)."""
    made(tmp_path / "out")
    folder = tmp_path / "out"
    public = json.loads((folder / "identity.json").read_text(encoding="utf-8"))["public_key"]
    salt = (folder / "commitment_salt.txt").read_text(encoding="utf-8").strip()
    assert stranger(folder, ["credential.json", "--key", public.upper()])["verified"]
    assert stranger(folder, ["--reveal", "commitment_receipt.json", "--content", "commitment_content.txt", "--salt",
                             salt.upper(), "--identity", "identity.json"])["verified"]
    # delta check 2's G9: and with the spaces a person leaves around one, or between its bytes
    spaced = " ".join(public[i:i + 2] for i in range(0, 64, 2))
    for typed in (f"  {public.upper()} ", spaced, public + "\t"):
        assert stranger(folder, ["credential.json", "--key", typed])["verified"], typed
    for typed in (salt + " ", f" {salt.upper()}", " ".join(salt[i:i + 8] for i in range(0, 64, 8))):
        assert stranger(folder, ["--reveal", "commitment_receipt.json", "--content", "commitment_content.txt",
                                 "--salt", typed, "--identity", "identity.json"])["verified"], typed


def test_r5c_an_argument_that_is_not_one_and_a_file_that_is_not_there_are_no_verdict(tmp_path):
    """Section 13: "a file that is not there, or an argument that is not one, is no verdict: status 2 and a
    message", and that holds when JSON was asked for. A salt that is one and is too short is a verdict: it opens
    nothing (delta check 2's G4, G9, G11)."""
    made(tmp_path / "out")
    folder = tmp_path / "out"
    public = json.loads((folder / "identity.json").read_text(encoding="utf-8"))["public_key"]
    salt = (folder / "commitment_salt.txt").read_text(encoding="utf-8").strip()
    reveal = ["--reveal", "commitment_receipt.json", "--content", "commitment_content.txt", "--identity",
              "identity.json", "--salt"]
    for args in (["credential.json", "--key", "zz"], ["credential.json", "--key", public[:-1]],
                 ["credential.json", "--key", public + "00"], ["credential.json", "--key", ""],
                 ["credential.json", "--key", "0x" + public], [*reveal, "zz"], [*reveal, salt[:-1]],
                 [*reveal, "0x" + salt], [*reveal, "g" * 64],
                 ["no-such-credential.json", "--identity", "identity.json"],
                 ["credential.json", "--identity", "no-such-identity.json"],
                 ["--chain", "no-such-chain.json", "--identity", "identity.json"]):
        got = stranger(folder, args)
        assert got["exit"] == 2 and got["failures"][0].startswith("no verdict"), (args, got)
    short = stranger(folder, [*reveal, salt[:32]])
    assert short["exit"] == 1 and [f.split(":")[0] for f in short["failures"]] == ["unsalted_hash"], short
    # delta check 3's H6: a salt is an argument of --reveal; beside a credential it is not read, as before
    stray = stranger(folder, ["credential.json", "--identity", "identity.json", "--salt", "zz"])
    assert stray["exit"] == 0 and stray["verified"], stray


def test_r5d_an_identity_that_lists_no_keys_is_not_taken_for_no_identity(tmp_path):
    """`--identity` was given: a file whose `keys` is null, or not a list, is an identity that cannot be read,
    never "no --identity or --key was given" (delta check 2's G18)."""
    made(tmp_path / "out")
    folder = tmp_path / "out"
    identity = json.loads((folder / "identity.json").read_text(encoding="utf-8"))
    for keys in (None, 5, "x", {}):
        (folder / "identity_bad.json").write_text(json.dumps({**identity, "keys": keys}), encoding="utf-8")
        got = stranger(folder, ["credential.json", "--identity", "identity_bad.json"])
        names = [f.split(":")[0] for f in got["failures"]]
        assert got["exit"] == 2 and names == ["malformed"] and "identity" in got["failures"][0], (keys, got)


@pytest.mark.parametrize("clause", CLAUSES, ids=[c[0] for c in CLAUSES])
def test_r4_a_clause_one_deviation_a_rule_leaves_out_is_refused_too(tmp_path, clause):
    """R2 plants one deviation for each rule, and a rule has clauses: with the checks of fifteen of them removed
    from the verifier, every test of this file passed (the first verifier's V23), and deviations from others
    verified (V1, V4, V7, V9 to V15). Each clause here names its rule, and its rule is one of the document's."""
    what, rule, which, plant, expected = clause
    assert rule in rules(SPEC.read_text(encoding="utf-8")), f"{what}: {rule} is not a rule of the document"
    assert clause_refused(which, plant, expected, tmp_path / "out") is None, what


# ---------------------------------------------------------------- V1: the vectors

GOLDEN = REPO / "tests" / "fixtures" / "tb_golden"
VECTOR = re.compile(r"^\| `([^`]+)` \| `([0-9a-f]{64})` \| ([^|]+) \| ?(verified)? ?\|$", re.M)


def vector_digest(path: Path) -> str:
    """A vector's SHA-256 as the document names it: of the file's bytes as they are committed. A JSON vector is
    committed with LF line ends, and a checkout that rewrote them to CRLF holds the same document, so they are
    read back as LF here; any other file is hashed exactly as it stands."""
    data = path.read_bytes()
    return hashlib.sha256(data.replace(b"\r\n", b"\n") if path.suffix == ".json" else data).hexdigest()


def vector_problems(text: str, folder: Path, golden: Path = GOLDEN) -> list:
    """Every way the document's vector table disagrees with the files and the verifier; empty when it does not."""
    rows, problems = VECTOR.findall(text), []
    folder.mkdir(parents=True, exist_ok=True)
    for name, digest, _how, _verdict in rows:
        path = golden / name
        if not path.is_file():
            problems.append(f"{name}: no such vector")
            continue
        if vector_digest(path) != digest:
            problems.append(f"{name}: the file is not the bytes the document names")
        shutil.copy2(path, folder / path.name)
    for name, _digest, how, verdict in rows:
        if verdict and (golden / name).is_file():
            got = stranger(folder, how.strip().strip("`").split())
            if got["exit"] != 0 or not got.get("verified"):
                problems.append(f"{name}: not verified as the document says: {got.get('failures')}")
    return problems + ([] if len(rows) >= 8 else [f"only {len(rows)} vectors are listed"])


def test_v1_each_vector_is_the_bytes_named_and_gets_the_verdict_shown(tmp_path):
    assert vector_problems(SPEC.read_text(encoding="utf-8"), tmp_path / "stranger") == []


def test_v1_companions_a_changed_digest_and_a_missing_file_are_named(tmp_path):
    text = SPEC.read_text(encoding="utf-8")
    name, digest, _how, _verdict = VECTOR.findall(text)[0]
    flipped = ("0" if digest[0] != "0" else "1") + digest[1:]
    assert vector_problems(text.replace(digest, flipped), tmp_path / "a") == \
        [f"{name}: the file is not the bytes the document names"]
    assert f"no-such-{name}: no such vector" in vector_problems(text.replace(f"`{name}`", f"`no-such-{name}`", 1),
                                                                tmp_path / "b")


def test_v1_the_vectors_hold_on_a_checkout_that_rewrote_their_line_ends(tmp_path):
    """A Windows checkout turns LF into CRLF in text files (`.gitattributes` records two earlier cases, and the
    vectors had no rule there). A JSON vector is then other bytes and the same document: its digest is taken over
    the line ends it was committed with, and `.gitattributes` now keeps a new checkout's bytes as committed. The
    held content is never treated so: its line ends are part of what the commitment is over."""
    text = SPEC.read_text(encoding="utf-8")
    golden = tmp_path / "fixtures" / "tb_golden"
    golden.mkdir(parents=True)
    (tmp_path / "fixtures" / "w11d").mkdir()
    for name, _digest, _how, _verdict in VECTOR.findall(text):
        data = (GOLDEN / name).read_bytes()
        assert name.endswith(".txt") or b"\r" not in data, f"{name} is committed with LF line ends"
        (golden / name).write_bytes(data if name.endswith(".txt") else data.replace(b"\n", b"\r\n"))
    assert vector_problems(text, tmp_path / "stranger", golden=golden) == []
    attributes = (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert "tests/fixtures/tb_golden/** -text" in attributes and "tests/fixtures/w11d/** -text" in attributes
    content = golden / "commitment_content.txt"
    assert b"\r\n" in content.read_bytes(), "the population: the held content has line ends of its own"
    content.write_bytes(content.read_bytes().replace(b"\r\n", b"\n"))
    assert "commitment_content.txt: the file is not the bytes the document names" in \
        vector_problems(text, tmp_path / "other", golden=golden)


# ---------------------------------------------------------------- X1: the worked examples

def examples(text: str) -> dict:
    return json.loads(text.split("```json talima-examples\n", 1)[1].split("\n```", 1)[0])


def test_x2_the_verifier_hashes_no_header_that_holds_what_json_cannot_carry():
    """The reader refuses a NaN before any hash is taken. A caller that hands the verifier an object of its own
    making (no file was read) is held to the same rule where a header is hashed (the first verifier's V1)."""
    from scripts import nexus_verify as nv
    for value in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            nv._canonical_hash({"layout": 2, "timestamp": value})
        with pytest.raises(ValueError):
            nv.strict_json('{"timestamp": %s}' % ("NaN" if value != value else "Infinity"))
    with pytest.raises(ValueError, match="twice"):
        nv.strict_json('{"a": 1, "a": 2}')
    assert nv.strict_json('{"a": 1.5, "b": [true, null], "c": "\u00e9"}') == {"a": 1.5, "b": [True, None], "c": "\u00e9"}


def test_x1_the_worked_examples_hold_for_the_second_producer_and_for_the_verifier():
    from scripts import nexus_verify as nv
    ex, m = examples(SPEC.read_text(encoding="utf-8")), producer()
    assert len(ex["canonical"]) >= 12 and len(ex["merkle"]) >= 5
    for row in ex["canonical"]:
        assert nv.canonical(row["value"]).decode("ascii") == row["encoding"] == \
            m.canonical(row["value"]).decode("ascii"), row
    for row in ex["merkle"]:
        leaves = [bytes.fromhex(x) for x in row["leaves"]]
        assert nv.merkle_root(leaves).hex() == row["root"] == m.merkle_root(leaves).hex(), row
    c = ex["commitment"]
    salt = bytes.fromhex(c["salt"])
    assert nv.commitment(salt, c["value"]).hex() == c["commitment"] == m.commitment(salt, c["value"]).hex()
    r = ex["commitment_root"]
    held = {name: bytes.fromhex(value) for name, value in r["commitments"].items()}
    assert nv.commitment_root(held).hex() == r["root"] == m.commitment_root(held).hex()
    s = ex["signature"]
    seed, public = bytes.fromhex(s["seed_rfc8032_test_1"]), bytes.fromhex(s["public_key"])
    statement = m.statement(s["block_index"], s["block_hash"])
    assert m.public_key(seed) == public and m.key_id(public) == s["key_id"] and statement.hex() == s["statement"]
    assert statement == nv.STATEMENT_PREFIX + s["block_index"].to_bytes(8, "big") + bytes.fromhex(s["block_hash"])
    assert m.sign(seed, statement).hex() == s["signature"]
    assert nv.ed25519_verify(public, statement, bytes.fromhex(s["signature"]))
    assert not nv.ed25519_verify(public, statement + b"x", bytes.fromhex(s["signature"]))
