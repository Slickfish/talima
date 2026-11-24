"""W10b part 1 (run/specs/W10b.md D1 to D5, D8): every runtime action recorded by one recorder, fail closed.

WHAT IS HELD HERE (the spec's claims)
    T1  (A1) A turn's consented search produces exactly one action record, naming the id the turn's record carries;
        its row exists before the request arrives, and its outcome is written before the result becomes prompt text.
    T2  (A1) `POST /search/execute` produces exactly one, naming its route and no turn.
    T3  (A1) A seed produces one per request it sends; T3b a Wikidata seed both of a concept's, and what each
        sends is what the seeder sent before the recorder owned its client; T3c its failures are its
        warnings and failed rows.
    T4  (A2) Every use of what can reach outside the process (a socket library, a child process) in the tracked
        backend is the recorder's or answered for, counted per function; the action set takes nothing from outside
        but pinned names, reads no private attribute, loads no code by name and awaits only the recorder's handle;
        the paths answered for as dead have no production caller. T4b: 28 plants spelled as the guard never spells
        one are each named. What a static reading cannot see is listed at T4's head; the runtime trace
        (tests/test_w10b_network_trace.py) holds the rest on every path it drives.
    T5  (A3) A planted insert failure: no request leaves; the turn says `not_recorded` and injects nothing; the
        routes answer 503 `action_not_recorded`.
    T6  (A3) A planted outcome failure: the result is withheld (503) and the row says only that it was requested.
    T7  (A5) A planted API key header is recorded by name and fingerprint only; its value is in no row.
    T8  (D2) The table is written by the recorder's two statements and no other, by AST, with a planted companion.
    T9  (D8) `/lineage/{id}` lists the turn's actions.
    T10 (D6) The governance seal covers each action whose outcome is written, once, under the block's `action`
        root; a row still `requested` waits.
    T11 (A6) An action's receipt verifies offline with `nexus-verify --action`, isolated, from an empty folder.
    T12 (A6) Any part of the receipt changed fails by name: a field, the record hash, the path, the header, the
        signature; and an unpinned check fails.
    T13 (D7) The receipt route refuses by name: no such action (404), an outcome not recorded and an action no
        seal covers yet (409); it declares itself per-person (A-2).
    T14 The verifier's copy of the hashed fields is the recorder's.
    T15 (A4) An action a client reports through MCP is stored `client_reported`, never counted as runtime, and its
        receipt verifies offline saying the client reported it.
    T16 (D9) A report against an unknown turn, or with a request hash that is not one, is refused by name and
        writes nothing.
    T17 (D9) The coverage block says what NEXUS can say about a client's actions: only what it is told.
    T18 (A8) With signing off (the default), no request carries a signature header and no row a signature.
    T19 (A7) With NEXUS_SIGN_REQUESTS=on, the request as received verifies against `/identity`'s key from the RFC 9421
        base its recipient rebuilds, and the row carries the signature sent.
    T20 (A7, D11) RFC 9421's own Ed25519 vector (Appendix B.2.6, its key from B.1.4) reproduces byte for byte.
    T21 (A7) `GET /identity/signed-action` (open) returns the sealed action behind a signature and nothing else; an
        unknown signature is 404 and an unsealed one 409.
    T22 (D10) Signing on and no key to sign with: nothing is sent, said by name.
    T23 (D10) An unknown signing setting reads off and is declared on `/cortex/stats`.

LAW 16. Run on 41ded90 (the spec, before any W10b code), with `backend/memory/actions.py` stubbed only so this file
imports: all nine failed there. T4 named the search tool's and the seeder's direct calls, T8 found no writer, and
the rest had no table to write or read (the stand-in server's row query failed with them). Run on 6707fb6
(part 1) before part 2: T10 to T14 failed there (no `action` root, no receipt route, no `verify_action`), and T8
found two writers where three are named. Run on c18c348 (part 2) before part 3: T15 to T17 failed there (no
`nexus_record_action`, no actions line on the coverage block). Run on 0d055ea (part 3) before part 4: T12 and T19
to T23 failed there (no signing, no lookup, the identity in clear in the hash); T18 held there trivially, nothing
signing, and is the default's guard: it fails against a mutant whose default is `on`.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import functools
import hashlib
import json
import os
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.memory import actions as act
from backend.server import create_app
from scripts import nexus_verify as nv
from tests import _proc
from tests.fixtures.ce_rig import RigClock
from tests.test_mcp2_build import _mcp_env  # noqa: F401 — autouse here too: MCP mode writes NEXUS_LEARNING
from tests.test_w8_proofs import _console, _turn

REPO = Path(__file__).resolve().parent.parent
QUERY = "what is the latest python release"
BODY = json.dumps({"results": [{"title": "Python 3.13", "url": "https://docs.python.org/3.13/whatsnew",
                                "content": "What's new", "engine": "stub"}]}).encode("utf-8")
SECRET = "sk-not-a-real-key-7f3a9c"


class _Searx:
    """A stand-in SearXNG on loopback. Each request is kept with the action rows that existed when it arrived."""

    def __init__(self, rows_now):
        self.requests, outer = [], self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):                       # noqa: N802 (http.server's name)
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "rows": rows_now()})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(BODY)))
                self.end_headers()
                self.wfile.write(BODY)

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def _rows(svc) -> list[dict]:
    return [dict(r) for r in svc.lineage.conn.execute("SELECT * FROM action_records ORDER BY requested_at")]


@pytest.fixture
def console(tmp_path):
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
    searx = _Searx(lambda: _rows(svc))
    svc.search_tool.base_url = searx.url
    yield svc, client, bearer, searx
    searx.stop()


def _frames(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        if block.startswith("event: ") and "\ndata: " in block:
            name, data = block.split("\ndata: ", 1)
            out.append((name[len("event: "):], json.loads(data)))
    return out


def _searching_turn(client, bearer) -> list[tuple[str, dict]]:
    assert client.post("/search/confirm", headers=bearer, json={"query": QUERY, "confirmed": True}).status_code == 200
    with RigClock().installed():
        return _frames(client.post("/chat", headers=bearer, json={"message": QUERY}).text)


# ---------- T1 to T3 (A1) ----------

def test_t1_a_turns_search_is_one_action_naming_the_turn_recorded_before_it_left_and_before_use(console,
                                                                                                monkeypatch):
    svc, client, bearer, searx = console
    import backend.loop.agentic_loop as loop
    seen_at_use = []
    real = loop.admit_results

    def watched(results):
        seen_at_use.append([r["state"] for r in _rows(svc)])
        return real(results)
    monkeypatch.setattr(loop, "admit_results", watched)
    frames = _searching_turn(client, bearer)
    done = next(d for e, d in frames if e == "done")
    rows = _rows(svc)
    assert len(rows) == 1 and len(searx.requests) == 1, (rows, searx.requests)
    row = rows[0]
    assert (row["kind"], row["evidence_class"], row["state"], row["lineage_id"]) == (
        "web_search", "runtime", "completed", done["lineage_id"]), row
    assert row["response_hash"] == hashlib.sha256(BODY).hexdigest() and row["status"] == "200", row
    assert [r["state"] for r in searx.requests[0]["rows"]] == ["requested"], "the row exists before the request"
    assert seen_at_use == [["completed"]], "the outcome is written before the result becomes prompt text"
    assert any(e == "rag_injected" for e, _d in frames), "non-vacuity: the result was used"
    assert row["record_hash"] == act.record_hash(row), "the record hash is over the row's fields"


def test_t2_the_search_route_is_one_action_naming_its_route_and_no_turn(console):
    svc, client, bearer, searx = console
    resp = client.post("/search/execute", headers=bearer, json={"query": QUERY, "confirmed": True})
    assert resp.status_code == 200 and resp.json()["count"] == 1, resp.text
    rows = _rows(svc)
    assert [(r["kind"], r["trigger_route"], r["lineage_id"], r["state"]) for r in rows] == [
        ("web_search", "/search/execute", None, "completed")], rows
    assert rows[0]["target"] == f"{searx.url}/search" and rows[0]["identity_mechanism"] == "asserted", rows[0]


class _SeedResp:
    def __init__(self, payload, status=200):
        self._p, self.status_code, self.content = payload, status, json.dumps(payload).encode("utf-8")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=httpx.Request("GET", "http://seed"),
                                        response=self)

    def json(self):
        return self._p


def _seed_transport(monkeypatch, answer):
    """`httpx.AsyncClient`, wherever it is made, replaced by a stand-in that keeps each client's default headers
    and each request's URL, parameters, headers and timeout, answering from `answer(url)`: a payload, a
    (payload, status) pair, or an exception to raise."""
    clients, sent = [], []

    class _Client:
        def __init__(self, *a, headers=None, **k):
            self.headers = dict(headers or {})
            clients.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, *, params=None, headers=None, timeout=None, **kw):
            sent.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {}),
                         "timeout": timeout})
            got = answer(url)
            if isinstance(got, Exception):
                raise got
            return _SeedResp(*got) if isinstance(got, tuple) else _SeedResp(got)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)   # the transport, wherever it is made
    return clients, sent


# What the seeder sent before the recorder owned its client, pinned (the first delta check's SF6).
WIKIDATA_HEADERS = {"User-Agent": "NEXUS-Cognitive-Substrate/1.0 (https://github.com/Slickfish/talima; "
                                  "operator-contact@nexus.local) httpx/0.27", "Accept": "application/json"}


def test_t3_a_seed_is_one_action_per_request_it_sends(console, monkeypatch):
    svc, client, bearer, _searx = console
    clients, sent = _seed_transport(monkeypatch, lambda url: {"edges": []})
    resp = client.post("/kb/seed/conceptnet", headers=bearer,
                       json={"concepts": ["sleep", "rest"], "confirmed": True})
    assert resp.status_code == 200, resp.text
    rows = _rows(svc)
    assert len(sent) == 2 and [(r["kind"], r["trigger_route"], r["state"]) for r in rows] == [
        ("seed_conceptnet", "/kb/seed/conceptnet", "completed")] * 2, rows
    assert [(s["url"].rsplit("/", 1)[-1], s["params"], s["timeout"]) for s in sent] == [
        ("sleep", {"limit": 20}, 10.0), ("rest", {"limit": 20}, 10.0)], sent
    assert len(clients) == 1, "one client a seed"


def test_t3b_a_wikidata_seed_records_both_requests_a_concept_takes(console, monkeypatch):
    svc, client, bearer, _searx = console
    from backend.kb.source_registry import default_registry
    clients, sent = _seed_transport(monkeypatch, lambda url: {"search": [{"id": "Q1"}]} if "api.php" in url else {
        "results": {"bindings": [{"propLabel": {"value": "instance of"}, "valueLabel": {"value": "habit"}}]}})
    resp = client.post("/kb/seed/wikidata", headers=bearer, json={"concepts": ["sleep"], "confirmed": True})
    assert resp.status_code == 200 and resp.json()["seeded"] == 1, resp.text
    rows = _rows(svc)
    assert len(sent) == 2 == len(rows), (sent, rows)
    assert [(r["kind"], r["trigger_route"], r["state"], r["target"]) for r in rows] == [
        ("seed_wikidata", "/kb/seed/wikidata", "completed", "https://www.wikidata.org/w/api.php"),
        ("seed_wikidata", "/kb/seed/wikidata", "completed", act.target_of(default_registry.get("wikidata").endpoint))
    ], rows
    qid, sparql = sent
    assert (qid["params"], qid["headers"], qid["timeout"]) == ({"action": "wbsearchentities", "search": "sleep",
                                                                "language": "en", "format": "json", "limit": 1},
                                                               {}, 15.0), qid
    assert sparql["params"]["format"] == "json" and "wd:Q1 ?prop ?value" in sparql["params"]["query"], sparql
    assert (sparql["headers"], sparql["timeout"]) == ({"Accept": "application/sparql-results+json"}, 15.0), sparql
    assert [c.headers for c in clients] == [WIKIDATA_HEADERS], "one client a seed, with the seeder's own headers"


def test_t3c_a_seeds_failed_requests_are_its_warnings_and_failed_rows(console, monkeypatch):
    """The except clauses the round rewrote, on the paths no other test drives (the first delta check's SF8)."""
    svc, client, bearer, _searx = console

    def answer(url):
        last = url.rsplit("/", 1)[-1]           # the concept, for ConceptNet's requests
        if last == "sleep":
            return httpx.ConnectError("refused")
        if last == "rest":
            return ({}, 500)
        return {"search": [{"id": "Q1"}]} if "api.php" in url else httpx.ConnectError("refused")
    _seed_transport(monkeypatch, answer)
    cn = client.post("/kb/seed/conceptnet", headers=bearer, json={"concepts": ["sleep", "rest"], "confirmed": True})
    wd = client.post("/kb/seed/wikidata", headers=bearer, json={"concepts": ["habit"], "confirmed": True})
    assert cn.status_code == 200 and cn.json()["warnings"] == [
        "sleep: network error — ConnectError", "rest: HTTP 500 from ConceptNet"], cn.text
    assert wd.status_code == 200 and wd.json()["warnings"] == ["habit: SPARQL query failed — ConnectError"], wd.text
    assert sorted((r["kind"], r["state"], r["status"]) for r in _rows(svc)) == sorted([
        ("seed_conceptnet", "failed", "ConnectError"), ("seed_conceptnet", "completed", "500"),
        ("seed_wikidata", "completed", "200"), ("seed_wikidata", "failed", "ConnectError")])


# ---------- T4 (A2): what can reach outside the process, by use; the action set, closed ----------
#
# What this static reading holds, and what it does not (the runtime trace, tests/test_w10b_network_trace.py, holds
# the rest on every path it drives):
#   - Everywhere: each USE of something that can reach outside the process (a load of it: a call, an argument, an
#     annotation, an element of a container), under any alias, through a backend module that re-exports it, by a
#     constant load by name, or of a local name holding one, is counted per function and answered for; a module
#     loaded by a computed name counts as one. Not seen here: a name reached by `getattr` on a module with a
#     computed string, code run by `exec`, a socket a C library opens, a call made through an object the reading
#     cannot type (a parameter nobody annotated), and who calls an answered-for function.
#   - In the action set: nothing but the recorder's handle reaches outside the process. Everything the two modules
#     take from outside themselves is pinned name by name; neither reads a private attribute (so no handle hands back
#     its client); neither loads code by name; each awaits only calls, and every awaited call on a local value is
#     the handle's. Not seen in the action set: a call that is not awaited on a parameter nobody annotated (a sync
#     client a caller handed in), which the census sees where the caller made it.

# What can reach outside the process: these libraries, and these names in libraries that also hold harmless ones.
OUTBOUND = ("httpx", "requests", "urllib.request", "urllib3", "aiohttp", "http.client", "socket", "ssl",
            "websockets", "ftplib", "smtplib", "poplib", "imaplib", "nntplib", "telnetlib", "xmlrpc",
            "socketserver", "SPARQLWrapper", "webbrowser", "pycurl", "paramiko", "chromadb.HttpClient",
            "chromadb.AsyncHttpClient", "asyncio.open_connection", "asyncio.open_unix_connection",
            "asyncio.start_server", "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell",
            "subprocess", "os.system", "os.popen", "os.execl", "os.execle", "os.execlp", "os.execlpe", "os.execv",
            "os.execve", "os.execvp", "os.execvpe", "os.spawnl", "os.spawnle", "os.spawnlp", "os.spawnlpe",
            "os.spawnv", "os.spawnve", "os.spawnvp", "os.spawnvpe", "os.posix_spawn", "os.posix_spawnp",
            "os.startfile", "pty.spawn")
LOADERS = {"__import__", "importlib.import_module", "importlib.__import__"}
# Every tracked backend module that uses one and is not an action, answered for (the spec's establish, D5), with
# its uses counted per function: a use added anywhere is named, and so is a module that is not here.
NOT_ACTIONS = {
    "backend/memory/actions.py": ("the recorder itself: the one place an action is sent",
                                  {"_merged_headers": 1, "recorded_get": 7, "session": 2}),
    "backend/memory/anchor.py": ("anchoring: `block_anchors` is its record (D5)", {"anchor_pending": 1, "stamp": 5}),
    # W10c — the interceptor's forward to its upstream: a `model_exchange` action whose row is committed before
    # `send` is called (`Recorder.exchange_requested`) and whose outcome commits with the exchange's record. It is
    # the recorder's rule kept by another module, because the response is relayed while it arrives.
    "backend/intercept/forward.py": ("the interceptor's forward: its row is committed before it leaves (W10c)",
                                     {"Forwarder.__init__": 3, "Forwarder.aclose": 1, "Forwarder.send": 3,
                                      "Upstream.__init__": 6, "Upstream.aclose": 1, "Upstream.chunks": 1,
                                      # a log filter: it reads a URL's type and sends nothing
                                      "_NoQueryInTheRequestLine.filter": 1}),
    "backend/providers/anthropic.py": ("model calls and probes: the turn's record holds the call", {"AnthropicProvider.health": 3, "AnthropicProvider.list_models": 4,
                                                "AnthropicProvider.stream_pieces": 5}),
    "backend/providers/api_common.py": ("model embeddings and the API's refusals: the turn's record holds the call",
                                        {"OpenAICompatEmbedder.embed": 4, "refusal": 3}),
    "backend/providers/base.py": ("the probes' timeouts: an object, no request", {"health_timeout": 2,
                                                                                  "list_models_timeout": 2}),
    "backend/providers/lmstudio.py": ("model calls and probes: the turn's record holds the call", {"LMStudioProvider.get_embedding": 4, "LMStudioProvider.health": 3,
                                               "LMStudioProvider.list_models": 4, "LMStudioProvider.stream_pieces": 4}),
    "backend/providers/ollama.py": ("model calls and probes: the turn's record holds the call", {"OllamaProvider.get_embedding": 4, "OllamaProvider.health": 3,
                                             "OllamaProvider.inference_environment": 5, "OllamaProvider.list_models": 4,
                                             "OllamaProvider.served_model_digest": 5,
                                             "OllamaProvider.stream_pieces": 6}),
    "backend/providers/openai_compat.py": ("model calls and probes: the turn's record holds the call", {"OpenAICompatProvider.health": 3,
                                                    "OpenAICompatProvider.list_models": 4,
                                                    "OpenAICompatProvider.stream_pieces": 5}),
    "backend/providers/replay_capture.py": ("the record's environment capture: `git` and `nvidia-smi`, local reads "
                                            "once a process", {"_run": 4}),
    "backend/router/swap_manager.py": ("no production caller (the establish; DEAD below)",
                                       {"SwapManager._preload": 2, "SwapManager._unload": 2}),
    "backend/store_lock.py": ("the host's name for the lock's holder: a local read", {"_holder": 1}),
    "backend/vector/embeddings.py": ("no production caller (CLAUDE.md W.1; DEAD below)", {"embed_text": 2}),
}
# Answered for as having no production caller: no other tracked backend module may use these.
DEAD = {"backend/router/swap_manager.py": "backend.router.swap_manager.SwapManager",
        "backend/vector/embeddings.py": "backend.vector.embeddings.embed_text"}
# The action set: the modules that send through the recorder (derived by what their names resolve to). Everything
# they take from outside themselves is here, name by name.
ACTION_SET = {
    "backend/tools/search.py": [
        "__future__.annotations", "backend.memory.actions", "backend.memory.actions.HTTPError",
        "backend.memory.actions.recorded_get", "dataclasses.dataclass", "logging", "logging.getLogger", "os",
        "os.getenv", "re", "re.compile", "typing.Iterable"],
    "backend/kb/seeder.py": [
        "__future__.annotations", "asyncio", "asyncio.run", "backend.kb.gate.BeliefRejected",
        "backend.kb.granularity.Granularity", "backend.kb.granularity.Granularity.TOPIC",
        "backend.kb.source_registry.KBSource", "backend.kb.source_registry.default_registry",
        "backend.kb.source_registry.default_registry.get", "backend.kb.tiered_store.TripleStore",
        "backend.memory.actions", "backend.memory.actions.ActionNotRecorded", "backend.memory.actions.HTTPError",
        "backend.memory.actions.HTTPStatusError", "backend.memory.actions.Session", "backend.memory.actions.session",
        "logging", "logging.getLogger", "typing.Any", "typing.NamedTuple", "urllib.parse.urlsplit",
        "urllib.parse.urlunsplit"],
}
# Builtins that load code or reach a namespace by name: an action module calls none of them.
LOADS_BY_NAME = {"__import__", "exec", "eval", "compile", "globals", "vars", "getattr", "setattr", "delattr"}


def _dotted(node) -> "str | None":
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return ".".join([node.id] + parts[::-1]) if isinstance(node, ast.Name) else None


def _module_of(rel: str) -> str:
    """A tracked path's module name, by its parts (test_derived_writer_guard forbids the separator swap)."""
    parts = PurePosixPath(rel).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _bindings(tree, module: str) -> dict:
    """Each name an import binds (anywhere in the module) -> the dotted name it stands for."""
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                base = ".".join(module.split(".")[:-n.level] + ([n.module] if n.module else []))
            for a in n.names:
                out[a.asname or a.name] = f"{base}.{a.name}"
    return out


def _loads_by_name(func, table) -> bool:
    d = _dotted(func)
    if d is None:
        return False
    root, _, rest = d.partition(".")
    return (table.get(root, root) + ("." + rest if rest else "")) in LOADERS


def _qualified(node, table) -> "str | None":
    """The dotted name a chain stands for, through the module's imports and a constant load by name; `?` for a
    module loaded by a computed name; None for a chain that starts at something else (a call's result)."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        root = table.get(node.id, node.id)
    elif isinstance(node, ast.Call) and _loads_by_name(node.func, table):
        arg = node.args[0] if node.args else None
        root = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else "?"
    else:
        return None
    return ".".join([root] + parts[::-1])


_RESOLVED: dict = {}


def _resolve(qualified: str):
    """The object a qualified name stands for, by import; None when it cannot be had. Only backend names and the
    outbound libraries are imported to resolve a name."""
    if qualified.split(".")[0] not in {"backend"} | {lib.split(".")[0] for lib in OUTBOUND}:
        return None
    if qualified not in _RESOLVED:
        import importlib
        obj, parts = None, qualified.split(".")
        for i in range(len(parts), 0, -1):
            try:
                obj = importlib.import_module(".".join(parts[:i]))
            except Exception:
                continue
            try:
                for attr in parts[i:]:
                    obj = getattr(obj, attr)
            except AttributeError:
                obj = None
            break
        _RESOLVED[qualified] = obj
    return _RESOLVED[qualified]


def _origin(qualified: str) -> str:
    """Where a qualified name's object comes from: a backend name by import (a re-export is seen for what it is),
    any other its own name."""
    if qualified.split(".")[0] != "backend":
        return qualified
    import types
    obj = _resolve(qualified)
    if obj is None:
        return qualified
    if isinstance(obj, types.ModuleType):
        return obj.__name__
    mod, name = getattr(obj, "__module__", None), getattr(obj, "__qualname__", None) or getattr(obj, "__name__", "")
    return f"{mod}.{name}" if isinstance(mod, str) else qualified


def _outbound(qualified: "str | None") -> bool:
    """Can the object reach outside the process? An exception class cannot: catching one sends nothing."""
    if not qualified:
        return False
    if qualified.startswith("?"):
        return True
    o = _origin(qualified)
    if not any(o == lib or o.startswith(lib + ".") for lib in OUTBOUND):
        return False
    obj = _resolve(qualified)
    return not (isinstance(obj, type) and issubclass(obj, BaseException))


def _enclosing(tree) -> dict:
    """Each node -> the dotted name of the function (and class) it is in, `<module>` at the top."""
    out = {}

    def visit(node, name):
        for child in ast.iter_child_nodes(node):
            inner = name
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = f"{name}.{child.name}" if name != "<module>" else child.name
            out[child] = inner
            visit(child, inner)
    visit(tree, "<module>")
    return out


def _heads(tree):
    """Each chain head that is read: a name not under an attribute, the outermost attribute of a chain, and a
    string annotation parsed as the expression it names."""
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    for n in ast.walk(tree):
        if isinstance(n, (ast.Name, ast.Attribute)) and isinstance(n.ctx, ast.Load):
            p = parents.get(n)
            if not (isinstance(p, ast.Attribute) and p.value is n):
                yield n, n
        elif isinstance(n, ast.arg) and isinstance(n.annotation, ast.Constant) and isinstance(n.annotation.value,
                                                                                               str):
            try:
                yield n, ast.parse(n.annotation.value, mode="eval").body
            except SyntaxError:
                pass


def outbound_uses(source: str, module: str) -> dict:
    """{function: n}: each use in `source` of something that can reach outside the process, or of a local name
    that holds one, by function."""
    return dict(_outbound_uses(source, module))


@functools.lru_cache(maxsize=None)
def _outbound_uses(source: str, module: str) -> tuple:
    tree = ast.parse(source)
    table, held = _bindings(tree, module), set()
    for n in ast.walk(tree):                 # a constant load by name binds like an import: `h = __import__("x")`
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and _loads_by_name(n.value.func, table):
            q = _qualified(n.value, table)
            table.update({t.id: q for t in n.targets if isinstance(t, ast.Name) and q})

    def is_outbound(expr) -> bool:
        """The expression IS something that can reach out: a library name, a name holding one, a call on one (a
        client made, a stream opened), or a choice between such. An awaited result is not; nor is a container."""
        if isinstance(expr, (ast.Name, ast.Attribute)):
            return _outbound(_qualified(expr, table)) or (_dotted(expr) or ".").split(".")[0] in held
        if isinstance(expr, ast.Call):
            return is_outbound(expr.func) or (_loads_by_name(expr.func, table)
                                              and _outbound(_qualified(expr, table)))
        if isinstance(expr, ast.BoolOp):
            return any(is_outbound(v) for v in expr.values)
        if isinstance(expr, ast.IfExp):
            return is_outbound(expr.body) or is_outbound(expr.orelse)
        return False

    for _ in range(4):                       # to a fixed point: `c = client` after `client = httpx.AsyncClient()`
        for n in ast.walk(tree):
            if isinstance(n, (ast.With, ast.AsyncWith)):
                for item in n.items:
                    if item.optional_vars is not None and is_outbound(item.context_expr):
                        held |= {x.id for x in ast.walk(item.optional_vars) if isinstance(x, ast.Name)}
            elif isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None and is_outbound(n.value):
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                    held |= {x.id for x in ast.walk(t) if isinstance(x, ast.Name)}
            elif isinstance(n, ast.arg) and n.annotation is not None:
                ann = n.annotation
                if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
                    try:
                        ann = ast.parse(ann.value, mode="eval").body
                    except SyntaxError:
                        continue
                if is_outbound(ann):
                    held.add(n.arg)       # a parameter typed as a client holds one
    where, counts = _enclosing(tree), {}
    for node, head in _heads(tree):
        if is_outbound(head) or (isinstance(head, ast.Name) and head.id in held):
            counts[where[node]] = counts.get(where[node], 0) + 1
    for n in ast.walk(tree):                 # a load by name: by a computed name, or of an outbound library
        if isinstance(n, ast.Call) and _loads_by_name(n.func, table) and _outbound(_qualified(n, table)):
            counts[where[n]] = counts.get(where[n], 0) + 1
    return tuple(sorted(counts.items()))


@functools.lru_cache(maxsize=None)
def _uses_of(source: str, module: str, target: str) -> list:
    """Lines where `source` uses the object `target` names, through its imports (a re-export included)."""
    tree = ast.parse(source)
    table, want = _bindings(tree, module), _resolve(target)
    out = []
    for node, head in _heads(tree):
        q = _qualified(head, table)
        if q and q.split(".")[0] == "backend":
            d = q.split(".")
            for i in range(len(d), 1, -1):         # the head or any prefix of it: `m.embed_text.__wrapped__`
                if _resolve(".".join(d[:i])) is want:
                    out.append(getattr(node, "lineno", 0))
                    break
    return tuple(out)


def external_names(source: str, module: str) -> list:
    """What a module takes from outside itself: each import, each name read on what it imported, and each builtin
    it calls that loads code or reaches a namespace by name."""
    tree = ast.parse(source)
    table, out = _bindings(tree, module), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            out |= {f"{n.module}.*" if a.name == "*" else table[a.asname or a.name] for a in n.names}
        elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in table:
            out.add(f"{table[n.value.id]}.{n.attr}")
        elif isinstance(n, ast.Name) and n.id in LOADS_BY_NAME:
            out.add(f"builtins.{n.id}")
    return sorted(out)


@functools.lru_cache(maxsize=None)
def _sends_through_the_recorder(source: str, module: str) -> bool:
    """The module takes the recorder's `session` or `recorded_get`, by whatever name or path it reaches them."""
    tree = ast.parse(source)
    table = _bindings(tree, module)
    senders = (act.session, act.recorded_get)
    for _node, head in _heads(tree):
        q = _qualified(head, table)
        if q and q.split(".")[0] == "backend":
            d = q.split(".")
            if any(_resolve(".".join(d[:i])) in senders for i in range(len(d), 1, -1)):
                return True
    return False


# The private names an action module may read: each is a string, and nothing is reached from a string.
NAMES_ONLY = {"__name__", "__qualname__", "__doc__"}


def private_reads(source: str) -> list:
    """Lines where `source` reads or writes an attribute whose name begins with an underscore (a name in
    `NAMES_ONLY` apart)."""
    return sorted(n.lineno for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Attribute)
                  and n.attr.startswith("_") and n.attr not in NAMES_ONLY)


def _own_nodes(fn):
    """The nodes of a function, not descending into a function or class defined inside it."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(n))


def unhandled_awaits(source: str, module: str) -> list:
    """Lines where an action module awaits anything but a call, or a call on a local value that is not the
    recorder's handle: a name `session()` bound, a parameter annotated `Session`, or a name assigned from one."""
    tree = ast.parse(source)
    table = _bindings(tree, module)
    recorder = {name for name, q in table.items() if q == "backend.memory.actions"}
    methods = {m for m, v in vars(act.Session).items() if not m.startswith("_") and callable(v)}
    own = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    lines = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        handles = {a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
                   if a.annotation is not None and _dotted(a.annotation) in {f"{r}.Session" for r in recorder}}
        for _ in range(3):
            for n in _own_nodes(fn):
                if isinstance(n, ast.AsyncWith):
                    handles |= {i.optional_vars.id for i in n.items if isinstance(i.optional_vars, ast.Name)
                                and isinstance(i.context_expr, ast.Call)
                                and _dotted(i.context_expr.func) in {f"{r}.session" for r in recorder}}
                elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Name) and n.value.id in handles:
                    handles |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        for n in _own_nodes(fn):
            if isinstance(n, ast.Await) and not isinstance(n.value, ast.Call):
                lines.append(n.lineno)        # a coroutine made elsewhere and awaited by name: awaits only calls
                continue
            if not (isinstance(n, ast.Await) and isinstance(n.value, ast.Call)):
                continue
            f, d = n.value.func, _dotted(n.value.func)
            if d is not None and (d.split(".")[0] in table or (isinstance(f, ast.Name) and f.id in own)):
                continue                      # a name it took (pinned above) or a function of its own
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in handles \
                    and f.attr in methods:
                continue                      # the recorder's handle
            lines.append(n.lineno)
    return sorted(lines)


def unanswered(files: dict) -> dict:
    """{module: [what is not answered for]} over `files` ({repo path: source})."""
    out = {}
    for rel, src in files.items():
        mod, problems = _module_of(rel), []
        uses, pinned = outbound_uses(src, mod), NOT_ACTIONS.get(rel, ("", {}))[1]
        if uses != pinned:
            problems.append(f"uses of what reaches outside the process {uses}; answered for: {pinned}")
        for dead_rel, target in DEAD.items():
            if rel != dead_rel and (lines := _uses_of(src, mod, target)):
                problems.append(f"uses {target}, answered for as having no production caller, at {lines}")
        if rel in ACTION_SET or _sends_through_the_recorder(src, mod):
            names = external_names(src, mod)
            if names != ACTION_SET.get(rel):
                problems.append(f"takes {sorted(set(names) - set(ACTION_SET.get(rel, [])))} and no longer "
                                f"{sorted(set(ACTION_SET.get(rel, [])) - set(names))}, unanswered for")
            if (lines := private_reads(src)):
                problems.append(f"reads a private attribute at lines {lines}")
            if (lines := unhandled_awaits(src, mod)):
                problems.append(f"awaits a call on a value that is not the recorder's at lines {lines}")
        if problems:
            out[rel] = problems
    return out


def _backend_sources() -> dict:
    from tests._tracked import tracked_backend
    files = {}
    for p in tracked_backend():
        path = Path(p) if Path(p).is_absolute() else REPO / p
        if path.suffix == ".py":
            files[path.relative_to(REPO).as_posix()] = path.read_text(encoding="utf-8")
    return files


def test_t4_every_use_of_the_network_or_a_process_is_the_recorders_or_answered_for():
    files = _backend_sources()
    assert len(files) > 50 and set(ACTION_SET) <= set(files) and set(DEAD) <= set(files), "non-vacuity"
    assert unanswered(files) == {}, unanswered(files)
    gone = sorted(set(NOT_ACTIONS) - set(files))
    assert not gone, f"answered for, but gone: {gone}"
    senders = sorted(rel for rel, src in files.items() if rel != "backend/memory/actions.py"
                     and _sends_through_the_recorder(src, _module_of(rel)))
    assert senders == sorted(ACTION_SET), f"the action set, derived: {senders}"
    for rel, target in DEAD.items():
        assert _resolve(target) is not None and target.rsplit(".", 1)[-1] in files[rel], f"non-vacuity: {target}"
    print(f"\nT4 census: {sum(sum(c.values()) for _r, c in NOT_ACTIONS.values())} uses in {len(NOT_ACTIONS)} "
          f"modules answered for; the action set {senders} reaches out only through the recorder")


SEARCH, SEEDER, ANCHOR = "backend/tools/search.py", "backend/kb/seeder.py", "backend/memory/anchor.py"
LOOP = "backend/loop/agentic_loop.py"
STAMP_SEND = '        resp = await client.post(url, content=request, headers={"Content-Type": "application/timestamp-query"})\n'


def _after_send(text: str):
    return lambda s: s.replace(STAMP_SEND, STAMP_SEND + text)


def _appended(text: str):
    return lambda s: s + "\n\n" + text


# Each plant reaches outside the process past the recorder, spelled as the guard's source never spells one: the
# verifier's (MF-1), the first delta check's (DC1-MF1 and DC1-MF2), and the class around them.
PLANTS = {
    # in the action set
    "a coroutine awaited by name": (SEARCH, _appended(
        "async def _q(client):\n    pending = client.get('http://x')\n    return await pending\n")),
    "an unannotated parameter, aliased": (SEARCH, _appended(
        "async def _q(client):\n    c = client\n    return await c.get('http://searx/search')\n")),
    "the standard library's opener": (SEARCH, _appended(
        "def _q():\n    import urllib.request\n    return urllib.request.urlopen('http://searx/search')\n")),
    "a module loaded by a computed name": (SEARCH, _appended("def _q():\n    return __import__('ht' + 'tpx')\n")),
    "a library no list names": (SEEDER, _appended("import grpc  # noqa\n")),
    "a library through the recorder": (SEEDER, _appended(
        "async def _q():\n    return await _actions.httpx.AsyncClient().get('http://x')\n")),
    "everything the recorder holds": (SEEDER, _appended("from backend.memory.actions import *  # noqa\n")),
    "the handle's client, awaited elsewhere": (SEEDER, _appended(
        "async def _q(client: _actions.Session):\n    pending = client._client.get('http://x')\n"
        "    return await pending\n")),
    "the handle's client, streamed": (SEEDER, _appended(
        "async def _q(client: _actions.Session):\n    async with client._client.stream('GET', 'http://x') as r:\n"
        "        return r.status_code\n")),
    "the handle's client, passed as the handle": (SEEDER, _appended(
        "async def _own(c: _actions.Session):\n    return await c.get('http://x', kind='seed_x')\n\n\n"
        "async def _q(client: _actions.Session):\n    return await _own(client._client)\n")),
    "an exception class's globals": (SEEDER, _appended(
        "def _q():\n    return _actions.HTTPError.__init__.__globals__['httpx']\n")),
    "the recorder reached through an action module": ("backend/loop/agentic_loop.py", _appended(
        "from backend.tools import search as _s\n\n\nasync def _q(u):\n    async with _s._actions.session("
        "store=None, trigger=None) as c:\n        return await c.get(u, kind='x')\n")),
    # in a module answered for
    "a client under another name": (ANCHOR, _appended(
        "async def _q(u):\n    import httpx as h\n    async with h.AsyncClient() as http:\n        return await "
        "http.get(u)\n")),
    "a new call in an answered function": (ANCHOR, _after_send("        side = client\n        await side.patch("
                                                               "url, content=request)\n")),
    "a computed attribute": (ANCHOR, _after_send('        await getattr(client, "patch")(url, content=request)\n')),
    "a method in a container": (ANCHOR, _after_send("        fs = [client.patch]\n        await fs[0](url)\n")),
    "a helper's parameter": (ANCHOR, lambda s: _after_send("        await _also(client, url)\n")(s)
                             + "\n\nasync def _also(c, u):\n    return await c.patch(u)\n"),
    "a parameter typed as a client": (ANCHOR, _appended(
        "async def _q(c: httpx.AsyncClient, u):\n    return await c.patch(u)\n")),
    "a thread": (ANCHOR, _after_send("        import asyncio\n        await asyncio.to_thread(httpx.get, url)\n")),
    "a new function in an answered module": (ANCHOR, _appended(
        "async def _ping(u):\n    import httpx\n    async with httpx.AsyncClient() as beacon:\n        return await "
        "beacon.options(u)\n")),
    # in a module that sends nothing
    "a re-export": (LOOP, _appended("from backend.providers.ollama import httpx as transport\n\n\nasync def "
                                    "_beacon(u):\n    return await transport.get(u)\n")),
    "a constant load by name": (LOOP, _appended(
        "async def _beacon(u):\n    h = __import__('httpx')\n    return await h.AsyncClient().get(u)\n")),
    "a computed load by name": (LOOP, _appended(
        "import importlib\n\n\ndef _beacon():\n    return importlib.import_module('ht' + 'tpx')\n")),
    "another protocol": (LOOP, _appended(
        "import xmlrpc.client\n\n\ndef _beacon(u):\n    return xmlrpc.client.ServerProxy(u).ping()\n")),
    "a raw connection": (LOOP, _appended(
        "import asyncio\n\n\nasync def _beacon(h):\n    return await asyncio.open_connection(h, 80)\n")),
    "a child process": (LOOP, _appended(
        "import subprocess\n\n\ndef _beacon(u):\n    return subprocess.run(['curl', u])\n")),
    "a vector store's client": (LOOP, _appended(
        "import chromadb\n\n\ndef _beacon(h):\n    return chromadb.HttpClient(host=h)\n")),
    "a path answered for as dead": (LOOP, _appended(
        "from backend.vector import embeddings as _e\n\n\nasync def _q(t):\n    return await _e.embed_text(t)\n")),
}


@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_t4b_a_request_past_the_recorder_is_named(plant):
    files = _backend_sources()
    rel, edit = PLANTS[plant]
    planted = edit(files[rel])
    assert planted != files[rel], "non-vacuity: the plant is in"
    ast.parse(planted)
    assert list(unanswered({**files, rel: planted})) == [rel], unanswered({**files, rel: planted})


# ---------- T5, T6 (A3): fail closed ----------

def test_t5_an_insert_that_fails_sends_nothing_and_says_so(console, monkeypatch):
    svc, client, bearer, searx = console

    def refuses(row):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(svc.lineage, "action_requested", refuses)
    frames = _searching_turn(client, bearer)
    said = [d for e, d in frames if e == "kb_pressure_alert" and d.get("action") == "not_recorded"]
    assert len(said) == 1 and "it was not sent" in said[0]["detail"], frames
    assert not any(e == "rag_injected" for e, _d in frames) and searx.requests == [], "nothing left"
    resp = client.post("/search/execute", headers=bearer, json={"query": QUERY, "confirmed": True})
    assert resp.status_code == 503 and resp.json()["refused"] == "action_not_recorded", resp.text
    resp = client.post("/kb/seed/conceptnet", headers=bearer, json={"concepts": ["sleep"], "confirmed": True})
    assert resp.status_code == 503 and resp.json()["refused"] == "action_not_recorded", resp.text
    assert searx.requests == [] and _rows(svc) == []


def test_t6_an_outcome_that_fails_withholds_the_result_and_the_row_says_only_requested(console, monkeypatch):
    svc, client, bearer, searx = console

    def refuses(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(svc.lineage, "action_outcome", refuses)
    resp = client.post("/search/execute", headers=bearer, json={"query": QUERY, "confirmed": True})
    assert resp.status_code == 503 and "withheld" in resp.json()["detail"], resp.text
    assert len(searx.requests) == 1, "declared: the request left"
    assert [(r["state"], r["record_hash"], r["response_hash"]) for r in _rows(svc)] == [("requested", None, None)]
    frames = _searching_turn(client, bearer)
    assert not any(e == "rag_injected" for e, _d in frames), "the turn does not use a result it could not record"
    assert any(e == "kb_pressure_alert" and d.get("action") == "not_recorded" for e, d in frames)


# ---------- T7 (A5): no secret in a record ----------

def test_t7_a_key_header_is_recorded_by_name_and_fingerprint_only(console, monkeypatch):
    svc, client, bearer, searx = console
    from backend.tools.search import WebSearchTool
    monkeypatch.setattr(WebSearchTool, "DEFAULT_HEADERS", {**WebSearchTool.DEFAULT_HEADERS, "X-Api-Key": SECRET})
    assert client.post("/search/execute", headers=bearer, json={"query": QUERY, "confirmed": True}).status_code == 200
    assert searx.requests[0]["headers"].get("X-Api-Key") == SECRET, "non-vacuity: the key was sent"
    row = _rows(svc)[0]
    assert json.loads(row["credentials_json"]) == [
        {"header": "x-api-key", "fingerprint": hashlib.sha256(SECRET.encode()).hexdigest()[:16]}], row
    assert SECRET not in json.dumps(row), "the key's value is in no field"


# ---------- T8 (D2): one writer ----------

def action_writers(source: str) -> list[str]:
    """The functions in `source` holding a SQL string that writes `action_records`."""
    out = []
    for fn in ast.walk(ast.parse(source)):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and "action_records" in n.value \
                        and any(w in n.value.upper() for w in ("INSERT", "UPDATE", "DELETE", "REPLACE")):
                    out.append(fn.name)
                    break
    return out


WRITERS = {"action_requested", "action_outcome", "mark_actions_sealed"}


def test_t8_the_table_is_written_by_the_recorders_two_statements_and_no_other():
    from tests._tracked import tracked_backend
    found = {}
    for p in tracked_backend():
        path = Path(p) if Path(p).is_absolute() else REPO / p
        if path.suffix == ".py":
            names = action_writers(path.read_text(encoding="utf-8"))
            if names:
                found[path.relative_to(REPO).as_posix()] = sorted(names)
    assert found == {"backend/memory/lineage.py": sorted(WRITERS)}, found
    planted = "def forget(conn, i):\n    conn.execute('DELETE FROM action_records WHERE action_id = ?', (i,))\n"
    assert action_writers(planted) == ["forget"], "a second writer is named"


# ---------- T9 (D8) ----------

def test_t9_the_turns_record_lists_its_actions(console):
    svc, client, bearer, _searx = console
    done = next(d for e, d in _searching_turn(client, bearer) if e == "done")
    listed = client.get(f"/lineage/{done['lineage_id']}", headers=bearer).json()["actions"]
    row = _rows(svc)[0]
    assert listed == [{"action_id": row["action_id"], "kind": "web_search", "target": row["target"],
                       "evidence_class": "runtime", "state": "completed"}], listed


# ---------- T10 to T14 (part 2: sealed, proved, verified offline) ----------

def _sealed_action(console):
    svc, client, bearer, _searx = console
    assert client.post("/search/execute", headers=bearer, json={"query": QUERY, "confirmed": True}).status_code == 200
    action_id = _rows(svc)[0]["action_id"]
    with RigClock().installed():
        asyncio.run(svc.kairos.maybe_seal_governance(force=True))
    return action_id


def test_t10_the_seal_covers_each_recorded_outcome_once_and_a_requested_row_waits(console, monkeypatch):
    svc, client, bearer, _searx = console
    real = svc.lineage.action_outcome
    monkeypatch.setattr(svc.lineage, "action_outcome", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    client.post("/search/execute", headers=bearer, json={"query": QUERY, "confirmed": True})
    monkeypatch.setattr(svc.lineage, "action_outcome", real)
    assert client.post("/search/execute", headers=bearer, json={"query": QUERY, "confirmed": True}).status_code == 200
    with RigClock().installed():
        block = asyncio.run(svc.kairos.maybe_seal_governance(force=True))
    rows = {r["state"]: r for r in _rows(svc)}
    assert rows["completed"]["sealed_block"] == block.index and rows["requested"]["sealed_block"] is None, rows
    seal = block.fingerprint["action_seal"]
    assert seal["action_ids"] == [rows["completed"]["action_id"]] and seal["leaves"] == [
        rows["completed"]["record_hash"]] and block.roots["action"]["size"] == 1, seal
    with RigClock().installed():
        asyncio.run(svc.kairos.maybe_seal_governance(force=True))
    assert _rows(svc)[0]["sealed_block"] in (block.index, None) and svc.lineage.unsealed_actions() == [], \
        "a sealed action is not sealed again"


def _offline(tmp_path, receipt, identity, name="action.json") -> tuple[int, str]:
    where = tmp_path / f"stranger-{len(list(tmp_path.glob('stranger-*')))}"
    where.mkdir()
    (where / name).write_text(json.dumps(receipt), encoding="utf-8")
    (where / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    proc = _proc.run([sys.executable, "-I", str(REPO / "scripts" / "nexus_verify.py"), "--action", name,
                      "--identity", "identity.json"], cwd=where, env=env, timeout=120, capture_output=True,
                     text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout + proc.stderr


def test_t11_an_actions_receipt_verifies_offline_from_an_empty_folder(console, tmp_path):
    svc, client, bearer, _searx = console
    action_id = _sealed_action(console)
    receipt = client.get(f"/actions/{action_id}", headers=bearer)
    assert receipt.status_code == 200, receipt.text
    code, out = _offline(tmp_path, receipt.json(), client.get("/identity").json())
    assert code == 0 and "nexus-verify: VERIFIED" in out and "runtime web_search GET" in out, out


def test_t12_any_part_of_the_receipt_changed_fails_by_name(console):
    svc, client, bearer, _searx = console
    good = client.get(f"/actions/{_sealed_action(console)}", headers=bearer).json()
    keys = client.get("/identity").json()["keys"]
    assert nv.verify_action(good, keys)["verified"], "non-vacuity"

    def names(changed):
        return [f.split(":", 1)[0] for f in nv.verify_action(changed, keys)["failures"]]
    for change, expect in (
            (lambda r: r["fields"].__setitem__("status", "500"), "action_mismatch"),
            (lambda r: r["fields"].__setitem__("identity_commitment", "00" * 32), "action_mismatch"),
            (lambda r: r["disclosed"]["accountable_identity"].__setitem__("value", "someone else"),
             "disclosure_mismatch"),
            (lambda r: r.__setitem__("record_hash", "00" * 32), "action_mismatch"),
            (lambda r: r["block"]["header"].__setitem__("timestamp", 0.0), "block_hash_mismatch"),
            (lambda r: r["signatures"][0].__setitem__("value", "00" * 64), "signature_invalid")):
        changed = copy.deepcopy(good)
        change(changed)
        assert expect in names(changed), (expect, names(changed))
    assert "key_unpinned" in [f.split(":", 1)[0] for f in nv.verify_action(good)["failures"]]


def test_t13_the_receipt_route_refuses_by_name_and_declares_itself_per_person(console, monkeypatch):
    svc, client, bearer, _searx = console
    assert client.get("/actions/no-such", headers=bearer).json()["refused"] == "no_action"
    assert client.post("/search/execute", headers=bearer, json={"query": QUERY, "confirmed": True}).status_code == 200
    unsealed = client.get(f"/actions/{_rows(svc)[0]['action_id']}", headers=bearer)
    assert unsealed.status_code == 409 and unsealed.json()["refused"] == "unsealed", unsealed.text
    monkeypatch.setattr(svc.lineage, "action_outcome", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    client.post("/search/execute", headers=bearer, json={"query": QUERY, "confirmed": True})
    requested = next(r for r in _rows(svc) if r["state"] == "requested")
    incomplete = client.get(f"/actions/{requested['action_id']}", headers=bearer)
    assert incomplete.status_code == 409 and incomplete.json()["refused"] == "incomplete", incomplete.text
    monkeypatch.undo()
    with RigClock().installed():
        asyncio.run(svc.kairos.maybe_seal_governance(force=True))
    sealed = client.get(f"/actions/{_rows(svc)[0]['action_id']}", headers=bearer).json()
    assert sealed["per_person"]["declared"] is True and "accountable_identity" in sealed["per_person"]["field"]
    assert client.get(f"/actions/{_rows(svc)[0]['action_id']}", headers={"Authorization": ""}).status_code == 401, \
        "gated (the suite's client adds the token unless a request sends its own header)"


def test_t14_the_verifiers_copy_of_the_hashed_fields_is_the_recorders():
    assert nv.ACTION_FIELDS == act.HASHED_FIELDS


# ---------- T15 to T17 (part 3: actions a client reports) ----------

def _reporting_runtime(tmp_path):
    from backend.mcp.mcp_server import build_server
    from tests.test_mcp2_build import _call, _runtime
    rt = _runtime(tmp_path / "mcp", owner="alice")
    srv = build_server(rt, with_lifespan=False)
    r = _call(srv, "nexus_retrieve", question="tell me about python")
    rec = _call(srv, "nexus_record", retrieval_id=r["retrieval_id"], answer="Python is a programming_language.")
    return rt, srv, _call, rec["lineage_id"]


def test_t15_a_reported_action_is_client_reported_never_runtime_and_verifies_saying_so(tmp_path):
    from backend.memory import credentials as cr
    rt, srv, call, lineage_id = _reporting_runtime(tmp_path)
    before = rt.services.lineage.action_counts()
    out = call(srv, "nexus_record_action", lineage_id=lineage_id, kind="file_write", target="notes/todo.md",
               request_hash=hashlib.sha256(b"write notes/todo.md").hexdigest(), status="ok")
    after = rt.services.lineage.action_counts()
    assert out["evidence_class"] == "client_reported" and out["state"] == "completed", out
    assert after["runtime"] == before["runtime"] and after["client_reported"]["completed"] == \
        before["client_reported"]["completed"] + 1, (before, after)
    row = rt.services.lineage.action(out["action_id"])
    assert (row["lineage_id"], row["accountable_identity"], row["method"]) == (lineage_id, "alice", "reported"), row
    asyncio.run(rt.services.kairos.maybe_seal_governance(force=True))
    receipt = cr.action_receipt(rt.services.lineage, rt.services.archive_chain, out["action_id"],
                                key_path=rt.services.instance_key_path)
    keys = cr._ik.identity_document(cr._ik.load_or_create(rt.services.instance_key_path,
                                                          chain=rt.services.archive_chain),
                                    rt.services.archive_chain)["keys"]
    got = nv.verify_action(receipt, keys)
    assert got["verified"] and any("the client reported it" in n for n in got["notes"]), got


def test_t16_a_report_against_no_turn_or_with_no_hash_is_refused_and_writes_nothing(tmp_path):
    rt, srv, call, lineage_id = _reporting_runtime(tmp_path)
    good = hashlib.sha256(b"x").hexdigest()
    for args, reason in (({"lineage_id": "no-such", "request_hash": good}, "unknown_lineage_id"),
                         ({"lineage_id": lineage_id, "request_hash": "not a hash"}, "bad_request_hash"),
                         ({"lineage_id": lineage_id, "request_hash": good.upper()}, "bad_request_hash")):
        with pytest.raises(Exception, match=reason):
            call(srv, "nexus_record_action", kind="file_write", target="notes/todo.md", status="ok", **args)
    with pytest.raises(Exception, match="bad_kind"):
        call(srv, "nexus_record_action", lineage_id=lineage_id, kind="", target="t", request_hash=good, status="ok")
    assert rt.services.lineage.conn.execute("SELECT COUNT(*) AS n FROM action_records").fetchone()["n"] == 0


def test_t17b_the_instructions_name_the_id_the_report_takes_from_the_tool_that_returns_it():
    """The verifier's SF-10: the instructions sent clients to "the retrieval's lineage_id", which nexus_retrieve
    does not return; the id a report takes is the one nexus_record returns."""
    from backend.mcp import mcp_server as ms
    from backend.mcp.instructions import INSTRUCTIONS
    sentence = next(x for x in INSTRUCTIONS.replace("\n", " ").split(". ") if "nexus_record_action" in x)
    returns = {name: "lineage_id" in model.model_fields for name, model in
               (("nexus_retrieve", ms.RetrieveOut), ("nexus_record", ms.RecordOut))}
    assert returns == {"nexus_retrieve": False, "nexus_record": True}, returns
    assert "lineage_id nexus_record returned" in sentence and "retriev" not in sentence, sentence


def test_t17_the_coverage_block_says_nexus_sees_only_the_actions_it_is_told(tmp_path):
    from backend.mcp import coverage as cov
    rt, srv, call, _lineage_id = _reporting_runtime(tmp_path)
    status = call(srv, "nexus_status")
    assert status["coverage"]["actions_sentence"] == cov.ACTIONS_SENTENCE
    assert "only as a client reports them" in cov.ACTIONS_SENTENCE and "sees none of them happen" in \
        cov.ACTIONS_SENTENCE


# ---------- T18 to T23 (part 4: signed outbound requests, built switched off) ----------

def _received_url(searx) -> str:
    req = searx.requests[-1]
    return f"http://{req['headers']['Host']}{req['path']}"


@pytest.mark.parametrize("setting", [None, "off"])
def test_t18_signing_off_by_default_sends_no_signature(console, monkeypatch, setting):
    svc, client, bearer, searx = console
    if setting is None:
        monkeypatch.delenv("NEXUS_SIGN_REQUESTS", raising=False)          # the shipped default
    else:
        monkeypatch.setenv("NEXUS_SIGN_REQUESTS", "off")                   # the rollback, written out
    assert client.post("/search/execute", headers=bearer, json={"query": QUERY, "confirmed": True}).status_code == 200
    sent = {k.lower() for k in searx.requests[-1]["headers"]}
    assert "signature" not in sent and "signature-input" not in sent, sent
    assert (_rows(svc)[0]["signature"], _rows(svc)[0]["signature_key_id"]) == (None, None)


def test_t19_a_signed_request_verifies_as_received_against_the_published_key(console, monkeypatch):
    from backend.memory import http_signatures as hs
    svc, client, bearer, searx = console
    monkeypatch.setenv("NEXUS_SIGN_REQUESTS", "on")
    assert client.post("/search/execute", headers=bearer, json={"query": QUERY, "confirmed": True}).status_code == 200
    received = searx.requests[-1]["headers"]
    identity = client.get("/identity").json()
    public_key = bytes.fromhex(identity["public_key"])
    assert hs.verify_request(public_key, method="GET", url=_received_url(searx), headers=received)
    label, covered, params = hs.parse_signature_input(received["Signature-Input"])
    assert (label, covered) == ("nexus", hs.COVERED) and f'keyid="{identity["key_id"]}"' in params, params
    row = _rows(svc)[0]
    assert received["Signature"] == f"nexus=:{row['signature']}:" and row["signature_key_id"] == identity["key_id"]
    tampered = dict(received, **{"Signature-Input": received["Signature-Input"].replace("created=", "created=1")})
    assert not hs.verify_request(public_key, method="GET", url=_received_url(searx), headers=tampered)
    assert not hs.verify_request(public_key, method="GET", url=_received_url(searx) + "&x=1", headers=received)


RFC9421_KEY = (b"-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIJ+DYvh6SEqVTm50DFtMDoQikTmiCqirVv9mWG9qfSnF\n"
               b"-----END PRIVATE KEY-----\n")            # RFC 9421 Appendix B.1.4, test-key-ed25519
RFC9421_B26 = "wqcAqbmYJ2ji2glfAMaRy4gruYYnx2nEFN2HN6jrnDnQCK1u02Gb04v9EDgwUPiu4A0w6vuQv5lIp5WPpBKRCw=="


def test_t20_rfc_9421s_ed25519_vector_reproduces():
    import base64
    from cryptography.hazmat.primitives import serialization
    from backend.memory import http_signatures as hs
    key = serialization.load_pem_private_key(RFC9421_KEY, password=None)
    covered = ("date", "@method", "@path", "@authority", "content-type", "content-length")
    params = hs.signature_params(covered, created=1618884473, keyid="test-key-ed25519")
    headers = {"Date": "Tue, 20 Apr 2021 02:07:55 GMT", "Content-Type": "application/json", "Content-Length": "18"}
    base = hs.signature_base(covered, params, method="POST", url="https://example.com/foo?param=Value&Pet=dog",
                             headers=headers)
    assert base64.b64encode(key.sign(base)).decode("ascii") == RFC9421_B26
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    assert hs.verify_request(public, method="POST", url="https://example.com/foo?param=Value&Pet=dog",
                             headers={**headers, "Signature-Input": f"sig-b26={params}",
                                      "Signature": f"sig-b26=:{RFC9421_B26}:"})


def test_t21_the_lookup_returns_the_sealed_action_behind_a_signature_and_nothing_else(console, monkeypatch):
    svc, client, bearer, searx = console
    monkeypatch.setenv("NEXUS_SIGN_REQUESTS", "on")
    assert client.post("/search/execute", headers=bearer, json={"query": QUERY, "confirmed": True}).status_code == 200
    signature = _rows(svc)[0]["signature"]
    open_ = {"Authorization": ""}
    early = client.get("/identity/signed-action", params={"signature": signature}, headers=open_)
    assert early.status_code == 409 and early.json()["refused"] == "unsealed", early.text
    with RigClock().installed():
        asyncio.run(svc.kairos.maybe_seal_governance(force=True))
    found = client.get("/identity/signed-action", params={"signature": signature}, headers=open_)
    assert found.status_code == 200, found.text
    receipt = found.json()
    assert set(receipt) == {"receipt", "version", "action_id", "fields", "record_hash", "leaf_index", "tree_size",
                            "path", "block", "signatures", "keys", "anchors"}, sorted(receipt)
    got = nv.verify_action(receipt, client.get("/identity").json()["keys"])
    assert got["verified"] and any("committed, not disclosed" in n for n in got["notes"]), got
    assert QUERY not in json.dumps(receipt), "the turn's content is not in it"
    assert "accountable" not in json.dumps(receipt), "an open surface names no person (A-2)"
    gated = client.get(f"/actions/{receipt['action_id']}", headers=bearer).json()
    assert gated["fields"] == receipt["fields"] and "accountable_identity" in gated["disclosed"], gated
    assert nv.verify_action(gated, client.get("/identity").json()["keys"])["verified"], "the disclosure opens it"
    missing = client.get("/identity/signed-action", params={"signature": "AAAA"}, headers=open_)
    assert missing.status_code == 404 and missing.json()["refused"] == "no_action"


def test_t22_signing_on_with_no_key_to_sign_sends_nothing(console, monkeypatch):
    svc, client, bearer, searx = console
    monkeypatch.setenv("NEXUS_SIGN_REQUESTS", "on")

    def no_key(**_):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(svc.lineage, "request_signer", no_key)
    resp = client.post("/search/execute", headers=bearer, json={"query": QUERY, "confirmed": True})
    assert resp.status_code == 503 and "could not be signed" in resp.json()["detail"], resp.text
    assert searx.requests == [] and _rows(svc) == []


def test_t23_an_unknown_signing_setting_reads_off_and_is_declared(console, monkeypatch):
    svc, client, bearer, searx = console
    monkeypatch.setenv("NEXUS_SIGN_REQUESTS", "yes please")
    signing = client.get("/cortex/stats").json()["actions"]["signing"]
    assert signing["mode"] == "off" and "unknown NEXUS_SIGN_REQUESTS" in signing["declared"], signing
    assert client.post("/search/execute", headers=bearer, json={"query": QUERY, "confirmed": True}).status_code == 200
    assert "signature" not in {k.lower() for k in searx.requests[-1]["headers"]}
