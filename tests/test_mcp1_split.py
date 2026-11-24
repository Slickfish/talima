"""MCP-1 — NEXUS as an MCP server, the establish. Characterisation pins.

Under MCP the turn is split at the model call across a model NEXUS does not
run, and the client decides when to call tools. These tests pin what the
code says about that split, so a later change to what crosses the line, to
the record's fields, or to the route surface fails here and the MCP design
is revisited rather than drifting.

Nothing under backend/ changes. Everything here is derived by AST from the
tracked source, traced on CE's rig, or driven through the existing public
functions (`assemble_context`, `record_turn`, `cited_ids`) with a scripted
stub client. The rig is not the instance and the stub is not Claude: the
coverage numbers characterise the harness, and the test says so.

PLATFORM is stamped on every printed line (sys.platform).
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from backend.kb.citation import cited_ids, citation_test_version
from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import CLASS_ORDER, SOURCE_CLASS, UNKNOWN_CLASS, TripleStore
from backend.loop.agentic_loop import MAX_INJECTED_TRIPLES, assemble_context
from backend.loop.compaction import (STABLE_SYSTEM_PREFIX, TIER_1_SNIP_AT, estimate_tokens,
                                     estimate_tokens_text)
from backend.loop.prompt_render import MAX_CORTEX_LINES, context_hash, render_version
from backend.loop.state import NexusLoopState
from backend.memory.hippocampus import Hippocampus
from backend.memory.lineage import LineageStore
from tests._tracked import tracked_backend
from tests.fixtures.ce_rig import CERig

ROOT = Path(__file__).resolve().parent.parent
LOOP = ROOT / "backend" / "loop" / "agentic_loop.py"
LINEAGE = ROOT / "backend" / "memory" / "lineage.py"


# ---------------------------------------------------------------------------
# 1. The split — the state that crosses the model call, derived by AST
# ---------------------------------------------------------------------------

def _run_turn_fn():
    tree = ast.parse(LOOP.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_turn")


def crossing_state() -> dict:
    """MCP-2 split the loop: the state that crosses the model call is now
    HELD BY NAME on `TurnHandle` (backend/loop/agentic_loop.py), so this
    derives it from the handle's fields — the thing a later change must edit
    — and from `run_turn`'s own body, where only the reply crosses back."""
    tree = ast.parse(LOOP.read_text(encoding="utf-8"))
    handle = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "TurnHandle")
    fields = [n.target.id for n in handle.body if isinstance(n, ast.AnnAssign)]
    run = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_turn")
    call = next(n.lineno for n in ast.walk(run)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "reply_stream")
    consumed = next(n.end_lineno for n in ast.walk(run)
                    if isinstance(n, ast.AsyncFor) and isinstance(n.iter, ast.Name) and n.iter.id == "reply")
    # What only the model call produces, read after the stream in run_turn.
    stores: dict[str, list[int]] = {}
    loads: dict[str, list[int]] = {}
    for n in ast.walk(run):
        if isinstance(n, ast.Name):
            (stores if isinstance(n.ctx, ast.Store) else loads).setdefault(n.id, []).append(n.lineno)
    model_output = {name for name, ls in stores.items()
                    if any(call <= s <= consumed for s in ls) and any(r > consumed for r in loads.get(name, []))}
    complete = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "complete_turn")
    reply_attrs = sorted({f"{n.value.id}.{n.attr}" for n in ast.walk(complete)
                          if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                          and n.value.id in ("reply", "structured")})
    return {"model_call_line": call, "consumed_line": consumed, "handle_fields": fields,
            "model_output": model_output, "reply_attrs": reply_attrs}


# PINNED: the handle a `retrieval_id` holds between the two MCP calls — MCP-1's
# crossing set by name (leading underscores dropped), minus the two stream
# vessels (the model call's own; under MCP there is no stream), plus the four
# the second half needs from the first and the handle's own two keys.
PINNED_CROSSING = {
    # the retrieval and the block (item 1's "injected set, rendered block")
    "hits", "activation", "rendered_activation", "dynamic_block", "system_content",
    # the request's hash and its inputs (the history is the console's; under MCP none)
    "request_hash", "history",
    # the forecast row (its id lives inside the object)
    "citation_forecast",
    # prediction
    "predicted_top",
    # the cascade's result and the fire capture's entry facts (CP)
    "cascade", "fire_observed", "fire_sizes_entry", "fire_block_entry",
    # the work unit and attribution (v3.1 Phase 1/1b)
    "unit_id", "unit_status", "attribution", "accountable", "asserted_by", "boundary", "flags",
    # the provider the second half extracts with (item 4's question)
    "active_provider",
}
HANDLE_CARRIED = {"state", "user_message", "scope", "request_messages",
                  # W10b (D4): the id the record will carry, minted at the turn's start so an action the first
                  # half takes (a search) can name it; the second half hands it to `record_turn`
                  "lineage_id"}
HANDLE_KEYS = {"retrieval_id", "created_at"}
# The stream vessels (`assistant_chunks`, `reasoning_chunks`) are bound before
# the call and filled during it; what the call BINDS is the reply and the
# token counter.
PINNED_MODEL_OUTPUT = {"reply", "token_count"}


def test_the_split_crossing_state_is_pinned():
    """Item 1, after MCP-2 built the split. The handle's fields are exactly
    MCP-1's crossing set (by name) plus what the second half needs from the
    first and the handle's own keys; `run_turn` produces only the reply and
    its stream vessels between the halves; and `complete_turn` reads the
    reply as `answer`, `reasoning`, `raw`, `answer_empty` and
    `answer_empty_reason`. Under MCP the answer arrives from the client;
    reasoning, raw and unclosed do not exist. The learning declaration does
    not cross: LS reads it at the record write."""
    d = crossing_state()
    print(f"\nMCP1-1 ({sys.platform}) model call at :{d['model_call_line']}, stream consumed by :{d['consumed_line']}")
    print(f"   handle fields: {d['handle_fields']}")
    print(f"   model output in run_turn: {sorted(d['model_output'])}; reply attrs read by complete_turn: {d['reply_attrs']}")
    assert set(d["handle_fields"]) == PINNED_CROSSING | HANDLE_CARRIED | HANDLE_KEYS, (
        f"the handle changed: +{set(d['handle_fields']) - (PINNED_CROSSING | HANDLE_CARRIED | HANDLE_KEYS)} "
        f"-{(PINNED_CROSSING | HANDLE_CARRIED | HANDLE_KEYS) - set(d['handle_fields'])}")
    assert set(d["model_output"]) == PINNED_MODEL_OUTPUT, d["model_output"]
    assert set(d["reply_attrs"]) == {"structured.answer", "structured.answer_empty",
                                     "structured.answer_empty_reason", "structured.raw", "structured.reasoning"}
    assert "learning_declaration" not in d["handle_fields"]
    src = LOOP.read_text(encoding="utf-8")
    assert "learning_mode=learning_declaration()" in src


def test_the_model_call_sits_between_the_cascade_and_extraction_on_the_rig(tmp_path, monkeypatch):
    """Item 1, traced: on CE's run-1 rig the first provider call is after the
    cascade and the request hash, and before extraction and the record. That
    is the line MCP draws."""
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    rig = CERig(tmp_path / "trace", pairs=42, answer_chars=800, kb_lines=17, user_chars=76,
                seed_session_memory=True)
    events = rig.trace_turn()
    rig.close()
    order = [e["func"] for e in events]
    first = lambda name: next(i for i, f in enumerate(order) if f == name)   # noqa: E731
    print(f"\nMCP1-1b ({sys.platform}) traced: cascade@{first('backend.loop.compaction.run_cascade')} "
          f"request_hash@{first('backend.loop.prompt_render.request_hash_of')} "
          f"model@{first('tests.fixtures.ce_rig.stream_chat')} extraction@{first('backend.cortex.extraction.extract_triples')} "
          f"record@{first('backend.memory.lineage.record_turn')}")
    assert first("backend.loop.compaction.run_cascade") < first("backend.loop.prompt_render.request_hash_of") \
        < first("tests.fixtures.ce_rig.stream_chat") < first("backend.cortex.extraction.extract_triples") \
        < first("backend.memory.lineage.record_turn")


# ---------------------------------------------------------------------------
# 2. The record's claims under the split — every field classified
# ---------------------------------------------------------------------------

UNCHANGED, CHANGED, UNDEFINED = "unchanged", "changed_in_meaning", "undefined"

# PINNED by field name: a field added to LineageRecord fails here until it is
# classified. The reason is the claim the field makes and who makes it.
RECORD_CLAIMS = {
    "lineage_id":           (UNCHANGED, "minted by NEXUS at the record write"),
    "ts":                   (UNCHANGED, "NEXUS's clock at the record write, as today"),
    "query_hash":           (UNCHANGED, "over the question the client handed to retrieve; the console was a client too"),
    "injected":             (UNCHANGED, "the set NEXUS retrieved; cited flags by NEXUS's own citation test over the client's answer text"),
    "contradictions":       (UNDEFINED, "surfaced by extraction; empty forever when NEXUS does not extract (option c)"),
    "model_id":             (CHANGED,   "today the model NEXUS ran; under MCP the client's model, reported by the client or unknown"),
    "prompt_prefix_hash":   (CHANGED,   "today the stable prefix NEXUS sent; under MCP NEXUS sends no prompt — only the block it returned"),
    "response_hash":        (CHANGED,   "over the answer text as the client sent it: the client's claim, not NEXUS's observation"),
    "record_hash":          (UNCHANGED, "the seal's mechanism; its members' meanings move with the rows above"),
    "sealed_block":         (UNCHANGED, "KAIROS's seal — holds iff the idle cycle runs in the MCP process; NULL forever if it does not"),
    "scope":                (UNCHANGED, "a caller-supplied label, recorded literally"),
    "citation_test":        (UNCHANGED, "NEXUS's own instrument version"),
    "context":              (UNCHANGED, "render, hash and activation of the block NEXUS returned — pins exactly what NEXUS handed over"),
    "request_hash":         (CHANGED,   "today the whole request as sent; under MCP it can cover only NEXUS's block, never the client's prompt"),
    "work_unit_id":         (UNCHANGED, "caller-supplied, recorded literally"),
    "accountable_identity": (UNCHANGED, "asserted, never authenticated — from the bundle's configuration instead of the request body"),
    "boundary_asserted":    (UNCHANGED, "caller-supplied, recorded literally"),
    "boundary_asserted_by": (UNCHANGED, "caller-supplied, recorded literally"),
    "attribution_flags":    (UNCHANGED, "derived by NEXUS from the request's own fields"),
    "learning_mode":        (UNCHANGED, "read at the record write (LS), the second half's own read"),
    "answer_hash":          (CHANGED,   "over the answer text as the client sent it: the client's claim"),
    "reasoning_hash":       (UNDEFINED, "no reasoning exists under MCP"),
    "answer_chars":         (CHANGED,   "the length of the text the client sent"),
    "reasoning_chars":      (UNDEFINED, "no reasoning exists under MCP"),
    "answer_empty_reason":  (UNDEFINED, "a provider-boundary declaration; the client sends what it sends"),
    "record_scope":         (UNCHANGED, "MCP-2: the declaration itself — full_turn (NULL) or mcp_block, sealed when non-default"),
    # W0b — the replay capture describes the model NEXUS ran; under MCP the
    # client's model ran outside NEXUS, so the four are NULL and the notes
    # name that as the reason.
    "inference_seed":       (UNDEFINED, "W0b: the seed NEXUS sent; the client's model ran outside NEXUS"),
    "model_digest":         (UNDEFINED, "W0b: the content digest of the model NEXUS ran; not observable for the client's"),
    "inference_config_hash": (UNDEFINED, "W0b: the options NEXUS sent; NEXUS sends none under MCP"),
    "environment_hash":     (UNDEFINED, "W0b: the runtime that ran the model; the client's is not observable"),
    "capture_notes":        (CHANGED,   "W0b: the reason each capture field is NULL; under MCP it names the client's model"),
    # W4 — how `accountable_identity` was established. MCP has no peer and its
    # owner is configuration, so there is no mechanism but the default: NULL
    # (`asserted`) on every mcp_block record, and `verify_scope` names a value.
    "identity_mechanism":   (UNDEFINED, "W4: asserted (NULL) | proxy, sealed when non-default; MCP has no peer and its owner is configuration"),
    # W6 — two decision fields. Both are NEXUS's own reads at the record
    # write, in either scope: the envelope in force in the store, and the
    # score of the forecast NEXUS committed at retrieve against the flags
    # its own citation test put on the answer it was sent.
    "envelope_version":     (UNCHANGED, "W6: the recalibration envelope in force in the store at the record write; NULL under the default"),
    "forecast":             (UNCHANGED, "W6: NEXUS's forecast scored on NEXUS's citation flags over the answer it was sent; NULL when none ran"),
}


def lineage_record_fields() -> list[str]:
    tree = ast.parse(LINEAGE.read_text(encoding="utf-8"))
    rec = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "LineageRecord")
    return [n.target.id for n in rec.body if isinstance(n, ast.AnnAssign)]


def hashed_when_present() -> set[str]:
    """The keys `compute_record_hash` seals only when non-default — the LS
    mechanism a `record_scope` field would ride."""
    tree = ast.parse(LINEAGE.read_text(encoding="utf-8"))
    # W8 — the body the hash is over is built by `hashed_body` (one builder for the hash and the commitments).
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "hashed_body")
    keys: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.If):                 # `if payload.get("<key>") ...:`
            for c in ast.walk(n.test):
                if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value not in ("default", "live"):
                    keys.add(c.value)
        if isinstance(n, ast.For):                # `for key in ("<a>", "<b>"): if payload.get(key): ...`
            for e in ast.walk(n.iter):
                if isinstance(e, ast.Constant) and isinstance(e.value, str):
                    keys.add(e.value)
    return keys


def test_every_record_field_is_classified_under_the_split():
    """Item 2. The field list is derived from `LineageRecord` by AST, never
    typed; every field has exactly one classification; the table is printed.
    The hashed-when-present keys are derived from `compute_record_hash`, and
    the question MCP-2 asks — does the LS mechanism carry `record_scope`? —
    is answered by the shape: every one of those keys enters the hashed body
    only when non-default, so a `record_scope` defaulting to `full_turn`
    recomputes every existing record unchanged."""
    fields = lineage_record_fields()
    assert set(fields) == set(RECORD_CLAIMS), (
        f"unclassified: {set(fields) - set(RECORD_CLAIMS)}; stale: {set(RECORD_CLAIMS) - set(fields)}")
    print(f"\nMCP1-2 ({sys.platform}) the record's {len(fields)} fields under the split:")
    for f in fields:
        c, why = RECORD_CLAIMS[f]
        print(f"   {f:22s} {c:18s} {why}")
    counts = {k: sum(1 for v in RECORD_CLAIMS.values() if v[0] == k) for k in (UNCHANGED, CHANGED, UNDEFINED)}
    print(f"   counts: {counts}")
    # MCP-2 pinned 16/6/4; W0b added capture_notes (changed) and the four
    # capture fields (undefined) — a recorded change, not a drift. W4 added
    # identity_mechanism (undefined: MCP has no peer and its owner is
    # configuration), so undefined moved from 8 to 9. W6 added
    # envelope_version and forecast (unchanged: NEXUS's own reads at the
    # write in either scope), so unchanged moved from 16 to 18.
    assert counts == {UNCHANGED: 18, CHANGED: 7, UNDEFINED: 9}
    hp = hashed_when_present()
    print(f"   hashed only when non-default (the LS mechanism): {sorted(hp)}")
    assert {"scope", "request_hash", "work_unit_id", "accountable_identity", "boundary_asserted",
            "boundary_asserted_by", "learning_mode", "answer_hash", "reasoning_hash",
            "inference_seed", "model_digest", "inference_config_hash", "environment_hash",
            "capture_notes"} <= hp
    assert "record_scope" in hp, "MCP-2 built record_scope on the LS mechanism; it must be hashed when non-default"


# ---------------------------------------------------------------------------
# 3. Coverage — what NEXUS can observe, with a scripted stub client
# ---------------------------------------------------------------------------

COVERAGE_SENTENCE = ("complete for every turn the client recorded; retrievals without a record are "
                     "counted; turns with neither are not visible to NEXUS")

# The script: one letter pair per turn. R = retrieve-before, r = record-after.
# A deterministic client follows its script exactly, which is the point: the
# numbers characterise the harness and the observer, never Claude.
SCRIPT = ["Rr"] * 25 + ["R-"] * 6 + ["-r"] * 5 + ["--"] * 4
HANDLE_TIMEOUT_TURNS = 1     # a handle not closed by the next turn has expired


class _Nexus:
    """The two halves, driven through the existing public functions and a
    handle table — the observer's view. No loop, no provider, no route."""

    def __init__(self, root: Path):
        self.store = TripleStore(root / "kb.db")
        self.lineage = LineageStore(root / "kb.db")
        for i in range(1, 6):
            self.store.add(subject=f"concept{i}", relation="improves", object=f"outcome{i}", weight=0.9,
                           tier=1, granularity=Granularity.TOPIC, source="human_assertion")
        self.retriever = TripleRetriever(self.store)
        self.hippocampus = Hippocampus()
        self.handles: dict[str, dict] = {}
        self.seen = {"retrieve": 0, "record": 0, "record_with_handle": 0,
                     "record_without_handle": 0, "handles_expired": 0}
        self.turn = 0

    def retrieve(self, question: str) -> str:
        self.seen["retrieve"] += 1
        ctx = assemble_context(question, self.retriever, self.hippocampus)
        hid = hashlib.sha256(f"{self.turn}:{question}".encode()).hexdigest()[:12]
        self.handles[hid] = {"turn": self.turn, "question": question, "hits": ctx.hits,
                             "block": ctx.block, "rendered": ctx.rendered_activation}
        return hid

    def record(self, answer: str, handle: str | None) -> str | None:
        self.seen["record"] += 1
        h = self.handles.pop(handle, None) if handle else None
        if h is None:
            self.seen["record_without_handle"] += 1
            return None
        self.seen["record_with_handle"] += 1
        cited = cited_ids(h["hits"], answer)
        rec = self.lineage.record_turn(
            scope="default", query=h["question"],
            injected=[{"belief_id": rt.triple.id, "weight_at_injection": round(rt.score, 6),
                       "tier_at_injection": rt.triple.tier, "cited": rt.triple.id in cited} for rt in h["hits"]],
            contradictions=[], model_id="client-reported:unknown",
            response_text=answer, answer_text=answer, reasoning_text=None, answer_empty_reason=None,
            prompt_prefix=STABLE_SYSTEM_PREFIX,
            context={"render": render_version(), "hash": context_hash(h["block"]),
                     "activation": [list(r) for r in h["rendered"]], "extraction_failed": False},
            citation_test=citation_test_version(),
            request_hash=context_hash(h["block"]),        # block-only: the MCP meaning
            learning_mode="live")
        return rec.lineage_id

    def tick(self) -> None:
        self.turn += 1
        expired = [k for k, v in self.handles.items() if self.turn - v["turn"] > HANDLE_TIMEOUT_TURNS]
        for k in expired:
            self.handles.pop(k)
            self.seen["handles_expired"] += 1

    def close(self):
        self.store.close(); self.lineage.close()


def test_coverage_with_a_scripted_stub_client(tmp_path):
    """Item 3. A scripted client runs 40 turns: 25 retrieve-then-record, 6
    retrieve with no record, 5 record with no retrieve, 4 neither. NEXUS
    observes exactly what the script says it can: the records it wrote, the
    handles that expired (retrieve without record), the records with no
    handle (record without retrieve) — and never the four turns with neither.
    The stub follows its script by construction, so the compliance figure is
    100% of the script and says nothing about Claude; the `instructions`
    field and tool descriptions are the only server-side levers, and their
    effect on a real client is not measurable here."""
    nx = _Nexus(tmp_path)
    answers = {"Rr": "concept1 improves outcome1.", "R-": "no record follows", "-r": "an answer with no retrieve"}
    truth = {k: 0 for k in ("Rr", "R-", "-r", "--")}
    for i, step in enumerate(SCRIPT):
        truth[step] += 1
        hid = nx.retrieve(f"turn {i}: concept improves outcome") if step[0] == "R" else None
        if step[1] == "r":
            nx.record(answers[step], hid)
        nx.tick()
    nx.tick()                                     # let the last handle expire
    seen = nx.seen
    print(f"\nMCP1-3 ({sys.platform}) script {truth}; NEXUS observed {seen}; "
          f"invisible = {truth['--']} (known only to the script)")
    print(f"   coverage sentence: {COVERAGE_SENTENCE}")
    assert seen["retrieve"] == truth["Rr"] + truth["R-"] == 31
    assert seen["record"] == truth["Rr"] + truth["-r"] == 30
    assert seen["record_with_handle"] == truth["Rr"] == 25
    assert seen["handles_expired"] == truth["R-"] == 6            # detectable: retrieve, no record
    assert seen["record_without_handle"] == truth["-r"] == 5       # detectable: record, no retrieve
    assert nx.handles == {}
    recorded = nx.lineage.recent(limit=100)
    assert len(recorded) == 25, "the record is complete for every turn the client recorded, and no other"
    # The second half ran NEXUS's own citation test over the client's text.
    assert any(i["cited"] for r in recorded for i in r.injected)
    # Pinned: the script's compliance is 100% by construction (harness, not Claude).
    assert (seen["record_with_handle"] + seen["handles_expired"] + seen["record_without_handle"]) == 36
    assert 40 - 36 == truth["--"] == 4
    nx.close()


# ---------------------------------------------------------------------------
# 4. Extraction without a local model — the gate's evidence classes
# ---------------------------------------------------------------------------

PINNED_CLASSES = ("asserted", "external", "fixture", "procedural", "consolidation", "extraction")


def test_the_evidence_classes_name_no_client_self_report():
    """Item 4a. The classes are enumerated from the code (`CLASS_ORDER`,
    `SOURCE_CLASS`); none names the answering model's own report of its
    claims. The nearest is `extraction` — model-produced claims under the
    gate's STRICT rules — which an unregistered source falls into by
    default with a warning (Law X). Whether a client self-report is a new
    SOURCE under `extraction` or a new CLASS is a record-layer decision for
    Fable, not MCP-2's."""
    assert CLASS_ORDER == PINNED_CLASSES, f"the class vocabulary moved: {CLASS_ORDER}"
    assert UNKNOWN_CLASS == "extraction"
    print(f"\nMCP1-4 ({sys.platform}) classes {CLASS_ORDER}; sources -> class: "
          f"{ {k: v for k, v in SOURCE_CLASS.items()} }")
    assert set(SOURCE_CLASS.values()) <= set(CLASS_ORDER)
    assert not any("client" in s or "self" in s or "mcp" in s for s in SOURCE_CLASS), \
        "a client self-report source exists already — the establish is stale"


# ---------------------------------------------------------------------------
# 5. The history path has no object — the cascade over the block alone
# ---------------------------------------------------------------------------

def test_the_block_alone_never_reaches_tier_1(tmp_path, monkeypatch):
    """Item 5. Under MCP NEXUS's block is one message in a prompt it never
    sees: the estimate is over the prefix and the block, with no messages.
    At the shipped caps (20 KB lines, 10 cortex lines) that is a few hundred
    tokens against a 5,734 threshold, so no tier fires by construction;
    tier 4 has nothing to drop; `HISTORY_ANSWER_CAP_CHARS` and the carrier
    have no object. Measured on the rig at the cap, not asserted from the
    cap alone."""
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    rig = CERig(tmp_path / "blk", kb_lines=MAX_INJECTED_TRIPLES, user_chars=76)
    with rig.clocked():                     # the rig's clock for a direct call (W0)
        ctx = assemble_context(rig.question(1), rig.retriever, rig.hippocampus)
    rig.close()
    est = estimate_tokens(NexusLoopState(), prefix=STABLE_SYSTEM_PREFIX, dynamic_block=ctx.block)
    kb_lines = len(ctx.hits)
    print(f"\nMCP1-5 ({sys.platform}) prefix {estimate_tokens_text(STABLE_SYSTEM_PREFIX)} + block "
          f"{estimate_tokens_text(ctx.block)} ({kb_lines} KB lines, cortex cap {MAX_CORTEX_LINES}) = {est} "
          f"vs tier-1 threshold {TIER_1_SNIP_AT}")
    assert kb_lines == MAX_INJECTED_TRIPLES, "the rig did not fill the cap"
    assert est < TIER_1_SNIP_AT // 4, "the block alone is within a quarter of tier 1 — the claim is not safe"


# ---------------------------------------------------------------------------
# 6. Learning with no .env
# ---------------------------------------------------------------------------

def test_learning_reads_live_when_the_setting_is_absent(monkeypatch):
    """Item 6. A bundle ships no `.env`; with `NEXUS_LEARNING` unset the
    switch reads `live`, so a fresh store would learn from client-supplied
    answers unless the bundle sets `frozen` itself. The recommendation is
    frozen by default; where that default lives is a decision in the table."""
    from backend import learning
    monkeypatch.delenv("NEXUS_LEARNING", raising=False)
    d = learning.learning_declaration()
    print(f"\nMCP1-6 ({sys.platform}) NEXUS_LEARNING unset -> {d}")
    assert d["state"] == "live" and learning.learning_mode() == "live"


# ---------------------------------------------------------------------------
# 7 & 8. Identity under stdio and the tool surface, from the routes
# ---------------------------------------------------------------------------

NAMED_READ_ROUTES = {"/belief/{belief_id}/recall", "/kb/as-of", "/cortex/stats", "/lineage/{lineage_id}"}


def test_the_tool_surface_is_derived_from_the_routes(tmp_path, monkeypatch):
    """Items 7 and 8. The four routes the packet names exist as GET reads and
    map one-to-one onto tools; no route serves retrieve-as-a-call or
    record-as-a-call (`/chat` runs the whole turn); the bearer token is read
    only by the HTTP app, which MCP mode would never construct. Listed, not
    designed."""
    monkeypatch.setenv("NEXUS_API_TOKEN", "mcp1-token")
    from backend.server import create_app, create_services
    svc = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl", archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    app = create_app(svc)
    routes = {(m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or ()) if m in ("GET", "POST", "DELETE")}
    gets = sorted(p for m, p in routes if m == "GET")
    posts = sorted(p for m, p in routes if m == "POST")
    print(f"\nMCP1-8 ({sys.platform}) GET reads ({len(gets)}): {gets}\n   POST acts ({len(posts)}): {posts}")
    assert NAMED_READ_ROUTES <= set(gets)
    assert not any("retrieve" in p or "record" in p for _m, p in routes), "a retrieve/record route exists already"
    assert ("POST", "/chat") in routes, "the whole-turn route is the only way a turn runs today"
    # Item 7: the token is the HTTP app's; nothing else reads it.
    src = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    assert "NEXUS_API_TOKEN" in src
    others = [p.relative_to(ROOT).as_posix() for p in tracked_backend()
              if p.name != "server.py" and "NEXUS_API_TOKEN" in p.read_text(encoding="utf-8")]
    print(f"   NEXUS_API_TOKEN read outside server.py (tracked backend): {others}")
