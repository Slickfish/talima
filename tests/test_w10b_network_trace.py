"""W10b (the first delta check's DC1-MF2) — the network and child processes as the process sees them, on every path
driven: what a static reading cannot hold "under any spelling", a trace holds on the paths it runs.

WHAT IS HELD HERE
    N1 The console, driven through its whole surface (a consented search turn, `POST /search/execute`, both seeds,
       every other write route from the app's own route table with FZ-2's fixture payloads, every GET route with
       its path filled, and two KAIROS cycles with anchoring on), reaches outside the process only as answered for:
       every request httpx sends and every socket or process the interpreter audits is attributed to the backend
       frames that made it, and each (host, function) pair is in `ANSWERED`. The recorder's requests are the
       action rows, one for one and target for target; anchoring sends one request per stamp; every answered pair
       is seen (non-vacuity). It runs twice: with the platform's own socketpair, and with Python's own forced in
       its place, the path Windows takes (no native socketpair there, and asyncio makes one per event loop). That
       emulation connects one socket of the process to another over loopback, and its connection is answered for
       by where it is made, never by its address: the connecting frame is the emulation's own and the target is
       the listener it has just bound.
    N2 Planted, each spelled as nothing here names it, and each makes N1's own verdict (`holds`) fail: a sync
       client on a thread, the standard library's opener, a computed attribute inside anchoring, a child process, a
       raw client inside the action set (the verifier's MF-1 class, at run time), a bare connection, a bare lookup;
       and, for the verdict's other clauses (the second delta check's SF-3), an insert that writes nothing (the
       recorder's requests stop being its rows), a GET body that never runs, and an answered pair no drive reaches;
       and a loopback pair made outside socketpair (its shape exactly: a listener bound on 127.0.0.1 and a connect
       to the port it was given, from a backend frame).
    N3 With the emulation forced, so its connections are answered for on the same drive, every loopback plant is
       still named: the bare connection and the opener to 127.0.0.1:9, and the loopback pair made outside it.

    Not seen here, declared: a path no driver reaches (the MCP process; the scripts; a model backend, which the
    suite's provider stands in for); a connection a C library opens without an audit event (libpq's, for
    example); an outbound request that does not use httpx and raises no audited event.

LAW 16. N1 is a new guard over unchanged code: it passes on 40bc2ee, where nothing on a driven path reaches past
the recorder. It is seen failing on the first delta check's mutants B1 to B3 and the verifier's MSR1, applied to
this tree and each restored from a saved copy: N1 fails on every one, naming the pair or the count. N2's five
plants are each named. The socketpair rule (the operator's Windows gate on 001f940: 66 connects to 127.0.0.1 on
ephemeral ports, attributed `other:?`): N1 at 001f940 with the emulation forced fails exactly so (63 here); N3 fails
under a rule that answers for loopback in general.
"""
from __future__ import annotations

import _socket
import socket
import sys
import threading
import types
import urllib.request

import httpx
import pytest

from tests import _fz_trace as fz
from tests import _proc
from tests.fixtures.ce_rig import RigClock
from tests.test_w8_proofs import _console

QUERY = "what is the latest python release"
SEARX = "http://searx.invalid"
TSA = "http://tsa.invalid/tsr"
WATCHED = {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex",
           "socket.gethostbyaddr", "socket.getnameinfo", "socket.sendto", "socket.sendmsg", "subprocess.Popen",
           "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile"}
# Every (host, innermost backend function) that may reach outside the process on a driven path, answered for.
ANSWERED = {
    ("turn", "actions.recorded_get"): "a turn's consented web search: a runtime action, recorded",
    ("route:search_execute", "actions.recorded_get"): "POST /search/execute: a runtime action, recorded",
    ("route:kb_seed_conceptnet", "actions.recorded_get"): "a ConceptNet seed's requests: runtime actions, recorded",
    ("route:kb_seed_wikidata", "actions.recorded_get"): "a Wikidata seed's requests: runtime actions, recorded",
    ("cycle", "anchor.stamp"): "anchoring: `block_anchors` is its record (D5)",
    ("turn", "replay_capture._run"): "the record's environment capture: `git`, `nvidia-smi`, once a process",
    ("turn", "replay_capture.nexus_environment"): "the same capture's `uname`, run by the standard library's "
                                                  "`platform`, once a process",
}
# The capture runs once a process; a pair it alone makes may already have been made by an earlier test.
ONCE_A_PROCESS = {("turn", "replay_capture._run"), ("turn", "replay_capture.nexus_environment")}
# The two socketpairs N1 runs under; on Windows both are Python's own.
SOCKETPAIRS = {"native": "the platform's own (on Windows, where `_socket` has none, Python's own)",
               "emulated": "Python's own, forced in its place: the path Windows takes, on every platform"}


def _socketpair_emulation() -> frozenset:
    """The code of Python's own socketpair: `socket.socketpair` itself where `_socket` has none (Windows), and
    `socket._fallback_socketpair` on every platform since CPython's 2024 fix to it. Taken at import, before any
    fixture can patch `socket`."""
    fallback = getattr(socket, "_fallback_socketpair", None)
    codes = {fallback.__code__} if fallback is not None else set()
    if not hasattr(_socket, "socketpair"):
        codes.add(socket.socketpair.__code__)
    return frozenset(codes)


SOCKETPAIR_EMULATION = _socketpair_emulation()


def _made_by_socketpair(event: str, frame, args) -> bool:
    """A connection answered for by where it is made, never by its address: the connecting frame is the
    emulation's own, and the target is the listener that frame has just bound on loopback. A loopback connect
    anywhere else is the process reaching a socket like any other."""
    if event != "socket.connect" or frame.f_code not in SOCKETPAIR_EMULATION:
        return False
    here = frame.f_locals
    return len(args) == 2 and args[1] == (here.get("addr"), here.get("port")) and here.get("addr") in (
        "127.0.0.1", "::1")

# A value for each required query parameter a GET route takes, by name.
QUERY_FILL = {"date": "2026-11-18T00:00:00+00:00", "q": QUERY}

_ACTIVE: "list | None" = None
_HOOKED = False


def _chain(frame) -> tuple:
    frames = []
    while frame is not None:
        frames.append(frame)
        frame = frame.f_back
    return fz.chain_of(frames[::-1])


def _audit(event, args):
    if _ACTIVE is not None and event in WATCHED:
        frame = sys._getframe(1)
        _ACTIVE.append({"kind": event, "what": repr(args)[:120], "chain": _chain(frame),
                        "self_pipe": _made_by_socketpair(event, frame, args)})


def _respond(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.startswith(SEARX + "/search"):
        body = {"results": [{"title": "Python 3.13", "url": "https://docs.python.org/3.13/whatsnew",
                             "content": "What's new", "engine": "stand-in"}]}
    elif "/c/en/" in url:
        body = {"edges": [{"start": {"@id": "/c/en/sleep"}, "end": {"@id": "/c/en/rest"}, "rel": {"@id": "/r/IsA"},
                           "weight": 2.0}]}
    elif "api.php" in url:
        body = {"search": [{"id": "Q1"}]}
    elif "sparql" in url:
        body = {"results": {"bindings": [{"propLabel": {"value": "instance of"}, "valueLabel": {"value": "habit"}}]}}
    else:
        return httpx.Response(200, content=b"no timestamp here", request=request)
    return httpx.Response(200, json=body, request=request)


@pytest.fixture
def traced(tmp_path, monkeypatch, request):
    """The console, with httpx's transports answering from `_respond` and every request and audited event kept
    with the frames that made it. The audit hook is installed once a process (hooks cannot be removed) and keeps
    nothing outside this fixture. Parametrised indirectly with "emulated", Python's own socketpair replaces the
    platform's for the fixture's life, before the console makes its first event loop."""
    global _ACTIVE, _HOOKED
    events: list = []
    if getattr(request, "param", "native") == "emulated":
        if not SOCKETPAIR_EMULATION:
            pytest.skip("this Python has no pure-Python socketpair to force")
        monkeypatch.setattr(socket, "socketpair", getattr(socket, "_fallback_socketpair", socket.socketpair))

    def sync_send(self, request):
        events.append({"kind": "http", "what": f"{request.method} {request.url}", "chain": _chain(sys._getframe(1)),
                       "url": str(request.url)})
        return _respond(request)

    async def async_send(self, request):
        events.append({"kind": "http", "what": f"{request.method} {request.url}", "chain": _chain(sys._getframe(1)),
                       "url": str(request.url)})
        return _respond(request)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", sync_send)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_send)
    monkeypatch.setenv("NEXUS_ANCHOR", "rfc3161")
    monkeypatch.setenv("NEXUS_ANCHOR_URL", TSA)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    from backend.memory import anchor
    stamps = []
    real_stamp = anchor.stamp

    async def counted_stamp(*a, **k):
        stamps.append(1)
        return await real_stamp(*a, **k)
    monkeypatch.setattr(anchor, "stamp", counted_stamp)
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
    svc.search_tool.base_url = SEARX
    if not _HOOKED:
        sys.addaudithook(_audit)
        _HOOKED = True
    _ACTIVE = events
    try:
        yield svc, client, bearer, events, stamps
    finally:
        _ACTIVE = None


def _drive(svc, client, bearer) -> dict:
    """The console's whole surface, once: what was driven, and what could not be."""
    out = {"statuses": []}
    assert client.post("/search/confirm", headers=bearer, json={"query": QUERY, "confirmed": True}).status_code == 200
    with RigClock().installed():
        turn = client.post("/chat", headers=bearer, json={"message": QUERY}).text
    out["lineage_id"] = fz.done_frames(turn)[-1]["lineage_id"]
    for path, body in (("/search/execute", {"query": QUERY, "confirmed": True}),
                       ("/kb/seed/conceptnet", {"concepts": ["sleep", "rest"], "confirmed": True}),
                       ("/kb/seed/wikidata", {"concepts": ["sleep"], "confirmed": True})):
        out["statuses"].append((path, client.post(path, headers=bearer, json=body).status_code))
    for _ in range(2):
        out["statuses"].append(("/kairos/cycle", client.post("/kairos/cycle?force_snapshot=false",
                                                             headers=bearer).status_code))
    action = svc.lineage.conn.execute("SELECT action_id FROM action_records LIMIT 1").fetchone()
    fill = {"lineage_id": out["lineage_id"], "action_id": action[0] if action else "none", "belief_id": "1",
            "commitment_id": "1"}
    routes = fz.drive_routes(client, types.SimpleNamespace(rev3=1), bearer)
    out["undriven"] = routes["undriven"]
    query = dict(QUERY_FILL)
    gets, refused = 0, []
    for route in client.app.routes:
        if "GET" not in (getattr(route, "methods", None) or ()) or route.path in fz.FRAMEWORK_PATHS:
            continue
        path = route.path
        for key, value in fill.items():
            path = path.replace("{" + key + "}", str(value))
        assert "{" not in path, f"a GET route with a path parameter nothing fills: {route.path}"
        needed = [q.name for q in route.dependant.query_params if q.field_info.is_required()]
        assert set(needed) <= set(query), f"a GET route with a query parameter nothing fills: {route.path} {needed}"
        resp = client.get(path, headers=bearer, params={k: query[k] for k in needed})
        if resp.status_code == 422:
            refused.append(route.path)       # answered before its body ran: not driven
        gets += 1
    out["gets"], out["gets_refused"] = gets, refused
    return out


def _pair(event) -> tuple:
    return (fz.host_of(event["chain"]), fz.method_of(event["chain"]))


def unanswered(events) -> list:
    return [(_pair(e), e["kind"], e["what"]) for e in events if not e.get("self_pipe") and _pair(e) not in ANSWERED]


def _rows(svc) -> list:
    return [tuple(r) for r in svc.lineage.conn.execute("SELECT kind, target FROM action_records")]


def _verdict(svc, events, stamps) -> dict:
    """What the trace says: each event not answered for; the recorder's requests against the rows; anchoring's
    requests against its calls."""
    from backend.memory import actions as act
    recorded = sorted(act.target_of(e["url"]) for e in events if e["kind"] == "http"
                      and fz.method_of(e["chain"]) == "actions.recorded_get")
    stamped = sum(1 for e in events if e["kind"] == "http" and fz.method_of(e["chain"]) == "anchor.stamp")
    return {"unanswered": unanswered(events), "recorded": recorded, "rows": sorted(t for _k, t in _rows(svc)),
            "stamped": stamped, "stamps": len(stamps), "seen": sorted({_pair(e) for e in events} & set(ANSWERED)),
            "self_pipe": sum(1 for e in events if e.get("self_pipe"))}


def holds(driven: dict, v: dict) -> None:
    """The trace's verdict on one drive, raising on the first thing that does not hold: N1 asserts it on the
    console as it is, and every N2 plant asserts that it raises (the consequence, not a flag)."""
    assert driven["undriven"] == [], f"write routes with no fixture, so not driven: {driven['undriven']}"
    assert driven["gets_refused"] == [], f"GET routes answered 422, their bodies not run: {driven['gets_refused']}"
    assert v["unanswered"] == [], f"reached outside the process, not answered for: {v['unanswered'][:3]}"
    assert v["recorded"] == v["rows"] and len(v["rows"]) >= 5, f"the recorder's requests are not its rows: {v}"
    assert v["stamped"] == v["stamps"] >= 1, f"anchoring sent {v['stamped']} requests in {v['stamps']} calls"
    missing = set(ANSWERED) - ONCE_A_PROCESS - set(v["seen"])
    assert not missing, f"answered for and never seen on the drive: {sorted(missing)}"


@pytest.mark.parametrize("traced", sorted(SOCKETPAIRS), indirect=True)
def test_n1_the_console_reaches_outside_the_process_only_as_answered_for(traced, request):
    svc, client, bearer, events, stamps = traced
    driven = _drive(svc, client, bearer)
    v = _verdict(svc, events, stamps)
    mode = request.node.callspec.params["traced"]
    print(f"\nN1 ({mode} socketpair) {len(events)} events; driven {driven['statuses']}, {driven['gets']} GET routes, "
          f"undriven {driven['undriven']}; pairs seen {v['seen']}; {len(v['rows'])} action rows; {v['stamped']} stamp "
          f"requests in {v['stamps']} calls; {v['self_pipe']} connections made inside Python's own socketpair")
    holds(driven, v)
    if mode == "emulated":
        assert v["self_pipe"] >= 1, "the emulation was forced and made no connection, so this run proves nothing"


def _thread_with_a_sync_client(monkeypatch):
    from backend.kb import seeder
    real = seeder._normalise_concept

    def planted(uri):
        t = threading.Thread(target=lambda: httpx.get("http://beacon.invalid/"))
        t.start()
        t.join()
        return real(uri)
    monkeypatch.setattr(seeder, "_normalise_concept", planted)


def _the_standard_librarys_opener(monkeypatch):
    from backend.kb import seeder
    real = seeder._normalise_relation

    def planted(uri):
        try:
            urllib.request.urlopen("http://127.0.0.1:9/", timeout=1)
        except OSError:
            pass
        return real(uri)
    monkeypatch.setattr(seeder, "_normalise_relation", planted)


def _a_computed_attribute_in_anchoring(monkeypatch):
    real = httpx.AsyncClient.post

    async def planted(self, url, **kw):
        resp = await real(self, url, **kw)
        await getattr(self, "patch")(url)
        return resp
    monkeypatch.setattr(httpx.AsyncClient, "post", planted)


def _a_child_process(monkeypatch):
    from backend.tools import search
    real = search._tokenise

    def planted(text):
        _proc.run([sys.executable, "-c", "pass"], check=False, timeout=60)
        return real(text)
    monkeypatch.setattr(search, "_tokenise", planted)


def _a_raw_client_in_the_action_set(monkeypatch):
    from backend.tools import search

    async def planted(url, params, *, timeout=10.0, headers=None, store=None, trigger=None):
        async with httpx.AsyncClient() as raw:
            return (await raw.get(url, params=params)).json()
    monkeypatch.setattr(search, "fetch_json", planted)


def _a_raw_connection(monkeypatch):
    import socket
    from backend.kb import seeder
    real = seeder._normalise_relation

    def planted(uri):
        with socket.socket() as raw:                   # a socket.connect alone, no lookup before it
            try:
                raw.connect(("127.0.0.1", 9))
            except OSError:
                pass
        return real(uri)
    monkeypatch.setattr(seeder, "_normalise_relation", planted)


def _a_loopback_pair_outside_socketpair(monkeypatch):
    """Python's socketpair's own shape, outside it: a listener bound on 127.0.0.1 and a connect to the port it was
    given, from a backend frame. Its address is the one the emulation's connections use; only the frame differs."""
    from backend.kb import seeder
    real = seeder._normalise_relation

    def planted(uri):
        with socket.socket() as lsock, socket.socket() as csock:
            lsock.bind(("127.0.0.1", 0))
            lsock.listen()
            addr, port = lsock.getsockname()[:2]
            csock.connect((addr, port))
        return real(uri)
    monkeypatch.setattr(seeder, "_normalise_relation", planted)


def _a_lookup(monkeypatch):
    import socket
    from backend.tools import search
    real = search._tokenise

    def planted(text):
        socket.getaddrinfo("127.0.0.1", 9)             # a lookup alone, no connection after it
        return real(text)
    monkeypatch.setattr(search, "_tokenise", planted)


def _an_insert_that_writes_nothing(monkeypatch):
    """The first action's insert writes nothing and raises nothing, so its request still leaves: the rows stay
    many enough, and only the requests-equal-rows clause can see it."""
    from backend.memory.lineage import LineageStore
    real, skipped = LineageStore.action_requested, []

    def planted(self, row):
        if not skipped:
            skipped.append(row["action_id"])
            return None
        return real(self, row)
    monkeypatch.setattr(LineageStore, "action_requested", planted)


def _a_get_body_that_never_runs(monkeypatch):
    monkeypatch.setitem(QUERY_FILL, "date", "not a date")


def _an_answered_pair_the_drive_never_reaches(monkeypatch):
    monkeypatch.setitem(ANSWERED, ("cycle", "anchor.anchor_pending"), "a pair nothing on the drive makes")


PLANTS = {"a sync client on a thread": _thread_with_a_sync_client,
          "the standard library's opener": _the_standard_librarys_opener,
          "a computed attribute in anchoring": _a_computed_attribute_in_anchoring,
          "a child process": _a_child_process,
          "a raw client in the action set": _a_raw_client_in_the_action_set,
          "a raw connection": _a_raw_connection,
          "a loopback pair made outside socketpair": _a_loopback_pair_outside_socketpair,
          "a lookup": _a_lookup,
          "an insert that writes nothing": _an_insert_that_writes_nothing,
          "a GET body that never runs": _a_get_body_that_never_runs,
          "an answered pair the drive never reaches": _an_answered_pair_the_drive_never_reaches}


@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_n2_a_planted_reach_past_the_recorder_is_named(traced, monkeypatch, plant):
    svc, client, bearer, events, stamps = traced
    PLANTS[plant](monkeypatch)
    driven = _drive(svc, client, bearer)
    with pytest.raises(AssertionError) as caught:
        holds(driven, _verdict(svc, events, stamps))
    print(f"\nN2 {plant}: {str(caught.value).splitlines()[0][:160]}")


LOOPBACK = ("a raw connection", "the standard library's opener", "a loopback pair made outside socketpair")


@pytest.mark.parametrize("traced", ["emulated"], indirect=True)
@pytest.mark.parametrize("plant", LOOPBACK)
def test_n3_with_the_emulation_live_a_loopback_connect_outside_it_is_still_named(traced, monkeypatch, plant):
    svc, client, bearer, events, stamps = traced
    PLANTS[plant](monkeypatch)
    driven = _drive(svc, client, bearer)
    v = _verdict(svc, events, stamps)
    assert v["self_pipe"] >= 1, "the emulation made no connection, so its rule was not live on this drive"
    with pytest.raises(AssertionError) as caught:
        holds(driven, v)
    assert str(caught.value).startswith("reached outside the process, not answered for"), caught.value
    print(f"\nN3 {plant}: {v['self_pipe']} socketpair connections answered for; named {v['unanswered'][:1]}")
