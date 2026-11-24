"""Packet H.2 — the per-answer provenance surface.

Two halves, tested differently:

  the ENDPOINT  invoked, and its payload asserted field by field. The three
                weights are the point: I-5 says two weights never share a
                label, and the store holds three distinct quantities.

  the SURFACE   **executed**, not grepped. The render functions are extracted
                from nexus_ui.html and run under node against real payloads,
                so the assertions are about what a reviewer would see rather
                than about which strings appear in a file. Grepping the HTML
                is level-3 evidence — the whole reason this packet's audit
                existed (pack §15.4).

WHAT THE SURFACE MUST NOT DO, and is asserted not to do:
  * omit a belief that no longer resolves
  * hide an uncited belief
  * render the three weights as one number that "changed"
  * treat block 0 as unsealed (`sealed_block` truthiness)
  * report a tampered record as fine because the per-layer counts look good
"""
import json
import re
import shutil
from tests import _proc
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore
from backend.memory.lineage import LineageStore

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "nexus_ui.html"
NODE = shutil.which("node") or "/opt/node22/bin/node"

DAY = 86400.0
T0 = 1_700_000_000.0


# ---------------------------------------------------------------------------
# The endpoint — three weights, three names (amendments 1 and 2)
# ---------------------------------------------------------------------------

@pytest.fixture
def lineage_app(tmp_path):
    """A record whose beliefs have since drifted in every way that matters:
    one live, one archived, one decayed to the floor, one deleted."""
    import os
    os.environ["NEXUS_API_TOKEN"] = "h2-token"
    from backend.server import create_app, create_services

    store = TripleStore(tmp_path / "kb.db")
    lineage = LineageStore(tmp_path / "kb.db")
    ids = {
        "live": store.add(subject="python", relation="is_a",
                          object="programming_language", weight=0.9, tier=1,
                          granularity=Granularity.TOPIC, source="wikidata"),
        "archived": store.add(subject="dupe", relation="is_a", object="thing",
                              weight=0.6, tier=2, granularity=Granularity.TOPIC,
                              source="extraction"),
        "decayed": store.add(subject="stale", relation="is_a", object="fact",
                             weight=0.97, tier=0, granularity=Granularity.SESSION,
                             source="hippocampus_consolidation"),
    }
    rec = lineage.record_turn(
        query="tell me about python",
        injected=[
            {"belief_id": ids["live"], "weight_at_injection": 0.90,
             "tier_at_injection": 1, "cited": True},
            {"belief_id": ids["archived"], "weight_at_injection": 0.60,
             "tier_at_injection": 2, "cited": False},
            {"belief_id": ids["decayed"], "weight_at_injection": 0.97,
             "tier_at_injection": 0, "cited": False},
            {"belief_id": 999999, "weight_at_injection": 0.42,
             "tier_at_injection": 3, "cited": False},
        ],
        contradictions=[{"subject": "python", "relation_a": "is_a",
                         "relation_b": "is_not_a", "object": "snake"}],
        model_id="deepseek-r1:14b",
        response_text="Python is a programming_language.",
        prompt_prefix="STABLE")

    # Drift AFTER the record was written — the demo case.
    store.archive("dupe", "is_a", "thing")
    store.conn.execute("UPDATE triples SET last_fired = last_fired - ? WHERE id = ?",
                       (400 * DAY, ids["decayed"]))
    store.conn.commit()
    store.rebalance()
    store.close(); lineage.close()

    services = create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots")
    return create_app(services), rec, {"Authorization": "Bearer h2-token"}


def _payload(lineage_app):
    app, rec, auth = lineage_app
    with TestClient(app) as c:
        r = c.get(f"/lineage/{rec.lineage_id}", headers=auth)
    assert r.status_code == 200
    return r.json()


def test_three_weights_have_three_distinct_names(lineage_app):
    """I-5, extended. The store holds three quantities; the payload names all
    three, and the one that decays is not the one that never moves."""
    p = _payload(lineage_app)
    decayed = next(b for b in p["injected"] if b.get("subject") == "stale")

    assert decayed["weight_at_injection"] == 0.97   # recorded, sealed
    assert decayed["high_water_weight"] == 0.97     # record layer, monotone
    assert decayed["current_prominence"] == 0.0     # prominence, decayed away
    assert decayed["tier_at_injection"] == 0
    assert decayed["current_tier"] == 3

    # NON-VACUITY: the fixture must actually exercise divergence, or this
    # test would pass on a store where all three happen to be equal.
    assert decayed["current_prominence"] != decayed["high_water_weight"], (
        "fixture does not exercise the case the field split exists for")

    assert "weight_vocabulary" in p
    assert p["weight_vocabulary"]["stored_weight_deprecated"] is True


def test_stored_weight_retained_as_a_deprecated_alias(lineage_app):
    """P2 pattern: new key, old key, marker. `stored_weight` was the field
    that invited "then 0.97 / now 0.97" for a belief with zero prominence."""
    p = _payload(lineage_app)
    for b in p["injected"]:
        if b["resolved"]:
            assert b["stored_weight"] == b["high_water_weight"]


def test_archived_belief_is_marked_not_silently_live(lineage_app):
    p = _payload(lineage_app)
    archived = next(b for b in p["injected"] if b.get("subject") == "dupe")
    live = next(b for b in p["injected"] if b.get("subject") == "python")
    assert archived["archived"] is True
    assert live["archived"] is False


def test_unresolved_belief_is_present_and_flagged(lineage_app):
    p = _payload(lineage_app)
    gone = next(b for b in p["injected"] if b["belief_id"] == 999999)
    assert gone["resolved"] is False
    assert gone["weight_at_injection"] == 0.42      # the record survives it
    assert len(p["injected"]) == 4                  # nothing dropped


# ---------------------------------------------------------------------------
# The surface — executed, not grepped
# ---------------------------------------------------------------------------

def _extract_js() -> str:
    """Pull the provenance render functions out of the page.

    Executing them is the point: asserting that a string appears in an HTML
    file proves the code is present, which is precisely the evidence level
    that let four defects ship (pack §15.4).
    """
    body = FRONTEND.read_text(encoding="utf-8")
    names = ["_esc", "_num", "renderSealState", "renderBeliefRow"]
    out = []
    for name in names:
        m = re.search(r"^function " + name + r"\(.*?^\}", body, re.S | re.M)
        assert m, f"could not extract {name} from nexus_ui.html"
        out.append(m.group(0))
    return "\n".join(out)


def _run_js(snippet: str) -> str:
    # `text=True` without `encoding=` decodes the subprocess pipes with the
    # locale default — cp1252 on Windows. The JS being executed contains the
    # em dashes and arrows from nexus_ui.html's comments, so this is the same
    # defect as the file reads, one layer out (Packet I.1).
    proc = _proc.run([NODE, "-e", _extract_js() + "\n" + snippet],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=30)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.mark.skipif(not Path(NODE).exists(), reason="node not available")
def test_surface_renders_all_three_weights_distinctly(lineage_app):
    """The decayed belief must not read as "still worth 0.97"."""
    p = _payload(lineage_app)
    decayed = next(b for b in p["injected"] if b.get("subject") == "stale")
    html = _run_js(f"console.log(renderBeliefRow({json.dumps(decayed)}))")

    assert "at injection 0.97" in html
    assert "prominence now 0.00" in html
    assert "high-water 0.97" in html
    # Each carries its own class, so they cannot read as one number.
    for cls in ("w-inj", "w-now", "w-high"):
        assert cls in html, f"missing distinct styling for {cls}"


@pytest.mark.skipif(not Path(NODE).exists(), reason="node not available")
def test_surface_shows_uncited_and_archived_beliefs(lineage_app):
    p = _payload(lineage_app)
    archived = next(b for b in p["injected"] if b.get("subject") == "dupe")
    html = _run_js(f"console.log(renderBeliefRow({json.dumps(archived)}))")
    assert "not cited" in html, (
        "an uncited belief must be visible — that the answer was given "
        "evidence it did not use is information (F.5)")
    assert "archived" in html


@pytest.mark.skipif(not Path(NODE).exists(), reason="node not available")
def test_surface_names_an_unresolved_belief_rather_than_dropping_it(lineage_app):
    p = _payload(lineage_app)
    gone = next(b for b in p["injected"] if b["belief_id"] == 999999)
    html = _run_js(f"console.log(renderBeliefRow({json.dumps(gone)}))")
    assert "no longer resolves" in html
    assert "999999" in html
    assert "0.42" in html, "the recorded weight survives the belief"


@pytest.mark.skipif(not Path(NODE).exists(), reason="node not available")
def test_surface_uses_verify_sealed_not_sealed_block_truthiness():
    """Amendment 3. Block indices start at 0, so `if (rec.sealed_block)`
    calls the FIRST block unsealed — the demo's most likely record."""
    block_zero = {"exists": True, "record_intact": True,
                  "sealed": True, "sealed_block": 0}
    html = _run_js(f"console.log(renderSealState({json.dumps(block_zero)}))")
    assert "sealed into chain block 0" in html
    assert "awaiting seal" not in html, (
        "block 0 was reported as unsealed — sealed_block truthiness bug")


@pytest.mark.skipif(not Path(NODE).exists(), reason="node not available")
def test_surface_distinguishes_awaiting_seal_from_tampered():
    unsealed = {"exists": True, "record_intact": True,
                "sealed": False, "sealed_block": None}
    tampered = {"exists": True, "record_intact": False,
                "sealed": True, "sealed_block": 2}

    wait = _run_js(f"console.log(renderSealState({json.dumps(unsealed)}))")
    assert "awaiting seal" in wait
    assert "ALTERED" not in wait, "a normal unsealed record read as a failure"

    bad = _run_js(f"console.log(renderSealState({json.dumps(tampered)}))")
    assert "RECORD ALTERED" in bad
    assert "seal-bad" in bad


@pytest.mark.skipif(not Path(NODE).exists(), reason="node not available")
def test_surface_escapes_belief_text():
    """Beliefs come from model extraction, which the audit showed accepts
    `<script>alert(1)</script>` verbatim. The surface renders stored strings,
    so it must escape them."""
    hostile = {"resolved": True, "cited": False, "archived": False,
               "subject": "<script>alert(1)</script>", "relation": "is_a",
               "object": "xss", "source": "extraction",
               "weight_at_injection": 0.5, "current_prominence": 0.5,
               "high_water_weight": 0.5, "tier_at_injection": 2,
               "current_tier": 2}
    html = _run_js(f"console.log(renderBeliefRow({json.dumps(hostile)}))")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Page-level invariants
# ---------------------------------------------------------------------------

def test_provenance_is_attached_to_the_message_not_a_turn_panel():
    """The right panel's turn-scoped sections are overwritten by the next
    turn. Provenance that vanishes when you ask a follow-up is not
    provenance, so it hangs off the message it explains."""
    body = FRONTEND.read_text(encoding="utf-8")
    assert "attachProvenance(body, data.lineage_id)" in body
    assert "class=\"prov\"" in body or "'prov'" in body


def test_provenance_adds_no_external_urls():
    """P1/F-07 sovereignty: the page must still load air-gapped."""
    body = FRONTEND.read_text(encoding="utf-8")
    for token in ("http://", "https://"):
        for m in re.finditer(re.escape(token) + r"[^\s\"'<>)]+", body):
            url = m.group(0)
            assert url.startswith("http://www.w3.org/"), (
                f"external URL introduced: {url}")
