"""W3b part 4 — the parity claims, measured on both backends in one process
(run/specs/W3b.md S2's P1 to P5, P9, P10, and the close's P11; S3: T9). The SQLite side opens
`StoreTarget.sqlite(path)` and the Postgres side a fresh schema on the named
test database, so both run in the one job that names a database (the
Postgres job); without `NEXUS_TEST_DATABASE_URL` each test skips by name.

R   THE RIG (P1, P2, P3). CE's rig (tests/fixtures/ce_rig.py: its
    deterministic clock, the stub provider) runs twelve turns on each
    backend with the lineage ids minted from one counter, so the two runs
    can be compared record for record: every record's `record_hash` and
    every field of it, and every record recomputing to its hash on its own
    store (P1); the injected ids in RECORD order, not the rig's sorted copy
    (P2); every TurnRow — the cascade's estimates, each tier's
    before/after/removed, the stop reason (P3). Two shapes: the pinned one
    (seventeen claims, which every term's quota admits whole) and a cutting
    one (thirty claims, so the per-term LIMIT decides which tied claims are
    candidates: where a tie-break could part the backends). The clock is
    read once more by the first `add()` of an unregistered source in a
    process (`classify` logs one warning, and the log record's timestamp is a
    read of the rig's clock: measured), so each arm starts from an empty
    warned set and both take that read. Companions: Postgres given `real`
    (float4) for REAL columns, the type D4 forbids, is named at the first
    turn, and its records no longer recompute to their hashes on their own
    store; Postgres given the tie-break reversed on `search_terms` is named at
    the first turn, on the cutting shape, with a different injected set.
A   AS-OF (P4): the as-of fixture built on each backend; the pages equal at
    the earliest admission, at every archival event's instant and after the
    last act; and two archival acts stamped the same second (an archival and
    its reversal), whose order only the ledger's rowid decides. Companion:
    the ledger read in reverse rowid order on Postgres is named.
C   THE TEXT ORDERS (P11; the close's F3). The DDL twin maps TEXT to `TEXT
    COLLATE "C"` so that every text ORDER BY sorts by bytes, as SQLite's
    BINARY does; a Postgres database's own collation (the deployment's
    en_US.utf8, or ICU) puts `alpha` before `Beta`, ignores `_` or puts it
    before the digits, and files `école` beside `ecole`. C1 derives, by AST
    over the tracked backend (tests/_tracked.py), every ORDER BY whose terms
    include a column the schema the store classes make declares TEXT, prints
    each with its file:line and what reads it, and holds the list to
    `TEXT_ORDERS`: each site answered for by a parity case or by a
    construction, so a new text order fails C1 until it is answered (C2
    plants two the table does not name, a plain string and an aliased
    f-string, and both are named). C3: four surfaces, each with rows that
    tie on every key before the text key they exercise (mixed case, `_`,
    `-`, a space, digits, non-ASCII), the same rows in the same order on
    both backends: the as-of page (claims admitted at one instant; then
    ties on the subject, on subject and relation, on everything but the
    source), the trail siblings (one subject and relation; then one
    object), a claim's units (one first-seen turn) and the recorded trail
    pairs (then one rel_a). The source key is exercised with two
    mixed-case spellings the store accepts unregistered (Law X's warning,
    decayed as extraction), because no pair of the registered sources
    moves under a linguistic collation (C7 checks it). C4, the companion:
    the same rows on a Postgres schema whose DDL maps TEXT to a linguistic
    collation chosen from the server (the first of en-US-x-icu, und-x-icu,
    en_US.utf8, en_US that exists and orders 'Beta' after 'alpha', checked
    there; skipped by name when the server has none): C3's comparison names
    every surface and the rows that moved, one planted mover per text key
    of each order. Without the twin's COLLATE "C" (TEXT mapped to plain
    TEXT, the database's en_US.UTF-8) C3 fails, naming all four surfaces.
    Two text orders need no case, by construction. `records_injecting`
    breaks a timestamp tie on `lineage_id`, which is `str(uuid.uuid4())`,
    minted by `record_turn` alone; `verify_request_bodies` orders
    `request_hash`, which the one writer keeps only when the bytes hash to
    it (a sha256 hexdigest; tests/test_w0b_capture.py holds the refusal).
    Values of one length over `[0-9a-f]`, with any hyphens at the same
    places and no case pairs, sort as their bytes under every default
    collation Postgres offers (C; glibc's locales; ICU's root and en-US):
    in each, the digits precede the Latin letters, each run ascends, each
    glyph is distinct at the first level, and an aligned hyphen never
    decides. A tailoring that put the digits after the letters or compared
    digit runs as numbers (ICU's `kr` and `kn` options) would move them;
    the DDL's `COLLATE "C"` keeps them as bytes whatever the database's
    default. C5 pins the construction on the rig's records (every lineage
    id, in the records and in the recall index, and every kept request hash
    in its one form), C6 plants an id and a hash outside the form and C5's
    check names both, and C7 orders a sample of each under every default
    collation the server has and finds the bytes' order.
S   SEARCH (P5): `search_terms` over a corpus with non-ASCII capitals, mixed
    case, `_`, `%`, a space and a backslash, under the test database's
    en_US.UTF-8: the same candidates in the same order. The backslash is the
    term part 4 found parting the backends (Postgres's LIKE escapes with it,
    SQLite's has no escape character: `ESCAPE ''` in the dialect); the
    companion takes the rule away and the backslash terms part again.
G   THE GRAPH (P9): saved, rewritten by an upsert, reopened: the same
    nodes and edges in the same order on both; the companion plants the order
    the establish rejected (`ORDER BY source, target`) on the Postgres load
    and is named.
M   THE SUMS (P10): the three recalibration sums equal on both and equal
    `math.fsum` over the same random deltas. Companion: Postgres's own
    `sum(double precision)` over those rows, the SQL D6 removed, differs
    from `fsum` for a belief, so a store that summed in SQL would be named.
"""
from __future__ import annotations

import ast
import hashlib
import itertools
import json
import math
import random
import re
import secrets
import uuid
from pathlib import Path
from unittest import mock

import pytest

from tests._tracked import REPO, tracked_backend
from tests.conftest import POSTGRES_TEST_URL as PG_URL  # noqa: E402

NO_PG = "no Postgres named: NEXUS_TEST_DATABASE_URL is unset (the Postgres job sets it; windows and macos run SQLite)"
RIG = dict(pairs=20, answer_chars=800, user_chars=76)        # the pinned rig's shape (test_mcp2_build, SNIP-DET)
PINNED_CLAIMS, CUTTING_CLAIMS = 17, 30


@pytest.fixture
def pg_target(tmp_path):
    """A fresh schema on the named Postgres, as a StoreTarget; dropped afterwards."""
    if not PG_URL:
        pytest.skip(NO_PG)
    import psycopg
    from backend.store import StoreTarget
    schema = "p_" + hashlib.sha1(str(tmp_path).encode()).hexdigest()[:20]
    try:
        yield StoreTarget.postgres(PG_URL, schema=schema)
    finally:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def lite(path: Path):
    from backend.store import StoreTarget
    return StoreTarget.sqlite(path)


# ---------------------------------------------------------------------------
# R — the rig on both backends
# ---------------------------------------------------------------------------

def _counter_ids():
    n = itertools.count(1)
    return lambda: uuid.UUID(int=next(n))


def run_rig(root: Path, store, claims: int, turns: int = 12) -> list[dict]:
    """Twelve turns of CE's rig on `store`, the lineage ids from a counter
    and the warned-source set emptied first: one dict per turn with the row,
    the record, whether it recomputes on its store, and the injected ids in
    record order."""
    from backend.kb import tiered_store
    from tests.fixtures.ce_rig import CERig
    with mock.patch.object(tiered_store, "_warned_sources", set()), \
            mock.patch("backend.memory.lineage.uuid.uuid4", side_effect=_counter_ids()):
        rig = CERig(root, kb_lines=claims, store=store, **RIG)
        try:
            out = []
            for row in rig.run(turns):
                rec = rig.lineage.get(row.lineage_id)
                out.append({"turn": row.turn, "row": row.as_tuple(), "record_hash": rec.record_hash,
                            "record": json.dumps(rec.to_dict(), sort_keys=True, default=str),
                            "intact": rig.lineage.verify_record(row.lineage_id)["record_intact"],
                            "injected": [i["belief_id"] for i in rec.injected]})
            return out
        finally:
            rig.close()


def first_divergence(a: list[dict], b: list[dict]) -> "str | None":
    """The first turn and claim at which two runs part, or None."""
    if len(a) != len(b):
        return f"the runs have {len(a)} and {len(b)} turns"
    for x, y in zip(a, b):
        for key, claim in (("record_hash", "P1 record_hash"), ("record", "P1 the record"),
                           ("intact", "P1 the record recomputing on its store"),
                           ("injected", "P2 the injected ids in record order"), ("row", "P3 the turn row")):
            if x[key] != y[key]:
                return f"turn {x['turn']}: {claim} differs"
    return None


@pytest.mark.parametrize("claims", [PINNED_CLAIMS, CUTTING_CLAIMS], ids=["pinned", "cutting"])
def test_r1_the_rig_runs_the_same_on_both_backends(tmp_path, pg_target, claims):
    sqlite_run = run_rig(tmp_path / "lite", lite(tmp_path / "lite" / "kb.db"), claims)
    pg_run = run_rig(tmp_path / "pg", pg_target, claims)
    fired = [t["turn"] for t in sqlite_run if t["row"][2]]
    print(f"R1 ({claims} claims): {len(sqlite_run)} turns, a compaction fire at turns {fired}; turn 1 injected "
          f"{len(sqlite_run[0]['injected'])} ids, {sqlite_run[0]['injected'][:5]}…; turn 12 record "
          f"{sqlite_run[-1]['record_hash'][:16]}…")
    assert fired, "the rig fired no tier: P3 would compare nothing"
    assert all(t["intact"] for t in sqlite_run + pg_run), "a record does not recompute on its own store"
    assert first_divergence(sqlite_run, pg_run) is None, first_divergence(sqlite_run, pg_run)


def test_r2_companion_float4_on_postgres_is_named_at_the_first_turn(tmp_path, pg_target, monkeypatch):
    """D4 forbids `real` (float4) for a REAL column. Planted, the record's
    instant loses its digits on the way into the store, so the record read
    back is not the record written and does not recompute to its hash."""
    from backend.store import dialect
    sqlite_run = run_rig(tmp_path / "lite", lite(tmp_path / "lite" / "kb.db"), PINNED_CLAIMS, turns=2)
    monkeypatch.setitem(dialect.TYPE_MAP, "REAL", "REAL")
    pg_run = run_rig(tmp_path / "pg", pg_target, PINNED_CLAIMS, turns=2)
    found = first_divergence(sqlite_run, pg_run)
    print(f"R2: {found}; the turn-1 record intact on Postgres: {pg_run[0]['intact']}")
    assert found is not None and found.startswith("turn 1: P1"), found
    assert pg_run[0]["intact"] is False and sqlite_run[0]["intact"] is True


def test_r3_companion_a_reversed_tie_break_is_named_where_the_quota_cuts(tmp_path, pg_target, monkeypatch):
    """On the cutting shape the per-term LIMIT admits twenty of thirty tied
    claims and the tie-break decides which; reversed on the Postgres side,
    the injected set moves at turn 1. (On the pinned shape every tied claim
    is admitted, so this plant moves nothing there: the reason the cutting
    shape exists.)"""
    from backend.store import dialect
    real = dialect.translate
    tie = "ORDER BY tier ASC, weight DESC, id ASC LIMIT"

    def reversed_tie(sql, has_params=True):
        return real(sql.replace(tie, "ORDER BY tier ASC, weight DESC, id DESC LIMIT"), has_params)

    sqlite_run = run_rig(tmp_path / "lite", lite(tmp_path / "lite" / "kb.db"), CUTTING_CLAIMS, turns=2)
    monkeypatch.setattr(dialect, "translate", reversed_tie)
    pg_run = run_rig(tmp_path / "pg", pg_target, CUTTING_CLAIMS, turns=2)
    found = first_divergence(sqlite_run, pg_run)
    print(f"R3: {found}; turn 1 injected on SQLite {sorted(sqlite_run[0]['injected'])[:3]}… and on Postgres "
          f"{sorted(pg_run[0]['injected'])[:3]}…")
    assert found is not None and found.startswith("turn 1:"), found
    assert set(sqlite_run[0]["injected"]) != set(pg_run[0]["injected"]), "the quota cut no tie group"


# ---------------------------------------------------------------------------
# A — as-of
# ---------------------------------------------------------------------------

def _pages(kb_target) -> dict:
    from backend.kb.as_of import earliest_admission, held_at
    from backend.kb.tiered_store import TripleStore
    store = TripleStore(kb_target)
    try:
        db = store.conn
        first = earliest_admission(db)
        events = [float(r[0]) for r in db.execute("SELECT DISTINCT ts FROM archival_events ORDER BY ts")]
        last = max([first] + events)
        out = {}
        for ts in [first] + events + [math.nextafter(last, math.inf)]:
            page, total = held_at(db, ts, limit=1000)
            out[repr(ts)] = (total, [repr(sorted(r.items())) for r in page])
        return out
    finally:
        store.close()


def test_a1_as_of_pages_are_equal_at_every_instant_that_matters(tmp_path, pg_target):
    from tests.fixtures.as_of_fixture import build
    lite_target = lite(tmp_path / "lite" / "kb.db")
    build(tmp_path / "lite", store=lite_target)
    build(tmp_path / "pg", store=pg_target)
    a, b = _pages(lite_target), _pages(pg_target)
    held = {k: v[0] for k, v in a.items()}
    print(f"A1: {len(a)} instants compared; claims held at each: {list(held.values())}")
    assert len(a) >= 5 and len(set(held.values())) > 1, "the fixture's acts moved nothing as-of would show"
    assert a == b


def _same_second(tmp_path, pg_target) -> dict:
    """An archival and its reversal stamped the same second, on each
    backend: what as-of says at that instant."""
    from backend.kb.as_of import held_at
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    results = {}
    for name, target in (("sqlite", lite(tmp_path / "s" / "kb.db")), ("postgres", pg_target)):
        store = TripleStore(target)
        try:
            with mock.patch("backend.kb.tiered_store.time.time", return_value=1_700_000_000.0):
                bid = store.add(subject="ganymede", relation="orbits", object="jupiter", weight=0.8, tier=1,
                                granularity=Granularity.TOPIC, source="asserted")
            with mock.patch("backend.kb.tiered_store.time.time", return_value=1_700_000_100.0):
                store.archive_ids([bid], reason="same second")
                store.unarchive_ids([bid], reason="same second")
            page, total = held_at(store.conn, 1_700_000_100.0, limit=10)
            results[name] = (total, [(r["belief_id"], r["status_at"], r["archival_at_d"]["last_event"])
                                     for r in page])
        finally:
            store.close()
    return results


def test_a2_two_acts_stamped_the_same_second_are_ordered_by_the_ledger(tmp_path, pg_target):
    """Held at that instant only because the ledger's rowid puts the
    reversal last, on both."""
    results = _same_second(tmp_path, pg_target)
    print(f"A2: {results}")
    assert results["sqlite"] == results["postgres"] == (1, [(1, "held", "unarchived")]), results


def test_a3_companion_the_ledger_read_in_reverse_rowid_order_is_named(tmp_path, pg_target, monkeypatch):
    from backend.store import dialect
    real = dialect.translate

    def reversed_ledger(sql, has_params=True):
        return real(sql.replace("ORDER BY e.ts DESC, e.rowid DESC", "ORDER BY e.ts DESC, e.rowid ASC"), has_params)

    monkeypatch.setattr(dialect, "translate", reversed_ledger)
    results = _same_second(tmp_path, pg_target)
    assert results["sqlite"] != results["postgres"] and results["postgres"][0] == 0, results


# ---------------------------------------------------------------------------
# C — the text orders (P11)
# ---------------------------------------------------------------------------

#: What reads each surface's order. A parity case (C3, C4) measures each.
TEXT_SURFACES = {
    "as-of page": "backend/kb/as_of.py held_at: the page order of GET /kb/as-of and MCP nexus_asof (AS-1), "
                  "and the migration's as-of proof",
    "trail siblings": "backend/kb/tiered_store.py TripleStore.search_subject_relation: which siblings the trail "
                      "channel admits when NEXUS_TRAIL_SLOTS cuts, so the injected set and record_hash",
    "a claim's units": "backend/kb/work_units.py WorkUnitStore.units_for_triple: a store read no route calls today",
    "recorded trail pairs": "backend/kb/trails.py TrailStore._classified: its lists' order (status() sorts them "
                            "and active_map() keys them, so no route shows it)",
}
UUID_FORM = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
HEX_FORM = re.compile(r"[0-9a-f]{64}")
#: The text orders whose values sort as their bytes under every default
#: collation by construction (the module docstring's reason; pinned by C5).
CONSTRUCTIONS = {
    "uuid": "the recall page's tie-break (GET /belief/{id}/recall, MCP nexus_recall): lineage_id is "
            "str(uuid.uuid4()), minted by LineageStore.record_turn alone",
    "hex": "the order of /lineage/verify's request_bodies report: request_hash is a sha256 hexdigest, kept only "
           "when the bytes hash to it",
}
#: Every text ORDER BY the tracked backend holds, (file, function, clause) ->
#: the surface a parity case measures or the construction that answers it.
#: C1 derives the population and holds it to this table, both directions.
TEXT_ORDERS = {
    ("backend/kb/as_of.py", "held_at",
     "created_at ASC, subject ASC, relation ASC, object ASC, source ASC"): "as-of page",
    ("backend/kb/tiered_store.py", "TripleStore.search_subject_relation",
     "object ASC, source ASC, id ASC"): "trail siblings",
    ("backend/kb/work_units.py", "WorkUnitStore.units_for_triple",
     "j.first_seen_turn, j.work_unit_id"): "a claim's units",
    ("backend/kb/trails.py", "TrailStore._classified", "rel_a, rel_b"): "recorded trail pairs",
    ("backend/memory/lineage.py", "LineageStore.records_injecting", "r.ts ASC, r.lineage_id ASC"): "uuid",
    ("backend/memory/lineage.py", "LineageStore.verify_request_bodies", "request_hash"): "hex",
}

_ORDER_BY = re.compile(r"\bORDER\s+BY\b", re.I)
_CLAUSE_STOP = re.compile(r"(?:LIMIT|OFFSET)\b|;", re.I)
_TABLE_REF = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_]\w*)(?:\s+(?:AS\s+)?([A-Za-z_]\w*))?", re.I)
_NOT_ALIAS = frozenset({"where", "join", "on", "using", "group", "order", "limit", "offset", "left", "right",
                        "inner", "outer", "cross", "natural", "union", "as", "and", "or", "set", "values", "select"})
_PLAIN_TERM = re.compile(r"(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)(?:\s+(?:ASC|DESC))?(?:\s+NULLS\s+(?:FIRST|LAST))?",
                         re.I)
_IDENT = re.compile(r"[A-Za-z_]\w*")


def declared_types(tmp_path) -> dict[str, dict[str, str]]:
    """table -> column -> declared type, from the schema the store classes
    make (the DDL and the open-time migrations: T3's `_real_schema`), read
    on SQLite, whose `table_info` reports the type as the DDL declares it."""
    from backend.store import StoreTarget
    from tests.test_w3b_dialect import _real_schema
    store = _real_schema(StoreTarget.sqlite(tmp_path / "declared" / "kb.db"))
    try:
        return {t: {c.name: c.type.upper() for c in store.conn.columns(t)} for t in store.conn.tables()}
    finally:
        store.close()


def _string_text(node, consts: dict) -> "str | None":
    """The text a string node spells: a constant, or an f-string with the
    module's string constants resolved and any other value a placeholder
    (a term made of one resolves to no column)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                out.append(str(part.value))
            elif isinstance(part.value, ast.Name) and part.value.id in consts:
                out.append(consts[part.value.id])
            else:
                out.append(" {?} ")
        return "".join(out)
    return None


def _table_refs(masked: str) -> list[tuple[str, "str | None"]]:
    out = []
    for m in _TABLE_REF.finditer(masked):
        alias = m.group(2) if m.group(2) and m.group(2).lower() not in _NOT_ALIAS else None
        out.append((m.group(1), alias))
    return out


def _clause_after(masked: str, start: int) -> str:
    """An ORDER BY's terms: to LIMIT, OFFSET, `;`, the `)` that closes its
    subquery, or the end."""
    depth, k = 0, start
    while k < len(masked):
        ch = masked[k]
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and not (masked[k - 1].isalnum() or masked[k - 1] == "_") and _CLAUSE_STOP.match(masked, k):
            break
        k += 1
    return masked[start:k]


def _split_terms(clause: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in clause:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [" ".join(p.split()) for p in parts if p.strip()]


def _term_kind(term: str, refs, scope_refs, types) -> str:
    """The declared type a term sorts by: a column of a table the statement
    names (its alias respected), else of a table its function names (a CTE
    or a statement assembled from pieces); `unresolved` when no table in
    reach declares it or the term is an expression over no TEXT column."""
    m = _PLAIN_TERM.fullmatch(term)
    if m is None:
        names = set(_IDENT.findall(term)) - {"ASC", "DESC", "asc", "desc", "COLLATE", "collate"}
        for candidates in (refs, scope_refs):
            if any(types.get(t, {}).get(n) == "TEXT" for t, _a in candidates for n in names):
                return "TEXT"
        return "unresolved"
    alias, col = m.group(1), m.group(2)
    if col.lower() == "rowid":
        return "INTEGER"                       # SQLite's rowid; the twin's ordinal is BIGINT (D3)
    for candidates in (refs, scope_refs):
        kinds = {types[t][col] for t, a in candidates
                 if t in types and col in types[t] and (alias is None or alias in (a, t))}
        if kinds:
            return "TEXT" if "TEXT" in kinds else "/".join(sorted(kinds))
    return "unresolved"


def order_sites(paths, types) -> list[dict]:
    """Every ORDER BY in the SQL text the modules at `paths` hold, by AST:
    every string constant and f-string outside a docstring that spells one,
    each clause with its file:line, its function, its terms and the type
    each sorts by. A fragment with no terms (`sql.replace("ORDER BY", …)`)
    is skipped: the clause it edits is found where it is spelled."""
    from backend.store import dialect
    sites = []
    for path in paths:
        path = Path(path)
        try:
            rel = path.resolve().relative_to(REPO).as_posix()
        except ValueError:
            rel = path.name                    # a planted module outside the repository (C2)
        # The interface's own SQL (information_schema; the support tables,
        # which spell COLLATE "C" themselves) is the driver's, not a store's
        # rows: T3's line, on the same `.as_posix()` keys.
        if rel.startswith("backend/store/"):
            continue
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        consts = {n.targets[0].id: n.value.value for n in tree.body
                  if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                  and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                      and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}

        def strings_in(scope) -> list[str]:
            out, todo = [], [scope]
            while todo:
                n = todo.pop()
                if isinstance(n, (ast.Constant, ast.JoinedStr)):
                    text = _string_text(n, consts) if id(n) not in docstrings else None
                    if text:
                        out.append(text)
                    continue                   # an f-string's pieces are not strings of their own
                todo.extend(ast.iter_child_nodes(n))
            return out

        def clauses(node, text, function, scope_texts):
            masked, held = dialect.mask(text)
            refs = _table_refs(masked)
            scope_refs = [r for t in scope_texts for r in _table_refs(dialect.mask(t)[0])]
            segment = ast.get_source_segment(src, node) or ""
            hits = [m.start() for m in _ORDER_BY.finditer(segment)]
            for k, m in enumerate(_ORDER_BY.finditer(masked)):
                clause = " ".join(dialect.unmask(_clause_after(masked, m.end()), held).split())
                if not clause:
                    continue
                line = node.lineno + (segment[:hits[k]].count("\n") if k < len(hits) else 0)
                terms = [(t, _term_kind(t, refs, scope_refs, types)) for t in _split_terms(clause)]
                sites.append({"file": rel, "line": line, "function": function, "clause": clause, "terms": terms})

        def visit(node, qual, scope_texts):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, qual + [child.name], strings_in(child))
                elif isinstance(child, ast.ClassDef):
                    visit(child, qual + [child.name], scope_texts)
                elif isinstance(child, (ast.Constant, ast.JoinedStr)):
                    text = _string_text(child, consts) if id(child) not in docstrings else None
                    if text and _ORDER_BY.search(text):
                        clauses(child, text, ".".join(qual), scope_texts)
                else:
                    visit(child, qual, scope_texts)

        visit(tree, [], strings_in(tree))
    return sites


def _is_text(site: dict) -> bool:
    return any(kind == "TEXT" for _t, kind in site["terms"])


def _text_columns(site: dict) -> str:
    """The TEXT terms of a site, their direction dropped: `subject, relation`."""
    return ", ".join(re.sub(r"\s+(?:ASC|DESC)\b.*$", "", t, flags=re.I) for t, kind in site["terms"] if kind == "TEXT")


def check_text_orders(paths, types) -> tuple[list[dict], list[dict]]:
    """C1's rule: every text ORDER BY at `paths` is answered for in
    `TEXT_ORDERS`, every entry there is still found, every answer is a
    surface C3 measures or a construction C5 pins, and no term sorts by a
    value the DDL does not declare. Raises naming each site that breaks it."""
    sites = order_sites(paths, types)
    text = [s for s in sites if _is_text(s)]
    found = {(s["file"], s["function"], s["clause"]) for s in text}
    problems = []
    for s in text:
        if (s["file"], s["function"], s["clause"]) not in TEXT_ORDERS:
            problems.append(f"{s['file']}:{s['line']} {s['function']}: ORDER BY {s['clause']} sorts by TEXT "
                            f"({_text_columns(s)}) "
                            f"and no parity case or construction answers for it: a database's collation decides "
                            f"its order unless the column's COLLATE \"C\" does (add a case to C3 and C4, or a "
                            f"construction to C5)")
    for key in sorted(set(TEXT_ORDERS) - found):
        problems.append(f"{key}: answered for in TEXT_ORDERS but no longer found: remove it or say where it went")
    for key, answer in TEXT_ORDERS.items():
        if answer not in TEXT_SURFACES and answer not in CONSTRUCTIONS:
            problems.append(f"{key}: answered by {answer!r}, which is no surface C3 measures and no construction")
    for s in sites:
        for term, kind in s["terms"]:
            if kind == "unresolved":
                problems.append(f"{s['file']}:{s['line']} {s['function']}: ORDER BY {s['clause']}: the term "
                                f"{term!r} sorts by a value no table in reach declares (an expression or a runtime "
                                f"identifier): answer it by hand")
    if problems:                               # raised, not asserted: the message is the problems and nothing else
        raise AssertionError("\n".join(problems))
    return sites, text


def test_c1_every_text_order_the_backend_holds_is_answered_for(tmp_path):
    types = declared_types(tmp_path)
    sites, text = check_text_orders(tracked_backend(), types)
    print(f"C1: {len(sites)} ORDER BY clauses in the tracked backend, {len(text)} sorting by a column declared TEXT:")
    for s in sorted(text, key=lambda s: (s["file"], s["line"])):
        answer = TEXT_ORDERS[(s["file"], s["function"], s["clause"])]
        verdict = (f"its values can sort differently under a linguistic collation: measured by C3/C4 ({answer})"
                   if answer in TEXT_SURFACES else
                   f"its values cannot, by construction ({answer}): pinned by C5, checked by C7")
        reads = (TEXT_SURFACES.get(answer) or CONSTRUCTIONS[answer]).split(": ", 1)[-1]
        print(f"  {s['file']}:{s['line']} {s['function']}: ORDER BY {s['clause']} [TEXT: {_text_columns(s)}]"
              f" — {reads}; {verdict}")
    files = {s["file"] for s in sites}
    assert len(sites) >= 30 and "backend/kb/tiered_store.py" in files and "backend/memory/lineage.py" in files
    assert len(text) == len(TEXT_ORDERS)


def test_c2_companion_a_text_order_the_table_does_not_name_is_named(tmp_path):
    """Two text orders C1's table does not name, beside the tracked backend:
    a plain string over `chunks.source` and an f-string whose table is a
    module constant, through an alias. C1's rule fails and names both, each
    at its line (whatever else it names is C1's to report)."""
    planted = tmp_path / "planted_orders.py"
    planted.write_text(
        'TABLE = "refused_requests"\n'
        "\n\n"
        "def chunk_sources(conn):\n"
        '    return conn.execute("SELECT DISTINCT source FROM chunks ORDER BY source").fetchall()\n'
        "\n\n"
        "def rules(conn, n):\n"
        '    return conn.execute(f"SELECT r.rule FROM {TABLE} r ORDER BY r.ts DESC, r.rule LIMIT {n}").fetchall()\n',
        encoding="utf-8")
    types = declared_types(tmp_path)
    with pytest.raises(AssertionError) as refused:
        check_text_orders(list(tracked_backend()) + [planted], types)
    named = [line for line in str(refused.value).splitlines() if line.startswith("planted_orders.py")]
    print("C2:\n  " + "\n  ".join(named))
    assert len(named) == 2, str(refused.value)
    assert named[0].startswith("planted_orders.py:5 chunk_sources: ORDER BY source sorts by TEXT (source)"), named
    assert named[1].startswith("planted_orders.py:9 rules: ORDER BY r.ts DESC, r.rule sorts by TEXT (r.rule)"), named


TEXT_T0 = 1_700_000_000.0
TEXT_SUBJECTS = ("alpha claim", "Beta claim", "_under claim", "a-b claim", "a b claim", "Zeta claim", "école claim",
                 "10 claims", "9 claims")
TEXT_RELATIONS = ("part_of", "partner", "p31", "p_31")        # slugs: `_` against a letter and against a digit
TEXT_OBJECTS = ("alpha", "Beta", "_x", "a-b", "a b", "Zeta", "école")
TEXT_SOURCES = ("extraction", "Extraction", "wikidata", "Wikidata")
TEXT_UNITS = ("WU-alpha", "wu-Beta", "WU_under", "WU 7", "WU-10", "WU-9", "école-1", "Zeta-unit")
#: One row per text key of each order that leaves its place under a
#: linguistic collation, under ICU's en-US and root and under glibc's
#: en_US.utf8 alike (measured on the scratch cluster: C4 under ICU, C3
#: under the database's own en_US.UTF-8 with TEXT unmapped). A row can keep
#: its place while its neighbours swap: under glibc ('shared object',
#: 'Extraction') stays sixth among the siblings, so the source key's mover
#: there is ('shared object', 'wikidata').
TEXT_MOVERS = {
    "as-of page": [("Beta claim", "orbits", "jupiter moon", "human_assertion"),        # the subject decides
                   ("shared subject", "p_31", "jupiter moon", "human_assertion"),      # the relation
                   ("shared subject", "orbits", "Beta", "human_assertion"),            # the object
                   ("shared subject", "orbits", "shared object", "Extraction")],       # the source
    "trail siblings": [("Beta", "human_assertion"), ("shared object", "wikidata")],    # the object; the source
    "a claim's units": ["école-1"],
    "recorded trail pairs": [("p31", "orbits"), ("orbits", "p_31")],                   # rel_a; rel_b
}


def text_order_surfaces(target) -> dict:
    """Each text-ordered surface's rows, in its order, on `target`. Every
    claim is admitted at one instant (as-of orders by admission first); each
    group then ties on the keys before the text key it exercises."""
    from backend.kb import tiered_store
    from backend.kb.as_of import held_at
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    from backend.kb.trails import TrailStore
    from backend.kb.work_units import WorkUnitStore, actor_fields
    out: dict = {}
    store = TripleStore(target)
    try:
        def add(subject, relation, obj, source="human_assertion"):
            return store.add(subject=subject, relation=relation, object=obj, weight=0.5, tier=1,
                             granularity=Granularity.TOPIC, source=source)

        # The warned set emptied: two sources here are unregistered, and the
        # process-wide set must be left as the rest of the suite expects it.
        with mock.patch.object(tiered_store, "_warned_sources", set()), \
                mock.patch("backend.kb.tiered_store.time.time", return_value=TEXT_T0):
            claim = [add(s, "orbits", "jupiter moon") for s in TEXT_SUBJECTS][0]
            for r in TEXT_RELATIONS:
                add("shared subject", r, "jupiter moon")
            for o in TEXT_OBJECTS:
                add("shared subject", "orbits", o)
            for source in TEXT_SOURCES:
                add("shared subject", "orbits", "shared object", source)
        page, total = held_at(store.conn, TEXT_T0 + 1, limit=1000)
        assert total == len(page) == len(TEXT_SUBJECTS) + len(TEXT_RELATIONS) + len(TEXT_OBJECTS) + len(TEXT_SOURCES)
        out["as-of page"] = [(r["subject"], r["relation"], r["object"], r["source"]) for r in page]
        siblings = store.search_subject_relation("shared subject", "orbits")
        out["trail siblings"] = [(t.object, t.source) for t in siblings]
        units = WorkUnitStore(store.conn)
        for u in TEXT_UNITS:
            units.open(u, work_unit_type="claim", opened_by=actor_fields(), now=TEXT_T0)
            units.admit([claim], u, turn=3)
        out["a claim's units"] = [r["work_unit_id"] for r in units.units_for_triple(claim)]
    finally:
        store.close()
    trails = TrailStore(target)
    try:
        rels = ("orbits",) + TEXT_RELATIONS
        trails.conn.executemany(
            "INSERT INTO relation_trails (rel_a, rel_b, event_count, recurrence_count, last_counted_at, "
            "first_seen_ts, last_seen_ts) VALUES (?, ?, 0, 0, 0, ?, ?)",
            [(a, b, TEXT_T0, TEXT_T0) for a in rels for b in rels if a != b])
        trails.conn.commit()
        out["recorded trail pairs"] = [tuple(p) for p in trails._classified()["below_floor"]]
    finally:
        trails.close()
    return out


def text_parted(lite_rows: dict, other_rows: dict) -> dict:
    """surface -> every row whose place differs, as (row, its place on
    SQLite, its place on the other store; None where it is absent)."""
    out = {}
    for surface, rows in lite_rows.items():
        theirs = other_rows.get(surface, [])
        if rows == theirs:
            continue
        mine, at = {r: i for i, r in enumerate(rows)}, {r: j for j, r in enumerate(theirs)}
        out[surface] = ([(r, i, at.get(r)) for i, r in enumerate(rows) if at.get(r) != i]
                        + [(r, None, j) for j, r in enumerate(theirs) if r not in mine])
    return out


def describe_parted(parted: dict) -> str:
    return "\n".join(f"{surface} ({TEXT_SURFACES.get(surface, '?')}): {len(moved)} rows moved: "
                     + ", ".join(f"{row!r} {i}->{j}" for row, i, j in moved)
                     for surface, moved in parted.items())


@pytest.fixture
def pg_text():
    """A fresh schema, a random suffix in its name, on the named Postgres;
    dropped afterwards."""
    if not PG_URL:
        pytest.skip(NO_PG)
    import psycopg
    from backend.store import StoreTarget
    schema = f"c_{secrets.token_hex(8)}"
    try:
        yield StoreTarget.postgres(PG_URL, schema=schema)
    finally:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


LINGUISTIC = ("en-US-x-icu", "und-x-icu", "en_US.utf8", "en_US")


def linguistic_collations() -> list[str]:
    """The candidates the server has that order 'Beta' after 'alpha' (bytes
    put 'B', 0x42, before 'a', 0x61), in preference order."""
    import psycopg
    out = []
    with psycopg.connect(PG_URL, autocommit=True) as c:
        for name in LINGUISTIC:
            if c.execute("SELECT 1 FROM pg_collation WHERE collname = %s", (name,)).fetchone() is None:
                continue
            try:
                after = c.execute(f"SELECT 'Beta' > ('alpha' COLLATE \"{name}\")").fetchone()[0]
            except psycopg.Error:
                continue                       # a collation for another encoding than the database's
            if after:
                out.append(name)
    return out


def test_c3_every_text_order_is_the_same_on_both_backends(tmp_path, pg_text):
    a = text_order_surfaces(lite(tmp_path / "lite" / "kb.db"))
    b = text_order_surfaces(pg_text)
    assert set(a) == set(TEXT_SURFACES) == set(TEXT_MOVERS)
    print("C3: the orders on SQLite, which Postgres under the DDL's COLLATE \"C\" must give row for row:\n  "
          + "\n  ".join(f"{surface}: {rows}" for surface, rows in a.items()))
    parted = text_parted(a, b)
    assert not parted, describe_parted(parted)


def test_c4_companion_under_a_linguistic_collation_every_text_order_moves(tmp_path, pg_text, monkeypatch):
    """The same rows on a Postgres schema whose DDL maps TEXT to the first
    linguistic collation the server has: C3's comparison names every
    surface, and the rows that moved include one mover per text key."""
    from backend.store import dialect
    found = linguistic_collations()
    if not found:
        pytest.skip(f"the server has none of {LINGUISTIC} ordering 'Beta' after 'alpha': no linguistic "
                    f"collation to plant")
    a = text_order_surfaces(lite(tmp_path / "lite" / "kb.db"))
    monkeypatch.setitem(dialect.TYPE_MAP, "TEXT", f'TEXT COLLATE "{found[0]}"')
    b = text_order_surfaces(pg_text)
    parted = text_parted(a, b)
    named = describe_parted(parted)
    print(f"C4 (TEXT COLLATE \"{found[0]}\"):\n{named}")
    assert set(parted) == set(TEXT_SURFACES), named
    for surface, movers in TEXT_MOVERS.items():
        moved = [row for row, _i, _j in parted[surface]]
        for row in movers:
            assert row in moved and repr(row) in named, (surface, row, named)


def _off_form(db) -> list[str]:
    """Every lineage id (the records, the recall index) and every kept
    request hash on `db` that is not in its one form."""
    out = []
    for table, col, form in (("lineage_records", "lineage_id", UUID_FORM),
                             ("lineage_injections", "lineage_id", UUID_FORM),
                             ("request_bodies", "request_hash", HEX_FORM)):
        values = [row[0] for row in db.execute(f"SELECT {col} FROM {table}").fetchall()]
        for value in sorted({v for v in values if not (isinstance(v, str) and form.fullmatch(v))}, key=repr):
            out.append(f"{table}.{col} = {value!r} ({values.count(value)} rows) is not in the form {form.pattern}")
    return out


def check_one_form(db) -> dict:
    """C5's rule: the constructed values the uuid and hex orders sort by are
    all in their one form, and there are some. Raises naming each value
    outside it."""
    counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("lineage_records", "lineage_injections", "request_bodies")}
    problems = _off_form(db)
    if problems:
        raise AssertionError("\n".join(problems))
    return counts


def _rig_records(root: Path, target, turns: int):
    """CE's rig on `target`, `turns` turns, with whatever ids the record
    writer mints (uuid4 as it is, unless the caller patched it) and the
    warned-source set emptied as run_rig empties it. The caller closes it."""
    from backend.kb import tiered_store
    from tests.fixtures.ce_rig import CERig
    with mock.patch.object(tiered_store, "_warned_sources", set()):
        rig = CERig(root, kb_lines=PINNED_CLAIMS, store=target, **RIG)
        try:
            rig.run(turns)
        except BaseException:
            rig.close()
            raise
    return rig


def test_c5_the_uuid_and_hex_orders_sort_values_of_one_form(tmp_path):
    rig = _rig_records(tmp_path / "rig", lite(tmp_path / "rig" / "kb.db"), turns=3)
    try:
        counts = check_one_form(rig.lineage.conn)
        sample = rig.lineage.conn.execute("SELECT lineage_id, request_hash FROM lineage_records").fetchone()
    finally:
        rig.close()
    print(f"C5: {counts}; e.g. lineage_id {sample[0]!r}, request_hash {sample[1][:16]}…")
    assert counts["lineage_records"] == 3 and counts["lineage_injections"] > 0 and counts["request_bodies"] > 0, counts


def test_c6_companion_an_id_and_a_hash_outside_the_form_are_named(tmp_path):
    """The record writer given ids that are not uuids (a counter of
    `Turn-B<n>`), and a request body row planted under an upper-case
    digest beside the writer: C5's check fails and names both."""
    minted = (f"Turn-B{n}" for n in itertools.count(1))

    class _Id:
        def __init__(self):
            self.text = next(minted)

        def __str__(self):
            return self.text

    from backend.memory.lineage import REQUEST_ENCODING_MESSAGES
    with mock.patch("backend.memory.lineage.uuid.uuid4", side_effect=_Id):
        rig = _rig_records(tmp_path / "rig", lite(tmp_path / "rig" / "kb.db"), turns=1)
    try:
        body = b"planted"
        rig.lineage.conn.execute(
            "INSERT INTO request_bodies (request_hash, encoding, body, raw_bytes, first_seen_ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (hashlib.sha256(body).hexdigest().upper(), REQUEST_ENCODING_MESSAGES, body, len(body), TEXT_T0))
        rig.lineage.conn.commit()
        with pytest.raises(AssertionError) as refused:
            check_one_form(rig.lineage.conn)
    finally:
        rig.close()
    named = str(refused.value)
    print(f"C6:\n{named}")
    assert "lineage_records.lineage_id = 'Turn-B" in named and "lineage_injections.lineage_id = 'Turn-B" in named
    assert f"request_bodies.request_hash = '{hashlib.sha256(b'planted').hexdigest().upper()}'" in named


def test_c7_the_constructed_values_sort_as_bytes_under_every_default_collation_the_server_has():
    """The construction's reason, checked on the server: canonical uuid4s and
    sha256 hexdigests ordered under C and under each linguistic collation the
    server has come back in the bytes' order, while the as-of subjects (the
    control) do not, so the collations are linguistic indeed. The registered
    sources are checked the same way: they are why C3's source key is
    exercised with unregistered spellings."""
    if not PG_URL:
        pytest.skip(NO_PG)
    import psycopg
    from backend.kb.tiered_store import SOURCE_CLASS
    rng = random.Random(20261110)
    samples = {"uuid": [str(uuid.UUID(int=rng.getrandbits(128), version=4)) for _ in range(400)],
               "hex": [hashlib.sha256(rng.randbytes(24)).hexdigest() for _ in range(400)],
               "registered sources": list(SOURCE_CLASS)}
    assert all(UUID_FORM.fullmatch(v) for v in samples["uuid"]) and all(HEX_FORM.fullmatch(v) for v in samples["hex"])
    collations = ["C"] + linguistic_collations()
    if len(collations) == 1:
        pytest.skip(f"the server has none of {LINGUISTIC} ordering 'Beta' after 'alpha': only C to order under")
    with psycopg.connect(PG_URL, autocommit=True) as c:
        def ordered(values, collation):
            return [r[0] for r in c.execute(
                f'SELECT v FROM unnest(%s::text[]) AS u(v) ORDER BY v COLLATE "{collation}"', (values,))]
        for collation in collations:
            for name, values in samples.items():
                assert ordered(values, collation) == sorted(values), (
                    f"{name} do not sort as bytes under {collation}"
                    + (": a registered pair now moves, so C3's source key could use registered sources and the "
                       "module docstring's reason for the unregistered spellings no longer holds"
                       if name == "registered sources" else ""))
        control = {coll: ordered(list(TEXT_SUBJECTS), coll) != sorted(TEXT_SUBJECTS) for coll in collations}
    print(f"C7: {len(samples['uuid'])} uuids, {len(samples['hex'])} hex digests and the "
          f"{len(samples['registered sources'])} registered sources in bytes' order under {collations}; the "
          f"subjects' order moves under {[k for k, v in control.items() if v]}")
    assert control["C"] is False and all(v for k, v in control.items() if k != "C") and len(collations) > 1


# ---------------------------------------------------------------------------
# S — search_terms
# ---------------------------------------------------------------------------

CORPUS = ["École Normale", "école normale", "naïve_set", "naive set", "100% juice", "100 juice", "two words",
          "two-words", "back\\slash", "backslash", "a\\b", "ab", "MiXeD CaSe", "mixed case", "Ärger", "ärger",
          "a_b", "axb", "trail\\", "plain"]
TERMS = [["école"], ["naïve_set"], ["100%"], ["two words"], ["back\\slash"], ["a\\b"], ["trail\\"], ["mixed"],
         ["ärger"], ["a_b"], ["\\"], ["école", "mixed", "a_b"]]
BACKSLASH_TERMS = [repr(t) for t in TERMS if any("\\" in x for x in t)]


def _search(tmp_path, pg_target) -> dict:
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    out = {}
    for name, target in (("sqlite", lite(tmp_path / "s" / "kb.db")), ("postgres", pg_target)):
        store = TripleStore(target)
        try:
            for i, s in enumerate(CORPUS):
                store.add(subject=s, relation="is", object=f"item {i}", weight=0.5 + (i % 3) * 0.1, tier=1,
                          granularity=Granularity.TOPIC, source="asserted")
            out[name] = {repr(t): [x.subject for x in store.search_terms(t, limit=40)] for t in TERMS}
        finally:
            store.close()
    return out


def test_s1_search_terms_finds_the_same_candidates_in_the_same_order(tmp_path, pg_target):
    out = _search(tmp_path, pg_target)
    diff = {t: (out["sqlite"][t], out["postgres"][t]) for t in out["sqlite"] if out["sqlite"][t] != out["postgres"][t]}
    print(f"S1: {len(TERMS)} term lists; the backslash terms find "
          f"{ {t: out['sqlite'][t] for t in BACKSLASH_TERMS} }")
    assert not diff, diff


def test_s2_companion_without_the_escape_rule_the_backslash_terms_part(tmp_path, pg_target, monkeypatch):
    from backend.store import dialect
    monkeypatch.setattr(dialect, "_like_without_escape", lambda masked, held: masked)
    dialect.translate.cache_clear()
    try:
        out = _search(tmp_path, pg_target)
    finally:
        dialect.translate.cache_clear()
    parted = sorted(t for t in out["sqlite"] if out["sqlite"][t] != out["postgres"][t])
    print(f"S2: without ESCAPE '' the terms that part: {parted}")
    assert parted and set(parted) <= set(BACKSLASH_TERMS), parted


# ---------------------------------------------------------------------------
# G — the graph
# ---------------------------------------------------------------------------

EDGES = [("b", "a", 0.3), ("a", "c", 0.6), ("a", "b", 0.4), ("c", "a", 0.2), ("b", "c", 0.5)]


def _reloaded(target) -> dict:
    """Saved, rewritten by an upsert (a new weight on a saved edge: its row
    keeps its place) plus one new edge, saved again, reopened: the
    hippocampus's node order, edge order and adjacency per node."""
    from backend.cortex.edges import SynapticEdge
    from backend.memory.graph_store import HebbianGraphStore
    from backend.memory.hippocampus import Hippocampus
    h = Hippocampus()
    for s, t, w in EDGES:
        h.add_edge(SynapticEdge(source=s, target=t, weight=w))
    gs = HebbianGraphStore(target)
    gs.save(h)
    h.add_edge(SynapticEdge(source="a", target="c", weight=0.65))
    h.add_edge(SynapticEdge(source="c", target="b", weight=0.7))
    gs.save(h)
    gs.close()
    reopened = HebbianGraphStore(target)                 # the restart
    h2 = Hippocampus()
    reopened.load_into(h2)
    reopened.close()
    return {"nodes": list(h2.graph.nodes), "edges": [(e.source, e.target, e.weight) for e in h2.edges()],
            "outgoing": {n: [t for t, _e in h2.outgoing(n)] for n in h2.graph.nodes}}


def test_g1_the_graph_reloads_with_the_same_order(tmp_path, pg_target):
    a, b = _reloaded(lite(tmp_path / "s" / "kb.db")), _reloaded(pg_target)
    print(f"G1: nodes {a['nodes']}, outgoing {a['outgoing']}")
    assert a == b and a["outgoing"]["a"] == ["c", "b"], (a, b)


def test_g2_companion_the_rejected_order_is_named(tmp_path, pg_target, monkeypatch):
    from backend.store import dialect
    real = dialect.translate

    def sorted_load(sql, has_params=True):
        return real(sql.replace("FROM hebbian_edges ORDER BY rowid", "FROM hebbian_edges ORDER BY source, target"),
                    has_params)

    a = _reloaded(lite(tmp_path / "s" / "kb.db"))
    monkeypatch.setattr(dialect, "translate", sorted_load)
    b = _reloaded(pg_target)
    assert a != b and b["nodes"] == ["a", "b", "c"] and b["outgoing"]["a"] == ["b", "c"], (a, b)


# ---------------------------------------------------------------------------
# M — the sums
# ---------------------------------------------------------------------------

def _deltas() -> dict:
    rng = random.Random(20261110)
    return {bid: [rng.choice((-1.0, 1.0)) * rng.random() * 10.0 ** rng.randint(-12, 2) for _ in range(300)]
            for bid in range(1, 5)}


def _insert(rs, deltas) -> None:
    rows = [(f"e{b}-{i}", 1_700_000_000.0 + i, b, d, 0.5, 0.5 + d, 0.5, "v0", "operator", "local_operator",
             "local", "", 1, "", "", "h") for b, ds in deltas.items() for i, d in enumerate(ds)]
    rs.conn.executemany(
        "INSERT INTO recalibration_events (event_id, ts, belief_id, delta, prominence_before, "
        "prominence_after, stored_weight, envelope_version, actor_id, actor_role, actor_source, "
        "outcome_ref, applied, refusal_rule, refusal_detail, record_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    rs.conn.commit()


def test_m1_the_three_sums_are_equal_on_both_and_equal_fsum(tmp_path, pg_target, monkeypatch):
    from backend.kb.recalibration import RecalibrationStore
    monkeypatch.setenv("NEXUS_RECALIBRATION", "on")
    deltas = _deltas()
    got = {}
    for name, target in (("sqlite", lite(tmp_path / "s" / "kb.db")), ("postgres", pg_target)):
        rs = RecalibrationStore(target)
        try:
            _insert(rs, deltas)
            got[name] = (rs.adjustments(), {b: rs.adjustment_for(b) for b in deltas},
                         {b: rs.cumulative_movement(b) for b in deltas})
        finally:
            rs.close()
    want = ({b: math.fsum(d) for b, d in deltas.items()}, {b: math.fsum(d) for b, d in deltas.items()},
            {b: math.fsum(abs(x) for x in d) for b, d in deltas.items()})
    assert got["sqlite"] == got["postgres"] == want


def test_m2_companion_postgres_own_sum_differs_from_fsum_on_these_rows(tmp_path, pg_target):
    """The SQL D6 removed, run on the server over the same rows: a store
    that summed there would not equal `fsum`, and M1 would name it."""
    import psycopg
    from backend.kb.recalibration import RecalibrationStore
    deltas = _deltas()
    rs = RecalibrationStore(pg_target)
    try:
        _insert(rs, deltas)
    finally:
        rs.close()
    with psycopg.connect(PG_URL, autocommit=True) as c:
        server = {int(b): float(s) for b, s in c.execute(
            f'SELECT belief_id, sum(delta) FROM "{pg_target.schema}".recalibration_events GROUP BY belief_id')}
    exact = {b: math.fsum(d) for b, d in deltas.items()}
    parted = sorted(b for b in exact if server[b] != exact[b])
    print(f"M2: the server's sum differs from fsum for beliefs {parted}")
    assert parted, "the server's sum equalled fsum on every belief: these rows exercise nothing"
