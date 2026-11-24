"""Is the missing task prefix why 'cardiac safety' finds nothing?

READ ONLY. Embeds four short strings and prints two cosines. Writes nothing,
stores nothing, changes nothing. The operator's run of this (bare 0.4047 /
prefixed 0.5251 against control 0.2570) is the W.2 finding recorded in
CLAUDE.md's task-prefix section; kept so that number can be reproduced.

    python scripts/probe_prefix.py

nomic-embed-text is trained with mandatory `search_query:` / `search_document:`
prefixes. NEXUS sends neither, and sends the identical un-prefixed call for
both sides — so the query/document asymmetry the model was trained for is
absent. This measures whether that is the cause.

READING THE RESULT
    bare low, prefixed high   -> the prefix is the cause. Two-line fix in the
                                 providers.
    both low                  -> the claim's surface form is the limit. The
                                 fix is upstream in extraction, not retrieval.
    both high                 -> neither: the claim is reachable and something
                                 else (slot budget, top-k cut) is dropping it.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402

Q = "what do we know about cardiac safety?"
C = "mean placebo-corrected QTc change at 600mg supratherapeutic dose was 11.4ms"


async def go():
    from backend.providers.factory import get_provider
    from backend.vector.embeddings import embed_via_provider
    from backend.vector.matryoshka import cosine

    # W.3 — a measurement may not fall back, so every call below passes
    # strict=True. Without it a dead daemon substituted hash pseudo-embeddings
    # and this script PRINTED COSINES OF NOISE with no indication (found by
    # running it in a sandbox with no provider — the "FAILED / Is Ollama
    # running?" branch was unreachable because the fallback swallowed the
    # ConnectError). test_the_measurement_scripts_embed_strictly pins it.
    p = get_provider()

    print()
    print("question :", Q)
    print("claim    :", C)
    print()

    pairs = [
        (Q, C, "bare      "),
        ("search_query: " + Q, "search_document: " + C, "prefixed  "),
    ]

    results = {}
    for a, b, label in pairs:
        va = await embed_via_provider(p, a, strict=True)
        vb = await embed_via_provider(p, b, strict=True)
        s = cosine(va, vb)
        results[label.strip()] = s
        print("  {} cosine {:.4f}".format(label, s))

    # A control: something that should obviously NOT match, so a high bare
    # score can be recognised as the embedder being uniformly generous rather
    # than as a real match.
    ctrl = "banana bread recipe with walnuts"
    vq = await embed_via_provider(p, Q, strict=True)
    vc = await embed_via_provider(p, ctrl, strict=True)
    s_ctrl = cosine(vq, vc)
    print("  control    cosine {:.4f}   ('{}')".format(s_ctrl, ctrl))

    print()
    bare = results.get("bare", 0.0)
    pref = results.get("prefixed", 0.0)
    print("  claim minus control, bare     : {:+.4f}".format(bare - s_ctrl))
    print("  claim minus control, prefixed : {:+.4f}".format(pref - s_ctrl))
    print()
    print("  The GAP matters more than either number. A model that scores")
    print("  everything at 0.9 tells you nothing; what matters is whether the")
    print("  real claim beats an unrelated one, and by how much.")
    print()


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the provider URL comes from the same .env the server reads
    from backend.vector.embeddings import EmbeddingUnavailable
    try:
        asyncio.run(go())
    except EmbeddingUnavailable as e:
        # Exit 5, the W.3 convention shared with the measure scripts: the
        # measurement ABORTED by name rather than printing numbers from noise.
        print("ABORTED (embedding unavailable): {}".format(e))
        print("Is Ollama running?  ollama list")
        return 5
    except Exception as e:
        print("FAILED: {}: {}".format(type(e).__name__, e))
        print("Is Ollama running?  ollama list")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
