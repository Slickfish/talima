"""Generate `tests/fixtures/pre_run_data/`: a data directory as NEXUS writes
it, captured at the record layer the run to production started from.

    python tests/fixtures/make_pre_run_data.py <empty output directory>

WHY IT EXISTS. `RUN_CHARTER.md` law 4: every record-layer change keeps every
existing record recomputing unchanged, and "the LS fixture literal and the
pre-run data fixture prove it on every commit". W4 upgrades from it ("a data
dir captured at W0's base; every record recomputes unchanged"). It is
captured ONCE, before W0b's first record-layer change, from backend code
identical to MCP-2's `e9894ae` (the run's base; W0 changed nothing under
`backend/`), and committed; `tests/test_pre_run_fixture.py` proves every
later commit still reads it.

HOW. Through the product's own writers, never by INSERT, so the fixture
cannot drift from what the system persists: `create_services()` and the
HTTP app with a stub model for console turns (live learning, a frozen turn,
an attributed work unit, the session pseudo-unit, a close boundary), the
assertion and outcome confirm routes (a confirmed assertion, an unconfirmed
one refused and recorded, an approval, a supersede that records its recall
count), the store's own archive and unarchive acts (ledgered), a forced
KAIROS cycle (a snapshot block and a governance seal), then an MCP turn
through `NexusRuntime` (the `mcp_block` scope) and its housekeeping seal.

WHAT IS KEPT. `kb.db`, `archive.db` and `snapshots/`. Not `chroma/`: the
vector store is dark by design (CLAUDE.md, W.0) and its files are
chromadb's own format, not part of the record. `MANIFEST.json` names every
kept file with its sha256 and the facts the test re-derives: the lineage
records' count and a digest over their sorted hashes, the scopes and
learning modes, the chain's length and head, and as-of counts at instants
between the acts.

Writes nothing outside the output directory and a temporary directory it
removes; never touches `data/`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tests import _proc  # noqa: E402  (W4b; below the insert, so the documented `python tests/fixtures/make_pre_run_data.py <dir>` imports it)

TOKEN = "pre-run-fixture"
H = {"Authorization": f"Bearer {TOKEN}"}
KEPT = ("kb.db", "archive.db")          # plus every file under snapshots/


class _Answering:
    """The stub model: a fixed answer naming the seeded claims, so the
    citation test marks them cited; extraction returns nothing."""

    def __init__(self):
        self.calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls += 1
        if self.calls % 2 == 1:
            yield "Interval training improves VO2 max. The QT interval is a segment of the ECG."
        else:
            yield "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return False


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _done(resp) -> dict:
    frame = [f for f in resp.text.split("\n\n") if f.startswith("event: done")][-1]
    return json.loads(frame.split("data: ", 1)[1])


def _close_all(services) -> None:
    """Close every service that holds a connection, so the files are complete
    on disk before they are copied (no journal left open)."""
    for name in ("store", "lineage", "archive_chain", "recalibration", "work_units",
                 "graph_store", "trails", "semantic", "checkpointer"):
        obj = getattr(services, name, None)
        closer = getattr(obj, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:                  # a handle another service already closed
                pass


def _git(*args: str) -> str | None:
    try:
        cp = _proc.run(["git", *args], cwd=str(ROOT), capture_output=True,
                            encoding="utf-8", errors="replace", timeout=30)
        return cp.stdout.strip() if cp.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def generate(out: Path) -> dict:
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise SystemExit(f"refused: {out} is not empty")
    work = Path(tempfile.mkdtemp(prefix="pre_run_"))
    cwd = Path.cwd()
    # Production builds its stores on RELATIVE paths under `data/`
    # (`create_services()` defaults), and the snapshot block seals the path it
    # wrote; the generator runs from a work directory with the same relative
    # paths, so the fixture's sealed paths read as an instance's do.
    os.chdir(work)
    try:
        return _generate(out, work)
    finally:
        os.chdir(cwd)
        shutil.rmtree(work, ignore_errors=True)


def _generate(out: Path, work: Path) -> dict:
    from fastapi.testclient import TestClient

    from backend.kb import as_of as as_of_mod
    from backend.kb.granularity import Granularity
    from backend.kb.recalibration import RecalibrationStore
    from backend.mcp.mcp_server import NexusRuntime
    from backend.memory.chain import NexusArchiveChain
    from backend.memory.lineage import LineageStore
    from backend.server import create_app, create_services

    root = Path("data")
    root.mkdir()
    os.environ["NEXUS_API_TOKEN"] = TOKEN
    os.environ["NEXUS_CALIBRATION_LOG"] = str(work / "cal.jsonl")
    os.environ["NEXUS_FIRE_CAPTURE_PATH"] = str(work / "fire.jsonl")
    os.environ["NEXUS_CITATION_CAPTURE_PATH"] = str(work / "cap.jsonl")
    for k in ("NEXUS_LEARNING", "NEXUS_LEARNING_FROZEN_SINCE", "NEXUS_FIRE_CAPTURE",
              "NEXUS_CITATION_CAPTURE", "NEXUS_MCP_OWNER", "NEXUS_MCP_DATA_ROOT"):
        os.environ.pop(k, None)
    instants: list[float] = []

    svc = create_services(kb_db_path=root / "kb.db", chroma_dir=Path("chroma"),
                          eval_log_path=Path("m.jsonl"), archive_db_path=root / "archive.db",
                          snapshots_dir=root / "snapshots", provider=_Answering())
    client = TestClient(create_app(svc))
    ids = {}
    for s, r, o, w, src in [
        ("interval training", "improves", "vo2 max", 0.8, "human_assertion"),
        ("QT interval", "is_a_segment_of", "ECG", 0.9, "extraction"),
        ("ganymede", "orbits", "jupiter", 0.9, "wikidata"),
        ("water", "is_a", "liquid", 0.7, "offline_fixture"),
    ]:
        ids[s] = svc.store.add(subject=s, relation=r, object=o, weight=w, tier=1,
                               granularity=Granularity.TOPIC, source=src)
    time.sleep(1.1); instants.append(time.time()); time.sleep(1.1)

    # Console turns: an attributed unit, the session pseudo-unit, a close.
    d1 = _done(client.post("/chat", headers=H, json={
        "message": "what does interval training improve?", "work_unit": "claim-1001",
        "work_unit_type": "claim", "accountable": "alice@example", "actor_id": "alice@example",
        "actor_role": "person"}))
    d2 = _done(client.post("/chat", headers=H, json={"message": "what is the QT interval?"}))
    d3 = _done(client.post("/chat", headers=H, json={
        "message": "summarise the claim", "work_unit": "claim-1001", "accountable": "alice@example",
        "decision_boundary": "close"}))
    # A confirmed assertion inside a unit, and an unconfirmed one (refused and recorded).
    a_ok = client.post("/kb/assert/confirm", headers=H, json={
        "sentence": "The dose was 600mg.", "confirmed": True, "work_unit": "claim-1002"})
    a_no = client.post("/kb/assert/confirm", headers=H, json={
        "sentence": "The QTc change was 11.4ms.", "confirmed": False})
    # Outcomes: an approval, and a supersede that records its recall count.
    o1 = client.post("/outcome/confirm", headers=H, json={
        "event_type": "review", "decision": "approved", "direction": "confirming",
        "references": {"lineage_id": d1["lineage_id"]}, "confirmed": True,
        "actor_id": "alice@example", "actor_role": "person"})
    o2 = client.post("/outcome/confirm", headers=H, json={
        "event_type": "review", "decision": "rejected", "direction": "contradicting",
        "references": {"belief_ids": [ids["QT interval"]]}, "confirmed": True,
        "notes": f"superseded_by:{ids['interval training']}",
        "actor_id": "alice@example", "actor_role": "person"})
    time.sleep(1.1); instants.append(time.time()); time.sleep(1.1)
    # The store's own ledgered acts: one archival, one archival reversed.
    svc.store.archive_ids([ids["water"]], reason="pre-run fixture: archived", actor_id="operator")
    svc.store.archive_ids([ids["ganymede"]], reason="pre-run fixture: archived then restored",
                          actor_id="operator")
    svc.store.unarchive_ids([ids["ganymede"]], reason="pre-run fixture: restored", actor_id="operator")
    # A frozen turn, declared since a date.
    os.environ["NEXUS_LEARNING"] = "frozen"
    os.environ["NEXUS_LEARNING_FROZEN_SINCE"] = "2026-10-01"
    d4 = _done(client.post("/chat", headers=H, json={"message": "what does interval training improve?"}))
    os.environ.pop("NEXUS_LEARNING", None)
    os.environ.pop("NEXUS_LEARNING_FROZEN_SINCE", None)
    # A forced cycle: a snapshot block, then the governance seal.
    cyc = client.post("/kairos/cycle", headers=H)
    time.sleep(1.1); instants.append(time.time()); time.sleep(1.1)
    responses = {"assert_confirmed": a_ok.status_code, "assert_unconfirmed": a_no.status_code,
                 "outcome_approved": o1.status_code, "outcome_superseded": o2.status_code,
                 "kairos_cycle": cyc.status_code}
    client.close()
    _close_all(svc)

    # The MCP turn on the same store: retrieve, the client's answer, record, seal.
    os.environ["NEXUS_MCP_OWNER"] = "bob@example"
    rt = NexusRuntime.open(root=root, owner="bob@example")
    got = asyncio.run(rt.retrieve("what does interval training improve?"))
    rec = asyncio.run(rt.record(got.retrieval_id, "Interval training improves VO2 max.",
                                model="client-model"))
    asyncio.run(rt.housekeep_once())
    responses["mcp_record_keys"] = sorted(rec)
    lineage = rt.services.lineage
    rows = lineage._all_rows()
    records = [lineage._row_to_record(r) for r in rows]
    _close_all(rt.services)

    # Keep the record layer, not the vector store.
    for name in KEPT:
        shutil.copy2(root / name, out / name)
    snaps = sorted(p.name for p in (root / "snapshots").iterdir() if p.is_file())
    (out / "snapshots").mkdir()
    for name in snaps:
        shutil.copy2(root / "snapshots" / name, out / "snapshots" / name)

    # The facts the test re-derives from a copy.
    check = LineageStore(out / "kb.db")
    hashes = sorted(r.record_hash for r in records)
    intact = [check.verify_record(r.lineage_id)["record_intact"] for r in records]
    as_of_counts = [{"ts": t, "iso": dt.datetime.fromtimestamp(t, tz=dt.timezone.utc).isoformat(),
                     "held": as_of_mod.held_at(check.conn, t)[1]} for t in instants]
    chain = NexusArchiveChain(out / "archive.db")
    head = chain.latest()
    gov = RecalibrationStore(out / "kb.db").verify_governance_records(chain)
    chain_facts = {"blocks": chain.count(), "head_hash": head.block_hash if head else None,
                   "valid_at_capture": chain.verify_chain().valid}
    chain.close()
    check.close()
    files = {name: _sha256(out / name) for name in KEPT}
    files.update({f"snapshots/{n}": _sha256(out / "snapshots" / n) for n in snaps})
    manifest = {
        "what": "a data directory as NEXUS writes it, captured before W0b's first record-layer change",
        "generator": "tests/fixtures/make_pre_run_data.py",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "head": _git("rev-parse", "HEAD"),
        "backend_tree": _git("rev-parse", "HEAD:backend"),
        "python": platform.python_version(), "platform": sys.platform,
        "files": files,
        "lineage": {
            "count": len(records),
            "record_hashes": hashes,
            "record_hashes_sha256": hashlib.sha256("\n".join(hashes).encode()).hexdigest(),
            "scopes": sorted({r.record_scope or "full_turn" for r in records}),
            "learning_modes": sorted({r.learning_mode or "live" for r in records}),
            "all_intact_at_capture": all(intact),
        },
        "turns": {"console": [d1["lineage_id"], d2["lineage_id"], d3["lineage_id"], d4["lineage_id"]],
                  "mcp": [rec.get("lineage_id")]},
        "responses": responses,
        "as_of_instants": as_of_counts,
        "chain": chain_facts,
        "governance_at_capture": {"verdict": gov["verdict"], "row_failures": gov["row"]["failed"],
                                  "failing": gov["row"]["failures"]},
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
    return manifest


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    m = generate(Path(argv[1]))
    print(json.dumps({k: m[k] for k in ("head", "backend_tree", "lineage", "responses")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
