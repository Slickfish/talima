"""W11b (run/specs/W11b.md C2 to C4; RUN_WORKLIST W11b's exit): step 1's golden fixtures, held at the head.

The fixtures in `tests/fixtures/tb_golden/` were made once at W11b's base (`ac32452`, `v1.0.0-uat`'s code) and
committed alone (`4ca41c4`), each sha256 in that commit's message; the generator is `run/tb_golden/`, unchanged.

WHAT IS HELD HERE
    G1 Every credential and receipt step 1 made verifies at the head under `nexus-verify` and under `talima-verify`,
       each run as a stranger runs it (isolated, from a folder holding only the files it names), with the base's
       exit and the base's `--json` verdict, field for field.
    G2 The verdicts `nexus-verify` does not give hold as at the base: the HTTP signature against `identity.json`'s
       key, each proof, the kept request against its hash; and (G2b, SQLite only: they are SQLite files) the chain and
       the sealed records on copies of the stores.
    G3 CE's rig, six turns at the head: each turn's request bytes, `request_hash`, context hash and answer hash equal
       step 1's, byte for byte.
    G4 `talima-verify` prints what `nexus-verify` prints, line for line, with its own name in place of the other's:
       on every fixture and on an input it cannot read; on `--help`, `-h` and argparse's two errors (no argument, an
       unknown option), the same words in the same order (argparse indents its usage line's continuation by the
       name's length, and the names differ by one character).
    G5 `talima_verify.py` alone, without `nexus_verify.py` beside it: named, exit 2, no traceback.

LAW 16. G1 to G3 hold the head to what the base made: they pass at the base by construction and fail on a change to a
frozen format (`tests/test_tb_frozen_identifiers.py` T2 shows one: a respelled kind, and the base credential
refused). G4 is new with `talima-verify`: seen failing first at `75ad2d7`, where `scripts/talima_verify.py` does not
exist.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sys
import zlib
from dataclasses import asdict
from pathlib import Path

import pytest

from tests import _proc
from tests._tracked import REPO
from tests.fixtures.ce_rig import CERig

GOLDEN = REPO / "tests" / "fixtures" / "tb_golden"
BASE = json.loads((GOLDEN / "verdicts_at_base.json").read_text(encoding="utf-8"))
VERIFIERS = {"nexus-verify": "nexus_verify.py", "talima-verify": "talima_verify.py"}
CLI = sorted(k for k, v in BASE.items() if "args" in v)


def _stranger(tmp_path: Path, script: str, args: list[str]):
    """The verifier as a stranger runs it: `python -I`, from a new folder holding the two scripts and the files the
    arguments name, and nothing else."""
    where = tmp_path / f"stranger-{len(list(tmp_path.iterdir()))}"
    where.mkdir()
    for name in VERIFIERS.values():
        shutil.copy2(REPO / "scripts" / name, where / name)
    for arg in args:
        if (GOLDEN / arg).is_file():
            shutil.copy2(GOLDEN / arg, where / arg)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    return _proc.run([sys.executable, "-I", script, *args], cwd=where, env=env, timeout=120, capture_output=True,
                     text=True, encoding="utf-8", errors="replace")


def test_g0_the_fixtures_are_the_ones_step_1_committed():
    assert len(CLI) == 5 and {"store", "proof.json", "http_signed_request.json"} <= set(BASE), sorted(BASE)
    assert all(BASE[k]["exit"] == 0 and BASE[k]["verdict"]["verified"] for k in CLI), "non-vacuity: the base verified"


@pytest.mark.parametrize("verifier", sorted(VERIFIERS))
@pytest.mark.parametrize("fixture", CLI)
def test_g1_each_fixture_verifies_at_the_head_with_the_bases_verdict(tmp_path, fixture, verifier):
    base = BASE[fixture]
    proc = _stranger(tmp_path, VERIFIERS[verifier], base["args"] + ["--json"])
    assert proc.returncode == base["exit"], proc.stdout + proc.stderr
    assert json.loads(proc.stdout) == base["verdict"]


def test_g2_the_in_process_verdicts_are_the_bases(tmp_path):
    from backend.memory import http_signatures, proofs
    identity = json.loads((GOLDEN / "identity.json").read_text(encoding="utf-8"))
    signed = json.loads((GOLDEN / "http_signed_request.json").read_text(encoding="utf-8"))
    assert http_signatures.verify_request(bytes.fromhex(identity["public_key"]), method=signed["method"],
                                          url=signed["url"], headers=signed["headers"]) is \
        BASE["http_signed_request.json"]["verified"] is True
    for name in ("proof.json", "proof_tombstoned.json"):
        proof = json.loads((GOLDEN / name).read_text(encoding="utf-8"))
        assert proofs.verify_proof(proof) == BASE[name]["refusal"] is None, name
    capture = json.loads((GOLDEN / "replay_capture.json").read_text(encoding="utf-8"))
    raw = base64.b64decode(capture["request"]["bytes_b64"])
    assert (hashlib.sha256(raw).hexdigest() == capture["record"]["request_hash"]) is \
        BASE["replay_capture.json"]["request_matches_its_hash"] is True


@pytest.mark.sqlite_only("it opens copies of step 1's committed SQLite stores; under Postgres the store classes open "
                         "the test database instead, and the formats it checks are the files'")
def test_g2b_the_chain_and_the_sealed_records_on_copies_of_the_stores_are_the_bases(tmp_path):
    """G2's store half (W11c's Postgres run found it read the test database there): the chain and the sealed records
    of step 1's stores, opened from copies, as at the base."""
    from backend.memory.chain import NexusArchiveChain
    from backend.memory.lineage import LineageStore, verify_sealed_lineage
    for name in ("kb.db", "archive.db"):                  # opening a store writes its derived rows: copies only
        shutil.copy2(GOLDEN / "store" / name, tmp_path / name)
    chain, lineage = NexusArchiveChain(tmp_path / "archive.db"), LineageStore(tmp_path / "kb.db")
    try:
        sealed = verify_sealed_lineage(lineage, chain)
        assert json.loads(json.dumps(asdict(chain.verify_chain()))) == BASE["store"]["chain"]   # as JSON kept it
        assert {k: v for k, v in sealed.items() if not isinstance(v, (list, dict))} == BASE["store"]["sealed_lineage"]
    finally:
        lineage.close()
        chain.close()


def test_g3_the_ce_rigs_requests_are_the_bases_byte_for_byte(tmp_path):
    golden = json.loads((GOLDEN / "ce_rig_requests.json").read_text(encoding="utf-8"))
    rig = CERig(tmp_path / "rig", **golden["rig"])
    try:
        rows = rig.run(len(golden["turns"]))
        got = []
        for r in rows:
            rec = rig.lineage.get(r.lineage_id)
            kept = rig.lineage.conn.execute("SELECT encoding, body FROM request_bodies WHERE request_hash = ?",
                                            (rec.request_hash,)).fetchone()
            got.append({"turn": r.turn, "request_hash": rec.request_hash, "context_hash": rec.context["hash"],
                        "answer_hash": rec.answer_hash, "encoding": kept["encoding"],
                        "request_b64": base64.b64encode(zlib.decompress(kept["body"])).decode()})
    finally:
        rig.close()
    assert [t["turn"] for t in got] == [t["turn"] for t in golden["turns"]]
    for mine, base in zip(got, golden["turns"]):
        assert mine == base, f"turn {base['turn']}: {[k for k in base if mine.get(k) != base[k]]}"


def _named(text: str, name: str) -> str:
    return text.replace(name, "<the verifier>")


@pytest.mark.parametrize("fixture", CLI)
def test_g4_talima_verify_prints_what_nexus_verify_prints_with_its_own_name(tmp_path, fixture):
    args = BASE[fixture]["args"]
    nexus, talima = (_stranger(tmp_path, VERIFIERS[v], args) for v in ("nexus-verify", "talima-verify"))
    assert talima.returncode == nexus.returncode == BASE[fixture]["exit"]
    assert "talima-verify: VERIFIED" in talima.stdout and "nexus-verify" not in talima.stdout, talima.stdout
    assert _named(talima.stdout, "talima-verify").splitlines() == _named(nexus.stdout, "nexus-verify").splitlines()


def test_g4b_the_help_argparses_errors_and_an_unreadable_input_differ_only_in_the_name(tmp_path):
    """Word for word where argparse prints (its usage line wraps and indents by the name's length, and the names
    differ by one character), line for line where the verifier does."""
    for args, stream, exact in ((["--help"], "stdout", False), (["-h"], "stdout", False),
                                ([], "stderr", False), (["--no-such-option"], "stderr", False),
                                (["no-such-file.json", "--identity", "identity.json"], "stderr", True)):
        nexus, talima = (_stranger(tmp_path, VERIFIERS[v], args) for v in ("nexus-verify", "talima-verify"))
        assert talima.returncode == nexus.returncode, args
        n, t = _named(getattr(nexus, stream), "nexus-verify"), _named(getattr(talima, stream), "talima-verify")
        assert "<the verifier>" in t and "verify" not in t.replace("<the verifier>", ""), getattr(talima, stream)
        if exact:
            assert t.splitlines() == n.splitlines(), args
        else:
            assert t.split() == n.split(), args


def test_g5_talima_verify_without_its_verifier_beside_it_is_named_and_exits_2(tmp_path):
    """`talima_verify.py` runs `nexus_verify.py` from its own folder. Alone, it says so and exits 2 (an installation
    error, as argparse's usage errors are), never a traceback and never exit 1, which is NOT VERIFIED's."""
    alone = tmp_path / "alone"
    alone.mkdir()
    shutil.copy2(REPO / "scripts" / "talima_verify.py", alone / "talima_verify.py")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    proc = _proc.run([sys.executable, "-I", "talima_verify.py", "credential.json"], cwd=alone, env=env, timeout=60,
                     capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 2, proc.returncode
    assert "Traceback" not in proc.stderr and proc.stdout == "", proc.stdout + proc.stderr
    assert proc.stderr.startswith("talima-verify: cannot find nexus_verify.py beside "), proc.stderr


def test_g6_talima_verify_beside_an_older_verifier_is_named_and_exits_2(tmp_path):
    """Beside a `nexus_verify.py` from before talima-verify (v1.0.0-uat's: `main(argv=None)`, no name), it says so and
    exits 2, never a traceback and never exit 1. The older verifier is stood in by its shape, so the test needs no
    history."""
    older = tmp_path / "older"
    older.mkdir()
    shutil.copy2(REPO / "scripts" / "talima_verify.py", older / "talima_verify.py")
    (older / "nexus_verify.py").write_text("def force_utf8_stdio():\n    pass\n\n\ndef main(argv=None):\n"
                                           "    print('nexus-verify: VERIFIED')\n    return 0\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    proc = _proc.run([sys.executable, "-I", "talima_verify.py", "credential.json"], cwd=older, env=env, timeout=60,
                     capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "Traceback" not in proc.stderr and proc.stdout == "", proc.stdout + proc.stderr
    assert proc.stderr.startswith("talima-verify: the nexus_verify.py beside it is from before talima-verify"), \
        proc.stderr


def _beside(tmp_path, label: str, sibling: str):
    """`talima_verify.py` run as a stranger runs it, beside a `nexus_verify.py` holding `sibling`."""
    where = tmp_path / label
    where.mkdir()
    shutil.copy2(REPO / "scripts" / "talima_verify.py", where / "talima_verify.py")
    (where / "nexus_verify.py").write_text(sibling, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    return _proc.run([sys.executable, "-I", "talima_verify.py", "credential.json"], cwd=where, env=env, timeout=60,
                     capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_g6b_a_verifier_taking_the_name_by_keyword_is_run_not_refused(tmp_path):
    """The test of "older" is whether its main() accepts the name, by binding the call, not by a parameter's
    spelling: a main(argv=None, **options) that reads the name runs."""
    proc = _beside(tmp_path, "kwargs", "def force_utf8_stdio():\n    pass\n\n\ndef main(argv=None, **options):\n"
                                       "    print(options['name'] + ': VERIFIED')\n    return 0\n")
    assert proc.returncode == 0 and proc.stdout == "talima-verify: VERIFIED\n", (proc.returncode, proc.stdout,
                                                                                proc.stderr)


@pytest.mark.parametrize("label, sibling, said", [
    ("truncated", "def main(argv=None, name=None:\n", "talima-verify: cannot load the nexus_verify.py beside it"),
    ("no-main", "def force_utf8_stdio():\n    pass\n", "talima-verify: the nexus_verify.py beside it has no main()"),
    ("main-none", "def force_utf8_stdio():\n    pass\n\n\nmain = None\n",
     "talima-verify: the nexus_verify.py beside it has no main()"),
])
def test_g7_a_verifier_that_cannot_be_run_is_named_and_exits_2(tmp_path, label, sibling, said):
    """An installation error is never NOT VERIFIED's 1 and never a traceback: a sibling that does not load, or has
    no main() to call, is named and the exit is 2."""
    proc = _beside(tmp_path, label, sibling)
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "Traceback" not in proc.stderr and proc.stdout == "", proc.stdout + proc.stderr
    assert proc.stderr.startswith(said), proc.stderr
