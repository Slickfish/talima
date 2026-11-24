"""W5 — the reference policy: the sources of content, what each declares, and where its content may flow.

WHY THIS EXISTS
    Two tables already said something about a source, and neither said where
    its content may go. `source_registry.py` knows how to fetch from two of
    them; `tiered_store.SOURCE_CLASS` gives each an evidence class, a decay
    rate and an arrival scale. Content from outside the runtime reached the
    prompt by two paths that nothing joined: search results as free text that
    nothing judged or labelled, and seeded rows through the gate's universal
    rules (run/specs/W5.md, S1).

    This module is the one table that answers, per source: its declared
    class, the provenance label rendered beside its content, whether its
    content may be written to the store, and how it may reach the prompt.
    It is DATA. The information-flow rule in docs/REFERENCE_GATEWAY.md is
    this table in sentences.

WHAT IT DOES, AND ONLY WHEN SWITCHED ON
    `NEXUS_REFERENCE_GATEWAY=off` (the default) is the request, the record
    and the store exactly as before: `store_refusal` answers None for every
    source and no label changes. On, `TripleStore.add()` refuses by name a
    source that is not declared here or is not permitted to the store, at
    the one chokepoint every ingest route passes through; and the renderer
    writes `via=<provenance>` beside `src=<class>` on every stored row it
    injects. The search results' half is `backend/loop/reference_gateway.py`.

WHAT IT DOES NOT DO
    It judges no content. The content rule for a permitted source is the
    gate's, inside `add()`, in both positions. It reduces what can flow and
    it labels what does. The item's measurement says by how much
    (run/specs/W5.md, S3): a rate, with its denominator.

LAYERING
    Standard library only. `tiered_store` imports this module, so it imports
    nothing from `backend.kb`: the declared classes are written here and
    `tests/test_w5_reference_gateway.py` asserts each equals `SOURCE_CLASS`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

SETTING = "NEXUS_REFERENCE_GATEWAY"
ON = "on"
OFF = "off"

# How a source's content may reach the prompt.
AS_STORED_ROW = "stored_row"          # only as a row the store holds, rendered as a KB line
AS_JUDGED_RESULTS = "judged_results"  # as search results, judged, reduced and labelled by the gateway
NEVER = "never"

# Why `add()` refuses a source when the gateway is on.
UNDECLARED = "source_undeclared"
NOT_PERMITTED = "source_not_permitted"


@dataclass(frozen=True)
class SourcePolicy:
    source: str           # the `source` value a row carries
    declared_class: str   # its evidence class: equal to tiered_store.SOURCE_CLASS[source], by test
    provenance: str       # the label's `via=` value
    outside: bool         # its content comes from outside the runtime
    to_prompt: str        # AS_STORED_ROW | AS_JUDGED_RESULTS | NEVER
    to_store: bool        # its content may be written to the store


REFERENCE_POLICY: dict[str, SourcePolicy] = {p.source: p for p in (
    # Search results reach the prompt only through the gateway, and are never
    # written to the store: no writer exists, and with the gateway on none
    # can be added without a line here.
    SourcePolicy("web_search", "external", "web_search", True, AS_JUDGED_RESULTS, False),
    SourcePolicy("wikidata", "external", "wikidata", True, AS_STORED_ROW, True),
    SourcePolicy("conceptnet", "external", "conceptnet", True, AS_STORED_ROW, True),
    SourcePolicy("offline_fixture", "fixture", "offline_fixture", False, AS_STORED_ROW, True),
    SourcePolicy("human_assertion", "asserted", "human_assertion", False, AS_STORED_ROW, True),
    SourcePolicy("procedural_promotion", "procedural", "procedural_promotion", False, AS_STORED_ROW, True),
    SourcePolicy("hippocampus_consolidation", "consolidation", "hippocampus_consolidation", False,
                 AS_STORED_ROW, True),
    SourcePolicy("extraction", "extraction", "extraction", False, AS_STORED_ROW, True),
)}

# In-process counts since start, reset on restart (the FZ-2 pattern): so that
# "the gateway refused nothing" and "the gateway is off" read differently.
_COUNTS: dict[str, int] = {}


def gateway_setting() -> str:
    """The setting as written, lower-cased. Read at call time (the E.2 pattern)."""
    return os.environ.get(SETTING, OFF).strip().lower()


def gateway_on() -> bool:
    """True only for the literal `on`. Any other value is off, and
    `declaration()` says so: an unreadable setting never switches a policy on."""
    return gateway_setting() == ON


def count(event: str, n: int = 1) -> None:
    _COUNTS[event] = _COUNTS.get(event, 0) + n


def provenance_of(source: str) -> str:
    """The `via=` value for a row's source. A source with no line here is
    labelled as what it is, never as a declared one."""
    policy = REFERENCE_POLICY.get(source)
    return policy.provenance if policy is not None else "undeclared"


def store_refusal(source: str) -> str | None:
    """Why this source may not write to the store, or None. Always None with
    the gateway off: `add()` then does exactly what it did before W5."""
    if not gateway_on():
        return None
    policy = REFERENCE_POLICY.get(source)
    if policy is None:
        count("store_refused:" + UNDECLARED)
        return UNDECLARED
    if not policy.to_store:
        count("store_refused:" + NOT_PERMITTED)
        return NOT_PERMITTED
    return None


def declaration() -> dict:
    """What a status surface says: the state, an unrecognised setting named
    (and treated as off), the policy's rows, and the counts since start."""
    raw = gateway_setting()
    out: dict = {"state": ON if raw == ON else OFF, "setting": SETTING}
    if raw not in (ON, OFF, ""):
        out["setting_not_recognised"] = raw
        out["treated_as"] = OFF
    out["policy"] = [
        {"source": p.source, "declared_class": p.declared_class, "provenance": p.provenance,
         "outside": p.outside, "to_prompt": p.to_prompt, "to_store": p.to_store}
        for p in REFERENCE_POLICY.values()]
    out["since_start"] = dict(sorted(_COUNTS.items()))
    return out
