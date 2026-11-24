"""AR (advice recall), A-1 and A-2 — the just-culture guards.

NEXUS never aggregates by person. A surface that returns per-person rows
declares that it does, in its payload, in docs/CONTRACTS.md, and in its ratchet
classification. Two guards, both DERIVED:

  T9   no exposed surface groups, counts, ranks or orders by a person
       field — in SQL-string form and in Python form, by AST over every
       route endpoint plus, one call level deep, the service methods and
       module functions it calls;
  T10  every exposed read whose response carries a person field carries
       all three declarations — found by CALLING every GET route on a
       populated fixture and walking the JSON for a population member,
       because a response is what a consumer receives and a static reading
       of it guesses.

THE POPULATION IS DERIVED, NOT LISTED (`person_field_population`):
  1. every column in the live DDL whose name ends `_by`;
  2. every field the actor vocabulary module defines, as `actor_<field>`;
  3. every DDL column or request-model field whose name begins `accountable`.
A new person field is covered without an edit here. Non-vacuity: the
population and the surface count are printed by the tests.

EXISTING HITS ARE EXEMPTED, NOT FIXED, in this packet: each is named with
the reason `pre-existing, recorded for the register`, and each exemption is
pinned by a test that fails when the hit disappears, so the list cannot
rot. The companions plant a column name that this module's guard source
never mentions — in SQL (T9c), in Python (T9d), and as an undeclared
per-person route (T10c); T9e derives the planted name from the companions
and asserts it occurs nowhere else in this file.

What T9 proves is *not computed here*. It does not prove *not derivable*:
a consumer can group the recall's rows in one line. That is why the
declaration exists.
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import os
import re
import sqlite3
import tempfile
import textwrap
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from tests._tracked import tracked_backend
from tests.fixtures.advice_recall_fixture import build
from tests.test_bind_conditional_routes import PER_PERSON_ROWS

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "just-culture-token"
H = {"Authorization": f"Bearer {TOKEN}"}

# B-40 — THE WORK/AUTHORITY CLAUSE (Fable, 30 September). Aggregates of
# cognition, turns, citations or units by the person doing the work are
# forbidden. Governed acts are attributable to their actor by construction;
# surfaces over them are declared per-person and gated. The guard ALLOWS a
# person-field aggregate on an AUTHORITY surface and forbids it everywhere
# else, and an authority surface is DERIVED, never listed: served by the
# governance router (the APIRouter the app exposes on `app.state`), gated
# with the token dependency, and carrying the per-person declaration. No
# route path appears in this rule, and the module's own source is checked
# for the two live paths (test_guard_source_names_no_governance_path). The
# old two-name "pre-existing" aggregation exemption is gone.
#
# Pre-existing per-person read surfaces without the three declarations;
# each is pinned by test_per_person_surfaces_declare's stale-exemption check.
# B-40 declared the two governance reads and removed them from here.
EXEMPT_DECLARATION: dict[str, str] = {
    "/lineage/{lineage_id}": "pre-existing, recorded for the register: the record carries accountable_identity and boundary_asserted_by; gated",
    "/lineage/recent": "pre-existing, recorded for the register: records carry accountable_identity; gated",
    "/belief/{belief_id}/provenance": "pre-existing, recorded for the register: steps carry the actor fields; gated",
    "/governance/refusals": "pre-existing, recorded for the register: rows carry the actor fields; gated",
}


def _gated(route) -> bool:
    """Gated with the token dependency, by introspection of the route's dependant."""
    from backend.server import require_token
    return any(getattr(d, "call", None) is require_token
               for d in getattr(getattr(route, "dependant", None), "dependencies", []) or [])


def _declares_per_person(route) -> bool:
    try:
        node = _fn_ast(route.endpoint)
    except (OSError, TypeError):
        return False
    return any(isinstance(n, ast.Constant) and n.value == "per_person" for n in ast.walk(node))


def authority_surfaces(app, ratchet: dict | None = None) -> set[str]:
    """DERIVED: the paths served by the app's governance router (the object on
    `app.state.governance_router`, never a prefix string) that are gated and
    carry the per-person declaration in their payload and in the ratchet
    (`PER_PERSON_ROWS`, or the dict a companion passes). Empty when the app
    exposes no governance router."""
    router = getattr(app.state, "governance_router", None)
    if router is None:
        return set()
    ratchet = PER_PERSON_ROWS if ratchet is None else ratchet
    endpoints = {r.endpoint for r in router.routes}
    out = set()
    for route in app.routes:
        if getattr(route, "endpoint", None) not in endpoints:
            continue
        if _gated(route) and route.path in ratchet and _declares_per_person(route):
            out.add(route.path)
    return out


# ---------------------------------------------------------------------------
# the population, derived
# ---------------------------------------------------------------------------

def _live_schema_columns() -> list[tuple[str, str]]:
    """(table, column) over every table the application's DDL creates, by
    executing the real constructors into a scratch directory."""
    from backend.server import create_services
    from tests.conftest import OfflineTestProvider
    os.environ.setdefault("NEXUS_API_TOKEN", TOKEN)
    d = Path(tempfile.mkdtemp())
    svc = create_services(kb_db_path=d / "kb.db", chroma_dir=d / "chroma", eval_log_path=d / "m.jsonl",
                          archive_db_path=d / "a.db", snapshots_dir=d / "snap", provider=OfflineTestProvider())
    out = []
    for conn in (svc.store.conn, svc.archive_chain.conn):          # the two stores, on either backend
        for t in conn.tables():
            out += [(t, col.name) for col in conn.columns(t)]
    del svc
    return out


def _request_model_fields() -> list[str]:
    import backend.server as srv
    out = []
    for _name, obj in inspect.getmembers(srv):
        if inspect.isclass(obj) and issubclass(obj, BaseModel) and obj is not BaseModel:
            out += list(obj.model_fields)
    return out


def person_field_population(schema_columns=None, request_fields=None) -> set[str]:
    """The person fields, derived from three sources (A-1). `schema_columns`
    and `request_fields` exist so a companion can feed a planted schema."""
    from backend.kb.recalibration import Actor
    cols = schema_columns if schema_columns is not None else _live_schema_columns()
    fields = request_fields if request_fields is not None else _request_model_fields()
    pop = {c for _t, c in cols if c.endswith("_by")}
    pop |= {f"actor_{f.name}" for f in dataclasses.fields(Actor)}
    pop |= {c for _t, c in cols if c.startswith("accountable")}
    pop |= {f for f in fields if f.startswith("accountable")}
    return pop


# ---------------------------------------------------------------------------
# the surfaces, derived — T9's static population
# ---------------------------------------------------------------------------

def _backend_defs() -> tuple[dict[str, dict[str, ast.AST]], dict[str, list[ast.AST]]]:
    """(class -> method -> node, module-level function name -> nodes) over
    the tracked backend."""
    methods: dict[str, dict[str, ast.AST]] = {}
    functions: dict[str, list[ast.AST]] = {}
    for f in tracked_backend():
        tree = ast.parse(Path(f).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.setdefault(node.name, []).append(node)
            elif isinstance(node, ast.ClassDef):
                for m in node.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.setdefault(node.name, {})[m.name] = m
    return methods, functions


def _service_classes() -> dict[str, str]:
    """NexusServices attribute -> class name, from the dataclass's own
    annotations (the string's last identifier, so `"WorkUnitStore | None"`
    reads WorkUnitStore)."""
    from backend.server import NexusServices
    out = {}
    for f in dataclasses.fields(NexusServices):
        t = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", str(f.type))
        ids = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", t)
        ids = [i for i in ids if i not in ("None", "Optional", "list", "dict", "tuple", "set")]
        if ids:
            out[f.name] = ids[0]
    return out


def _fn_ast(fn) -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]


def _callees(node: ast.AST, methods, functions, service_classes) -> list[tuple[str, ast.AST]]:
    """One call level deep: `services.<attr>.<m>(…)` (or via a local name
    bound to `services.<attr>`) resolves to that service class's method;
    a bare `f(…)` resolves to module-level functions named f."""
    bound: dict[str, str] = {}
    for n in ast.walk(node):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) \
                and isinstance(n.value, ast.Attribute) and isinstance(n.value.value, ast.Name) \
                and n.value.value.id == "services" and n.value.attr in service_classes:
            bound[n.targets[0].id] = service_classes[n.value.attr]
    out = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name):
            out += [(f.id, d) for d in functions.get(f.id, [])]
        elif isinstance(f, ast.Attribute):
            cls = None
            v = f.value
            if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) and v.value.id == "services":
                cls = service_classes.get(v.attr)
            elif isinstance(v, ast.Name) and v.id in bound:
                cls = bound[v.id]
            if cls and f.attr in methods.get(cls, {}):
                out.append((f.attr, methods[cls][f.attr]))
    return out


def exposed_surfaces(app) -> list[tuple[str, str, ast.AST]]:
    """(surface, function name, AST): every route endpoint, its one-level
    callees resolved as above, and every backend function whose name
    contains `export` (none today; the derivation is what would find one)."""
    methods, functions = _backend_defs()
    service_classes = _service_classes()
    out = []
    for route in app.routes:
        path = getattr(route, "path", None)
        endpoint = getattr(route, "endpoint", None)
        if not path or endpoint is None or path.startswith(("/docs", "/redoc", "/openapi")):
            continue
        try:
            node = _fn_ast(endpoint)
        except (OSError, TypeError):
            continue
        out.append((path, endpoint.__name__, node))
        for name, callee in _callees(node, methods, functions, service_classes):
            out.append((path, name, callee))
    for name, defs in functions.items():
        if "export" in name.lower():
            out += [(f"export:{name}", name, d) for d in defs]
    return out


# ---------------------------------------------------------------------------
# the detector — SQL form and Python form
# ---------------------------------------------------------------------------

def _member_in_expr(node: ast.AST, population: set[str], bound: dict[str, ast.AST]) -> str | None:
    """The member named by `node`: a subscript `x["m"]`, an attribute `x.m`,
    or a name bound in the same function to either (one hop)."""
    for n in ast.walk(node):
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and n.slice.value in population:
            return n.slice.value
        if isinstance(n, ast.Attribute) and n.attr in population:
            return n.attr
        if isinstance(n, ast.Name) and n.id in bound:
            hit = _member_in_expr(bound[n.id], population, {})
            if hit:
                return hit
    return None


def _sql_hits(text: str, population: set[str]) -> list[tuple[str, str]]:
    hits = []
    for kw in ("GROUP BY", "ORDER BY"):
        for m in re.finditer(kw + r"\s+([^;]*)", text, re.I):
            for member in population:
                if re.search(rf"\b{re.escape(member)}\b", m.group(1)):
                    hits.append((kw.lower().replace(" ", "_"), member))
    for m in re.finditer(r"COUNT\s*\(([^)]*)\)", text, re.I):
        for member in population:
            if re.search(rf"\b{re.escape(member)}\b", m.group(1)):
                hits.append(("count", member))
    return hits


def detect_person_aggregation(node: ast.AST, population: set[str]) -> list[dict]:
    """Every grouping, counting, ranking or ordering over a population member
    in one function: SQL string constants and Python forms."""
    hits: list[dict] = []
    bound: dict[str, ast.AST] = {}
    for n in ast.walk(node):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            bound[n.targets[0].id] = n.value
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            for form, member in _sql_hits(n.value, population):
                hits.append({"form": f"sql:{form}", "member": member, "line": n.lineno})
        elif isinstance(n, ast.Call):
            name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if name in ("Counter", "groupby", "defaultdict"):
                for arg in n.args:
                    m = _member_in_expr(arg, population, bound)
                    if m:
                        hits.append({"form": f"py:{name}", "member": m, "line": n.lineno})
            if name in ("sorted", "max", "min"):
                for kw in n.keywords:
                    if kw.arg == "key":
                        m = _member_in_expr(kw.value, population, bound)
                        if m:
                            hits.append({"form": f"py:{name}(key=)", "member": m, "line": n.lineno})
            if name == "setdefault" and n.args:
                m = _member_in_expr(n.args[0], population, bound)
                if m:
                    hits.append({"form": "py:dict.setdefault", "member": m, "line": n.lineno})
        elif isinstance(n, (ast.Assign, ast.AugAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Subscript):
                    m = _member_in_expr(t.slice, population, bound)
                    if m:
                        hits.append({"form": "py:dict[member] =", "member": m, "line": n.lineno})
        elif isinstance(n, (ast.DictComp, ast.SetComp)):
            m = _member_in_expr(n.key if isinstance(n, ast.DictComp) else n.elt, population, bound)
            if m:
                hits.append({"form": "py:comprehension-key", "member": m, "line": n.lineno})
    return hits


def person_aggregation_hits(app, population=None) -> list[dict]:
    population = population if population is not None else person_field_population()
    out = []
    for surface, fname, node in exposed_surfaces(app):
        for h in detect_person_aggregation(node, population):
            out.append({"surface": surface, "function": fname, **h})
    return out


# ---------------------------------------------------------------------------
# T10's population — the responses, walked
# ---------------------------------------------------------------------------

def _actor_field_names() -> set[str]:
    from backend.kb.recalibration import Actor
    return {f.name for f in dataclasses.fields(Actor)}


def _keys_in(obj, population: set[str], found: set[str]) -> None:
    """A population member as a key anywhere in the document; and the actor
    vocabulary's nested form — `"actor": {"id", "role", "source"}` — read as
    the `actor_<field>` members it serialises (derived from the dataclass,
    so a fourth actor field is covered without an edit here)."""
    if isinstance(obj, dict):
        actor_fields = _actor_field_names()
        for k, v in obj.items():
            if k in population:
                found.add(k)
            if k == "actor" and isinstance(v, dict) and actor_fields <= set(v):
                found |= {f"actor_{f}" for f in actor_fields if f"actor_{f}" in population}
            _keys_in(v, population, found)
    elif isinstance(obj, list):
        for v in obj:
            _keys_in(v, population, found)


def per_person_surfaces(client, population, substitutions: dict[str, str],
                        headers: dict | None = None) -> dict[str, set[str]]:
    """Every GET route whose JSON response carries a population member as a
    key, anywhere in the document. Called on the app's own router, with path
    parameters substituted from the fixture."""
    found: dict[str, set[str]] = {}
    for route in client.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path or "GET" not in methods or path.startswith(("/docs", "/redoc", "/openapi")):
            continue
        url = path
        for k, v in substitutions.items():
            url = url.replace(k, v)
        r = client.get(url, headers=headers or {})
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("application/json"):
            continue
        members: set[str] = set()
        _keys_in(r.json(), population, members)
        if members:
            found[path] = members
    return found


def declarations_for(app, path: str) -> dict[str, bool]:
    """The three declarations a per-person surface must carry."""
    payload = False
    for route in app.routes:
        if getattr(route, "path", None) == path:
            node = _fn_ast(route.endpoint)
            payload = any(isinstance(n, ast.Constant) and n.value == "per_person" for n in ast.walk(node))
    claude = (ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
    return {"payload": payload,
            "contracts_md": path in claude and "per-person" in claude,
            "ratchet": path in PER_PERSON_ROWS}


# ---------------------------------------------------------------------------
# the app under test — the recall fixture, with a supersede on record
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    from backend.server import create_app, create_services
    tmp = tmp_path_factory.mktemp("just_culture")
    # W1 — set for this module and restored after it. Assigned directly, they
    # outlived the module: every later module ran with this token and wrote
    # its calibration rows here (conftest's per-module redirect check).
    env = pytest.MonkeyPatch()
    env.setenv("NEXUS_API_TOKEN", TOKEN)
    env.setenv("NEXUS_CALIBRATION_LOG", str(tmp / "cal.jsonl"))
    fx = build(tmp)
    svc = create_services(kb_db_path=fx.kb_path, chroma_dir=tmp / "chroma", eval_log_path=tmp / "m.jsonl",
                          archive_db_path=fx.archive_path, snapshots_dir=tmp / "snap")
    client = TestClient(create_app(svc))
    # One governance row of each kind, so every governance read has a person field to show.
    client.post("/governance/envelope/confirm", json={"version": "v1", "max_delta": 0.1,
                "movable_classes": ["asserted"], "reason": "fixture", "confirmed": True,
                "actor_id": "board"}, headers=H)
    client.post("/outcome/confirm", json={"event_type": "review", "decision": "ok", "direction": "neutral",
                "references": {"belief_ids": [fx.rev3]}, "confirmed": True, "actor_id": "alice"}, headers=H)
    client.post("/kb/index/confirm", json={"confirmed": False}, headers=H)     # a recorded refusal
    # W6 — a unit's close, so the boundary ledger's read has a row to show, with its actor and its accountable
    # identity (the verifier's finding: with no act in the fixture the guard never saw that read). Made through
    # the store's one call for an act off a turn: this fixture's services have no model to answer a turn.
    svc.work_units.resolve_for_turn(session_id="jc", requested_id="jc-unit", accountable="alice",
                                    actor={"id": "alice"}, boundary="close")
    yield fx, svc, client
    env.undo()


# ---------------------------------------------------------------------------
# T9 — no person aggregation on exposed surfaces
# ---------------------------------------------------------------------------

def test_no_person_aggregation_on_exposed_surfaces(rig):
    """No exposed surface groups, counts, ranks or orders by a person field; the fields are
    derived, so a new one is covered without an edit here."""
    _fx, _svc, client = rig
    population = person_field_population()
    print(f"\nT9 population ({len(population)}): {sorted(population)}")
    assert len(population) >= 5 and "accountable_identity" in population
    surfaces = exposed_surfaces(client.app)
    print(f"T9 surfaces walked: {len(surfaces)} (route endpoints plus one resolved call level)")
    assert len(surfaces) > 40
    hits = person_aggregation_hits(client.app, population)
    authority = authority_surfaces(client.app)
    unexempt = [h for h in hits if h["surface"] not in authority]
    print(f"T9 hits: {len(hits)}, on an authority surface: {len(hits) - len(unexempt)}: "
          f"{[(h['surface'], h['function'], h['form'], h['member']) for h in hits]}")
    assert not unexempt, f"person aggregation on a non-authority surface: {unexempt}"
    # The allowed direction: grouping by work unit passes.
    unit_route = ast.parse(textwrap.dedent("""
        async def by_unit(rows):
            from collections import Counter
            return Counter(r["work_unit_id"] for r in rows)
    """)).body[0]
    assert detect_person_aggregation(unit_route, population) == []


def test_authority_surfaces_are_derived(rig):
    """B-40, non-vacuity: the authority-surface set derived today is exactly two routes, both
    under the governance router's prefix (read from the router object), printed — and the live
    per-actor aggregation the budget report performs falls on one of them, so the rule is
    exercised rather than vacuous. No path is listed here."""
    _fx, _svc, client = rig
    router = client.app.state.governance_router
    authority = authority_surfaces(client.app)
    on_router = {x.endpoint for x in router.routes}
    print(f"\nB-40 authority surfaces (derived): {sorted(authority)}; router prefix {router.prefix!r}; "
          f"routes on the router: {sorted(r.path for r in client.app.routes if getattr(r, 'endpoint', None) in on_router)}")
    assert len(authority) == 2 and all(p.startswith(router.prefix + "/") for p in authority)
    assert authority <= set(PER_PERSON_ROWS)
    on_authority = [h for h in person_aggregation_hits(client.app) if h["surface"] in authority]
    assert on_authority, "the budget report no longer aggregates per actor — the rule has nothing to allow"
    assert {h["member"] for h in on_authority} & {"actor_id", "actor_role", "actor_source"}


def test_governance_reads_gated_same_body(rig):
    """Both governance reads return 401 without a token, and with one the same body as before
    B-40 apart from the declaration block."""
    _fx, _svc, client = rig
    client.headers.pop("Authorization", None)
    for path in sorted(authority_surfaces(client.app)):
        assert client.get(path).status_code == 401, path
        body = client.get(path, headers=H).json()
        assert body["per_person"]["declared"] is True and body["per_person"]["label"] == "asserted"
        rest = {k: v for k, v in body.items() if k != "per_person"}
        assert rest and "per_person" not in rest
        assert "Aggregates of cognition" in body["per_person"]["note"]


# ---------------------------------------------------------------------------
# B-40 companions — the authority rule, planted from every side
# ---------------------------------------------------------------------------

def _fixture_governance_app(*, on_router: bool, gated: bool, declared: bool, aggregate: str):
    """A fixture app with its own governance APIRouter on app.state, and one route placed as
    the arguments say, whose body performs `aggregate` (a SQL string over a person field)."""
    from fastapi import APIRouter, Depends
    from backend.server import require_token
    app = FastAPI()
    router = APIRouter(prefix="/governance")
    app.state.governance_router = router
    deps = [Depends(require_token)] if gated else []
    target = router if on_router else app
    src = ("async def planted(conn):\n"
           f"    rows = conn.execute({aggregate!r}).fetchall()\n"
           + ("    return {'rows': rows, 'per_person': {'declared': True}}\n" if declared
              else "    return {'rows': rows}\n"))
    ns: dict = {}
    exec(src, ns)                                    # the route's source is inspectable
    import linecache
    linecache.cache["<planted>"] = (len(src), None, src.splitlines(True), "<planted>")
    ns["planted"].__code__ = ns["planted"].__code__.replace(co_filename="<planted>")
    target.get("/planted", dependencies=deps)(ns["planted"])
    for r in router.routes:                          # mounted flat, as the app does (B-40)
        app.router.routes.append(r)
    path = "/governance/planted" if on_router else "/planted"
    ratchet = {path: "planted"} if declared else {}
    return app, path, ratchet


def _planted_hits(app, path):
    route = next(r for r in app.routes if getattr(r, "path", None) == path)
    return detect_person_aggregation(_fn_ast(route.endpoint), person_field_population())


def test_companion_1_work_aggregate_outside_the_router_is_named():
    """Companion 1: a route OUTSIDE the governance router that groups by `actor_id` is named,
    even though it is gated and declared."""
    app, path, ratchet = _fixture_governance_app(
        on_router=False, gated=True, declared=True,
        aggregate="SELECT actor_id, COUNT(*) FROM outcome_events GROUP BY actor_id")
    hits = _planted_hits(app, path)
    assert hits and {h["member"] for h in hits} == {"actor_id"}
    assert path not in authority_surfaces(app, ratchet), "a route off the router is not an authority surface"


def test_companion_2_undeclared_route_on_the_router_is_named():
    """Companion 2: a route ON the governance router that returns actor fields WITHOUT the
    declaration is named — by the authority rule (not an authority surface) and by the
    declaration guard."""
    app, path, ratchet = _fixture_governance_app(
        on_router=True, gated=True, declared=False,
        aggregate="SELECT actor_id, COUNT(*) FROM outcome_events GROUP BY actor_id")
    assert _planted_hits(app, path)
    assert path not in authority_surfaces(app, ratchet)
    assert not any(declarations_for(app, path).values())


def test_companion_3_declared_but_open_route_on_the_router_is_named():
    """Companion 3: a route on the governance router, declared but OPEN, is named."""
    app, path, ratchet = _fixture_governance_app(
        on_router=True, gated=False, declared=True,
        aggregate="SELECT actor_id, COUNT(*) FROM outcome_events GROUP BY actor_id")
    assert _planted_hits(app, path)
    assert path not in authority_surfaces(app, ratchet), "an open route is not an authority surface"
    # The mirror, non-vacuity: the same route gated IS an authority surface.
    app2, path2, ratchet2 = _fixture_governance_app(
        on_router=True, gated=True, declared=True,
        aggregate="SELECT actor_id, COUNT(*) FROM outcome_events GROUP BY actor_id")
    assert authority_surfaces(app2, ratchet2) == {path2}


@pytest.mark.xfail(strict=True, reason=(
    "authority is derived from router membership; a work aggregate on the governance router "
    "passes; deriving authority from the tables a route reads (governance tables, FZ-2's "
    "confirmed-act set, versus lineage, triples and work units) is the structural fix, on its "
    "own line."))
def test_companion_4_work_aggregate_dressed_as_authority_is_named():
    """Companion 4 (Fable): the edge of the guard. A route ON the governance router, gated and
    declared, that aggregates `lineage_records` by `accountable` — a WORK aggregate dressed as an
    authority surface. The router-based rule cannot name it: it is an authority surface by
    construction and the aggregate is allowed. Pinned as xfail with the reason."""
    app, path, ratchet = _fixture_governance_app(
        on_router=True, gated=True, declared=True,
        aggregate="SELECT accountable_identity, COUNT(*) FROM lineage_records GROUP BY accountable_identity")
    hits = _planted_hits(app, path)
    assert hits and {h["member"] for h in hits} == {"accountable_identity"}
    assert path not in authority_surfaces(app, ratchet), "a work aggregate must not pass as authority"


def test_guard_source_names_no_governance_path(rig):
    """The guard's source contains neither live governance read path, by AST: the authority rule
    keys on the router object, and the paths it allows today are derived, not written here."""
    _fx, _svc, client = rig
    live = authority_surfaces(client.app)
    assert len(live) == 2
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    # A whole path, not a prefix: the fixture that installs an envelope posts to the write
    # route one segment further on, and that is not a read surface.
    named = [(n.lineno, n.value) for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and any(re.search(rf"(?<![\w/]){re.escape(p)}(?![\w/])", n.value) for p in live)]
    assert not named, f"the guard names an authority path: {named}"


# ---------------------------------------------------------------------------
# T9c / T9d / T9e — the companions plant a field the guard never names
# ---------------------------------------------------------------------------

def _planted_population() -> tuple[set[str], str]:
    planted_column = "reviewed_by"
    planted_schema = _live_schema_columns() + [("reviews", planted_column)]
    pop = person_field_population(planted_schema, _request_model_fields())
    assert planted_column in pop and planted_column not in person_field_population()
    return pop, planted_column


def test_planted_person_field_sql_is_named():
    """A person field the guard's source never mentions is caught through the derivation."""
    pop, col = _planted_population()
    route = ast.parse(textwrap.dedent("""
        async def reviews_by_reviewer(conn):
            rows = conn.execute("SELECT reviewed_by, COUNT(*) FROM reviews GROUP BY reviewed_by").fetchall()
            return {"rows": rows}
    """)).body[0]
    hits = detect_person_aggregation(route, pop)
    assert hits and all(h["member"] == col for h in hits), hits
    assert {h["form"] for h in hits} == {"sql:group_by"}


def test_planted_person_field_python_is_named():
    """The same field in the Python form is caught too, because a guard over one syntactic
    form is B-35's defect."""
    pop, col = _planted_population()
    route = ast.parse(textwrap.dedent("""
        async def reviews_by_reviewer(rows):
            from collections import Counter
            counts = Counter(r["reviewed_by"] for r in rows)
            key = (r["reviewed_by"], r["scope"])
            per = {}
            per.setdefault(key, 0)
            ranked = sorted(rows, key=lambda r: r["reviewed_by"])
            return counts, per, ranked
    """)).body[0]
    hits = detect_person_aggregation(route, pop)
    forms = {h["form"] for h in hits}
    assert {"py:Counter", "py:dict.setdefault", "py:sorted(key=)"} <= forms, forms
    assert all(h["member"] == col for h in hits)


COMPANIONS = {"test_planted_person_field_sql_is_named", "test_planted_person_field_python_is_named",
              "test_undeclared_per_person_route_is_named", "_planted_population",
              "_fixture_governance_app", "test_companion_1_work_aggregate_outside_the_router_is_named",
              "test_companion_2_undeclared_route_on_the_router_is_named",
              "test_companion_3_declared_but_open_route_on_the_router_is_named",
              "test_companion_4_work_aggregate_dressed_as_authority_is_named"}


def test_guard_source_names_no_planted_field():
    """The companions prove derivation only if the guard never names what they plant."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    # The planted names are DERIVED from the companions: every `_by` string
    # constant they carry that the live population does not.
    live = person_field_population()
    planted: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in COMPANIONS:
            for n in ast.walk(node):
                if isinstance(n, ast.Constant) and isinstance(n.value, str):
                    planted |= {w for w in re.findall(r"[a-z_]+_by\b", n.value) if w not in live}
    assert planted, "the companions plant nothing"
    outside = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in COMPANIONS:
            continue
        for n in ast.walk(node):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and any(p in n.value for p in planted):
                outside.append(n.lineno)
    assert not outside, f"the guard names a planted field {sorted(planted)} at lines {outside}"


# ---------------------------------------------------------------------------
# T10 / T10c — per-person surfaces declare
# ---------------------------------------------------------------------------

def test_per_person_surfaces_declare(rig):
    """A surface that returns per-person rows says so in its payload note, docs/CONTRACTS.md and its
    ratchet classification."""
    fx, _svc, client = rig
    population = person_field_population()
    found = per_person_surfaces(client, population, {
        "{belief_id}": str(fx.rev3), "{lineage_id}": fx.lineage_ids[0]}, headers=H)
    print(f"\nT10 GET surfaces whose response carries a person field: {len(found)}")
    for path, members in sorted(found.items()):
        print(f"   {path}: {sorted(members)}  {'(exempt, pre-existing)' if path in EXEMPT_DECLARATION else ''}")
    to_declare = {p: m for p, m in found.items() if p not in EXEMPT_DECLARATION}
    assert to_declare, "non-vacuity: at least one surface (the recall route) must need the declaration"
    assert "/belief/{belief_id}/recall" in to_declare
    for path in to_declare:
        decl = declarations_for(client.app, path)
        assert all(decl.values()), f"{path} returns per-person rows and lacks a declaration: {decl}"
    stale = sorted(set(EXEMPT_DECLARATION) - set(found))
    assert not stale, f"exemptions for surfaces that no longer carry a person field: {stale}"


def test_undeclared_per_person_route_is_named():
    """A new per-person surface without the declaration fails."""
    pop, col = _planted_population()
    planted = FastAPI()

    @planted.get("/reviews")
    async def reviews():
        return {"rows": [{"reviewed_by": "carol", "n": 1}]}

    with TestClient(planted) as c:
        found = per_person_surfaces(c, pop, {})
    assert found == {"/reviews": {col}}
    decl = declarations_for(planted, "/reviews")
    assert not any(decl.values()), decl
