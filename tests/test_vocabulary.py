"""P2 — vocabulary honesty (F-02 rename, F-13 scope, F-20 formula, F-17 README).

External surfaces say "evidential weight"; the old "confidence" key survives
exactly one release as a deprecated alias and is marked as such. DB schema
unchanged (`weight` column untouched — pinned implicitly by the whole suite).
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.cortex.curiosity import CuriousNode
from backend.cortex.nodes import EpistemicState
from backend.server import create_app

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


def test_graph_snapshot_exposes_evidential_weight(client, app):
    services = app.state.services
    services.hippocampus.upsert_node(
        "probe_node", epistemic_state=EpistemicState.PARTIAL, confidence=0.4)

    snap = client.get("/graph/snapshot").json()
    assert snap["metadata"]["confidence_deprecated"] is True
    node = next(n for n in snap["nodes"] if n["id"] == "probe_node")
    assert node["evidential_weight"] == pytest.approx(0.4)
    assert node["confidence"] == node["evidential_weight"]   # deprecated alias, one release


def test_memory_column_exposes_evidential_weight(client, app):
    # Note: the endpoint reads raw networkx node attrs (which upsert_node does
    # not populate — pre-existing quirk, out of P2 scope), so we pin the
    # vocabulary contract: canonical key present, alias equal.
    services = app.state.services
    services.hippocampus.upsert_node(
        "col_node", epistemic_state=EpistemicState.KNOWN, confidence=0.8)

    data = client.get("/memory/column").json()
    node = next(n for n in data["surface"]["nodes"] if n["id"] == "col_node")
    assert "evidential_weight" in node
    assert node["confidence"] == node["evidential_weight"]   # deprecated alias


def test_curious_node_payload_renamed_with_alias():
    d = CuriousNode(node_id="x", score=1.4, question="q?",
                    epistemic_state="partial", in_degree=2,
                    confidence=0.3).to_dict()
    assert d["evidential_weight"] == 0.3
    assert d["confidence"] == 0.3
    assert d["confidence_deprecated"] is True


def test_prompt_render_label_is_evidential_weight():
    tag = EpistemicState.KNOWN.render(confidence=0.85)
    assert "evidential weight: 0.85" in tag
    assert "confidence" not in tag


def test_contradiction_scope_statement_present():
    from backend.cortex.contradiction import ContradictionDetector
    doc = ContradictionDetector.__doc__ or ""
    assert "curated" in doc
    assert "unconditional" in doc          # retention is structural + unconditional
    assert "Not claimed" in doc            # anti-claim stated, not implied


def test_readme_front_door_exists():
    # Encoding-tolerant read: an editor/toolchain in this project has
    # re-encoded README.md to UTF-16 before; content is what matters.
    raw = (ROOT / "README.md").read_bytes()
    if b"\x00" in raw:
        raw = raw.replace(b"\x00", b"")
    readme = raw.decode("utf-8", "ignore")
    lines = readme.strip().splitlines()
    assert len(lines) >= 25
    assert "belief substrate" in readme
    assert "evidential weight" in readme
    assert "THREAT_MODEL" in readme
