"""AS-1 — the as-of fixture that is also the demo.

Twelve claims admitted across six UTC days D1..D6 (10:00 UTC each, except
claim 7, admitted at 23:50 on D3 — the bare-date boundary case), three
archived (claims 1, 3 and 5 at 11:00 on D2, D3 and D4), one of those
unarchived (claim 3 at 11:00 on D5), one superseded (claim 2 at 12:00 on
D4). Built through the REAL writers — `TripleStore.add`, `archive_ids`,
`unarchive_ids`, `RecalibrationStore.record_outcome` with the sealed
supersede selector set — with `time.time` fixed per act. Nothing runs the
model; no row is inserted directly.

THE EXPECTED SETS are written here, before any test runs, as literals.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from backend.kb.granularity import Granularity
from backend.kb.recalibration import RecalibrationStore
from backend.kb.tiered_store import TripleStore

SOURCE = "human_assertion"
DAY1 = datetime(2026, 3, 10, tzinfo=timezone.utc)
DATES = {f"D{i}": (DAY1 + timedelta(days=i - 1)).strftime("%Y-%m-%d") for i in range(0, 7)}  # D0 .. D6
D0, D1, D2, D3, D4, D5, D6 = (DATES[f"D{i}"] for i in range(7))


def at(day: str, hh: int, mm: int = 0) -> float:
    return datetime.strptime(day, "%Y-%m-%d").replace(hour=hh, minute=mm, tzinfo=timezone.utc).timestamp()


# claim number -> (admission day, hour, minute)
ADMISSIONS = {1: (D1, 10, 0), 2: (D1, 10, 0), 3: (D2, 10, 0), 4: (D2, 10, 0),
              5: (D3, 10, 0), 6: (D3, 10, 0), 7: (D3, 23, 50),
              8: (D4, 10, 0), 9: (D4, 10, 0), 10: (D5, 10, 0), 11: (D5, 10, 0), 12: (D6, 10, 0)}
ARCHIVALS = {1: (D2, 11), 3: (D3, 11), 5: (D4, 11)}
UNARCHIVALS = {3: (D5, 11)}
SUPERSEDES = {2: (D4, 12)}

# AS-1b — the route refuses a bare date, so every query is an INSTANT WITH
# AN OFFSET: the end of each day, stated in UTC by the test, not read into
# the request by the server.
END = {d: f"{d}T23:59:59.999999+00:00" for d in (D0, D1, D2, D3, D4, D5, D6)}


def end_of(day: str) -> str:
    """The instant the tests mean by 'as of <day>': that day's last microsecond, UTC."""
    return END[day]


# The six as-of answers (end-of-day instants, UTC), by claim number.
EXPECTED = {
    END[D1]: {1, 2},
    END[D2]: {2, 3, 4},
    END[D3]: {2, 4, 5, 6, 7},
    END[D4]: {2, 4, 6, 7, 8, 9},
    END[D5]: {2, 3, 4, 6, 7, 8, 9, 10, 11},
    END[D6]: {2, 3, 4, 6, 7, 8, 9, 10, 11, 12},
}
EXPECTED_SUPERSEDED = {END[D1]: set(), END[D2]: set(), END[D3]: set(), END[D4]: {2}, END[D5]: {2}, END[D6]: {2}}
# The boundary case: claim 7 (23:50 on D3) is held as of the end of D3 and not as of D3T23:00+00:00.
BOUNDARY_INSTANT = f"{D3}T23:00:00+00:00"
EXPECTED_AT_BOUNDARY = {2, 4, 5, 6}
# The earliest archival event on the fixture (claim 1 at 11:00 on D2).
EARLIEST_EVENT = (D2, 11)


@dataclass(frozen=True)
class AsOfFixture:
    kb_path: Path
    ids: dict             # claim number -> belief id
    numbers: dict         # belief id -> claim number


def claim(n: int) -> tuple[str, str, str]:
    return (f"claim {n} subject", "states", f"claim {n} object")


def build(tmp: Path, store=None) -> AsOfFixture:
    """`store` (W3b part 4): where the fixture's acts land, a path or a
    `StoreTarget`; the default, kb.db under `tmp`, is every other test's."""
    kb = Path(tmp) / "kb.db"
    target = store if store is not None else kb
    store = TripleStore(target)
    rc = RecalibrationStore(target)
    ids: dict[int, int] = {}
    acts: list[tuple[float, str, int]] = []
    for n, (day, hh, mm) in ADMISSIONS.items():
        acts.append((at(day, hh, mm), "admit", n))
    for n, (day, hh) in ARCHIVALS.items():
        acts.append((at(day, hh), "archive", n))
    for n, (day, hh) in UNARCHIVALS.items():
        acts.append((at(day, hh), "unarchive", n))
    for n, (day, hh) in SUPERSEDES.items():
        acts.append((at(day, hh), "supersede", n))
    for ts, kind, n in sorted(acts):              # the real order of acts, by time
        with mock.patch("backend.kb.tiered_store.time.time", return_value=ts), \
                mock.patch("backend.kb.recalibration.time.time", return_value=ts):
            if kind == "admit":
                s, r, o = claim(n)
                ids[n] = store.add(subject=s, relation=r, object=o, weight=0.7, tier=1,
                                   granularity=Granularity.TOPIC, source=SOURCE)
            elif kind == "archive":
                store.archive_ids([ids[n]], reason="fixture")
            elif kind == "unarchive":
                store.unarchive_ids([ids[n]], reason="fixture")
            else:
                rc.record_outcome(event_type="supersede", decision="superseded",
                                  direction="contradicting", references={"belief_ids": [ids[n]]},
                                  actor="alice", notes=f"superseded_by: claim {n} revised",
                                  recall_count=0, recall_route=f"/belief/{ids[n]}/recall")
    rc.close(); store.close()
    return AsOfFixture(kb_path=kb, ids=dict(ids), numbers={v: k for k, v in ids.items()})


def print_as_of(day: str, rows: list[dict], fx: AsOfFixture) -> None:
    """The demo's rendering: one line per claim held, with its status at D."""
    print(f"as of {day}: {len(rows)} held")
    for r in rows:
        n = fx.numbers[r["belief_id"]]
        print(f"  claim {n:>2}  admitted {r['admitted_at_iso']}  {r['status_at']:<15} "
              f"archival_at_d={r['archival_at_d']['last_event'] or '-'}/{r['archival_at_d']['read_from']}")


# T3c's plant: a route-shaped function under a tracked path that reaches the
# projection through a helper the guard does not name.
def _rank(triple):
    from backend.kb.tiered_store import effective_prominence
    return effective_prominence(triple)


def planted_projection_route(store):
    return [{"id": t.id, "prominence": _rank(t)} for t in store.all()]


# T9c's plant: a response row that carries a standing column.
def planted_standing_row(row: dict) -> dict:
    return {**row, "high_water": 0.7}
