"""Phase 6.A — RAGAS-equivalent per-turn evaluator.

Three metrics, all locally computable so tests don't need an external LLM:

  - answer_relevancy : Jaccard overlap between query terms and response terms.
                      READ WHAT THAT MEANS before quoting it (BB Part 1): the
                      query has three to six terms and the response up to 64,
                      so the union is the response's vocabulary and the score
                      is (query terms the answer echoes) / (distinct terms in
                      the answer). It measures brevity, not relevance: on the
                      same question, a one-line correct answer scored 0.200, a
                      thorough correct essay 0.031, and an answer that merely
                      repeats the question's words 1.000. `max_terms=64` also
                      means nothing past an answer's 64th distinct term is
                      read. This is not cheaply repairable into relevancy — a
                      coverage ratio (|Q∩R|/|Q|) would drop the length
                      denominator but scores the question echoed back at 1.0,
                      and RAGAS's actual answer_relevancy needs a model to
                      generate questions from the answer and embed them. The
                      function is kept so the per-turn series continues; it
                      appears in no shape report.
  - faithfulness    : fraction of injected triples whose subject AND object
                      surface in the response. NOT COMPUTED (None) when
                      nothing was injected — see NOT_COMPUTED below.
  - context_precision: fraction of injected triples actually fired in the
                      cortex propagation map (i.e. not just keyword-matched
                      noise). NOT COMPUTED (None) when nothing was injected.

NOT_COMPUTED (BB Part 1). Both fractions have the injected count as their
denominator. When it is zero they used to return 1.0 — "nothing to be
unfaithful to" — and every turn that retrieved nothing scored perfect: an
empty denominator reported as a score, the aborted-cycle defect
(`KairosCycleResult` built from defaults, `edges_promoted: 0`) in the tool
the history-shape harness used to judge shapes, where a shape that injects
less scored higher for it. The value is None — on the dataclass, in the JSONL
record, on the `eval_metric` frame and on `/eval/recent` as JSON null — and
every rendering surface prints "not computed" through `render_metric`.
`composite()` is None whenever a component is, because a mean over the
metrics that happened to compute is a different number wearing the same
name. The n=0 case is distinguishable on the record: `n_injected_triples`.

If `deepeval` is installed the evaluator can wrap the local metric in a
deepeval BaseMetric for compatibility with their CLI tooling. Either way,
every turn appends one JSONL record to the configured log path so the
"DeepEval RAGAS score logged per turn" invariant holds.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from backend.kb.retriever import RetrievedTriple, extract_terms

try:                                # optional — makes this metric usable from
    from deepeval.metrics import BaseMetric  # the deepeval CLI / suite
    DEEPEVAL_AVAILABLE = True
except ImportError:
    BaseMetric = object             # type: ignore[misc, assignment]
    DEEPEVAL_AVAILABLE = False


NOT_COMPUTED = None
NOT_COMPUTED_TEXT = "not computed"


def render_metric(value: float | None, digits: int = 3) -> str:
    """The one rendering of a metric: a number, or the words "not computed".
    Never a numeral for an absent value."""
    return NOT_COMPUTED_TEXT if value is None else f"{value:.{digits}f}"


@dataclass(frozen=True)
class TurnMetrics:
    turn: int
    answer_relevancy: float
    faithfulness: float | None          # None = nothing injected, not computed
    context_precision: float | None     # None = nothing injected, not computed
    n_injected_triples: int
    timestamp: float = field(default_factory=time.time)

    def composite(self) -> float | None:
        parts = (self.answer_relevancy, self.faithfulness, self.context_precision)
        if any(p is None for p in parts):
            return NOT_COMPUTED
        return sum(parts) / 3.0


# ---------- local RAGAS-equivalent computations ----------

def _tokens(text: str) -> set[str]:
    return set(extract_terms(text or "", max_terms=64))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def answer_relevancy_score(query: str, response: str) -> float:
    """|Q ∩ R| / |Q ∪ R| over extracted terms — see the module docstring for
    what that is (the answer's brevity), and why it is not repaired here."""
    return _jaccard(_tokens(query), _tokens(response))


def faithfulness_score(response: str, hits: list[RetrievedTriple]) -> float | None:
    if not hits:
        return NOT_COMPUTED     # the denominator is the injected count; empty is not 1.0
    resp_tokens = _tokens(response)
    surfaced = 0
    for rt in hits:
        s_terms = _tokens(rt.triple.subject)
        o_terms = _tokens(rt.triple.object)
        if s_terms & resp_tokens and o_terms & resp_tokens:
            surfaced += 1
    return surfaced / len(hits)


def context_precision_score(
    hits: list[RetrievedTriple], fired_node_ids: set[str]
) -> float | None:
    if not hits:
        return NOT_COMPUTED     # same denominator, same rule
    relevant = sum(
        1 for rt in hits
        if rt.triple.subject in fired_node_ids or rt.triple.object in fired_node_ids
    )
    return relevant / len(hits)


# ---------- evaluator ----------

class TurnEvaluator:
    """Computes + logs metrics per turn. Records held in memory and (optionally)
    appended to a JSONL file for offline review."""

    def __init__(self, log_path: Path | str | None = None):
        self.records: list[TurnMetrics] = []
        self.log_path: Path | None = Path(log_path) if log_path else None
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def evaluate(
        self,
        *,
        turn: int,
        query: str,
        response: str,
        hits: list[RetrievedTriple] | None = None,
        fired_node_ids: set[str] | None = None,
    ) -> TurnMetrics:
        hits = hits or []
        fired = fired_node_ids or set()
        m = TurnMetrics(
            turn=turn,
            answer_relevancy=answer_relevancy_score(query, response),
            faithfulness=faithfulness_score(response, hits),
            context_precision=context_precision_score(hits, fired),
            n_injected_triples=len(hits),
        )
        self.records.append(m)
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(asdict(m)) + "\n")
        return m

    def latest(self) -> TurnMetrics | None:
        return self.records[-1] if self.records else None


# ---------- optional deepeval wrapper ----------

class _LocalRagasMetric(BaseMetric):
    """deepeval-compatible facade. Plug into deepeval.evaluate() if installed."""
    threshold: float = 0.5
    name: str = "nexus_local_ragas"

    def __init__(self, evaluator: TurnEvaluator, *, hits=(), fired=frozenset()):
        self.evaluator = evaluator
        self.hits = list(hits)
        self.fired = set(fired)
        self.score = 0.0
        self.success = False
        self.reason = ""

    def measure(self, test_case) -> float:                     # pragma: no cover
        m = self.evaluator.evaluate(
            turn=len(self.evaluator.records),
            query=getattr(test_case, "input", ""),
            response=getattr(test_case, "actual_output", ""),
            hits=self.hits, fired_node_ids=self.fired,
        )
        self.score = m.composite()
        self.success = self.score is not None and self.score >= self.threshold
        self.reason = (
            f"answer_relevancy={render_metric(m.answer_relevancy, 2)} "
            f"faithfulness={render_metric(m.faithfulness, 2)} "
            f"context_precision={render_metric(m.context_precision, 2)}"
        )
        return self.score

    async def a_measure(self, test_case) -> float:             # pragma: no cover
        return self.measure(test_case)

    def is_successful(self) -> bool:                           # pragma: no cover
        return self.success
