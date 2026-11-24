"""Packet F.5 — reinforcement by citation, not by retrieval.

The loop F.5 closes: `store.fire()` resets `last_fired`, and therefore
`current_weight`, to the stored high-water mark. Firing every retrieved belief
every turn meant a belief was retrieved BECAUSE it was prominent (retrieval
ranks tier ascending) and then strengthened FOR having been retrieved. No
evidence entered that loop at any point.

This is why the 555 co-activation rows never decayed. F.3 made them *able* to
sink by removing tier 0's immunity; F.5 is what stops them re-selecting
themselves back to the top before they can.

Note what is NOT touched: `store.fire()` writes `triples.fire_count`, which
nothing reads for a decision. I-1's counter is `SynapticEdge.fire_count`
(neocortex.py:86) — a different object on a different layer.
"""
import asyncio
from pathlib import Path

import pytest

from backend.kb.citation import (
    DEFAULT_REINFORCE_MODE, cited_ids, is_cited, reinforce_mode,
)
from backend.kb.granularity import Granularity
from backend.kb.retriever import RetrievedTriple, TripleRetriever
from backend.kb.tiered_store import LOWEST_TIER, TripleStore
from backend.loop.agentic_loop import run_turn
from backend.loop.state import NexusLoopState

DAY = 86400.0
T0 = 1_700_000_000.0


class _Provider:
    """Two stream_chat calls per turn: the answer, then extraction."""

    def __init__(self, answer: str):
        self.answer = answer
        self.calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls += 1
        if self.calls == 1:
            for tok in self.answer.split(" "):
                yield tok + " "
        else:
            yield "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _seed(store, subject, object_, *, idle_days, weight=0.9, tier=0,
          source="wikidata"):
    tid = store.add(subject=subject, relation="is_a", object=object_,
                    weight=weight, tier=tier, granularity=Granularity.TOPIC,
                    source=source)
    store.conn.execute("UPDATE triples SET last_fired = ? WHERE id = ?",
                       (T0 - idle_days * DAY, tid))
    store.conn.commit()
    return tid


def _turn(store, query, answer):
    """Drive one full turn and return the final `done` frame payload."""
    import json
    retriever = TripleRetriever(store)
    frames = []

    async def drive():
        async for frame, _state in run_turn(
            NexusLoopState(), query, retriever=retriever,
            provider=_Provider(answer),
        ):
            frames.append(frame)

    asyncio.run(drive())
    for frame in reversed(frames):
        if frame.startswith("event: done\n"):
            return json.loads(frame.split("data: ", 1)[1].strip())
    raise AssertionError("no done frame emitted")


# ---------------------------------------------------------------------------
# THE SENTINEL
# ---------------------------------------------------------------------------

def test_uncited_retrieval_does_not_reset_the_decay_clock(tmp_path: Path):
    """The property the whole packet exists for.

    Two beliefs are retrieved by the same query. The answer uses one and not
    the other. Only the used one may have its decay clock reset.
    """
    store = TripleStore(tmp_path / "kb.db")
    used   = _seed(store, "python", "programming_language", idle_days=40)
    unused = _seed(store, "python", "snake_genus",          idle_days=40)

    before = {t.id: t.last_fired for t in store.all()}

    payload = _turn(store, "tell me about python",
                    "Python is a programming_language used widely.")

    after = {t.id: t.last_fired for t in store.all()}

    # Non-vacuity first: the turn must actually have retrieved BOTH, or the
    # comparison below is about a belief that was never a candidate.
    assert payload["reinforcement"]["injected"] >= 2, payload
    assert payload["reinforcement"]["mode"] == "citation"

    assert after[used] > before[used],   "a cited belief was not reinforced"
    assert after[unused] == before[unused], (
        "an uncited belief had its decay clock reset merely for being "
        "retrieved — the feedback loop is still closed")
    assert payload["reinforcement"]["reinforced"] == 1
    assert payload["reinforcement"]["suppressed"] >= 1
    store.close()


def test_suppression_is_declared_in_the_turn_telemetry(tmp_path: Path):
    """I-6 — an operator must be able to see that beliefs were shown to the
    model and deliberately not strengthened, without inferring it."""
    store = TripleStore(tmp_path / "kb.db")
    _seed(store, "python", "programming_language", idle_days=10)
    _seed(store, "python", "snake_genus", idle_days=10)

    payload = _turn(store, "tell me about python",
                    "Python is a programming_language.")
    r = payload["reinforcement"]

    # AU — the flag's producer is declared beside the counts it produced.
    # B-43 — `skipped` is the attempts the freeze refused; 0 under live.
    assert set(r) == {"mode", "citation_test", "injected", "cited",
                      "reinforced", "skipped", "suppressed"}
    assert r["injected"] == r["reinforced"] + r["skipped"] + r["suppressed"]
    assert r["skipped"] == 0
    assert r["cited"] == r["reinforced"]          # in citation mode, live, they agree
    store.close()


def test_prior_behaviour_is_recoverable_by_configuration(tmp_path, monkeypatch):
    """`NEXUS_REINFORCE_MODE=retrieval` restores the pre-F.5 loop exactly, so
    a before/after can be measured on one store without a code edit."""
    monkeypatch.setenv("NEXUS_REINFORCE_MODE", "retrieval")
    store = TripleStore(tmp_path / "kb.db")
    unused = _seed(store, "python", "snake_genus", idle_days=40)
    before = store.get_by_id(unused).last_fired

    payload = _turn(store, "tell me about python",
                    "Python is a programming_language.")

    assert payload["reinforcement"]["mode"] == "retrieval"
    assert payload["reinforcement"]["suppressed"] == 0
    assert store.get_by_id(unused).last_fired > before
    store.close()


def test_off_mode_reinforces_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_REINFORCE_MODE", "off")
    store = TripleStore(tmp_path / "kb.db")
    cited = _seed(store, "python", "programming_language", idle_days=40)
    before = store.get_by_id(cited).last_fired

    payload = _turn(store, "tell me about python",
                    "Python is a programming_language.")

    assert payload["reinforcement"]["reinforced"] == 0
    assert store.get_by_id(cited).last_fired == before
    store.close()


def test_unknown_mode_falls_back_to_the_safe_default(monkeypatch):
    monkeypatch.setenv("NEXUS_REINFORCE_MODE", "banana")
    assert reinforce_mode() == DEFAULT_REINFORCE_MODE == "citation"


# ---------------------------------------------------------------------------
# The citation record
# ---------------------------------------------------------------------------

def test_lineage_record_distinguishes_cited_from_merely_injected(tmp_path: Path):
    """Before F.5 the record could not answer "did the answer use this?".

    `injected` was built straight from `hits`, so it recorded what was placed
    in front of the model and nothing about what the model did with it. The
    flag rides inside the record payload, so it is covered by `record_hash`
    and by the chain seal that follows.
    """
    from backend.memory.lineage import LineageStore
    store = TripleStore(tmp_path / "kb.db")
    lineage = LineageStore(tmp_path / "kb.db")
    used   = _seed(store, "python", "programming_language", idle_days=5)
    unused = _seed(store, "python", "snake_genus", idle_days=5)

    retriever = TripleRetriever(store)

    async def drive():
        async for _f, _s in run_turn(
            NexusLoopState(), "tell me about python", retriever=retriever,
            provider=_Provider("Python is a programming_language."),
            lineage=lineage,
        ):
            pass

    asyncio.run(drive())

    rec = lineage.recent(limit=1)[0]
    flags = {i["belief_id"]: i["cited"] for i in rec.injected}
    assert flags[used] is True
    assert flags[unused] is False
    # The flag is inside the hashed payload, so the record still verifies —
    # i.e. citation is covered by the tamper-evidence, not bolted beside it.
    assert lineage.verify_record(rec.lineage_id)["record_intact"] is True
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# The definition itself
# ---------------------------------------------------------------------------

class _T:
    # AU — a triple carries a relation, and the citation test now reads it
    # (a substrate relation never cites). A claim relation keeps these tests
    # exercising exactly what they always did: the term match.
    def __init__(self, subject, object_, tid=1, relation="is_a"):
        self.subject, self.object, self.id = subject, object_, tid
        self.relation = relation


def test_citation_requires_both_ends_not_either():
    """An answer that mentions the subject has talked about the topic; one
    that mentions subject AND object has used the belief."""
    t = _T("python", "programming_language")
    assert is_cited(t, "Python is a programming language.") is True
    assert is_cited(t, "Python is popular.") is False           # subject only
    assert is_cited(t, "It is a programming language.") is False  # object only
    assert is_cited(t, "") is False


def test_citation_matches_underscored_and_spaced_forms():
    t = _T("machine_learning", "subfield_of_ai")
    assert is_cited(t, "machine learning is a subfield of ai") is True
    assert is_cited(t, "machine_learning is a subfield_of_ai") is True


def test_citation_does_not_match_substrings():
    """`cat` must not be cited by `catastrophe` — whole tokens only."""
    t = _T("cat", "mammal")
    assert is_cited(t, "A catastrophe befell the mammalian order.") is False
    assert is_cited(t, "The cat is a mammal.") is True


def test_citation_is_conservative_on_paraphrase():
    """A known and deliberate gap, in the forgiving direction.

    A belief used in the model's own words scores uncited, so it decays
    slightly rather than being credited. That is self-correcting — the belief
    keeps its full stored weight and the next turn that needs it will
    retrieve and cite it — whereas a false citation would silently reopen the
    feedback loop. Pinned so the asymmetry is a decision, not an accident.
    """
    t = _T("python", "programming_language")
    assert is_cited(t, "Python is a coding language.") is False


def test_cited_ids_selects_from_the_retrieved_set(tmp_path: Path):
    store = TripleStore(tmp_path / "kb.db")
    a = _seed(store, "python", "programming_language", idle_days=1)
    b = _seed(store, "python", "snake_genus", idle_days=1)
    hits = [RetrievedTriple(triple=store.get_by_id(i), score=0.5) for i in (a, b)]
    assert cited_ids(hits, "python is a programming_language") == {a}
    assert cited_ids(hits, "nothing relevant here") == set()
    store.close()


# ---------------------------------------------------------------------------
# The payoff — the two packets together
# ---------------------------------------------------------------------------

def test_a_never_cited_belief_actually_sinks_across_turns(tmp_path: Path):
    """F.3 made a belief ABLE to sink; F.5 is what lets it.

    Retrieved on every one of ten turns and never used, a co-activation row
    must still reach the floor. Under the pre-F.5 loop its clock was reset by
    each of those retrievals and it would sit at tier 0 forever.
    """
    store = TripleStore(tmp_path / "kb.db")
    tid = _seed(store, "python", "scaffolding_term", idle_days=0, weight=0.971,
                tier=0, source="hippocampus_consolidation")

    for turn in range(1, 11):
        # Each turn the belief is retrieved (the query matches it) but the
        # answer never uses it. Age the clock by three days per turn.
        store.conn.execute(
            "UPDATE triples SET last_fired = last_fired - ? WHERE id = ?",
            (3 * DAY, tid))
        store.conn.commit()
        payload = _turn(store, "tell me about python",
                        "I have nothing useful to say about that.")
        assert payload["reinforcement"]["reinforced"] == 0

    store.rebalance()
    final = store.get_by_id(tid)
    assert final.tier == LOWEST_TIER, f"never-cited belief held at tier {final.tier}"
    assert final.current_weight() == pytest.approx(0.0)
    # I-2 — sunk, not destroyed.
    assert final.weight == 0.971 and final.source == "hippocampus_consolidation"
    assert store.get_by_id(tid) is not None
    store.close()
