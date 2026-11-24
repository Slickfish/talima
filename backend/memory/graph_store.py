"""Persistent Hebbian association graph (Packet E.3).

The graph was in-memory only (`hippocampus.py`, a bare `nx.DiGraph`), so
consolidation — which reads `SynapticEdge.fire_count` — could only ever see
WITHIN-PROCESS firing. An association reinforced weekly for six months
consolidated never; 25 query-scaffolding terms co-firing in one debugging
afternoon produced 555 of the store's 787 triples. On a volatile graph that
is the only output the mechanism can produce. Persisting the graph is what
makes E.2's recurrence counter mean anything: `recurrence_count` is monotone
across episodes, and episodes are separated by more than a process lifetime.

Write topology (REGROUND_REPORT §4, reaffirmed in E.0.3): this lives in
**kb.db**. `archive.db` keeps exactly one writer (KAIROS). Two writers touch
this table — the chat path at turn end and KAIROS at cycle end — so both
hold `NexusServices.graph_lock` across mutate-plus-persist. `session_active`
alone is insufficient: it is a cooperative flag that a cycle already in
flight does not re-check, leaving a window at every `await`.

Decay is wall-clock and lazy. `last_seen` is persisted and
`SynapticEdge.current_weight(now)` already decays from it at read time, so a
month of downtime is a month of forgetting with no startup sweep and no
third decay mechanism (E.0.4 / amendment 3).

Migration follows the `chain.py` / `lineage.py` pattern: additive,
re-runnable, `CREATE TABLE IF NOT EXISTS` plus `PRAGMA table_info` + `ALTER`.
Deliberately NOT the checkpointer pattern — that is `InMemorySaver` and
persists nothing across processes (pack §4).
"""
from __future__ import annotations

import json
import logging
from backend.store import StoreTarget, connect as store_connect
from pathlib import Path

from backend.cortex.edges import ActivationType, SynapticEdge

logger = logging.getLogger("nexus.graph_store")

DEFAULT_KB_DB = Path("data/kb.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS hebbian_edges (
    source           TEXT    NOT NULL,
    target           TEXT    NOT NULL,
    weight           REAL    NOT NULL DEFAULT 0.5,
    fire_count       INTEGER NOT NULL DEFAULT 0,
    recurrence_count INTEGER NOT NULL DEFAULT 0,
    last_counted_at  REAL    NOT NULL DEFAULT 0.0,
    last_seen        REAL    NOT NULL DEFAULT 0.0,
    demoted          INTEGER NOT NULL DEFAULT 0,
    activation_type  TEXT    NOT NULL DEFAULT 'excitatory',
    -- Persisted because wall-clock decay depends on it: without it a
    -- reloaded edge silently reverts to the class default and forgets at a
    -- different rate than it did before the restart.
    decay_per_day    REAL    NOT NULL DEFAULT 0.02,
    PRIMARY KEY (source, target)
);
CREATE INDEX IF NOT EXISTS idx_hebbian_recurrence
    ON hebbian_edges(recurrence_count);
"""


class HebbianGraphStore:
    """kb.db-backed persistence for the association graph."""

    def __init__(self, db_path: "Path | str | StoreTarget" = DEFAULT_KB_DB):
        # W3b — a path is SQLite exactly as before; a StoreTarget names the
        # backend (`backend.store`).
        self.target = StoreTarget.of(db_path)
        self.db_path = self.target.path
        self.conn = store_connect(self.target)
        self.conn.executescript(SCHEMA_SQL)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Additive and re-runnable — the chain.py pattern."""
        cols = {c.name for c in self.conn.columns("hebbian_edges")}
        for name, ddl in (
            ("recurrence_count", "INTEGER NOT NULL DEFAULT 0"),
            ("last_counted_at",  "REAL NOT NULL DEFAULT 0.0"),
            ("last_seen",        "REAL NOT NULL DEFAULT 0.0"),
            ("demoted",          "INTEGER NOT NULL DEFAULT 0"),
            ("activation_type",  "TEXT NOT NULL DEFAULT 'excitatory'"),
            ("decay_per_day",    "REAL NOT NULL DEFAULT 0.02"),
        ):
            if name not in cols:
                self.conn.execute(
                    f"ALTER TABLE hebbian_edges ADD COLUMN {name} {ddl}")

    def close(self) -> None:
        self.conn.close()

    # ---------- write ----------

    def save(self, hippocampus) -> int:
        """Upsert every edge. Never deletes a row: a demoted edge persists
        with demoted=1 (I-2 — prominence falls, recoverability does not)."""
        rows = []
        for edge in hippocampus.edges():
            rows.append((
                edge.source, edge.target, float(edge.weight),
                int(edge.fire_count), int(edge.recurrence_count),
                float(edge.last_counted_at),
                float(edge.last_fired),          # last_seen
                1 if edge.demoted else 0,
                getattr(edge.activation_type, "value", "excitatory"),
                float(edge.decay_per_day),
            ))
        if not rows:
            return 0
        # FZ-2 — the freeze, checked here because this is the one method
        # that writes the association graph (both hosts: the post-turn
        # persist and the cycle's). Under frozen: nothing written, the
        # rows counted as skipped, the caller's no-op value returned.
        from backend.learning import learning_frozen, record_skip
        if learning_frozen():
            record_skip("HebbianGraphStore.save", len(rows))
            return 0
        self.conn.executemany(
            """
            INSERT INTO hebbian_edges
                (source, target, weight, fire_count, recurrence_count,
                 last_counted_at, last_seen, demoted, activation_type,
                 decay_per_day)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, target) DO UPDATE SET
                weight           = excluded.weight,
                fire_count       = MAX(hebbian_edges.fire_count, excluded.fire_count),
                recurrence_count = MAX(hebbian_edges.recurrence_count,
                                       excluded.recurrence_count),
                last_counted_at  = MAX(hebbian_edges.last_counted_at,
                                       excluded.last_counted_at),
                last_seen        = MAX(hebbian_edges.last_seen, excluded.last_seen),
                demoted          = excluded.demoted,
                activation_type  = excluded.activation_type,
                decay_per_day    = excluded.decay_per_day
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    # ---------- read ----------

    def load_into(self, hippocampus) -> int:
        """Rehydrate edges into a hippocampus. No decay sweep: weight is
        stored as-is and `current_weight(now)` applies elapsed wall-clock at
        read time, so downtime is forgotten lazily and locally."""
        n = 0
        # W3b D3 — `ORDER BY rowid` says what this read always relied on: the
        # save order (a row keeps its rowid across an upsert), which seeds
        # networkx's adjacency order and through it `fired_sequence`, the
        # activation tie-break, the Hebbian top-k, `select_cortex` and
        # collapse. On SQLite it is the scan order the bare SELECT returned
        # (measured); on Postgres `rowid` is an explicit insertion ordinal.
        for r in self.conn.execute("SELECT * FROM hebbian_edges ORDER BY rowid"):
            try:
                atype = ActivationType(r["activation_type"])
            except ValueError:
                atype = ActivationType.EXCITATORY
            edge = SynapticEdge(
                source=r["source"], target=r["target"], weight=r["weight"],
                activation_type=atype,
                decay_per_day=r["decay_per_day"],
                last_fired=r["last_seen"], fire_count=r["fire_count"],
                last_counted_at=r["last_counted_at"],
                recurrence_count=r["recurrence_count"],
                demoted=bool(r["demoted"]),
            )
            hippocampus.add_edge(edge)
            n += 1
        return n

    def count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) AS n FROM hebbian_edges").fetchone()["n"])

    def recurrence_histogram(self) -> dict:
        return {int(r["recurrence_count"]): int(r["n"]) for r in self.conn.execute(
            "SELECT recurrence_count, COUNT(*) AS n FROM hebbian_edges "
            "GROUP BY recurrence_count ORDER BY recurrence_count")}


# ---------------------------------------------------------------------------
# I-4 — citation confers durability
# ---------------------------------------------------------------------------

def cited_pairs(lineage_store, triple_store) -> set[tuple[str, str]]:
    """(subject, object) pairs of beliefs cited by a SEALED lineage record.

    A belief that fed a decision someone may have to defend is load-bearing:
    its association is exempt from demotion entirely, not merely undeleted.
    Only sealed records confer this — an unsealed record is not yet part of
    the tamper-evident history.
    """
    pairs: set[tuple[str, str]] = set()
    if lineage_store is None or triple_store is None:
        return pairs
    try:
        rows = lineage_store.conn.execute(
            "SELECT injected_json FROM lineage_records "
            "WHERE sealed_block IS NOT NULL").fetchall()
    except Exception as e:
        # AN.1 — this returned the EMPTY set with a console log line, so a
        # transient db error stripped I-4's exemption from every sealed-cited
        # belief for that pass, silently. Named, so the prune it feeds can
        # fail closed instead of failing open.
        from backend.cortex.hebbian import ProtectionUnavailable
        raise ProtectionUnavailable(
            f"cited-pair lookup failed: {type(e).__name__}: {e}") from e

    for row in rows:
        try:
            injected = json.loads(row["injected_json"] or "[]")
        except (TypeError, ValueError):
            continue
        for item in injected:
            bid = item.get("belief_id") if isinstance(item, dict) else None
            if bid is None:
                continue
            # AN.1 — the old per-row `except Exception: t = None` could not
            # tell a MISSING row from a BROKEN store: `get_by_id` already
            # returns None for an id that does not resolve (that row is
            # skipped — protection for an unresolvable belief is not
            # constructible, and one bad id must not strip it from the
            # rest), while a raising call is infrastructure failure and
            # must fail the whole lookup closed, by name.
            try:
                t = triple_store.get_by_id(int(bid))
            except (TypeError, ValueError):
                continue                       # malformed id in old JSON
            except Exception as e:
                from backend.cortex.hebbian import ProtectionUnavailable
                raise ProtectionUnavailable(
                    f"cited-pair belief resolution failed: "
                    f"{type(e).__name__}: {e}") from e
            if t is not None:
                pairs.add((t.subject, t.object))
    return pairs
