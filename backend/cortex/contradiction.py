"""ContradictionDetector (arch §6.3) — finds logically opposed triples.

A contradiction is two triples sharing (subject, object) where the relations
sit on opposite sides of a known antonym pair. Contradictions are NOT errors:
they are records of evolving belief, archived with timestamps and surfaced in
the ancestor interface (Law VIII).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

logger = logging.getLogger("nexus.contradiction")


CONTRADICTION_PAIRS: tuple[tuple[str, str], ...] = (
    ("increases", "decreases"),
    ("causes",    "prevents"),
    ("requires",  "excludes"),
    ("improves",  "worsens"),
    ("is_a",      "is_not_a"),
    ("supports",  "contradicts"),
    ("activates", "inhibits"),
)


# ---------------------------------------------------------------------------
# Packet T.2 — the vocabulary is configuration; the mechanism is ours
# ---------------------------------------------------------------------------
#
# `__init__` has accepted a `pairs=` argument since this shipped, and the one
# construction site (`server.py`) passed nothing. K.0's report already said
# the vocabulary is configuration and the mechanism is ours — this is the
# sentence made true. The O.2b shape: mechanism shipped, offer never built.
#
# WHY THIS IS WORTH DOING ON ITS OWN. It needs NO compliance from the
# extractor: the pairs match relations the store already contains, whatever
# the extractor decided to call them. So it is the fallback if the vocabulary
# measurement comes back badly and an improvement if it comes back well —
# coupling it to that measurement would let a poor result delay the change a
# poor result makes more important.
#
# FILE, NOT ENV LIST. A pair set is a domain artefact — the seam spec calls
# the opposition ontology "the configuration surface of the vertical",
# supplied by domain experts. That is a thing someone edits, reviews and
# version-controls, not a thing they cram into a shell variable. The env var
# names the file.
#
# FORMAT: a JSON array of two-element arrays.
#     [["increases", "decreases"], ["agonises", "antagonises"]]
#
# DECLARED, NOT SILENT (I-6). A file that cannot be read or parsed WARNS and
# falls back to the compiled default; it never leaves the detector empty and
# never stops the app. `pairs_source()` reports which happened, and
# `/cortex/stats` carries it, because a detector silently running on seven
# pairs when an operator installed thirty is exactly the failure this packet
# exists to end.

DEFAULT_PAIRS_SOURCE = "compiled_default"


def _normalise_pair(a, b) -> tuple[str, str] | None:
    """Lowercase, underscore-joined — the same shape `_norm` compares with.

    A pair whose two sides normalise to the same string is dropped: it would
    make every relation contradict itself, which is not an opposition.
    """
    x = ContradictionDetector._norm(str(a))
    y = ContradictionDetector._norm(str(b))
    if not x or not y or x == y:
        return None
    return (x, y)


def load_pairs(path: "str | Path | None" = None) -> tuple[tuple[str, str], ...]:
    """The opposition set in force. Falls back to the compiled default.

    Read at call time so a `.env` loaded after import is honoured — the same
    reason `gate_enabled()` and `recalibration_enabled()` resolve late.
    """
    raw = str(path) if path else os.environ.get("NEXUS_CONTRADICTION_PAIRS", "")
    raw = raw.strip()
    if not raw:
        _SOURCE["source"] = DEFAULT_PAIRS_SOURCE
        _SOURCE["error"] = ""
        return CONTRADICTION_PAIRS

    p = Path(raw)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(
            "NEXUS_CONTRADICTION_PAIRS=%s could not be read as JSON (%s: %s); "
            "falling back to the %d compiled pairs. Contradiction detection "
            "is running on the default set.",
            raw, type(e).__name__, e, len(CONTRADICTION_PAIRS))
        _SOURCE["source"] = DEFAULT_PAIRS_SOURCE
        _SOURCE["error"] = f"{type(e).__name__}: {e}"
        return CONTRADICTION_PAIRS

    pairs: list[tuple[str, str]] = []
    dropped = 0
    for item in (data if isinstance(data, list) else []):
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            dropped += 1
            continue
        pair = _normalise_pair(*item)
        if pair is None:
            dropped += 1
            continue
        pairs.append(pair)

    if not pairs:
        logger.warning(
            "NEXUS_CONTRADICTION_PAIRS=%s yielded no usable pairs (%d "
            "malformed); falling back to the %d compiled pairs.",
            raw, dropped, len(CONTRADICTION_PAIRS))
        _SOURCE["source"] = DEFAULT_PAIRS_SOURCE
        _SOURCE["error"] = f"no usable pairs ({dropped} malformed)"
        return CONTRADICTION_PAIRS

    if dropped:
        logger.warning("%s: %d malformed pair(s) skipped", raw, dropped)
    logger.info("contradiction pairs loaded from %s: %d pair(s)", raw, len(pairs))
    _SOURCE["source"] = str(p)
    _SOURCE["error"] = f"{dropped} malformed pair(s) skipped" if dropped else ""
    return tuple(dict.fromkeys(pairs))       # dedupe, order preserved


_SOURCE = {"source": DEFAULT_PAIRS_SOURCE, "error": ""}


def pairs_source() -> dict:
    """Where the set in force came from, and what went wrong if anything.

    Read-only copy. `/cortex/stats` surfaces it: an operator who installed
    thirty pairs and is silently running on seven has no other way to tell.
    """
    return dict(_SOURCE)


@dataclass(frozen=True)
class Triple:
    """Minimal triple shape the detector accepts. Concrete callers may pass
    objects with these attributes — duck-typing is fine."""
    subject: str
    relation: str
    object: str
    timestamp: float = 0.0
    source: str = ""
    confidence: float = 0.5


@dataclass(frozen=True)
class Contradiction:
    subject: str
    object: str
    relation_a: str
    relation_b: str
    timestamp_a: float
    timestamp_b: float
    source_a: str = ""
    source_b: str = ""

    def to_dict(self) -> dict:
        return {
            "subject":     self.subject,
            "object":      self.object,
            "relation_a":  self.relation_a,
            "relation_b":  self.relation_b,
            "timestamp_a": self.timestamp_a,
            "timestamp_b": self.timestamp_b,
            "source_a":    self.source_a,
            "source_b":    self.source_b,
        }


class ContradictionDetector:
    """P2/F-13 — honest scope statement.

    Detection operates over a curated, configurable relation set (currently
    7 antonym pairs, injectable via `pairs` — a vertical's domain opposition
    ontology is configuration, not code). Retention is structural and
    unconditional: conflicting beliefs are distinct rows in the store and are
    never overwritten, whether or not detection relates them.

    Not claimed: arbitrary open-vocabulary contradiction, or joint
    multi-dimensional inconsistency (X, Y, Z mutually inconsistent with no
    pair opposed). TODO (REVIEW_REPORT F-13 direction): canonical-relation
    normalisation at extraction time so free-form relations land on the
    curated set more often.
    """

    def __init__(
        self,
        pairs: tuple[tuple[str, str], ...] = CONTRADICTION_PAIRS,
    ):
        self.pairs = pairs
        # bidirectional opposition lookup
        self._opp: dict[str, set[str]] = {}
        for a, b in pairs:
            self._opp.setdefault(a, set()).add(b)
            self._opp.setdefault(b, set()).add(a)

    @staticmethod
    def _norm(s: str) -> str:
        return (s or "").strip().lower().replace(" ", "_")

    def is_contradictory(self, rel_a: str, rel_b: str) -> bool:
        a = self._norm(rel_a)
        b = self._norm(rel_b)
        return b in self._opp.get(a, set())

    # ---------- scans ----------

    def scan_recent(self, triples: Iterable) -> list[Contradiction]:
        """Pairwise scan within `triples` for matching (subject, object) and
        contradictory relations. Each triple may be a `Triple` dataclass or
        any object with subject/relation/object/timestamp/source attributes;
        plain (s, r, o) tuples are also accepted (timestamps default to 0)."""
        rows = [self._coerce(t) for t in triples]
        out: list[Contradiction] = []
        seen: set[tuple[str, str, str, str]] = set()
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                if self._norm(a.subject) != self._norm(b.subject):
                    continue
                if self._norm(a.object) != self._norm(b.object):
                    continue
                if not self.is_contradictory(a.relation, b.relation):
                    continue
                key = (
                    self._norm(a.subject), self._norm(a.object),
                    *sorted((self._norm(a.relation), self._norm(b.relation))),
                )
                if key in seen:
                    continue
                seen.add(key)
                out.append(Contradiction(
                    subject=a.subject, object=a.object,
                    relation_a=a.relation, relation_b=b.relation,
                    timestamp_a=a.timestamp, timestamp_b=b.timestamp,
                    source_a=a.source, source_b=b.source,
                ))
        return out

    def scan_against(
        self,
        new_triples: Iterable,
        existing: Iterable,
    ) -> list[Contradiction]:
        """Compare `new_triples` against `existing` (e.g. KB rows). Only
        cross-pair contradictions are returned — internal contradictions in
        `existing` are not (use scan_recent for that)."""
        new_rows  = [self._coerce(t) for t in new_triples]
        old_rows  = [self._coerce(t) for t in existing]
        out: list[Contradiction] = []
        for n in new_rows:
            for o in old_rows:
                if self._norm(n.subject) != self._norm(o.subject):
                    continue
                if self._norm(n.object) != self._norm(o.object):
                    continue
                if not self.is_contradictory(n.relation, o.relation):
                    continue
                out.append(Contradiction(
                    subject=n.subject, object=n.object,
                    relation_a=o.relation, relation_b=n.relation,
                    timestamp_a=o.timestamp, timestamp_b=n.timestamp,
                    source_a=o.source, source_b=n.source,
                ))
        return out

    # ---------- helpers ----------

    @staticmethod
    def _coerce(t) -> Triple:
        if isinstance(t, Triple):
            return t
        if isinstance(t, tuple) and len(t) >= 3:
            return Triple(
                subject=str(t[0]), relation=str(t[1]), object=str(t[2]),
                timestamp=float(t[3]) if len(t) > 3 else 0.0,
            )
        return Triple(
            subject=getattr(t, "subject", ""),
            relation=getattr(t, "relation", ""),
            object=getattr(t, "object", ""),
            timestamp=float(getattr(t, "timestamp", getattr(t, "last_fired", 0.0)) or 0.0),
            source=getattr(t, "source", ""),
            confidence=float(getattr(t, "confidence", 0.5) or 0.5),
        )
