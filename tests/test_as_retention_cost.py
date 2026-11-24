"""Packet AS — does retention cost anything? Sentinels on the measurement.

The experiment is measurement only and builds no mechanism; this file pins
the instrument's honesty:
  - the pre-registered thresholds and rules live in SOURCE and match the
    packet (>= 25% supported / 10–25% marginal / < 10% closes; H2 constant
    iff std < mean/2; marginal or constant means no mechanism);
  - the three wasted categories are reported SEPARATELY, and a report that
    only carried a combined figure would fail here;
  - "duplicate-bearing" IS AQ's curated grouping and "malformed" IS the
    ingest gate's rules — identity-pinned imports, not copies;
  - the rendered surface is production's renderer, not a re-implementation;
  - token counts come from a real tokeniser or the output is marked
    NOT_A_MEASUREMENT with no verdict (stub exits 4); a real backend that
    cannot be reached raises rather than falling back (exit 5);
  - read-only: the store's md5 is identical across a run;
  - non-vacuity: the fixture holds a turn with duplicates and one without,
    and the report says so — a sample where every turn looks the same makes
    the H2 spread meaningless, and that is the likeliest failure shape;
  - the script imports its real backends lazily (AST).
"""
from __future__ import annotations

import ast
import hashlib
import json
from tests import _proc
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.kb import gate                                   # noqa: E402
from backend.kb.granularity import Granularity                # noqa: E402
from backend.kb.tiered_store import TripleStore               # noqa: E402
from backend.memory.lineage import LineageStore               # noqa: E402
from scripts import _as, citation_judge_extract               # noqa: E402
from scripts._as import (                                     # noqa: E402
    PRIMARY, SECONDARY, categorise, evaluate, h2, stub_tokeniser,
    tokeniser_repo_for, turn_shares, verdict,
)

SCRIPT = ROOT / "scripts" / "measure_retention_cost.py"


# ---------------------------------------------------------------------------
# Pre-registration in source
# ---------------------------------------------------------------------------

def test_thresholds_in_source_match_the_packet():
    assert _as.THRESHOLDS == {"supported": 0.25, "marginal": (0.10, 0.25),
                              "closes": 0.10}
    assert verdict(0.25) == "supported" and verdict(0.249) == "marginal"
    assert verdict(0.10) == "marginal" and verdict(0.099) == "closes"
    assert verdict(None) is None
    assert "std" in _as.H2_CONSTANT_RULE and "mean / 2" in _as.H2_CONSTANT_RULE
    assert "no mechanism" in _as.TIE_RULE and "biological" in _as.TIE_RULE
    assert _as.PRIMARY == "structural"
    assert "27%" in _as.UNCONTRIBUTING_CAVEAT and "lower bound" in _as.UNCONTRIBUTING_CAVEAT


def test_the_definitions_reuse_existing_machinery_by_identity():
    """Duplicate-bearing is AQ's grouping; malformed is the gate's rules.
    A copy would be a second judgement that could drift from the first."""
    assert _as.group_key is citation_judge_extract.group_key
    assert _as.alias_version is citation_judge_extract.alias_version
    assert _as.inspect_term is gate.inspect_term
    assert _as.inspect_relation is gate.inspect_relation
    assert _as.RELATION_ALIASES is gate.RELATION_ALIASES


def test_the_script_renders_with_production_and_imports_backends_lazily():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    heavy = {"tokenizers", "huggingface_hub", "httpx", "transformers"}
    assert not [ast.unparse(n) for n in top
                if (getattr(n, "module", "") or "").split(".")[0] in heavy
                or any(a.name.split(".")[0] in heavy for a in getattr(n, "names", []))]
    calls = {ast.unparse(c.func) for c in ast.walk(tree) if isinstance(c, ast.Call)}
    assert "RetrievedTriple" in calls, "the surface must come from production's renderer"
    # W1 — by AST, not the text `mode=ro` (a comment would have satisfied it).
    _assert_the_store_is_opened_read_only(tree)


def _assert_the_store_is_opened_read_only(tree: ast.AST) -> None:
    """The guard's read-only check, shared with its companion: every open in
    the tree is the shared read-only open, resolved through the tree's own
    imports (tests/_readonly_opens.py), and there is one."""
    from tests._readonly_opens import read_only_problems
    problems = read_only_problems(tree)
    assert not problems, f"the store must be opened read-only: {problems}"


def test_companion_the_read_only_check_names_a_connect_the_text_mode_ro_would_have_hidden():
    """COMPANION (W1 part 5f, SF5) for the read-only check above, through the
    guard's own assertion: a script whose docstring says `mode=ro` and whose
    code connects read-write through `from sqlite3 import connect` (a name
    the pre-5f check, keyed on the spelling `connect`, could not see as the
    call `connect(db)`) fails it by line; a script that also opens one store
    read-only and another writable fails it naming the writable one."""
    planted = ('"""Opens the store mode=ro and reads it."""\n'
               "from sqlite3 import connect as open_store\n"
               "conn = open_store(db)\n")
    with pytest.raises(AssertionError, match="line 3: a direct connect \\(open_store\\), not open_read_only") as e:
        _assert_the_store_is_opened_read_only(ast.parse(planted))
    assert "the script never calls open_read_only" in str(e.value)
    planted = "import sqlite3\nfrom scripts._readonly import open_read_only\n" \
              "ro = open_read_only(db)\nrw = sqlite3.connect(copy)\n"
    with pytest.raises(AssertionError, match="line 4: a direct connect \\(sqlite3.connect\\), not open_read_only"):
        _assert_the_store_is_opened_read_only(ast.parse(planted))
    # ...and the script's own shape passes: one read-only open, no connect.
    _assert_the_store_is_opened_read_only(ast.parse(planted.replace("rw = sqlite3.connect(copy)\n", "")))


# ---------------------------------------------------------------------------
# Fixture — a turn with duplicates and a turn without, malformed included
# ---------------------------------------------------------------------------

def _item(s, r, o, *, cited=True, source="extraction", tokens=None, weight=0.6):
    return {"subject": s, "relation": r, "object": o, "source": source,
            "cited": cited, "weight": weight,
            "tokens": tokens if tokens is not None else len((s + r + o).split()) + 3}


def _dup_turn():
    return [
        _item("TAK-441", "is_metabolized_by", "CYP3A4", tokens=10),
        _item("tak-441", "is metabolized by", "cyp3a4", tokens=10),   # same group
        _item("TAK-441", "is_a", "prodrug", tokens=6),
        _item("TAK-441", "is", "prodrug", tokens=6),                   # alias -> is_a
        _item("Hemoglobin", "None", "protein", tokens=5),              # malformed relation
        _item("dose", "was", "600mg", cited=False, tokens=4),          # uncontributing
    ]


def _clean_turn():
    return [
        _item("interval training", "improves", "vo2 max", tokens=8),
        _item("aspirin", "is_used_for", "pain relief", tokens=7, cited=False),
        _item("ganymede", "orbits", "jupiter", tokens=5),
    ]


def test_non_vacuity_the_fixture_has_both_kinds_of_turn():
    d, c = turn_shares(_dup_turn()), turn_shares(_clean_turn())
    assert d["has_duplicates"] and not c["has_duplicates"]
    assert d["malformed"]["tokens"] > 0 and c["malformed"]["tokens"] == 0


def test_categories_are_counted_separately_and_never_only_combined():
    t = turn_shares(_dup_turn())
    assert t["tokens_injected"] == 41
    assert t["duplicate_bearing"]["tokens"] == 16          # second of each pair
    assert t["malformed"]["tokens"] == 5
    assert t["malformed"]["reasons"] == {"null_literal": 1}
    assert t["uncontributing"]["tokens"] == 4
    assert t[PRIMARY]["tokens"] == 21 and t[PRIMARY]["share"] == pytest.approx(21 / 41)
    assert t[PRIMARY]["breakdown"] == {"duplicate_bearing": 16, "malformed": 5}
    assert t[SECONDARY]["tokens"] == 25
    assert set(t[SECONDARY]["breakdown"]) == {"duplicate_bearing", "malformed",
                                              "uncontributing"}
    # A union without its members beside it is the shape the packet forbids.
    for key in (PRIMARY, SECONDARY):
        assert "breakdown" in t[key]


# ---------------------------------------------------------------------------
# AT (term identity) — the counting fix: term_key and trail-widened aliases
# ---------------------------------------------------------------------------

def test_term_key_folds_case_punctuation_and_spacing_and_nothing_else():
    """The two classes the operator's store showed (24 punctuation groups,
    15 case groups, no wrong merge among them) fold together; a genuinely
    different word does not."""
    from scripts._as import term_key
    assert term_key("HERG potassium_channel") == term_key("HERG_potassium_channel") \
        == term_key("hERG potassium channel") == "herg potassium channel"
    assert term_key("QT prolongation risk") == term_key("QT_prolongation_risk")
    assert term_key("['Hebbian learning']") == "hebbian learning"
    assert term_key("CYP3A4") != term_key("CYP3A4 enzymes"), (
        "a trailing word is not a spelling; the headword class is not folded")
    assert term_key("blood cell") != term_key("blood cells"), "plurals are not folded"


def test_term_key_is_digit_safe():
    """The probe's own key turned 11.4ms into 114ms; the counting key must
    not: punctuation between two digits is kept."""
    from scripts._as import term_key
    assert term_key("11.4ms") == "11.4ms" and term_key("114ms") == "114ms"
    assert term_key("11.4ms") != term_key("114ms")
    assert term_key("TAK-441") == term_key("tak_441") == "tak 441"
    assert term_key("1,000 mg") == "1,000 mg"


def test_spelling_variants_now_count_as_duplicate_bearing():
    """The AS zero, reproduced and closed: the same claim injected in the
    spaced and the underscored spelling is one group."""
    t = turn_shares([
        _item("TAK-441", "has_affinity_for", "HERG potassium_channel", tokens=9),
        _item("TAK-441", "has_affinity_for", "HERG_potassium_channel", tokens=9),
        _item("tak-441", "has_affinity_for", "hERG potassium channel", tokens=9),
    ])
    assert t["duplicate_bearing"]["tokens"] == 18 and t["has_duplicates"]


def test_relation_aliases_widen_with_trail_pairs_under_union_find():
    """AB's derived pairs join K.3's curated map; a~b and b~c is one
    cluster; the curated map alone still holds is_a's spellings; a pair
    never recorded stays apart."""
    from scripts._as import relation_aliases
    base = relation_aliases([])
    assert base["is"] == base["is_a"]
    widened = relation_aliases([("is_metabolized_by", "metabolized_by"),
                                ("metabolized_by", "primarily_metabolized_by")])
    assert widened["is_metabolized_by"] == widened["metabolized_by"] \
        == widened["primarily_metabolized_by"]
    assert widened.get("is_mediated_by", "is_mediated_by") != widened["metabolized_by"]
    assert widened["is"] == widened["is_a"]
    t = turn_shares([
        _item("TAK-441", "is_metabolized_by", "CYP3A4", tokens=8),
        _item("TAK-441", "primarily_metabolized_by", "CYP3A4", tokens=8),
    ], aliases=widened)
    assert t["duplicate_bearing"]["tokens"] == 8
    untouched = turn_shares([
        _item("TAK-441", "is_metabolized_by", "CYP3A4", tokens=8),
        _item("TAK-441", "primarily_metabolized_by", "CYP3A4", tokens=8),
    ])
    assert untouched["duplicate_bearing"]["tokens"] == 0, (
        "without a recorded pair the wordings stay apart — no list")


def test_the_report_declares_the_grouping_it_used():
    from scripts._as import relation_aliases
    rep = evaluate([turn_shares(_dup_turn())], prefix_tokens=1, framing_tokens=1,
                   measurement=True, aliases=relation_aliases([("x", "y")]),
                   trail_pairs=1)
    g = rep["grouping"]
    assert g["trail_pairs"] == 1 and g["alias_version"] != g["curated_alias_version"]
    assert "between two digits" in g["term_key"]


def test_the_first_member_of_a_group_is_kept_and_opposites_never_merge():
    flagged = categorise([
        _item("x", "increases", "y"), _item("x", "decreases", "y"),
        _item("x", "is_a", "z"), _item("x", "is a kind of", "z"),
    ])
    assert [f["duplicate"] for f in flagged] == [False, False, False, True]


def test_an_absent_cited_flag_is_declared_not_guessed():
    items = [_item("a", "has", "b"), {**_item("c", "has", "d"), "cited": None}]
    t = turn_shares(items)
    assert t["uncontributing"]["flags_absent"] == 1
    assert t["uncontributing"]["tokens"] == 0


def test_h2_spread_rule_and_correlations():
    dup = turn_shares(_dup_turn())
    clean = turn_shares(_clean_turn())
    same = [turn_shares(_clean_turn()) for _ in range(6)]
    const = h2(same)
    assert const["spread"]["std"] == 0 and const["constant"] is None, (
        "a mean of zero cannot be halved into a rule; declared, not forced")
    mixed = h2([dup, clean] * 3)
    assert mixed["spread"]["n"] == 6 and mixed["constant"] is False
    assert set(mixed["correlations"]) >= {"injected_count", "distinct_subjects",
                                          "substrate_share", "cited_share",
                                          "mean_weight"}
    few = h2([dup, clean])
    assert few["constant"] is None, "below the minimum turns there is no spread"


def test_evaluate_pools_tokens_and_reports_the_denominator_beside_the_shares():
    rep = evaluate([turn_shares(_dup_turn()), turn_shares(_clean_turn())],
                   prefix_tokens=400, framing_tokens=12, measurement=True)
    s = rep["shares"]
    assert rep["tokens_injected_total"] == 61
    assert s[PRIMARY]["share"] == pytest.approx(21 / 61)
    assert s["malformed"]["reasons"] == {"null_literal": 1}
    assert rep["verdict"][PRIMARY] == "supported"
    assert "OVERSTATES" in rep["denominator"]["note"]
    assert rep["denominator"]["injected_share_of_reconstructed_prompt"] == \
        pytest.approx(30.5 / (400 + 12 + 30.5))
    assert rep["non_vacuity"] == {"turns_with_duplicates": 1,
                                  "turns_without_duplicates": 1,
                                  "meaningful": True,
                                  "cited_flags_absent_items": 0}
    assert rep["uncontributing_caveat"] == _as.UNCONTRIBUTING_CAVEAT
    assert rep["grouping"]["alias_version"].startswith("k3-")


def test_a_stub_run_draws_no_verdict():
    rep = evaluate([turn_shares(_dup_turn())], prefix_tokens=1,
                   framing_tokens=1, measurement=False)
    assert rep["verdict"][PRIMARY] is None and rep["measurement"] is False
    _fn, inst = stub_tokeniser()
    assert inst["NOT_A_MEASUREMENT"] is True


def test_tokeniser_map_is_longest_prefix_and_overridable():
    assert tokeniser_repo_for("deepseek-r1:14b-qwen-distill-q4_K_M") == \
        "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"
    assert tokeniser_repo_for("qwen3.5:9b-q4_K_M") == "Qwen/Qwen3.5-9B"
    assert tokeniser_repo_for("unknown:1b") is None
    assert tokeniser_repo_for("unknown:1b", "me/mine") == "me/mine"


def test_a_real_backend_raises_rather_than_falling_back():
    with pytest.raises(_as.TokeniserUnavailable):
        _as.ollama_tokeniser(base_url="http://127.0.0.1:9", model="x")
    with pytest.raises(_as.TokeniserUnavailable):
        _as.hf_tokeniser(tokenizer_json=None, repo=None)


# ---------------------------------------------------------------------------
# End to end on a real store, read-only, stub tokeniser
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    kb = tmp_path / "kb.db"
    s = TripleStore(kb)
    ids = {}
    for sub, rel, obj, src in [
        ("TAK-441", "is_metabolized_by", "CYP3A4", "human_assertion"),
        ("TAK-441", "is metabolized by", "CYP3A4", "extraction"),
        ("interval training", "improves", "vo2 max", "wikidata"),
        ("aspirin", "is_used_for", "pain relief", "extraction"),
    ]:
        ids[(sub, rel, obj)] = s.add(subject=sub, relation=rel, object=obj,
                                     weight=0.7, tier=1,
                                     granularity=Granularity.TOPIC, source=src)
    lin = LineageStore(kb)
    a, b, c, d = ids.values()
    lin.record_turn(query="q1", injected=[
        {"belief_id": a, "weight_at_injection": 0.7, "tier_at_injection": 1, "cited": True},
        {"belief_id": b, "weight_at_injection": 0.6, "tier_at_injection": 1, "cited": False},
        {"belief_id": c, "weight_at_injection": 0.5, "tier_at_injection": 1, "cited": True},
    ], contradictions=[], model_id="deepseek-r1:14b-qwen-distill-q4_K_M",
        response_text="r1", prompt_prefix="p")
    lin.record_turn(query="q2", injected=[
        {"belief_id": c, "weight_at_injection": 0.7, "tier_at_injection": 1, "cited": True},
        {"belief_id": d, "weight_at_injection": 0.4, "tier_at_injection": 2, "cited": False},
    ], contradictions=[], model_id="deepseek-r1:14b-qwen-distill-q4_K_M",
        response_text="r2", prompt_prefix="p")
    lin.record_turn(query="q3", injected=[], contradictions=[],
                    model_id="m", response_text="r3", prompt_prefix="p")
    s.conn.close(); lin.conn.close()
    return kb


@pytest.mark.sqlite_only("scripts/measure_retention_cost.py reads the SQLite file through the read-only open (law 15); the URL form is W3b part 3 (D9, T8)")
def test_end_to_end_stub_run_is_read_only_marked_and_exits_4(store, tmp_path):
    before = hashlib.md5(store.read_bytes()).hexdigest()
    out = tmp_path / "rc.json"
    proc = _proc.run(
        [sys.executable, str(SCRIPT), "--kb", str(store), "--tokeniser", "stub",
         "--capture", str(tmp_path / "none.jsonl"),
         "--eval-log", str(tmp_path / "none2.jsonl"), "--out", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT, timeout=300)
    assert proc.returncode == 4, proc.stdout + proc.stderr
    assert hashlib.md5(store.read_bytes()).hexdigest() == before
    assert "UNCHANGED" in proc.stdout
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["NOT_A_MEASUREMENT"] is True and rep["verdict"][PRIMARY] is None
    assert rep["coverage"] == {"records": 3, "with_injected": 2,
                               "flags_absent_records": 0, "unresolved_items": 0,
                               "capture_matched": 0, "capture_surface_mismatch": 0}
    assert rep["turns"] == 2
    assert rep["non_vacuity"]["meaningful"] is True, rep["non_vacuity"]
    rows = rep["turn_rows"]
    assert rows[0]["duplicate_bearing"]["tokens"] > 0 and rows[1]["duplicate_bearing"]["tokens"] == 0
    assert rows[0]["uncontributing"]["tokens"] > 0
    assert rep["denominator"]["system_prefix_tokens"] > 0
    assert list(rep["instrument"].values())[0]["backend"] == "stub"
    assert "items" not in rows[0], "per-item text stays out of the summary rows"
