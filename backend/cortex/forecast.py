"""Packet AF.1 — the citation forecast.

WHAT THIS IS, NAMED FOR WHAT IT DOES (I-7)
    A forecast of WHICH CLAIMS THE ANSWER WILL CITE, seeded from the query's
    terms alone and formed — and PERSISTED — before `retrieve()` runs.

    The thing it replaces was called "prediction error" and contained no
    prediction: `predicted_top` was seeded from the retrieved hits, so the
    system "predicted" what it had already looked up. That reordering still
    runs (it drives the Hebbian surprise modulation and is now honestly
    described); this module is the actual predictor P.4 reserved the
    `prediction` calibration mechanism for.

THE BOUNDARY THAT MAKES IT REAL
    The forecast is a ranked top-k with k committed at forecast time, written
    to the calibration ledger BEFORE retrieval returns, so the score cannot
    be fitted to the outcome. Scoring is precision@k against the turn's
    citation flags (the F.5 evidence standard — decided by what the model
    wrote, recorded, sealed), always beside the PROMINENCE NULL for the same
    turn: propagation is itself prominence-flavoured, so a forecast score
    without its control is not evidence.

READ-ONLY BY CONSTRUCTION
    `_spread_readonly` mirrors `propagation.propagate` — same attenuation,
    same firing threshold, same lateral inhibition, an edge transmits once
    per walk — but accumulates activation in LOCAL state. The live
    `propagate` excites nodes, advances fire counters and calls
    `edge.mark_fired`, which feeds E.2's recurrence episodes; a forecast that
    did any of that would change the very turn it is trying to predict.
    test_the_forecast_does_not_mutate_the_graph pins every mutable field.

WHAT IT IS EMPTY FOR — a property of the signal, not a bug
    Query terms that are not yet graph nodes seed nothing, so early-domain
    forecasts are near-empty. Every forecast therefore declares its
    forecastable-term coverage, so phrasing drift reads as "the graph does
    not know these words yet" rather than as forgetting.

ANCHORING, NOT RETRIEVAL
    Activated nodes map to beliefs by EXACT surface equality
    (`TripleStore.anchored_exact`) — not `search_terms`' substring LIKE, not
    the retriever. Substrate-minted relations are excluded from anchoring for
    W.2's measured reason: co-activation surfaces are assembled from query
    vocabulary and outrank every claim. Ranking is by anchor activation with
    belief id as the deterministic tiebreak — prominence contributes NOTHING
    to the order, which is what makes the null a control rather than a
    mirror.
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.cortex.columns import Column, ColumnState
from backend.cortex.propagation import MAX_HOPS, PROPAGATION_TAU
from backend.eval.calibration_log import log_confidence, resolve, latest_by_entry
from backend.kb.citation import citation_test_version          # AU
from backend.kb.retriever import extract_terms
from backend.kb.semantic_index import DEFAULT_EXCLUDED_RELATIONS

if TYPE_CHECKING:
    from backend.kb.tiered_store import TripleStore
    from backend.memory.hippocampus import Hippocampus

logger = logging.getLogger("nexus.forecast")

# k is a CEILING committed per forecast: the committed k is
# min(DEFAULT_FORECAST_K, candidates found), written into every row.
DEFAULT_FORECAST_K = 5

# How many activated nodes are offered to the anchor lookup. Bounds the IN
# clause; nodes are taken in descending activation so a cap only ever drops
# the faintest anchors.
MAX_ANCHOR_NODES = 64


def forecast_enabled() -> bool:
    """Read at call time (the E.2 pattern). `off` restores the pre-AF.1 turn
    exactly: no spread, no ledger rows, no block on the done payload."""
    return os.environ.get(
        "NEXUS_CITATION_FORECAST", "on").strip().lower() != "off"


@dataclass(frozen=True)
class ForecastRow:
    belief_id: int
    activation: float          # the anchor activation the ranking used
    rank: int                  # committed at forecast time
    entry_id: str | None       # calibration ledger handle; None if the
                               # append failed (the ledger never raises)
    seed_anchored: bool        # anchored by a hop-0 node — the GIVEN half,
                               # P.4's observation/propagation distinction


@dataclass(frozen=True)
class CitationForecast:
    forecast_id: str
    k: int                     # committed: len(rows), capped by the ceiling
    rows: tuple[ForecastRow, ...]
    seeds: tuple[str, ...]     # query terms that WERE graph nodes
    total_terms: int
    forecastable_terms: int


def _spread_readonly(
    hippocampus: "Hippocampus",
    seeds: list[str],
    *,
    seed_activation: float = 1.0,
    max_hops: int = MAX_HOPS,
) -> dict[str, float]:
    """`propagation.propagate`, minus every write.

    Divergences from the live walk, each because the live behaviour IS a
    mutation: no `node.excite`/`fire` (activation and firing tracked in local
    dicts, seeded from the node's current activation so a warm graph
    forecasts as the warm graph it is); no `edge.mark_fired` (an edge
    transmits once per walk via a local fired set — the live walk gets the
    same effect from the refractory window mark_fired opens).
    """
    act: dict[str, float] = {}          # peak activation per node (the result)
    level: dict[str, float] = {}        # simulated membrane level
    if not seeds:
        return act

    column_state = ColumnState()
    frontier: dict[str, float] = {}
    for sid in seeds:
        if sid in hippocampus.nodes:
            node = hippocampus.get_node(sid)
            v = max(0.0, min(1.0, node.activation + seed_activation))
            level[sid] = v
            act[sid] = seed_activation
            if node.column:
                column_state.add(Column(node.column), seed_activation)
            frontier[sid] = seed_activation

    fired_edges: set[tuple[str, str]] = set()
    for hop in range(1, max_hops + 1):
        next_frontier: dict[str, float] = {}
        inhibition = column_state.lateral_inhibit()
        for src_id, src_act in frontier.items():
            for tgt_id, edge in hippocampus.outgoing(src_id):
                if tgt_id not in hippocampus.nodes:
                    continue
                if (src_id, tgt_id) in fired_edges:
                    continue
                signal = edge.transmit(src_act)
                if signal == 0.0:
                    continue
                fired_edges.add((src_id, tgt_id))
                tgt = hippocampus.get_node(tgt_id)
                col_factor = (inhibition.get(Column(tgt.column), 1.0)
                              if tgt.column else 1.0)
                delivered = signal * (PROPAGATION_TAU ** (hop - 1)) * col_factor
                v = level.get(tgt_id, tgt.activation)
                v = max(0.0, min(1.0, v + delivered))
                level[tgt_id] = v
                act[tgt_id] = max(act.get(tgt_id, 0.0), v)
                if tgt.column:
                    column_state.add(Column(tgt.column), abs(delivered))
                if v >= tgt.firing_threshold:
                    next_frontier[tgt_id] = v
        if not next_frontier:
            break
        frontier = next_frontier
    return act


def forecast_citations(
    hippocampus: "Hippocampus",
    store: "TripleStore",
    user_message: str,
    *,
    turn: int,
    model_id: str,
    k: int = DEFAULT_FORECAST_K,
) -> CitationForecast | None:
    """Form and PERSIST the forecast. Returns None only when disabled.

    An empty forecast (no seed is a graph node, or nothing anchors) is a
    CitationForecast with k=0 and no rows — declared, not silent, and it
    writes nothing because there is nothing to commit to.
    """
    if not forecast_enabled():
        return None

    terms = extract_terms(user_message)
    seeds = [t for t in terms if t in hippocampus.nodes]
    fid = str(uuid.uuid4())

    activations = _spread_readonly(hippocampus, seeds)
    seed_set = set(seeds)

    empty = CitationForecast(
        forecast_id=fid, k=0, rows=(), seeds=tuple(seeds),
        total_terms=len(terms), forecastable_terms=len(seeds))
    if not activations:
        return empty

    surfaces = [n for n, _ in sorted(activations.items(),
                                     key=lambda kv: (-kv[1], kv[0]))]
    surfaces = surfaces[:MAX_ANCHOR_NODES]
    anchored = store.anchored_exact(
        surfaces, exclude_relations=DEFAULT_EXCLUDED_RELATIONS)
    if not anchored:
        return empty

    # Rank by anchor activation; belief id is the deterministic tiebreak.
    # Prominence contributes nothing — that is what the null controls for.
    scored = []
    for t in anchored:
        a = max(activations.get(t.subject, 0.0),
                activations.get(t.object, 0.0))
        if a <= 0.0:
            continue
        seed_anchored = t.subject in seed_set or t.object in seed_set
        scored.append((a, t.id, seed_anchored))
    scored.sort(key=lambda x: (-x[0], x[1]))

    committed = scored[:max(0, k)]
    if not committed:
        return empty

    rows = []
    for rank, (a, bid, seed_anchored) in enumerate(committed):
        entry_id = log_confidence(
            "prediction", a, belief_id=bid,
            context={
                "forecast_id":        fid,
                "rank":               rank,
                "k":                  len(committed),
                "turn":               turn,
                "model_id":           model_id,
                "seed_anchored":      seed_anchored,
                "total_terms":        len(terms),
                "forecastable_terms": len(seeds),
            })
        rows.append(ForecastRow(belief_id=bid, activation=a, rank=rank,
                                entry_id=entry_id,
                                seed_anchored=seed_anchored))
    return CitationForecast(
        forecast_id=fid, k=len(rows), rows=tuple(rows), seeds=tuple(seeds),
        total_terms=len(terms), forecastable_terms=len(seeds))


def score_forecast(
    fc: CitationForecast,
    hits,
    cited: "set[int] | None",
    *,
    lineage_id: str | None = None,
    resolve_rows: bool = True,
) -> dict:
    """Score a committed forecast against the turn's citation flags, resolve
    its ledger rows, and return the declared block for the done payload.

    TWO LAYERS, deliberately distinct:
      row level    each row's claim ("this belief will be cited") is a fact
                   once the flags exist, so rows resolve whenever `cited` is
                   known — including on a turn where nothing was cited.
      turn level   precision@k vs the prominence null is a COMPARISON, and
                   the clearance measurement excluded no_injected /
                   flags_absent / none_cited turns from it. The block says
                   which exclusion applied, by name.

    `cited=None` means the flags do not exist (the pre-F.5 shape). An absent
    flag never scores — not as cited (which would make precision trivially
    1.0 and manufacture the signal under test) and not as uncited either:
    nothing resolves, the turn is excluded, declared.

    NO AMENDMENT: rows already resolved are skipped (Z.2's idempotent-by-skip
    — latest-row-wins would otherwise let a rescore rewrite the
    adjudication), so the first outcome stands and a rescore reports
    `resolved: 0`.
    """
    block = {
        "forecast_id":    fc.forecast_id,
        "k":              fc.k,
        "predicted":      [r.belief_id for r in fc.rows],
        "coverage":       {"forecastable": fc.forecastable_terms,
                           "total": fc.total_terms},
        "scorable":       False,
        "reason":         None,
        "precision":      None,
        "null_precision": None,
        "resolved":       0,
    }
    if not fc.rows:
        block["reason"] = "empty_forecast"
        return block
    if cited is None:
        block["reason"] = "flags_absent"
        return block

    # Row-level adjudication, first outcome stands. W6 — skipped when the
    # caller wants the turn-level score alone (`resolve_rows=False`): the
    # record carries that score and is written before the lineage id the
    # ledger's resolve needs exists, so the loop scores twice, once without
    # a ledger write and once, after the record, with it.
    already = ({eid: row for eid, row in latest_by_entry().items()
                if row.get("resolved_outcome") is not None} if resolve_rows else {})
    resolved = 0
    for r in (fc.rows if resolve_rows else ()):
        if r.entry_id is None or r.entry_id in already:
            continue
        resolve(r.entry_id, r.belief_id in cited,
                context={"forecast_id": fc.forecast_id,
                         "lineage_id": lineage_id,
                         # AU — the test that produced the flag this row
                         # resolves against; resolutions never amend, so the
                         # series is only readable if each row says which.
                         "citation_test": citation_test_version()})
        resolved += 1
    block["resolved"] = resolved

    if not hits:
        block["reason"] = "no_injected"
        return block
    if not cited:
        block["reason"] = "none_cited"
        return block

    predicted_ids = {r.belief_id for r in fc.rows}
    # The prominence null for the SAME turn: top-k of the injected set by
    # weight at injection — the degenerate predictor the clearance
    # measurement scored at mean 0.399.
    null_ids = {rt.triple.id for rt in
                sorted(hits, key=lambda rt: -rt.score)[:fc.k]
                if rt.triple.id is not None}
    block["scorable"] = True
    block["precision"] = len(predicted_ids & cited) / fc.k
    block["null_precision"] = len(null_ids & cited) / fc.k
    return block


# ---------------------------------------------------------------------------
# W6 — the turn-level score as the lineage record carries it
# ---------------------------------------------------------------------------
# Before W6 the forecast's precision and its null lived on the `done` frame
# only. The record now carries them, with what they were computed from: k,
# the predicted ids, the coverage, and whether and why the turn was scorable.
# Two keys of the `done` block are left out. `forecast_id` is a random id:
# with it two identical turns would seal different blocks, and the ledger's
# rows already carry the lineage id that joins them to the record.
# `resolved` counts ledger writes that happen after the record exists.
RECORD_KEYS = ("k", "predicted", "coverage", "scorable", "reason", "precision", "null_precision")
SCORING_FAILED = "scoring_failed"


def record_block(fc: CitationForecast, hits, cited: "set[int] | None") -> dict:
    """The block for the record: `score_forecast` with no ledger write, its
    two rates rounded to six decimals (the record's precision). Never
    raises: a scoring failure is recorded as `scorable: false` with the
    reason `scoring_failed`, never as a forecast that did not run."""
    try:
        block = score_forecast(fc, hits, cited, resolve_rows=False)
    except Exception as e:
        logger.warning("forecast scoring for the record failed: %s: %s", type(e).__name__, e)
        block = {"k": fc.k, "predicted": [r.belief_id for r in fc.rows],
                 "coverage": {"forecastable": fc.forecastable_terms, "total": fc.total_terms},
                 "scorable": False, "reason": SCORING_FAILED, "precision": None, "null_precision": None}
    out = {k: block[k] for k in RECORD_KEYS}
    for k in ("precision", "null_precision"):
        if out[k] is not None:
            out[k] = round(out[k], 6)
    return out

