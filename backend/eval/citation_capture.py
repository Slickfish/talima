"""AO — capture what the citation judge judged, so it can be judged.

AN.3 established the citation test's accuracy cannot be measured from
anything recorded: lineage stores `response_hash`, never answer text (the
fourth instance of the inputs-were-never-recorded class), so no
retrospective labelled sample can exist and every turn without capture is
unlabellable forever. This module captures, per turn, exactly the inputs a
human labeller needs: the answer text, the lineage_id (the join key to the
record the labels exist to correct), and per injected item the belief id,
the RENDERED SURFACE as the model saw it, and the flag the current judge
assigned.

DERIVED, NOT RECORD. `sha256(answer)` must equal the sealed record's
`response_hash`, so a tampered or mismatched capture is detectable against
the record for free — but the capture itself is not sealed, verification
endpoints do not extend to it, and the record layer still holds only the
hash. A labelling artifact, not evidence.

OFF BY DEFAULT, AND OFF MEANS NOTHING: no file, no directory, the path
constant consumed by nothing at runtime. This module never raises into a
turn (the calibration_log discipline).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path

from backend.data_root import default_file

logger = logging.getLogger("nexus.capture")

# Acknowledged in tests/_writers.py ACKNOWLEDGED_DATA_CONSTANTS
# (function-based writer, the calibration_log shape) and redirected in
# tests by the NEXUS_CITATION_CAPTURE_PATH env line in conftest.
DEFAULT_CAPTURE_PATH = "data/citation_capture.jsonl"


def capture_enabled() -> bool:
    """`NEXUS_CITATION_CAPTURE=on` enables. Off (the default) is today
    exactly: nothing is consumed, created, or written. Read at call time."""
    return os.environ.get(
        "NEXUS_CITATION_CAPTURE", "off").strip().lower() == "on"


def capture_path() -> Path:
    """Resolved at call time so tests/deployments can redirect via env.
    W1 — `NEXUS_CITATION_CAPTURE_PATH` wins whenever it is present; otherwise
    the file sits under `NEXUS_DATA_ROOT` when that is set."""
    return default_file("NEXUS_CITATION_CAPTURE_PATH", DEFAULT_CAPTURE_PATH)


def capture_turn(*, lineage_id: str, response_text: str,
                 hits, cited_ids: set, citation_test: str | None = None,
                 reasoning_text: str | None = None) -> bool:
    """Append one labelling row. Returns True if written. Never raises.

    Requires a lineage_id: without it the labels cannot be joined to the
    record they exist to correct, so a turn with no lineage record is not
    captured (counted as uncaptured on the coverage surface, since
    `captured_turns` will lag the lineage count).

    C-38 — `response_text` is the stated ANSWER (what the citation judge
    judged) and `reasoning_text` the reasoning, stored apart, each bound to
    its own hash (`answer_hash`, `reasoning_hash`, the record's columns).
    """
    if not capture_enabled() or not lineage_id:
        return False
    try:
        row = {
            "lineage_id": lineage_id,
            "ts": time.time(),
            # AU — which citation test assigned the `cited` flags below.
            "citation_test": citation_test,
            "answer": response_text,
            "answer_hash": hashlib.sha256(response_text.encode("utf-8")).hexdigest(),
            # C-38 — the reasoning apart from the answer, null when the reply
            # carried none; bound to the record's `reasoning_hash`.
            "reasoning": reasoning_text,
            "reasoning_hash": (hashlib.sha256(reasoning_text.encode("utf-8")).hexdigest()
                               if reasoning_text else None),
            "injected": [
                {"belief_id": rt.triple.id,
                 "surface": rt.render(),
                 "cited": rt.triple.id in cited_ids}
                for rt in hits
            ],
            # For the labeller: fill per-item "label" with used | unused |
            # unsure in a copy of this file; the scorer joins on lineage_id
            # + belief_id. Recorded here so the file explains itself.
            "labelling_note": ("copy this file, add 'label': "
                               "'used'|'unused'|'unsure' per injected item"),
        }
        p = capture_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        return True
    except Exception as e:                       # never break a turn
        logger.warning("citation capture failed: %s: %s",
                       type(e).__name__, e)
        return False


def coverage() -> dict:
    """The declared surface: enabled state and captured-turn count. Reading
    must not create the file (off means nothing)."""
    n = 0
    p = capture_path()
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                n = sum(1 for line in f if line.strip())
        except OSError:
            n = 0
    return {
        "enabled": capture_enabled(),
        "captured_turns": n,
        "note": ("derived, not record: the capture is a labelling artifact "
                 "verified against the lineage record by hash equality — the "
                 "answer against answer_hash and the reasoning against "
                 "reasoning_hash (C-38; records older than C-38 bind the answer "
                 "to response_hash); it is not sealed and verification endpoints "
                 "do not extend to it. Turns before capture was enabled are "
                 "unlabellable forever (forward-only)."),
    }
