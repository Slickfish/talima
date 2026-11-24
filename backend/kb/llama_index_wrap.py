"""LlamaIndex wrapper — chunk text with source metadata + granularity.

Phase 2 requirement: "LlamaIndex chunks have source metadata; each triple
tagged with granularity level". We use llama-index-core's SentenceSplitter
to produce nodes; each node carries `source` and `granularity` in its
metadata dict, and we mirror those rows into the `chunks` SQLite table so
they survive process restart.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Iterable

from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore

try:
    from llama_index.core.node_parser import SentenceSplitter
    from llama_index.core.schema import Document, TextNode
    LLAMA_AVAILABLE = True
except ImportError:  # llama-index optional at import time; tests skip cleanly
    SentenceSplitter = None  # type: ignore[assignment]
    Document = None  # type: ignore[assignment]
    TextNode = None  # type: ignore[assignment]
    LLAMA_AVAILABLE = False


@dataclass(frozen=True)
class Chunk:
    text: str
    source: str
    granularity: str
    metadata: dict


def _infer_granularity(text: str) -> Granularity:
    n_chars = len(text)
    if n_chars <= 200:
        return Granularity.UTTERANCE
    if n_chars <= 1500:
        return Granularity.TURN
    if n_chars <= 8000:
        return Granularity.SESSION
    return Granularity.TOPIC


def split_with_metadata(
    text: str,
    *,
    source: str,
    extra_metadata: dict | None = None,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> list[Chunk]:
    """Sentence-split `text` into chunks. Each chunk carries source + granularity."""
    if not LLAMA_AVAILABLE:
        raise RuntimeError(
            "llama-index-core is not installed — `pip install llama-index-core`"
        )
    splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    base_meta = {"source": source, **(extra_metadata or {})}
    doc = Document(text=text, metadata=base_meta)
    nodes = splitter.get_nodes_from_documents([doc])
    out = []
    for n in nodes:
        gran = _infer_granularity(n.text)
        n.metadata["granularity"] = gran.value
        n.metadata["source"] = source
        out.append(Chunk(
            text=n.text, source=source,
            granularity=gran.value, metadata=dict(n.metadata),
        ))
    return out


def persist_chunks(store: TripleStore, chunks: Iterable[Chunk]) -> int:
    """Write chunks to the SQLite `chunks` table for restart-survivable RAG."""
    n = 0
    now = time.time()
    for c in chunks:
        store.conn.execute(
            "INSERT INTO chunks (text, source, granularity, metadata_json, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (c.text, c.source, c.granularity, json.dumps(c.metadata), now),
        )
        n += 1
    store.conn.commit()
    return n


def load_chunks(store: TripleStore, *, source: str | None = None) -> list[Chunk]:
    sql = "SELECT * FROM chunks"
    args: list = []
    if source:
        sql += " WHERE source = ?"; args.append(source)
    rows = store.conn.execute(sql, args).fetchall()
    return [
        Chunk(text=r["text"], source=r["source"], granularity=r["granularity"],
              metadata=json.loads(r["metadata_json"]))
        for r in rows
    ]
