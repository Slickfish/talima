"""AP.4 — the reference-set extract. READ ONLY over capture and store.

REDESIGNED per the adjudication-by-exception review: the unit is the ITEM,
not the row, and the human's task is built from strata rather than a
ten-row sample:

  - the CITED stratum (items the lexical test flagged) is EXHAUSTED — the
    human labels all of it, grouped, so the false-positive rate is fully
    measured with no dependence on the judge;
  - the UNCITED stratum is where the judge is load-bearing: the human
    labels every judge-USED item there (the false-negative candidates)
    plus a seeded, recorded sample of judge-UNUSED items sized for the
    shared-error ceiling wanted (rule of three: 30 -> ~10%, 45 -> ~7%);
  - CONSEQUENCE-BEARING items — beliefs whose cited flags already fed a
    mechanism (cited in a SEALED lineage record: the archival exemption
    and reinforcement rest on those) — enter the human set
    unconditionally, whatever stratum or sample they fell in.

GROUPING is by (normalised subject, normalised object, relation alias
cluster) plus exact-claim identity — the curated K.3 alias list, never
similarity (the OQ-2 antonym finding settles that). The list is a
judgement, so it is VERSIONED: its version rides every output beside the
seeds, and it is never shown to the judge. Because the lexical test is
applied per item, one group can carry mixed flags — that within-group
inconsistency is measured by the scorer as a free surface-sensitivity
number.

This script emits the judge's stripped file (same schema AP.2 already
consumes — the judge itself is unchanged and judges everything) and the
key file. The BLIND HUMAN QUEUE is built afterwards by
citation_queue.py, because it needs the judgements.

    python scripts/citation_judge_extract.py
        [--capture data/citation_capture.jsonl] [--kb data/kb.db]
        [--out citation_stripped.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402

_META = re.compile(r"\s*\[w=[^\]]*\]\s*$")


def strip_surface(surface: str) -> str:
    """The bare claim text: drop the trailing `[w=... g=...]` metadata and
    the OUTER render parentheses only — an external label like
    `mercury (planet)` keeps its own."""
    s = _META.sub("", (surface or "").strip())
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    return s.strip()


def alias_version(aliases: dict) -> str:
    """The grouping list is a judgement; its version rides every output so
    a changed alias is visible beside the seed rather than silent."""
    body = json.dumps(sorted(aliases.items()))
    return f"k3-{len(aliases)}-{hashlib.sha256(body.encode()).hexdigest()[:8]}"


def _norm(term: str) -> str:
    return " ".join((term or "").lower().split())


def group_key(subject: str, relation: str, object_: str,
              aliases: dict) -> tuple:
    """Curated clustering only: same (subject, object) with relations in
    one alias cluster. Opposites never merge — `increases` and `decreases`
    are distinct clusters unless a human puts them in one, which the
    version string would then show."""
    canon = aliases.get(_norm(relation).replace(" ", "_"),
                        _norm(relation).replace(" ", "_"))
    return (_norm(subject), canon, _norm(object_))


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else "-"


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture", default="data/citation_capture.jsonl")
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--out", default="citation_stripped.json")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    from backend.kb.gate import RELATION_ALIASES
    aversion = alias_version(RELATION_ALIASES)

    cap = Path(args.capture)
    if not cap.is_file():
        print(f"MISSING: {cap}")
        return 2
    kb = Path(args.kb)
    md5_before = _md5(kb)

    rows = [json.loads(l) for l in cap.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    print(f"capture rows: {len(rows)}   store md5 {md5_before}   "
          f"alias list {aversion}")
    if not rows:
        print("nothing to extract")
        return 2

    con = None
    if kb.is_file():
        con = open_read_only(kb, row_factory=None)
        con.row_factory = sqlite3.Row

    # Consequence-bearing beliefs: cited in a SEALED lineage record — the
    # archival exemption (cited_pairs) and reinforcement rest on exactly
    # these flags, so their items enter the human set unconditionally.
    consequence: set[int] = set()
    models: dict[str, str] = {}
    if con is not None:
        for r in con.execute("SELECT injected_json FROM lineage_records "
                             "WHERE sealed_block IS NOT NULL"):
            for item in json.loads(r["injected_json"] or "[]"):
                if isinstance(item, dict) and item.get("cited") is True \
                        and item.get("belief_id") is not None:
                    consequence.add(int(item["belief_id"]))
        for row in rows:
            m = con.execute(
                "SELECT model_id FROM lineage_records WHERE lineage_id = ?",
                (row["lineage_id"],)).fetchone()
            models[row["lineage_id"]] = m["model_id"] if m else "unknown"
    dist: dict[str, int] = {}
    for m in models.values():
        dist[m] = dist.get(m, 0) + 1
    print(f"answering-model distribution: {dist}")
    print(f"consequence-bearing beliefs (cited in sealed records): "
          f"{len(consequence)}")

    def _sro(belief_id):
        if con is None:
            return None
        r = con.execute("SELECT subject, relation, object FROM triples "
                        "WHERE id = ?", (belief_id,)).fetchone()
        return (r["subject"], r["relation"], r["object"]) if r else None

    out_rows, key_rows = [], []
    n_cited = n_items = 0
    for r in rows:
        items, keys = [], []
        groups: dict[tuple, str] = {}
        for i, item in enumerate(r.get("injected", []), start=1):
            claim = strip_surface(item["surface"])
            sro = _sro(item.get("belief_id"))
            gk = (group_key(*sro, RELATION_ALIASES) if sro
                  else ("claim", claim, ""))
            gid = groups.setdefault(gk, f"g{len(groups) + 1}")
            cited = bool(item.get("cited"))
            n_items += 1
            n_cited += cited
            items.append({"n": i, "claim": claim})
            keys.append({"n": i, "belief_id": item.get("belief_id"),
                         "claim": claim, "group": gid,
                         "lexical_cited": cited,
                         "consequence": (item.get("belief_id") in consequence)})
        out_rows.append({"lineage_id": r["lineage_id"],
                         "answer": r["answer"], "items": items})
        key_rows.append({"lineage_id": r["lineage_id"],
                         "answer_model": models.get(r["lineage_id"],
                                                    "unknown"),
                         "items": keys})

    print(f"items: {n_items} total | cited stratum {n_cited} "
          f"(exhausted by hand) | uncited {n_items - n_cited} "
          f"(judge is load-bearing there)")

    out = Path(args.out)
    out.write_text(json.dumps({
        "alias_version": aversion,
        "rows": out_rows,
        "note": ("the judge's file: answers and bare claims only — no "
                 "flags, weights, group tags, ids, strata, or question. "
                 "The judge judges every item; strata exist only in the "
                 "key file."),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    key = out.with_suffix(".key.json")
    key.write_text(json.dumps({
        "alias_version": aversion,
        "consequence_beliefs": sorted(consequence),
        "rows": key_rows,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out} and {key}")
    print("next: scripts/citation_judge.py " + str(out) +
          " --judge primary   then scripts/citation_queue.py")

    if con is not None:
        con.close()
    md5_after = _md5(kb)
    print(f"store md5 after: {md5_after}   "
          f"{'UNCHANGED' if md5_after == md5_before else '*** CHANGED ***'}")
    return 0 if md5_after == md5_before else 3


if __name__ == "__main__":
    sys.exit(main())
