"""AU — the citation test's two rules, forward-only, versioned.

THE DEFECT (the citation false-positive investigation): `is_cited` marks a
belief used when both its terms appear as whole tokens in the answer, so a
row whose terms are conversational filler — `tell | co_activated | know` —
or a null literal — `BBC | … | None` — is "cited" by almost any answer.
Forty such rows were exempt from archival under I-4 because a sealed
record cites them; they were never used, only lexically coincident.

THE RULES, both over sets the project already maintains and argued once:
  informative    each term carries at least one token outside the gate's
                 FILLER_TOKENS and NULL_LITERALS (the gate's own judgement
                 about terms, composed — never its verdict on the row)
  no_substrate   a substrate row (co_activated, recurring_query_term) is an
                 association, not a claim an answer can use; it never cites

FORWARD-ONLY. Sealed lineage flags are amendment-proof and the exemption
reader is untouched; the forty stay recorded as they are.

THE INSTRUMENT IS VERSIONED. The flag's producer is named on every surface
the series read from — `done.reinforcement.citation_test`, the lineage
record (an additive, unhashed column; older rows read null = the F.5
test), the AO capture row and the forecast's resolve context — so the
retention-cost and forecast series are readable across the change.

THE FIFTH MEMBER of the F.5 / AK / AM / AN.2 family, recorded here: a
substrate row "cited" by coincidence was `fire()`d, and `fire()` resets
`last_fired` — the decay clock AN.2 stopped re-promotion from refreshing
was still being refreshed through citation. `no_substrate` closes that
path in citation mode; the test below proves the clock holds.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb import citation, gate, schema
from backend.kb.citation import (
    CITATION_TEST_VERSION, PRE_RULE_TEST_VERSION, citation_test_version,
    cited_ids, is_cited)
from backend.kb.granularity import Granularity
from backend.kb.retriever import RetrievedTriple
from backend.kb.tiered_store import TripleStore
from backend.memory.lineage import LineageStore
from backend.server import create_app, create_services

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "au-token"
H = {"Authorization": f"Bearer {TOKEN}"}


def _t(store, s, r, o, *, source="extraction", gate_off=False, monkeypatch=None):
    if gate_off and monkeypatch is not None:
        monkeypatch.setenv("NEXUS_INGEST_GATE", "off")
    bid = store.add(subject=s, relation=r, object=o, weight=0.6, tier=1,
                    granularity=Granularity.TOPIC, source=source)
    if gate_off and monkeypatch is not None:
        monkeypatch.delenv("NEXUS_INGEST_GATE", raising=False)
    return store.get_by_id(bid)


@pytest.fixture
def store(tmp_path):
    return TripleStore(tmp_path / "kb.db")


ANSWER = ("I can tell you what we know about the BBC: none of its reports on "
          "TAK-441 mention CYP3A4, though the QT prolongation risk is real.")


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------

def test_filler_terms_cannot_cite(store, monkeypatch):
    tell = _t(store, "tell", "co_activated", "know", source="hippocampus_consolidation",
              gate_off=True, monkeypatch=monkeypatch)
    about = _t(store, "about", "is_a", "BBC", gate_off=True, monkeypatch=monkeypatch)
    assert citation._mentions(citation.normalise(ANSWER), "tell"), "fixture: the word is there"
    assert is_cited(tell, ANSWER) is False
    assert is_cited(about, ANSWER) is False


def test_a_null_literal_cannot_cite(store, monkeypatch):
    bbc_none = _t(store, "BBC", "is_a_reliable_news_source", "None",
                  gate_off=True, monkeypatch=monkeypatch)
    assert is_cited(bbc_none, ANSWER) is False


def test_a_substrate_row_never_cites_and_the_same_terms_as_a_claim_do(store):
    sub = _t(store, "TAK-441", "co_activated", "CYP3A4", source="hippocampus_consolidation")
    claim = _t(store, "TAK-441", "is_metabolized_by", "CYP3A4", source="human_assertion")
    assert is_cited(sub, ANSWER) is False
    assert is_cited(claim, ANSWER) is True
    assert cited_ids([RetrievedTriple(sub, 0.6), RetrievedTriple(claim, 0.6)], ANSWER) \
        == {claim.id}


def test_genuine_short_terms_and_one_content_word_terms_still_cite(store):
    """A length rule would have taken these. The rules must not."""
    bbc = _t(store, "BBC", "is_a", "broadcaster", source="human_assertion")
    risk = _t(store, "QT prolongation risk", "is", "real", source="human_assertion")
    assert is_cited(bbc, "The BBC is a broadcaster.") is True
    assert is_cited(risk, ANSWER) is True, "one content token is enough"


def test_the_sets_are_the_gates_own_by_identity():
    """Composed, not copied: the citation test consults the gate's filler
    and null sets and the schema's substrate partition — the same objects."""
    assert citation.FILLER_TOKENS is gate.FILLER_TOKENS
    assert citation.NULL_LITERALS is gate.NULL_LITERALS
    assert citation.SUBSTRATE_RELATIONS is schema.SUBSTRATE_RELATIONS


def test_off_restores_the_pre_rule_test_exactly(store, monkeypatch):
    monkeypatch.setenv("NEXUS_CITATION_RULES", "off")
    tell = _t(store, "tell", "co_activated", "know", source="hippocampus_consolidation",
              gate_off=True, monkeypatch=monkeypatch)
    monkeypatch.setenv("NEXUS_CITATION_RULES", "off")
    assert is_cited(tell, ANSWER) is True, "off must be the F.5 verdict"
    assert citation_test_version() == PRE_RULE_TEST_VERSION
    monkeypatch.setenv("NEXUS_CITATION_RULES", "on")
    assert citation_test_version() == CITATION_TEST_VERSION


def test_the_default_position_is_on(monkeypatch):
    monkeypatch.delenv("NEXUS_CITATION_RULES", raising=False)
    assert citation.citation_rules_enabled() is True


# ---------------------------------------------------------------------------
# The instrument is versioned on every surface the series read from
# ---------------------------------------------------------------------------

class _Answering:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls += 1
        if self.calls == 1:
            for tok in self.answer.split(" "):
                yield tok + " "
        else:
            yield "[]"

    async def get_embedding(self, t): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    monkeypatch.setenv("NEXUS_CITATION_CAPTURE", "on")
    monkeypatch.setenv("NEXUS_CITATION_CAPTURE_PATH", str(tmp_path / "cap.jsonl"))
    svc = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap",
                          provider=_Answering(ANSWER))
    monkeypatch.setenv("NEXUS_INGEST_GATE", "off")
    sub = svc.store.add(subject="tell", relation="co_activated", object="know",
                        weight=0.6, tier=1, granularity=Granularity.TOPIC,
                        source="hippocampus_consolidation")
    monkeypatch.delenv("NEXUS_INGEST_GATE", raising=False)
    claim = svc.store.add(subject="TAK-441", relation="is_metabolized_by",
                          object="CYP3A4", weight=0.6, tier=1,
                          granularity=Granularity.TOPIC, source="human_assertion")
    return svc, TestClient(create_app(svc)), sub, claim, tmp_path


def _done(text: str) -> dict:
    for frame in text.split("\n\n"):
        if "event: done" in frame:
            return json.loads(next(l[6:] for l in frame.split("\n") if l.startswith("data: ")))
    raise AssertionError("no done frame")


def test_the_version_rides_done_lineage_and_capture(rig):
    svc, client, sub, claim, tmp_path = rig
    with client:
        r = client.post("/chat", json={"message": "tell me what we know about TAK-441 and CYP3A4"},
                        headers=H)
    done = _done(r.text)
    assert done["reinforcement"]["citation_test"] == CITATION_TEST_VERSION
    rec = svc.lineage.get(done["lineage_id"])
    assert rec.citation_test == CITATION_TEST_VERSION
    assert rec.to_dict()["citation_test"] == CITATION_TEST_VERSION
    rows = [json.loads(l) for l in
            (tmp_path / "cap.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    assert rows and rows[-1]["citation_test"] == CITATION_TEST_VERSION
    # And the flags themselves are the rules': the substrate row is uncited,
    # the claim is cited, both terms of both being in the answer.
    flags = {it["belief_id"]: it["cited"] for it in rec.injected}
    assert flags.get(claim) is True and flags.get(sub) is False


def test_the_forecast_resolve_context_names_the_test():
    """The forecast's rows resolve against these flags and never amend; the
    context must say which test produced the flag. Pinned by AST on the
    resolve call, because a forecast on a synthetic store is empty."""
    src = (ROOT / "backend" / "cortex" / "forecast.py").read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "resolve"]
    assert calls, "no resolve call in forecast.py"
    keys = set()
    for c in calls:
        for kw in c.keywords:
            if kw.arg == "context" and isinstance(kw.value, ast.Dict):
                keys |= {k.value for k in kw.value.keys if isinstance(k, ast.Constant)}
    assert "citation_test" in keys, keys


def test_old_records_read_null_and_the_column_is_unhashed(tmp_path):
    """Additive migration, F.2's shape: a store from before the column gets
    it with NULL for existing rows, and the record hash is unchanged by it
    (the version is provenance about the flag, not part of the sealed body)."""
    import sqlite3
    db = tmp_path / "kb.db"
    lin = LineageStore(db)
    rec = lin.record_turn(query="q", injected=[], contradictions=[], model_id="m",
                          response_text="r", prompt_prefix="p")
    lin.conn.execute("UPDATE lineage_records SET citation_test = NULL WHERE lineage_id = ?",
                     (rec.lineage_id,))
    lin.conn.commit()
    lin.conn.close()
    again = LineageStore(db)
    old = again.get(rec.lineage_id)
    assert old.citation_test is None
    assert again.verify_record(rec.lineage_id)["record_intact"] is True
    new = again.record_turn(query="q", injected=[], contradictions=[], model_id="m",
                            response_text="r", prompt_prefix="p",
                            citation_test="probe-version")
    assert again.get(new.lineage_id).citation_test == "probe-version"
    payload = {**new.to_dict()}
    payload["citation_test"] = "something else"
    assert LineageStore.compute_record_hash(payload) == new.record_hash, (
        "the version must not be in the hashed body")


# ---------------------------------------------------------------------------
# The fifth member: the clock no longer refreshes through citation
# ---------------------------------------------------------------------------

def test_a_coincidentally_cited_substrate_row_keeps_its_clock(rig, monkeypatch):
    svc, client, sub, claim, _tmp = rig
    before = svc.store.get_by_id(sub).last_fired
    with client:
        client.post("/chat", json={"message": "tell me what we know about TAK-441 and CYP3A4"},
                    headers=H)
    assert svc.store.get_by_id(sub).last_fired == before, (
        "the substrate row was fired by a coincidental citation — the clock reset")
    assert svc.store.get_by_id(claim).last_fired > before, "the claim IS reinforced"


def test_off_reopens_the_clock_path_exactly(tmp_path, monkeypatch):
    """The reducibility proof: with the rules off the substrate row fires
    through citation again, which is the pre-AU turn."""
    monkeypatch.setenv("NEXUS_CITATION_RULES", "off")
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    svc = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap",
                          provider=_Answering(ANSWER))
    monkeypatch.setenv("NEXUS_INGEST_GATE", "off")
    sub = svc.store.add(subject="tell", relation="co_activated", object="know",
                        weight=0.6, tier=1, granularity=Granularity.TOPIC,
                        source="hippocampus_consolidation")
    monkeypatch.delenv("NEXUS_INGEST_GATE", raising=False)
    before = svc.store.get_by_id(sub).last_fired
    with TestClient(create_app(svc)) as client:
        r = client.post("/chat", json={"message": "tell me what we know"}, headers=H)
    assert _done(r.text)["reinforcement"]["citation_test"] == PRE_RULE_TEST_VERSION
    assert svc.store.get_by_id(sub).last_fired > before
