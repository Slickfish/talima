"""FZ-2 — the freeze switch for association learning (retires T-1).

WHAT FROZEN MEANS, MEASURED (FZ S1.2, traced writes on the rig)
    `NEXUS_RECURRENCE_THRESHOLD=999` stood in for a freeze from 5 September
    and stopped exactly one write path: consolidation promotion. Every other
    automatic write from the learning layer stayed live — the association
    graph persisted on every turn and every cycle, relation trails, procedural
    promotion, `rebalance`'s tier moves and `fire()`'s reinforcement counter.
    "Muted, not frozen." FZ-2 is the switch: `NEXUS_LEARNING=frozen` stops
    every automatic, unconfirmed write from the learning layer to the graph
    or the store, at the five store methods that make them:

        HebbianGraphStore.save          the association graph (turn and cycle)
        TripleStore.fire                reinforcement by citation (turn)
        TripleStore.rebalance           tier moves (cycle)
        Neocortex promotion entry       consolidate_from_hippocampus and
                                        store_pattern_triple (cycle)
        TrailStore.scan_new             relation trails (cycle) — DEFERRED,
                                        never dropped: the cursor does not
                                        advance, so an unfreeze lays them

    Frozen stops learning, not answering. Retrieval, extraction (the claim
    path through `add()` on a turn), the lineage record with its cited flags,
    the seals, and every confirmed human act stay live. `tests/test_fz_freeze.py`
    holds the guard: a traced run under frozen must write exactly the
    (table, host, method) pairs answered for in PERMITTED_UNDER_FROZEN, over
    drivers that are derived (boot, turns, cycles, every write route, every
    store-importing script) rather than listed.

SETTINGS (read at call time, through the dotenv path like every other knob)
    NEXUS_LEARNING=live | frozen           shipped default live; rollback literal
                                           NEXUS_LEARNING=live
    NEXUS_LEARNING_FROZEN_SINCE=YYYY-MM-DD the date the operator asserts; shown
                                           as asserted, never validated into a
                                           different date, never invented.
                                           Frozen with no date reads
                                           "frozen since: undeclared".

THE COUNTER
    A write the freeze stops is COUNTED, so frozen and broken look different.
    It lives in process memory and resets on restart, and its name says so:
    `skipped_writes_since_start`. New persistent writers: none.
"""
from __future__ import annotations

import os
import threading

LEARNING_SETTING = "NEXUS_LEARNING"
SINCE_SETTING = "NEXUS_LEARNING_FROZEN_SINCE"
LIVE, FROZEN = "live", "frozen"
ROLLBACK_LITERAL = f"{LEARNING_SETTING}={LIVE}"

# The methods that check the state — the freeze's own account of itself,
# reported on /cortex/stats. The guard does not read this list; it reads
# the database.
LEARNING_METHODS = (
    "HebbianGraphStore.save",
    "TripleStore.fire",
    "TripleStore.rebalance",
    "Neocortex.consolidate_from_hippocampus",
    "Neocortex.store_pattern_triple",
    "TrailStore.scan_new",
)

_lock = threading.Lock()
_skips: dict = {"total": 0, "by_method": {}}


def learning_mode() -> str:
    """`live` unless the setting says exactly `frozen` (case-insensitive)."""
    raw = (os.environ.get(LEARNING_SETTING) or LIVE).strip().lower()
    return FROZEN if raw == FROZEN else LIVE


def learning_frozen() -> bool:
    return learning_mode() == FROZEN


def frozen_since() -> str | None:
    """The operator's asserted from-date, verbatim; None when not stated."""
    raw = (os.environ.get(SINCE_SETTING) or "").strip()
    return raw or None


def learning_declaration() -> dict:
    """What every turn and the stats surface say. Never absent, never a
    blank, never an invented date."""
    mode = learning_mode()
    since = frozen_since() if mode == FROZEN else None
    if mode == LIVE:
        display = LIVE
    elif since:
        display = f"frozen since {since}"
    else:
        display = "frozen since: undeclared"
    return {"state": mode, "since": since, "since_source": "asserted", "display": display}


def record_skip(method: str, n: int = 1) -> int:
    """Count `n` writes `method` did not make because learning is frozen.
    Returns the running total."""
    with _lock:
        _skips["total"] += int(n)
        _skips["by_method"][method] = _skips["by_method"].get(method, 0) + int(n)
        return _skips["total"]


def skipped_writes() -> dict:
    with _lock:
        return {"skipped_writes_since_start": _skips["total"],
                "by_method": dict(_skips["by_method"])}


def reset_skips() -> None:
    """Tests only: a fresh process-start."""
    with _lock:
        _skips["total"] = 0
        _skips["by_method"].clear()


def learning_status() -> dict:
    """The /cortex/stats block."""
    return {
        **learning_declaration(),
        **skipped_writes(),
        "checked_at": list(LEARNING_METHODS),
        "rollback": ROLLBACK_LITERAL,
        "note": ("frozen means no automatic, unconfirmed write from the learning "
                 "layer to the graph or the store, checked at the methods listed; "
                 "retrieval, extraction, the lineage record and confirmed acts are "
                 "unaffected; relation trails are deferred, not dropped. The skip "
                 "counter is in-process and resets on restart. The from-date is the "
                 "operator's assertion."),
    }
