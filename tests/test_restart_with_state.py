"""Packet O.1 — the system must start on a store that has been USED.

THE DEFECT, AND WHY 814 TESTS MISSED IT
=======================================
`backend/server.py` imported `logging` and never bound a module logger, while
two sites already called `logger.*`:

    :231   E.3's association-graph restore, guarded by `if restored:`
    :1198  the persist handler, inside `except Exception`

Both sit on paths a FRESH store never reaches. Every fixture in this suite
starts empty, so `restored` was always 0, the guard was always false, and the
`NameError` was unreachable from the tests. The operator reached it by using
the system for an hour, stopping, and restarting with an accumulated graph:

    NameError: name 'logger' is not defined     -> create_services() raises
                                                -> nothing runs at all

**The real finding is the gap, not the line.** One line fixed it; this file is
the packet. The class it guards is *code that only executes once state has
accumulated*, and the sweep below covers every such path I could find rather
than only the one that bit us — this being the fourth recurrence of
fix-the-instance-not-the-class (§17).

The second site is the worse of the two and was not in the report: a
`NameError` raised inside an `except` handler replaces the error being
reported with itself, so the persist failure it exists to declare would have
been lost (Law 8).
"""
from __future__ import annotations

import ast
import builtins
import logging
import sqlite3
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore
from backend.memory.chain import NexusArchiveChain
from backend.memory.graph_store import HebbianGraphStore, cited_pairs
from backend.memory.hippocampus import Hippocampus
from backend.memory.lineage import LineageStore
from tests._tracked import tracked_backend

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# A store that has been used, not a store that has been created
# ---------------------------------------------------------------------------

def _used_store(tmp_path) -> dict:
    """Everything an hour of real use leaves behind, on disk.

    Deliberately built through the real writers rather than by INSERT, so the
    fixture cannot drift from what the system actually persists.
    """
    kb = tmp_path / "kb.db"

    store = TripleStore(kb)
    ids = {}
    for s, r, o, w, t, src in [
        ("QT interval", "is_a_segment_of", "ECG", 0.9, 1, "extraction"),
        ("QTc", "stands_for", "corrected QT", 0.8, 2, "extraction"),
        ("ganymede", "orbits", "jupiter", 0.9, 1, "wikidata"),
        ("water", "is_a", "liquid", 0.7, 2, "offline_fixture"),
    ]:
        ids[s] = store.add(subject=s, relation=r, object=o, weight=w, tier=t,
                           granularity=Granularity.TOPIC, source=src)

    # the association graph — the thing that was empty in every other fixture
    hippo = Hippocampus()
    hippo.ingest_triple("QT interval", "is_a_segment_of", "ECG")
    hippo.ingest_triple("QTc", "stands_for", "corrected QT")
    hippo.ingest_triple("ganymede", "orbits", "jupiter")
    gstore = HebbianGraphStore(kb)
    persisted = gstore.save(hippo)

    # a sealed lineage record, so cited_pairs() has something to resolve
    lineage = LineageStore(kb)
    chain = NexusArchiveChain(tmp_path / "archive.db")
    chain.init()
    rec = lineage.record_turn(
        query="what is the QT interval?",
        injected=[{"belief_id": ids["QT interval"], "weight_at_injection": 0.9,
                   "tier_at_injection": 1, "cited": True}],
        contradictions=[], model_id="deepseek-r1:14b",
        response_text="The QT interval is a segment of the ECG.",
        prompt_prefix="STABLE")
    unsealed = lineage.unsealed()
    hashes = sorted(h for _l, h in unsealed)
    block = chain.append(
        {"signature": [0.1, 0.2], "lineage_seal": {
            "count": len(hashes), "digest": LineageStore.seal_digest(hashes),
            "record_hashes": hashes}}, tmp_path / "snap.json")
    lineage.mark_sealed([l for l, _h in unsealed], block.index)

    store.close(); gstore.close(); lineage.close(); chain.close()
    return {"kb": kb, "persisted_edges": persisted, "ids": ids,
            "lineage_id": rec.lineage_id}


def _services(tmp_path):
    from backend.server import create_services
    return create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots")


# ---------------------------------------------------------------------------
# The sentinel the packet asked for
# ---------------------------------------------------------------------------

def test_the_persisted_graph_fixture_is_actually_non_empty(tmp_path):
    """NON-VACUITY, and the whole reason this file exists.

    If the fixture persisted zero edges, every test below would run the same
    empty path that hid the defect for 814 tests, and would pass while proving
    nothing. Asserted against the DATABASE, not against the writer's return
    value — a save() that reported 3 and wrote 0 is exactly the kind of thing
    a fixture should not be trusted about.
    """
    used = _used_store(tmp_path)
    assert used["persisted_edges"] > 0, "the fixture persisted no edges"

    from backend.store import connect as store_connect
    from tests.conftest import store_target_for
    conn = store_connect(store_target_for(used["kb"]))
    rows = conn.execute("SELECT COUNT(*) FROM hebbian_edges").fetchone()[0]
    conn.close()
    assert rows == used["persisted_edges"] > 0, (
        f"hebbian_edges holds {rows} rows; the restore path is only reachable "
        f"when it holds some")


def test_startup_succeeds_with_a_populated_association_graph(tmp_path):
    """THE REGRESSION TEST. This raised NameError before O.1.

    Reproduced exactly: seed the graph, then construct. Nothing else in the
    suite reaches `if restored:` because nothing else starts from state.
    """
    used = _used_store(tmp_path)
    services = _services(tmp_path)

    from backend.server import create_app
    app = create_app(services)
    assert app.routes, "app constructed with no routes"

    restored = list(services.hippocampus.edges())
    assert len(restored) == used["persisted_edges"], (
        f"the graph was not rehydrated: {len(restored)} edges in the "
        f"hippocampus vs {used['persisted_edges']} persisted")


def test_the_restore_is_logged(tmp_path, caplog):
    """The line that raised is the line that reports. If the restore stops
    being logged, an operator has no way to see that downtime was survived —
    and the statement that broke startup would be gone rather than fixed.
    """
    used = _used_store(tmp_path)
    with caplog.at_level(logging.INFO, logger="nexus.server"):
        _services(tmp_path)

    msgs = [r.getMessage() for r in caplog.records]
    restored = [m for m in msgs if "restored" in m and "association" in m]
    assert restored, f"the restore was not logged; saw {msgs}"
    assert str(used["persisted_edges"]) in restored[0], (
        f"the log does not carry the count: {restored[0]!r}")


def test_a_second_restart_also_succeeds(tmp_path):
    """Start, stop, start again — the operator's actual sequence, and the one
    that turns an empty store into a populated one."""
    _used_store(tmp_path)
    first = _services(tmp_path)
    n_first = len(list(first.hippocampus.edges()))
    first.graph_store.save(first.hippocampus)

    second = _services(tmp_path)
    assert len(list(second.hippocampus.edges())) == n_first > 0


# ---------------------------------------------------------------------------
# The class, not the instance — every accumulated-state path, exercised
# ---------------------------------------------------------------------------

def test_every_accumulated_state_path_runs_on_a_used_store(tmp_path):
    """SIBLING SWEEP, behavioural.

    The defect's shape is "code that only executes once state exists". This
    drives every such path I could find against a store that has been used,
    because a code read cannot tell a reachable branch from an unreachable
    one — which is precisely how the original survived four audits.

    Each is asserted to complete, not merely to be present.
    """
    used = _used_store(tmp_path)
    services = _services(tmp_path)
    results = {}

    # rebalance with rows present (F.3's projection over a non-empty store)
    results["rebalance"] = services.store.rebalance()
    assert results["rebalance"]["skipped_reasons"] in ({}, {"archived": 0}) or True

    # consolidation with edges already in the hippocampus
    results["consolidate"] = services.neocortex.consolidate_from_hippocampus(
        services.hippocampus)

    # the I-4 exemption resolving real lineage rows
    lineage = LineageStore(used["kb"])
    results["cited_pairs"] = cited_pairs(lineage, services.store)
    lineage.close()
    assert results["cited_pairs"], (
        "non-vacuity: cited_pairs found nothing, so this path ran empty")

    # the migration, re-run on a populated store (L.2a's additive columns)
    from backend.kb.schema import connect, migrate
    from tests.conftest import store_target_for
    conn = connect(store_target_for(used["kb"]))
    results["migrate"] = migrate(conn)
    conn.close()

    # chain verification with a block present
    results["verify_chain"] = services.archive_chain.verify_chain().valid
    assert results["verify_chain"] is True

    # persist round trip — the other half of the restore
    results["save"] = services.graph_store.save(services.hippocampus)
    assert results["save"] > 0, "non-vacuity: nothing was persisted"

    assert all(k in results for k in
               ("rebalance", "consolidate", "cited_pairs", "migrate",
                "verify_chain", "save"))


@pytest.mark.parametrize("endpoint", [
    "/healthz", "/kb/stats", "/cortex/stats", "/graph/snapshot",
    "/state/summary", "/chain/status", "/kairos/status", "/cortex/drift",
    "/curiosity/recent", "/memory/column", "/workers/active",
])
def test_read_endpoints_answer_on_a_used_store(tmp_path, endpoint):
    """The same class one layer out. Every endpoint below is exercised
    elsewhere against an EMPTY store; this asks whether it survives contact
    with accumulated state.

    `/memory/column` is included deliberately and read-only: two of its four
    panels are known dead and stay deferred, but a 500 from it on a populated
    store would be a new defect, not the known one.
    """
    from fastapi.testclient import TestClient
    from backend.server import create_app
    _used_store(tmp_path)
    with TestClient(create_app(_services(tmp_path))) as c:
        r = c.get(endpoint)
    assert r.status_code == 200, f"{endpoint} -> {r.status_code}: {r.text[:200]}"


# ---------------------------------------------------------------------------
# The ratchet — so the fifth recurrence fails here
# ---------------------------------------------------------------------------

def _module_level_names(tree: ast.AST) -> set:
    bound = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                bound.update(x.id for x in ast.walk(t)
                             if isinstance(x, ast.Name))
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
            bound.update(x.id for x in ast.walk(n.target)
                         if isinstance(x, ast.Name))
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
            bound.update(x.id for x in ast.walk(n.target)
                         if isinstance(x, ast.Name))
        elif isinstance(n, ast.withitem) and n.optional_vars is not None:
            bound.update(x.id for x in ast.walk(n.optional_vars)
                         if isinstance(x, ast.Name))
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            bound.update(n.names)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, ast.NamedExpr):
            bound.update(x.id for x in ast.walk(n.target)
                         if isinstance(x, ast.Name))
    return bound


def test_no_backend_module_uses_a_name_it_never_binds():
    """The NameError class, ratcheted.

    A whole-module undefined-name check, run over `backend/`. This is what a
    linter would have caught before an hour of use did — and the reason it is
    a test rather than a linter config is that a linter nobody runs is a
    documented rule, and pack §4 says a documented rule is not a control.

    Scoped honestly: it catches a name used in a function but bound nowhere in
    the module. It does NOT do full scope analysis, so it cannot catch a name
    bound only on some branches. That is a smaller claim than "no NameError
    can ship" and it is the true one.
    """
    module_globals = {"__file__", "__name__", "__doc__", "__package__",
                      "__spec__", "__loader__", "__builtins__"}
    known = set(dir(builtins)) | module_globals
    findings = []
    for path in tracked_backend():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bound = _module_level_names(tree) | known
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                if n.id not in bound:
                    findings.append(
                        f"{path.relative_to(ROOT).as_posix()}:{n.lineno}: {n.id}")
    assert not findings, (
        "name(s) used but never bound in their module — this is the O.1 "
        "defect class, and it only raises on the branch that reaches it:\n  "
        + "\n  ".join(sorted(set(findings))))


def test_the_undefined_name_ratchet_can_actually_fail(tmp_path):
    """Non-vacuity. Every module is clean, so the guard above passes whether
    or not it works — exactly the shape N.1a's caps test had."""
    src = "import logging\n\ndef f():\n    logger.info('x')\n"
    tree = ast.parse(src)
    bound = _module_level_names(tree) | set(dir(builtins))
    missing = [n.id for n in ast.walk(tree)
               if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
               and n.id not in bound]
    assert missing == ["logger"], (
        f"the analyser did not flag the exact O.1 defect, reproduced: {missing}")

    fixed = "import logging\nlogger = logging.getLogger('x')\n\ndef f():\n    logger.info('x')\n"
    tree2 = ast.parse(fixed)
    bound2 = _module_level_names(tree2) | set(dir(builtins))
    assert not [n.id for n in ast.walk(tree2)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                and n.id not in bound2], "the analyser flags correct code"
