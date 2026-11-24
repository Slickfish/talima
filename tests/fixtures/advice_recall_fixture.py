"""AR (advice recall) — the fixture that is also the demo.

A synthetic controlled document with a revision 3 and a revision 4 that
differ in one isolation step: rev3 centrifuges at 3000g for 10 minutes,
rev4 for 12. Twelve SEALED lineage records across three work units (WU-A,
WU-B, WU-C) and two accountable identities (alice, bob). Seven records
injected the rev3 claim; four of those seven cited it. The other five
injected rev4, a third claim, or nothing. Timestamps are fixed. Nothing
runs the model.

Built through the REAL writers — `TripleStore.add`, `LineageStore.record_turn`,
the archive chain's governance seal — so the records are genuinely sealed.
No row is inserted into the lineage table directly (the spec forbids it,
and a row the writer did not write would not carry the writer's index).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from backend.kb.granularity import Granularity
from backend.kb.recalibration import RecalibrationStore
from backend.kb.tiered_store import TripleStore
from backend.memory.chain import NexusArchiveChain
from backend.memory.lineage import LineageStore

REV3 = ("sample isolation", "requires", "centrifuge at 3000g for 10 minutes")
REV4 = ("sample isolation", "requires", "centrifuge at 3000g for 12 minutes")
OTHER = ("sample isolation", "precedes", "lysis")
SOURCE = "human_assertion"

T0 = 1_760_000_000.0            # fixed; each record is one hour after the last
HOUR = 3600.0

# (index, work unit, accountable, injected: (claim, weight, cited) …)
# Seven records inject REV3 (0–6); four of them cite it (0, 1, 3, 5).
PLAN = (
    (0,  "WU-A", "alice", (("rev3", 0.71, True),)),
    (1,  "WU-A", "alice", (("rev3", 0.70, True), ("other", 0.40, False))),
    (2,  "WU-B", "bob",   (("rev3", 0.69, False),)),
    (3,  "WU-B", "bob",   (("rev3", 0.68, True),)),
    (4,  "WU-C", "alice", (("rev3", 0.67, False), ("other", 0.41, True))),
    (5,  "WU-C", "bob",   (("rev3", 0.66, True),)),
    (6,  "WU-A", "bob",   (("rev3", 0.65, False),)),
    (7,  "WU-A", "alice", (("rev4", 0.72, True),)),
    (8,  "WU-B", "alice", (("rev4", 0.71, False),)),
    (9,  "WU-C", "bob",   (("rev4", 0.70, True), ("other", 0.42, False))),
    (10, "WU-B", "alice", (("other", 0.43, True),)),
    (11, "WU-C", "bob",   ()),
)
EXPECTED_RECALL_COUNT = 7        # rev3 injected — not the 4 that cited it
EXPECTED_CITED_COUNT = 4


@dataclass(frozen=True)
class RecallFixture:
    kb_path: Path
    archive_path: Path
    rev3: int
    rev4: int
    other: int
    lineage_ids: tuple            # index -> lineage_id, in PLAN order
    expected: tuple               # the seven rows the recall of rev3 must return, oldest first
    sealed_block: int


def build(tmp: Path) -> RecallFixture:
    kb, archive = Path(tmp) / "kb.db", Path(tmp) / "archive.db"
    store = TripleStore(kb)
    lineage = LineageStore(kb)
    RecalibrationStore(kb).close()            # the governance tables exist before any act
    chain = NexusArchiveChain(archive)
    ids = {}
    for name, (s, r, o) in (("rev3", REV3), ("rev4", REV4), ("other", OTHER)):
        ids[name] = store.add(subject=s, relation=r, object=o, weight=0.7, tier=1,
                              granularity=Granularity.TOPIC, source=SOURCE)
    lineage_ids, expected = [], []
    for i, unit, who, injected in PLAN:
        ts = T0 + i * HOUR
        items = [{"belief_id": ids[name], "weight_at_injection": w, "tier_at_injection": 1, "cited": c}
                 for name, w, c in injected]
        # T5's planted input: record 3's actor fields CLAIM an IdP-verified
        # source. The recall must still label the identity `asserted`.
        asserted_by = ({"id": who, "role": "person", "source": "idp"} if i == 3 else None)
        with mock.patch("backend.memory.lineage.time.time", return_value=ts):
            rec = lineage.record_turn(
                query=f"question {i} about the isolation step", injected=items, contradictions=[],
                model_id="fixture-model", response_text=f"answer {i}", prompt_prefix="P",
                work_unit_id=unit, accountable_identity=who,
                boundary_asserted=("close" if asserted_by else None), boundary_asserted_by=asserted_by)
        lineage_ids.append(rec.lineage_id)
        for name, w, c in injected:
            if name == "rev3":
                expected.append({"lineage_id": rec.lineage_id, "ts": ts, "work_unit_id": unit,
                                 "accountable": who, "weight_at_injection": w, "cited": c})
    hashes = [r.record_hash for r in lineage.recent(limit=100)]
    block = chain.append({"kind": "governance", "lineage_seal": {
        "count": len(hashes), "digest": LineageStore.seal_digest(hashes), "record_hashes": hashes}},
        "", kind="governance")
    lineage.mark_sealed(lineage_ids, block.index)
    lineage.close(); store.close(); chain.close()
    return RecallFixture(kb_path=kb, archive_path=archive, rev3=ids["rev3"], rev4=ids["rev4"],
                         other=ids["other"], lineage_ids=tuple(lineage_ids), expected=tuple(expected),
                         sealed_block=block.index)


def print_recall(rows: list[dict]) -> None:
    """The demo's rendering: one line per sealed answer that rested on the claim."""
    for r in rows:
        print(f"  {r['lineage_id'][:8]}  {r['work_unit_id']:<5} {r['accountable']:<6} "
              f"({r['identity_label']})  ts={r['ts']:.0f}  w={r['weight_at_injection']}  "
              f"cited={r['cited']}  sealed_block={r['sealed_block']}")
    print(f"recall: {len(rows)}")
