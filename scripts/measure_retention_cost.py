"""AS — does retention cost anything? Read-only measurement over the records.

    python scripts/measure_retention_cost.py [--kb data/kb.db]
        [--tokeniser hf|ollama|stub] [--tokenizer-json PATH | --hf-repo REPO]
        [--ollama-url URL] [--capture data/citation_capture.jsonl]
        [--eval-log data/turn_metrics.jsonl] [--out retention_cost.json]

Per lineage record (every turn since F.5's cited flag, not only the forty
captured — the injected set is reconstructible from `injected[].belief_id`
against the store, rendered by production's own `RetrievedTriple.render`):
the injected tokens counted with the answering model's tokeniser, the three
wasted categories as token shares — duplicate-bearing (AQ's curated
grouping), malformed (the ingest gate's rules under the row's class),
uncontributing (the lexical cited flag, caveat carried) — and the
denominator beside them. Then H2: the spread of the per-turn share and what
it correlates with. Thresholds and rules are pre-registered in scripts/_as.py.

The store is opened read-only and its md5 is printed before and after.
`--tokeniser stub` runs the mechanics on whitespace tokens: its output is
marked NOT_A_MEASUREMENT, no verdict is drawn, and the exit code is 4.
A real tokeniser that cannot be reached exits 5 rather than falling back.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._as import (                                        # noqa: E402
    PRIMARY, SECONDARY, THRESHOLDS, TokeniserUnavailable, evaluate,
    hf_tokeniser, ollama_tokeniser, relation_aliases, stub_tokeniser,
    tokeniser_repo_for, turn_shares,
)
from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402

KB_HEADER = "[KB CONTEXT — retrieved triples ranked by current weight]\n"


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else "-"


def _render(row, score: float) -> str:
    """Production's renderer over the store row — no second copy."""
    from backend.kb.retriever import RetrievedTriple
    return RetrievedTriple(triple=row, score=float(score)).render()


def _load_eval_log(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _nearest_eval(evals: list[dict], ts: float, window: float = 60.0) -> dict | None:
    best = None
    for e in evals:
        d = abs(float(e.get("timestamp", 0.0)) - ts)
        if d <= window and (best is None or d < best[0]):
            best = (d, e)
    return best[1] if best else None


def trail_active_pairs(con) -> list[tuple[str, str]]:
    """AB's active trail pairs, read through the store's own classifier
    on the read-only connection — the constructor is bypassed because it
    runs DDL, and a measurement may not write."""
    has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                      "name='relation_trails'").fetchone()
    if not has:
        return []
    from backend.kb.trails import TrailStore
    ts = TrailStore.__new__(TrailStore)
    ts.conn = con
    return [tuple(p) for p in ts._classified()["active"]]


def build_turns(con, *, count_for_model, limit: int | None,
                evals: list[dict], capture: dict[str, dict],
                aliases: dict | None = None) -> tuple[list[dict], dict]:
    from backend.kb.tiered_store import TripleStore
    store = TripleStore.__new__(TripleStore)          # read-only: only get_by_id
    store.conn = con
    sql = "SELECT * FROM lineage_records ORDER BY ts ASC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    turns, coverage = [], {"records": 0, "with_injected": 0,
                           "flags_absent_records": 0, "unresolved_items": 0,
                           "capture_matched": 0, "capture_surface_mismatch": 0}
    for rec in con.execute(sql):
        coverage["records"] += 1
        injected = json.loads(rec["injected_json"])
        if not injected:
            continue
        coverage["with_injected"] += 1
        if any("cited" not in it for it in injected):
            coverage["flags_absent_records"] += 1
        count = count_for_model(rec["model_id"])
        items = []
        cap = capture.get(rec["lineage_id"])
        cap_surfaces = ({it["belief_id"]: it["surface"] for it in cap["injected"]}
                        if cap else {})
        if cap:
            coverage["capture_matched"] += 1
        for it in injected:
            row = store.get_by_id(int(it["belief_id"])) if it.get("belief_id") is not None else None
            if row is None:
                coverage["unresolved_items"] += 1
                continue
            surface = _render(row, it.get("weight_at_injection", 0.0))
            if cap_surfaces and cap_surfaces.get(row.id) not in (None, surface):
                coverage["capture_surface_mismatch"] += 1
            items.append({
                "belief_id": row.id, "subject": row.subject,
                "relation": row.relation, "object": row.object,
                "source": row.source, "weight": it.get("weight_at_injection"),
                "cited": it.get("cited"), "surface": surface,
                "tokens": count(surface),
            })
        if not items:
            continue
        t = turn_shares(items, aliases)
        t["lineage_id"] = rec["lineage_id"]
        t["ts"] = rec["ts"]
        t["model_id"] = rec["model_id"]
        # AU — which citation test produced this turn's `cited` flags (NULL
        # = F.5, before the instrument was versioned); the uncontributing
        # share is only comparable within one version.
        t["citation_test"] = (rec["citation_test"]
                              if "citation_test" in rec.keys() else None)
        t["block_tokens"] = count(KB_HEADER + "\n".join(i["surface"] for i in items))
        ev = _nearest_eval(evals, float(rec["ts"]))
        if ev:
            t["context_precision"] = ev.get("context_precision")
            t["faithfulness"] = ev.get("faithfulness")
        turns.append(t)
    return turns, coverage


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--tokeniser", choices=("hf", "ollama", "stub"), default="hf")
    ap.add_argument("--tokenizer-json", default=None,
                    help="a local tokenizer.json (hf backend); overrides the map")
    ap.add_argument("--hf-repo", default=None,
                    help="HF repo to fetch tokenizer.json from; overrides the map")
    ap.add_argument("--ollama-url", default=None)
    ap.add_argument("--capture", default="data/citation_capture.jsonl",
                    help="AO capture, used only to cross-check the reconstructed surfaces")
    ap.add_argument("--eval-log", default="data/turn_metrics.jsonl",
                    help="joined by nearest timestamp (60s) for the H2 signals")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="retention_cost.json")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    kb = Path(args.kb)
    if not kb.is_file():
        print(f"MISSING: {kb}")
        return 2
    md5_before = _md5(kb)
    con = open_read_only(kb, row_factory=None)
    con.row_factory = sqlite3.Row
    tables = {r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"lineage_records", "triples"} <= tables:
        print(f"NO RECORDS: {kb} has no lineage_records/triples tables — "
              f"a store no turn has ever been answered from")
        return 2

    from backend.loop.agentic_loop import STABLE_SYSTEM_PREFIX
    import os
    base_url = args.ollama_url or os.getenv("OLLAMA_URL", "http://localhost:11434")

    instruments: dict[str, dict] = {}
    counters: dict[str, object] = {}

    def count_for_model(model_id: str):
        key = model_id or "?"
        if key in counters:
            return counters[key]
        try:
            if args.tokeniser == "stub":
                fn, inst = stub_tokeniser()
            elif args.tokeniser == "ollama":
                fn, inst = ollama_tokeniser(base_url=base_url, model=model_id)
            else:
                repo = tokeniser_repo_for(model_id, args.hf_repo)
                fn, inst = hf_tokeniser(tokenizer_json=args.tokenizer_json, repo=repo)
        except TokeniserUnavailable as e:
            print(f"TOKENISER UNAVAILABLE for {model_id!r}: {e}")
            print("a measurement may not fall back — exit 5")
            sys.exit(5)
        counters[key] = fn
        instruments[key] = {**inst, "model_id": model_id}
        return fn

    evals = _load_eval_log(Path(args.eval_log))
    capture: dict[str, dict] = {}
    cap_path = Path(args.capture)
    if cap_path.is_file():
        for line in cap_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    row = json.loads(line)
                    capture[row["lineage_id"]] = row
                except (ValueError, KeyError):
                    continue

    # AT (term identity) — the grouping consumes AB's derived pairs and the
    # digit-safe term key; counting only, nothing else moves.
    pairs = trail_active_pairs(con)
    aliases = relation_aliases(pairs)
    turns, coverage = build_turns(con, count_for_model=count_for_model,
                                  limit=args.limit, evals=evals, capture=capture,
                                  aliases=aliases)
    con.close()
    measurement = bool(turns) and not any(
        i.get("NOT_A_MEASUREMENT") for i in instruments.values())
    if turns:
        any_count = counters[next(iter(counters))]
        prefix_tokens = any_count(STABLE_SYSTEM_PREFIX)
        framing_tokens = any_count(KB_HEADER)
    else:
        prefix_tokens = framing_tokens = 0
    report = evaluate(turns, prefix_tokens=prefix_tokens,
                      framing_tokens=framing_tokens, measurement=measurement,
                      aliases=aliases, trail_pairs=len(pairs))
    report["coverage"] = coverage
    report["instrument"] = instruments
    # AU — the series split by the citation test that flagged each turn.
    versions: dict = {}
    for t in turns:
        v = t.get("citation_test") or "f5.1-lexical (pre-versioning)"
        versions.setdefault(v, {"turns": 0, "uncontributing_tokens": 0,
                                "tokens_injected": 0})
        versions[v]["turns"] += 1
        versions[v]["uncontributing_tokens"] += t["uncontributing"]["tokens"]
        versions[v]["tokens_injected"] += t["tokens_injected"]
    report["citation_test_versions"] = versions
    report["eval_log_joined"] = sum(1 for t in turns if "context_precision" in t)
    report["turn_rows"] = [{k: v for k, v in t.items() if k != "items"} for t in turns]
    if not measurement:
        report["NOT_A_MEASUREMENT"] = True

    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    s = report["shares"]
    print(f"lineage records {coverage['records']}, with injected "
          f"{coverage['with_injected']}, turns measured {report['turns']}, "
          f"injected tokens {report['tokens_injected_total']}")
    for cat in ("duplicate_bearing", "malformed", "uncontributing", PRIMARY, SECONDARY):
        sh = s[cat]["share"]
        print(f"  {cat:34} {s[cat]['tokens']:7d} tokens  "
              f"{'-' if sh is None else f'{sh:6.1%}'}")
    d = report["denominator"]
    inj_share = d["injected_share_of_reconstructed_prompt"]
    inj_share_s = "-" if inj_share is None else f"{inj_share:.1%}"
    print(f"  denominator: prefix {d['system_prefix_tokens']} + framing "
          f"{d['kb_block_framing_tokens']} + injected/turn "
          f"{d['injected_tokens_per_turn_mean']:.1f} -> injected share of "
          f"reconstructed prompt {inj_share_s} (overstates; history not recorded)")
    h = report["h2"]
    print(f"  H2 spread: {h['spread']}  constant={h['constant']}")
    print(f"  H2 correlations: {h['correlations']}")
    print(f"  non-vacuity: {report['non_vacuity']}")
    print(f"  citation test versions: {report['citation_test_versions']}")
    g = report["grouping"]
    print(f"  grouping: aliases {g['alias_version']} (curated "
          f"{g['curated_alias_version']} + {g['trail_pairs']} trail pairs); "
          f"term key: {g['term_key']}")
    v = report["verdict"]
    print(f"  verdict ({v['primary']}): {v[PRIMARY]}   secondary: {v[SECONDARY]}   "
          f"thresholds {THRESHOLDS}")
    if not measurement:
        print("NOT_A_MEASUREMENT: stub tokeniser or no turns — no verdict drawn")
    print(f"wrote {args.out}")
    md5_after = _md5(kb)
    print(f"store md5 {md5_before} -> {md5_after} "
          f"{'UNCHANGED' if md5_after == md5_before else '*** CHANGED ***'}")
    if md5_after != md5_before:
        return 3
    return 0 if measurement else 4


if __name__ == "__main__":
    sys.exit(main())
