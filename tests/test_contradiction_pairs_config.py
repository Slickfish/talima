"""Packet T.2 — the opposition set is configuration, not a constant.

`ContradictionDetector.__init__` has accepted `pairs=` since it shipped, and
the one construction site passed nothing. K.0's report already said the
vocabulary is configuration and the mechanism is ours; this is that sentence
made true. The O.2b shape — mechanism shipped, offer never built.

WHY IT IS WORTH DOING ALONE
    It needs no compliance from the extractor. The pairs match relations the
    store already contains, whatever the extractor decided to call them. So it
    is the fallback if the vocabulary measurement comes back badly and an
    improvement if it comes back well.

NON-VACUITY, BOTH DIRECTIONS
    The packet asked for it and it is the whole difficulty here. "The injected
    set is used" is trivially true on a set that behaves like the default. So
    the fixture uses a pair the default does NOT contain and drops one it
    does, and both halves are asserted: the new pair fires, the dropped one
    stops firing. Either alone would pass on a detector that ignored the
    argument entirely.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.cortex.contradiction import (
    CONTRADICTION_PAIRS,
    DEFAULT_PAIRS_SOURCE,
    ContradictionDetector,
    Triple,
    load_pairs,
    pairs_source,
)
from backend.server import create_app, create_services

TOKEN = "packet-t2-token"

# A domain pair the compiled set does not contain — pharmacology, the store's
# actual domain, and deliberately not a near-synonym of anything shipped.
NEW_PAIR = ("agonises", "antagonises")
# ...and one it does, dropped from the fixture so its absence is observable.
DROPPED = ("activates", "inhibits")


def _pairs_file(tmp_path: Path, pairs) -> Path:
    p = tmp_path / "pairs.json"
    p.write_text(json.dumps([list(x) for x in pairs]), encoding="utf-8")
    return p


def _services(tmp_path):
    return create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "turn_metrics.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots")


# ---------------------------------------------------------------------------
# NON-VACUITY — the fixture genuinely differs from the default
# ---------------------------------------------------------------------------

def test_the_fixture_pair_is_not_already_in_the_default_set():
    """If `agonises/antagonises` were already compiled in, every assertion
    below would pass on a detector that ignored its argument."""
    flat = {r for pair in CONTRADICTION_PAIRS for r in pair}
    assert NEW_PAIR[0] not in flat and NEW_PAIR[1] not in flat
    assert DROPPED in CONTRADICTION_PAIRS, (
        "the dropped pair is no longer a default; the 'stops firing' half of "
        "this file has stopped meaning anything")


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------

def test_the_default_is_unchanged_when_nothing_is_injected(monkeypatch):
    monkeypatch.delenv("NEXUS_CONTRADICTION_PAIRS", raising=False)
    assert load_pairs() == CONTRADICTION_PAIRS
    assert pairs_source()["source"] == DEFAULT_PAIRS_SOURCE
    assert pairs_source()["error"] == ""


def test_an_injected_set_is_what_the_detector_uses(tmp_path, monkeypatch):
    """BOTH DIRECTIONS, which is the point.

    A pair outside the injected set does not fire, and a pair inside it does —
    including one the compiled default has never heard of.
    """
    monkeypatch.setenv("NEXUS_CONTRADICTION_PAIRS",
                       str(_pairs_file(tmp_path, [NEW_PAIR])))
    detector = ContradictionDetector(pairs=load_pairs())

    assert detector.is_contradictory(*NEW_PAIR), (
        "the injected pair does not fire; the argument is being ignored")
    assert detector.is_contradictory(NEW_PAIR[1], NEW_PAIR[0]), (
        "opposition is not bidirectional under injection")
    assert not detector.is_contradictory(*DROPPED), (
        "a default pair still fires under an injected set that omits it — "
        "the sets are being merged, not replaced")


def test_the_injected_set_reaches_a_real_scan(tmp_path, monkeypatch):
    """Structural equality is not use. This drives `scan_recent`, which is the
    method the loop actually calls."""
    monkeypatch.setenv("NEXUS_CONTRADICTION_PAIRS",
                       str(_pairs_file(tmp_path, [NEW_PAIR])))
    detector = ContradictionDetector(pairs=load_pairs())
    triples = [
        Triple("drug x", NEW_PAIR[0], "receptor y", 1.0, "extraction", 0.9),
        Triple("drug x", NEW_PAIR[1], "receptor y", 2.0, "extraction", 0.9),
        Triple("drug z", DROPPED[0], "enzyme q", 1.0, "extraction", 0.9),
        Triple("drug z", DROPPED[1], "enzyme q", 2.0, "extraction", 0.9),
    ]
    found = detector.scan_recent(triples)
    pairs_found = {(c.relation_a, c.relation_b) for c in found}
    assert any(set(p) == set(NEW_PAIR) for p in pairs_found), (
        f"the injected pair produced no contradiction: {pairs_found}")
    assert not any(set(p) == set(DROPPED) for p in pairs_found), (
        f"a pair outside the injected set fired: {pairs_found}")


def test_the_app_uses_the_injected_set(tmp_path, monkeypatch):
    """The seam's whole point — `server.py` passed nothing for as long as this
    argument has existed."""
    monkeypatch.setenv("NEXUS_CONTRADICTION_PAIRS",
                       str(_pairs_file(tmp_path, [NEW_PAIR])))
    services = _services(tmp_path)
    assert services.contradiction.pairs == (NEW_PAIR,), (
        f"the app built its detector with {services.contradiction.pairs}")
    assert services.contradiction.is_contradictory(*NEW_PAIR)


# ---------------------------------------------------------------------------
# Declared, not silent (I-6)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("content,why", [
    ("not json at all",                      "unparseable"),
    ('{"increases": "decreases"}',           "an object, not an array"),
    ('[]',                                   "empty"),
    ('[["only-one-side"]]',                  "wrong arity"),
    ('[["same", "same"]]',                   "both sides identical"),
])
def test_a_bad_pair_file_warns_and_falls_back(tmp_path, monkeypatch, caplog,
                                              content, why):
    """A malformed policy file must not empty the detector and must not stop
    the app — but it must not pass unremarked either. `error` carries what
    went wrong so `/cortex/stats` can show it."""
    p = tmp_path / "bad.json"
    p.write_text(content, encoding="utf-8")
    monkeypatch.setenv("NEXUS_CONTRADICTION_PAIRS", str(p))
    with caplog.at_level(logging.WARNING, logger="nexus.contradiction"):
        loaded = load_pairs()
    assert loaded == CONTRADICTION_PAIRS, f"{why}: did not fall back"
    assert pairs_source()["source"] == DEFAULT_PAIRS_SOURCE
    assert pairs_source()["error"], f"{why}: fell back silently"
    assert caplog.records, f"{why}: no warning was logged"


def test_a_missing_file_falls_back_rather_than_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_CONTRADICTION_PAIRS", str(tmp_path / "nope.json"))
    assert load_pairs() == CONTRADICTION_PAIRS
    assert "FileNotFoundError" in pairs_source()["error"]


def test_a_partly_malformed_file_keeps_the_good_pairs_and_says_so(
        tmp_path, monkeypatch):
    """Refusing the whole file over one bad row would throw away work someone
    did; accepting it silently would hide that a pair they wrote is not in
    force. Keep the good ones, count the rest, declare it."""
    p = tmp_path / "mixed.json"
    p.write_text(json.dumps([list(NEW_PAIR), ["lonely"], ["x", "x"]]),
                 encoding="utf-8")
    monkeypatch.setenv("NEXUS_CONTRADICTION_PAIRS", str(p))
    assert load_pairs() == (NEW_PAIR,)
    assert "2 malformed" in pairs_source()["error"]


def test_pairs_are_normalised_on_load(tmp_path, monkeypatch):
    """`_norm` lowercases and underscores before comparing, so a file written
    with spaces or capitals must still match stored relations."""
    p = tmp_path / "caps.json"
    p.write_text(json.dumps([["Is A", "IS NOT A"]]), encoding="utf-8")
    monkeypatch.setenv("NEXUS_CONTRADICTION_PAIRS", str(p))
    assert load_pairs() == (("is_a", "is_not_a"),)


def test_cortex_stats_reports_the_set_in_force(tmp_path, monkeypatch):
    """I-6. An operator who installed thirty pairs and is silently running on
    the compiled seven — unreadable file, bad JSON — has no other way to find
    out."""
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    monkeypatch.setenv("NEXUS_CONTRADICTION_PAIRS",
                       str(_pairs_file(tmp_path, [NEW_PAIR, DROPPED])))
    with TestClient(create_app(_services(tmp_path))) as client:
        body = client.get("/cortex/stats").json()["contradiction_pairs"]

    assert body["count"] == 2
    assert [tuple(p) for p in body["pairs"]] == [NEW_PAIR, DROPPED]
    assert body["source"].endswith("pairs.json")
    assert body["error"] == ""


def test_cortex_stats_shows_the_fallback_after_a_bad_file(tmp_path, monkeypatch):
    """The half that matters: a silent fallback is the failure this reporting
    exists to end, so the surface must show the default AND the reason."""
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    monkeypatch.setenv("NEXUS_CONTRADICTION_PAIRS", str(bad))
    with TestClient(create_app(_services(tmp_path))) as client:
        body = client.get("/cortex/stats").json()["contradiction_pairs"]

    assert body["count"] == len(CONTRADICTION_PAIRS)
    assert body["source"] == DEFAULT_PAIRS_SOURCE
    assert body["error"], "the fallback is not declared"
