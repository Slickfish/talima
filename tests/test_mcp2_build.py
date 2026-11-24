"""MCP-2 — NEXUS as an MCP server, the build. The S3 pins.

THE PIN THAT MATTERS: the two-call turn equals the one-call turn. On CE's
rig, `prepare_turn` + a stub model + `complete_turn` gives the same block
bytes and the same record (every field MCP-1 marked unchanged) as
`run_turn`; and the console path, which now calls both, is pinned byte for
byte against the base — the per-turn request, context and answer hashes
measured at fd75e52 before the split, as literals.

Every companion plants what its guard does not name. PLATFORM is stamped
on every printed line (sys.platform).
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import dataclasses
import hashlib
import importlib.metadata
import json
import os
import socket
import sys
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.loop.agentic_loop import TurnHandle, complete_turn, prepare_turn
from backend.loop.compaction import STABLE_SYSTEM_PREFIX
from backend.memory.lineage import FULL_TURN, MCP_BLOCK, LineageStore, _hash_payload_from_row
from backend.providers.base import Reply
from backend.mcp import coverage as cov
from backend.mcp.instructions import INSTRUCTIONS, INSTRUCTIONS_VERSION, instructions_hash
from backend.mcp.scope import verify_scope
from backend.mcp.mcp_server import MCP_CONTRACT_VERSION, NexusRuntime, build_server, contract
from scripts.history_shape_harness import production_wrap_off
from tests._tracked import tracked
from tests.fixtures.ce_rig import CERig
from tests.test_mcp1_split import RECORD_CLAIMS, UNCHANGED

ROOT = Path(__file__).resolve().parent.parent
MCP_FILES = [p for p in tracked("backend/mcp/*.py")]

# The console pinned byte for byte: measured at the base fd75e52, BEFORE the
# split, on CE's rig (pairs 20, answer 800, 17 KB lines, 76-char question).
BASE_TURNS = [
    (1, '8d74f3b231264f897be15475eb9df73bc8babc7c45ac2b2bd5b4961c2dcba9c3', '8cfe1c08fb286480ba0aec6f56d6db0c163f386649bb49af9438fdb957dad726', 'ee943393f769299219e484b57f4ca101524959920527a5f09fff2796fd81cde5', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17], [1, 2, 3, 4, 5]),
    (2, '2b13ca6eefdfb5e3ab7cda3e6fc67130e4b6246d5c44c2f5e0b87672d3a1660a', '8cfe1c08fb286480ba0aec6f56d6db0c163f386649bb49af9438fdb957dad726', '2d7a2ab2355c0dc7d2138c42da6e94aaaaaeeda8e4bd6636e1151e4584d2fad0', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17], [1, 2, 3, 4, 5]),
    (3, 'a4a16fbbb98cec96087f2c21648caf7a8445c62867e6e0d6f4e6a272f6b7d6d4', '8cfe1c08fb286480ba0aec6f56d6db0c163f386649bb49af9438fdb957dad726', '5d229211bb5e62b173a733e5767fed20edd28e256a8e8c5544710241b3dc661f', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17], [1, 2, 3, 4, 5]),
    (4, 'ea44a4e2371f2efaaf138e8ecf52f00e1488809219ecf08d1666e2d42ba34b14', '8cfe1c08fb286480ba0aec6f56d6db0c163f386649bb49af9438fdb957dad726', 'afe3cfadf97d6e90165622ee50f3f0beb0294a1035dbb82fb47902500cfae8d0', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17], [1, 2, 3, 4, 5]),
    (5, '2f00106f189d1d1f26be1f33bd3703812ea5ff68c9601ca8c1c39a16a5fd1e9c', '8cfe1c08fb286480ba0aec6f56d6db0c163f386649bb49af9438fdb957dad726', '2d29ee2227640bdfc0ba6192135ecb3b7e7e7113f709306a84b0b98397a57e0b', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17], [1, 2, 3, 4, 5]),
    (6, '880b86d1639dcecc83049a81e531cc09ab73ab25ba0bbe12bbc5549064a1d810', '8cfe1c08fb286480ba0aec6f56d6db0c163f386649bb49af9438fdb957dad726', '99a66aeb050154aa700887063b5f430b608b8d2f626fa3d5a05facf95f7086ae', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17], [1, 2, 3, 4, 5]),
]
INSTRUCTIONS_SHA256 = "53db4ee572f25d93f9046934ec99137bcd59a51ea6c0cb9bfd4fb981d1348c77"   # W10b: report actions, with nexus_record's id

RIG = dict(pairs=20, answer_chars=800, kb_lines=17, user_chars=76)
COMPARED = sorted(f for f, (cls, _why) in RECORD_CLAIMS.items()
                  if cls == UNCHANGED and f not in ("lineage_id", "ts", "record_hash", "sealed_block"))


LEARNING_KEYS = ("NEXUS_LEARNING", "NEXUS_LEARNING_FROZEN_SINCE")


@pytest.fixture(autouse=True)
def _mcp_env(monkeypatch, tmp_path):
    """Isolate every test's environment. `apply_learning_default` writes
    NEXUS_LEARNING into the process environment on purpose (MCP mode's
    default is read by every learning site), so the keys are snapshotted
    and restored by hand: monkeypatch records nothing for a key that was
    absent, and a `frozen` left behind starves every later test's graph
    persist."""
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    before = {k: os.environ.get(k) for k in LEARNING_KEYS + ("NEXUS_MCP_OWNER", "NEXUS_MCP_DATA_ROOT", "NEXUS_MCP_HANDLE_TIMEOUT")}
    for k in before:
        os.environ.pop(k, None)
    yield
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _runtime(root: Path, **kw) -> NexusRuntime:
    rt = NexusRuntime.open(root=root, **kw)
    rt.services.store.add(subject="python", relation="is_a", object="programming_language", weight=0.8,
                          tier=1, granularity=Granularity.TOPIC, source="human_assertion")
    rt.services.store.add(subject="python", relation="is_a", object="snake_genus", weight=0.7,
                          tier=1, granularity=Granularity.TOPIC, source="wikidata")
    return rt


def _call(server, name, **args):
    return asyncio.run(server.call_tool(name, args)).model_dump()["structured_content"]


# ---------------------------------------------------------------------------
# 1. The equivalence pin, and the console byte for byte
# ---------------------------------------------------------------------------

def _console_turn(root: Path):
    """Arm A: `run_turn` on CE's rig, with the request it sent captured."""
    rig = CERig(root, **RIG)
    seen = []
    orig = rig.provider.stream_chat

    async def rec(model, messages, num_ctx=8192, _o=orig):
        seen.append(messages)
        async for c in _o(model, messages, num_ctx=num_ctx):
            yield c
    rig.provider.stream_chat = rec
    row = rig.run_one()
    record = rig.lineage.get(row.lineage_id)
    block_sent = seen[0][0]["content"][len(STABLE_SYSTEM_PREFIX):]
    rig.close()
    return record, block_sent


def _two_call_turn(root: Path, *, tamper=None):
    """Arm B: `prepare_turn`, the rig's own stub answer as the "model", then
    `complete_turn` — the same collaborators, the same question, the same
    answer text, under the same history-wrap setting as the rig."""
    rig = CERig(root, **RIG)
    rig.turn = 1
    out: dict = {}

    async def drive():
        handle = None
        async for frame, payload in prepare_turn(
                rig.state, rig.question(1), retriever=rig.retriever, hippocampus=rig.hippocampus,
                provider=rig.provider):
            if frame is None:
                handle = payload
        if tamper is not None:
            handle = tamper(handle)
        answer = rig.answer(rig.turn + 1)          # the stub's text for the first turn (ce_rig: answer(turn + 1))
        rig.provider.calls = 1                     # the stub's next call is the extraction arm
        reply = Reply(answer=answer, reasoning=None, raw=answer, unclosed=False)
        async for _f, _s in complete_turn(handle, reply, token_count=16, retriever=rig.retriever,
                                          hippocampus=rig.hippocampus, hebbian=rig.hebbian,
                                          lineage=rig.lineage, outcome=out):
            pass
        out["block"] = handle.dynamic_block
    # The rig's clock around the direct calls, as `run_one` installs it for
    # arm A: both arms then read the same deterministic instant-series, so
    # `weight_at_injection` and every age are what arm A saw (W0).
    with production_wrap_off(), rig.clocked():
        asyncio.run(drive())
    record = rig.lineage.get(out["lineage_id"])
    rig.close()
    return record, out["block"]


def _diff(a: dict, b: dict) -> dict:
    return {k: (a.get(k), b.get(k)) for k in COMPARED if a.get(k) != b.get(k)}


def test_the_two_call_turn_equals_the_one_call_turn(tmp_path):
    """THE PIN. Same block bytes; same record on every field MCP-1 marked
    unchanged (lineage_id, ts, record_hash and the seal excluded: minted per
    record). Printed beside each other."""
    a, block_a = _console_turn(tmp_path / "a")
    b, block_b = _two_call_turn(tmp_path / "b")
    da, db = a.to_dict(), b.to_dict()
    diff = _diff(da, db)
    print(f"\nMCP2-1 ({sys.platform}) block bytes equal={block_a == block_b} ({len(block_a)} chars); "
          f"compared fields {COMPARED}; differing: {diff}")
    assert block_a == block_b, "the block the two-call turn returned is not the block the console sent"
    assert not diff, f"records differ on unchanged fields: {diff}"
    # The changed-meaning fields are the SAME too in console mode (no scope):
    for f in ("model_id", "prompt_prefix_hash", "response_hash", "answer_hash", "answer_chars", "request_hash"):
        assert da[f] == db[f], f
    assert da["record_scope"] == db["record_scope"] == FULL_TURN


def test_companion_a_planted_handle_divergence_is_named(tmp_path):
    """COMPANION: one id dropped from the handle's hits between the halves.
    The record's injected set is what names it."""
    a, _ = _console_turn(tmp_path / "a")
    b, _ = _two_call_turn(tmp_path / "b", tamper=lambda h: dataclasses.replace(h, hits=h.hits[1:]))
    diff = _diff(a.to_dict(), b.to_dict())
    print(f"\nMCP2-1c ({sys.platform}) planted divergence named by: {sorted(diff)}")
    assert "injected" in diff and len(b.injected) == len(a.injected) - 1


def test_the_console_is_pinned_byte_for_byte_against_the_base(tmp_path):
    """Six turns on CE's rig: request hash, context hash, answer hash,
    injected and cited sets equal the literals measured at fd75e52 before
    the split. A byte moved anywhere on the console path fails here."""
    rig = CERig(tmp_path / "rig", **RIG)
    rows = rig.run(6)
    got = []
    for r in rows:
        rec = rig.lineage.get(r.lineage_id)
        got.append((r.turn, rec.request_hash, rec.context["hash"], rec.answer_hash,
                    sorted(i["belief_id"] for i in rec.injected),
                    sorted(i["belief_id"] for i in rec.injected if i["cited"])))
    rig.close()
    print(f"\nMCP2-1b ({sys.platform}) console turns 1-6 equal to base: {got == BASE_TURNS}")
    assert got == BASE_TURNS


# ---------------------------------------------------------------------------
# 2. The scope, declared and sealed by the LS mechanism
# ---------------------------------------------------------------------------

def test_scope_declared_and_every_pre_mcp_record_recomputes(tmp_path):
    """An MCP record carries `mcp_block`, sealed (a changed scope on a sealed
    record is named); a console record carries the default; the LS fixture
    literal still holds, so every pre-MCP record recomputes unchanged."""
    from unittest import mock
    from tests.test_ls_learning_state import FIXED_ID, FIXED_TS, FIXTURE_TURN, PRE_LS_HASH
    rt = _runtime(tmp_path / "d", owner="alice")
    srv = build_server(rt, with_lifespan=False)
    r = _call(srv, "nexus_retrieve", question="tell me about python")
    rec = _call(srv, "nexus_record", retrieval_id=r["retrieval_id"], answer="Python is a programming_language.", model="m1")
    row = rt.services.lineage.get(rec["lineage_id"])
    raw = rt.services.lineage.conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (row.lineage_id,)).fetchone()
    payload = _hash_payload_from_row(raw)
    print(f"\nMCP2-2 ({sys.platform}) mcp record scope={row.record_scope} model={row.model_id} "
          f"intact={rt.services.lineage.verify_record(row.lineage_id)['record_intact']}")
    assert row.record_scope == MCP_BLOCK and payload["record_scope"] == MCP_BLOCK
    assert LineageStore.compute_record_hash(payload) == row.record_hash
    assert LineageStore.compute_record_hash({**payload, "record_scope": None}) != row.record_hash, "the scope is in the seal"
    # The console's record: the default, out of the hash, and the LS literal.
    lineage = LineageStore(tmp_path / "ls" / "kb.db")
    with mock.patch("backend.memory.lineage.time.time", return_value=FIXED_TS), \
            mock.patch("backend.memory.lineage.uuid.uuid4", return_value=FIXED_ID):
        pre = lineage.record_turn(**FIXTURE_TURN)
    assert pre.record_hash == PRE_LS_HASH and pre.record_scope is None and pre.to_dict()["record_scope"] == FULL_TURN
    # The dict form names the default (`full_turn`); recomputing from it gives
    # the stored hash, because `full_turn` normalises to absent in the seal
    # (found by tests/test_citation_rules.py, which recomputes from to_dict).
    assert LineageStore.compute_record_hash(pre.to_dict()) == pre.record_hash
    # Tamper, the other direction: an MCP record rewritten to the console's scope is named.
    mcp_lin = rt.services.lineage
    mcp_lin.conn.execute("UPDATE lineage_records SET record_scope = ? WHERE lineage_id = ?", (FULL_TURN, row.lineage_id))
    mcp_lin.conn.commit()
    assert mcp_lin.verify_record(row.lineage_id)["record_intact"] is False
    # Tamper companion: a scope written into a sealed full-turn record's NULL column is named.
    lineage.conn.execute("UPDATE lineage_records SET record_scope = ? WHERE lineage_id = ?", (MCP_BLOCK, pre.lineage_id))
    lineage.conn.commit()
    assert lineage.verify_record(pre.lineage_id)["record_intact"] is False
    lineage.close()


# ---------------------------------------------------------------------------
# 3. Coverage, counted
# ---------------------------------------------------------------------------

def test_coverage_counts_what_nexus_can_observe(tmp_path):
    """Retrieve then no record → after the timeout one coverage row, zero
    lineage rows. Record with an unknown retrieval_id → refused by name,
    counted. Retrieve then record → one lineage row; the counters read
    1 / 1 / 1 and the sentence rides every read."""
    rt = _runtime(tmp_path / "d", owner="alice")
    srv = build_server(rt, with_lifespan=False)
    r1 = _call(srv, "nexus_retrieve", question="tell me about python")
    expired = rt.expire_handles(now=rt.handles._open[r1["retrieval_id"]].created_at + 10_000)
    assert expired == [r1["retrieval_id"]]
    assert rt.coverage.counters()[cov.RETRIEVED_WITHOUT_RECORD] == 1
    assert rt.services.lineage.recent(limit=10) == []
    with pytest.raises(Exception, match="unknown_retrieval_id"):
        _call(srv, "nexus_record", retrieval_id="no-such", answer="x")
    assert rt.coverage.counters()[cov.RECORDED_WITHOUT_RETRIEVE] == 1
    r2 = _call(srv, "nexus_retrieve", question="tell me about python")
    rec = _call(srv, "nexus_record", retrieval_id=r2["retrieval_id"], answer="Python is a programming_language.")
    c = rt.coverage.counters()
    print(f"\nMCP2-3 ({sys.platform}) coverage {c}")
    assert (c[cov.RECORDED], c[cov.RETRIEVED_WITHOUT_RECORD], c[cov.RECORDED_WITHOUT_RETRIEVE]) == (1, 1, 1)
    assert len(rt.services.lineage.recent(limit=10)) == 1 and rec["lineage_id"]
    assert c["sentence"] == cov.COVERAGE_SENTENCE
    status = _call(srv, "nexus_status")
    assert status["coverage"] == c and status["owner"] == "alice" and status["handles_open"] == 0


# ---------------------------------------------------------------------------
# 4. Undefined fields are null and declared
# ---------------------------------------------------------------------------

def test_undefined_fields_are_null_and_a_planted_value_is_named(tmp_path):
    rt = _runtime(tmp_path / "d", owner="alice")
    srv = build_server(rt, with_lifespan=False)
    r = _call(srv, "nexus_retrieve", question="tell me about python")
    rec = _call(srv, "nexus_record", retrieval_id=r["retrieval_id"], answer="Python is a programming_language.")
    row = rt.services.lineage.get(rec["lineage_id"])
    assert row.reasoning_hash is None and row.reasoning_chars is None and row.answer_empty_reason is None
    assert row.contradictions == () and row.context["extraction_failed"] is None
    assert row.model_id == "client-reported:unknown" and row.prompt_prefix_hash == hashlib.sha256(b"").hexdigest()
    assert row.request_hash == row.context["hash"]
    assert verify_scope(row) == []
    planted = dataclasses.replace(row, reasoning_hash="ab" * 32)
    named = verify_scope(planted)
    print(f"\nMCP2-4 ({sys.platform}) planted reasoning_hash named: {named}")
    assert any("reasoning_hash" in n for n in named)
    # The resource carries the declaration and the (empty) violations.
    res = asyncio.run(srv.read_resource(f"nexus://lineage/{row.lineage_id}"))
    payload = json.loads(list(res)[0].content)
    assert payload["record_scope"] == MCP_BLOCK and payload["scope_declaration"]["violations"] == []
    assert "reasoning_hash" in payload["scope_declaration"]["undefined"]


# ---------------------------------------------------------------------------
# 5. No HTTP in MCP mode; 6. the token has no role
# ---------------------------------------------------------------------------

def _mcp_sources() -> dict[str, str]:
    return {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in MCP_FILES}


def http_constructions(sources: dict[str, str]) -> set[tuple[str, str]]:
    """(file, name) for every HTTP-app construction or server-start name
    the MCP package mentions, by AST."""
    forbidden = {"create_app", "FastAPI", "uvicorn", "TestClient", "sse_app", "streamable_http_app"}
    out = set()
    for path, src in sources.items():
        for n in ast.walk(ast.parse(src)):
            name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else None
            if name in forbidden:
                out.add((path, name))
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                mod = getattr(n, "module", None) or ""
                names = [a.name for a in n.names]
                if "uvicorn" in mod or "uvicorn" in names or "fastapi" in mod:
                    out.add((path, mod or names[0]))
    return out


def token_reads(sources: dict[str, str]) -> set[tuple[str, str]]:
    """(file, literal-or-name) for every read of the token setting, by AST:
    the setting's string literal, or the server's token helpers by name."""
    out = set()
    for path, src in sources.items():
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and "NEXUS_API_TOKEN" in n.value:
                out.add((path, n.value))
            name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else None
            if name in {"active_token", "require_token"}:
                out.add((path, name))
    return out


class ListenerRefused(AssertionError):
    """A socket was bound or put to listen in MCP mode, outside the standard
    library's own `socketpair`."""


# The standard library's socketpair as Windows runs it: CPython has no native
# `socketpair` there, so `socket.py` builds one in Python — it binds a
# listening socket to 127.0.0.1:0, connects to it, accepts and closes the
# listener. The function's name depends on the patch release, not the minor
# version: CPython 3.11.9 (the Windows CI runner's) defines it as `socketpair`;
# 3.11.17 (the Linux sandbox's) and later releases define
# `_fallback_socketpair` and bind `socketpair` to it (the W0 verifier read
# both). asyncio makes its event loop's self-pipe with it,
# so `asyncio.run` binds and listens on loopback on Windows and nowhere else.
# That is not a server: the listener never outlives the call and no peer can
# be accepted after it returns.
STDLIB_SOCKETPAIR_FUNCTIONS = ("socketpair", "_fallback_socketpair")


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def in_stdlib_socketpair(frames) -> bool:
    """True iff the stack `frames` — `(filename, function)` pairs, innermost
    first — passes through the standard library's own socketpair. Matched on
    the function's name AND on its frame belonging to `socket.py` itself, so a
    function merely named `socketpair` anywhere else is not exempt."""
    return any(func in STDLIB_SOCKETPAIR_FUNCTIONS and _same_file(filename, socket.__file__)
               for filename, func in frames)


def _stack_from(frame) -> list[tuple[str, str]]:
    out = []
    while frame is not None:
        out.append((frame.f_code.co_filename, frame.f_code.co_name))
        frame = frame.f_back
    return out


@contextlib.contextmanager
def refuse_listeners(monkeypatch):
    """Every `bind` and `listen` on a socket is refused (raised and recorded)
    unless its stack passes through the stdlib's socketpair, which is let
    through and counted. Yields `{"refused": [...], "exempt": [...]}`."""
    seen: dict[str, list] = {"refused": [], "exempt": []}

    def guarded(name, original):
        def method(self, *args, **kwargs):
            stack = _stack_from(sys._getframe(1))
            if in_stdlib_socketpair(stack):
                seen["exempt"].append((name, args))
                return original(self, *args, **kwargs)
            caller = next(((f, fn) for f, fn in stack if not _same_file(f, __file__)), stack[0])
            seen["refused"].append((name, args, caller))
            raise ListenerRefused(f"a socket {name} in MCP mode: {args!r} from {caller}")
        return method

    monkeypatch.setattr(socket.socket, "bind", guarded("bind", socket.socket.bind))
    monkeypatch.setattr(socket.socket, "listen", guarded("listen", socket.socket.listen))
    yield seen


def test_no_http_app_in_mcp_mode_by_introspection(tmp_path, monkeypatch):
    """The package names no HTTP construction (AST), and `setup()` — every
    step of the entry point but the transport — opens no listener: every
    `bind` and `listen` is refused and recorded, and none is. Checked, not
    slept.

    W0 (the run to production): the first version made bind and listen raise
    outright, which failed on Windows for a reason that is not a server —
    asyncio's self-pipe is the stdlib's pure-Python socketpair there, which
    binds 127.0.0.1:0 and listens for the length of one call. The exemption is
    by stack and by file (`in_stdlib_socketpair`), never by address, so a
    real loopback listener is still named — the companions below plant one,
    and a look-alike function named `socketpair` outside `socket.py`."""
    assert http_constructions(_mcp_sources()) == set(), http_constructions(_mcp_sources())
    monkeypatch.setenv("NEXUS_MCP_DATA_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("NEXUS_ENV_ROOT", str(tmp_path))
    from backend.mcp.__main__ import setup
    with refuse_listeners(monkeypatch) as seen:
        runtime, server = setup()
        n_tools = len(asyncio.run(server.list_tools()))
    print(f"\nMCP2-5 ({sys.platform}) setup() built {server.name!r} with {n_tools} tools; "
          f"listeners refused {len(seen['refused'])}, stdlib socketpair calls let through {len(seen['exempt'])}")
    assert seen["refused"] == [], seen["refused"]
    assert server.name == "nexus" and runtime.services is not None
    if sys.platform == "win32":
        # Non-vacuity where the exemption is the reason this passes: the
        # self-pipe went through the stdlib's socketpair and was let through.
        assert seen["exempt"], "on Windows the event loop's self-pipe should have gone through socketpair"
    # Companion: the guard names a planted construction.
    planted = {**_mcp_sources(), "backend/mcp/planted.py": "from backend.server import create_app\napp = create_app()\n"}
    assert ("backend/mcp/planted.py", "create_app") in http_constructions(planted)


def test_companion_a_planted_loopback_listener_is_refused_on_every_platform(monkeypatch):
    """The listener the exemption must not let through: a real socket bound
    to 127.0.0.1:0 and listening, made from test code. Refused and named on
    every platform — including Windows, where the exempt socketpair binds the
    same address."""
    with refuse_listeners(monkeypatch) as seen:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(ListenerRefused, match="bind"):
                s.bind(("127.0.0.1", 0))
            with pytest.raises(ListenerRefused, match="listen"):
                s.listen()
        finally:
            s.close()
    print(f"\nMCP2-5c ({sys.platform}) planted listener refused: {[(n, a) for n, a, _c in seen['refused']]}")
    assert [n for n, _a, _c in seen["refused"]] == ["bind", "listen"]
    assert seen["exempt"] == []


def test_companion_a_lookalike_socketpair_outside_the_stdlib_is_refused(monkeypatch):
    """The exemption is by file as well as by name: a function called
    `socketpair` defined here, which binds, is refused; the predicate itself
    accepts the stdlib's file and refuses any other, and refuses another
    function from the stdlib's file."""
    def socketpair():                      # same name as the stdlib's, wrong file
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 0))
        finally:
            s.close()

    with refuse_listeners(monkeypatch) as seen:
        with pytest.raises(ListenerRefused):
            socketpair()
    assert [n for n, _a, _c in seen["refused"]] == ["bind"] and seen["exempt"] == []
    assert in_stdlib_socketpair([(socket.__file__, "socketpair")])
    assert in_stdlib_socketpair([(socket.__file__, "_fallback_socketpair")])
    assert not in_stdlib_socketpair([(__file__, "socketpair")])
    assert not in_stdlib_socketpair([(__file__, "_fallback_socketpair")])
    assert not in_stdlib_socketpair([(socket.__file__, "create_server")])


def test_the_token_has_no_role_in_mcp_mode():
    """No read of the token setting anywhere under backend/mcp (AST).
    Companion: a planted `os.environ.get("NEXUS_API_TOKEN")` is named, and so
    is a planted call to the server's helper by name."""
    assert token_reads(_mcp_sources()) == set(), token_reads(_mcp_sources())
    planted = {**_mcp_sources(),
               "backend/mcp/p1.py": "import os\nTOKEN = os.environ.get('NEXUS_API_TOKEN')\n",
               "backend/mcp/p2.py": "from backend.server import active_token\nT = active_token()\n"}
    got = token_reads(planted)
    assert ("backend/mcp/p1.py", "NEXUS_API_TOKEN") in got and ("backend/mcp/p2.py", "active_token") in got


# ---------------------------------------------------------------------------
# 7. Frozen by default in MCP mode; live is the opt-in
# ---------------------------------------------------------------------------

def test_frozen_default_with_no_env_and_live_as_the_opt_in(tmp_path):
    """With no `.env` and NEXUS_LEARNING unset, a fresh MCP-mode store reads
    frozen, the from-date is the first run, the record says `frozen`, and
    `fire` writes nothing. COMPANION: with NEXUS_LEARNING=live set, the same
    turn fires the cited belief."""
    rt = _runtime(tmp_path / "frozen", owner="alice")
    assert rt.learning_applied["set_by"] == "mcp_default" and rt.learning_applied["since_set_by"] == "first_run"
    srv = build_server(rt, with_lifespan=False)
    before = rt.services.store.get_by_id(1).fire_count
    r = _call(srv, "nexus_retrieve", question="tell me about python")
    assert r["learning_mode"] == "frozen"
    rec = _call(srv, "nexus_record", retrieval_id=r["retrieval_id"], answer="Python is a programming_language.")
    row = rt.services.lineage.get(rec["lineage_id"])
    after = rt.services.store.get_by_id(1).fire_count
    print(f"\nMCP2-7 ({sys.platform}) frozen: record.learning_mode={row.learning_mode} since={rt.learning_applied['since']} "
          f"fire_count {before}->{after}; cited={rec['cited']}")
    assert row.learning_mode == "frozen" and rec["cited"] == [1] and after == before
    # Companion: the opt-in. Written to os.environ directly, NOT through
    # monkeypatch: the runtime above set `frozen`, monkeypatch would record
    # that as the value to restore, and its undo runs AFTER `_mcp_env`'s —
    # leaking `frozen` into every later test (found: AW's used-store
    # migration test read an empty persisted graph). `_mcp_env` restores both.
    os.environ["NEXUS_LEARNING"] = "live"
    os.environ.pop("NEXUS_LEARNING_FROZEN_SINCE", None)
    rt2 = _runtime(tmp_path / "live", owner="alice")
    assert rt2.learning_applied["set_by"] == "environment"
    srv2 = build_server(rt2, with_lifespan=False)
    r2 = _call(srv2, "nexus_retrieve", question="tell me about python")
    rec2 = _call(srv2, "nexus_record", retrieval_id=r2["retrieval_id"], answer="Python is a programming_language.")
    row2 = rt2.services.lineage.get(rec2["lineage_id"])
    assert row2.learning_mode is None and rt2.services.store.get_by_id(1).fire_count == 1


# ---------------------------------------------------------------------------
# 8. Instructions versioned; 9. the contract; 10. the HTTP surfaces read it
# ---------------------------------------------------------------------------

def test_instructions_are_versioned_by_a_pinned_hash():
    assert instructions_hash() == INSTRUCTIONS_SHA256, (
        "the server instructions changed: bump INSTRUCTIONS_VERSION and re-pin the hash, as a recorded change")
    assert INSTRUCTIONS_VERSION == "w10b.1"      # mcp2.1 until W10b asked clients to report their actions
    assert instructions_hash(INSTRUCTIONS + " ") != INSTRUCTIONS_SHA256          # companion
    assert "nexus_retrieve" in INSTRUCTIONS and "nexus_record" in INSTRUCTIONS
    assert "nexus_record_action" in INSTRUCTIONS


def test_the_mcp_contract_lists_every_tool_with_its_schema(tmp_path):
    rt = _runtime(tmp_path / "d")
    srv = build_server(rt, with_lifespan=False)
    c = asyncio.run(contract(srv))
    names = [t["name"] for t in c["tools"]]
    print(f"\nMCP2-9 ({sys.platform}) contract v{c['contract_version']}: tools {names}; resources "
          f"{[r['uri_template'] for r in c['resources']]}")
    # 1 = MCP-2; 2 = W0b (`nexus_status.replay_capture`), a recorded bump; 3 = W6; 4 = W9 (`nexus_commit`,
    # `nexus_status.identity`); 5 = W10b (`nexus_record_action`).
    assert c["contract_version"] == MCP_CONTRACT_VERSION == 5
    assert "identity" in next(t["output_schema"] for t in c["tools"] if t["name"] == "nexus_status")["properties"]
    status_schema = next(t["output_schema"] for t in c["tools"] if t["name"] == "nexus_status")
    assert "replay_capture" in status_schema["properties"], "W0b: the kept bytes' size rides nexus_status"
    assert names == ["nexus_retrieve", "nexus_record", "nexus_recall", "nexus_asof", "nexus_assert",
                     "nexus_assert_confirm", "nexus_commit", "nexus_record_action", "nexus_status"]
    assert all(t["input_schema"] and t["description"] for t in c["tools"])
    typed = {t["name"] for t in c["tools"] if t["output_schema"]}
    assert typed == {"nexus_retrieve", "nexus_record", "nexus_assert", "nexus_assert_confirm", "nexus_commit",
                     "nexus_record_action", "nexus_status"}
    assert [r["uri_template"] for r in c["resources"]] == ["nexus://lineage/{lineage_id}"]
    assert c["record_scope"]["record_scope"] == MCP_BLOCK and "reasoning_hash" in c["record_scope"]["undefined"]
    assert c["instructions_sha256"] == INSTRUCTIONS_SHA256


def test_asof_refuses_a_bare_date_and_recall_reads_the_record(tmp_path):
    rt = _runtime(tmp_path / "d", owner="alice")
    srv = build_server(rt, with_lifespan=False)
    with pytest.raises(Exception, match="bare_date"):
        _call(srv, "nexus_asof", date="2026-03-12")
    r = _call(srv, "nexus_retrieve", question="tell me about python")
    rec = _call(srv, "nexus_record", retrieval_id=r["retrieval_id"], answer="Python is a programming_language.")
    res = asyncio.run(srv.call_tool("nexus_recall", {"belief_id": 1})).model_dump()
    recall = json.loads(res["content"][0]["text"])
    assert recall["resolved"] and recall["total"] == 1 and recall["records"][0]["lineage_id"] == rec["lineage_id"]
    assert recall["records"][0]["accountable"] == "alice" and recall["per_person"]["declared"] is True
    asof = json.loads(asyncio.run(srv.call_tool("nexus_asof", {"date": "2099-01-01T00:00:00+00:00"})).model_dump()["content"][0]["text"])
    assert asof["total"] == 2 and asof["contract_version"] == 2


def test_the_http_surfaces_read_the_scope_and_the_counters(tmp_path, monkeypatch):
    """`/cortex/stats.mcp` reads the counters the MCP process wrote beside the
    record (absent before it ever ran, present after), and `/lineage/{id}`
    carries the scope declaration. The HTTP app constructs nothing MCP."""
    monkeypatch.setenv("NEXUS_API_TOKEN", "mcp2-token")
    from fastapi.testclient import TestClient
    from backend.server import create_app, create_services
    root = tmp_path / "root"
    svc = create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma", eval_log_path=root / "m.jsonl",
                          archive_db_path=root / "archive.db", snapshots_dir=root / "snapshots")
    client = TestClient(create_app(svc))
    before = client.get("/cortex/stats").json()["mcp"]
    assert before["coverage"]["present"] is False and before["contract_version"] == 5   # W0b 1 -> 2; W6 2 -> 3; W9 3 -> 4; W10b 4 -> 5
    rt = NexusRuntime.open(root=root, owner="alice")
    rt.services.store.add(subject="python", relation="is_a", object="programming_language", weight=0.8,
                          tier=1, granularity=Granularity.TOPIC, source="human_assertion")
    srv = build_server(rt, with_lifespan=False)
    r = _call(srv, "nexus_retrieve", question="tell me about python")
    rec = _call(srv, "nexus_record", retrieval_id=r["retrieval_id"], answer="Python is a programming_language.")
    after = client.get("/cortex/stats").json()["mcp"]
    print(f"\nMCP2-10 ({sys.platform}) /cortex/stats.mcp before={before['coverage']['present']} after={after['coverage']}")
    assert after["coverage"]["present"] is True and after["coverage"]["recorded"] == 1
    page = client.get(f"/lineage/{rec['lineage_id']}", headers={"Authorization": "Bearer mcp2-token"}).json()
    assert page["record_scope"] == MCP_BLOCK and page["scope_declaration"]["violations"] == []


def test_sdk_pinned_and_stdio_is_the_transport_wired():
    """S1.2: the SDK version and the transport, printed; `__main__` runs
    stdio and names no other; `build_server` names none (the SDK's
    streamable-http stays reachable for a later runner)."""
    version = importlib.metadata.version("mcp")
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    print(f"\nMCP2-S1 ({sys.platform}) mcp SDK {version}; transport: stdio (python -m backend.mcp)")
    assert version.startswith("2.") and "mcp>=2.3,<3" in req
    main_src = (ROOT / "backend" / "mcp" / "__main__.py").read_text(encoding="utf-8")
    runs = [n for n in ast.walk(ast.parse(main_src)) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "run"]
    assert [a.value for r in runs for a in r.args if isinstance(a, ast.Constant)] == ["stdio"]
    server_src = (ROOT / "backend" / "mcp" / "mcp_server.py").read_text(encoding="utf-8")
    assert not any(isinstance(n, ast.Constant) and n.value in ("stdio", "sse", "streamable-http")
                   for n in ast.walk(ast.parse(server_src)) if isinstance(n, ast.Constant)), \
        "the tool layer must not choose a transport"


# ---------------------------------------------------------------------------
# 12. The assert pair: Law 3 on the MCP surface, the refusal recorded (AT)
# ---------------------------------------------------------------------------

def test_assert_confirm_is_law3_gated_and_the_refusal_is_recorded(tmp_path):
    """`nexus_assert_confirm` with `confirmed` omitted or false stores nothing,
    raises a named refusal, and writes one `refused_requests` row on the MCP
    route (AT: a well-formed request for a governed act, declined, is
    recorded). COMPANION: `confirmed=true` stores the belief and the ledger
    gains no row for it. `nexus_assert` is the offer and stores nothing."""
    rt = _runtime(tmp_path / "d", owner="alice")
    srv = build_server(rt, with_lifespan=False)
    conn, rconn = rt.services.store.conn, rt.services.recalibration.conn
    n_triples = conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0]
    offer = _call(srv, "nexus_assert", sentence="The dose was 600mg.")
    assert offer["shaped"] and offer["triples"]
    assert conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0] == n_triples, "the offer stored something"
    for args in ({"sentence": "The dose was 600mg."},
                 {"sentence": "The dose was 600mg.", "confirmed": False}):
        with pytest.raises(Exception, match="Law 3"):
            _call(srv, "nexus_assert_confirm", **args)
    rows = [tuple(r) for r in rconn.execute("SELECT route, rule FROM refused_requests ORDER BY ts, event_id")]
    assert rows == [("mcp:nexus_assert_confirm", "law3_unconfirmed")] * 2, rows
    assert conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0] == n_triples, "an unconfirmed assert stored"
    ok = _call(srv, "nexus_assert_confirm", sentence="The dose was 600mg.", confirmed=True)
    print(f"\nMCP2-12 ({sys.platform}) assert_confirm: 2 unconfirmed refused, {len(rows)} recorded; "
          f"confirmed stored {ok['stored']} refused {ok['refused']}")
    assert ok["stored"] >= 1 and ok["refused"] == 0
    assert conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0] > n_triples
    assert rconn.execute("SELECT COUNT(*) FROM refused_requests").fetchone()[0] == 2
