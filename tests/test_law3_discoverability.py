"""Packet T.1 — a Law 3 read surface names the write route that answers it.

WHAT THE RUN-THROUGH FOUND, AND WHAT IT ACTUALLY WAS
    `POST /governance/envelope` -> 405, and the conclusion drawn was that the
    envelope could not be installed. Measured against the router rather than
    remembered: the write route was never missing. It is
    `POST /governance/envelope/confirm`, it has existed since S.2, it is
    token-gated, and it is already in `test_auth_coverage`'s writer ratchet at
    13. Nothing was absent.

    What was absent is any way to LEARN that from the surface. `GET
    /governance/envelope` returned the policy, said `is_default: true`, and
    named no route that could change it.

AND IT IS A CLASS
    Measured across every Law 3 surface: the two payloads that are PUSHED to a
    client both name their endpoint (`web_search` and `assertion` alerts carry
    `endpoint`). All three that are PULLED did not —

        GET /kb/dedup/pending     -> named /kb/dedup/confirm    : False
        GET /search/pending       -> named /search/confirm      : False
        GET /governance/envelope  -> named .../confirm          : False

    A convention that holds on the push path and not the pull path is not a
    convention. The run-through happened to hit the instance where it mattered
    most, which is how this class was found rather than the class being looked
    for. All three declare it now, in one shape, and this file pins that the
    set does not shrink.

WHAT THIS FILE DOES NOT ASSERT
    That the body template is CORRECT for every future field — a template can
    drift from its endpoint's model. `test_the_envelope_template_is_accepted_
    by_its_own_endpoint` closes that for the envelope by POSTing the template
    verbatim; the other two need a candidate to exist first, and inventing one
    to satisfy a test would be testing the fixture.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.server import create_app, create_services

TOKEN = "packet-t1-token"

# Every Law 3 read surface, and the write route each proposes work for.
# Frozen: a new propose/confirm pair that does not declare its endpoint is a
# new instance of the defect, and a removed one is a decision.
LAW3_READ_SURFACES = {
    "/governance/envelope": "/governance/envelope/confirm",
    "/kb/dedup/pending":    "/kb/dedup/confirm",
    "/search/pending":      "/search/confirm",
    # W.1 — the semantic index backfill. Read surface reports coverage; the
    # confirm route is a bulk outbound operation (one embedding per belief).
    "/kb/index/pending":    "/kb/index/confirm",
    # AB — trail backfill over historical lineage records. The read surface
    # is the trail status (active pairs, sensitivity bound, coverage).
    "/kb/trails":           "/kb/trails/backfill/confirm",
}


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    services = create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "turn_metrics.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots")
    return TestClient(create_app(services)), services


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


# ---------------------------------------------------------------------------
# The write route exists — the finding, pinned so it cannot be "fixed" twice
# ---------------------------------------------------------------------------

def test_the_envelope_write_route_exists_and_the_bare_path_is_read_only(
        tmp_path, monkeypatch):
    """Both halves of the 405.

    A future packet reading "the envelope cannot be installed" must find this
    test rather than add a second write route beside the one that works.
    """
    client, _s = _client(tmp_path, monkeypatch)
    with client:
        assert client.get("/governance/envelope").status_code == 200
        # The bare path is GET-only ON PURPOSE — this is where the 405 came
        # from, and it is correct behaviour, not the defect.
        assert client.post("/governance/envelope", headers=_auth(),
                           json={}).status_code == 405
        # ...and the write route is one segment further on.
        r = client.post("/governance/envelope/confirm", headers=_auth(), json={
            "version": "t1-probe", "max_delta": 0.05,
            "movable_classes": ["extraction"],
            "reason": "sentinel", "confirmed": True})
    assert r.status_code == 200, r.text


def test_the_envelope_write_route_needs_a_token(tmp_path, monkeypatch):
    """It sets policy, so it is a writer. Already in the ratchet at 13 since
    S.2 — asserted here too because T.1 is where someone would think to
    reclassify it."""
    client, _s = _client(tmp_path, monkeypatch)
    with client:
        r = client.post("/governance/envelope/confirm",
                        headers={"Authorization": "Bearer wrong"},
                        json={"version": "x", "reason": "y", "confirmed": True})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# The class
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,confirm", sorted(LAW3_READ_SURFACES.items()))
def test_every_law3_read_surface_names_its_write_route(
        path, confirm, tmp_path, monkeypatch):
    client, _s = _client(tmp_path, monkeypatch)
    with client:
        r = client.get(path, headers=_auth())
    assert r.status_code == 200, r.text
    block = r.json().get("confirm")
    assert block is not None, f"{path} does not name a confirm route"
    assert block["endpoint"] == confirm
    assert block["method"] == "POST"
    assert block["auth"], "the surface does not say a token is needed"
    assert block["body"], "the surface names a route and not its shape"
    assert block["body"].get("confirmed") is True, (
        f"{path}'s template omits Law 3's explicit confirmation, which is the "
        f"one field a caller is most likely to leave out")


def test_the_set_of_law3_read_surfaces_is_frozen(tmp_path, monkeypatch):
    """Derived from the router, not hand-listed — Packet G's pattern. A new
    propose/confirm pair that forgets to declare its endpoint fails here
    rather than being discovered by an operator guessing at a 405.
    """
    client, _s = _client(tmp_path, monkeypatch)
    app = client.app
    confirm_routes = {r.path for r in app.routes
                      if "POST" in (getattr(r, "methods", None) or set())
                      and r.path.endswith("/confirm")}
    # Every confirm route in the app is either declared by a read surface
    # here, or is reached by an SSE alert that carries its own `endpoint`.
    declared = set(LAW3_READ_SURFACES.values())
    # `/outcome/confirm` is declared by `GET /lineage/{id}` under
    # `confirm_outcome`, pre-filled with that record's own id — see
    # `test_the_lineage_view_names_the_outcome_route`. It is not in
    # LAW3_READ_SURFACES because that path is parameterised and the generic
    # probe above has no record to fetch.
    alert_borne = {"/kb/assert/confirm", "/search/confirm",
                   "/outcome/confirm"}
    unaccounted = confirm_routes - declared - alert_borne
    assert unaccounted == set(), (
        f"confirm route(s) no surface points at: {sorted(unaccounted)}. Add "
        f"the pointer, or add it to `alert_borne` with the alert that carries "
        f"it — an operator cannot guess a path.")


def test_the_envelope_template_is_accepted_by_its_own_endpoint(
        tmp_path, monkeypatch):
    """A template that its endpoint rejects is worse than none — it turns one
    guess into a confident wrong answer. So the surface's own body is POSTed
    verbatim, with only the version changed to avoid the immutability refusal.
    """
    client, services = _client(tmp_path, monkeypatch)
    with client:
        template = client.get("/governance/envelope",
                              headers=_auth()).json()["confirm"]["body"]
        body = dict(template)
        body["version"] = "from-the-template"
        r = client.post("/governance/envelope/confirm", headers=_auth(),
                        json=body)
    assert r.status_code == 200, (
        f"the surface advertises a body its own endpoint refuses: {r.text}")


# ---------------------------------------------------------------------------
# The sentinel the packet asked for — end to end, with non-vacuity
# ---------------------------------------------------------------------------

def test_an_installed_envelope_is_readable_recorded_and_cited(tmp_path, monkeypatch, recalibration_on):
    """Installed -> readable -> in `history` -> cited by the recalibration it
    permitted.

    NON-VACUITY FIRST: `history` must be empty and `is_default` true before
    the install, or every assertion after it passes on a store where a policy
    was already in force and proves nothing about installing one.
    """
    client, services = _client(tmp_path, monkeypatch)
    bid = services.store.add(subject="QTc", relation="stands_for",
                             object="corrected QT", weight=0.55, tier=2,
                             granularity=Granularity.TOPIC, source="extraction")
    with client:
        before = client.get("/governance/envelope", headers=_auth()).json()
        assert before["history"] == [], "a policy was already installed"
        assert before["is_default"] is True
        assert before["active"]["version"] == "v0-immovable"

        # ...and nothing may move under it. This is the "installing nothing
        # leaves today's behaviour" half, asserted rather than asserted-about.
        blocked = client.post("/outcome/confirm", headers=_auth(), json={
            "event_type": "review", "decision": "approved",
            "direction": "confirming", "references": {"belief_ids": [bid]},
            "magnitude": 0.05, "confirmed": True}).json()
        assert blocked["applied"] == []
        assert blocked["refused"][0]["rule"] == "class_not_movable"

        r = client.post("/governance/envelope/confirm", headers=_auth(), json={
            "version": "board-2026-01", "max_delta": 0.05,
            "movable_classes": ["extraction"], "cumulative_max": 0.20,
            "reason": "approved at change board", "confirmed": True})
        assert r.status_code == 200, r.text

        after = client.get("/governance/envelope", headers=_auth()).json()
        assert after["is_default"] is False
        assert after["active"]["version"] == "board-2026-01"
        assert [h["version"] for h in after["history"]] == ["board-2026-01"]
        assert after["history"][0]["reason"] == "approved at change board"

        moved = client.post("/outcome/confirm", headers=_auth(), json={
            "event_type": "review", "decision": "approved",
            "direction": "confirming", "references": {"belief_ids": [bid]},
            "magnitude": 0.05, "confirmed": True}).json()
        assert [a["belief_id"] for a in moved["applied"]] == [bid], moved
        assert moved["applied"][0]["envelope_version"] == "board-2026-01"

        trace = client.get(f"/belief/{bid}/provenance", headers=_auth()).json()

    assert trace["stored_weight"] == 0.55, "the record layer moved"
    assert len(trace["steps"]) == 1
    assert trace["steps"][0]["envelope_version"] == "board-2026-01", (
        "the recalibration does not cite the policy that permitted it")


def test_installing_a_second_envelope_does_not_orphan_the_first(tmp_path, monkeypatch, recalibration_on):
    """A recalibration cites a version. Installing a newer policy must leave
    that citation resolvable — otherwise the audit trail says "permitted by
    board-2026-01" and nothing can show what board-2026-01 was."""
    client, services = _client(tmp_path, monkeypatch)
    bid = services.store.add(subject="QTc", relation="stands_for",
                             object="corrected QT", weight=0.55, tier=2,
                             granularity=Granularity.TOPIC, source="extraction")
    with client:
        for version, delta in (("v1", 0.05), ("v2", 0.02)):
            client.post("/governance/envelope/confirm", headers=_auth(), json={
                "version": version, "max_delta": delta,
                "movable_classes": ["extraction"],
                "reason": f"policy {version}", "confirmed": True})
            client.post("/outcome/confirm", headers=_auth(), json={
                "event_type": "review", "decision": "approved",
                "direction": "confirming", "references": {"belief_ids": [bid]},
                "magnitude": 0.02, "confirmed": True})
        history = client.get("/governance/envelope",
                             headers=_auth()).json()["history"]
        trace = client.get(f"/belief/{bid}/provenance", headers=_auth()).json()

    assert [h["version"] for h in history] == ["v1", "v2"], (
        "an installed policy vanished from the record")
    cited = {s["envelope_version"] for s in trace["steps"]}
    assert cited == {"v1", "v2"}
    known = {h["version"] for h in history}
    assert cited <= known, (
        f"a recalibration cites a policy the history cannot show: {cited - known}")


def test_the_lineage_view_names_the_outcome_route(tmp_path, monkeypatch, recalibration_on):
    """THE INSTANCE THE RUN-THROUGH ACTUALLY HIT, and the one I missed until
    the frozen-set guard above failed on it.

    `/outcome/confirm` had no surface pointing at it — not a read surface, not
    an alert. The 422 naming three missing fields was a script guessing at a
    shape nothing published. The template here comes back PRE-FILLED with the
    record's own lineage_id, so there is nothing left to guess.
    """
    client, services = _client(tmp_path, monkeypatch)
    bid = services.store.add(subject="QTc", relation="stands_for",
                             object="corrected QT", weight=0.55, tier=2,
                             granularity=Granularity.TOPIC, source="extraction")
    rec = services.lineage.record_turn(
        query="what is QTc?",
        injected=[{"belief_id": bid, "weight_at_injection": 0.55,
                   "tier_at_injection": 2, "cited": True}],
        contradictions=[], model_id="test", response_text="QTc.",
        prompt_prefix="STABLE")

    with client:
        view = client.get(f"/lineage/{rec.lineage_id}", headers=_auth()).json()
        block = view["confirm_outcome"]
        assert block["endpoint"] == "/outcome/confirm"
        assert block["body"]["references"]["lineage_id"] == rec.lineage_id, (
            "the template is generic; the caller still has to fill in the id "
            "they were already looking at")

        # ...and the advertised body is accepted verbatim. A pre-filled
        # template its endpoint refuses is a confident wrong answer.
        client.post("/governance/envelope/confirm", headers=_auth(), json={
            "version": "v1", "max_delta": 0.05,
            "movable_classes": ["extraction"],
            "reason": "so the outcome has something to move", "confirmed": True})
        r = client.post("/outcome/confirm", headers=_auth(), json=block["body"])
    assert r.status_code == 200, (
        f"the lineage view advertises a body /outcome/confirm refuses: {r.text}")
    assert [a["belief_id"] for a in r.json()["applied"]] == [bid]
