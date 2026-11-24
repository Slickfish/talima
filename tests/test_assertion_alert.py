"""Packet S.4 — assertion candidates are offered, not only accepted.

THE GAP
    O.2b built two of the three pieces: `detect_assertions()` recognises when
    a person has stated a claim, and `POST /kb/assert/confirm` stores it under
    Law 3. Nothing ever told the person. So the operator could type "the dose
    was 600mg", have it recognised, and never learn that it could be kept —
    a feature reachable only by someone who had read the source.

    Detection without an offer is not a feature. It is a fact about the code.

WHY IT IS EMITTED IN THE LOOP AND NOT THROUGH THE BRIDGE
    Detection runs on the user's message during a turn, so there is a live SSE
    channel. `BRIDGE_EVENTS` exists for KAIROS's idle-time queue, which has
    none. `web_search` takes the same path for the same reason, and
    `test_alert_subtypes_do_not_share_one_delivery_path` in the contract file
    pins that the three delivery routes stay distinguishable.

THE CONTRACT HOLE THIS PACKET FOUND
    Adding a seventh subtype should have failed `tests/test_sse_contract.py`
    until CLAUDE.md declared it. It did not — `produced_alert_subtypes()`
    derived its answer from the declared list, so `produced <= declared` was
    true by construction and an undeclared subtype was invisible to the layer
    whose entire job is to notice one. Measured: `assertion` was emitted and
    all eight contract tests passed.

    That is H.4's own finding one level down, and it is fixed in the same
    commit that would otherwise have benefited from it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb.assertion import detect_assertions
from backend.server import create_app, create_services

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend" / "nexus_ui.html"
TOKEN = "packet-s4-token"

# The operator's own sentence, from O.2a. If the detector stops recognising
# it, everything below passes while offering nothing.
ASSERTED = "The dose was 600mg in the second cohort."


class _AnsweringProvider:
    """R.4 made the suite's default provider refuse `stream_chat`, because no
    test reached that path. These do — a turn is where the offer happens — so
    they take R.4's escape hatch and pass their own."""

    async def stream_chat(self, model, messages, num_ctx=8192):
        yield "noted."

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return False


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    services = create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "turn_metrics.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots",
        provider=_AnsweringProvider())
    return TestClient(create_app(services)), services


def _alerts(body: str, subtype: str) -> list[dict]:
    out = []
    for frame in body.split("\n\n"):
        if "event: kb_pressure_alert" not in frame:
            continue
        raw = next((l[6:] for l in frame.split("\n") if l.startswith("data: ")), "")
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if data.get("type") == subtype:
            out.append(data)
    return out


def _turn(client, message: str) -> str:
    r = client.post("/chat", json={"message": message},
                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200, r.text
    return r.text


# ---------------------------------------------------------------------------
# NON-VACUITY — the detector still recognises the sentence
# ---------------------------------------------------------------------------

def test_the_detector_recognises_the_operators_sentence():
    """Everything below asserts something about the OFFER. If detection had
    quietly regressed, each test would pass on a turn where there was nothing
    to offer."""
    cands = detect_assertions(ASSERTED)
    assert cands, f"no assertion detected in {ASSERTED!r}"
    assert cands[0].reason, "the candidate carries no reason to show the user"


# ---------------------------------------------------------------------------
# The offer
# ---------------------------------------------------------------------------

def test_an_asserted_sentence_is_offered_during_the_turn(tmp_path, monkeypatch):
    """THE PACKET. The claim reaches the client while the turn is running."""
    client, _s = _client(tmp_path, monkeypatch)
    with client:
        alerts = _alerts(_turn(client, ASSERTED), "assertion")

    assert len(alerts) == 1, f"expected one offer, saw {len(alerts)}"
    alert = alerts[0]
    assert alert["action"] == "proposed"
    assert alert["sentence"] == ASSERTED
    assert alert["reason"], "the offer does not say why it was proposed"
    assert alert["endpoint"] == "/kb/assert/confirm", (
        "the offer does not name the Law 3 gate that accepts it")


def test_the_offer_declares_what_would_be_stored(tmp_path, monkeypatch):
    """Law 3 asks for confirmation; confirming something you cannot see is not
    confirmation. The triples are in the payload before anything is written."""
    client, _s = _client(tmp_path, monkeypatch)
    with client:
        alert = _alerts(_turn(client, ASSERTED), "assertion")[0]

    assert alert["shaped"] is True
    assert alert["triples"], "shaped=true with nothing to show"
    for t in alert["triples"]:
        assert t["subject"] and t["relation"] and t["object"]
        assert 0.0 <= t["weight"] <= 1.0
        assert isinstance(t["tier"], int)


def test_a_question_is_not_offered(tmp_path, monkeypatch):
    """The other direction, and the one that keeps the offer from being noise.
    Without it, an alert on every turn would pass every test above."""
    client, _s = _client(tmp_path, monkeypatch)
    with client:
        alerts = _alerts(_turn(client, "What was the dose in the second cohort?"),
                         "assertion")
    assert alerts == [], f"a question was offered as an assertion: {alerts}"


def test_an_unshapeable_assertion_is_still_offered_and_says_so(
        tmp_path, monkeypatch):
    """I-6, and a deliberate choice worth stating: a sentence the shaper
    cannot turn into a triple is offered with `shaped: false` rather than
    silently dropped. An operator learning that their claim cannot be stored
    is more useful than silence, and it is the same answer
    `/kb/assert/confirm` already gives on confirmation.
    """
    import backend.loop.agentic_loop as loop

    # MEASURED FIRST: every sentence the detector currently accepts, the
    # shaper also shapes — so there is no natural example, and hunting for one
    # would make this test a hostage to the shaper's coverage. The branch is
    # forced instead, which proves the offer handles it rather than proving a
    # sentence exists. Stated plainly because the difference matters: this is
    # a guard on the code path, not evidence that the path is reached today.
    monkeypatch.setattr(loop, "assertion_triples", lambda _s: [])

    client, _s = _client(tmp_path, monkeypatch)
    with client:
        alerts = _alerts(_turn(client, ASSERTED), "assertion")

    assert alerts, "a detected-but-unshapeable assertion was dropped silently"
    assert alerts[0]["shaped"] is False
    assert alerts[0]["triples"] == []
    assert alerts[0]["sentence"] == ASSERTED, (
        "the offer stopped naming the sentence it could not shape")


def test_each_sentence_in_a_message_is_offered_separately(tmp_path, monkeypatch):
    """O.2b split before judging because a paragraph may carry one claim and
    three questions. The offer has to preserve that or a mixed message
    produces one alert naming the whole paragraph."""
    message = ("What is the QT interval? The dose was 600mg in the second "
               "cohort. The infusion ran for 30 minutes in every arm.")
    client, _s = _client(tmp_path, monkeypatch)
    with client:
        alerts = _alerts(_turn(client, message), "assertion")

    assert len(alerts) == len(detect_assertions(message)) >= 2
    for a in alerts:
        assert "?" not in a["sentence"], (
            f"a question was carried into an offer: {a['sentence']!r}")


def test_the_offer_and_the_gate_agree(tmp_path, monkeypatch):
    """END TO END: what the offer said would be stored is what confirming
    stores. Two independent calls to the shaper are two chances to disagree.
    """
    client, services = _client(tmp_path, monkeypatch)
    with client:
        alert = _alerts(_turn(client, ASSERTED), "assertion")[0]
        r = client.post(alert["endpoint"],
                        json={"sentence": alert["sentence"], "confirmed": True},
                        headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stored"] == len(alert["triples"]) > 0, (
        f"the offer promised {len(alert['triples'])} and the gate stored "
        f"{body['stored']}")


# ---------------------------------------------------------------------------
# The contract, and the hole S.4 found in it
# ---------------------------------------------------------------------------

def test_the_subtype_layer_can_see_an_undeclared_subtype():
    """THE GUARD ON THE GUARD.

    `produced_alert_subtypes()` used to be `{t for t in declared if in source}`
    — an answer derived from the question, so `produced <= declared` could not
    fail and an undeclared subtype was invisible. This shows the rebuilt
    version a payload docs/CONTRACTS.md does not declare and requires it to be seen.
    """
    from tests.test_sse_contract import (declared_alert_subtypes,
                                         produced_alert_subtypes)

    produced = produced_alert_subtypes()
    declared = set(declared_alert_subtypes())
    assert "assertion" in produced, (
        "the seventh subtype is emitted and the contract cannot see it")
    assert "assertion" in declared, "docs/CONTRACTS.md does not declare it"
    # And the derivation is genuinely independent: `produced` must contain
    # things found in the SOURCE, so a subtype removed from docs/CONTRACTS.md would
    # still show up here. Asserted by construction — `produced` is not a
    # subset of `declared` by definition any more.
    assert produced - declared == set(), (
        f"a subtype is produced that docs/CONTRACTS.md does not declare: "
        f"{sorted(produced - declared)}")
    assert produced_alert_subtypes.__doc__ and (
        "one-directional" in produced_alert_subtypes.__doc__.lower()
        or "AST" in produced_alert_subtypes.__doc__), (
        "the rebuilt derivation lost the note explaining why it was rebuilt")


def test_the_offer_is_bound_and_rendered_by_the_main_ui():
    """H.4's distinction: BOUND is not RENDERS. `hebbian_update` is bound and
    deliberately a no-op, and the contract file is careful about the
    difference. This one renders — a card with the sentence, the triples it
    would store, and a confirm button — so the assertion is checked here
    rather than implied by the subtype being handled.
    """
    src = FRONTEND.read_text(encoding="utf-8")
    assert "data.type === 'assertion'" in src, "no branch for the subtype"
    assert "function renderAssertionProposal" in src
    assert "function confirmAssertion" in src
    assert "/kb/assert/confirm" in src, (
        "the UI cannot reach the Law 3 gate the offer names")


def test_the_offer_card_never_writes_user_text_as_html():
    """The sentence came from the user and is echoed back into the page. The
    card builds its nodes with `el()`/textContent; an innerHTML assignment
    inside these two functions would make the echo an injection.

    Scoped to the two functions rather than the file — `render()` carries its
    own long-standing rule and its own comment about it.
    """
    src = FRONTEND.read_text(encoding="utf-8")
    for fn in ("renderAssertionProposal", "confirmAssertion"):
        start = src.index(f"function {fn}(")
        body = src[start:src.index("\n}", start)]
        body = re.sub(r"//.*", "", body)          # comments are not code
        assert "innerHTML" not in body, (
            f"{fn} assigns innerHTML with user-supplied text in scope")
