"""Packet W.2 — the harness measures what production does, on a copy.

DEFECT C, AND WHY IT MATTERED
    W.1's harness reproduced `search_terms`' selection rule in Python, because
    constructing a `TripleStore` migrates the database it opens (H.2) and a
    read-only measurement must not rewrite the store it measures. The
    reproduction was faithful about SELECTION and wrong about RANKING: it
    sorted the merged pool by `(tier, stored weight)` while production sorts by
    `(tier, effective_prominence)` and then caps co-activations at 20% of the
    output.

    Measured on a store two thirds full of high-weight tier-0 association rows,
    those two orderings disagree completely — the harness reported a claim NOT
    FOUND that production retrieves. A measurement tool that under-reports the
    thing it measures is worse than no tool, because its output is quoted.

    The drift guard that was supposed to catch this asserted `real ⊆ mine` on
    candidate selection only. It stayed green throughout.

THE FIX, AND WHAT THIS FILE NOW GUARDS
    Copy the store to a temp file and run the REAL `TripleStore`,
    `SemanticIndex` and `TripleRetriever` against the copy. There is no
    reproduction left to drift. What needs guarding instead is that the copy
    discipline holds and that nobody reintroduces a private ranking.

    These tests never reach a provider.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "measure_retrieval.py"

# W.2 — the copy-and-close discipline is a CLASS, not one script's habit. Both
# of these drive the real store classes against a copy, and both would take the
# operator's Windows `PermissionError [WinError 32]` if they let a temp
# directory be removed while SQLite still held the file. Sweeping the class
# rather than the instance is the standing rule; the alternative is finding out
# again the next time a script in this family is written.
COPY_DISCIPLINE_SCRIPTS = [
    ROOT / "scripts" / "measure_retrieval.py",
    ROOT / "scripts" / "who_outranks.py",
]


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("measure_retrieval", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["measure_retrieval"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tree():
    return ast.parse(SCRIPT.read_text(encoding="utf-8"))


def test_the_script_imports(harness):
    """U.0: `ast.parse` accepts a duplicate keyword argument and `compile()`
    does not, so a script can sit un-runnable for weeks behind an AST guard.
    Importing it is the check that catches both."""
    assert harness.MIN_PAIRS >= 30


def test_it_uses_the_production_retriever(tree):
    """The whole of defect C in one assertion."""
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for required in ("TripleRetriever", "SemanticIndex", "TripleStore",
                     "retrieve"):
        assert required in names, (
            f"the harness no longer calls {required} — if it has gone back to "
            f"reproducing retrieval, it will drift from production again")


def test_it_defines_no_ranking_of_its_own(tree):
    """A private sort over candidate rows is how the last divergence started.
    Ordering belongs to `retriever.py`; the harness may sort its own OUTPUT
    (thresholds, report lines) and nothing else."""
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "sort":
            offenders.append(node.lineno)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "sorted":
            offenders.append(node.lineno)
    assert not offenders, (
        f"the harness sorts at line(s) {offenders}. Ranking is production's "
        f"job — reproducing it is defect C.")


@pytest.mark.parametrize("script", COPY_DISCIPLINE_SCRIPTS,
                         ids=lambda p: p.name)
def test_it_works_on_a_copy_and_checks_the_original(script):
    """`mode=ro` was the old guarantee and it forced the reproduction. The new
    guarantee is a copy plus a checked md5 — 'read-only' as an assertion, not
    a promise."""
    src = script.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fns = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_md5" in fns, f"{script.name} does not hash the store it reads"
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "copy2" in calls or "_copy_store" in fns, (
        f"{script.name} drives the store classes against the ORIGINAL")
    assert "MUTATED" in src, (
        f"{script.name} no longer reports whether it changed the store it read")


@pytest.mark.parametrize("script", COPY_DISCIPLINE_SCRIPTS,
                         ids=lambda p: p.name)
def test_the_store_is_closed_before_the_temp_directory_is_removed(script):
    """The operator's Windows crash: `PermissionError [WinError 32]` on exit.

    POSIX lets you unlink a file another process holds open, so the first
    version cleaned up fine here and failed on their box as
    `TemporaryDirectory.__exit__` ran with SQLite still holding
    `measure_kb.db`. Every number had already printed — a script that reports
    correctly and then exits non-zero is one CI calls a failure.

    Guarded by ORDER, not by presence: closing after the removal would satisfy
    a "does it call close()" check and still crash. Verified non-vacuous
    against a mutant with the two statements swapped.
    """
    tree = ast.parse(script.read_text(encoding="utf-8"))
    tries = [n for n in ast.walk(tree) if isinstance(n, ast.Try) and n.finalbody
             and any(isinstance(c, ast.Call)
                     and getattr(c.func, "attr", getattr(c.func, "id", ""))
                     == "rmtree" for c in ast.walk(n))]
    assert tries, (
        f"{script.name} has no finally that removes its temp directory — "
        f"cleanup depends on the happy path")
    block = tries[0]
    for node in ast.walk(block):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "TemporaryDirectory":
            pytest.fail("TemporaryDirectory's context manager removes the "
                        "directory before the finally block can close the "
                        "connection — that is the Windows crash")
    lines = {"close": None, "rmtree": None}
    for node in ast.walk(block):
        if isinstance(node, ast.Call):
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", ""))
            if name in lines and lines[name] is None:
                lines[name] = node.lineno
    assert lines["close"] and lines["rmtree"], (
        f"{script.name}'s cleanup does not both close and remove: {lines}")
    assert lines["close"] < lines["rmtree"], (
        f"{script.name} removes the temp directory before closing the "
        f"connection — this is the Windows failure exactly")


# ---------------------------------------------------------------------------
# who_outranks.py — the sibling that answers WHO, not WHICH
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def who():
    path = ROOT / "scripts" / "who_outranks.py"
    spec = importlib.util.spec_from_file_location("who_outranks", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["who_outranks"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_who_outranks_imports(who):
    assert callable(who.main)


def test_who_outranks_requires_all_expected_substrings(who):
    """ALL, not ANY — the same rule the pairs file follows, and for the same
    reason: a row containing "QTc" but not "11.4ms" is not the finding."""
    assert who._matches("QTc change at 600mg was 11.4ms", ["QTc", "11.4ms"])
    assert not who._matches("QTc change at 600mg was measured",
                            ["QTc", "11.4ms"])
    assert who._matches("Anything At All", ["at all"]), "must be case-blind"


def test_who_outranks_uses_the_shipped_index_not_its_own(who):
    """It must rank through `SemanticIndex.search`, not a private cosine.

    The whole point of the script is to explain a ranking production produced;
    a second implementation would explain a ranking nobody runs — defect C,
    one file over.
    """
    tree = ast.parse((ROOT / "scripts" / "who_outranks.py")
                     .read_text(encoding="utf-8"))
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert {"SemanticIndex", "search", "TripleStore"} <= names
    assert "cosine" not in names and "dot" not in names, (
        "who_outranks.py computes its own similarity — it must read the "
        "index's, or it explains a ranking nothing produced")


def test_it_reports_the_binding_constraint(harness):
    """The diagnostic the operator's run needed and W.1 did not print.

    A uniform miss across a threshold sweep is consistent with three different
    causes and only one of them is about similarity. `_diagnose` names which.
    """
    class _Idx:
        def __init__(self, ranked, surfaces):
            self._r, self._s = ranked, surfaces

        def search(self, qv, *, k, min_similarity=None):
            return self._r[:k]

        def surface_for(self, tid):
            return self._s.get(tid, "")

    # the claim is 3rd by cosine, above the threshold -> the QUOTA binds
    idx = _Idx([(1, 0.90), (2, 0.80), (7, 0.42)], {7: "QTc change was 11.4ms"})
    d = harness._diagnose(idx, [0.0], ["11.4ms"], slots=2, threshold=0.30,
                          n_rows=3)
    assert d["binding"] == "slots" and d["rank"] == 3
    assert abs(d["cosine"] - 0.42) < 1e-6

    # same rank, but now below the threshold -> the THRESHOLD binds
    d = harness._diagnose(idx, [0.0], ["11.4ms"], slots=10, threshold=0.50,
                          n_rows=3)
    assert d["binding"] == "threshold"

    # inside both budgets -> it was admitted and lost later, on rank
    d = harness._diagnose(idx, [0.0], ["11.4ms"], slots=10, threshold=0.30,
                          n_rows=3)
    assert d["binding"] == "rank"

    # never indexed at all -> a fourth answer, not silence
    d = harness._diagnose(idx, [0.0], ["nothing like this"], slots=10,
                          threshold=0.30, n_rows=3)
    assert d["binding"] == "not_indexed" and d["found_in_index"] is False


def test_the_default_match_rule_is_any(harness):
    """CORRECTED BY W.4 — this test used to assert the opposite.

    It read: "O.2b's own failure, guarded: retrieved the claim but not the
    number that makes it a finding must not count as found", and it pinned
    ALL as the unconditional rule. That reasoning is right about ONE pair —
    `["11.4ms", "supratherapeutic"]` — and wrong as a default, because a list
    of alternative correct answers is what `expect` mostly holds. Under the
    unconditional rule 32 of the operator's 36 pairs were unanswerable
    whatever the store contained.

    A test that encodes a wrong default is where the wrong default survives
    review, so the correction is recorded here rather than the test quietly
    deleted. The O.2b case keeps its rule, per pair, by name.
    """
    surfaces = ["QTc change at 600mg was measured"]
    assert harness._found(surfaces, ["QTc", "11.4ms"]) is True, (
        "the default is 'any' — one alternative is enough")
    assert harness._found(surfaces, ["QTc", "11.4ms"], "all") is False, (
        "'all' is still available, and O.2b's pair still needs it")


def test_pairs_below_the_floor_are_loaded_but_flagged(harness, tmp_path):
    import json
    p = tmp_path / "pairs.json"
    p.write_text(json.dumps(harness.TEMPLATE), encoding="utf-8")
    pairs = harness._load_pairs(p)
    assert len(pairs) == len(harness.TEMPLATE)
    assert len(pairs) < harness.MIN_PAIRS, (
        "the shipped template must be a SHAPE, not a corpus someone could "
        "mistake for a measurable sample")


def test_malformed_pairs_are_dropped_not_crashed_on(harness, tmp_path):
    import json
    p = tmp_path / "pairs.json"
    p.write_text(json.dumps([
        {"question": "ok?", "expect": ["x"]},
        {"question": "", "expect": ["x"]},
        {"question": "no expectations?", "expect": []},
        "not a dict",
    ]), encoding="utf-8")
    assert [d["question"] for d in harness._load_pairs(p)] == ["ok?"]


@pytest.mark.sqlite_only("scripts/measure_retrieval.py copies the SQLite file and prints its md5: the claim is about the file")
def test_the_copy_is_a_copy(harness, tmp_path):
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore

    src = tmp_path / "src.db"
    store = TripleStore(src)
    store.add(subject="aspirin", relation="is_a", object="analgesic",
              weight=0.5, tier=2, granularity=Granularity.TOPIC,
              source="extraction")
    store.close()
    before = harness._md5(src)

    dest = tmp_path / "work"
    dest.mkdir()
    copy = harness._copy_store(src, dest)
    assert copy != src and copy.is_file()

    # writing to the copy must not disturb the original — the property the
    # whole approach rests on.
    TripleStore(copy).add(subject="ibuprofen", relation="is_a",
                          object="analgesic", weight=0.5, tier=2,
                          granularity=Granularity.TOPIC, source="extraction")
    assert harness._md5(src) == before
