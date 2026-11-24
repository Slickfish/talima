"""AS-1 — the demo is the fixture (T8).

Twelve claims across six days, three archivals, one unarchival, one
supersede; six as-of queries whose expected sets were written in
tests/fixtures/as_of_fixture.py before any test ran. Prints the six answers
as the route would serve them and asserts them. Tracked.
"""
from __future__ import annotations

import sys

from backend.kb.as_of import (FIXED_SENTENCE, PREDICATE, archival_ledger_from, held_at, ledger_sentence,
                              resolve_instant)
from backend.kb.tiered_store import TripleStore
from tests.fixtures.as_of_fixture import EXPECTED, EXPECTED_SUPERSEDED, build, print_as_of


def test_as_of_demo(tmp_path):
    """The demo is the fixture: six end-of-day instants, six pre-written sets, printed as served."""
    fx = build(tmp_path)
    store = TripleStore(fx.kb_path)
    ledger = archival_ledger_from(store.conn)
    print(f"\nAS-1 demo ({sys.platform})\npredicate: {PREDICATE}\nnote: {FIXED_SENTENCE} {ledger_sentence(ledger['iso'])}")
    for day, expected in EXPECTED.items():
        instant = resolve_instant(day)
        rows, total = held_at(store.conn, instant["ts"], limit=100)
        print_as_of(f"{day} ({instant['interpretation']})", rows, fx)
        got = {fx.numbers[r["belief_id"]] for r in rows}
        assert got == expected and total == len(expected), (day, got, expected)
        sup = {fx.numbers[r["belief_id"]] for r in rows if r["status_at"] == "held_superseded"}
        assert sup == EXPECTED_SUPERSEDED[day], (day, sup)
    store.close()
