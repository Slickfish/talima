"""ChromaDB wrapper (arch §3.2 backend/vector/).

Two collections — `nexus_coarse` (256-dim) and `nexus_full` (768-dim) — share
the same persist directory so add() lands one row in each. Reopening the
PersistentClient at the same path returns the same data, satisfying CLAUDE.md
"ChromaDB persists across restart".
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import chromadb

from backend.providers import default_provider

# BA fix 6 — the vector channel's failure state, read by /healthz. The
# query path used to return [] on any exception, indistinguishable from an
# empty result; it now returns None and marks this. Monotone, like the
# embedding fallback counter (W.3): a recovery does not reset it.
_VECTOR_STATE: dict = {"failures": 0, "last_error": "", "ts": 0.0}


def mark_vector_failure(reason: str) -> None:
    import time as _time
    _VECTOR_STATE["failures"] += 1
    _VECTOR_STATE["last_error"] = reason
    _VECTOR_STATE["ts"] = _time.time()


def vector_status() -> dict:
    return dict(_VECTOR_STATE)
from backend.vector.embeddings import (
    COARSE_DIM, FULL_DIM, _offline_embed, embed_via_provider)
from backend.vector.matryoshka import (
    DEFAULT_COARSE_K,
    DEFAULT_FINAL_K,
    cosine,
    matryoshka_truncate,
)


COARSE_COLLECTION = "nexus_coarse"
FULL_COLLECTION   = "nexus_full"

DEFAULT_PERSIST_DIR = Path("data/chroma")


@dataclass(frozen=True)
class RetrievedChunk:
    id: str
    text: str
    metadata: dict
    score: float

    @property
    def doc(self) -> str:
        """Alias for `text` — Fix A's deduplicator does dict-style lookups
        like r['doc'] expecting the surface document string."""
        return self.text

    # Dict-style access so callers writing r['score'] / r['doc'] / r['id'] /
    # r['text'] / r['metadata'] all work without object-vs-dict branching.
    _DICT_KEYS = frozenset({"id", "text", "metadata", "score", "doc"})

    def __getitem__(self, key: str):
        if key not in self._DICT_KEYS:
            raise KeyError(key)
        return getattr(self, key)

    def get(self, key: str, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):                              # pragma: no cover - helper
        return list(self._DICT_KEYS)


class VectorStore:
    def __init__(
        self,
        persist_directory: Path | str = DEFAULT_PERSIST_DIR,
        *,
        provider=None,
    ):
        """`provider` is the app's LLMProvider (R.4).

        It used to be the module-global `PROVIDER`, resolved at import from
        `LLM_BACKEND`. Fix 0's stated design is "NexusServices.provider
        per-app instance for test isolation", and this layer opted out of it:
        31 tests that carefully injected a stub provider still made 82 live
        HTTP calls to localhost:11434, because KAIROS's dedup scan queries
        this store once per triple and this store went around them.

        Defaults to the module global so existing callers keep working; it is
        not a legacy hatch anyone should reach for, and `create_services()`
        always passes the app's own.
        """
        self.provider = provider or default_provider()
        self.persist_directory = Path(persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.persist_directory))
        self.coarse = self.client.get_or_create_collection(
            name=COARSE_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        self.full = self.client.get_or_create_collection(
            name=FULL_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )

    # ---------- write ----------

    async def add(
        self,
        chunk_id: str,
        text: str,
        *,
        metadata: dict | None = None,
        embedding: list[float] | None = None,
    ) -> None:
        if embedding is not None:
            emb_full = list(embedding)
        else:
            # W.1 — same vector, same fallback conditions; the difference is
            # that a degraded provider now reaches `/healthz.embedding_degraded`
            # instead of being swallowed here.
            emb_full = await embed_via_provider(self.provider, text, dim=FULL_DIM)
        if len(emb_full) != FULL_DIM:
            raise ValueError(
                f"embedding has {len(emb_full)} dims; expected {FULL_DIM}"
            )
        emb_coarse = matryoshka_truncate(emb_full, COARSE_DIM)
        meta = (metadata or {}) | {"text_len": len(text)}
        self.coarse.upsert(
            ids=[chunk_id], documents=[text],
            embeddings=[emb_coarse], metadatas=[meta],
        )
        self.full.upsert(
            ids=[chunk_id], documents=[text],
            embeddings=[emb_full], metadatas=[meta],
        )

    def add_sync(
        self,
        chunk_id: str,
        text: str,
        *,
        metadata: dict | None = None,
        embedding: list[float] | None = None,
    ) -> None:
        """Synchronous variant for tests / scripts. Uses offline embedding
        when no embedding is supplied (live provider calls are async-only)."""
        emb_full = list(embedding) if embedding is not None else _offline_embed(text, dim=FULL_DIM)
        if len(emb_full) != FULL_DIM:
            raise ValueError(f"embedding has {len(emb_full)} dims; expected {FULL_DIM}")
        emb_coarse = matryoshka_truncate(emb_full, COARSE_DIM)
        meta = (metadata or {}) | {"text_len": len(text)}
        self.coarse.upsert(
            ids=[chunk_id], documents=[text],
            embeddings=[emb_coarse], metadatas=[meta],
        )
        self.full.upsert(
            ids=[chunk_id], documents=[text],
            embeddings=[emb_full], metadatas=[meta],
        )

    # ---------- read ----------

    def count(self) -> int:
        return self.full.count()

    async def query(
        self,
        text: str,
        *,
        coarse_k: int = DEFAULT_COARSE_K,
        final_k: int = DEFAULT_FINAL_K,
    ) -> list[RetrievedChunk]:
        # W.2 — the QUERY side of the pair. Chroma holds no production data
        # (W.0), but the asymmetry belongs on the right call regardless.
        emb_full = await embed_via_provider(self.provider, text, dim=FULL_DIM,
                                            kind="query")
        return self._two_stage(emb_full, coarse_k=coarse_k, final_k=final_k)

    def query_sync(
        self,
        text: str,
        *,
        coarse_k: int = DEFAULT_COARSE_K,
        final_k: int = DEFAULT_FINAL_K,
    ) -> list[RetrievedChunk]:
        emb_full = _offline_embed(text, dim=FULL_DIM)
        return self._two_stage(emb_full, coarse_k=coarse_k, final_k=final_k)

    # ---------- two-stage core ----------

    def _two_stage(
        self,
        emb_full: list[float],
        *,
        coarse_k: int,
        final_k: int,
    ) -> list[RetrievedChunk]:
        if not emb_full or len(emb_full) != FULL_DIM:
            return []
        emb_coarse = matryoshka_truncate(emb_full, COARSE_DIM)

        # Stage 1 — coarse on 256-dim collection
        coarse_n = max(1, min(coarse_k, max(self.coarse.count(), 1)))
        coarse_res = self.coarse.query(
            query_embeddings=[emb_coarse], n_results=coarse_n,
        )
        coarse_ids = (coarse_res.get("ids") or [[]])[0]
        if not coarse_ids:
            return []

        # Stage 2 — fetch the full embeddings of those candidates and rerank.
        # ChromaDB may return numpy arrays here, so guard with `is None` rather
        # than truthiness checks.
        got = self.full.get(
            ids=coarse_ids, include=["embeddings", "documents", "metadatas"],
        )
        ids   = got.get("ids")
        embs  = got.get("embeddings")
        docs  = got.get("documents")
        metas = got.get("metadatas")
        if ids is None or embs is None:
            return []
        ids   = list(ids)
        docs  = list(docs)  if docs  is not None else ["" for _ in ids]
        metas = list(metas) if metas is not None else [{} for _ in ids]

        scored: list[RetrievedChunk] = []
        for i, e in enumerate(embs):
            vec = list(e) if hasattr(e, "__iter__") else []
            if not vec:
                continue
            scored.append(RetrievedChunk(
                id=ids[i],
                text=docs[i] if i < len(docs) else "",
                metadata=metas[i] if i < len(metas) else {},
                score=cosine(emb_full, vec),
            ))
        scored.sort(key=lambda r: -r.score)
        return scored[:final_k]

    # ---------- maintenance ----------

    def reset(self) -> None:
        for name in (COARSE_COLLECTION, FULL_COLLECTION):
            try:
                self.client.delete_collection(name=name)
            except Exception:
                pass
        self.coarse = self.client.get_or_create_collection(
            name=COARSE_COLLECTION, metadata={"hnsw:space": "cosine"},
        )
        self.full = self.client.get_or_create_collection(
            name=FULL_COLLECTION, metadata={"hnsw:space": "cosine"},
        )


# ---------- batch helpers ----------

async def index_chunks(
    store: VectorStore,
    chunks: Iterable[tuple[str, str, dict | None]],
) -> int:
    n = 0
    for chunk_id, text, meta in chunks:
        await store.add(chunk_id, text, metadata=meta or {})
        n += 1
    return n


def index_chunks_sync(
    store: VectorStore,
    chunks: Iterable[tuple[str, str, dict | None]],
) -> int:
    n = 0
    for chunk_id, text, meta in chunks:
        store.add_sync(chunk_id, text, metadata=meta or {})
        n += 1
    return n
