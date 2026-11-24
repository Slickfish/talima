"""Law VII — the stable system prefix, and the ONE predicate that checks it.

BA fix 3 moved the constant here from agentic_loop.py so that compaction's
integrity report can compare against it. Before this, `run_cascade` passed
its own `prefix` argument as both sides of the comparison
(`prefix_pre=prefix, prefix_post=prefix`), so the integrity report's
"prefix bit-identical" rule could never fail and had never been evidence
— a defect found in the record, not a hardening. The cascade now compares
the prefix of the ASSEMBLED system message against this constant, through
`prefix_intact`, which is the same predicate `assert_stable_prefix` raises
on at prompt assembly. One comparison, one source of truth.

agentic_loop.py and compaction.py both import from here; agentic_loop
re-exports the name so every existing importer keeps working.
"""
from __future__ import annotations

# P1/F-03 — the prefix names the substrate so the model never disclaims its
# own memory. Law VII: this remains a stable constant; only the constant text
# changed (extend here, never make it dynamic).
STABLE_SYSTEM_PREFIX = (
    "You are NEXUS, a local synthetic cortex with a persistent memory substrate. "
    "The retrieved context appended below is your own durable memory, accumulated "
    "across sessions — use it as what you know, and never claim to lack memory or "
    "previous sessions. When retrieval is thin, say so plainly. "
    "You answer concisely, honestly, and acknowledge knowledge gaps."
)


def prefix_intact(system_content: str, stable_prefix: str = STABLE_SYSTEM_PREFIX) -> bool:
    """True iff the assembled system message begins with the stable prefix,
    byte for byte. The predicate both the runtime check and the compaction
    integrity rule use — there is no second implementation."""
    return system_content.startswith(stable_prefix)


def assert_stable_prefix(system_content: str, stable_prefix: str = STABLE_SYSTEM_PREFIX) -> None:
    """Raise if the assembled system message does not start with the stable prefix.

    This is the runtime check that backs the Phase 4 test
    'system prompt prefix never modified mid-session'. The prefix is allowed to
    have CONTENT APPENDED after it (Law VII) but not edited or removed.
    """
    if not prefix_intact(system_content, stable_prefix):
        raise RuntimeError(
            "STABLE_SYSTEM_PREFIX has been modified — Law VII violated. "
            "Dynamic content must be appended, not interpolated."
        )
