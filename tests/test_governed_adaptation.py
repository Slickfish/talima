"""Packet S.1 — guards before anything moves a weight.

THE CLAIM THIS FILE PROTECTS
    Every ML system moves its weights silently; that is what training is.
    NEXUS is to treat a change in what it believes as an EVENT — author,
    reason, timestamp, a bound it cannot exceed, sealed into the same chain as
    everything else. The whole packet succeeds or fails on whether an operator
    can be handed the record and shown that no weight moved outside a limit
    they set.

    Sentinels precede the change. Section A is true TODAY and must stay true
    after S.2: it pins the ground the change stands on. Section B is
    `xfail(strict)` — it states the behaviour S.2 must produce, and flips to a
    plain pass when it does.

WHY SECTION A IS THE LOAD-BEARING HALF
    S.0.4 called the weight move "the crux". It is not, and Section A is why.
    F.3 and Q.2 already built the seam: `current_weight()` is a read-time
    projection over a stored value nothing writes, and Q.2 added
    `arrival_scale` as a second factor with its reason stated in the source —
    *"Scaling the stored weight would write a number nobody claimed."*
    Recalibration is the third factor in that product, which means the
    invariant S.2 must not break is one that holds right now:

        the stored `weight` column has exactly one writer, and it is monotone.

    `test_stored_weight_has_exactly_one_writer` asserts that over the whole
    backend, by AST, today. If S.2 ever reaches for `UPDATE triples SET
    weight`, that test fails before any of Section B goes green — which is the
    correct order for the failure to arrive in.

WHAT THIS FILE FIXES ABOUT S.2'S SHAPE, DELIBERATELY
    Section B imports names that do not exist yet. That is not a guess
    dressed as a test: a sentinel that cannot name what it is waiting for
    cannot fail when the wrong thing arrives. The names are S.0's
    recommendation made concrete, and S.2 either satisfies them or argues
    them down — either way the argument happens before the code, not after.
"""
from __future__ import annotations

import ast
import re
import sqlite3
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore
from tests._tracked import tracked_backend

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"


# ---------------------------------------------------------------------------
# Fixture: a store that has been USED, not a store that has been created
# ---------------------------------------------------------------------------

def _used_store(tmp_path) -> tuple[TripleStore, dict]:
    """Beliefs across several evidence classes, weights and tiers.

    Several classes deliberately: F.3 makes decay a property of
    `classify(source)` and Q.2 makes arrival scale one too, so a single-source
    fixture cannot see an interaction between recalibration and either of
    them. Weights straddle `HIGH_CONF_FLOOR` and both `TIER_ENTRY[0]` and
    `TIER_ENTRY[1]` so a move has somewhere to move to.
    """
    store = TripleStore(tmp_path / "kb.db")
    ids: dict[str, int] = {}
    for s, r, o, w, tier, src in [
        ("QT interval", "is_a_segment_of", "ECG",           0.95, 0, "human_assertion"),
        ("ganymede",    "orbits",          "jupiter",       0.90, 1, "wikidata"),
        ("water",       "is_a",            "liquid",        0.70, 2, "offline_fixture"),
        ("QTc",         "stands_for",      "corrected QT",  0.55, 2, "extraction"),
        ("aspirin",     "is_used_for",     "pain relief",   0.40, 3, "extraction"),
    ]:
        ids[s] = store.add(subject=s, relation=r, object=o, weight=w, tier=tier,
                           granularity=Granularity.TOPIC, source=src)
    return store, ids


def _weights(store: TripleStore, ids: dict) -> dict[int, float]:
    return {i: store.get_by_id(i).weight for i in ids.values()}


def _record_layer(store: TripleStore, ids: dict) -> dict[int, tuple]:
    """(weight, source, created_at) — I-8's absolute half, per belief."""
    out = {}
    for i in ids.values():
        t = store.get_by_id(i)
        out[i] = (t.weight, t.source, t.created_at)
    return out


# ===========================================================================
# SECTION A — true today, and must stay true after S.2
# ===========================================================================

def _sql_literals(path: Path) -> list[str]:
    """Every SQL-ish string a module builds, f-strings included.

    AST, not a text search — the sixth packet running. A docstring saying
    "UPDATE triples SET weight" (this file has several) is prose; a string
    node the module evaluates is code. `FormattedValue` holes become `?`,
    which is what they hold at runtime anyway.
    """
    return _sql_literals_from_source(path.read_text(encoding="utf-8"), str(path))


def _sql_literals_from_source(source: str, filename: str = "<test>") -> list[str]:
    out: list[str] = []
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            parts = []
            for piece in node.values:
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                    parts.append(piece.value)
                else:
                    parts.append("?")
            out.append("".join(parts))
    return out


_SET_CLAUSE = re.compile(
    r"UPDATE\s+triples\s+SET\s+(.*?)(?:\s+WHERE\b|$)", re.I | re.S)
# Scoped to `INSERT INTO triples` on purpose. The first draft matched any
# upsert and caught `hebbian_edges`, whose `weight` column IS freely written —
# correctly, because an association's strength is prominence, not a claim
# anybody made. Conflating the two tables' `weight` is precisely the mistake
# this packet must not make, so the guard names its table.
_DO_UPDATE = re.compile(
    r"INSERT\s+INTO\s+triples\b.*?DO\s+UPDATE\s+SET\s+(.*?)(?:\s+RETURNING\b|$)",
    re.I | re.S)
_ASSIGNED = re.compile(r"([a-z_]+)\s*=", re.I)


def _assigned_columns(pattern: re.Pattern) -> dict[str, set[str]]:
    """{module: columns it assigns} for one UPDATE flavour."""
    found: dict[str, set[str]] = {}
    for path in tracked_backend():
        rel = path.relative_to(ROOT).as_posix()
        for sql in _sql_literals(path):
            for clause in pattern.findall(sql):
                # `weight = MAX(triples.weight, excluded.weight)` — the LHS of
                # each assignment only, so the read of `triples.weight` inside
                # the expression is not miscounted as a write to it.
                cols = {m.group(1).lower()
                        for m in _ASSIGNED.finditer(clause.split("(")[0])
                        } | {m.group(1).lower()
                             for m in _ASSIGNED.finditer(
                                 re.sub(r"\([^)]*\)", "", clause))}
                found.setdefault(rel, set()).update(cols)
    return found


# Frozen both ways. Adding a column here is a decision about the record layer
# and must be argued; removing one silently would mean a writer disappeared.
EXPECTED_PLAIN_UPDATES = {
    "backend/kb/schema.py":       {"archived", "tier"},        # F.2 migration
    "backend/kb/tiered_store.py": {"archived", "archive_reason", "archived_at",
                                   "fire_count", "last_fired", "tier"},
}

# The ONE place `weight` is assigned: `add()`'s upsert.
EXPECTED_UPSERT = {
    "backend/kb/tiered_store.py": {"weight", "tier", "last_fired"},
}


def test_stored_weight_has_exactly_one_writer():
    """I-8's record half, enforced rather than described.

    `tiered_store.py` opens by saying stored weight "is never written downward
    anywhere in the codebase". That was prose. This is the control — and the
    reason it exists in S.1 rather than S.2 is that a recalibration which
    reaches for `UPDATE triples SET weight` must fail HERE, before any of the
    governed-adaptation tests can go green and make it look intended.
    """
    plain = _assigned_columns(_SET_CLAUSE)
    assert plain == EXPECTED_PLAIN_UPDATES, (
        f"the set of plain UPDATEs on `triples` changed:\n"
        f"  got      {plain}\n  expected {EXPECTED_PLAIN_UPDATES}")
    for module, cols in plain.items():
        assert "weight" not in cols, (
            f"{module} now writes `triples.weight` through a plain UPDATE. "
            f"Recalibration belongs in the PROMINENCE layer as a read-time "
            f"factor (F.3, Q.2), not in the record layer.")

    upsert = _assigned_columns(_DO_UPDATE)
    assert upsert == EXPECTED_UPSERT, (
        f"the upsert's assigned columns changed:\n"
        f"  got      {upsert}\n  expected {EXPECTED_UPSERT}")


def test_the_writer_guard_can_actually_see_a_weight_write():
    """NON-VACUITY FOR THE GUARD ITSELF.

    `test_stored_weight_has_exactly_one_writer` passes today. It would also
    pass if the extractor silently found nothing — a regex that matches no SQL
    at all reports an empty set, and an empty set contains no `weight`. So the
    extractor is shown a write it must catch, in both the plain and the
    f-string form that `tiered_store.py` actually uses.
    """
    synthetic = (
        'def bad(conn, w, i):\n'
        '    conn.execute("UPDATE triples SET weight = ? WHERE id = ?", (w, i))\n'
        '    conn.execute(f"UPDATE triples SET weight = {w} WHERE id = ?", (i,))\n'
    )
    clauses = [c for sql in _sql_literals_from_source(synthetic)
               for c in _SET_CLAUSE.findall(sql)]
    assert clauses, "the extractor found no UPDATE at all"
    cols = {m.group(1).lower() for c in clauses for m in _ASSIGNED.finditer(c)}
    assert "weight" in cols, (
        f"the guard cannot see a weight write; it found {cols}. Every "
        f"assertion it makes would be vacuous.")


def test_association_weight_is_a_different_layer_and_is_freely_written():
    """THE CONTRAST, and the reason the guard above names its table.

    `hebbian_edges.weight` is written every cycle — strengthened, decayed,
    pruned — and that is right: an association's strength is prominence, not a
    claim anyone made, so nothing is lost when it moves. The hebbian
    prediction-error machinery (2.5x on surprise, 0.4x on confirmation) works
    entirely on this layer and never touches `triples`.

    S.0.1 found that machinery and it is tempting to reuse: "confidence
    already moves". It moves for EDGES. Asserting the two layers behave
    differently keeps the temptation visible — if a future change makes belief
    weight as freely writable as edge weight, this test still passes while the
    one above fails, which is the correct pair of signals.
    """
    upserts = _assigned_columns(re.compile(
        r"INSERT\s+INTO\s+hebbian_edges\b.*?DO\s+UPDATE\s+SET\s+(.*?)"
        r"(?:\s+RETURNING\b|$)", re.I | re.S))
    edge_cols = set().union(*upserts.values()) if upserts else set()
    assert "weight" in edge_cols, (
        "hebbian_edges.weight is no longer written — either the association "
        "layer froze, or this guard stopped finding it")


def test_the_one_writer_is_monotone():
    """The other half of I-8: the single writer may raise and may not lower.

    Behavioural, because an AST guard can see `MAX(` and not see that the
    arguments were swapped.
    """
    import tempfile

    store = TripleStore(Path(tempfile.mkdtemp()) / "kb.db")
    kw = dict(subject="ganymede", relation="orbits", object="jupiter",
              granularity=Granularity.TOPIC, source="wikidata")
    bid = store.add(weight=0.80, tier=1, **kw)

    store.add(weight=0.20, tier=1, **kw)
    assert store.get_by_id(bid).weight == pytest.approx(0.80), (
        "a lower-weight re-assertion lowered the stored weight")

    store.add(weight=0.93, tier=1, **kw)
    assert store.get_by_id(bid).weight == pytest.approx(0.93), (
        "a higher-weight re-assertion did not raise the stored weight")
    store.close()


def recorded_weight_moves(store: TripleStore) -> dict[int, list]:
    """{belief_id: [recalibration events]} — empty until S.2 exists.

    A function rather than a literal `{}` so the test below is written in its
    post-S.2 form now: when the store exists, this resolves to it and the
    assertion does not change shape.
    """
    try:
        from backend.kb.recalibration import RecalibrationStore
    except ImportError:
        return {}
    return RecalibrationStore(store.target).all_events_by_belief()  # pragma: no cover


def test_no_weight_moves_without_a_recorded_event(tmp_path):
    """THE INVARIANT THE WHOLE CLAIM RESTS ON.

    An aggressive pass over a used store — everything that touches a belief
    short of re-asserting it — must move no stored weight that a recalibration
    record does not account for.

    Unmarked, and it passes today because nothing moves weights at all. That
    is the point: it pins the change rather than validating it. The day S.2
    moves a weight, this either finds the record or fails.

    `add()` is excluded from the pass on purpose and covered by
    `test_the_one_writer_is_monotone` instead — monotone reinforcement is a
    DECLARED exception, and burying it inside the fixture would hide the one
    path that legitimately moves a stored number.
    """
    store, ids = _used_store(tmp_path)
    before = _weights(store, ids)

    # --- the pass ---------------------------------------------------------
    for bid in ids.values():
        store.fire(bid)
    store.rebalance()
    store.archive("water", "is_a", "liquid", source="offline_fixture",
                  reason="s1_probe")
    store.unarchive("water", "is_a", "liquid", source="offline_fixture")
    store.get_relevant(concepts=["qtc", "ganymede", "aspirin"], top_k=10)
    # ...and again 400 days on, so decay has had every chance to write back.
    import time as _time
    store.rebalance(now=_time.time() + 400 * 86400.0)

    after = _weights(store, ids)

    # --- non-vacuity: the pass has to have DONE something -----------------
    from backend.store import connect as store_connect
    conn = store_connect(store.target)              # the store's backing, on either backend
    fired = conn.execute(
        "SELECT COUNT(*) n FROM triples WHERE fire_count > 0").fetchone()["n"]
    tiers = {r["id"]: r["tier"] for r in
             conn.execute("SELECT id, tier FROM triples")}
    conn.close()
    assert fired == len(ids), (
        f"only {fired} of {len(ids)} beliefs were fired; the pass did not run")
    assert any(tiers[i] != t for i, t in
               [(ids["QT interval"], 0), (ids["ganymede"], 1),
                (ids["water"], 2), (ids["QTc"], 2), (ids["aspirin"], 3)]), (
        "no tier moved across 400 days of decay — the pass touched nothing, "
        "so 'no weight moved' proves nothing")

    # --- the assertion ----------------------------------------------------
    moved = {i: (before[i], after[i]) for i in before if before[i] != after[i]}
    accounted = recorded_weight_moves(store)
    unexplained = {i: v for i, v in moved.items() if not accounted.get(i)}
    assert unexplained == {}, (
        f"stored weight moved with no recalibration record: {unexplained}. "
        f"Prominence is allowed to move freely; the RECORD is not.")
    store.close()


def test_the_prominence_layer_did_move(tmp_path):
    """The mirror of the test above, and the reason it is not vacuous.

    If prominence were also frozen, "the record did not move" would be a
    statement about a system where nothing happens. Decay must visibly change
    what a belief is worth while leaving what it says untouched.
    """
    import time as _time

    store, ids = _used_store(tmp_path)
    bid = ids["ganymede"]
    t = store.get_by_id(bid)
    later = _time.time() + 400 * 86400.0
    assert t.current_weight(later) < t.current_weight(), (
        "prominence did not decay over 400 days; the record-layer assertion "
        "next door is measuring a system in which nothing moves")
    assert store.get_by_id(bid).weight == pytest.approx(t.weight), (
        "reading prominence altered the record")
    store.close()


# ===========================================================================
# SECTION B — what S.2 and S.3 must produce. xfail(strict) until they do.
# ===========================================================================
#
# Each of these names the API S.2 has to satisfy. If S.2 lands a different
# shape, these are the argument — updated deliberately, in the same commit,
# with the reason written down. What they must NOT do is quietly relax.

def test_a_recalibration_cannot_exceed_its_envelope(tmp_path, recalibration_on):
    """THE BOUND, with non-vacuity: the attempt is MADE and REFUSED.

    A test that only checks "the weight did not move a lot" passes on a system
    where nothing moved because nothing was tried. So this asserts three
    things in order — a within-bounds move succeeds, an over-bounds move
    raises, and the belief is untouched afterwards.
    """
    from backend.kb.recalibration import (
        Envelope, EnvelopeViolation, RecalibrationStore, apply_outcome)

    store, ids = _used_store(tmp_path)
    recal = RecalibrationStore(store.target)
    store.recalibration = recal
    envelope = Envelope(version="test-v0", max_delta=0.05,
                        movable_classes=("extraction",))
    bid = ids["QTc"]

    ok = apply_outcome(store, recal, belief_id=bid, delta=-0.04,
                       envelope=envelope, actor="operator",
                       outcome_ref="outcome-1")
    assert ok.delta == pytest.approx(-0.04)
    assert ok.envelope_version == "test-v0"

    # NON-VACUITY: the refused attempt really is attempted.
    with pytest.raises(EnvelopeViolation) as excinfo:
        apply_outcome(store, recal, belief_id=bid, delta=-0.40,
                      envelope=envelope, actor="operator",
                      outcome_ref="outcome-2")
    assert "max_delta" in str(excinfo.value), (
        "the refusal did not name the limit that refused it (I-6)")
    assert len(recal.events_for(bid)) == 1, (
        "the refused recalibration was recorded as though it had applied")
    store.close()


def test_changing_the_envelope_is_itself_an_event(tmp_path):
    """What makes it a policy object rather than a constant.

    "Bounded by limits you set" is only true if the limits are a thing that
    can be set, named, and audited — so the history of the bound is itself
    part of the record, and a recalibration cites the version that permitted
    it.
    """
    from backend.kb.recalibration import Envelope, RecalibrationStore

    store, _ids = _used_store(tmp_path)
    recal = RecalibrationStore(store.target)

    recal.record_envelope(Envelope(version="v0", max_delta=0.00),
                          actor="operator", reason="initial: nothing moves")
    recal.record_envelope(Envelope(version="v1", max_delta=0.05),
                          actor="operator", reason="board approved ±0.05")

    history = recal.envelope_history()
    assert [e.version for e in history] == ["v0", "v1"]
    assert all(e.actor and e.ts and e.reason for e in history), (
        "an envelope change was recorded without author, time or reason")
    store.close()


def test_the_record_layer_survives_a_recalibration(tmp_path, recalibration_on):
    """I-8's enforcement for this packet.

    After a recalibration the belief's `source`, `created_at` and stored
    `weight` are byte-for-byte what they were. If any of those move, the
    system has started writing a number nobody claimed — the thing Q.2
    refused to do for arrival scale and the thing `MAX()` proved
    unrecoverable.
    """
    from backend.kb.recalibration import (
        Envelope, RecalibrationStore, apply_outcome)

    store, ids = _used_store(tmp_path)
    recal = RecalibrationStore(store.target)
    store.recalibration = recal
    before = _record_layer(store, ids)
    bid = ids["QTc"]
    # S.1 compared the post-recalibration effective weight against the STORED
    # weight, which extraction's 0.65 arrival scale already puts it below —
    # the assertion would have passed with no recalibration at all. Corrected
    # here rather than left, because a sentinel that cannot fail is worse than
    # no sentinel: the comparison is now before-vs-after the act itself.
    effective_before = store.effective_weight(bid)

    apply_outcome(store, recal, belief_id=bid, delta=-0.05,
                  envelope=Envelope(version="v1", max_delta=0.05,
                                    movable_classes=("extraction",)),
                  actor="operator", outcome_ref="outcome-1")

    assert _record_layer(store, ids) == before, (
        "a recalibration altered the record layer")
    # ...and it did something, or the assertion above is about a no-op.
    assert recal.events_for(bid), "no recalibration event was recorded"
    assert store.effective_weight(bid) == pytest.approx(
        effective_before - 0.05), (
        "the recalibration changed nothing an operator could observe")
    store.close()


def test_a_belief_can_be_traced_to_the_outcomes_that_moved_it(tmp_path, recalibration_on):
    """Learning provenance as a QUERY, not a reconstruction.

    Each recalibration resolves to its outcome, its actor and the envelope
    version that permitted it. The chain of custody for a number.

    It answers only for weights moved after S.2 ships. TEMPORAL_PROBE
    established that `MAX()` erased everything before that irrecoverably, and
    the surface must say so rather than rendering an empty list that reads as
    "nothing ever moved this".
    """
    from backend.kb.recalibration import (
        Envelope, RecalibrationStore, apply_outcome, provenance_for)

    store, ids = _used_store(tmp_path)
    recal = RecalibrationStore(store.target)
    store.recalibration = recal
    env = Envelope(version="v1", max_delta=0.05,
                   movable_classes=("extraction",))
    bid = ids["QTc"]

    apply_outcome(store, recal, belief_id=bid, delta=-0.05, envelope=env,
                  actor="operator", outcome_ref="review-approved-17")
    apply_outcome(store, recal, belief_id=bid, delta=+0.03, envelope=env,
                  actor="operator", outcome_ref="qc-passed-42")

    trace = provenance_for(store, recal, belief_id=bid)
    assert trace.stored_weight == pytest.approx(0.55), (
        "provenance reported a stored weight that is not the record")
    assert [s.outcome_ref for s in trace.steps] == [
        "review-approved-17", "qc-passed-42"], "steps are out of order"
    assert all(s.actor and s.envelope_version and s.ts for s in trace.steps), (
        "a step could not name its author, its bound, or when it happened")
    assert trace.records_begin_at is not None, (
        "the trace does not say from when it is complete — an empty history "
        "before that date must not read as 'nothing moved this'")
    store.close()
