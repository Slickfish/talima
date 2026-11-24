"""MCP-2 — what a record's scope declares, and the verifier for it.

MCP-1 classified every sealed field under the split. `record_scope =
mcp_block` on a record is the declaration; this module says, field by field,
what that declaration means, and names a record that contradicts it (an
undefined field holding a value).
"""
from __future__ import annotations

import hashlib

from backend.memory.lineage import FULL_TURN, IDENTITY_ASSERTED, IDENTITY_MECHANISMS, INTERCEPTED, MCP_BLOCK

EMPTY_PREFIX_HASH = hashlib.sha256(b"").hexdigest()

# The fields whose meaning changes under mcp_block, and the ones that are
# undefined there (MCP-1 S1.2, pinned in tests/test_mcp1_split.py): six and
# four at MCP-2; W0b added `capture_notes` to the first and its four capture
# fields to the second; W4 added `identity_mechanism` to the second. The
# three tables agree with RECORD_CLAIMS field for field
# (tests/test_w4_identity_record.py R6).
CHANGED_UNDER_MCP = {
    "model_id":           "the client's model as the client named it (`client-reported:<name>`), or `client-reported:unknown`; never a model NEXUS ran",
    "prompt_prefix_hash": "sha256 of the empty string: NEXUS sent no prompt, only the block it returned",
    "response_hash":      "sha256 of the answer text as the client sent it — the client's claim, not NEXUS's observation",
    "answer_hash":        "the same text, the same claim",
    "answer_chars":       "the length of the text the client sent",
    "request_hash":       "over NEXUS's block only (equal to context.hash); never the client's prompt, which NEXUS does not see; the block is what NEXUS keeps for it (W0b)",
    "capture_notes":      "names the client's model as the reason every replay-capture field is NULL (W0b)",
}
# W0b — the replay capture describes the model NEXUS ran. Under MCP the
# client's model ran outside NEXUS: none of the four is observable.
CAPTURE_UNDEFINED = ("inference_seed", "model_digest", "inference_config_hash", "environment_hash")
UNDEFINED_UNDER_MCP = {
    "reasoning_hash":      "NULL — no reasoning exists under MCP",
    "reasoning_chars":     "NULL — no reasoning exists under MCP",
    "answer_empty_reason": "NULL — a provider-boundary declaration; the client sends what it sends",
    "contradictions":      "empty — surfaced by extraction, and MCP-2 runs none",
    **{f: "NULL — the client's model ran outside NEXUS; the reason is in capture_notes (W0b)"
       for f in CAPTURE_UNDEFINED},
    "identity_mechanism":  ("NULL (asserted) — MCP has no peer and its owner is configuration, asserted "
                            "and never authenticated (W4)"),
}
UNCHANGED_UNDER_MCP = (
    "lineage_id", "ts", "query_hash", "injected", "record_hash", "sealed_block", "scope",
    "citation_test", "context", "work_unit_id", "accountable_identity", "boundary_asserted",
    "boundary_asserted_by", "attribution_flags", "learning_mode", "record_scope",
    # W6 — NEXUS's own reads at the record write, in either scope: the
    # envelope in force in the store, and the score of the forecast NEXUS
    # committed at retrieve against its own citation flags.
    "envelope_version", "forecast",
)

# W10c (run/specs/W10c.md S5) — what `intercepted` declares: one model exchange relayed by the interceptor
# (`backend/intercept/`), between an agent harness and its provider. Every field of the record is in one of the three
# tables, as under MCP (tests/test_w10c_interceptor.py T6 holds the two classifications to the same fields).
UPSTREAM_REPORTED = "upstream-reported:"
CHANGED_UNDER_INTERCEPTED = {
    "request_hash":       "sha256 of the request's body exactly as the harness sent it; those bytes are what is kept for it (W0b)",
    "response_hash":      "sha256 of the response's body exactly as the upstream returned it, compressed if the upstream compressed it",
    "model_id":           "the model the upstream's response names (`upstream-reported:<name>`), or `upstream-reported:unknown`: the provider's claim, never a model this process ran",
    "query_hash":         "sha256 of the empty string: the interceptor singles out no question; the whole request is in request_hash",
    "prompt_prefix_hash": "sha256 of the empty string: the interceptor sends no prompt of its own",
    "learning_mode":      "always `frozen`: no learning layer runs in the interceptor, whatever the switch says",
    "scope":              "always `default`: a harness's request has nowhere to state one",
    "capture_notes":      "names the upstream's model as the reason every replay-capture field is NULL, and whatever of the exchange could not be read or was cut (W0b)",
    "identity_mechanism": "`proxy` behind the identity proxy; otherwise NULL (asserted): the owner is configuration, never authenticated (W4)",
}
UNDEFINED_UNDER_INTERCEPTED = {
    **{f: "NULL — the reply is not separated into an answer and reasoning; its bytes are in response_hash"
       for f in ("answer_hash", "answer_chars", "reasoning_hash", "reasoning_chars", "answer_empty_reason")},
    "contradictions":      "empty — surfaced by extraction, and the interceptor runs none",
    **{f: "NULL — the upstream's model ran outside this process; the reason is in capture_notes (W0b)"
       for f in CAPTURE_UNDEFINED},
    "citation_test":       "NULL — nothing is cited: no citation test runs",
    **{f: "NULL — a harness's request has nowhere to state a work unit or a boundary"
       for f in ("work_unit_id", "boundary_asserted", "boundary_asserted_by")},
    "attribution_flags":   "empty — no work unit is attributed",
    "envelope_version":    "NULL — the interceptor's store holds no recalibration policy",
    "forecast":            "NULL — no citation forecast is made",
}
UNCHANGED_UNDER_INTERCEPTED = (
    "lineage_id", "ts", "record_hash", "sealed_block", "record_scope", "accountable_identity",
    # what NEXUS added to the request and told the model: nothing, unless NEXUS_INTERCEPT_INJECT=on
    "injected", "context",
)
COVERAGE_INTERCEPTED = ("complete for every exchange that passed through the endpoint; a harness pointed elsewhere "
                        "is not visible; tools are recorded as requested and as reported, not as executed")


def declaration(record_scope: str | None) -> dict:
    """The declaration a reader of `/lineage/{id}` or the resource gets."""
    scope = record_scope or FULL_TURN
    if scope == INTERCEPTED:
        return {"record_scope": INTERCEPTED,
                "note": ("one model exchange relayed by the interceptor: the request an agent harness sent its "
                         "model and the response the upstream returned, recorded as their bytes; the request was "
                         "recorded before it was sent, the response when it was complete; bytes already relayed "
                         "when a write failed are not recalled"),
                "coverage": COVERAGE_INTERCEPTED,
                "changed_in_meaning": dict(CHANGED_UNDER_INTERCEPTED),
                "undefined": dict(UNDEFINED_UNDER_INTERCEPTED),
                "unchanged": list(UNCHANGED_UNDER_INTERCEPTED)}
    if scope == MCP_BLOCK:
        return {"record_scope": MCP_BLOCK,
                "note": ("a turn split at the model call across a client's model: NEXUS returned "
                         "the block and recorded the answer the client sent"),
                "changed_in_meaning": dict(CHANGED_UNDER_MCP),
                "undefined": dict(UNDEFINED_UNDER_MCP),
                "unchanged": list(UNCHANGED_UNDER_MCP)}
    return {"record_scope": FULL_TURN,
            "note": "the console's full turn: NEXUS ran the model and every field is NEXUS's own observation"}


def verify_scope(record) -> list[str]:
    """Names every way a record contradicts its own scope. Empty = consistent.
    A full-turn record is never named here (its fields are the console's).
    """
    problems: list[str] = []
    if (getattr(record, "record_scope", None) or FULL_TURN) == INTERCEPTED:
        return _verify_intercepted(record)
    if (getattr(record, "record_scope", None) or FULL_TURN) != MCP_BLOCK:
        return problems
    if getattr(record, "reasoning_hash", None) is not None:
        problems.append("reasoning_hash is set on an mcp_block record (no reasoning exists under MCP)")
    if getattr(record, "reasoning_chars", None) is not None:
        problems.append("reasoning_chars is set on an mcp_block record")
    if getattr(record, "answer_empty_reason", None) is not None:
        problems.append("answer_empty_reason is set on an mcp_block record")
    if getattr(record, "contradictions", ()) :
        problems.append("contradictions are present on an mcp_block record (no extraction under MCP)")
    if getattr(record, "prompt_prefix_hash", None) != EMPTY_PREFIX_HASH:
        problems.append("prompt_prefix_hash is not sha256('') on an mcp_block record (NEXUS sent no prompt)")
    if getattr(record, "answer_hash", None) is None:
        problems.append("answer_hash is missing on an mcp_block record (the client's answer binds the record)")
    model_id = getattr(record, "model_id", "") or ""
    if not model_id.startswith("client-reported:"):
        problems.append(f"model_id {model_id!r} does not declare itself client-reported")
    # W0b — a capture value on an mcp_block record names a model NEXUS did
    # not run. A record that carries notes must give each NULL its reason; a
    # record with no notes at all predates the capture and says so by absence.
    notes = getattr(record, "capture_notes", None)
    for f in CAPTURE_UNDEFINED:
        if getattr(record, f, None) is not None:
            problems.append(f"{f} is set on an mcp_block record (the client's model ran outside NEXUS)")
        elif notes is not None and not (notes or {}).get(f):
            problems.append(f"{f} is NULL on an mcp_block record without a declared reason")
    # W4 — MCP has no peer and its owner is configuration: the only mechanism
    # an mcp_block record can have is the default. Any other value names a
    # way of establishing identity that MCP does not have. The default
    # spelled out is the default, as it is in the seal.
    mechanism = getattr(record, "identity_mechanism", None)
    if (mechanism or IDENTITY_ASSERTED) != IDENTITY_ASSERTED:
        problems.append(f"identity_mechanism is set on an mcp_block record ({mechanism!r}; MCP has no peer "
                        f"and its owner is configuration)")
    return problems


def _verify_intercepted(record) -> list[str]:
    """W10c — every way an `intercepted` record contradicts its scope."""
    problems: list[str] = []
    model_id = getattr(record, "model_id", "") or ""
    if not model_id.startswith(UPSTREAM_REPORTED):
        problems.append(f"model_id {model_id!r} does not declare itself upstream-reported")
    if getattr(record, "request_hash", None) is None:
        problems.append("request_hash is missing on an intercepted record (the harness's bytes bind the record)")
    for field in ("query_hash", "prompt_prefix_hash"):
        if getattr(record, field, None) != EMPTY_PREFIX_HASH:
            problems.append(f"{field} is not sha256('') on an intercepted record")
    if getattr(record, "learning_mode", None) != "frozen":
        problems.append("learning_mode is not `frozen` on an intercepted record (no learning layer runs there)")
    if (getattr(record, "scope", None) or "default") != "default":
        problems.append("scope is not `default` on an intercepted record (a harness's request states none)")
    notes = getattr(record, "capture_notes", None) or {}
    for field in UNDEFINED_UNDER_INTERCEPTED:
        value = getattr(record, field, None)
        if value not in (None, (), [], {}):
            problems.append(f"{field} is set on an intercepted record ({UNDEFINED_UNDER_INTERCEPTED[field]})")
        elif field in CAPTURE_UNDEFINED and not notes.get(field):
            problems.append(f"{field} is NULL on an intercepted record without a declared reason")
    mechanism = getattr(record, "identity_mechanism", None)
    if (mechanism or IDENTITY_ASSERTED) not in IDENTITY_MECHANISMS:
        problems.append(f"identity_mechanism {mechanism!r} is not one a record can have")
    return problems
