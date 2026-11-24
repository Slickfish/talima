"""W0b part 3 — `scripts/replay_check.py` against stubs (RUN_WORKLIST.md W0b).

  RC-1  a seeded deterministic model: every captured record reproduced, and
        the body the daemon received is the body the turn sent
  RC-2  a planted non-deterministic model: every record diverged, with the
        offset of the first differing character where AO captured the
        answer, and declared unmeasurable where it did not
  RC-3  pre-capture records (the pre-run fixture's) are not replayable,
        counted apart with their reasons, never folded into a rate
  RC-4  a moved tag is reported, never replayed: the daemon is never asked
  RC-5  read-only: the first line is the database path, the store's bytes do
        not change, a write through the opened connection is refused, and
        the one file written is the report
  RC-6  it refuses to run while something listens where NEXUS serves
  RC-7  the dry run classifies the same records and sends nothing
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import itertools
import json
import socket
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.loop.compaction import STABLE_SYSTEM_PREFIX
from backend.providers import replay_capture as rc
from backend.providers.ollama import OllamaProvider
from backend.server import create_app, create_services
from scripts import replay_check
from scripts.cp_b49_tally import open_read_only
from tests import _w0b_wire as wire

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "pre_run_data"
TOKEN = "w0b-replay"
H = {"Authorization": f"Bearer {TOKEN}"}
DIGEST, STEM, answer_for = wire.DIGEST, wire.STEM, wire.answer_for


class _Daemon:
    """The replay check's daemon interface, faked: `/api/tags` and the chat
    endpoint. Every body it is sent is kept."""

    def __init__(self, tags: dict, reply):
        self._tags, self.reply, self.sent = dict(tags), reply, []

    def tags(self):
        return dict(self._tags)

    def chat(self, endpoint, body):
        self.sent.append((endpoint, body))
        pieces = self.reply(body)
        return 200, ([{"message": {"role": "assistant", "content": p}, "done": False} for p in pieces]
                     + [{"message": {"role": "assistant", "content": ""}, "done": True}])


def _make_store(tmp_path, monkeypatch):
    """Four console turns by the REAL `OllamaProvider`, the network replaced
    at the transport (`tests/_w0b_wire.py`): Ollama's own body, seed
    placement, digest read and stream parse. AO's capture is on for three,
    so their answers are kept for offsets, and off for the fourth. Returns
    (db, ao, ids, model, the bodies the turns sent, newest first)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    daemon = wire.install(monkeypatch)
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    monkeypatch.setenv("NEXUS_CITATION_CAPTURE", "on")
    monkeypatch.setenv("NEXUS_CITATION_CAPTURE_PATH", str(tmp_path / "ao.jsonl"))
    for key in (rc.SEED_SETTING, rc.KEEP_SETTING):
        monkeypatch.delenv(key, raising=False)
    svc = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl", archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap", provider=OllamaProvider())
    svc.store.add(subject="interval training", relation="improves", object="VO2 max", weight=0.9,
                  tier=1, granularity=Granularity.TOPIC, source="human_assertion")
    client = TestClient(create_app(svc))
    ids = []
    for q in ("what does interval training improve?", "what is VO2 max?", "and resting heart rate?"):
        r = client.post("/chat", headers=H, json={"message": q})
        frame = [f for f in r.text.split("\n\n") if f.startswith("event: done")][-1]
        ids.append(json.loads(frame.split("data: ", 1)[1])["lineage_id"])
    monkeypatch.setenv("NEXUS_CITATION_CAPTURE", "off")
    r = client.post("/chat", headers=H, json={"message": "one more, uncaptured?"})
    frame = [f for f in r.text.split("\n\n") if f.startswith("event: done")][-1]
    ids.append(json.loads(frame.split("data: ", 1)[1])["lineage_id"])
    model = svc.lineage.get(ids[0]).model_id
    # Every connection the product holds is closed before the check reads the
    # file (`work_units.close` is the domain act of closing a unit, not a
    # connection, and is not called).
    for name in ("store", "lineage", "archive_chain", "recalibration", "graph_store", "trails", "semantic"):
        closer = getattr(getattr(svc, name, None), "close", None)
        if callable(closer):
            closer()
    sent = [b for _u, b in daemon.bodies if wire.answering(b)]
    return tmp_path / "kb.db", tmp_path / "ao.jsonl", ids, model, list(reversed(sent))


@pytest.fixture
def store(tmp_path, monkeypatch):
    db, ao, ids, model, _sent = _make_store(tmp_path, monkeypatch)
    return db, ao, ids, model


def _check(db: Path, daemon, ao: Path | None = None, last: int = 10):
    conn = open_read_only(db)
    try:
        return replay_check.check(conn, last=last, daemon=daemon,
                                  answers=replay_check.captured_answers(ao), db_label=str(db))
    finally:
        conn.close()


@pytest.mark.parametrize("separation", ["on", "off"])
def test_a_seeded_deterministic_model_reproduces_every_captured_record(tmp_path, monkeypatch, separation):
    """RC-1. The body re-sent is the body the turn sent: compared with what
    the real provider put on the wire during the turn (captured at the
    transport), never with a body rebuilt by the same code. The store's
    bytes do not change under `--confirm` (S6)."""
    monkeypatch.setenv("NEXUS_REPLY_SEPARATION", separation)     # off: the stream_chat path (part 5)
    db, ao, ids, model, turn_bodies = _make_store(tmp_path, monkeypatch)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    daemon = _Daemon({model: DIGEST}, lambda body: answer_for(body["options"]["seed"]))
    rep = _check(db, daemon, ao)
    k = rep.counts()
    print(f"\nRC-1 ({sys.platform}) {k}; bodies re-sent {len(daemon.sent)}; turn bodies {len(turn_bodies)}")
    assert k["reproduced"] == 4 and k["diverged"] == 0 and k["not replayable"] == 0
    assert [b for _e, b in daemon.sent] == turn_bodies, "the re-sent bodies are not the bodies the turns sent"
    assert all(e == "/api/chat" for e, _b in daemon.sent)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before, "the check changed the store"


def _non_deterministic(counter):
    """A planted model that ignores the seed. Its text diverges from every
    original at the first character after STEM by construction: a letter
    where the original has a digit (before part 4 it was a digit too, and
    the offset moved with the seed: CI run 8, ubuntu, 44 against 43)."""
    return lambda body: [STEM, f"n{next(counter)})."]


def test_a_non_deterministic_model_diverges_with_offsets_where_measurable(tmp_path, monkeypatch):
    db, ao, ids, model, _sent = _make_store(tmp_path, monkeypatch)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    daemon = _Daemon({model: DIGEST}, _non_deterministic(itertools.count(100_000)))
    rep = _check(db, daemon, ao)
    by_id = {o.candidate.lineage_id: o for o in rep.outcomes}
    print(f"\nRC-2 ({sys.platform}) {rep.counts()}")
    for o in rep.outcomes:
        print(f"   {o.candidate.lineage_id[:8]} {o.verdict}: {o.detail}")
    assert rep.counts()["diverged"] == 4
    for lid in ids[:3]:                                    # AO captured these answers
        assert by_id[lid].offset == len(STEM), by_id[lid].detail
        assert f"first differing character at {len(STEM)}" in by_id[lid].detail
    uncaptured = by_id[ids[3]]
    assert uncaptured.offset is None and "offset unmeasurable" in uncaptured.detail
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_companion_the_offset_does_not_move_with_the_seed(tmp_path, monkeypatch):
    """COMPANION (the verifier's M1). Seeds whose text begins with the digit
    the old planted text began with (1, 19, 1000: the verifier's
    reproductions) are pinned through the one declared hook. With the old
    planted text the offset was 44 or 47; now it is 43 for every one."""
    for seed in (1, 19, 1000):
        with rc.fixed_seed(seed):
            db, ao, ids, model, _sent = _make_store(tmp_path / str(seed), monkeypatch)
        rep = _check(db, _Daemon({model: DIGEST}, _non_deterministic(itertools.count(100_000))), ao)
        offsets = sorted({o.offset for o in rep.outcomes if o.offset is not None})
        print(f"\nRC-2c ({sys.platform}) seed {seed}: offsets {offsets}")
        assert offsets == [len(STEM)], (seed, offsets)


def test_pre_capture_records_are_not_replayable_and_counted_apart(tmp_path):
    import shutil
    copy = tmp_path / "pre_run"
    shutil.copytree(FIXTURE, copy)
    daemon = _Daemon({}, lambda body: pytest.fail("a pre-capture record was sent"))
    rep = _check(copy / "kb.db", daemon)
    k = rep.counts()
    text = replay_check.render(rep, provider_url="http://stub")
    print(f"\nRC-3 ({sys.platform}) {k}\n{text}")
    assert k["not replayable"] == 5 and k["reproduced"] == 0 and k["diverged"] == 0
    assert k["not_replayable_by_reason"] == {"written before the replay capture": 4, "mcp_block": 1}
    assert daemon.sent == []
    assert "%" not in text, "counts, never a rate"


def test_a_moved_tag_is_reported_and_never_replayed(store):
    db, ao, _ids, model = store
    daemon = _Daemon({model: "sha256:" + "0f" * 32}, lambda body: pytest.fail("a moved tag was replayed"))
    rep = _check(db, daemon, ao)
    print(f"\nRC-4 ({sys.platform}) {rep.counts()}")
    assert rep.counts()["not_replayable_by_reason"] == {"the tag moved": 4} and daemon.sent == []
    gone = _check(db, _Daemon({}, lambda body: pytest.fail("sent")), ao)
    assert gone.counts()["not_replayable_by_reason"] == {"tag not installed": 4}


def test_read_only_first_line_the_database_and_one_report(store, tmp_path, monkeypatch):
    db, ao, _ids, _model = store
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    monkeypatch.setattr(replay_check, "nexus_serving", lambda: None)
    out = io.StringIO()
    reports = tmp_path / "reports"
    with contextlib.redirect_stdout(out):
        code = replay_check.main(["--db", str(db), "--report-dir", str(reports), "--capture", str(ao)])
    lines = out.getvalue().splitlines()
    print(f"\nRC-5 ({sys.platform}) exit {code}; first line {lines[0]!r}; files {sorted(p.name for p in reports.iterdir())}")
    assert code == 0
    assert lines[0].startswith(f"database: {db.resolve().as_posix()}")
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before, "the store's bytes changed"
    (report,) = list(reports.iterdir())
    assert report.name.startswith("replay_check_") and report.suffix == ".md"
    conn = open_read_only(db)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized|readonly database"):
        conn.execute("DELETE FROM request_bodies")
    conn.close()


def test_it_refuses_to_run_while_something_serves_where_nexus_serves(store, tmp_path, monkeypatch):
    db, _ao, _ids, _model = store
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    monkeypatch.setenv("NEXUS_HOST", "127.0.0.1")
    monkeypatch.setenv("NEXUS_PORT", str(listener.getsockname()[1]))
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = replay_check.main(["--db", str(db), "--report-dir", str(tmp_path / "r"), "--confirm"])
    finally:
        listener.close()
    print(f"\nRC-6 ({sys.platform}) exit {code}: {out.getvalue().splitlines()[1]}")
    assert code == replay_check.EXIT_SERVING and "REFUSED" in out.getvalue()
    assert not (tmp_path / "r").exists(), "a refused run writes nothing"


def _more_turns(tmp_path: Path, questions, monkeypatch) -> list[str]:
    """Reopen the store the way an operator restarts NEXUS, and answer more
    questions through the real provider. Returns the new lineage ids.
    Chroma gets a directory of its own: on macos chromadb's Rust core refuses
    a second client at a path the process already holds (os error 35), and
    the refusal broke every later client in the process (CI run 11). Chroma is
    dark by design, and the check reads kb.db only."""
    import uuid as _uuid
    svc = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / f"chroma-{_uuid.uuid4().hex[:8]}",
                          eval_log_path=tmp_path / "m.jsonl", archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap", provider=OllamaProvider())
    client = TestClient(create_app(svc))
    ids = []
    for q in questions:
        r = client.post("/chat", headers=H, json={"message": q})
        frame = [f for f in r.text.split("\n\n") if f.startswith("event: done")][-1]
        ids.append(json.loads(frame.split("data: ", 1)[1])["lineage_id"])
    for name in ("store", "lineage", "archive_chain", "recalibration", "graph_store", "trails", "semantic"):
        closer = getattr(getattr(svc, name, None), "close", None)
        if callable(closer):
            closer()
    return ids


def test_every_capture_gap_is_named_and_counted_apart(tmp_path, monkeypatch):
    """RC-8. Each gap made the way it arises: a record written with
    NEXUS_KEEP_REQUESTS=off, one with NEXUS_INFERENCE_SEED=off, and one whose
    kept copy was corrupted afterwards (the copy is beside the record, so the
    record still verifies). Each is not replayable with its own reason; the
    clean records beside them reproduce; the store's bytes do not change on
    the not-replayable branch either (verifier 2's note B)."""
    db, ao, ids, model, _sent = _make_store(tmp_path, monkeypatch)
    monkeypatch.setenv("NEXUS_KEEP_REQUESTS", "off")
    (keep_off,) = _more_turns(tmp_path, ["kept nothing?"], monkeypatch)
    monkeypatch.setenv("NEXUS_KEEP_REQUESTS", "on")
    monkeypatch.setenv("NEXUS_INFERENCE_SEED", "off")
    (seed_off,) = _more_turns(tmp_path, ["no seed?"], monkeypatch)
    monkeypatch.delenv("NEXUS_INFERENCE_SEED", raising=False)
    conn = sqlite3.connect(db)
    rh = conn.execute("SELECT request_hash FROM lineage_records WHERE lineage_id = ?", (ids[0],)).fetchone()[0]
    conn.execute("UPDATE request_bodies SET body = ? WHERE request_hash = ?", (__import__("zlib").compress(b"[]"), rh))
    conn.commit()
    conn.close()
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    daemon = _Daemon({model: DIGEST}, lambda body: answer_for(body["options"]["seed"]))
    rep = _check(db, daemon, ao)
    by_id = {o.candidate.lineage_id: o for o in rep.outcomes}
    print(f"\nRC-8 ({sys.platform}) {rep.counts()}")
    for lid in (ids[0], keep_off, seed_off):
        print(f"   {lid[:8]} {by_id[lid].verdict}: {by_id[lid].detail}")
    assert by_id[ids[0]].detail == "the kept request is corrupted: it does not hash to request_hash"
    assert by_id[keep_off].detail == "request not kept (not kept: NEXUS_KEEP_REQUESTS=off)"
    assert by_id[seed_off].detail == "no seed (seed off (NEXUS_INFERENCE_SEED=off))"
    assert [by_id[i].verdict for i in ids[1:]] == ["reproduced"] * 3 and len(daemon.sent) == 3
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_the_dry_run_classifies_the_same_records_and_sends_nothing(store):
    db, ao, _ids, _model = store
    dry = _check(db, None, ao)
    print(f"\nRC-7 ({sys.platform}) dry {dry.counts()}")
    assert dry.mode == "dry run" and dry.counts()["would_send"] == 4 and dry.counts()["not replayable"] == 0


def _lineage_only_store(directory: Path) -> Path:
    """A store holding one fully captured record, written by `LineageStore`
    alone: no services, so no Chroma client is built under the path (RC-9
    hung on CI's macos runner while it built the full services under these
    names; whether Chroma was the cause is not established, and the test
    needs only the lineage tables)."""
    from backend.memory.lineage import LineageStore
    directory.mkdir(parents=True, exist_ok=True)
    db = directory / "kb.db"
    body = json.dumps([{"content": "q", "role": "user"}], sort_keys=True, separators=(",", ":")).encode()
    ls = LineageStore(db)
    ls.record_turn(query="q", injected=[], contradictions=[], model_id=wire.MODEL, response_text="a",
                   prompt_prefix="p", answer_text="a", request_hash=hashlib.sha256(body).hexdigest(),
                   request_body=body, inference_seed=7, model_digest=DIGEST,
                   inference_config={"provider": "ollama", "endpoint": "/api/chat", "model": wire.MODEL,
                                     "stream": True, "options": {"num_ctx": 8192}, rc.SEPARATION_KEY: True},
                   environment={"os": sys.platform})
    ls.close()
    return db


def test_a_store_under_an_awkward_path_is_read_and_nothing_is_created(tmp_path, monkeypatch):
    """RC-9 (S5). `C#`, `%20`, a space and `a?b` are folder names, not URI
    syntax: the store under them is read read-only, a write is refused, and
    no stray file appears (before part 4 a `#` cut the path short and SQLite
    created `C`)."""
    # `?` cannot be in a Windows file name; `#`, `%` and a space can.
    names = ("C#", "pct%20x", "sp ace") + (("a?b",) if sys.platform != "win32" else ())
    monkeypatch.setattr(replay_check, "nexus_serving", lambda: None)
    for name in names:
        db = _lineage_only_store(tmp_path / name / "data")
        listing = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = replay_check.main(["--db", str(db), "--report-dir", str(tmp_path / "r" / name.replace("?", "_"))])
        new = sorted(set(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) - set(listing))
        print(f"\nRC-9 ({sys.platform}) {name!r}: exit {code}; new paths {new}; output: {out.getvalue()!r}")
        assert code == 0 and "would send 1" in out.getvalue()
        assert all(Path(n).parts[0] == "r" for n in new), new          # only the report directory is new
        conn = open_read_only(db)
        with pytest.raises(sqlite3.DatabaseError, match="not authorized|readonly database"):
            conn.execute("DELETE FROM lineage_records")
        conn.close()


def test_an_edited_options_row_or_another_model_is_never_re_sent(store):
    """RC-10 (S3). The options are trusted only through the key the record
    seals. An edited row (num_ctx 8192 to 2048) is not replayable, because
    the row no longer hashes to the record's key; an options row naming
    another model is named too. The daemon is never asked."""
    db, ao, ids, model = store
    conn = sqlite3.connect(db)
    key = conn.execute("SELECT inference_config_hash FROM lineage_records WHERE lineage_id = ?",
                       (ids[0],)).fetchone()[0]
    conn.execute("UPDATE inference_configs SET config_json = replace(config_json, '8192', '2048') "
                 "WHERE config_hash = ?", (key,))
    conn.commit()
    conn.close()
    daemon = _Daemon({model: DIGEST}, lambda body: pytest.fail("an edited options row was re-sent"))
    rep = _check(db, daemon, ao)
    print(f"\nRC-10 ({sys.platform}) {rep.counts()}")
    assert rep.counts()["not_replayable_by_reason"] == {"the options row is corrupted": 4}
    assert daemon.sent == []


def test_it_refuses_a_store_that_is_not_nexus_and_a_count_below_one(tmp_path, monkeypatch):
    """RC-11. A database with no lineage_records is refused by name (exit 2,
    no traceback), and so is --last 0. Nothing is written in either case."""
    other = tmp_path / "other.db"
    c = sqlite3.connect(other)
    c.execute("CREATE TABLE t (x)")
    c.commit()
    c.close()
    monkeypatch.setattr(replay_check, "nexus_serving", lambda: None)
    for argv, why in ((["--db", str(other)], "is not a NEXUS store"),
                      (["--db", str(other), "--last", "0"], "--last 0")):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = replay_check.main(argv + ["--report-dir", str(tmp_path / "r")])
        print(f"\nRC-11 ({sys.platform}) {argv[2:]}: exit {code}: {out.getvalue().splitlines()[1]}")
        assert code == replay_check.EXIT_ARGS and why in out.getvalue()
    assert not (tmp_path / "r").exists()


class _OtherModel(OllamaProvider):
    """An Ollama provider whose recorded options name another model than the
    one it serves: the record verifies and the row hashes to its key, so only
    the model check stands between it and a re-send."""

    def request_config(self, model, num_ctx, *, separate):
        return {**super().request_config(model, num_ctx, separate=separate), "model": "other:1"}


def test_an_options_row_naming_another_model_is_never_re_sent(tmp_path, monkeypatch):
    """RC-10b (verifier 2's SF4). The model check, reached on its own."""
    db, ao, _ids, model, _sent = _make_store(tmp_path, monkeypatch)
    svc = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma-other",   # see _more_turns
                          eval_log_path=tmp_path / "m.jsonl", archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap", provider=_OtherModel())
    r = TestClient(create_app(svc)).post("/chat", headers=H, json={"message": "which model?"})
    lid = json.loads([f for f in r.text.split("\n\n") if f.startswith("event: done")][-1].split("data: ", 1)[1])["lineage_id"]
    svc.lineage.close()
    svc.store.close()
    daemon = _Daemon({model: DIGEST}, lambda body: answer_for(body["options"]["seed"]))
    rep = _check(db, daemon, ao)
    by_id = {o.candidate.lineage_id: o for o in rep.outcomes}
    print(f"\nRC-10b ({sys.platform}) {by_id[lid].detail}")
    assert by_id[lid].detail == f"the options name model 'other:1', the record {model!r}"
    assert all(b["model"] == model for _e, b in daemon.sent)


def test_a_tampered_or_re_keyed_record_is_never_re_sent(store):
    """RC-12 (verifier 2's SF4). An edited options row stored under its own
    correct hash, with the record's key moved to match, passed every
    row-level check and was re-sent. The record's own hash is now checked
    first, so it is refused; so is a record whose sealed notes were edited."""
    db, ao, ids, model = store
    conn = sqlite3.connect(db)
    old_key, text = conn.execute(
        "SELECT c.config_hash, c.config_json FROM lineage_records r JOIN inference_configs c "
        "ON c.config_hash = r.inference_config_hash WHERE r.lineage_id = ?", (ids[0],)).fetchone()
    edited = text.replace("8192", "2048")
    new_key = hashlib.sha256(edited.encode("utf-8")).hexdigest()
    conn.execute("INSERT INTO inference_configs (config_hash, config_json, first_seen_ts) VALUES (?, ?, 0)",
                 (new_key, edited))
    conn.execute("UPDATE lineage_records SET inference_config_hash = ? WHERE lineage_id = ?", (new_key, ids[0]))
    conn.execute("UPDATE lineage_records SET capture_notes = ? WHERE lineage_id = ?",
                 (json.dumps({"note": "planted"}), ids[1]))
    conn.commit()
    conn.close()
    daemon = _Daemon({model: DIGEST}, lambda body: answer_for(body["options"]["seed"]))
    rep = _check(db, daemon, ao)
    by_id = {o.candidate.lineage_id: o for o in rep.outcomes}
    print(f"\nRC-12 ({sys.platform}) {by_id[ids[0]].detail} | {by_id[ids[1]].detail}")
    for lid in ids[:2]:
        assert by_id[lid].detail == "the record does not verify: its fields do not hash to its record_hash"
    assert all(b["options"]["num_ctx"] == 8192 for _e, b in daemon.sent) and len(daemon.sent) == 2
