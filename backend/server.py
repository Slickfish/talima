"""FastAPI entry point — **factory only. Importing this module does nothing.**

Production: `python start.py`, which runs `python -m uvicorn
backend.server:create_app --factory --host H --port P --workers 1
--forwarded-allow-ips "127.0.0.1,::1"` (QUICKSTART.md gives the command and
the environment it removes). There is no module-level `app`;
construction happens when someone asks for it.
Tests: `TestClient(create_app())` produces a fresh, isolated NexusServices
per call so module-level state cannot leak between tests (Fix 4).

WHY THERE IS NO MODULE-LEVEL `app` (Packet J)
    There used to be: `app = create_app()` on the last line. Merely importing
    this module therefore built the entire production stack — eight
    persistent writers — and, because the path defaults are relative
    (`Path("data/kb.db")`, resolved against the cwd at open time), **created
    files in whatever directory the importer happened to be in.**

    That is not hypothetical. A scratchpad script imported this module from
    the repo root and migrated the operator's live `data/kb.db`
    (Packet H.2: `pre-import md5 3697657…` -> `post-import md5 32b12d8…`).
    Measured footprint of a bare import: 3 files and 2 directories —
    `data/kb.db`, `data/archive.db`, `data/chroma/`,
    `data/chroma/chroma.sqlite3`, `data/snapshots/`.

    Enforced by `tests/test_import_purity.py`, which imports this module in a
    fresh interpreter rooted in a temp directory and asserts that nothing was
    created or modified, and that no socket was opened. Prose asking people
    not to import it existed for months and was broken by its own author;
    pack §4 — *a documented rule is not a control.*

NOTE — the outbound probe is NOT here. `create_app()` opens no socket. The
Ollama call lives in the lifespan below (`sync_pulled_state`), which runs on
server start and on every `TestClient` entry. Three-stage measurement:
import `[]`, construction `[]`, lifespan `[('127.0.0.1', 11434)]`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import weakref
import os
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.cortex.contradiction import (
    ContradictionDetector, load_pairs, pairs_source)
from backend.cortex.curiosity import CuriosityEngine
from backend.cortex.hebbian import HebbianUpdater
from backend.cortex.predictive import PredictiveCortex
from backend.data_root import console_kwargs
from backend.eval.ragas import TurnEvaluator
from backend.kb.deduplicator import DuplicateCandidate, SemanticDeduplicator
from backend.kb.granularity import Granularity
from backend.kb.recalibration import (
    ACTOR_ROLES, ACTOR_SOURCES, Actor, DIRECTION_SIGN, Envelope,
    EnvelopeViolation, RecalibrationStore, apply_outcome, provenance_for,
    recalibration_enabled)
from backend.kb.retriever import TripleRetriever
from backend.kb.trails import TrailStore, trail_slots, trails_enabled
from backend.kb.semantic_index import (
    SemanticIndex, index_pending, semantic_min_similarity, semantic_slots,
    surface_of)
from backend.kb.seeder import seed_conceptnet, seed_offline_fixture, seed_wikidata
from backend.kb.vocabulary import (
    load_relations, vocabulary_enabled, vocabulary_source)
from backend.kb.schema import DEFAULT_DB
from backend.store_lock import EXIT_STORE_LOCKED, StoreLocked, StoreLockUnavailable
from backend.store import configured_store_target
from backend.store_lock import acquire as acquire_store_lock
from backend.kb.tiered_store import (
    AmbiguousArchiveTarget, BeliefRejected, TripleStore)
from backend.loop.agentic_loop import (
    recorder_declaration, run_turn, set_event_recorder, substrate_weight_restore_enabled,
)
from backend.loop.state import NexusLoopState, next_state
from backend.loop.worker import WorkerContextManager
from backend.memory.chain import NexusArchiveChain
from backend.memory.checkpoint import NexusCheckpointer
from backend.memory.fingerprint import (
    CortexFingerprint,
    CortexFingerprinter,
    assess_drift,
)
import logging

from backend.memory.graph_store import HebbianGraphStore, cited_pairs
from backend.memory.hippocampus import Hippocampus
from backend.memory import instance_key
from backend.memory.kairos import KairosProcess
from backend.memory.lineage import IDENTITY_ASSERTED, IDENTITY_PROXY, LineageStore, verify_sealed_lineage
from backend.memory.neocortex import Neocortex
from backend.memory.procedural import ProceduralMemory
from backend.providers import LLMProvider, get_provider
from backend.providers.backends import EXIT_UNKNOWN_BACKEND, BackendConfigError
from backend.providers.model_map import ROLES, answering_roles, resolve
from backend.router.capabilities import can_answer
from backend.router.model_kernel import ModelKernel
from backend.router.model_registry import default_registry, matches_available
from backend.tools.search import WebSearchTool
from backend.vector.chroma import VectorStore, vector_status
from backend.vector.embeddings import embedding_status

# ---------- `.env`, loaded where the reader is (INVESTIGATE, 2026-08) ----
#
# THE DEFECT, measured: `start.py` read `.env` twice and NOTHING under
# `backend/` did, so every launch except `start.py`'s happy path got no `.env`
# at all — including the manual `uvicorn` command `start.py` itself prints.
# An operator with `NEXUS_API_TOKEN=…` in `.env` watched the server announce
# "NEXUS_API_TOKEN not set" and mint its own.
#
# U.2 — the implementation moved to `backend/env.py` so scripts can import it
# without importing this module (H.2). It is loaded HERE, at import, because
# this is where the readers are. See `backend/env.py` for the precedence
# argument and the order.
from backend.env import env_file_candidates, load_env_files    # noqa: F401

LOADED_ENV_FILES = load_env_files()

DEFAULT_THREAD_ID = "default"
EVAL_LOG_PATH = "data/turn_metrics.jsonl"
FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "nexus_ui.html"

# Packet O.1 — `logging` was imported but no module logger was ever bound,
# while TWO sites already called `logger.*`. Both sit on paths that a fresh
# store never reaches:
#
#   :231   E.3's restore, guarded by `if restored:` — zero on an empty store
#   :1198  the association-graph persist handler, inside `except Exception`
#
# So the whole suite passed and the operator's machine could not boot, because
# they had used it for an hour, stopped, and restarted with an accumulated
# graph. The second site is the worse of the two: a NameError inside an
# exception handler replaces the error being reported with itself.
#
# Named for the module, matching every other logger in the codebase
# (`nexus.kairos`, `nexus.kb.store`, `nexus.graph_store`, …) rather than
# inventing a second convention.
logger = logging.getLogger("nexus.server")


# ---------- Package A — minimal bearer-token auth (see THREAT_MODEL.md) ----------

_SESSION_TOKEN: str | None = None      # generated lazily when env var is absent
_TOKEN_BANNER_SHOWN = False


def active_token() -> str:
    """Current API token. NEXUS_API_TOKEN env var wins; otherwise a per-process
    token is generated once and announced on stdout so the local operator can
    copy it. Reading the env per-call keeps tests (monkeypatch.setenv) honest."""
    env = os.getenv("NEXUS_API_TOKEN", "").strip()
    if env:
        return env
    global _SESSION_TOKEN, _TOKEN_BANNER_SHOWN
    if _SESSION_TOKEN is None:
        _SESSION_TOKEN = secrets.token_urlsafe(32)
    if not _TOKEN_BANNER_SHOWN:
        _TOKEN_BANNER_SHOWN = True
        print(
            "\n================================================================\n"
            "NEXUS_API_TOKEN not set. Generated session token:\n"
            f"    {_SESSION_TOKEN}\n"
            "\n"
            "This token is per-PROCESS. Under `uvicorn --reload` it is\n"
            "regenerated every time the server restarts, so a token copied\n"
            "now stops working on the next file save.\n"
            "\n"
            "To fix it: set NEXUS_API_TOKEN in .env (or as a real environment\n"
            "variable) and RESTART the server — it is read at startup, not\n"
            "per request.\n"
            "================================================================\n",
            flush=True,
        )
    return _SESSION_TOKEN


def _identity_config_of(request: Request):
    """W4 part 1 — the identity settings this app's factory read (D1), or
    None for an app `create_app` did not build: that app keeps the bearer."""
    return getattr(request.app.state, "identity_config", None)


def require_token(request: Request, authorization: str = Header(default="")) -> None:
    """Bearer check for every state-changing endpoint and the gated evidence
    reads (A-2). Constant-time compare; the other GET endpoints stay open
    (localhost-bind posture — THREAT_MODEL.md).

    W4 part 1 (D3, D4) — the one dependency object on the gated routes in
    both modes, because two derived guards key on this object. Token mode is
    the check above, exactly. In proxy mode it asks for no bearer and accepts
    none: it requires the identity gate's verdict on this request
    (backend/identity.py) and answers 401 without it, so a gated route
    refuses even with the gate missing; `active_token()` is never called
    there, so no token is minted or printed. backend.identity is imported
    here, not at the top, so MCP (which imports this module for
    `create_services`) never imports it (I15)."""
    config = _identity_config_of(request)
    if config is not None and config.is_proxy:
        from backend.identity import NO_ESTABLISHED, identity_on_scope
        if identity_on_scope(request.scope) is None:
            raise HTTPException(status_code=401, detail=NO_ESTABLISHED)
        return
    expected = f"Bearer {active_token()}"
    if not secrets.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="unauthorized")


def established_identity(request: Request):
    """W4 part 1 — the identity the gate established for this request
    (`EstablishedIdentity`: the login, and `proxy`), or None: always None in
    token mode, whatever the scope carries. A dependency, so a route takes
    the login from here and never reads a header itself (D7: part 1b's
    `/chat`)."""
    config = _identity_config_of(request)
    if config is None or not config.is_proxy:
        return None
    from backend.identity import identity_on_scope
    return identity_on_scope(request.scope)


def _login_of(identity) -> "str | None":
    """W10b — the login an established identity names, or None (token mode)."""
    return getattr(identity, "login", None) if identity is not None else None


def _action_not_recorded(e) -> JSONResponse:
    """W10b (D2) — an action whose record could not be written was not sent, or its result is withheld: said by
    name, with the count a seed admitted before it stopped."""
    body = {"refused": "action_not_recorded", "detail": str(e)}
    if getattr(e, "seeded", None) is not None:
        body["seeded_before_the_stop"] = e.seeded
    return JSONResponse(status_code=503, content=body)


def _identity_mechanism_of(identity) -> str:
    """W4 part 1b — how a request's identity was established, in the
    record's vocabulary (`backend/memory/lineage.py`, IDENTITY_MECHANISMS):
    `proxy` when the gate established a login for the request, else
    `asserted` (token mode: the body says who a turn is for). The one
    mapping /whoami reports and /chat records, so the word /whoami prints is
    the value the turn's record carries (`record_turn` stores `asserted` as
    NULL and seals `proxy`)."""
    return IDENTITY_PROXY if identity is not None else IDENTITY_ASSERTED

RECENT_EVENTS_CAP = 200


def _record_event(services: "NexusServices", event_name: str, payload: dict) -> None:
    """Append a loop event to the per-app ring buffer (memory-column poll)."""
    import time
    services.recent_events.append({
        "event":     event_name,
        "data":      payload,
        "timestamp": time.time(),
    })
    if len(services.recent_events) > RECENT_EVENTS_CAP:
        services.recent_events = services.recent_events[-RECENT_EVENTS_CAP:]


# ---------- service container ----------

@dataclass
class NexusServices:
    """All per-app singletons. Mutable so `state` can advance turn-by-turn.

    Each `create_services()` call returns a fresh container, giving tests
    full isolation and letting production keep one stack at module scope.

    R.4 — `provider` propagates. Assigning it rebinds every collaborator that
    holds one, because the codebase's established way to isolate a test is

        services = create_services(...)
        services.provider = _StubProvider()

    and before R.4 that line was half true. `VectorStore` was constructed
    during `create_services()` and kept whatever provider it was given, so 31
    tests that injected a stub still made 82 live HTTP calls to
    localhost:11434 — KAIROS's dedup scan queries the vector store once per
    triple, and the vector store went around `services.provider` to the
    module-global `PROVIDER`.

    Threading the provider in at construction fixes new call sites; it does
    not fix that line, and that line is the one thirty test files already
    wrote. So the assignment itself has to mean what everyone already read it
    as meaning. `__setattr__` is deliberate and narrow: one field, one fan-out
    target, and a field ordering (`vector` is declared before `provider`) that
    makes it correct during `__init__` as well as after it.
    """

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if name == "provider":
            vector = getattr(self, "vector", None)
            if vector is not None:
                vector.provider = value

    state:           NexusLoopState
    store:           TripleStore
    retriever:       TripleRetriever
    hippocampus:     Hippocampus
    hebbian:         HebbianUpdater
    neocortex:       Neocortex
    procedural:      ProceduralMemory
    kairos:          KairosProcess
    checkpointer:    NexusCheckpointer
    kernel:          ModelKernel
    evaluator:       TurnEvaluator
    vector:          VectorStore
    predictive:      PredictiveCortex
    provider:        LLMProvider
    contradiction:   ContradictionDetector
    curiosity:       CuriosityEngine
    fingerprinter:   CortexFingerprinter
    archive_chain:   NexusArchiveChain
    search_tool:     WebSearchTool
    # P3 / L2 — per-turn answer-lineage records (kb.db; chain-sealed by KAIROS)
    lineage:         LineageStore
    # S.2 — governed adaptation: envelopes, outcome events, recalibrations.
    # kb.db, sealed by KAIROS on the same path as lineage records.
    recalibration:   "RecalibrationStore | None" = None
    # v3.1 Phase 1 — work units and the admission junction, on the
    # TripleStore's own kb.db connection (no new file, no new writer).
    work_units:      "WorkUnitStore | None" = None
    # W.1 — triple-surface embeddings (kb.db). A derived index, not a record:
    # rebuildable from `triples` plus a model name, and deliberately NOT
    # chain-sealed — see backend/kb/semantic_index.py for that reasoning.
    semantic:        "SemanticIndex | None" = None
    # Packet AB — relation trails. Always constructed (the store is cheap and
    # laying is separately switched); applied only at NEXUS_TRAIL_SLOTS > 0.
    trails:          "TrailStore | None" = None
    # E.3 — persistent Hebbian association graph (kb.db). `graph_lock` is
    # held by BOTH writers (chat path at turn end, KAIROS at cycle end)
    # across mutate-plus-persist: session_active is cooperative and a cycle
    # already in flight does not re-check it, leaving a window at every await.
    graph_store:     "HebbianGraphStore | None" = None
    graph_lock:      "asyncio.Lock | None" = None
    # Per-query Law 3 consent. Set by POST /search/confirm; consumed (and
    # cleared) by run_turn when the matching turn fires the search trigger.
    search_consents: dict = field(default_factory=dict)
    # Pre-Phase-10c governance: queue of payloads emitted by KAIROS during
    # idle cycles. Drained by the agentic loop at turn-start and re-emitted
    # as kb_pressure_alert SSE frames over the live /chat connection.
    pending_alerts:  list = field(default_factory=list)
    # Fix A — semantic dedup. SemanticDeduplicator scans during KAIROS idle;
    # the latest candidate set is held here for /kb/dedup/pending and the
    # confirm endpoint (Law 3 — never auto-collapsed).
    deduplicator:           "SemanticDeduplicator | None" = None
    last_dedup_candidates:  list = field(default_factory=list)
    # Fix C — execution isolation manager. Phase 10c will spawn file-tool
    # workers through this; tool results enter NexusLoopState only via
    # worker.distill() (Law 1 boundary).
    worker_manager:         "WorkerContextManager | None" = None
    # Phase 2A — ring buffer of recent loop events for memory-column
    # subscribers polling /events/recent. Capped at 200; purely additive.
    recent_events:          list = field(default_factory=list)
    # R.2 — the startup probe's outcome, declared (I-6). A probe that quietly
    # never runs is worse than one that costs five seconds, so the state is
    # reportable on /healthz rather than inferable from whether routing
    # happens to propose a pull.
    model_probe:            dict = field(default_factory=lambda: {
        "mode": None, "state": "not_started", "flagged": 0,
        "models": [], "error": "",
    })
    # W2 — the single-writer lock `create_services` took before its first
    # store opened (`backend/store_lock.py`); released when this object is
    # collected, at interpreter exit, or by `store_lock.release()`.
    store_lock:             object = None
    # W9 (run/specs/W9.md D1) — where the instance's Ed25519 key lives: beside
    # kb.db, made at its first use (`backend/memory/instance_key.py`).
    instance_key_path:      "Path | None" = None


def _registry_for(provider):
    """W3 — the roster the kernel routes on: the provider's own when it
    offers one (`model_registry()`: the stub's `stub:<role>` profiles, so a
    turn under it names the stub and nothing else), else a COPY of the
    default roster (R.3: `sync_pulled_state` writes to whatever registry it
    is given, so the process-global is never handed out)."""
    roster = getattr(provider, "model_registry", None)
    return roster() if callable(roster) else default_registry.copy()


def create_services(
    *,
    kb_db_path: str | Path | None = None,
    chroma_dir: str | Path | None = None,
    eval_log_path: str | Path | None = None,
    archive_db_path: str | Path | None = None,
    snapshots_dir: str | Path | None = None,
    provider: LLMProvider | None = None,
    lock_mode: str = "services",
) -> NexusServices:
    """Build one fresh stack of singletons.

    Path overrides are intended for tests — production passes None and lands
    on the same defaults the previous module-level singletons used.

    `provider` (R.4) is the preferred injection point for a test stub, since
    it is in place before anything is constructed. Assigning
    `services.provider` afterwards also works and propagates; see
    `NexusServices.__setattr__`.

    W2 — the single-writer lock is taken here, before the first store
    connects (every store constructor writes its schema), on the kb.db the
    stores below open; `lock_mode` names the taker in the holder text
    (`console`, `mcp`; `services` for a direct call). Another process holding
    it raises `StoreLocked` and nothing is opened.
    """
    # W3 — the provider first, before the lock and before any store opens: an
    # unknown LLM_BACKEND is refused by name (`UnknownBackend`) having opened
    # nothing, as a held lock is. R.4's rule stands: one provider instance per
    # app, and every collaborator below gets THIS object; nothing reaches
    # around it to the module-level default.
    provider      = provider or get_provider()
    # W3b part 3 (D1) — the store the settings name, resolved before the lock
    # and before any store opens: `NEXUS_STORE=postgres` with its URL makes
    # every store below open the one database (kb.db's and archive.db's
    # tables alike, D7); unset or `sqlite`, the paths decide exactly as
    # before; a value NEXUS does not know, a missing URL or a URL of another
    # scheme is refused by name (`StoreConfigError`), nothing opened.
    pg_target     = configured_store_target()
    kb_target     = pg_target if pg_target is not None else kb_db_path
    archive_target = pg_target if pg_target is not None else archive_db_path
    store_lock    = acquire_store_lock(pg_target if pg_target is not None
                                       else (Path(kb_db_path) if kb_db_path else DEFAULT_DB), mode=lock_mode)
    store         = TripleStore(kb_target) if kb_target else TripleStore()
    hippocampus   = Hippocampus()
    hebbian       = HebbianUpdater()
    neocortex     = Neocortex(kb_target) if kb_target else Neocortex()
    procedural    = ProceduralMemory()
    fingerprinter = CortexFingerprinter()
    archive_chain = (
        NexusArchiveChain(archive_target) if archive_target
        else NexusArchiveChain()
    )
    snapshots_dir_p = Path(snapshots_dir) if snapshots_dir else Path("data/snapshots")
    snapshots_dir_p.mkdir(parents=True, exist_ok=True)
    lineage = LineageStore(kb_target) if kb_target else LineageStore()
    recalibration = (RecalibrationStore(kb_target) if kb_target
                     else RecalibrationStore())
    # S.2 — the store reads its own recalibrations at ranking time.
    # Injected, not imported: recalibration.py imports `classify` from
    # tiered_store, so the reverse edge would be a cycle.
    store.recalibration = recalibration
    # v3.1 Phase 1 — the work-unit tables ride the store's connection.
    from backend.kb.work_units import WorkUnitStore
    work_units = WorkUnitStore(store.conn)
    # E.3 — rehydrate the association graph. No decay sweep here: weight is
    # restored as stored and current_weight(now) applies elapsed wall-clock
    # at read time, so downtime is forgotten lazily and locally.
    graph_store = HebbianGraphStore(kb_target) if kb_target else HebbianGraphStore()
    restored = graph_store.load_into(hippocampus)
    if restored:
        logger.info("restored %d persisted association edges", restored)
    # I-4 — a belief cited by a sealed lineage record is load-bearing for a
    # claim someone may have to defend; its association is exempt from
    # demotion. Injected once so neither update() nor decay_pass() needs a
    # store reference.
    hebbian.protected_pairs = lambda: cited_pairs(lineage, store)
    kairos = KairosProcess(
        hippocampus, neocortex, hebbian, procedural,
        fingerprinter=fingerprinter,
        archive_chain=archive_chain,
        snapshots_dir=snapshots_dir_p,
        lineage=lineage,
        recalibration=recalibration,
        graph_store=graph_store,
    )
    # R.4 — one provider instance per app (resolved above, first of all: W3).
    # W.1 — the semantic recall channel. Constructing it touches nothing: the
    # embeddings table is created on first WRITE, and with
    # NEXUS_SEMANTIC_SLOTS=0 (the default) nothing ever writes.
    semantic = SemanticIndex(store)
    # Packet AB — trails live in kb.db (write-topology rule). Constructing
    # touches nothing but the two empty tables; laying is gated by
    # NEXUS_RELATION_TRAILS and application by NEXUS_TRAIL_SLOTS.
    trails = TrailStore(kb_target) if kb_target else TrailStore()
    services = NexusServices(
        state           = NexusLoopState(),
        store           = store,
        retriever       = TripleRetriever(store, semantic=semantic,
                                          trails=trails),
        semantic        = semantic,
        trails          = trails,
        hippocampus     = hippocampus,
        hebbian         = hebbian,
        neocortex       = neocortex,
        procedural      = procedural,
        kairos          = kairos,
        work_units      = work_units,           # v3.1 Phase 1
        checkpointer    = NexusCheckpointer(),
        # R.3 — a COPY, not the process-global. sync_pulled_state()
        # writes to whatever registry it is given, and until the probe
        # started returning installed models it never wrote anything,
        # so the sharing was invisible rather than absent.
        kernel          = ModelKernel(_registry_for(provider)),
        evaluator       = TurnEvaluator(log_path=eval_log_path or EVAL_LOG_PATH),
        vector          = (VectorStore(chroma_dir, provider=provider)
                           if chroma_dir else VectorStore(provider=provider)),
        predictive      = PredictiveCortex(hippocampus),
        provider        = provider,
        # T.2 — the opposition set is configuration, not a constant.
        # `pairs=` has been accepted since this shipped and nothing
        # ever passed it. Defaults to the compiled seven, so nothing
        # changes until a file is named.
        contradiction   = ContradictionDetector(pairs=load_pairs()),
        curiosity       = CuriosityEngine(),
        fingerprinter   = fingerprinter,
        archive_chain   = archive_chain,
        search_tool     = WebSearchTool(store=lineage),      # W10b: each search recorded as an action
        lineage         = lineage,
        recalibration   = recalibration,
        graph_store     = graph_store,
        graph_lock      = asyncio.Lock(),
        search_consents = {},
        pending_alerts  = [],
    )
    # Fix A — wire deduplicator now that vector + store exist on services.
    # E.3 — KAIROS is constructed before `services` exists, so hand it the
    # shared lock now. Both graph writers then serialise on the same object.
    kairos.graph_lock = services.graph_lock
    services.deduplicator = SemanticDeduplicator(
        vector_store=services.vector,
        triple_store=services.store,
    )
    # Fix C — execution isolation manager (read-only surface for now;
    # Phase 10c wires file-tool spawn() calls through this).
    services.worker_manager = WorkerContextManager()
    # Pre-Phase-10c governance: bind the services back-ref so KAIROS-side
    # hooks can reach deduplicator / last_dedup_candidates / drift state, and
    # default notify_ui to enqueueing onto pending_alerts.
    kairos.bind_services(services)

    async def _enqueue_alert(payload: dict) -> None:
        services.pending_alerts.append(payload)

    kairos.notify_ui = _enqueue_alert
    services.store_lock = store_lock
    services.instance_key_path = (Path(kb_db_path) if kb_db_path else DEFAULT_DB).parent / instance_key.KEY_FILE
    # W9 (D4) — every block appended from here on is signed and announces a
    # key the chain has not announced last; the key is read at the first need.
    archive_chain.signer = instance_key.signer_for(services.instance_key_path, archive_chain)
    archive_chain.identity_source = instance_key.identity_source_for(services.instance_key_path, archive_chain)
    # W10b (D10) — the key a runtime action's request is signed with when NEXUS_SIGN_REQUESTS=on.
    from backend.memory import http_signatures as _hs
    services.lineage.request_signer = _hs.signer_for(services.instance_key_path, archive_chain)
    weakref.finalize(services, store_lock.release)
    return services


# ---------- request models ----------

class Attachment(BaseModel):
    """Fix 9 — multimodal payload. `data` is base64 (image bytes / PDF /
    docx); `type` is the MIME from the file-input element."""
    name: str
    type: str
    data: str


class CommitRequest(BaseModel):
    """W9 (D8) — `POST /commit`: the note to commit to, and Law 3's flag,
    never defaulted to true (the operator's decision 2)."""
    content: str
    confirmed: bool = False


class ChatRequest(BaseModel):
    message: str
    attachments: list[Attachment] = []
    # AD — which process step / programme the consuming workflow says this
    # turn belongs to. Recorded, NEVER inferred: the workflow knows which
    # step it is in and the substrate does not. One opaque field — the
    # partner's hierarchy (programme/step/…) is theirs to encode; splitting
    # later is additive, collapsing two wrongly-guessed fields is a
    # migration across sealed records. "" means unstated and records as
    # "default", which is a VALUE, never null.
    scope: str = ""
    # v3.1 Phase 1 / 1b — attribution, the way scope is done: asserted by
    # the caller on THIS request, body only, recorded literally, never
    # inferred, never confirmed. `work_unit` names the unit of value this
    # turn was applied to (a row created on first mention with the request's
    # actor fields); `accountable` is the answerable human, recorded and
    # never authenticated, per record; `decision_boundary` is the boundary
    # the caller asserts — `close` sets the unit's `closed_at` and, since
    # W6, `reopen` clears it; each is an act in the sealed boundary ledger.
    # Any other value is recorded as asserted and has no effect. The actor
    # fields follow the outcome
    # route's vocabulary and defaults. There is no header channel (1b
    # removed it): two channels for one value was the only way a
    # contradiction could exist, and the same cap, whitespace and position
    # rules as `scope` apply (`_attribution_refusal`).
    work_unit: str = ""
    work_unit_type: str = ""
    accountable: str = ""
    decision_boundary: str = ""
    actor_id: str = ""
    actor_role: str = ""
    actor_source: str = ""


class SeedConceptNetRequest(BaseModel):
    concepts: list[str]
    confirmed: bool = False
    per_concept_limit: int = 20
    # v3.1 Phase 1b — the unit the seeded claims are admitted to, asserted
    # on this request as on `/chat` (1a attributed seeds to "the session's
    # open unit"; there is no such thing now, and without this field the
    # seed routes' junction writes would have been retired as a side
    # effect). Empty = no unit named = no junction row, declared.
    work_unit: str = ""


class SeedWikidataRequest(BaseModel):
    """Fix 1 — Wikidata SPARQL seed request, Law 3 gated."""
    concepts: list[str]
    confirmed: bool = False
    per_concept_limit: int = 20
    work_unit: str = ""     # v3.1 Phase 1b — see SeedConceptNetRequest


class OverrideReq(BaseModel):
    model: str
    lock: bool = False


class SearchConfirmReq(BaseModel):
    """Phase 10b — user records consent for one specific query.

    The next /chat call carrying that exact query string will fire the search;
    consent is single-use and clears once consumed.

    AT.0 — `confirmed` has NO default. It defaulted to True since Phase 10b,
    so a body that omitted the field granted consent: a Law 3 gate defeated
    by a default value. Every other gate is an explicit boolean with no
    default, and an AST guard (tests/test_at0_confirm_defaults.py) now holds
    every `confirmed` field and parameter in this module to that shape."""
    query: str
    confirmed: bool


class SearchExecuteReq(BaseModel):
    """Fix SEARCH_UX — fire a web search immediately on user click.

    Replaces the consent-key-matching pattern: the click itself IS Law 3
    confirmation. Frontend posts here, gets results, and injects them into
    the next /chat message body."""
    query: str
    confirmed: bool = False


class OutcomeConfirmReq(BaseModel):
    """S.2 — an outcome event, and the recalibration it may authorise.

    Shaped to the seam spec's Flow 4 contract so a workflow platform's events
    fit later without a redesign. `references` accepts `lineage_id` or
    `belief_ids`; `claim_id` is REFUSED explicitly rather than ignored,
    because S.0.2 measured that nothing in NEXUS can resolve one and a silent
    accept would surface as a 500 at the first integration.

    Law 3: `confirmed` is an explicit boolean with no default, the same shape
    `/kb/dedup/confirm`, `/search/confirm` and `/kb/assert/confirm` use. An
    outcome that moves a weight is exactly the irreversible-write class those
    gates exist for.
    """
    event_type: str                 # review | qc | agency_response | manual
    decision: str                   # approved | rejected | query | ...
    direction: str                  # confirming | contradicting | neutral
    references: dict                # {lineage_id: str} | {belief_ids: [int]}
    magnitude: float = 0.0          # size of the move; sign comes from direction
    workflow_ref: str = ""
    notes: str = ""
    actor_id: str = "operator"
    # Z.1 — who, or WHAT, supplied this outcome. Before this field existed a
    # system caller could state only its id, and `_as_actor(str)` defaulted
    # the rest — so `actor_id="lims-system"` was stored role='local_operator',
    # a QC system sealed into the chain as a person. Stated values are
    # validated against ACTOR_ROLES (`person | source_system`); "" means
    # unstated and keeps the pre-Z.1 default `local_operator`, so silence
    # keeps meaning what it always meant.
    actor_role: str = ""
    # Validated against ACTOR_SOURCES (`local | idp`), never extended:
    # "how was this identity established" is a different axis from "what
    # kind of agent is this", and a source system's identity can itself be
    # IdP-attested.
    actor_source: str = ""
    # AD — see ChatRequest.scope. Flows to the outcome event and every
    # recalibration event it authorises.
    scope: str = ""
    confirmed: bool


class IndexBackfillReq(BaseModel):
    """W.1 — Law 3 for a bulk outbound operation.

    Backfilling the semantic index is one embedding call per un-indexed
    belief. It creates no beliefs and moves no weights, so the gate is a
    plain confirm rather than a governance envelope — but it is still a bulk
    operation against a live provider, which is exactly the class Law 3's
    "confirm before" exists for.
    """
    confirmed: bool
    limit: int = 200


class EnvelopeConfirmReq(BaseModel):
    """S.2 — installing a policy version. Changing the bound is itself an
    event, so it goes through the same Law 3 gate as the moves it permits."""
    version: str
    max_delta: float = 0.0
    movable_classes: list[str] = []
    frozen_belief_ids: list[int] = []
    cumulative_max: float | None = None
    reason: str
    actor_id: str = "operator"
    # AD — "installed in the context of". A recording fact only: nothing
    # enforces a scoped envelope, and building that would be scope READING,
    # which AD deliberately excludes.
    scope: str = ""
    confirmed: bool


class AssertConfirmReq(BaseModel):
    """O.2b — Law 3 confirmation gate for a human assertion.

    `confirmed` is an explicit boolean with no default, the same shape
    DedupConfirmReq uses, so accidental True-coercion cannot happen. The
    SENTENCE is sent, not a triple: O.2b decides the shape, because a lenient
    evidence class gets no protection from the gate (measured in O.2a — the
    operator's own sentence is ACCEPTED as a term under `external`).
    """
    sentence: str
    confirmed: bool
    work_unit: str = ""     # v3.1 Phase 1b — see SeedConceptNetRequest


class DedupConfirmReq(BaseModel):
    """Fix A — Law 3 dedup confirmation gate.

    `confirmed` is an explicit boolean (no default) so accidental True-coercion
    can't happen. `keep` selects which side of the duplicate pair survives;
    the other archives to tier 4 (Law 4 — never deletes)."""
    subject_a: str
    relation_a: str
    object_a: str
    subject_b: str
    relation_b: str
    object_b: str
    keep: str           # "a" | "b"
    # L.2a — optional, and additive so existing clients keep working. Names
    # the evidence class of the side being archived, so a claim held by two
    # sources archives the one that was judged. `/kb/dedup/pending` already
    # returns `source` on both candidates, so a UI has it to hand. Omitting it
    # is safe whenever the claim exists once; when it does not, the endpoint
    # refuses rather than archiving both (409).
    source_a: str | None = None
    source_b: str | None = None
    confirmed: bool     # required — Pydantic enforces the type
    # AL — who confirmed the archival. Optional and additive (existing
    # clients keep working); an archival with no actor is half a record, so
    # the AL ledger records the defaults when none is stated — the pre-Z.1
    # meaning of silence, "the local human operator". Same validation as
    # /outcome/confirm: role and source come from closed vocabularies.
    actor_id: str = "operator"
    actor_role: str = ""
    actor_source: str = ""


# ---------- Packet R.2 — startup probe scheduling. PROVISIONAL. ----------
#
# THE PROBLEM, MEASURED (R.0)
#     The lifespan awaited `sync_pulled_state()` — one HTTP GET to the local
#     Ollama daemon — inside `try/except Exception: pass`. 56 of those across
#     the test suite, ~2ms each in a Linux sandbox and ~2.45s each on the
#     operator's Windows box, for a result whose failure is explicitly
#     ignored. A probe whose failure is ignored has no business blocking boot.
#
# WHY DEFERRING IS SAFE, AND WHY THAT IS A MEASUREMENT NOT AN OPINION
#     `is_pulled` has exactly one production consumer: the routing branch in
#     `agentic_loop.run_turn`, which reads it on a chat turn. Nothing reads it
#     during startup. If a turn arrives before the probe lands, the registry
#     looks exactly as it does when the daemon is down — and that state is not
#     merely handled, it is the ONLY startup this suite has ever run (all 56
#     probes fail in the sandbox and 944 tests passed). Deferral therefore
#     puts the app into a state it already occupies, rather than a new one.
#     `tests/test_startup_contract.py` pins all of that, and pins it in both
#     directions so it cannot quietly stop being true.
#
# MODES
#     background  schedule it, serve immediately, cancel it on shutdown
#     sync        await it before serving — EXACTLY the pre-R.2 behaviour,
#                 and therefore the rollback and the reducibility proof
#     off         never probe; nothing is flagged, and /healthz says so
STARTUP_PROBE_MODES   = ("sync", "background", "off")
DEFAULT_STARTUP_PROBE = "background"     # PROVISIONAL


def startup_probe_mode() -> str:
    """Read NEXUS_STARTUP_PROBE at call time. Unrecognised values warn and
    fall back — a typo in a tuning knob must not stop the app from starting,
    but it must not pass unremarked either (I-6)."""
    raw = os.getenv("NEXUS_STARTUP_PROBE", "").strip().lower()
    if not raw:
        return DEFAULT_STARTUP_PROBE
    if raw not in STARTUP_PROBE_MODES:
        logger.warning(
            "NEXUS_STARTUP_PROBE=%r is not one of %s; using %r",
            raw, STARTUP_PROBE_MODES, DEFAULT_STARTUP_PROBE)
        return DEFAULT_STARTUP_PROBE
    return raw


async def _run_startup_probe(services: "NexusServices") -> None:
    """Poll the provider for installed models and record what happened.

    Never raises except CancelledError, which is re-raised so the task really
    is cancelled rather than merely reported as such.

    Note on `state == "ok"` with `flagged == 0`: `sync_pulled_state` swallows
    provider failures internally and returns [], so from here "the daemon is
    down" and "the daemon is up and nothing new matched" are the same value.
    That distinction is not lost — `/healthz` reports `ollama` liveness beside
    this — but it is not THIS field's to make, and pretending otherwise would
    be a declaration that isn't one.
    """
    try:
        flipped = await services.kernel.registry.sync_pulled_state(services.provider)
    except asyncio.CancelledError:
        services.model_probe.update(state="cancelled")
        raise
    except Exception as e:                                  # pragma: no cover
        services.model_probe.update(
            state="failed", error=f"{type(e).__name__}: {e}")
        logger.warning("startup model probe failed: %s: %s", type(e).__name__, e)
        return
    services.model_probe.update(
        state="ok", flagged=len(flipped), models=list(flipped), error="")


def _resolve_outcome_references(services: "NexusServices",
                                references: dict) -> tuple[list[int], str]:
    """(belief_ids, how it was resolved) for one outcome's references.

    S.0.2 measured what the code can actually resolve: `lineage_id` (primary
    key) and `belief_id` (`get_by_id`). `claim_id` appears nowhere in NEXUS
    and is refused at the boundary rather than here.

    THE FAN-OUT RULE, and why it is this one. A lineage record cites N
    beliefs, and "approved" does not mean all N were load-bearing. The only
    discriminator that exists is the `cited` flag, whose three states are
    already defined once in `sealed_cited_belief_ids` — true is cited, absent
    counts as cited, explicit false is not. Following that rather than
    inventing a second reading of the same word.

    It is a policy question dressed as a lookup, and it belongs in the
    envelope eventually. Recorded here as the v0 rule, in one place, so that
    when it moves it moves once.
    """
    refs = references or {}
    ids = [int(i) for i in (refs.get("belief_ids") or [])]
    if ids:
        return [i for i in ids if services.store.get_by_id(i) is not None], \
            "belief_ids"
    lineage_id = refs.get("lineage_id")
    if not lineage_id:
        return [], "no lineage_id or belief_ids supplied"
    record = services.lineage.get(str(lineage_id))
    if record is None:
        return [], f"lineage_id {lineage_id!r} not found"
    out = []
    for item in record.injected:
        if item.get("cited") is False:      # explicit negative is evidence
            continue
        bid = item.get("belief_id")
        if bid is not None and services.store.get_by_id(int(bid)) is not None:
            out.append(int(bid))
    return out, f"lineage_id -> {len(out)} cited belief(s)"


# ---------- T.1 — a read surface names the write route that answers it ----
#
# THE FINDING, measured against the router rather than remembered: the
# envelope's write route was never missing. `POST /governance/envelope`
# returned 405 because the write lives at `/governance/envelope/confirm`, and
# `GET /governance/envelope` said nothing about where it was. So the operator
# could read a policy object, see `is_default: true`, and have no way to learn
# how to change it short of reading `server.py`.
#
# AND IT IS A CLASS, NOT AN INSTANCE. The two Law 3 payloads that are PUSHED
# to a client both name their endpoint — `web_search` and `assertion` alerts
# carry `"endpoint": "/..."`. The three that are PULLED did not:
#
#     GET /kb/dedup/pending      -> names /kb/dedup/confirm       : False
#     GET /search/pending        -> names /search/confirm         : False
#     GET /governance/envelope   -> names .../envelope/confirm    : False
#
# A convention that holds on the push path and not the pull path is not a
# convention, and the run-through found the one instance where it mattered
# most. All three now declare it, in one shape.
#
# `body` is a TEMPLATE, not documentation: the run-through's 422 named three
# missing fields because a script guessed the shape. Showing the shape is the
# fix for the class of guess, not just for that guess.

from backend.kb.work_units import actor_fields   # v3.1 Phase 1b
from backend.learning import learning_status     # FZ-2
from backend.kb import as_of as _as_of            # AS-1 — as-of on transaction time
from backend.kb import source_policy as _source_policy   # W5 — the reference policy

MAX_SCOPE_CHARS = 256


def _clean_scope(raw: str) -> str:
    """AD — the recorded value: stripped, defaulted, NEVER null. Length is
    capped because the scope rides the hashed record body; an unbounded
    request field inside sealed records is a storage grief nobody signed."""
    return (raw or "").strip() or "default"


def _scope_too_long(raw: str) -> "JSONResponse | None":
    if raw and len(raw) > MAX_SCOPE_CHARS:
        return JSONResponse(
            {"error": "scope_too_long", "max_chars": MAX_SCOPE_CHARS,
             "got": len(raw)}, status_code=422)
    return None


# v3.1 Phase 1b — the attribution fields copy scope's rule, field for field:
# the RAW value is capped at MAX_SCOPE_CHARS and refused with a named error
# (`<field>_too_long`, 422) in scope's position — first, before any gate —
# and recorded nowhere (a client error, AT's rule); at the read, whitespace
# only counts as unsupplied (`_clean_field`). The actor fields are validated
# against the closed vocabularies exactly as `/outcome/confirm` does.
ATTRIBUTION_FIELDS = ("work_unit", "work_unit_type", "accountable", "decision_boundary")


def _field_too_long(name: str, raw: str) -> "JSONResponse | None":
    if raw and len(raw) > MAX_SCOPE_CHARS:
        return JSONResponse(
            {"error": f"{name}_too_long", "max_chars": MAX_SCOPE_CHARS,
             "got": len(raw)}, status_code=422)
    return None


def _clean_field(raw: str | None) -> str | None:
    """The recorded value: stripped; absent (None) when empty or whitespace."""
    return (raw or "").strip() or None


def _attribution_refusal(req) -> "JSONResponse | None":
    for name in ATTRIBUTION_FIELDS:
        refuse = _field_too_long(name, getattr(req, name, "") or "")
        if refuse is not None:
            return refuse
    role = getattr(req, "actor_role", "") or ""
    source = getattr(req, "actor_source", "") or ""
    if role and role not in ACTOR_ROLES:
        return JSONResponse({"error": "unknown_actor_role", "role": role,
                             "valid": sorted(ACTOR_ROLES)}, status_code=422)
    if source and source not in ACTOR_SOURCES:
        return JSONResponse({"error": "unknown_actor_source", "source": source,
                             "valid": sorted(ACTOR_SOURCES)}, status_code=422)
    return None


# W4 part 1b (D7) — the turn's `accountable` once the identity gate has
# established a login (proxy mode): the login. A body value equal to it after
# `_clean_field`, or none, is the login, compared exactly as the gate compares
# the owner; one that differs is refused by name with this fixed sentence,
# which names neither value: a client error, declared on the response and
# recorded nowhere (AT's rule). It runs after the field caps above, so
# `accountable_too_long` keeps scope's position.
ACCOUNTABLE_MISMATCH_HINT = (
    "proxy mode: a turn's accountable is the login the identity proxy established for this "
    "request (GET /whoami names it); send no accountable, or exactly that login")


def _turn_accountable(req, identity) -> "tuple[str | None, JSONResponse | None]":
    """(the turn's accountable, None), or (None, the refusal). With no
    established identity (token mode) it is the body's value, cleaned, as
    asserted, exactly as before W4."""
    asserted = _clean_field(req.accountable)
    if identity is None:
        return asserted, None
    if asserted is not None and asserted != identity.login:
        return None, JSONResponse({"error": "accountable_mismatch", "hint": ACCOUNTABLE_MISMATCH_HINT},
                                  status_code=422)
    return identity.login, None


def _request_actor(req) -> dict:
    """The request's actor fields with the vocabulary's recorded defaults
    for whatever it left unsaid — never a fresh literal (1b, B.2)."""
    return actor_fields({"id": getattr(req, "actor_id", "") or "",
                         "role": getattr(req, "actor_role", "") or "",
                         "source": getattr(req, "actor_source", "") or ""})


def _confirm_block(endpoint: str, body: dict, note: str = "") -> dict:
    """Law 3's write route, declared on the surface that proposes the work."""
    block = {"method": "POST", "endpoint": endpoint,
             "auth": "Bearer token required", "body": body}
    if note:
        block["note"] = note
    return block


# ---------- app factory ----------

def create_app(services: NexusServices | None = None) -> FastAPI:
    # W4 part 1 (D1) — the identity settings, read once, here, before any
    # store opens: a NEXUS_AUTH_MODE that names no mode is refused by name
    # having opened nothing (uvicorn's factory call: the reason on stderr and
    # exit 2, as for the backend and the lock). Given stores the caller opened,
    # the refusal is raised instead: "nothing was opened" would not be true.
    from backend.identity import EXIT_AUTH_CONFIG, AuthConfigError, IdentityGate, identity_config
    try:
        identity_cfg = identity_config()
    except AuthConfigError as e:
        if services is not None:
            raise
        print(f"NEXUS: {e}. Nothing was opened.", file=sys.stderr, flush=True)
        raise SystemExit(EXIT_AUTH_CONFIG) from None
    # W1 — uvicorn's `--factory` calls this with no arguments, so the console's
    # data root reaches it through the environment: `NEXUS_DATA_ROOT` unset is
    # `create_services()` exactly as before; set, every store sits under it
    # (`backend/data_root.py`; `start.py --data-root` sets it).
    if services is None:
        try:
            services = create_services(**console_kwargs(), lock_mode="console")
        except (StoreLocked, StoreLockUnavailable) as e:
            # W2 — uvicorn's factory call: say why on stderr and leave with the
            # named exit, instead of a traceback and uvicorn's generic 1.
            print(f"NEXUS: {e}. Nothing was opened.", file=sys.stderr, flush=True)
            raise SystemExit(EXIT_STORE_LOCKED) from None
        except BackendConfigError as e:
            # W3 — the same shape for a LLM_BACKEND that names no backend, or
            # an api backend whose model is unset: the provider is resolved
            # before any store opens, so nothing was.
            print(f"NEXUS: {e}. Nothing was opened.", file=sys.stderr, flush=True)
            raise SystemExit(EXIT_UNKNOWN_BACKEND) from None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        services.kairos.start()
        saved = services.checkpointer.get(DEFAULT_THREAD_ID)
        if saved is not None:
            services.state = saved

        # Fix 4 — learn which models are installed so the kernel doesn't route
        # to one that isn't. R.2 — schedule it rather than block on it; see
        # the block above `create_app` for why that is safe.
        mode = startup_probe_mode()
        services.model_probe.update(
            mode=mode,
            state="disabled" if mode == "off" else "pending",
            flagged=0, models=[], error="",
        )
        probe_task: "asyncio.Task | None" = None
        if mode == "sync":
            await _run_startup_probe(services)
        elif mode == "background":
            probe_task = asyncio.create_task(_run_startup_probe(services))

        try:
            yield
        finally:
            # Cancel BEFORE stopping KAIROS. `kairos.stop()` awaits its task,
            # and an await is exactly where a pending probe gets its slice —
            # so cancelling second would hand back the seconds this packet
            # exists to remove, and would also leave the loop closing over a
            # live task ("Task was destroyed but it is pending").
            if probe_task is not None and not probe_task.done():
                probe_task.cancel()
                try:
                    await probe_task
                except (asyncio.CancelledError, Exception):
                    pass
            await services.kairos.stop()

    app = FastAPI(title="NEXUS", version="0.8.1", lifespan=lifespan)
    app.state.services = services   # exposed for tests / introspection
    # W4 part 1 — the mode this app was built in, kept for `require_token`,
    # `established_identity`, /whoami and /healthz. Token mode installs no
    # middleware at all (zero, as before W4); proxy mode installs the one gate
    # (D2), in front of every route, the /vendor mount and the framework's own.
    app.state.identity_config = identity_cfg
    if identity_cfg.is_proxy:
        app.add_middleware(IdentityGate, config=identity_cfg)
        if not identity_cfg.owner_set:
            # D9 — the worklist's request-time answer, not a startup refusal.
            print("NEXUS: proxy mode with no owner (NEXUS_OWNER_IDENTITY is unset): every request will be "
                  "refused, 403 \"owner not set\" to a loopback peer carrying the identity header and 401 to "
                  "any other; /health and /healthz still answer.", file=sys.stderr, flush=True)
    # B-40 — the governance router. Every /governance/* route is served by
    # this object, and the just-culture guard's AUTHORITY rule keys on it
    # (tests/test_just_culture_guard.py): a surface may aggregate by person
    # only if it is served by this router, gated, and declared per-person.
    # Governed acts are attributable to their actor by construction; work
    # (cognition, turns, citations, units) is never aggregated by person.
    # Exposed on app.state so the derivation reads the object, not a path.
    governance = APIRouter(prefix="/governance", tags=["governance"])
    app.state.governance_router = governance
    # The one declaration both governance reads carry (B-40). `field` is
    # the actor id as the request asserted it; never authenticated.
    GOVERNANCE_PER_PERSON = {
        "declared": True,
        "field": "actor_id",
        "label": "asserted",
        "note": ("per-person rows: governed acts are attributable to their actor by "
                 "construction, so this surface keys them by the actor the request "
                 "asserted (recorded, never authenticated). It is an authority surface "
                 "— the governance router, gated, declared — the only kind NEXUS may "
                 "aggregate by person on. Aggregates of cognition, turns, citations or "
                 "units by the person doing the work are forbidden everywhere."),
    }

    # P1/F-07 — vendored UI assets (d3, three, gsap, fonts) served locally so
    # both UIs load fully air-gapped. Assets live in frontend/vendor/.
    vendor_dir = FRONTEND.parent / "vendor"
    if vendor_dir.is_dir():
        app.mount("/vendor", StaticFiles(directory=vendor_dir), name="vendor")

    # Phase 2A — record loop events into this app's ring buffer alongside SSE
    # emission. Module-global hook; rebound per create_app() to this services.
    set_event_recorder(lambda name, payload: _record_event(services, name, payload))

    # ---- AT.2 — one Law 3 refusal, one record, every confirm route ----
    #
    # THE LINE (AT): "would not" is a governance act and is RECORDED; "was
    # never valid" is a client error and "could not" is system state, both
    # DECLARED on the response and recorded nowhere. Before AT only
    # /outcome/confirm recorded its Law 3 refusal; the other eight gates
    # refused with the same words and left nothing. This helper is the one
    # implementation: it writes the generic ledger (RecalibrationStore.
    # record_refusal, sealed like every governance row) and returns the
    # named refusal with `event_id` and `recorded` beside it. The outcome
    # route keeps its own richer outcome_events row and does not call this.
    # A guard derived from the router (tests/test_at_refusal_ledger.py)
    # posts confirmed=false to every gated route and expects the row, so a
    # new gate cannot quietly join the silent set.
    def _law3_refusal(route: str, *, request: dict | None,
                      actor: "Actor | None" = None, scope: str = "default",
                      hint: str = "explicit boolean, not implicit",
                      status_code: int = 422) -> JSONResponse:
        payload = {"error": "Law 3 — confirmed=true required", "hint": hint}
        rec = services.recalibration.record_refusal(
            route=route, rule="law3_unconfirmed",
            detail="confirmed=true required", actor=actor, scope=scope,
            request=request) if services.recalibration is not None else None
        if rec is not None:
            payload.update(event_id=rec["event_id"], recorded=True)
        return JSONResponse(payload, status_code=status_code)

    # ---- v3.1 Phase 1 / 1b — admission outside a turn ----
    #
    # The assertion and seed routes admit claims with no turn around them.
    # They are attributed to the unit the REQUEST names (created on first
    # mention, the route's actor being the vocabulary's default because
    # these routes carry no actor fields); a request naming none writes no
    # junction row and the response says so. 1a attributed them to "the
    # session's open unit"; 1b has no such thing. The pseudo-unit is a
    # turn's fallback, not a route's: a seed run naming no unit is not "the
    # session's work". A closed unit named here is attributed and declared,
    # as on the turn.
    def _admit_outside_turn(admitted: list, work_unit: str | None) -> dict:
        wu = services.work_units
        if wu is None:
            return {"work_unit_id": None, "admitted": 0, "reason": "no work-unit store"}
        unit_id = _clean_field(work_unit)
        if unit_id is None:
            return {"work_unit_id": None, "admitted": 0, "reason": "no unit named"}
        unit, declared = wu.resolve_for_turn(session_id=DEFAULT_THREAD_ID, requested_id=unit_id,
                                             actor=actor_fields(None))
        n = wu.admit(admitted, unit["work_unit_id"],
                     turn=services.state.turn_count, lineage_id=None)
        out = {"work_unit_id": unit["work_unit_id"], "admitted": n,
               "unit_first_seen": declared["unit_first_seen"]}
        if declared.get("unit_closed_at_time_of_turn"):
            out["unit_closed_at_time_of_turn"] = True
        return out

    # ---- routes (closures over `services`) ----

    # H.3b — `/health` is an ALIAS, not the canonical path. The UI has polled
    # it since it shipped and received 404 every time; `embeddings.py:31`
    # documents the same wrong path. Adding the alias fixes the server half —
    # the caller half (nexus_ui.html) mattered more, because `fetch` RESOLVES
    # on a 404 and the handler never checked `r.ok`, so the KB status light
    # read "online" whether the server was healthy, unhealthy, or serving a
    # route that did not exist. A falsely reassuring indicator is worse than
    # a broken one.
    def _frontend_status() -> dict:
        """Which page this process would serve, and what is in it.

        Read per call rather than cached: `GET /` re-reads the file on every
        request, so a `git pull` takes effect on the next page load without a
        restart — and a status surface that cached the hash would report the
        version that was current at boot, which is exactly the wrong answer to
        "is the fix deployed".
        """
        import hashlib
        if not FRONTEND.exists():
            return {"path": str(FRONTEND), "exists": False,
                    "note": "no page — GET / returns the JSON stub"}
        raw = FRONTEND.read_bytes()
        return {
            "path":   str(FRONTEND),
            "exists": True,
            "bytes":  len(raw),
            "mtime":  round(FRONTEND.stat().st_mtime, 3),
            "sha256": hashlib.sha256(raw).hexdigest()[:16],
        }

    def _key_refusal(e: Exception) -> JSONResponse:
        """W9 — a key file that is not its seed's (`key_mismatch`) or that the process cannot read
        (`key_unreadable`), declared by name; never a path, since /identity is open."""
        chain = services.archive_chain
        if isinstance(e, instance_key.KeyMismatch):
            refused, detail = "key_mismatch", str(e)
        else:
            refused, detail = "key_unreadable", (f"the instance's key file cannot be read or made ({type(e).__name__}); "
                                                 "nothing was signed (docs/OPERATOR_RUNBOOK.md, W9)")
        return JSONResponse(status_code=503, content={"refused": refused, "detail": detail, "signing": _signing(chain)})

    def _signing(chain) -> dict:
        last = getattr(chain, "last_signing_error", None)
        return {"unsigned_appends_since_start": getattr(chain, "unsigned_appends", 0),
                "last_error": last.split(":", 1)[0] if last else None}

    def _signing_declaration() -> dict:
        """W10b (D10) — whether runtime actions' requests are signed, and how; an unknown setting reads off."""
        from backend.memory import http_signatures as _hs
        return _hs.declaration()

    def _anchoring_stats() -> dict:
        from backend.memory import anchor as _anchor
        raw = (os.environ.get(_anchor.ANCHOR_SETTING) or "off").strip().lower()
        chain, kairos = services.archive_chain, services.kairos
        counts = chain.anchor_counts() if chain is not None and hasattr(chain, "anchor_counts") else {}
        return {"mode": _anchor.anchor_mode(), "setting": raw,
                "declared": None if raw in _anchor.MODES else f"unknown {_anchor.ANCHOR_SETTING}={raw!r}: read as off",
                "authority": _anchor.anchor_url() or None,
                "leaves_the_machine": "each signed block's 32-byte hash, a nonce and the request flags; nothing else",
                **counts, **(getattr(kairos, "anchor_stats", None) or {"anchored_since_start": 0,
                                                                        "failures_since_start": 0,
                                                                        "last_error": None, "last_attempt": None})}

    @app.get("/identity")
    async def identity():
        """W9 (run/specs/W9.md D3) — the instance's public key: open, since a
        public key is public. A key file that is not its seed's is refused by
        name and left as it was."""
        try:
            key = instance_key.load_or_create(services.instance_key_path, chain=services.archive_chain)
        except (instance_key.KeyMismatch, OSError) as e:      # the key alone: a store error is not the key's
            return _key_refusal(e)
        chain = services.archive_chain
        return {**instance_key.identity_document(key, chain), "signing": _signing(chain)}

    @app.get("/healthz")
    @app.get("/health")
    async def healthz():
        # R.4 — the app's provider, not the module-global one. This called
        # `is_reachable()` -> `OllamaProvider().health()`, so every /healthz
        # in a test opened a real socket regardless of what was injected.
        ok = await services.provider.health()
        # P1/F-10 — embedding degradation is surfaced, never silent.
        emb = embedding_status()
        from backend.providers.backends import backend_name
        return JSONResponse({
            # The liveness key, kept under its historical name: `ollama` is
            # the provider's health whatever the provider (W3's establish, H3).
            "ollama": ok,
            # W3 part 3 (H3) — the same fact named by role: which backend the
            # environment selected, the provider class serving it, and whether
            # it answered. 200 means NEXUS serves; this says what it serves with.
            "model_server": {"backend": backend_name(), "provider": type(services.provider).__name__,
                             "reachable": ok},
            # W3b part 3 (T13) — the store this process serves from, by role:
            # `sqlite` with the file's path, or `postgres` with the database
            # and schema (never the URL: it carries a secret).
            "store": {"backend": services.store.conn.backend, "target": services.store.conn.describe()},
            # W4 part 1 (D9) — the identity boundary this process was built
            # with: the mode, the header proxy mode reads (null in token
            # mode), and whether an owner is set. Never the owner's login:
            # this route is open in both modes.
            "identity": {"mode": identity_cfg.mode,
                         "header": identity_cfg.header if identity_cfg.is_proxy else None,
                         "owner_set": identity_cfg.owner_set},
            "kb_triples": services.store.count(),
            "embedding_degraded": emb["degraded"],
            "embedding_degraded_reason": emb["reason"],
            # W.3 — MONOTONE, and never cleared by a recovery. `degraded`
            # answers "is it broken right now"; this answers "did it break at
            # any point in this process's life", which is the only one that
            # can invalidate an index already written. A single ReadTimeout
            # during a KAIROS indexing batch sets the flag and the next
            # success clears it, so `degraded` alone would report healthy
            # while some stored vectors are hash noise.
            "embedding_fallbacks": emb["fallbacks"],
            "embedding_first_fallback_reason": emb["first_reason"],
            # BA fix 6 — the vector channel: a query failure is "unavailable",
            # never an empty result. Monotone, like the fallback counter.
            "vector_query_failures": vector_status()["failures"],
            "vector_query_last_error": vector_status()["last_error"],
            # R.2 / I-6 — the startup probe no longer blocks boot, so its
            # outcome has to be readable somewhere. `state` is one of
            # not_started | pending | ok | failed | cancelled | disabled.
            "model_probe": dict(services.model_probe),
            # THE PAGE THIS PROCESS WOULD SERVE, declared (I-6).
            #
            # Three rounds of a provenance defect went on establishing what
            # was being served, and the system knew all along: `FRONTEND` is
            # absolute and derived from `__file__`, so the answer was one
            # attribute away and appeared on no surface. "Is the fix
            # deployed?" should be a lookup, not an argument.
            #
            # `sha256` is over the file on disk, not the served response —
            # `GET /` injects a token script, so hashing the response would
            # change with the token and be useless for comparing against a
            # checkout. Compare it with:
            #     python -c "import hashlib,pathlib;
            #     print(hashlib.sha256(pathlib.Path('frontend/nexus_ui.html')
            #     .read_bytes()).hexdigest()[:16])"
            "frontend": _frontend_status(),
        })

    @app.get("/")
    async def index():
        if FRONTEND.exists():
            # Same-origin token handoff (Package A): the page's POST calls need
            # the bearer token. Localhost-bind posture — see THREAT_MODEL.md.
            # W4 part 1 (D4) — token mode only: in proxy mode the bearer is no
            # credential, so the page is served as it is and no token is minted.
            html = FRONTEND.read_text(encoding="utf-8")
            if not identity_cfg.is_proxy:
                html = html.replace(
                    "<head>",
                    f"<head><script>window.NEXUS_TOKEN={json.dumps(active_token())};</script>",
                    1,
                )
            return HTMLResponse(html)
        return JSONResponse({"status": "NEXUS", "ui": "missing"})

    @app.get("/whoami", dependencies=[Depends(require_token)])
    async def whoami(identity=Depends(established_identity)):
        """W4 part 1 (D8) — who this process takes the request to be from,
        and how it knows. Gated like the evidence reads (the ratchet's
        twelfth). Not a per-person surface: it returns no row about anyone's
        work, only the requester's own login, under keys none of which is in
        the just-culture guard's derived population. In token mode the login
        is null and the mechanism `asserted`: the body asserts who a turn is
        for. The console shows "running as <login>" from it in proxy mode.
        W4 part 1b — the mechanism is the record's word, from the one mapping
        /chat records (`_identity_mechanism_of`)."""
        from backend.identity import NOTES
        return JSONResponse({
            "auth_mode": identity_cfg.mode,
            "login": identity.login if identity is not None else None,
            "identity_mechanism": _identity_mechanism_of(identity),
            "identity_header": identity_cfg.header if identity_cfg.is_proxy else None,
            "owner_set": identity_cfg.owner_set,
            "note": NOTES[identity_cfg.mode],
        })

    @app.get("/kb/stats")
    async def kb_stats():
        # L.2a — `total` is the RECORD count and always has been; it does not
        # move when a belief is archived, which is correct (nothing was
        # deleted) and, alone, misleading. Archiving hundreds of rows while
        # every counter holds steady is silent loss on the surface whose job
        # is declaring it, so the split is stated rather than left to be
        # inferred: `retrievable` is what can still reach a retrieval slot.
        return JSONResponse({
            "total": services.store.count(),
            "retrievable": services.store.count(archived=False),
            "archived": services.store.count(archived=True),
            "by_granularity": {
                g: services.store.count(granularity=g)
                for g in ("utterance", "turn", "session", "topic")
            },
        })

    @app.get("/events/recent")
    async def events_recent(since: float = 0.0):
        """Loop events recorded since `since` (unix timestamp).

        Drives the memory-column animations from real cognitive events
        without subscribing to the chat SSE stream. Purely additive.
        """
        import time
        events = [e for e in services.recent_events if e["timestamp"] > since]
        return JSONResponse({
            "events":      events,
            "now":         time.time(),
            "buffer_size": len(services.recent_events),
        })

    @app.post("/kb/seed/offline", dependencies=[Depends(require_token)])
    async def kb_seed_offline(confirmed: bool = False, work_unit: str = ""):
        refuse = _field_too_long("work_unit", work_unit)   # v3.1 Phase 1b, scope's position
        if refuse is not None:
            return refuse
        if not confirmed:
            # AT.2 — recorded, and 422 like every other gate (was 400).
            return _law3_refusal("/kb/seed/offline",
                                 request={"confirmed": confirmed},
                                 hint="POST /kb/seed/offline?confirmed=true")
        with services.store.admissions() as admitted:
            res = seed_offline_fixture(services.store, confirmed=True)
        return JSONResponse({"seeded": res.seeded, "source": "offline_fixture",
                             "refusals": res.refusals,
                             "attribution": _admit_outside_turn(admitted, work_unit)})

    @app.get("/kb/trails")
    async def kb_trails():
        """Packet AB — the trails in force: active pairs, the sensitivity
        bound, closure vetoes, coverage, and (T.1) the Law 3 write route
        this surface proposes. Same shape as /kb/index/pending."""
        if services.trails is None:
            return JSONResponse({"enabled": False})
        return JSONResponse({
            **services.trails.status(),
            "confirm": _confirm_block(
                "/kb/trails/backfill/confirm",
                {"confirmed": True},
                "processes lineage records older than the cursor, once; a "
                "fresh install lays trails only forward from install time "
                "until this is confirmed"),
        })

    @app.post("/kb/trails/backfill/confirm",
              dependencies=[Depends(require_token)])
    async def trails_backfill(confirmed: bool = False):
        """Packet AB — lay trails over lineage records that predate the
        cursor. Law 3: history is only entered on explicit confirmation; a
        fresh install lays forward from install time and touches nothing
        older. Idempotent — a second confirm reports already_backfilled
        rather than double-counting the same records."""
        if not confirmed:
            # AT.2 — recorded, and 422 like every other gate (was 400).
            return _law3_refusal("/kb/trails/backfill/confirm",
                                 request={"confirmed": confirmed},
                                 hint="POST /kb/trails/backfill/confirm?confirmed=true")
        if services.trails is None:
            return JSONResponse({"error": "trails_unavailable"},
                                status_code=503)
        result = services.trails.backfill()
        return JSONResponse({**result, "status": services.trails.status()})

    @app.post("/kb/seed/conceptnet", dependencies=[Depends(require_token)])
    async def kb_seed_conceptnet(req: SeedConceptNetRequest, identity=Depends(established_identity)):
        refuse = _attribution_refusal(req)                  # v3.1 Phase 1b, scope's position
        if refuse is not None:
            return refuse
        if not req.confirmed:
            # AT.2 — recorded, and 422 like every other gate (was 400).
            return _law3_refusal("/kb/seed/conceptnet",
                                 request=req.model_dump(),
                                 hint="set confirmed: true in body")
        from backend.memory import actions as _actions
        try:
            with services.store.admissions() as admitted:
                res = await seed_conceptnet(
                    req.concepts, confirmed=True,
                    store=services.store, per_concept_limit=req.per_concept_limit,
                    action_store=services.lineage,
                    trigger=_actions.Trigger(route="/kb/seed/conceptnet", accountable_identity=_login_of(identity),
                                             identity_mechanism=_identity_mechanism_of(identity)),
                )
        except _actions.ActionNotRecorded as e:
            return _action_not_recorded(e)
        return JSONResponse({
            "seeded":   res.seeded,
            "source":   "conceptnet",
            "concepts": req.concepts,
            "warnings": res.warnings,  # Fix 3 — per-concept HTTP / network failures
            "refusals": res.refusals,  # rows the ingest gate declined
            "attribution": _admit_outside_turn(admitted, req.work_unit),
        })

    @app.post("/kb/seed/wikidata", dependencies=[Depends(require_token)])
    async def kb_seed_wikidata(req: SeedWikidataRequest, identity=Depends(established_identity)):
        """Fix 1 — Wikidata SPARQL seed endpoint, Law 3 gated."""
        refuse = _attribution_refusal(req)                  # v3.1 Phase 1b, scope's position
        if refuse is not None:
            return refuse
        if not req.confirmed:
            # AT.2 — recorded, and 422 like every other gate (was 403).
            return _law3_refusal("/kb/seed/wikidata",
                                 request=req.model_dump(),
                                 hint="POST with confirmed=true")
        from backend.memory import actions as _actions
        try:
            with services.store.admissions() as admitted:
                res = await seed_wikidata(
                    req.concepts, confirmed=True,
                    store=services.store, per_concept_limit=req.per_concept_limit,
                    action_store=services.lineage,
                    trigger=_actions.Trigger(route="/kb/seed/wikidata", accountable_identity=_login_of(identity),
                                             identity_mechanism=_identity_mechanism_of(identity)),
                )
        except _actions.ActionNotRecorded as e:
            return _action_not_recorded(e)
        return JSONResponse({
            "seeded":   res.seeded,
            "source":   "wikidata",
            "concepts": req.concepts,
            "warnings": res.warnings,  # P1/F-10 — parity with ConceptNet
            "refusals": res.refusals,  # rows the ingest gate declined
            "attribution": _admit_outside_turn(admitted, req.work_unit),
        })

    def _embedder_block() -> dict:
        """W3 (H5) — the provider's declaration (`embedder_declaration`), the
        provider's class, and the two idle scans' consequence of it. A
        declaration, not a probe: `/healthz.embedding_degraded` says whether
        a declared embedder answered."""
        from backend.vector.embeddings import embedder_declaration
        declared = embedder_declaration(services.provider)
        state = "runs" if declared["has_embedder"] else f"skipped: {declared['reason']}"
        return {**declared, "provider": type(services.provider).__name__,
                "dedup_scan": state, "semantic_index": state,
                "note": ("the dedup scan and the semantic indexer embed through the provider on the idle "
                         "cycle; a provider that declares no embedder is not asked, and the cycle result "
                         "says so (/kairos/status.last_cycle.dedup_skipped_reason)")}

    def _semantic_index_stats() -> dict:
        idx = services.semantic
        if idx is None:
            return {"enabled": False, "slots": 0, "indexed": 0,
                    "live_beliefs": 0, "pending": 0, "models": [],
                    "note": "no semantic index on this app"}
        cov = idx.coverage()
        # W.2 — the denominator is what the index is WILLING to hold, not every
        # live belief. With substrate relations excluded, `live_beliefs` would
        # leave `pending` stuck at ~2/3 of the store forever and read as a
        # permanently failing backfill.
        indexable = idx.indexable_count()
        return {
            "enabled":            semantic_slots() > 0,
            "slots":              semantic_slots(),
            "indexed":            cov["indexed"],
            "indexable_beliefs":  indexable,
            "live_beliefs":       services.store.count(archived=False),
            "pending":            max(0, indexable - cov["indexed"]),
            "models":             cov["models"],
            "dim":                cov["dim"],
            "excluded_relations": cov["excluded_relations"],
            "min_similarity":     semantic_min_similarity(),
        }

    @app.get("/kb/index/pending")
    async def kb_index_pending():
        """W.1 — how much of the store retrieval can reach semantically.

        The read half of the Law 3 pair, in the same shape as
        `/kb/dedup/pending`. It exists because "retrieval found nothing" and
        "retrieval could not look" are different answers and nothing else on
        the surface distinguishes them: a store with 867 beliefs and 0
        embeddings answers every question lexically and reports no error.
        """
        stats = _semantic_index_stats()
        sample = (services.semantic.unindexed(limit=10)
                  if services.semantic is not None else [])
        return JSONResponse({
            **stats,
            "sample_unindexed": [{"triple_id": i, "surface": s}
                                 for i, s in sample],
            "confirm": _confirm_block(
                "/kb/index/confirm",
                {"confirmed": True, "limit": 200},
                "one embedding call per un-indexed belief; KAIROS also "
                "indexes a batch per idle cycle while the channel is on"),
        })

    @app.post("/kb/index/confirm", dependencies=[Depends(require_token)])
    async def kb_index_confirm(req: IndexBackfillReq):
        """Law 3 — backfill the semantic index over existing beliefs.

        KAIROS indexes a batch per idle cycle, which reaches full coverage on
        a store of any size eventually and on the operator's 867 rows in a
        couple of hours of idle time. This is the deliberate version of the
        same operation, and it goes through a confirm gate for the reason Law
        3 names: it is one provider call per belief, so on a large store it is
        a bulk outbound operation someone should have asked for.

        It writes NO beliefs and changes no weights — it fills a derived
        cache. That is why the gate is `confirmed=true` and not an envelope.
        """
        if not req.confirmed:
            # AT.2 — recorded, and a NAMED error (this was an HTTPException
            # in the schema shape, the one gate outside the two-shape rule).
            return _law3_refusal("/kb/index/confirm",
                                 request=req.model_dump(),
                                 hint="backfilling the semantic index issues "
                                      "one embedding call per un-indexed belief")
        if services.semantic is None:
            raise HTTPException(status_code=503,
                                detail="no semantic index on this app")
        result = await index_pending(services.semantic, services.provider,
                                     limit=req.limit)
        return JSONResponse({**result, "coverage": _semantic_index_stats()})

    def _capture_coverage() -> dict:
        from backend.eval.citation_capture import coverage
        return coverage()

    def _fire_capture_coverage() -> dict:
        from backend.eval.fire_capture import coverage
        return coverage()

    def _mcp_coverage() -> dict:
        from backend.mcp.coverage import counters, first_run
        from backend.mcp.mcp_server import MCP_CONTRACT_VERSION
        from backend.memory.lineage import RECORD_SCOPES
        return {"contract_version": MCP_CONTRACT_VERSION,
                "coverage": counters(services.lineage.conn),
                "first_run": first_run(services.lineage.conn),
                "record_scopes": list(RECORD_SCOPES),
                "note": ("counts of what the MCP process observed, read from kb.db on every call; "
                         "the HTTP server never runs the MCP tools")}

    def _recall_coverage() -> dict:
        """AR — derived from the record on every call; nothing is cached."""
        sup = services.recalibration.superseded()
        return {
            "superseded_claims": len(sup["claims"]),
            "records_resting": services.lineage.records_resting_on(sup["claims"]),
            "since": sup["since"],
            "supersede_events": sup["events"],
            "index": services.lineage.recall_index_coverage(),
            "read": "/belief/{belief_id}/recall",
            "note": ("counts derived from the record, never stored; a count, "
                     "not a rate. `records_resting` reads the injected set. "
                     "The recall route returns per-person rows and declares it."),
        }

    def _archival_ledger_stats() -> dict:
        from backend.kb.tiered_store import archival_ledger_enabled
        row = services.store.conn.execute(
            "SELECT COUNT(*) AS n, MIN(ts) AS begin FROM archival_events"
        ).fetchone()
        return {
            "enabled": archival_ledger_enabled(),
            "events": int(row["n"]),
            "records_begin_at": row["begin"],
            "note": ("an empty ledger means nothing recorded yet, not "
                     "nothing ever archived — acts before the ledger "
                     "shipped were never recorded and cannot be recovered "
                     "(forward-only, the records_begin_at class)."),
        }

    @app.get("/cortex/stats")
    async def cortex_stats():
        g = services.hippocampus.graph
        n_edges = g.number_of_edges()
        return JSONResponse({
            "nodes": g.number_of_nodes(),
            "edges": n_edges,
            "mean_edge_weight": (
                sum(e.weight for e in services.hippocampus.edges()) / max(1, n_edges)
            ),
            # T.2 / I-6 — the opposition set in force, and where it came from.
            # An operator who installed thirty pairs and is silently running on
            # the compiled seven (unreadable file, bad JSON) has no other way
            # to find out. `error` is empty on a clean load.
            # V.1 / I-6 — the relation vocabulary offered to extraction, and
            # where it came from. An operator who installed sixty predicates
            # and is silently running on the compiled thirty-three (bad path,
            # bad JSON) has no other way to find out.
            "relation_vocabulary": {
                "enabled":   vocabulary_enabled(),
                "count":     len(load_relations()),
                "relations": list(load_relations()),
                **vocabulary_source(),
            },
            "contradiction_pairs": {
                "count":  len(services.contradiction.pairs),
                "pairs":  [list(p) for p in services.contradiction.pairs],
                **pairs_source(),
            },
            # W.1 / I-6 — how much of the store is reachable semantically.
            # "retrieval found nothing" and "retrieval could not look" are
            # different answers and an operator cannot tell them apart from
            # the outside. `slots = 0` means the channel is off whatever the
            # coverage says, which is why both numbers are here.
            "semantic_index": _semantic_index_stats(),
            # W3 (H5) / I-6 — what the provider declares about its embedder
            # and what the idle scans do with the declaration: `runs`, or
            # `skipped: <the provider's reason>` (the stub; an api backend
            # naming no NEXUS_EMBED_URL and NEXUS_EMBED_MODEL). Without it, an
            # instance whose scans never embed reads like one whose provider
            # is down.
            "embedder": _embedder_block(),
            # AD / I-6 — declared coverage for the scope field, the
            # records_begin_at answer borrowed verbatim: a 'default' before
            # `recordable_since` means "could not be stated"; after it,
            # "the request stated none". Two facts one value cannot carry,
            # made decidable by the timestamp.
            "scope": {
                "recordable_since": services.lineage.scope_recordable_since(),
                "default_value": "default",
                "note": ("records before recordable_since predate the field "
                         "and read 'default' forever; after it, 'default' "
                         "means the request stated no scope. Scope is "
                         "recorded, never inferred."),
            },
            # AO / I-6 — the capture instrument's coverage. Fifth instance
            # of the coverage class, with the forward-only warning built in:
            # turns before capture was enabled are unlabellable forever.
            "citation_capture": _capture_coverage(),
            # CP / I-6 — the fire capture's coverage: the gate, `fire_rows`,
            # `last_fire_turn` and B-49's tally so far (`removed_total`,
            # `present_next`, `absent_next`, `demoted_next`, numbers only),
            # read from the file on every call. Reports; decides nothing.
            "fire_capture": _fire_capture_coverage(),
            # MCP-2 / I-6 — the MCP coverage counters, read from the table the
            # MCP process writes beside the record (`present: false` on a
            # store it never opened — zero and absent are different facts),
            # the contract version and the scope vocabulary. The HTTP app
            # constructs nothing MCP; this is a read.
            "mcp": _mcp_coverage(),
            # AR / I-6 — the recall coverage block: superseded claims and the
            # sealed records resting on at least one of them, recomputed from
            # the record on every read (a count, never a stored number, never
            # a rate). The seed of the knowledge-debt figure; not the figure.
            "recall": _recall_coverage(),
            # FZ-2 / I-6 — whether the learning layer is writing. `state`,
            # the operator-asserted from-date (`since_source: asserted`),
            # the methods that check it, and the skips counted since this
            # process started — so frozen and broken read differently.
            "learning": learning_status(),
            # AL / I-6 — the archival act ledger's coverage, the fourth
            # instance of the records_begin_at class. An empty ledger must
            # read as "nothing recorded yet", never as "nothing ever
            # archived": every archival before the ledger shipped is
            # invisible to it, forward-only.
            "archival_ledger": _archival_ledger_stats(),
            # AS-1 / I-6 — the as-of coverage block: the earliest admission
            # (before it an as-of is empty by construction, declared), the
            # timed archival events and the supersede acts, derived on every
            # read. Read surface: GET /kb/as-of (gated).
            "as_of": _as_of.coverage(services.store.conn),
            # W9 (D7) — anchoring: what leaves the machine, and how far behind
            # the stamps are. Off by default; an unknown setting reads off.
            "anchoring": _anchoring_stats(),
            # W10b — the governed actions, by evidence class and state, derived on every read: a
            # client's report is never counted as runtime. Read surface: GET /actions/{id} (gated).
            "actions": {**services.lineage.action_counts(), "signing": _signing_declaration()},
            # AT / I-6 — the refusal ledger's coverage and the line it
            # records, beside the archival ledger's. Read surface:
            # GET /governance/refusals (gated).
            "refusal_ledger": {**services.recalibration.refusal_coverage(),
                               "read": "/governance/refusals"},
            # W0b / I-6 — the replay capture: the two switches in force, how
            # many records carry each capture field, and the kept request
            # bytes (raw and stored), derived from the store on every read.
            "replay_capture": services.lineage.capture_stats(),
            # W5 / I-6 — the reference gateway: its state (an unrecognised
            # setting is named and treated as off), the policy's rows, and
            # what it withheld or refused since start, so that "refused
            # nothing" and "is off" read differently.
            "reference_gateway": _source_policy.declaration(),
            # W6 — the recorder: whether an answer is released after its
            # record (the default) or live, and the failed writes since start.
            "recorder": recorder_declaration(),
            # AK / I-6 — whether retrieved substrate rows may copy their
            # stored (monotone, high-water) weight onto live edges, and how
            # many such restorations were suppressed this session. Declared
            # because the leak ran silently for the life of the project.
            "weight_restoration": {
                "substrate_restore_enabled": substrate_weight_restore_enabled(),
                "suppressed_total":
                    services.hippocampus.restorations_suppressed,
                "note": ("a substrate row's stored weight is the record "
                         "layer's high-water mark; copying it onto a live "
                         "edge undid decay (I-8). Suppressions counted "
                         "here; NEXUS_SUBSTRATE_WEIGHT_RESTORE=on is the "
                         "pre-AK behaviour."),
            },
            # AB / I-6 — the relation trails in force: active pairs, the
            # sensitivity bound (structurally_unconfirmed — frequency-
            # qualified pairs the shared-(s,o) leg cannot confirm), the
            # closure vetoes, and declared coverage. The backfill is Law 3;
            # the confirm block names its route (T.1).
            "relation_trails": {
                **services.trails.status(),
                "confirm_backfill": _confirm_block(
                    "/kb/trails/backfill/confirm",
                    {"confirmed": True},
                    "processes lineage records older than the cursor, once; "
                    "a fresh install lays trails only forward from install "
                    "time until this is confirmed"),
            } if services.trails is not None else {"enabled": False},
        })

    @app.get("/kairos/status")
    async def kairos_status():
        last = services.kairos.last_cycle
        return JSONResponse({
            "is_idle": services.kairos.is_idle,
            "session_active": services.kairos.session_active,
            "last_cycle": last.__dict__ if last else None,
        })

    @app.post("/kairos/cycle", dependencies=[Depends(require_token)])
    async def kairos_force_cycle(force_snapshot: bool = True):
        # D.3 — this is the manual force-cycle control; it exists to commit a
        # seal before session end, so it forces by default. The background
        # cadence (KairosProcess.run) is unchanged and still respects the
        # interval. Pass ?force_snapshot=false to exercise the gate manually.
        services.kairos.session_ended()
        res = await services.kairos.run_one_cycle(force_snapshot=force_snapshot)
        return JSONResponse(res.__dict__)

    @app.get("/chain/status")
    async def chain_status(audit: bool = False):
        """`?audit=true` adds `snapshot_audit` — the file-level check that
        `verify_chain()` deliberately is not (see `audit_snapshots()`).
        OPT-IN because it re-reads every snapshot file, disk I/O proportional
        to chain length, and the UI polls this endpoint on a timer; the
        default response is byte-identical to pre-audit behaviour."""
        chain = services.archive_chain
        latest = chain.latest()
        verification = chain.verify_chain()
        body = {
            "n_blocks":     chain.count(),
            "latest_block": latest.to_dict() if latest else None,
            "verification": verification.to_dict(),
        }
        if audit:
            body["snapshot_audit"] = chain.audit_snapshots()
        return JSONResponse(body)

    @app.get("/memory/column")
    async def memory_column():
        """
        Unified depth-ordered payload for the memory column visualisation.
        Read-only assembly from existing services. No new computation.

        Returns:
          surface:    hippocampus nodes + edges (live session memory)
          neocortex:  consolidated triples (tier <= 2 OR fire_count >= 7)
          archive:    sealed blocks with fingerprints
          procedural: cross-cutting patterns
        """
        import time

        # Surface — current hippocampus state
        try:
            hippo = services.hippocampus
            surface_nodes = [
                {
                    "id":         str(n),
                    "ep":         hippo.graph.nodes[n].get("epistemic_state", "unknown"),
                    # P2/F-02 — evidential_weight canonical; confidence = deprecated alias.
                    "evidential_weight": float(hippo.graph.nodes[n].get("confidence", 0.5)),
                    "confidence": float(hippo.graph.nodes[n].get("confidence", 0.5)),
                    "fire_count": int(hippo.graph.nodes[n].get("fire_count", 0)),
                }
                for n in list(hippo.graph.nodes())[:80]
            ]
            surface_edges = [
                {
                    "source":  str(u),
                    "target":  str(v),
                    "weight":  float(hippo.graph.edges[u, v].get("current_weight", 0.5)),
                }
                for u, v in list(hippo.graph.edges())[:200]
            ]
        except Exception:
            surface_nodes, surface_edges = [], []

        # Neocortex — consolidated triples from tiered store
        try:
            # Query for triples that have been consolidated
            # Tier 0-2 OR high fire_count = neocortex layer
            all_triples = services.store.fetch_all(limit=120) \
                if hasattr(services.store, 'fetch_all') \
                else []
            neocortex = []
            for t in all_triples:
                fc = getattr(t, 'fire_count', 0) or 0
                tier = getattr(t, 'tier', 99) or 99
                # F.3 — membership in the neocortex layer is a claim about
                # HOW a belief was learned, not about how salient it is right
                # now. The old predicate (`tier <= 2 or fc >= 7`) decided a
                # structural question with a prominence value, so under F.3 a
                # genuinely consolidated belief that had not been salient
                # recently would sink to tier 3 and vanish from the
                # consolidated-memory panel — the panel would start lying
                # precisely when forgetting began working.
                source = getattr(t, 'source', '') or ''
                gran = str(getattr(t, 'granularity', '') or '')
                if source == 'hippocampus_consolidation' or gran == Granularity.SESSION.value:
                    neocortex.append({
                        "subject":    getattr(t, 'subject', ''),
                        "relation":   getattr(t, 'relation', ''),
                        "object":     getattr(t, 'object', ''),
                        "weight":     float(getattr(t, 'weight', 0.5) or 0.5),
                        "fire_count": int(fc),
                        "tier":       int(tier),
                        "granularity":str(getattr(t, 'granularity', 'topic')),
                    })
        except Exception:
            neocortex = []

        # Archive — sealed blocks with fingerprints
        try:
            chain = services.archive_chain
            blocks = []
            if hasattr(chain, 'all_blocks'):
                blocks = chain.all_blocks()
            elif hasattr(chain, 'get_blocks'):
                blocks = chain.get_blocks()
            elif hasattr(chain, 'fetch_all'):
                blocks = chain.fetch_all()

            archive = []
            for i, b in enumerate(blocks[-20:]):  # last 20 blocks
                fp = getattr(b, 'fingerprint', None) or {}
                archive.append({
                    "index":      i,
                    "block_hash": (getattr(b, 'block_hash', '') or '')[:16],
                    "timestamp":  float(getattr(b, 'timestamp', 0) or 0),
                    "fingerprint": {
                        "node_count":         int(fp.get('node_count', 0)),
                        "edge_count":         int(fp.get('edge_count', 0)),
                        "avg_clustering":     float(fp.get('avg_clustering', 0.0)),
                        "density":            float(fp.get('density', 0.0)),
                        "hub_count":          int(fp.get('hub_count', 0)),
                        "cross_domain_count": int(fp.get('cross_domain_count', 0)),
                    } if isinstance(fp, dict) else {},
                })
        except Exception:
            archive = []

        # Procedural — cross-cutting patterns
        try:
            proc = services.procedural
            patterns = []
            if hasattr(proc, 'get_all_patterns'):
                patterns = proc.get_all_patterns()
            elif hasattr(proc, 'patterns'):
                patterns = list(proc.patterns)

            procedural_list = [
                {
                    "pattern": str(getattr(p, 'pattern', p)),
                    "count":   int(getattr(p, 'count', 1)),
                }
                for p in patterns[:30]
            ]
        except Exception:
            procedural_list = []

        return {
            "surface":    {"nodes": surface_nodes, "edges": surface_edges},
            "neocortex":  neocortex,
            "archive":    archive,
            "procedural": procedural_list,
            "timestamp":  time.time(),
        }

    @app.get("/memory", response_class=HTMLResponse)
    async def memory_view():
        """Serve the memory column visualisation."""
        from pathlib import Path
        path = Path(__file__).parent.parent / "frontend" / "nexus_memory.html"
        if not path.exists():
            return HTMLResponse("<h1>nexus_memory.html not found</h1>", status_code=404)
        return HTMLResponse(path.read_text(encoding="utf-8"))

    @app.get("/workers/active")
    async def workers_active():
        """Read-only view of active workers — UI inspector panel.
        Phase 10c will spawn workers; this endpoint is the read surface."""
        wm = services.worker_manager
        if wm is None:                                # pragma: no cover
            return JSONResponse({"active": 0, "workers": []})
        active = [
            {"task_id": w.task_id, "task_type": w.task_type,
             "status":  w.status,  "turns_used": w.turns_used,
             "turn_limit": w.turn_limit}
            for w in wm._workers.values()
            if w.status == "active"
        ]
        return JSONResponse({"active": len(active), "workers": active})

    @app.get("/cortex/drift")
    async def cortex_drift():
        """Fix B — drift assessment between the latest two snapshot
        fingerprints. Returns 'insufficient_history' until at least two
        chain blocks exist."""
        blocks = services.archive_chain.load_recent_blocks(n=2)
        if len(blocks) < 2:
            return JSONResponse({"status": "insufficient_history"})
        fp_prev = CortexFingerprint.from_dict(blocks[-2].fingerprint)
        fp_curr = CortexFingerprint.from_dict(blocks[-1].fingerprint)
        a = assess_drift(
            fp_prev, fp_curr, services.kairos.consecutive_drift_warns,
        )
        return JSONResponse(a.to_dict())

    @app.get("/curiosity/recent")
    async def curiosity_recent(top_k: int = 5):
        nodes = services.curiosity.find_curious_nodes(
            services.hippocampus, top_k=top_k,
        )
        return JSONResponse({"nodes": [n.to_dict() for n in nodes]})

    # ---------- P3 / L2: answer lineage (auth — evidence records are ----------
    # ---------- more sensitive than the open read posture; pack-mandated) ----

    @app.get("/lineage/recent", dependencies=[Depends(require_token)])
    async def lineage_recent(limit: int = 20):
        recs = services.lineage.recent(limit=max(1, min(limit, 200)))
        return JSONResponse({
            "records": [r.to_dict() for r in recs],
            "total":   services.lineage.count(),
        })

    # H.1 — the two-layer tamper result, exposed. REGISTERED BEFORE
    # `/lineage/{lineage_id}`: FastAPI matches in declaration order, so a
    # literal path declared after a path-parameter route on the same prefix
    # would never be reached — `verify` would arrive as a lineage_id and 404.
    @app.get("/lineage/verify", dependencies=[Depends(require_token)])
    async def lineage_verify_all(limit: int | None = None):
        """Verify every answer-lineage record at all three layers.

        Layers are reported separately and never collapsed into a boolean: a
        consistent forgery passes `row` and fails `seal`, and that distinction
        is the guarantee. `unsealed` is a declared state, not a failure — a
        record written since the last KAIROS cycle is simply awaiting its
        seal (I-6).
        """
        return JSONResponse(verify_sealed_lineage(
            services.lineage, services.archive_chain,
            limit=None if limit is None else max(1, min(limit, 1000)),
        ))

    @app.get("/lineage/{lineage_id}/verify", dependencies=[Depends(require_token)])
    async def lineage_verify_one(lineage_id: str):
        """The same three layers for a single answer — the demo path:
        *can this specific claim's evidence still be proven intact?*"""
        result = verify_sealed_lineage(
            services.lineage, services.archive_chain, lineage_id=lineage_id,
        )
        if result.get("exists") is False:
            return JSONResponse({"error": "unknown lineage_id"}, status_code=404)
        return JSONResponse(result)

    @app.get("/belief/{belief_id}/provenance",
             dependencies=[Depends(require_token)])
    async def belief_provenance(belief_id: int):
        """S.3 — why this belief weighs what it weighs.

        A query over `recalibration_events`, indexed by belief, each step
        resolved to the outcome that caused it, the actor who confirmed it and
        the envelope version that permitted it.

        GATED, deliberately, and for the same reason every `/lineage/*` route
        is: this is evidence about decisions, and it names the people who made
        them. (`/governance/envelope` was open because a policy is meant to
        be inspectable; B-40 gated it too, because its history names the
        actors who installed each policy.)

        A belief that does not resolve is REPORTED (`resolved: false`), not
        404'd — H.2's discipline, and the reason is the same: an unresolved
        belief in an answer's evidence is a finding, and a surface that hides
        it makes the answer look better supported than it was.
        """
        trace = provenance_for(services.store, services.recalibration,
                               belief_id=belief_id)
        return JSONResponse(trace.to_dict())

    # ---- AR (advice recall): the backward read ----
    #
    # Given a claim, every sealed answer that rested on it. Read from the
    # INJECTED set, never the cited flag (the flag under-reports by about a
    # fifth; a recall that misses affected answers is wrong in the unsafe
    # direction). Nothing here writes; nothing changes standing; the
    # supersede act (`/outcome/confirm` with `notes: superseded_by: …`)
    # returns this count and this route.
    #
    # GATED like every /lineage/* and /belief/* evidence read: it names the
    # people who were answerable for those answers. PER-PERSON ROWS: each
    # row carries `accountable` as recorded, labelled by the record's
    # `identity_mechanism` (`asserted` or `proxy`; NEXUS itself authenticates
    # no person). NEXUS never aggregates by person; a surface that returns
    # per-person rows says so here, in CLAUDE.md and in the ratchet
    # (PER_PERSON_ROWS), because a consumer can aggregate them in one line
    # and the declaration is what makes that a choice, not a surprise.
    # W4 — the contract's version (2) and the declaration are defined once,
    # beside the rows, for this route and MCP's `nexus_recall`.
    from backend.memory.lineage import RECALL_CONTRACT_VERSION, RECALL_PER_PERSON

    def recall_route_for(belief_id: int) -> str:
        return f"/belief/{int(belief_id)}/recall"

    @app.get("/belief/{belief_id}/recall", dependencies=[Depends(require_token)])
    async def belief_recall(belief_id: int, limit: int = 100, offset: int = 0):
        """AR — every sealed answer that rested on this claim: the injected
        set read backwards, oldest first, paginated. Read-only."""
        triple = services.store.get_by_id(belief_id)
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        rows, total = services.lineage.records_injecting(belief_id, limit=limit, offset=offset)
        return JSONResponse({
            "belief_id": belief_id,
            "resolved": triple is not None,
            "claim": (None if triple is None else {
                "subject": triple.subject, "relation": triple.relation,
                "object": triple.object, "source": triple.source,
                "archived": bool(getattr(triple, "archived", False))}),
            "records": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "sorted_by": "ts ascending",
            "read_from": "injected",
            "per_person": RECALL_PER_PERSON,
            "note": ("built from the injected set, not the cited flag: the flag "
                     "under-reports by about a fifth and a recall that misses "
                     "affected answers is wrong in the unsafe direction. "
                     "Over-inclusion is the safe direction."),
            "contract_version": RECALL_CONTRACT_VERSION,
        })

    # AS-1 — as-of on transaction time. What the store held at an instant:
    # a filter over timestamps the store already records (first admission,
    # the archival ledger read in order, the sealed supersede selector).
    # GATED like every evidence read (A-2). No standing at D (A-1): no
    # column has a timed history. The fixed sentence rides every response.
    @app.get(_as_of.ROUTE, dependencies=[Depends(require_token)])
    async def kb_as_of(date: str, subject: str | None = None, relation: str | None = None,
                       source: str | None = None, limit: int = 100, offset: int = 0):
        """AS-1 — every claim held at the instant `date` names, under the
        predicate: a claim is held at D if it was first admitted on or before
        D, and, reading its archival events up to D in order, its last event
        is not an archival; a claim whose supersede act is dated on or before
        D is held and superseded. Read-only; paginated; ordered by admitted-at
        then claim key."""
        try:
            instant = _as_of.resolve_instant(date)
        except _as_of.AsOfRefused as e:
            # AS-1b — a named refusal in `scope_too_long`'s shape (422, a
            # client error, recorded nowhere): a bare date or a naive
            # instant has no zone, so the conversion belongs in the client.
            return JSONResponse(e.payload(), status_code=422)
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        conn = services.store.conn
        earliest = _as_of.earliest_admission(conn)
        ledger = _as_of.archival_ledger_from(conn)
        rows, total = _as_of.held_at(conn, instant["ts"], subject=subject, relation=relation,
                                     source=source, limit=limit, offset=offset)
        if earliest is None:
            reason = "the store holds no claim"
        elif instant["ts"] < earliest:
            reason = (f"before the earliest admission at {_as_of.iso_utc(earliest)}: "
                      f"empty by construction")
        else:
            reason = None
        return JSONResponse({
            "as_of": instant,
            "claims": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "sorted_by": "admitted_at ascending, then claim key (subject, relation, object, source)",
            "filters": {"subject": subject, "relation": relation, "source": source},
            "earliest_admitted_at": earliest,
            "reason": reason,
            # AS-1b — the ledger's first recorded act, never its beginning;
            # null with a stated reason on a store with no event.
            "archival_ledger_from": ledger["iso"],
            "archival_ledger_reason": ledger["reason"],
            "predicate": _as_of.PREDICATE,
            "read_from": _as_of.READ_FROM,
            "note": _as_of.FIXED_SENTENCE + " " + _as_of.ledger_sentence(ledger["iso"]),
            "contract_version": _as_of.CONTRACT_VERSION,
        })

    @app.get("/lineage/{lineage_id}/proof", dependencies=[Depends(require_token)])
    async def lineage_proof(lineage_id: str):
        """W8 — the record's inclusion proof (run/specs/W8.md, D7): the leaf,
        its index, the tree's size, the audit path and the block's header (or,
        for a block sealed before W8, its body), verifiable from the proof
        alone by `backend/memory/proofs.verify_proof`. Gated, as every
        /lineage read is. An unsealed record says so."""
        from backend.memory import proofs as _proofs
        proof = _proofs.build_proof(services.lineage, services.archive_chain, lineage_id)
        if proof is None:
            return JSONResponse({"error": "unknown lineage_id"}, status_code=404)
        return JSONResponse(proof)

    @app.post("/commit", dependencies=[Depends(require_token)])
    async def commit_now(req: CommitRequest):
        """W9 (run/specs/W9.md D8; the operator's decision 2) — commit now,
        reveal later: a salted commitment to `content`, kept here with its
        salt and sealed by the next seal. Law 3: nothing is held without
        `confirmed=true`; unconfirmed, the offer comes back and the refusal is
        recorded (by the request's digest, never its text). The content enters
        no prompt, retrieval or record; the response is the only place the
        salt is handed back, and the person keeps it to reveal."""
        content = req.content if isinstance(req.content, str) else ""
        if not content:
            return JSONResponse({"error": "empty_content", "hint": "send the note to commit to"}, status_code=422)
        if not req.confirmed:
            resp = _law3_refusal("/commit", request={"content_chars": len(content), "confirmed": False})
            body = json.loads(resp.body)
            body["offer"] = {"content_chars": len(content), "salt_bytes": 32,
                             "sealed_by": "the next seal, as a leaf of its block's commit root"}
            return JSONResponse(body, status_code=422)
        made = services.lineage.hold_commitment(content)
        return JSONResponse({**made, "sealed": False,
                             "note": "keep the salt: it is never shown again; the receipt is at "
                                     f"/commitments/{made['commitment_id']}/receipt once the next seal covers it"})

    @app.get("/commitments/{commitment_id}/receipt", dependencies=[Depends(require_token)])
    async def commitment_receipt(commitment_id: str):
        """W9 (D8) — the receipt a person keeps to reveal a held commitment
        later with `nexus-verify --reveal`; no content, no salt. Gated."""
        from backend.memory import credentials as _credentials
        try:
            instance_key.load_or_create(services.instance_key_path, chain=services.archive_chain)
        except (instance_key.KeyMismatch, OSError) as e:      # the key alone: a store error is not the key's
            return _key_refusal(e)
        try:
            return JSONResponse(_credentials.commitment_receipt(services.lineage, services.archive_chain,
                                                                commitment_id, key_path=services.instance_key_path))
        except _credentials.CredentialRefused as e:
            reason = str(e).split(":", 1)[0]
            return JSONResponse({"refused": reason, "detail": str(e)},
                                status_code=404 if reason == "no_commitment" else 409)

    @app.get("/identity/signed-action")
    async def signed_action(signature: str = ""):
        """W10b (run/specs/W10b.md D10; Ruling 8) — for whoever received a
        request this instance signed: the sealed action behind that signature,
        as its receipt, and nothing else (no turn's content). Open, beside the
        key directory it is checked against, because its caller holds the
        signature and no token; declared per-person, because the action names
        the identity accountable for it."""
        from backend.memory import credentials as _credentials
        row = services.lineage.action_by_signature(signature.strip()) if signature.strip() else None
        if row is None:
            return JSONResponse({"refused": "no_action", "detail": "no action this instance recorded carried that "
                                 "signature"}, status_code=404)
        try:
            instance_key.load_or_create(services.instance_key_path, chain=services.archive_chain)
        except (instance_key.KeyMismatch, OSError) as e:
            return _key_refusal(e)
        try:
            return JSONResponse(_credentials.action_receipt(services.lineage, services.archive_chain,
                                                            row["action_id"], key_path=services.instance_key_path,
                                                            disclose_identity=False))
        except _credentials.CredentialRefused as e:
            reason = str(e).split(":", 1)[0]
            return JSONResponse({"refused": reason, "detail": str(e)}, status_code=409)

    @app.get("/actions/{action_id}", dependencies=[Depends(require_token)])
    async def action_receipt(action_id: str):
        """W10b (run/specs/W10b.md D7) — a governed action's receipt, for a
        stranger to check offline with `nexus-verify --action`: the action's
        fields, its path to the sealing block's `action` root, the block's
        header and signature and the key list. Gated, and declared per-person:
        it names the identity accountable for the action."""
        from backend.memory import credentials as _credentials
        try:
            instance_key.load_or_create(services.instance_key_path, chain=services.archive_chain)
        except (instance_key.KeyMismatch, OSError) as e:      # the key alone: a store error is not the key's
            return _key_refusal(e)
        try:
            return JSONResponse(_credentials.action_receipt(services.lineage, services.archive_chain, action_id,
                                                            key_path=services.instance_key_path))
        except _credentials.CredentialRefused as e:
            reason = str(e).split(":", 1)[0]
            return JSONResponse({"refused": reason, "detail": str(e)},
                                status_code=404 if reason == "no_action" else 409)

    @app.get("/lineage/{lineage_id}/credential", dependencies=[Depends(require_token)])
    async def lineage_credential(lineage_id: str, disclose: str = ""):
        """W9 (run/specs/W9.md D5) — the record's decision credential, for a
        stranger to check offline with `scripts/nexus_verify.py`: the fields
        named in `disclose` (comma-separated) with their values and salts,
        every other field as its salted commitment, the proof, the instance's
        signatures and its key list. Gated, as every /lineage read is. A record
        that cannot be proved is refused by name (409), never issued."""
        from backend.memory import credentials as _credentials
        fields = [f.strip() for f in disclose.split(",") if f.strip()]
        try:
            instance_key.load_or_create(services.instance_key_path, chain=services.archive_chain)
        except (instance_key.KeyMismatch, OSError) as e:      # the key alone: a store error is not the key's
            return _key_refusal(e)
        try:
            return JSONResponse(_credentials.build_credential(services.lineage, services.archive_chain, lineage_id,
                                                              key_path=services.instance_key_path, disclose=fields))
        except _credentials.CredentialRefused as e:
            reason = str(e).split(":", 1)[0]
            return JSONResponse({"refused": reason, "detail": str(e)}, status_code=404 if reason == "no_record" else 409)

    @app.get("/lineage/{lineage_id}", dependencies=[Depends(require_token)])
    async def lineage_get(lineage_id: str):
        rec = services.lineage.get(lineage_id)
        if rec is None:
            return JSONResponse({"error": "unknown lineage_id"}, status_code=404)
        # Resolve injected belief ids against the live store. Beliefs that no
        # longer resolve are reported honestly, never papered over.
        #
        # H.2 — THREE WEIGHTS, THREE NAMES (I-5, extended).
        # I-5 says "two weights, never one label". The store actually holds
        # three distinct quantities, and this endpoint previously exposed the
        # two least comparable ones under names that invited exactly the wrong
        # reading:
        #   weight_at_injection — recorded, sealed, immutable. What the claim
        #                         rested on. Comes from the lineage record and
        #                         is NEVER recomputed.
        #   current_prominence  — current_weight() now. What the belief is
        #                         worth to retrieval today. Decays (F.3).
        #   high_water_weight   — the stored `weight` column: a monotone
        #                         high-water mark that is never written
        #                         downward, i.e. a RECORD-layer field.
        # Before H.2 the third was returned as `stored_weight` in the slot a
        # reader takes for "value now". A belief decayed to the floor returned
        # `stored_weight: 0.97` while its actual prominence was 0.000 — a UI
        # rendering "then 0.97 / now 0.97" would have stated the opposite of
        # the truth. `stored_weight` is retained one release as a deprecated
        # alias (the P2 pattern).
        now = time.time()
        resolved = []
        for item in rec.injected:
            bid = item.get("belief_id")
            t = services.store.get_by_id(bid) if bid is not None else None
            entry = dict(item)
            entry["resolved"] = t is not None
            if t is not None:
                entry.update({
                    "subject": t.subject, "relation": t.relation,
                    "object": t.object, "source": t.source,
                    "current_tier":       t.tier,
                    "current_prominence": round(t.current_weight(now), 6),
                    "high_water_weight":  t.weight,
                    "stored_weight":      t.weight,   # DEPRECATED alias
                    # H.2 — a superseded belief resolved as though it were live
                    # evidence. Archival is a record-layer judgement and must
                    # be visible to anyone reading what an answer rested on.
                    # L.2a — and so must WHICH judgement. A reviewer seeing
                    # `archived: true` on a cited belief needs to know whether
                    # a human deduplicated it or a rule refused its shape;
                    # those carry very different weight in defending the
                    # answer it grounded. NULL means the row was archived
                    # before reasons were recorded.
                    "archived":           bool(t.archived),
                    "archive_reason":     t.archive_reason,
                    "archived_at":        t.archived_at,
                })
                # S.3 — learning provenance, where the belief is already
                # shown. A COMPACT form: the numbers that changed and how many
                # recorded decisions changed them, with the full trace one hop
                # away at /belief/{id}/provenance. Embedding every step would
                # make a twelve-belief answer unreadable; omitting it entirely
                # would mean the one view that shows what an answer rested on
                # cannot show that the ground moved underneath it.
                prov = provenance_for(services.store, services.recalibration,
                                      belief_id=bid, now=now)
                entry["provenance"] = {
                    "effective_weight":         round(prov.effective_weight, 6),
                    "recalibration_adjustment": round(prov.adjustment, 6),
                    "recalibrations":           len(prov.steps),
                    "refused":                  len(prov.refused),
                    "sealed":                   sum(1 for st in prov.steps
                                                    if st.sealed_block is not None),
                    "detail":                   f"/belief/{bid}/provenance",
                }
            resolved.append(entry)
        payload = rec.to_dict()
        payload["injected"] = resolved
        payload["verify"] = services.lineage.verify_record(lineage_id)
        # MCP-2 — which scope the record has, and what that scope declares:
        # `full_turn` (the console) or `mcp_block` (a turn split across a
        # client's model), with the changed and undefined fields named.
        from backend.mcp.scope import declaration as _scope_declaration, verify_scope as _verify_scope
        payload["scope_declaration"] = _scope_declaration(rec.record_scope)
        payload["scope_declaration"]["violations"] = _verify_scope(rec)
        # W0b — what a later re-run of this answer would need, resolved: the
        # options and the environment the record's hashes key, and whether
        # the exact request is kept (its size, never its text — the text is
        # one call away for whoever re-runs it, not on every read).
        payload["replay_capture"] = services.lineage.replay_capture_view(rec)
        # W10b (D8) — the actions this turn triggered, by id, kind, target, class and state.
        payload["actions"] = [{k: a[k] for k in ("action_id", "kind", "target", "evidence_class", "state")}
                              for a in services.lineage.actions_for(lineage_id)]
        payload["weight_vocabulary"] = {
            "weight_at_injection": "recorded at claim time; sealed; never recomputed",
            "current_prominence":  "current_weight() now; decays; retrieval rank",
            "high_water_weight":   "stored weight column; monotone; record layer",
            "effective_weight":    "current_prominence x arrival scale + "
                                   "recorded recalibrations; retrieval rank (S.2)",
            "stored_weight_deprecated": True,
        }
        # S.3 — ON ITS FACE, on every response, whether or not any belief here
        # has a recorded movement. An empty `recalibrations: 0` means "nothing
        # was recorded", and only this sentence distinguishes that from "this
        # belief never moved". `MAX()` erased the difference irrecoverably
        # (TEMPORAL_PROBE.md), so the surface has to carry it.
        _cov = provenance_for(services.store, services.recalibration,
                              belief_id=-1)
        payload["provenance_coverage"] = {
            "records_begin_at": _cov.records_begin_at,
            "note":             _cov.coverage,
        }
        # T.1 — the DEEPEST instance of the class, and the one the run-through
        # actually hit: `/outcome/confirm` had no surface pointing at it at
        # all. Not a read surface, not an alert. The 422 that named three
        # missing fields was a script guessing at a shape nothing published.
        #
        # This is its home: an outcome is about an ANSWER, and this is the
        # view of one. The template comes back PRE-FILLED with this record's
        # own lineage_id, so the caller has nothing left to guess.
        payload["confirm_outcome"] = _confirm_block(
            "/outcome/confirm",
            {"event_type": "review", "decision": "approved",
             "direction": "confirming",
             "references": {"lineage_id": lineage_id},
             "magnitude": 0.05, "workflow_ref": "", "notes": "",
             "actor_id": "operator", "confirmed": True},
            "direction sets the sign; magnitude is bounded by the active "
            "envelope. Beliefs marked cited=false are excluded. "
            "references also accepts {\"belief_ids\": [...]}; claim_id is "
            "refused because nothing in NEXUS can resolve one.")
        return JSONResponse(payload)

    # ---------- Fix A: dedup pending + confirm ----------

    @app.get("/kb/dedup/pending")
    async def dedup_pending():
        """Near-duplicate candidates flagged by KAIROS for user review."""
        return JSONResponse({
            "candidates": [vars(c) for c in services.last_dedup_candidates],
            "confirm": _confirm_block(
                "/kb/dedup/confirm",
                {"subject_a": "…", "relation_a": "…", "object_a": "…",
                 "subject_b": "…", "relation_b": "…", "object_b": "…",
                 "source_a": None, "source_b": None,
                 "keep": "a", "confirmed": True},
                "source_a/source_b scope the archive to one evidence class; "
                "omitting them is safe only when the claim exists once (L.2a)"),
        })

    @app.post("/kb/dedup/confirm", dependencies=[Depends(require_token)])
    async def dedup_confirm(req: DedupConfirmReq):
        """Law 3: explicit confirmed=True required; archives the loser
        (Law 4: never deletes).

        F.2 — this used to write `tier = 4` by direct SQL, bypassing `add()`'s
        validation (which rejects tier 4 as out of range) and leaving a row
        that raised `KeyError: 4` out of both `rebalance()` and
        `current_weight()`. One confirmation killed the KAIROS daemon and any
        chat turn whose retrieval touched the row. Archive status is now its
        own record-layer column, set through the store."""
        if not req.confirmed:
            # AT.2 — recorded. The actor is the stated one when it is in
            # vocabulary; a value outside it is handled below as a client
            # error, so the ledger never carries an invalid role.
            return _law3_refusal(
                "/kb/dedup/confirm", request=req.model_dump(),
                actor=Actor(id=req.actor_id,
                            role=(req.actor_role if req.actor_role in ACTOR_ROLES
                                  else "local_operator"),
                            source=(req.actor_source if req.actor_source in ACTOR_SOURCES
                                    else "local")))
        if req.keep not in ("a", "b"):
            return JSONResponse(
                {"error": "keep must be 'a' or 'b'", "got": req.keep},
                status_code=422,
            )
        remove = (
            (req.subject_b, req.relation_b, req.object_b) if req.keep == "a"
            else (req.subject_a, req.relation_a, req.object_a)
        )
        remove_source = req.source_b if req.keep == "a" else req.source_a
        # L.2a — the archive is now source-scoped and reason-stamped. If the
        # claim exists under several sources and the caller named none, the
        # store refuses; reporting that is far better than archiving an
        # external belief because a duplicate of it was extracted.
        # AR.5 — the permitted-values list is `valid` on every route that
        # names a closed vocabulary (pinned by AST in
        # tests/test_ar5_error_key_names.py); this route said `allowed`.
        if req.actor_role and req.actor_role not in ACTOR_ROLES:
            return JSONResponse(
                {"error": "unknown_actor_role", "role": req.actor_role,
                 "valid": sorted(ACTOR_ROLES)}, status_code=422)
        if req.actor_source and req.actor_source not in ACTOR_SOURCES:
            return JSONResponse(
                {"error": "unknown_actor_source", "source": req.actor_source,
                 "valid": sorted(ACTOR_SOURCES)}, status_code=422)
        try:
            archived_rows = services.store.archive(
                *remove, source=remove_source, reason="dedup",
                actor_id=req.actor_id,
                actor_role=req.actor_role or "local_operator",
                actor_source=req.actor_source or "local")
        except AmbiguousArchiveTarget as e:
            # AT.4 — RECORDED. A pre-L.2a system performed this exact request
            # (matched on s, r, o and archived both copies); the refusal
            # exists because L.2a installed a policy — ambiguity is refused,
            # not resolved by guessing — and a refusal that exists because a
            # policy was installed is a governance act by definition. Same
            # shape as Law 3: the system understood the request, could act,
            # and declined to supply a judgement that is the caller's. The
            # row carries the request digest, not the claim text — the same
            # information and the same limit as the Law 3 row.
            payload = {"error": "ambiguous archive target",
                       "detail": str(e),
                       "triple": list(remove),
                       "sources": sorted(e.sources),
                       "hint": "resend with source_a/source_b naming the side to archive"}
            # The DETAIL names the evidence classes, never the claim: the
            # exception message carries the triple, and the row must not.
            rec = services.recalibration.record_refusal(
                route="/kb/dedup/confirm", rule="ambiguous_archive_target",
                detail=(f"claim exists under {len(e.sources)} sources "
                        f"{sorted(e.sources)}; none named"),
                actor=Actor(id=req.actor_id,
                            role=req.actor_role or "local_operator",
                            source=req.actor_source or "local"),
                request=req.model_dump())
            if rec is not None:
                payload.update(event_id=rec["event_id"], recorded=True)
            return JSONResponse(payload, status_code=409)
        # Drop any pending candidate that referenced the archived triple so
        # it can't be re-confirmed.
        services.last_dedup_candidates[:] = [
            cand for cand in services.last_dedup_candidates
            if (cand.triple_a["subject"], cand.triple_a["relation"],
                cand.triple_a["object"]) != remove
            and (cand.triple_b["subject"], cand.triple_b["relation"],
                 cand.triple_b["object"]) != remove
        ]
        # `tier` retained one release as a deprecated key (the P2 pattern):
        # it always reported 4, which is no longer a tier. `archived` is the
        # honest name for what this endpoint does.
        return JSONResponse({
            "archived":        list(remove),
            "kept":            req.keep,
            "rows_archived":   archived_rows,
            "tier":            4,
            "tier_deprecated": True,
        })

    # Z.1 follow-up — `verify_governance_records()` existed and nothing
    # pointed at it: callable only from Python, which is the envelope-write-
    # route defect (T.1) wearing a verifier's coat — a guarantee an operator
    # cannot invoke is a guarantee that exists on inspection only. Mirrors
    # `/lineage/verify`: gated read, layers reported separately, never
    # collapsed to a boolean — a consistent forgery passes `row` and fails
    # `seal`, and that distinction is the guarantee.
    @governance.get("/verify", dependencies=[Depends(require_token)])
    async def governance_verify():
        """Recompute every governance record's hash (envelopes, outcomes,
        recalibrations) and check sealed ones against their chain blocks."""
        return JSONResponse(
            services.recalibration.verify_governance_records(
                services.archive_chain))

    # AG — budget used against budget permitted. TOKEN-GATED, and in the
    # governance family beside /governance/verify, deliberately NOT the alert
    # stream: alert cards render to whoever is chatting, and in a partner
    # deployment that is an end user — a budget-consumption card there would
    # read as an accusation in front of exactly the wrong audience. This is
    # an operator surface. Read-only; nothing may act on it (the seam pin on
    # `_insert_recalibration` in tests/test_budget_report.py is the guard).
    # W6 — the READ surface for the work-unit boundary ledger, built with
    # the ledger (a channel plumbed is not a channel connected). Gated, and
    # declared per-person: its rows carry the actor and the accountable
    # identity each request asserted. NOT on the governance router, on
    # purpose. A surface there is an authority surface, where the
    # just-culture guard allows an aggregate by person; "units closed, by
    # whom" is an aggregate of work by the person doing it, which the
    # clause forbids, and here the guard forbids it.
    @app.get("/work-units/events", dependencies=[Depends(require_token)])
    async def work_unit_events(work_unit: str | None = None, limit: int = 200):
        """A unit's closes and reopens, oldest first (every unit's when none
        is named). A close lists the ids of the unit's lineage records at
        the act; a reopen names the close it reverses. `consistency` says
        whether the ledger agrees with the unit rows."""
        events = services.work_units.events(_clean_field(work_unit))
        return JSONResponse({
            "events": events[-max(1, min(limit, 1000)):],
            "total": len(events),
            "consistency": services.work_units.verify_events(),
            "note": ("an act asserted on a turn is made after that turn's record is written: "
                     "asserted_on names the record, and a close's lineage_ids include it. A turn "
                     "whose record could not be written makes no act"),
            "per_person": {
                "declared": True,
                "field": "accountable",
                # W4 — each row is labelled by its own `identity_mechanism`
                # (`asserted` or `proxy`), as the recall rows are.
                "label": "per row: identity_mechanism",
                "note": ("per-person rows: each act carries the actor (id, role, source) and the "
                         "accountable identity its request asserted, labelled by how that identity "
                         "was established: asserted (recorded, never authenticated) or proxy (taken "
                         "from a loopback identity proxy's header; the proxy authenticated the "
                         "person, NEXUS did not). Rows only: no aggregate by person is computed here."),
            },
        })

    @governance.get("/budget", dependencies=[Depends(require_token)])
    async def governance_budget():
        """What the recalibration budget was spent on — per belief against
        cumulative_max, per actor split by role, store-wide with refusals —
        anchored to the envelope version each event cites. The payload
        declares its own limits; see `declarations` and `note`.

        B-40 — PER-PERSON ROWS, DECLARED: a steward's bound on governed
        adjustments is accountability by design, so the per-actor split
        stays; the surface is an authority surface (governance router,
        gated, declared) and says so."""
        report = services.recalibration.budget_report()
        report["per_person"] = GOVERNANCE_PER_PERSON
        return JSONResponse(report)

    # AT.2 — the READ surface for the refusal ledger, built with the ledger
    # because AL shipped one with no reader and Z.2 named that class: a
    # channel plumbed is not a channel connected. Token-gated like every
    # governance read — it names actors. One list over both ledgers (the
    # generic table and the outcome route's own refused rows), each row
    # saying which it came from, with the rule and coverage beside it.
    @governance.get("/refusals", dependencies=[Depends(require_token)])
    async def governance_refusals(limit: int = 50):
        recal = services.recalibration
        return JSONResponse({
            "refusals": recal.refusals(limit=max(1, min(limit, 500))),
            "coverage": recal.refusal_coverage(),
            "verify": "/governance/verify",
        })

    @governance.get("/envelope", dependencies=[Depends(require_token)])
    async def governance_envelope():
        """The policy in force, its history, and whether anything may move.

        I-6 — `enabled` and `active` are reported separately: "the kill switch
        is off" and "the policy permits nothing" are different states that a
        single boolean would collapse, and an operator debugging why nothing
        moved needs to know which one they are in.

        B-40 — GATED, and PER-PERSON ROWS DECLARED: the history names the
        actor who installed each policy, a governed act attributable to its
        actor by construction. It was an open read (S.2: a policy is meant
        to be inspectable); the actor ids on it are why it is not.
        """
        recal = services.recalibration
        active = recal.active_envelope()
        return JSONResponse({
            "enabled":  recalibration_enabled(),
            "active":   active.to_dict(),
            # W6 — whether a policy is installed, never what it is called: the
            # compiled default's name is free, and a policy installed under it
            # read `is_default: true` here.
            "is_default": recal.installed_envelope_version() is None,
            "per_person": GOVERNANCE_PER_PERSON,
            "confirm": _confirm_block(
                "/governance/envelope/confirm",
                {"version": "board-2026-01", "max_delta": 0.05,
                 "movable_classes": ["extraction"], "frozen_belief_ids": [],
                 "cumulative_max": None, "reason": "why the board approved it",
                 "actor_id": "operator", "confirmed": True},
                "installing nothing leaves v0-immovable, which permits no "
                "movement at all — that is the reversal, not a placeholder"),
            "history": [
                {"version": e.version, "ts": e.ts, "reason": e.reason,
                 "actor": {"id": e.actor.id, "role": e.actor.role,
                           "source": e.actor.source},
                 "policy": e.envelope.to_dict(),
                 "sealed_block": e.sealed_block}
                for e in recal.envelope_history()
            ],
        })

    @governance.post("/envelope/confirm",
                     dependencies=[Depends(require_token)])
    async def governance_envelope_confirm(req: EnvelopeConfirmReq):
        refuse = _scope_too_long(req.scope)
        if refuse is not None:
            return refuse
        if not req.confirmed:
            # AT.2 — recorded, under the installation's stated scope.
            return _law3_refusal("/governance/envelope/confirm",
                                 request=req.model_dump(),
                                 actor=Actor(id=req.actor_id),
                                 scope=_clean_scope(req.scope))
        if not (req.reason or "").strip():
            return JSONResponse(
                {"error": "reason_required",
                 "hint": "an envelope change with no stated reason is what a "
                         "change-control board exists to prevent"},
                status_code=422)
        envelope = Envelope(
            version=req.version, max_delta=req.max_delta,
            movable_classes=tuple(req.movable_classes),
            frozen_belief_ids=tuple(req.frozen_belief_ids),
            cumulative_max=req.cumulative_max)
        try:
            rec = services.recalibration.record_envelope(
                envelope, actor=req.actor_id, reason=req.reason,
                scope=_clean_scope(req.scope))
        except EnvelopeViolation as e:
            # AT.3 — `version_reused` is RECORDED: a well-formed request to
            # install a policy under an existing name, which the system
            # could have overwritten and declined to because versions are
            # immutable. "Would not" on the line, not "never valid" — the
            # Law 3 scoping in AT.2 was narrower than the principle. Honours
            # the ledger switch; the response claims a record only when a
            # row exists.
            payload = {"error": e.rule, "detail": e.detail}
            rec = services.recalibration.record_refusal(
                route="/governance/envelope/confirm", rule=e.rule,
                detail=e.detail, actor=Actor(id=req.actor_id),
                scope=_clean_scope(req.scope), request=req.model_dump())
            if rec is not None:
                payload.update(event_id=rec["event_id"], recorded=True)
            return JSONResponse(payload, status_code=409)
        return JSONResponse({"installed": True, "version": rec.version,
                             "ts": rec.ts, "record_hash": rec.record_hash,
                             "policy": envelope.to_dict()})

    @app.post("/outcome/confirm", dependencies=[Depends(require_token)])
    async def outcome_confirm(req: OutcomeConfirmReq):
        """Record an outcome and apply the recalibrations it authorises.

        DECLARED, NOT SILENT (I-6). The response reports every belief that
        moved AND every one that was refused, with the rule that refused it.
        A response listing only successes describes a system that never
        declines anything.
        """
        recal = services.recalibration
        # One Actor for every record this request produces — the outcome row,
        # a refusal row, and each recalibration event — so a declined
        # attempt is attributed to the identity that made it, not a default.
        default = Actor(id=req.actor_id)      # pre-Z.1 meaning of silence
        role_ok = not req.actor_role or req.actor_role in ACTOR_ROLES
        source_ok = not req.actor_source or req.actor_source in ACTOR_SOURCES
        actor = Actor(id=req.actor_id,
                      role=(req.actor_role or default.role) if role_ok
                      else default.role,
                      source=(req.actor_source or default.source) if source_ok
                      else default.source)
        scope_refusal = _scope_too_long(req.scope)
        scope = "default" if scope_refusal is not None else _clean_scope(req.scope)

        # AT.1 — THE LINE (adopted after AR.2 recorded these and AT argued
        # against it): a request that was never valid — an over-long scope,
        # a value outside a closed vocabulary, a reference nothing can
        # resolve — is a CLIENT ERROR, declared on the response and recorded
        # nowhere. Recording it made the ledger's scope unstatable: the same
        # misconfigured caller's schema error leaves no trace, so
        # completeness over attempts was unreachable and a partial record
        # pretending otherwise is worse than a clear boundary. Rows AR.2
        # already wrote stay (forward-only). What IS recorded here: the Law
        # 3 gate and `no_beliefs_resolved` — well-formed requests the system
        # declined. Pinned in tests/test_ar2_refusal_record.py.
        if scope_refusal is not None:
            return scope_refusal
        if req.direction not in DIRECTION_SIGN:
            return JSONResponse(
                {"error": "unknown_direction", "direction": req.direction,
                 "valid": sorted(DIRECTION_SIGN)}, status_code=422)
        # Z.1 — refused BY NAME, like unknown_direction, and for the same
        # reason event_type is the counter-example: a comment naming intended
        # values constrains nothing.
        if not role_ok:
            return JSONResponse(
                {"error": "unknown_actor_role", "role": req.actor_role,
                 "valid": sorted(ACTOR_ROLES)}, status_code=422)
        if not source_ok:
            return JSONResponse(
                {"error": "unknown_actor_source", "source": req.actor_source,
                 "valid": sorted(ACTOR_SOURCES)}, status_code=422)
        if "claim_id" in (req.references or {}):
            return JSONResponse(
                {"error": "claim_id_unresolvable",
                 "hint": "NEXUS resolves lineage_id and belief_ids. There is "
                         "no claim registry (S.0.2); refusing rather than "
                         "silently ignoring the reference."},
                status_code=422)

        if not req.confirmed:
            # Recorded even though refused — the attempt is part of the story.
            ev = recal.record_outcome(
                event_type=req.event_type, decision=req.decision,
                direction=req.direction, references=req.references,
                actor=actor, workflow_ref=req.workflow_ref,
                notes=req.notes, accepted=False, scope=scope,
                refusal_reason="Law 3 — confirmed=true required")
            return JSONResponse(
                {"error": "Law 3 — confirmed=true required",
                 "hint": "explicit boolean, not implicit",
                 "event_id": ev.event_id, "recorded": True}, status_code=422)

        belief_ids, resolution = _resolve_outcome_references(
            services, req.references)
        if not belief_ids:
            ev = recal.record_outcome(
                event_type=req.event_type, decision=req.decision,
                direction=req.direction, references=req.references,
                actor=actor, workflow_ref=req.workflow_ref,
                notes=req.notes, accepted=False, scope=scope,
                refusal_reason=f"no beliefs resolved: {resolution}")
            return JSONResponse(
                {"error": "no_beliefs_resolved", "detail": resolution,
                 "event_id": ev.event_id, "recorded": True}, status_code=422)

        # AR — a supersede is a reason on THIS act, not a new act kind. When
        # the notes begin `superseded_by:`, the response and the governance
        # row carry how many sealed records rested on the referenced claims
        # and the route to the list. Read from the injected set; a claim no
        # record injected reports a measured 0, present, never absent.
        # Nothing else about the act changes: standing moves only as the
        # envelope already allows, and the recall itself is a read.
        from backend.kb.recalibration import is_supersede_reason
        recall_count = recall_route = None
        if is_supersede_reason(req.notes):
            recall_count = services.lineage.records_resting_on(belief_ids)
            recall_route = (recall_route_for(belief_ids[0]) if len(belief_ids) == 1
                            else "; ".join(recall_route_for(b) for b in belief_ids))

        ev = recal.record_outcome(
            event_type=req.event_type, decision=req.decision,
            direction=req.direction, references=req.references,
            actor=actor, workflow_ref=req.workflow_ref, notes=req.notes,
            scope=scope, recall_count=recall_count, recall_route=recall_route)

        delta = DIRECTION_SIGN[req.direction] * abs(float(req.magnitude))
        applied, refused = [], []
        for bid in belief_ids:
            try:
                r = apply_outcome(services.store, recal, belief_id=bid,
                                  delta=delta, actor=actor,
                                  outcome_ref=ev.event_id, scope=scope)
                applied.append({"belief_id": bid, "delta": r.delta,
                                "prominence_before": r.prominence_before,
                                "prominence_after": r.prominence_after,
                                "envelope_version": r.envelope_version,
                                "event_id": r.event_id})
            except EnvelopeViolation as e:
                refused.append({"belief_id": bid, "rule": e.rule,
                                "detail": e.detail})

        # Z.2 — the outcome resolves the calibration rows it adjudicates.
        # `resolve()` had ZERO production callers before this: every stated
        # confidence sat at resolved_outcome null forever, and closing a loop
        # fed the governance record while starving the calibration record.
        #
        # DELIBERATELY INDEPENDENT of `applied`/`refused`: the world's answer
        # is a fact whether or not the envelope let a weight move.
        # Calibration is epistemics; the envelope is policy.
        #
        # `neutral` resolves nothing — resolved_outcome is a bool and "the
        # world answered neither way" is not a truth value. An outcome
        # referencing belief_ids directly resolves nothing — no lineage ties
        # it to any stated confidence. Both DECLARED (I-6): zero-forever
        # assumed-working is exactly how the disconnection went unnoticed.
        calibration_note = ""
        calibration_resolved = 0
        if recall_count is not None:
            # AR — a supersede says a newer revision replaced the claim. It
            # is not the world's answer to a stated confidence, so it
            # resolves no calibration row and reaches no forecast score
            # (which reads `resolved_outcome`). Declared, like `neutral`.
            calibration_note = ("supersede adjudicates nothing about a stated "
                                "confidence; no calibration rows resolved")
        elif req.direction == "neutral":
            calibration_note = ("neutral direction adjudicates nothing; "
                                "no calibration rows resolved")
        elif "lineage_id" not in (req.references or {}):
            calibration_note = ("no lineage_id reference — nothing ties this "
                                "outcome to a stated confidence; no "
                                "calibration rows resolved")
        else:
            from backend.eval.calibration_log import resolve_for_outcome
            calibration_resolved = resolve_for_outcome(
                lineage_id=str(req.references["lineage_id"]),
                belief_ids=belief_ids,
                outcome=(req.direction == "confirming"),
                outcome_ref=ev.event_id)

        return JSONResponse({
            "event_id":   ev.event_id,
            "resolution": resolution,
            "envelope":   recal.active_envelope().to_dict(),
            "applied":    applied,
            "refused":    refused,
            "calibration_resolved": calibration_resolved,
            "calibration_note": calibration_note,
            # AR — only on a supersede; a measured 0 is present, not absent.
            **({"recall_count": recall_count, "recall_route": recall_route}
               if recall_count is not None else {}),
        })

    @app.post("/kb/assert/confirm", dependencies=[Depends(require_token)])
    async def assert_confirm(req: AssertConfirmReq):
        """O.2b — the operator's own statement becomes a belief, on confirm.

        Law 3: nothing is stored without `confirmed=true`. This is the same
        gate `/kb/dedup/confirm` and `/search/confirm` already use, and for
        the same reason — an irreversible write in a substrate whose founding
        law is that beliefs are never deleted.

        The response DECLARES what it did and did not store (I-6): a sentence
        that cannot be shaped into a triple stores nothing and says so, rather
        than silently succeeding with an empty result.
        """
        refuse = _attribution_refusal(req)                  # v3.1 Phase 1b, scope's position
        if refuse is not None:
            return refuse
        from backend.kb.assertion import (
            HUMAN_SOURCE, assertion_triples, looks_like_assertion)

        sentence = (req.sentence or "").strip()
        if not sentence:
            return JSONResponse(
                {"error": "empty_sentence",
                 "hint": "send the sentence the user asserted"},
                status_code=422)
        if not req.confirmed:
            # AT.2 — recorded. The sentence itself is not stored on the
            # ledger; the request digest identifies the attempt.
            resp = _law3_refusal("/kb/assert/confirm", request=req.model_dump())
            body = json.loads(resp.body)
            body["sentence"] = sentence
            return JSONResponse(body, status_code=422)

        candidate, why = looks_like_assertion(sentence)
        triples = assertion_triples(sentence)
        if not triples:
            # Declared, not silent. A confirmed sentence that cannot be shaped
            # stores NOTHING — better than storing a triple nobody meant.
            return JSONResponse({
                "sentence": sentence, "stored": 0, "beliefs": [],
                "shaped": False,
                "detected_as_assertion": candidate,
                "detection_reason": why,
                "hint": "no subject-verb-object could be derived; rephrase "
                        "as a statement with a verb, e.g. 'X was 11.4ms'"})

        stored = []
        _admission_scope = services.store.admissions()
        admitted = _admission_scope.__enter__()
        for t in triples:
            try:
                bid = services.store.add(
                    subject=t.subject, relation=t.relation, object=t.object,
                    weight=t.weight, tier=t.tier,
                    granularity=Granularity.TOPIC, source=HUMAN_SOURCE)
                # AR.3 — the response reports what was STORED, read back
                # from the row `add()` returned, not the parsed triple:
                # `add()` normalises (K.3), so an integrator handed the
                # parsed relation would join on a value the store never
                # held. The parsed form rides beside it, named.
                row = services.store.get_by_id(bid)
                stored.append({
                    "belief_id": bid,
                    "subject": row.subject, "relation": row.relation,
                    "object": row.object,
                    "as_parsed": {"subject": t.subject,
                                  "relation": t.relation,
                                  "object": t.object}})
            except BeliefRejected as e:
                # The gate is lenient for this class, so a refusal here is
                # rare and worth surfacing rather than swallowing (K.2 / I-6).
                stored.append({"refused": str(e), "subject": t.subject})

        _admission_scope.__exit__(None, None, None)
        return JSONResponse({
            "sentence": sentence,
            "stored": sum(1 for b in stored if "belief_id" in b),
            "beliefs": stored,
            "shaped": True,
            "source": HUMAN_SOURCE,
            "attribution": _admit_outside_turn(admitted, req.work_unit),
            "detected_as_assertion": candidate,
            "detection_reason": why,
        })

    @app.post("/search/confirm", dependencies=[Depends(require_token)])
    async def search_confirm(req: SearchConfirmReq):
        """Phase 10b — Law 3 confirmation gate for the web search tool.

        Records consent for the EXACT query string. The next /chat call
        carrying the same query will fire the search and clear the entry.
        """
        key = (req.query or "").strip()
        if not key:
            return JSONResponse(
                {"error": "empty_query", "hint": "send the user query string"},
                status_code=422,
            )
        if req.confirmed:
            services.search_consents[key] = True
            recorded = "consent_granted"
        else:
            services.search_consents.pop(key, None)
            recorded = "consent_revoked"
        return JSONResponse({
            "query":   key,
            "status":  recorded,
            "pending": list(services.search_consents.keys()),
        })

    @app.get("/search/pending")
    async def search_pending():
        """Inspect outstanding consents (UI debugging)."""
        return JSONResponse({
            "pending": list(services.search_consents.keys()),
            "confirm": _confirm_block(
                "/search/confirm",
                {"query": "the query as the user typed it", "confirmed": True},
                "consent is single-use and keyed on the exact query string"),
        })

    @app.post("/search/execute", dependencies=[Depends(require_token)])
    async def search_execute(req: SearchExecuteReq, identity=Depends(established_identity)):
        """Fix SEARCH_UX — fire search immediately on user confirmation.

        Law 3: confirmed=True required. The click that posts here IS the
        confirmation event; no consent-key lookup. Returns results as JSON;
        the frontend injects them into the next /chat message body.
        """
        if not req.confirmed:
            # AT.2 — recorded, and the same named error every gate uses
            # (this one said `confirmed_required`).
            return _law3_refusal("/search/execute", request=req.model_dump(),
                                 hint="the click that posts here is the "
                                      "confirmation; send confirmed=true")
        if not req.query.strip():
            return JSONResponse(
                {"error": "empty_query", "hint": "query required"},
                status_code=422,
            )
        from backend.memory import actions as _actions
        try:
            results = await services.search_tool.search(
                req.query, confirmed=True,
                trigger=_actions.Trigger(route="/search/execute", accountable_identity=_login_of(identity),
                                         identity_mechanism=_identity_mechanism_of(identity)),
            )
            # H.3b — I-6. The tool swallows HTTPError/OSError and returns [],
            # which two Packet 10a sentinels pin as its fails-soft contract,
            # so the `except` below could never fire and this endpoint
            # returned `count: 0` identically for "unreachable" and "no
            # results". `search_failed`/`error` are ADDITIVE: every existing
            # key keeps its meaning and both sentinels stay green.
            failure = getattr(services.search_tool, "last_error", "")
            payload = {
                "query":   req.query,
                "results": [r.to_dict() for r in results],
                "count":   len(results),
                "search_failed": bool(failure),
            }
            if failure:
                payload["error"] = failure
            return JSONResponse(payload)
        except _actions.ActionNotRecorded as e:
            return _action_not_recorded(e)                  # W10b (D2): never a silent count of 0
        except Exception as e:
            return JSONResponse({
                "query":   req.query,
                "results": [],
                "count":   0,
                "error":   str(e),
            })

    @app.get("/router/preview")
    async def router_preview(q: str):
        decision = services.kernel.route(query=q)
        return JSONResponse({
            "capability": decision.capability.value,
            "model": decision.profile.display_name if decision.profile else None,
            "model_name": decision.model_name,
            "reason": decision.reason,
            "fallback_used": decision.fallback_used,
        })

    @app.get("/router/registry")
    async def router_registry():
        return JSONResponse({
            "models": [
                {
                    "name": m.name, "display_name": m.display_name,
                    "capabilities": [c.value for c in m.capabilities],
                    "vram_gb": m.vram_gb, "tokens_per_sec": m.tokens_per_sec,
                    "deprecated": m.deprecated,
                }
                for m in services.kernel.registry.all(include_deprecated=True)
            ],
        })

    @app.get("/eval/recent")
    async def eval_recent(n: int = 20):
        recent = services.evaluator.records[-n:]
        return JSONResponse({
            "records": [r.__dict__ | {"composite": r.composite()} for r in recent],
        })

    @app.get("/vector/stats")
    async def vector_stats():
        return JSONResponse({
            "count": services.vector.count(),
            "persist_directory": str(services.vector.persist_directory),
        })

    @app.get("/graph/snapshot")
    async def graph_snapshot():
        nodes = []
        for nid in services.hippocampus.nodes:
            n = services.hippocampus.get_node(nid)
            nodes.append({
                "id":              nid,
                "activation":      n.activation,
                "epistemic_state": n.epistemic_state.value,
                # P2/F-02 — canonical key is evidential_weight; "confidence"
                # stays one release as a deprecated alias (see metadata flag).
                "evidential_weight": n.confidence,
                "confidence":      n.confidence,
                "node_type":       n.node_type.value,
                "column":          n.column,
                "fire_count":      n.fire_count,
            })
        edges = []
        for e in services.hippocampus.edges():
            edges.append({
                "source":          e.source,
                "target":          e.target,
                "weight":          e.weight,
                "current_weight":  e.current_weight(),
                "fire_count":      e.fire_count,
                "activation_type": e.activation_type.value,
            })
        return JSONResponse({
            "nodes":    nodes,
            "edges":    edges,
            "metadata": {
                "n_nodes":           len(nodes),
                "n_edges":           len(edges),
                "active_model":      services.state.active_model,
                "turn_count":        services.state.turn_count,
                "prediction_error":  services.state.prediction_error,
                # P2/F-02 — node payloads carry evidential_weight; the
                # "confidence" node key is a deprecated alias, one release.
                "confidence_deprecated": True,
            },
        })

    @app.get("/state/summary")
    async def state_summary():
        s = services.state
        return JSONResponse({
            "turn_count":         s.turn_count,
            "token_count":        s.token_count,
            "active_model":       s.active_model,
            "compaction_state":   s.compaction_state,
            "transition_reason":  s.transition_reason,
            "n_messages":         len(s.messages),
            "n_graph_context":    len(s.graph_context),
            "n_session_memory":   len(s.session_memory),
            "n_predicted_nodes":  len(s.predicted_nodes),
            "prediction_error":   s.prediction_error,
        })

    @app.post("/model/override", dependencies=[Depends(require_token)])
    async def set_override(req: OverrideReq):
        """User-initiated model pin. Bypasses kernel routing for the duration.

        lock=False: cleared after this turn (trial mode).
        lock=True:  persists until DELETE /model/override.
        Extraction always uses FAST_EXTRACTION regardless of override.
        """
        # AR.4 — a role or model that cannot ANSWER is refused by name. The
        # roster is read from the registry's capability declarations
        # (`answering_roles`), not from a second hand-kept list: `embed`
        # resolves to a profile that declares EMBEDDING only, and pinning it
        # made the embedding model the answering model. The raw-name path
        # gets the same check against the registry when the tag is known
        # to it; a tag the registry has never heard of is accepted as
        # before (experimental), because nothing can vouch either way.
        def _cannot_answer(model: str, resolved: str, caps) -> JSONResponse:
            return JSONResponse(
                {"error": "model_cannot_answer",
                 "model": model, "resolved_model": resolved,
                 "capabilities": [c.value for c in caps],
                 "valid": sorted(answering_roles(services.kernel.registry)),
                 "hint": "this model declares no answering capability, so "
                         "it cannot take a chat turn; pin one of the roles "
                         "in `valid`, or a raw model name that can answer"},
                status_code=422)

        if req.model in ROLES:
            if req.model not in answering_roles(services.kernel.registry):
                resolved = resolve(req.model)
                profile = services.kernel.registry._models.get(resolved)
                return _cannot_answer(
                    req.model, resolved,
                    profile.capabilities if profile is not None else ())
        else:
            # Raw provider name — accept but flag as experimental.
            try:
                available = await services.provider.list_models()
            except Exception as e:
                # U.3's class, one layer up: a daemon that is not running is
                # an EXPECTED condition — /healthz names it, /model/status
                # degrades, the startup probe declares it — so the one
                # endpoint that surfaced it as a raw httpx traceback was the
                # anomaly. Named 503, never a 500. Role-name overrides do
                # not reach this branch and keep working with the daemon
                # down: pinning a role is a statement about the roster, not
                # about what is currently answering.
                return JSONResponse(
                    {"error": "provider_unreachable",
                     "backend": os.getenv("LLM_BACKEND", "ollama"),
                     "detail": f"{type(e).__name__}: {e}",
                     "hint": "the model backend did not answer, so a raw "
                             "model name cannot be validated. Start it "
                             "(ollama serve / LM Studio server) and retry — "
                             "or pin a canonical role, which needs no "
                             "backend: " + "|".join(sorted(ROLES))},
                    status_code=503,
                )
            # R.3 — the same base-family rule the registry uses. This was a
            # plain `not in`, which was harmless only because list_models()
            # returned [] on a booted box and the `available and` guard
            # short-circuited. Now that it returns the real roster, an exact
            # match would 422 an override naming `qwen2.5-coder:7b` on a box
            # where the installed tag is `qwen2.5-coder:7b-q4_K_M` — i.e.
            # refuse the operator's own model over a quantisation suffix.
            if available and not matches_available(req.model, available):
                # Don't 422 if backend is unreachable (no available list).
                return JSONResponse(
                    {"error": "unknown_model",
                     "model": req.model,
                     "hint": "use canonical role or a model name in /model/status.available"},
                    status_code=422,
                )
            # AR.4 — the registry's own base-family match (exact tag, or
            # the same base before ':'), deliberately stricter than
            # `matches_available`'s substring rule so `qwen3` cannot be
            # refused on `qwen3.5`'s declarations.
            base = req.model.split(":")[0].lower()
            known = next((m for m in services.kernel.registry.all(include_deprecated=True)
                          if m.name.lower() == req.model.lower()
                          or m.name.split(":")[0].lower() == base), None)
            if known is not None and not can_answer(known.capabilities):
                return _cannot_answer(req.model, known.name, known.capabilities)
        services.state = next_state(
            services.state,
            model_override=req.model,
            override_locked=req.lock,
        )
        resolved = resolve(req.model) if req.model in ROLES else req.model
        return JSONResponse({
            "override_active": True,
            "resolved_model":  resolved,
            "locked":          req.lock,
        })

    @app.delete("/model/override", dependencies=[Depends(require_token)])
    async def clear_override():
        services.state = next_state(
            services.state, model_override=None, override_locked=False,
        )
        return JSONResponse({"override_active": False})

    @app.get("/model/status")
    async def model_status():
        """Active model, override state, available models from the backend."""
        try:
            available = await services.provider.list_models()
        except Exception:
            available = []
        # W3 part 3 (H3) — the backend as NEXUS reads it (stripped, lowered,
        # `ollama` when unset), not the raw variable.
        from backend.providers.backends import backend_name
        return JSONResponse({
            "active_model": services.state.active_model,
            "override":     services.state.model_override,
            "locked":       services.state.override_locked,
            "available":    available,
            "backend":      backend_name(),
        })

    @app.post("/chat", dependencies=[Depends(require_token)])
    async def chat(req: ChatRequest, identity=Depends(established_identity)):
        refuse = _scope_too_long(req.scope)
        if refuse is None:
            refuse = _attribution_refusal(req)              # v3.1 Phase 1b, scope's position
        if refuse is not None:
            return refuse
        # v3.1 Phase 1b — ONE channel, the body, like scope. 1a also read
        # `X-Nexus-*` headers with a body-wins rule and a declared conflict;
        # 1b removed the header path: a contradiction between two channels
        # for one value is removed by construction, not adjudicated, and a
        # deployment that needs headers gets a configuration selecting the
        # source, later, not two live channels (W4's proxy mode is that
        # configuration: below). The guard in
        # tests/test_p1b_attribution_as_scope.py derives every header read in
        # this route and fails on any.
        actor = _request_actor(req)
        # W4 part 1b (D7) — WHO THE TURN IS FOR. In proxy mode it is the login
        # the identity gate established, taken through the
        # `established_identity` dependency (this route reads no header): a
        # body `accountable` equal to the login, or none, is the login, and
        # one that differs is refused by name (`_turn_accountable`: 422,
        # recorded nowhere); the record carries `proxy`. In token mode nothing
        # changes: the body's value, as asserted, and the default mechanism
        # (stored NULL). UNCHANGED, AND DECLARED: the session pseudo-unit
        # carries no accountable; a unit first named by the assertion or seed
        # routes carries none (`_admit_outside_turn` passes none); and the
        # actor fields (a unit's `opened_by` and `closed_by`, the record's
        # `boundary_asserted_by`) and every governed act's `actor_id` stay the
        # body's assertion in proxy mode too (run/specs/W4.md, "What is not
        # built").
        accountable, refuse = _turn_accountable(req, identity)
        if refuse is not None:
            return refuse
        identity_mechanism = _identity_mechanism_of(identity)

        async def gen():
            working = services.state
            before = working      # W6 — the session's state before this request
            # Fix 9 — pin any submitted attachments onto state so the loop's
            # routing layer can promote to MULTIMODAL. Cleared at Stage 7
            # atomic transition (single-use, per turn).
            if req.attachments:
                working = next_state(working, attachments=tuple(
                    {"name": a.name, "type": a.type, "data": a.data}
                    for a in req.attachments
                ))
            services.kairos.session_started()
            try:
                async for frame, state_after in run_turn(
                    working, req.message,
                    retriever=services.retriever,
                    hippocampus=services.hippocampus,
                    hebbian=services.hebbian,
                    procedural=services.procedural,
                    kernel=services.kernel,
                    evaluator=services.evaluator,
                    vector_store=services.vector,
                    predictive=services.predictive,
                    provider=services.provider,
                    contradiction=services.contradiction,
                    curiosity=services.curiosity,
                    search_tool=services.search_tool,
                    search_consents=services.search_consents,
                    pending_alerts=services.pending_alerts,
                    lineage=services.lineage,
                    scope=_clean_scope(req.scope),
                    work_units=services.work_units,
                    session_id=DEFAULT_THREAD_ID,
                    work_unit=_clean_field(req.work_unit),
                    work_unit_type=_clean_field(req.work_unit_type),
                    accountable=accountable,                       # W4 part 1b
                    actor=actor,
                    decision_boundary=_clean_field(req.decision_boundary),
                    identity_mechanism=identity_mechanism,         # W4 part 1b: the record's word
                ):
                    # W6 — a turn whose record failed leaves the session as it
                    # was before the request: not the first half's state, and
                    # not the request's attachments pinned above.
                    working = before if frame.startswith("event: record_failed\n") else state_after
                    yield frame
            finally:
                services.state = working
                services.checkpointer.put(DEFAULT_THREAD_ID, services.state)
                # E.3 — persist the association graph the turn just mutated,
                # under the shared lock so a KAIROS cycle already in flight
                # cannot interleave a write (session_active alone misses that
                # window). Failure is declared, never silent (Law 8), and
                # never breaks the turn.
                if services.graph_store is not None and services.graph_lock is not None:
                    try:
                        async with services.graph_lock:
                            services.graph_store.save(services.hippocampus)
                    except Exception as e:
                        logger.warning("association-graph persist failed: %s: %s",
                                       type(e).__name__, e)
                services.kairos.session_ended()

        return StreamingResponse(gen(), media_type="text/event-stream")

    # B-40 — mount the governance router after every route on it is declared.
    # Mounted FLAT, the router's own APIRoute objects appended to the app's
    # table: FastAPI 0.141's `include_router` would nest them inside an
    # `_IncludedRouter` entry, which every derivation over `app.routes` in
    # the suite (the ratchet, the writer census, the refusal-ledger guard,
    # the just-culture guard) would stop seeing. Membership of the governance
    # router is therefore object identity: `route in governance.routes`.
    for _route in governance.routes:
        app.router.routes.append(_route)
    return app


# ---------- no module-level app, deliberately ----------
#
# `app = create_app()` used to live here. See this module's docstring: it made
# `import backend.server` construct eight persistent writers and create files
# relative to the importer's cwd, which wrote to the operator's live database
# once already.
#
# Launch with the factory instead:
#     uvicorn backend.server:create_app --factory
#
# Do not reinstate a module-level `app` — `tests/test_import_purity.py` will
# fail, and it will be right.
