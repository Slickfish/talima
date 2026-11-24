"""BB Part 2 — placement by replay, not by sampling.

    python scripts/history_shape_replay.py [--provider stub|real] [--conversations 20]
        [--record answers.json | --answers answers.json] [--print 1] [--out history_replay.json]

B1 asks where a planted item in a prior answer lands in the successor's
request. Placement depends only on the prior answer's text and the shape's
wrapping — not on which model wrote the answer — so it is enumerated, not
sampled:

  1. RECORD the turn-1 answer of each fixture conversation once, under the
     control shape (`--record` writes them; `--answers` reads them back; with
     neither, the stub's canned answers are recorded in memory);
  2. PLANT every corpus item into a copy of each recorded answer, plus two
     truncation plants — the same item at the HEAD and at the TAIL of an
     answer padded past HISTORY_MAX_CHARS;
  3. REPLAY turn 2 through the real assembly (`run_turn`, the production
     loop, on a copy of the fixture store) under each shape with a stub
     provider, and read the request the provider was sent.

Every item × every shape, and the region is printed verbatim: the reader
sees the text, not a label.

  IN_INSTRUCTION_REGION   the item is in the system message — the worst case;
  in_labelled_block       inside `<nexus-block source="prior_answer" …>` — A;
  bare_in_history         in an unlabelled assistant message — control, and
                          the Part A finding;
  absent                  nowhere in the request — B, and A's truncated tail.

THE WRAPPER'S COST is arithmetic, printed per wrapped turn: utf-8 bytes of
the wrapped message minus the original —
    len(open tag) + 2 newlines + len(close tag) − bytes cut by truncation —
so "does it grow with longer answers" has an answer without a model: it does
not; past the bound it shrinks by one byte per character cut. Shape B's cost
is the bytes of every message it drops. The turn-2 request length of a
harness run is NOT this figure (different answers, different lengths).

LIMIT. The stub's extraction returns nothing, so the path by which a prior
answer's CLAIMS reach the next system message — extraction → gate → store →
KB lines, shape B's designed channel — is not exercised here. That channel
is the gate's, and `tests/test_ba_fix5_imperative_shape.py` runs this corpus
through it in every evidence class. This replay measures the history
channel only.

The shapes are applied here, never in the loop (`shape_messages`, from the
harness). Nothing changes production. (CHANGE A, 18 October, then shipped A
to the loop; this replay drives the loop under `NEXUS_HISTORY_WRAP=off`,
the pre-A path it was pre-registered against.)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402
import history_shape_harness as hsh                              # noqa: E402
from history_shape_harness import (HISTORY_MAX_CHARS, PRIOR_CLOSE, PRIOR_OPEN,   # noqa: E402
                                   SHAPES, production_wrap_off, shape_messages)

sys.path.insert(0, str(ROOT / "tests"))
from _corpus import CORPUS, CORPUS_SHA256                        # noqa: E402

# PRE-REGISTERED
IN_INSTRUCTION_REGION = "IN_INSTRUCTION_REGION"
IN_LABELLED_BLOCK = "in_labelled_block"
BARE_IN_HISTORY = "bare_in_history"
ABSENT = "absent"

TRUNCATION_HEAD = "truncation_head"
TRUNCATION_TAIL = "truncation_tail"
TRUNCATION_ITEM = "authority"       # the item planted at the head and the tail
# Padding from the fixture domain, longer than the bound on its own, so a
# recorded answer of any length overruns HISTORY_MAX_CHARS once it is added.
LONG_FILLER = (" ".join(
    "Adaptation to interval training accumulates over weeks of consistent sessions, "
    "with recovery between them doing as much of the work as the intervals themselves."
    for _ in range(5)))
assert len(LONG_FILLER) > HISTORY_MAX_CHARS

REPLAY_ITEMS = {**CORPUS, TRUNCATION_HEAD: CORPUS[TRUNCATION_ITEM], TRUNCATION_TAIL: CORPUS[TRUNCATION_ITEM]}


def plant(answer: str, name: str) -> str:
    """The recorded answer with one item planted. Corpus items are appended,
    the two truncation plants pad the answer past the bound."""
    text = REPLAY_ITEMS[name]
    if name == TRUNCATION_HEAD:
        return text + " " + answer + " " + LONG_FILLER
    if name == TRUNCATION_TAIL:
        return answer + " " + LONG_FILLER + " " + text
    return answer + " " + text


def wrapper_cost_bytes(original: str, *, turn: int) -> int:
    """Shape A's cost for one wrapped turn, as arithmetic: the tags and two
    newlines, minus whatever truncation cut."""
    truncated = max(0, len(original) - HISTORY_MAX_CHARS)
    cut = len(original[HISTORY_MAX_CHARS:].encode("utf-8"))
    return (len(PRIOR_OPEN.format(turn=turn, truncated=truncated).encode("utf-8"))
            + 2 + len(PRIOR_CLOSE.encode("utf-8")) - cut)


def truncated_in(content: str) -> int:
    """K from a `truncated=K>` delimiter, 0 when the message carries none."""
    if not content.startswith('<nexus-block source="prior_answer"'):
        return 0
    head = content.split(">", 1)[0]
    return int(head.rsplit("truncated=", 1)[1])


def locate(request: list, item: str) -> dict:
    """Where `item` sits in a request as sent. The worst case wins."""
    for i, m in enumerate(request):
        if m.get("role") == "system" and item in m.get("content", ""):
            return {"region": IN_INSTRUCTION_REGION, "message_index": i, "role": "system",
                    "excerpt": _around(m["content"], item)}
    for i, m in enumerate(request):
        if m.get("role") == "system":
            continue
        c = m.get("content", "")
        at = c.find(item)
        if at < 0:
            continue
        labelled = (c.startswith('<nexus-block source="prior_answer"')
                    and PRIOR_CLOSE in c and at < c.rfind(PRIOR_CLOSE))
        return {"region": IN_LABELLED_BLOCK if labelled else BARE_IN_HISTORY,
                "message_index": i, "role": m.get("role"), "excerpt": c}
    # Absent: show the assistant message (if any) so a truncation delimiter is visible.
    prior = next((m for m in request if m.get("role") == "assistant"), None)
    return {"region": ABSENT, "message_index": None, "role": None,
            "excerpt": prior["content"] if prior else ""}


def _around(text: str, item: str, width: int = 240) -> str:
    at = text.find(item)
    return text[max(0, at - width): at + len(item) + width]


class _Stub(hsh._Stub):
    """The harness's stub, planting nothing: the plant is already in the
    seeded history."""

    def __init__(self):
        super().__init__(plant=None)


def _turn(kb: Path, state, question: str, provider, tmp: Path):
    """One turn of the production loop on `kb`. Returns (next state, frames)."""
    from backend.kb.retriever import TripleRetriever
    from backend.kb.tiered_store import TripleStore
    from backend.loop.agentic_loop import run_turn
    from backend.memory.hippocampus import Hippocampus
    store = TripleStore(kb)
    frames = []
    out = state

    async def drive():
        nonlocal out
        async for f, st in run_turn(out, question, retriever=TripleRetriever(store),
                                    hippocampus=Hippocampus(), provider=provider):
            frames.append(f)
            out = st
    try:
        with production_wrap_off():             # the pre-A loop this replay was pre-registered against (CHANGE A)
            asyncio.run(drive())
    finally:
        store.close()
    return out, frames


def _frame(frames, name):
    return next((json.loads(f.split("data: ", 1)[1].strip())
                 for f in frames if f.startswith(f"event: {name}\n")), {})


def record(provider_kind: str, conversations: list, tmp: Path) -> list[dict]:
    """Turn 1 of each conversation under control, recorded once."""
    from backend.loop.state import NexusLoopState
    tmp.mkdir(parents=True, exist_ok=True)
    kb_src = tmp / "record_fixture.db"
    hsh._build_store(kb_src).close()
    out = []
    for ci, questions in enumerate(conversations):
        kb = tmp / f"record_{ci}.db"
        shutil.copy(kb_src, kb)
        if provider_kind == "stub":
            prov = _Stub()
        else:
            from backend.providers.factory import get_provider
            prov = hsh._Recording(get_provider())
        state, frames = _turn(kb, NexusLoopState(), questions[0], prov, tmp)
        out.append({"conversation": ci, "questions": list(questions),
                    "answer": state.messages[-1]["content"] if state.messages else "",
                    "provider": provider_kind, "model": _frame(frames, "model_routed").get("model")})
    return out


def replay(kb_src: Path, recorded: list[dict], tmp: Path) -> list[dict]:
    """Every recorded answer × every item × every shape: seed the history,
    apply the shape, run turn 2 through the loop, read the request."""
    from backend.loop.agentic_loop import STABLE_SYSTEM_PREFIX
    from backend.loop.state import NexusLoopState
    tmp.mkdir(parents=True, exist_ok=True)
    rows = []
    for rec in recorded:
        q1, q2 = rec["questions"][0], rec["questions"][1]
        for name, text in REPLAY_ITEMS.items():
            planted = plant(rec["answer"], name)
            history = ({"role": "user", "content": q1}, {"role": "assistant", "content": planted})
            for shape in SHAPES:
                shaped = shape_messages(history, shape)
                kb = tmp / f"replay_{rec['conversation']}_{name}_{shape}.db"
                shutil.copy(kb_src, kb)
                prov = _Stub()
                _turn(kb, NexusLoopState(messages=shaped, turn_count=1), q2, prov, tmp)
                kb.unlink(missing_ok=True)
                request = prov.requests[0]
                loc = locate(request, text)
                dropped = sum(len(m["content"].encode("utf-8")) for m in history) \
                    - sum(len(m["content"].encode("utf-8")) for m in shaped)
                rows.append({
                    "conversation": rec["conversation"], "item": name, "shape": shape,
                    **loc,
                    "truncated": max(truncated_in(m["content"]) for m in request),
                    "planted_chars": len(planted),
                    "cost_bytes": (wrapper_cost_bytes(planted, turn=1) if shape == "A"
                                   else -dropped if shape == "B" else 0),
                    "request_shape": [(m["role"], len(m["content"])) for m in request],
                    "system_starts_with_prefix": request[0]["content"].startswith(STABLE_SYSTEM_PREFIX),
                    "request": request,
                    "corpus_sha256": CORPUS_SHA256,
                })
    return rows


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=("stub", "real"), default="stub",
                    help="who answers turn 1 when recording (replay is always the stub)")
    ap.add_argument("--conversations", type=int, default=20)
    ap.add_argument("--record", default=None, help="write the turn-1 answers here and continue")
    ap.add_argument("--answers", default=None, help="replay these recorded answers instead of recording")
    ap.add_argument("--print", dest="n_print", type=int, default=1,
                    help="conversations whose regions are printed verbatim")
    ap.add_argument("--out", default="history_replay.json")
    args = ap.parse_args()

    import os
    tmp = Path(tempfile.mkdtemp(prefix="nexus_replay_"))
    os.environ["NEXUS_CALIBRATION_LOG"] = str(tmp / "calibration.jsonl")
    t0 = time.monotonic()
    if args.answers:
        recorded = json.loads(Path(args.answers).read_text(encoding="utf-8"))["answers"]
        source = f"recorded: {args.answers}"
    else:
        recorded = record(args.provider, hsh.CONVERSATIONS[:args.conversations], tmp / "rec")
        source = f"{args.provider} turn-1 answers, recorded now"
        if args.record:
            Path(args.record).write_text(json.dumps({"provider": args.provider, "answers": recorded},
                                                    indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"recorded {len(recorded)} turn-1 answers ({source}) in {time.monotonic() - t0:.1f}s", flush=True)

    kb_src = tmp / "fixture.db"
    hsh._build_store(kb_src).close()
    md5 = hsh._md5(kb_src)
    rows = replay(kb_src, recorded, tmp)
    assert hsh._md5(kb_src) == md5, "the fixture store changed"
    print(f"replayed {len(rows)} turn-2 requests ({len(recorded)} answers × {len(REPLAY_ITEMS)} items "
          f"× {len(SHAPES)} shapes) in {time.monotonic() - t0:.1f}s\n", flush=True)

    # The matrix: item × shape → region, with any disagreement across answers named.
    matrix, disagreements = {}, []
    for name in REPLAY_ITEMS:
        for shape in SHAPES:
            regions = sorted({r["region"] for r in rows if r["item"] == name and r["shape"] == shape})
            matrix[(name, shape)] = regions[0] if len(regions) == 1 else "MIXED:" + "/".join(regions)
            if len(regions) > 1:
                disagreements.append((name, shape, regions))
    print(f"B1 placement, every item × every shape, over {len(recorded)} recorded answers "
          f"(corpus {CORPUS_SHA256[:12]}…):")
    print(f"{'item':18s} {'control':>18s} {'A':>18s} {'B':>18s}")
    for name in REPLAY_ITEMS:
        print(f"{name:18s} " + " ".join(f"{matrix[(name, s)]:>18s}" for s in SHAPES))
    print("disagreement across answers: " + (str(disagreements) if disagreements else "none — placement "
          "is a function of the wrapping alone"))

    # The wrapper's cost, per recorded answer, as arithmetic.
    print("\nwrapper cost per wrapped turn (utf-8 bytes of the message as sent minus the answer as produced):")
    print(f"  formula A: len(open tag) + 2 + len(close tag) − bytes cut = "
          f"{len(PRIOR_OPEN.format(turn=1, truncated=0).encode('utf-8'))} + 2 + "
          f"{len(PRIOR_CLOSE.encode('utf-8'))} − cut  (turn=1, untruncated; +1 per extra digit of turn or K)")
    print(f"  {'conv':>4s} {'answer chars':>12s} {'A bytes':>8s} {'A truncated K':>14s} {'B bytes':>9s}  "
          f"(B drops every prior message; control 0)")
    for rec in recorded:
        a = rec["answer"]
        k = max(0, len(a) - HISTORY_MAX_CHARS)
        b = -(len(rec["questions"][0].encode("utf-8")) + len(a.encode("utf-8")))
        print(f"  {rec['conversation']:4d} {len(a):12d} {wrapper_cost_bytes(a, turn=1):+8d} {k:14d} {b:+9d}")

    # The text, verbatim, for the first --print conversations.
    for rec in recorded[:args.n_print]:
        print(f"\n=== conversation {rec['conversation']} — Q1: {rec['questions'][0]!r}  "
              f"Q2: {rec['questions'][1]!r}  (turn-1 answer {len(rec['answer'])} chars) ===")
        for name in REPLAY_ITEMS:
            print(f"\n--- item {name}: {REPLAY_ITEMS[name]!r} ---")
            for shape in SHAPES:
                r = next(x for x in rows if x["conversation"] == rec["conversation"]
                         and x["item"] == name and x["shape"] == shape)
                where = (f"message {r['message_index']} ({r['role']})" if r["message_index"] is not None
                         else "no message")
                print(f"[{shape}] region={r['region']}  {where}  request={r['request_shape']}  "
                      f"truncated={r['truncated']}  cost={r['cost_bytes']:+d} bytes")
                if r["excerpt"]:
                    for line in r["excerpt"].splitlines():
                        print(f"    | {line}")
                else:
                    print("    | (no assistant message in the request)")

    report = {"answers_source": source, "provider_for_recording": args.provider,
              "NOT_A_MEASUREMENT": False,
              "note": ("placement is a function of the prior answer's text and the wrapping; the "
                       "stub's answers are as valid for it as a model's. What the stub cannot show "
                       "is the extraction channel (see LIMIT in the docstring)."),
              "pre_registered": {"history_max_chars": HISTORY_MAX_CHARS, "shapes": SHAPES,
                                 "items": list(REPLAY_ITEMS), "corpus_sha256": CORPUS_SHA256,
                                 "prior_open": PRIOR_OPEN, "prior_close": PRIOR_CLOSE},
              "matrix": {f"{n}|{s}": v for (n, s), v in matrix.items()},
              "disagreements": disagreements,
              "answers": recorded, "rows": rows}
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {args.out}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
