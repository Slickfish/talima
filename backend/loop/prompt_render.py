"""AW — the dynamic prompt block says only what the record can re-derive.

THE DEFECT, printed by the render investigation and read verbatim:

    [CORTEX ACTIVATION — top firing nodes with epistemic state]
      strength training [partial knowledge — use with caution] (act=1.00, col=fitness)
      improve [partial knowledge — use with caution] (act=1.00, col=general)
      ...
    (training co_activated recovery) [w=0.36 g=topic]      <- in the KB list

Three things in it were not true, and one thing about it was not recorded:

  - the epistemic tag and the weight are ARTEFACTS. Nothing in the backend
    writes a node's state or weight after creation and neither is
    persisted, so after a restart every node says "partial knowledge — use
    with caution" and every concept first retrieved since boot says
    "known, evidential weight 0.50". The stable prefix tells the model the
    context "is your own durable memory ... use it as what you know"; the
    block then told it to use every concept with caution. Two instructions,
    one of them a constant.
  - the question's own words were rendered as half-known concepts. Every
    non-stopword query token becomes a graph node and seeds fire at 1.00,
    so "improve", "help", "training" headed the list. Nothing in the store
    says anything about them.
  - a substrate row (`co_activated`) sat in the ranked KB list with a
    weight, indistinguishable from a claim. The retriever partitions those
    rows and AU's `no_substrate` rule already says they are associations,
    not claims; the prompt was the one surface still presenting them as
    facts.
  - the lineage record hashed the STABLE prefix only. The KB half was
    re-derivable from `injected` and the store; the cortex half — live
    activation over a warm graph — was derivable from nothing recorded.
    I-6: the record must contain what influenced the answer.

WHAT SHIPS (`NEXUS_PROMPT_RENDER=narrow`, the default):

  KB CONTEXT      claim rows only, the same line as before (`render_kb_line`,
                  the one implementation the derivation also uses).
  ASSOCIATIONS    substrate rows, under their own header naming what they
                  are. They stay INJECTED — the record's `injected` list is
                  still exactly the hits — they are no longer presented as
                  claims.
  CORTEX          `  <concept> (act=0.62)` — the node id and this turn's
                  activation, both real. No tag, no weight, no column. A
                  node renders only when a live claim stands behind it
                  (`TripleStore.surfaces_with_claims`, substrate excluded),
                  so a bare query token never appears. Up to ten.

  The record gains an additive, unhashed `context` block: which render
  produced the block, a hash of the block that went to the model (the
  system message minus the stable prefix — exactly what was sent, by
  construction), and the (concept, activation) pairs the cortex section
  rendered. `derive_block()` rebuilds the KB, ASSOCIATIONS and CORTEX
  sections from the record and the store; the sentinel compares the
  rebuilt text with the hash. Sections built from outside the store (web
  search, vector RAG) are pinned by the hash and not re-derived.

`NEXUS_PROMPT_RENDER=legacy` is the rollback: the pre-AW block
byte-identically — tags, weights, columns, query tokens, substrate rows in
the KB list. The record is written in both positions and names which
render it saw, so a reader of the series knows which prompt a record's
hash pins.

WHAT WAS ARGUED OUT. `col=` was true and was dropped: it is a keyword
heuristic over three word lists (columns.py) and reads "general" for
everything outside them, so in a fourth domain it is a constant beside
every concept — the tag's defect in a smaller font — and the model can read
the words it would label. The block still does not appear when retrieval
missed: nothing in the store substantiates anything then, and an
association-reached name without a claim is the class this packet stops
rendering; the empty block is recorded (its hash) rather than shown.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import TYPE_CHECKING, Iterable

from backend.kb.retriever import render_kb_line
from backend.kb.schema import SUBSTRATE_RELATIONS
from backend.kb.source_policy import gateway_on, provenance_of
from backend.kb.tiered_store import classify

if TYPE_CHECKING:
    from backend.kb.tiered_store import TripleStore

# BA fix 4 — the version moved because the line format did: KB and
# ASSOCIATIONS lines carry `src=<class>` and cortex lines `(act=.. src=..)`.
# A record whose `context.render` is the AW value derives the AW format.
PROMPT_RENDER_VERSION = "ba.4-narrow-src"
# W5 — with the reference gateway on, KB and ASSOCIATIONS lines also carry
# `via=<provenance>`. The line format moved, so the render has its own name
# and a record says which prompt its hash pins.
GATEWAY_RENDER_VERSION = "wg.1-narrow-src-via"
# The legacy render with the gateway on: the KB and cortex sections are the
# legacy ones, with no label, and only the web block is the gateway's. The two
# requests differ, so the record must be able to say which it saw (the W5
# verifier's V4).
LEGACY_GATEWAY_RENDER = "legacy+wg.1-web"
AW_RENDER_VERSION = "aw.1-narrow"
LEGACY_RENDER = "legacy"

KB_HEADER = "[KB CONTEXT — retrieved triples ranked by current weight]"
ASSOC_HEADER = "[ASSOCIATIONS — co-activation trails, not claims]"
CORTEX_HEADER = "[CORTEX ACTIVATION — top firing concepts]"
LEGACY_CORTEX_HEADER = "[CORTEX ACTIVATION — top firing nodes with epistemic state]"
MAX_CORTEX_LINES = 10


def prompt_render_mode() -> str:
    """`narrow` (default) or `legacy`. Read at call time (the E.2 pattern)."""
    raw = os.environ.get("NEXUS_PROMPT_RENDER", "narrow").strip().lower()
    return LEGACY_RENDER if raw == LEGACY_RENDER else "narrow"


def render_version() -> str:
    """The value the record carries: which render produced the block."""
    if prompt_render_mode() == LEGACY_RENDER:
        return LEGACY_GATEWAY_RENDER if gateway_on() else LEGACY_RENDER
    return GATEWAY_RENDER_VERSION if gateway_on() else PROMPT_RENDER_VERSION


def is_substrate(relation: str) -> bool:
    return relation in SUBSTRATE_RELATIONS


def context_hash(block: str) -> str:
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


def request_bytes_of(messages) -> bytes:
    """BA fix 2 / W0b — the canonical serialisation of the request as sent to
    the provider: role, content, order, nothing else. These are the bytes
    `request_hash` covers, and the bytes W0b keeps so a turn can be re-run."""
    canon = json.dumps([{"role": m.get("role"), "content": m.get("content")}
                        for m in messages], sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False)
    return canon.encode("utf-8")


def request_hash_of(messages) -> str:
    """BA fix 2 — sha256 over the canonical serialisation of the request as
    sent to the provider: role, content, order, nothing else. One function,
    used at assembly and by the derivability check."""
    return hashlib.sha256(request_bytes_of(messages)).hexdigest()


# ---------------------------------------------------------------------------
# The narrow render
# ---------------------------------------------------------------------------

def render_cortex_line(node: str, act: float, src: str | None = None) -> str:
    tail = f" src={src}" if src else ""
    return f"  {node} (act={act:.2f}{tail})"


def kb_line(rt) -> str:
    """The narrow KB line: the row's evidence class read from the ROW's
    source and nowhere else (BA fix 4). `rt.render()` stays the legacy line."""
    t = rt.triple
    return render_kb_line(t.subject, t.relation, t.object, rt.score, t.granularity,
                          classify(t.source), provenance_of(t.source) if gateway_on() else None)


def kb_sections(hits: Iterable) -> list[str]:
    """Claims under KB_HEADER, substrate rows under ASSOC_HEADER, hit order
    kept within each. Either section is omitted when empty."""
    claims, assoc = [], []
    for rt in hits:
        (assoc if is_substrate(rt.triple.relation) else claims).append(kb_line(rt))
    out = []
    if claims:
        out.append(KB_HEADER + "\n" + "\n".join(claims))
    if assoc:
        out.append(ASSOC_HEADER + "\n" + "\n".join(assoc))
    return out


def select_cortex(activation, store: "TripleStore",
                  k: int = MAX_CORTEX_LINES) -> list[tuple[str, float, str]]:
    """The first `k` firing nodes, by activation, that have a live claim
    behind them, each with the strongest evidence class among its claims
    (BA fix 4). Walks the whole activation map rather than its top ten, so
    query tokens (always at 1.00) do not consume the slots they no longer
    fill."""
    if activation is None or not activation.activations:
        return []
    ranked = activation.peak_activations
    classes = store.surface_classes(
        [n for n, _ in ranked], exclude_relations=SUBSTRATE_RELATIONS)
    out = []
    for node, act in ranked:
        if node in classes:
            out.append((node, round(float(act), 2), classes[node]))
            if len(out) >= k:
                break
    return out


def cortex_section(rendered: Iterable[tuple]) -> str | None:
    lines = [render_cortex_line(*r) for r in rendered]
    return (CORTEX_HEADER + "\n" + "\n".join(lines)) if lines else None


# ---------------------------------------------------------------------------
# The legacy render — the pre-AW strings, byte for byte
# ---------------------------------------------------------------------------

def legacy_kb_section(hits: list) -> str | None:
    if not hits:
        return None
    return KB_HEADER + "\n" + "\n".join(rt.render() for rt in hits)


def legacy_cortex_section(activation, hippocampus) -> str | None:
    if activation is None or not activation.activations:
        return None
    tagged = []
    for nid, act in activation.top_k(10):
        node = hippocampus.get_node(nid)
        tagged.append(
            f"  {nid} {node.epistemic_state.render(confidence=node.confidence)} "
            f"(act={act:.2f}, col={node.column})"
        )
    return LEGACY_CORTEX_HEADER + "\n" + "\n".join(tagged)


# ---------------------------------------------------------------------------
# The derivation — the sentinel's half
# ---------------------------------------------------------------------------

def derive_block(record, store: "TripleStore") -> str:
    """Rebuild the store-derived sections of a turn's dynamic block from its
    lineage record and the store: KB CONTEXT and ASSOCIATIONS from
    `injected` (belief id -> row, `weight_at_injection` -> `w=`), CORTEX from
    the record's `context.activation`. Returns the block in the shape
    `assemble_context` produces (leading blank line, sections separated by a
    blank line) so it compares byte-for-byte with what the model was sent
    when nothing outside the store contributed.

    A record with no `context` (written before AW) derives its KB half only,
    which is exactly what was derivable then.
    """
    if isinstance(record, dict):
        injected = list(record.get("injected") or [])
        context = record.get("context")
    else:
        injected = list(record.injected or [])
        context = record.context
    render = (context or {}).get("render")
    with_src = render not in (None, AW_RENDER_VERSION, LEGACY_RENDER, LEGACY_GATEWAY_RENDER)
    with_via = render == GATEWAY_RENDER_VERSION        # W5: the record names the render it saw
    claims, assoc = [], []
    for item in injected:
        t = store.get_by_id(int(item["belief_id"]))
        if t is None:
            continue
        line = render_kb_line(t.subject, t.relation, t.object,
                              float(item["weight_at_injection"]), t.granularity,
                              classify(t.source) if with_src else None,
                              provenance_of(t.source) if with_via else None)
        (assoc if is_substrate(t.relation) else claims).append(line)
    parts = []
    if claims:
        parts.append(KB_HEADER + "\n" + "\n".join(claims))
    if assoc:
        parts.append(ASSOC_HEADER + "\n" + "\n".join(assoc))
    rendered = (context or {}).get("activation") or []
    section = cortex_section(
        (str(r[0]), float(r[1]), (str(r[2]) if len(r) > 2 and r[2] else None))
        for r in rendered)
    if section:
        parts.append(section)
    return ("\n\n" + "\n\n".join(parts)) if parts else ""
