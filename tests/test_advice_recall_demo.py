"""AR (advice recall) — the demo, as a tracked test.

T11: the demo is the fixture; if the demo drifts from the test, one of them
is lying. Run it alone to see the seven rows:

    python -m pytest -q tests/test_advice_recall_demo.py -s
"""
from __future__ import annotations

from backend.memory.lineage import LineageStore
from tests.fixtures.advice_recall_fixture import (EXPECTED_RECALL_COUNT, REV3, REV4, build,
                                                  print_recall)


def test_demo_recall(tmp_path, monkeypatch):
    """The demo is the fixture; if the demo drifts from the test, one of them is lying."""
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    fx = build(tmp_path)
    lineage = LineageStore(fx.kb_path)
    rows, total = lineage.records_injecting(fx.rev3)
    print(f"\nrevision 3: {' | '.join(REV3)}\nrevision 4: {' | '.join(REV4)}\n"
          f"sealed answers that rested on revision 3 (injected, not cited):")
    print_recall(rows)
    assert total == len(rows) == EXPECTED_RECALL_COUNT == 7
    lineage.close()
