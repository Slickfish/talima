"""W4 part 1 — the identity boundary: NEXUS_AUTH_MODE token | proxy
(run/specs/W4.md S2's D1 to D9; S3's I1 to I15, I9 by part 1b).

Token mode is today's gate, byte for byte. Proxy mode takes who a request is
from from the identity header of a loopback peer and answers only for its
configured owner: the proxy in front authenticated the person, NEXUS did not.

I1  The mode is decided once, at the factory, and an unknown value is refused
    by name, exit 2, nothing launched or opened: `start.py --serve`, `--check`,
    the interactive flow, and the factory uvicorn calls. Unset and empty are token.
I2  Token mode is what it was: zero middleware, `GET /` injects the token, and
    five turns through `/chat` give the request hashes captured on the base
    (STEP 0, with its not-vacuous companion). The 26 gated routes' 401 matrix
    is held by tests/test_auth_coverage.py, unedited apart from the twelfth
    route (`/whoami`).
I3  Proxy mode's matrix over everything the app serves, derived from
    `app.routes` (the routes, the /vendor mount, the framework's own); the
    exempt set is exactly /health and /healthz; a route planted on the built
    app is held to the same matrix with no edit here.
I4  The forwarded-for spoof is 401 under a launch that trusts every address,
    including a forwarded entry that carries a port: uvicorn 0.54.0 carries
    that port into the peer (measured below), so D6's port-0 rule alone would
    admit `X-Forwarded-For: 127.0.0.1:4444`. Proxy mode's argv carries
    `--no-proxy-headers`; token mode's is the pinned argv.
I5  On a real socket (a `start.py --serve` child in proxy mode): a loopback
    request with the owner's header and an X-Forwarded-For, as a real proxy
    sends it, is 200; without the header 401; with another login 403.
I6  A bearer is not a credential in proxy mode; `GET /` carries no token;
    none is minted or printed.
I7  The functions that read a request's headers are the bearer's and the
    identity's one reader (AST over the tracked backend); planted second
    readers are named.
I8  The dependency without the middleware answers 401 on every gated route.
I9  The login on the record (part 1b, D7): in proxy mode a turn's
    `accountable_identity` is the login the gate established, taken through
    the `established_identity` dependency, and its `identity_mechanism` is
    `proxy`; a body `accountable` that differs is 422 `accountable_mismatch`,
    recorded nowhere; a named unit's first-mention row carries the login;
    `/whoami` reports the word the record carries. Token mode is unchanged,
    and a planted identity moves no field of its record.
I10 `GET /whoami` in both modes; the ratchet's count is 12 and the three
    documents say so; no key of the just-culture population.
I11 `/healthz` says the mode and never the owner's login.
I12 The header's shapes. I13 The peer's shapes. I14 The three settings take
    effect from `.env`. I15 MCP is untouched.

W4's close (the verifier's V1, V2 with V6, and V8; the critic's C1):
V1  In proxy mode the identity is ambient (the proxy attaches the login to
    every request the tester's browser sends), so a page from another site
    could post as the tester. An unsafe request (any method but GET, HEAD
    and OPTIONS) is refused 403 with one fixed sentence when its
    Sec-Fetch-Site is present and not `same-origin`, or when it carries an
    Origin and no Sec-Fetch-Site; it is the gate's last step, after the owner
    checks. A same-origin request and one with neither header (a client that
    is no browser) are not refused. Derived from `app.routes`, with a route
    planted on the built app; the three query writers change nothing; on a
    real socket (I5); token mode has no such step (zero middleware).
V2  The login is compared with the owner's exactly: a login that extends the
    owner's, is a prefix of it, differs in case or in the domain only, is
    NFKC-equal, is a confusable, or is a comma-joined value naming the owner
    is 403. The verifier's loosening mutants are these tests' companions.
V6  Only HTTP's optional whitespace (space and horizontal tab) is trimmed,
    from the header and from NEXUS_OWNER_IDENTITY alike: the owner's login
    padded with U+00A0, U+2003 or U+3000 is 403.
V8  A planted identity is removed on the open paths.

THE ORDER OF THE RULE, DECLARED. D2 numbers it: the peer (401), the header
(401), the owner unset (403 "owner not set"), the mismatch (403); D6 says the
forwarded-for spoof is 401. I3's text says that with the owner unset "every
one of them is 403 'owner not set'", which disagrees with both for a request
the peer or the header rule refuses. The build follows D2's order (and D6,
and the brief's "in D2's order"): with no owner, such a request is still 401,
every other is 403 "owner not set", and none is accepted.

LAW 16. Every test here was run on the base (9b200e9) before the product
change; the output is SP/w4/p1/prefix.txt. A test whose base failure is only
that a name the build adds does not exist yet says "base: the new name" in
its docstring. The two pins (I2's route hashes, I15) pass on the base by
construction; their companions show what moves them. Part 1b's I9 tests were
run the same way on the integration branch at bbc4bb8 (parts 0, 1 and 2),
before 1b's change; the output is SP/w4/p1b/prefix.txt. Its token-mode pin
passes there by construction, and its companion is its failing case. The
close's tests (V1, V2, V6, V8) were run on f3a6385 before the fix; the output
is SP/w4/fix/prefix.txt. Each V1 and V6 test fails there on its consequence,
but the reader's, whose failure is the new names (declared on it). The V2
near misses but the three Unicode paddings, the ASCII-padding pin and V8 pass
there by construction: the base compares exactly and removes the planted key,
and nothing held it; each one's failing case is a mutant of the comparison or
of the removal, applied to the fix and killed (SP/w4/fix/mutants_*.log).
"""
from __future__ import annotations

import ast
import asyncio
import dataclasses
import http.client
import json
import os
import re
import shutil
import subprocess
from tests import _proc
import sys
import textwrap
import threading
import time
import unicodedata
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

import start
from backend.kb.work_units import actor_fields, pseudo_unit_id
from backend.memory.lineage import IDENTITY_ASSERTED, IDENTITY_MECHANISMS, IDENTITY_PROXY
from backend.server import MAX_SCOPE_CHARS, create_app, create_services
from tests._tracked import REPO, tracked, tracked_backend
from tests.fixtures.ce_rig import RigClock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import history_shape_harness as hsh  # noqa: E402

ROUTE_PIN = ROOT / "tests" / "_w4_route_request_hashes.json"
P1_FIXTURE = ROOT / "tests" / "_p1_request_hashes.json"
START = REPO / "start.py"

AUTH_KEYS = ("NEXUS_AUTH_MODE", "NEXUS_IDENTITY_HEADER", "NEXUS_OWNER_IDENTITY")
DEFAULT_HEADER = "Tailscale-User-Login"
OWNER = "owner@example.test"
OTHER = "intruder@example.test"
LOOPBACK = ("127.0.0.1", 40123)
REMOTE = ("203.0.113.9", 40123)
EXEMPT = {"/health", "/healthz"}
EXIT_USAGE = 2                      # start.py's documented literal


def _done(resp) -> dict:
    frame = [f for f in resp.text.split("\n\n") if f.startswith("event: done")][-1]
    return json.loads(frame.split("data: ", 1)[1])


# ---------------------------------------------------------------------------
# STEP 0 / I2 — the route pin (captured on the base, 9b200e9, before any change)
# ---------------------------------------------------------------------------

def route_request_hashes(root: Path, questions, *, clock=None) -> list[str]:
    """Each turn's `request_hash`, read from its lineage record, for the
    questions sent one by one through `POST /chat` with the bearer on a
    `TestClient`, on the fixture store and stub P1's pin uses
    (`history_shape_harness._build_store`, its `_Stub` with no plant), under
    production's defaults (the history wrapper on). The client is used
    without a context manager, as every route test is: the lifespan, and so
    KAIROS's first cycle and the startup probe, does not run, so nothing
    races the turns. The clock is an input (W0's rule, tests/fixtures/ce_rig.py):
    an edge fired in one turn transmits nothing for 0.5 s (its refractory)
    and prominence is ranked at six decimals, so the host's speed would
    decide what the next turn's cortex lines say; `clock` (a `RigClock`,
    one microsecond per read) is installed as `time.time` around the
    seeding and every request."""
    clock = clock or RigClock()
    hashes = []
    with clock.installed():
        hsh._build_store(root / "kb.db").close()
        svc = create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma",
                              eval_log_path=root / "metrics.jsonl", archive_db_path=root / "archive.db",
                              snapshots_dir=root / "snapshots", provider=hsh._Stub(plant=None))
        client = TestClient(create_app(svc))
        bearer = {"Authorization": f"Bearer {os.environ['NEXUS_API_TOKEN']}"}
        for question in questions:
            resp = client.post("/chat", headers=bearer, json={"message": question})
            assert resp.status_code == 200, resp.text
            hashes.append(svc.lineage.get(_done(resp)["lineage_id"]).request_hash)
    return hashes


def _route_pin() -> dict:
    return json.loads(ROUTE_PIN.read_text(encoding="utf-8"))


def test_i2_five_turns_through_the_route_give_the_base_capture(tmp_path):
    """D13: the literals were captured on the base (9b200e9) by this same
    function, before any change. A pin of unchanged behaviour passes on the
    base by construction; its law-16 failure is the companion below."""
    pin = _route_pin()
    p1 = json.loads(P1_FIXTURE.read_text(encoding="utf-8"))
    assert pin["questions"] == p1["questions"], "the five questions are P1's fixture's"
    got = route_request_hashes(tmp_path, pin["questions"])
    for i, (question, act) in enumerate(zip(pin["questions"], got), 1):
        exp = pin["request_hashes"][i - 1] if i <= len(pin["request_hashes"]) else None
        print(f"I2 turn {i}: {act} {'==' if act == exp else '!='} {exp}  {question}")
    assert len(pin["request_hashes"]) == 5 and len(set(pin["request_hashes"])) == 5, "five distinct requests"
    assert got == pin["request_hashes"]


def test_i2_the_route_pin_is_not_vacuous_a_planted_system_line_moves_every_hash(tmp_path, monkeypatch):
    """Companion: an extra line in the system message (appended to the
    prefix the loop assembles from) and the same five turns give five other
    hashes, so the pin reads what the route sends."""
    from backend.loop import agentic_loop
    monkeypatch.setattr(agentic_loop, "STABLE_SYSTEM_PREFIX",
                        agentic_loop.STABLE_SYSTEM_PREFIX + "\nA planted extra system line.")
    pin = _route_pin()
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    got = route_request_hashes(tmp_path / "a", pin["questions"])
    moved = [i for i, (a, e) in enumerate(zip(got, pin["request_hashes"]), 1) if a != e]
    print(f"\nI2 companion: the planted line moved turns {moved}")
    assert moved == [1, 2, 3, 4, 5]
    with pytest.raises(AssertionError):
        test_i2_five_turns_through_the_route_give_the_base_capture(tmp_path / "b")


# ---------------------------------------------------------------------------
# The apps under test
# ---------------------------------------------------------------------------

class _Offline:
    """No model server: the one writer that would reach the network answers here."""

    async def stream_chat(self, model, messages, num_ctx=8192):
        yield "Interval training improves VO2 max."

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _services(root: Path):
    return create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma",
                           eval_log_path=root / "metrics.jsonl", archive_db_path=root / "archive.db",
                           snapshots_dir=root / "snapshots", provider=_Offline())


def _app(root: Path, monkeypatch, *, mode=None, owner=None, header=None):
    """An app built with the three settings as given (unset otherwise): the
    factory reads them once, so the app keeps them after the test's
    environment moves on."""
    for key in AUTH_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in zip(AUTH_KEYS, (mode, header, owner)):
        if value is not None:
            monkeypatch.setenv(key, value)
    return create_app(_services(root))


def _proxy_app(root: Path, monkeypatch, *, owner=OWNER, header=None):
    return _app(root, monkeypatch, mode="proxy", owner=owner, header=header)


def _client(app, peer=LOOPBACK) -> TestClient:
    """conftest gives every TestClient the valid bearer; proxy mode ignores it."""
    return TestClient(app, client=peer, raise_server_exceptions=False)


def _detail(resp) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    return body.get("detail", "") if isinstance(body, dict) and isinstance(body.get("detail"), str) else ""


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


# ---------------------------------------------------------------------------
# I1 — the mode is decided once; an unknown value is refused by name
# ---------------------------------------------------------------------------

def test_i1_unset_and_empty_are_token_and_an_unknown_mode_is_refused_by_name():
    """base: the new name (backend.identity)."""
    from backend.identity import AuthConfigError, IdentityConfig, identity_config
    for environ in ({}, {"NEXUS_AUTH_MODE": ""}, {"NEXUS_AUTH_MODE": "  "}, {"NEXUS_AUTH_MODE": "token"},
                    {"NEXUS_AUTH_MODE": " Token "}):
        assert identity_config(environ).mode == "token", environ
    assert identity_config({"NEXUS_AUTH_MODE": "proxy"}).mode == "proxy"
    assert identity_config({"NEXUS_AUTH_MODE": " PROXY "}).mode == "proxy"
    with pytest.raises(AuthConfigError) as refused:
        identity_config({"NEXUS_AUTH_MODE": "nonsense"})
    print(f"\nI1 refusal: {refused.value}")
    assert "NEXUS_AUTH_MODE='nonsense'" in str(refused.value)
    assert "token" in str(refused.value) and "proxy" in str(refused.value)
    config = identity_config({})
    assert isinstance(config, IdentityConfig) and config.header == DEFAULT_HEADER and config.owner is None
    assert identity_config({"NEXUS_IDENTITY_HEADER": " ", "NEXUS_OWNER_IDENTITY": " "}) == config, \
        "blank is unset"
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.mode = "proxy"                                   # Law 1


def test_i1_the_mode_is_read_at_the_factory_and_never_per_request(tmp_path, monkeypatch):
    """An app keeps the mode it was built in when the environment moves after."""
    token = _app(tmp_path / "t", monkeypatch)
    monkeypatch.setenv("NEXUS_AUTH_MODE", "proxy")
    tc = _client(token, REMOTE)
    assert tc.get("/kb/stats").status_code == 200, "the token app turned into a proxy app"
    proxy = _proxy_app(tmp_path / "p", monkeypatch)
    monkeypatch.delenv("NEXUS_AUTH_MODE")
    assert _client(proxy, REMOTE).get("/kb/stats").status_code == 401, "the proxy app turned into a token app"


def test_i1_create_app_with_services_raises_the_refusal_by_name(tmp_path, monkeypatch):
    """Given stores the caller opened, the factory raises the refusal (it
    cannot say "Nothing was opened"); called by uvicorn with none, it leaves
    with exit 2 (the subprocess test below). base: the refusal does not exist."""
    services = _services(tmp_path)
    monkeypatch.setenv("NEXUS_AUTH_MODE", "nonsense")
    with pytest.raises(ValueError) as refused:
        create_app(services)
    assert type(refused.value).__name__ == "UnknownAuthMode" and "'nonsense'" in str(refused.value)


class _ReadingChild:
    """A child whose output is read on a thread the whole time it runs
    (W3b-D13: a wait that does not read the pipe blocks on Windows), waited
    for with plain sockets (no HTTP proxy from the environment), stopped
    with SIGINT on POSIX and as a tree on Windows."""

    def __init__(self, argv: list, env: dict):
        # W4b: tests/_proc.py reads the output on its own threads and bounds
        # every wait.
        self.proc = _proc.Child(argv, label="start.py --serve (proxy mode)", cwd=REPO, env=env, stdin=_proc.DEVNULL,
                                stdout=_proc.PIPE, stderr=_proc.STDOUT, text=True, encoding="utf-8",
                                errors="replace")

    def wait_until(self, port: int, timeout: float = 150.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if _http(port, "GET", "/healthz", timeout=2.0)[0] == 200:
                    return "serving"
            except (OSError, http.client.HTTPException):
                pass
            if self.proc.poll() is not None:
                return "exited"
            time.sleep(0.25)
        return "neither"

    def stop(self) -> int:
        """SIGINT on POSIX, the tree killed on Windows; the exit within 90 s
        or the tree is killed and named."""
        if self.proc.poll() is None:
            self.proc.interrupt()
        return self.proc.wait(timeout=90)

    def output(self) -> str:
        return self.proc.output() or ""


def _http(port: int, method: str, path: str, headers: dict | None = None, timeout: float = 10.0):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", errors="replace")
    finally:
        conn.close()


def _child_env(tmp_path: Path, **extra: str) -> dict:
    from tests.test_w3_serve_stub import _env
    env = _env(tmp_path, **extra)
    for key in AUTH_KEYS:
        if key not in extra:
            env.pop(key, None)
    return env


def test_i1_serve_refuses_an_unknown_mode_by_name_and_launches_nothing(tmp_path):
    from tests.test_w3_serve_stub import _free_port
    root, port = tmp_path / "root", _free_port()
    child = _ReadingChild([sys.executable, str(START), "--serve", "--port", str(port), "--data-root", str(root)],
                          _child_env(tmp_path, LLM_BACKEND="stub", NEXUS_AUTH_MODE="nonsense"))
    state = child.wait_until(port, timeout=120)
    code = child.stop()
    out = child.output()
    print(f"\nI1 --serve: state {state}, exit {code}; {out.strip().splitlines()[-1:]}")
    assert state == "exited", f"--serve served under an unknown NEXUS_AUTH_MODE\n{out[-1500:]}"
    assert code == EXIT_USAGE, out[-1500:]
    assert "NEXUS_AUTH_MODE='nonsense'" in out and "token, proxy" in out and "nothing was launched" in out
    assert not root.exists() or list(root.iterdir()) == [], f"the data root is not empty: {list(root.iterdir())}"


def test_i1_the_factory_refuses_an_unknown_mode_by_name_and_opens_nothing(tmp_path):
    """The console as uvicorn's --factory calls it, without start.py."""
    from tests.test_w3_serve_stub import _free_port
    root, port = tmp_path / "console", _free_port()
    child = _ReadingChild([sys.executable, "-m", "uvicorn", "backend.server:create_app", "--factory", "--host",
                           "127.0.0.1", "--port", str(port), "--workers", "1"],
                          _child_env(tmp_path, LLM_BACKEND="stub", NEXUS_AUTH_MODE="nonsense",
                                     NEXUS_DATA_ROOT=str(root)))
    state = child.wait_until(port, timeout=120)
    code = child.stop()
    out = child.output()
    print(f"\nI1 factory: state {state}, exit {code}; {out.strip().splitlines()[-1:]}")
    assert state == "exited", f"the console served under an unknown NEXUS_AUTH_MODE\n{out[-1500:]}"
    assert code == EXIT_USAGE, out[-1500:]
    assert "NEXUS: NEXUS_AUTH_MODE='nonsense' is not an auth mode NEXUS knows (token, proxy)" in out, out[-1500:]
    assert "Nothing was opened" in out and "Traceback" not in out and not root.exists()


def _arranged(monkeypatch, tmp_path, environ):
    from tests.test_w1_start import _arrange
    for key in AUTH_KEYS:
        monkeypatch.delenv(key, raising=False)
    return _arrange(monkeypatch, tmp_path, environ)


def _run(fn, args):
    try:
        return fn(args)
    except SystemExit as stop:
        return stop.code


def test_i1_check_refuses_an_unknown_mode_by_name(tmp_path, monkeypatch, capsys):
    handed = _arranged(monkeypatch, tmp_path, {"NEXUS_AUTH_MODE": "nonsense"})
    code = _run(start.run_check, start.parse_args(["--check", "--data-root", str(tmp_path / "root")]))
    out = capsys.readouterr().out
    print(f"\nI1 --check: exit {code}")
    assert code == EXIT_USAGE and handed == [], out
    assert "NEXUS_AUTH_MODE='nonsense'" in out and "token, proxy" in out


def test_i1_the_interactive_flow_refuses_an_unknown_mode_before_the_launch(tmp_path, monkeypatch, capsys):
    from tests.test_w1_start import _composed_answering
    for key in AUTH_KEYS:
        monkeypatch.delenv(key, raising=False)
    handed, prompts, code = _composed_answering(
        monkeypatch, tmp_path, [], {"Begin startup sequence?": "y", "Start NEXUS now?": "y"},
        {"NEXUS_AUTH_MODE": "nonsense"})
    out = capsys.readouterr().out
    print(f"\nI1 interactive: exit {code}, handed over {len(handed)}")
    assert code == EXIT_USAGE and handed == [], out[-1500:]
    assert "NEXUS_AUTH_MODE='nonsense'" in out
    assert not [p for p in prompts if "Start NEXUS now?" in p], "refused before the launch is offered"


# ---------------------------------------------------------------------------
# I2 — token mode is what it was (the route pin is STEP 0, above)
# ---------------------------------------------------------------------------

def test_i2_token_mode_has_zero_middleware_and_proxy_mode_the_gate_alone(tmp_path, monkeypatch):
    token = _app(tmp_path / "t", monkeypatch)
    assert token.user_middleware == [], "token mode keeps zero middleware (S1.A1)"
    proxy = _proxy_app(tmp_path / "p", monkeypatch)
    names = [m.cls.__name__ for m in proxy.user_middleware]
    print(f"\nI2 middleware: token {token.user_middleware}, proxy {names}")
    assert names == ["IdentityGate"]


def test_i2_get_root_injects_the_token_in_token_mode(tmp_path, monkeypatch):
    """A pin of today's behaviour (passes on the base); I6 holds proxy mode's."""
    token = _app(tmp_path, monkeypatch)
    page = _client(token).get("/")
    assert page.status_code == 200
    assert f"<head><script>window.NEXUS_TOKEN={json.dumps(os.environ['NEXUS_API_TOKEN'])};</script>" in page.text


# ---------------------------------------------------------------------------
# I3 — proxy mode's matrix, derived over everything the app serves
# ---------------------------------------------------------------------------

KINDS = {
    "a remote peer with the owner's login": (REMOTE, "owner"),
    "a loopback peer with no identity": (LOOPBACK, None),
    "a loopback peer with another login": (LOOPBACK, "other"),
    "a loopback peer with the owner's login": (LOOPBACK, "owner"),
}


def served_targets(app) -> list[tuple[str, str]]:
    """(method, url) for everything the app serves, from `app.routes`: every
    route with every method it declares, path parameters filled with a
    placeholder, and each mount at a path below it (the /vendor mount
    declares no methods, so a ratchet over methods never sees it)."""
    from starlette.routing import Mount
    out = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is None:
            continue
        if isinstance(route, Mount):
            out.add(("GET", path.rstrip("/") + "/probe.txt"))
            continue
        for method in getattr(route, "methods", None) or ():
            out.add((method, _fill(path)))
    return sorted(out)


def run_matrix(app, targets, header=DEFAULT_HEADER) -> dict:
    """{(method, url): {kind: (status, detail)}} for the four kinds of request."""
    clients = {peer: _client(app, peer) for peer in (REMOTE, LOOPBACK)}
    logins = {"owner": OWNER, "other": OTHER}
    out = {}
    for method, url in targets:
        row = {}
        for kind, (peer, who) in KINDS.items():
            headers = {header: logins[who]} if who else {}
            resp = clients[peer].request(method, url, headers=headers)
            row[kind] = (resp.status_code, _detail(resp))
        out[(method, url)] = row
    return out


def matrix_problems(matrix: dict, *, owner_set: bool) -> list[str]:
    """The rule (D2's order) checked cell by cell; the exempt paths answer
    200 to every kind."""
    problems = []
    for (method, url), row in sorted(matrix.items()):
        if url in EXEMPT:
            problems += [f"{method} {url} {kind}: {row[kind][0]} (exempt: 200)"
                         for kind in KINDS if row[kind][0] != 200]
            continue
        want = {"a remote peer with the owner's login": 401, "a loopback peer with no identity": 401,
                "a loopback peer with another login": 403}
        for kind, status in want.items():
            if row[kind][0] != status:
                problems.append(f"{method} {url} {kind}: {row[kind][0]} (want {status})")
        own = row["a loopback peer with the owner's login"]
        if owner_set and own[0] in (401, 403):
            problems.append(f"{method} {url} the owner: {own} (want neither 401 nor 403)")
        if not owner_set:
            for kind in ("a loopback peer with another login", "a loopback peer with the owner's login"):
                # A HEAD response carries no body, so its detail cannot be read; its status can.
                said = method == "HEAD" or row[kind][1].startswith("owner not set")
                if row[kind][0] != 403 or not said:
                    problems.append(f"{method} {url} {kind} with no owner: {row[kind]} (want 403 owner not set)")
    return problems


def exempt_set(matrix: dict) -> set[str]:
    return {url for (_m, url), row in matrix.items() if all(code not in (401, 403) for code, _d in row.values())}


def test_i3_proxy_modes_matrix_over_everything_the_app_serves(tmp_path, monkeypatch):
    app = _proxy_app(tmp_path, monkeypatch)
    targets = served_targets(app)
    urls = {u for _m, u in targets}
    assert {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect", "/", "/chat"} <= urls, urls
    assert any(u.startswith("/vendor/") for u in urls), "the mount was not derived"
    matrix = run_matrix(app, targets)
    problems = matrix_problems(matrix, owner_set=True)
    print(f"\nI3 {len(targets)} targets x {len(KINDS)} kinds; problems {len(problems)}; exempt {sorted(exempt_set(matrix))}")
    assert not problems, "\n".join(problems[:40])
    assert exempt_set(matrix) == EXEMPT


def test_i3_with_the_owner_unset_nothing_is_accepted(tmp_path, monkeypatch, capsys):
    """D2's order: a request the peer or the header rule refuses is 401 as
    with an owner; every other is 403 "owner not set"; none is accepted, and
    the factory says so on stderr (D9)."""
    app = _proxy_app(tmp_path, monkeypatch, owner=None)
    err = capsys.readouterr().err
    matrix = run_matrix(app, served_targets(app))
    problems = matrix_problems(matrix, owner_set=False)
    print(f"\nI3 no owner: problems {len(problems)}; stderr {err.strip()!r}")
    assert not problems, "\n".join(problems[:40])
    assert exempt_set(matrix) == EXEMPT
    assert "every request will be refused" in err and "owner not set" in err


def test_i3_companion_a_route_planted_on_the_built_app_is_held_to_the_same_matrix(tmp_path, monkeypatch):
    """Planted after the factory returned, with no edit to the matrix."""
    app = _proxy_app(tmp_path, monkeypatch)

    @app.get("/planted/after-the-factory")
    async def planted_after_the_factory():
        return {"planted": True}

    targets = served_targets(app)
    assert ("GET", "/planted/after-the-factory") in targets
    row = run_matrix(app, [("GET", "/planted/after-the-factory")])[("GET", "/planted/after-the-factory")]
    print(f"\nI3 companion: {[row[k][0] for k in KINDS]}")
    assert [row[k][0] for k in KINDS] == [401, 401, 403, 200]


# ---------------------------------------------------------------------------
# I4 — the forwarded-for spoof
# ---------------------------------------------------------------------------

def test_i4_the_forwarded_for_spoof_is_refused_under_a_launch_that_trusts_everything(tmp_path, monkeypatch):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    # Measured first: what the wrapper hands the app as its peer.
    seen = []

    async def peer_probe(scope, receive, send):
        seen.append(tuple(scope["client"]))

    probe = ProxyHeadersMiddleware(peer_probe, trusted_hosts="*")
    asyncio.run(probe({"type": "http", "client": REMOTE, "headers": [(b"x-forwarded-for", b"127.0.0.1:4444")]},
                      None, None))
    print(f"\nI4 measured: a remote peer with X-Forwarded-For 127.0.0.1:4444 reaches the app as {seen[0]}")
    assert seen == [("127.0.0.1", 4444)], "the premise: a port-0 rule alone would take this peer"
    app = _proxy_app(tmp_path, monkeypatch)
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts="*")
    remote, proxy = (TestClient(wrapped, client=p, raise_server_exceptions=False) for p in (REMOTE, LOOPBACK))
    cases = {
        "a remote peer, X-Forwarded-For 127.0.0.1": (remote, "127.0.0.1"),
        "a remote peer, X-Forwarded-For 127.0.0.1:4444": (remote, "127.0.0.1:4444"),
        "a remote peer, X-Forwarded-For [::1]:4444": (remote, "[::1]:4444"),
        "a loopback proxy, X-Forwarded-For <client>": (proxy, "198.51.100.7"),
    }
    got = {}
    for name, (client, forwarded) in cases.items():
        resp = client.get("/kb/stats", headers={DEFAULT_HEADER: OWNER, "X-Forwarded-For": forwarded})
        got[name] = (resp.status_code, _detail(resp))
    for name, cell in got.items():
        print(f"I4 {name}: {cell}")
    assert {name: cell[0] for name, cell in got.items()} == {name: 401 for name in cases}
    assert all("rewritten" in detail and "--no-proxy-headers" in detail for _s, detail in got.values()), got


def test_i4_proxy_modes_argv_carries_no_proxy_headers_and_token_modes_is_the_pinned_argv(tmp_path, monkeypatch,
                                                                                         capsys):
    """Through `run_serve`, the launch a container runs, in process."""
    pinned = [sys.executable, "-m", "uvicorn", "backend.server:create_app", "--factory", "--host", "127.0.0.1",
              "--port", "8123", "--workers", "1", "--forwarded-allow-ips", "127.0.0.1,::1"]
    args = start.parse_args(["--serve", "--port", "8123", "--data-root", str(tmp_path / "root")])
    handed = _arranged(monkeypatch, tmp_path, {"LLM_BACKEND": "stub"})
    _run(start.run_serve, args)
    token_out = capsys.readouterr().out
    assert [argv for argv, _env in handed] == [pinned], handed
    assert "auth " not in token_out, "token mode's lines are today's"
    monkeypatch.setenv("NEXUS_AUTH_MODE", "proxy")
    monkeypatch.setenv("NEXUS_OWNER_IDENTITY", OWNER)
    handed.clear()
    _run(start.run_serve, args)
    out = capsys.readouterr().out
    print(f"\nI4 proxy argv: {handed[0][0] if handed else None}")
    assert [argv for argv, _env in handed] == [pinned + ["--no-proxy-headers"]], handed
    auth = [line for line in out.splitlines() if line.startswith("  auth ")]
    assert auth and "proxy" in auth[0] and DEFAULT_HEADER in auth[0] and "owner set" in auth[0], out
    assert "NEXUS_API_TOKEN" not in out and "Bearer" not in out and "token" not in "\n".join(auth).lower()


def test_i4_the_interactive_launch_in_proxy_mode_says_the_mode_and_nothing_of_a_token(tmp_path, monkeypatch,
                                                                                     capsys):
    from tests.test_w1_start import _composed_answering
    for key in AUTH_KEYS:
        monkeypatch.delenv(key, raising=False)
    handed, _prompts, code = _composed_answering(
        monkeypatch, tmp_path, [], {"Begin startup sequence?": "y", "Start NEXUS now?": "y"},
        {"NEXUS_AUTH_MODE": "proxy", "NEXUS_OWNER_IDENTITY": OWNER})
    out = capsys.readouterr().out
    assert code == 0 and len(handed) == 1, out[-1500:]
    assert handed[0][0][-1] == "--no-proxy-headers", handed[0][0]
    assert "proxy" in out and DEFAULT_HEADER in out
    assert "Bearer" not in out and "NEXUS_API_TOKEN" not in out and "with the token" not in out, out[-1500:]


# ---------------------------------------------------------------------------
# I5 — on a real socket
# ---------------------------------------------------------------------------

def test_i5_on_a_real_socket_a_proxied_request_is_served_and_the_rest_refused(tmp_path):
    """W4's close adds, on the same socket: a cross-site form POST to the
    offline seed (the verifier's forgery, V1) is 403 and a same-origin one is
    served; the owner's login padded with U+00A0 is 403 and padded with ASCII
    spaces is the owner (V6: h11 itself removes a value's spaces and tabs,
    and passes U+00A0's two UTF-8 bytes as they came). base (f3a6385): fails
    on its consequence (the forgery is served, 200, and so is the padding)."""
    from tests.test_w3_serve_stub import _free_port
    root, port = tmp_path / "root", _free_port()
    child = _ReadingChild([sys.executable, str(START), "--serve", "--port", str(port), "--data-root", str(root)],
                          _child_env(tmp_path, LLM_BACKEND="stub", NEXUS_AUTH_MODE="proxy",
                                     NEXUS_OWNER_IDENTITY=OWNER))
    try:
        state = child.wait_until(port)
        assert state == "serving", f"--serve did not serve in proxy mode: {state}\n{child.output()[-2500:]}"
        forwarded = {"X-Forwarded-For": "198.51.100.7"}
        no_identity = _http(port, "GET", "/kb/stats", forwarded)
        another = _http(port, "GET", "/kb/stats", {**forwarded, DEFAULT_HEADER: OTHER})
        proxied = _http(port, "GET", "/whoami", {**forwarded, DEFAULT_HEADER: OWNER})
        form = {**forwarded, DEFAULT_HEADER: OWNER, "Content-Type": "application/x-www-form-urlencoded"}
        forged = _http(port, "POST", "/kb/seed/offline?confirmed=true",
                       {**form, "Origin": EVIL, "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"})
        claims_after_forgery = json.loads(_http(port, "GET", "/kb/stats", {**forwarded, DEFAULT_HEADER: OWNER})[1])
        own = _http(port, "POST", "/kb/seed/offline?confirmed=true",
                    {**form, "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin",
                     "Sec-Fetch-Mode": "cors"})
        no_break = _http(port, "GET", "/whoami", {**forwarded, DEFAULT_HEADER: f" {OWNER}".encode("utf-8")})
        spaced = _http(port, "GET", "/whoami", {**forwarded, DEFAULT_HEADER: f"  {OWNER}  "})
    finally:
        code = child.stop()
    out = child.output()
    print(f"\nI5 ({sys.platform}) no identity {no_identity[0]}, another login {another[0]}, the owner's {proxied[0]}; "
          f"exit {code}")
    print(f"I5 the close: a cross-site form POST {forged[0]} {forged[1][:160]}; claims then "
          f"{claims_after_forgery.get('total')}; a same-origin one {own[0]} {own[1][:120]}; the owner's login with "
          f"U+00A0 {no_break[0]}, with spaces {spaced[0]}")
    assert no_identity[0] == 401 and another[0] == 403, (no_identity, another)
    assert proxied[0] == 200, proxied
    assert json.loads(proxied[1])["login"] == OWNER
    assert forged[0] == 403 and claims_after_forgery.get("total") == 0, (forged, claims_after_forgery)
    assert own[0] == 200 and json.loads(own[1])["seeded"] > 0, own
    assert no_break[0] == 403 and spaced[0] == 200 and json.loads(spaced[1])["login"] == OWNER, (no_break, spaced)
    assert "--no-proxy-headers" in out and "auth        proxy" in out, out[-2500:]
    assert "NEXUS_API_TOKEN" not in out and "Generated session token" not in out


# ---------------------------------------------------------------------------
# I6 — a bearer is not a credential in proxy mode
# ---------------------------------------------------------------------------

def test_i6_a_bearer_is_not_a_credential_and_none_is_minted_or_printed(tmp_path, monkeypatch, capsys):
    import backend.server as srv
    app = _proxy_app(tmp_path, monkeypatch)
    client = _client(app)
    assert client.headers["Authorization"] == f"Bearer {os.environ['NEXUS_API_TOKEN']}", "the valid bearer"
    for method, path, body in (("GET", "/lineage/recent", None), ("POST", "/model/override",
                                                                   {"model": "primary", "lock": False}),
                               ("GET", "/kb/stats", None)):
        resp = client.request(method, path, json=body)
        assert resp.status_code == 401, (method, path, resp.status_code)
    calls = []
    real = srv.active_token
    monkeypatch.setattr(srv, "active_token", lambda: calls.append(1) or real())
    monkeypatch.delenv("NEXUS_API_TOKEN")
    monkeypatch.setattr(srv, "_SESSION_TOKEN", None)
    monkeypatch.setattr(srv, "_TOKEN_BANNER_SHOWN", False)
    capsys.readouterr()
    page = client.get("/", headers={DEFAULT_HEADER: OWNER})
    gated = client.get("/lineage/recent", headers={DEFAULT_HEADER: OWNER})
    out = capsys.readouterr()
    print(f"\nI6 page {page.status_code}, gated {gated.status_code}, active_token calls {len(calls)}")
    assert page.status_code == 200 and "<script>window.NEXUS_TOKEN=" not in page.text
    assert gated.status_code == 200
    assert calls == [] and srv._SESSION_TOKEN is None, "a token was asked for in proxy mode"
    assert "Generated session token" not in out.out + out.err


# ---------------------------------------------------------------------------
# I7 — one reader
# ---------------------------------------------------------------------------

HEADER_READERS = {
    ("backend/server.py", "require_token"): "the bearer's: Header(default='') on Authorization (token mode)",
    ("backend/identity.py", "read_request_headers"): "the identity's: the one reader of the identity header "
                                                     "(and of the forwarded-for entries the peer rule needs)",
    # W10c — the interceptor relays a harness's request: it reads the headers to forward them and to fingerprint a
    # credential, and the Host name to refuse a request addressed elsewhere. It takes no identity from a header:
    # behind the proxy the identity is the gate's (`identity_on_scope`), and its cross-site step reads through the
    # identity's own reader.
    ("backend/intercept/app.py", "Interceptor._exchange"): "the interceptor's: the harness's headers, to forward",
    ("backend/intercept/app.py", "Interceptor._refusal"): "the interceptor's: the Host name a request is addressed to",
    ("backend/intercept/forward.py", "Upstream.__init__"): "the upstream's RESPONSE headers, to relay (no request's)",
    ("backend/intercept/forward.py", "Forwarder.__init__"): "the forwarding client's own defaults, cleared",
    ("backend/intercept/stub_upstream.py", "StubUpstream.__call__"): "the stub upstream's: what it was sent, kept "
                                                                     "for the tests that compare bytes",
}


def _is_header_read(node) -> bool:
    """A read of a request's headers: a `Header(...)` call; an attribute
    named `headers` on anything but `self` (the providers' `self.headers()`
    builds the headers of the request they SEND, and is the clause's whole
    population today, printed by the guard); a subscript or a `.get` with
    the constant "headers"."""
    if isinstance(node, ast.Call):
        f = node.func
        if (isinstance(f, ast.Name) and f.id == "Header") or (isinstance(f, ast.Attribute) and f.attr == "Header"):
            return True
        if isinstance(f, ast.Attribute) and f.attr == "get" and node.args \
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "headers":
            return True
    if isinstance(node, ast.Attribute) and node.attr == "headers":
        return not (isinstance(node.value, ast.Name) and node.value.id == "self")
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == "headers":
        return True
    return False


def header_reads(sources: dict[str, str]) -> dict[tuple[str, str], list[int]]:
    """{(file, the innermost enclosing function's qualified name): [lines]}
    for every read of a request's headers; `<module>` outside any function."""
    found: dict[tuple[str, str], list[int]] = {}
    for path, src in sorted(sources.items()):
        stack: list[str] = []

        def visit(node):
            named = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            if named:
                stack.append(node.name)
            elif _is_header_read(node):
                found.setdefault((path, ".".join(stack) or "<module>"), []).append(node.lineno)
            for child in ast.iter_child_nodes(node):
                visit(child)
            if named:
                stack.pop()

        visit(ast.parse(src))
    return found


def self_header_sites(sources: dict[str, str]) -> list[str]:
    return sorted(f"{path}:{n.lineno}" for path, src in sources.items() for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.Attribute) and n.attr == "headers"
                  and isinstance(n.value, ast.Name) and n.value.id == "self")


def _backend_sources() -> dict[str, str]:
    return {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8") for p in tracked_backend()}


def test_i7_the_bearers_and_the_identitys_are_the_only_readers_of_a_requests_headers():
    """base: the identity's reader does not exist yet."""
    sources = _backend_sources()
    found = header_reads(sources)
    print(f"\nI7 readers, derived over {len(sources)} tracked backend files: {sorted(found)}")
    print(f"I7 the self clause's population (outbound headers the providers build): {self_header_sites(sources)}")
    assert set(found) == set(HEADER_READERS), {
        "unanswered": sorted(set(found) - set(HEADER_READERS)), "missing": sorted(set(HEADER_READERS) - set(found))}


PLANTED_READERS = {
    "a function reading request.headers": (
        "backend/server.py", "\n\ndef _planted_remote_user(request):\n    return request.headers.get('x-remote-user')\n",
        ("backend/server.py", "_planted_remote_user")),
    "middleware reading scope['headers']": (
        "backend/kb/schema.py",
        "\n\nclass _PlantedGate:\n    async def __call__(self, scope, receive, send):\n"
        "        return dict(scope['headers']).get(b'x-user')\n",
        ("backend/kb/schema.py", "_PlantedGate.__call__")),
    "scope.get('headers')": (
        "backend/loop/state.py", "\n\ndef _planted_who(scope):\n    return [v for k, v in scope.get('headers', [])]\n",
        ("backend/loop/state.py", "_planted_who")),
    "a Header() parameter": (
        "backend/server.py", "\n\ndef _planted_dep(user: str = Header(default='', alias='X-User')):\n    return user\n",
        ("backend/server.py", "_planted_dep")),
    "self.request.headers, past the self clause": (
        "backend/server.py", "\n\nclass _PlantedView:\n    def who(self):\n        return self.request.headers['x-user']\n",
        ("backend/server.py", "_PlantedView.who")),
    "a read at module scope": (
        "backend/env.py", "\n\nPLANTED = __import__('os').environ\nUSER = PLANTED.get('headers')\n",
        ("backend/env.py", "<module>")),
}


@pytest.mark.parametrize("name", sorted(PLANTED_READERS))
def test_i7_companion_a_second_reader_naming_neither_the_header_nor_the_reader_is_named(name):
    """Each plant goes into a scratch copy of a real backend module's source
    and names neither the identity header nor the one reader; the guard
    names exactly the planted function."""
    path, code, expected = PLANTED_READERS[name]
    sources = _backend_sources()
    planted = {**sources, path: sources[path] + code}
    extra = set(header_reads(planted)) - set(header_reads(sources))
    assert extra == {expected}, (name, extra)
    assert "Tailscale" not in code and "read_request_headers" not in code


# ---------------------------------------------------------------------------
# I8 — the dependency without the middleware
# ---------------------------------------------------------------------------

def test_i8_without_the_middleware_every_gated_route_is_401(tmp_path, monkeypatch):
    from backend.server import require_token
    from tests.test_auth_coverage import PAYLOADS
    app = _proxy_app(tmp_path, monkeypatch)
    app.user_middleware = []          # the gate removed before the stack is built; the proxy config stays
    gated = sorted((method, route.path) for route in app.routes
                   for method in (getattr(route, "methods", None) or ())
                   if any(getattr(d, "call", None) is require_token
                          for d in getattr(getattr(route, "dependant", None), "dependencies", None) or []))
    assert len(gated) >= 26, gated
    client = _client(app)              # the valid bearer, from conftest
    codes = {}
    for method, path in gated:
        body = PAYLOADS.get((method, path))
        kwargs = {"json": body} if body is not None else {}
        codes[(method, path)] = client.request(method, _fill(path), headers={DEFAULT_HEADER: OWNER},
                                               **kwargs).status_code
    print(f"\nI8 {len(gated)} gated routes without the gate: {sorted(set(codes.values()))}")
    assert {k: v for k, v in codes.items() if v != 401} == {}
    assert client.get("/kb/stats", headers={DEFAULT_HEADER: OWNER}).status_code == 200, \
        "an open route answers: nothing but the dependency stands in front of the gated ones"


def test_i8_an_identity_the_gate_did_not_establish_is_not_taken(tmp_path, monkeypatch):
    """An outer wrapper that plants the scope key the gate writes: the gate
    decides afresh (and strips it on the open paths), and token mode has no
    established identity at all. base: the new names."""
    from fastapi import Depends
    from backend.identity import SCOPE_KEY, EstablishedIdentity
    from backend.server import established_identity

    def forging(app):
        async def wrapper(scope, receive, send):
            if scope["type"] == "http":
                scope = {**scope, SCOPE_KEY: EstablishedIdentity(login="mallory@example.test")}
            await app(scope, receive, send)
        return wrapper

    def plant_who(app):
        @app.get("/planted/who")
        async def planted_who(identity=Depends(established_identity)):
            return {"login": identity.login if identity else None}

    proxy = _proxy_app(tmp_path / "p", monkeypatch)
    plant_who(proxy)
    forged = TestClient(forging(proxy), client=LOOPBACK, raise_server_exceptions=False)
    assert forged.get("/planted/who").status_code == 401
    assert forged.get("/planted/who", headers={DEFAULT_HEADER: OWNER}).json() == {"login": OWNER}
    token = _app(tmp_path / "t", monkeypatch)
    plant_who(token)
    assert TestClient(forging(token), raise_server_exceptions=False).get("/planted/who").json() == {"login": None}


def test_v8_a_planted_identity_is_removed_on_the_open_paths(tmp_path, monkeypatch):
    """V8 (W4's close). The gate passes a request on /health or /healthz on
    without deciding it, and first removes any identity an outer layer
    planted on the scope, so nothing below the gate on an open path can take
    one. A probe is planted on each open path (a POST: the gate exempts the
    path whatever the method, and the app's own routes there are GET) and on
    a gated path, under an outer layer that plants a forged verdict on every
    request: the open paths' probes see no identity and no scope key; the
    gated one sees the gate's own verdict (the owner's login), never the
    planted one, so the probe reads what the gate leaves. A pin of the
    base's behaviour: it passes on f3a6385 by construction; its failing case
    is the verifier's mutant MI22 (the removal deleted), killed on the fix
    (SP/w4/fix/mutants_identity.log)."""
    from fastapi import Depends
    from backend.identity import SCOPE_KEY
    from backend.server import established_identity
    app = _proxy_app(tmp_path, monkeypatch)
    seen = []

    async def probe(request: Request, identity=Depends(established_identity)):
        seen.append((request.scope["path"], SCOPE_KEY in request.scope, identity.login if identity else None))
        return {"probed": True}

    paths = sorted(EXEMPT) + ["/planted/probe"]
    for path in paths:
        app.add_api_route(path, probe, methods=["POST"])
    forged = TestClient(_forging(app, "mallory@example.test"), client=LOOPBACK, raise_server_exceptions=False)
    codes = {path: forged.post(path, headers=OWNER_HEADERS).status_code for path in paths}
    print(f"\nV8 ({sys.platform}) probes: {codes}; seen {seen}")
    assert codes == {path: 200 for path in paths}, codes
    assert seen == [("/health", False, None), ("/healthz", False, None), ("/planted/probe", True, OWNER)], seen


# ---------------------------------------------------------------------------
# D2 — the gate is pure ASGI: the stream passes it, the lifespan passes it,
# any other scope type is refused
# ---------------------------------------------------------------------------

def test_d2_the_chat_stream_passes_the_gate_and_is_refused_without_identity(tmp_path, monkeypatch):
    app = _proxy_app(tmp_path, monkeypatch)
    client = _client(app)
    refused = client.post("/chat", json={"message": "what does interval training improve?"})
    assert refused.status_code == 401, refused.status_code
    served = client.post("/chat", json={"message": "what does interval training improve?"},
                         headers={DEFAULT_HEADER: OWNER})
    assert served.status_code == 200 and served.headers["content-type"].startswith("text/event-stream")
    assert _done(served)["lineage_id"]
    from starlette.middleware.base import BaseHTTPMiddleware
    from backend.identity import IdentityGate
    assert not issubclass(IdentityGate, BaseHTTPMiddleware), "/chat streams: the gate must be pure ASGI"


def test_d2_the_lifespan_passes_the_gate_and_a_websocket_is_refused(tmp_path, monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    app = _proxy_app(tmp_path, monkeypatch)
    services = app.state.services
    with TestClient(app, client=LOOPBACK) as client:
        assert services.model_probe["state"] != "not_started", "the lifespan did not run"
        assert client.get("/healthz").status_code == 200
        assert client.get("/kb/stats").status_code == 401
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/healthz", headers={DEFAULT_HEADER: OWNER}):
                pass
    print(f"\nD2 websocket refused with close code {closed.value.code}")
    assert closed.value.code == 1008


def test_d2_a_scope_type_the_gate_does_not_know_is_refused():
    """base: the new name."""
    from backend.identity import IdentityGate, identity_config
    reached = []

    async def app(scope, receive, send):
        reached.append(scope["type"])

    gate = IdentityGate(app, identity_config({"NEXUS_AUTH_MODE": "proxy", "NEXUS_OWNER_IDENTITY": OWNER}))
    with pytest.raises(RuntimeError, match="scope type"):
        asyncio.run(gate({"type": "telepathy"}, None, None))
    asyncio.run(gate({"type": "lifespan"}, None, None))
    assert reached == ["lifespan"]


# ---------------------------------------------------------------------------
# I9 — the login on the record (part 1b: D7, and one vocabulary with D8 and D10)
# ---------------------------------------------------------------------------
#
# Every test below but the token-mode pin was run on the integration branch at
# bbc4bb8 (parts 0, 1 and 2, before 1b's change) and failed there on its
# consequence (SP/w4/p1b/prefix.txt). The token-mode pin holds unchanged
# behaviour, so it passes there by construction; its companion plants the
# regression it exists for and shows the pin fail.

QUESTION = "what does interval training improve?"
BODY_ASSERTED = "alice@example.test"      # what a body asserts: never the owner's login
MISMATCH = "accountable_mismatch"
OWNER_HEADERS = {DEFAULT_HEADER: OWNER}


def _turn(client, headers=None, **body):
    """One `/chat` turn: (the response, its done frame, or None when it was
    not a stream)."""
    resp = client.post("/chat", json={"message": QUESTION, **body}, headers=headers or {})
    return resp, (_done(resp) if resp.status_code == 200 else None)


def _proxy_rig(root: Path, monkeypatch):
    app = _proxy_app(root, monkeypatch)
    return app.state.services, _client(app)


def what_a_refusal_must_leave(svc) -> dict:
    """Everything a turn writes that a refused request must leave as it was:
    the lineage records, both refusal ledgers, the work units with their
    junction, and the loop's turn count."""
    return {"lineage_records": svc.lineage.count(),
            "refusal_events": svc.recalibration.refusal_coverage()["events"],
            "work_units": svc.work_units.counts(),
            "turn_count": svc.state.turn_count}


def test_i9_a_proxy_turn_records_the_login_as_proxy_and_verifies(tmp_path, monkeypatch):
    """D7 and D10. A turn through `/chat` in proxy mode whose body names no
    accountable: the record's `accountable_identity` is the login the gate
    established, its `identity_mechanism` is `proxy` (stored, so sealed),
    and `/lineage/{id}/verify` reads intact. base (bbc4bb8): fails on its
    consequence, the record carries no accountable and the default."""
    svc, client = _proxy_rig(tmp_path, monkeypatch)
    resp, done = _turn(client, OWNER_HEADERS)
    assert resp.status_code == 200, resp.status_code
    rec = svc.lineage.get(done["lineage_id"])
    page = client.get(f"/lineage/{rec.lineage_id}", headers=OWNER_HEADERS).json()
    verify = client.get(f"/lineage/{rec.lineage_id}/verify", headers=OWNER_HEADERS).json()
    print(f"\nI9 proxy turn ({sys.platform}): accountable_identity={rec.accountable_identity!r} "
          f"identity_mechanism={rec.identity_mechanism!r}; verify {verify.get('verdict')} {verify['layers']['row']}")
    assert (rec.accountable_identity, rec.identity_mechanism) == (OWNER, IDENTITY_PROXY)
    assert (page["accountable_identity"], page["identity_mechanism"]) == (OWNER, IDENTITY_PROXY)
    assert verify["verdict"] == "intact" and verify["layers"]["row"] == {"ok": 1, "failed": 0, "failures": []}


def test_i9_a_body_accountable_equal_to_the_login_or_none_is_the_login(tmp_path, monkeypatch):
    """D7. Absent, empty, whitespace, the login, and the login padded (equal
    after `_clean_field`): each turn is served and its record is the login,
    `proxy`, intact. base (bbc4bb8): fails on its consequence (the absent
    ones record no accountable; the equal ones record the default)."""
    svc, client = _proxy_rig(tmp_path, monkeypatch)
    cases = {"absent": {}, "empty": {"accountable": ""}, "whitespace": {"accountable": "   "},
             "the login": {"accountable": OWNER}, "the login, padded": {"accountable": f"  {OWNER}  "}}
    seen = {}
    for name, body in cases.items():
        resp, done = _turn(client, OWNER_HEADERS, **body)
        rec = svc.lineage.get(done["lineage_id"]) if done else None
        seen[name] = (resp.status_code, rec and rec.accountable_identity, rec and rec.identity_mechanism,
                      bool(rec) and svc.lineage.verify_record(rec.lineage_id)["record_intact"])
    print(f"\nI9 equal or none ({sys.platform}): {seen}")
    assert seen == {name: (200, OWNER, IDENTITY_PROXY, True) for name in cases}


def test_i9_a_body_accountable_that_differs_is_refused_by_name_and_writes_nothing(tmp_path, monkeypatch):
    """D7, AT's rule. A body `accountable` that differs from the login after
    cleaning is 422 `accountable_mismatch` with one fixed sentence that
    echoes neither value; no turn runs and nothing is written: no lineage
    record, no refusal row in either ledger, no work unit or junction row.
    The comparison is exact, as the gate's is. The field cap keeps scope's
    position, ahead of it. base (bbc4bb8): fails on its consequence (each
    is served, 200, and recorded as the body asserts)."""
    svc, client = _proxy_rig(tmp_path, monkeypatch)
    differing = {"another login": OTHER, "padded, another login": f"  {OTHER} ",
                 "the login in upper case": OWNER.upper(), "the login and more": OWNER + "x"}
    seen, hints = {}, set()
    for name, value in differing.items():
        before = what_a_refusal_must_leave(svc)
        resp = client.post("/chat", json={"message": QUESTION, "accountable": value, "work_unit": "WU-refused"},
                           headers=OWNER_HEADERS)
        after = what_a_refusal_must_leave(svc)
        body = resp.json() if resp.headers.get("content-type") == "application/json" else {}
        hints.add(body.get("hint"))
        seen[name] = {"status": resp.status_code, "error": body.get("error"), "nothing written": before == after,
                      "echoed": [v for v in (value.strip(), OWNER) if v in resp.text]}
    print(f"\nI9 differing ({sys.platform}): {seen}\n   the sentence: {sorted(map(str, hints))}")
    assert seen == {name: {"status": 422, "error": MISMATCH, "nothing written": True, "echoed": []}
                    for name in differing}
    assert len(hints) == 1 and all(isinstance(h, str) and h for h in hints), "one fixed sentence"
    assert svc.work_units.get("WU-refused") is None
    too_long = client.post("/chat", json={"message": QUESTION, "accountable": "x" * (MAX_SCOPE_CHARS + 1)},
                           headers=OWNER_HEADERS)
    assert too_long.status_code == 422 and too_long.json()["error"] == "accountable_too_long"
    resp, done = _turn(client, OWNER_HEADERS, accountable=OWNER)
    assert resp.status_code == 200 and svc.lineage.get(done["lineage_id"]).accountable_identity == OWNER, \
        "the refusals were per request: the next one that agrees is served"


def test_i9_a_unit_first_named_on_a_proxy_turn_carries_the_login(tmp_path, monkeypatch):
    """D7. A unit first named on a proxy-mode turn: its first-mention row
    carries the login. Unchanged and declared (the comment at /chat): its
    `opened_by` is the body's actor fields; the session pseudo-unit carries
    no accountable while the turn's record carries the login; a unit first
    named by the assertion route carries none. base (bbc4bb8): fails on its
    consequence (the unit row carries no accountable)."""
    svc, client = _proxy_rig(tmp_path, monkeypatch)
    resp, done = _turn(client, OWNER_HEADERS, work_unit="WU-proxy", actor_id="steward-7", actor_role="person")
    assert resp.status_code == 200 and done["unit_first_seen"] is True and done["work_unit_id"] == "WU-proxy"
    unit = svc.work_units.get("WU-proxy")
    print(f"\nI9 unit ({sys.platform}): accountable_identity={unit['accountable_identity']!r} "
          f"opened_by={unit['opened_by']}")
    assert unit["accountable_identity"] == OWNER
    assert unit["opened_by"] == actor_fields({"id": "steward-7", "role": "person"}), "the actor stays asserted"
    _resp, pseudo = _turn(client, OWNER_HEADERS)
    assert pseudo["work_unit_id"] == pseudo_unit_id("default")
    assert svc.lineage.get(pseudo["lineage_id"]).accountable_identity == OWNER
    assert svc.work_units.get(pseudo_unit_id("default"))["accountable_identity"] is None
    asserted = client.post("/kb/assert/confirm", json={"sentence": "The dose was 600mg.", "confirmed": True,
                                                       "work_unit": "WU-asserted"}, headers=OWNER_HEADERS)
    assert asserted.status_code == 200 and asserted.json()["attribution"]["unit_first_seen"] is True, asserted.text
    assert svc.work_units.get("WU-asserted")["accountable_identity"] is None


def test_i9_a_close_in_proxy_mode_is_never_missing_its_accountable(tmp_path, monkeypatch):
    """D7. A close on a proxy-mode turn whose body names no accountable is
    not flagged `accountability.missing`: the login is the accountable, on
    the record and so for the close; the unit's `closed_by` stays the body's
    actor fields (declared). base (bbc4bb8): fails on its consequence (the
    close is flagged and the record names no one)."""
    svc, client = _proxy_rig(tmp_path, monkeypatch)
    _turn(client, OWNER_HEADERS, work_unit="WU-close")
    resp, done = _turn(client, OWNER_HEADERS, work_unit="WU-close", decision_boundary="close")
    rec = svc.lineage.get(done["lineage_id"])
    print(f"\nI9 close ({sys.platform}): unit_closed={done['unit_closed']} flags={done.get('flags')} "
          f"record=({rec.accountable_identity!r}, {rec.attribution_flags})")
    assert done["unit_closed"] is True and "flags" not in done
    assert (rec.accountable_identity, rec.attribution_flags, rec.boundary_asserted) == (OWNER, (), "close")
    assert svc.work_units.get("WU-close")["closed_by"] == actor_fields(None)


def test_i9_whoami_reports_the_mechanism_the_turns_record_carries_in_each_mode(tmp_path, monkeypatch):
    """One vocabulary (D8, D10): the word `/whoami` reports is the value the
    turn's record carries (`/lineage/{id}`, which names the default), and
    the stored column is NULL for `asserted`; both words are members of
    `backend/memory/lineage.py`'s IDENTITY_MECHANISMS. base (bbc4bb8):
    fails on its consequence in proxy mode (`/whoami` says `proxy`, the
    record `asserted`)."""
    seen = {}
    for mode, headers in (("token", {}), ("proxy", OWNER_HEADERS)):
        app = _app(tmp_path / mode, monkeypatch, mode=mode, owner=OWNER)
        svc, client = app.state.services, _client(app)
        word = client.get("/whoami", headers=headers).json()["identity_mechanism"]
        resp, done = _turn(client, headers)
        page = client.get(f"/lineage/{done['lineage_id']}", headers=headers).json()
        seen[mode] = (word, page["identity_mechanism"], svc.lineage.get(done["lineage_id"]).identity_mechanism)
    print(f"\nI9 one vocabulary ({sys.platform}): (whoami, the record's page, stored) {seen}")
    assert seen == {"token": (IDENTITY_ASSERTED, IDENTITY_ASSERTED, None),
                    "proxy": (IDENTITY_PROXY, IDENTITY_PROXY, IDENTITY_PROXY)}
    assert {word for word, _page, _stored in seen.values()} <= set(IDENTITY_MECHANISMS)


TOKEN_CASES = (
    ("no identity header", {}, False),
    ("the owner's identity header", OWNER_HEADERS, False),
    ("the header and the gate's verdict planted on the scope", OWNER_HEADERS, True),
)
# The record's own id and instant, and the hash over them: the only fields in
# which the same first turn on two fresh rigs differs (measured on the base,
# with and without a planted header: SP/w4/p1b/probe).
PER_RECORD = ("lineage_id", "ts", "record_hash")


def _forging(app, login: str):
    """An outer layer that plants the gate's verdict on every request."""
    from backend.identity import SCOPE_KEY, EstablishedIdentity

    async def wrapper(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, SCOPE_KEY: EstablishedIdentity(login=login)}
        await app(scope, receive, send)
    return wrapper


def token_mode_problems(root: Path, monkeypatch) -> tuple[list[str], dict]:
    """One fresh token-mode rig per case, the owner set as a proxy
    deployment would set it (token mode reads none of it). Turn A asserts
    `BODY_ASSERTED` and names a unit; its record must carry the body's value
    and the default, verify, give the unit's row the body's value, and equal
    the first case's record in every field but `PER_RECORD`. Turn B names
    no one; its record must name no one, with the default."""
    problems, seen, first = [], {}, None
    for i, (name, headers, forge) in enumerate(TOKEN_CASES):
        app = _app(root / f"token{i}", monkeypatch, owner=OWNER)
        svc = app.state.services
        client = TestClient(_forging(app, OWNER) if forge else app, client=LOOPBACK,
                            raise_server_exceptions=False)
        resp, done = _turn(client, headers, accountable=BODY_ASSERTED, work_unit="WU-token")
        if done is None:
            problems.append(f"{name}: turn A was answered {resp.status_code}, not served")
        else:
            rec = svc.lineage.get(done["lineage_id"])
            fields = {k: v for k, v in rec.to_dict().items() if k not in PER_RECORD}
            unit = svc.work_units.get("WU-token") or {}
            seen[name] = (rec.accountable_identity, rec.identity_mechanism, unit.get("accountable_identity"))
            if (rec.accountable_identity, rec.identity_mechanism, fields["identity_mechanism"]) != (
                    BODY_ASSERTED, None, IDENTITY_ASSERTED):
                problems.append(f"{name}: turn A recorded accountable_identity={rec.accountable_identity!r}, "
                                f"identity_mechanism={rec.identity_mechanism!r}; the body asserted "
                                f"{BODY_ASSERTED!r}")
            if unit.get("accountable_identity") != BODY_ASSERTED:
                problems.append(f"{name}: the unit's row carries {unit.get('accountable_identity')!r}")
            if not svc.lineage.verify_record(rec.lineage_id)["record_intact"]:
                problems.append(f"{name}: turn A's record does not verify")
            first = fields if first is None else first
            moved = sorted(k for k in first if fields.get(k) != first[k])
            if moved:
                problems.append(f"{name}: the planted identity moved {moved}")
        resp, done = _turn(client, headers)
        if done is None:
            problems.append(f"{name}: turn B was answered {resp.status_code}, not served")
            continue
        rec = svc.lineage.get(done["lineage_id"])
        seen[f"{name}, no body accountable"] = (rec.accountable_identity, rec.identity_mechanism)
        if (rec.accountable_identity, rec.identity_mechanism) != (None, None):
            problems.append(f"{name}: turn B named no one and recorded accountable_identity="
                            f"{rec.accountable_identity!r}, identity_mechanism={rec.identity_mechanism!r}")
    return problems, seen


def test_i9_token_mode_records_the_body_and_a_planted_identity_moves_no_field(tmp_path, monkeypatch):
    """I9's token half (D13): in token mode the record's `accountable_identity`
    is the body's, as asserted, its `identity_mechanism` NULL, and a planted
    identity header (the owner's login, the owner set), or the gate's
    verdict planted on the scope with it, moves no field of the record. A pin
    of unchanged behaviour: it passes on the base by construction; the
    companion below is its failing case."""
    problems, seen = token_mode_problems(tmp_path, monkeypatch)
    print(f"\nI9 token mode ({sys.platform}): {seen}")
    assert problems == [], problems


def _leaky_established_identity(request: Request):
    """The regression the pin exists for: an identity taken from the
    identity header whatever the mode (through the one reader, so the plant
    is a dependency and not a second reader of the headers)."""
    from backend.identity import EstablishedIdentity, read_request_headers
    login = read_request_headers(request.scope, request.app.state.identity_config.header_key).identity
    return EstablishedIdentity(login=login) if login else None


def test_i9_companion_an_identity_taken_in_token_mode_fails_the_pin(tmp_path, monkeypatch):
    """COMPANION (OQ-16 rules 1 and 3). `established_identity` replaced, before
    the factory, by a dependency that takes the header's login in token mode
    too, a plant the pin's source does not name: the pin fails, naming the
    record fields the header moved and the turns it refused. base (bbc4bb8):
    fails because the base `/chat` takes no dependency for the plant to
    replace, so the pin holds (the new dependency, in effect: declared)."""
    import backend.server as server
    monkeypatch.setattr(server, "established_identity", _leaky_established_identity)
    with pytest.raises(AssertionError) as failed:
        test_i9_token_mode_records_the_body_and_a_planted_identity_moves_no_field(tmp_path, monkeypatch)
    text = str(failed.value)
    print(f"\nI9 companion ({sys.platform}): the pin failed: {text[:600]}")
    assert "accountable_identity" in text and "identity_mechanism" in text and "answered 422" in text


# ---------------------------------------------------------------------------
# I10 — whoami
# ---------------------------------------------------------------------------

WHOAMI_KEYS = {"auth_mode", "login", "identity_mechanism", "identity_header", "owner_set", "note"}


def test_i10_whoami_in_both_modes(tmp_path, monkeypatch):
    from tests.test_just_culture_guard import person_field_population
    token = _client(_app(tmp_path / "t", monkeypatch))
    resp = token.get("/whoami")
    assert resp.status_code == 200, resp.status_code
    mine = resp.json()
    print(f"\nI10 token: {mine}")
    assert {k: v for k, v in mine.items() if k != "note"} == {
        "auth_mode": "token", "login": None, "identity_mechanism": "asserted", "identity_header": None,
        "owner_set": False}
    token.headers.pop("Authorization")
    assert token.get("/whoami").status_code == 401, "gated by the token in token mode"
    proxy = _client(_proxy_app(tmp_path / "p", monkeypatch))
    theirs = proxy.get("/whoami", headers={DEFAULT_HEADER: OWNER}).json()
    print(f"I10 proxy: {theirs}")
    assert {k: v for k, v in theirs.items() if k != "note"} == {
        "auth_mode": "proxy", "login": OWNER, "identity_mechanism": "proxy", "identity_header": DEFAULT_HEADER,
        "owner_set": True}
    assert set(mine) == set(theirs) == WHOAMI_KEYS and mine["note"] and theirs["note"]
    assert not (WHOAMI_KEYS & person_field_population()), "whoami is no per-person surface"


def test_i10_the_ratchet_counts_the_gated_reads_and_the_documents_say_so(tmp_path, monkeypatch):
    from tests.test_auth_coverage import GATED_READ_PREFIXES, SECURITY_DOCUMENTS, derive_gets, posture_violations
    gets = derive_gets(_app(tmp_path, monkeypatch))
    gated = sorted(p for p, g in gets.items() if g)
    print(f"\nI10 gated reads: {gated}")
    assert "/whoami" in gated and len(gated) == 17       # twelve with W4's /whoami; W6 added /work-units/events, W8 /lineage/{id}/proof, W9 the credential and the commitment receipt, W10b the action receipt
    assert "/whoami" in GATED_READ_PREFIXES
    for name in SECURITY_DOCUMENTS:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "seventeen routes" in text and all(f"{n} routes" not in text            # W10b: the action receipt
                                                  for n in ("sixteen", "fifteen", "fourteen", "thirteen", "twelve",
                                                            "eleven")), name
        assert not posture_violations(text), (name, posture_violations(text))


# ---------------------------------------------------------------------------
# D8 — the console: "running as <login>" in proxy mode, nothing new in token mode
# ---------------------------------------------------------------------------

CONSOLE = REPO / "frontend" / "nexus_ui.html"
NODE = shutil.which("node")
WHOAMI_BLOCK = ("// W4 part 1 — who this console runs as", "init();")
ATTRIBUTION_LINES = ("// W4 part 1 — in proxy mode the identity proxy supplies", "if (closeBtn")

CONSOLE_HARNESS = r"""
const [whoamiBlock, attributionLines, answerJson] = JSON.parse(require('fs').readFileSync(process.argv[1], 'utf8'));
function element(tag) {
  const el = {tag, className: '', id: '', textContent: '', value: '', style: {}, children: [],
              appendChild(c) { this.children.push(c); return c; }};
  return el;
}
(async () => {
  const accountable = Object.assign(element('input'), {id: 'accountable-input', value: 'typed-by-hand'});
  const unit = Object.assign(element('input'), {id: 'unit-input', value: ''});
  const classes = new Set();
  const body = Object.assign(element('body'), {classList: {add: c => classes.add(c)}});
  global.document = {body, createElement: element,
                     getElementById: id => ({'accountable-input': accountable, 'unit-input': unit})[id] || null};
  global.window = {};
  const asked = [];
  const answer = JSON.parse(answerJson);
  global.fetch = async url => { asked.push(url); return answer === null ? {ok: false} : {ok: true, json: async () => answer}; };
  eval(whoamiBlock.replace(/init\(\);\s*$/, ''));
  await window.NEXUS_WHOAMI;
  const attribute = new Function('reqBody', 'unitEl', 'accEl', 'window',
    'return (async () => {' + attributionLines + '\n return reqBody; })();');
  const reqBody = await attribute({message: 'q'}, unit, accountable, window);
  console.log(JSON.stringify({asked, bar: body.children.map(b => ({cls: b.className, text: b.children.map(c => c.textContent)})),
                              classes: [...classes], accountableShown: accountable.style.display !== 'none', reqBody}));
})();
"""


def _between(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i:text.index(end, i) + len(end)]


def _run_console(answer, root: Path) -> dict:
    """The two pieces of the page, and the answer, handed to node in a file
    (stdin reads are not portable across the CI runners' node builds)."""
    page = CONSOLE.read_text(encoding="utf-8")
    payload = root / "console.json"
    payload.write_text(json.dumps([_between(page, *WHOAMI_BLOCK),
                                   _between(page, *ATTRIBUTION_LINES).rsplit("if (closeBtn", 1)[0],
                                   json.dumps(answer)]), encoding="utf-8")
    proc = _proc.run([NODE, "-e", CONSOLE_HARNESS, str(payload)], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(NODE is None, reason="node is not on PATH: the console's whoami block is run in node")
def test_d8_the_console_shows_running_as_in_proxy_mode_and_sends_no_accountable_of_its_own(tmp_path):
    """The page's own code, run in node against a minimal DOM: the proxy
    answer builds the status bar and hides the accountable box, and the
    chat body then carries no `accountable` though one was typed. base:
    the new block (the base page has no /whoami code; SP/w4/p1/prefix_d8_console.txt)."""
    proxy = _run_console({"auth_mode": "proxy", "login": OWNER}, tmp_path)
    print(f"\nD8 proxy: {proxy}")
    assert proxy["asked"] == ["/whoami"]
    assert proxy["bar"] == [{"cls": "statusbar", "text": [f"running as {OWNER}"]}]
    assert proxy["classes"] == ["has-statusbar"] and proxy["accountableShown"] is False
    assert "accountable" not in proxy["reqBody"]


@pytest.mark.skipif(NODE is None, reason="node is not on PATH: the console's whoami block is run in node")
def test_d8_in_token_mode_or_with_no_answer_nothing_visible_changes(tmp_path):
    """base: the new block, as above."""
    for answer in ({"auth_mode": "token", "login": None}, None):
        token = _run_console(answer, tmp_path)
        print(f"\nD8 token ({answer}): {token}")
        assert token["bar"] == [] and token["classes"] == [] and token["accountableShown"] is True
        assert token["reqBody"]["accountable"] == "typed-by-hand", "the body is as it was"


# ---------------------------------------------------------------------------
# I11 — /healthz says the mode
# ---------------------------------------------------------------------------

def test_i11_healthz_says_the_mode_and_never_the_owners_login(tmp_path, monkeypatch):
    token = _client(_app(tmp_path / "t", monkeypatch), REMOTE).get("/healthz").json()
    assert token["identity"] == {"mode": "token", "header": None, "owner_set": False}
    proxy = _proxy_app(tmp_path / "p", monkeypatch)
    for peer, headers in ((REMOTE, {}), (LOOPBACK, {}), (LOOPBACK, {DEFAULT_HEADER: OWNER})):
        resp = _client(proxy, peer).get("/healthz", headers=headers)
        assert resp.status_code == 200, (peer, headers)
        assert resp.json()["identity"] == {"mode": "proxy", "header": DEFAULT_HEADER, "owner_set": True}
        assert OWNER not in resp.text, "the owner's login on an open route"


# ---------------------------------------------------------------------------
# I12 — the header's shapes
# ---------------------------------------------------------------------------

def test_i12_twice_empty_or_blank_is_no_identity(tmp_path, monkeypatch):
    client = _client(_proxy_app(tmp_path, monkeypatch))
    cases = {
        "sent twice": [(DEFAULT_HEADER, OWNER), (DEFAULT_HEADER, OWNER)],
        "empty": [(DEFAULT_HEADER, "")],
        "whitespace": [(DEFAULT_HEADER, "   ")],
    }
    got = {name: client.get("/kb/stats", headers=headers) for name, headers in cases.items()}
    print(f"\nI12 {[(n, r.status_code) for n, r in got.items()]}")
    assert {n: r.status_code for n, r in got.items()} == {n: 401 for n in cases}
    assert all(_detail(r).startswith("unauthorized: no identity") for r in got.values())
    assert client.get("/kb/stats", headers={DEFAULT_HEADER: f"  {OWNER} "}).status_code == 200, "stripped"


def test_i12_the_configured_header_is_the_one_read(tmp_path, monkeypatch):
    client = _client(_proxy_app(tmp_path, monkeypatch, header="Cf-Access-Authenticated-User-Email"))
    assert client.get("/kb/stats", headers={"Cf-Access-Authenticated-User-Email": OWNER}).status_code == 200
    assert client.get("/kb/stats", headers={"cf-access-authenticated-user-email": OWNER}).status_code == 200
    assert client.get("/kb/stats", headers={DEFAULT_HEADER: OWNER}).status_code == 401, "the default is ignored"


def test_i12_the_reader_one_header_at_a_time():
    """base: the new name."""
    from backend.identity import read_request_headers
    key = b"tailscale-user-login"

    def scope(*pairs):
        return {"type": "http", "headers": [(k, v) for k, v in pairs]}

    assert read_request_headers(scope((key, OWNER.encode())), key).identity == OWNER
    assert read_request_headers(scope((b"Tailscale-User-Login", OWNER.encode())), key).identity == OWNER
    for shapes in ((), ((key, b""),), ((key, b"  "),), ((key, b"a"), (key, b"a")), ((key, b"\xff\xfe"),)):
        assert read_request_headers(scope(*shapes), key).identity is None, shapes
    view = read_request_headers(scope((b"x-forwarded-for", b"198.51.100.7, 127.0.0.1:4444"),
                                      (b"x-forwarded-for", b"[::1]:5555")), key)
    assert {("198.51.100.7", 0), ("127.0.0.1", 4444), ("::1", 5555)} <= set(view.forwarded)
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.identity = "x"


def test_i12_a_header_name_no_request_can_carry_is_refused_at_startup():
    """base: the new name."""
    from backend.identity import AuthConfigError, identity_config
    with pytest.raises(AuthConfigError, match="NEXUS_IDENTITY_HEADER='X User'"):
        identity_config({"NEXUS_AUTH_MODE": "proxy", "NEXUS_IDENTITY_HEADER": "X User"})
    assert identity_config({"NEXUS_IDENTITY_HEADER": "X User"}).mode == "token", "token mode reads no header"


# ---------------------------------------------------------------------------
# I13 — the peer's shapes
# ---------------------------------------------------------------------------

LOOPBACK_PEERS = [("127.0.0.1", 40123), ("127.0.0.2", 1), ("127.255.255.254", 65535), ("::1", 40123),
                  ("::ffff:127.0.0.1", 40123), ["127.0.0.1", 40123]]
NOT_LOOPBACK_PEERS = [None, ("testclient", 50000), ("not-an-ip", 40123), ("localhost", 40123), ("0.0.0.0", 40123),
                      ("::", 40123), ("fe80::1", 40123), ("169.254.10.1", 40123), ("10.0.0.5", 40123),
                      ("192.168.1.5", 40123), ("172.18.0.5", 40123), ("203.0.113.9", 40123), ("8.8.8.8", 40123),
                      ("::ffff:10.0.0.5", 40123), ("::ffff:8.8.8.8", 40123), ("::7f00:1", 40123),
                      "/run/nexus.sock", ("/run/nexus.sock", None), ("127.0.0.1",), ("127.0.0.1", 0), ("::1", 0),
                      ("127.0.0.1", -1), ("127.0.0.1", 65536), ("127.0.0.1", "40123"), ("127.0.0.1", True),
                      (b"127.0.0.1", 40123)]


def test_i13_the_peers_shapes():
    """base: the new name."""
    from backend.identity import is_loopback_peer
    wrong = [p for p in LOOPBACK_PEERS if not is_loopback_peer(p)] + \
            [p for p in NOT_LOOPBACK_PEERS if is_loopback_peer(p)]
    assert wrong == [], wrong


def test_i13_port_zero_on_a_loopback_address_is_refused_as_rewritten(tmp_path, monkeypatch):
    app = _proxy_app(tmp_path, monkeypatch)
    for host in ("127.0.0.1", "::1"):
        resp = _client(app, (host, 0)).get("/kb/stats", headers={DEFAULT_HEADER: OWNER})
        assert resp.status_code == 401 and "rewritten" in _detail(resp), (host, resp.status_code, _detail(resp))
    assert _client(app, ("127.0.0.1", 1)).get("/kb/stats", headers={DEFAULT_HEADER: OWNER}).status_code == 200


def test_i13_no_refusal_echoes_the_presented_login(tmp_path, monkeypatch):
    app = _proxy_app(tmp_path, monkeypatch)
    presented = "presented-login@example.test"
    for peer in (REMOTE, LOOPBACK, ("127.0.0.1", 0)):
        resp = _client(app, peer).get("/kb/stats", headers={DEFAULT_HEADER: presented})
        assert resp.status_code in (401, 403) and presented not in resp.text, (peer, resp.status_code)


# ---------------------------------------------------------------------------
# V1 — a cross-site request is the tester's in proxy mode (W4's close)
# ---------------------------------------------------------------------------
#
# The verifier forged `/kb/seed/offline?confirmed=true` (10 claims and a work
# unit), `/kb/trails/backfill/confirm?confirmed=true` and `/kairos/cycle`
# with a plain form POST carrying the owner's login, as the identity proxy
# attaches it to whatever the tester's browser sends. The browser says where
# a request came from in Sec-Fetch-Site; NEXUS cannot work out its own
# origin (it reads no Host, and a proxy may rewrite it), so the rule reads
# that header, and refuses Origin without it (a browser too old to send
# Sec-Fetch-Site sends Origin on a POST: it fails closed).

SAFE = ("GET", "HEAD", "OPTIONS")            # the rule's words, never the module's constant
EVIL = "https://evil.example"
CONSOLE_ORIGIN = "https://nexus.example.test"  # the console's own origin, as the browser names it
FORM = (("Content-Type", "application/x-www-form-urlencoded"),)
# One shape per condition of the rule, each alone, then two of "anything else".
REFUSED_SHAPES = {
    "Sec-Fetch-Site: cross-site": (("Sec-Fetch-Site", "cross-site"),),
    "Sec-Fetch-Site: same-site": (("Sec-Fetch-Site", "same-site"),),
    "Sec-Fetch-Site: none": (("Sec-Fetch-Site", "none"),),
    "Origin alone (a browser that sends no Sec-Fetch-Site)": (("Origin", EVIL),),
    "Sec-Fetch-Site: a value no browser sends": (("Sec-Fetch-Site", "Same-Origin"),),
    "Sec-Fetch-Site twice": (("Sec-Fetch-Site", "same-origin"), ("Sec-Fetch-Site", "cross-site")),
}
SAME_ORIGIN_SHAPE = "Sec-Fetch-Site: same-origin, with the console's own Origin"
ALLOWED_SHAPES = {
    SAME_ORIGIN_SHAPE: (("Sec-Fetch-Site", "same-origin"), ("Origin", CONSOLE_ORIGIN)),
    "neither header (a client that is no browser)": (),
}


def _cell(resp) -> tuple:
    return resp.status_code, _detail(resp)


def unsafe_targets(app) -> list[tuple[str, str]]:
    """(method, url) for every method a route of the app declares but GET,
    HEAD and OPTIONS, from `app.routes` through I3's derivation."""
    return [(method, url) for method, url in served_targets(app) if method not in SAFE]


def cross_site_matrix(app, targets) -> dict:
    """{(method, url): {shape: (status, detail)}}: each target sent with the
    owner's login from a loopback peer under every refused and allowed shape."""
    client = _client(app)
    out = {}
    for method, url in targets:
        out[(method, url)] = {
            shape: _cell(client.request(method, url, headers=[(DEFAULT_HEADER, OWNER), *FORM, *pairs]))
            for shape, pairs in {**REFUSED_SHAPES, **ALLOWED_SHAPES}.items()}
    return out


def cross_site_problems(matrix: dict) -> list[str]:
    problems = []
    for (method, url), row in sorted(matrix.items()):
        problems += [f"{method} {url} {shape}: {row[shape][0]} (want 403, the cross-site refusal)"
                     for shape in REFUSED_SHAPES if row[shape][0] != 403]
        problems += [f"{method} {url} {shape}: {row[shape]} (want neither 401 nor 403: not the gate's)"
                     for shape in ALLOWED_SHAPES if row[shape][0] in (401, 403)]
    return problems


def test_v1_every_unsafe_route_refuses_a_cross_site_request_and_serves_a_same_origin_one(tmp_path, monkeypatch):
    """Derived from `app.routes`: every route method but GET, HEAD and
    OPTIONS, sent with the owner's login from a loopback peer. Under each
    refused shape it is 403 with one fixed sentence that names neither the
    origin nor the login; with Sec-Fetch-Site `same-origin` (and the
    console's own Origin, as a browser sends both on a POST) and with
    neither header it is not refused by the gate. base (f3a6385): fails on
    its consequence (every refused shape is served)."""
    app = _proxy_app(tmp_path, monkeypatch)
    targets = unsafe_targets(app)
    assert {("POST", "/kb/seed/offline"), ("POST", "/kb/trails/backfill/confirm"), ("POST", "/kairos/cycle"),
            ("POST", "/chat"), ("DELETE", "/model/override")} <= set(targets), targets
    matrix = cross_site_matrix(app, targets)
    problems = cross_site_problems(matrix)
    print(f"\nV1 ({sys.platform}) {len(targets)} unsafe targets ({sorted({m for m, _u in targets})}) x "
          f"{len(REFUSED_SHAPES)} refused and {len(ALLOWED_SHAPES)} allowed shapes; problems {len(problems)}")
    assert not problems, "\n".join(problems[:40])
    from backend.identity import CROSS_SITE
    said = {row[shape][1] for row in matrix.values() for shape in REFUSED_SHAPES}
    print(f"V1 the sentence: {sorted(said)}")
    assert said == {CROSS_SITE}, said
    assert "evil" not in CROSS_SITE.lower() and CONSOLE_ORIGIN not in CROSS_SITE and OWNER not in CROSS_SITE


def what_a_forged_post_must_leave(svc, unit: str) -> dict:
    """What the three query writers write: claims and a work unit (the
    seed), the trail backfill's mark (under `coverage`, None until the
    trail cursor exists), a KAIROS cycle."""
    return {"claims": svc.store.count(), "unit": svc.work_units.get(unit) is not None,
            "trails backfilled": bool((svc.trails.status()["coverage"] or {}).get("backfilled")),
            "a cycle ran": svc.kairos.last_cycle is not None}


def test_v1_a_forged_post_to_the_three_query_writers_writes_nothing(tmp_path, monkeypatch):
    """The three writers whose inputs are in the query string, so a plain
    form can reach them: under every refused shape each is 403 and the store
    is as it was (no claim, no work unit, no trail backfill, no cycle). The
    same three from the console's own origin then write, so the observation
    can see a write; with neither header they are served. base (f3a6385):
    fails on its consequence (the claims seeded, the unit made, the backfill
    marked, a cycle run)."""
    app = _proxy_app(tmp_path, monkeypatch)
    svc, client = app.state.services, _client(app)
    unit = "WU-forged"
    writers = {"the offline seed": f"/kb/seed/offline?confirmed=true&work_unit={unit}",
               "the trail backfill": "/kb/trails/backfill/confirm?confirmed=true", "a KAIROS cycle": "/kairos/cycle"}
    before = what_a_forged_post_must_leave(svc, unit)
    assert before == {"claims": 0, "unit": False, "trails backfilled": False, "a cycle ran": False}, before
    forged = {(shape, name): client.post(url, headers=[(DEFAULT_HEADER, OWNER), *FORM, *pairs]).status_code
              for shape, pairs in REFUSED_SHAPES.items() for name, url in writers.items()}
    after = what_a_forged_post_must_leave(svc, unit)
    own = {name: client.post(url, headers=[(DEFAULT_HEADER, OWNER), *FORM, *ALLOWED_SHAPES[SAME_ORIGIN_SHAPE]])
           .status_code for name, url in writers.items()}
    written = what_a_forged_post_must_leave(svc, unit)
    plain = {name: client.post(url, headers={DEFAULT_HEADER: OWNER}).status_code for name, url in writers.items()}
    print(f"\nV1 ({sys.platform}) forged: {sorted(set(forged.values()))}; the store {before} -> {after}; "
          f"same-origin {own} -> {written}; neither header {plain}")
    assert forged == {key: 403 for key in forged} and after == before, (forged, after)
    assert own == {name: 200 for name in writers}, own
    assert written["claims"] > 0 and written["unit"] and written["trails backfilled"] and written["a cycle ran"], \
        written
    assert plain == {name: 200 for name in writers}, plain


def test_v1_companion_a_route_planted_on_the_built_app_is_held_with_no_edit(tmp_path, monkeypatch):
    """COMPANION (OQ-16 rules 1 and 3). A route planted after the factory
    returned, with two methods no route of the app declares and no string
    in the rule's source names (PATCH and PUT), whose handler records every
    call. The derivation finds both; under each refused shape it is 403 and
    the handler never runs; each allowed shape reaches it. No edit to the
    matrix. base (f3a6385): fails on its consequence (the handler runs for
    every shape)."""
    from backend import identity
    app = _proxy_app(tmp_path, monkeypatch)
    declared = {method for method, _url in unsafe_targets(app)}
    named = {n.value for n in ast.walk(ast.parse(Path(identity.__file__).read_text(encoding="utf-8")))
             if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not {"PATCH", "PUT"} & (declared | named), "the plant must be a member the rule's source does not name"
    reached = []

    @app.api_route("/planted/after-the-factory", methods=["PATCH", "PUT"])
    async def planted_writer(request: Request):
        reached.append(request.method)
        return {"planted": True}

    targets = [t for t in unsafe_targets(app) if t[1] == "/planted/after-the-factory"]
    assert targets == [("PATCH", "/planted/after-the-factory"), ("PUT", "/planted/after-the-factory")], targets
    matrix = cross_site_matrix(app, targets)
    print(f"\nV1 companion ({sys.platform}): {matrix}; reached {reached}")
    assert not cross_site_problems(matrix), cross_site_problems(matrix)
    assert sorted(reached) == sorted(m for m, _u in targets for _shape in ALLOWED_SHAPES), reached


def test_v1_the_rule_is_the_gates_last_step_and_token_mode_has_none(tmp_path, monkeypatch):
    """D2's order with the new step after the owner checks: a cross-site
    request from a remote peer is 401 (the peer's rule), with no identity
    401, with another login 403 not the owner, with no owner set 403 owner
    not set, and with the owner's login 403 the cross-site sentence (the
    case the rule exists for: the owner's own browser). Token mode is
    untouched, zero middleware: a cross-site request carrying the bearer is
    served as before (a page from another site cannot read the bearer), and
    one without it is 401. base (f3a6385): fails on its consequence (the
    owner's cross-site request is served)."""
    from backend.identity import NO_IDENTITY, NOT_OWNER
    cross = [*FORM, ("Sec-Fetch-Site", "cross-site"), ("Origin", EVIL)]
    proxy = _proxy_app(tmp_path / "p", monkeypatch)
    cases = {"a remote peer, the owner's login": (proxy, REMOTE, [(DEFAULT_HEADER, OWNER)]),
             "no identity": (proxy, LOOPBACK, []),
             "another login": (proxy, LOOPBACK, [(DEFAULT_HEADER, OTHER)]),
             "no owner set": (_proxy_app(tmp_path / "n", monkeypatch, owner=None), LOOPBACK,
                              [(DEFAULT_HEADER, OWNER)]),
             "the owner's login": (proxy, LOOPBACK, [(DEFAULT_HEADER, OWNER)])}
    got = {name: _cell(_client(app, peer).delete("/model/override", headers=[*login, *cross]))
           for name, (app, peer, login) in cases.items()}
    print(f"\nV1 order ({sys.platform}): {got}")
    assert {name: cell[0] for name, cell in got.items()} == {
        "a remote peer, the owner's login": 401, "no identity": 401, "another login": 403, "no owner set": 403,
        "the owner's login": 403}, got
    assert got["no identity"][1] == NO_IDENTITY and got["another login"][1] == NOT_OWNER
    assert got["no owner set"][1].startswith("owner not set")
    from backend.identity import CROSS_SITE
    assert got["the owner's login"][1] == CROSS_SITE
    token = _app(tmp_path / "t", monkeypatch)
    assert token.user_middleware == []
    with_bearer = _client(token).delete("/model/override", headers=cross)
    bare = _client(token)
    bare.headers.pop("Authorization")
    without = bare.delete("/model/override", headers=cross)
    print(f"V1 token mode: with the bearer {with_bearer.status_code}, without {without.status_code}")
    assert (with_bearer.status_code, without.status_code) == (200, 401)


def test_v1_a_safe_method_is_never_refused_as_cross_site(tmp_path, monkeypatch):
    """GET and HEAD stay as they are: no GET route writes, and no response
    carries CORS headers, so a page from another site cannot read one. Every
    GET and HEAD the app serves (I3's derivation), sent with the owner's
    login as a cross-site page's request and as an address-bar navigation
    (Sec-Fetch-Site `none`: the console opened from a bookmark), is not
    refused by the gate. A browser's preflight before a cross-site JSON POST
    (OPTIONS) is not refused by the gate either and finds no CORS headers, so
    the POST is never sent. A pin of the base's behaviour for the safe
    methods (it passes on f3a6385 by construction); its failing case is the
    mutant that applies the rule to every method, killed on the fix."""
    app = _proxy_app(tmp_path, monkeypatch)
    client = _client(app)
    targets = [(m, u) for m, u in served_targets(app) if m in SAFE]
    shapes = {"a cross-site page": (("Sec-Fetch-Site", "cross-site"), ("Origin", EVIL)),
              "the address bar": (("Sec-Fetch-Site", "none"),)}
    refused = []
    for method, url in targets:
        for shape, pairs in shapes.items():
            code = client.request(method, url, headers=[(DEFAULT_HEADER, OWNER), *pairs]).status_code
            if code in (401, 403):
                refused.append((method, url, shape, code))
    preflight = client.options("/chat", headers={DEFAULT_HEADER: OWNER, "Origin": EVIL, "Sec-Fetch-Site": "cross-site",
                                                 "Access-Control-Request-Method": "POST",
                                                 "Access-Control-Request-Headers": "content-type"})
    cors = sorted(k for k in preflight.headers if k.lower().startswith("access-control-"))
    print(f"\nV1 safe methods ({sys.platform}): {len(targets)} GET and HEAD targets x {len(shapes)}; refused "
          f"{refused}; OPTIONS /chat {preflight.status_code}, CORS headers {cors}")
    assert len(targets) >= 40 and refused == [], refused
    assert preflight.status_code not in (401, 403) and cors == [], (preflight.status_code, cors)


def test_v1_the_reader_takes_sec_fetch_site_and_origin_in_its_one_pass():
    """D5: the two headers are read by the one reader, in its one pass,
    beside the identity and the forwarded-for entries: every copy of each,
    in order, Sec-Fetch-Site with its spaces and tabs trimmed as a field
    value's are. base: the new names (RequestHeaders has no such fields)."""
    from backend.identity import read_request_headers
    key = b"tailscale-user-login"

    def view(*pairs):
        return read_request_headers({"type": "http", "headers": list(pairs)}, key)

    plain = view((key, OWNER.encode()))
    assert (plain.identity, plain.fetch_site, plain.origin) == (OWNER, (), ())
    both = view((b"Sec-Fetch-Site", b" same-origin\t"), (b"Origin", EVIL.encode()), (key, OWNER.encode()))
    assert (both.identity, both.fetch_site, both.origin) == (OWNER, ("same-origin",), (EVIL,))
    twice = view((b"sec-fetch-site", b"same-origin"), (b"sec-fetch-site", b"cross-site"), (b"origin", b""))
    assert (twice.fetch_site, twice.origin) == (("same-origin", "cross-site"), ("",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        both.fetch_site = ()


# ---------------------------------------------------------------------------
# V2 with V6 — the owner compared exactly, and only HTTP's optional
# whitespace trimmed (W4's close)
# ---------------------------------------------------------------------------

def _fullwidth(text: str) -> str:
    """Every printable ASCII character as its fullwidth form (U+FF01 to
    U+FF5E), which NFKC maps back."""
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in text)


NEAR_MISSES = {
    "extends the owner's": OWNER + ".evil.example",
    "a prefix of the owner's": OWNER.rsplit(".", 1)[0],
    "differs in case only": "Owner@Example.test",
    "differs in the domain only": OWNER.split("@")[0] + "@evil.example",
    "NFKC-equal (fullwidth)": _fullwidth(OWNER),
    "a Cyrillic confusable": OWNER.replace("o", "о"),
    "owner, other": f"{OWNER}, {OTHER}",
    "other, owner": f"{OTHER}, {OWNER}",
    "padded with U+00A0": f" {OWNER}",
    "padded with U+2003": f"{OWNER} ",
    "padded with U+3000": f"　{OWNER}　",
}
OWS_PADDED = {"spaces": f"  {OWNER}  ", "a tab": f"\t{OWNER}", "spaces and tabs": f" \t{OWNER}\t "}


def _as_sent(login: str) -> list:
    """The identity header with the login's UTF-8 bytes, as a proxy sends a
    login that is not ASCII (h11 passes those bytes as they came)."""
    return [(DEFAULT_HEADER, login.encode("utf-8"))]


def near_miss_premises() -> list[str]:
    """What each case's name says, checked on the strings themselves, so
    each case tests what it is called: the extension and the prefix, the
    case variant (it casefolds to the owner), the domain (the local part is
    the owner's), the fullwidth one (NFKC-equal), the confusable (neither
    NFKC-equal nor ASCII), the comma-joined values (each names the owner),
    and the three paddings (Python's `str.strip()` removes them; they are
    not spaces or tabs)."""
    m, bad = NEAR_MISSES, []
    checks = {
        "extends the owner's": m["extends the owner's"].startswith(OWNER),
        "a prefix of the owner's": OWNER.startswith(m["a prefix of the owner's"]),
        "differs in case only": m["differs in case only"].casefold() == OWNER.casefold(),
        "differs in the domain only": m["differs in the domain only"].split("@")[0] == OWNER.split("@")[0],
        "NFKC-equal (fullwidth)": unicodedata.normalize("NFKC", m["NFKC-equal (fullwidth)"]) == OWNER,
        "a Cyrillic confusable": unicodedata.normalize("NFKC", m["a Cyrillic confusable"]) != OWNER
        and not m["a Cyrillic confusable"].isascii() and len(m["a Cyrillic confusable"]) == len(OWNER),
        "owner, other": m["owner, other"].split(",")[0].strip() == OWNER,
        "other, owner": m["other, owner"].split(",")[-1].strip() == OWNER,
        **{name: m[name].strip() == OWNER and m[name].strip(" \t") != OWNER
           for name in ("padded with U+00A0", "padded with U+2003", "padded with U+3000")},
    }
    bad += [name for name, ok in checks.items() if not ok]
    bad += [name for name, login in m.items() if login == OWNER]
    return bad + sorted(set(m) - set(checks))


def test_v2_a_login_that_is_not_the_owners_exactly_is_403(tmp_path, monkeypatch):
    """V2 and V6. Each near miss (its premise checked first) is 403 "not
    this instance's owner", and none is taken as the login. base (f3a6385):
    the three Unicode paddings fail on their consequence (200, taken as the
    owner: `str.strip()` removed them); the other eight pass there (the base
    compares with `!=`, and no test held it), and their failing cases are
    the verifier's loosening mutants (case, NFKC, prefix, local part, the
    first comma entry) and the fix's own (the owner a prefix of the login, a
    confusable fold, any comma entry), each killed on the fix
    (SP/w4/fix/mutants_*.log)."""
    from backend.identity import NOT_OWNER
    assert near_miss_premises() == []
    client = _client(_proxy_app(tmp_path, monkeypatch))
    got = {}
    for name, login in NEAR_MISSES.items():
        resp = client.get("/whoami", headers=_as_sent(login))
        got[name] = (resp.status_code, _detail(resp) if resp.status_code != 200 else resp.json().get("login"))
    print(f"\nV2 ({sys.platform}): {got}")
    assert got == {name: (403, NOT_OWNER) for name in NEAR_MISSES}, {n: c for n, c in got.items()
                                                                     if c != (403, NOT_OWNER)}


def test_v6_the_owners_login_with_spaces_or_tabs_around_it_is_the_owner(tmp_path, monkeypatch):
    """HTTP's optional whitespace is not part of a field value: the owner's
    login with spaces or tabs around it is the owner, and the login taken is
    the owner's without them. A pin (the base's `str.strip()` removes those
    too); its failing case is the mutant that trims nothing, killed on the
    fix."""
    client = _client(_proxy_app(tmp_path, monkeypatch))
    got = {name: client.get("/whoami", headers=_as_sent(login)) for name, login in OWS_PADDED.items()}
    print(f"\nV6 ({sys.platform}): {[(n, r.status_code) for n, r in got.items()]}")
    assert {n: (r.status_code, r.json().get("login")) for n, r in got.items()} == {
        n: (200, OWNER) for n in OWS_PADDED}


def test_v6_the_reader_trims_spaces_and_tabs_and_nothing_else():
    """The one reader's value: spaces and tabs around it removed, every other
    character kept (U+00A0, U+2003, U+3000 among them); a value of spaces
    and tabs alone is no identity, and one of U+2003 alone is a login (not
    the owner's). base (f3a6385): fails on its consequence (`str.strip()`
    returns the owner's login for the padded value)."""
    from backend.identity import read_request_headers
    key = b"tailscale-user-login"

    def login(raw: bytes):
        return read_request_headers({"type": "http", "headers": [(key, raw)]}, key).identity

    assert login(b" \towner@example.test\t ") == OWNER
    assert login(f" {OWNER}".encode("utf-8")) == f" {OWNER}"
    assert login(f"{OWNER}　".encode("utf-8")) == f"{OWNER}　"
    assert login(" ".encode("utf-8")) == " "
    assert login(b" \t ") is None and login(b"") is None


def test_v6_the_owner_setting_is_trimmed_of_spaces_and_tabs_and_nothing_else(tmp_path, monkeypatch):
    """NEXUS_OWNER_IDENTITY is trimmed as the header is: spaces and tabs
    around it removed, nothing else. An owner set with a U+00A0 before it is
    not the plain login (403); one set with spaces and a tab around it is
    (200). base (f3a6385): fails on its consequence (`str.strip()` removed
    the U+00A0, so the plain login was taken as the owner)."""
    from backend.identity import identity_config
    proxy = {"NEXUS_AUTH_MODE": "proxy"}
    assert identity_config({**proxy, "NEXUS_OWNER_IDENTITY": f" \t{OWNER}\t "}).owner == OWNER
    kept = {pad: identity_config({**proxy, "NEXUS_OWNER_IDENTITY": f"{pad}{OWNER}"}).owner
            for pad in (" ", " ", "　")}
    no_break = _client(_proxy_app(tmp_path / "nb", monkeypatch, owner=f" {OWNER}")).get(
        "/whoami", headers={DEFAULT_HEADER: OWNER})
    ows = _client(_proxy_app(tmp_path / "ows", monkeypatch, owner=f" {OWNER}\t")).get(
        "/whoami", headers={DEFAULT_HEADER: OWNER})
    print(f"\nV6 owner ({sys.platform}): kept {[(hex(ord(p)), o) for p, o in kept.items()]}; an owner with U+00A0 "
          f"{no_break.status_code}, with spaces and a tab {ows.status_code}")
    assert no_break.status_code == 403 and ows.status_code == 200 and ows.json()["login"] == OWNER
    assert kept == {pad: f"{pad}{OWNER}" for pad in kept}, kept


# ---------------------------------------------------------------------------
# I14 — the three settings take effect from .env
# ---------------------------------------------------------------------------

def _env_reads(node) -> bool:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("getenv", "get"):
        return "environ" in ast.unparse(node.func.value) or ast.unparse(node.func.value) == "os"
    return isinstance(node, ast.Subscript) and "environ" in ast.unparse(node.value)


def module_scope_env_reads(source: str) -> list[str]:
    """Environment reads that run at import: anywhere but inside a function."""
    found = []

    def visit(node, in_function):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            in_function = True
        elif not in_function and _env_reads(node):
            found.append(f"line {node.lineno}: {ast.unparse(node)}")
        for child in ast.iter_child_nodes(node):
            visit(child, in_function)

    visit(ast.parse(source), False)
    return found


def test_i14_the_three_settings_are_documented_and_probed_from_dotenv():
    """The probes themselves run in tests/test_dotenv_settings_take_effect.py
    (a child reads each from `.env` alone)."""
    from tests.test_dotenv_settings_take_effect import PROBES, documented_settings
    documented = set(documented_settings())
    assert [k for k in AUTH_KEYS if k not in documented or k not in PROBES] == []


def test_i14_the_identity_module_reads_no_setting_at_import():
    """base: the new file."""
    source = (REPO / "backend" / "identity.py").read_text(encoding="utf-8")
    assert module_scope_env_reads(source) == []
    planted = source + "\nMODE = os.environ.get('NEXUS_AUTH_MODE', 'token')\n"
    assert len(module_scope_env_reads(planted)) == 1, "companion: a module-scope read is named"


# ---------------------------------------------------------------------------
# I15 — MCP is untouched
# ---------------------------------------------------------------------------

def _mcp_sources() -> dict[str, str]:
    return {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8") for p in tracked("backend/mcp/*.py")}


def identity_uses(sources: dict[str, str]) -> set[tuple[str, str]]:
    """(file, what) for every use of the identity boundary, by AST: the
    module (imported, or named as a string), the three settings' literals,
    and the boundary's names."""
    names = {"identity_config", "IdentityGate", "established_identity", "read_request_headers"}
    out = set()
    for path, src in sources.items():
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                out |= {(path, k) for k in AUTH_KEYS if k in n.value}
                if "backend.identity" in n.value:
                    out.add((path, "backend.identity"))
            if isinstance(n, ast.Import) and any(a.name.startswith("backend.identity") for a in n.names):
                out.add((path, "backend.identity"))
            if isinstance(n, ast.ImportFrom) and ((n.module or "").startswith("backend.identity") or (
                    n.module == "backend" and any(a.name == "identity" for a in n.names))):
                out.add((path, "backend.identity"))
            name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else None
            if name in names:
                out.add((path, name))
    return out


def test_i15_the_mcp_package_names_no_part_of_the_identity_boundary():
    assert identity_uses(_mcp_sources()) == set()


def test_i15_companion_planted_uses_are_named():
    planted = {**_mcp_sources(),
               "backend/mcp/p1.py": "from backend.identity import identity_config\nC = identity_config()\n",
               "backend/mcp/p2.py": "import os\nOWNER = os.environ.get('NEXUS_OWNER_IDENTITY')\n",
               "backend/mcp/p3.py": "import importlib\nM = importlib.import_module('backend.identity')\n",
               "backend/mcp/p4.py": "from backend import identity\n"}
    got = identity_uses(planted)
    assert {("backend/mcp/p1.py", "backend.identity"), ("backend/mcp/p2.py", "NEXUS_OWNER_IDENTITY"),
            ("backend/mcp/p3.py", "backend.identity"), ("backend/mcp/p4.py", "backend.identity")} <= got, got


def test_i15_python_m_backend_mcp_imports_no_identity_module_under_proxy_settings(tmp_path):
    """A real MCP process: `setup()` and one retrieve-and-record turn, with
    the three settings in its environment. The identity module is never
    imported (so nothing of it is read), and the record is MCP's."""
    code = textwrap.dedent("""
        import asyncio, json, sys
        from backend.mcp.__main__ import setup
        runtime, server = setup()
        def call(name, **args):
            return asyncio.run(server.call_tool(name, args)).model_dump()["structured_content"]
        r = call("nexus_retrieve", question="tell me about python")
        rec = call("nexus_record", retrieval_id=r["retrieval_id"], answer="Python is a language.", model="m1")
        row = runtime.services.lineage.get(rec["lineage_id"])
        print("W4-I15 " + json.dumps({"identity_imported": "backend.identity" in sys.modules,
                                      "accountable": row.accountable_identity, "scope": row.record_scope,
                                      "intact": runtime.services.lineage.verify_record(row.lineage_id)["record_intact"]}))
    """)
    proc = _proc.run([sys.executable, "-c", code], cwd=REPO, stdin=subprocess.DEVNULL,
                          env=_child_env(tmp_path, LLM_BACKEND="stub", NEXUS_AUTH_MODE="proxy",
                                         NEXUS_IDENTITY_HEADER="X-Planted-Login",
                                         NEXUS_OWNER_IDENTITY="mallory@example.test", NEXUS_MCP_OWNER="alice",
                                         NEXUS_MCP_DATA_ROOT=str(tmp_path / "mcp")),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
    lines = [line for line in proc.stdout.splitlines() if line.startswith("W4-I15 ")]
    assert proc.returncode == 0 and lines, proc.stderr[-2500:]
    got = json.loads(lines[-1][len("W4-I15 "):])
    print(f"\nI15 ({sys.platform}) {got}")
    assert got == {"identity_imported": False, "accountable": "alice", "scope": "mcp_block", "intact": True}


@pytest.fixture
def _mcp_env_restored():
    """NexusRuntime.open writes the learning default and log paths into the
    process environment on purpose (tests/test_mcp2_build.py's `_mcp_env`):
    snapshot every NEXUS_ key and put it back by hand."""
    before = {k: v for k, v in os.environ.items() if k.startswith("NEXUS_")}
    yield
    for k in [k for k in os.environ if k.startswith("NEXUS_")]:
        if k not in before:
            del os.environ[k]
    os.environ.update(before)


def test_i15_an_mcp_turn_is_the_same_with_the_three_settings_in_the_environment(tmp_path, monkeypatch,
                                                                                _mcp_env_restored):
    from backend.kb.granularity import Granularity
    from backend.mcp.mcp_server import NexusRuntime, build_server

    def call(server, name, **args):
        return asyncio.run(server.call_tool(name, args)).model_dump()["structured_content"]

    def turn(root):
        with RigClock().installed():
            rt = NexusRuntime.open(root=root, owner="alice")
            rt.services.store.add(subject="python", relation="is_a", object="programming_language", weight=0.8,
                                  tier=1, granularity=Granularity.TOPIC, source="human_assertion")
            srv = build_server(rt, with_lifespan=False)
            got = call(srv, "nexus_retrieve", question="tell me about python")
            rec = call(srv, "nexus_record", retrieval_id=got["retrieval_id"], answer="Python is a language.",
                       model="m1")
            row = rt.services.lineage.get(rec["lineage_id"])
            return got["block"], row, rt.services.lineage.verify_record(row.lineage_id)["record_intact"]

    for key in AUTH_KEYS:
        monkeypatch.delenv(key, raising=False)
    block_before, before, intact_before = turn(tmp_path / "before")
    monkeypatch.setenv("NEXUS_AUTH_MODE", "proxy")
    monkeypatch.setenv("NEXUS_IDENTITY_HEADER", "X-Planted-Login")
    monkeypatch.setenv("NEXUS_OWNER_IDENTITY", "mallory@example.test")
    block_after, after, intact_after = turn(tmp_path / "after")
    fields = ("request_hash", "response_hash", "answer_hash", "accountable_identity", "record_scope", "model_id",
              "prompt_prefix_hash")
    print(f"\nI15 in process: {[(f, getattr(after, f)) for f in ('accountable_identity', 'record_scope')]}")
    assert block_before == block_after and intact_before and intact_after
    assert {f: getattr(before, f) for f in fields} == {f: getattr(after, f) for f in fields}
    assert after.accountable_identity == "alice"
    assert getattr(after, "identity_mechanism", None) is None, "part 2's field: NULL under MCP"
