"""Packet H.4 — the SSE contract, reconciled three ways and kept honest.

WHY THIS FILE EXISTS
    CLAUDE.md claimed "All 19 SSE events update correct panel in real time"
    and its Phase 8 criteria claimed "all 19 SSE types handled without JS
    error". Both were false, and **no test compared the lists** until the
    endpoint audit did it by hand. Three declared events were never emitted,
    two declared events had no handler anywhere, and three handled events were
    not in the contract — while the count coincidentally matched on both
    sides, which is why it read as consistent for so long.

    A constitution asserting something false is worse than one that is
    silent. This is C10 one level up: no constitutional claim without a test
    that can falsify it.

THE THREE SETS, REPORTED SEPARATELY
    declared    written in docs/CONTRACTS.md's SSE Events block
    reachable   can actually arrive at a client, by ANY route
    handled     bound by a `case '...'` in a frontend

    Each failure mode is a different defect and is reported as its own set,
    never collapsed into one boolean:
      declared - reachable   the C10 shape: a contract member the code cannot
                             produce, which reads as satisfied on inspection
      reachable - declared   an event no client is obliged to handle and no
                             test covers — how hebbian/hebbian_update drifted
      declared - handled     an event that arrives and nothing listens for

REACHABILITY, NOT GREP — the trap this test is built to avoid
    `chain_appended` and `kairos_consolidation` have NO `sse_event(...)` call.
    They are minted by KAIROS onto `pending_alerts` and emitted by the bridge
    in `agentic_loop.py`, gated by `BRIDGE_EVENTS`. A test that greps emit
    sites would call them unreachable and demand they be removed from the
    contract — passing confidently while being exactly wrong.

    The same asymmetry exists one level down: of the six `kb_pressure_alert`
    subtypes, two arrive via the bridge, one (`web_search`) is emitted
    directly in the loop, and three have no producer at all. Three different
    paths, one declared list.
"""
import ast
import re
from pathlib import Path

import pytest

from backend.loop.agentic_loop import BRIDGE_EVENTS
from tests._tracked import tracked, tracked_backend

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTENDS = tracked("frontend/*.html")
CLAUDE_MD = ROOT / "docs" / "CONTRACTS.md"            # W11c: moved from CLAUDE.md


# ---------------------------------------------------------------------------
# Derivation — each set computed, never hand-listed
# ---------------------------------------------------------------------------

def declared_events() -> set[str]:
    """The SSE Events block in docs/CONTRACTS.md — the contract's own list (in CLAUDE.md until W11c)."""
    block = CLAUDE_MD.read_text(encoding="utf-8").split("## SSE Events")[1].split("```")[1]
    return {line.split()[0] for line in block.strip().splitlines()
            if re.match(r"^[a-z_]+\s", line)}


def reachable_events() -> set[str]:
    """Every event type that can arrive at a client, by any route.

    Two routes, and missing the second is the trap in the docstring above:
      1. a direct `sse_event("name", ...)` call somewhere in backend/
      2. KAIROS queueing onto pending_alerts, which the bridge emits under
         the queued `event` name — but only for names in BRIDGE_EVENTS, so
         the allow-list IS the reachability condition, not the emit call.
    """
    direct: set[str] = set()
    for path in tracked_backend():
        direct |= set(re.findall(r'sse_event\(\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))
    # The bridge relabels anything outside BRIDGE_EVENTS, so only members are
    # genuinely reachable under their own name.
    bridged = set(BRIDGE_EVENTS) & queued_event_names()
    return direct | bridged


def queued_event_names() -> set[str]:
    """Event names KAIROS mints onto the queue, from the producer source."""
    kairos = (BACKEND / "memory" / "kairos.py").read_text(encoding="utf-8")
    return set(re.findall(r'"event":\s*"([a-z_]+)"', kairos))


def handled_events() -> dict[str, set[str]]:
    """{frontend filename: set of event names it binds}."""
    return {f.name: set(re.findall(r"case\s*'([a-z_]+)'", f.read_text(encoding="utf-8")))
            for f in FRONTENDS}


def all_handled() -> set[str]:
    return set().union(*handled_events().values())


# Declared events with no handler in any frontend. **Pinned as an explicit
# exception, and the pin is the finding**: the test fails if the set GROWS
# (a new unhandled event slipped in) and equally if it SHRINKS (someone fixed
# one, which should be a deliberate, recorded act rather than a silent drift
# in the direction we happen to like).
KNOWN_UNHANDLED = frozenset({"rag_injected", "model_pull_proposal"})


# ---------------------------------------------------------------------------
# The three reconciliations
# ---------------------------------------------------------------------------

def test_no_declared_event_is_unreachable():
    """The C10 shape. A contract member nothing can emit reads as satisfied
    on inspection and fails only when the untested path fires.

    This was the audit's headline: chain_appended, hebbian_update and
    kairos_consolidation sat here, all three with UI handlers waiting.
    """
    unreachable = declared_events() - reachable_events()
    assert not unreachable, (
        f"declared but nothing can emit them: {sorted(unreachable)}. Either "
        f"add an emit (and, if it comes from KAIROS, add it to "
        f"BRIDGE_EVENTS), or remove it from docs/CONTRACTS.md.")


def test_no_reachable_event_is_undeclared():
    """An undeclared event is one no client is obliged to handle and no test
    covers. That is precisely how `hebbian` and `hebbian_update` drifted into
    being two names for one thing."""
    undeclared = reachable_events() - declared_events()
    assert not undeclared, (
        f"reachable but not in docs/CONTRACTS.md's SSE contract: {sorted(undeclared)}")


def test_declared_events_are_handled_except_the_pinned_gap():
    """Report the third set, and hold the known gap steady in both
    directions."""
    unhandled = declared_events() - all_handled()
    assert unhandled == set(KNOWN_UNHANDLED), (
        f"the set of declared-but-unhandled events changed.\n"
        f"  expected: {sorted(KNOWN_UNHANDLED)}\n"
        f"  actual  : {sorted(unhandled)}\n"
        f"If it grew, an event was declared with nothing listening. If it "
        f"shrank, a handler was added — update KNOWN_UNHANDLED and the claim "
        f"in docs/CONTRACTS.md's SSE Events section together, so the document and the code move "
        f"as one.")


def test_the_three_sets_are_reported_not_collapsed():
    """§15 — aggregate verdict first, components second; and never a single
    boolean where three distinct diagnoses exist.

    This test exists to make the reconciliation legible in one place when it
    fails, rather than leaving whoever hits it to re-derive the sets.
    """
    declared, reachable, handled = declared_events(), reachable_events(), all_handled()
    report = {
        "declared":              len(declared),
        "reachable":             len(reachable),
        "declared_unreachable":  sorted(declared - reachable),
        "reachable_undeclared":  sorted(reachable - declared),
        "declared_unhandled":    sorted(declared - handled),
    }
    assert report["declared_unreachable"] == [], report
    assert report["reachable_undeclared"] == [], report
    assert report["declared_unhandled"] == sorted(KNOWN_UNHANDLED), report
    # Non-vacuity: a contract of zero events would satisfy every set
    # difference above. It must actually contain the events we think it does.
    assert report["declared"] == 22, report      # W6: record_failed


def test_claude_md_no_longer_asserts_the_false_claim():
    """The specific sentence that was false, gone from the places it was
    asserted as current fact.

    It survives once, quoted inside the correction that explains it — a
    document that records what it used to say wrongly is more useful than one
    that quietly rewrites history, so the test targets the assertion, not the
    string.
    """
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "all 19 SSE handled" not in text
    assert "all 19 SSE types handled without JS error" not in text
    # The remaining occurrence is the quoted correction.
    assert text.count("All 19 SSE events update correct panel in real time") == 1
    assert "previously read" in text


# ---------------------------------------------------------------------------
# Layer two — the kb_pressure_alert subtypes
# ---------------------------------------------------------------------------

def declared_alert_subtypes() -> list[str]:
    """The `kb_pressure_alert valid types` bullet list in docs/CONTRACTS.md."""
    block = CLAUDE_MD.read_text(encoding="utf-8").split("**`kb_pressure_alert` valid types:**")[1]
    block = block.split("\n\n")[0]
    return re.findall(r"^- `([a-z_]+)`", block, re.M)


def produced_alert_subtypes() -> set[str]:
    """Subtypes something actually mints, by ANY of the three paths.

    S.4 — THIS FUNCTION USED TO BE ONE-DIRECTIONAL, and the flaw was
    structural rather than a missing case. It read:

        {t for t in declared_alert_subtypes() if re.search(..., src)}

    i.e. it looked only for subtypes docs/CONTRACTS.md already declared. So
    `produced <= declared` below was true by construction, and a subtype the
    code emitted without declaring was invisible to the layer whose job is to
    notice exactly that. Measured: S.4's `assertion` was emitted and every
    contract test still passed.

    That is H.4's own finding one level down — "an event nothing declares" is
    a defect, and the guard for it could not fail.

    Now derived by AST from the payloads themselves: a dict literal is an
    alert payload if it is the second argument to
    `sse_event("kb_pressure_alert", ...)` or if it carries
    `"event": "kb_pressure_alert"` (the KAIROS queue form). Its `"type"` value
    is the subtype. AST rather than a text scan because `"type": "..."` is a
    common key — `model_pull_proposal`, attachments and worker contexts all
    have one — and a text scan would report subtypes nobody ever alerted with.
    """
    found: set[str] = set()
    for path in tracked_backend():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {k.value: v for k, v in zip(node.keys, node.values)
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if "type" not in keys:
                continue
            ev = keys.get("event")
            queued = (isinstance(ev, ast.Constant)
                      and ev.value == "kb_pressure_alert")
            if not (queued or _is_alert_arg(tree, node)):
                continue
            value = keys["type"]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.add(value.value)
    return found


def _is_alert_arg(tree: ast.AST, target: ast.Dict) -> bool:
    """True iff `target` is the payload of a `sse_event("kb_pressure_alert", …)`."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "sse_event"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "kb_pressure_alert"
                and node.args[1] is target):
            return True
    return False


def test_alert_subtypes_declared_and_produced_are_reported_separately():
    declared = set(declared_alert_subtypes())
    produced = produced_alert_subtypes()
    assert declared == {"gap", "staleness", "dilution", "semantic_duplicates",
                        "cortex_drift", "web_search",
                        # S.4 — the seventh. Detection shipped in O.2b with no
                        # offer, so the feature existed and was undiscoverable.
                        "assertion"}
    assert produced <= declared, (
        f"a subtype is produced that docs/CONTRACTS.md does not declare: "
        f"{sorted(produced - declared)}")


def test_three_declared_alert_subtypes_have_no_producer():
    """CARRIED FORWARD from H.3a. An INVERTED sentinel: its passing is the
    finding, and it fails when a producer appears.

    `gap`, `staleness` and `dilution` are declared in docs/CONTRACTS.md with nothing
    to emit them — dead contract members one level below the three dead SSE
    events. When a producer is added, this fails, and that is the signal to
    move the entry out of this list rather than to loosen the assertion.
    """
    producerless = sorted(set(declared_alert_subtypes()) - produced_alert_subtypes())
    assert producerless == ["dilution", "gap", "staleness"], (
        f"the producerless alert subtypes changed: {producerless}")


def test_alert_subtypes_do_not_share_one_delivery_path():
    """The asymmetry that makes a grep-based test wrong.

    Two subtypes reach a client through the KAIROS bridge, one is emitted
    directly in the loop, three not at all. Pinned so a future refactor that
    "unifies" the paths has to notice it is changing three things.
    """
    loop_src = (BACKEND / "loop" / "agentic_loop.py").read_text(encoding="utf-8")
    kairos_src = (BACKEND / "memory" / "kairos.py").read_text(encoding="utf-8")
    assert '"type":      "web_search"' in loop_src, (
        "web_search is emitted directly in the loop, not via the bridge")
    for bridged in ("semantic_duplicates", "cortex_drift"):
        assert f'"{bridged}"' in kairos_src
    assert "kb_pressure_alert" in BRIDGE_EVENTS
