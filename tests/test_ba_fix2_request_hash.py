"""BA fix 2 — the whole request is hashed into lineage.

THE FINDING (audit A1a/A2): lineage hashed the stable prefix and, since AW,
the dynamic block; the history — prior answers carried verbatim — was
recorded nowhere. Part of what influenced the answer was unrecorded.

THE FIX: `request_hash`, sha256 over the canonical serialisation of the
request as sent (role, content, order), computed at assembly by the one
function `request_hash_of` and sealed INTO the record hash when present,
under the non-default-only rule chain.py and the scope column already use,
so every earlier record recomputes unchanged.

THE ASYMMETRY, stated: the dynamic block RE-DERIVES from the record and
the store (AW's sentinel); the history only HASHES, because it is not
retained beyond an in-memory checkpoint. "The whole request is hashed" is
weaker than "the whole request is verifiable". This closes nothing on the
history path; it makes the path auditable.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

from backend.kb.retriever import TripleRetriever
from backend.loop.agentic_loop import STABLE_SYSTEM_PREFIX, run_turn
from backend.loop.prompt_render import derive_block, request_hash_of
from backend.loop.state import NexusLoopState
from backend.memory.lineage import LineageStore, verify_sealed_lineage
from tests._tracked import tracked_backend
from tests.test_aw_prompt_render import _rig

ROOT = Path(__file__).resolve().parent.parent
DONE_KEYS_BEFORE_FIX_2 = {"turn", "tokens", "lineage_id", "reinforcement",
                          "extraction_gate", "citation_forecast"}
# v3.1 Phase 1 — `done` gains the turn's work unit, deliberately: which unit
# the answer was attributed to, how, and the junction rows written.
# (`attribution` appears only when there is something to declare and is
# therefore absent on this fixture's plain turn.) The pin above is fix 2's
# claim — no field for the request hash — and it still holds.
DONE_KEYS_PHASE_1 = {"work_unit_id", "attribution_status", "admitted_to_unit",
                     # 1b — whether this request created the unit and whether
                     # it closed it; the declarations ride only when set.
                     "unit_first_seen", "unit_closed"}
# FZ-2 — `done` becomes a versioned contract. `learning` is its first
# versioned bump; the version rides the payload as `contract_version`, the
# way the recall route carries its own (AR, S1.8's convention). A change to
# the key set is a bump of this constant, pinned three ways: here, the
# docs/CONTRACTS.md payload note and the INTEGRATION_REFERENCE row.
DONE_KEYS_FZ2 = {"learning", "contract_version"}
DONE_KEYS_V1 = DONE_KEYS_BEFORE_FIX_2 | DONE_KEYS_PHASE_1 | DONE_KEYS_FZ2
# Pre-CE / B-43 — version 2. The top-level key set is unchanged; the bump is
# for a key inside `reinforcement`: `skipped`, the attempts the freeze
# refused, beside `reinforced`, which now counts writes MADE (0 under
# frozen). A nested key is a contract change like any other.
DONE_KEYS_V2 = DONE_KEYS_V1
# C-38 — version 3: the reply separated at the provider boundary. `answer_chars`
# and `reasoning_chars` (null when the reply carried none), the declared
# `answer_empty` state (its `answer_empty_reason` rides only when set), and the
# extraction window declared (`extraction_input_chars`, `extraction_input_truncated`).
DONE_CONTRACT_VERSION = 4       # W6 part 3: two declarations only when set; the always-present keys are V3's still
DONE_KEYS_C38 = {"answer_chars", "reasoning_chars", "answer_empty",
                 "extraction_input_chars", "extraction_input_truncated"}
DONE_KEYS_V3 = DONE_KEYS_V2 | DONE_KEYS_C38
REINFORCEMENT_KEYS_V1 = {"mode", "citation_test", "injected", "cited", "reinforced", "suppressed"}
REINFORCEMENT_KEYS_V2 = REINFORCEMENT_KEYS_V1 | {"skipped"}


class _Capture:
    def __init__(self, answer="Interval training improves vo2 max."):
        self.answer, self.calls, self.requests = answer, 0, []

    async def stream_chat(self, model, messages, num_ctx):
        self.calls += 1
        if self.calls % 2 == 1:
            self.requests.append([dict(m) for m in messages])
            yield self.answer
        else:
            yield "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _turns(store, hippo, lineage, queries):
    prov = _Capture()
    state = NexusLoopState()
    dones = []

    async def turn(q):
        nonlocal state
        async for f, st in run_turn(state, q, retriever=TripleRetriever(store),
                                    hippocampus=hippo, provider=prov, lineage=lineage):
            state = st
            if f.startswith("event: done\n"):
                dones.append(json.loads(f.split("data: ", 1)[1].strip()))
    for q in queries:
        asyncio.run(turn(q))
    return prov.requests, dones


# ---------------------------------------------------------------------------
# Derivability: the request the provider received hashes to the record
# ---------------------------------------------------------------------------

def test_the_request_as_sent_hashes_to_the_record(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    requests, dones = _turns(store, hippo, lineage,
                             ["what does training improve?", "and what about recovery?"])
    for req, done in zip(requests, dones):
        rec = lineage.get(done["lineage_id"])
        assert rec.request_hash == request_hash_of(req)
        # AW still holds beside it: the block re-derives, the history only hashes.
        assert derive_block(rec, store) == req[0]["content"][len(STABLE_SYSTEM_PREFIX):]
    # The second request carried the first answer verbatim, and the hash saw it.
    assert any(m["role"] == "assistant" for m in requests[1])
    assert dones[0]["lineage_id"] != dones[1]["lineage_id"]
    assert lineage.get(dones[0]["lineage_id"]).request_hash \
        != lineage.get(dones[1]["lineage_id"]).request_hash
    store.close(); lineage.close()


def test_one_altered_character_of_history_changes_the_hash_and_fails_derivation(
        tmp_path, monkeypatch):
    """Must-fail companion."""
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    requests, dones = _turns(store, hippo, lineage,
                             ["what does training improve?", "and what about recovery?"])
    rec = lineage.get(dones[1]["lineage_id"])
    altered = [dict(m) for m in requests[1]]
    prior = next(i for i, m in enumerate(altered) if m["role"] == "assistant")
    altered[prior]["content"] = altered[prior]["content"][:-1] + "X"
    assert request_hash_of(altered) != rec.request_hash
    assert request_hash_of(requests[1]) == rec.request_hash
    store.close(); lineage.close()


def test_the_hash_is_present_and_non_null_on_every_new_record(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    _turns(store, hippo, lineage, ["what does training improve?", "tell me about jupiter"])
    for rec in lineage.recent(limit=10):
        assert rec.request_hash and len(rec.request_hash) == 64
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# Sealing: the field is in the hashed body for new records only
# ---------------------------------------------------------------------------

def test_the_field_is_sealed_into_new_records_and_old_records_recompute_unchanged(
        tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    _requests, dones = _turns(store, hippo, lineage, ["what does training improve?"])
    lid = dones[0]["lineage_id"]
    assert lineage.verify_record(lid)["record_intact"] is True
    # Tamper with the stored hash: the seal must notice (it is IN the body).
    lineage.conn.execute("UPDATE lineage_records SET request_hash = ? WHERE lineage_id = ?",
                         ("0" * 64, lid))
    lineage.conn.commit()
    assert lineage.verify_record(lid)["record_intact"] is False
    store.close(); lineage.close()


def test_a_pre_fix_sealed_record_still_verifies_and_reads_null(tmp_path, monkeypatch):
    from tests.test_restart_with_state import _used_store
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "calibration.jsonl"))
    used = _used_store(tmp_path)
    lineage = LineageStore(used["kb"])
    old = lineage.get(used["lineage_id"])
    assert old.request_hash is None
    assert lineage.verify_record(used["lineage_id"])["record_intact"] is True
    assert old.sealed_block is not None
    lineage.close()


# ---------------------------------------------------------------------------
# `done` gains nothing; the guard is derived from the call sites
# ---------------------------------------------------------------------------

def test_done_gains_no_field(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    _requests, dones = _turns(store, hippo, lineage, ["what does training improve?"])
    assert set(dones[0]) == DONE_KEYS_V3
    assert set(dones[0]["reinforcement"]) == REINFORCEMENT_KEYS_V2
    assert dones[0]["contract_version"] == DONE_CONTRACT_VERSION == 4
    assert "request_hash" not in json.dumps(dones[0])
    store.close(); lineage.close()


def test_every_record_turn_call_site_passes_the_request_hash():
    sites = []
    for path in tracked_backend():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "record_turn":
                sites.append((path.name, node.lineno,
                              any(kw.arg == "request_hash" for kw in node.keywords)))
    assert sites, "no record_turn call site found"
    assert all(ok for _f, _ln, ok in sites), f"record_turn without request_hash at {sites}"
