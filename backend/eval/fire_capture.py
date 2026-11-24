"""CP — the durable fire capture (CE's named successor).

WHY
    CE (C-33) measured on the rig that tiers 1 to 3 of the cascade change
    neither the estimate that drives escalation nor the next turn's content:
    their edits are rebuilt away at stage 7. B-49 asks whether to retire,
    re-aim or document them, and the rig is not the instance. Today a fire
    appears only as two frames in the 200-event ring: no `lineage_id`, no
    turn number, nothing after the next twenty turns, nothing after a
    restart. So the question cannot be answered from anything that exists.

WHAT IS CAPTURED
    One row per fire, keyed by `lineage_id` and turn, in AO's shape (a JSONL
    file, a path constant acknowledged to the writer ratchet, an env gate
    read at call time, a coverage block on `/cortex/stats`): CE's S1.5 field
    list — the entry estimate and the thresholds in force, per tier
    attempted the estimate before and after, items removed and the integrity
    verdict, the tier reached and the stop reason by name, what tier 4
    removed, the dynamic block's tokens and KB-line count, and the session
    memory / graph context sizes at entry and after stage 7 — plus, per
    tier, the IDS it removed or demoted.

    And on the NEXT row, the next-turn check: for every id the previous
    turn's fire removed or demoted, one of `present`, `absent`, `demoted` in
    the next turn's rebuilt block, by id. That is B-49's content decision,
    written per turn so nobody has to reconstruct it. A turn that did not
    fire writes a row only when it carries that check.

IDS AND NUMBERS, NEVER TEXT
    No answer, no reasoning, no prompt, no question, no rendered surface.
    The block appears as a token count and a KB-line count; tier 4's summary
    as a character count. `tests/test_cp_fire_capture.py` holds both halves
    of that: the row's key set against `ROW_KEYS` and `TEXT_KEY_MARKERS`,
    and a planted rare token from the turn's answer, question and block that
    must appear in no value of any row.

OBSERVED AT ASSEMBLY, NOT AT THE END OF THE TURN
    The check resolves against the block the next turn SAW, so `observe()`
    is called before that turn's own cascade. A turn's own fire must not be
    allowed to answer the previous turn's question.

BOUND TO THE RECORD, DERIVED NOT RECORD
    Each row carries the sealed record's `lineage_id` and `record_hash`, so
    a row with no record behind it, or one whose record has moved, is named
    by `verify()` for free — the capture itself is not sealed and no
    verification endpoint extends to it.

OFF BY DEFAULT, AND OFF MEANS NOTHING
    No file, no directory, no walk of the graph, no env read beyond the
    gate. `NEXUS_FIRE_CAPTURE=on` enables; the destination is redirected in
    tests by the `NEXUS_FIRE_CAPTURE_PATH` line in conftest. This module
    never raises into a turn (the calibration_log discipline) — `observe`
    included, which runs inside the turn at stage 2: a failed observation
    is a warning and a row whose ids read `unresolved`, not a broken turn.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from backend.data_root import default_file
from backend.loop.compaction import (COMPACTION_BLOCK_OPEN, TIER_1_SNIP_AT, TIER_2_MICRO_AT,
                                     TIER_3_COLLAPSE_AT, TIER_4_AUTO_AT, estimate_tokens_text)
from backend.loop.prompt_render import KB_HEADER

logger = logging.getLogger("nexus.capture")

# Acknowledged in tests/_writers.py ACKNOWLEDGED_DATA_CONSTANTS (function-based
# writer, AO's shape) and redirected in tests by the NEXUS_FIRE_CAPTURE_PATH
# env line in conftest.
DEFAULT_FIRE_CAPTURE_PATH = "data/fire_capture.jsonl"

PRESENT, ABSENT, DEMOTED = "present", "absent", "demoted"
# A fourth verdict, for ids no observation could judge: the turn's
# observation failed or was not taken. Counted by the tally as unresolved,
# never as present. `NO_OBSERVATION` is the check's `reason` then — a
# closed vocabulary, never text.
UNRESOLVED = "unresolved"
NO_OBSERVATION = "no_observation"

# CE's S1.1 stop reasons, by the tier the cascade last fired. The names are
# the ones `tests/fixtures/ce_rig.py` pins (a test asserts the two agree, so
# the series reads across the rig and the instance).
STOP_REASONS = {
    "snip":           "below_tier2_after_snip",
    "microcompact":   "below_tier3_after_micro",
    "graph_collapse": "below_tier4_after_collapse",
    "auto_compact":   "tier4_ran",
}
NO_FIRE = "below_tier1_at_entry"

THRESHOLDS = {"snip": TIER_1_SNIP_AT, "microcompact": TIER_2_MICRO_AT,
              "graph_collapse": TIER_3_COLLAPSE_AT, "auto_compact": TIER_4_AUTO_AT}

# The row's declared schema — the file's equivalent of a table's columns, in
# the BD-2 shape: a key arrives on a row only by editing this set.
ROW_KEYS = frozenset({
    "kind", "turn", "lineage_id", "record_hash", "ts", "entry_estimate",
    "thresholds", "tier_reached", "stop_reason", "final_tokens", "tiers",
    "tier4", "block", "sizes", "removed_total", "check",
})

# A key whose name contains one of these would be a text sink. The value half
# of the rule is the test's: no row value may contain the turn's answer,
# question or block.
TEXT_KEY_MARKERS = ("text", "answer", "reasoning", "prompt", "content",
                    "surface", "query", "question", "message", "body", "utterance")


def capture_enabled() -> bool:
    """`NEXUS_FIRE_CAPTURE=on` enables. Off (the default) is exactly: nothing
    is consumed, created, walked or written. Read at call time."""
    return os.environ.get("NEXUS_FIRE_CAPTURE", "off").strip().lower() == "on"


def capture_path() -> Path:
    """Resolved at call time so tests/deployments can redirect via env.
    W1 — `NEXUS_FIRE_CAPTURE_PATH` wins whenever it is present; otherwise the
    file sits under `NEXUS_DATA_ROOT` when that is set."""
    return default_file("NEXUS_FIRE_CAPTURE_PATH", DEFAULT_FIRE_CAPTURE_PATH)


# ---------------------------------------------------------------------------
# The id vocabulary. Three kinds, because three different things are removed:
# a session-memory row (an edge key), a graph_context row (a triple key) and
# a hippocampus edge (an edge key). The cascade emits the raw tuples and this
# module names them, so `compaction.py` carries no id format.
# ---------------------------------------------------------------------------

def edge_id(source: str, target: str) -> str:
    return f"edge:{source}|{target}"


def triple_id(subject: str, relation: str, object: str) -> str:
    return f"triple:{subject}|{relation}|{object}"


def concept_id(concept: str) -> str:
    return f"concept:{concept}"


def ids_of(event) -> dict:
    """The ids one CompactionEvent removed or demoted, from the sets the
    cascade emits: a session-memory row (tier 1) or a graph_context row
    (tier 2) is a removed id; a hippocampus edge collapse demoted (tier 3)
    is a demoted id. FC-0's concept set rides BESIDE them as `concepts` —
    recorded (S1.5 asks for it), never counted as removals, because a
    concept vanishes only because a row carrying it did, and counting both
    would read three removed rows as nine ids. Tier 4 drops messages, which
    have no ids: its count rides `items_removed` and its detail the `tier4`
    block."""
    removed = [triple_id(*t) for t in getattr(event, "removed_triples", ()) or ()]
    removed += [edge_id(*e) for e in getattr(event, "removed_edges", ()) or ()]
    demoted = [edge_id(*e) for e in getattr(event, "demoted_edges", ()) or ()]
    concepts = [concept_id(c) for c in getattr(event, "removed_concepts", ()) or ()]
    return {"removed": sorted(set(removed)), "demoted": sorted(set(demoted)),
            "concepts": sorted(set(concepts))}


# ---------------------------------------------------------------------------
# The observation and the resolution
# ---------------------------------------------------------------------------

def _observe(*, hits, rendered_activation=(), hippocampus=None) -> dict:
    """The ids the turn's rebuilt block stands on, at the moment it was
    assembled: the injected triples and their concepts, the cortex section's
    nodes, and the hippocampus's live and demoted edges (an edge is not in
    the block, but a demoted one transmits nothing, so it cannot reach the
    cortex section — that is what `demoted` means for the check)."""
    triples, concepts = set(), set()
    injected = set()
    for rt in hits or ():
        t = rt.triple
        triples.add(triple_id(t.subject, t.relation, t.object))
        concepts.add(concept_id(t.subject))
        concepts.add(concept_id(t.object))
        if t.id is not None:
            injected.add(t.id)
    for r in rendered_activation or ():
        concepts.add(concept_id(r[0]))
    live, demoted = set(), set()
    for e in (hippocampus.edges() if hippocampus is not None else ()):
        (demoted if e.demoted else live).add(edge_id(e.source, e.target))
    return {"triples": sorted(triples), "concepts": sorted(concepts),
            "edges_live": sorted(live), "edges_demoted": sorted(demoted),
            "injected_ids": sorted(injected)}


def observe(*, hits, rendered_activation=(), hippocampus=None) -> dict | None:
    """`_observe`, guarded. It runs INSIDE the turn, at stage 2, when the gate
    is on, so it must never raise there: on any exception it logs a warning
    and returns None, which `capture_fire` records as a check whose ids are
    `unresolved` with `reason: no_observation` — the row is still written,
    and no later turn is allowed to answer that question from its own
    block."""
    try:
        return _observe(hits=hits, rendered_activation=rendered_activation,
                        hippocampus=hippocampus)
    except Exception as e:                       # never break a turn
        logger.warning("fire capture observation failed: %s: %s",
                       type(e).__name__, e)
        return None


def resolve(ids, observed: dict | None) -> dict:
    """`present` | `absent` | `demoted` per id, against one observation;
    `unresolved` for every id when there is no observation to judge by.

    An edge the graph no longer holds is `absent`; one it holds demoted is
    `demoted`; one it holds live is `present`. A triple is `present` iff the
    rebuilt block carries it — there is no demoted state for a row of
    `graph_context`, because nothing demotes one. (A concept id, if one is
    ever handed in, is judged against the block's concepts the same way.)
    """
    if observed is None:
        return {i: UNRESOLVED for i in ids}
    live = set(observed.get("edges_live") or ())
    dem = set(observed.get("edges_demoted") or ())
    tri = set(observed.get("triples") or ())
    con = set(observed.get("concepts") or ())
    out: dict[str, str] = {}
    for i in ids:
        if i.startswith("edge:"):
            out[i] = DEMOTED if i in dem else (PRESENT if i in live else ABSENT)
        elif i.startswith("triple:"):
            out[i] = PRESENT if i in tri else ABSENT
        else:
            out[i] = PRESENT if i in con else ABSENT
    return out


def _kb_lines(block: str) -> int:
    """KB CONTEXT lines in the block as sent — a count, never the lines."""
    if KB_HEADER not in block:
        return 0
    tail = block.split(KB_HEADER, 1)[1]
    n = 0
    for line in tail.splitlines()[1:]:
        if not line.strip():
            break
        n += 1
    return n


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------

# The one fire awaiting its next-turn check: the turn, its lineage_id and the
# ids it removed or demoted. IN-PROCESS and deliberately not persisted: a
# restart between a fire and the next turn loses that one check, and the
# tally's `unresolved_ids` says so rather than the numbers quietly not adding
# up (I-6). Nothing else is remembered between turns.
_PENDING: dict | None = None


def reset_pending() -> None:
    """Tests only: drop the awaiting check so one test cannot resolve into
    another's row."""
    global _PENDING
    _PENDING = None


def pending() -> dict | None:
    return dict(_PENDING) if _PENDING else None


def _write(row: dict) -> None:
    p = capture_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def capture_fire(*, turn: int, lineage_id: str | None, record_hash: str | None,
                 result, observed: dict | None,
                 sizes_entry: tuple, sizes_after: tuple,
                 block_entry: str = "", block_sent: str = "") -> str | None:
    """Write this turn's row, if it has one. Returns `"fire"`, `"check"` or
    None. Never raises.

    Called on EVERY turn (a quiet turn carries the previous fire's check),
    after the lineage write so `lineage_id` and `record_hash` are real.
    `observed` is the turn's own observation, taken before its cascade —
    None when it failed or was not taken, and then the pending ids are
    recorded `unresolved` with the reason, never silently dropped.
    """
    global _PENDING
    if not capture_enabled() or not lineage_id:
        return None
    try:
        events = list(getattr(result, "events", ()) or ())
        reports = list(getattr(result, "reports", ()) or ())
        entry = (events[0].tokens_before if events
                 else int(getattr(result, "final_tokens", 0) or 0))

        check = None
        if _PENDING is not None:
            ids = resolve(_PENDING["ids"], observed)
            counts = {PRESENT: 0, ABSENT: 0, DEMOTED: 0, UNRESOLVED: 0}
            for verdict in ids.values():
                counts[verdict] = counts.get(verdict, 0) + 1
            check = {
                "of_turn":        _PENDING["turn"],
                "of_lineage_id":  _PENDING["lineage_id"],
                # S1.5 — turn N+1 joined to turn N: this turn's entry
                # estimate and injected belief ids, reported beside the
                # content decision and never used to make it.
                "entry_estimate": entry,
                "injected_ids":   (list(observed.get("injected_ids") or ())
                                   if observed is not None else []),
                "ids":            ids,
                "counts":         counts,
                # Why the ids could not be judged, when they could not.
                "reason":         (NO_OBSERVATION if observed is None else None),
            }
            # Cleared either way: a later turn's block cannot answer THIS
            # turn's question, so an unobserved turn leaves its predecessor's
            # ids unresolved for good, and the tally counts them.
            _PENDING = None

        if not events:
            if check is None:
                return None
            _write({"kind": "check", "turn": turn, "lineage_id": lineage_id,
                    "record_hash": record_hash, "ts": time.time(),
                    "entry_estimate": entry, "check": check})
            return "check"

        tiers, all_ids = [], set()
        for i, ev in enumerate(events):
            report = reports[i] if i < len(reports) else None
            ids = ids_of(ev)
            all_ids.update(ids["removed"])
            all_ids.update(ids["demoted"])
            tiers.append({
                "tier":           ev.tier.value,
                "before":         ev.tokens_before,
                "after":          ev.tokens_after,
                "items_removed":  ev.items_removed,
                "integrity":      (report.pass_fail if report is not None else None),
                "removed_ids":    ids["removed"],
                "demoted_ids":    ids["demoted"],
                "removed_count":  len(ids["removed"]),
                "demoted_count":  len(ids["demoted"]),
                # FC-0's set, beside the ids and outside the tally.
                "concepts":       ids["concepts"],
                "concepts_count": len(ids["concepts"]),
            })

        tier4 = None
        if events[-1].tier.value == "auto_compact":
            msgs = getattr(getattr(result, "state", None), "messages", ()) or ()
            head = msgs[0].get("content", "") if msgs else ""
            summary = (reports[-1].structural_summary if reports else {}) or {}
            tier4 = {
                "messages_dropped": events[-1].items_removed,
                # A LENGTH, not the summary: the block's characters when the
                # head of the history is the runtime's compaction block, else
                # null (not measured).
                "summary_chars": (len(head) if head.startswith(COMPACTION_BLOCK_OPEN)
                                  else None),
                "work_unit_ids_summarised": list(summary.get("work_unit_ids_summarised") or ()),
            }

        row = {
            "kind":           "fire",
            "turn":           turn,
            "lineage_id":     lineage_id,
            "record_hash":    record_hash,
            "ts":             time.time(),
            "entry_estimate": entry,
            "thresholds":     dict(THRESHOLDS),
            "tier_reached":   events[-1].tier.value,
            "stop_reason":    STOP_REASONS.get(events[-1].tier.value, NO_FIRE),
            "final_tokens":   int(getattr(result, "final_tokens", 0) or 0),
            "tiers":          tiers,
            "tier4":          tier4,
            "block": {
                "tokens_entry": estimate_tokens_text(block_entry or ""),
                "tokens_sent":  estimate_tokens_text(block_sent or ""),
                "kb_lines":     _kb_lines(block_sent or ""),
            },
            "sizes": {
                "session_memory_entry":         int(sizes_entry[0]),
                "graph_context_entry":          int(sizes_entry[1]),
                "session_memory_after_rebuild": int(sizes_after[0]),
                "graph_context_after_rebuild":  int(sizes_after[1]),
            },
            "removed_total":  len(all_ids),
            "check":          check,
        }
        _write(row)
        if all_ids:
            _PENDING = {"turn": turn, "lineage_id": lineage_id,
                        "ids": sorted(all_ids)}
        return "fire"
    except Exception as e:                       # never break a turn
        logger.warning("fire capture failed: %s: %s", type(e).__name__, e)
        return None


# ---------------------------------------------------------------------------
# The readers: the tally, the binding, the coverage block
# ---------------------------------------------------------------------------

def read_rows(path: Path | None = None) -> list[dict]:
    """Every row, in file order. Reading must not create the file."""
    p = path or capture_path()
    if not p.exists():
        return []
    rows = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        return rows
    return rows


def tally(rows: list[dict]) -> dict:
    """B-49's numbers so far. Counts, never rates, never a verdict."""
    fires = [r for r in rows if r.get("kind") == "fire"]
    checks = [r["check"] for r in rows if r.get("check")]
    removed_total = sum(int(r.get("removed_total") or 0) for r in fires)
    counts = {PRESENT: 0, ABSENT: 0, DEMOTED: 0}
    resolved = 0
    for c in checks:
        for verdict in (c.get("ids") or {}).values():
            if verdict in counts:
                counts[verdict] += 1
                resolved += 1          # `unresolved` is not a resolution
    tier_1_to_3 = [r for r in fires if r.get("tier_reached") != "auto_compact"]
    return {
        "fire_rows":      len(fires),
        "check_rows":     sum(1 for r in rows if r.get("kind") == "check"),
        "last_fire_turn": (fires[-1].get("turn") if fires else None),
        "b49": {
            "removed_total":  removed_total,
            "present_next":   counts[PRESENT],
            "absent_next":    counts[ABSENT],
            "demoted_next":   counts[DEMOTED],
            # The ids no next turn has answered for yet (the last fire, or a
            # restart between a fire and its successor). removed_total minus
            # the three verdicts is this number, not a silent gap.
            "unresolved_ids":           max(0, removed_total - resolved),
            "fires_tier_1_to_3":        len(tier_1_to_3),
            "fires_needed_for_the_rule": max(0, 10 - len(tier_1_to_3)),
        },
    }


def verify(rows: list[dict], lookup) -> dict:
    """The binding. `lookup(lineage_id)` returns the sealed record's
    `record_hash`, or None when there is no record. A row with no record is
    named; so is one whose record hash has moved."""
    unbound, mismatched = [], []
    for r in rows:
        lid = r.get("lineage_id")
        got = lookup(lid) if lid else None
        if got is None:
            unbound.append(lid)
        elif got != r.get("record_hash"):
            mismatched.append(lid)
    return {"rows": len(rows), "unbound": unbound, "mismatched": mismatched,
            "bound": len(rows) - len(unbound) - len(mismatched)}


NOTE = (
    "derived, not record: one row per compaction fire, bound to the sealed "
    "record by lineage_id and record_hash and verifiable against it, but not "
    "sealed and reached by no verification endpoint. It records IDS AND "
    "NUMBERS ONLY — no answer, no reasoning, no prompt, no question, no "
    "rendered surface; the dynamic block appears as a token count and a "
    "KB-line count, tier 4's summary as a character count. What it does NOT "
    "record: quiet turns (a turn that did not fire writes a row only when it "
    "carries the previous fire's next-turn check), the whole prompt, the "
    "history, and anything before the gate was turned on (forward-only). The "
    "next-turn check is observed at block assembly, before that turn's own "
    "cascade; a turn whose observation failed records its predecessor's ids "
    "as unresolved with reason no_observation, and unresolved_ids counts "
    "them. The capture reports; it does not decide B-49."
)


def coverage() -> dict:
    """The declared surface. Reading must not create the file (off means
    nothing)."""
    t = tally(read_rows())
    return {
        "enabled":        capture_enabled(),
        "fire_rows":      t["fire_rows"],
        "last_fire_turn": t["last_fire_turn"],
        "check_rows":     t["check_rows"],
        "b49":            t["b49"],
        "note":           NOTE,
    }
