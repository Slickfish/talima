"""MCP-2 — the tool layer, defined ONCE against the SDK's server object.

`build_server(runtime)` returns an `MCPServer` carrying seven tools and one
resource. The transport is chosen by whoever runs it: `python -m backend.mcp`
wires stdio and nothing here excludes the SDK's streamable-HTTP transport
for later. No HTTP app of NEXUS's own is constructed and no token setting is
read (tests/test_mcp2_build.py holds both by AST).

The turn under MCP is the loop's two halves (`backend/loop/agentic_loop.py`):
`nexus_retrieve` runs `prepare_turn` and keeps the handle; the client's model
answers; `nexus_record` runs `complete_turn` on the answer the client sent,
with `record_scope = mcp_block` declared on the record, no extraction, and
the client's model name as the client reported it.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from backend.data_root import paths_under
from backend.kb import as_of as _as_of
from backend.kb.assertion import HUMAN_SOURCE, assertion_triples, looks_like_assertion
from backend.kb.granularity import Granularity
from backend.kb.tiered_store import BeliefRejected, TripleStore
from backend.learning import learning_declaration, learning_mode
from backend.loop.agentic_loop import TurnHandle, complete_turn, prepare_turn
from backend.loop.state import NexusLoopState
from backend.mcp.config import MCP_SESSION_ID, apply_learning_default, data_root, owner as configured_owner
from backend.mcp.config import point_logs_at
from backend.mcp.coverage import (RECORD_FAILED, RECORDED, RECORDED_WITHOUT_RETRIEVE, RETRIEVED_WITHOUT_RECORD,
                                  CoverageStore, counters as coverage_counters)
from backend.mcp.handles import HandleTable, handle_timeout_seconds
from backend.mcp.instructions import INSTRUCTIONS, INSTRUCTIONS_VERSION, instructions_hash
from backend.mcp.scope import declaration as scope_declaration, verify_scope
# W4 — the recall contract's version and per-person declaration are defined
# once, beside the rows (backend/memory/lineage.py), for `nexus_recall` and the
# HTTP route alike.
from backend.memory.lineage import MCP_BLOCK, RECALL_CONTRACT_VERSION, RECALL_PER_PERSON
from backend.providers.base import Reply

logger = logging.getLogger("nexus.mcp")


class RecordNotWritten(RuntimeError):
    """W6 — `nexus_record` could not write the record. Raised by name so the
    tool answers with a failure, never with a success that carries no id."""

SERVER_NAME = "nexus"
# The `mcp` contract's version. 1 = MCP-2; 2 = W0b: `nexus_status` carries
# `replay_capture` (the switches in force and the kept request bytes).
MCP_CONTRACT_VERSION = 5      # 3 = W6; 4 = W9 (nexus_commit, identity); 5 = W10b (nexus_record_action)
HOUSEKEEPING_INTERVAL_SECONDS = 300.0       # KAIROS's poll: the seal's own cadence
DAEMON_FREE = ("no HTTP server runs in this process, no bearer token exists, and NEXUS "
               "calls no model: the client's model answers between nexus_retrieve and "
               "nexus_record")


# ---------------------------------------------------------------------------
# The runtime: one process, one person, one store
# ---------------------------------------------------------------------------

@dataclass
class NexusRuntime:
    """What the tools close over. `services` is the same object the HTTP app
    would hold, built by `create_services` with no app around it."""
    services: object
    coverage: CoverageStore
    owner: str | None = None
    handles: HandleTable = field(default_factory=HandleTable)
    learning_applied: dict = field(default_factory=dict)
    # W6 — failed records whose coverage row could not be written either
    # (the counter lives in the store that refused the record). Counted in
    # this process only, and said so on nexus_status; reset on restart.
    record_failed_uncounted: int = 0

    @classmethod
    def open(cls, *, root: Path | None = None, owner: str | None = None,
             apply_defaults: bool = True) -> "NexusRuntime":
        """Build the services under `root` (default: the configured data
        root) with the HTTP app never constructed, open the coverage table
        beside the record, and apply MCP mode's learning default before
        anything reads the switch."""
        from backend.server import create_services
        root = Path(root) if root is not None else data_root()
        root.mkdir(parents=True, exist_ok=True)
        # W2 — the three logs under this root too, unless the environment
        # names their files (it wins, as for the console); before W2 they
        # landed in whatever directory the host started this process in.
        point_logs_at(root)
        # W1 — the one layout, shared with the console's NEXUS_DATA_ROOT.
        services = create_services(**paths_under(root), lock_mode="mcp")   # W2: the single-writer lock
        coverage = CoverageStore(services.lineage.conn)
        applied = apply_learning_default(first_run_ts=coverage.first_run()) if apply_defaults else {}
        return cls(services=services, coverage=coverage,
                   owner=(owner if owner is not None else configured_owner()),
                   learning_applied=applied)

    # -- housekeeping: expiry and the seal, alone ---------------------------
    def expire_handles(self, now: float | None = None) -> list[str]:
        expired = self.handles.expire(now)
        for h in expired:
            self.coverage.record_event(RETRIEVED_WITHOUT_RECORD, retrieval_id=h.retrieval_id,
                                       reason=f"no record within {handle_timeout_seconds():g}s")
        return [h.retrieval_id for h in expired]

    async def housekeep_once(self) -> dict:
        """One tick: expire unrecorded handles, then seal what is unsealed.
        The seal runs ALONE — `KairosProcess.maybe_seal_governance` needs the
        chain, the lineage and the recalibration stores and nothing else — so
        the idle cycle's learning passes never run in the MCP process."""
        expired = self.expire_handles()
        seal = None
        kairos = getattr(self.services, "kairos", None)
        if kairos is not None:
            try:
                seal = await kairos.maybe_seal_governance()
            except Exception as e:                   # never kill the process
                logger.warning("seal failed: %s: %s", type(e).__name__, e)
                seal = {"error": f"{type(e).__name__}: {e}"}
        return {"expired": expired, "seal": seal}

    @contextlib.asynccontextmanager
    async def lifespan(self, _server):
        task = asyncio.create_task(self._housekeeping_loop())
        try:
            yield {}
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _housekeeping_loop(self) -> None:
        while True:
            await asyncio.sleep(HOUSEKEEPING_INTERVAL_SECONDS)
            await self.housekeep_once()

    # -- the two halves --------------------------------------------------------
    async def retrieve(self, question: str, *, scope: str = "default",
                       work_unit: str | None = None) -> TurnHandle:
        """The first half on the process's state. The frames the console would
        stream are dropped (no stream to carry them); the handle is kept."""
        self.expire_handles()
        s = self.services
        handle: TurnHandle | None = None
        async for frame, payload in prepare_turn(
                s.state, question,
                retriever=s.retriever, hippocampus=s.hippocampus, procedural=s.procedural,
                vector_store=s.vector, predictive=s.predictive,
                scope=scope, work_units=s.work_units, session_id=MCP_SESSION_ID,
                work_unit=work_unit, accountable=self.owner):
            if frame is None:
                handle = payload
        assert handle is not None
        self.handles.put(handle)
        return handle

    async def record(self, retrieval_id: str, answer: str, *, model: str | None = None) -> dict:
        """The second half on the handle, with the client's answer. Refused by
        name when NEXUS holds no such retrieval; counted either way."""
        self.expire_handles()
        handle = self.handles.pop(retrieval_id)
        if handle is None:
            self.coverage.record_event(RECORDED_WITHOUT_RETRIEVE, retrieval_id=retrieval_id,
                                       reason="unknown or expired retrieval_id")
            raise LookupError("refused: unknown_retrieval_id — no open retrieval with this id "
                              "(expired, already recorded, or never retrieved); counted as "
                              "recorded_without_retrieve")
        s = self.services
        reply = Reply(answer=answer, reasoning=None, raw=answer, unclosed=False)
        outcome: dict = {}
        failed: dict | None = None        # W6 — the second half's declared failure frame, if it ended in one
        async for _frame, _state in complete_turn(
                handle, reply,
                retriever=s.retriever, hippocampus=s.hippocampus, hebbian=s.hebbian,
                predictive=s.predictive, curiosity=s.curiosity, lineage=s.lineage,
                work_units=s.work_units,
                record_scope=MCP_BLOCK, extract=False,
                model_id=f"client-reported:{model.strip()}" if model and model.strip() else "client-reported:unknown",
                outcome=outcome):
            if _frame.startswith("event: record_failed\n"):
                failed = json.loads(_frame.split("\ndata: ", 1)[1])
        if failed is not None:
            # W6 — the client already holds its answer, so nothing can be
            # withheld here. What NEXUS owes is a failure by name (never a
            # success with a null id) and a counted gap. The retrieval stays
            # open: the client may record again, and if it does not, it
            # expires as a retrieval without a record like any other.
            self.handles.put(handle)
            try:
                self.coverage.record_event(RECORD_FAILED, retrieval_id=retrieval_id,
                                           reason=failed["reason"])
                counted = "counted as record_failed"
            except Exception as e:                   # the same store may be the one that failed
                logger.warning("coverage write failed after a failed record: %s: %s", type(e).__name__, e)
                self.record_failed_uncounted += 1
                counted = ("the coverage counter could not be written either (the same store refused it), "
                           "so it is counted in this process only, as nexus_status.record_failed_uncounted")
            raise RecordNotWritten(
                "failed: record_not_written — NEXUS could not write the record of this turn "
                f"({failed['reason']}); nothing was recorded; {counted}; the "
                "retrieval stays open, so nexus_record may be called again with the same retrieval_id")
        s.state = outcome["state"]
        await self._persist_after_turn()
        self.coverage.record_event(RECORDED, retrieval_id=retrieval_id, lineage_id=outcome.get("lineage_id"))
        return outcome

    async def _persist_after_turn(self) -> None:
        """What `/chat` does after a turn: checkpoint the state and persist
        the graph under the graph lock (a learning write, so a counted no-op
        under frozen)."""
        s = self.services
        try:
            from backend.server import DEFAULT_THREAD_ID
            s.checkpointer.put(DEFAULT_THREAD_ID, s.state)
        except Exception as e:                       # never break the record
            logger.warning("checkpoint failed: %s: %s", type(e).__name__, e)
        store = getattr(s, "graph_store", None)
        lock = getattr(s, "graph_lock", None)
        if store is not None:
            try:
                if lock is not None:
                    async with lock:
                        store.save(s.hippocampus)
                else:
                    store.save(s.hippocampus)
            except Exception as e:
                logger.warning("graph persist failed: %s: %s", type(e).__name__, e)

    # -- status --------------------------------------------------------------
    def _identity(self) -> dict:
        """W9 — the identity document, or the key's refusal by name."""
        from backend.memory import instance_key as _ik
        path = getattr(self.services, "instance_key_path", None)
        if path is None:
            return {"state": "no_key_path"}
        try:
            key = _ik.load_or_create(path, chain=getattr(self.services, "archive_chain", None))
        except _ik.KeyMismatch as e:
            return {"state": "key_mismatch", "detail": str(e)}
        except OSError as e:                        # never the path: the status names the state and the error's type
            return {"state": "key_unreadable",
                    "detail": f"the instance's key file cannot be read or made ({type(e).__name__})"}
        return _ik.identity_document(key, getattr(self.services, "archive_chain", None))

    def status(self) -> dict:
        return {
            "learning": learning_declaration(),
            "learning_applied": dict(self.learning_applied),
            "owner": self.owner,
            "coverage": self.coverage.counters(),
            # W6 — failed records the coverage table could not count (in-process, reset on restart).
            "record_failed_uncounted": self.record_failed_uncounted,
            "handles_open": len(self.handles),
            "handle_timeout_seconds": handle_timeout_seconds(),
            "first_run": self.coverage.first_run(),
            "record_scope": MCP_BLOCK,
            "instructions_version": INSTRUCTIONS_VERSION,
            "instructions_sha256": instructions_hash(),
            "contract_version": MCP_CONTRACT_VERSION,
            "daemon_free": DAEMON_FREE,
            # W9 (contract v4) — the instance's keys, as `GET /identity` serves
            # them: who signed this instance's blocks, never when.
            "identity": self._identity(),
            # W0b — the replay capture's counts and the kept request bytes,
            # derived from the store on every read (contract v2). Under MCP
            # NEXUS sends no answering request, so the seed switch does not
            # apply here, and the status says so rather than "on".
            "replay_capture": {**self.services.lineage.capture_stats(),
                               "inference_seed": "not applicable: under MCP the client's model answers"},
        }


# ---------------------------------------------------------------------------
# Structured outputs — the contract's output schemas
# ---------------------------------------------------------------------------

class RetrieveOut(BaseModel):
    block: str
    retrieval_id: str
    injected: list[int]
    learning_mode: str
    record_scope: str
    expires_in_seconds: float


class RecordOut(BaseModel):
    lineage_id: str | None
    record_hash: str | None
    cited: list[int]
    injected: list[int]
    record_scope: str
    learning_mode: str
    coverage: dict


class StatusOut(BaseModel):
    learning: dict
    learning_applied: dict
    owner: str | None
    coverage: dict
    record_failed_uncounted: int
    handles_open: int
    handle_timeout_seconds: float
    first_run: float
    record_scope: str
    instructions_version: str
    instructions_sha256: str
    contract_version: int
    daemon_free: str
    identity: dict
    replay_capture: dict


class AssertOffer(BaseModel):
    sentence: str
    detected_as_assertion: bool
    detection_reason: str
    shaped: bool
    triples: list[dict]
    next: str


class ActionOut(BaseModel):
    action_id: str
    evidence_class: str
    kind: str
    target: str
    state: str
    record_hash: str
    sealed: bool
    note: str


class CommitOut(BaseModel):
    commitment_id: str
    commitment: str
    salt: str
    ts: float
    sealed: bool
    note: str


class AssertConfirmOut(BaseModel):
    sentence: str
    stored: int
    refused: int
    beliefs: list[dict]
    shaped: bool
    admitted_to_unit: int


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------

def build_server(runtime: NexusRuntime, *, with_lifespan: bool = True):
    """Define every tool and resource once, against the SDK's server object.
    The transport is the caller's: `server.run("stdio")` in
    `backend/mcp/__main__.py`; nothing here names one."""
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, version=INSTRUCTIONS_VERSION,
                       lifespan=(runtime.lifespan if with_lifespan else None))

    @server.tool()
    async def nexus_retrieve(question: str, scope: str = "default",
                             work_unit: str | None = None) -> RetrieveOut:
        """Call BEFORE answering. Returns the context block NEXUS would inject
        for this question — put it, verbatim, into the context you answer
        from — and a retrieval_id to pass to nexus_record after you answer.
        """
        handle = await runtime.retrieve(question, scope=scope, work_unit=work_unit)
        return RetrieveOut(
            block=handle.dynamic_block, retrieval_id=handle.retrieval_id,
            injected=[rt.triple.id for rt in handle.hits if rt.triple.id is not None],
            learning_mode=learning_mode(), record_scope=MCP_BLOCK,
            expires_in_seconds=handle_timeout_seconds())

    @server.tool()
    async def nexus_record(retrieval_id: str, answer: str, model: str | None = None) -> RecordOut:
        """Call AFTER answering, with the retrieval_id from nexus_retrieve and
        the exact answer text you gave. Seals the record of what you were
        shown and what you said. `model` is your model's name, if you know it.
        """
        try:
            outcome = await runtime.record(retrieval_id, answer, model=model)
        except (LookupError, RecordNotWritten) as e:
            raise ToolError(str(e)) from None
        return RecordOut(
            lineage_id=outcome.get("lineage_id"), record_hash=outcome.get("record_hash"),
            cited=list(outcome.get("cited") or []), injected=list(outcome.get("injected") or []),
            record_scope=MCP_BLOCK, learning_mode=learning_mode(),
            coverage=runtime.coverage.counters())

    @server.tool()
    def nexus_recall(belief_id: int, limit: int = 100, offset: int = 0) -> dict:
        """Every sealed answer that rested on a claim, oldest first (read-only).
        Rows name each record's accountable identity and how it was established.
        """
        s = runtime.services
        triple = s.store.get_by_id(belief_id)
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        rows, total = s.lineage.records_injecting(belief_id, limit=limit, offset=offset)
        return {
            "belief_id": belief_id,
            "resolved": triple is not None,
            "claim": (None if triple is None else {
                "subject": triple.subject, "relation": triple.relation,
                "object": triple.object, "source": triple.source,
                "archived": bool(getattr(triple, "archived", False))}),
            "records": rows, "total": total, "limit": limit, "offset": offset,
            "sorted_by": "ts ascending", "read_from": "injected",
            "per_person": RECALL_PER_PERSON,
            "note": ("built from the injected set, not the cited flag: the flag "
                     "under-reports by about a fifth and a recall that misses "
                     "affected answers is wrong in the unsafe direction. "
                     "Over-inclusion is the safe direction."),
            "contract_version": RECALL_CONTRACT_VERSION,
        }

    @server.tool()
    def nexus_asof(date: str, subject: str | None = None, relation: str | None = None,
                   source: str | None = None, limit: int = 100, offset: int = 0) -> dict:
        """What the store held at an instant (read-only). `date` must be an
        instant with a UTC offset, e.g. 2026-03-12T23:59:59+08:00; a bare
        date is refused by name.
        """
        try:
            instant = _as_of.resolve_instant(date)
        except _as_of.AsOfRefused as e:
            p = e.payload()
            raise ToolError(f"refused: {p.get('refused')} — {p.get('reason')} (example: {p.get('example')})") from None
        s = runtime.services
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        conn = s.store.conn
        earliest = _as_of.earliest_admission(conn)
        ledger = _as_of.archival_ledger_from(conn)
        rows, total = _as_of.held_at(conn, instant["ts"], subject=subject, relation=relation,
                                     source=source, limit=limit, offset=offset)
        if earliest is None:
            reason = "the store holds no claim"
        elif instant["ts"] < earliest:
            reason = f"before the earliest admission at {_as_of.iso_utc(earliest)}: empty by construction"
        else:
            reason = None
        return {
            "as_of": instant, "claims": rows, "total": total, "limit": limit, "offset": offset,
            "sorted_by": "admitted_at ascending, then claim key (subject, relation, object, source)",
            "filters": {"subject": subject, "relation": relation, "source": source},
            "earliest_admitted_at": earliest, "reason": reason,
            "archival_ledger_from": ledger["iso"], "archival_ledger_reason": ledger["reason"],
            "predicate": _as_of.PREDICATE, "read_from": _as_of.READ_FROM,
            "note": _as_of.FIXED_SENTENCE + " " + _as_of.ledger_sentence(ledger["iso"]),
            "contract_version": _as_of.CONTRACT_VERSION,
        }

    @server.tool()
    def nexus_assert(sentence: str) -> AssertOffer:
        """Offer a statement the user made as a belief: shows exactly what
        WOULD be stored, and stores nothing. Then ask the user, and only on
        their yes call nexus_assert_confirm with confirmed=true.
        """
        text = (sentence or "").strip()
        candidate, why = looks_like_assertion(text) if text else (False, "empty sentence")
        shaped = assertion_triples(text) if text else []
        declared = []
        for t in shaped:
            s_, r_, o_ = TripleStore.stored_form(t.subject, t.relation, t.object)
            declared.append({"subject": s_, "relation": r_, "object": o_,
                             "as_parsed": {"subject": t.subject, "relation": t.relation, "object": t.object},
                             "weight": t.weight, "tier": t.tier})
        return AssertOffer(sentence=text, detected_as_assertion=bool(candidate), detection_reason=why,
                           shaped=bool(shaped), triples=declared,
                           next="nexus_assert_confirm(sentence, confirmed=true) after the user confirms")

    @server.tool()
    def nexus_assert_confirm(sentence: str, confirmed: bool = False, scope: str = "default",
                             work_unit: str | None = None) -> AssertConfirmOut:
        """Store the statement as the user's own belief. Law 3: nothing is
        stored unless confirmed is true; a call without it is refused and
        recorded as a declined governed act.
        """
        s = runtime.services
        text = (sentence or "").strip()
        if not text:
            raise ToolError("refused: empty_sentence — send the sentence the user asserted")
        if not confirmed:
            rec = None
            if getattr(s, "recalibration", None) is not None:
                rec = s.recalibration.record_refusal(
                    route="mcp:nexus_assert_confirm", rule="law3_unconfirmed",
                    detail="confirmed=true required", scope=scope,
                    request={"sentence_sha256": _sha(text), "confirmed": False, "work_unit": work_unit})
            tail = f" (recorded, event {rec['event_id']})" if rec else " (refusal ledger off: declared, not recorded)"
            raise ToolError("refused: Law 3 — confirmed=true required" + tail)
        triples = assertion_triples(text)
        if not triples:
            return AssertConfirmOut(sentence=text, stored=0, refused=0, beliefs=[], shaped=False,
                                    admitted_to_unit=0)
        stored = []
        with s.store.admissions() as admitted:
            for t in triples:
                # One refused belief must not end the loop (Packet K's class,
                # tests/test_ingest_refusal_class.py): caught per belief,
                # reported in `beliefs` and counted in `refused`, exactly as
                # the HTTP route does.
                try:
                    bid = s.store.add(subject=t.subject, relation=t.relation, object=t.object,
                                      weight=t.weight, tier=t.tier, granularity=Granularity.TOPIC,
                                      source=HUMAN_SOURCE)
                except BeliefRejected as e:
                    stored.append({"refused": str(e), "subject": t.subject})
                    continue
                row = s.store.get_by_id(bid)
                stored.append({"belief_id": bid, "subject": row.subject, "relation": row.relation,
                               "object": row.object,
                               "as_parsed": {"subject": t.subject, "relation": t.relation, "object": t.object}})
        junction = 0
        if work_unit and getattr(s, "work_units", None) is not None and admitted:
            unit, _attr = s.work_units.resolve_for_turn(
                session_id=MCP_SESSION_ID, requested_id=work_unit, requested_type=None,
                accountable=runtime.owner, actor=None, boundary=None)
            junction = s.work_units.admit(admitted, unit["work_unit_id"], turn=s.state.turn_count, lineage_id=None)
        return AssertConfirmOut(sentence=text, stored=sum(1 for b in stored if "belief_id" in b),
                                refused=sum(1 for b in stored if "refused" in b),
                                beliefs=stored, shaped=True, admitted_to_unit=junction)

    @server.tool()
    def nexus_commit(content: str, confirmed: bool = False) -> CommitOut:
        """Commit now, reveal later: a salted commitment to a note you supply,
        held in NEXUS's store with its salt and sealed by the next seal; the
        note enters no prompt, retrieval or record. Law 3: nothing is held
        unless confirmed is true; a call without it is refused and recorded,
        and the refusal names what would be held. Keep the salt it returns:
        it is never shown again, and `nexus-verify --reveal` needs it.
        """
        s = runtime.services
        text = content if isinstance(content, str) else ""
        if not text:
            raise ToolError("refused: empty_content — send the note to commit to")
        if not confirmed:
            rec = None
            if getattr(s, "recalibration", None) is not None:
                rec = s.recalibration.record_refusal(
                    route="mcp:nexus_commit", rule="law3_unconfirmed", detail="confirmed=true required",
                    scope="default", request={"content_chars": len(text), "confirmed": False})
            tail = f" (recorded, event {rec['event_id']})" if rec else " (refusal ledger off: declared, not recorded)"
            raise ToolError(f"refused: Law 3 — confirmed=true required; the offer: a commitment to {len(text)} "
                            "characters under a fresh 32-byte salt, sealed by the next seal" + tail)
        made = s.lineage.hold_commitment(text)
        return CommitOut(**made, sealed=False,
                         note="keep the salt: it is never shown again; once a seal covers the commitment, "
                              "nexus-verify --reveal checks the content and salt against it")

    @server.tool()
    def nexus_record_action(lineage_id: str, kind: str, target: str, request_hash: str, status: str) -> ActionOut:
        """Report an action you took for the turn `lineage_id` names (the id
        nexus_record returned): its kind, its target, the SHA-256 of its
        request and its status. NEXUS stores it as client_reported: it did
        not see it happen, and its record says so. Sealed by the next seal;
        its receipt is checked offline with `nexus-verify --action`.
        """
        from backend.memory import actions as _actions
        s = runtime.services
        if s.lineage.get(lineage_id) is None:
            raise ToolError("refused: unknown_lineage_id — report an action against the lineage_id nexus_record "
                            "returned")
        try:
            row = _actions.record_reported(s.lineage, lineage_id=lineage_id, kind=kind, target=target,
                                           request_hash=request_hash, status=status,
                                           accountable_identity=runtime.owner, identity_mechanism="asserted")
        except _actions.ReportRefused as e:
            raise ToolError(f"refused: {e}")
        except _actions.ActionNotRecorded as e:
            raise ToolError(f"refused: {e}")
        return ActionOut(action_id=row["action_id"], evidence_class=row["evidence_class"], kind=row["kind"],
                         target=row["target"], state=row["state"], record_hash=row["record_hash"], sealed=False,
                         note="the client reported this action and NEXUS did not see it happen; once a seal "
                              "covers it, GET /actions/{action_id} is its receipt")

    @server.tool()
    def nexus_status() -> StatusOut:
        """NEXUS's state for this process: learning mode, owner, the coverage
        counters, open retrievals, and the daemon-free statement.
        """
        return StatusOut(**runtime.status())

    @server.resource("nexus://lineage/{lineage_id}")
    def lineage_record(lineage_id: str) -> str:
        """One sealed record, read-only, with its verification and its scope
        declaration."""
        s = runtime.services
        rec = s.lineage.get(lineage_id)
        if rec is None:
            return json.dumps({"error": "unknown lineage_id", "lineage_id": lineage_id})
        payload = rec.to_dict()
        payload["verify"] = s.lineage.verify_record(lineage_id)
        payload["scope_declaration"] = scope_declaration(rec.record_scope)
        payload["scope_declaration"]["violations"] = verify_scope(rec)
        return json.dumps(payload, sort_keys=True)

    return server


def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def contract(server) -> dict:
    """The `mcp` contract (MCP_CONTRACT_VERSION; 2 since W0b, 3 since W6): every tool with its schemas, the
    resource templates, the instructions' version and hash, and the
    record-scope declaration. Derived from the server object, never typed."""
    tools = []
    for t in await server.list_tools():
        d = t.model_dump()
        tools.append({"name": t.name, "description": t.description,
                      "input_schema": d.get("input_schema"), "output_schema": d.get("output_schema")})
    templates = [{"uri_template": r.model_dump().get("uri_template"), "name": r.name,
                  "description": r.description} for r in await server.list_resource_templates()]
    return {
        "contract_version": MCP_CONTRACT_VERSION,
        "server": SERVER_NAME,
        "instructions_version": INSTRUCTIONS_VERSION,
        "instructions_sha256": instructions_hash(),
        "tools": tools,
        "resources": templates,
        "record_scope": scope_declaration(MCP_BLOCK),
        "transport": "chosen by the runner; stdio in backend/mcp/__main__.py",
    }
