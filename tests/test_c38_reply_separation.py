"""C-38 — the reply separated at the provider boundary.

S1 answers are printed (the consumer set by AST, the streaming path); S3's
tests, each companion planting what its guard does not name:

T1   three fixture replies give the same structured reply: a separate
     thinking field, inline tags, and both at once
T2   the leak, end to end: a raw output holding a claim-shaped sentence in a
     <think> span plus a stated answer — no zebrafish triple admitted, not
     cited, absent from the next turn's history and from the console stream;
     reasoning_hash set. Companion: the same turn under the rollback admits it
T3   the empty answer: a reply that is only an unclosed <think> — declared,
     nothing extracted, nothing cited, the console message present, no fallback
T4   the extraction window declared: a 5,000-character answer reads truncated
     with the character count; the window's size is unchanged
T5   the single reader: the AST guard over the tracked backend, and the planted
     read in extraction it names
T6   hash continuity: the pre-C-38 fixture record recomputes unchanged (the LS
     literal), a record with reasoning recomputes with both hashes, and editing
     reasoning_hash on a sealed record is named
T7   per-model keying: a model with no tag pair leaves content untouched, even
     with the literal text <think> in prose; a tag split across tokens is held
     back and never shown as answer text
S1.2 every consumer of the model's output text in run_turn, derived by AST,
     equals the answered-for set (a consumer the plan does not cover fails here)
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

from backend.cortex.extraction import EXTRACTION_MAX_CHARS
from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.loop.agentic_loop import ANSWER_EMPTY_CONSOLE_MESSAGE, run_turn
from backend.loop.state import NexusLoopState
from backend.memory.chain import NexusArchiveChain
from backend.memory.lineage import LineageStore, _hash_payload_from_row, verify_sealed_lineage
from backend.providers import base as PB
from backend.providers.lmstudio import lmstudio_delta_pieces
from backend.providers.model_map import think_option, think_tags
from backend.providers.ollama import ollama_chunk_pieces
from tests._tracked import tracked_backend

ROOT = Path(__file__).resolve().parent.parent
THINK_CLAIM = "Zebrafish regenerate heart tissue."
ANSWER = "Interval training improves vo2 max and reduces resting heart rate."
THINKING_MODEL = "deepseek-r1:14b-qwen-distill-q4_K_M"
PLAIN_MODEL = "phi4-mini:3.8b-q4_K_M"


# ---------------------------------------------------------------------------
# providers for the fixtures
# ---------------------------------------------------------------------------

class _Pieces:
    """A provider that separates natively (Ollama's shape): yields (source, text)
    pieces; `stream_chat` yields the content pieces only, as the pre-C-38 path did."""

    def __init__(self, pieces):
        self.pieces = pieces

    async def stream_pieces(self, model, messages, num_ctx):
        for p in self.pieces:
            yield p

    async def stream_chat(self, model, messages, num_ctx):
        for source, text in self.pieces:
            if source == "content":
                yield text


class _Chat:
    """A provider with `stream_chat` only (LM Studio's shape, every stub): the
    answer in the given token pieces, then the extraction arm."""

    def __init__(self, tokens, extract=None):
        self.tokens, self.extract, self.calls, self.extraction_calls = tokens, extract, 0, 0

    async def stream_chat(self, model, messages, num_ctx):
        self.calls += 1
        if self.calls % 2 == 1:
            for t in self.tokens:
                yield t
        else:
            self.extraction_calls += 1
            yield self.extract(messages[-1]["content"]) if self.extract else "[]"


def _collect(provider, model=THINKING_MODEL):
    rs = PB.reply_stream(provider, model, [{"role": "user", "content": "q"}], num_ctx=4096)

    async def go():
        return [c async for c in rs]
    chunks = asyncio.run(go())
    answer = "".join(c.text for c in chunks if c.kind == PB.ANSWER)
    reasoning = "".join(c.text for c in chunks if c.kind == PB.REASONING) or None
    return PB.Reply(answer=answer, reasoning=reasoning, raw=rs.raw, unclosed=rs.unclosed), chunks


def _extract_zebrafish(prompt: str) -> str:
    if THINK_CLAIM in prompt:
        return json.dumps([{"subject": "zebrafish", "relation": "regenerate", "object": "heart tissue", "confidence": 0.9}])
    return "[]"


def _turn(store, lineage, provider, state=None, question="what helps vo2 max?"):
    frames, out = [], state or NexusLoopState(active_model=THINKING_MODEL)

    async def drive():
        nonlocal out
        async for f, s in run_turn(out, question, retriever=TripleRetriever(store), provider=provider, lineage=lineage):
            frames.append(f)
            out = s
    asyncio.run(drive())
    done = next(json.loads(f.split("data: ", 1)[1].strip()) for f in reversed(frames) if f.startswith("event: done\n"))
    tokens = [json.loads(f.split("data: ", 1)[1].strip())["t"] for f in frames if f.startswith("event: token\n")]
    return out, done, tokens


def _plant(tmp_path) -> TripleStore:
    store = TripleStore(tmp_path / "kb.db")
    store.add(subject="interval training", relation="improves", object="vo2 max", weight=0.9, tier=1,
              granularity=Granularity.TOPIC, source="wikidata")
    return store


# ---------------------------------------------------------------------------
# T1 — three fixture replies, one structured reply
# ---------------------------------------------------------------------------

def test_t1_three_fixture_replies_give_the_same_structured_reply():
    separate = _Pieces([("thinking", "Let me "), ("thinking", "recall."), ("content", ANSWER[:20]), ("content", ANSWER[20:])])
    inline = _Chat(["<thi", "nk>Let me recall.</th", "ink>\n\n" + ANSWER[:20], ANSWER[20:]])
    both = _Pieces([("thinking", "Let me "), ("content", "<think>recall.</think>\n" + ANSWER)])
    r1, _ = _collect(separate)
    r2, chunks2 = _collect(inline)
    r3, _ = _collect(both)
    print(f"\nT1 ({sys.platform}) separate field: {r1}\n   inline tags: {r2}\n   both: {r3}")
    for r in (r1, r2, r3):
        assert r.answer == ANSWER and r.reasoning == "Let me recall." and not r.answer_empty and not r.unclosed
    assert r1.raw == ANSWER, "a separate field leaves the content stream as the answer"
    assert r2.raw == "<think>Let me recall.</think>\n\n" + ANSWER, "raw is the content stream as delivered"
    # In the stream: no answer chunk ever carried a tag or reasoning text.
    assert all("<" not in c.text and "recall" not in c.text for c in chunks2 if c.kind == PB.ANSWER)
    # The daemon's shape, parsed without a daemon: thinking before content, per chunk; a thinking
    # chunk carries content "" and the final chunk may carry both.
    assert ollama_chunk_pieces({"message": {"thinking": "t", "content": ""}}, separate=True) == [("thinking", "t")]
    assert ollama_chunk_pieces({"message": {"thinking": "t", "content": "c"}, "done": True}, separate=True) == [("thinking", "t"), ("content", "c")]
    assert ollama_chunk_pieces({"message": {"thinking": "t", "content": "c"}}, separate=False) == [("content", "c")], \
        "the pre-C-38 path drops the field"
    assert lmstudio_delta_pieces({"reasoning_content": "t", "content": "c"}, separate=True) == [("thinking", "t"), ("content", "c")]
    assert lmstudio_delta_pieces({"reasoning": "t"}, separate=True) == [("thinking", "t")]
    assert lmstudio_delta_pieces({"reasoning_content": "t", "content": "c"}, separate=False) == [("content", "c")]
    # No model is asked for separation (THINK_OPTION is empty, for the reason recorded beside it);
    # the field is read whenever the daemon sends it, and the tag pair is keyed per model.
    assert not think_option(THINKING_MODEL) and not think_option(PLAIN_MODEL)


# ---------------------------------------------------------------------------
# T2 — the leak, end to end, and its companion
# ---------------------------------------------------------------------------

def test_t2_a_claim_inside_the_reasoning_reaches_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    monkeypatch.delenv("NEXUS_REPLY_SEPARATION", raising=False)
    store, lineage = _plant(tmp_path), LineageStore(tmp_path / "kb.db")
    prov = _Chat([f"<think>{THINK_CLAIM}</think>\n", ANSWER], extract=_extract_zebrafish)
    state, done, tokens = _turn(store, lineage, prov)
    rec = lineage.get(done["lineage_id"])
    admitted = [(t.subject, t.relation, t.object) for t in store.search_terms(["zebrafish"])]
    print(f"\nT2 ({sys.platform}) tokens streamed: {tokens!r}\n   admitted zebrafish rows: {admitted}; done.extraction_gate={done['extraction_gate']}"
          f"\n   record: answer_chars={rec.answer_chars} reasoning_chars={rec.reasoning_chars} reasoning_hash={rec.reasoning_hash[:12]}…")
    assert admitted == [], "the reasoning's claim was admitted"
    assert not any(THINK_CLAIM in t or "<think>" in t for t in tokens), "the console saw the reasoning"
    assert "".join(tokens) == ANSWER
    assert [m["content"] for m in state.messages if m["role"] == "assistant"][-1].endswith(ANSWER + "\n</nexus-block>")
    assert THINK_CLAIM not in json.dumps(list(state.messages)), "the reasoning reached history"
    assert done["reinforcement"]["cited"] >= 1 and all(i["cited"] for i in rec.injected if i["belief_id"] is not None)
    assert rec.reasoning_hash == hashlib.sha256(THINK_CLAIM.encode()).hexdigest() and rec.reasoning_chars == len(THINK_CLAIM)
    assert rec.answer_hash == hashlib.sha256(ANSWER.encode()).hexdigest() and rec.answer_chars == len(ANSWER)
    assert rec.response_hash == hashlib.sha256((f"<think>{THINK_CLAIM}</think>\n" + ANSWER).encode()).hexdigest(), \
        "response_hash stays the hash of the content stream as delivered"
    assert done["answer_chars"] == len(ANSWER) and done["reasoning_chars"] == len(THINK_CLAIM) and done["answer_empty"] is False
    assert lineage.verify_record(rec.lineage_id)["record_intact"]
    store.close(); lineage.close()


def test_t2c_under_the_rollback_the_same_turn_admits_the_reasonings_claim(tmp_path, monkeypatch):
    """The companion proves the test can see the leak: NEXUS_REPLY_SEPARATION=off
    is the pre-C-38 path, and the zebrafish triple is admitted through the gate."""
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    monkeypatch.setenv("NEXUS_REPLY_SEPARATION", "off")
    store, lineage = _plant(tmp_path), LineageStore(tmp_path / "kb.db")
    prov = _Chat([f"<think>{THINK_CLAIM}</think>\n", ANSWER], extract=_extract_zebrafish)
    state, done, tokens = _turn(store, lineage, prov)
    admitted = [(t.subject, t.relation, t.object) for t in store.search_terms(["zebrafish"])]
    rec = lineage.get(done["lineage_id"])
    print(f"\nT2c ({sys.platform}) rollback: admitted {admitted}; tokens carried the tag: {any('<think>' in t for t in tokens)}")
    assert admitted == [("zebrafish", "regenerate", "heart tissue")]
    assert any("<think>" in t for t in tokens) and THINK_CLAIM in json.dumps(list(state.messages))
    assert rec.reasoning_hash is None and rec.reasoning_chars is None and done["reasoning_chars"] is None
    assert rec.answer_chars == len(f"<think>{THINK_CLAIM}</think>\n" + ANSWER)
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# T3 — the empty answer is a declared state
# ---------------------------------------------------------------------------

def test_t3_an_unclosed_reasoning_is_declared_and_nothing_is_extracted_from_it(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    monkeypatch.delenv("NEXUS_REPLY_SEPARATION", raising=False)
    store, lineage = _plant(tmp_path), LineageStore(tmp_path / "kb.db")
    prov = _Chat(["<think>", f"{THINK_CLAIM} Interval training improves vo2 max and"], extract=_extract_zebrafish)
    state, done, tokens = _turn(store, lineage, prov)
    rec = lineage.get(done["lineage_id"])
    print(f"\nT3 ({sys.platform}) done: answer_empty={done['answer_empty']} reason={done.get('answer_empty_reason')!r}; "
          f"tokens={tokens!r}; extraction calls={prov.extraction_calls}; gate={done['extraction_gate']}")
    assert done["answer_empty"] is True and done["answer_empty_reason"] == "the reply was cut off inside its thinking"
    assert tokens == [ANSWER_EMPTY_CONSOLE_MESSAGE], "the console is told, and shown no reasoning"
    assert prov.extraction_calls == 0, "extraction received nothing — no fallback to the reasoning"
    assert done["extraction_gate"]["extracted"] == 0 and done["extraction_gate"]["extraction_failed"] is False
    assert done["extraction_input_chars"] == 0 and done["reinforcement"]["cited"] == 0
    assert store.search_terms(["zebrafish"]) == []
    assert rec.answer_chars == 0 and rec.answer_empty_reason == "the reply was cut off inside its thinking"
    assert rec.reasoning_chars == len(f"{THINK_CLAIM} Interval training improves vo2 max and")
    assert [m["content"] for m in state.messages if m["role"] == "assistant"][-1].endswith("\n\n</nexus-block>"), "history carries the empty answer, not the message"
    assert ANSWER_EMPTY_CONSOLE_MESSAGE not in json.dumps(list(state.messages))
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# T4 — the extraction window, declared
# ---------------------------------------------------------------------------

def test_t4_a_long_answer_declares_the_window_without_changing_it(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    store, lineage = _plant(tmp_path), LineageStore(tmp_path / "kb.db")
    seen = {}

    def extract(prompt):
        seen["prompt"] = prompt
        return "[]"
    long = ("Interval training improves vo2 max. " * 140)[:4990] + " ZZ-TAIL-MARKER"
    assert len(long) == 5005
    _state, done, _tokens = _turn(store, lineage, _Chat([long], extract=extract))
    print(f"\nT4 ({sys.platform}) answer {len(long)} chars -> extraction_input_chars={done['extraction_input_chars']} "
          f"truncated={done['extraction_input_truncated']}")
    assert done["extraction_input_truncated"] is True and done["extraction_input_chars"] == EXTRACTION_MAX_CHARS == 4000
    assert long[:EXTRACTION_MAX_CHARS] in seen["prompt"] and "ZZ-TAIL-MARKER" not in seen["prompt"], "the window is 4,000 characters, head first"
    assert done["answer_chars"] == 5005
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# T5 — exactly one reader of the reasoning
# ---------------------------------------------------------------------------

def reasoning_readers(sources: dict[str, str]) -> set[tuple[str, str]]:
    """(file, function) for every function in `sources` that reads the
    reasoning: a Name or keyword `reasoning_text`, or an attribute `.reasoning`
    on anything, by AST. The provider boundary that PRODUCES it is excluded by
    path, so the set is the consumers."""
    out = set()
    for path, src in sources.items():
        if path.startswith("backend/providers/"):
            continue
        tree = ast.parse(src)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(fn):
                if (isinstance(n, ast.Attribute) and n.attr == "reasoning") or \
                   (isinstance(n, ast.Name) and n.id == "reasoning_text") or \
                   (isinstance(n, ast.keyword) and n.arg == "reasoning_text"):
                    out.add((path, fn.name))
                    break
    return out


ANSWERED_REASONING_READERS = {
    ("backend/loop/agentic_loop.py", "run_turn"),              # the carrier: splits the stream into the reply (MCP-2: the console half)
    ("backend/loop/agentic_loop.py", "complete_turn"),         # MCP-2: the second half hands the reply to the record and the capture
    ("backend/memory/lineage.py", "record_turn"),              # THE reader: hashes and counts it; stores no text
    ("backend/eval/citation_capture.py", "capture_turn"),      # the record's labelling artefact, off by default; stores it apart
}


def _sources() -> dict[str, str]:
    return {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in tracked_backend()}


def test_t5_the_record_is_the_one_reader_of_the_reasoning():
    got = reasoning_readers(_sources())
    print(f"\nT5 ({sys.platform}) readers of the reasoning, derived: {sorted(got)}")
    assert got == ANSWERED_REASONING_READERS, got ^ ANSWERED_REASONING_READERS


def test_t5c_a_planted_read_in_extraction_is_named():
    planted = {"backend/cortex/extraction.py": _sources()["backend/cortex/extraction.py"]
               + "\n\ndef extract_from_reasoning(reply):\n    return extract_triples(reply.reasoning, None)\n"}
    got = reasoning_readers({**_sources(), **planted})
    assert ("backend/cortex/extraction.py", "extract_from_reasoning") in got and got != ANSWERED_REASONING_READERS


# ---------------------------------------------------------------------------
# S1.2 — every consumer of the model's output text, derived by AST
# ---------------------------------------------------------------------------

def output_text_consumers(src: "str | None" = None) -> set[str]:
    """Every call in run_turn whose arguments read `answer_text`, `structured`,
    `assistant_msg` or `assistant_chunks` — the names the reply flows through —
    plus the SSE events built from them. Derived, so a new consumer fails the pin.
    W1 — `src` lets the companion below plant a consumer (law 9: the guard
    had none)."""
    if src is None:
        src = (ROOT / "backend" / "loop" / "agentic_loop.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # MCP-2 split the loop: the reply flows through `run_turn` (the stream
    # and the Reply) and `complete_turn` (every consumer after the call); the
    # union is the consumer set, derived over both.
    fns = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name in ("run_turn", "complete_turn")]
    assert len(fns) == 2, "run_turn and complete_turn must both exist"
    out = set()
    for fn in fns:
        names = _reply_names(fn)
        for call in (c for c in ast.walk(fn) if isinstance(c, ast.Call)):
            reads = {n.id for a in list(call.args) + [k.value for k in call.keywords] for n in ast.walk(a)
                     if isinstance(n, ast.Name) and n.id in names}
            if reads:
                out.add(ast.unparse(call.func))
    return out


REPLY_NAMES = {"answer_text", "structured", "assistant_msg", "assistant_chunks", "reasoning_text", "reasoning_chunks"}


def _carries(value, names) -> bool:
    """Whether an expression hands on a reply name's text unchanged or merely
    re-shaped: the name itself, an attribute or subscript of it, an f-string
    or a `+` holding it, or a str method or str()/repr() of it. A call into
    anything else is a consumer, recorded as one, not an alias."""
    if isinstance(value, ast.Name):
        return value.id in names
    if isinstance(value, (ast.Attribute, ast.Subscript)):
        return _carries(value.value, names)
    if isinstance(value, ast.JoinedStr):
        return any(_carries(v.value, names) for v in value.values if isinstance(v, ast.FormattedValue))
    if isinstance(value, ast.BinOp):
        return _carries(value.left, names) or _carries(value.right, names)
    if isinstance(value, ast.Call):
        f = value.func
        if isinstance(f, ast.Attribute) and f.attr in STR_METHODS:
            return _carries(f.value, names)
        if isinstance(f, ast.Name) and f.id in ("str", "repr") and value.args:
            return _carries(value.args[0], names)
    return False


STR_METHODS = {"strip", "lstrip", "rstrip", "lower", "upper", "casefold", "replace", "split", "splitlines",
               "join", "format", "removeprefix", "removesuffix", "encode", "title"}


def _reply_names(fn) -> set[str]:
    """REPLY_NAMES and every local alias of them in `fn` (W1's verifier,
    lens 3 F6: `text = structured.answer; consume(text)` passed). Grown to a
    fixpoint over plain, annotated and augmented assignments and walrus
    targets. The history of EARLIER turns (`handle.state.history`) is the
    prompt's, not this reply's, and is outside this derivation."""
    names = set(REPLY_NAMES)
    while True:
        grown = set(names)
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and _carries(n.value, names):
                grown |= {t.id for t in n.targets if isinstance(t, ast.Name)}
            elif isinstance(n, (ast.AnnAssign, ast.NamedExpr)) and n.value is not None and _carries(n.value, names) \
                    and isinstance(n.target, ast.Name):
                grown.add(n.target.id)
            elif isinstance(n, ast.AugAssign) and _carries(n.value, names) and isinstance(n.target, ast.Name):
                grown.add(n.target.id)
        if grown == names:
            return names
        names = grown


ANSWERED_CONSUMERS = {
    "Reply",                      # the structured reply itself
    # MCP-2 — `sse_event` left the set: the done frame is built as a dict
    # (`_done_payload`) before the call so the MCP record tool can read it,
    # and the token frames read the chunk, not the names. The second half
    # entered it: `complete_turn` receives the reply, by construction.
    "complete_turn",              # the second half of the split: every consumer below lives in it
    "extract_triples",            # extraction: answer only, through the window
    "cited_ids",                  # the citation detector: answer only
    "lineage.record_turn",        # the record: raw for response_hash, answer and reasoning hashed apart
    "capture_turn",               # the capture: answer and reasoning stored apart when on
    "evaluator.evaluate",         # the evaluator: answer only
    "extract_terms",              # prediction's fallback terms: answer only
    "carrier_entry",              # A's history carrier: the answer joins the history
    "len", "min",                 # the lengths and the window declaration
    "''.join",                    # the join of the chunks
    "tuple",                      # the history tuple the answer joins (stage 7)
    "set",                        # prediction's fallback term set
}


def test_s1_2_the_consumer_set_is_the_answered_for_set():
    got = output_text_consumers()
    print(f"\nS1.2 ({sys.platform}) consumers of the model's output text in run_turn, derived: {sorted(got)}")
    assert got == ANSWERED_CONSUMERS, got ^ ANSWERED_CONSUMERS


def test_s1_2c_a_planted_consumer_of_the_answer_is_named():
    """W1 — the companion S1.2 shipped without: a new reader of the answer in
    complete_turn, under a name the answered set does not hold, must surface
    in the derived set (and so fail the pin above)."""
    src = (ROOT / "backend" / "loop" / "agentic_loop.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    complete = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "complete_turn")
    complete.body.insert(0, ast.parse("summarise_for_operator(structured.answer)").body[0])
    got = output_text_consumers(ast.unparse(tree))
    assert "summarise_for_operator" in got and got != ANSWERED_CONSUMERS


@pytest.mark.parametrize("planted", [
    "text = structured.answer\nsummarise_for_operator(text)",
    "note = f'{structured.answer}'\nsummarise_for_operator(note)",
    "head = structured.answer.strip()[:80]\nlabel = 'answer: ' + head\nsummarise_for_operator(label)",
])
def test_s1_2c_an_aliased_consumer_of_the_answer_is_named(planted):
    """W1's verifier (lens 3, F6): the first companion planted a name the
    derivation compares. These plant the answer under a local alias first,
    which the first derivation did not follow, and the pin must fail."""
    src = (ROOT / "backend" / "loop" / "agentic_loop.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    complete = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "complete_turn")
    complete.body[0:0] = ast.parse(planted).body
    got = output_text_consumers(ast.unparse(tree))
    assert "summarise_for_operator" in got and got != ANSWERED_CONSUMERS, got ^ ANSWERED_CONSUMERS


# ---------------------------------------------------------------------------
# T6 — hash continuity and the tamper companion
# ---------------------------------------------------------------------------

def test_t6_pre_c38_records_recompute_unchanged_and_a_record_with_reasoning_seals_both_hashes(tmp_path):
    from tests.test_ls_learning_state import FIXED_ID, FIXED_TS, FIXTURE_TURN, PRE_LS_HASH, _seal
    from unittest import mock
    lineage = LineageStore(tmp_path / "kb.db")
    with mock.patch("backend.memory.lineage.time.time", return_value=FIXED_TS), \
            mock.patch("backend.memory.lineage.uuid.uuid4", return_value=FIXED_ID):
        rec = lineage.record_turn(**FIXTURE_TURN)
    assert rec.record_hash == PRE_LS_HASH, "a record written without the reply fields hashes as the pre-C-38 tree wrote it"
    assert rec.answer_hash is None and rec.reasoning_hash is None and rec.answer_chars is None
    with_reasoning = lineage.record_turn(**FIXTURE_TURN, answer_text="stated", reasoning_text="private")
    row = lineage.conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (with_reasoning.lineage_id,)).fetchone()
    payload = _hash_payload_from_row(row)
    assert payload["answer_hash"] == hashlib.sha256(b"stated").hexdigest() and payload["reasoning_hash"] == hashlib.sha256(b"private").hexdigest()
    assert LineageStore.compute_record_hash(payload) == with_reasoning.record_hash
    assert LineageStore.compute_record_hash({**payload, "reasoning_hash": None}) != with_reasoning.record_hash, "the reasoning hash is in the seal"
    assert LineageStore.compute_record_hash({**payload, "answer_hash": None}) != with_reasoning.record_hash, "the answer hash is in the seal"
    # No reasoning text in the record.
    assert "private" not in json.dumps(dict(row))
    # The tamper companion: editing reasoning_hash on a sealed record is named.
    chain = NexusArchiveChain(tmp_path / "archive.db")
    _seal(lineage, chain)
    assert verify_sealed_lineage(lineage, chain)["verdict"] == "intact"
    lineage.conn.execute("UPDATE lineage_records SET reasoning_hash = ? WHERE lineage_id = ?",
                         ("0" * 64, with_reasoning.lineage_id))
    lineage.conn.commit()
    v = verify_sealed_lineage(lineage, chain)
    print(f"\nT6 ({sys.platform}) reasoning_hash tampered on a sealed record -> verdict {v['verdict']}; "
          f"record_intact={lineage.verify_record(with_reasoning.lineage_id)['record_intact']}")
    assert v["verdict"] != "intact" and lineage.verify_record(with_reasoning.lineage_id)["record_intact"] is False
    lineage.close(); chain.close()


# ---------------------------------------------------------------------------
# T7 — per-model keying and the held-back tag
# ---------------------------------------------------------------------------

def test_t7_a_model_with_no_tag_pair_leaves_content_untouched_and_a_split_tag_is_held_back():
    prose = "As I said, the literal text <think> is how some models mark reasoning. The answer is 4."
    assert think_tags(PLAIN_MODEL) is None and think_tags(THINKING_MODEL) == ("<think>", "</think>")
    r, chunks = _collect(_Chat([prose[:30], prose[30:]]), model=PLAIN_MODEL)
    assert r.answer == prose and r.reasoning is None and r.raw == prose and [c.kind for c in chunks] == [PB.ANSWER, PB.ANSWER]
    # The same text under the thinking model's pair is a span, parsed.
    r2, _ = _collect(_Chat([prose[:30], prose[30:]]), model=THINKING_MODEL)
    assert r2.reasoning == " is how some models mark reasoning. The answer is 4." and r2.unclosed and r2.answer_empty is False
    # A tag split across tokens is never shown as answer text: the separator holds back a partial prefix.
    sep = PB.ReplySeparator(("<think>", "</think>"))
    out = sep.feed("Yes. <th")
    assert out == [PB.ReplyChunk(PB.ANSWER, "Yes. ")], out
    out = sep.feed("ink>hidden</thi")
    assert out == [PB.ReplyChunk(PB.REASONING, "hidden")], out
    out = sep.feed("nk> No.")
    assert out == [PB.ReplyChunk(PB.ANSWER, "No.")], out
    assert sep.flush() == [] and not sep.in_reasoning
    # A lone '<' that never becomes a tag is released as answer text at the end.
    sep = PB.ReplySeparator(("<think>", "</think>"))
    assert sep.feed("a < b") == [PB.ReplyChunk(PB.ANSWER, "a < b")]
    sep2 = PB.ReplySeparator(("<think>", "</think>"))
    assert sep2.feed("a <") == [PB.ReplyChunk(PB.ANSWER, "a ")] and sep2.flush() == [PB.ReplyChunk(PB.ANSWER, "<")]
