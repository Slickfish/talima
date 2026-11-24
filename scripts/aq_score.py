"""AQ step 2 — score every item with MiniCheck and the NLI head.

Runs in an ISOLATED venv, never from the repository's requirements.
Linux/macOS paths shown; on Windows the venv's executables live under
`.venv-aq\\Scripts\\` (e.g. `.venv-aq\\Scripts\\pip`, `.venv-aq\\Scripts\\python`):

    python -m venv .venv-aq
    .venv-aq/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
    .venv-aq/bin/pip install transformers sentencepiece accelerate nltk \
        "minicheck @ git+https://github.com/Liyan06/MiniCheck.git"
    .venv-aq/bin/python -c "import nltk; nltk.download('punkt_tab')"
    .venv-aq/bin/python scripts/aq_score.py citation_stripped.json aq_vmap.json \
        [--previous-scores aq_scores_deberta.json]

`accelerate` is required by the model loader and was omitted from the
first draft of these notes; `punkt_tab` is required because MiniCheck does
its own sentence splitting with nltk (the backend also fetches it quietly
if absent). `--previous-scores` carries the superseded variant's weights
digest into this run's instrument record beside the new one — the
variant switch (see scripts/_aq.py VARIANT_RECORD) is recorded, never
silently overwritten.

The model stack is imported INSIDE main() so the repository's test suite
never needs it (pinned by AST). Scores every item across the forty turns
on the verbalised claim text, records the lexical bit from the key, and
writes aq_scores.json with: the map version, each model's HF id +
revision + weights digest, the venv's package versions, per-item
{lexical, minicheck_score, minicheck (bit at the pre-registered 0.5), nli
(argmax entailment)}, the answering model per row (from the key), the
discriminator flag (same subject, different object elsewhere in the row),
and the disagreement counts per direction as a fraction of all items —
the first OUTPUT of the measurement phase.

`--backend stub` scores deterministically without models, for mechanics
only; its output is marked and refused by the result script.
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

from scripts._aq import (                                        # noqa: E402
    MINICHECK_PACKAGE_NAME, MINICHECK_THRESHOLD, MODELS, VARIANT_RECORD,
    strata, verbalise,
)
from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402


def _stub_backend():
    def mc(answer, claim):
        h = int(hashlib.sha256(f"{answer}|{claim}".encode()).hexdigest()[:8], 16)
        return (h % 100) / 100.0

    def nli(answer, claim):
        return mc(answer, claim) >= 0.5
    return mc, nli, {"backend": "stub", "NOT_A_MEASUREMENT": True}


def _real_backend():
    import importlib.metadata as md

    import torch                                   # noqa: F401
    from minicheck.minicheck import MiniCheck
    from transformers import (AutoModelForSequenceClassification,
                              AutoTokenizer)
    from huggingface_hub import snapshot_download

    def digest(repo: str) -> dict:
        path = Path(snapshot_download(repo))
        weights = sorted(p for p in path.rglob("*") if p.suffix in
                         (".safetensors", ".bin"))
        h = hashlib.sha256()
        for w in weights:
            h.update(w.read_bytes())
        rev = (path.name if path.parent.name == "snapshots" else "unknown")
        return {"repo": repo, "revision": rev, "weights_sha256": h.hexdigest(),
                "weight_files": [w.name for w in weights]}

    # MiniCheck splits sentences itself with nltk; punkt_tab must exist.
    try:
        import nltk
        try:
            nltk.data.find("tokenizers/punkt_tab")
        except LookupError:
            nltk.download("punkt_tab", quiet=True)
    except ImportError:
        pass                                    # minicheck's own dependency

    scorer = MiniCheck(model_name=MINICHECK_PACKAGE_NAME,
                       cache_dir="./.aq-cache")

    def mc(answer, claim):
        _label, prob, _, _ = scorer.score(docs=[answer], claims=[claim])
        return float(prob[0])

    tok = AutoTokenizer.from_pretrained(MODELS["nli"])
    model = AutoModelForSequenceClassification.from_pretrained(MODELS["nli"])
    labels = {v.lower(): k for k, v in model.config.id2label.items()}
    ent = labels.get("entailment", 0)

    def nli(answer, claim):
        enc = tok(answer, claim, return_tensors="pt", truncation=True,
                  max_length=512)
        with torch.no_grad():
            logits = model(**enc).logits[0]
        return int(logits.argmax()) == ent

    meta = {"backend": "real",
            "models": {"minicheck": digest(MODELS["minicheck"]),
                       "nli": digest(MODELS["nli"])},
            "variant_record": VARIANT_RECORD,
            "packages": {p: md.version(p) for p in
                         ("torch", "transformers", "minicheck")
                         if _installed(p)}}
    return mc, nli, meta


def _installed(pkg: str) -> bool:
    import importlib.metadata as md
    try:
        md.version(pkg)
        return True
    except md.PackageNotFoundError:
        return False


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stripped_file")
    ap.add_argument("vmap_file")
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--backend", choices=("real", "stub"), default="real")
    ap.add_argument("--out", default="aq_scores.json")
    ap.add_argument("--previous-scores", default=None,
                    help="the superseded variant's scores file; its "
                         "minicheck weights digest is carried into this "
                         "run's record beside the new one")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    stripped = json.loads(Path(args.stripped_file).read_text(encoding="utf-8"))
    key = json.loads(Path(args.stripped_file).with_suffix(".key.json")
                     .read_text(encoding="utf-8"))
    vmap = json.loads(Path(args.vmap_file).read_text(encoding="utf-8"))
    kb = Path(args.kb)
    md5_before = hashlib.md5(kb.read_bytes()).hexdigest() if kb.is_file() else "-"
    con = open_read_only(kb, row_factory=None)
    con.row_factory = sqlite3.Row

    mc, nli, meta = _stub_backend() if args.backend == "stub" else _real_backend()
    if args.previous_scores:
        prev = json.loads(Path(args.previous_scores).read_text(encoding="utf-8"))
        meta.setdefault("models", {})["minicheck_previous"] = (
            prev.get("meta", {}).get("models", {}).get("minicheck"))
        meta["previous_summary"] = prev.get("summary")
    answers = {r["lineage_id"]: r["answer"] for r in stripped["rows"]}

    items = []
    for row in key["rows"]:
        sro = {}
        for it in row["items"]:
            r = con.execute("SELECT subject, relation, object FROM triples "
                            "WHERE id = ?", (it["belief_id"],)).fetchone()
            if r:
                sro[it["n"]] = (r["subject"], r["relation"], r["object"])
        subj_objs: dict[str, set] = {}
        for s, _r, o in sro.values():
            subj_objs.setdefault(s.lower(), set()).add(o.lower())
        answer = answers[row["lineage_id"]]
        for it in row["items"]:
            if it["n"] not in sro:
                continue
            s, rel, o = sro[it["n"]]
            claim = verbalise(vmap, s, rel, o)
            score = mc(answer, claim)
            items.append({
                "lineage_id": row["lineage_id"], "n": it["n"],
                "answer_model": row.get("answer_model", "unknown"),
                "answer": answer, "claim": claim, "relation": rel,
                "lexical": int(bool(it["lexical_cited"])),
                "minicheck_score": round(score, 4),
                "minicheck": int(score >= MINICHECK_THRESHOLD),
                "nli": int(bool(nli(answer, claim))),
                "discriminator": len(subj_objs.get(s.lower(), ())) > 1,
            })
            print(f"{row['lineage_id']} #{it['n']:2d} lex={items[-1]['lexical']} "
                  f"mc={items[-1]['minicheck']} ({score:.2f}) nli={items[-1]['nli']}")
    con.close()

    s = strata(items)
    disagreements = len(s["fp_candidates"]) + len(s["fn_candidates"])
    summary = {
        "items": len(items),
        "fp_candidates": len(s["fp_candidates"]),
        "fn_candidates": len(s["fn_candidates"]),
        "disagreements": disagreements,
        "disagreement_fraction": disagreements / len(items) if items else None,
        "discriminators_in_disagreements": sum(
            1 for it in s["fp_candidates"] + s["fn_candidates"]
            if it["discriminator"]),
    }
    out = Path(args.out)
    out.write_text(json.dumps({
        "vmap_version": vmap["version"],
        "minicheck_threshold": MINICHECK_THRESHOLD,
        # The instrument record rides the scores file itself, whatever
        # backend produced them: which variant, which it replaced, why.
        "variant_record": VARIANT_RECORD,
        "meta": meta, "summary": summary, "items": items,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{summary}\nwrote {out}")
    md5_after = hashlib.md5(kb.read_bytes()).hexdigest() if kb.is_file() else "-"
    print(f"store md5 {md5_before} -> {md5_after} "
          f"{'UNCHANGED' if md5_after == md5_before else '*** CHANGED ***'}")
    return 0 if md5_after == md5_before else 3


if __name__ == "__main__":
    sys.exit(main())
