"""verify_packet_k.py §3b — the list L.2c is gated on.

Read-only reporting, so these sentinels guard three things: that the report is
COMPLETE (no truncation, no silent cap), that the sealed-cited alarm actually
fires, and that "cited" has exactly ONE definition in this codebase.

WHY EACH MATTERS
================
**Completeness.** The list exists to be read by eye before anyone archives
anything, and a legitimate concept hides in the part a truncation cuts off.
`test_surfaces_are_printed_in_full` asserts the untruncated text of a surface
longer than the old `[:72]` slice appears in the output. `test_..._no_silent_cap`
asserts every rejected row of the class is listed, not the first N.

**The alarm.** If a rule refuses beliefs that participated in recorded, sealed
decisions, that must surface before confirmation, not after. Both the per-rule
count and the per-row `[SEALED-CITED]` marker are asserted, and the "no
sealed-cited" case is asserted too — an alarm that cannot stay silent is not
an alarm.

**One definition of cited.** `verify_packet_k.py` reports which rows are
sealed-cited; `scripts/archive_consolidation.py` exempts them. If those two
ever computed it differently, an operator would read one number and act on
another. That is the `hebbian`/`hebbian_update` drift H.4 was written about,
so the rule lives once in `backend/memory/lineage.py` and
`test_cited_has_exactly_one_definition` asserts by object identity that both
call it — not that both contain similar code.

**Read-only.** Asserted on the file's bytes, not on an absence of calls.
"""
from __future__ import annotations

import os
from tests import _proc
import sys
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore
from backend.memory.chain import NexusArchiveChain
from backend.memory.lineage import LineageStore, sealed_cited_belief_ids

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "verify_packet_k.py"

# Longer than the 72 characters §3's example list truncates at, so a slice
# would be visible in the output as a missing tail.
LONG_SUBJECT = ("Master of Science in Computer Science and Engineering "
                "at the Institute of Technology")


def _run(kb: Path, *args: str) -> str:
    env = dict(os.environ)
    env.pop("NEXUS_INGEST_GATE", None)          # the gate must be ON to replay
    proc = _proc.run(
        [sys.executable, str(SCRIPT), "--kb", str(kb), *args],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=REPO, env=env, timeout=600)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _seed(tmp_path, *, cited: list[int] | None = None,
          seal: bool = True) -> dict:
    """Pre-gate history: seeded with the gate OFF, because rows written before
    Packet K could not have passed through it."""
    os.environ["NEXUS_INGEST_GATE"] = "off"
    try:
        store = TripleStore(tmp_path / "kb.db")
        ids = {}
        for subj, rel, obj in [
            ("WHO", "is_a", "health organisation"),      # legitimate, refused
            (LONG_SUBJECT, "is_a", "degree"),            # legitimate, refused
            ("i think the answer is", "relates_to", "something"),  # artefact
            ("photosynthesis", "produces", "glucose"),   # clean — must not list
        ]:
            ids[subj] = store.add(subject=subj, relation=rel, object=obj,
                                  weight=0.6, tier=2,
                                  granularity=Granularity.TOPIC,
                                  source="extraction")
        store.add(subject="ganymede", relation="orbits", object="jupiter",
                  weight=0.9, tier=1, granularity=Granularity.TOPIC,
                  source="wikidata")
        store.close()
    finally:
        os.environ.pop("NEXUS_INGEST_GATE", None)

    if cited:
        lineage = LineageStore(tmp_path / "kb.db")
        chain = NexusArchiveChain(tmp_path / "archive.db"); chain.init()
        lineage.record_turn(
            query="q",
            injected=[{"belief_id": b, "weight_at_injection": 0.6,
                       "tier_at_injection": 2, "cited": True} for b in cited],
            contradictions=[], model_id="m", response_text="a",
            prompt_prefix="P")
        if seal:
            un = lineage.unsealed()
            hs = sorted(h for _l, h in un)
            blk = chain.append({"signature": [0.0], "lineage_seal": {
                "count": len(hs), "digest": LineageStore.seal_digest(hs),
                "record_hashes": hs}}, tmp_path / "snap.json")
            lineage.mark_sealed([l for l, _h in un], blk.index)
        lineage.close(); chain.close()
    return ids


def _section(out: str) -> str:
    start = out.index("[3b] REJECTED SURFACES")
    return out[start:out.index("[4] TERM LENGTH")]


# ---------------------------------------------------------------------------
# Completeness — the property truncation would destroy
# ---------------------------------------------------------------------------

def test_surfaces_are_printed_in_full(tmp_path):
    """A legitimate concept hides in the part a truncation cuts off."""
    _seed(tmp_path)
    sec = _section(_run(tmp_path / "kb.db"))
    assert LONG_SUBJECT in sec, (
        "the surface was truncated — this list is read by eye, and the tail "
        "is where a legitimate concept hides")
    assert len(LONG_SUBJECT) > 72, "non-vacuity: the case must exceed the old slice"


def test_every_rejected_row_of_the_class_is_listed_with_no_silent_cap(tmp_path):
    ids = _seed(tmp_path)
    sec = _section(_run(tmp_path / "kb.db"))
    for subj in ("WHO", LONG_SUBJECT, "i think the answer is"):
        assert subj in sec, f"{subj!r} missing — the list is capped or filtered"
    assert "photosynthesis" not in sec, \
        "a row the gate accepts must not appear in a rejection list"
    assert "ganymede" not in sec, \
        "a lenient-class row must not appear in the extraction list"
    assert str(ids["WHO"]) in sec, "row ids must be printed, or nothing is actionable"


def test_the_list_is_grouped_by_rule(tmp_path):
    """71 multi_preposition rows are one decision about a rule, not 71
    decisions about rows."""
    _seed(tmp_path)
    sec = _section(_run(tmp_path / "kb.db"))
    assert "subject:pronoun" in sec
    assert "subject:multi_preposition" in sec
    # the per-rule header carries the count, so a cluster can be sized at a glance
    assert "row(s)," in sec and "sealed-cited" in sec


def test_the_header_says_what_the_list_is_for(tmp_path):
    """A list of rejections is an invitation to act on it. The header has to
    say plainly that it is not one."""
    _seed(tmp_path)
    sec = _section(_run(tmp_path / "kb.db"))
    assert "INPUT TO A DECISION, NOT THE DECISION" in sec
    assert "not a licence to archive" in sec
    assert "NOTHING IS ARCHIVED BY THIS SCRIPT" in sec


# ---------------------------------------------------------------------------
# The alarm
# ---------------------------------------------------------------------------

def test_a_sealed_cited_rejection_is_flagged_per_row_and_per_rule(tmp_path):
    ids = _seed(tmp_path, cited=None)
    ids = _seed(tmp_path, cited=[ids["WHO"]])
    out = _run(tmp_path / "kb.db")
    sec = _section(out)

    assert "[SEALED-CITED]" in sec, "the per-row marker did not fire"
    assert "<<< REVIEW" in sec, "the per-rule alarm did not fire"
    assert "participated in a RECORDED" in sec
    assert "a rule is refusing recorded evidence" in out, \
        "the summary must carry the alarm too — §3b is long and gets scrolled"


def test_the_alarm_stays_silent_when_nothing_is_sealed_cited(tmp_path):
    """An alarm that cannot stay silent is not an alarm."""
    _seed(tmp_path)
    out = _run(tmp_path / "kb.db")
    sec = _section(out)
    assert "[SEALED-CITED]" not in sec
    assert "<<< REVIEW" not in sec
    assert "No rejected row of this class is cited by a sealed record" in sec
    assert "a rule is refusing recorded evidence" not in out


def test_an_unsealed_record_raises_no_alarm(tmp_path):
    """Same doctrine as the L.2b pass: an unsealed record is not yet part of
    the tamper-evident history and confers nothing."""
    ids = _seed(tmp_path)
    _seed(tmp_path, cited=[ids["WHO"]], seal=False)
    sec = _section(_run(tmp_path / "kb.db"))
    assert "[SEALED-CITED]" not in sec


# ---------------------------------------------------------------------------
# One definition of "cited"
# ---------------------------------------------------------------------------

def test_cited_has_exactly_one_definition():
    """Asserted by object identity, not by comparing code.

    `verify_packet_k.py` REPORTS which rows are sealed-cited and
    `archive_consolidation.py` EXEMPTS them. If those disagreed, an operator
    would read one number and act on another — the `hebbian`/`hebbian_update`
    drift H.4 was written about.
    """
    import importlib.util

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    verify = load("vk", REPO / "scripts" / "verify_packet_k.py")
    archive = load("ac", REPO / "scripts" / "archive_consolidation.py")

    assert verify.sealed_cited_belief_ids is sealed_cited_belief_ids
    assert archive.sealed_cited_ids is sealed_cited_belief_ids


@pytest.mark.parametrize("cited_value,expect_exempt", [
    (True, True), (None, True), (False, False),
], ids=["cited_true", "cited_null", "cited_false"])
def test_the_shared_rule_reads_all_three_states(tmp_path, cited_value,
                                                expect_exempt):
    """The same three-state table L.2b tests through its script, asserted
    directly against the shared function so the rule is pinned where it lives."""
    import sqlite3
    store = TripleStore(tmp_path / "kb.db")
    bid = store.add(subject="ganymede", relation="orbits", object="jupiter",
                    weight=0.9, tier=1, granularity=Granularity.TOPIC,
                    source="wikidata")
    lineage = LineageStore(tmp_path / "kb.db")
    chain = NexusArchiveChain(tmp_path / "archive.db"); chain.init()
    item = {"belief_id": bid, "weight_at_injection": 0.9, "tier_at_injection": 1}
    if cited_value is not None:
        item["cited"] = cited_value
    lineage.record_turn(query="q", injected=[item], contradictions=[],
                        model_id="m", response_text="a", prompt_prefix="P")
    un = lineage.unsealed()
    hs = sorted(h for _l, h in un)
    blk = chain.append({"signature": [0.0], "lineage_seal": {
        "count": len(hs), "digest": LineageStore.seal_digest(hs),
        "record_hashes": hs}}, tmp_path / "snap.json")
    lineage.mark_sealed([l for l, _h in un], blk.index)
    store.close(); lineage.close(); chain.close()

    conn = sqlite3.connect(f"file:{tmp_path / 'kb.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    ids, stats = sealed_cited_belief_ids(conn)
    conn.close()

    assert (bid in ids) is expect_exempt
    assert stats["records_sealed"] == 1


def test_a_missing_lineage_table_is_declared_not_swallowed(tmp_path):
    """Law 8 — "no exemptions found" and "could not look" are different
    answers, and reporting them the same way is how a silent zero becomes an
    archived belief."""
    import sqlite3
    db = tmp_path / "bare.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE unrelated (x INTEGER)")
    c.commit(); c.close()

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    ids, stats = sealed_cited_belief_ids(conn)
    conn.close()

    assert ids == set()
    assert "error" in stats and "lineage_records" in stats["error"]


# ---------------------------------------------------------------------------
# Read-only
# ---------------------------------------------------------------------------

def test_the_script_never_writes_to_the_store(tmp_path):
    _seed(tmp_path)
    kb = tmp_path / "kb.db"
    before = kb.read_bytes()
    _run(kb)
    assert kb.read_bytes() == before, "verify_packet_k.py modified the store"
