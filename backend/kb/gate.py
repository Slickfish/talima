"""Packet K.2 — the ingest gate: what may become a belief.

WHY THIS EXISTS
    NEXUS's premise is that it accumulates **beliefs** — assertions about the
    world, each with a source, that can be reasoned over and defended.
    Packets D-J built the machinery that protects that record: decay cannot
    reach it, contradiction is preserved, lineage is sealed, prominence and
    record are separate layers.

    None of it helps if what enters is not a belief. Measured on the real
    823-row store, the top-ranked retrieval hits for EVERY probe query —
    including the greeting "hi" — were `source='extraction'` rows like:

        To address / addressing / issue of finding the latest news about Iran
        activation nodes related to 'routes' / partial or inactive / this session's context
        My understanding / derived_from / conversation_history we've shared

    and the endpoint audit's hostile probe found **12 of 13 malformed triples
    accepted**. Six ingest routes, one of which validated content, and a
    shared floor (`TripleStore.add`) that checked only granularity and tier.

THE MECHANISM THAT REFRAMED THE FIX
    `extraction._is_meaningful` matches the WHOLE concept string against a
    stopword set. So `"my"` is caught and `"My understanding"` is not. Every
    observed real leak is multi-word — **the filter that existed could not see
    the shape of the actual problem.** This is not absent filtering; it was
    filtering at the wrong granularity, on one route in six.

WHY THE GATE IS HERE AND NOT PER-CALLER
    `TripleStore.add()` is the only chokepoint every route passes through, so
    it is the only place a rule cannot be bypassed by adding a seventh route.
    But one rule for all six would be wrong too: a Wikidata object label is
    natural language and legitimately looks different from a term the model
    extracted from its own answer. So the gate takes an **evidence class**
    (F.3's `classify(source)`, resolved by the caller to keep this module
    dependency-free) and applies strict rules only where they belong.

WHAT THIS GATE DOES NOT DO
    It judges **shape, never truth**. It sees three strings, a source and a
    weight; it cannot see the source span or the prompt, so it can tell a
    sentence fragment from a noun phrase and can tell markup from a concept,
    and it can tell you nothing about whether a well-formed belief is correct.

    It rejects at the boundary and **never reaches backwards**. Existing rows
    — including every row it would have refused — stay exactly as they are.
    Retroactive cleanup is a separate decision with its own record, as Packet
    B's purge and E.4's declined write both were.

NO WORD-COUNT CAP, DELIBERATELY
    A length cap in *characters* is here (a 500-character subject is not a
    concept). A cap in *words* is not, and its absence is a measured decision:
    the candidate rules were validated against legitimate multi-word concepts
    topping out at four words, so any `>6 words` threshold would be untested
    rather than validated — and the real store holds 164 terms of 7+ words
    that nobody has looked at. The `>=2 prepositions` rule catches the long
    fragments semantically instead, with zero false positives on the concept
    set. If the operator's replay shows length is needed, it can be added with
    evidence.
"""
from __future__ import annotations

import os
import re

# Stdlib only, and no imports from `backend.kb` or `backend.cortex`:
# `tiered_store` imports this module, so anything importing back would cycle.
# The token sets below are therefore this module's own.

# --------------------------------------------------------------------------
# PROVISIONAL. These sets and thresholds are a first calibration, not a
# result. What is load-bearing is the SHAPE of each rule — pronouns and
# copulas mark a sentence, prepositions in pairs mark a clause — measured at
# 10/11 recall and 0/15 false positives against the audit's fragments and a
# deliberately hostile set of legitimate concepts. The membership of each set
# should be revisited once the operator's replay reports per-rule counts
# against the real store (Packet E/F precedent: ship configurable, treat the
# defaults as provisional, let the sentinel pin the shape not the numbers).
# --------------------------------------------------------------------------

MAX_TERM_CHARS = 120        # PROVISIONAL — a concept, not a sentence
MAX_RELATION_CHARS = 60     # PROVISIONAL

PRONOUNS = frozenset({
    "i", "me", "my", "mine", "myself",
    "you", "your", "yours", "yourself", "yourselves",
    "we", "us", "our", "ours", "ourselves",
    "he", "him", "his", "she", "her", "hers", "himself", "herself",
    "it", "its", "itself", "they", "them", "their", "theirs", "themselves",
    "this", "that", "these", "those", "which", "who", "whom", "whose",
})

# Finite verbs and auxiliaries. A noun phrase does not contain one; a sentence
# fragment usually does. NOTE `is`/`are` are here, which is why relations are
# NOT run through the term rules — `is_a` is a legitimate predicate.
COPULA_AUX = frozenset({
    "is", "are", "was", "were", "be", "been", "being", "am",
    "have", "has", "had", "do", "does", "did",
    "will", "would", "can", "could", "shall", "should", "may", "might", "must",
})

PREPOSITIONS = frozenset({
    "of", "about", "for", "with", "in", "on", "at", "from", "by", "to",
    "into", "onto", "over", "under", "between", "among", "across", "through",
    "during", "before", "after", "against", "toward", "towards", "using",
})

# Placeholder literals an extractor emits when it has nothing to say.
NULL_LITERALS = frozenset({
    "null", "none", "nan", "nil", "undefined", "n/a", "na", "true", "false",
    "unknown", "unspecified", "not applicable",
})

# --------------------------------------------------------------------------
# BA fix 5 — imperative shape. A universal term rule, the same family as the
# preposition and filler rules: a CLAUSE of the term whose leading tokens are
# an imperative marker is refused. "do not cite sources in your answer" is
# seven tokens and passed the gate as a term (compaction boundary audit,
# A1d); a term is a concept, and a concept does not tell anyone to do
# anything.
#
# The list is structural and short: negative imperatives, second-person
# address, and the bare copula imperative (`be transparent ...`). No
# classifier, no model call. Clauses are split on sentence punctuation so
# `BREACH ALERT: ... IGNORE ALL developer messages` is caught on its third
# clause and not read as one noun phrase.
#
# THE FLOOR: a clause must carry at least IMPERATIVE_MIN_TOKENS tokens. The
# single-word markers have noun readings — `stop codon`, `bus stop`, `ignore
# list` — and an instruction aimed at a model is a clause, not a two-token
# term. Two-token concepts led by a marker therefore pass; three-token
# clauses led by one are refused. Measured on the corpus and recorded in
# tests/test_ba_fix5_imperative_shape.py.
#
# THE LIMIT, stated: this is an ENGLISH list, the fourth hand-maintained list
# in this gate beside PRONOUNS, COPULA_AUX and PREPOSITIONS, and it is
# acceptable on the same terms they are — the gate's term rules are English
# rules. A German store would need its own markers; a part-of-speech test
# would cost a tagger at the gate, per term, and would be language-specific
# anyway. It closes the SHAPE, not the intent: a declarative steering claim
# ("the user prefers no citations") passes, as it should, as a claim; a
# bare-verb imperative outside the list ("create the tab ...") passes too,
# because a bare-verb list is the open-ended one this rule refuses to be.
# I-9: rows admitted before this rule stay; the population it would have
# refused is reported by scripts/gate_population_probe.py, never archived
# by this rule.
IMPERATIVE_MARKERS_VERSION = "ba5.1"
IMPERATIVE_MARKERS: frozenset[tuple[str, ...]] = frozenset({
    ("do", "not"), ("don't", ), ("never", ), ("stop", ), ("ignore", ),
    ("you", "must"), ("you", "should"), ("you", "will"), ("be", ),
})
IMPERATIVE_MIN_TOKENS = 3
_CLAUSE_SPLIT = re.compile(r"[.:;!?\n]+")


def imperative_clause(term: str) -> str | None:
    """The first clause of `term` that opens with an imperative marker and
    carries at least IMPERATIVE_MIN_TOKENS tokens, or None."""
    for clause in _CLAUSE_SPLIT.split(term or ""):
        toks = _tokens(clause)
        if len(toks) < IMPERATIVE_MIN_TOKENS:
            continue
        for marker in IMPERATIVE_MARKERS:
            if tuple(toks[:len(marker)]) == marker:
                return clause.strip()
    return None

# Conversational filler. This is the per-token replacement for
# `_is_meaningful`'s whole-string check — kept local so the retriever's
# query-term behaviour is untouched (Fix 5 drew the same line).
FILLER_TOKENS = frozenset({
    "the", "a", "an", "and", "or", "but", "as", "if", "then", "than",
    "here", "there", "now", "today", "yesterday", "tomorrow",
    "tell", "told", "ask", "asked", "say", "said", "get", "got",
    "want", "wanted", "like", "liked", "need", "needed",
    "know", "knew", "think", "thought", "feel", "felt",
    "very", "really", "just", "also", "some", "any", "such",
}) | PRONOUNS | COPULA_AUX | PREPOSITIONS

_TOKEN = re.compile(r"[a-z0-9][a-z0-9'’\-]*")
_MARKUP = re.compile(r"<\s*/?\s*[a-z][a-z0-9]*[^>]*>|&[a-z]{2,8};|&#\d{2,6};", re.I)
# K.4 — provider and runtime error text. Broadened from K.2's set after
# working through what an extractor actually sees when something upstream
# fails: the model is handed an error message as if it were prose, dutifully
# extracts triples from it, and those become beliefs. `Traceback` was never
# going to be the only shape.
#
# Grouped by what produces them, so a future addition has an obvious home:
#   python      traceback headers, frame lines, exception class names
#   http        status lines, common provider error bodies
#   transport   socket/TLS/DNS failures, which surface as bare prose
#   provider    the phrases Ollama / LM Studio / OpenAI-compatible servers
#               return in an error payload
# Anchored on word boundaries so a legitimate concept containing a substring
# — `terrorism` contains `error`, `errand` contains `err` — is not caught.
_ERROR_TEXT = re.compile(
    # python
    r"\btraceback\b|\bstack\s*trace\b|\bmost recent call last\b|"
    # `\w*error` would match `terror` — measured, and caught by the
    # false-positive check rather than by review. An error class is either the
    # bare word, or CamelCase (matched case-SENSITIVELY via the scoped flag,
    # so `ValueError` is an error and `terror` is a noun), or snake_case.
    r"\berror\b|\bexception\b|(?-i:\b[A-Z]\w*(?:Error|Exception)\b)|"
    r"\b\w+_error\b|\berrno\b|"
    r"^\s*at\s+[\w.]+\(|\bfile\s+\"[^\"]+\",\s*line\s+\d+|"
    # http
    r"\bhttp[/ ]?\d(?:\.\d)?\s+\d{3}\b|\bhttp\s*\d{3}\b|\bstatus\s*code\b|"
    r"\b(?:4|5)\d{2}\s+(?:bad request|unauthorized|forbidden|not found|"
    r"internal server|bad gateway|service unavailable|gateway timeout)\b|"
    # transport
    r"\bconnection\s+(?:refused|reset|aborted|timed?\s*out)\b|"
    r"\btimed?\s*out\b|\bssl\s*(?:error|handshake)\b|\bcertificate verify\b|"
    r"\bname or service not known\b|\bfailed to (?:connect|resolve)\b|"
    # provider
    r"\bmodel\s+(?:not found|is not available|does not exist)\b|"
    r"\bcontext (?:length|window) exceeded\b|\brate[- ]limit(?:ed|s)?\b|"
    r"\bapi key\b|\binsufficient (?:quota|memory|vram)\b|"
    r"\bout of memory\b|\bcuda\b.*\bmemory\b|\bunable to load model\b|"
    r"\bi'?m sorry, (?:but )?i (?:cannot|can't|am unable)\b",
    re.I)
_CONTRACTION = re.compile(r"\b\w+['’](ve|re|ll|d|m|t)\b", re.I)
_SQUARE_CURLY = re.compile(r"[\[\]{}]")
_PAREN = re.compile(r"[()]")
_QUOTE = re.compile(r"[\"`]|(?<![a-z])['’]|['’](?![sS]\b)")

# Rules every class is held to. Markup, error text and stringified structures
# are never a belief about the world, whatever produced them.
UNIVERSAL_RULES = ("empty", "too_long", "markup", "error_text",
                   "structured_literal", "null_literal",
                   "imperative_shape")                      # BA fix 5

# Classes whose terms are model- or system-derived, with no external referent
# to make a strange-looking string legitimate. These get the full rule set.
#
# `procedural` is here because K.1's chokepoint sentinel put it here: the
# first draft of this set held only extraction and consolidation, and
# test_every_ingest_route_passes_through_the_same_chokepoint kept failing —
# `procedural_promotion` is a pattern the system observed in its own use, so
# it has exactly the same provenance problem. The guard written first
# corrected the implementation, which is the point of writing it first.
#
# `external` (wikidata, conceptnet) and `fixture` stay lenient: a Wikidata
# label like `Mercury (planet)` is a legitimate disambiguation, and the
# offline fixture is our own hardcoded data. They are still held to the
# universal rules — markup and stringified structures are never beliefs.
STRICT_CLASSES = frozenset({"extraction", "consolidation", "procedural"})

# --------------------------------------------------------------------------
# Packet O.3 — the clause rules K.2 deliberately deferred
# --------------------------------------------------------------------------
# K.2 shipped WITHOUT a word cap, and said why: "My validating sample topped
# out at four words, so any >6 threshold would be untested rather than
# validated, and the real store's 164 terms of 7+ words are the population
# that would decide it."
#
# An hour of real use produced that population. Extraction asks the model for
# snake_case (extraction.py:67) and the model complies by snake-casing whole
# clauses, so the corpus is now long noun-phrase-shaped strings that carry
# exactly ONE preposition — under `multi_preposition`'s >=2 threshold, and
# therefore invisible to every rule K shipped.
#
# NOTE, because the packet's diagnosis was different and it matters for
# anyone reading this later: `_tokens()` has ALWAYS replaced `_` with a space,
# so the rules were never blind to underscore-joined text. Measured — the
# tokens for `TAK-441_prolonged_the_QT_interval_as_a_potential_indicator_of_
# cardiotoxicity` are ['tak-441','prolonged','the','qt','interval','as','a',
# 'potential','indicator','of','cardiotoxicity'], which is correct. The rules
# saw the tokens perfectly well and had nothing to say about them.
#
# MEASURED on 23 legitimate technical concepts and 7 clauses stored by the
# operator's session (O.3):
#
#   rule                             recall     false positives
#   word cap >= 8                    6/7        0/23
#   subordinator present             2/7        0/23
#   word cap >= 8 OR subordinator    7/7        0/23      <- shipped
#
# THE BOUNDARY IS TIGHT AND THE HONEST NUMBER IS THIS: the longest legitimate
# concept in the corpus is 7 tokens — `corrected QT adjusted for resting heart
# rate`, which is the object of a row the operator's own session wrote — and
# the shortest clause is also 7. They overlap. A cap at 8 refuses none of the
# 23 and catches 6 of 7; the subordinator rule catches the seventh. One more
# legitimate 8-token concept and the cap is wrong.
#
# `verify_packet_k.py` §4 prints the store's 7+ word terms. Run it before
# trusting this threshold on a corpus other than the one it was measured on.
MAX_TERM_TOKENS = 8

# A concept never contains one of these. A clause routinely does. This is the
# rule that catches `TAK-441_when_concomitantly_used_with_CYP3A4_inhibitors`,
# which is 7 tokens and so slips under the cap.
SUBORDINATORS = frozenset({
    "when", "if", "because", "while", "although", "unless", "since",
    "whether", "though", "whereas", "until", "that", "which",
})


# --------------------------------------------------------------------------
# K.3 — relation normalisation
# --------------------------------------------------------------------------
# The real store holds `is_a`, `is a type of`, `is`, `is an example of` and
# `is_an_example_of` — five spellings of one predicate, plus one relation that
# is literally `partial or inactive`. Five spellings mean five rows for one
# assertion, five retrieval targets, and a contradiction detector that cannot
# see two claims are about the same thing.
#
# SLUG + ALIAS, AND NEVER REJECT. A relation vocabulary is open-ended:
# refusing an unrecognised predicate would refuse legitimate new ones, and the
# gate would be making an ontology decision it has no basis for. Anything the
# alias map does not know is slug-normalised and stored as itself.
#
# The slug half is not new — `seeder.py:190` has done `rel.lower().replace(
# " ", "_")[:40]` for Wikidata since Phase 2. This generalises that one
# route's behaviour to all six, which is the same shape as the gate itself.
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

# PROVISIONAL, and deliberately SMALL. Every entry is a spelling of "is a"
# observed in the real store or trivially adjacent to one.
#
# What is NOT here matters more than what is: `subclass_of` and `instance_of`
# are absent on purpose. Wikidata distinguishes P279 (subclass of) from P31
# (instance of), and collapsing them would destroy information the source took
# care to record — a class is not an instance. An alias map is a claim that
# two predicates mean the same thing, and that claim needs evidence, not
# convenience.
RELATION_ALIASES = {
    "is":                 "is_a",
    "isa":                "is_a",
    "is_an":              "is_a",
    "is_a_type_of":       "is_a",
    "is_a_kind_of":       "is_a",
    "is_an_example_of":   "is_a",
    "is_example_of":      "is_a",
    "are":                "is_a",
}


def normalise_relation(relation: str) -> str:
    """Canonical form of a relation. Never raises, never returns empty-for-
    non-empty: an unmappable relation comes back as its own slug.

    Emptiness is `inspect_relation`'s business, not this function's — a
    normaliser that silently turned a bad relation into a good one would hide
    exactly what the gate is for.
    """
    raw = (relation or "").strip().lower()
    if not raw:
        return ""
    slug = _SLUG_STRIP.sub("_", raw).strip("_")
    if not slug:
        return ""
    # O.3 — THE TRUNCATION IS GONE, and this is the mechanism behind the
    # "truncated relation" the operator found. This line used to end
    # `[:MAX_RELATION_CHARS]`, and `add()` normalises BEFORE it gates
    # (tiered_store.py:329-332), so by the time `inspect_relation` checked
    # `len(raw) > MAX_RELATION_CHARS` the value was already exactly 60.
    #
    # Two consequences, both measured:
    #   * `relation_too_long` was UNREACHABLE through add(). A 200-character
    #     relation returned ACCEPTED and was stored at 60. That is the C10 /
    #     H.4 defect class — a rule naming a state the code cannot reach.
    #   * the gate SANITISED rather than rejected, which is precisely what
    #     K.5 ruled against: "Sanitising stores a belief that differs from
    #     what the source said — a provenance loss in the layer whose job is
    #     faithfulness, and a SILENT one. Rejecting is faithful and loud."
    #
    # Reproduced exactly: the operator's
    # `highlight_the_importance_of_monitoring_levels_and_adjusting_` is what
    # this slice makes of an 86-character relation, trailing underscore and
    # all. It is now refused instead, with a reason.
    return RELATION_ALIASES.get(slug, slug)


def normalise_term(term: str) -> str:
    """Whitespace-canonical form of a subject/object.

    Strip and collapse internal runs — nothing else. This is NOT sanitisation:
    it does not alter what the source said, it removes formatting that is not
    part of the assertion. The audit found `"  padded  "` stored with its
    spaces, which makes two identical beliefs into two rows.

    Terms are deliberately NOT slugged. `theory of relativity` must not become
    `theory_of_relativity`: a term is a name, and mangling a name is a
    provenance loss of exactly the kind K.0.5 rejected for markup.
    """
    return re.sub(r"\s+", " ", (term or "").strip())


class BeliefRejected(ValueError):
    """Raised by `TripleStore.add()` when the gate refuses a triple.

    A raise, not a `None` return: `add()`'s callers assign the returned id and
    pass it onward, so a `None` would flow into `_log_confidence(belief_id=…)`
    and be recorded as a belief that exists. Subclasses ValueError because
    `add()` already raises ValueError for an unknown granularity or tier —
    a caller distinguishing "bad input" from "database failure" keeps working.
    """

    def __init__(self, reason: str, *, field: str = "", term: str = ""):
        self.reason = reason
        self.field = field
        self.term = term
        excerpt = (term[:60] + "…") if len(term) > 60 else term
        super().__init__(
            f"belief rejected at ingest [{reason}]"
            + (f" on {field}={excerpt!r}" if field else ""))


# --------------------------------------------------------------------------
# Configuration — reversible, per the E.2/F.3 precedent
# --------------------------------------------------------------------------

def gate_enabled() -> bool:
    """`NEXUS_INGEST_GATE=off` restores exactly the pre-K.2 behaviour.

    Read at call time so it can be flipped without a restart. The gate is a
    judgement about what counts as a belief, and a judgement that cannot be
    withdrawn is one nobody can afford to be wrong about — if it proves too
    aggressive on real traffic, the operator turns it off and we learn from
    the replay rather than from lost beliefs.
    """
    return os.environ.get("NEXUS_INGEST_GATE", "on").strip().lower() != "off"


def min_confidence() -> float:
    """Floor on the extractor's parse confidence. **Default 0.0 — off.**

    Deliberately not a guess: there is no evidence for a threshold, and the
    calibration log (`_log_confidence`) already accrues the data that would
    set one empirically. Shipping a plausible-looking number here would be
    inventing a measurement.
    """
    raw = os.environ.get("NEXUS_EXTRACTION_MIN_CONFIDENCE", "0.0")
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return 0.0


# --------------------------------------------------------------------------
# The rules
# --------------------------------------------------------------------------

def _tokens(term: str) -> list[str]:
    return _TOKEN.findall(term.lower().replace("_", " "))


def inspect_term(term: str, *, evidence_class: str,
                 position: str = "subject") -> str | None:
    """Why this subject/object may not enter, or None if it may.

    Returns a reason string rather than a bool so the rejection can be
    declared (I-6) — "rejected" alone tells an operator nothing about whether
    the extractor is emitting prose, markup or placeholders.
    """
    raw = (term or "").strip()
    strict = evidence_class in STRICT_CLASSES

    # ---- universal: true of any source ------------------------------------
    if not raw:
        return "empty"
    if len(raw) > MAX_TERM_CHARS:
        return "too_long"
    if _MARKUP.search(raw):
        return "markup"
    if _ERROR_TEXT.search(raw):
        return "error_text"
    if _SQUARE_CURLY.search(raw):
        # `['a', 'b', 'c']` — a stringified list is not a belief, from any
        # source. Parentheses are NOT here: `Mercury (planet)` is how Wikidata
        # disambiguates, and that distinction is the whole reason the gate
        # takes an evidence class.
        return "structured_literal"
    if raw.lower() in NULL_LITERALS:
        return "null_literal"
    if imperative_clause(raw) is not None:
        # BA fix 5 — a term that tells someone to do something is not a
        # concept, whatever produced it. See IMPERATIVE_MARKERS.
        return "imperative_shape"

    toks = _tokens(raw)
    if not toks:
        return "no_tokens"

    if not strict:
        return None

    # ---- strict: model-derived terms only ---------------------------------
    if len(raw) < 2:
        # `_is_meaningful` rejected <= 2 characters, and this gate inherited
        # that bound without examining it. Running the suite surfaced the
        # problem: a two-character bound rejects `AI`, `pH`, `UK`, `T4`, `B12`
        # — real concepts, in exactly the technical domains this substrate is
        # aimed at. Loosened to single characters, which is strictly MORE
        # permissive than the behaviour it replaces, so nothing that used to
        # be storable stopped being storable.
        return "too_short"
    if all(t.isdigit() for t in toks):
        return "all_digits"
    if _PAREN.search(raw):
        return "parenthetical"
    if _QUOTE.search(raw):
        return "quoted"
    if _CONTRACTION.search(raw):
        return "contraction"
    if raw.lower().startswith("to ") and len(toks) >= 2:
        # An infinitive opens a verb phrase: "To address …". No concept does.
        return "leading_infinitive"
    hit = next((t for t in toks if t in PRONOUNS), None)
    if hit:
        return f"pronoun:{hit}"
    hit = next((t for t in toks if t in COPULA_AUX), None)
    if hit:
        return f"copula:{hit}"
    if position == "subject" and sum(1 for t in toks if t in PREPOSITIONS) >= 2:
        # One preposition is a concept (`theory of relativity`); two is a
        # clause (`issue of finding the news about Iran`). Measured at K.2:
        # this catches the long fragments with zero false positives, where a
        # word-count cap would then have been an untested threshold.
        #
        # P.2 — SUBJECTS ONLY, and the measurement overturned the instinct
        # that this was a close call. Since P.1 shipped the word cap, almost
        # everything this rule uniquely caught is refused by `too_many_tokens`
        # regardless:
        #
        #   alternating periods of intense physical activity with recovery…
        #       9 tokens -> too_many_tokens, whatever this rule does
        #   the process of converting light into chemical energy   8 -> ditto
        #   a measure of the spread of values around the mean     10 -> ditto
        #   issue of finding the latest news about Iran online     9 -> ditto
        #
        # The band it uniquely governs is objects of SEVEN TOKENS OR FEWER
        # with two prepositions, and there the population is legitimate
        # descriptive phrases (`reputable_outlets for real-time updates on
        # Iran`). §3b measured 7 of 15 rows this rule refused as cited by
        # SEALED lineage records.
        #
        # The asymmetry is a statement about what the two positions are: a
        # SUBJECT is the thing being described and must be a concept; an
        # OBJECT is the description, and a description is allowed to be a
        # phrase. `theory of relativity` is a subject; "a measure of the
        # spread of values around the mean" is what an object is for.
        return "multi_preposition"
    if len(toks) >= MAX_TERM_TOKENS:
        # O.3 — the cap K.2 deferred for want of evidence. See MAX_TERM_TOKENS.
        return "too_many_tokens"
    hit = next((t for t in toks if t in SUBORDINATORS), None)
    if hit:
        # A subordinating conjunction is a clause joint. No concept has one.
        return f"subordinate_clause:{hit}"
    if all(t in FILLER_TOKENS for t in toks):
        # THE GRANULARITY FIX. `_is_meaningful` tested the whole string, so
        # "my" was caught and "My understanding" was not. This is per token.
        return "all_filler"
    return None


def inspect_relation(relation: str, *, evidence_class: str) -> str | None:
    """Why this relation may not enter, or None if it may.

    Deliberately far weaker than `inspect_term`: a predicate legitimately
    contains the very tokens that mark a term as a fragment — `is_a` is
    built from a copula and an article. Running relations through the term
    rules would reject the most common relation in the store.

    K.2 therefore checks only that a relation is present and is not markup,
    error text, or a stringified null. Making `is_a`, `is a type of` and
    `is_an_example_of` into one predicate is normalisation, which is K.3.

    `null_literal` joined late (the Hemoglobin incident): the check ran on
    subject and object and skipped the field between them, so
    `Hemoglobin | None | protein` was accepted while `None` in either term
    slot was refused. The weaker-rules argument above does not cover it — a
    predicate legitimately contains copulas and articles, but no predicate
    anyone means is the stringified nothing.
    """
    raw = (relation or "").strip()
    if not raw:
        return "empty_relation"
    if len(raw) > MAX_RELATION_CHARS:
        return "relation_too_long"
    if _MARKUP.search(raw):
        return "markup"
    if _ERROR_TEXT.search(raw):
        return "error_text"
    if _SQUARE_CURLY.search(raw):
        return "structured_literal"
    if raw.lower() in NULL_LITERALS:
        return "null_literal"
    return None


def inspect_triple(subject: str, relation: str, object: str, *,
                   evidence_class: str, weight: float | None = None) -> str | None:
    """The whole triple. Returns the first reason found, or None.

    Field order is subject, relation, object so a reason reads in the order a
    human would inspect the triple.
    """
    reason = inspect_term(subject, evidence_class=evidence_class)
    if reason:
        return f"subject:{reason}"
    reason = inspect_relation(relation, evidence_class=evidence_class)
    if reason:
        return f"relation:{reason}"
    reason = inspect_term(object, evidence_class=evidence_class,
                          position="object")
    if reason:
        return f"object:{reason}"
    if (weight is not None and evidence_class == "extraction"
            and weight < min_confidence()):
        # For extraction, `weight` IS the extractor's clamped parse
        # confidence (agentic_loop maps it directly), so the floor can live
        # at the chokepoint with everything else. Off by default.
        return f"below_confidence_floor:{weight:.3f}"
    return None


def check_triple(subject: str, relation: str, object: str, *,
                 evidence_class: str, weight: float | None = None) -> None:
    """Raise `BeliefRejected` if this triple may not enter. No-op if the gate
    is switched off."""
    if not gate_enabled():
        return
    reason = inspect_triple(subject, relation, object,
                            evidence_class=evidence_class, weight=weight)
    if reason is None:
        return
    field, _, detail = reason.partition(":")
    term = {"subject": subject, "relation": relation, "object": object}.get(field, "")
    raise BeliefRejected(detail or reason, field=field, term=term)
