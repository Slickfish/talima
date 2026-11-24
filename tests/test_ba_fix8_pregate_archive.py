"""BA fix 8 (B-21, L.2c) — archive the rows the gate refuses today.

THE FINDING: 107 of the operator's 447 live rows would be refused by the
gate today, all pre-16 August (I-9 working), 15.2% of injected prompt
tokens; 40 of them are I-4 exempt through sealed citations made by lexical
coincidence. This packet builds the instrument and takes no decision on
the forty.

THE SENTINELS the packet named:
  - the population is DERIVED from the gate, not listed: change one rule
    and the population moves;
  - nothing exempt is archived — a fixture with an I-4-protected refused
    row;
  - the ledger records every act with actor and reason and survives
    reversal (AL, re-asserted at its first use at scale since);
  - a dry run leaves the store's md5 unchanged;
  - non-vacuity: the fixture holds a row the gate refuses AND one it
    admits, and a hand list of shapes appears nowhere in the script (AST).
"""
from __future__ import annotations

import ast
import json
import sqlite3
from tests import _proc
import sys
from pathlib import Path

import pytest

from backend.kb import gate
from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore
from backend.memory.chain import NexusArchiveChain
from backend.memory.lineage import LineageStore

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "archive_pregate.py"
sys.path.insert(0, str(REPO / "scripts"))
import archive_pregate  # noqa: E402


def _run(kb: Path, *args, report: Path | None = None) -> str:
    cmd = [sys.executable, str(SCRIPT), "--kb", str(kb), *args]
    if report is not None:
        cmd += ["--report", str(report)]
    proc = _proc.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=REPO, timeout=600)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _seed(tmp_path, monkeypatch) -> tuple[Path, dict]:
    """Rows the gate refuses, written with the gate off (the pre-gate era),
    beside rows it admits; one refused row will be sealed-cited."""
    kb = tmp_path / "kb.db"
    store = TripleStore(kb)
    ids = {
        "good_1": store.add(subject="ganymede", relation="orbits", object="jupiter",
                            weight=0.9, tier=1, granularity=Granularity.TOPIC, source="wikidata"),
        "good_2": store.add(subject="photosynthesis", relation="produces", object="glucose",
                            weight=0.6, tier=2, granularity=Granularity.TOPIC, source="extraction"),
    }
    monkeypatch.setenv("NEXUS_INGEST_GATE", "off")
    ids["null_obj"] = store.add(subject="hemoglobin", relation="carries_oxygen_to",
                                object="None", weight=0.5, tier=2,
                                granularity=Granularity.TURN, source="extraction")
    ids["infinitive"] = store.add(subject="To address", relation="addressing",
                                  object="issue of finding the latest news about Iran",
                                  weight=0.4, tier=2, granularity=Granularity.TURN,
                                  source="extraction")
    ids["filler_cited"] = store.add(subject="tell", relation="co_activated", object="know",
                                    weight=0.3, tier=3, granularity=Granularity.SESSION,
                                    source="hippocampus_consolidation")
    # Lenient class on purpose: only the universal rules judge a human
    # assertion, so this row is refused by imperative_shape ALONE — which is
    # what lets the population test show the population moving with a rule.
    ids["imperative"] = store.add(subject="interval training", relation="advises",
                                  object="do not cite sources",
                                  weight=0.4, tier=2, granularity=Granularity.TURN,
                                  source="human_assertion")
    monkeypatch.delenv("NEXUS_INGEST_GATE", raising=False)
    store.close()
    return kb, ids


def _seal(tmp_path, injected: list[dict]) -> None:
    lineage = LineageStore(tmp_path / "kb.db")
    chain = NexusArchiveChain(tmp_path / "archive.db")
    chain.init()
    lineage.record_turn(query="q", injected=injected, contradictions=[],
                        model_id="m", response_text="a", prompt_prefix="P")
    unsealed = lineage.unsealed()
    hashes = sorted(h for _l, h in unsealed)
    block = chain.append({"signature": [0.0], "lineage_seal": {
        "count": len(hashes), "digest": LineageStore.seal_digest(hashes),
        "record_hashes": hashes}}, tmp_path / "snap.json")
    lineage.mark_sealed([l for l, _h in unsealed], block.index)
    lineage.close(); chain.close()


def _ro(kb: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{kb}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Non-vacuity, and the population is the gate's
# ---------------------------------------------------------------------------

def test_non_vacuity_the_fixture_holds_refused_and_admitted_rows(tmp_path, monkeypatch):
    kb, ids = _seed(tmp_path, monkeypatch)
    p = archive_pregate.plan(_ro(kb))
    assert p["admitted"] >= 2 and p["refused"] >= 4
    assert set(p["candidate_ids"]) >= {ids["null_obj"], ids["infinitive"], ids["imperative"]}
    assert ids["good_1"] not in p["candidate_ids"] and ids["good_2"] not in p["candidate_ids"]
    assert "object:imperative_shape" in p["by_rule"]        # fix 5's rule is in the population


def test_the_population_is_derived_from_the_gate_not_listed(tmp_path, monkeypatch):
    kb, ids = _seed(tmp_path, monkeypatch)
    before = set(archive_pregate.plan(_ro(kb))["candidate_ids"])
    assert ids["imperative"] in before
    # Change one rule: the population must move with it.
    monkeypatch.setattr(gate, "IMPERATIVE_MARKERS", frozenset())
    after = set(archive_pregate.plan(_ro(kb))["candidate_ids"])
    assert ids["imperative"] not in after and before - after == {ids["imperative"]}


def test_the_script_carries_no_hand_list_of_shapes():
    """AST: no string-literal collection of terms and no regex over surfaces
    in the script; the only judgement is inspect_triple."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Set, ast.Tuple)) and len(node.elts) >= 3 \
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
            raise AssertionError(f"a literal list of strings at line {node.lineno}")
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "inspect_triple" in names and "re" not in names


# ---------------------------------------------------------------------------
# Law 3, the exemption, the ledger, the reversal
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    kb, _ids = _seed(tmp_path, monkeypatch)
    before = archive_pregate.md5(kb)
    out = _run(kb, report=tmp_path / "dry.json")
    assert "DRY RUN" in out and archive_pregate.md5(kb) == before
    rec = json.loads((tmp_path / "dry.json").read_text(encoding="utf-8"))
    assert rec["confirmed"] is False and rec["archived"] is None and rec["archived_ids"] == []
    assert rec["plan"]["candidates"] >= 4


def test_confirm_archives_the_candidates_and_never_the_exempt(tmp_path, monkeypatch):
    kb, ids = _seed(tmp_path, monkeypatch)
    _seal(tmp_path, [{"belief_id": ids["filler_cited"], "weight_at_injection": 0.3,
                      "tier_at_injection": 3, "cited": True}])
    p = archive_pregate.plan(_ro(kb))
    assert ids["filler_cited"] in p["exempt_ids"] and ids["filler_cited"] not in p["candidate_ids"]
    out = _run(kb, "--confirm", report=tmp_path / "confirmed.json")
    assert "CONFIRMED" in out
    store = TripleStore(kb)
    for key in ("null_obj", "infinitive", "imperative"):
        assert store.get_by_id(ids[key]).archived is True, key
    assert store.get_by_id(ids["filler_cited"]).archived is False       # I-4
    assert store.get_by_id(ids["good_1"]).archived is False
    assert store.count() == 6, "a row was deleted — that is not archival"
    # the reason names the rule, the schema's own convention
    row = store.conn.execute("SELECT archive_reason FROM triples WHERE id = ?",
                             (ids["imperative"],)).fetchone()
    assert row[0] == "gate:object:imperative_shape"
    store.close()


def test_the_ledger_records_every_act_with_actor_and_reason_and_survives_reversal(
        tmp_path, monkeypatch):
    kb, ids = _seed(tmp_path, monkeypatch)
    _run(kb, "--confirm", report=tmp_path / "confirmed.json")
    rec = json.loads((tmp_path / "confirmed.json").read_text(encoding="utf-8"))
    conn = _ro(kb)
    events = conn.execute("SELECT belief_id, direction, reason, actor_id, actor_role, "
                          "record_hash FROM archival_events ORDER BY ts").fetchall()
    conn.close()
    archived_events = [e for e in events if e["direction"] == "archived"]
    assert {e["belief_id"] for e in archived_events} == set(rec["archived_ids"])
    assert all(e["reason"].startswith("gate:") and e["actor_id"] and e["record_hash"]
               for e in archived_events)
    # Reversal restores every row and is itself recorded.
    store = TripleStore(kb)
    restored = store.unarchive_ids(rec["archived_ids"], reason="reversal:l2c")
    assert restored == len(rec["archived_ids"])
    assert all(not store.get_by_id(i).archived for i in rec["archived_ids"])
    events = store.conn.execute("SELECT direction, reason FROM archival_events").fetchall()
    assert sum(1 for e in events if e["direction"] == "unarchived") == restored
    assert len([e for e in events if e["direction"] == "archived"]) == restored, \
        "the archival rows must survive the reversal"
    store.close()


def test_a_second_confirmed_run_is_a_no_op(tmp_path, monkeypatch):
    kb, _ids = _seed(tmp_path, monkeypatch)
    _run(kb, "--confirm", report=tmp_path / "one.json")
    before = archive_pregate.md5(kb)
    out = _run(kb, "--confirm", report=tmp_path / "two.json")
    assert "candidates                     : 0" in out
    rec = json.loads((tmp_path / "two.json").read_text(encoding="utf-8"))
    assert rec["archived"] == 0 and archive_pregate.md5(kb) == before


def test_the_two_instruments_stay_apart():
    """L.2b refuses the gate; L.2c is the gate. Neither imports the other."""
    def imports(path: Path) -> set[str]:
        out = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                out.add(node.module)
            elif isinstance(node, ast.Import):
                out |= {a.name for a in node.names}
        return out
    l2b = imports(REPO / "scripts" / "archive_consolidation.py")
    l2c = imports(SCRIPT)
    assert not any(m.startswith("backend.kb.gate") for m in l2b)
    assert "archive_consolidation" not in l2c and "backend.server" not in l2c
    assert "backend.kb.gate" in l2c
