"""MCP-2 — the server's `instructions` and the tool descriptions: the only
server-side levers that make a client retrieve before it answers and record
after (MCP-1 item 3, measured on a scripted client; their effect on a real
client is not measurable here). Versioned: the text's hash is pinned by
`tests/test_mcp2_build.py`, so a change to a word is a recorded change.
"""
from __future__ import annotations

import hashlib

INSTRUCTIONS_VERSION = "w10b.1"

INSTRUCTIONS = (
    "NEXUS is a local, persistent memory with a sealed record. Use it in two "
    "calls around every answer you give the user:\n"
    "1. BEFORE answering, call nexus_retrieve with the user's question. Put "
    "the returned block, verbatim, into the context you answer from, and "
    "keep the retrieval_id.\n"
    "2. AFTER answering, call nexus_record with that retrieval_id and the "
    "exact answer text you gave. This seals the record of what you were "
    "shown and what you said.\n"
    "A retrieve with no record after it, and a record with no retrieve before "
    "it, are both counted; a turn with neither is invisible to NEXUS. Use "
    "nexus_recall, nexus_asof and nexus_status to read the record; use "
    "nexus_assert and then nexus_assert_confirm to store a claim the user "
    "states, never without the user's confirmation. When you take an action "
    "outside this conversation (a tool that sends a request or changes "
    "something), report it with nexus_record_action and the lineage_id "
    "nexus_record returned: NEXUS sees only the actions you report."
)


def instructions_hash(text: str = INSTRUCTIONS) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
