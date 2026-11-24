"""Packet AT.2 — one Law 3 refusal, one record, every confirm route.

THE LINE (AT, adopted): would not is a governance act and is RECORDED;
was never valid is a client error and could not is system state, both
DECLARED on the response only; ingest-gate refusals are counted, never
ledgered. Before AT only /outcome/confirm recorded its Law 3 refusal.

THE GUARD IS DERIVED FROM THE ROUTER, NOT LISTED. It walks `app.routes`,
takes every POST whose body model or query signature carries `confirmed`,
builds a well-formed body — from the template the read surfaces already
publish under `confirm` (T.1), or, where no surface publishes one, from
the request model's own fields with type-appropriate placeholders — posts
it with `confirmed: false`, and expects the refusal to be recorded. A new
gated route that refuses without recording fails here by name; a route
that accepts `confirmed: false` as an ACT (search consent revocation) is
recognised by its 2xx and must record nothing.

Which routes need the model-derived body, and why (the operator asked for
this named rather than assumed): /kb/seed/conceptnet, /kb/seed/wikidata,
/kb/assert/confirm, /search/execute and W9's /commit publish no template because nothing
PULLS them (a commitment is the person's own note, typed where they choose) — seeds are UI controls, the assertion offer and the search
proposal arrive on the SSE stream naming only the endpoint. /kb/seed/offline
and /kb/trails/backfill/confirm take `confirmed` as a query parameter and
need no body. No route's body is written by hand here.

Also pinned: the seal ride (unsealed / mark_sealed / verify), tamper
evidence, the read surface (gated, both ledgers, coverage and rule), the
stats block, the off position, and non-vacuity of the derived set.
"""
from __future__ import annotations

import asyncio
from typing import get_args, get_origin

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.kb.recalibration import (
    REFUSAL_RULE, RecalibrationStore, refusal_ledger_enabled)
from backend.server import create_app, create_services
from tests.test_governed_adaptation import _used_store

TOKEN = "at-token"
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    bid = svc.store.add(subject="batch 47", relation="passed", object="qc",
                        weight=0.6, tier=1, granularity=Granularity.TOPIC,
                        source="human_assertion")
    rec = svc.lineage.record_turn(
        query="q", injected=[{"belief_id": bid, "weight_at_injection": 0.6,
                              "tier_at_injection": 1, "cited": True}],
        contradictions=[], model_id="m", response_text="r", prompt_prefix="p")
    return svc, TestClient(create_app(svc)), rec.lineage_id


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------

def _gated_routes(app) -> dict[str, dict]:
    """{path: {"body_model": cls | None, "query": bool}} for every POST whose
    request carries a `confirmed` field or parameter."""
    out = {}
    for route in app.routes:
        if "POST" not in (getattr(route, "methods", None) or set()):
            continue
        dep = getattr(route, "dependant", None)
        if dep is None:
            continue
        body_model = None
        for p in dep.body_params:
            annotation = p.field_info.annotation
            fields = getattr(annotation, "model_fields", None)
            if fields and "confirmed" in fields:
                body_model = annotation
        query = any(p.name == "confirmed" for p in dep.query_params)
        if body_model is not None or query:
            out[route.path] = {"body_model": body_model, "query": query}
    return out


def _placeholder(annotation):
    origin = get_origin(annotation)
    if annotation is str:
        return "x"
    if annotation is int:
        return 1
    if annotation is float:
        return 0.0
    if annotation is bool:
        return False
    if origin is list:
        (inner,) = get_args(annotation) or (str,)
        return [_placeholder(inner)]
    if annotation is dict or origin is dict:
        return {}
    args = [a for a in get_args(annotation) if a is not type(None)]
    if args:
        return _placeholder(args[0])
    return "x"


def _model_body(model) -> dict:
    """A well-formed body from the model itself: required fields get a
    placeholder of their annotated type, optional fields their default."""
    body = {}
    for name, f in model.model_fields.items():
        if f.is_required():
            body[name] = _placeholder(f.annotation)
    body["confirmed"] = False
    return body


def _published_templates(client, lineage_id) -> dict[str, dict]:
    """endpoint -> body template, from every `confirm*` block an open GET
    publishes, plus the lineage view's pre-filled outcome template."""
    templates = {}
    for path in ("/kb/dedup/pending", "/kb/index/pending", "/kb/trails",
                 "/search/pending", "/governance/envelope"):
        body = client.get(path).json()
        for key, block in body.items():
            if key.startswith("confirm") and isinstance(block, dict) \
                    and "endpoint" in block:
                templates[block["endpoint"]] = dict(block["body"])
    view = client.get(f"/lineage/{lineage_id}", headers=H).json()
    templates[view["confirm_outcome"]["endpoint"]] = dict(view["confirm_outcome"]["body"])
    return templates


def _post_unconfirmed(client, path, info, templates):
    if info["query"] and info["body_model"] is None:
        return client.post(f"{path}?confirmed=false", headers=H), "query"
    if path in templates:
        body = {**templates[path], "confirmed": False}
        return client.post(path, json=body, headers=H), "template"
    body = _model_body(info["body_model"])
    return client.post(path, json=body, headers=H), "model"


def test_the_derived_set_is_not_vacuous(rig):
    _svc, client, _lid = rig
    gated = _gated_routes(client.app)
    assert {"/outcome/confirm", "/governance/envelope/confirm",
            "/kb/dedup/confirm", "/kb/assert/confirm", "/kb/index/confirm",
            "/kb/trails/backfill/confirm", "/kb/seed/offline",
            "/kb/seed/conceptnet", "/kb/seed/wikidata", "/search/confirm",
            "/search/execute"} <= set(gated), sorted(gated)


def test_every_gated_route_records_its_law3_refusal_or_accepts_false_as_an_act(rig):
    """THE GUARD. Derived, both directions: a refusal must be ledgered and
    say so; an acceptance of `false` (consent revocation) must ledger
    nothing and declare what it did."""
    svc, client, lid = rig
    gated = _gated_routes(client.app)
    templates = _published_templates(client, lid)
    outcomes = {}
    for path, info in sorted(gated.items()):
        before = len(svc.recalibration.refusals(limit=500))
        r, source = _post_unconfirmed(client, path, info, templates)
        after = svc.recalibration.refusals(limit=500)
        if 200 <= r.status_code < 300:
            assert "status" in r.json(), f"{path} accepted false silently"
            assert len(after) == before, f"{path} ledgered an act"
            outcomes[path] = ("act", source)
            continue
        assert r.status_code == 422, f"{path} -> {r.status_code}: {r.text}"
        body = r.json()
        assert body.get("error", "").startswith("Law 3"), (
            f"{path} refused with {body} — the model-derived body did not "
            f"reach the Law 3 gate; publish a template or check the gate first")
        assert body.get("recorded") is True and body.get("event_id"), (
            f"{path} refused without recording — it joined the silent set")
        assert len(after) == before + 1, path
        assert any(row["event_id"] == body["event_id"] for row in after), path
        outcomes[path] = ("recorded", source)
    assert outcomes["/search/confirm"][0] == "act"
    assert sum(1 for v in outcomes.values() if v[0] == "recorded") >= 10
    # The routes whose bodies had to come from the model, named — see the
    # module docstring for why each publishes no template.
    assert {p for p, v in outcomes.items() if v[1] == "model"} == {
        "/kb/seed/conceptnet", "/kb/seed/wikidata", "/kb/assert/confirm",
        "/search/execute", "/commit"}


def _probe_all(client, svc, lid) -> dict[str, tuple[int, dict, list]]:
    """{path: (status, body, rows added by that request)} for every gated
    route posted unconfirmed — the raw material for the shape guards."""
    gated = _gated_routes(client.app)
    templates = _published_templates(client, lid)
    out = {}
    for path, info in sorted(gated.items()):
        before = {r["event_id"] for r in svc.recalibration.refusals(limit=500)}
        r, _ = _post_unconfirmed(client, path, info, templates)
        after = [r2 for r2 in svc.recalibration.refusals(limit=500)
                 if r2["event_id"] not in before]
        out[path] = (r.status_code, r.json(), after)
    return out


@pytest.mark.parametrize("position", ["on", "off"])
def test_a_response_claims_a_record_only_when_a_row_exists(rig, monkeypatch, position):
    """THE SHAPE GUARD, both switch positions, derived over every gate. A
    stale `recorded: true` with no row would be worse than either silence
    or a refusal — so the claim and the row are asserted TOGETHER: every
    4xx response carries `recorded` iff exactly one new row carries its
    `event_id`. Under `off` the generic ledger writes nothing and no route
    may claim otherwise; /outcome/confirm's S.2 rows are not behind the AT
    switch and its claim stays true because its row exists."""
    monkeypatch.setenv("NEXUS_REFUSAL_LEDGER", position)
    svc, client, lid = rig
    claimed = 0
    for path, (status, body, rows) in _probe_all(client, svc, lid).items():
        if status < 400:
            assert rows == [] and "recorded" not in body, path
            continue
        if body.get("recorded"):
            assert body["recorded"] is True and body.get("event_id"), path
            assert [r["event_id"] for r in rows] == [body["event_id"]], (
                f"{path} claims recorded with no matching row ({position})")
            claimed += 1
        else:
            assert rows == [], f"{path} recorded without saying so ({position})"
            assert "event_id" not in body, path
    if position == "off":
        assert claimed == 1, "only the outcome route's S.2 row survives `off`"
    else:
        assert claimed >= 10


def test_version_reused_is_recorded_as_would_not(rig):
    """AT.3. A well-formed request to install a policy under an existing
    name: the system could have overwritten and declined because versions
    are immutable — a governance act, recorded with the actor and scope."""
    svc, client, _lid = rig
    body = {"version": "v1", "reason": "first", "confirmed": True,
            "actor_id": "alice", "scope": "trial-alpha"}
    assert client.post("/governance/envelope/confirm", json=body, headers=H).status_code == 200
    r = client.post("/governance/envelope/confirm", json=body, headers=H)
    assert r.status_code == 409 and r.json()["error"] == "version_reused"
    assert r.json()["recorded"] is True
    row = next(x for x in svc.recalibration.refusals()
               if x["event_id"] == r.json()["event_id"])
    assert row["rule"] == "version_reused"
    assert row["route"] == "/governance/envelope/confirm"
    assert row["actor"]["id"] == "alice" and row["scope"] == "trial-alpha"


def _ambiguous_body():
    return {"subject_a": "qtc", "relation_a": "rises", "object_a": "at 600mg",
            "subject_b": "x", "relation_b": "y", "object_b": "z",
            "keep": "b", "confirmed": True, "actor_id": "alice",
            "actor_role": "person"}


def test_ambiguous_archive_target_is_recorded_as_would_not(rig):
    """AT.4. The claim exists under two sources and the caller named
    neither; a pre-L.2a system archived both, and L.2a's policy declines to
    guess. A refusal that exists because a policy was installed is a
    governance act — recorded, with the stated actor, as a digest."""
    svc, client, _lid = rig
    for src in ("wikidata", "extraction"):
        svc.store.add(subject="qtc", relation="rises", object="at 600mg",
                      weight=0.7, tier=1, granularity=Granularity.TOPIC,
                      source=src)
    r = client.post("/kb/dedup/confirm", json=_ambiguous_body(), headers=H)
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error"] == "ambiguous archive target" and body["recorded"] is True
    row = next(x for x in svc.recalibration.refusals()
               if x["event_id"] == body["event_id"])
    assert row["rule"] == "ambiguous_archive_target"
    assert row["route"] == "/kb/dedup/confirm"
    assert row["actor"] == {"id": "alice", "role": "person", "source": "local"}
    assert "600mg" not in str(row), "the row carries a digest, not the claim"
    # Nothing was archived (I-2): both copies still resolve live.
    assert all(t.archived is False for t in svc.store.all()
               if t.subject == "qtc")


def test_ambiguous_archive_target_honours_the_switch(rig, monkeypatch):
    svc, client, _lid = rig
    for src in ("wikidata", "extraction"):
        svc.store.add(subject="qtc", relation="rises", object="at 600mg",
                      weight=0.7, tier=1, granularity=Granularity.TOPIC,
                      source=src)
    monkeypatch.setenv("NEXUS_REFUSAL_LEDGER", "off")
    r = client.post("/kb/dedup/confirm", json=_ambiguous_body(), headers=H)
    assert r.status_code == 409 and "recorded" not in r.json()
    assert svc.recalibration.refusals() == []


def test_version_reused_honours_the_switch(rig, monkeypatch):
    svc, client, _lid = rig
    body = {"version": "v1", "reason": "first", "confirmed": True}
    client.post("/governance/envelope/confirm", json=body, headers=H)
    monkeypatch.setenv("NEXUS_REFUSAL_LEDGER", "off")
    r = client.post("/governance/envelope/confirm", json=body, headers=H)
    assert r.status_code == 409 and "recorded" not in r.json()
    assert svc.recalibration.refusals() == []


def test_the_status_codes_are_one_code(rig):
    """The seed routes said 400, 400 and 403 and the index route refused in
    the schema shape; every gate is now 422 with the named error."""
    svc, client, lid = rig
    gated = _gated_routes(client.app)
    templates = _published_templates(client, lid)
    codes = set()
    for path, info in gated.items():
        r, _ = _post_unconfirmed(client, path, info, templates)
        if r.status_code >= 400:
            codes.add(r.status_code)
            assert "error" in r.json(), path
    assert codes == {422}


# ---------------------------------------------------------------------------
# The ledger itself
# ---------------------------------------------------------------------------

def test_the_row_holds_the_attempt_not_the_text(tmp_path):
    _store, _ids = _used_store(tmp_path)
    recal = RecalibrationStore(tmp_path / "kb.db")
    row = recal.record_refusal(route="/kb/assert/confirm", rule="law3_unconfirmed",
                               detail="confirmed=true required",
                               request={"sentence": "the dose was 600mg",
                                        "confirmed": False})
    assert row["request_sha256"] and len(row["request_sha256"]) == 64
    listed = recal.refusals()[0]
    assert "600mg" not in str(listed)
    assert listed["ledger"] == "refused_requests" and listed["actor"]["role"] == "local_operator"
    recal.close()


def test_refusals_ride_the_governance_seal_and_are_tamper_evident(tmp_path):
    _store, _ids = _used_store(tmp_path)
    recal = RecalibrationStore(tmp_path / "kb.db")
    row = recal.record_refusal(route="/kb/seed/offline", rule="law3_unconfirmed",
                               detail="d", request={"confirmed": False})
    assert any(k == row["event_id"] for k, _h in recal.unsealed())
    recal.mark_sealed([row["event_id"]], block_index=3)
    assert recal.refusals()[0]["sealed_block"] == 3
    v = recal.verify_governance_records(None)
    assert v["verdict"] == "intact" and v["row"]["checked"] >= 1
    recal.conn.execute("UPDATE refused_requests SET route = '/elsewhere' "
                       "WHERE event_id = ?", (row["event_id"],))
    recal.conn.commit()
    v2 = recal.verify_governance_records(None)
    assert v2["verdict"] == "row_hash_mismatch"
    assert any(f["table"] == "refused_requests" for f in v2["row"]["failures"])
    recal.close()


def test_a_real_cycle_seals_a_refusal(rig):
    svc, client, _lid = rig
    r = client.post("/kb/index/confirm", json={"confirmed": False}, headers=H)
    eid = r.json()["event_id"]
    svc.kairos.session_ended()
    asyncio.run(svc.kairos.run_one_cycle(force_snapshot=True))
    row = next(x for x in svc.recalibration.refusals() if x["event_id"] == eid)
    assert row["sealed_block"] is not None
    v = client.get("/governance/verify", headers=H).json()
    assert v["verdict"] == "intact" and v["seal"]["sealed"] >= 1


def test_the_read_surface_is_gated_and_carries_rule_and_coverage(rig):
    _svc, client, _lid = rig
    assert client.get("/governance/refusals",
                      headers={"Authorization": "Bearer wrong"}).status_code == 401
    body = client.get("/governance/refusals", headers=H).json()
    assert body["refusals"] == []
    assert body["coverage"]["records_begin_at"] is None
    assert body["coverage"]["rule"] == REFUSAL_RULE
    assert "nothing recorded yet" in body["coverage"]["note"]
    assert body["verify"] == "/governance/verify"


def test_the_stats_surface_declares_coverage(rig):
    _svc, client, _lid = rig
    block = client.get("/cortex/stats").json()["refusal_ledger"]
    assert block["enabled"] is True and block["events"] == 0
    assert block["read"] == "/governance/refusals"
    assert block["by_ledger"] == {"refused_requests": 0, "outcome_events": 0}


def test_off_declares_and_records_nothing(rig, monkeypatch):
    monkeypatch.setenv("NEXUS_REFUSAL_LEDGER", "off")
    svc, client, _lid = rig
    r = client.post("/kb/index/confirm", json={"confirmed": False}, headers=H)
    assert r.status_code == 422 and r.json()["error"].startswith("Law 3")
    assert "recorded" not in r.json(), "off must not claim a record"
    assert svc.recalibration.refusals() == []
    assert client.get("/cortex/stats").json()["refusal_ledger"]["enabled"] is False


def test_the_default_position_is_on(monkeypatch):
    monkeypatch.delenv("NEXUS_REFUSAL_LEDGER", raising=False)
    assert refusal_ledger_enabled() is True
