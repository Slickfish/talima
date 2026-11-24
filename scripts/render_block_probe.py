"""INVESTIGATE — what the answering model is actually told. Read-only.

    python scripts/render_block_probe.py --query "what do we know about X?"
        [--query "..."]... [--kb data/kb.db]
        [--capture data/citation_capture.jsonl] [--out render_block.json]

Every turn's system message is `STABLE_SYSTEM_PREFIX + dynamic_block`
(agentic_loop.py). The dynamic block is built by `assemble_context` from
retrieval and a propagation over the association graph, and one of its
sections renders each top-firing node's epistemic state and evidential
weight — values nothing in the backend writes after node creation.

This probe runs the PRODUCTION `assemble_context` on a COPY of the store
with the persisted graph rehydrated exactly as the server does at boot, and
prints, for each query:

  [1] the string that would go into the prompt, verbatim — prefix and
      dynamic block — with the block's lines parsed: which epistemic tags
      appear and how often; which rendered nodes are claim concepts and
      which are bare query terms with no claim behind them; the activation
      and column values, which are the per-turn parts;
  [2] what a record keeps of it: `lineage_records.prompt_prefix_hash` is
      compared with the hash of the STABLE prefix over every record, so the
      report says whether the dynamic block has ever been recorded;
  [3] a leak scan over captured answers (AO capture, when present) for the
      render's own phrases — a cheap first look at whether the tags reach
      the answer text.

The original store is never opened for writing: it is copied to a temp
directory and the copy is discarded; the original's md5 is printed before
and after. The calibration log is redirected to the temp directory so the
turn's accrual (if any) lands nowhere durable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402

# AW — narrow lines are `  <node> (act=0.62)`; legacy lines carry a tag and a
# column. Both parse, so the probe reads either render.
LINE_RE = re.compile(r"^  (?P<node>.+?)(?: \[(?P<tag>[^\]]+)\])? \(act=(?P<act>[0-9.]+)(?: src=(?P<src>[a-z_]+))?(?:, col=(?P<col>[^)]+))?\)$")
RENDER_PHRASES = ("partial knowledge", "use with caution", "evidential weight",
                  "knowledge gap", "inferred by association", "contradictory evidence",
                  "referenced but uncharted")


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else "-"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--query", action="append", required=True,
                    help="a question to assemble context for (repeatable)")
    ap.add_argument("--capture", default="data/citation_capture.jsonl")
    ap.add_argument("--out", default="render_block.json")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    kb = Path(args.kb)
    if not kb.is_file():
        print(f"MISSING: {kb}")
        return 2
    md5_before = _md5(kb)

    tmp = Path(tempfile.mkdtemp(prefix="nexus_render_"))
    os.environ["NEXUS_CALIBRATION_LOG"] = str(tmp / "calibration.jsonl")
    copy = tmp / "kb.db"
    shutil.copy(kb, copy)
    try:
        from backend.kb.retriever import TripleRetriever
        from backend.kb.schema import SUBSTRATE_RELATIONS
        from backend.kb.tiered_store import TripleStore
        from backend.loop.agentic_loop import STABLE_SYSTEM_PREFIX, assemble_context
        from backend.memory.graph_store import HebbianGraphStore
        from backend.memory.hippocampus import Hippocampus

        store = TripleStore(copy)
        hippo = Hippocampus()
        restored = HebbianGraphStore(copy).load_into(hippo)
        retriever = TripleRetriever(store)
        con = open_read_only(copy, row_factory=None)
        con.row_factory = sqlite3.Row
        claim_concepts: set[str] = set()
        for r in con.execute("SELECT subject, relation, object FROM triples WHERE archived = 0"):
            if r["relation"] not in SUBSTRATE_RELATIONS:
                claim_concepts.add(r["subject"]); claim_concepts.add(r["object"])

        report: dict = {"kb": str(kb), "restored_edges": restored,
                        "graph_nodes": len(hippo.nodes),
                        "stable_prefix": STABLE_SYSTEM_PREFIX, "queries": []}
        print(f"restored {restored} association edges, {len(hippo.nodes)} nodes; "
              f"{len(claim_concepts)} claim concepts\n")
        print("=== STABLE_SYSTEM_PREFIX (verbatim) ===")
        print(STABLE_SYSTEM_PREFIX)

        # ---- [1] the block, per query ------------------------------------------
        for q in args.query:
            ctx = assemble_context(q, retriever, hippo)
            block = ctx.block
            hippo.relax_all()     # what run_turn does at the end of a turn
            lines = [ln for ln in block.splitlines() if LINE_RE.match(ln)]
            parsed = [LINE_RE.match(ln).groupdict() for ln in lines]
            tags = Counter(p["tag"] or "<none>" for p in parsed)
            cols = Counter(p["col"] or "<none>" for p in parsed)
            not_claims = [p["node"] for p in parsed if p["node"] not in claim_concepts]
            # KB CONTEXT lines only — under AW's narrow render substrate rows
            # sit in their own ASSOCIATIONS section, which is not the KB half.
            kb_lines: list[str] = []
            for part in block.split("\n\n"):
                if part.startswith("[KB CONTEXT"):
                    kb_lines = [ln for ln in part.splitlines()[1:] if ln.startswith("(")]
            substrate_in_kb = [ln for ln in kb_lines
                               if any(f" {rel} " in ln for rel in SUBSTRATE_RELATIONS)]
            entry = {
                "query": q, "block": block, "block_chars": len(block),
                "kb_context_lines": len(kb_lines),
                "kb_context_substrate_lines": len(substrate_in_kb),
                "cortex_lines": len(parsed), "tags": dict(tags), "columns": dict(cols),
                "rendered_nodes_without_a_claim": not_claims,
                "activations": [float(p["act"]) for p in parsed],
            }
            report["queries"].append(entry)
            print(f"\n=== QUERY: {q!r} ===")
            print("=== DYNAMIC BLOCK (verbatim, appended to the prefix) ===")
            print(block if block else "<empty — no retrieval hit, so no block at all>")
            print("=== END BLOCK ===")
            print(f"  kb-context lines {len(kb_lines)} (substrate rows among them "
                  f"{len(substrate_in_kb)}); cortex lines {len(parsed)}; tags {dict(tags)}; "
                  f"columns {dict(cols)}; rendered nodes with no claim behind them: "
                  f"{not_claims}")
        con.close()
        store.close()

        # ---- [2] what the record keeps ------------------------------------------
        con = open_read_only(copy, row_factory=None)
        con.row_factory = sqlite3.Row
        tables = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        rec: dict = {"lineage_records": 0}
        if "lineage_records" in tables:
            hashes = Counter(r["prompt_prefix_hash"] for r in
                             con.execute("SELECT prompt_prefix_hash FROM lineage_records"))
            stable = _sha256(STABLE_SYSTEM_PREFIX)
            cols = {r["name"] for r in con.execute("PRAGMA table_info(lineage_records)")}
            with_context = (con.execute(
                "SELECT COUNT(*) FROM lineage_records WHERE context_json IS NOT NULL"
            ).fetchone()[0] if "context_json" in cols else 0)
            rec = {"lineage_records": sum(hashes.values()),
                   "records_whose_prefix_hash_is_the_stable_prefix": hashes.get(stable, 0),
                   "distinct_prefix_hashes": len(hashes),
                   # AW — the dynamic block is recorded (hash + rendered cortex
                   # pairs) in `context_json` on every record written since.
                   "records_with_a_recorded_block": with_context,
                   "records_without": sum(hashes.values()) - with_context,
                   "note": ("prompt_prefix_hash is sha256(STABLE_SYSTEM_PREFIX) by "
                            "construction; pre-AW records recorded the dynamic "
                            "block nowhere — KB CONTEXT re-derives from `injected` "
                            "+ the store, the cortex half does not. Records with "
                            "`context_json` carry the block's hash and the cortex "
                            "pairs, and `backend.loop.prompt_render.derive_block` "
                            "rebuilds the store-derived sections from them.")}
        con.close()
        report["record"] = rec
        print(f"\n[2] record: {rec}")

        # ---- [3] leak scan --------------------------------------------------------
        cap = Path(args.capture)
        leak: dict = {"capture_exists": cap.is_file(), "answers": 0, "hits": {}}
        if cap.is_file():
            hits: Counter = Counter()
            examples: dict[str, str] = {}
            for line in cap.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                ans = (json.loads(line).get("answer") or "")
                leak["answers"] += 1
                low = ans.lower()
                for ph in RENDER_PHRASES:
                    if ph in low:
                        hits[ph] += 1
                        i = low.find(ph)
                        examples.setdefault(ph, ans[max(0, i - 60):i + 80])
            leak["hits"] = dict(hits)
            leak["examples"] = examples
        report["leak_scan"] = leak
        print(f"\n[3] leak scan over captured answers: {leak}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    print(f"\nwrote {args.out}")
    md5_after = _md5(kb)
    print(f"store md5 {md5_before} -> {md5_after} "
          f"{'UNCHANGED' if md5_after == md5_before else '*** CHANGED ***'}")
    return 0 if md5_after == md5_before else 3


if __name__ == "__main__":
    sys.exit(main())
