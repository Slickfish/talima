"""AQ version two — the criterion ruling, fixed in source, with version one
left standing.

THE STOP. Version one stopped at calibration, 2 of 6: qid 23, an agreed-
unused item whose subject was a markdown heading (operator: unsure; both
scorers: unused; scored as a miss by CALIBRATION_UNSURE_RULE), and qid 25,
an agreed-used item the operator judged unused consistently across four
answers (both scorers wrong the same way — every word of the claim was in
the answer). No third kind. No counted-set figure existed.

THE RULING. An incoherent claim is `unsure`, never `unused` (the judge
prompt's own sentence, fixed into the labelling note). An unsure on a
calibration item is an exclusion with a seeded replacement from the same
stratum, not a miss; a calibration miss that is a shared scorer error
(the operator's reading consistent on another entry with the same claim)
is excluded as evidence about the scorers, with a replacement. Every other
miss stops the run. The replacement's own unsure or miss is a miss.

WHAT DOES NOT MOVE: version one's constants and rule text, the 32 labels,
the counted set, and `evaluate` with no exclusions — which is version one
exactly, pinned here against the existing tests' fixture.
"""
from __future__ import annotations

import json
from tests import _proc
import sys
from pathlib import Path

import pytest

from scripts import _aq
from scripts._aq import (
    CALIBRATION_EXCLUSION_RULES, PREREG_HISTORY, build_queue, coherent_candidate,
    evaluate, justify_exclusions, replacement_calibration,
)
from tests.test_aq_minicheck import _items, _label

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Version one stands
# ---------------------------------------------------------------------------

def test_version_one_is_recorded_verbatim_with_its_outcome():
    assert _aq.CALIBRATION_UNSURE_RULE == "an unsure on a calibration item is a miss"
    v1 = PREREG_HISTORY[1]
    assert v1["calibration_unsure_rule"] == _aq.CALIBRATION_UNSURE_RULE
    assert "stopped at calibration" in v1["outcome"]
    assert "qid 23" in v1["outcome"] and "qid 25" in v1["outcome"]
    assert "No precision figure" in v1["outcome"]
    assert _aq.PREREG_VERSION == 2
    assert "before the counted set" in PREREG_HISTORY[2]["fixed"]


def test_evaluate_without_exclusions_is_version_one_exactly():
    items = _items()
    queue, key, _ = build_queue(items, seed=7)

    def rule(k, it):
        if k["tag"] == "calibration_used":
            return "unsure"
        if k["tag"] == "calibration_unused":
            return "unused"
        return "used"
    by = _label(queue, key, items, rule=rule)
    out = evaluate(queue, key, by)
    assert out["calibration"]["passed"] is False
    assert out["calibration"]["prereg_version"] == 1
    assert len(out["calibration"]["failed_items"]) == 3      # unsure = miss under v1
    assert out["result"] is None


# ---------------------------------------------------------------------------
# The fixture for version two: a heading-shaped calibration item and a
# shared-error item whose claim recurs in the counted set
# ---------------------------------------------------------------------------

def _enriched():
    """The existing fixture carries no `relation` and two-token claims, which
    the mechanical pre-screen would refuse. Version two's cases give every
    item a relation and a three-token claim so the pre-screen is exercised
    by its own test rather than tripped by the fixture."""
    items = _items()
    for it in items:
        it["relation"] = "relates_to"
        it["claim"] = f"claim {it['n']} relates to thing"
    return items


def _v2_case(seed=7):
    items = _enriched()
    queue, key, _ = build_queue(items, seed=seed)
    by = {(i["lineage_id"], i["n"]): i for i in items}
    kq = {k["qid"]: k for k in key}
    cal_unused = [k for k in key if k["tag"] == "calibration_unused"]
    cal_used = [k for k in key if k["tag"] == "calibration_used"]
    incoherent = cal_unused[0]["qid"]           # the operator will say unsure
    shared = cal_used[0]["qid"]                 # the operator will say unused
    # The shared-error claim recurs on a disagreement item the operator also
    # labels unused — the consistency the rule requires.
    shared_it = by[(kq[shared]["lineage_id"], kq[shared]["n"])]
    other = next(e for e in queue if kq[e["qid"]]["tag"] == "fp_candidate")
    other["claim"] = shared_it["claim"]
    for e in queue:
        tag = kq[e["qid"]]["tag"]
        if e["qid"] == incoherent:
            e["label"] = "unsure"
        elif e["qid"] == shared or e["qid"] == other["qid"]:
            e["label"] = "unused"
        elif tag == "calibration_used":
            e["label"] = "used"
        elif tag == "calibration_unused":
            e["label"] = "unused"
        else:
            e["label"] = "used" if by[(kq[e["qid"]]["lineage_id"], kq[e["qid"]]["n"])]["minicheck"] else "unused"
    exclusions = [{"qid": incoherent, "reason": "incoherent_unsure"},
                  {"qid": shared, "reason": "shared_error"}]
    return items, queue, key, by, exclusions, incoherent, shared


def _labelled_replacements(rqueue, rkey, *, miss=False, unsure=False):
    for e in rqueue:
        tag = next(k["tag"] for k in rkey if k["qid"] == e["qid"])
        expected = "used" if tag == "calibration_used" else "unused"
        wrong = "unused" if expected == "used" else "used"
        e["label"] = "unsure" if unsure else (wrong if miss else expected)
    return rqueue, rkey


def test_non_vacuity_the_v2_fixture_stops_under_version_one():
    items, queue, key, by, _ex, _i, _s = _v2_case()
    out = evaluate(queue, key, by)
    assert out["calibration"]["passed"] is False
    assert len(out["calibration"]["failed_items"]) == 2


# ---------------------------------------------------------------------------
# Justification — nothing is excluded on assertion
# ---------------------------------------------------------------------------

def test_exclusions_are_justified_on_the_labels_not_asserted():
    items, queue, key, by, exclusions, incoherent, shared = _v2_case()
    just = justify_exclusions(queue, key, exclusions)
    assert all(j["justified"] for j in just), just
    assert next(j for j in just if j["qid"] == shared)["consistent_with_qids"]
    # An incoherent claim the operator labelled unused is NOT excludable.
    bad = [e for e in queue if e["qid"] == incoherent][0]
    bad["label"] = "unused"
    j = justify_exclusions(queue, key, exclusions)[0]
    assert j["justified"] is False and "nothing to exclude" in j["basis"]
    bad["label"] = "used"
    j = justify_exclusions(queue, key, exclusions)[0]
    assert j["justified"] is False and "not unsure" in j["basis"]
    # A shared-error claim with no consistent duplicate is a miss, not evidence.
    other = next(e for e in queue if e["claim"] == next(
        e2 for e2 in queue if e2["qid"] == shared)["claim"] and e["qid"] != shared)
    other["label"] = "used"
    j = [x for x in justify_exclusions(queue, key, exclusions) if x["qid"] == shared][0]
    assert j["justified"] is False and "no other entry" in j["basis"]
    # A non-calibration qid and an unknown reason are refused.
    fp = next(k["qid"] for k in key if k["tag"] == "fp_candidate")
    assert justify_exclusions(queue, key, [{"qid": fp, "reason": "shared_error"}])[0]["basis"] \
        == "not a calibration item"
    assert justify_exclusions(queue, key, [{"qid": shared, "reason": "vibes"}])[0]["basis"] \
        == "unknown reason"


# ---------------------------------------------------------------------------
# Replacements — seeded, same stratum, disjoint, coherent, blind
# ---------------------------------------------------------------------------

def test_replacements_are_seeded_same_stratum_disjoint_and_blind():
    items, queue, key, by, exclusions, incoherent, shared = _v2_case()
    r1 = replacement_calibration(items, seed=7, key=key, exclusions=exclusions, next_qid=33)
    r2 = replacement_calibration(items, seed=7, key=key, exclusions=exclusions, next_qid=33)
    assert r1 == r2, "the replacement draw is not a function of (seed, key)"
    # The fixture leaves two undrawn items per agreed stratum, so a different
    # seed cannot be shown to draw differently here; determinism is the pin.
    rqueue, rkey = r1
    assert [e["qid"] for e in rqueue] == [33, 34]
    drawn = {(k["lineage_id"], k["n"]) for k in key}
    tag_of = {k["qid"]: k["tag"] for k in key}
    for k in rkey:
        assert (k["lineage_id"], k["n"]) not in drawn, "replacement re-drew a queue item"
        assert k["tag"] == tag_of[k["replaces"]], "replacement crossed strata"
        it = by[(k["lineage_id"], k["n"])]
        assert (bool(it["lexical"]), bool(it["minicheck"])) == (
            (True, True) if k["tag"] == "calibration_used" else (False, False))
    text = json.dumps(rqueue).lower()
    for leak in ("minicheck", "lexical", "score", "tag", "calibration", "replaces"):
        assert leak not in text
    assert all(set(e) == {"qid", "answer", "claim", "label"} for e in rqueue)


def test_replacement_refuses_a_non_calibration_qid():
    items, queue, key, by, _ex, _i, _s = _v2_case()
    fp = next(k["qid"] for k in key if k["tag"] == "fp_candidate")
    with pytest.raises(ValueError):
        replacement_calibration(items, seed=7, key=key,
                                exclusions=[{"qid": fp, "reason": "shared_error"}], next_qid=33)


def test_the_mechanical_prescreen_keeps_incoherent_candidates_out():
    assert coherent_candidate({"relation": "causes", "claim": "A causes B"})
    assert not coherent_candidate({"relation": "", "claim": "Cardiac Safety"})
    assert not coherent_candidate({"relation": "is", "claim": "Cardiac Safety"})
    items, queue, key, by, exclusions, _i, _s = _v2_case()
    # Make every undrawn agreed-unused candidate incoherent: no replacement is left.
    drawn = {(k["lineage_id"], k["n"]) for k in key}
    for it in items:
        if not it["lexical"] and not it["minicheck"] and (it["lineage_id"], it["n"]) not in drawn:
            it["relation"] = ""
    with pytest.raises(ValueError, match="no coherent replacement"):
        replacement_calibration(items, seed=7, key=key,
                                exclusions=[e for e in exclusions if e["reason"] == "incoherent_unsure"],
                                next_qid=33)


# ---------------------------------------------------------------------------
# Scoring under version two
# ---------------------------------------------------------------------------

def test_justified_exclusions_with_labelled_replacements_open_the_gate():
    items, queue, key, by, exclusions, incoherent, shared = _v2_case()
    rqueue, rkey = replacement_calibration(items, seed=7, key=key,
                                           exclusions=exclusions, next_qid=33)
    _labelled_replacements(rqueue, rkey)
    out = evaluate(queue, key, by, exclusions=exclusions, replacements=(rqueue, rkey))
    cal = out["calibration"]
    assert cal["passed"] is True and cal["prereg_version"] == 2
    assert {r["qid"] for r in cal["excluded"]} == {incoherent, shared}
    assert cal["replacements_labelled"] == 2
    assert "qid 25" in cal["v1_outcome"]
    # The counted set is the same 20 whichever version scored the gate.
    assert out["total"]["n"] == 20
    ref = evaluate([dict(e, label=("used" if by[(k["lineage_id"], k["n"])]["minicheck"] else "unused"))
                    for e, k in zip(sorted(queue, key=lambda e: e["qid"]),
                                    sorted(key, key=lambda k: k["qid"]))
                    if not k["tag"].startswith("calibration")]
                   + [dict(e, label=("used" if k["tag"] == "calibration_used" else "unused"))
                      for e, k in zip(sorted(queue, key=lambda e: e["qid"]),
                                      sorted(key, key=lambda k: k["qid"]))
                      if k["tag"].startswith("calibration")],
                   key, by)
    assert ref["calibration"]["passed"] is True
    assert out["per_stratum"] == ref["per_stratum"] or out["total"]["n"] == ref["total"]["n"]


def test_a_replacement_miss_or_unsure_is_a_miss():
    for kw in ({"miss": True}, {"unsure": True}):
        items, queue, key, by, exclusions, _i, _s = _v2_case()
        rqueue, rkey = replacement_calibration(items, seed=7, key=key,
                                               exclusions=exclusions, next_qid=33)
        _labelled_replacements(rqueue, rkey, **kw)
        out = evaluate(queue, key, by, exclusions=exclusions, replacements=(rqueue, rkey))
        assert out["calibration"]["passed"] is False
        assert all("replacement" in f["basis"] for f in out["calibration"]["failed_items"])
        assert out["result"] is None


def test_an_exclusion_without_a_replacement_or_unjustified_fails():
    items, queue, key, by, exclusions, incoherent, shared = _v2_case()
    out = evaluate(queue, key, by, exclusions=exclusions, replacements=None)
    assert out["calibration"]["passed"] is False
    assert all("without a replacement" in f["basis"] for f in out["calibration"]["failed_items"])
    rqueue, rkey = replacement_calibration(items, seed=7, key=key,
                                           exclusions=exclusions, next_qid=33)
    _labelled_replacements(rqueue, rkey)
    next(e for e in queue if e["qid"] == incoherent)["label"] = "unused"   # no longer unsure
    out = evaluate(queue, key, by, exclusions=exclusions, replacements=(rqueue, rkey))
    assert out["calibration"]["passed"] is False
    assert any(f.get("refused_exclusion") == "incoherent_unsure" for f in out["calibration"]["failed_items"])


def test_other_calibration_misses_still_stop_the_run():
    items, queue, key, by, exclusions, incoherent, shared = _v2_case()
    third = next(k["qid"] for k in key if k["tag"] == "calibration_used" and k["qid"] != shared)
    next(e for e in queue if e["qid"] == third)["label"] = "unused"
    rqueue, rkey = replacement_calibration(items, seed=7, key=key,
                                           exclusions=exclusions, next_qid=33)
    _labelled_replacements(rqueue, rkey)
    out = evaluate(queue, key, by, exclusions=exclusions, replacements=(rqueue, rkey))
    assert out["calibration"]["passed"] is False
    assert [f["qid"] for f in out["calibration"]["failed_items"]] == [third]


# ---------------------------------------------------------------------------
# The scripts, end to end on a synthetic scores file
# ---------------------------------------------------------------------------

def _run(args, cwd):
    return _proc.run([sys.executable, *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=600)


def test_the_scripts_freeze_labels_refuse_unjustified_and_score_the_counted_set(tmp_path):
    items = _enriched()
    scores = {"vmap_version": "vmap-test", "minicheck_threshold": 0.5,
              "meta": {"models": {}}, "summary": {}, "items": items}
    (tmp_path / "aq_scores.json").write_text(json.dumps(scores), encoding="utf-8")
    q = _run([ROOT / "scripts/aq_queue.py", "aq_scores.json", "--seed", "7"], tmp_path)
    assert q.returncode == 0, q.stdout + q.stderr
    # replace on an unlabelled queue is refused
    r = _run([ROOT / "scripts/aq_queue.py", "aq_scores.json", "--replace", "aq_queue.json",
              "--exclude", "1:shared_error"], tmp_path)
    assert r.returncode == 4 and "unlabelled" in r.stdout
    # label version one with the v2 fixture's pattern
    _items_, queue, key, by, exclusions, incoherent, shared = _v2_case()
    qfile = json.loads((tmp_path / "aq_queue.json").read_text(encoding="utf-8"))
    labels = {e["qid"]: e["label"] for e in queue}
    claims = {e["qid"]: e["claim"] for e in queue}
    for e in qfile["entries"]:
        e["label"] = labels[e["qid"]]
        e["claim"] = claims[e["qid"]]
    (tmp_path / "aq_queue.json").write_text(json.dumps(qfile), encoding="utf-8")
    # an unjustified exclusion is refused and writes nothing
    r = _run([ROOT / "scripts/aq_queue.py", "aq_scores.json", "--replace", "aq_queue.json",
              "--exclude", f"{shared}:incoherent_unsure"], tmp_path)
    assert r.returncode == 4 and not (tmp_path / "aq_replacements.json").exists()
    # justified exclusions draw blind replacements and freeze the labels by hash
    r = _run([ROOT / "scripts/aq_queue.py", "aq_scores.json", "--replace", "aq_queue.json",
              "--exclude", f"{incoherent}:incoherent_unsure",
              "--exclude", f"{shared}:shared_error"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    rep = json.loads((tmp_path / "aq_replacements.json").read_text(encoding="utf-8"))
    assert all(e["label"] == "" for e in rep["entries"]) and len(rep["entries"]) == 2
    assert "unsure, never unused" in rep["note"]
    prereg = json.loads((tmp_path / "aq_prereg.json").read_text(encoding="utf-8"))
    assert "v2" in prereg and prereg["v2"]["labelled_queue_sha256"]
    # a second draw never overwrites the replacement file
    r = _run([ROOT / "scripts/aq_queue.py", "aq_scores.json", "--replace", "aq_queue.json",
              "--exclude", f"{incoherent}:incoherent_unsure"], tmp_path)
    assert r.returncode == 4 and "never overwritten" in r.stdout
    # result refuses unlabelled replacements, then scores once they are labelled
    r = _run([ROOT / "scripts/aq_result.py", "aq_scores.json", "aq_queue.json",
              "--replacements", "aq_replacements.json"], tmp_path)
    assert r.returncode == 4 and "unlabelled replacements" in r.stdout
    rkey = json.loads((tmp_path / "aq_replacements.key.json").read_text(encoding="utf-8"))
    for e in rep["entries"]:
        tag = next(k["tag"] for k in rkey["entries"] if k["qid"] == e["qid"])
        e["label"] = "used" if tag == "calibration_used" else "unused"
    (tmp_path / "aq_replacements.json").write_text(json.dumps(rep), encoding="utf-8")
    r = _run([ROOT / "scripts/aq_result.py", "aq_scores.json", "aq_queue.json",
              "--replacements", "aq_replacements.json"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    report = json.loads(r.stdout)
    assert report["calibration"]["passed"] is True
    assert report["prereg"]["version"] == 2 and "qid 25" in report["prereg"]["v1"]["outcome"]
    assert report["hashes"]["v1_labels_unchanged_since_replacements_drawn"] is True
    assert report["total"]["n"] == 20 and report["total"]["verdict"] in (
        "adopt_here", "inconclusive_keep_incumbent", "no_evidence_keep_incumbent")
