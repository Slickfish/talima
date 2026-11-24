"""A deterministic embedder for tests, CALIBRATED to the operator's geometry.

WHY THIS EXISTS
    The suite's provider is `OfflineTestProvider`, whose `get_embedding`
    returns `_offline_embed` — a SHA-256 hash expanded to 768 positive floats.
    W.0 measured what that is worth as a semantic signal: over 400 unrelated
    text pairs the cosine ranged 0.766-0.995 with a median of 0.935. It is a
    near-constant vector. Nothing built on it can distinguish "cardiac safety"
    from "banana bread recipe".

    The alternatives were: require a live daemon for the acceptance test
    (which reacquires the 143 outbound calls R.4 removed), or weaken the test
    to a phrasing that shares a token with the stored claim (the exact failure
    the test was written `strict` to prevent). So the claim is SPLIT — the
    suite proves the MECHANISM, `scripts/measure_retrieval.py` proves the
    NUMBER.

WHY IT IS CALIBRATED, AND WHAT THAT FIXED (W.2)
    The first version was an uncalibrated bag of words: unrelated pairs scored
    exactly 0.0 and the acceptance pair scored 0.1091. That let the acceptance
    test pass at `NEXUS_SEMANTIC_MIN_SIMILARITY=0.05` while the shipped default
    was 0.55, so **no test in the suite exercised the value that ships** and a
    threshold wrong by a factor of five was invisible. The operator's harness
    run found it on the real store.

    An embedder used to test a threshold must live in the same numeric range
    as the embedder that meets that threshold in production. So this one is
    fitted to two numbers measured on the operator's machine with
    nomic-embed-text, un-prefixed:

        signal  (the question vs the claim it should find)  0.4047
        control (two unrelated texts)                       0.2570

    `_BACKGROUND` sets the floor and `_CONCEPT_WEIGHT` sets the signal;
    `test_semantic_recall.py::test_the_toy_embedder_matches_the_measured_geometry`
    asserts both to within a tolerance and fails if either drifts. That test is
    what stops this file quietly becoming a fixture that agrees with whatever
    the code does.

    **It is still a toy.** It is a bag of words with a hand-written synonym
    table: it knows `cardiac` and `qtc` name one concept because this file says
    so, not because it learned anything. What it now reproduces faithfully is
    the SHAPE of a real embedder's number line — a non-zero floor between
    unrelated texts, and a signal only moderately above it. That shape is the
    thing threshold decisions are made against.

WHY IT IS STILL A REAL TEST
    The property under test is not "an embedder knows medicine". It is: given
    two texts with ZERO tokens in common and a cosine above the floor, does the
    claim reach the answer? `cardiac safety` and `mean placebo-corrected QTc
    change at 600mg ... was 11.4ms` share no token — `extract_terms` on the
    question yields `['know', 'about', 'cardiac', 'safety']` and `LIKE` on
    those matches nothing. That is the operator's measured failure exactly,
    reproduced rather than approximated.
"""
from __future__ import annotations

import hashlib
import math
import re

DIM = 768

# --- the fit -------------------------------------------------------------
# cos(a, b) = (B^2 + <bag_a, bag_b>) / (1 + B^2), with unit-normalised bags on
# axes orthogonal to the background axis. So:
#     unrelated  -> B^2 / (1 + B^2)          = 0.2570  ->  B = 0.5881
#     signal     -> needs <bag, bag> = 0.1988 for 0.4047
# _CONCEPT_WEIGHT is what buys that 0.1988 on the acceptance pair, which
# shares exactly one concept axis across 7 and 12 tokens. Solved, then pinned
# by a test rather than trusted.
_BACKGROUND = 0.5881
_CONCEPT_WEIGHT = 1.427

_MEASURED_SIGNAL = 0.4047        # nomic-embed-text, un-prefixed, operator's box
_MEASURED_CONTROL = 0.2570

# The synonym table. Words on the same axis are "the same concept" as far as
# this embedder is concerned. Deliberately tiny — a bigger table would make
# the tests look cleverer without making them prove more.
_AXES: dict[str, int] = {}
for _axis, _words in enumerate([
    # 0 — cardiac electrophysiology. The acceptance test's whole point: not one
    #     of these words appears in more than one of the other's texts.
    ("cardiac", "heart", "qt", "qtc", "ecg", "electrocardiogram",
     "arrhythmia", "repolarisation", "repolarization"),
    # 1 — hepatic metabolism, so the index has a second concept to be wrong about
    ("hepatic", "liver", "cyp3a4", "metabolism", "metabolized", "metabolised"),
    # 2 — software, so a query can land somewhere with no medical content at all
    ("python", "rust", "compiler", "programming"),
]):
    for _w in _words:
        _AXES[_w] = _axis

_RESERVED_AXES = 8          # 0..7 belong to the table above
_BACKGROUND_AXIS = 8        # the shared floor every text sits on
_FREE_BAND = (16, DIM)      # everything else gets a stable axis in here
_WORD_RE = re.compile(r"[a-z0-9]+")


def _axis_for(word: str) -> int:
    """The dimension a word loads on.

    Table words share an axis — that is the synonymy this embedder models.
    Every other word gets its own stable axis, so two texts that share no
    vocabulary and no concept meet only on the background axis.
    """
    known = _AXES.get(word)
    if known is not None:
        return known
    lo, hi = _FREE_BAND
    digest = hashlib.sha256(word.encode("utf-8")).digest()
    return lo + int.from_bytes(digest[:4], "big") % (hi - lo)


def toy_embed(text: str, *, dim: int = DIM) -> list[float]:
    """Deterministic, L2-normalised, and calibrated to the measured geometry.

    Unlike `_offline_embed`, two unrelated texts here score the measured
    control (~0.26) rather than an undifferentiated ~0.94, and a real
    relationship scores the measured signal (~0.40) rather than 0.
    """
    bag = [0.0] * dim
    for word in _WORD_RE.findall(text.lower()):
        axis = _axis_for(word) % dim
        bag[axis] += _CONCEPT_WEIGHT if axis < _RESERVED_AXES else 1.0
    norm = math.sqrt(sum(v * v for v in bag))
    if norm == 0.0:
        # Punctuation-only text: the background axis alone. It then scores the
        # control against everything, which is the honest answer for a text
        # with no content.
        vec = [0.0] * dim
        vec[_BACKGROUND_AXIS] = 1.0
        return vec
    vec = [v / norm for v in bag]
    vec[_BACKGROUND_AXIS] += _BACKGROUND
    scale = math.sqrt(sum(v * v for v in vec))
    return [v / scale for v in vec]


class ToyEmbedProvider:
    """An LLMProvider-shaped stub whose embeddings mean something.

    Only `get_embedding` is real; the rest reproduces a down daemon exactly as
    `OfflineTestProvider` does, so a test that accidentally starts streaming
    fails loudly instead of silently going live.
    """

    def __init__(self):
        self.calls = {"get_embedding": 0}

    async def stream_chat(self, model, messages, num_ctx=8192):
        raise ConnectionRefusedError("ToyEmbedProvider: embeddings only")
        yield ""                                     # pragma: no cover

    async def get_embedding(self, text: str) -> list[float]:
        self.calls["get_embedding"] += 1
        return toy_embed(text)

    async def list_models(self) -> list[str]:
        return []

    async def health(self) -> bool:
        return False
