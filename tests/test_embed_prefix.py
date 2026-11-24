"""Packet W.2 — embedding task prefixes, and the models they must not reach.

MEASURED ON THE OPERATOR'S MACHINE, nomic-embed-text:

    bare      signal 0.4047   control 0.2570   gap +0.1477
    prefixed  signal 0.5251   control 0.2570   gap +0.2681

ONE CAVEAT ON THAT TABLE, RECORDED RATHER THAN SMOOTHED OVER
    The control is identical to four decimal places in both rows, which means
    it was measured once, un-prefixed, and reused. A prefix adds tokens BOTH
    texts share, and shared tokens lift every cosine — including between
    unrelated texts. So the prefixed arm's `gap` compares a prefixed signal
    against a bare control, and the true separation is unknown until the
    control is measured prefixed too.

    The change is still right on the model's documented contract, and the
    signal moving 0.4047 -> 0.5251 is real. But "the gap nearly doubled" is
    not established, and this file does not assert it.

WHAT IS ASSERTED HERE
    That the prefix is applied to the right side of the pair, that a model
    with no entry is left alone, and that there is no second route to
    `provider.get_embedding` that could bypass it.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from backend.providers.model_map import EMBED_PREFIXES, embed_prefix
from backend.vector.embeddings import embed_via_provider
from tests._tracked import tracked_backend

REPO = Path(__file__).resolve().parent.parent


class _Recorder:
    def __init__(self):
        self.seen: list[str] = []

    async def get_embedding(self, text: str) -> list[float]:
        self.seen.append(text)
        return [0.1] * 768


def _embed(provider, text, **kw):
    return asyncio.run(embed_via_provider(provider, text, **kw))


# ---------------------------------------------------------------------------
# the table
# ---------------------------------------------------------------------------

def test_the_two_sides_get_different_prefixes():
    """The asymmetry is the point. Embedding a question and a claim with the
    same call throws away what the model was trained to distinguish."""
    q = embed_prefix("nomic-embed-text", "query")
    d = embed_prefix("nomic-embed-text", "document")
    assert q == "search_query: "
    assert d == "search_document: "
    assert q != d


def test_a_model_with_no_entry_gets_nothing():
    """A prefix is a per-model contract, not an improvement. all-MiniLM, gte
    and mxbai take no prefix; e5 wants `query:`/`passage:`; bge instructs the
    query only. Guessing on their behalf makes the prefix into content."""
    for model in ("all-MiniLM-L6-v2", "mxbai-embed-large", "gte-base",
                  "intfloat/e5-large", "BAAI/bge-base-en", "", "unknown:latest"):
        assert embed_prefix(model, "query") == ""
        assert embed_prefix(model, "document") == ""


def test_the_match_is_a_prefix_so_variants_and_tags_are_covered():
    for model in ("nomic-embed-text", "nomic-embed-text:latest",
                  "nomic-embed-text-v1.5-Q4_0", "NOMIC-EMBED-TEXT"):
        assert embed_prefix(model, "document") == "search_document: "


def test_an_unknown_kind_is_not_guessed():
    assert embed_prefix("nomic-embed-text", "passage") == ""
    assert embed_prefix("nomic-embed-text", "") == ""


def test_every_entry_defines_both_sides():
    """A table with a query prefix and no document prefix would silently embed
    one side raw, which is the failure this whole change is about."""
    for model, table in EMBED_PREFIXES.items():
        assert set(table) == {"query", "document"}, model
        assert all(v for v in table.values()), model


# ---------------------------------------------------------------------------
# application
# ---------------------------------------------------------------------------

def test_the_prefix_reaches_the_provider(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    p = _Recorder()
    _embed(p, "cardiac safety", kind="query")
    _embed(p, "QTc change was 11.4ms", kind="document")
    assert p.seen == ["search_query: cardiac safety",
                      "search_document: QTc change was 11.4ms"]


def test_document_is_the_default_kind(monkeypatch):
    """Indexing is the bulk of the calls, and a document embedded as a query
    is the more damaging mistake — it would poison the stored index rather
    than one turn."""
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    p = _Recorder()
    _embed(p, "some belief")
    assert p.seen == ["search_document: some belief"]


def test_a_backend_whose_embed_model_has_no_entry_sends_raw(monkeypatch):
    """The rollback, and the pre-W.2 behaviour byte for byte."""
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    monkeypatch.setitem(
        __import__("backend.providers.model_map", fromlist=["MODEL_MAP"])
        .MODEL_MAP["ollama"], "embed", "all-MiniLM-L6-v2")
    p = _Recorder()
    _embed(p, "some belief", kind="query")
    assert p.seen == ["some belief"]


def test_lmstudio_gets_the_prefix_too(monkeypatch):
    """Both backends ship a nomic variant, so both are covered — the table is
    keyed by MODEL, not by backend, which is why this works without a second
    implementation."""
    monkeypatch.setenv("LLM_BACKEND", "lmstudio")
    p = _Recorder()
    _embed(p, "cardiac safety", kind="query")
    assert p.seen == ["search_query: cardiac safety"]


# ---------------------------------------------------------------------------
# no second route
# ---------------------------------------------------------------------------

def test_embed_via_provider_is_the_only_caller_of_get_embedding():
    """AST, not text search. The prefix is applied at one chokepoint, so a
    second call site would silently embed without it — and an index built half
    prefixed and half not is worse than one built neither way."""
    offenders = []
    for path in tracked_backend():
        rel = Path(path).resolve().relative_to(REPO).as_posix()
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get_embedding"):
                if rel == "backend/vector/embeddings.py":
                    continue          # the chokepoint itself
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        f"provider.get_embedding called outside embed_via_provider: {offenders}")


# ---------------------------------------------------------------------------
# W.3 — a measurement may not fall back
# ---------------------------------------------------------------------------

def test_strict_raises_instead_of_returning_a_hash_vector():
    from backend.vector import embeddings as emb

    class _Down:
        async def get_embedding(self, text):
            raise TimeoutError("ReadTimeout")

    emb._mark_healthy()
    with pytest.raises(emb.EmbeddingUnavailable) as excinfo:
        _embed(_Down(), "anything", strict=True)
    assert "TimeoutError" in str(excinfo.value)
    emb._mark_healthy()


def test_strict_does_not_count_or_claim_a_fallback(caplog):
    """The log must not describe something that did not happen.

    The first W.3 run printed `embedding fallback engaged — using
    deterministic hash pseudo-embeddings` immediately before raising, so the
    operator would have read that hash vectors were in use when the whole
    point was that none were. That is the same class of untrue status message
    the packet exists to remove, one level down.
    """
    import logging

    from backend.vector import embeddings as emb

    class _Down:
        async def get_embedding(self, text):
            raise TimeoutError("ReadTimeout")

    emb._mark_healthy()
    before = emb.embedding_status()["fallbacks"]
    with caplog.at_level(logging.WARNING, logger="nexus.embeddings"):
        with pytest.raises(emb.EmbeddingUnavailable):
            _embed(_Down(), "anything", strict=True)
    assert emb.embedding_status()["fallbacks"] == before, (
        "a refusal was counted as a substitution")
    assert emb.embedding_status()["degraded"] is True, (
        "the provider IS degraded and /healthz should still say so")
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "hash pseudo-embeddings" not in text, (
        f"the log claims a fallback that did not happen: {text}")
    assert "refused" in text
    emb._mark_healthy()


def test_strict_also_refuses_an_empty_vector():
    """A provider that answers 200 with `{"embedding": []}` is not reachable
    in any sense that matters, and it took the same silent fallback."""
    from backend.vector import embeddings as emb

    class _Empty:
        async def get_embedding(self, text):
            return []

    emb._mark_healthy()
    with pytest.raises(emb.EmbeddingUnavailable):
        _embed(_Empty(), "anything", strict=True)
    emb._mark_healthy()


def test_production_still_degrades_rather_than_raising():
    """The asymmetry is deliberate and is the whole design. A turn whose query
    embedding times out must still be answered from the lexical channel;
    only a measurement has the luxury of refusing to produce a number."""
    from backend.vector import embeddings as emb

    class _Down:
        async def get_embedding(self, text):
            raise TimeoutError("ReadTimeout")

    emb._mark_healthy()
    vec = _embed(_Down(), "anything")          # strict defaults to False
    assert len(vec) == emb.FULL_DIM
    emb._mark_healthy()


def test_the_fallback_counter_survives_a_recovery():
    """THE SILENT CONTINUE, in one test.

    One ReadTimeout in the middle of indexing 800 beliefs sets `degraded`;
    the next success clears it. A script that checked `degraded` at the end
    therefore saw a clean flag while some of the vectors it had just written
    were hash noise — which is exactly what was reported. `fallbacks` is
    monotone so the question "did anything fall back during this work" stays
    answerable after recovery.
    """
    from backend.vector import embeddings as emb

    class _Flaky:
        def __init__(self):
            self.n = 0

        async def get_embedding(self, text):
            self.n += 1
            if self.n == 2:
                raise TimeoutError("ReadTimeout")
            return [0.1] * 768

    emb._mark_healthy()
    before = emb.embedding_status()["fallbacks"]
    p = _Flaky()
    for _ in range(4):
        _embed(p, "text")                       # non-strict: the old behaviour
    status = emb.embedding_status()
    assert status["degraded"] is False, "the recovery did happen"
    assert status["fallbacks"] == before + 1, (
        "a fallback occurred and left no trace a completed run could read")
    assert "TimeoutError" in status["first_reason"]
    emb._mark_healthy()


@pytest.mark.parametrize("script", ["measure_retrieval.py", "who_outranks.py",
                                    "probe_prefix.py"],
                         ids=lambda s: s)
def test_the_measurement_scripts_embed_strictly(script):
    """The class, not the instance. Both scripts produce numbers; neither may
    produce one from a fallback vector. AST so that importing the helper and
    forgetting the keyword still fails."""
    path = REPO / "scripts" / script
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", getattr(n.func, "attr", ""))
             == "embed_via_provider"]
    assert calls, f"{script} no longer embeds — re-derive this guard"
    for call in calls:
        strict = next((kw.value for kw in call.keywords if kw.arg == "strict"),
                      None)
        assert isinstance(strict, ast.Constant) and strict.value is True, (
            f"{script}:{call.lineno} embeds without strict=True — a "
            f"measurement may not fall back")


@pytest.mark.parametrize("script", ["measure_retrieval.py", "who_outranks.py",
                                    "probe_prefix.py"],
                         ids=lambda s: s)
def test_the_measurement_scripts_name_the_failure_and_exit(script):
    """Aborting is not enough on its own: the operator's report was that the
    scripts continued *silently*, so the failure has to be visible in the text
    and in the exit code."""
    src = (REPO / "scripts" / script).read_text(encoding="utf-8")
    tree = ast.parse(src)
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)
                and n.type is not None
                and "EmbeddingUnavailable" in ast.dump(n.type)]
    assert handlers, f"{script} does not catch EmbeddingUnavailable"
    returns = [n.value.value for h in handlers for n in ast.walk(h)
               if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)]
    assert 5 in returns, (
        f"{script} does not exit 5 on EmbeddingUnavailable — a caller cannot "
        f"distinguish it from a missing store")
    assert "EmbeddingUnavailable" in src and "ABORTED" in src


def test_the_degradation_flag_still_fires_through_the_prefixed_path():
    from backend.vector import embeddings as emb

    class _Down:
        async def get_embedding(self, text):
            raise ConnectionRefusedError("no daemon")

    emb._mark_healthy()
    vec = _embed(_Down(), "anything", kind="query")
    assert len(vec) == emb.FULL_DIM
    assert emb.embedding_status()["degraded"] is True
    emb._mark_healthy()
