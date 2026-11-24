"""Matryoshka 2-stage retrieval (CLAUDE.md Phase 7).

Stage 1 (coarse): query truncated to 256 dims against a 256-dim ChromaDB
                  collection. Returns top `coarse_k` candidate IDs.
Stage 2 (rerank): full 768-dim cosine similarity computed in Python over the
                  coarse candidates only. Returns top `final_k` ranked.

Truncating the query at 256 dims is a valid Matryoshka operation only because
the embedding model (nomic-embed-text) is Matryoshka-trained — the leading
dims still carry meaning.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

COARSE_DIM = 256
FULL_DIM   = 768

DEFAULT_COARSE_K = 50
DEFAULT_FINAL_K  = 10


def matryoshka_truncate(vec: Sequence[float], dim: int = COARSE_DIM) -> list[float]:
    """Take the first `dim` coordinates and L2-renormalise."""
    if len(vec) < dim:
        raise ValueError(f"vector len {len(vec)} < requested dim {dim}")
    arr = np.array(vec[:dim], dtype=np.float32)
    n = float(np.linalg.norm(arr)) or 1.0
    return (arr / n).tolist()


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    av = np.array(a, dtype=np.float32)
    bv = np.array(b, dtype=np.float32)
    na = float(np.linalg.norm(av)) or 1.0
    nb = float(np.linalg.norm(bv)) or 1.0
    return float((av / na) @ (bv / nb))
