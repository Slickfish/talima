"""Stage 4 — Triple extraction (arch §5.2).

Always uses the FAST_EXTRACTION capability (phi4-mini in the default roster).
Emits a list of structured triples extracted from the assistant response.

Provider call returns free-form text; we accept JSON arrays of objects with
`subject` / `relation` / `object` (and optional `confidence`), or plain
arrays of [s, r, o]. Anything that fails to parse becomes an empty list —
extraction failure is recoverable, not fatal.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.kb.retriever import _STOPWORDS    # Fix 5 — re-used for extraction filter
from backend.providers.model_map import resolve


# Fix 5 — extraction-specific stopword extension. The shared _STOPWORDS in
# retriever.py is tuned for KB query-term extraction; here we add the
# conversational pronouns and relational fillers that an LLM happily
# emits as "subjects" but that have no place in the cortex.
# Kept LOCAL to extraction so retriever tests aren't perturbed.
_EXTRACTION_STOPWORDS: frozenset[str] = frozenset(_STOPWORDS | {
    # First/second-person pronouns + reflexives
    "me", "my", "mine", "myself",
    "your", "yours", "yourself",
    "us", "our", "ours", "ourselves",
    "him", "her", "hers", "his", "himself", "herself",
    "their", "theirs", "themselves",
    # Conversational verbs the model often nominalises
    "tell", "told", "ask", "asked", "say", "said", "get", "got",
    "want", "wanted", "like", "liked", "need", "needed",
    "know", "knew", "think", "thought", "feel", "felt",
    # Spatial / temporal filler
    "about", "around", "between", "among", "across", "through",
    "here", "there", "now", "then", "today", "yesterday", "tomorrow",
})


def _is_meaningful(concept: str) -> bool:
    """Fix 5 — Reject conversational pronouns / relational fragments /
    single- or double-char tokens / pure digits from entering the cortex.

    Uses the extraction-local stopword set so retriever query-term
    behaviour stays unchanged."""
    norm = (concept or "").strip().lower()
    if len(norm) <= 2:
        return False
    if norm in _EXTRACTION_STOPWORDS:
        return False
    if norm.isdigit():
        return False
    return True

if TYPE_CHECKING:
    from backend.providers.base import LLMProvider


EXTRACTOR_ROLE = "extraction"

# C-38 — the extraction window, named so the loop can declare it on `done`
# (`extraction_input_chars`, `extraction_input_truncated`). The SIZE is not
# changed by C-38: 4,000 characters is what `extract_triples` has always
# kept (`text[:max_chars]`), head-first, by character.
EXTRACTION_MAX_CHARS = 4000

EXTRACTION_PROMPT = (
    "Extract 3-10 atomic (subject, relation, object) triples from the text "
    "below. Use snake_case for relations. Only return facts directly stated. "
    "Reply with a JSON array of objects with keys subject, relation, object, "
    "confidence (0.0-1.0). No prose.\n\n"
    "TEXT:\n{text}\n"
)

JSON_BLOCK_RE = re.compile(r"\[\s*[\{\[].*?[\}\]]\s*\]", re.DOTALL)


def extraction_prompt(text: str) -> str:
    """The prompt as sent. Arm B of V.1's measurement when the vocabulary is
    enabled; byte-identical to the pre-V.1 prompt when it is not.

    The vocabulary block is inserted BEFORE `TEXT:` so the instruction sits
    with the other instructions rather than after the payload, where a long
    chunk would bury it. That placement is not a preference — it is what the
    measured arm did, and moving it would make the 0.08 -> 0.38 figure a
    statement about a prompt nobody sent.

    Imported lazily: `backend.kb.vocabulary` derives its default from
    `decompose` and `gate`, which is a heavier import than this module needs
    at load time, and `vocabulary_enabled()` must be read per call anyway so a
    `.env` loaded after import is honoured.
    """
    from backend.kb.vocabulary import vocabulary_block, vocabulary_enabled

    if not vocabulary_enabled():
        return EXTRACTION_PROMPT.format(text=text)
    head, _, tail = EXTRACTION_PROMPT.partition("TEXT:")
    return (head + vocabulary_block() + "TEXT:" + tail).format(text=text)


@dataclass(frozen=True)
class ExtractedTriple:
    subject: str
    relation: str
    object: str
    confidence: float = 0.5

    def to_dict(self) -> dict:
        return {
            "subject":    self.subject,
            "relation":   self.relation,
            "object":     self.object,
            "confidence": self.confidence,
        }

    def as_tuple(self) -> tuple:
        return (self.subject, self.relation, self.object, self.confidence)


def _parse_triples(payload: str) -> list[ExtractedTriple]:
    """Best-effort JSON-array → list[ExtractedTriple]. Tolerates leading/
    trailing prose, code fences, etc."""
    if not payload:
        return []
    candidate = payload.strip()
    # Strip markdown code fences
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if "\n" in candidate:
            candidate = candidate.split("\n", 1)[1]
    # Heuristic: locate the first JSON array via regex if outer noise present
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        m = JSON_BLOCK_RE.search(candidate)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []
    out: list[ExtractedTriple] = []
    for item in data:
        if isinstance(item, dict):
            s = str(item.get("subject", "")).strip()
            r = str(item.get("relation", "")).strip()
            o = str(item.get("object", "")).strip()
            try:
                c = float(item.get("confidence", 0.5))
            except (TypeError, ValueError):
                c = 0.5
        elif isinstance(item, list) and len(item) >= 3:
            s, r, o = str(item[0]).strip(), str(item[1]).strip(), str(item[2]).strip()
            try:
                c = float(item[3]) if len(item) > 3 else 0.5
            except (TypeError, ValueError):
                c = 0.5
        else:
            continue
        if not (s and r and o):
            continue
        # Fix 5 — stopword + length + digit filter on subject AND object.
        # Relation is left untouched (relations like "is_a" are always meaningful).
        if not _is_meaningful(s) or not _is_meaningful(o):
            continue
        out.append(ExtractedTriple(
            subject=s, relation=r, object=o,
            confidence=max(0.0, min(1.0, c)),
        ))
    return out


def extraction_num_ctx(max_chars: int = EXTRACTION_MAX_CHARS) -> int:
    """The context extraction requests, DERIVED from its input cap.

    It was the literal 8192 — Law 5's ceiling used as a value — and on the
    operator's card that loaded phi4-mini at 8192 (4.1 GB) beside a 8.8 GB
    answering model, so every turn evicted one to load the other. The input
    is `text[:max_chars]` inside the prompt template, at most ~4,700
    characters, and the output is 3–10 JSON triples; 4096 covers the worst
    case with more than twice the margin. Sized from the CAP rather than
    the text so the runner's context is constant per configuration and
    never reloads on a longer answer. 4096 at the shipped cap; a bigger cap
    derives a bigger context, up to the ceiling.
    """
    from backend.providers.base import num_ctx_for
    return num_ctx_for(len(extraction_prompt("x" * max_chars)))


async def extract_triples(
    text: str,
    provider: "LLMProvider",
    *,
    max_chars: int = EXTRACTION_MAX_CHARS,
) -> list[ExtractedTriple] | None:
    """Run Stage 4 extraction.

    BA fix 6 — NOT MEASURED IS NOT REPORTED AS A NUMBER (the aborted-cycle
    rule, kairos.py). A provider failure returns `None`, which the loop
    declares as `extraction_failed`; a model that answered and produced
    zero parseable triples returns `[]`, which is a measurement. Blank
    input is `[]` too: there was nothing to extract, and that is known.
    """
    if not text or not text.strip():
        return []
    snippet = text[:max_chars]
    model = resolve(EXTRACTOR_ROLE)
    prompt = extraction_prompt(snippet)
    chunks: list[str] = []
    try:
        async for tok in provider.stream_chat(
            model, [{"role": "user", "content": prompt}],
            num_ctx=extraction_num_ctx(max_chars),
        ):
            chunks.append(tok or "")
    except Exception:
        # The failure branch must not construct the success branch's
        # "nothing found" value (tests/test_ba_fix6_unknown_not_zero.py).
        return None
    return _parse_triples("".join(chunks))
