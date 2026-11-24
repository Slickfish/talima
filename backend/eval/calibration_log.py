"""Append-only calibration log — accrues (stated confidence, resolved outcome)
pairs so a calibration analysis is possible later.

This module ONLY appends. It never rewrites, truncates, or deletes a row, and
it never raises into a caller: a logging failure must never affect a turn.

Row schema (one JSON object per line):

    {
      "entry_id":           str,            # uuid4 — the handle for later resolution
      "timestamp":          float,          # unix epoch, write time
      "mechanism":          str,            # triple | router | retrieval | prediction
      "belief_id":          int|str|None,   # triple id, or node id for prediction
      "stated_confidence":  float,          # the value the system emitted
      "resolved_outcome":   bool|None,      # null at write time; filled later
      "context":            dict            # optional, mechanism-specific
    }

`entry_id` is not in the brief's field list but is required for the brief's
own "outcome may be null at write time and filled later" — without a handle a
later row cannot be matched to its prediction. Because the file is append-only,
`resolve()` appends a NEW row carrying the same `entry_id` with the outcome
set; the latest row for an `entry_id` wins. Nothing is ever overwritten, which
matches the substrate's own never-delete discipline (Law 4).

Mechanism coverage as wired today:
  retrieval   — evidential weight of each injected belief at injection time.
  triple      — extraction confidence of each newly persisted belief.
  prediction  — the citation forecast (AF.1): stated confidence is the
                anchor activation the forecast ranked by, resolved by the
                turn's F.5 citation flags. One writer, by sentinel.
  router      — DECLARED BUT NOT WIRED: RoutingDecision (model_kernel.py)
                carries capability/profile/reason/fallback_used and emits no
                numeric confidence, so there is no value to log. The mechanism
                stays in MECHANISMS so a future router confidence lands here
                without a schema change.

Analysis of this log is deliberately out of scope; it accrues data only.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from backend.data_root import default_file

logger = logging.getLogger("nexus.calibration")

DEFAULT_LOG_PATH = "data/calibration_log.jsonl"

# Packet P.4 — three mechanisms, and the reservation is the point.
#
# The channel had accrued 297 rows on the operator's store, every one
# `retrieval` / `stated_confidence: 1.0` / `resolved_outcome: null` — rows
# unusable by construction, because "the query contained this term" is GIVEN,
# not estimated, so there is nothing to calibrate.
#
#   observation   seed activation: the query contained the term. Constant 1.0
#                 by construction, and logged UNAPOLOGETICALLY — the channel
#                 should say what it observed, not hide the trivial half.
#   propagation   computed propagated activation, hop > 0. THE CALIBRATABLE
#                 ROWS: an estimate that can be right or wrong.
#   prediction    RESERVED by P.4 for a real predictor; FILLED by AF.1's
#                 citation forecast (backend/cortex/forecast.py), which is
#                 its ONLY permitted writer —
#                 test_no_path_emits_the_reserved_prediction_mechanism now
#                 enforces the exclusive grant rather than emptiness. The
#                 old `predict()` stays quarantined (predictive.py): filling
#                 this channel from its map would have made the real
#                 predictor uncalibratable against its own history.
#   triple        the extractor's own stated confidence. A different channel
#                 from activation and untouched by P.4, kept because removing
#                 it would lose the one signal that already resolves.
#   resolution    an outcome arriving later for an existing entry_id.
#
# `router` is dropped: nothing ever emitted it. A member no code can reach
# reads as satisfied on inspection and is exactly the C10 / H.4 defect.
MECHANISMS = frozenset({"observation", "propagation", "prediction",
                        "triple", "resolution"})

# Reserved: writable from exactly one permitted path (AF.1's forecast),
# asserted by sentinel. Kept as a named constant so the reservation is a
# value the sentinel can read rather than a string it duplicates.
RESERVED_MECHANISMS = frozenset({"prediction"})


def log_path() -> Path:
    """Resolved at call time so tests/deployments can redirect via env.
    W1 — `NEXUS_CALIBRATION_LOG` wins whenever it is present; otherwise the
    file sits under `NEXUS_DATA_ROOT` when that is set (`backend/data_root.py`)."""
    return default_file("NEXUS_CALIBRATION_LOG", DEFAULT_LOG_PATH)


def _append(row: dict) -> None:
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    except Exception as e:                                   # never break a turn
        logger.warning("calibration log append failed: %s: %s", type(e).__name__, e)


def log_confidence(
    mechanism: str,
    stated_confidence: float,
    *,
    belief_id: int | str | None = None,
    resolved_outcome: bool | None = None,
    context: dict[str, Any] | None = None,
) -> str | None:
    """Append one stated-confidence row. Returns its entry_id (None on failure).

    `resolved_outcome` stays None when the outcome is not yet known; call
    `resolve()` later with the returned entry_id.
    """
    if mechanism not in MECHANISMS:
        logger.warning("calibration log: unknown mechanism %r (expected one of %s)",
                       mechanism, sorted(MECHANISMS))
        return None
    try:
        conf = float(stated_confidence)
    except (TypeError, ValueError):
        logger.warning("calibration log: non-numeric confidence %r for %s",
                       stated_confidence, mechanism)
        return None

    entry_id = str(uuid.uuid4())
    _append({
        "entry_id":          entry_id,
        "timestamp":         time.time(),
        "mechanism":         mechanism,
        "belief_id":         belief_id,
        "stated_confidence": conf,
        "resolved_outcome":  resolved_outcome,
        "context":           context or {},
    })
    return entry_id


def resolve(entry_id: str, outcome: bool, *, context: dict[str, Any] | None = None) -> None:
    """Record the outcome for a previously-logged entry.

    Appends a new row with the same entry_id (append-only); readers take the
    latest row per entry_id.
    """
    _append({
        "entry_id":          entry_id,
        "timestamp":         time.time(),
        "mechanism":         "resolution",
        "belief_id":         None,
        "stated_confidence": None,
        "resolved_outcome":  bool(outcome),
        "context":           context or {},
    })


def resolve_for_outcome(
    *,
    lineage_id: str,
    belief_ids: list,
    outcome: bool,
    outcome_ref: str = "",
) -> int:
    """Z.2 — resolve the confidences one outcome actually adjudicates.

    SCOPE: rows whose `context.lineage_id` matches AND whose `belief_id` is
    among the beliefs the outcome resolved to, still unresolved. The
    confidences stated in THAT turn about THOSE beliefs are what the world
    answered; all-rows-for-the-belief would fabricate outcomes for
    predictions never tested, and an outcome with no lineage reference
    resolves nothing because nothing ties it to a statement.

    IDEMPOTENT BY SKIP, not by rewrite: `resolve()` appends and the latest
    row per entry wins the merge, so re-resolving would let a later outcome
    silently rewrite an earlier adjudication. Rows already resolved are
    skipped, so a retry reports 0 and moves nothing — the first adjudication
    stands (append-only discipline, Law 4's shape one ledger over).

    Returns the count so the caller can DECLARE it (I-6): this channel was
    invisible for exactly as long as nothing reported how many rows it
    resolved — zero forever, assumed working.
    """
    if not lineage_id:
        return 0
    wanted = {str(b) for b in belief_ids}
    n = 0
    for eid, row in latest_by_entry().items():
        if row.get("resolved_outcome") is not None:
            continue
        if (row.get("context") or {}).get("lineage_id") != lineage_id:
            continue
        if str(row.get("belief_id")) not in wanted:
            continue
        resolve(eid, outcome, context={"outcome_ref": outcome_ref,
                                       "lineage_id": lineage_id})
        n += 1
    return n


def log_many(mechanism: str, items: list[tuple], **kw) -> list[str]:
    """Convenience: [(belief_id, confidence), ...] or
    [(belief_id, confidence, outcome), ...]."""
    ids = []
    for item in items:
        bid, conf = item[0], item[1]
        outcome = item[2] if len(item) > 2 else None
        eid = log_confidence(mechanism, conf, belief_id=bid,
                             resolved_outcome=outcome, **kw)
        if eid:
            ids.append(eid)
    return ids


def read_entries(path: Path | str | None = None) -> Iterator[dict]:
    """Stream rows for a later analysis. Not analysed here by design."""
    p = Path(path) if path else log_path()
    if not p.exists():
        return
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def latest_by_entry(path: Path | str | None = None) -> dict[str, dict]:
    """Collapse the append-only log: latest row per entry_id, outcome merged."""
    out: dict[str, dict] = {}
    for row in read_entries(path):
        eid = row.get("entry_id")
        if not eid:
            continue
        if eid in out and row.get("mechanism") == "resolution":
            out[eid] = {**out[eid], "resolved_outcome": row.get("resolved_outcome")}
        elif eid in out:
            out[eid] = {**out[eid], **row}
        else:
            out[eid] = row
    return out
