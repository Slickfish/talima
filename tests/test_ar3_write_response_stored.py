"""Packet AR.3 — a write response reports what was stored, not what was sent.

THE DEFECT, measured while re-deriving the integration reference:
`POST /kb/assert/confirm` returned `relation: "is"` for a belief the store
holds as `is_a`. `add()` normalises relations (K.3: five spellings of "is a"
become one) and the handler composed its response from the PARSED triple,
so an integrator building a join on the returned value would build it on a
value that can never be retrieved. The assertion OFFER (S.4) had the same
shape one step earlier: it declared "what would be stored" from the parsed
triple.

THE FIX: one function says what `add()` will store — `TripleStore.
stored_form()` — and `add()` itself uses it, so the two cannot drift. The
response and the offer report the stored form under the plain keys (the
value an integrator joins on) and the parsed form under `as_parsed`, named
distinctly. The ambiguous single field is gone.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore
from backend.server import create_app, create_services

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "ar3-token"
H = {"Authorization": f"Bearer {TOKEN}"}

# A sentence whose parsed relation ("is") differs from its stored one
# ("is_a"). Non-vacuity for everything below.
SENTENCE = "TAK-441 is metabolized by CYP3A4."


class _AnsweringProvider:
    async def stream_chat(self, model, messages, num_ctx=8192):
        yield "noted."

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return False


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap",
                          provider=_AnsweringProvider())
    return svc, TestClient(create_app(svc))


def test_non_vacuity_parsed_and_stored_forms_differ_for_the_fixture():
    s, r, o = TripleStore.stored_form("TAK-441", "is", "metabolized by CYP3A4")
    assert r == "is_a" and r != "is"
    assert (s, o) == ("TAK-441", "metabolized by CYP3A4")


def test_stored_form_is_what_add_actually_stores(tmp_path):
    """The helper is only worth having if it is the truth: whatever it says,
    the row says the same."""
    store = TripleStore(tmp_path / "kb.db")
    for s, r, o in [("  padded  ", "IS an", "thing"),
                    ("x", "is_a_kind_of", "y"),
                    ("x", "orbits", "y")]:
        bid = store.add(subject=s, relation=r, object=o, weight=0.6, tier=1,
                        granularity=Granularity.TOPIC, source="human_assertion")
        t = store.get_by_id(bid)
        assert (t.subject, t.relation, t.object) == TripleStore.stored_form(s, r, o)


def test_add_derives_from_stored_form_so_they_cannot_drift():
    """AST: `add()` calls `stored_form`. A second copy of the normalisation
    block is exactly how the response drifted from the store."""
    src = (ROOT / "backend" / "kb" / "tiered_store.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "TripleStore")
    add = next(n for n in cls.body
               if isinstance(n, ast.FunctionDef) and n.name == "add")
    calls = {getattr(c.func, "attr", None) for c in ast.walk(add)
             if isinstance(c, ast.Call)}
    assert "stored_form" in calls, "add() no longer derives from stored_form"


def test_the_assert_response_reports_the_stored_belief(rig):
    """THE SENTINEL. The plain keys are the stored values — the ones a later
    read returns — and the parsed form is beside them, named."""
    svc, client = rig
    r = client.post("/kb/assert/confirm",
                    json={"sentence": SENTENCE, "confirmed": True}, headers=H)
    assert r.status_code == 200, r.text
    b = r.json()["beliefs"][0]
    t = svc.store.get_by_id(b["belief_id"])
    assert (b["subject"], b["relation"], b["object"]) == \
        (t.subject, t.relation, t.object)
    assert b["relation"] == "is_a"
    assert b["as_parsed"] == {"subject": "TAK-441", "relation": "is",
                              "object": "metabolized by CYP3A4"}


def test_the_offer_declares_the_stored_form_not_the_parsed_one(rig):
    """S.4's offer says what WOULD be stored; that has to be what add() will
    store, or the confirmation confirms something else."""
    _svc, client = rig
    with client:
        r = client.post("/chat", json={"message": SENTENCE}, headers=H)
    alerts = []
    for frame in r.text.split("\n\n"):
        if "event: kb_pressure_alert" in frame:
            raw = next(l[6:] for l in frame.split("\n") if l.startswith("data: "))
            d = json.loads(raw)
            if d.get("type") == "assertion":
                alerts.append(d)
    assert alerts, "no assertion offer for the fixture sentence"
    t = alerts[0]["triples"][0]
    assert t["relation"] == "is_a"
    assert t["as_parsed"]["relation"] == "is"
    assert (t["subject"], t["relation"], t["object"]) == \
        TripleStore.stored_form("TAK-441", "is", "metabolized by CYP3A4")


def test_with_the_gate_off_stored_form_reports_the_unnormalised_row(tmp_path, monkeypatch):
    """K.3: normalisation is behind the same switch as the gate. The helper
    must follow the switch, or with the gate off it would report a
    normalised value the store never wrote."""
    monkeypatch.setenv("NEXUS_INGEST_GATE", "off")
    assert TripleStore.stored_form("x", "is", "y") == ("x", "is", "y")
    store = TripleStore(tmp_path / "kb.db")
    bid = store.add(subject="x", relation="is", object="y", weight=0.6,
                    tier=1, granularity=Granularity.TOPIC,
                    source="human_assertion")
    assert store.get_by_id(bid).relation == "is"
