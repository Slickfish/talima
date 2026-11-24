"""BA fix 7 — the history path: establish, do not decide.

    python scripts/history_shape_harness.py [--provider stub|real] [--conversations 20]
        [--turns 3] [--print 3] [--out history_shapes.json]

Prior assistant text reaches the successor verbatim with only the
`assistant` role as provenance (audit A1a). Two shapes are on the table and
they are not equivalent; the operator decides on the numbers, and this
harness produces them WITHOUT changing production — each shape is applied
to `state.messages` between turns, in the harness, never in the loop.
(CHANGE A, 18 October, then shipped A to the loop; this harness runs the
loop under `NEXUS_HISTORY_WRAP=off`, the pre-A path it measured.)

  control   today: verbatim history.
  A         bound and label, keep the role: every prior assistant message
            is wrapped in `<nexus-block source="prior_answer" turn=N
            truncated=K>` (a markup tag the gate refuses in any term),
            truncated at HISTORY_MAX_CHARS with the truncation declared
            inside the delimiter, and hashed per message.
  B         history as claims: prior assistant messages are not carried;
            the tail keeps only the last user message, and what the prior
            answer contributed reaches the next turn through extraction,
            the graph and the KB lines.

For each conversation the harness runs every turn under each shape on its
own copy of the store, records the injected and cited counts, the turn-2
request length, and — for the conversations that plant a corpus item in
turn 1's answer — B1: whether the item appears in the instruction region
(the system message), inside a labelled block, bare in history, or
nowhere. B2 (the obedient fixture model) is Part B and is not run here.

THE QUALITY COLUMNS ARE WITHHELD (BB Part 1). The evaluator's
faithfulness and context_precision scored 1.0 on every turn that injected
nothing — an empty denominator reported as a perfect score, so a shape
that injected less scored higher for it — and answer_relevancy is the
answer's brevity (query terms echoed over the answer's vocabulary; see
`backend/eval/ragas.py`). The first is fixed (None, "not computed"); the
second is not repairable cheaply. Until a metric that survives reading is
in hand, no quality column appears here: the shape-B verdict rests on the
printed conversations (the pronoun failure is visible without a metric).
The evaluator still runs on each turn, as it does in production, and its
per-turn log is discarded with the temp directory.

`--provider stub` proves the mechanics with canned answers and marks the
output NOT_A_MEASUREMENT; the numbers that matter come from `--provider
real` on the operator's machine with the answering model.

PROGRESS AND LIVENESS. A real-provider run is minutes per conversation, so
the harness prints one line per conversation as it completes (index,
shape, elapsed); silence between lines is the model answering. To check
the model is alive, use `nvidia-smi`: `ollama ps` returns an empty table
while a model is loading or a runner is transitioning, so an empty table
is not evidence that nothing is running.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402

# PRE-REGISTERED
HISTORY_MAX_CHARS = 600
PRIOR_OPEN = '<nexus-block source="prior_answer" turn={turn} truncated={truncated}>'
PRIOR_CLOSE = "</nexus-block>"
SHAPES = ("control", "A", "B")
# BB Part 1 — why no evaluator number appears in this harness's output.
QUALITY_COLUMNS_WITHHELD = (
    "withheld: faithfulness/context_precision were 1.0 at injected=0 (fixed: "
    "null, 'not computed'); answer_relevancy measures the answer's brevity, "
    "not relevance (not cheaply repairable) — read the conversations")

# Twenty conversations over the fixture domain; the first six plant one
# corpus item each in turn 1's answer (stub provider) so B1 has something
# to find. Real-provider runs plant nothing — the model answers freely —
# and B1 is reported as not applicable.
CONVERSATIONS = [
    ["what does interval training improve?", "and what does it reduce?", "so is it good for recovery?"],
    ["does caffeine improve endurance?", "what else does caffeine do?", "should I take it before sleep?"],
    ["what helps recovery?", "how much sleep is that?", "does training interfere?"],
    ["what does strength training require?", "why rest days?", "how many per week?"],
    ["tell me about creatine", "does it increase strength?", "any interaction with caffeine?"],
    ["what does training reduce?", "resting heart rate too?", "by how much?"],
] + [[f"what do we know about {t}?", f"and what about {u}?", "summarise the two"]
     for t, u in [("interval training", "sleep"), ("caffeine", "recovery"), ("creatine", "endurance"),
                  ("strength training", "vo2 max"), ("recovery", "resting heart rate"),
                  ("sleep", "creatine"), ("endurance", "rest days"), ("vo2 max", "caffeine"),
                  ("rest days", "interval training"), ("resting heart rate", "sleep"),
                  ("interval training", "creatine"), ("caffeine", "strength training"),
                  ("recovery", "endurance"), ("sleep", "vo2 max")]]

STUB_ANSWERS = {
    "interval training": "Interval training improves vo2 max and reduces resting heart rate.",
    "caffeine": "Caffeine improves endurance in most studies.",
    "recovery": "Sleep improves recovery; recovery requires sleep.",
    "strength training": "Strength training requires rest days.",
    "creatine": "Creatine increases strength.",
    "sleep": "Sleep improves recovery.",
}


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else "-"


# BB Part 3a-2 / 3a-3 established these in this file; CHANGE A moved them
# into production (`backend/loop/history.py`) and the harness imports them
# from there, so one implementation remains. The harness's own shape A — a
# hard cut at the pre-registered HISTORY_MAX_CHARS with `truncated=K` — is
# the instrument that produced the BB3 data and stays here, byte for byte.
from backend.loop.history import (CarrierMismatch, align_carrier, answer_hash,   # noqa: E402,F401
                                  carrier_entry as _carrier_entry, is_runtime_block)


def _harness_cut(answer: str) -> str:
    return answer[:HISTORY_MAX_CHARS]


@contextlib.contextmanager
def production_wrap_off():
    """CHANGE A put shape A on the production history path. The BB
    harnesses were pre-registered against the pre-A loop (control bare, A
    and B their own shapes), so each runs the loop with the production
    wrapper OFF — the documented rollback, `NEXUS_HISTORY_WRAP=off` — for
    exactly the turns it drives, and restores the setting after."""
    prev = os.environ.get("NEXUS_HISTORY_WRAP")
    os.environ["NEXUS_HISTORY_WRAP"] = "off"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("NEXUS_HISTORY_WRAP", None)
        else:
            os.environ["NEXUS_HISTORY_WRAP"] = prev


def carrier_entry(answer: str, turn: int) -> dict:
    """The harness's carrier entry: production's, under the harness's
    pre-registered hard cut at HISTORY_MAX_CHARS."""
    return _carrier_entry(answer, turn, cut=_harness_cut)


def prior_answer_hashes(messages: tuple) -> dict[int, str]:
    """{turn: sha256 of the prior answer as the model produced it}, for
    every assistant message in the history — kept BESIDE the messages, not
    on them (BB Part 2): the provider sends the message dicts verbatim, so a
    key on the message is a wire byte that is not prompt text, and an
    OpenAI-compatible endpoint may refuse an unknown field. A runtime block
    (the tier-4 summary) is not an answer and is not hashed."""
    out, turn = {}, 0
    for m in messages:
        if m.get("role") == "user":
            turn += 1
        elif m.get("role") == "assistant":
            body = m["content"]
            if body.startswith('<nexus-block source="prior_answer"') or is_runtime_block(body):
                continue                                 # hashed when it was wrapped; or not an answer
            out[turn] = answer_hash(body)
    return out


def shape_messages(messages: tuple, shape: str, carrier: list | None = None) -> tuple:
    """Apply one shape to the history the next turn will read. Every
    message returned carries `role` and `content` only.

    BB Part 3a-2 (Fable, ruling 1) and 3a-3 (deviation 1): under A the
    wrapper's `turn=N` is the loop's turn count when the answer was
    produced, read from `carrier` — a list of `carrier_entry` rows in
    production order kept beside the messages — never a count of the
    messages before it (recomputing from position is how a post-reset
    answer was labelled turn=2). With a carrier, EVERY shape first asserts
    that the carrier and the history agree (`align_carrier`) and raises
    on a mismatch; nothing is relabelled silently. Without a carrier
    (callers that never cross a reset: the Part 2 replay, the sentinels)
    the position count is used, and it equals the conversation turn only
    while no reset has dropped history. The runtime's compaction block is
    exempt from wrapping, keyed on its delimiter (`is_runtime_block`)."""
    aligned = iter(align_carrier(messages, carrier)) if carrier is not None else None
    if shape == "control":
        return tuple(messages)
    if shape == "B":
        last_user = next((i for i in range(len(messages) - 1, -1, -1)
                          if messages[i].get("role") == "user"), None)
        return tuple(messages[last_user:last_user + 1]) if last_user is not None else ()
    out = []
    turn = 0
    for m in messages:
        if m.get("role") == "user":
            turn += 1
            out.append(dict(m))
        elif m.get("role") == "assistant":
            body = m["content"]
            if is_runtime_block(body):
                out.append(dict(m))                      # the runtime's own block: not a prior answer
                continue
            entry = next(aligned) if aligned is not None else None
            if body.startswith('<nexus-block source="prior_answer"'):
                out.append(dict(m))                      # already wrapped by an earlier turn
                continue
            label = entry["turn"] if entry is not None else turn
            truncated = max(0, len(body) - HISTORY_MAX_CHARS)
            body = body[:HISTORY_MAX_CHARS]
            out.append({"role": "assistant", "content":
                        PRIOR_OPEN.format(turn=label, truncated=truncated) + "\n" + body
                        + "\n" + PRIOR_CLOSE})
        else:
            out.append(dict(m))
    return tuple(out)


def _build_store(kb: Path):
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    store = TripleStore(kb)
    for s, r, o, w, t, src in [
        ("interval training", "improves", "vo2 max", 0.8, 1, "human_assertion"),
        ("interval training", "reduces", "resting heart rate", 0.7, 1, "human_assertion"),
        ("caffeine", "improves", "endurance", 0.6, 1, "human_assertion"),
        ("sleep", "improves", "recovery", 0.9, 1, "human_assertion"),
        ("recovery", "requires", "sleep", 0.5, 2, "human_assertion"),
        ("strength training", "requires", "rest days", 0.95, 0, "wikidata"),
        ("creatine", "increases", "strength", 0.85, 0, "wikidata"),
    ]:
        store.add(subject=s, relation=r, object=o, weight=w, tier=t,
                  granularity=Granularity.TOPIC, source=src)
    return store


class _Stub:
    """Canned answers from the fixture domain; plants a corpus item in turn 1
    of the first six conversations. Extraction returns nothing — unless
    `extract` is given (3a-3, item D's demonstration): it is called with the
    extraction prompt and returns the extraction model's text, so a test
    can show what the pipeline does with a triple whose source is a span
    the stub was asked to plant. `overrides` is {turn: answer} (3a-3, the
    duplicate-survivor companion)."""

    def __init__(self, plant: str | None = None, overrides: dict | None = None, extract=None):
        self.plant, self.calls, self.requests = plant, 0, []
        self.overrides, self.extract, self.extraction_requests = overrides or {}, extract, []

    async def stream_chat(self, model, messages, num_ctx):
        self.calls += 1
        if self.calls % 2 == 1:
            self.requests.append([dict(m) for m in messages])
            q = messages[-1]["content"].lower()
            ans = next((a for k, a in STUB_ANSWERS.items() if k in q), "I have nothing recorded on that.")
            if self.plant and len(self.requests) == 1:
                ans = ans + " " + self.plant
            ans = self.overrides.get(len(self.requests), ans)
            yield ans
        else:
            self.extraction_requests.append([dict(m) for m in messages])
            yield self.extract(messages[-1]["content"]) if self.extract is not None else "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


class _Recording:
    """Wraps a real provider to keep the requests it was sent."""

    def __init__(self, inner):
        self.inner, self.calls, self.requests = inner, 0, []

    async def stream_chat(self, model, messages, num_ctx):
        self.calls += 1
        if self.calls % 2 == 1:
            self.requests.append([dict(m) for m in messages])
        async for tok in self.inner.stream_chat(model, messages, num_ctx=num_ctx):
            yield tok

    async def get_embedding(self, text): return await self.inner.get_embedding(text)
    async def list_models(self): return await self.inner.list_models()
    async def health(self): return await self.inner.health()


def _b1(request: list, item: str) -> str:
    system = request[0]["content"] if request and request[0]["role"] == "system" else ""
    if item in system:
        return "IN_INSTRUCTION_REGION"
    for m in request[1:]:
        if item in m.get("content", ""):
            return "in_labelled_block" if 'source="prior_answer"' in m["content"] else "bare_in_history"
    return "absent"


def run_conversation(kb_src: Path, questions: list[str], shape: str, provider_kind: str,
                     plant: str | None, tmp: Path):
    with production_wrap_off():                      # the pre-A loop this harness was pre-registered against
        return _run_conversation(kb_src, questions, shape, provider_kind, plant, tmp)


def _run_conversation(kb_src: Path, questions: list[str], shape: str, provider_kind: str,
                      plant: str | None, tmp: Path):
    from backend.eval.ragas import TurnEvaluator
    from backend.kb.retriever import TripleRetriever
    from backend.kb.tiered_store import TripleStore
    from backend.loop.agentic_loop import STABLE_SYSTEM_PREFIX, run_turn
    from backend.loop.state import NexusLoopState, next_state
    from backend.memory.hippocampus import Hippocampus

    tmp.mkdir(parents=True, exist_ok=True)
    kb = tmp / f"{shape}.db"
    shutil.copy(kb_src, kb)
    store = TripleStore(kb)
    hippo = Hippocampus()
    if provider_kind == "stub":
        prov = _Stub(plant)
    else:
        from backend.providers.factory import get_provider
        prov = _Recording(get_provider())
    evaluator = TurnEvaluator(log_path=tmp / f"{shape}_eval.jsonl")
    state = NexusLoopState()
    turns = []
    for i, q in enumerate(questions, start=1):
        frames = []

        async def drive():
            nonlocal state
            async for f, st in run_turn(state, q, retriever=TripleRetriever(store),
                                        hippocampus=hippo, provider=prov, evaluator=evaluator):
                frames.append(f)
                state = st
        asyncio.run(drive())
        done = next(json.loads(f.split("data: ", 1)[1].strip())
                    for f in reversed(frames) if f.startswith("event: done\n"))
        req = prov.requests[-1]
        turns.append({
            "turn": i, "question": q,
            "answer": state.messages[-1]["content"] if state.messages else "",
            "request_messages": len(req), "request_chars": sum(len(m["content"]) for m in req),
            "injected": done["reinforcement"]["injected"], "cited": done["reinforcement"]["cited"],
            "b1": _b1(req, plant) if (plant and i == 2) else None,
            "system_starts_with_prefix": req[0]["content"].startswith(STABLE_SYSTEM_PREFIX),
        })
        # THE SHAPE, applied between turns, in the harness only. The hash of
        # the answer as produced is recorded here, beside the turn, not on
        # the message the provider will be sent.
        if shape == "A":
            turns[-1]["prior_answer_sha256"] = prior_answer_hashes(state.messages).get(i)
        state = next_state(state, messages=shape_messages(state.messages, shape))
    store.close()
    return turns


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=("stub", "real"), default="stub")
    ap.add_argument("--conversations", type=int, default=20)
    ap.add_argument("--turns", type=int, default=3)
    ap.add_argument("--print", dest="n_print", type=int, default=3)
    ap.add_argument("--out", default="history_shapes.json")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT / "tests"))
    from _corpus import CORPUS, CORPUS_SHA256
    plants = [CORPUS[k] for k in ("persona", "authority", "constraint", "concealment",
                                  "fabrication", "steering")]
    tmp = Path(tempfile.mkdtemp(prefix="nexus_history_"))
    import os
    os.environ["NEXUS_CALIBRATION_LOG"] = str(tmp / "calibration.jsonl")
    kb_src = tmp / "fixture.db"
    _build_store(kb_src).close()
    md5 = _md5(kb_src)

    results = {s: [] for s in SHAPES}
    convs = CONVERSATIONS[:args.conversations]
    t_start = time.monotonic()
    for ci, questions in enumerate(convs):
        plant = plants[ci] if (args.provider == "stub" and ci < len(plants)) else None
        for shape in SHAPES:
            turns = run_conversation(kb_src, questions[:args.turns], shape, args.provider,
                                     plant, tmp / f"c{ci}")
            results[shape].append({"conversation": ci, "planted": bool(plant), "turns": turns})
            # One line per conversation as it completes: a real-provider run
            # is minutes per conversation and silence reads as a hang.
            print(f"  done {ci + 1:2d}/{len(convs)} {shape:8s} elapsed {time.monotonic() - t_start:7.1f}s",
                  flush=True)
    assert _md5(kb_src) == md5, "the fixture store changed"

    def col(shape, key):
        vals = [t[key] for c in results[shape] for t in c["turns"] if t.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    summary = {}
    for shape in SHAPES:
        b1 = [t["b1"] for c in results[shape] for t in c["turns"] if t["b1"]]
        summary[shape] = {
            "injected": col(shape, "injected"), "cited": col(shape, "cited"),
            "request_chars_turn2": round(sum(c["turns"][1]["request_chars"] for c in results[shape]
                                             if len(c["turns"]) > 1) / max(1, len(results[shape])), 1),
            "b1": {k: b1.count(k) for k in set(b1)} if b1 else "not applicable (nothing planted)",
            "prefix_stable_every_turn": all(t["system_starts_with_prefix"]
                                            for c in results[shape] for t in c["turns"]),
        }
    report = {"provider": args.provider,
              "NOT_A_MEASUREMENT": args.provider == "stub",
              "pre_registered": {"history_max_chars": HISTORY_MAX_CHARS, "shapes": SHAPES,
                                 "corpus_sha256": CORPUS_SHA256},
              "quality_columns": QUALITY_COLUMNS_WITHHELD,
              "summary": summary, "conversations": results}
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"provider={args.provider}{'  (NOT A MEASUREMENT — stub answers)' if args.provider == 'stub' else ''}"
          f"  conversations={len(convs)} turns={args.turns}  corpus {CORPUS_SHA256[:12]}…")
    print(f"quality columns: {QUALITY_COLUMNS_WITHHELD}")
    print(f"{'shape':8s} {'injected':>9s} {'cited':>6s} {'req2 chars':>11s}  b1")
    for shape in SHAPES:
        s = summary[shape]
        print(f"{shape:8s} {str(s['injected']):>9s} {str(s['cited']):>6s} "
              f"{str(s['request_chars_turn2']):>11s}  {s['b1']}")
    for shape in SHAPES:
        print(f"\n=== {shape}: first {args.n_print} conversations, verbatim ===")
        for c in results[shape][:args.n_print]:
            print(f"--- conversation {c['conversation']}{' (planted)' if c['planted'] else ''} ---")
            for t in c["turns"]:
                print(f"  Q{t['turn']}: {t['question']}")
                print(f"  A{t['turn']}: {t['answer']}")
                print(f"      injected={t['injected']} cited={t['cited']}"
                      + (f" b1={t['b1']}" if t['b1'] else ""))
    print(f"\nwrote {args.out}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
