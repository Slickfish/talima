"""Pre-Phase-10c Governance Fix B — Drift Policy.

Spec contract under test:
  - assess_drift "stable" when distance < DRIFT_WARN_THRESHOLD
  - assess_drift "warn" when DRIFT_WARN <= distance < DRIFT_ALERT
  - assess_drift "alert" when distance >= DRIFT_ALERT_THRESHOLD
  - consecutive_warns increments on warn, resets on stable
  - action escalates to "consolidation_recommended" after 2 consecutive warns
  - alert always returns "consolidation_pass_recommended"
  - /cortex/drift returns "insufficient_history" with < 2 archive blocks
  - /cortex/drift returns valid assessment with >= 2 blocks
  - KAIROS emits kb_pressure_alert SSE on warn/alert (existing event type)
  - KAIROS does NOT emit on stable (no false alarms)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.cortex.edges import SynapticEdge
from backend.memory.chain import NexusArchiveChain
from backend.memory.fingerprint import (
    DRIFT_ALERT_THRESHOLD,
    DRIFT_WARN_THRESHOLD,
    CortexFingerprint,
    CortexFingerprinter,
    DriftAssessment,
    assess_drift,
)
from backend.memory.hippocampus import Hippocampus
from backend.server import create_app, create_services


# --------------- assess_drift ---------------

def _fp(distance_from_zero: float) -> CortexFingerprint:
    """Synthesise a fingerprint whose signature has known L2 norm so the
    distance metric produces a predictable value when compared to another."""
    # The distance() metric is normalised Euclidean / sum of L2 norms — for
    # tests we just construct two raw fingerprints and rely on the actual
    # metric. distance_from_zero is unused in this helper; tests build pairs.
    return CortexFingerprint(
        n_nodes=int(distance_from_zero * 100),
        n_edges=int(distance_from_zero * 100),
        mean_degree=distance_from_zero,
        clustering_coefficient=distance_from_zero,
        avg_path_length=distance_from_zero,
        structural_entropy=distance_from_zero,
    )


def test_drift_stable_below_warn_threshold():
    """Two identical fingerprints → distance 0 → stable."""
    fp = _fp(0.5)
    a = assess_drift(fp, fp, consecutive_warns=3)
    assert a.severity == "stable"
    assert a.consecutive_warns == 0     # resets on stable
    assert a.recommended_action == "none"


def test_drift_warn_at_threshold():
    """Mock distance to exactly land in the warn band."""
    fp1, fp2 = _fp(0.5), _fp(0.5)
    with patch.object(CortexFingerprint, "distance", return_value=0.30):
        a = assess_drift(fp1, fp2, consecutive_warns=0)
    assert a.severity == "warn"
    assert a.consecutive_warns == 1
    assert a.recommended_action == "monitor"   # first warn


def test_drift_action_escalates_after_two_consecutive_warns():
    fp1, fp2 = _fp(0.5), _fp(0.5)
    with patch.object(CortexFingerprint, "distance", return_value=0.30):
        a = assess_drift(fp1, fp2, consecutive_warns=1)
    assert a.severity == "warn"
    assert a.consecutive_warns == 2
    assert a.recommended_action == "consolidation_recommended"


def test_drift_alert_at_threshold():
    fp1, fp2 = _fp(0.5), _fp(0.5)
    with patch.object(CortexFingerprint, "distance", return_value=0.50):
        a = assess_drift(fp1, fp2, consecutive_warns=0)
    assert a.severity == "alert"
    assert a.recommended_action == "consolidation_pass_recommended"


def test_alert_severity_independent_of_warn_count():
    """Alert always recommends consolidation_pass — high warn count doesn't
    matter, alert is its own immediate severity."""
    fp1, fp2 = _fp(0.5), _fp(0.5)
    with patch.object(CortexFingerprint, "distance", return_value=0.95):
        a = assess_drift(fp1, fp2, consecutive_warns=99)
    assert a.severity == "alert"
    assert a.recommended_action == "consolidation_pass_recommended"


def test_drift_thresholds_constants():
    assert DRIFT_WARN_THRESHOLD == 0.25
    assert DRIFT_ALERT_THRESHOLD == 0.45


def test_drift_assessment_to_dict_schema():
    a = DriftAssessment(distance=0.3, severity="warn",
                         consecutive_warns=1, recommended_action="monitor")
    d = a.to_dict()
    assert set(d.keys()) == {"distance", "severity",
                              "consecutive_warns", "recommended_action"}


# --------------- /cortex/drift endpoint ---------------

@pytest.fixture
def app_services(tmp_path: Path):
    services = create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "ch",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snaps",
    )
    yield services
    services.store.close()
    services.neocortex.close()
    services.archive_chain.close()


def test_drift_endpoint_returns_insufficient_history_with_no_blocks(app_services, tmp_path):
    """With zero or one archive blocks, drift cannot be computed."""
    app = create_app(app_services)
    # The lifespan will run KAIROS once and may create the first block, but
    # we explicitly want to test the < 2 case — clear any blocks the lifespan
    # populated.
    with TestClient(app) as c:
        # Force the chain to look "fresh" by truncating
        app_services.archive_chain.conn.execute("DELETE FROM archive_blocks")
        app_services.archive_chain.conn.commit()
        r = c.get("/cortex/drift")
    assert r.status_code == 200
    assert r.json() == {"status": "insufficient_history"}


def test_drift_endpoint_returns_assessment_with_two_blocks(app_services, tmp_path):
    """Append two archive blocks with distinct fingerprints → endpoint
    returns a full DriftAssessment."""
    fp_a = CortexFingerprint(n_nodes=10, n_edges=15, mean_degree=3.0,
                              clustering_coefficient=0.2, avg_path_length=2.0,
                              structural_entropy=0.5)
    fp_b = CortexFingerprint(n_nodes=20, n_edges=40, mean_degree=4.0,
                              clustering_coefficient=0.3, avg_path_length=3.0,
                              structural_entropy=0.8)
    snap_a = tmp_path / "snap-a.json"          # any folder: the path is sealed as text (W3b: a store may have none)
    snap_b = tmp_path / "snap-b.json"
    snap_a.write_text("a", encoding="utf-8"); snap_b.write_text("b", encoding="utf-8")
    app_services.archive_chain.append(fp_a.to_dict(), snap_a)
    app_services.archive_chain.append(fp_b.to_dict(), snap_b)

    app = create_app(app_services)
    with TestClient(app) as c:
        r = c.get("/cortex/drift")
    assert r.status_code == 200
    body = r.json()
    assert "distance" in body and "severity" in body
    assert "recommended_action" in body
    assert body["severity"] in {"stable", "warn", "alert"}


# --------------- KAIROS emits kb_pressure_alert on warn/alert ---------------

def test_kairos_emits_alert_on_warn(tmp_path: Path):
    services = create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "ch",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snaps",
    )
    # Pre-populate the chain with a block so the next force-snapshot has
    # something to compare against.
    fp_a = CortexFingerprint(n_nodes=5, n_edges=5, mean_degree=2.0,
                              clustering_coefficient=0.1, avg_path_length=1.0,
                              structural_entropy=0.3)
    snap_a = tmp_path / "first.json"; snap_a.write_text("first", encoding="utf-8")
    services.archive_chain.append(fp_a.to_dict(), snap_a)

    captured: list[dict] = []
    async def hook(payload):
        captured.append(payload)
    services.kairos.notify_ui = hook

    # Patch CortexFingerprint.distance so the next snapshot lands in warn band
    with patch.object(CortexFingerprint, "distance", return_value=0.30):
        asyncio.run(services.kairos.maybe_snapshot_and_fingerprint(force=True))

    drift_alerts = [p for p in captured if p.get("type") == "cortex_drift"]
    assert len(drift_alerts) == 1
    assert drift_alerts[0]["severity"] == "warn"
    services.store.close(); services.neocortex.close()
    services.archive_chain.close()


def test_kairos_emits_alert_on_alert(tmp_path: Path):
    services = create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "ch",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snaps",
    )
    fp_a = CortexFingerprint(n_nodes=5, n_edges=5, mean_degree=2.0,
                              clustering_coefficient=0.1, avg_path_length=1.0,
                              structural_entropy=0.3)
    snap_a = tmp_path / "first.json"; snap_a.write_text("first", encoding="utf-8")
    services.archive_chain.append(fp_a.to_dict(), snap_a)

    captured: list[dict] = []
    async def hook(p): captured.append(p)
    services.kairos.notify_ui = hook

    with patch.object(CortexFingerprint, "distance", return_value=0.95):
        asyncio.run(services.kairos.maybe_snapshot_and_fingerprint(force=True))

    drift_alerts = [p for p in captured if p.get("type") == "cortex_drift"]
    assert len(drift_alerts) == 1
    assert drift_alerts[0]["severity"] == "alert"
    services.store.close(); services.neocortex.close()
    services.archive_chain.close()


def test_kairos_does_not_emit_on_stable(tmp_path: Path):
    """Identical fingerprints → distance ~0 → stable → no alert."""
    services = create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "ch",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snaps",
    )
    fp_a = CortexFingerprint(n_nodes=5, n_edges=5, mean_degree=2.0,
                              clustering_coefficient=0.1, avg_path_length=1.0,
                              structural_entropy=0.3)
    snap_a = tmp_path / "first.json"; snap_a.write_text("first", encoding="utf-8")
    services.archive_chain.append(fp_a.to_dict(), snap_a)

    captured: list[dict] = []
    async def hook(p): captured.append(p)
    services.kairos.notify_ui = hook

    with patch.object(CortexFingerprint, "distance", return_value=0.05):
        asyncio.run(services.kairos.maybe_snapshot_and_fingerprint(force=True))

    drift_alerts = [p for p in captured if p.get("type") == "cortex_drift"]
    assert drift_alerts == []
    services.store.close(); services.neocortex.close()
    services.archive_chain.close()


def test_consecutive_warns_persists_on_kairos(tmp_path: Path):
    """Two consecutive warn-band assessments → consecutive_drift_warns=2."""
    services = create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "ch",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snaps",
    )
    fp_a = CortexFingerprint(n_nodes=5, n_edges=5, mean_degree=2.0,
                              clustering_coefficient=0.1, avg_path_length=1.0,
                              structural_entropy=0.3)
    snap_a = tmp_path / "first.json"; snap_a.write_text("first", encoding="utf-8")
    services.archive_chain.append(fp_a.to_dict(), snap_a)
    services.kairos.notify_ui = lambda *a, **kw: _noop()

    async def _noop(): pass

    with patch.object(CortexFingerprint, "distance", return_value=0.30):
        asyncio.run(services.kairos.maybe_snapshot_and_fingerprint(force=True))
        asyncio.run(services.kairos.maybe_snapshot_and_fingerprint(force=True))

    assert services.kairos.consecutive_drift_warns == 2
    services.store.close(); services.neocortex.close()
    services.archive_chain.close()
