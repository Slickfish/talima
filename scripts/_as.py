"""AS — shared, pure logic for the retention-cost measurement. No I/O here.

Everything a sentinel needs to prove is a pure function in this module:
the pre-registered thresholds and tie rules, the three definitions of
"wasted" (each reusing machinery that already exists rather than a new
judgement), the per-turn shares, the H2 spread rule and correlations, and
the tokeniser backends (real ones import lazily inside the constructor so
the repository's suite never needs them).

PRE-REGISTERED, FIXED BEFORE THE FIRST RUN — these do not move afterwards.
"""
from __future__ import annotations

import hashlib
import math
import re

# ---------------------------------------------------------------------------
# Pre-registration (packet AS §2, §3). Recorded in source before any run.
# ---------------------------------------------------------------------------

# H1 — the share of injected tokens carrying no distinct information.
THRESHOLDS = {"supported": 0.25,          # >= 25%: a cost worth gating against
              "marginal": (0.10, 0.25),   # real but marginal: record, do not build
              "closes": 0.10}             # < 10%: retention is cheap; the idea closes

# Which figure H1 is read from. `structural` = duplicate-bearing OR malformed,
# token-deduplicated across the two; defensible on its own. The figure that
# adds `uncontributing` rests on the lexical cited flag and is reported as
# secondary and inferential (packet §3), never as the headline.
PRIMARY = "structural"
SECONDARY = "structural_plus_uncontributing"

# Pooled over all injected tokens, not a mean of per-turn shares: the cost is
# paid in tokens, and a mean of shares would let ten tiny turns outvote one
# large one. Per-turn shares exist for H2, not for the H1 verdict.
H1_AGGREGATION = "pooled: wasted tokens / injected tokens, summed over turns"

# H2 — does the share vary with anything observable? The packet's rule: if
# the spread across turns is narrower than half the mean, treat it as
# constant. Spread is the population standard deviation of the per-turn
# PRIMARY share. A fixed quota is then the honest answer, not a gate.
H2_CONSTANT_RULE = ("constant iff std(per-turn primary share) < mean / 2; "
                    "a constant share gives a gate nothing to respond to")
H2_MIN_TURNS = 5      # below this the spread is not a spread

TIE_RULE = ("marginal or constant means no mechanism; a biological parallel "
            "does not win a tie")

# The uncontributing category's stated limitation, carried on the payload
# (packet §3). AQ measured the lexical flag against an entailment model:
# they disagree on 27% of items, with both around 73% on published
# benchmarks, so a figure resting on it is a lower bound at best.
UNCONTRIBUTING_CAVEAT = (
    "uncontributing rests on the lexical cited flag, which AQ measured to "
    "disagree with an entailment model on 27% of items (both ~73% on "
    "published benchmarks); this share is inferential and a lower bound at "
    "best. The structural shares do not depend on it.")

# Model tag prefix -> HF tokenizer repo. DATA, and each entry is a claim the
# operator can override with --tokenizer-json / --hf-repo; the instrument
# record carries the sha256 of the tokenizer.json actually used, so a wrong
# entry is visible beside the numbers rather than silent. Longest prefix
# wins. Matching is on the ollama tag as recorded in lineage.model_id.
TOKENISER_MAP = {
    "deepseek-r1:14b-qwen-distill": "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
    "qwen2.5-coder:7b":             "Qwen/Qwen2.5-Coder-7B-Instruct",
    "qwen3.5:9b":                   "Qwen/Qwen3.5-9B",
    "qwen3:8b":                     "Qwen/Qwen3-8B",
    "phi4-mini":                    "microsoft/Phi-4-mini-instruct",
}

# ---------------------------------------------------------------------------
# The three definitions of "wasted" — reused machinery, no new judgement
# ---------------------------------------------------------------------------
# Duplicate-bearing: AQ's curated grouping — identical after normalisation,
# or same (subject, object) with relations in one alias cluster. Imported,
# not copied, so the two measurements cannot drift (identity-pinned).
from scripts.citation_judge_extract import alias_version, group_key   # noqa: E402
# Malformed: the ingest gate's own per-field rules under the row's evidence
# class — the criteria the consolidation investigation characterised. A row
# is malformed iff the gate would refuse it today. Imported, identity-pinned.
from backend.kb.gate import (                                        # noqa: E402
    RELATION_ALIASES, inspect_relation, inspect_term)
from backend.kb.schema import SUBSTRATE_RELATIONS                     # noqa: E402
from backend.kb.tiered_store import classify                          # noqa: E402


def malformed_reason(subject: str, relation: str, object_: str,
                     source: str) -> str | None:
    """First gate reason under the row's own class, or None."""
    cls = classify(source or "")
    return (inspect_term(subject, evidence_class=cls, position="subject")
            or inspect_relation(relation, evidence_class=cls)
            or inspect_term(object_, evidence_class=cls, position="object"))


# ---------------------------------------------------------------------------
# AT (term identity) — the counting fix, and only that
# ---------------------------------------------------------------------------
# The probe on the operator's store (AT, 426 claim rows) found the duplicates
# that recur in nearly every captured turn are spelling variants —
# `HERG potassium_channel` / `HERG_potassium_channel`, `QT prolongation
# risk` / `QT_prolongation_risk`, `hERG` / `HERG` — 24 punctuation groups
# and 15 case groups with no wrong merge visible among them, and 481 of 564
# terms appearing once (85.3%), which is OQ-2's distribution for terms and
# settles the list question. This key folds exactly those two classes, at
# COUNTING time only: no stored byte changes, no uniqueness key changes, no
# retrieval or injection behaviour changes. Ingest folding was argued
# against (provenance loss on case, and a third spelling during the
# transition since existing rows keep theirs); read-time folding already
# exists for recall (LOWER LIKE) and is the CAUSE of the six-in-one-prompt
# cost, not a fix for it.
#
# DIGIT-SAFE: punctuation between two digits is kept, so `11.4ms` and
# `114ms` never fold together — the probe's own key had that hole.
_PUNCT_NOT_BETWEEN_DIGITS = re.compile(r"(?<!\d)[^a-z0-9]+|[^a-z0-9]+(?!\d)")
TERM_KEY_RULE = ("case-folded; runs of punctuation, underscore and whitespace "
                 "become one space, except between two digits; nothing else")


def term_key(term: str) -> str:
    """The counting identity of a term. Derived, no table."""
    t = (term or "").casefold()
    t = _PUNCT_NOT_BETWEEN_DIGITS.sub(" ", t)
    return " ".join(t.split())


def relation_aliases(active_pairs, base: dict | None = None) -> dict:
    """The alias map AS groups relations with: K.3's curated map (eight
    spellings of is_a) plus AB's DERIVED active trail pairs, closed under
    union-find so a~b and b~c land in one cluster. AB's pairs are the thing
    that separated relation wordings on real data; the retention measurement
    had consulted only the curated map, which is why four of the operator's
    five metabolism spellings were invisible to it."""
    base = dict(base if base is not None else RELATION_ALIASES)
    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for k, v in base.items():
        parent[find(k)] = find(v)
    for a, b in active_pairs or ():
        parent[find(a)] = find(b)
    members: dict = {}
    for x in list(parent):
        members.setdefault(find(x), set()).add(x)
    out = {}
    for root, ms in members.items():
        canon = min(ms)                     # deterministic representative
        for m in ms:
            out[m] = canon
    return out


def categorise(items: list[dict], aliases: dict | None = None) -> list[dict]:
    """Flag every injected item of ONE turn. `items` are in injected order
    and carry subject, relation, object, source, tokens, cited (True/False/
    None when the F.5 flag is absent). Returns new dicts; input untouched.

    duplicate      not the first member of its group in injected order —
                   the first is kept, the rest carry no distinct information
    malformed      the gate's reason, or None
    uncontributing cited is False (None when the flag is absent)

    Grouping is still AQ's `group_key` (identity-pinned) — applied to the
    term_key of each term, with the alias map widened by AB's trail pairs.
    """
    aliases = aliases if aliases is not None else RELATION_ALIASES
    seen: set = set()
    out = []
    for it in items:
        gk = group_key(term_key(it["subject"]), it["relation"],
                       term_key(it["object"]), aliases)
        dup = gk in seen
        seen.add(gk)
        cited = it.get("cited")
        out.append({
            **it,
            "group": gk,
            "duplicate": dup,
            "malformed": malformed_reason(it["subject"], it["relation"],
                                          it["object"], it.get("source", "")),
            "uncontributing": (None if cited is None else not cited),
            "substrate": it["relation"] in SUBSTRATE_RELATIONS,
        })
    return out


def _share(tokens: int, total: int) -> dict:
    return {"tokens": tokens, "share": (tokens / total) if total else None}


def turn_shares(items: list[dict], aliases: dict | None = None) -> dict:
    """The three categories, separately, plus the two unions — each as a
    token count and a share of the turn's injected tokens. A category never
    appears summed without its members beside it."""
    flagged = categorise(items, aliases)
    total = sum(it["tokens"] for it in flagged)
    dup = sum(it["tokens"] for it in flagged if it["duplicate"])
    mal = sum(it["tokens"] for it in flagged if it["malformed"])
    unc_known = [it for it in flagged if it["uncontributing"] is not None]
    unc = sum(it["tokens"] for it in unc_known if it["uncontributing"])
    structural = sum(it["tokens"] for it in flagged
                     if it["duplicate"] or it["malformed"])
    with_unc = sum(it["tokens"] for it in flagged
                   if it["duplicate"] or it["malformed"] or it["uncontributing"])
    reasons: dict[str, int] = {}
    for it in flagged:
        if it["malformed"]:
            r = it["malformed"].split(":")[0]
            reasons[r] = reasons.get(r, 0) + 1
    subjects = {" ".join(it["subject"].lower().split()) for it in flagged}
    return {
        "n_items": len(flagged),
        "tokens_injected": total,
        "duplicate_bearing": _share(dup, total),
        "malformed": {**_share(mal, total), "reasons": reasons},
        "uncontributing": {**_share(unc, total),
                           "flags_absent": len(flagged) - len(unc_known)},
        PRIMARY: {**_share(structural, total),
                  "breakdown": {"duplicate_bearing": dup, "malformed": mal}},
        SECONDARY: {**_share(with_unc, total),
                    "breakdown": {"duplicate_bearing": dup, "malformed": mal,
                                  "uncontributing": unc}},
        "has_duplicates": dup > 0,
        "distinct_subjects": len(subjects),
        "substrate_items": sum(1 for it in flagged if it["substrate"]),
        "cited_items": sum(1 for it in flagged if it.get("cited") is True),
        "mean_weight": (sum(float(it.get("weight") or 0.0) for it in flagged)
                        / len(flagged)) if flagged else None,
        "items": flagged,
    }


# ---------------------------------------------------------------------------
# H1 / H2 evaluation
# ---------------------------------------------------------------------------

def verdict(share: float | None) -> str | None:
    if share is None:
        return None
    if share >= THRESHOLDS["supported"]:
        return "supported"
    if share >= THRESHOLDS["marginal"][0]:
        return "marginal"
    return "closes"


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or len(ys) != n:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "std": None, "min": None, "max": None}
    n = len(values)
    mean = sum(values) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / n)
    return {"n": n, "mean": mean, "std": std, "min": min(values),
            "max": max(values)}


def h2(turns: list[dict]) -> dict:
    """Spread of the per-turn PRIMARY share, the constant rule, and what
    correlates with it. Correlation, not causation."""
    rows = [t for t in turns if t[PRIMARY]["share"] is not None]
    shares = [t[PRIMARY]["share"] for t in rows]
    st = _stats(shares)
    constant = None
    if st["n"] >= H2_MIN_TURNS and st["mean"]:
        constant = st["std"] < st["mean"] / 2
    signals = {
        "injected_count": [t["n_items"] for t in rows],
        "distinct_subjects": [t["distinct_subjects"] for t in rows],
        "substrate_share": [t["substrate_items"] / t["n_items"] for t in rows],
        "cited_share": [t["cited_items"] / t["n_items"] for t in rows],
        "mean_weight": [t["mean_weight"] for t in rows],
    }
    for extra in ("context_precision", "faithfulness"):
        vals = [t.get(extra) for t in rows]
        if all(v is not None for v in vals) and vals:
            signals[extra] = vals
    return {
        "spread": st,
        "rule": H2_CONSTANT_RULE,
        "min_turns": H2_MIN_TURNS,
        "constant": constant,
        "correlations": {k: pearson(shares, v) for k, v in signals.items()},
        "note": "correlation, not causation — only whether a signal tracks the waste",
    }


def evaluate(turns: list[dict], *, prefix_tokens: int, framing_tokens: int,
             measurement: bool, aliases: dict | None = None,
             trail_pairs: int = 0) -> dict:
    """The aggregate, in the packet's order: the three shares separately,
    the denominator beside them, H2, the verdicts, the tie rule."""
    total = sum(t["tokens_injected"] for t in turns)
    pooled = {}
    for cat in ("duplicate_bearing", "malformed", "uncontributing",
                PRIMARY, SECONDARY):
        toks = sum(t[cat]["tokens"] for t in turns)
        pooled[cat] = _share(toks, total)
    pooled[PRIMARY]["breakdown"] = {
        "duplicate_bearing": pooled["duplicate_bearing"]["tokens"],
        "malformed": pooled["malformed"]["tokens"]}
    pooled[SECONDARY]["breakdown"] = {
        **pooled[PRIMARY]["breakdown"],
        "uncontributing": pooled["uncontributing"]["tokens"]}
    reasons: dict[str, int] = {}
    for t in turns:
        for r, n in t["malformed"]["reasons"].items():
            reasons[r] = reasons.get(r, 0) + n
    pooled["malformed"]["reasons"] = reasons
    flags_absent = sum(t["uncontributing"]["flags_absent"] for t in turns)
    n_turns = len(turns)
    per_turn_injected = (total / n_turns) if n_turns else 0
    reconstructed = prefix_tokens + framing_tokens + per_turn_injected
    dup_turns = sum(1 for t in turns if t["has_duplicates"])
    return {
        "aggregation": H1_AGGREGATION,
        "turns": n_turns,
        "tokens_injected_total": total,
        "shares": pooled,
        "uncontributing_caveat": UNCONTRIBUTING_CAVEAT,
        "denominator": {
            "system_prefix_tokens": prefix_tokens,
            "kb_block_framing_tokens": framing_tokens,
            "injected_tokens_per_turn_mean": per_turn_injected,
            "injected_share_of_reconstructed_prompt": (
                per_turn_injected / reconstructed if reconstructed else None),
            "note": ("reconstructed = prefix + KB framing + injected; the "
                     "conversation history and the user message are NOT "
                     "recorded (query_hash only), so this share OVERSTATES "
                     "the injected set's share of the whole prompt. The true "
                     "denominator is Ollama's prompt_eval_count, which the "
                     "provider discards today (establish 1)."),
        },
        "h2": h2(turns),
        "verdict": {
            PRIMARY: verdict(pooled[PRIMARY]["share"]) if measurement else None,
            SECONDARY: verdict(pooled[SECONDARY]["share"]) if measurement else None,
            "thresholds": THRESHOLDS,
            "tie_rule": TIE_RULE,
            "primary": PRIMARY,
        },
        "non_vacuity": {
            "turns_with_duplicates": dup_turns,
            "turns_without_duplicates": n_turns - dup_turns,
            "meaningful": dup_turns >= 1 and (n_turns - dup_turns) >= 1,
            "cited_flags_absent_items": flags_absent,
        },
        "grouping": {"alias_version": alias_version(
                         aliases if aliases is not None else RELATION_ALIASES),
                     "curated_alias_version": alias_version(RELATION_ALIASES),
                     "trail_pairs": trail_pairs,
                     "term_key": TERM_KEY_RULE,
                     "rule": "AQ group_key over term_key(subject), "
                             "term_key(object) and the relation canonicalised "
                             "by the curated aliases plus AB's active trail "
                             "pairs (union-find); no similarity grouping"},
        "measurement": measurement,
    }


# ---------------------------------------------------------------------------
# Tokeniser backends — the instrument, recorded with the numbers
# ---------------------------------------------------------------------------

class TokeniserUnavailable(RuntimeError):
    """A measurement may not fall back (W.3's stance): a real backend that
    cannot be reached raises this and the script exits 5."""


def tokeniser_repo_for(model_id: str, override: str | None = None) -> str | None:
    if override:
        return override
    m = (model_id or "").lower()
    best = None
    for prefix, repo in TOKENISER_MAP.items():
        if m.startswith(prefix.lower()) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, repo)
    return best[1] if best else None


def hf_tokeniser(*, tokenizer_json: str | None = None, repo: str | None = None):
    """(count_fn, instrument) over a HF tokenizer.json — the same vocabulary
    the GGUF the answering model runs was converted from. Lazy imports."""
    from tokenizers import Tokenizer
    path = tokenizer_json
    if path is None:
        if not repo:
            raise TokeniserUnavailable("no tokenizer.json and no HF repo for this model")
        try:
            from huggingface_hub import hf_hub_download
            path = hf_hub_download(repo, "tokenizer.json")
        except Exception as e:
            raise TokeniserUnavailable(f"cannot fetch {repo}: {type(e).__name__}: {e}")
    tok = Tokenizer.from_file(str(path))
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()

    def count(text: str) -> int:
        return len(tok.encode(text, add_special_tokens=False).ids)
    return count, {"backend": "hf", "repo": repo, "tokenizer_json": str(path),
                   "tokenizer_sha256": digest, "NOT_A_MEASUREMENT": False}


def ollama_tokeniser(*, base_url: str, model: str):
    """(count_fn, instrument) via the daemon's own prompt_eval_count on a
    raw, zero-prediction generate — literally the tokeniser the model used.

    THE HAZARD, checked rather than assumed: the daemon caches prompt
    prefixes and reports only the tokens it evaluated, so a repeated or
    prefix-sharing prompt can come back short. The self-check counts one
    string twice and refuses the backend if the two disagree; a passing
    check does not prove every later call is uncached, so the instrument
    record says which backend produced the numbers and the HF path is the
    recommended one."""
    import httpx

    def count(text: str) -> int:
        r = httpx.post(f"{base_url}/api/generate", json={
            "model": model, "prompt": text, "raw": True, "stream": False,
            "options": {"num_predict": 0}}, timeout=120.0)
        r.raise_for_status()
        body = r.json()
        if "prompt_eval_count" not in body:
            raise TokeniserUnavailable("daemon returned no prompt_eval_count")
        return int(body["prompt_eval_count"])
    try:
        probe = "The quick brown fox jumps over the lazy dog."
        a, b = count(probe), count(probe)
    except (httpx.HTTPError, OSError) as e:
        raise TokeniserUnavailable(f"{type(e).__name__}: {e}")
    if a != b or a <= 0:
        raise TokeniserUnavailable(
            f"prompt cache contaminates counts ({a} then {b}); use --tokeniser hf")
    return count, {"backend": "ollama", "base_url": base_url, "model": model,
                   "self_check": {"first": a, "second": b},
                   "NOT_A_MEASUREMENT": False}


_WS = re.compile(r"\S+")


def stub_tokeniser():
    """Whitespace tokens. Mechanics only — its output is marked and no
    verdict is drawn from it."""
    def count(text: str) -> int:
        return len(_WS.findall(text))
    return count, {"backend": "stub", "NOT_A_MEASUREMENT": True}
