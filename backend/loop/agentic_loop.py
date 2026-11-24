"""Agentic loop — async generator yielding SSE events.

Phase 1: streaming inference + atomic state transition.
Phase 2: Stage 2 KB triple retrieval, dynamic block appended (Law VII).
Phase 3: cortex propagation, epistemic-state injection, post-turn Hebbian.

Stage 4.5 / 5 (prediction-error-modulated Hebbian) lands in Phase 7. Here the
multiplier is fixed at 1.0 — the surface stays identical so Phase 7 only flips
the multiplier feed.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import time as _time
import uuid as _uuid
from dataclasses import dataclass
from typing import AsyncIterator

from backend.cortex.contradiction import ContradictionDetector
from backend.cortex.curiosity_placement import turn_curiosity      # AX.1
from backend.cortex.curiosity import CuriosityEngine
from backend.cortex.extraction import EXTRACTION_MAX_CHARS, ExtractedTriple, extract_triples
from backend.loop.history import align_carrier, carrier_entry, history_wrap_enabled, wrap_prior_answers
from backend.providers.base import REASONING, Reply, reply_stream         # C-38
from backend.cortex.forecast import (
    forecast_citations, record_block as forecast_record_block, score_forecast,
)
from backend.cortex.hebbian import HebbianUpdater
from backend.cortex.predictive import PredictiveCortex
from backend.cortex.propagation import propagate
from backend.eval.calibration_log import log_confidence as _log_confidence
from backend.eval.citation_capture import capture_turn
from backend.eval.fire_capture import capture_fire, observe as fire_observe
from backend.kb.assertion import assertion_triples, detect_assertions
from backend.eval.ragas import TurnEvaluator
from backend.kb.decompose import decompose
from backend.kb.citation import (                          # F.5 / AU
    cited_ids, citation_test_version, reinforce_mode)
from backend.kb.gate import BeliefRejected                 # K.2
from backend.kb.granularity import Granularity   # Fix 2 — tag session triples
from backend.learning import learning_declaration          # FZ-2 / pre-CE
from backend.kb.retriever import RetrievedTriple, TripleRetriever, extract_terms
from backend.loop.prompt_render import (
    LEGACY_RENDER, context_hash, cortex_section, kb_sections,
    legacy_cortex_section, legacy_kb_section, prompt_render_mode,
    render_version, request_bytes_of, request_hash_of, select_cortex,
)
from backend.kb.tiered_store import TripleStore              # AR.3 stored_form
# AK — the substrate's own relations: the same partition the retriever's 20%
# cap and the semantic index's exclusion already draw, imported rather than
# restated so the category has one spelling.
from backend.kb.semantic_index import (
    DEFAULT_EXCLUDED_RELATIONS as _SUBSTRATE_RELATIONS,
)
from backend.loop.compaction import (
    TOKEN_BUDGET,                                   # AV — the answering num_ctx
    CompactionEvent,
    Summariser,
    assert_single_system_message,                   # BA fix 1
    assert_stable_prefix,
    run_cascade,
)
from backend.loop.state import AssembledContext, NexusLoopState, next_state
from backend.memory.hippocampus import Hippocampus
from backend.memory.lineage import LineageStore
from backend.memory.procedural import ProceduralMemory
from backend.providers import default_provider
from backend.providers.base import LLMProvider
from backend.providers.model_map import ROLES, resolve
from backend.router.model_kernel import ModelKernel
from backend.loop.reference_gateway import admit_results
from backend.tools.search import SearchRefused, SearchResult, WebSearchTool, should_trigger_search
from backend.vector.chroma import VectorStore, mark_vector_failure    # BA fix 6

MAX_VECTOR_CHUNKS = 5

# P1/F-03 — the prefix names the substrate so the model never disclaims its
# own memory. Law VII: this remains a stable constant. BA fix 3 moved the
# text to backend/loop/prefix.py so compaction's integrity rule can compare
# against it; re-exported here for every existing importer.
from backend.loop.prefix import STABLE_SYSTEM_PREFIX                 # noqa: E402,F401

MAX_INJECTED_TRIPLES = 20


_loop_logger = logging.getLogger("nexus.loop")

# C-38 — what the console is told when the reply carried reasoning and no
# stated answer. Sent as a `token` frame so it appears where the answer
# would; it is NOT the assistant message (history, extraction, the citation
# detector and the record see an empty answer, and `done` declares it).
ANSWER_EMPTY_CONSOLE_MESSAGE = "no stated answer; the model's reasoning was cut off"

# Phase 2A — optional side-channel for non-SSE subscribers (memory column).
# Stays None in normal operation; set_event_recorder() wires it during
# create_app(). sse_event() invokes it defensively so recording can never
# affect the live /chat stream.
_event_recorder = None


def set_event_recorder(fn):
    """Register a callback invoked alongside sse_event. fn(event_name, payload)."""
    global _event_recorder
    _event_recorder = fn


def sse_event(event: str, data) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    if _event_recorder is not None:
        try:
            _event_recorder(event, data if isinstance(data, dict) else {})
        except Exception:
            pass  # Never let recording break the loop
    return f"event: {event}\ndata: {payload}\n\n"


# ---------------------------------------------------------------------------
# H.3a — the KAIROS -> client bridge
# ---------------------------------------------------------------------------
# KAIROS runs on idle time and has no live SSE channel; it enqueues onto
# `NexusServices.pending_alerts` and each /chat call drains the queue. That
# drain is the ONLY path by which anything KAIROS observes reaches a client,
# and it is shared by every alert in the system — which is why the routing fix
# ships alone, ahead of any new emit.
#
# The allowed set is a RATCHET, not a filter for its own sake. The bridge was
# a router with one destination; making it honour `event` without bounding it
# would turn it into an arbitrary event injector, and an event name nothing
# declares is how the SSE contract drifted in the first place. Adding a member
# here is the deliberate acknowledgement that a new event type exists.
#
# Anything unrecognised falls back to the historical behaviour rather than
# being dropped or emitted under a name no client knows — and says so (I-6).
# A typo must not silently become a new event type, and must not lose an alert.
DEFAULT_ALERT_EVENT = "kb_pressure_alert"

BRIDGE_EVENTS = frozenset({
    "kb_pressure_alert",
    # H.3b — added deliberately, each declared in CLAUDE.md's SSE contract
    # and each with a UI handler that has been waiting since Phase 9.
    "chain_appended",         # kairos.py — a block sealed
    "kairos_consolidation",   # kairos.py — edges promoted to the store
    "curiosity_surfaced",     # kairos.py — AX.1: the idle-cycle pass's questions
})


def _bridge_event_name(payload: dict) -> str:
    """Which SSE event this queued payload should be emitted as."""
    raw = payload.get("event")
    if raw is None:
        # Pre-H.3a producers minted no `event` key, or minted it and relied on
        # the hardcoded destination. Both land here, unchanged.
        return DEFAULT_ALERT_EVENT
    name = str(raw).strip()
    if name in BRIDGE_EVENTS:
        return name
    _loop_logger.warning(
        "queued alert names event %r, which the bridge does not route; "
        "emitting as %r so the alert is not lost. Add it to BRIDGE_EVENTS "
        "(and to docs/CONTRACTS.md's SSE contract) if it is intended.",
        name, DEFAULT_ALERT_EVENT)
    return DEFAULT_ALERT_EVENT


# ---------------------------------------------------------------------------
# W6 — the recorder fails closed
# ---------------------------------------------------------------------------
# Before W6 the console released each answer frame while the model wrote it
# and wrote the record afterwards, and a record write that raised was logged
# while the turn went on with `lineage_id: null`. Now the answer's frames are
# held until `record_turn` has returned, and a write that raises ends the
# turn with one declared frame, `record_failed`. `live` is the rollback for
# the hold only: the frames go out as they arrive, and a failed write is
# declared all the same.
#
# A held frame is BUILT when it is released, never when it is held:
# `sse_event` also feeds the event ring, and a frame built early would have
# left the process by that door while its answer was still being held.
ANSWER_RELEASE_SETTING = "NEXUS_ANSWER_RELEASE"
AFTER_RECORD = "after_record"
LIVE = "live"
RECORD_FAILED_CONTRACT_VERSION = 1
_record_failures: dict = {"count": 0, "last_reason": None}      # in process, reset on restart


def _envelope_in_force(retriever) -> str | None:
    """W6 — the version of the installed recalibration policy in force, read
    when the record is written. None when no policy is installed (the
    compiled default is in force), and when no governance record is wired to
    the store (a loop composed without one). The store decides which by
    whether a policy row exists, never by the policy's name."""
    recal = getattr(getattr(retriever, "store", None), "recalibration", None)
    return recal.installed_envelope_version() if recal is not None else None


def answer_release() -> str:
    """`live` only for the literal `live`; any other value is `after_record`,
    the position that fails closed. Read at call time."""
    raw = os.environ.get(ANSWER_RELEASE_SETTING, AFTER_RECORD).strip().lower()
    return LIVE if raw == LIVE else AFTER_RECORD


def recorder_declaration() -> dict:
    """What a status surface says about the recorder: the release position,
    a setting it does not recognise (named, and treated as the closed
    position), and the failed writes since this process started."""
    raw = os.environ.get(ANSWER_RELEASE_SETTING, AFTER_RECORD).strip().lower()
    out = {"answer_release": answer_release(), "setting": ANSWER_RELEASE_SETTING,
           "record_failures_since_start": _record_failures["count"],
           "last_failure_reason": _record_failures["last_reason"]}
    if raw not in (AFTER_RECORD, LIVE, ""):
        out["setting_not_recognised"] = raw
        out["treated_as"] = AFTER_RECORD
    return out


def substrate_weight_restore_enabled() -> bool:
    """AK's rollback. `on` reproduces the pre-AK `max()` exactly: a
    retrieved substrate row restores its edge to the stored high-water
    mark. Read at call time (the E.2 pattern)."""
    return os.environ.get(
        "NEXUS_SUBSTRATE_WEIGHT_RESTORE", "off").strip().lower() == "on"


def _ingest_hits_into_hippocampus(
    hippocampus: Hippocampus, hits: list[RetrievedTriple]
) -> list[str]:
    """Fold retrieved triples into the session graph; return seed node ids.

    AK — a SUBSTRATE row (co_activated / recurring_query_term) no longer
    copies its stored weight onto an existing edge. That weight is add()'s
    monotone MAX across every promotion — a record, not a strength — and
    copying it back silently undid Hebbian weakening and the KAIROS LTD
    pass for any association whose row got retrieved (I-8: the record
    layer was writing the prominence layer). CLAIM rows keep the warming
    path unchanged: their weight is evidential, not the graph's own
    recycled output. Suppressions that would actually have raised a weight
    are COUNTED on the hippocampus and surfaced at /cortex/stats (I-6).
    """
    seeds: list[str] = []
    restore_all = substrate_weight_restore_enabled()
    for rt in hits:
        restore = restore_all or rt.triple.relation not in _SUBSTRATE_RELATIONS
        if not restore:
            edge = hippocampus.get_edge(rt.triple.subject, rt.triple.object)
            if edge is not None and rt.triple.weight > edge.weight:
                hippocampus.restorations_suppressed += 1
        hippocampus.ingest_triple(
            rt.triple.subject, rt.triple.relation, rt.triple.object,
            weight=rt.triple.weight, restore_weight=restore,
        )
        seeds.append(rt.triple.subject)
    return seeds


def assemble_context(
    user_message: str,
    retriever: TripleRetriever | None,
    hippocampus: Hippocampus | None = None,
    vector_store: VectorStore | None = None,
    *,
    top_k: int = MAX_INJECTED_TRIPLES,
    vector_k: int = MAX_VECTOR_CHUNKS,
    query_embedding=None,
) -> AssembledContext:
    """Stage 2 — KB retrieval + cortex propagation + Matryoshka 2-stage RAG.

    W.1 — `query_embedding` is the turn's query vector, computed ONCE by the
    caller. It is an argument rather than something computed here because this
    function is synchronous and runs inside a live event loop, and because one
    embedding per turn is the whole per-turn cost of semantic recall. `None`
    (the default, and what every existing caller passes) means the semantic
    channel does not run.
    """
    hits: list[RetrievedTriple] = []
    block_parts: list[str] = []
    activation = None
    vector_chunks: "list | None" = []

    # AW — the block says only what the record can re-derive. `legacy` is
    # the rollback: the pre-AW strings byte for byte (see prompt_render.py).
    legacy = prompt_render_mode() == LEGACY_RENDER
    rendered: tuple = ()

    if retriever is not None:
        hits = retriever.retrieve(user_message, top_k=top_k,
                                  query_embedding=query_embedding)
        if hits:
            if legacy:
                block_parts.append(legacy_kb_section(hits))
            else:
                # Claims under KB CONTEXT; substrate rows under their own
                # header — still injected, no longer presented as facts.
                block_parts.extend(kb_sections(hits))

    if hippocampus is not None and hits:
        seeds = _ingest_hits_into_hippocampus(hippocampus, hits)
        # Also seed query-term nodes so propagation has anchors when KB misses
        for term in extract_terms(user_message):
            hippocampus.upsert_node(term)
            seeds.append(term)
        activation = propagate(hippocampus, list(dict.fromkeys(seeds)))
        if legacy:
            section = legacy_cortex_section(activation, hippocampus)
        else:
            # Node id and this turn's activation, both real; a node renders
            # only with a live claim behind it, so the query's own tokens
            # (always at 1.00) never appear.
            rendered = tuple(select_cortex(activation, retriever.store))
            section = cortex_section(rendered)
        if section:
            block_parts.append(section)

    if vector_store is not None:
        try:
            vector_chunks = vector_store.query_sync(
                user_message, final_k=vector_k,
            )
        except Exception as e:
            # BA fix 6 — channel unavailable is not "nothing found". None,
            # declared on /healthz; unreachable while Chroma is dark, fixed
            # so it is not found again when it is not.
            vector_chunks = None
            mark_vector_failure(f"{type(e).__name__}: {e}")
        if vector_chunks:
            rendered = "\n".join(
                f"  [{c.score:.2f}] {c.text[:160]}" for c in vector_chunks
            )
            block_parts.append(
                "[VECTOR RAG — Matryoshka 2-stage 256→768]\n" + rendered
            )

    block = ("\n\n" + "\n\n".join(block_parts)) if block_parts else ""
    return AssembledContext(
        hits=hits, activation=activation,
        vector_chunks=vector_chunks, block=block,
        rendered_activation=rendered,
    )



def _accrue_calibration(hits, activation, state, lineage_id) -> None:
    """Packet P.4 — one row per injected belief, on the right channel.

    THE SPLIT. A retrieved belief either matched a query term directly or was
    reached through the graph, and those are different claims:

      observation   hop 0 — the query contained the term. GIVEN, not
                    estimated. Its confidence is 1.0 by construction and it
                    is logged unapologetically, because a channel that hides
                    its trivial half misrepresents what it observed.
      propagation   hop > 0 — a computed activation. THE CALIBRATABLE ROWS.

    Before P.4 every row said `retrieval` and every stated_confidence was 1.0
    — 297 of them on the operator's store, unusable by construction because
    nothing distinguished the given from the estimated.

    CONTEXT ADDED: `hop`, `rank`, `saturated`, `model_id`, `lineage_id`.
    `lineage_id` is the join key to the evidence channel and is the reason
    this accrual moved after the lineage record — it cannot be backfilled.
    `saturated` marks a row at the very top of the descending `top_k`, where
    a ceiling bias would show first.
    """
    if not hits:
        return
    hops: dict[str, int] = {}
    if activation is not None:
        for node_id, hop in getattr(activation, "fired_sequence", ()) or ():
            hops.setdefault(str(node_id), int(hop))

    for rank, rt in enumerate(hits):
        hop = hops.get(str(rt.triple.subject))
        if hop is None:
            hop = hops.get(str(rt.triple.object))
        mechanism = "observation" if not hop else "propagation"
        try:
            _log_confidence(
                mechanism, rt.score, belief_id=rt.triple.id,
                context={
                    "turn":       state.turn_count + 1,
                    "tier":       rt.triple.tier,
                    "source":     rt.triple.source,
                    "hop":        hop if hop is not None else 0,
                    "rank":       rank,
                    # top of a descending top_k — where a ceiling bias shows
                    # first. Recorded, not acted on.
                    "saturated":  rank == 0,
                    "model_id":   state.active_model,
                    "lineage_id": lineage_id,
                })
        except Exception as e:                    # never break a turn
            _loop_logger.warning("calibration accrual failed: %s: %s",
                                 type(e).__name__, e)


async def _query_embedding(user_message: str, retriever, provider):
    """The turn's query vector, or None. Never raises, never blocks the turn.

    W.1 — three gates before a single byte goes out, in this order:
      1. no retriever, or no semantic index on it — nothing to search;
      2. `NEXUS_SEMANTIC_SLOTS = 0` (the default) — the channel is off, so
         paying for an embedding would be buying a vector nobody reads;
      3. no provider — the tests' usual state.

    A failed embedding degrades to lexical-only retrieval rather than to a
    failed turn: the question this packet exists to fix is one a person can
    ask again, and a substrate that refuses to answer at all is worse than one
    that answers from the lexical channel. The failure IS declared —
    `embed_via_provider` flips `/healthz.embedding_degraded` on its way past.
    """
    if retriever is None or getattr(retriever, "semantic", None) is None:
        return None
    from backend.kb.semantic_index import semantic_slots
    if semantic_slots() <= 0 or provider is None:
        return None
    from backend.vector.embeddings import embed_via_provider
    try:
        return await embed_via_provider(provider, user_message, kind="query")
    except Exception as e:                            # noqa: BLE001
        _loop_logger.warning("query embedding failed, falling back to lexical "
                             "retrieval only: %s: %s", type(e).__name__, e)
        return None


# ---------------------------------------------------------------------------
# MCP-2 — the turn, split at the model call
# ---------------------------------------------------------------------------
# `prepare_turn` runs every stage before the model call and ends by yielding
# a `TurnHandle`; `complete_turn` takes the handle and the model's reply and
# runs every stage after it. `run_turn` (at the bottom of this module) is the
# console's composition of the two with NEXUS's own model between them, and
# it is pinned byte for byte: the same frames, requests and records as before
# the split (tests/test_mcp2_build.py). Under MCP the client's model sits
# between the two calls instead, and the record says so (`record_scope`).

FULL_TURN = "full_turn"
MCP_BLOCK = "mcp_block"

# W0b — the reasons a record's replay-capture fields are NULL, when the turn
# itself (not the provider boundary) is why.
MCP_CAPTURE_REASON = ("not observable: the client's model ran outside NEXUS "
                      "(its seed, weights, options and runtime are the client's)")
NO_CAPTURE_REASON = "not captured: the reply did not pass through the provider boundary"


def _replay_capture_args(handle, capture, record_scope) -> dict:
    """W0b — what the record keeps so the turn can be re-run.

    The console's turn: the four fields from the reply stream's capture, each
    absent one with its reason, and the exact bytes `request_hash` covers.
    Under `mcp_block` the client's model ran outside NEXUS, so the four are
    NULL with that reason, and NEXUS's block — what entered the client's
    context, the bytes that record's `request_hash` covers — is kept the
    same way. The store refuses bytes that do not hash to the record's
    `request_hash` and notes the refusal, so nothing here is trusted."""
    from backend.memory.lineage import REQUEST_ENCODING_BLOCK, REQUEST_ENCODING_MESSAGES
    from backend.providers.replay_capture import CAPTURE_FIELDS
    if record_scope == MCP_BLOCK:
        return {"capture_notes": {f: MCP_CAPTURE_REASON for f in CAPTURE_FIELDS},
                "request_body": handle.dynamic_block.encode("utf-8"),
                "request_body_encoding": REQUEST_ENCODING_BLOCK}
    body = request_bytes_of(handle.request_messages)
    if capture is None:
        return {"capture_notes": {f: NO_CAPTURE_REASON for f in CAPTURE_FIELDS},
                "request_body": body, "request_body_encoding": REQUEST_ENCODING_MESSAGES}
    notes = dict(capture.notes)
    for name, value in zip(CAPTURE_FIELDS, (capture.seed, capture.digest,
                                            capture.config, capture.environment)):
        if value is None:
            notes.setdefault(name, "absent: the provider boundary gave no reason")
    return {"inference_seed": capture.seed, "model_digest": capture.digest,
            "inference_config": capture.config, "environment": capture.environment,
            "capture_notes": notes, "request_body": body,
            "request_body_encoding": REQUEST_ENCODING_MESSAGES}


@dataclass(frozen=True)
class TurnHandle:
    """Exactly the state that crosses the model call — MCP-1's pinned set,
    derived by AST over the unsplit loop and held here by name, plus the
    four things the second half needs that the first half was given
    (`state`, `user_message`, `scope`, `request_messages`). The two stream
    vessels MCP-1 listed (`assistant_chunks`, `reasoning_chunks`) are the
    model call's own and are not here: under MCP there is no stream. Frozen
    (Law 1); `retrieval_id` keys the MCP handle table and `created_at` its
    timeout."""
    retrieval_id: str
    created_at: float
    state: NexusLoopState
    user_message: str
    scope: str
    request_messages: tuple
    # the retrieval and the block
    hits: tuple
    activation: object
    rendered_activation: tuple
    dynamic_block: str
    system_content: str
    # the request's hash and its inputs
    request_hash: str
    history: tuple
    # the forecast and the prediction
    citation_forecast: object
    predicted_top: tuple
    # the cascade result and the fire capture's entry facts (CP)
    cascade: object
    fire_observed: object
    fire_sizes_entry: tuple
    fire_block_entry: str
    # the work unit and attribution (v3.1 Phase 1/1b)
    unit_id: str | None
    unit_status: str
    attribution: dict
    accountable: str | None
    asserted_by: dict | None
    boundary: str | None
    flags: tuple
    # the provider the second half extracts with
    active_provider: object
    # W10b (D4) — the id the record will carry, minted at the turn's start so
    # an action the first half takes can name it
    lineage_id: str | None = None


async def prepare_turn(
    state: NexusLoopState,
    user_message: str,
    *,
    retriever: TripleRetriever | None = None,
    hippocampus: Hippocampus | None = None,
    summariser: Summariser | None = None,
    procedural: ProceduralMemory | None = None,
    kernel: ModelKernel | None = None,
    vector_store: VectorStore | None = None,
    predictive: PredictiveCortex | None = None,
    provider: LLMProvider | None = None,
    search_tool: WebSearchTool | None = None,
    search_consents: dict | None = None,
    pending_alerts: list | None = None,
    scope: str = "default",
    # v3.1 Phase 1 / 1b — work-unit attribution, the way scope is done:
    # every value below is THIS request's, asserted by the caller, recorded
    # literally and never inferred. `work_units` is the store; the request
    # may name a unit, a type, an answerable identity, its actor fields and
    # a boundary (`close` and, since W6, `reopen` are honoured, each after
    # the turn's record is written); the session id names
    # the pseudo-unit a turn that names nothing falls into. Nothing is
    # inherited from an earlier turn.
    work_units: "WorkUnitStore | None" = None,
    session_id: str = "default",
    work_unit: str | None = None,
    work_unit_type: str | None = None,
    accountable: str | None = None,
    actor: dict | None = None,
    decision_boundary: str | None = None,
    identity_mechanism: str | None = None,
) -> AsyncIterator[tuple]:
    """The first half of the turn: every stage before the model call, yielding
    `(frame, state)` pairs exactly as the unsplit loop did and, LAST, the
    `(None, TurnHandle)` pair that carries the crossing state to
    `complete_turn`. The console (`run_turn`) consumes both; the MCP
    retrieve tool keeps the handle and drops the frames (no stream to
    carry them). Takes the first half's collaborators only."""
    # Pre-Phase-10c governance: drain any KAIROS-emitted alerts (semantic
    # duplicate scan, cortex drift) before turn proper. KAIROS has no live
    # SSE channel by design — it's idle-time. Each /chat call is the bridge:
    # alerts queued during idle surface as kb_pressure_alert frames here.
    if pending_alerts:
        while pending_alerts:
            payload = pending_alerts.pop(0)
            # H.3a — the payload is minted with an "event" key for routing.
            # This used to strip that key and hardcode every frame to
            # `kb_pressure_alert`, so the router had exactly one destination:
            # a producer could enqueue `chain_appended` and the client would
            # receive `kb_pressure_alert`. That is why three declared SSE
            # events are never emitted — adding an emit call in KAIROS was
            # never going to be sufficient on its own.
            data = {k: v for k, v in payload.items() if k != "event"}
            yield sse_event(_bridge_event_name(payload), data), state

    # Phase 5: procedural memory observes every turn — recurring patterns
    # promote when count >= threshold (KAIROS performs the actual write-out).
    if procedural is not None:
        procedural.observe(user_message)

    # W.1 — the turn's query vector, computed ONCE and reused by every
    # assemble_context call below (the preview, the compaction rebuild, and
    # the post-autocompact rebuild). This is the entire per-turn cost of
    # semantic recall: one provider call, and none at all when the channel is
    # off, which is the default.
    # v3.1 Phase 1 / 1b — which work unit this turn belongs to, resolved
    # before anything is written so the lineage record and the junction
    # agree. With a store: the request's unit (created on first mention),
    # else the session pseudo-unit — never an earlier turn's unit, never
    # fails. Without one (library callers): the request's values are carried
    # as declared. `_accountable` is the REQUEST's, per turn, not the row's:
    # accountability is a field in force and the records are its history.
    # Nothing here changes the prompt — attribution is recorded, not
    # rendered (acceptance 3 / 6).
    from backend.kb.work_units import actor_fields as _actor_fields
    _accountable = accountable or None
    # W10b (D4) — the id this turn's record will carry, minted now so an action
    # the turn takes before its record is written names it.
    _lineage_id = str(_uuid.uuid4())
    _boundary = decision_boundary or None
    _asserted_by = _actor_fields(actor) if _boundary else None
    if work_units is not None:
        # W6 — attribution only: the boundary act (close, reopen) is a
        # governance act and is made after the turn's record exists.
        _unit, _attribution = work_units.attribute_for_turn(
            session_id=session_id, requested_id=work_unit or None,
            requested_type=work_unit_type or None, accountable=_accountable,
            actor=actor, boundary=_boundary)
        _unit_id, _unit_status = _unit["work_unit_id"], _unit["attribution_status"]
    else:
        _unit_id = work_unit or None
        _unit_status = "attributed" if _unit_id else "unattributed"
        _attribution = {"unit_first_seen": False, "unit_closed": False, "flags": []}
    _flags = list(_attribution.get("flags") or [])

    query_embedding = await _query_embedding(user_message, retriever, provider)

    # AF.1 — the citation forecast, formed and PERSISTED before retrieval
    # runs. Seeded from the query's terms alone (only those already graph
    # nodes), read-only over the session graph, committed to the calibration
    # ledger under the mechanism P.4 reserved. This sits ABOVE the first
    # assemble_context call on purpose: the forecast must not be able to see
    # `hits`, and the sentinel checks the line numbers.
    citation_forecast = None
    if retriever is not None and hippocampus is not None:
        try:
            citation_forecast = forecast_citations(
                hippocampus, retriever.store, user_message,
                turn=state.turn_count + 1, model_id=state.active_model)
        except Exception as e:                        # never break a turn
            _loop_logger.warning("citation forecast failed: %s: %s",
                                 type(e).__name__, e)

    # Stage 2 (preview): assemble dynamic context so we can size the prompt
    # honestly before deciding whether to compact.
    ctx = assemble_context(user_message, retriever, hippocampus, vector_store,
                           query_embedding=query_embedding)
    hits         = ctx.hits
    activation   = ctx.activation
    vector_chunks = ctx.vector_chunks
    dynamic_block = ctx.block
    rendered_activation = ctx.rendered_activation

    # CP — the fire capture's observation of THIS turn's rebuilt block, taken
    # here, at assembly, BEFORE this turn's own cascade: the previous fire's
    # next-turn check must not be answered by a graph this turn's fire has
    # already edited. Gated, so off walks nothing. None with the gate off,
    # and `capture_fire` then writes nothing this turn (declared, not silent:
    # the coverage block reads the gate).
    from backend.eval.fire_capture import capture_enabled as _fire_capture_on
    fire_observed = (fire_observe(hits=hits,
                                  rendered_activation=rendered_activation,
                                  hippocampus=hippocampus)
                     if _fire_capture_on() else None)

    # Phase 10b — web-search trigger (after KB retrieval, before context assembly
    # finalisation). Held in a list so the rebuild_block_fn closure picks up
    # whatever the search injection added even if autocompact reruns later.
    # ---- S.4 — the assertion offer -------------------------------------
    # O.2b built detection AND `POST /kb/assert/confirm`, and built no offer.
    # So the operator could state "the dose was 600mg", have it recognised as
    # an assertion, and never be told — a feature reachable only by someone
    # who had read the source. Detection without an offer is not a feature,
    # it is a fact about the code.
    #
    # Emitted DIRECTLY here, not through the KAIROS bridge: detection runs on
    # the user's message during a turn, so there is a live SSE channel and
    # BRIDGE_EVENTS is not involved. Same path `web_search` takes, and for the
    # same reason.
    #
    # The payload declares what WOULD be stored, including when the answer is
    # "nothing" (I-6). A sentence the shaper cannot turn into a triple still
    # gets offered with `shaped: false`, because the operator learning that
    # their claim cannot be stored is more useful than silence — and it is the
    # same thing `/kb/assert/confirm` already answers on confirmation.
    #
    # AR.3 — "what WOULD be stored" is what `add()` will store, not what the
    # shaper parsed: the plain keys carry the stored form (asked of the one
    # function that knows, `TripleStore.stored_form`), and the parsed form
    # rides beside it under `as_parsed`, named distinctly.
    for _cand in detect_assertions(user_message):
        _shaped = assertion_triples(_cand.text)
        _declared = []
        for t in _shaped:
            _s, _r, _o = TripleStore.stored_form(t.subject, t.relation, t.object)
            _declared.append({
                "subject": _s, "relation": _r, "object": _o,
                "as_parsed": {"subject": t.subject, "relation": t.relation,
                              "object": t.object},
                "weight": t.weight, "tier": t.tier})
        yield sse_event("kb_pressure_alert", {
            "type":     "assertion",
            "action":   "proposed",
            "domain":   "kb",
            "sentence": _cand.text,
            "reason":   _cand.reason,
            "shaped":   bool(_shaped),
            "triples":  _declared,
            "endpoint": "/kb/assert/confirm",
        }), state

    web_block_holder: list[str] = [""]
    web_results: list[SearchResult] = []
    if search_tool is not None:
        gap_density = float(activation.density) if activation is not None else None
        if should_trigger_search(user_message, gap_density=gap_density):
            proposal = search_tool.proposal(user_message, gap_density=gap_density)
            yield sse_event("kb_pressure_alert", {
                "type":      "web_search",
                "action":    "proposed",
                "query":     user_message,
                "reasons":   proposal["reasons"],
                "endpoint":  proposal["endpoint"],
            }), state

            consent_key = (user_message or "").strip()
            granted = bool(
                search_consents and search_consents.get(consent_key, False)
            )
            if granted:
                from backend.memory import actions as _actions
                try:
                    web_results = await search_tool.search(
                        user_message, confirmed=True,
                        trigger=_actions.Trigger(lineage_id=_lineage_id, accountable_identity=_accountable,
                                                 identity_mechanism=identity_mechanism),
                    )
                except SearchRefused:
                    web_results = []
                except _actions.ActionNotRecorded as e:
                    # W10b (D2) — fail closed and said: the search was not sent,
                    # or its result is withheld, and the turn goes on without it.
                    web_results = []
                    yield sse_event("kb_pressure_alert", {
                        "type": "web_search", "action": "not_recorded", "query": user_message,
                        "detail": str(e),
                    }), state
                if web_results:
                    # W5 — search results become prompt text in one place,
                    # backend/loop/reference_gateway.py, and this loop reads
                    # no field of a result itself. With
                    # NEXUS_REFERENCE_GATEWAY off (the default) the block and
                    # the frame are what they always were, byte for byte; on,
                    # each result is judged, reduced and labelled, and what
                    # was withheld is declared on the frame.
                    _admission = admit_results(web_results)
                    web_block_holder[0] = _admission.block
                    dynamic_block = dynamic_block + web_block_holder[0]
                    yield sse_event("rag_injected", {
                        "source":    "web",
                        "n_results": len(_admission.results),
                        "results":   list(_admission.results),
                        **({"gateway": _admission.gateway} if _admission.gateway is not None else {}),
                    }), state
                # Single-use consent — clear regardless of result count so a
                # zero-result search doesn't lock in indefinite consent.
                search_consents.pop(consent_key, None)

    # Stage 2.5 (Phase 7): predictive cortex generates an expected activation
    # map BEFORE inference. The propagation that produced `activation` was
    # itself the prediction in the offline path; here we snapshot it so the
    # post-inference compute_error has a reference. Extracted-triple-driven
    # actual activation lands in Phase 9 (kept symmetric here).
    predicted_top: list[str] = []
    if predictive is not None and activation is not None and activation.activations:
        predicted_top = [n for n, _ in activation.top_k(20)]
        yield sse_event("prediction_generated", {
            "expected_nodes": predicted_top[:10],
            "n_predicted": len(predicted_top),
        }), state

    # Stage 1.5 (Phase 6.B + Fix 0 / Part B): override wins, kernel is fallback.
    # Drift anchor: STABLE_SYSTEM_PREFIX must not be touched by either path.
    _prefix_pre = STABLE_SYSTEM_PREFIX
    column_state = activation.column_state if activation is not None else None
    selected: str | None = None
    capability_str: str = "user_override"

    # Fix 9 — when the turn carries any image attachment, force MULTIMODAL
    # routing (resolves to the multimodal model in the registry). PDF/docx
    # text-only attachments do NOT trigger this. BA (audit A1f) corrected
    # this comment: no attachment content is rendered into the prompt on
    # any path — the only reader of `state.attachments` is the image-type
    # check below, and the tuple is cleared at the end of the turn. We
    # honour model_override if set — the user's explicit pin always wins
    # (Law 2 / Law 9 spirit).
    has_image_attachment = any(
        (a.get("type") or "").startswith("image/")
        for a in state.attachments
    )
    if has_image_attachment and not state.model_override:
        mm_model = resolve("multimodal")
        yield sse_event("model_routed", {
            "model":           mm_model,
            "capability":      "multimodal",
            "override_active": False,
            "reason":          "image_attachment_present",
        }), state
        if mm_model != state.active_model:
            state = next_state(state, active_model=mm_model)
        capability_str = "multimodal"
    elif state.model_override:
        selected = (
            resolve(state.model_override)
            if state.model_override in ROLES
            else state.model_override
        )
        yield sse_event("model_routed", {
            "model": selected,
            "capability": "user_override",
            "override_active": True,
            "locked": state.override_locked,
        }), state
        if selected != state.active_model:
            state = next_state(state, active_model=selected)
    elif kernel is not None:
        decision = kernel.route(query=user_message, column_state=column_state)
        if decision.profile is not None:
            chosen_profile = decision.profile

            # Fix 6 — if the routed profile is not yet pulled, surface a
            # model_pull_proposal SSE and fall back to the best installed
            # GENERAL_REASONING model for this turn rather than 404-ing.
            if not getattr(chosen_profile, "is_pulled", False):
                # Best general-reasoning model that IS pulled, if any.
                from backend.router.capabilities import Capability as _Cap
                fallback = None
                for cand in kernel.registry.with_capability(_Cap.GENERAL_REASONING):
                    if getattr(cand, "is_pulled", False):
                        fallback = cand
                        break
                yield sse_event("model_pull_proposal", {
                    "model":    chosen_profile.display_name,
                    "name":     chosen_profile.name,
                    "size_gb":  chosen_profile.pull_size_gb,
                    "improves": f"Required for {decision.capability.value} tasks",
                    "fallback": fallback.name if fallback else None,
                }), state
                if fallback is not None and fallback.name != chosen_profile.name:
                    capability_str = decision.capability.value
                    selected = fallback.name
                    yield sse_event("model_routed", {
                        "model":            selected,
                        "capability":       "general_reasoning",
                        "override_active":  False,
                        "fallback_used":    True,
                        "reason":           "specialist_unavailable_fallback",
                        "note":             f"fallback_from:{chosen_profile.name}",
                    }), state
                    if selected != state.active_model:
                        state = next_state(state, active_model=selected)
                    # Skip the normal model_routed/swap path below.
                    chosen_profile = None

            if chosen_profile is not None:
                capability_str = decision.capability.value
                selected = chosen_profile.name
                yield sse_event("model_routed", {
                    "model": selected,
                    "capability": capability_str,
                    "override_active": False,
                    "fallback_used": decision.fallback_used,
                    "reason": decision.reason,
                }), state
                if chosen_profile.name != state.active_model:
                    yield sse_event("model_swap_starting", {
                        "from": state.active_model,
                        "to": chosen_profile.name,
                        "capability": decision.capability.value,
                        "reason": decision.reason,
                        "fallback_used": decision.fallback_used,
                    }), state
                    state = next_state(state, active_model=chosen_profile.name)
    if STABLE_SYSTEM_PREFIX != _prefix_pre:                       # pragma: no cover
        raise RuntimeError("Prefix drift on override (Law VII)")

    # Stage 1: compaction cascade (Law VII — never silent truncate).
    # The cascade calls rebuild_block_fn after every tier so collapse-driven
    # propagation changes (and any future state-driven block content) flow
    # through the threshold check without re-running the whole loop.
    def _rebuild_block(_state, _hippo) -> str:
        return assemble_context(
            user_message, retriever, _hippo, vector_store,
            query_embedding=query_embedding,
        ).block + web_block_holder[0]
    # CP — the cascade-entry facts the fire row reports: sizes of the two
    # structures tiers 1–2 edit, and the preview block. Two lens and a name
    # bind; nothing the cascade reads.
    fire_sizes_entry = (len(state.session_memory), len(state.graph_context))
    fire_block_entry = dynamic_block
    cascade = await run_cascade(
        state, hippocampus,
        prefix=STABLE_SYSTEM_PREFIX,
        dynamic_block=dynamic_block,
        summariser=summariser,
        rebuild_block_fn=_rebuild_block,
    )
    state = cascade.state
    for i, ev in enumerate(cascade.events):
        yield sse_event("compaction_fired", {
            "tier": ev.tier.value,
            "tokens_before": ev.tokens_before,
            "tokens_after": ev.tokens_after,
            "tokens_freed": ev.tokens_freed,
            "items_removed": ev.items_removed,
            "reason": ev.reason,
        }), state
        # Phase 9 — emit compaction_report immediately after each compaction_fired
        if i < len(cascade.reports):
            yield sse_event("compaction_report", cascade.reports[i].to_dict()), state
    # BD-3 — no halted frame: the integrity report is PASS or WARN per tier,
    # nothing halts and no state is marked.

    # If autocompact ran, the messages tuple changed — rebuild dynamic context
    # so we don't double-count what was already summarised.
    if any(ev.tier.value == "auto_compact" for ev in cascade.events):
        ctx = assemble_context(user_message, retriever, hippocampus, vector_store,
                               query_embedding=query_embedding)
        hits         = ctx.hits
        activation   = ctx.activation
        vector_chunks = ctx.vector_chunks
        dynamic_block = ctx.block
        rendered_activation = ctx.rendered_activation

    system_content = STABLE_SYSTEM_PREFIX + dynamic_block
    assert_stable_prefix(system_content, STABLE_SYSTEM_PREFIX)  # Law VII guard

    new_user_msg = {"role": "user", "content": user_message}
    history = list(state.messages) + [new_user_msg]
    request_messages = [{"role": "system", "content": system_content}] + history
    # BA fix 1 — exactly one system-role message, first, prefix-led. The
    # compaction summary used to be a second one.
    assert_single_system_message(request_messages, STABLE_SYSTEM_PREFIX)
    # BA fix 2 — the whole request as sent, hashed for the record. Not the
    # history path closed: made auditable (the history is not retained).
    request_hash = request_hash_of(request_messages)

    yield sse_event("turn_start", {"turn": state.turn_count + 1}), state

    if hits:
        yield sse_event("triples_injected", [
            {"s": rt.triple.subject, "r": rt.triple.relation, "o": rt.triple.object,
             "w": round(rt.score, 3), "g": rt.triple.granularity, "tier": rt.triple.tier}
            for rt in hits
        ]), state
        # P.4 — the calibration accrual MOVED. It used to fire here, before
        # the lineage record exists, so it could never carry `lineage_id` —
        # the join key between the calibration channel and the evidence
        # channel, which the packet notes cannot be backfilled. It now fires
        # after the lineage record is written; see `_accrue_calibration`.

    if activation and activation.activations:
        yield sse_event("graph_activating", [
            {"id": nid, "act": round(a, 3)} for nid, a in activation.top_k(10)
        ]), state

    # Stage 3 setup (Fix 0 — provider-abstracted; provider arg falls back to
    # the module default for legacy callers, resolved on first use: W3). The
    # provider crosses the line: the second half extracts with it.
    active_provider = provider or default_provider()

    # MCP-2 — the line. Everything the second half reads from the first is
    # on the handle, and nothing else; the model call sits between the two.
    yield None, TurnHandle(
        retrieval_id=str(_uuid.uuid4()), created_at=_time.time(),
        state=state, user_message=user_message, scope=scope,
        request_messages=tuple(request_messages),
        hits=tuple(hits), activation=activation, rendered_activation=tuple(rendered_activation),
        dynamic_block=dynamic_block, system_content=system_content,
        request_hash=request_hash, history=tuple(history),
        citation_forecast=citation_forecast, predicted_top=tuple(predicted_top),
        cascade=cascade, fire_observed=fire_observed,
        fire_sizes_entry=tuple(fire_sizes_entry), fire_block_entry=fire_block_entry,
        unit_id=_unit_id, unit_status=_unit_status, attribution=_attribution,
        accountable=_accountable, asserted_by=_asserted_by, boundary=_boundary,
        flags=tuple(_flags), active_provider=active_provider, lineage_id=_lineage_id,
    )


async def complete_turn(
    handle: TurnHandle,
    reply: Reply,
    *,
    token_count: int = 0,
    retriever: TripleRetriever | None = None,
    hippocampus: Hippocampus | None = None,
    hebbian: HebbianUpdater | None = None,
    evaluator: TurnEvaluator | None = None,
    predictive: PredictiveCortex | None = None,
    contradiction: ContradictionDetector | None = None,
    curiosity: CuriosityEngine | None = None,
    lineage: "LineageStore | None" = None,
    work_units: "WorkUnitStore | None" = None,
    record_scope: str | None = None,
    identity_mechanism: str | None = None,
    extract: bool = True,
    model_id: str | None = None,
    outcome: dict | None = None,
    capture=None,
    held_tokens: list[str] | None = None,
) -> AsyncIterator[tuple[str, NexusLoopState]]:
    """The second half of the turn: everything after the model call, from the
    reply to the `done` frame, yielding `(frame, state)` pairs exactly as the
    unsplit loop did.

    `capture` (W0b) is the reply stream's `InferenceCapture` — the seed sent,
    the options, the model's digest, the environment — which the record
    carries so the turn can be re-run; the console passes it, MCP has none.

    `reply` is the model's separated reply (C-38). The console builds it from
    the stream and passes the token count it streamed; under MCP the client's
    answer arrives whole, with no reasoning and no stream, and
    `record_scope=MCP_BLOCK` declares on the record which fields are the
    client's claims. `extract=False` runs no extraction (the MCP-2 decision:
    no local model, no client self-report); `model_id` names the client's
    model on the record. `outcome`, when given, is filled with the record's
    identity and the new state so a caller that does not consume `done` can
    still read them.

    `identity_mechanism` (W4) is how the turn's `accountable_identity` was
    established, recorded and sealed when it is not the default (`asserted`,
    None); `proxy` when the identity came from a loopback proxy's header. It
    is a parameter here, as `record_scope` is, and never crosses on the
    handle; MCP passes none (its owner is configuration).
    """
    state = handle.state
    user_message, scope = handle.user_message, handle.scope
    hits = list(handle.hits)
    activation, rendered_activation = handle.activation, handle.rendered_activation
    dynamic_block, system_content, request_hash = handle.dynamic_block, handle.system_content, handle.request_hash
    history = list(handle.history)
    citation_forecast, predicted_top, cascade = handle.citation_forecast, list(handle.predicted_top), handle.cascade
    fire_observed, fire_sizes_entry, fire_block_entry = handle.fire_observed, handle.fire_sizes_entry, handle.fire_block_entry
    _unit_id, _unit_status, _attribution = handle.unit_id, handle.unit_status, handle.attribution
    _accountable, _asserted_by, _boundary, _flags = handle.accountable, handle.asserted_by, handle.boundary, list(handle.flags)
    active_provider = handle.active_provider

    structured = reply
    answer_text = structured.answer
    assistant_msg = {"role": "assistant", "content": answer_text}
    # C-38 — the empty answer is a DECLARED STATE, never a fallback: the
    # console is told, `done` and the record carry it, and nothing below
    # extracts from the reasoning because the answer is empty.
    # W6 — this half yields NOTHING before the record step. The three frames
    # it used to yield here (the empty-answer notice, `triples_extracted`,
    # `contradiction_found`) go out right after the record is written, in
    # the order they always had, and are never built if the write fails.

    # v3.1 Phase 1 — every claim `add()` admits during this turn's extraction
    # is collected here and written to the work-unit junction after the
    # lineage record exists (admission, not use; keyed on the id `add()`
    # returns, insert or upsert hit alike). The scope is opened only around
    # this stage so nothing else that calls `add()` is attributed by accident.
    _admission_stack = contextlib.ExitStack()
    _admitted: list[int] = (_admission_stack.enter_context(retriever.store.admissions())
                            if retriever is not None and hasattr(retriever.store, "admissions")
                            else [])

    # Stage 4 (Phase 9): real triple extraction via FAST_EXTRACTION model.
    # Provider call is mockable; failure path returns []. Result drives both
    # contradiction detection and the prediction-error "actual" activation set.
    extracted: list[ExtractedTriple] = []
    # K.2 — gate rejections for this turn, declared in `done` (I-6). Collected
    # here rather than emitted with `triples_extracted`, which fires before
    # persistence and carries a list payload with nowhere to put a count.
    _rejections: list[dict] = []
    _stored = 0
    _decomposed = 0
    # BA fix 6 — per-candidate outcomes, one each, so
    # `extracted == sum(outcomes.values())` is an identity the report can
    # assert; and storage failures are COUNTED, never `pass`ed.
    _outcomes = {"stored": 0, "split": 0, "rejected": 0, "storage_failed": 0}
    _storage_failed = 0
    # W6 — the extractor's stated confidences, held until the turn's record
    # exists: each row names a claim read out of the answer, so a turn whose
    # record cannot be written accrues none.
    _stated: list[tuple] = []
    extraction_failed = False
    # C-38 — the extraction window, declared: what the extractor was given
    # and whether the answer was longer than it. The size is unchanged.
    extraction_input_chars = 0 if structured.answer_empty else min(len(answer_text), EXTRACTION_MAX_CHARS)
    extraction_input_truncated = (not structured.answer_empty) and len(answer_text) > EXTRACTION_MAX_CHARS
    if not extract:
        # MCP-2 — no extraction under MCP: not attempted, so not "failed"
        # and not "0 found" either. None on the record's context and the
        # window undeclared, never defaulted.
        extraction_failed = None
        extraction_input_chars = None
        extraction_input_truncated = None
    if extract and active_provider is not None and not structured.answer_empty:
        _result = await extract_triples(assistant_msg["content"], active_provider)
        extraction_failed = _result is None
        extracted = _result or []

        # Fix 2 — persist extracted triples to the KB with Granularity.TURN.
        # Seeded triples remain Granularity.TOPIC (handled in seeder.py); the
        # extraction path is the session-level granularity bucket.
        # We add via the same store the retriever uses, so /kb/stats and
        # downstream retrieval immediately see these turn-scoped facts.
        if extracted and retriever is not None:
            for et in extracted:
                # P.2 — one candidate may encode several claims. A list object
                # is n facts in one row; a `None` object usually means the
                # predicate swallowed the object, and those rows are
                # retrievable ONLY by subject because search_terms never
                # searches the relation. Decomposition offers the parts; the
                # gate still judges each on its own merits and `add()` stays
                # the single chokepoint. Nothing is sanitised — the original
                # is not stored altered, it is read as what it encodes, and
                # the count is declared in `done`.
                _parts = decompose(et.subject, et.relation, et.object)
                if _parts:
                    _outcomes["split"] += 1
                    for _d in _parts:
                        try:
                            _dbid = retriever.store.add(
                                subject     = _d.subject,
                                relation    = _d.relation,
                                object      = _d.object,
                                weight      = max(0.05, min(1.0, et.confidence)),
                                tier        = 2,
                                granularity = Granularity.TURN,
                                source      = "extraction",
                            )
                            _stored += 1
                            _decomposed += 1
                        except BeliefRejected as rej:
                            _rejections.append({
                                "reason": f"{rej.reason} (after decomposition)",
                                "subject": _d.subject})
                        except Exception as e:
                            # BA fix 6 — a part that could not be stored is
                            # counted, not dropped.
                            _storage_failed += 1
                            _loop_logger.warning("storage failed for a decomposed "
                                                 "part: %s: %s", type(e).__name__, e)
                    continue
                try:
                    _bid = retriever.store.add(
                        subject     = et.subject,
                        relation    = et.relation,
                        object      = et.object,
                        weight      = max(0.05, min(1.0, et.confidence)),
                        tier        = 2,
                        granularity = Granularity.TURN,
                        source      = "extraction",
                    )
                    # Calibration accrual: the extractor's stated confidence,
                    # resolvable against an outcome later. Append-only; never
                    # affects the turn.
                    _stored += 1
                    _outcomes["stored"] += 1
                    _stated.append((et.confidence, _bid,
                                    {"turn": state.turn_count + 1,
                                     "subject": et.subject,
                                     "relation": et.relation}))
                except BeliefRejected as rej:
                    _outcomes["rejected"] += 1
                    # K.2 / I-6 — a rejection is DECLARED, never a silent drop.
                    #
                    # This `except` used to be a bare `except Exception: pass`
                    # ("persistence is best-effort"), which would have
                    # swallowed the gate whole: every refused belief would
                    # vanish with no count and no log, which is the exact
                    # defect class the gate exists to end. The specific catch
                    # comes FIRST so a rejection can never fall through to the
                    # blanket handler below.
                    _rejections.append({
                        "reason":  rej.reason,
                        "field":   rej.field,
                        "subject": et.subject,
                        "relation": et.relation,
                        "object":  et.object,
                    })
                    _loop_logger.info(
                        "extraction gate rejected (%s on %s): %s | %s | %s",
                        rej.reason, rej.field, et.subject, et.relation, et.object)
                except Exception as e:
                    # Persistence is best-effort — never let it abort the turn.
                    # Still reached for genuine storage failures, which are a
                    # different thing from a refused belief and must not be
                    # reported as one. BA fix 6: COUNTED and declared in
                    # `done.extraction_gate.storage_failed`; this used to be
                    # a bare `pass`, so `extracted 5, stored 2` with no reason
                    # was the only trace.
                    _storage_failed += 1
                    _outcomes["storage_failed"] += 1
                    _loop_logger.warning("storage failed for an extracted belief: "
                                         "%s: %s", type(e).__name__, e)

    _admission_stack.close()          # v3.1 Phase 1 — the admission scope ends with extraction

    # Stage 6 (Phase 9): contradiction detection — scan within this turn's
    # extraction AND against the prior turn's extraction (state.last_extraction).
    contradictions = []
    if contradiction is not None and extracted:
        prior_triples = []
        for prev in state.last_extraction:
            if isinstance(prev, tuple) and len(prev) >= 3:
                prior_triples.append(prev[:3])
        contradictions = (
            contradiction.scan_recent(e.as_tuple() for e in extracted)
            + contradiction.scan_against(
                (e.as_tuple() for e in extracted), prior_triples,
            )
        )

    # Stage 6.5 (P3 / L2): answer-lineage record — which beliefs, at what
    # evidential weight and tier, with which surfaced contradictions,
    # produced this response. Chat path writes kb.db only; KAIROS
    # digest-seals the record into the chain later (write-topology rule).
    # F.5 — which of the injected beliefs did the answer actually use? This
    # did not exist before: `injected` was built straight from `hits`, so the
    # record said what was placed in front of the model and nothing about what
    # the model did with it. The flag is recorded here (and so is covered by
    # the record hash and, later, the chain seal) as well as driving
    # reinforcement below.
    cited_belief_ids = cited_ids(hits, assistant_msg["content"])
    reinforce_mode_used = reinforce_mode()
    reinforce_ids = (
        cited_belief_ids if reinforce_mode_used == "citation"
        else {rt.triple.id for rt in hits} if reinforce_mode_used == "retrieval"
        else set()
    )

    lineage_id: str | None = None
    record_hash: str | None = None          # CP — the fire row's binding key
    _record_error: str | None = None        # W6 — the class name of a failed write
    if lineage is not None:
        try:
            _rec = lineage.record_turn(
                lineage_id=handle.lineage_id,                   # W10b (D4): minted at the turn's start
                scope=scope,
                query=user_message,
                injected=[{
                    "belief_id":           rt.triple.id,
                    "weight_at_injection": round(rt.score, 6),
                    "tier_at_injection":   rt.triple.tier,
                    # I-7 — "injected" and "cited" are different claims and
                    # must not share a name. See the note in F.5's report on
                    # I-4, which is *called* citation-confers-durability but
                    # is *implemented* as injection-confers-durability.
                    "cited":               rt.triple.id in cited_belief_ids,
                } for rt in hits],
                contradictions=[c.to_dict() for c in contradictions],
                # MCP-2 — under MCP the client's model, as the client named
                # it (or `unknown`): never the console's active model, which
                # names a model NEXUS did not run.
                model_id=(model_id if model_id is not None else state.active_model),
                # C-38 — `response_text` stays the content stream as delivered
                # (response_hash's continuity); the answer and the reasoning
                # are hashed and counted apart. The record is the ONE reader
                # of the reasoning (an AST guard holds it); no reasoning text
                # enters the sealed record.
                response_text=structured.raw,
                answer_text=structured.answer,
                reasoning_text=structured.reasoning,
                answer_empty_reason=structured.answer_empty_reason,
                # MCP-2 — under MCP NEXUS sent no prompt: the prefix hash is
                # sha256("") and `request_hash` covers the block only, both
                # declared by `record_scope`. The console's values are
                # unchanged.
                prompt_prefix=(STABLE_SYSTEM_PREFIX if record_scope != MCP_BLOCK else ""),
                # AW / I-6 — what the model was told, outside the hashed body:
                # which render, a hash of the block that went (the system
                # message minus the stable prefix — exactly what was sent, by
                # construction), and the cortex pairs it rendered.
                context={
                    "render":     render_version(),
                    "hash":       context_hash(system_content[len(STABLE_SYSTEM_PREFIX):]),
                    "activation": [list(r) for r in rendered_activation],
                    # BA fix 6 — the record carries the same declaration.
                    "extraction_failed": extraction_failed,
                },
                # AU — the flag's producer, named on the record.
                citation_test=citation_test_version(),
                request_hash=(request_hash if record_scope != MCP_BLOCK
                              else context_hash(dynamic_block)),       # BA fix 2; MCP-2 block-only
                record_scope=record_scope,                             # MCP-2
                work_unit_id=_unit_id,                         # v3.1 Phase 1
                accountable_identity=_accountable,
                identity_mechanism=identity_mechanism,         # W4 — a parameter, never the handle
                # W6 — two decision fields, read at the write: the envelope in
                # force (a read that fails here fails the write, declared) and
                # the forecast's turn-level score, with no ledger write.
                envelope_version=_envelope_in_force(retriever),
                forecast=(forecast_record_block(citation_forecast, hits, cited_belief_ids)
                          if citation_forecast is not None else None),
                boundary_asserted=_boundary,                   # v3.1 Phase 1b
                boundary_asserted_by=_asserted_by,
                attribution_flags=_flags,
                learning_mode=learning_declaration()["state"],   # LS — read at write time
                # W0b — the replay capture and the exact request bytes.
                **_replay_capture_args(handle, capture, record_scope),
            )
            lineage_id = _rec.lineage_id
            record_hash = _rec.record_hash          # CP — bind the fire row
        except Exception as e:
            # W6 — the recorder fails closed. Before it, this handler logged
            # and the turn went on with `lineage_id: null`. The text of the
            # error goes to the log only: it can carry a path or a URL.
            _loop_logger.warning("lineage record failed: %s: %s",
                                 type(e).__name__, e)
            _record_error = type(e).__name__
    if _record_error is not None:
        # The turn ends here, declared. Nothing after the record step runs
        # (no junction rows, no accrual, no forecast resolve, no capture, no
        # reinforcement, no Hebbian update), the state does not advance, and
        # the answer's frames are never built. What extraction admitted
        # before the write stays in the store (Law 4) and is counted.
        _record_failures["count"] += 1
        _record_failures["last_reason"] = _record_error
        yield sse_event("record_failed", {
            "turn": state.turn_count + 1,
            "reason": _record_error,
            "answer_released": held_tokens is None,
            "claims_admitted": len(set(_admitted)),
            "contract_version": RECORD_FAILED_CONTRACT_VERSION,
        }), state
        return
    # W6 — the extractor's stated confidences and the boundary act, now that
    # the record exists and BEFORE the first frame is released: a client that
    # stops reading during the release (Starlette closes the generator) must
    # not leave a record whose act was never made (W6's delta check, D4).
    for _conf, _bid, _ctx in _stated:
        _log_confidence("triple", _conf, belief_id=_bid, context=_ctx)

    # W6 — the boundary act, after the record that asserted it: a close or a
    # reopen names this record (`asserted_on`) and, for a close, the unit's
    # record ids now include it. The act's outcome replaces the first half's
    # plan on `done`; an act that cannot be written is declared, never
    # raised (the record exists; the held answer is released after this).
    if work_units is not None and _unit_id and handle.boundary:
        try:
            _unit_row, _attribution = work_units.apply_boundary_for_turn(
                _unit_id, _attribution, boundary=handle.boundary, actor=handle.asserted_by,
                accountable=handle.accountable, asserted_on=lineage_id,
                identity_mechanism=identity_mechanism)
            _flags = list(_attribution.get("flags") or [])
        except Exception as e:
            _loop_logger.warning("work-unit boundary act failed: %s: %s", type(e).__name__, e)
            _attribution = {**{k: v for k, v in _attribution.items()
                               if k not in ("unit_closed", "unit_reopened")},
                            "unit_closed": False, "boundary_not_applied": "write_failed"}

    # The record exists (or no recorder is composed): the answer's frames go
    # out now, in the order they always had. `held_tokens` is the console's
    # held answer; under `live`, and under MCP, there is none to release.
    for _text in (held_tokens or ()):
        yield sse_event("token", {"t": _text}), state
    if structured.answer_empty:
        yield sse_event("token", {"t": ANSWER_EMPTY_CONSOLE_MESSAGE}), state
    if extracted:
        yield sse_event("triples_extracted", [
            e.to_dict() for e in extracted
        ]), state
    for c in contradictions:
        yield sse_event("contradiction_found", c.to_dict()), state

    # P.4 — calibration accrual, now that `lineage_id` exists.
    #
    # SAMPLING IS UNCHANGED, deliberately. `top_k(20)` sorts descending and
    # whether that is ceiling-biased at real scale is unmeasured; `rank` and
    # `saturated` are what make the bias VISIBLE so a later measurement can
    # decide. Adding them is not the same as acting on them.
    _accrue_calibration(hits, activation, state, lineage_id)

    # v3.1 Phase 1 — the junction: one row per claim `add()` admitted this
    # turn, keyed on the row id, carrying the answer it was admitted during.
    # Written after the lineage record so `lineage_id` is real; never at
    # citation (that would import the flag's under-report into attribution).
    _junction_rows = 0
    if work_units is not None and _unit_id and _admitted:
        try:
            _junction_rows = work_units.admit(_admitted, _unit_id,
                                              turn=state.turn_count + 1, lineage_id=lineage_id)
        except Exception as e:                        # never break a turn
            _loop_logger.warning("work-unit admission failed: %s: %s", type(e).__name__, e)

    # AF.1 — score the committed forecast now that the outcome exists.
    # `cited_belief_ids` is the F.5 flag set for THIS turn; it exists
    # whenever hits do, and the scorer excludes-by-name every turn it cannot
    # honestly score (flags absent, nothing injected, nothing cited). The
    # prominence null is computed inside score_forecast for the same turn,
    # mandatorily — a forecast score without its control is not reportable.
    forecast_block = None
    if citation_forecast is not None:
        try:
            forecast_block = score_forecast(
                citation_forecast, hits, cited=cited_belief_ids,
                lineage_id=lineage_id)
        except Exception as e:                        # never break a turn
            _loop_logger.warning("forecast scoring failed: %s: %s",
                                 type(e).__name__, e)

    # AO — the capture instrument. AFTER the lineage write on purpose: the
    # row carries lineage_id (the join key labels need) and is verifiable
    # for free against the record's response_hash. Off by default; when off
    # this call reads one env var and returns. Derived, not record.
    if lineage_id:
        capture_turn(lineage_id=lineage_id,
                     response_text=assistant_msg["content"],
                     reasoning_text=structured.reasoning,             # C-38 — stored apart, bound to its hash
                     hits=hits, cited_ids=cited_belief_ids,
                     citation_test=citation_test_version())

    # Stage 7-aux (Phase 9): curiosity surfacing — graph topology asks questions.
    # AX.1 — the pass runs on the KAIROS idle cycle now (the pack's recorded
    # decision: curiosity is proactive, an idle/reflective pass) and reaches
    # the client through the bridge on the next turn. `turn_curiosity` is
    # empty unless NEXUS_CURIOSITY_PLACEMENT=turn, the rollback, in which
    # case this is exactly the pre-AX.1 call and frame.
    curious_nodes = turn_curiosity(curiosity, hippocampus, top_k=3)
    for cn in curious_nodes:
        yield sse_event("curiosity_surfaced", cn.to_dict()), state

    # Stage 7.5 (Phase 6.A): RAGAS-equivalent eval, logged per turn.
    if evaluator is not None:
        fired_ids = set(activation.activations.keys()) if activation else set()
        m = evaluator.evaluate(
            turn=state.turn_count + 1,
            query=user_message,
            response=assistant_msg["content"],
            hits=hits,
            fired_node_ids=fired_ids,
        )
        # BB Part 1 — a metric with no denominator is null on the frame, not
        # a number; the frontend renders null as "not computed".
        def _r(v):
            return None if v is None else round(v, 3)
        yield sse_event("eval_metric", {
            "turn": m.turn,
            "answer_relevancy": _r(m.answer_relevancy),
            "faithfulness": _r(m.faithfulness),
            "context_precision": _r(m.context_precision),
            "composite": _r(m.composite()),
            "n_injected_triples": m.n_injected_triples,
        }), state

    # Stage 4.5 (Phase 7+9): prediction error — Phase 9 uses extracted triples
    # to populate the "actual" activation set; falls back to keyword matching
    # when extraction returned nothing (e.g. provider unavailable).
    error = None
    surprise_magnitude = 0.0
    if predictive is not None and hippocampus is not None and activation is not None:
        from backend.cortex.propagation import ActivationMap as _AM
        if extracted:
            actual_ids: set[str] = set()
            for e in extracted:
                actual_ids.add(e.subject); actual_ids.add(e.object)
        else:
            actual_ids = set(extract_terms(assistant_msg["content"], max_terms=64))
        actual = _AM(activations={
            nid: max(activation.activations.get(nid, 0.5), 0.5)
            for nid in actual_ids
        })
        predicted = _AM(activations={
            nid: activation.activations.get(nid, 0.0) for nid in predicted_top
        })
        error = predictive.compute_error(predicted, actual)
        surprise_magnitude = error.surprise_magnitude
        # Calibration accrual: predictions are self-resolving within the turn —
        # a predicted node either appeared in the actual set (confirmed) or did
        # not (missed). Stated confidence is the node's predicted activation.
        # P.4 quarantined the accrual these rows used to feed, and AF.1
        # filled the reserved `prediction` mechanism with the citation
        # forecast (the forecast_citations call above, before retrieval) —
        # NOT by un-quarantining this path. The map read here is seeded from
        # the retrieved hits, so rows from it would still be a different
        # computation wearing the forecast's name; `predict()` stays
        # quarantined in backend/cortex/predictive.py, and
        # test_no_path_emits_the_reserved_prediction_mechanism now enforces
        # that forecast.py is the mechanism's only writer.
        yield sse_event("prediction_error", {
            "high_surprise":  list(error.high_surprise)[:10],
            "confirmed":      list(error.confirmed)[:10],
            "missed":         list(error.missed)[:10],
            "magnitude":      round(error.surprise_magnitude, 3),
        }), state

    # Stage 5: Hebbian update — Phase 7 modulated by prediction error when present.
    if hippocampus is not None and hebbian is not None and activation is not None:
        result = hebbian.update(hippocampus, activation, error=error)
        # H.3b — declared as `hebbian_update` in CLAUDE.md's SSE contract and
        # emitted as `hebbian` since it shipped. Pure naming drift on a path
        # that runs every turn: the payload was always correct, the label was
        # not, and the contract member sat in the "never emitted" column
        # because of a missing seven characters. Both UI files already handle
        # the declared name (nexus_memory.html:1248 calls it "spec alias").
        yield sse_event("hebbian_update", {
            "strengthened": result.strengthened, "weakened": result.weakened,
            "formed": result.formed, "pruned": result.pruned,
            "surprise_strengthened":  result.surprise_strengthened,
            "confirmed_strengthened": result.confirmed_strengthened,
            "missed_weakened":        result.missed_weakened,
            # AM / I-6 — what the pairing considered and what it skipped.
            # In topk mode the counts above are DISTINCT edges moved; the
            # pre-AM payload shipped operation counts ~22,000x the edge
            # population under a name then declared as `edges_modified`
            # (I-7; W1 aligned the declaration to these keys).
            "pairing": {"mode": result.pairing_mode, "k": result.k,
                        "nodes_paired": result.nodes_paired,
                        "fired_events": result.fired_events},
            # AN.1 / I-6 — "unavailable" means the I-4 exemption set could
            # not be computed and the prune FAILED CLOSED (pruned is null,
            # nothing was demoted). Declared here because the old failure
            # reached a console log line and nothing else.
            "protection": result.protection_state,
        }), state
        hippocampus.relax_all()

    # Stage 7: atomic state transition
    _now = __import__("time").time()
    # Fix 0 / Part B — non-locked override clears at end of turn.
    next_override = state.model_override if state.override_locked else None
    next_locked   = state.override_locked
    # CHANGE A — shape A on the history path. The carrier gains this answer's
    # entry (labelled with THIS turn's count, carried from here on and never
    # recomputed); every prior answer the next prompt will read is wrapped,
    # cut at a sentence boundary at HISTORY_ANSWER_CAP_CHARS, the runtime's
    # compaction block exempt; the carrier is asserted against the history
    # and pruned to it. NEXUS_HISTORY_WRAP=off is the pre-A path byte for
    # byte — the carrier is kept either way, so the switch is safe to flip.
    if record_scope == MCP_BLOCK:
        # MCP-2 — the history path has no object under MCP (MCP-1 item 5):
        # the client holds the conversation and NEXUS's block is one message
        # in a prompt it never sees. NEXUS keeps no private history of its
        # own, so the cascade never grows one nobody reads.
        _messages, _carrier = state.messages, state.history_carrier
    else:
        _raw_messages = tuple(history + [assistant_msg])
        _carrier = state.history_carrier + (carrier_entry(assistant_msg["content"], state.turn_count + 1),)
        _messages = wrap_prior_answers(_raw_messages, _carrier) if history_wrap_enabled() else _raw_messages
        _carrier = tuple(align_carrier(_messages, _carrier, strict=False))
    new_state = next_state(
        state,
        messages=_messages,
        history_carrier=_carrier,
        graph_context=tuple(
            (rt.triple.subject, rt.triple.relation, rt.triple.object) for rt in hits
        ),
        # session_memory: 4-tuple (source, target, weight, last_fired) so the
        # snip tier can recompute current_weight at compaction time (Fix 3).
        session_memory=tuple(
            (e.source, e.target, round(e.weight, 3),
             e.last_fired if e.last_fired > 0 else _now)
            for e in (hippocampus.edges() if hippocampus else [])
        ),
        predicted_nodes=tuple(predicted_top),
        prediction_error=surprise_magnitude,
        last_extraction=tuple(e.as_tuple() for e in extracted),
        compaction_state=state.compaction_state,    # the last tier's name, a report field
        turn_count=state.turn_count + 1,
        token_count=state.token_count + token_count,
        transition_reason="turn_complete",
        model_override=next_override,
        override_locked=next_locked,
        attachments=(),                       # Fix 9 — single-use, clears at turn end
        # v3.1 Phase 1b — no current unit on the state (nothing inherits);
        # the tier-4 report list is the one unit-shaped field that remains.
        history_work_units=state.history_work_units + ((_unit_id,) if _unit_id else ()),
    )

    # CP — the durable fire capture. After stage 7 so the row reports the
    # REBUILT sizes stage 7 wrote (the S1.5 pair "before entry / after
    # rebuild"); after the lineage write so it binds on `lineage_id` and
    # `record_hash`. Off by default; when off, `fire_observed` is None and
    # this writes nothing (one env read, already made above). Derived, not
    # record; never raises. The observation was taken at THIS turn's
    # assembly, before THIS turn's cascade, so a fire cannot answer its own
    # previous turn's question.
    capture_fire(
        turn=new_state.turn_count, lineage_id=lineage_id, record_hash=record_hash,
        result=cascade, observed=fire_observed,
        sizes_entry=fire_sizes_entry,
        sizes_after=(len(new_state.session_memory), len(new_state.graph_context)),
        block_entry=fire_block_entry, block_sent=dynamic_block,
    )

    # F.5 — reinforcement by citation, not by retrieval.
    #
    # `store.fire()` resets `last_fired`, and therefore `current_weight`, to
    # the stored high-water mark. Firing every retrieved belief every turn
    # closed a loop with no evidence in it: retrieval ranks tier ascending, so
    # a prominent belief was retrieved BECAUSE it was prominent and then
    # strengthened FOR having been retrieved. That is why the 555
    # co-activation rows never decayed — not tier semantics, which F.3 already
    # fixed.
    #
    # Note this touches `triples.fire_count`, which nothing reads for a
    # decision. I-1's counter is `SynapticEdge.fire_count` (neocortex.py:86),
    # a different object on a different layer, and is untouched.
    # B-43 — `reinforced` counts writes MADE; `skipped` counts the attempts
    # the freeze refused (FZ-2). `fire()` reports which, so a frozen turn
    # reads reinforced 0 / skipped n beside `learning: frozen`, not a count
    # of calls that wrote nothing.
    reinforced = suppressed = skipped = 0
    if retriever is not None:
        for rt in hits:
            if rt.triple.id is None:
                continue
            if rt.triple.id in reinforce_ids:
                if retriever.store.fire(rt.triple.id):
                    reinforced += 1
                else:
                    skipped += 1
            else:
                suppressed += 1
        if suppressed:
            _loop_logger.debug(
                "reinforcement[%s]: %d/%d retrieved beliefs reinforced, "
                "%d suppressed as uncited", reinforce_mode_used,
                reinforced, len(hits), suppressed)

    # I-6 — declared, not inferable. An operator reading the turn must be able
    # to see that beliefs were shown to the model and deliberately not
    # strengthened, and under which policy.
    _done_payload = {
        "turn": new_state.turn_count, "tokens": token_count,
        "lineage_id": lineage_id,
        # FZ-2 — a system that is not learning says so on every turn;
        # `contract_version` makes `done` a versioned contract the way the
        # recall route's payload carries its own. 2: `reinforcement.skipped`
        # (B-43, pre-CE). 3: C-38 — `answer_empty`, `extraction_input_chars`,
        # `extraction_input_truncated`, `answer_chars`, `reasoning_chars`.
        "learning": learning_declaration(),
        "contract_version": 4,
        # C-38 — the reply, separated: the answer's length, the reasoning's
        # (null when the reply carried none), and the declared empty-answer
        # state with its reason (only when set). The extraction window is
        # declared, not changed.
        "answer_chars": len(structured.answer),
        "reasoning_chars": (len(structured.reasoning) if structured.reasoning is not None else None),
        "answer_empty": structured.answer_empty,
        **({"answer_empty_reason": structured.answer_empty_reason} if structured.answer_empty else {}),
        "extraction_input_chars": extraction_input_chars,
        "extraction_input_truncated": extraction_input_truncated,
        # v3.1 Phase 1 / 1b — which unit the turn was attributed to, how,
        # and the junction rows written; whether this request created the
        # unit and whether it closed it (always present, booleans); and,
        # only when set, the declarations: `unit_already_closed`,
        # `unit_closed_at_time_of_turn`, `boundary_not_applied`, `flags`
        # (`accountability.missing`). Declared, never refused (B.3).
        "work_unit_id": _unit_id,
        "attribution_status": _unit_status,
        "admitted_to_unit": _junction_rows,
        "unit_first_seen": bool(_attribution.get("unit_first_seen")),
        "unit_closed": bool(_attribution.get("unit_closed")),
        **({k: v for k, v in _attribution.items()
            if k in ("unit_already_closed", "unit_closed_at_time_of_turn", "boundary_not_applied",
                     "unit_reopened", "unit_not_closed")}),       # W6 — the reversal's two declarations
        **({"flags": _flags} if _flags else {}),
        "reinforcement": {
            "mode":       reinforce_mode_used,
            # AU — which citation test produced `cited` this turn.
            "citation_test": citation_test_version(),
            "injected":   len(hits),
            "cited":      len(cited_belief_ids),
            "reinforced": reinforced,       # writes made (0 under frozen)
            "skipped":    skipped,          # B-43 — attempts the freeze refused
            "suppressed": suppressed,
        },
        # K.2 / I-6 — what the ingest gate refused this turn, and why. A
        # rejection that only appears in a log is a silent drop to anyone
        # watching the stream, and "extracted 5" beside "stored 2" with no
        # explanation is worse than either number alone.
        "extraction_gate": {
            # BA fix 6 — null when the extractor FAILED (not measured), 0
            # when it answered and produced nothing (measured).
            "extracted": None if extraction_failed else len(extracted),
            "extraction_failed": extraction_failed,
            "stored":    _stored,
            # P.2 — how many of `stored` came from splitting a candidate that
            # would otherwise have been refused. Declared because one-in-
            # three-out is exactly the kind of count that should never be
            # silent (I-6): an operator reading `extracted 2, stored 4` needs
            # to know why, or the numbers look like a defect.
            "decomposed": _decomposed,
            "rejected":  len(_rejections),
            "reasons":   sorted({r["reason"] for r in _rejections}),
            # BA fix 6 — storage failures (whole candidates and decomposed
            # parts) and the per-candidate outcomes whose sum is `extracted`.
            "storage_failed": _storage_failed,
            "outcomes":  dict(_outcomes),
        },
        # AF.1 — the citation forecast's declared block: what was committed
        # before retrieval, its term coverage, and precision@k beside the
        # prominence null when the turn was scorable — with the exclusion
        # named when it was not. Absent entirely (not null) when the
        # forecast is off, so `off` is byte-identical to the pre-AF.1 frame.
        **({"citation_forecast": forecast_block}
           if forecast_block is not None else {}),
    }
    if outcome is not None:
        # MCP-2 — for a caller that does not consume `done` (the MCP record
        # tool): the record's identity and the state to carry forward.
        outcome.update({
            "lineage_id": lineage_id, "record_hash": record_hash,
            "cited": sorted(cited_belief_ids), "injected": [rt.triple.id for rt in hits],
            "state": new_state, "done": _done_payload,
        })
    yield sse_event("done", _done_payload), new_state


async def run_turn(
    state: NexusLoopState,
    user_message: str,
    *,
    retriever: TripleRetriever | None = None,
    hippocampus: Hippocampus | None = None,
    hebbian: HebbianUpdater | None = None,
    summariser: Summariser | None = None,
    procedural: ProceduralMemory | None = None,
    kernel: ModelKernel | None = None,
    evaluator: TurnEvaluator | None = None,
    vector_store: VectorStore | None = None,
    predictive: PredictiveCortex | None = None,
    provider: LLMProvider | None = None,
    contradiction: ContradictionDetector | None = None,
    curiosity: CuriosityEngine | None = None,
    search_tool: WebSearchTool | None = None,
    search_consents: dict | None = None,
    pending_alerts: list | None = None,
    lineage: "LineageStore | None" = None,
    scope: str = "default",
    work_units: "WorkUnitStore | None" = None,
    session_id: str = "default",
    work_unit: str | None = None,
    work_unit_type: str | None = None,
    accountable: str | None = None,
    actor: dict | None = None,
    decision_boundary: str | None = None,
    identity_mechanism: str | None = None,
) -> AsyncIterator[tuple[str, NexusLoopState]]:
    """The console's turn: `prepare_turn`, NEXUS's own model, `complete_turn`.

    MCP-2 — a thin composition of the two halves with the model call between
    them, pinned byte for byte against the unsplit loop (the same frames in
    the same order, the same requests, the same records). The signature is
    the unsplit loop's plus W4's `identity_mechanism`, which goes to
    `complete_turn` as a parameter (the record's, never the handle's); left
    out, it is the default and every pin above holds.
    """
    handle: TurnHandle | None = None
    given = state      # W6 — what a turn whose record fails hands back
    async for frame, payload in prepare_turn(
            state, user_message,
            retriever=retriever, hippocampus=hippocampus, summariser=summariser,
            procedural=procedural, kernel=kernel, vector_store=vector_store,
            predictive=predictive, provider=provider, search_tool=search_tool,
            search_consents=search_consents, pending_alerts=pending_alerts,
            scope=scope, work_units=work_units, session_id=session_id,
            work_unit=work_unit, work_unit_type=work_unit_type,
            accountable=accountable, actor=actor, decision_boundary=decision_boundary,
            identity_mechanism=identity_mechanism):
        if frame is None:
            handle = payload
            continue
        yield frame, payload
    assert handle is not None, "prepare_turn ended without a handle"
    state = handle.state

    # Stage 3: stream tokens. The answering context is compaction's
    # TOKEN_BUDGET — the number the cascade manages the prompt toward —
    # stated here rather than inherited from the provider's ceiling. Equal to
    # Law 5's ceiling by design; the two are different facts and a sentinel
    # asserts budget <= ceiling.
    #
    # C-38 — the reply arrives separated at the provider boundary: ANSWER
    # chunks are what every consumer below receives and what the console
    # streams; REASONING chunks are kept for the record and reach nothing
    # else — not the console, not history, not extraction, not the citation
    # detector. `reply.raw` is the content stream as delivered, which is what
    # `response_hash` has always covered.
    assistant_chunks: list[str] = []
    reasoning_chunks: list[str] = []
    # W6 — a third stream vessel: with a recorder composed and the release
    # position closed, the answer's chunks are held here and released by
    # `complete_turn` after its record is written.
    held_tokens: list[str] | None = (
        [] if lineage is not None and answer_release() == AFTER_RECORD else None)
    token_count = 0
    reply = reply_stream(handle.active_provider, state.active_model, list(handle.request_messages),
                         num_ctx=TOKEN_BUDGET)
    async for chunk in reply:
        if chunk.kind == REASONING:
            reasoning_chunks.append(chunk.text)
            continue
        assistant_chunks.append(chunk.text)
        token_count += 1
        if held_tokens is not None:
            held_tokens.append(chunk.text)
            continue
        yield sse_event("token", {"t": chunk.text}), state

    answer_text = "".join(assistant_chunks)
    reasoning_text = "".join(reasoning_chunks) or None
    structured = Reply(answer=answer_text, reasoning=reasoning_text, raw=reply.raw, unclosed=reply.unclosed)

    # W3 part 2 — a provider that reports the model that answered (the api
    # providers declare `model_id_prefix`) names the record with it, under
    # its prefix (`openai:gpt-4o-2024-08-06`), so a record made against an
    # API can never be read as a local tag's. Every other provider's record
    # names `state.active_model`, exactly as before.
    recorded_model, capture = _served_model_id(handle.active_provider, state.active_model, reply)
    async for frame, st in complete_turn(
            handle, structured, token_count=token_count,
            retriever=retriever, hippocampus=hippocampus, hebbian=hebbian,
            evaluator=evaluator, predictive=predictive, contradiction=contradiction,
            curiosity=curiosity, lineage=lineage, work_units=work_units,
            model_id=recorded_model, capture=capture,                  # W0b, W3
            identity_mechanism=identity_mechanism,
            held_tokens=held_tokens):                    # W4
        # W6 — a turn whose record could not be written did not happen: its
        # frame carries the state this turn was given, never the first
        # half's (the routed model, the request's attachments).
        yield frame, (given if frame.startswith("event: record_failed\n") else st)


def _served_model_id(provider, active_model: str, reply) -> tuple:
    """(the record's `model_id` or None for the default, the capture). Under
    a provider with a `model_id_prefix`: the model the stream REPORTED under
    the prefix; when the stream reported none, the model requested, and the
    capture notes say so (never guessed to be what served)."""
    prefix = getattr(provider, "model_id_prefix", None)
    capture = reply.capture
    if not prefix:
        return None, capture
    served = getattr(reply, "served_model", None)
    if served:
        return f"{prefix}:{served}", capture
    if capture is not None:
        import dataclasses
        capture = dataclasses.replace(capture, notes={
            **capture.notes,
            "model_id": "the stream reported no model; model_id names the model requested"})
    return f"{prefix}:{active_model}", capture
