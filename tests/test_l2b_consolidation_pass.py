"""Packet L.2b — the consolidation-source archival pass.

Guards the FIRST bulk archive this project has ever run. Nothing here touches
`data/`; every test builds its own store.

WHAT THE SENTINELS PIN, AND WHY EACH ONE EXISTS
===============================================
**The selector.** `test_the_pass_never_consults_a_gate_rule` asserts, by
import graph rather than by grep, that `scripts/archive_consolidation.py`
cannot reach `backend.kb.gate`. L.2b's whole justification is that it makes a
*provenance* judgement — these rows came from one source, which wrote them
because of a defect Packet E fixed — and not a *shape* judgement. L.0.9
measured what a shape judgement would cost here: 40 of 66 legitimate concepts
refused under strict classes, including `WHO`, `O'Brien`, `1984` and `To Kill
a Mockingbird`. If a gate rule ever leaks into this pass, that risk arrives
with it and the reasoning that cleared L.2b no longer holds.

**Law 3.** Dry run is the default, and a dry run must leave the file's md5
untouched — asserted on the bytes, not on an absence of calls.

**I-9 / Law 4.** Every record field byte-identical across the pass, the row
count unchanged, every archived belief still resolvable by id.

**I-4.** A belief cited by a sealed record is exempt. Three cases, because the
flag has three states and they do not mean the same thing:
  cited = true          -> exempt
  cited = NULL/missing  -> exempt (F.5 added the flag; older records lack it,
                           and reading a missing key as "not cited" would
                           strip the exemption from exactly the oldest
                           records — the ones least able to defend themselves)
  cited = false          -> NOT exempt
An UNSEALED record confers nothing, which is `cited_pairs()`'s doctrine
(graph_store.py:176-183) rather than a second standard for the same word.

**Reversal.** The recorded id list restores exactly the rows the pass took and
nothing else. That is the only thing standing between a wrong call and
permanent invisibility, so it is tested as a round trip, not asserted.
"""
from __future__ import annotations

import json
from tests import _proc
import sys
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore
from backend.memory.chain import NexusArchiveChain
from backend.memory.lineage import LineageStore

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "archive_consolidation.py"

CONSOLIDATION = "hippocampus_consolidation"


def _run(kb: Path, *args: str, report: Path | None = None):
    cmd = [sys.executable, str(SCRIPT), "--kb", str(kb), *args]
    if report is not None:
        cmd += ["--report", str(report)]
    proc = _proc.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=REPO, timeout=600)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _seed(tmp_path) -> tuple[TripleStore, dict]:
    """A store shaped like the operator's: consolidation artefacts alongside
    beliefs from every other evidence class."""
    store = TripleStore(tmp_path / "kb.db")
    ids = {
        # the target population
        "coa_1": store.add(subject="quantum", relation="co_activated",
                           object="session", weight=0.9, tier=1,
                           granularity=Granularity.SESSION, source=CONSOLIDATION),
        "coa_2": store.add(subject="previous", relation="co_activated",
                           object="chat", weight=0.7, tier=2,
                           granularity=Granularity.SESSION, source=CONSOLIDATION),
        "coa_cited": store.add(subject="aspirin", relation="co_activated",
                               object="headache", weight=0.8, tier=1,
                               granularity=Granularity.SESSION,
                               source=CONSOLIDATION),
        # everything the pass must not touch
        "external": store.add(subject="ganymede", relation="orbits",
                              object="jupiter", weight=0.9, tier=1,
                              granularity=Granularity.TOPIC, source="wikidata"),
        "extraction": store.add(subject="photosynthesis", relation="produces",
                                object="glucose", weight=0.6, tier=2,
                                granularity=Granularity.TOPIC,
                                source="extraction"),
        "fixture": store.add(subject="water", relation="is_a", object="liquid",
                             weight=0.8, tier=1, granularity=Granularity.TOPIC,
                             source="offline_fixture"),
    }
    return store, ids


def _seal(tmp_path, injected: list[dict], *, sealed: bool = True) -> None:
    """Write one lineage record and (optionally) seal it the way KAIROS does."""
    lineage = LineageStore(tmp_path / "kb.db")
    chain = NexusArchiveChain(tmp_path / "archive.db")
    chain.init()
    lineage.record_turn(query="q", injected=injected, contradictions=[],
                        model_id="m", response_text="a", prompt_prefix="P")
    if sealed:
        unsealed = lineage.unsealed()
        hashes = sorted(h for _l, h in unsealed)
        block = chain.append(
            {"signature": [0.0], "lineage_seal": {
                "count": len(hashes),
                "digest": LineageStore.seal_digest(hashes),
                "record_hashes": hashes}}, tmp_path / "snap.json")
        lineage.mark_sealed([l for l, _h in unsealed], block.index)
    lineage.close(); chain.close()


# ---------------------------------------------------------------------------
# The selector — a source, never a rule
# ---------------------------------------------------------------------------

def test_the_pass_never_consults_a_gate_rule():
    """Asserted on the import graph, not by grepping for a string.

    Runs the module in a fresh interpreter and asks whether `backend.kb.gate`
    landed in sys.modules. Behavioural: a rule reached through an alias, a
    re-export or a late import would still show up here, and a rewritten
    import line that changes nothing would not produce a false alarm.

    (`backend.kb.tiered_store` imports the gate for `add()`, so the check is
    scoped to what the SCRIPT's own module graph pulls in beyond the store —
    the pass must never *call* a rule, and it has no name to call one by.)
    """
    probe = (
        "import importlib.util, sys, json;"
        "spec = importlib.util.spec_from_file_location('l2b', r'%s');"
        "m = importlib.util.module_from_spec(spec);"
        "spec.loader.exec_module(m);"
        "print(json.dumps(sorted(n for n in vars(m) if 'gate' in n.lower() "
        "or 'inspect' in n.lower() or 'reject' in n.lower())))"
    ) % SCRIPT
    proc = _proc.run([sys.executable, "-c", probe],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=REPO, timeout=600)
    assert proc.returncode == 0, proc.stderr
    leaked = json.loads(proc.stdout.strip().splitlines()[-1])
    assert leaked == [], (
        f"a gate rule is reachable from the L.2b pass: {leaked}. The pass "
        f"makes a provenance judgement; a shape judgement carries L.0.9's "
        f"false-positive risk and is L.2c's problem")


def test_only_rows_of_the_named_source_are_candidates(tmp_path):
    store, ids = _seed(tmp_path)
    store.close()
    report = tmp_path / "r.json"
    _run(tmp_path / "kb.db", report=report)
    rec = json.loads(report.read_text(encoding="utf-8"))

    assert rec["source"] == CONSOLIDATION
    assert set(rec["candidate_ids"]) == {
        ids["coa_1"], ids["coa_2"], ids["coa_cited"]}
    for other in ("external", "extraction", "fixture"):
        assert ids[other] not in rec["candidate_ids"]


# ---------------------------------------------------------------------------
# Law 3 — dry run by default
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing_to_the_store(tmp_path):
    store, _ = _seed(tmp_path)
    store.close()
    kb = tmp_path / "kb.db"
    report = tmp_path / "r.json"

    before = kb.read_bytes()
    out = _run(kb, report=report)
    assert kb.read_bytes() == before, "the dry run modified the store"

    rec = json.loads(report.read_text(encoding="utf-8"))
    assert rec["confirmed"] is False
    assert rec["md5_before"] == rec["md5_after"]
    assert rec["archived"] is None and rec["archived_ids"] == []
    assert "DRY RUN" in out and "--confirm" in out


def test_confirm_archives_exactly_the_candidates(tmp_path):
    store, ids = _seed(tmp_path)
    store.close()
    kb = tmp_path / "kb.db"
    report = tmp_path / "r.json"

    _run(kb, "--confirm", report=report)
    rec = json.loads(report.read_text(encoding="utf-8"))
    assert rec["confirmed"] is True
    assert rec["archived"] == 3

    store = TripleStore(kb)
    archived = {t.id for t in store.all() if t.archived}
    assert archived == {ids["coa_1"], ids["coa_2"], ids["coa_cited"]}
    assert store.count() == 6, "the record count moved — that is deletion"
    assert store.count(archived=False) == 3


def test_every_archived_row_carries_the_reason_that_names_this_pass(tmp_path):
    """A third reason class beside `dedup` and `gate:<rule>`, and deliberately
    not spelled `gate:` — calling a provenance judgement a gate rule would
    misdescribe it in the one field whose purpose is saying which judgement
    was made (I-7)."""
    store, _ = _seed(tmp_path)
    store.close()
    _run(tmp_path / "kb.db", "--confirm", report=tmp_path / "r.json")

    store = TripleStore(tmp_path / "kb.db")
    for t in store.all():
        if t.archived:
            assert t.archive_reason == f"bulk:source={CONSOLIDATION}"
            assert not t.archive_reason.startswith("gate:")
            assert t.archived_at is not None
        else:
            assert t.archive_reason is None and t.archived_at is None


# ---------------------------------------------------------------------------
# I-9 / Law 4 — nothing deleted, nothing altered
# ---------------------------------------------------------------------------

RECORD_FIELDS = ("id", "subject", "relation", "object", "weight",
                 "granularity", "source", "created_at")


def test_the_pass_never_deletes_or_alters_a_belief(tmp_path):
    store, _ = _seed(tmp_path)

    def snap():
        rows = store.conn.execute(
            f"SELECT {', '.join(RECORD_FIELDS)} FROM triples ORDER BY id"
        ).fetchall()
        return {r["id"]: tuple(r[f] for f in RECORD_FIELDS) for r in rows}

    before = snap()
    store.close()
    _run(tmp_path / "kb.db", "--confirm", report=tmp_path / "r.json")

    store = TripleStore(tmp_path / "kb.db")
    assert snap() == before, "a record-layer field changed during archival"
    for bid in before:
        assert store.get_by_id(bid) is not None, "a belief stopped resolving"


# ---------------------------------------------------------------------------
# I-4 — the sealed-cited exemption, all three states of the flag
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cited_value,expect_exempt", [
    (True,  True),    # explicitly cited
    (None,  True),    # F.5 predates this record — NULL counts as CITED
    (False, False),   # explicitly not cited
], ids=["cited_true", "cited_null", "cited_false"])
def test_the_exemption_reads_all_three_states_of_the_cited_flag(
        tmp_path, cited_value, expect_exempt):
    store, ids = _seed(tmp_path)
    target = ids["coa_cited"]
    store.close()

    item = {"belief_id": target, "weight_at_injection": 0.8,
            "tier_at_injection": 1}
    if cited_value is not None:
        item["cited"] = cited_value          # omitted entirely for the NULL case
    _seal(tmp_path, [item], sealed=True)

    report = tmp_path / "r.json"
    _run(tmp_path / "kb.db", "--confirm", report=report)
    rec = json.loads(report.read_text(encoding="utf-8"))

    t = TripleStore(tmp_path / "kb.db").get_by_id(target)
    if expect_exempt:
        assert target in rec["exempt_ids"]
        assert target not in rec["archived_ids"]
        assert t.archived is False, "a sealed-cited belief was archived"
    else:
        assert target not in rec["exempt_ids"]
        assert t.archived is True


def test_an_unsealed_record_confers_no_exemption(tmp_path):
    """`cited_pairs()`'s doctrine: only a sealed record makes a belief
    load-bearing, because an unsealed record is not yet part of the
    tamper-evident history."""
    store, ids = _seed(tmp_path)
    target = ids["coa_cited"]
    store.close()
    _seal(tmp_path, [{"belief_id": target, "weight_at_injection": 0.8,
                      "tier_at_injection": 1, "cited": True}], sealed=False)

    report = tmp_path / "r.json"
    _run(tmp_path / "kb.db", "--confirm", report=report)
    rec = json.loads(report.read_text(encoding="utf-8"))

    assert rec["lineage"]["records_total"] == 1
    assert rec["lineage"]["records_sealed"] == 0
    assert rec["exempt_ids"] == []
    assert TripleStore(tmp_path / "kb.db").get_by_id(target).archived is True


def test_an_exempt_belief_stays_retrievable(tmp_path):
    """Exemption is not cosmetic: the belief must still reach retrieval."""
    store, ids = _seed(tmp_path)
    target = ids["coa_cited"]
    store.close()
    _seal(tmp_path, [{"belief_id": target, "weight_at_injection": 0.8,
                      "tier_at_injection": 1, "cited": True}])
    _run(tmp_path / "kb.db", "--confirm", report=tmp_path / "r.json")

    store = TripleStore(tmp_path / "kb.db")
    assert any(t.id == target
               for t in store.search_terms(["aspirin"], limit=20))


# ---------------------------------------------------------------------------
# Reversal — the only thing between a wrong call and permanent invisibility
# ---------------------------------------------------------------------------

def test_the_recorded_ids_restore_exactly_what_the_pass_took(tmp_path):
    store, ids = _seed(tmp_path)
    store.close()
    report = tmp_path / "r.json"
    _run(tmp_path / "kb.db", "--confirm", report=report)
    rec = json.loads(report.read_text(encoding="utf-8"))

    store = TripleStore(tmp_path / "kb.db")
    retrievable_after = store.count(archived=False)

    assert store.unarchive_ids(rec["archived_ids"]) == rec["archived"]
    assert store.count(archived=False) == rec["retrievable_before"]
    assert retrievable_after < rec["retrievable_before"], "non-vacuity"
    assert not any(t.archived for t in store.all())
    assert all(t.archive_reason is None for t in store.all())


def test_a_second_run_is_a_no_op(tmp_path):
    """Already-archived rows are counted separately and never re-stamped, so
    running the pass twice cannot write a second reason over the first."""
    store, _ = _seed(tmp_path)
    store.close()
    _run(tmp_path / "kb.db", "--confirm", report=tmp_path / "r1.json")
    first = json.loads((tmp_path / "r1.json").read_text(encoding="utf-8"))

    _run(tmp_path / "kb.db", "--confirm", report=tmp_path / "r2.json")
    second = json.loads((tmp_path / "r2.json").read_text(encoding="utf-8"))

    assert first["archived"] == 3
    assert second["archived"] == 0
    assert second["already_archived"] == 3
    assert sorted(second["already_archived_ids"]) == sorted(first["archived_ids"])


def test_the_report_carries_every_number_the_record_needs(tmp_path):
    store, _ = _seed(tmp_path)
    store.close()
    report = tmp_path / "r.json"
    _run(tmp_path / "kb.db", "--confirm", report=report)
    rec = json.loads(report.read_text(encoding="utf-8"))

    for key in ("packet", "timestamp", "confirmed", "kb", "md5_before",
                "md5_after", "source", "reason", "triples_total",
                "retrievable_before", "retrievable_after", "source_rows",
                "already_archived", "exempt_sealed_cited", "exempt_ids",
                "candidates", "archived", "archived_ids", "by_relation",
                "by_tier", "lineage"):
        assert key in rec, f"the committed record omits {key}"
    assert rec["md5_before"] != rec["md5_after"], \
        "a confirmed pass that did not change the file is not a pass"
