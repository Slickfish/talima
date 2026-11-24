"""AQ step 1 — the verbalisation map, versioned, before any score exists.

Reads the capture key (belief ids) and the store (mode=ro) to count
relation forms across the forty turns, hand-writes the head from the
HAND_WRITTEN table in scripts/_aq.py, templates the tail by stripping
underscores, and writes ONE versioned artefact marking every entry hand or
template. Refuses to finalise if any top-twenty relation is templated —
add its phrasing to HAND_WRITTEN and re-run.

Also writes the seeded SPOT-CHECK: fifty templated forms rendered on real
(subject, object) pairs from the capture, for a human to read in full
looking for PATTERNING — a class of relations coming out backwards would
corrupt a class of judgements; an individual clumsy phrasing is noise. If
patterning shows, the experiment pauses.

    python scripts/aq_verbalise.py [--key citation_stripped.key.json]
        [--kb data/kb.db] [--seed 7] [--out aq_vmap.json]
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

from scripts._aq import build_map, spot_check                    # noqa: E402
from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", default="citation_stripped.key.json")
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="aq_vmap.json")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    kb = Path(args.kb)
    md5_before = hashlib.md5(kb.read_bytes()).hexdigest() if kb.is_file() else "-"
    key = json.loads(Path(args.key).read_text(encoding="utf-8"))
    con = open_read_only(kb, row_factory=None)
    con.row_factory = sqlite3.Row

    freq: dict[str, int] = {}
    examples: dict[str, tuple[str, str]] = {}
    for row in key["rows"]:
        for it in row["items"]:
            r = con.execute("SELECT subject, relation, object FROM triples "
                            "WHERE id = ?", (it["belief_id"],)).fetchone()
            if not r:
                continue
            freq[r["relation"]] = freq.get(r["relation"], 0) + 1
            examples.setdefault(r["relation"], (r["subject"], r["object"]))
    con.close()

    vmap = build_map(freq)
    print(f"relation forms: {len(freq)} | hand {vmap['hand_count']} | "
          f"template {vmap['template_count']} | version {vmap['version']}")
    if vmap["top_n_templated"]:
        print(f"REFUSED: top-{vmap['top_n']} forms still templated — add "
              f"phrasings to HAND_WRITTEN and re-run: {vmap['top_n_templated']}")
        return 4

    check = spot_check(vmap, examples, seed=args.seed, n=50)
    out = Path(args.out)
    out.write_text(json.dumps({**vmap, "seed": args.seed,
                               "spot_check": check},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSPOT-CHECK ({len(check)} templated forms, seed {args.seed}) — "
          f"read in full, looking for a CLASS coming out wrong:")
    for c in check:
        print(f"  [{c['relation']}]  {c['rendered']}")
    print(f"\nwrote {out}")
    md5_after = hashlib.md5(kb.read_bytes()).hexdigest() if kb.is_file() else "-"
    print(f"store md5 {md5_before} -> {md5_after} "
          f"{'UNCHANGED' if md5_after == md5_before else '*** CHANGED ***'}")
    return 0 if md5_after == md5_before else 3


if __name__ == "__main__":
    sys.exit(main())
