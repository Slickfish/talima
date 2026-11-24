"""Packet V.2 — arms S and D, and the script that finds what is missing.

WHY ARM S EXISTS
    V.1 changed what the extractor sends. Arm A is now the BARE prompt and no
    longer describes production, so the next A-vs-B table would compare two
    things neither of which is running. Arm S calls `extraction_prompt()` —
    the shipped function, imported not restated, for exactly the reason arm A
    imports `EXTRACTION_PROMPT`.

WHY ARM D EXISTS
    `other` measured 0.00 in all three arms: the model never abstains, so the
    53% miss is invention rather than honest abstention. That is an argument
    the hatch buys nothing OBSERVABLE — but removing it changes the prompt
    that produced 0.38, and this project's record on unmeasured prompt changes
    is why the harness exists at all. D settles it on the same corpus.

WHY THE PROPOSAL SCRIPT DOES NOT PROPOSE
    The store contains legitimate domain predicates and extraction artefacts
    in one list, and several spellings of the same claim.
    `primarily_metabolized_by` and `is_metabolized_by_primary_enzyme_of` are
    one claim twice. Only a domain expert can cut that, so the script ranks
    and hands over — the same stance `propose_contradiction_pairs.py` took.
"""
from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
from tests import _proc
import sys
from pathlib import Path

import pytest

from backend.cortex.extraction import EXTRACTION_PROMPT, extraction_prompt
from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore
from backend.kb.vocabulary import OTHER, load_relations, vocabulary_block
from scripts.measure_vocabulary_compliance import arm_prompt

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "propose_relation_vocabulary.py"
SAMPLE = "Warfarin is a drug. CYP3A4 metabolises it."

# Off-list, and the operator's own — two spellings of one claim, which is what
# makes this a candidate list rather than a vocabulary.
OFF_LIST = [
    ("TAK-441", "primarily_metabolized_by", "CYP3A4"),
    ("TAK-441", "is_metabolized_by_primary_enzyme_of", "CYP3A4"),
    ("warfarin", "primarily_metabolized_by", "CYP2C9"),
    ("aspirin",  "treats", "pain"),
]


# ===========================================================================
# Arm S — what actually ships
# ===========================================================================

def test_arm_s_is_the_shipped_prompt_byte_for_byte(monkeypatch):
    monkeypatch.delenv("NEXUS_EXTRACTION_VOCABULARY", raising=False)
    monkeypatch.delenv("NEXUS_RELATION_VOCABULARY", raising=False)
    assert arm_prompt("S", SAMPLE) == extraction_prompt(SAMPLE)


def test_arm_s_and_arm_a_differ_now_that_v1_shipped(monkeypatch):
    """THE REASON ARM S EXISTS. If these were equal, arm A would still
    describe production and V.2 would be unnecessary — so the difference is
    the assertion, not an incidental."""
    monkeypatch.delenv("NEXUS_EXTRACTION_VOCABULARY", raising=False)
    assert arm_prompt("S", SAMPLE) != arm_prompt("A", SAMPLE)
    assert arm_prompt("A", SAMPLE) == EXTRACTION_PROMPT.format(text=SAMPLE)


def test_arm_s_follows_the_switch(monkeypatch):
    """With the vocabulary disabled the shipped prompt IS the bare one, and
    arm S must say so rather than carrying a snapshot of what shipped when the
    harness was written."""
    monkeypatch.setenv("NEXUS_EXTRACTION_VOCABULARY", "off")
    assert arm_prompt("S", SAMPLE) == arm_prompt("A", SAMPLE)


def test_arm_s_calls_the_shipped_function_rather_than_rebuilding_it():
    """AST — the same rule arm A is held to. A harness that reconstructs the
    shipped prompt measures a prompt that only resembles production."""
    import scripts.measure_vocabulary_compliance as harness

    tree = ast.parse(Path(harness.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "arm_prompt")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "extraction_prompt" in called, (
        "arm_prompt() no longer calls the shipped prompt builder")


# ===========================================================================
# Arm D — B minus the escape hatch
# ===========================================================================

def test_arm_d_is_arm_b_without_the_hatch(monkeypatch):
    monkeypatch.delenv("NEXUS_RELATION_VOCABULARY", raising=False)
    b, d = arm_prompt("B", SAMPLE), arm_prompt("D", SAMPLE)

    hatch_line = f"If none of them fits, use `{OTHER}`.\n"
    assert hatch_line in b
    assert hatch_line not in d, f"arm D still offers the escape hatch:\n{d}"
    # ...and differs ONLY by that line, or D measures something else too.
    assert b.replace(hatch_line, "") == d, (
        "arm D differs from B by more than the hatch line")


def test_arm_d_still_offers_the_whole_vocabulary(monkeypatch):
    """NON-VACUITY. A D that dropped the list as well as the hatch would score
    like arm A and be read as 'the hatch mattered'."""
    monkeypatch.delenv("NEXUS_RELATION_VOCABULARY", raising=False)
    d = arm_prompt("D", SAMPLE)
    for rel in load_relations():
        assert rel in d, f"{rel} missing from arm D"


def test_the_hatch_switch_has_one_definition():
    """`vocabulary_block(escape_hatch=False)` — the harness does not build its
    own hatchless block, so the shipped and measured forms cannot drift."""
    rels = load_relations()
    assert vocabulary_block(rels, escape_hatch=False) + \
        f"If none of them fits, use `{OTHER}`.\n" == vocabulary_block(rels)


def test_the_shipped_prompt_still_carries_the_hatch(monkeypatch):
    """D is a MEASUREMENT, not a change. Until it says otherwise the shipped
    prompt keeps what was measured at 0.38."""
    monkeypatch.delenv("NEXUS_EXTRACTION_VOCABULARY", raising=False)
    assert f"`{OTHER}`" in extraction_prompt(SAMPLE)


@pytest.mark.parametrize("arm", ["A", "S", "B", "C", "D"])
def test_every_arm_renders_and_carries_the_payload(arm, monkeypatch):
    monkeypatch.delenv("NEXUS_EXTRACTION_VOCABULARY", raising=False)
    prompt = arm_prompt(arm, SAMPLE)
    assert SAMPLE in prompt
    assert "{text}" not in prompt, f"arm {arm} left the placeholder unformatted"


def test_the_arms_are_all_distinct(monkeypatch):
    """Two arms that render identically would report two independent
    measurements of one prompt."""
    monkeypatch.delenv("NEXUS_EXTRACTION_VOCABULARY", raising=False)
    rendered = {arm: arm_prompt(arm, SAMPLE) for arm in "ASBCD"}
    # S == B today (the shipped prompt IS arm B), and that is the point of
    # V.1 — so they are allowed to match each other and nothing else.
    assert rendered["S"] == rendered["B"], (
        "the shipped prompt is no longer arm B; V.1's measurement no longer "
        "describes production and the report must say so")
    others = {a: r for a, r in rendered.items() if a not in {"S", "B"}}
    assert len(set(others.values())) == len(others), "two arms are identical"
    assert rendered["A"] not in {rendered["B"], rendered["C"], rendered["D"]}, (
        "the bare prompt is identical to an offered arm")


def test_the_default_arm_list_includes_the_new_arms():
    """A harness that defines arms nobody runs measures nothing new."""
    import scripts.measure_vocabulary_compliance as harness

    src = Path(harness.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    defaults = [kw.value.value for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
                and any(isinstance(a, ast.Constant) and a.value == "--arms"
                        for a in node.args)
                for kw in node.keywords
                if kw.arg == "default" and isinstance(kw.value, ast.Constant)]
    assert defaults, "--arms has no default"
    arms = {a.strip() for a in defaults[0].split(",")}
    assert {"A", "S", "B", "C", "D"} <= arms, f"default arms are {sorted(arms)}"


# ===========================================================================
# The proposal script
# ===========================================================================

def _seed(tmp_path: Path) -> Path:
    db = tmp_path / "kb.db"
    store = TripleStore(db)
    for s, r, o in OFF_LIST:
        store.add(subject=s, relation=r, object=o, weight=0.6, tier=2,
                  granularity=Granularity.TOPIC, source="extraction")
    # one predicate that IS on the list, so "off-list" is doing work
    store.add(subject="water", relation="is_a", object="liquid", weight=0.7,
              tier=2, granularity=Granularity.TOPIC, source="offline_fixture")
    # ...and substrate rows, which must be excluded rather than proposed
    for i in range(3):
        store.add(subject=f"term{i}", relation="co_activated", object="query",
                  weight=0.5, tier=1, granularity=Granularity.SESSION,
                  source="hippocampus_consolidation")
    store.close()
    return db


def _run(db: Path, *extra: str) -> subprocess.CompletedProcess:
    return _proc.run(
        [sys.executable, str(SCRIPT), "--db", str(db), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT, timeout=180)


@pytest.mark.sqlite_only("scripts/propose_relation_vocabulary.py --store reads the SQLite file through the read-only open (law 15); the URL form is W3b part 3 (D9, T8)")
def test_the_script_ranks_off_list_predicates(tmp_path):
    """NON-VACUITY FIRST: the fixture must contain predicates the vocabulary
    does not offer, or 'it found none' would be the right answer for the
    wrong reason."""
    off = {r for _s, r, _o in OFF_LIST}
    assert not (off & set(load_relations())), (
        "the fixture's predicates are already on the list; this test cannot "
        "exercise the ranking path")

    proc = _run(_seed(tmp_path))
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = proc.stdout

    assert "primarily_metabolized_by" in out
    assert "treats" in out
    # the highest-count off-list predicate is ranked first
    lines = [l for l in out.splitlines() if l.strip().startswith("2 ")]
    assert any("primarily_metabolized_by" in l for l in lines), (
        f"the two-row predicate is not ranked at its count:\n{out}")


@pytest.mark.sqlite_only("scripts/propose_relation_vocabulary.py --store reads the SQLite file through the read-only open (law 15); the URL form is W3b part 3 (D9, T8)")
def test_the_script_excludes_substrate_relations(tmp_path):
    """`co_activated` is minted by consolidation, not by extraction. Offering
    it to the extractor would be a category error — it is a statement about
    two terms having fired together, not a claim anyone made."""
    proc = _run(_seed(tmp_path))
    out = proc.stdout
    assert "substrate-minted rows      : 3" in out, out
    assert "co_activated" not in _ranked_predicates(out), (
        "a substrate relation was proposed as domain vocabulary")


def _ranked_predicates(stdout: str) -> list[str]:
    """The predicates in the ranked block — parsed as `rows  predicate` rows
    rather than matched as substrings, so `is_a` cannot be found inside
    `is_metabolized_by_primary_enzyme_of`."""
    block = stdout.split("candidate list, for a human to cut")[-1]
    out = []
    for line in block.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            out.append(parts[1])
    return out


@pytest.mark.sqlite_only("scripts/propose_relation_vocabulary.py --store reads the SQLite file through the read-only open (law 15); the URL form is W3b part 3 (D9, T8)")
def test_the_script_does_not_propose_what_is_already_offered(tmp_path):
    ranked = _ranked_predicates(_run(_seed(tmp_path)).stdout)
    assert ranked, "nothing was ranked; the parse or the fixture is wrong"
    offered = set(load_relations())
    assert not (set(ranked) & offered), (
        f"on-list predicate(s) proposed: {sorted(set(ranked) & offered)}")


@pytest.mark.sqlite_only("scripts/propose_relation_vocabulary.py --store reads the SQLite file through the read-only open (law 15); the URL form is W3b part 3 (D9, T8)")
def test_the_written_file_is_the_vocabulary_plus_candidates(tmp_path):
    out_file = tmp_path / "relations.json"
    proc = _run(_seed(tmp_path), "--write", str(out_file))
    assert proc.returncode == 0, proc.stderr[-2000:]

    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert set(load_relations()) <= set(payload), (
        "the written file drops relations that are currently in force")
    assert "primarily_metabolized_by" in payload
    assert "co_activated" not in payload
    assert "EDIT IT" in proc.stdout, (
        "the script presents the file as installable rather than as a "
        "starting point")


@pytest.mark.sqlite_only("scripts/propose_relation_vocabulary.py --store reads the SQLite file through the read-only open (law 15); the URL form is W3b part 3 (D9, T8)")
def test_the_script_says_so_when_nothing_is_missing(tmp_path):
    """The other direction. A script that always finds candidates would be
    reporting its own fixture."""
    db = tmp_path / "clean.db"
    store = TripleStore(db)
    store.add(subject="water", relation="is_a", object="liquid", weight=0.7,
              tier=2, granularity=Granularity.TOPIC, source="offline_fixture")
    store.close()

    out = _run(db).stdout
    assert "Every predicate in this store is already offered" in out
    assert "OQ-2" in out, (
        "the empty case does not say where the remaining lever is")


@pytest.mark.sqlite_only("scripts/propose_relation_vocabulary.py --store reads the SQLite file through the read-only open (law 15); the URL form is W3b part 3 (D9, T8)")
def test_the_script_leaves_the_store_untouched(tmp_path):
    """Read-only, and asserted on the bytes rather than on the `mode=ro` in
    the source — a connection string is a claim, a checksum is evidence."""
    import hashlib

    db = _seed(tmp_path)
    before = hashlib.md5(db.read_bytes()).hexdigest()
    _run(db, "--write", str(tmp_path / "out.json"))
    assert hashlib.md5(db.read_bytes()).hexdigest() == before, (
        "the proposal script modified the store it was reading")


def _assert_opens_the_store_read_only(tree: ast.AST) -> None:
    """The guard's check, shared with its companion: the tree opens a store,
    and every open is the shared read-only open (tests/_readonly_opens.py:
    the class of calls, resolved through the tree's own imports, never the
    spelling)."""
    from tests._readonly_opens import read_only_problems, store_opens
    assert store_opens(tree), "the script opens no database"
    problems = read_only_problems(tree)
    assert not problems, f"the store is not opened read-only: {problems}"


def test_the_script_opens_the_store_read_only():
    """Belt as well as braces: the checksum test above would pass on a script
    that opened read-write and happened not to write."""
    # W1 — the store is opened through scripts/_readonly.py's open_read_only
    # (percent-encoded mode=ro, query_only, a write-refusing authorizer), and
    # the script connects nowhere itself: by AST (tests/_readonly_opens.py),
    # over the parsed tree, through any alias either call is bound to.
    _assert_opens_the_store_read_only(ast.parse(SCRIPT.read_text(encoding="utf-8")))


@pytest.mark.parametrize("planted,expected", [
    ("import sqlite3\nconn = sqlite3.connect(db)\n", "line 2: a direct connect (sqlite3.connect), not open_read_only"),
    ("import sqlite3 as s\nconn = s.connect(db)\n", "line 2: a direct connect (s.connect), not open_read_only"),
    ("from sqlite3 import Connection as C\nconn = C(db)\n", "line 2: a direct connect (C), not open_read_only"),
    ("import sqlite3\nconn = sqlite3.Connection(db)\n", "line 2: a direct connect (sqlite3.Connection), not open_read_only"),
    ("store = TripleStore(db)\nrows = store.all()\n", "the script opens no database"),
])
def test_companion_the_read_only_check_fails_on_a_direct_connect_and_on_a_script_that_never_opens(planted, expected):
    """COMPANION (W1 part 5f, SF5) for the guard above, through its own
    assertion: a direct connect under the plain name, a module alias, an
    imported-name alias, the constructor itself, and a source that opens
    nothing at all, each fail it by name."""
    import re
    with pytest.raises(AssertionError, match=re.escape(expected)):
        _assert_opens_the_store_read_only(ast.parse(planted))
    # ...and the script's own shape passes: one read-only open, no connect.
    _assert_opens_the_store_read_only(ast.parse(
        "from scripts._readonly import open_read_only as ro\nconn = ro(db)\n"))
