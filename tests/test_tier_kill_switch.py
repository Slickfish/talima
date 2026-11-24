"""Packet F.2 — a single row must not be able to disable the substrate.

Before F.2 the dedup-confirm endpoint wrote `tier = 4` by direct SQL, past
`add()`'s own validation (which rejects tier 4 as out of range). All three
tier tables are keyed 0-3, so that one row raised `KeyError: 4` out of:

  * `rebalance()`      -> out of `run_one_cycle` -> out of `run()`, which
                          awaits it outside any handler, so the KAIROS daemon
                          task died with an exception nobody retrieved;
  * `current_weight()` -> out of `TripleRetriever.retrieve()`, i.e. the chat
                          turn failed too.

and the partial UPDATEs from the aborted pass were left in an open
transaction on the shared connection, to be committed later by an unrelated
`add()` under that operation's name.

These sentinels pin all four properties. They are written against the shape
of the failure, not the shape of the fix, so a future change that reintroduces
an out-of-band tier value fails here rather than in production.
"""
import asyncio
import logging
import sqlite3
from pathlib import Path

import pytest

from backend.cortex.hebbian import HebbianUpdater
from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.schema import migrate
from backend.kb.tiered_store import TripleStore
from backend.memory.hippocampus import Hippocampus
from backend.memory.kairos import KairosProcess
from backend.memory.neocortex import Neocortex
from backend.memory.procedural import ProceduralMemory

T0 = 1_700_000_000.0


def _store(tmp_path: Path) -> TripleStore:
    return TripleStore(tmp_path / "kb.db")


def _add(store, subject, *, tier=1, weight=0.9, relation="r", object="oo",
         source="x"):
    return store.add(subject=subject, relation=relation, object=object,
                     weight=weight, tier=tier,
                     granularity=Granularity.TOPIC, source=source)


def _poison(store, triple_id, tier=4):
    """Write a tier the tables do not cover, exactly as pre-F.2 code did."""
    store.conn.execute("UPDATE triples SET tier = ? WHERE id = ?",
                       (tier, triple_id))
    store.conn.commit()


# ---------------------------------------------------------------------------
# The rebalance path
# ---------------------------------------------------------------------------

def test_tier4_row_cannot_disable_rebalance(tmp_path, caplog):
    """One unreadable row must not stop every other row from moving."""
    s = _store(tmp_path)
    # Q.2 — `source="wikidata"` is now load-bearing for the fixture's INTENT,
    # which is "rows that move". The default `source="x"` is unregistered, so
    # classify() reads it as `extraction` and the arrival discount holds it at
    # tier 1: correct behaviour, but it leaves nothing to promote and the test
    # would pass vacuously while asserting 0 == 0.
    movers = [_add(s, f"s{i}", source="wikidata") for i in range(5)]
    bad = _add(s, "poisoned", tier=3, weight=0.3)
    _poison(s, bad)

    with caplog.at_level(logging.WARNING, logger="nexus.kb.store"):
        result = s.rebalance(T0)                        # must not raise

    assert result["promoted"] == 5
    assert [s.get_by_id(i).tier for i in movers] == [0] * 5
    # I-6 — the skip is declared, not inferred from a silence.
    assert result["skipped"] == 1
    assert result["skipped_reasons"] == {"unknown_tier_4": 1}
    assert any("outside the" in r.getMessage() for r in caplog.records)
    # Law 4 — the unreadable row is left exactly where it was, not deleted.
    assert s.get_by_id(bad) is not None
    assert s.get_by_id(bad).tier == 4
    s.close()


def test_archived_rows_are_skipped_and_declared(tmp_path):
    """A superseded belief has no prominence to rebalance — and says so."""
    s = _store(tmp_path)
    # Q.2 — same reason as above: the assertion is that the KEPT row moves
    # while the archived one is skipped, so the kept row must be of a class
    # that promotes.
    keep = _add(s, "keeper", source="wikidata")
    gone = _add(s, "superseded", source="wikidata")
    s.archive("superseded", "r", "oo")

    result = s.rebalance(T0)
    assert result["promoted"] == 1
    assert result["skipped_reasons"] == {"archived": 1}
    assert s.get_by_id(keep).tier == 0
    assert s.get_by_id(gone).tier == 1          # untouched, still recoverable
    s.close()


def test_failed_rebalance_does_not_leak_a_partial_pass(tmp_path):
    """The measured hazard: partial UPDATEs left in an open transaction were
    committed later by an unrelated `add()`, under that operation's name.

    The pass is atomic now — a failure rolls back and raises. A half-applied
    pass is a store state no policy produced, and committing it deliberately
    would be the same corruption as committing it by accident.
    """
    s = _store(tmp_path)
    movers = [_add(s, f"s{i}") for i in range(5)]

    real_all = s.all

    def _explode_partway():
        rows = real_all()
        # Enough rows to have issued UPDATEs, then fail.
        class _Boom(list):
            def __iter__(self):
                for i, r in enumerate(rows):
                    if i == 3:
                        raise RuntimeError("db went away mid-pass")
                    yield r
        return _Boom()

    s.all = _explode_partway
    with pytest.raises(RuntimeError):
        s.rebalance(T0)
    s.all = real_all

    _add(s, "unrelated")                       # the write that used to commit it
    fresh = TripleStore(tmp_path / "kb.db")
    assert [fresh.get_by_id(i).tier for i in movers] == [1] * 5, (
        "a partial rebalance pass was committed by an unrelated write")
    fresh.close(); s.close()


# ---------------------------------------------------------------------------
# The retrieval path — the same row also broke the chat turn
# ---------------------------------------------------------------------------

def test_unreadable_tier_does_not_break_retrieval(tmp_path):
    s = _store(tmp_path)
    good = _add(s, "python", relation="is_a", object="language", weight=0.8, tier=2)
    bad = _add(s, "python_legacy", relation="is_a", object="language",
               weight=0.8, tier=2)
    _poison(s, bad)

    hits = TripleRetriever(s).retrieve("tell me about python")   # must not raise
    # The contract is "the turn survives", NOT "the row is hidden": an
    # unreadable tier is a data defect, not an archival judgement, and
    # silently dropping the belief would be a worse answer than ranking it.
    # (Exclusion is what `archived` is for — see the dedup tests.)
    assert {h.triple.id for h in hits} == {good, bad}
    assert hits[-1].triple.id == bad, "unreadable tier should rank last"
    # It decays like the coldest known tier rather than being read as immune
    # to time — an unknown tier must never mean "permanent".
    row = s.get_by_id(bad)
    assert row.current_weight(row.last_fired + 100 * 86400) == 0.0
    s.close()


# ---------------------------------------------------------------------------
# The daemon path — the largest blast radius
# ---------------------------------------------------------------------------

def test_kairos_cycle_survives_a_bad_tier_value(tmp_path, caplog):
    """C10 one level up: the cycle must declare the loss and keep running."""
    neo = Neocortex(tmp_path / "kb.db")
    bad = _add(neo.store, "poisoned", tier=3, weight=0.3)
    _poison(neo.store, bad)

    h = Hippocampus()
    h.ingest_triple("alpha", "is_a", "thing", weight=0.6)
    k = KairosProcess(h, neo, HebbianUpdater(), ProceduralMemory(),
                      fingerprinter=None, archive_chain=None,
                      snapshots_dir=tmp_path / "snapshots")

    res = asyncio.run(k.run_one_cycle())        # must not raise
    assert res.skipped is False                 # F.2 also stops it raising at all
    assert res.cycle_error == ""
    assert res.rebalanced["skipped_reasons"] == {"unknown_tier_4": 1}
    neo.close()


def test_kairos_daemon_survives_a_failing_cycle(tmp_path, caplog):
    """The property that actually mattered: the task must still be alive.

    Pre-F.2, `run()` awaited `run_one_cycle()` outside any handler, so one
    raise ended the loop and the exception was never retrieved — consolidation,
    decay, procedural promotion, dedup scanning, snapshotting and lineage
    sealing all stopped silently for the life of the process.
    """
    neo = Neocortex(tmp_path / "kb.db")
    h = Hippocampus()
    h.ingest_triple("alpha", "is_a", "thing", weight=0.6)
    k = KairosProcess(h, neo, HebbianUpdater(), ProceduralMemory(),
                      fingerprinter=None, archive_chain=None,
                      snapshots_dir=tmp_path / "snapshots",
                      poll_interval=0.01)

    def _boom():
        raise RuntimeError("rebalance exploded")
    neo.store.rebalance = _boom

    async def main():
        with caplog.at_level(logging.ERROR, logger="nexus.kairos"):
            res = await k.run_one_cycle()
        assert res.skipped is True
        assert res.skipped_reason == "error"
        assert "RuntimeError" in res.cycle_error       # I-6: declared
        assert any(r.levelno == logging.ERROR for r in caplog.records)

        k.start()
        await asyncio.sleep(0.1)
        assert k._task.done() is False, "daemon died on a failing cycle"
        await k.stop()

    asyncio.run(main())
    neo.close()


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def test_legacy_tier4_rows_migrate_without_deletion(tmp_path):
    """Existing stores carry tier-4 rows. They are re-tiered and flagged —
    never removed (Law 4) — and the migration declares what it did."""
    db = tmp_path / "kb.db"
    s = _store(tmp_path)
    legacy = [_add(s, f"legacy{i}", tier=3, weight=0.3) for i in range(3)]
    keep = _add(s, "keeper", tier=2)
    for i in legacy:
        _poison(s, i)
    before = s.count()
    s.close()

    from backend.store import connect as store_connect
    from tests.conftest import store_target_for
    conn = store_connect(store_target_for(db))
    summary = migrate(conn)
    conn.close()
    assert summary["legacy_tier4_migrated"] == 3

    s2 = TripleStore(db)
    assert s2.count() == before                      # nothing deleted
    for i in legacy:
        t = s2.get_by_id(i)
        assert t.archived is True and t.tier == 3
    assert s2.get_by_id(keep).archived is False
    # Re-runnable: a second pass migrates nothing and does not raise.
    conn2 = store_connect(store_target_for(db))
    assert migrate(conn2)["legacy_tier4_migrated"] == 0
    conn2.close(); s2.close()


def test_migration_is_rerunnable_on_a_fresh_store(tmp_path):
    s = _store(tmp_path)
    s.close()
    s2 = TripleStore(tmp_path / "kb.db")             # must not raise
    assert s2.count() == 0
    s2.close()
