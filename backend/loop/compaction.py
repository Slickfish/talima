"""Four-tier compaction cascade (arch §8, Law VII).

Cheapest first: snip -> microcompact -> graph_collapse -> auto_compact.
Each tier fires only at its threshold AND only when prior tiers did not bring
context back under the next threshold (T3 always runs before T4 — KEY PRINCIPLE).

Never silent truncate: every reduction emits a CompactionEvent. The stable
system prefix is NEVER modified by any tier.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace as _dc_replace
from enum import Enum
from typing import Awaitable, Callable, TYPE_CHECKING

from backend.cortex.edges import SynapticEdge
# BA fix 3 — the prefix and its one predicate live in prefix.py so the
# cascade can compare against the CONSTANT rather than against its own
# argument. `assert_stable_prefix` is re-exported here for its importers.
from backend.loop.prefix import (                       # noqa: F401
    STABLE_SYSTEM_PREFIX, assert_stable_prefix, prefix_intact)
from backend.loop.state import NexusLoopState, next_state

if TYPE_CHECKING:
    from backend.memory.hippocampus import Hippocampus


# ----- arch §8.1 budget -----
TOKEN_BUDGET           = 8192
RESPONSE_HEADROOM      = 1500
STABLE_PREFIX_BUDGET   = 400

# ----- arch §8.2 thresholds -----
TIER_1_SNIP_AT       = int(0.70 * TOKEN_BUDGET)  # 5734
TIER_2_MICRO_AT      = int(0.80 * TOKEN_BUDGET)  # 6553
TIER_3_COLLAPSE_AT   = int(0.88 * TOKEN_BUDGET)  # 7209
TIER_4_AUTO_AT       = int(0.95 * TOKEN_BUDGET)  # 7782

# ----- per-tier knobs -----
SESSION_TRIPLE_KEEP_TOP_N = 10                  # arch §8.2 "keep top 10"
MICRO_AGE_SECONDS         = 30 * 60             # arch §8.2 ">30min"
MICRO_WEIGHT_THRESHOLD    = 0.3                 # arch §8.2 "weight <0.3"

# ----- integrity guard tunables (CLAUDE.md §"Compaction Integrity") -----
# BD-3 — every rule reports at WARN. The FAIL tier, the halt and the
# marked-state flag were retired: E.5 established that no rule read a
# quantity tiers 3 and 4 touch, so the halt had no reachable trigger, and
# B-24 that nothing read the flag. The two rules over node epistemic state
# and node confidence went with them (LNS-1: both constants after creation).
EDGE_LOSS_THRESHOLD   = 0.30   # >30% edge loss without prop. node loss = WARN
MINORITY_LOSS_MAX     = 0.50   # >50% low-freq triples removed = WARN


class Tier(str, Enum):
    SNIP     = "snip"
    MICRO    = "microcompact"
    COLLAPSE = "graph_collapse"
    AUTO     = "auto_compact"


@dataclass(frozen=True)
class CompactionEvent:
    tier: Tier
    tokens_before: int
    tokens_after: int
    items_removed: int
    reason: str
    # CP — WHAT the tier removed or demoted, beside how many. Emit only: the
    # cascade diffs sets it already snapshots and appends them here, and no
    # threshold, order, decision or estimate reads them. Raw tuples, not id
    # strings: the id vocabulary belongs to the capture
    # (`backend/eval/fire_capture.py`), so this module carries no format and
    # no dependency on it. Default-empty, so a tier that removes nothing and
    # every pre-CP construction of this event are unchanged.
    removed_edges:    tuple = ()   # tier 1 — session_memory rows: (source, target)
    removed_triples:  tuple = ()   # tier 2 — graph_context rows: (subject, relation, object)
    removed_concepts: tuple = ()   # tier 2 — FC-0's set, read from the tier's own report
    demoted_edges:    tuple = ()   # tier 3 — hippocampus edges demoted: (source, target)

    @property
    def tokens_freed(self) -> int:
        return self.tokens_before - self.tokens_after


# ---------- token estimator (rough; precision is unnecessary for thresholds) ----------

def estimate_tokens_text(text: str) -> int:
    return len(text) // 4 + 1


def estimate_tokens_messages(messages: tuple) -> int:
    total = 0
    for m in messages:
        c = m.get("content", "") if isinstance(m, dict) else str(m)
        total += estimate_tokens_text(c) + 4   # 4 tokens of role/control overhead
    return total


def estimate_tokens(
    state: NexusLoopState,
    *,
    prefix: str = "",
    dynamic_block: str = "",
) -> int:
    return (
        estimate_tokens_text(prefix)
        + estimate_tokens_text(dynamic_block)
        + estimate_tokens_messages(state.messages)
    )


# ---------- tier 1: snip ----------

# Session-edge decay rate per hour. Conservative — ~3% per hour matches the
# Tier 1 (active) per-day decay used by the persistent triple store, scaled to
# the hour-scale of session memory.
SESSION_DECAY_PER_HOUR = 0.03


def _current_session_weight(triple, now: float | None = None) -> float:
    """Recompute weight with time decay since storage.

    Tuple format is (source, target, weight, last_fired). Older 3-tuples are
    accepted as a transitional fallback — they decay from now (no history).
    """
    weight = float(triple[2])
    if len(triple) >= 4:
        last_fired = float(triple[3])
    else:
        last_fired = now if now is not None else time.time()
    elapsed_h = max(0.0, ((now or time.time()) - last_fired) / 3600.0)
    return weight * ((1.0 - SESSION_DECAY_PER_HOUR) ** elapsed_h)


# The precision snip's order reads: the stored session weight is `round(w, 3)`
# (stage 7) and the record's weights are six decimals. A decayed difference
# below this is clock noise, not standing.
SNIP_RANK_PRECISION = 6


def _snip_key(triple, now: float) -> tuple:
    """snip's TOTAL order (SNIP-DET): the decayed weight at a stated
    precision — rows whose `last_fired` differ by microseconds tie exactly —
    then the edge key, so the same session memory keeps the same rows
    whatever order it arrived in and whatever microsecond the clock showed.
    The decay helper is unchanged; only the key rounds its result."""
    return (-round(_current_session_weight(triple, now=now), SNIP_RANK_PRECISION),
            str(triple[0]), str(triple[1]))


def snip(state: NexusLoopState, *, keep_top_n: int = SESSION_TRIPLE_KEEP_TOP_N
         ) -> tuple[NexusLoopState, int]:
    """Drop lowest current_weight() session triples; keep top N (arch §8.2)."""
    sm = list(state.session_memory)
    if len(sm) <= keep_top_n:
        return next_state(state, compaction_state="snipped"), 0
    # session_memory is (source, target, weight, last_fired); rank by current
    # weight under a total order (SNIP-DET — see `_snip_key`).
    now = time.time()
    ranked = sorted(sm, key=lambda t: _snip_key(t, now))
    kept = tuple(ranked[:keep_top_n])
    removed = len(sm) - len(kept)
    return next_state(state, session_memory=kept, compaction_state="snipped"), removed


# ---------- tier 2: microcompact ----------

def microcompact(
    state: NexusLoopState,
    hippocampus: "Hippocampus | None",
    *,
    age_seconds: float = MICRO_AGE_SECONDS,
    weight_threshold: float = MICRO_WEIGHT_THRESHOLD,
    now: float | None = None,
) -> tuple[NexusLoopState, int]:
    """Clear stale individual triples — preserve stable prefix integrity.

    Drops a triple from state.graph_context when EITHER:
      - hippocampus has no live edge for it, OR
      - that edge is below weight_threshold AND last fired more than age_seconds ago.
    """
    now = now or time.time()
    new_gc, removed = [], 0
    for triple in state.graph_context:
        if len(triple) < 3:
            removed += 1
            continue
        s, _r, o = triple[0], triple[1], triple[2]
        edge = hippocampus.get_edge(s, o) if hippocampus is not None else None
        if edge is None:
            removed += 1
            continue
        stale_age    = (now - edge.last_fired) > age_seconds and edge.last_fired > 0
        stale_weight = edge.current_weight(now) < weight_threshold
        if stale_age and stale_weight:
            removed += 1
            continue
        new_gc.append(triple)
    return (
        next_state(state, graph_context=tuple(new_gc), compaction_state="microcompacted"),
        removed,
    )


# ---------- tier 3: graph collapse ----------

def graph_collapse(hippocampus: "Hippocampus | None") -> int:
    """Compress A->B->C paths to A->C with averaged weight (arch §8.2).

    Only collapses simple linear nodes (one in-edge, one out-edge, no self-loop).
    Returns count of nodes collapsed.
    """
    if hippocampus is None:
        return 0
    collapsed = 0
    # SNIP-DET — `nodes` is a set; iterating it put the candidates in hash
    # order, which decides WHICH node of a chain is collapsed first and so
    # which edges are demoted, per process and per PYTHONHASHSEED. Sorted is
    # a total order.
    candidates = [
        nid for nid in sorted(hippocampus.nodes)
        if hippocampus.in_degree(nid) == 1 and hippocampus.out_degree(nid) == 1
    ]
    for b in candidates:
        preds = hippocampus.predecessors(b)
        succs = hippocampus.successors(b)
        if not preds or not succs:
            continue
        a, c = preds[0], succs[0]
        if a == c or a == b or c == b:
            continue
        edge_ab = hippocampus.get_edge(a, b)
        edge_bc = hippocampus.get_edge(b, c)
        if edge_ab is None or edge_bc is None:
            continue
        # E.3 — demoted edges stay in the graph, so without this an already
        # collapsed path would be re-collapsed on every pass, inflating the
        # count and re-demoting edges forever.
        if edge_ab.demoted or edge_bc.demoted:
            continue
        avg = (edge_ab.current_weight() + edge_bc.current_weight()) / 2.0
        # E.3 / I-2 — carry recurrence forward onto the synthesised edge.
        # MAX, not sum: recurrence_count is "episodes this association
        # survived". Summing would fabricate episodes that never happened and
        # could push a->c over the consolidation gate on evidence neither
        # source edge had; min would discard real evidence. Max preserves the
        # strongest evidence actually observed without inventing any.
        carried = max(edge_ab.recurrence_count, edge_bc.recurrence_count)
        existing = hippocampus.get_edge(a, c)
        if existing is None:
            hippocampus.add_edge(SynapticEdge(
                source=a, target=c, weight=avg,
                recurrence_count=carried,
                last_counted_at=max(edge_ab.last_counted_at,
                                    edge_bc.last_counted_at),
            ))
        else:
            existing.weight = max(existing.weight, avg)
            existing.recurrence_count = max(existing.recurrence_count, carried)
        # The source edges are DEMOTED, never removed: deleting them would
        # destroy their accumulated recurrence and their recoverability.
        edge_ab.demoted = True
        edge_bc.demoted = True
        collapsed += 1
    return collapsed


# ---------- tier 4: auto compact ----------

Summariser = Callable[[tuple], Awaitable[str]]


# ---------------------------------------------------------------------------
# BA fix 1 — the summary slot leaves system role
# ---------------------------------------------------------------------------
# THE FINDING (compaction boundary audit, A1b): tier 4 replaced history with
# `{"role": "system", "content": "[CONVERSATION SUMMARY]\n" + text}` at the
# head of the history. With no summariser wired the text is the count stub
# below; with one wired, model prose would land in a system-role slot with
# system authority — the exact shape the September 2026 misalignment reports
# described. The seam (`Summariser`) is kept; its container and position
# change.
#
# THE ROLE, decided rather than defaulted. Providers offer three roles.
#   system     — instruction authority; the defect.
#   user       — the model reads it as its interlocutor's words. A summary
#                the runtime (or a summariser model) wrote of the model's OWN
#                prior speech, presented as the user's words, misattributes
#                it — and a `user`-role block would be a second labelled-data
#                convention beside the history path fix 7 governs.
#   assistant  — the model reads it as its own prior speech, which is what a
#                summary of the conversation so far mostly is; it carries the
#                LOWEST instruction authority of the three; and it lands on
#                the history path, so fix 7's bounding and labelling cover
#                the summary and the verbatim history with one mechanism.
# The summary is therefore an `assistant`-role message wrapped in a
# runtime-written delimiter with `source="compaction"`. The delimiter is a
# markup tag, which the gate's universal `markup` rule refuses in any term,
# so no claim can carry the opening or closing tag into a prompt. Position:
# the head of the HISTORY (the tail's first user message follows it), never
# `messages[0]` of the request — that slot is the prefix message alone, and
# `assert_single_system_message` holds it so at assembly.
COMPACTION_BLOCK_OPEN = '<nexus-block source="compaction">'
COMPACTION_BLOCK_CLOSE = "</nexus-block>"
SUMMARY_ROLE = "assistant"


def compaction_block(summary_text: str) -> str:
    """The runtime-delimited container. The stub text (or a summariser's)
    sits inside; the delimiters and the label are the runtime's."""
    return (f"{COMPACTION_BLOCK_OPEN}\n[CONVERSATION SUMMARY]\n{summary_text}\n"
            f"{COMPACTION_BLOCK_CLOSE}")


def assert_single_system_message(messages: list, stable_prefix: str) -> None:
    """Runtime companion to the static guard: the request carries exactly one
    system-role message, it is the first, and it starts with the prefix. A
    second system message — however it got there — is Law VII violated."""
    system_idx = [i for i, m in enumerate(messages) if m.get("role") == "system"]
    if system_idx != [0]:
        raise RuntimeError(
            f"request carries system-role messages at {system_idx}; exactly one, "
            f"at index 0, is permitted (BA fix 1 / Law VII)")
    assert_stable_prefix(messages[0].get("content", ""), stable_prefix)


async def _stub_summariser(messages: tuple) -> str:
    """Deterministic offline summariser — only used when no LLM summariser is wired."""
    n_user = sum(1 for m in messages if m.get("role") == "user")
    n_asst = sum(1 for m in messages if m.get("role") == "assistant")
    return f"[Auto-summary stub: {n_user} user / {n_asst} assistant turns compacted.]"


async def auto_compact(
    state: NexusLoopState,
    *,
    summariser: Summariser | None = None,
) -> tuple[NexusLoopState, int]:
    """Full LLM summarisation. Replaces history with [summary, last_user_msg].

    BA fix 1 — the summary is an `assistant`-role message inside a
    runtime-written delimiter, never a system message; see SUMMARY_ROLE."""
    if not state.messages:
        return next_state(state, compaction_state="compacted"), 0
    summariser = summariser or _stub_summariser
    summary_text = await summariser(state.messages)

    last_user = next(
        (i for i in range(len(state.messages) - 1, -1, -1)
         if state.messages[i].get("role") == "user"),
        None,
    )
    tail = state.messages[last_user:] if last_user is not None else ()
    summary_msg = {"role": SUMMARY_ROLE, "content": compaction_block(summary_text)}
    new_msgs = (summary_msg,) + tuple(tail)
    removed = len(state.messages) - len(new_msgs)
    return next_state(state, messages=new_msgs, compaction_state="compacted"), removed


# ---------- integrity guard (CLAUDE.md §"Compaction Integrity") ----------

@dataclass(frozen=True)
class IntegrityViolation:
    invariant: str            # short slug — e.g. "concept_completely_removed"
    severity: str             # "WARN" — the only severity since BD-3
    detail: str
    # FC-0 — the rule's own set, structural. `concept_completely_removed`
    # carries every removed id, sorted, and the count; `detail` above stays
    # a display string sliced to five names and is no longer the only
    # record. None on every other rule: those compute no set, and a `[]`
    # there would read as "this rule removed nothing", a false blank.
    removed: "tuple | None" = None
    removed_count: "int | None" = None

    def to_dict(self) -> dict:
        d = {"invariant": self.invariant, "severity": self.severity, "detail": self.detail}
        if self.removed is not None:
            d["removed"] = list(self.removed)
            d["removed_count"] = self.removed_count
        return d


REMOVED_INVARIANT = "concept_completely_removed"


@dataclass(frozen=True)
class CompactionReport:
    pass_fail: str            # "PASS" | "WARN" — nothing halts, no state is marked (BD-3)
    violations: tuple
    retention_rate: float     # triples kept / triples before
    minority_loss_rate: float # fraction of low-freq triples removed
    structural_summary: dict  # n_nodes, n_edges, before/after deltas

    def removed_concepts(self) -> tuple:
        """The set the removal rule computed, read from its violation — the
        ONE stored copy. Empty when the rule did not fire."""
        for v in self.violations:
            if v.invariant == REMOVED_INVARIANT and v.removed is not None:
                return tuple(v.removed)
        return ()

    def to_dict(self) -> dict:
        removed = self.removed_concepts()
        return {
            "pass_fail":            self.pass_fail,
            "violations":           [v.to_dict() for v in self.violations],
            "retention_rate":       round(self.retention_rate, 4),
            "minority_loss_rate":   round(self.minority_loss_rate, 4),
            "structural_summary":   self.structural_summary,
            # FC-0 — present on every report, PASS included: an empty set
            # that declares itself, never an omitted field. Derived from the
            # violation at render so there is one stored copy to diverge from.
            "removed":              list(removed),
            "removed_count":        len(removed),
        }


def snapshot_for_integrity(
    state: NexusLoopState,
    hippocampus: "Hippocampus | None",
) -> dict:
    """Capture the integrity-relevant slice of the cortex + state.

    BD-3 — node ids only. The snapshot used to carry each node's epistemic
    state and confidence for two rules that compared them across the
    boundary; both values are written once at creation and never again
    (LNS-1 item 2), so the rules compared constants and were retired.
    """
    nodes: set[str] = set(hippocampus.nodes) if hippocampus is not None else set()
    edges = []
    if hippocampus is not None:
        for e in hippocampus.edges():
            edges.append((e.source, e.target))
    triples = list(state.graph_context)
    # All concepts referenced by graph_context tuples
    concepts: set[str] = set()
    for t in triples:
        if len(t) >= 3:
            concepts.add(str(t[0])); concepts.add(str(t[2]))
    return {
        "nodes":      nodes,
        "edges":      edges,
        "triples":    triples,
        "concepts":   concepts,
    }


def compute_integrity_report(
    pre: dict,
    post: dict,
    *,
    tier: "Tier",
    prefix_pre: str,
    prefix_post: str,
) -> CompactionReport:
    """Diff two snapshots and produce a CompactionReport.

    Every rule reports at WARN; the report is PASS or WARN. BD-3 retired the
    FAIL tier: no rule reads a quantity tiers 3 and 4 touch (E.5), so a
    severe verdict had no reachable trigger and its consequence, the marked
    state, had no reader (B-24). The two rules over node epistemic state and node
    confidence were removed with it — both compared constants (LNS-1). The
    `tier` argument is kept on the signature for the callers and the frame;
    it no longer changes any severity. Live on its inputs at tier 2 (micro
    edits `graph_context`); at tiers 1, 3 and 4 nothing these rules read
    moves.
    """
    violations: list[IntegrityViolation] = []

    # 1. Stable prefix bit-identical. BA fix 3: `prefix_pre` is the constant
    # and `prefix_post` is the ASSEMBLED system message after the tier; the
    # predicate is the one `assert_stable_prefix` raises on. Before this the
    # cascade passed its own argument as both sides and the rule could not
    # fail (compaction.py:498-500, audit A3).
    if not prefix_intact(prefix_post, prefix_pre):
        violations.append(IntegrityViolation(
            invariant="prefix_drift", severity="WARN",
            detail="STABLE_SYSTEM_PREFIX changed during compaction (Law VII)",
        ))

    # 2. Concept completely removed
    removed_concepts = pre["concepts"] - post["concepts"]
    if removed_concepts:
        # FC-0 — the set is carried structurally; the detail string is a
        # display slice and no longer the only record of what was removed.
        violations.append(IntegrityViolation(
            invariant=REMOVED_INVARIANT,
            severity="WARN",
            detail=f"concepts vanished: {sorted(removed_concepts)[:5]}",
            removed=tuple(sorted(removed_concepts)),
            removed_count=len(removed_concepts),
        ))

    # 3. Edge loss without proportional node loss (collapse-tier sanity).
    # Cannot fire on its current input — collapse demotes edges and never
    # removes them, and `edges()` yields demoted edges — kept because it is
    # honest at WARN and costs nothing; a candidate for the next tidy.
    n_pre,  n_post  = len(pre["nodes"]),  len(post["nodes"])
    e_pre,  e_post  = len(pre["edges"]),  len(post["edges"])
    edge_loss = (e_pre - e_post) / e_pre if e_pre else 0.0
    node_loss = (n_pre - n_post) / n_pre if n_pre else 0.0
    if edge_loss > EDGE_LOSS_THRESHOLD and node_loss < edge_loss * 0.25:
        violations.append(IntegrityViolation(
            invariant="edge_loss_disproportionate",
            severity="WARN",
            detail=f"edge_loss={edge_loss:.2f} node_loss={node_loss:.2f}",
        ))

    # 4. Minority loss rate (graph_context triple cardinality)
    t_pre  = len(pre["triples"])
    t_post = len(post["triples"])
    minority_loss = (t_pre - t_post) / t_pre if t_pre else 0.0
    if minority_loss > MINORITY_LOSS_MAX:
        violations.append(IntegrityViolation(
            invariant="minority_loss_excess",
            severity="WARN",
            detail=f"{minority_loss:.2f} of triples removed",
        ))

    retention_rate = (t_post / t_pre) if t_pre else 1.0

    pass_fail = "WARN" if violations else "PASS"

    return CompactionReport(
        pass_fail=pass_fail,
        violations=tuple(violations),
        retention_rate=retention_rate,
        minority_loss_rate=minority_loss,
        structural_summary={
            "nodes_pre": n_pre, "nodes_post": n_post,
            "edges_pre": e_pre, "edges_post": e_post,
            "triples_pre": t_pre, "triples_post": t_post,
        },
    )


# ---------- CP: the keys each tier edits, for the emit ----------
# Pure readers over the objects the tiers already touch, called only on a
# tier that fired. They decide nothing: `run_cascade` passes their difference
# to the event it was already constructing.

def session_keys(state: NexusLoopState) -> tuple:
    """Tier 1's objects: `(source, target)` per session-memory row."""
    return tuple((str(t[0]), str(t[1])) for t in state.session_memory if len(t) >= 2)


def triple_keys(state: NexusLoopState) -> tuple:
    """Tier 2's objects: `(subject, relation, object)` per graph_context row."""
    return tuple((str(t[0]), str(t[1]), str(t[2])) for t in state.graph_context if len(t) >= 3)


def demoted_pairs(hippocampus: "Hippocampus | None") -> tuple:
    """Tier 3's objects: the demoted edges, by endpoint pair. `edges()` yields
    demoted edges (E.3 — they stay in the graph), which is what makes the
    before/after difference exactly what collapse demoted."""
    if hippocampus is None:
        return ()
    return tuple((e.source, e.target) for e in hippocampus.edges() if e.demoted)


def dropped(pre: tuple, post: tuple) -> tuple:
    """What `pre` held and `post` does not, in `pre`'s order."""
    kept = set(post)
    seen: set = set()
    out = []
    for k in pre:
        if k not in kept and k not in seen:
            seen.add(k)
            out.append(k)
    return tuple(out)


# ---------- cascade orchestrator ----------

@dataclass
class CompactionResult:
    state: NexusLoopState
    events: list[CompactionEvent] = field(default_factory=list)
    final_tokens: int = 0
    reports: list[CompactionReport] = field(default_factory=list)


async def run_cascade(
    state: NexusLoopState,
    hippocampus: "Hippocampus | None" = None,
    *,
    prefix: str = "",
    dynamic_block: str = "",
    summariser: Summariser | None = None,
    rebuild_block_fn: "Callable[[NexusLoopState, Hippocampus | None], str] | None" = None,
) -> CompactionResult:
    """Cheapest-first cascade. Returns the new state plus an ordered event list.

    Tier 3 always runs before Tier 4 (KEY PRINCIPLE, arch §8.2). No tier ever
    silently truncates — every reduction emits a CompactionEvent.

    `rebuild_block_fn` is called after every tier so the cascade can re-render
    the dynamic content with the post-tier state. Without it, T1/T2/T3 cannot
    free tokens visible to the cascade's estimator and the cascade always
    escalates to T4 even when earlier tiers would have sufficed.

    Phase 9: every tier produces a CompactionReport, PASS or WARN. BD-3:
    nothing halts and no state is marked; the report is emitted and the
    cascade runs every tier the thresholds ask for.
    """
    events: list[CompactionEvent] = []
    reports: list[CompactionReport] = []
    s = state
    block = dynamic_block

    def t() -> int:
        return estimate_tokens(s, prefix=prefix, dynamic_block=block)

    def rebuild() -> None:
        nonlocal block
        if rebuild_block_fn is not None:
            block = rebuild_block_fn(s, hippocampus)

    def integrity(pre, post, *, tier: Tier) -> CompactionReport:
        # BA fix 3 — the assembled system message (the caller's prefix plus
        # the post-tier block) against the CONSTANT. A caller that passed an
        # altered prefix, or a tier that wrote into the region, fails here.
        return compute_integrity_report(
            pre, post, tier=tier, prefix_pre=STABLE_SYSTEM_PREFIX,
            prefix_post=prefix + block,
        )

    if t() < TIER_1_SNIP_AT:
        return CompactionResult(state=s, events=events, final_tokens=t(),
                                reports=reports)

    # Tier 1 — snip
    pre = snapshot_for_integrity(s, hippocampus)
    before = t()
    sm_pre = session_keys(s)                                  # CP — emit only
    s, removed = snip(s)
    rebuild()
    post = snapshot_for_integrity(s, hippocampus)
    reports.append(integrity(pre, post, tier=Tier.SNIP))
    events.append(CompactionEvent(Tier.SNIP, before, t(), removed, "session_triples_below_top_n",
                                  removed_edges=dropped(sm_pre, session_keys(s))))

    if t() < TIER_2_MICRO_AT:
        return CompactionResult(state=s, events=events, final_tokens=t(),
                                reports=reports)

    # Tier 2 — microcompact
    pre = snapshot_for_integrity(s, hippocampus)
    before = t()
    gc_pre = triple_keys(s)                                   # CP — emit only
    s, removed = microcompact(s, hippocampus)
    rebuild()
    post = snapshot_for_integrity(s, hippocampus)
    t2_report = integrity(pre, post, tier=Tier.MICRO)
    reports.append(t2_report)
    events.append(CompactionEvent(Tier.MICRO, before, t(), removed, "stale_triples_cleared",
                                  removed_triples=dropped(gc_pre, triple_keys(s)),
                                  # FC-0's set, read from the report that computed
                                  # it — one stored copy, not a second derivation.
                                  removed_concepts=t2_report.removed_concepts()))

    if t() < TIER_3_COLLAPSE_AT:
        return CompactionResult(state=s, events=events, final_tokens=t(),
                                reports=reports)

    # Tier 3 — graph collapse (always runs before Tier 4)
    pre = snapshot_for_integrity(s, hippocampus)
    before = t()
    dem_pre = demoted_pairs(hippocampus)                      # CP — emit only
    collapsed = graph_collapse(hippocampus)
    rebuild()
    post = snapshot_for_integrity(s, hippocampus)
    t3_report = integrity(pre, post, tier=Tier.COLLAPSE)
    reports.append(t3_report)
    events.append(CompactionEvent(Tier.COLLAPSE, before, t(), collapsed, "linear_paths_collapsed",
                                  demoted_edges=dropped(demoted_pairs(hippocampus), dem_pre)))

    if t() < TIER_4_AUTO_AT:
        return CompactionResult(state=s, events=events, final_tokens=t(),
                                reports=reports)

    # Tier 4 — auto compact (LLM summarisation)
    pre = snapshot_for_integrity(s, hippocampus)
    before = t()
    s, removed = await auto_compact(s, summariser=summariser)
    rebuild()
    post = snapshot_for_integrity(s, hippocampus)
    t4_report = integrity(pre, post, tier=Tier.AUTO)
    # v3.1 Phase 1 — tier 4 drops messages; which work units those turns
    # belonged to is a cheap report fact. Carried in `structural_summary`
    # (additive, no frame shape change) and the state's list is cleared so
    # the next fire reports only what it summarised.
    summarised = sorted({u for u in s.history_work_units if u})
    t4_report = _dc_replace(t4_report, structural_summary={
        **t4_report.structural_summary, "work_unit_ids_summarised": summarised})
    s = next_state(s, history_work_units=())
    reports.append(t4_report)
    events.append(CompactionEvent(Tier.AUTO, before, t(), removed, "llm_summarisation"))

    return CompactionResult(state=s, events=events, final_tokens=t(),
                            reports=reports)


# ---------- Law VII guard: stable prefix invariant ----------
# `assert_stable_prefix` lives in backend/loop/prefix.py (BA fix 3) and is
# re-exported above; the cascade's integrity rule and the runtime check at
# prompt assembly share `prefix_intact`, so there is one comparison.
