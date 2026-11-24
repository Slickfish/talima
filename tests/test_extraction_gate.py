"""Packet K.1 — extraction-gate sentinels, written before the gate exists.

WHAT THESE PIN, AND WHAT THEY DELIBERATELY DO NOT
    Every assertion here is about **the gate** — what may ENTER the store.
    None is about the contents of the store.

    That distinction is not stylistic. The packet's bar item reads "no stored
    triple has a … sentence fragment", and as a claim about the store it is
    false today and **must stay false**: the 213 extraction rows and 555
    co-activation rows are beliefs with provenance, some cited in sealed
    lineage records, and this packet forbids touching them. A sentinel
    asserting that claim would either fail forever or force the deletion the
    packet prohibits — the C10 shape, an invariant naming a state the code is
    not allowed to reach. So the sentinels say "cannot enter", never "is not
    present".

THE CONTRACT THESE SENTINELS DEFINE
    Guards precede the change they guard, which means K.1 chooses the shape
    K.2 must implement:

      backend.kb.gate.inspect_term(term, *, evidence_class) -> str | None
          a rejection reason, or None if the term may enter

      backend.kb.gate.BeliefRejected
          raised by TripleStore.add() when the gate refuses

    A raise, not a `None` return, because `add()`'s callers assign the
    returned id and pass it onward; a None would flow into
    `_log_confidence(belief_id=None)` and be recorded as a belief that exists.

    **And the raise alone is not enough.** `agentic_loop.py:547` wraps the
    extraction write in `except Exception: pass` ("persistence is best-effort")
    — so a raising gate would be swallowed there and every rejection would be
    a silent drop, which is precisely the defect class this project keeps
    finding. `test_the_loop_declares_rejections_rather_than_swallowing_them`
    pins that K.2 must fix the caller too, not only add().

PER-CLASS FROM THE START
    Strictness varies by evidence class, and the expectations are written into
    the parametrised table now rather than bolted on after the seeders break.
    A Wikidata object label like `Mercury (planet)` is a legitimate belief;
    the same string from the model's own answer is an extraction artefact.
    Pinning that asymmetry up front is what stops K.2 shipping one rule for
    six routes — the defect the endpoint audit found in the first place.
"""
import hashlib
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore

# The gate does not exist yet. Import defensively so collection succeeds and
# the guards below fail as *tests*, with a readable reason, rather than as a
# collection error that hides the rest of the file.
try:                                                # pragma: no cover
    from backend.kb.gate import BeliefRejected, inspect_term
    GATE_PRESENT = True
except ImportError:                                 # pragma: no cover
    GATE_PRESENT = False

    class BeliefRejected(Exception):
        """Placeholder so this module parses before K.2 lands."""

    def inspect_term(term, *, evidence_class):
        return None


# ---------------------------------------------------------------------------
# The audit's hostile candidates (ENDPOINT_AUDIT.md §3.6), parametrised.
#
# 12 of these 13 are accepted by `_parse_triples` today, and the 13th — the
# empty relation — is rejected there and then accepted by `store.add()`
# anyway. Each entry names the rule that should catch it, so a failure says
# WHICH rule regressed rather than only that something did.
# ---------------------------------------------------------------------------
# K.3 CORRECTION: three candidates were removed from this table —
# `IS A TYPE OF`, `is a`, and the whitespace triple. K.1 filed them here, which
# asserts `pytest.raises(BeliefRejected)`, and that was the wrong frame: the
# packet's rule is that relations are **never rejected**, only normalised.
# They were never going to be rejection cases, and forcing them to fit would
# have been fitting the evidence to the table. They now live in NORMALISED
# below, which asserts what is actually stored.
HOSTILE = [
    ("To address", "addressing",
     "issue of finding the latest news about Iran online using Nexus",
     "leading_infinitive / multi_preposition"),
    ("activation nodes related to 'routes'", "partial or inactive",
     "this session's context", "quotes_or_brackets"),
    ("My understanding", "derived_from", "conversation_history we've shared",
     "pronoun"),
    ("x" * 500, "is_a", "y" * 500, "length"),
    ("['a', 'b', 'c']", "is_a", "['d']", "quotes_or_brackets"),
    ("Traceback (most recent call last): ValueError", "raised", "line 42",
     "quotes_or_brackets"),
    ("The quick brown fox jumps over the lazy dog and keeps going for a while",
     "is a type of", "a very long sentence fragment that is clearly not a concept",
     "multi_preposition / length"),
    ("<script>alert(1)</script>", "is_a", "xss", "markup"),
    ("null", "is_a", "None", "null_literal"),
    ("cat", "", "mammal", "empty_relation"),
]

# Terms that MUST pass — the false-positive guard. Chosen to be hostile to the
# gate rather than to the store: every one contains something a naive rule
# would trip on (prepositions, hyphens, digits, Latin binomials, four words).
LEGITIMATE = [
    "programming_language", "python", "web_framework", "interval_training",
    "theory of relativity", "speed of light", "war of the roses",
    "united states of america", "TAK-441 therapeutic dose",
    "non-small cell lung carcinoma", "Neisseria gonorrhoeae infection",
    "vo2_max", "deepseek-r1", "machine_learning",
    # Two-character concepts, added after the suite caught the gate rejecting
    # them: the inherited `<= 2` bound from `_is_meaningful` would have
    # refused every one of these, and they are ordinary in the technical
    # domains this substrate targets. My first false-positive list had no
    # short concept in it, which is why the rule survived review.
    "AI", "pH", "UK", "T4", "B12",
]

# Evidence classes, per F.3's `classify()`. Strictness differs, and the
# difference is pinned here so K.2 cannot ship one rule for six routes.
STRICT_SOURCES = ("extraction", "hippocampus_consolidation")
LENIENT_SOURCES = ("wikidata", "conceptnet")

# Terms that are artefacts from the model but legitimate from an external
# reference. `Mercury (planet)` is how Wikidata disambiguates; the same string
# in the model's own answer is a parenthetical aside.
CLASS_DEPENDENT = [
    "Mercury (planet)",
    "Ceres (dwarf planet)",
    "Mercury (element)",
]

RECORD_FIELDS = ("subject", "relation", "object", "weight", "source", "created_at")


def _store(tmp_path) -> TripleStore:
    return TripleStore(tmp_path / "kb.db")


def _add(store, subject, relation, object, *, source="extraction", weight=0.9,
         tier=2):
    return store.add(subject=subject, relation=relation, object=object,
                     weight=weight, tier=tier, granularity=Granularity.TOPIC,
                     source=source)


def _fingerprint(store) -> dict:
    """Record-layer fingerprint, keyed by id — Packet A's pattern.

    `tier` and `last_fired` are excluded deliberately: they are prominence,
    and an upsert legitimately moves them (F.1 drew the same line).
    """
    return {t.id: tuple(getattr(t, f) for f in RECORD_FIELDS)
            for t in store.all()}


# ---------------------------------------------------------------------------
# THE HARDEST CONSTRAINT — unmarked, must pass today AND after
# ---------------------------------------------------------------------------

def test_the_gate_never_deletes_an_existing_belief(tmp_path, monkeypatch):
    """A gate rejects at the boundary. It must never reach backwards.

    Seeded with rows the gate WILL reject once it exists — the shapes the
    audit found in the real store — this asserts that an aggressive pass of
    rejectable writes leaves every stored row present and byte-identical on
    the record layer.

    Unmarked on purpose: it passes today (nothing rejects, nothing deletes)
    and must pass after K.2 (things reject, still nothing deletes). It
    therefore pins the gate rather than validating it, and it is the sentinel
    that would catch a "clean up while we're here" drive-by.
    """
    store = _store(tmp_path)

    # Seed with the gate OFF. These rows represent pre-gate history — the 213
    # extraction rows already in the operator's store, written before any of
    # this existed. They cannot be created through the gate, which is the
    # whole point: the gate refuses them at the boundary and must still leave
    # them alone once they are inside. (This is also the first real use of the
    # reversibility switch, which is a decent argument for having one.)
    monkeypatch.setenv("NEXUS_INGEST_GATE", "off")

    # Rows shaped like the real store's 213 extraction rows — every one of
    # them something the gate will refuse to accept in future.
    seeded = [
        _add(store, "My understanding", "derived_from",
             "conversation_history we've shared"),
        _add(store, "To address", "addressing",
             "issue of finding the latest news about Iran online"),
        _add(store, "<script>alert(1)</script>", "is_a", "xss"),
        _add(store, "python", "is_a", "programming_language"),   # a good one
    ]
    before = _fingerprint(store)
    assert len(before) == 4, "fixture did not seed what it claims"

    # NON-VACUITY: the seeded rows must genuinely be gate-rejectable, or this
    # test is about a store full of clean data and proves nothing.
    if GATE_PRESENT:
        assert inspect_term("My understanding", evidence_class="extraction")
        assert inspect_term("<script>alert(1)</script>",
                            evidence_class="extraction")

    # Gate ON for the pass itself — the state the operator actually runs in.
    monkeypatch.setenv("NEXUS_INGEST_GATE", "on")

    # An aggressive pass: re-attempt every hostile candidate. With the gate on
    # these reject; with it off they insert. Either way, nothing may vanish.
    accepted = rejected = 0
    for s, r, o, _rule in HOSTILE:
        try:
            _add(store, s, r, o)
            accepted += 1
        except Exception:
            rejected += 1             # rejection is fine; deletion is not

    # NON-VACUITY, era-independent: the pass must actually have run. Counting
    # both outcomes rather than asserting one keeps this honest before AND
    # after K.2 — today every candidate is accepted, afterwards every one is
    # rejected, and a silent no-op would show up as neither.
    assert accepted + rejected == len(HOSTILE), (
        f"the aggressive pass did not attempt every candidate: "
        f"{accepted} accepted + {rejected} rejected != {len(HOSTILE)}")

    after = _fingerprint(store)
    for tid, record in before.items():
        assert tid in after, f"belief {tid} disappeared during a gate pass"
        assert after[tid] == record, (
            f"belief {tid} was ALTERED during a gate pass:\n"
            f"  before {record}\n  after  {after[tid]}")


# ---------------------------------------------------------------------------
# The hostile set — xfail today
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("subject,relation,object,rule",
                         HOSTILE, ids=[h[3] for h in HOSTILE])
def test_hostile_triples_are_rejected(tmp_path, subject, relation, object, rule):
    """Each hostile candidate must be refused at the boundary.

    The `rule` id is carried so a future failure names which rule regressed,
    not merely that something did.
    """
    store = _store(tmp_path)
    with pytest.raises(BeliefRejected):
        _add(store, subject, relation, object)
    assert store.count() == 0, "a rejected triple was stored anyway"


# ---------------------------------------------------------------------------
# Non-vacuity, BOTH directions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("term", LEGITIMATE)
def test_legitimate_terms_are_not_rejected(tmp_path, term):
    """The other direction, and the one that matters more.

    A gate that rejects everything would satisfy every hostile test above
    while destroying the product. These terms are chosen to be hostile to the
    GATE: prepositional phrases that are real concepts (`theory of
    relativity`, `war of the roses`), hyphenated drug codes, Latin binomials,
    four-word noun phrases. A rule that trips on any of them is wrong, and
    this is where that shows up.
    """
    store = _store(tmp_path)
    tid = _add(store, term, "is_a", "thing")
    assert tid, f"legitimate term rejected: {term!r}"
    assert store.count() == 1
    if GATE_PRESENT:
        assert inspect_term(term, evidence_class="extraction") is None, (
            f"gate rejected a legitimate concept: {term!r}")


def test_the_hostile_and_legitimate_sets_are_actually_different(tmp_path):
    """Guard against the fixtures drifting into agreement.

    If a term appeared in both lists, the two tests above would contradict
    each other and one would be silently wrong. Cheap to assert, and the kind
    of thing that rots quietly.
    """
    hostile_terms = {h[0] for h in HOSTILE} | {h[2] for h in HOSTILE}
    assert not (hostile_terms & set(LEGITIMATE)), (
        f"a term is in both fixtures: {hostile_terms & set(LEGITIMATE)}")
    assert len(HOSTILE) == 10 and len(LEGITIMATE) >= 14


# ---------------------------------------------------------------------------
# Per-class strictness — pinned from the start, not added after the seeders break
# ---------------------------------------------------------------------------

# FLIPPED BY K.2 — strictness now comes from classify(source): parentheses are
# a strict-class rule, so `Mercury (planet)` enters from Wikidata and is
# refused from the model's own answer.
@pytest.mark.parametrize("term", CLASS_DEPENDENT)
def test_external_sources_are_treated_more_leniently(tmp_path, term):
    """`Mercury (planet)` is how Wikidata disambiguates. The identical string
    from the model's own answer is a parenthetical aside.

    Written now rather than after the seeders break, because a gate at add()
    governs Wikidata and ConceptNet too — and one rule for six routes is the
    defect the endpoint audit found. If K.2 ships without per-class
    strictness, this fails, which is the intended outcome.
    """
    store = _store(tmp_path)
    for src in LENIENT_SOURCES:
        assert _add(store, term, "is_a", "thing", source=src), (
            f"external source {src} had a legitimate label rejected: {term!r}")
    for src in STRICT_SOURCES:
        with pytest.raises(BeliefRejected):
            _add(store, term, "is_a", "thing", source=src)


def test_every_ingest_route_passes_through_the_same_chokepoint(tmp_path):
    """Six routes, one floor. The audit's core finding was that the floor
    validated nothing — not that the routes were unguarded individually."""
    store = _store(tmp_path)
    with pytest.raises(BeliefRejected):
        _add(store, "My understanding", "derived_from", "x",
             source="hippocampus_consolidation")
    with pytest.raises(BeliefRejected):
        _add(store, "My understanding", "derived_from", "x",
             source="procedural_promotion")


# ---------------------------------------------------------------------------
# One sentinel per bar item — all phrased about entry, none about the store
# ---------------------------------------------------------------------------

def test_sentence_fragments_cannot_enter(tmp_path):
    store = _store(tmp_path)
    for frag in ("To address", "My understanding", "this session's context",
                 "conversation_history we've shared"):
        with pytest.raises(BeliefRejected):
            _add(store, frag, "is_a", "thing")


# FLIPPED BY K.3 — was xfail(strict): nothing normalised relations except
# seeder.py:190, and only for Wikidata. The extraction prompt ASKS for
# snake_case (extraction.py:67) and nothing enforced it.
def test_relations_are_normalised_on_entry(tmp_path):
    """Five spellings of one predicate must become one.

    Asserted on what is STORED for a new write — never on existing rows,
    which K.3 explicitly does not touch.
    """
    store = _store(tmp_path)
    for spelling in ("is_a", "is a type of", "IS A TYPE OF",
                     "is an example of", "is_an_example_of"):
        _add(store, "cat", spelling, "mammal")
    relations = {t.relation for t in store.all()}
    assert len(relations) == 1, (
        f"five spellings of one predicate stored as {len(relations)}: "
        f"{sorted(relations)}")


def test_markup_cannot_enter(tmp_path):
    store = _store(tmp_path)
    for markup in ("<script>alert(1)</script>", "<img src=x onerror=alert(1)>",
                   "&lt;b&gt;bold&lt;/b&gt;"):
        with pytest.raises(BeliefRejected):
            _add(store, markup, "is_a", "thing")


def test_provider_error_text_cannot_enter(tmp_path):
    store = _store(tmp_path)
    for err in ("Traceback (most recent call last): ValueError",
                "HTTPError: 500 Internal Server Error",
                "ConnectionError: [Errno 111] Connection refused"):
        with pytest.raises(BeliefRejected):
            _add(store, err, "raised", "line 42")


# FLIPPED BY K.2 — was xfail(strict). `_is_meaningful` tested the WHOLE string,
# so "my" was caught and "My understanding" was not; gate.py's `all_filler`
# rule is per token, and the pronoun rule catches this one first.
def test_stopword_check_operates_per_token(tmp_path):
    """The granularity finding, pinned as behaviour rather than as a note."""
    store = _store(tmp_path)
    with pytest.raises(BeliefRejected):
        _add(store, "My understanding", "is_a", "thing")
    if GATE_PRESENT:
        # The single-token case must keep working — a per-token rewrite that
        # lost the original behaviour would be a regression wearing a fix.
        assert inspect_term("my", evidence_class="extraction")
        assert inspect_term("about", evidence_class="extraction")


def test_rejections_are_declared_not_silent(tmp_path):
    """I-6. A rejection must carry a reason a human can read."""
    store = _store(tmp_path)
    with pytest.raises(BeliefRejected) as excinfo:
        _add(store, "<script>alert(1)</script>", "is_a", "xss")
    message = str(excinfo.value)
    assert message.strip(), "rejection carried no reason"
    assert any(k in message.lower() for k in ("markup", "script", "reject")), (
        f"rejection reason does not say what was wrong: {message!r}")


# FLIPPED BY K.2 — the caller now catches BeliefRejected specifically, ahead of
# the blanket handler, and the count reaches the client in `done`.
def test_the_loop_declares_rejections_rather_than_swallowing_them():
    """The catch that would have turned a declared rejection back into a
    silent one.

    K.1 pinned this by inspecting the source, because at the time there was no
    rejection to observe. Now there is, so it is asserted BEHAVIOURALLY: drive
    a turn whose extraction emits one clean triple and one the gate refuses,
    and read what the client actually receives. Source-grepping is level-3
    evidence and this project has been bitten by it repeatedly (pack §15.4).
    """
    import asyncio, json
    from backend.loop.agentic_loop import run_turn
    from backend.loop.state import NexusLoopState
    from backend.kb.retriever import TripleRetriever

    class _Provider:
        def __init__(self): self.calls = 0
        async def stream_chat(self, model, messages, num_ctx=8192):
            self.calls += 1
            if self.calls == 1:
                yield "Python is a programming_language."
            else:
                # One clean triple, one the gate must refuse.
                yield json.dumps([
                    {"subject": "python", "relation": "is_a",
                     "object": "programming_language", "confidence": 0.9},
                    {"subject": "My understanding", "relation": "derived_from",
                     "object": "conversation_history", "confidence": 0.9},
                ])
        async def get_embedding(self, text): return [0.0] * 768
        async def list_models(self): return []
        async def health(self): return True

    import tempfile
    store = TripleStore(Path(tempfile.mkdtemp()) / "kb.db")
    frames = []

    async def go():
        async for f, _s in run_turn(NexusLoopState(), "tell me about python",
                                    retriever=TripleRetriever(store),
                                    provider=_Provider()):
            frames.append(f)
    asyncio.run(go())

    done = next(json.loads(f.split("data: ", 1)[1].strip())
                for f in reversed(frames) if f.startswith("event: done\n"))
    gate = done["extraction_gate"]

    # NON-VACUITY: the turn must actually have extracted both, or "1 rejected"
    # could mean the extractor simply produced less.
    assert gate["extracted"] == 2, gate
    assert gate["rejected"] == 1, gate
    assert gate["stored"] == 1, gate
    assert any("pronoun" in r for r in gate["reasons"]), gate

    # ...and the clean one really is in the store, so the gate refused the
    # right one rather than refusing arbitrarily.
    assert [t.subject for t in store.all()] == ["python"]
    store.close()


# ---------------------------------------------------------------------------
# K.3 — relation normalisation. Never rejection: a relation vocabulary is
# open-ended, and refusing an unrecognised predicate would refuse legitimate
# new ones. (subject, relation, object) -> what is actually STORED.
# ---------------------------------------------------------------------------
NORMALISED = [
    # The five spellings of one predicate the real store holds.
    (("cat", "is_a", "mammal"),               ("cat", "is_a", "mammal")),
    (("cat", "is a type of", "mammal"),       ("cat", "is_a", "mammal")),
    (("cat", "IS A TYPE OF", "mammal"),       ("cat", "is_a", "mammal")),
    (("cat", "is an example of", "mammal"),   ("cat", "is_a", "mammal")),
    (("cat", "is_an_example_of", "mammal"),   ("cat", "is_a", "mammal")),
    (("cat", "is", "mammal"),                 ("cat", "is_a", "mammal")),
    # Whitespace: stripped and collapsed, never slugged.
    (("  padded  ", "  spaced relation  ", "  obj  "),
     ("padded", "spaced_relation", "obj")),
    (("theory  of   relativity", "explains", "gravity"),
     ("theory of relativity", "explains", "gravity")),
    # Unmappable: slugged and STORED, not refused.
    (("python", "was created by", "guido"),   ("python", "was_created_by", "guido")),
    (("x", "partial or inactive", "y"),       None),   # term rules still apply
]


@pytest.mark.parametrize("given,expected", NORMALISED,
                         ids=[g[1] for g, _e in NORMALISED])
def test_relations_and_whitespace_are_normalised_on_entry(tmp_path, given, expected):
    """What goes in versus what is stored.

    `expected is None` marks a triple the TERM rules refuse for an unrelated
    reason — normalisation does not rescue a bad term, and a test that let it
    would be asserting the gate is weaker than it is.
    """
    store = _store(tmp_path)
    s_in, r_in, o_in = given
    if expected is None:
        with pytest.raises(BeliefRejected):
            _add(store, s_in, r_in, o_in)
        store.close()
        return
    _add(store, s_in, r_in, o_in)
    row = store.all()[0]
    assert (row.subject, row.relation, row.object) == expected
    store.close()


def test_unmappable_relations_are_stored_never_rejected(tmp_path):
    """The rule that keeps the gate out of ontology.

    An alias map is a claim that two predicates mean the same thing. Anything
    it does not know is stored as its own slug — the gate normalises spelling,
    it does not decide what predicates may exist.
    """
    store = _store(tmp_path)
    for rel in ("orbits", "was_created_by", "inhibits", "phosphorylates",
                "has_half_life", "binds to"):
        _add(store, "alpha_thing", rel, "beta_thing")
    stored = {t.relation for t in store.all()}
    assert stored == {"orbits", "was_created_by", "inhibits", "phosphorylates",
                      "has_half_life", "binds_to"}
    store.close()


def test_subclass_and_instance_are_deliberately_not_aliased(tmp_path):
    """What is NOT in the alias map matters more than what is.

    Wikidata distinguishes P279 (subclass of) from P31 (instance of). Folding
    them into `is_a` would destroy information the source took care to record
    — a class is not an instance — so both survive as themselves. This is
    pinned because it is the kind of "obvious tidy-up" a later reader would
    make without noticing what it costs.
    """
    store = _store(tmp_path)
    _add(store, "mammal", "subclass_of", "animal", source="wikidata")
    _add(store, "felix", "instance_of", "cat", source="wikidata")
    assert {t.relation for t in store.all()} == {"subclass_of", "instance_of"}
    store.close()


def test_terms_are_never_slugged(tmp_path):
    """A term is a NAME. Mangling a name is a provenance loss of exactly the
    kind K.0.5 rejected for markup — the stored belief would no longer say
    what the source said."""
    store = _store(tmp_path)
    _add(store, "theory of relativity", "explains", "speed of light",
         source="wikidata")
    row = store.all()[0]
    assert row.subject == "theory of relativity"    # not theory_of_relativity
    assert row.object == "speed of light"
    store.close()


# ---------------------------------------------------------------------------
# THE COLLISION — normalisation can merge a new triple onto an existing row
# ---------------------------------------------------------------------------

def test_normalisation_collision_merges_and_never_degrades(tmp_path):
    """The risk K.0.4 flagged, asserted rather than argued.

    Normalising `is a type of` to `is_a` can make a NEW triple collide with an
    EXISTING row under `UNIQUE(subject, relation, object, source)`. The upsert
    then merges: `weight = MAX`, `tier = MIN`, `last_fired = excluded`.

    The behaviour is asymmetric, and the asymmetry is the point:
      * a LOWER-weight collision leaves the stored row completely untouched,
        so a garbage extraction cannot degrade a good belief;
      * a HIGHER-weight collision RAISES the row's weight and prominence.

    So "nothing already stored is touched" is not strictly true under
    normalisation, and this test says so out loud rather than letting the
    claim stand unqualified. Nothing is deleted and nothing is degraded —
    which is the honest form of the constraint.
    """
    store = _store(tmp_path)
    original = _add(store, "cat", "is_a", "mammal", weight=0.9)
    store.conn.execute("UPDATE triples SET tier = 1 WHERE id = ?", (original,))
    store.conn.commit()
    before = store.get_by_id(original)
    assert (before.weight, before.tier) == (0.9, 1)          # non-vacuity

    # A LOWER-weight collision under a different spelling.
    merged = _add(store, "cat", "is a type of", "mammal", weight=0.05)
    after_low = store.get_by_id(original)
    assert merged == original, "normalisation did not collide as expected"
    assert store.count() == 1, "the collision inserted a second row"
    assert (after_low.weight, after_low.tier) == (0.9, 1), (
        "a low-weight collision degraded the stored belief")

    # A HIGHER-weight collision — this DOES move the stored row's weight.
    _add(store, "cat", "IS A TYPE OF", "mammal", weight=0.99)
    after_high = store.get_by_id(original)
    assert after_high.weight == 0.99

    # TIER is narrower than I first assumed, and the difference matters.
    # `MIN(tier)` promotes only when the INCOMING tier is more prominent than
    # the stored one — it is not raised by weight. The collision above arrived
    # at the default tier 2 against a stored tier 1, so the row did not move:
    assert after_high.tier == 1, "tier moved without a more prominent incoming tier"
    # ...and it moves only when a genuinely more prominent write collides:
    _add(store, "cat", "is", "mammal", weight=0.99, tier=0)
    assert store.get_by_id(original).tier == 0
    # Extraction always writes tier=2 (agentic_loop), so in practice an
    # extraction collision can only promote a row already at tier 3.
    # ...but the RECORD identity is untouched: same row, same text, same source.
    assert (after_high.id, after_high.subject, after_high.relation,
            after_high.object, after_high.source) == (
        original, "cat", "is_a", "mammal", "extraction")
    assert store.count() == 1
    store.close()


def test_no_retroactive_normalisation_of_existing_rows(tmp_path, monkeypatch):
    """Rows written before K.3 keep their spelling.

    Rewriting them is a separate decision with its own record, as Packet B's
    purge and E.4's declined write both were. Seeded with the gate off, since
    those spellings cannot be written through it any more — the same technique
    K.2's never-deletes sentinel needed.
    """
    store = _store(tmp_path)
    monkeypatch.setenv("NEXUS_INGEST_GATE", "off")
    # Pre-K.3 history: five spellings, five rows.
    for rel in ("is_a", "is a type of", "is", "is an example of",
                "is_an_example_of"):
        store.conn.execute(
            "INSERT INTO triples (subject, relation, object, weight, tier, "
            "granularity, source, fire_count, created_at, last_fired) "
            "VALUES ('legacy', ?, 'thing', 0.5, 2, 'topic', 'extraction', 0, 1, 1)",
            (rel,))
    store.conn.commit()
    assert store.count() == 5                                # non-vacuity

    monkeypatch.setenv("NEXUS_INGEST_GATE", "on")
    # A new write normalises, and touches none of the five.
    _add(store, "fresh", "is a type of", "thing")

    legacy = {t.relation for t in store.all() if t.subject == "legacy"}
    assert legacy == {"is_a", "is a type of", "is", "is an example of",
                      "is_an_example_of"}, (
        "existing rows were retroactively normalised — that is a separate "
        "decision and it was not taken here")
    assert {t.relation for t in store.all() if t.subject == "fresh"} == {"is_a"}
    store.close()


# ---------------------------------------------------------------------------
# K.4 — provider and runtime error text, broadened
# ---------------------------------------------------------------------------
PROVIDER_ERRORS = [
    "Traceback (most recent call last)", 'File "x.py", line 42',
    "HTTP/1.1 500 Internal Server Error", "503 service unavailable",
    "Connection refused", "connection reset by peer", "timed out",
    "SSL error", "certificate verify failed", "name or service not known",
    "model not found", "context length exceeded", "rate limit exceeded",
    "invalid api key", "insufficient quota", "CUDA out of memory",
    "unable to load model", "ValueError", "RuntimeException", "parse_error",
    "I'm sorry, but I cannot",
]

# The false-positive guard for THIS rule specifically. `terror` is here
# because the first draft used `\w*error`, which matched it — caught by
# measurement, not by review, which is why the list exists.
ERROR_LOOKALIKES = [
    "terror", "terrorism", "Terrorism", "errand", "mirror",
    "error_correcting_code", "timeout_setting", "connection_pooling",
    "http_protocol", "model_registry", "api_design", "certificate_authority",
    "memory_consolidation",
]


@pytest.mark.parametrize("text", PROVIDER_ERRORS)
def test_provider_and_runtime_error_text_cannot_enter(tmp_path, text):
    """An extractor handed an error message extracts triples from it.

    When something upstream fails, the model is given the failure as prose and
    dutifully turns it into beliefs. `Traceback` was never going to be the
    only shape, so the rule covers python frames, HTTP status lines, transport
    failures and the phrases Ollama / LM Studio / OpenAI-compatible servers
    return in an error payload.
    """
    store = _store(tmp_path)
    with pytest.raises(BeliefRejected):
        _add(store, text, "is_a", "thing")
    store.close()


@pytest.mark.parametrize("term", ERROR_LOOKALIKES)
def test_error_lookalikes_are_not_rejected(tmp_path, term):
    """The direction that keeps the rule honest.

    A pattern broad enough to catch every error shape will catch real words
    unless it is anchored on what an error actually looks like: the bare word,
    a CamelCase class name, or snake_case. `terror` is a noun.
    """
    store = _store(tmp_path)
    assert _add(store, term, "is_a", "thing"), f"legitimate term rejected: {term!r}"
    store.close()


def test_error_text_is_rejected_from_every_source(tmp_path):
    """Unlike the shape rules, this one is universal.

    A stack trace is not a belief about the world whatever produced it, so
    external sources get no leniency here — only for the natural-language
    shapes (parentheses, quotes) where a Wikidata label is legitimately
    different from a model's aside.
    """
    store = _store(tmp_path)
    for src in ("wikidata", "conceptnet", "offline_fixture",
                "procedural_promotion", "hippocampus_consolidation"):
        with pytest.raises(BeliefRejected):
            _add(store, "Connection refused", "is_a", "thing", source=src)
    store.close()


def test_the_switch_reverses_the_whole_packet_not_half_of_it(tmp_path, monkeypatch):
    """`NEXUS_INGEST_GATE=off` must restore prior behaviour COMPLETELY.

    The first implementation gated only rejection, leaving normalisation on —
    so an operator flipping the switch got neither the old behaviour nor the
    new one. Found while exercising the verification script, whose fixture
    came out already-normalised with the gate supposedly off. A switch that
    reverses half a packet is worse than no switch, because the label lies.
    """
    store = _store(tmp_path)
    monkeypatch.setenv("NEXUS_INGEST_GATE", "off")
    store.add(subject="  padded  ", relation="is a type of", object="  obj  ",
              weight=0.9, tier=2, granularity=Granularity.TOPIC,
              source="extraction")
    row = store.all()[0]
    assert (row.subject, row.relation, row.object) == \
        ("  padded  ", "is a type of", "  obj  "), (
            "the switch left normalisation running — prior behaviour was "
            "neither normalised nor gated")

    monkeypatch.setenv("NEXUS_INGEST_GATE", "on")
    store.add(subject="  padded2  ", relation="is a type of", object="  obj  ",
              weight=0.9, tier=2, granularity=Granularity.TOPIC,
              source="extraction")
    fresh = [t for t in store.all() if t.subject == "padded2"]
    assert len(fresh) == 1 and fresh[0].relation == "is_a"
    store.close()
