"""Packet G — auth coverage that grows with the surface, not with memory.

WHY THIS EXISTS
    The endpoint audit (ENDPOINT_AUDIT.md) found that all ten state-changing
    endpoints correctly reject a missing token and a wrong token. **Nothing
    was broken.** What was missing was the tripwire: `test_api_auth.py`
    exercises two paths, so nine of the ten writers had no coverage and a new
    endpoint shipped without `dependencies=[Depends(require_token)]` would
    pass CI unnoticed.

    That is the pack §15.4 defect class — *presence verified, behaviour
    unverified* — applied to a test. The test existed; its coverage did not.

THE DESIGN CONSTRAINT
    The writer list is derived from `app.routes` at run time, never hardcoded
    from the audit. A sentinel that enumerates from someone's memory goes
    stale the moment an eleventh writer lands, which is precisely the failure
    it is meant to catch.

    `EXPECTED_WRITERS` below is a *ratchet*, not the source of truth: the
    router supplies the list, and the frozen set forces a new writer to be
    acknowledged deliberately rather than sliding in under a `for` loop.
    Adding an endpoint therefore fails this file until someone adds it here —
    at which point they have looked at whether it needs auth.

WHAT IS DELIBERATELY NOT DONE HERE
    Nothing under `backend/` or `frontend/` is touched. If a writer were found
    unguarded, that is a finding to report, not to fix: an auth change inside
    a test commit is exactly the misattribution Packet F.2's transaction work
    was about.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.server import create_app, create_services

ROOT = Path(__file__).resolve().parent.parent

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

GOOD_TOKEN = "packet-g-token"
WRONG_TOKEN = "packet-g-wrong-token"
UNAUTHORISED = 401          # server.py:98 — require_token raises this uniformly


# The ten writers the audit found, as (method, path). Kept as a ratchet so an
# eleventh forces a deliberate decision. NOT used to drive the tests.
EXPECTED_WRITERS = frozenset({
    ("POST",   "/chat"),
    # O.2b — the human-assertion confirm gate. Acknowledged deliberately:
    # it is the only path that writes the slowest-decaying evidence class in
    # the system, so it is the LAST endpoint that should be reachable without
    # a token. Guarded by `Depends(require_token)` like every other writer.
    ("POST",   "/kb/assert/confirm"),
    # W9 (D8) — commit now, reveal later: holds a person's note and its salt.
    ("POST",   "/commit"),
    # W.1 — the semantic index backfill. Acknowledged deliberately: it writes
    # no belief and moves no weight, so it is NOT in the class the two gates
    # below guard. It is token-gated anyway because it is a bulk outbound
    # operation — one embedding call per un-indexed belief — and an
    # unauthenticated caller who can trigger that can make the box talk to a
    # model daemon as many times as there are rows in the store.
    ("POST",   "/kb/index/confirm"),
    # S.2 — governed adaptation. Both acknowledged deliberately and both
    # token-gated: `/outcome/confirm` is the only path in NEXUS that moves
    # what a belief is worth, and `/governance/envelope/confirm` sets the
    # bound that path is checked against. An unauthenticated caller who can
    # widen the envelope can move anything, so the policy endpoint is exactly
    # as sensitive as the one it governs — more so, arguably, since it is the
    # one an auditor is shown.
    ("POST",   "/outcome/confirm"),
    ("POST",   "/governance/envelope/confirm"),
    # AB — trail backfill. Acknowledged deliberately: it writes no belief and
    # moves no weight — it lays association rows in trails' own tables from
    # history that already exists. Token-gated because it consumes months of
    # lineage in one pass, and because a Law 3 gate reachable without a token
    # is not a gate.
    ("POST",   "/kb/trails/backfill/confirm"),
    ("POST",   "/kairos/cycle"),
    ("POST",   "/kb/dedup/confirm"),
    ("POST",   "/kb/seed/conceptnet"),
    ("POST",   "/kb/seed/offline"),
    ("POST",   "/kb/seed/wikidata"),
    ("POST",   "/model/override"),
    ("DELETE", "/model/override"),
    ("POST",   "/search/confirm"),
    ("POST",   "/search/execute"),
})

# Minimal bodies that reach the auth check. Deliberately chosen NOT to fire a
# side effect: the Law 3 gates are left unconfirmed, so the correct-token case
# returns the gate's own 400/403/422 rather than seeding the KB or making an
# outbound request. This sentinel tests authentication, not the endpoint's
# function, and a writer that reaches its own validation has already passed
# the only thing being asserted.
PAYLOADS: dict[tuple[str, str], dict | None] = {
    ("POST", "/chat"):               {"message": "hello"},
    ("POST", "/kairos/cycle"):       None,
    ("POST", "/kb/assert/confirm"):  {"sentence": "The dose was 600mg.",
                                      "confirmed": False},
    ("POST", "/commit"):             {"content": "a note to hold", "confirmed": False},
    # W.1 — unconfirmed for the same reason: the correct-token case must stop
    # at the Law 3 gate rather than embedding the whole store inside an auth
    # test.
    ("POST", "/kb/index/confirm"):   {"confirmed": False},
    # AB — unconfirmed for the same reason: the correct-token case must stop
    # at the Law 3 gate rather than consuming lineage history inside an auth
    # test.
    ("POST", "/kb/trails/backfill/confirm"): None,
    # S.2 — both left UNCONFIRMED, per this map's own rule: the correct-token
    # case must reach the Law 3 gate and stop there, not install a policy or
    # move a weight inside an auth test.
    ("POST", "/outcome/confirm"):    {"event_type": "review",
                                      "decision": "approved",
                                      "direction": "confirming",
                                      "references": {"belief_ids": []},
                                      "confirmed": False},
    ("POST", "/governance/envelope/confirm"): {"version": "auth-probe",
                                               "reason": "auth sentinel",
                                               "confirmed": False},
    ("POST", "/kb/dedup/confirm"):   {"subject_a": "a", "relation_a": "is_a",
                                      "object_a": "b", "subject_b": "c",
                                      "relation_b": "is_a", "object_b": "d",
                                      "keep": "a", "confirmed": False},
    ("POST", "/kb/seed/conceptnet"): {"concepts": ["cat"], "confirmed": False},
    ("POST", "/kb/seed/offline"):    None,
    ("POST", "/kb/seed/wikidata"):   {"concepts": ["cat"], "confirmed": False},
    ("POST", "/model/override"):     {"model": "code", "lock": False},
    ("DELETE", "/model/override"):   None,
    ("POST", "/search/confirm"):     {"query": "q", "confirmed": False},
    ("POST", "/search/execute"):     {"query": "q", "confirmed": False},
}


class _OfflineProvider:
    """`/chat` is the one writer that would otherwise reach the network.

    Stubbed so the correct-token case exercises the real handler instead of
    dying on a ConnectError that this sentinel would then have to special-case
    — and a special case is where a genuinely unguarded writer would hide.
    """

    async def stream_chat(self, model, messages, num_ctx=8192):
        yield "ok"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


@pytest.fixture(scope="module")
def app_and_token(tmp_path_factory, ) -> tuple:
    """An isolated app. Every store path is under tmp — nothing may reach the
    repo's real `data/` tree (Packet A's invariant)."""
    tmp: Path = tmp_path_factory.mktemp("packet_g")
    import os
    os.environ["NEXUS_API_TOKEN"] = GOOD_TOKEN
    services = create_services(
        kb_db_path=tmp / "kb.db",
        chroma_dir=tmp / "chroma",
        eval_log_path=tmp / "turn_metrics.jsonl",
        archive_db_path=tmp / "archive.db",
        snapshots_dir=tmp / "snapshots",
    )
    services.provider = _OfflineProvider()
    return create_app(services), GOOD_TOKEN


def derive_writers(app) -> list[tuple[str, str]]:
    """Every state-changing route, from the router itself.

    This is the function that makes the sentinel self-maintaining: it sees an
    eleventh writer the moment it is registered, whether or not anyone
    remembered to update a list.
    """
    out = []
    for route in app.routes:
        for method in sorted((getattr(route, "methods", None) or set()) & WRITE_METHODS):
            out.append((method, route.path))
    return sorted(out)


def _request(client, method, path, body, headers):
    if body is None:
        return client.request(method, path, headers=headers)
    return client.request(method, path, json=body, headers=headers)


# ---------------------------------------------------------------------------
# The ratchet
# ---------------------------------------------------------------------------

def test_writer_set_matches_the_acknowledged_list(app_and_token):
    """A new state-changing endpoint must be acknowledged, not absorbed.

    If this fails with an unexpected writer, do not delete the assertion:
    decide whether the endpoint needs `Depends(require_token)`, then add it to
    EXPECTED_WRITERS. The failure IS the review step.
    """
    app, _ = app_and_token
    derived = set(derive_writers(app))

    unacknowledged = derived - EXPECTED_WRITERS
    disappeared = EXPECTED_WRITERS - derived
    assert not unacknowledged, (
        f"new state-changing endpoint(s) with no auth decision on record: "
        f"{sorted(unacknowledged)}")
    assert not disappeared, (
        f"writer(s) in EXPECTED_WRITERS no longer exist: {sorted(disappeared)}")
    # O.2b — 10 -> 11 with /kb/assert/confirm.
    # S.2 — 11 -> 13 with /outcome/confirm and /governance/envelope/confirm.
    # A bare count is a second ratchet on the same fact and it must move in
    # the same commit as the set above, or the two disagree and the weaker
    # one wins.
    # W.1 — 13 -> 14 with /kb/index/confirm.
    # AB  — 14 -> 15 with /kb/trails/backfill/confirm.
    # W9  — 15 -> 16 with /commit (D8).
    assert len(derived) == len(EXPECTED_WRITERS) == 16


# ---------------------------------------------------------------------------
# Three cases per writer
# ---------------------------------------------------------------------------

def _cases(app):
    return derive_writers(app)


@pytest.mark.parametrize("method,path", sorted(EXPECTED_WRITERS))
def test_writer_rejects_missing_token(app_and_token, method, path):
    app, _ = app_and_token
    with TestClient(app) as client:
        client.headers.pop("Authorization", None)   # conftest adds one by default
        r = _request(client, method, path, PAYLOADS[(method, path)], {})
    assert r.status_code == UNAUTHORISED, (
        f"{method} {path} accepted a request with NO token "
        f"(got {r.status_code}) — it is missing Depends(require_token)")


@pytest.mark.parametrize("method,path", sorted(EXPECTED_WRITERS))
def test_writer_rejects_wrong_token(app_and_token, method, path):
    app, _ = app_and_token
    with TestClient(app) as client:
        r = _request(client, method, path, PAYLOADS[(method, path)],
                     {"Authorization": f"Bearer {WRONG_TOKEN}"})
    assert r.status_code == UNAUTHORISED, (
        f"{method} {path} accepted a WRONG token (got {r.status_code})")


@pytest.mark.parametrize("method,path", sorted(EXPECTED_WRITERS))
def test_writer_accepts_the_correct_token(app_and_token, method, path):
    """The case usually omitted, and the reason the other two are not enough.

    A writer that rejected *everything* would satisfy both rejection tests
    while being completely broken. This asserts only that the request is not
    turned away *for auth reasons* — a 400/403/422 from the endpoint's own
    Law 3 gate or body validation is a pass, because the request reached the
    handler.
    """
    app, token = app_and_token
    with TestClient(app) as client:
        r = _request(client, method, path, PAYLOADS[(method, path)],
                     {"Authorization": f"Bearer {token}"})
    assert r.status_code != UNAUTHORISED, (
        f"{method} {path} rejected the CORRECT token — auth is broken, not "
        f"merely strict")
    assert r.status_code < 500, (
        f"{method} {path} returned {r.status_code}; the auth check passed but "
        f"the handler crashed, which this sentinel should not mask")


# ---------------------------------------------------------------------------
# Properties of the auth mechanism itself
# ---------------------------------------------------------------------------

def test_rejection_code_is_consistent_across_every_writer(app_and_token):
    """One code for both failure modes, on every writer.

    Pinned because the seed endpoints already disagree on the code for their
    *Law 3* gate (400 / 400 / 403 — ENDPOINT_AUDIT.md P5). Auth must not
    acquire the same drift: a client cannot special-case per endpoint.
    """
    app, _ = app_and_token
    codes: dict[int, list] = {}
    with TestClient(app) as client:
        client.headers.pop("Authorization", None)
        for method, path in sorted(EXPECTED_WRITERS):
            for hdr in ({}, {"Authorization": f"Bearer {WRONG_TOKEN}"},
                        {"Authorization": "not-even-bearer"},
                        {"Authorization": "Bearer "}):
                r = _request(client, method, path, PAYLOADS[(method, path)], hdr)
                codes.setdefault(r.status_code, []).append((method, path, hdr))
    assert set(codes) == {UNAUTHORISED}, (
        f"auth rejection codes are inconsistent: "
        f"{ {k: v[:2] for k, v in codes.items()} }")


def test_read_endpoints_stay_open(app_and_token):
    """The posture is deliberate, so pin it rather than leaving it implied.

    THREAT_MODEL.md / `require_token`'s docstring: most GET endpoints are
    open on a localhost-bound single-user app; the writers, the evidence
    reads (A-2: every GET under /lineage, /belief, /governance and /kb/as-of)
    W4's /whoami, W6's /work-units, W8's record proof and W9's credential, fifteen routes derived below, are gated. If a future
    change closes the
    open reads, that is a decision someone should make on purpose — and this
    failing is how they find out they made it.
    """
    app, _ = app_and_token
    with TestClient(app) as client:
        client.headers.pop("Authorization", None)
        for path in ("/healthz", "/kb/stats", "/cortex/stats", "/state/summary"):
            assert client.get(path).status_code == 200, path
        # ...with deliberate exceptions, which are also pinned. Evidence
        # records are more sensitive than the open-GET posture, so every
        # /lineage/* read is gated — including H.1's verification endpoints,
        # which report on the same records. Z.1's /governance/verify and
        # AG's /governance/budget are gated for the same reason (the budget
        # report additionally names actors, which the open posture must
        # never serve).
        for gated in ("/lineage/recent", "/lineage/verify",
                      "/governance/verify", "/governance/budget",
                      "/governance/refusals",         # AT — names actors
                      "/whoami",                      # W4 — the identity a request carries
                      "/work-units/events"):          # W6 — a unit's closes and reopens; names actors
            assert client.get(gated).status_code == UNAUTHORISED, gated


# ---------------------------------------------------------------------------
# The gated reads (A-2), derived from the router, and the three documents
# that state the GET posture, checked against them (W1 part 5g, C4:
# SECURITY.md said every GET was unauthenticated by design)
# ---------------------------------------------------------------------------

# The prefixes under which every GET needs the token and outside which no
# GET does. The code's rule is the dependency on each route; this tuple is
# what the documents claim, and the test below holds the two together. W4
# added the fifth, `/whoami` (run/specs/W4.md D8): the identity a request
# carries, gated like the evidence reads. W6 added the sixth, `/work-units`
# (run/specs/W6.md D12): the boundary ledger's read, whose rows name the
# actor and the accountable identity of each close and reopen.
# W10b (D7): `/actions/{id}`, a governed action's receipt, is an evidence read like W9's commitment receipt.
GATED_READ_PREFIXES = ("/lineage", "/belief", "/governance", "/kb/as-of", "/whoami", "/work-units", "/commitments",
                       "/actions")
# The documents that state the posture. Each must carry the claim in one
# sentence that names every prefix, and none may state it without them.
SECURITY_DOCUMENTS = ("SECURITY.md", "THREAT_MODEL.md", ".env.example")


def derive_gets(app) -> dict[str, bool]:
    """{path: gated} for every GET route, from the router. Gated means the
    route's own dependant carries `require_token` — the function object, not
    its name and not a path list — the introspection
    tests/test_just_culture_guard.py uses."""
    from backend.server import require_token
    out = {}
    for route in app.routes:
        if "GET" not in (getattr(route, "methods", None) or set()):
            continue
        deps = getattr(getattr(route, "dependant", None), "dependencies", None) or []
        out[route.path] = any(getattr(d, "call", None) is require_token for d in deps)
    return out


def _under_gated_prefix(path: str) -> bool:
    """A route path (never a filesystem path) at or below one of the prefixes."""
    return any(path == p or path.startswith(p + "/") for p in GATED_READ_PREFIXES)


def gated_read_violations(gets: dict) -> list[str]:
    """Both directions: a gated GET under none of the prefixes, and an open
    GET under one of them."""
    found = []
    for path, gated in sorted(gets.items()):
        if gated and not _under_gated_prefix(path):
            found.append(f"GET {path} is gated and lies under none of {GATED_READ_PREFIXES}")
        if not gated and _under_gated_prefix(path):
            found.append(f"GET {path} is open and lies under one of {GATED_READ_PREFIXES}")
    return found


def _sentences(text: str) -> list[str]:
    """A document's sentences: comment markers (.env.example), emphasis and
    code ticks stripped, whitespace collapsed, split after . ! ? at a
    space."""
    lines = [re.sub(r"^\s*#\s?", "", line) for line in text.splitlines()]
    flat = re.sub(r"[*`]", "", " ".join(" ".join(lines).split()))
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", flat) if s.strip()]


_POSTURE_WORDS = re.compile(r"\b(unauthenticated|authenticated|open|gated|token)\b", re.I)


def posture_sentences(text: str) -> list[str]:
    """Every sentence that says something about GET and the auth posture."""
    return [s for s in _sentences(text)
            if re.search(r"\bGET\b", s) and _POSTURE_WORDS.search(s)]


def posture_violations(text: str) -> list[str]:
    """Each GET-posture sentence must name every gated prefix, so 'GET
    endpoints are unauthenticated' and 'every GET is open' fail however they
    are worded; a document with no such sentence fails too (the posture must
    be stated, not left implied)."""
    sentences = posture_sentences(text)
    if not sentences:
        return ["no sentence states the GET posture (GET beside unauthenticated, open, gated or token)"]
    found = []
    for s in sentences:
        missing = [p for p in GATED_READ_PREFIXES if p not in s]
        if missing:
            found.append(f"{s!r} does not name {missing}")
    return found


def test_gated_reads_are_exactly_the_evidence_reads_under_four_prefixes(app_and_token):
    """Derived from the router by the dependency, never by a path list: the
    gated GETs are exactly the routes under the prefixes, in both
    directions, and there are seventeen of them (A-2's eleven, W4's
    /whoami, W6's /work-units/events, W8's /lineage/{lineage_id}/proof,
    W9's /lineage/{lineage_id}/credential and /commitments/{id}/receipt, and
    W10b's /actions/{action_id})."""
    app, _ = app_and_token
    gets = derive_gets(app)
    gated = sorted(p for p, g in gets.items() if g)
    print(f"GET routes {len(gets)}; gated, derived: {gated}")
    assert len(gets) >= 20, "the GET population collapsed"
    assert not gated_read_violations(gets), gated_read_violations(gets)
    for prefix in GATED_READ_PREFIXES:                 # non-vacuity, per prefix
        assert any(p == prefix or p.startswith(prefix + "/") for p in gated), (
            f"no gated GET under {prefix}: the prefix is a claim about nothing")
    # The count beside the set rule: THREAT_MODEL.md, SECURITY.md and
    # .env.example say "sixteen routes" (eleven until W4's /whoami, twelve
    # until W6's /work-units/events, thirteen until W8's proof, fourteen until
    # W9's credential, fifteen until its commitment receipt, sixteen until
    # W10b's action receipt), so an eighteenth moves this line and those
    # sentences in the same commit.
    assert len(gated) == 17, gated


@pytest.mark.parametrize("name", SECURITY_DOCUMENTS)
def test_security_document_states_the_get_posture_with_every_gated_prefix(name):
    """Sentence-level: every sentence of the document that mentions GET and
    the posture names every gated prefix; a document that says nothing
    about the posture fails too. SECURITY.md at a918e25 failed this with
    'Remember GET endpoints are unauthenticated by design under the localhost
    posture' (C4)."""
    text = (ROOT / name).read_text(encoding="utf-8")
    print(f"{name} posture sentences: {posture_sentences(text)}")
    assert not posture_violations(text), posture_violations(text)


def test_companion_an_open_get_under_a_gated_prefix_and_a_gated_get_outside_are_named():
    """COMPANION (OQ-16 rules 1 and 3): a planted router with an open GET
    under a gated prefix and a gated GET under none of them. Neither path is
    in the guard's source; both are named, and a compliant open GET is not."""
    from fastapi import Depends, FastAPI
    from backend.server import require_token
    planted = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @planted.get("/lineage/planted")                        # open, under a gated prefix
    async def open_under_the_prefix():
        return {}

    @planted.get("/planted/report", dependencies=[Depends(require_token)])   # gated, outside
    async def gated_outside_the_prefixes():
        return {}

    @planted.get("/healthz")                                # open, outside: compliant
    async def compliant():
        return {}

    gets = derive_gets(planted)
    assert gets == {"/lineage/planted": False, "/planted/report": True, "/healthz": False}, gets
    found = gated_read_violations(gets)
    assert {f.split()[1] for f in found} == {"/lineage/planted", "/planted/report"}, found


def test_companion_a_sentence_claiming_every_get_is_open_is_named():
    """COMPANION: the sentence SECURITY.md carried until part 5g, planted
    into an otherwise compliant document, is named; so is a sentence naming
    three of the four prefixes (the member the guard's source does not name:
    the rule is per prefix, not 'mentions the token'); and a document with no
    posture sentence at all is named rather than passed. The compliant base
    is synthetic, so the companion does not move when a real document is
    edited."""
    compliant = ("NEXUS_HOST=127.0.0.1\n"
                 "# A document that states the posture as the router has it. The\n"
                 "# evidence reads, the GET routes under /lineage, /belief, /governance,\n"
                 "# /kb/as-of, /work-units, /commitments and /actions, and /whoami, need the token; every other\n"
                 "# GET is open under the localhost posture.\n")
    assert posture_sentences(compliant), "the base states no posture: the plants below would prove nothing"
    assert not posture_violations(compliant), posture_violations(compliant)
    old_claim = ("Remember GET endpoints are unauthenticated by design under the "
                 "localhost posture — see THREAT_MODEL.md before changing the bind.")
    found = posture_violations(compliant + "\n\n" + old_claim + "\n")
    assert len(found) == 1 and "unauthenticated by design" in found[0], found
    three = ("The evidence reads under /lineage, /belief, /governance, /work-units, /commitments and /actions, "
             "and /whoami, need the token; every other GET is open.")
    missing = [p for p in GATED_READ_PREFIXES if p not in three]
    assert missing == ["/kb/as-of"]
    assert posture_violations(compliant + "\n\n" + three + "\n") == [f"{three!r} does not name {missing}"]
    silent = "# Nothing here says anything about reads.\nNEXUS_HOST=127.0.0.1\n"
    assert posture_violations(silent) == [
        "no sentence states the GET posture (GET beside unauthenticated, open, gated or token)"]


def test_token_is_read_from_the_environment_per_call(app_and_token, monkeypatch):
    """`active_token()` reads the env every call (server.py:74), so rotating
    the token takes effect without a restart. Asserted because the sentinel
    above would still pass if the token were captured once at import."""
    app, _ = app_and_token
    with TestClient(app) as client:
        client.headers.pop("Authorization", None)
        monkeypatch.setenv("NEXUS_API_TOKEN", "rotated-token")
        assert client.post("/kairos/cycle",
                           headers={"Authorization": "Bearer rotated-token"}
                           ).status_code != UNAUTHORISED
        assert client.post("/kairos/cycle",
                           headers={"Authorization": f"Bearer {GOOD_TOKEN}"}
                           ).status_code == UNAUTHORISED
