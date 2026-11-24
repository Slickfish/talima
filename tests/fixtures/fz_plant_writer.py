"""FZ-2 companions — planted writers the guard's source never names.

`plant_writer` writes `triples` through a chain the permitted set has not
answered for. `_run_cycle_body` is named so that the trace's host
normalisation reads the chain as the cycle host: the shape of a sixth
automatic writer added to the cycle. `persist_via_helper` is the T1d
plant: one extra frame between the host and `graph_store.save`, which the
normalisation must drop.
"""
from __future__ import annotations

import time


def plant_writer(conn) -> None:
    now = time.time()
    conn.execute("INSERT INTO triples (subject, relation, object, weight, tier, granularity, source, "
                 "created_at, last_fired) VALUES ('planted', 'co_activated', 'writer', 0.5, 1, 'session', "
                 "'compaction', ?, ?)", (now, now))
    conn.commit()


def persist_via_helper(graph_store, hippocampus) -> int:
    return graph_store.save(hippocampus)


def _run_cycle_body(action):
    """A frame named like the real cycle host, so the chain reads `cycle`."""
    return action()


def planted_route_write(db) -> None:
    """T1e's plant: a route added later that writes a table nothing names."""
    from backend.memory.lineage import LineageStore
    st = LineageStore(db)
    st.conn.execute("CREATE TABLE IF NOT EXISTS planted_rows (n INTEGER)")
    st.conn.execute("INSERT INTO planted_rows (n) VALUES (1)")
    st.conn.commit()
    st.close()
