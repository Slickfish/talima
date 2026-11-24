"""Packet H.4.3 — the open-GET posture is conditional, so check the condition.

THE GAP THIS CLOSES
    `require_token` gates every state-changing endpoint and deliberately
    leaves GET endpoints open. `server.py:95` gives the reason —
    "localhost-bind posture — THREAT_MODEL.md" — and that reason is sound
    while the bind really is localhost.

    Nothing connected those two facts. `THREAT_MODEL.md` *assumes* a
    localhost bind; `resolve_bind()` *permits* any bind via `NEXUS_HOST`; and
    no test noticed that the second silently invalidates the first. A cloud
    port, a Docker `0.0.0.0`, or one `NEXUS_HOST=` in a `.env` would expose
    every read endpoint — including `/graph/snapshot`, `/state/summary` and
    `/kb/stats` — with the security argument still reading as satisfied
    because it lives in prose.

    Packet G closed the writer half (every writer is auth-tested). This
    closes the reader half: the posture must be *classified*, not assumed.

THE ASSERTION
    If the bind is not localhost, every route must be explicitly classified
    as GATED or DELIBERATELY-OPEN. A new route defaults to NEITHER — it fails
    until someone decides, which is the point. Defaulting to open is how an
    unclassified route becomes an exposed one.

DERIVED, NOT HAND-LISTED
    The route set comes from `app.routes`, following Packet G's
    `derive_writers()` pattern. A hand-written path list would go stale the
    moment a route is added — the stale-list defect that produced Packet G in
    the first place, and that `/memory/column`'s dead `hasattr` guards are
    another instance of.
"""
import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# FastAPI's own machinery, not ours. Excluded from classification because they
# are not NEXUS's surface and their posture is FastAPI's decision.
FRAMEWORK_ROUTES = frozenset({
    "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json",
})

# Read routes that are open ON PURPOSE. Each is a decision someone made, and
# the reason is recorded here so a future reader inherits the reasoning rather
# than the list.
#
#   status/health   an operator or a monitor must be able to see liveness
#                   without holding a write token
#   telemetry       counters and gauges about the substrate's own operation;
#                   no belief content, no evidence records
#   ui shell        the pages themselves; they carry no data until an
#                   authenticated fetch fills them
#
# NOT on this list, deliberately: every /lineage/* route. Evidence records are
# more sensitive than the open-read posture (Packet G pinned /lineage/recent;
# H.1 added the two verify routes alongside it).
DELIBERATELY_OPEN = frozenset({
    "/",                    # ui shell
    "/memory",              # ui shell
    "/healthz", "/health",  # status
    "/identity",            # W9 (D3): the instance's public key; a stranger checks a credential with it, no token
    "/identity/signed-action",  # W10b (D10, Ruling 8): its caller received a signed request and holds no token
    "/kb/stats",
    "/events/recent",
    "/cortex/stats",
    "/cortex/drift",
    "/kairos/status",
    "/chain/status",
    "/memory/column",
    "/workers/active",
    "/curiosity/recent",
    "/kb/dedup/pending",
    # W.1 — coverage of the semantic index, open for the same reason
    # `/kb/dedup/pending` is: it is the surface that says whether retrieval
    # can look at all, and it returns counts plus at most ten triple surfaces
    # already visible on `/kb/stats`. Its WRITE half is token-gated.
    "/kb/index/pending",
    # AB — trail status: relation-pair names and counts, all already visible
    # on /cortex/stats, which is open above for the same reason. The WRITE
    # half (/kb/trails/backfill/confirm) is token-gated.
    "/kb/trails",
    "/search/pending",
    "/router/preview",
    "/router/registry",
    "/eval/recent",
    "/vector/stats",
    "/graph/snapshot",
    "/state/summary",
    "/model/status",
    # S.2 opened `/governance/envelope` (a policy is meant to be inspectable);
    # B-40 gated it: its history names the actor who installed each policy,
    # a governed act attributable to its actor by construction, and such a
    # surface is declared per-person and gated (PER_PERSON_ROWS below).
})

# AR (advice recall) — the third classification, orthogonal to gated/open:
# a read surface that returns PER-PERSON ROWS declares it here, in its
# payload and in CLAUDE.md (tests/test_just_culture_guard.py holds the
# declaration guard). NEXUS never aggregates by person; a consumer can
# group these rows in one line, and the declaration is what makes that a
# choice made in the open. Value: what the rows carry and how it is
# labelled. Every entry must name a live route (stale-entry test below).
PER_PERSON_ROWS: dict[str, str] = {
    "/actions/{action_id}": (
        "the receipt names the action's `accountable_identity` as the request carried it, labelled by its "
        "`identity_mechanism` (W4): `asserted` or `proxy`; gated like /lineage/* (W10b D7)"),
    "/belief/{belief_id}/recall": (
        "each row carries `accountable` as recorded, labelled by the record's "
        "identity_mechanism (W4): `asserted` (recorded, never authenticated) or "
        "`proxy` (a loopback identity proxy authenticated the person, NEXUS did "
        "not); gated like /lineage/*"),
    # B-40 — the work/authority clause: governed acts are attributable to
    # their actor by construction, and surfaces over them are declared
    # per-person and gated. These two are AUTHORITY surfaces (served by the
    # governance router, gated, declared), the only kind the just-culture
    # guard allows a person-field aggregate on.
    "/governance/budget": (
        "per-actor rows: a steward's bound on governed adjustments is accountability "
        "by design; actors keyed (id, role, source) as asserted; gated"),
    "/governance/envelope": (
        "the policy history names the actor who installed each version, as "
        "asserted; gated since B-40 (was an open read)"),
    # W6 — the work-unit boundary ledger. A close and a reopen carry the
    # actor and the accountable identity the request asserted: per-act
    # rows. Gated, and NOT on the governance router, so it is no authority
    # surface and the guard forbids any aggregate by person on it ("units
    # closed, by whom" would be an aggregate of work by the person doing it).
    "/work-units/events": (
        "per-act rows: each close and reopen carries the actor (id, role, source) and the "
        "accountable identity its request asserted, never authenticated; gated; rows only"),
}


def test_per_person_rows_names_only_live_routes(app):
    """PER_PERSON_ROWS must not outlive the routes it declares for."""
    paths = {p for _m, p in derive_routes(app)}
    stale = sorted(set(PER_PERSON_ROWS) - paths)
    assert not stale, f"PER_PERSON_ROWS names routes that do not exist: {stale}"
    for path in PER_PERSON_ROWS:
        assert path not in DELIBERATELY_OPEN, f"{path} returns per-person rows and is open"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    """Isolated app — nothing may reach the repo's real data/ (Packet A)."""
    tmp = tmp_path_factory.mktemp("bind_sentinel")
    os.environ["NEXUS_API_TOKEN"] = "bind-sentinel-token"
    from backend.server import create_app, create_services
    return create_app(create_services(
        kb_db_path=tmp / "kb.db", chroma_dir=tmp / "chroma",
        eval_log_path=tmp / "m.jsonl", archive_db_path=tmp / "archive.db",
        snapshots_dir=tmp / "snapshots"))


def derive_routes(app) -> list[tuple[str, str]]:
    """(method, path) for every application route, from the router itself."""
    out = []
    for route in app.routes:
        for method in sorted(getattr(route, "methods", None) or set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            if route.path in FRAMEWORK_ROUTES:
                continue
            out.append((method, route.path))
    return sorted(out)


def is_gated(app, method: str, path: str) -> bool:
    """Does this route reject an unauthenticated request?

    Determined by INVOCATION, not by inspecting for a `Depends(require_token)`
    in the source: the question is whether the route actually refuses, and a
    dependency that is present but mis-wired would pass a source check.
    """
    with TestClient(app) as client:
        client.headers.pop("Authorization", None)
        return _is_gated_via(client, method, path)


def _is_gated_via(client, method: str, path: str) -> bool:
    """`is_gated` for a client the caller already opened.

    I.2 — entering a `TestClient` context runs the FastAPI lifespan, which
    awaits `sync_pulled_state()` -> `OllamaProvider.list_models()`: an
    outbound HTTP GET to localhost:11434 with a 5s timeout
    (`backend/providers/ollama.py:93`). On this sandbox that connection is
    refused in ~2ms; on the operator's Windows machine it costs seconds, so
    one client per route paid that toll once per route — measured at 12
    probes for the non-localhost sweep and 4 each for the other two.

    Sweeping with a single client collapses that to one lifespan per test.
    Nothing mutable is shared: the client is opened and disposed inside the
    calling test, so isolation is exactly as before. Packet A's hazard is
    shared *state*, and this shares none.
    """
    r = client.request(method, path.replace("{lineage_id}", "probe"))
    return r.status_code in (401, 403)


@contextmanager
def unauthenticated(app):
    """One unauthenticated client for a whole classification sweep."""
    with TestClient(app) as client:
        client.headers.pop("Authorization", None)
        yield client


# ---------------------------------------------------------------------------

def test_every_route_is_classified(app):
    """No route may be unclassified. A new one fails here until a human
    decides whether it is gated or deliberately open."""
    routes = derive_routes(app)
    assert routes, "no routes derived — has the app shape changed?"

    unclassified = []
    with unauthenticated(app) as client:          # one lifespan, not one per route
        for method, path in routes:
            if method in WRITE_METHODS:
                continue                   # writers: Packet G's territory
            if path in DELIBERATELY_OPEN:
                continue
            if _is_gated_via(client, method, path):
                continue
            unclassified.append((method, path))

    assert not unclassified, (
        f"unclassified read route(s): {unclassified}\n"
        f"Each must be either gated with Depends(require_token) or added to "
        f"DELIBERATELY_OPEN with a reason. Defaulting to open is how an "
        f"unclassified route becomes an exposed one.")


def test_deliberately_open_list_has_no_stale_entries(app):
    """The list must not outlive the routes it describes — otherwise it rots
    into reassurance about paths that no longer exist."""
    paths = {p for _m, p in derive_routes(app)}
    stale = sorted(DELIBERATELY_OPEN - paths)
    assert not stale, f"DELIBERATELY_OPEN names routes that do not exist: {stale}"


def test_lineage_routes_are_never_deliberately_open(app):
    """Evidence records are the one read surface that is gated, and this
    stops a future edit quietly moving them onto the open list."""
    lineage = [p for _m, p in derive_routes(app) if p.startswith("/lineage")]
    assert lineage, "no /lineage routes found — has the surface moved?"
    with unauthenticated(app) as client:          # one lifespan for the sweep
        for path in lineage:
            assert path not in DELIBERATELY_OPEN, (
                f"{path} was added to DELIBERATELY_OPEN — evidence records "
                f"are more sensitive than the open-read posture")
            assert _is_gated_via(client, "GET", path), f"{path} is not gated"


def test_non_localhost_bind_requires_full_classification(app, monkeypatch):
    """THE POINT OF THE FILE.

    The open-GET posture is defensible only while the bind is localhost.
    Under any other bind, "unclassified" stops being a tidiness issue and
    becomes an exposed endpoint — so the classification must be total.

    This runs the same check the first test does, but framed on the condition
    that makes it load-bearing, so a reader of a failure understands the
    stakes rather than seeing a style complaint.
    """
    import start

    for host in ("0.0.0.0", "192.168.1.50", "::"):
        monkeypatch.setenv("NEXUS_HOST", host)
        bind_host, _port = start.resolve_bind()
        assert bind_host == host                       # non-vacuity
        assert not _is_localhost(bind_host)

        with unauthenticated(app) as client:      # one lifespan per host
            unclassified = [
                (m, p) for m, p in derive_routes(app)
                if m not in WRITE_METHODS
                and p not in DELIBERATELY_OPEN
                and not _is_gated_via(client, m, p)
            ]
        assert not unclassified, (
            f"bind is {bind_host} (not localhost) and these read routes are "
            f"neither gated nor deliberately open: {unclassified}. "
            f"THREAT_MODEL.md's localhost assumption no longer holds, so "
            f"every one of these is publicly reachable.")


def _is_localhost(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def test_threat_model_still_states_the_assumption_this_guards():
    """If the document stops claiming a localhost posture, this sentinel is
    guarding a rule nobody is making any more — and should be revisited
    rather than left passing."""
    tm = Path(__file__).resolve().parent.parent / "THREAT_MODEL.md"
    if not tm.exists():
        pytest.skip("THREAT_MODEL.md absent")
    text = tm.read_text(encoding="utf-8").lower()
    assert "localhost" in text or "127.0.0.1" in text, (
        "THREAT_MODEL.md no longer states a localhost bind assumption; the "
        "open-GET posture needs re-justifying, not re-testing")


def test_default_bind_is_still_localhost(monkeypatch):
    """Duplicates test_api_auth.py::test_default_bind_is_localhost on
    purpose. That one is the sole cover for `start.resolve_bind()` and Packet
    G kept it for exactly that reason; this one states the same fact as the
    PREMISE of everything above, so a reader of this file does not have to go
    looking for why the open-GET posture is acceptable at all."""
    monkeypatch.delenv("NEXUS_HOST", raising=False)
    monkeypatch.delenv("NEXUS_PORT", raising=False)
    import start
    host, port = start.resolve_bind()
    assert _is_localhost(host)
    assert port == 8000
