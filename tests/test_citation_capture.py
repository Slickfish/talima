"""Packet AO — the capture instrument.

WHY
    AN.3 established the citation test's accuracy cannot be measured from
    anything recorded: lineage stores `response_hash`, not answer text —
    the fourth instance of the inputs-were-never-recorded class — and every
    turn without capture is unlabellable forever.

WHAT IS CAPTURED, and what it is not
    Per turn, one JSONL row: the answer text, the lineage_id, and per
    injected item the belief id, the RENDERED SURFACE exactly as the model
    saw it (`RetrievedTriple.render()`), and the currently assigned cited
    flag. The capture is DERIVED, NOT RECORD: verification endpoints do not
    extend to it and the record layer still holds only the hash — but
    because `sha256(captured answer)` must equal the sealed record's
    `response_hash`, a tampered or mismatched capture is detectable against
    the record for free, without sealing it.

OFF BY DEFAULT, AND OFF MEANS NOTHING
    No file, no directory, no runtime consumption of the path constant.
    `NEXUS_CITATION_CAPTURE=on` enables; the destination is a data-path
    constant acknowledged in ACKNOWLEDGED_DATA_CONSTANTS (function-writer,
    the calibration_log shape) and redirected in tests by the
    NEXUS_CITATION_CAPTURE_PATH env line in conftest.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.loop.agentic_loop import run_turn
from backend.loop.state import NexusLoopState
from backend.memory.lineage import LineageStore


class _P:
    def __init__(self, answer="Python is a programming_language."):
        self.answer = answer
        self.calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls += 1
        if self.calls == 1:
            for tok in self.answer.split(" "):
                yield tok + " "
        else:
            yield "[]"

    async def get_embedding(self, t): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _rig(tmp_path):
    store = TripleStore(tmp_path / "kb.db")
    lineage = LineageStore(tmp_path / "kb.db")
    store.add(subject="python", relation="is_a", object="programming_language",
              weight=0.8, tier=1, granularity=Granularity.TOPIC,
              source="human_assertion")
    store.add(subject="python", relation="is_a", object="snake_genus",
              weight=0.7, tier=1, granularity=Granularity.TOPIC,
              source="wikidata")
    return store, lineage


def _turn(store, lineage):
    frames = []

    async def drive():
        async for f, _s in run_turn(NexusLoopState(), "tell me about python",
                                    retriever=TripleRetriever(store),
                                    provider=_P(), lineage=lineage):
            frames.append(f)

    asyncio.run(drive())
    return next(json.loads(f.split("data: ", 1)[1].strip())
                for f in reversed(frames) if f.startswith("event: done\n"))


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


# ---------------------------------------------------------------------------
# THE SENTINEL — bound to the sealed record
# ---------------------------------------------------------------------------

def test_the_capture_matches_the_records_response_hash(tmp_path, monkeypatch):
    cap = tmp_path / "capture.jsonl"
    monkeypatch.setenv("NEXUS_CITATION_CAPTURE", "on")
    monkeypatch.setenv("NEXUS_CITATION_CAPTURE_PATH", str(cap))
    store, lineage = _rig(tmp_path)
    done = _turn(store, lineage)

    # NON-VACUITY per the packet: a non-empty injected set and a non-empty
    # answer, or the hash equality is an equality of nothings.
    assert done["reinforcement"]["injected"] >= 2
    rows = _rows(cap)
    assert len(rows) == 1
    row = rows[0]
    assert row["answer"].strip(), "empty answer captured"

    rec = lineage.get(row["lineage_id"])
    assert rec is not None
    got = hashlib.sha256(row["answer"].encode("utf-8")).hexdigest()
    # C-38 — the capture's answer is the STATED answer and binds to the
    # record's `answer_hash`; `response_hash` is the content stream as
    # delivered and equals it only when the reply carried no reasoning,
    # which this fixture's does not.
    assert got == rec.answer_hash == row["answer_hash"], (
        "the captured answer does not hash to the sealed record's "
        "answer_hash — the capture cannot be joined to the record it "
        "exists to correct")
    assert rec.reasoning_hash is None and row["reasoning"] is None and row["reasoning_hash"] is None
    assert got == rec.response_hash, "no reasoning: the answer is the whole content stream"

    # Per-item shape: belief id, the surface AS THE MODEL SAW IT, the flag.
    assert row["injected"], "no per-item entries"
    for item in row["injected"]:
        assert set(item) == {"belief_id", "surface", "cited"}
        assert item["surface"].startswith("("), (
            "surface is not the rendered KB-context line")
    flags = {i["belief_id"]: i["cited"] for i in row["injected"]}
    rec_flags = {i["belief_id"]: i["cited"] for i in rec.injected}
    assert flags == rec_flags, "captured flags disagree with the record's"
    store.close(); lineage.close()


def test_the_turn_is_unchanged_by_capture(tmp_path, monkeypatch):
    """Byte-identical turn outcome either way: same injected list and flags,
    same response hash, same done accounting. Capture observes; it must not
    participate."""
    outcomes = {}
    for mode in ("off", "on"):
        sub = tmp_path / mode
        sub.mkdir()
        monkeypatch.setenv("NEXUS_CITATION_CAPTURE", mode)
        monkeypatch.setenv("NEXUS_CITATION_CAPTURE_PATH",
                           str(sub / "capture.jsonl"))
        store, lineage = _rig(sub)
        done = _turn(store, lineage)
        rec = lineage.recent(limit=1)[0]
        done.pop("lineage_id")
        done.pop("citation_forecast", None)        # forecast ids are uuids
        outcomes[mode] = (done, rec.response_hash,
                          [(i["belief_id"], i["cited"]) for i in rec.injected])
        store.close(); lineage.close()
    assert outcomes["on"] == outcomes["off"]


# ---------------------------------------------------------------------------
# Off means NOTHING
# ---------------------------------------------------------------------------

def test_off_writes_no_file_and_creates_no_directory(tmp_path, monkeypatch):
    cap = tmp_path / "capdir" / "capture.jsonl"
    # The explicit off POSITION (not merely unset) — also what satisfies the
    # dotenv rollback scan, which looks for this literal setenv.
    monkeypatch.setenv("NEXUS_CITATION_CAPTURE", "off")
    monkeypatch.setenv("NEXUS_CITATION_CAPTURE_PATH", str(cap))
    store, lineage = _rig(tmp_path)
    done = _turn(store, lineage)
    assert done["reinforcement"]["injected"] >= 2   # the turn itself ran
    assert not cap.exists()
    assert not cap.parent.exists(), (
        "off created the capture directory — off must mean NOTHING")
    store.close(); lineage.close()


def test_the_default_position_is_off(monkeypatch):
    monkeypatch.delenv("NEXUS_CITATION_CAPTURE", raising=False)
    from backend.eval.citation_capture import capture_enabled
    assert capture_enabled() is False


# ---------------------------------------------------------------------------
# Declared coverage
# ---------------------------------------------------------------------------

def test_coverage_is_declared_on_the_stats_surface(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", "ao-token")
    monkeypatch.delenv("NEXUS_CITATION_CAPTURE", raising=False)
    from fastapi.testclient import TestClient
    from backend.server import create_app, create_services
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    client = TestClient(create_app(svc))
    block = client.get("/cortex/stats").json()["citation_capture"]
    assert block["enabled"] is False
    assert block["captured_turns"] == 0
    assert "derived, not record" in block["note"], (
        "the surface must say the capture is outside the sealed record")


def test_capture_is_acknowledged_to_the_writer_ratchet():
    """The derived-writer discipline: the data-path constant is declared to
    the ratchet with its reason, the calibration_log shape."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _writers import ACKNOWLEDGED_DATA_CONSTANTS
    key = ("backend.eval.citation_capture", "DEFAULT_CAPTURE_PATH")
    assert key in ACKNOWLEDGED_DATA_CONSTANTS
    assert "NEXUS_CITATION_CAPTURE_PATH" in ACKNOWLEDGED_DATA_CONSTANTS[key]
