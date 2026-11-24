"""Packet AB — relation trails: paths worn by walking.

THE MEASURED BASIS (AB.0, operator's store, 41 lineage records)
    Co-citation alone does not separate identity from topic: the genuine
    spelling pair (`is_metabolized_by` ~ `primarily_metabolized_by`) scored
    8 co-citations against topic pairs at 7 and 6. PMI inverted — 0.96 for
    the genuine pair, 1.78 and 1.56 for the topic pairs — because PMI
    measures association, and topic association is real; it just is not
    identity. What separated cleanly was STRUCTURE: shared (subject, object)
    was 1 for the genuine pair and 0 for every other row in the top fifteen.

THE THREE-LEG CONJUNCTION, each leg covering another's failure mode
    frequency    co-citation events with refractory recurrence (E.2's shape)
                 — kills dead spellings never used together; fails alone
                 because topic pairs co-cite.
    structure    >= 1 shared live (subject, object) between the two
                 relations' claims — kills topic pairs; fails alone because
                 oppositions share (s, o) too (T.2: "necessary and nowhere
                 near sufficient").
    veto         the configured opposition set, evaluated over the REACHABLE
                 SET, not the edge: trails are pairwise, so A~B and B~C
                 quietly yields A-reaches-C, and if A opposes C the paths
                 would wear around an edge-only veto. A component containing
                 any opposed pair is wholly inactive.

THE SENSITIVITY BOUND, declared rather than hidden
    AB.0 found `affinity_for` ~ `demonstrates_affinity_for` passing the
    frequency legs with shared_(s,o) = 0 — a genuine spelling pair the
    structural test misses, because its objects differ in spelling. The
    signal is specific, not sensitive, and its reach is bounded by term
    identity (deliberately out of AB's scope). `status()` therefore reports
    `structurally_unconfirmed` — pairs that clear frequency and fail
    structure — so the bound is a number on a surface, not a caveat in a
    document. Because structure is computed from LIVE claims at read time, a
    later claim sharing (s, o) activates the trail with no re-scan.

WHAT A TRAIL NEVER DOES
    No stored relation is rewritten, no two claims merged, no normalisation
    pass — trails live in their OWN tables and never as `triples` rows
    (writing associations as beliefs is OQ-1's category error; 13,183
    `co_activated` rows are the precedent for how that goes). A trail only
    ever ADDS retrieval candidates; ordering is untouched (W's law:
    admission, never rank).

REDUCIBILITY, two switches on the W.1 pattern
    NEXUS_RELATION_TRAILS=off  stops laying entirely — no writes, exactly as
                               today.
    NEXUS_TRAIL_SLOTS=0        (the default) records trails but applies them
                               nowhere — the semantic channel's exact shape:
                               installing AB changes no retrieval behaviour
                               until the operator turns the channel on.

COVERAGE IS DECLARED. Trails are laid from lineage records, so history can
be BACKFILLED — but only behind Law 3 (`POST /kb/trails/backfill/confirm`):
a fresh install starts its cursor at the newest existing record and touches
nothing older until confirmed. `status()` says from when trails run and
whether history was processed, for the reason every provenance surface does.
"""
from __future__ import annotations

import logging
import os
from backend.store import Row, StoreTarget, connect as store_connect
import time
from pathlib import Path

from backend.kb.schema import DEFAULT_DB

logger = logging.getLogger("nexus.kb.trails")

# PROVISIONAL floors, marked so. On the operator's measured distribution the
# genuine pair carried 8 events; the structural leg is what kills the noise,
# so the frequency floor stays modest — it exists to keep one-off co-citation
# from counting as a worn path, not to do the separating.
MIN_EVENTS = 3
MIN_RECURRENCE = 2
# E.2's distributed-repetition debounce, same reasoning: a burst of turns in
# one sitting is ONE episode of walking.
TRAIL_REFRACTORY_HOURS = 12.0


def trails_enabled() -> bool:
    """`NEXUS_RELATION_TRAILS=off` stops trail laying. Read at call time."""
    return os.environ.get("NEXUS_RELATION_TRAILS",
                          "on").strip().lower() != "off"


def trail_slots() -> int:
    """Candidates the trail channel may ADD to a retrieval pool. 0 (default)
    = trails are recorded and applied nowhere — the W.1 shape exactly."""
    raw = os.environ.get("NEXUS_TRAIL_SLOTS", "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("NEXUS_TRAIL_SLOTS=%r is not an integer — using 0", raw)
        return 0


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS relation_trails (
    rel_a            TEXT NOT NULL,
    rel_b            TEXT NOT NULL,
    event_count      INTEGER NOT NULL DEFAULT 0,
    recurrence_count INTEGER NOT NULL DEFAULT 0,
    last_counted_at  REAL NOT NULL DEFAULT 0,
    first_seen_ts    REAL NOT NULL,
    last_seen_ts     REAL NOT NULL,
    PRIMARY KEY (rel_a, rel_b)
);
CREATE TABLE IF NOT EXISTS trail_cursor (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    last_ts       REAL NOT NULL,
    initialized_at REAL NOT NULL,
    backfilled    INTEGER NOT NULL DEFAULT 0
);
"""


class TrailStore:
    """kb.db-backed (write-topology rule: archive.db keeps one writer)."""

    def __init__(self, db_path: "Path | str | StoreTarget" = DEFAULT_DB):
        # W3b — a path is SQLite exactly as before; a StoreTarget names the
        # backend (`backend.store`).
        self.target = StoreTarget.of(db_path)
        self.db_path = self.target.path
        self.conn = store_connect(self.target)
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------- cursor ----------

    def _ensure_cursor(self) -> Row:
        row = self.conn.execute("SELECT * FROM trail_cursor").fetchone()
        if row is not None:
            return row
        # FIRST CONTACT: start at the newest existing lineage record and
        # process NOTHING older. History is only entered via the Law 3
        # backfill — a fresh install must not silently consume months of
        # records nobody confirmed.
        newest = self.conn.execute(
            "SELECT COALESCE(MAX(ts), 0) FROM lineage_records").fetchone()[0] \
            if self._has_lineage() else 0
        now = time.time()
        self.conn.execute(
            "INSERT INTO trail_cursor (id, last_ts, initialized_at, "
            "backfilled) VALUES (1, ?, ?, 0)", (float(newest), now))
        self.conn.commit()
        return self.conn.execute("SELECT * FROM trail_cursor").fetchone()

    def _has_lineage(self) -> bool:
        return self.conn.has_table("lineage_records")

    # ---------- laying ----------

    def _cited_relations(self, injected_json: str) -> set:
        import json
        try:
            items = json.loads(injected_json)
        except ValueError:
            return set()
        ids = [i.get("belief_id") for i in items
               if i.get("cited") is not False and i.get("belief_id") is not None]
        if not ids:
            return set()
        marks = ",".join("?" * len(ids))
        return {r["relation"] for r in self.conn.execute(
            f"SELECT DISTINCT relation FROM triples WHERE id IN ({marks})",
            ids)}

    def _lay_range(self, since_ts: float, until_ts: float | None, *,
                   dry: bool = False) -> dict:
        """One pass over lineage records in (since_ts, until_ts]. One
        transaction for the whole pass (Packet AA's discipline).

        FZ-2 — `dry=True` walks the same records and counts the same events
        without writing a row, so a frozen cycle can report exactly how many
        writes it deferred."""
        if not self._has_lineage():
            return {"records": 0, "events": 0}
        refractory = TRAIL_REFRACTORY_HOURS * 3600.0
        sql = ("SELECT ts, injected_json FROM lineage_records "
               "WHERE ts > ? ORDER BY ts ASC")
        args: list = [since_ts]
        if until_ts is not None:
            sql = sql.replace("ORDER BY", "AND ts <= ? ORDER BY")
            args.append(until_ts)
        records = events = 0
        newest = since_ts
        cur = self.conn.cursor()
        for row in self.conn.execute(sql, args).fetchall():
            records += 1
            newest = max(newest, row["ts"])
            rels = sorted(self._cited_relations(row["injected_json"]))
            if len(rels) < 2:
                continue
            for i, a in enumerate(rels):
                for b in rels[i + 1:]:
                    events += 1
                    if dry:
                        continue
                    prev = cur.execute(
                        "SELECT last_counted_at FROM relation_trails "
                        "WHERE rel_a=? AND rel_b=?", (a, b)).fetchone()
                    recurred = int(prev is None
                                   or row["ts"] - prev[0] > refractory)
                    cur.execute(
                        "INSERT INTO relation_trails (rel_a, rel_b, "
                        "event_count, recurrence_count, last_counted_at, "
                        "first_seen_ts, last_seen_ts) "
                        "VALUES (?, ?, 1, ?, ?, ?, ?) "
                        # W3b — the existing row's columns qualified: Postgres
                        # reads a bare `event_count` in DO UPDATE SET as
                        # ambiguous between the row and EXCLUDED (the guard
                        # found it); SQLite read it as the row's, which the
                        # qualified name says on both.
                        "ON CONFLICT (rel_a, rel_b) DO UPDATE SET "
                        "event_count = relation_trails.event_count + 1, "
                        "recurrence_count = relation_trails.recurrence_count + excluded.recurrence_count, "
                        "last_counted_at = excluded.last_counted_at, "
                        "last_seen_ts = excluded.last_seen_ts",
                        (a, b, recurred, row["ts"], row["ts"], row["ts"]))
        return {"records": records, "events": events, "newest": newest}

    def scan_new(self) -> dict:
        """The steady-state pass: records newer than the cursor. KAIROS's
        idle-cycle entry point. No-op with laying switched off."""
        if not trails_enabled():
            return {"skipped": "disabled"}
        cursor = self._ensure_cursor()
        # FZ-2 — DEFERRED, NOT DROPPED. Under frozen the pass is walked dry:
        # the writes it would have made are counted, no trail row is
        # written and, decisively, the cursor does not advance, so the
        # first live cycle after an unfreeze lays exactly these records.
        # The cursor row itself is still initialised above: it is
        # bookkeeping, and a cursor first created AFTER an unfreeze would
        # start at the newest record and put the frozen period behind it.
        from backend.learning import learning_frozen, record_skip
        if learning_frozen():
            dry = self._lay_range(cursor["last_ts"], None, dry=True)
            record_skip("TrailStore.scan_new",
                        int(dry.get("events", 0)) + (1 if dry.get("records") else 0))
            return {"skipped": "frozen", "deferred": True, **dry}
        try:
            result = self._lay_range(cursor["last_ts"], None)
            if result.get("records"):
                self.conn.execute("UPDATE trail_cursor SET last_ts = ?",
                                  (result["newest"],))
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        return result

    def backfill(self) -> dict:
        """Law 3 body — the caller holds the confirm gate. Processes every
        record at or before the cursor's initial position, once."""
        cursor = self._ensure_cursor()
        if cursor["backfilled"]:
            return {"already_backfilled": True, "records": 0, "events": 0}
        try:
            result = self._lay_range(0.0, cursor["last_ts"])
            self.conn.execute("UPDATE trail_cursor SET backfilled = 1")
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        return result

    # ---------- the three legs ----------

    def _shared_so(self, rel_a: str, rel_b: str) -> int:
        """LIVE structural confirmation: claims of the two relations sharing
        (subject, object). Computed at read so a later claim can activate an
        old trail — structure confirmed after the walking, not only before."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM triples t1 JOIN triples t2 "
            "ON t1.subject = t2.subject AND t1.object = t2.object "
            "WHERE t1.relation = ? AND t2.relation = ? "
            "AND t1.archived = 0 AND t2.archived = 0",
            (rel_a, rel_b)).fetchone()[0]

    def _classified(self) -> dict:
        """Every recorded pair, sorted into its state. One pass, shared by
        `active_map()` and `status()` so the two surfaces cannot disagree."""
        from backend.cortex.contradiction import load_pairs
        oppositions = {frozenset(p) for p in load_pairs()}

        rows = self.conn.execute(
            "SELECT * FROM relation_trails ORDER BY rel_a, rel_b").fetchall()
        below, unconfirmed, structural = [], [], []
        for r in rows:
            pair = (r["rel_a"], r["rel_b"])
            if (r["event_count"] < MIN_EVENTS
                    or r["recurrence_count"] < MIN_RECURRENCE):
                below.append(pair)
            elif self._shared_so(*pair) < 1:
                # THE SENSITIVITY BOUND, on a surface. AB.0's
                # affinity_for ~ demonstrates_affinity_for lands here.
                unconfirmed.append(pair)
            else:
                structural.append(pair)

        # ---- the veto, over the REACHABLE SET -----------------------------
        # Union-find over would-be-active edges; a component containing any
        # opposed pair is wholly inactive, or paths wear around the veto.
        parent: dict = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            parent[find(a)] = find(b)

        for a, b in structural:
            union(a, b)
        components: dict = {}
        for a, b in structural:
            components.setdefault(find(a), set()).update((a, b))
        poisoned_roots = set()
        for root, members in components.items():
            for m in list(members):
                for n in list(members):
                    if m < n and frozenset((m, n)) in oppositions:
                        poisoned_roots.add(root)
        active = [p for p in structural if find(p[0]) not in poisoned_roots]
        vetoed = [p for p in structural if find(p[0]) in poisoned_roots]
        return {"below_floor": below, "structurally_unconfirmed": unconfirmed,
                "vetoed_closure": vetoed, "active": active,
                "recorded": len(rows)}

    def active_map(self) -> dict:
        """{relation: set(trail-linked relations)} — the retrieval channel's
        read surface. Empty when nothing qualifies."""
        out: dict = {}
        for a, b in self._classified()["active"]:
            out.setdefault(a, set()).add(b)
            out.setdefault(b, set()).add(a)
        return out

    def status(self) -> dict:
        """Declared coverage and the sensitivity bound, per AB.1's
        constraints — a number on a surface, not a caveat in a document."""
        c = self._classified()
        cursor = self.conn.execute("SELECT * FROM trail_cursor").fetchone()
        return {
            "enabled": trails_enabled(),
            "slots": trail_slots(),
            "recorded_pairs": c["recorded"],
            "active": sorted(map(list, c["active"])),
            "below_floor": len(c["below_floor"]),
            # Frequency-qualified pairs the structural leg cannot confirm —
            # the measured miss-set. AB.0: 1 known-genuine of 2 on n=2.
            "structurally_unconfirmed": sorted(
                map(list, c["structurally_unconfirmed"])),
            "vetoed_closure": sorted(map(list, c["vetoed_closure"])),
            "floors": {"min_events": MIN_EVENTS,
                       "min_recurrence": MIN_RECURRENCE,
                       "refractory_hours": TRAIL_REFRACTORY_HOURS,
                       "provisional": True},
            "coverage": (None if cursor is None else {
                "traces_since": cursor["initialized_at"],
                "cursor_ts": cursor["last_ts"],
                "backfilled": bool(cursor["backfilled"]),
            }),
        }
