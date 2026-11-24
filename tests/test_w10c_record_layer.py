"""W10c (run/specs/W10c.md S1, S3 to S6) — what W10c added to the record layer, on whichever backend the suite runs.

The interceptor's own store is SQLite files under its own root, and its tests (`tests/test_w10c_interceptor*.py`)
are SQLite's by nature. The lineage store is one interface over two backends, though (W3b), and what W10c added to
it is exercised here through that interface alone, so the Postgres run holds it too:

    L1  `record_turn(response_bytes=...)` hashes the bytes as they are; the text form is unchanged, and the two
        agree on the same bytes.
    L2  the scope `intercepted` is stored, sealed into the record hash, read back, and a scope no vocabulary has
        is refused before anything is written.
    L3  `in_transaction` commits with the record or not at all: what it raises rolls the record back with it.
    L4  `exchange_requested` writes the request's bytes, its row and the linked outcomes together, and none of
        them when one cannot be written.
    L5  an outcome is written once, by the statement itself.
    L6  the standalone seal (`backend/memory/seal.py`) seals records and actions into one governance block that
        `verify_sealed_lineage` and `actions.verify_sealed` both hold, and a second pass with nothing pending
        appends nothing.
    L7  a record written before W10c's arguments existed recomputes: the hashed body of a record that uses none of
        them holds none of their keys (law 4; the LS fixture literal is `tests/test_ls_learning_state.py`'s).
"""
from __future__ import annotations

import hashlib

import pytest

from backend.memory import actions as act
from backend.memory import seal
from backend.memory.chain import NexusArchiveChain
from backend.memory.lineage import (FULL_TURN, INTERCEPTED, MCP_BLOCK, RECORD_SCOPES, REQUEST_ENCODING_HTTP_BODY,
                                    REQUEST_ENCODINGS, LineageStore, verify_sealed_lineage)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _store(tmp_path) -> LineageStore:
    return LineageStore(tmp_path / "kb.db")


def _turn(store: LineageStore, **more):
    base = dict(query="q", injected=[], contradictions=[], model_id="m", response_text="the answer",
                prompt_prefix="p")
    return store.record_turn(**{**base, **more})


def _exchange_row(lineage_id: str, action_id: str, *, when: float = 100.0) -> dict:
    return {"action_id": action_id, "kind": "model_exchange", "evidence_class": "runtime", "method": "POST",
            "target": "http://127.0.0.1:9/v1/messages", "request_hash": _sha(b"{}"), "credentials_json": "[]",
            "lineage_id": lineage_id, "trigger_route": "intercept:/v1/messages", **act.identity_fields("o@example.test"),
            "identity_mechanism": None, "signature_key_id": None, "signature": None, "requested_at": when,
            "state": "requested"}


def _outcome(row: dict, **fields) -> tuple:
    done = {**row, **fields}
    return (row["action_id"], {k: done[k] for k in ("state", "status", "response_hash", "completed_at")},
            act.record_hash(done))


def test_l1_the_response_is_hashed_as_bytes_when_bytes_are_given(tmp_path):
    store = _store(tmp_path)
    raw = b"\x1f\x8b\x08\x00 not text \xff\xfe"
    assert _turn(store, response_bytes=raw).response_hash == _sha(raw)
    as_text = _turn(store, response_text="the answer")
    as_bytes = _turn(store, response_text="ignored when bytes are given", response_bytes="the answer".encode("utf-8"))
    assert as_text.response_hash == as_bytes.response_hash == _sha(b"the answer")


def test_l2_the_intercepted_scope_is_stored_sealed_and_read_back_and_an_unknown_one_is_refused(tmp_path):
    store = _store(tmp_path)
    assert RECORD_SCOPES == (FULL_TURN, MCP_BLOCK, INTERCEPTED) and REQUEST_ENCODING_HTTP_BODY in REQUEST_ENCODINGS
    rec = _turn(store, record_scope=INTERCEPTED, learning_mode="frozen", request_hash=_sha(b"{}"))
    back = store.get(rec.lineage_id)
    assert back.record_scope == INTERCEPTED and store.verify_record(rec.lineage_id)["record_intact"]
    assert LineageStore.hashed_body({**back.to_dict(), "record_scope": INTERCEPTED})["record_scope"] == INTERCEPTED
    before = store.count()
    with pytest.raises(ValueError, match="unknown record_scope"):
        _turn(store, record_scope="relayed")
    assert store.count() == before


def test_l3_what_commits_with_the_record_commits_or_neither_does(tmp_path):
    store = _store(tmp_path)
    row = _exchange_row("l-3", "a-3")
    store.action_requested(row)

    def tool_use_and_outcome():
        store.action_requested({**_exchange_row("l-3", "a-3-tool"), "kind": "tool_use", "evidence_class": "intercepted"},
                               commit=False)
        _id, outcome, record_hash = _outcome(row, state="completed", status="200", response_hash=_sha(b"r"),
                                             completed_at=101.0)
        assert store.action_outcome(_id, record_hash=record_hash, commit=False, **outcome) == 1

    def then_fails():
        tool_use_and_outcome()
        raise RuntimeError("planted: the last write of the transaction fails")

    before = store.count()
    with pytest.raises(RuntimeError, match="planted"):
        _turn(store, lineage_id="l-3", in_transaction=then_fails)
    assert store.count() == before and store.get("l-3") is None
    assert [a["action_id"] for a in store.actions_for("l-3")] == ["a-3"] and store.action("a-3")["state"] == "requested"
    rec = _turn(store, lineage_id="l-3", in_transaction=tool_use_and_outcome)
    assert rec.lineage_id == "l-3" and store.action("a-3")["state"] == "completed"
    assert [a["kind"] for a in store.actions_for("l-3")] == ["model_exchange", "tool_use"]
    # the store is usable after a rolled-back write (no transaction left open)
    assert _turn(store).lineage_id


def test_l4_a_requests_bytes_its_row_and_its_linked_outcomes_are_written_together_or_not_at_all(tmp_path, monkeypatch):
    store = _store(tmp_path)
    earlier = {**_exchange_row("l-0", "a-0-tool", when=90.0), "kind": "tool_use", "evidence_class": "intercepted"}
    store.action_requested(earlier)
    body = b'{"messages": []}'
    linked = _outcome(earlier, state="completed", status="reported", response_hash=_sha(b"18C"), completed_at=100.0)
    note = store.exchange_requested(_exchange_row("l-4", "a-4"), request_hash=_sha(body), request_body=body,
                                    encoding=REQUEST_ENCODING_HTTP_BODY, outcomes=[linked])
    assert note is None and store.request_body(_sha(body)) == body
    assert store.action("a-4")["state"] == "requested" and store.action("a-0-tool")["status"] == "reported"
    assert store.actions_in_state(evidence_class="runtime", kind="model_exchange", state="requested")[0]["action_id"] == "a-4"
    assert store.actions_in_state(evidence_class="intercepted", kind="tool_use", state="requested") == []
    # an outcome that cannot be written (its action is no longer `requested`): none of the three is written
    other = b'{"messages": [1]}'
    with pytest.raises(RuntimeError, match="was not `requested`"):
        store.exchange_requested(_exchange_row("l-5", "a-5"), request_hash=_sha(other), request_body=other,
                                 encoding=REQUEST_ENCODING_HTTP_BODY, outcomes=[linked])
    assert store.action("a-5") is None and store.request_body(_sha(other)) is None
    # bytes that do not hash to the hash given are refused, and the reason is returned; the row is still written
    note = store.exchange_requested(_exchange_row("l-6", "a-6"), request_hash=_sha(b"something else"), request_body=other,
                                    encoding=REQUEST_ENCODING_HTTP_BODY, outcomes=[])
    assert note == "not kept: the bytes did not hash to request_hash" and store.action("a-6") is not None
    monkeypatch.setenv("NEXUS_KEEP_REQUESTS", "off")
    note = store.exchange_requested(_exchange_row("l-7", "a-7"), request_hash=_sha(other), request_body=other,
                                    encoding=REQUEST_ENCODING_HTTP_BODY, outcomes=[])
    assert note == "not kept: NEXUS_KEEP_REQUESTS=off" and store.request_body(_sha(other)) is None
    with pytest.raises(ValueError, match="unknown request body encoding"):
        store.exchange_requested(_exchange_row("l-8", "a-8"), request_hash=_sha(other), request_body=other,
                                 encoding="zlib:something:v9", outcomes=[])
    assert store.action("a-8") is None


def test_l5_an_outcome_is_written_once(tmp_path):
    store = _store(tmp_path)
    row = _exchange_row("l-9", "a-9")
    store.action_requested(row)
    _id, outcome, record_hash = _outcome(row, state="incomplete", status="harness_disconnected", response_hash=None,
                                         completed_at=101.0)
    assert store.action_outcome(_id, record_hash=record_hash, **outcome) == 1
    written = store.action("a-9")
    assert written["state"] == "incomplete" and written["record_hash"] == act.record_hash(written)
    assert store.action_outcome(_id, record_hash="ab" * 32, state="completed", status="200", response_hash=None,
                                completed_at=102.0) == 0
    assert store.action("a-9") == written


def test_l6_the_standalone_seal_seals_records_and_actions_into_one_block_both_verifiers_hold(tmp_path):
    store, chain = _store(tmp_path), NexusArchiveChain(tmp_path / "archive.db")
    assert seal.seal_governance(chain, store) is None and chain.count() == 0, "nothing pending, and a block appended"
    row = _exchange_row("l-10", "a-10")
    store.action_requested(row)
    _id, outcome, record_hash = _outcome(row, state="completed", status="200", response_hash=_sha(b"r"),
                                         completed_at=101.0)
    rec = _turn(store, lineage_id="l-10", record_scope=INTERCEPTED, learning_mode="frozen", request_hash=_sha(b"{}"),
                in_transaction=lambda: store.action_outcome(_id, record_hash=record_hash, commit=False, **outcome))
    block = seal.seal_governance(chain, store)
    assert block is not None and block.kind == "governance"
    assert sorted(block.fingerprint) == ["action_seal", "kind", "lineage_seal"]
    assert block.fingerprint["lineage_seal"]["record_hashes"] == [rec.record_hash]
    assert block.fingerprint["action_seal"]["action_ids"] == ["a-10"]
    assert store.get("l-10").sealed_block == block.index and store.action("a-10")["sealed_block"] == block.index
    assert chain.verify_chain().valid
    assert verify_sealed_lineage(store, chain)["verdict"] == "intact"
    assert act.verify_sealed(store, chain) == {"checked": 1, "sealed": 1, "unsealed": 0, "failed": []}
    assert seal.seal_governance(chain, store) is None and chain.count() == 1, "a second pass sealed something again"


@pytest.mark.parametrize("forgery", ["replaced and sealed again", "erased", "deleted"])
def test_l6b_a_sealed_outcome_replaced_erased_or_deleted_is_named_from_the_block(tmp_path, forgery):
    """The verifier read from the row to its block only: a row rewritten after its seal, given the hash of its
    new self and its seal mark cleared, was sealed again and checked against the new block alone (delta check 3's
    F2). It reads from the block to the row too: every leaf a block sealed is the hash its row has now."""
    store, chain = _store(tmp_path), NexusArchiveChain(tmp_path / "archive.db")
    row = _exchange_row("l-11", "a-11")
    store.action_requested(row)
    _id, outcome, record_hash = _outcome(row, state="completed", status="200", response_hash=_sha(b"r"),
                                         completed_at=101.0)
    assert store.action_outcome(_id, record_hash=record_hash, **outcome) == 1
    assert seal.seal_governance(chain, store) is not None
    assert act.verify_sealed(store, chain)["failed"] == []
    if forgery == "replaced and sealed again":
        forged = {**store.action("a-11"), "status": "500", "response_hash": _sha(b"another")}
        store.conn.execute("UPDATE action_records SET status = ?, response_hash = ?, record_hash = ?, "
                           "sealed_block = NULL WHERE action_id = ?",
                           (forged["status"], forged["response_hash"], act.record_hash(forged), "a-11"))
        store.conn.commit()
        assert seal.seal_governance(chain, store) is not None and chain.count() == 2
    elif forgery == "erased":
        store.conn.execute("UPDATE action_records SET state = 'requested', status = NULL, response_hash = NULL, "
                           "completed_at = NULL, record_hash = NULL, sealed_block = NULL WHERE action_id = ?", ("a-11",))
        store.conn.commit()
    else:
        store.conn.execute("DELETE FROM action_records WHERE action_id = ?", ("a-11",))
        store.conn.commit()
    verdict = act.verify_sealed(store, chain)
    assert [f["action_id"] for f in verdict["failed"]] == ["a-11"], verdict
    assert "block 0" in verdict["failed"][0]["reason"], verdict


def test_l7_a_record_that_uses_none_of_w10cs_arguments_hashes_as_it_did(tmp_path):
    store = _store(tmp_path)
    rec = _turn(store)
    body = LineageStore.hashed_body({**rec.to_dict(), "record_scope": None})
    assert "record_scope" not in body and "response_bytes" not in body and "in_transaction" not in body
    assert rec.response_hash == _sha(b"the answer") and store.verify_record(rec.lineage_id)["record_intact"]
