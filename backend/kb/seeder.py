"""Seeders fetch from a KBSource and write Triples into the store.

Law 3 — Confirm before seeding. The async seeders never run on import; they
must be invoked explicitly with `confirmed=True` (or via UI confirmation flow
in Phase 8).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, NamedTuple

from backend.kb.gate import BeliefRejected
from backend.kb.granularity import Granularity
from backend.kb.source_registry import KBSource, default_registry
from backend.kb.tiered_store import TripleStore
from backend.memory import actions as _actions   # W10b: the recorder owns the network

logger = logging.getLogger(__name__)


class SeedRefused(RuntimeError):
    """Raised when a seeder is invoked without explicit confirmation."""


class SeedResult(NamedTuple):
    """What a seed run did, including what it declined to store.

    THREE FIELDS, NOT TWO. Every seeder writes `store.add()` in a loop, and
    since Packet K `add()` RAISES `BeliefRejected` rather than returning a
    falsy id. An unguarded loop therefore aborts the whole run on the first
    refused row, and a guarded-but-silent loop is worse: it seeds four of five
    and reports success. `refusals` is the channel that makes the difference
    visible (I-6 — declared, not inferred).

    Arity changed deliberately from `(seeded, warnings)`. A caller still
    unpacking two values fails loudly with a ValueError, which is the correct
    outcome — the alternative was an optional out-parameter nobody reads.
    """
    seeded: int
    warnings: list[str]
    refusals: list[str]


_REFUSAL_CAP = 25


def _record(refusals: list[str], scope: str, rej: BeliefRejected) -> None:
    """One line per refused row, and a log line whether or not anyone reads
    the return value — the seeders are also called from scripts."""
    logger.warning("seed refusal [%s]: %s", scope, rej)
    refusals.append(f"{scope}: {rej}")


def _cap(refusals: list[str]) -> list[str]:
    """Truncate the *reported* list, never the count.

    The list travels through a JSON response and a source whose labels this
    store refuses wholesale would put thousands of lines in it. The tail line
    carries the real total, so `len(refusals)` can be short but the operator
    is never told a smaller number than actually happened.
    """
    if len(refusals) <= _REFUSAL_CAP:
        return refusals
    return refusals[:_REFUSAL_CAP] + [
        f"… {len(refusals) - _REFUSAL_CAP} further refusals not listed "
        f"({len(refusals)} in total); all are in the log"]


def _normalise_concept(uri: str) -> str:
    # ConceptNet URIs look like "/c/en/cat/n" — strip prefix, keep label
    parts = uri.strip("/").split("/")
    return parts[2] if len(parts) >= 3 else uri


def _normalise_relation(uri: str) -> str:
    # "/r/IsA" -> "is_a"
    last = uri.strip("/").split("/")[-1]
    return "".join("_" + c.lower() if c.isupper() and i else c.lower()
                   for i, c in enumerate(last))


async def _fetch_conceptnet(concept: str, *, limit: int, client: _actions.Session) -> list[dict]:
    src: KBSource = default_registry.get("conceptnet")
    url = f"{src.endpoint}/c/en/{concept}"
    r = await client.get(url, kind="seed_conceptnet", params={"limit": limit}, timeout=10.0)
    r.raise_for_status()
    return r.json().get("edges", [])


async def seed_conceptnet(
    concepts: list[str],
    *,
    confirmed: bool,
    store: TripleStore,
    per_concept_limit: int = 20,
    granularity: Granularity = Granularity.TOPIC,
    action_store=None,
    trigger=None,
) -> SeedResult:
    """Fetch ConceptNet edges for each concept and persist as triples.

    W10b — every request is a recorded runtime action (`action_store`, the
    request's `trigger`): with no record to write, nothing is sent; a record
    that cannot be written stops the seed there (`ActionNotRecorded`, carrying
    the count seeded before it).

    Returns `SeedResult(seeded, warnings, refusals)`. `warnings` surfaces
    per-concept HTTP failures (5xx, 4xx) and network errors so the caller can
    show them to the user instead of returning a silent zero (Fix 3);
    `refusals` does the same for rows the ingest gate declined. ConceptNet is
    the likeliest of the three to produce them — it is an open crowd-sourced
    graph and its labels are not shaped for this store.
    """
    if not confirmed:
        raise SeedRefused("seed_conceptnet requires confirmed=True (Law 3)")
    if action_store is None or trigger is None:
        raise _actions.ActionNotRecorded("action_not_recorded: a ConceptNet seed with no record to write sent nothing")

    inserted = 0
    warnings: list[str] = []   # Fix 3 — function-local so concurrent calls don't bleed
    refusals: list[str] = []
    async with _actions.session(store=action_store, trigger=trigger) as client:
        for concept in concepts:
            try:
                edges = await _fetch_conceptnet(concept, limit=per_concept_limit, client=client)
            except _actions.ActionNotRecorded as e:
                e.seeded = inserted
                raise
            except _actions.HTTPStatusError as e:
                warnings.append(
                    f"{concept}: HTTP {e.response.status_code} from ConceptNet"
                )
                continue
            except (_actions.HTTPError, OSError) as e:
                warnings.append(
                    f"{concept}: network error — {type(e).__name__}"
                )
                continue
            for e in edges:
                start = _normalise_concept(e.get("start", {}).get("@id", ""))
                end = _normalise_concept(e.get("end", {}).get("@id", ""))
                rel = _normalise_relation(e.get("rel", {}).get("@id", ""))
                if not (start and end and rel):
                    continue
                weight = float(e.get("weight", 1.0)) / 5.0  # ConceptNet weights ~1-5
                weight = min(1.0, max(0.05, weight))
                try:
                    store.add(
                        subject=start, relation=rel, object=end,
                        weight=weight, tier=2, granularity=granularity,
                        source="conceptnet",
                    )
                except BeliefRejected as rej:
                    _record(refusals, concept, rej)
                    continue
                inserted += 1
    return SeedResult(inserted, warnings, _cap(refusals))


async def seed_wikidata(
    concepts: list[str],
    *,
    confirmed: bool,
    store: TripleStore,
    per_concept_limit: int = 20,
    granularity: Granularity = Granularity.TOPIC,
    action_store=None,
    trigger=None,
) -> SeedResult:
    """Fetch Wikidata triples for each concept via SPARQL.

    W10b — both requests a concept takes are recorded runtime actions, as
    `seed_conceptnet`'s are: with no record to write nothing is sent, and a
    record that cannot be written stops the seed there.

    Step 1: resolve concept string → QID via wbsearchentities API
    Step 2: SPARQL query for that QID's direct claims
    Step 3: parse bindings → triples → store.add()

    Returns `SeedResult` — P1/F-10 parity with seed_conceptnet: per-concept
    network failures surface as warnings instead of a silent seeded:0, and
    gate refusals surface as refusals rather than aborting the run. Wikidata
    labels are free text (`rel.lower().replace(" ", "_")[:40]` is the only
    shaping), so a refusal here is entirely reachable. The Wikidata URL is
    read from the KB source registry; the
    wbsearchentities endpoint lives on the same host so we derive it rather
    than hardcoding.
    """
    if not confirmed:
        raise SeedRefused("seed_wikidata requires confirmed=True (Law 3)")
    if action_store is None or trigger is None:
        raise _actions.ActionNotRecorded("action_not_recorded: a Wikidata seed with no record to write sent nothing")

    warnings: list[str] = []
    refusals: list[str] = []

    src: KBSource = default_registry.get("wikidata")
    # `src.endpoint` is the SPARQL endpoint, e.g.
    # https://query.wikidata.org/sparql. The wbsearchentities API lives at
    # https://www.wikidata.org/w/api.php — derive from the same scheme/host.
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(src.endpoint)
    api_url = urlunsplit((parts.scheme, "www.wikidata.org", "/w/api.php", "", ""))
    inserted = 0

    async with _actions.session(
        store=action_store, trigger=trigger,
        headers={
            "User-Agent": (
                "NEXUS-Cognitive-Substrate/1.0 "
                "(https://github.com/Slickfish/talima; "
                "operator-contact@nexus.local) "
                "httpx/0.27"
            ),
            "Accept": "application/json",
        },
        timeout=15.0,
    ) as client:
        for concept in concepts:
            # Step 1 — resolve to QID
            try:
                r = await client.get(api_url, kind="seed_wikidata", params={
                    "action":   "wbsearchentities",
                    "search":   concept,
                    "language": "en",
                    "format":   "json",
                    "limit":    1,
                }, timeout=15.0)
                r.raise_for_status()
                results = r.json().get("search", [])
                if not results:
                    warnings.append(f"{concept}: no Wikidata entity match")
                    continue
                qid = results[0]["id"]
            except _actions.ActionNotRecorded as e:
                e.seeded = inserted
                raise
            except (_actions.HTTPError, OSError, KeyError) as e:
                warnings.append(
                    f"{concept}: QID lookup failed — {type(e).__name__}")
                continue

            # Step 2 — SPARQL for direct claims
            sparql = f"""
            SELECT ?propLabel ?valueLabel WHERE {{
              wd:{qid} ?prop ?value .
              ?propEntity wikibase:directClaim ?prop .
              ?propEntity rdfs:label ?propLabel . FILTER(LANG(?propLabel)="en")
              ?value rdfs:label ?valueLabel . FILTER(LANG(?valueLabel)="en")
            }} LIMIT {per_concept_limit}
            """
            try:
                r = await client.get(
                    src.endpoint, kind="seed_wikidata",
                    params={"query": sparql, "format": "json"},
                    headers={"Accept": "application/sparql-results+json"},
                    timeout=15.0,
                )
                r.raise_for_status()
                bindings = r.json().get("results", {}).get("bindings", [])
            except _actions.ActionNotRecorded as e:
                e.seeded = inserted
                raise
            except (_actions.HTTPError, OSError) as e:
                warnings.append(
                    f"{concept}: SPARQL query failed — {type(e).__name__}")
                continue

            for b in bindings:
                rel = b.get("propLabel", {}).get("value", "")
                obj = b.get("valueLabel", {}).get("value", "")
                if not (rel and obj):
                    continue
                rel_norm = rel.lower().replace(" ", "_")[:40]
                try:
                    store.add(
                        subject=concept,
                        relation=rel_norm,
                        object=obj,
                        weight=0.7,
                        tier=2,
                        granularity=granularity,
                        source="wikidata",
                    )
                except BeliefRejected as rej:
                    _record(refusals, concept, rej)
                    continue
                inserted += 1

    return SeedResult(inserted, warnings, _cap(refusals))


def seed_offline_fixture(
    store: TripleStore,
    *,
    confirmed: bool,
    granularity: Granularity = Granularity.TOPIC,
) -> SeedResult:
    """Tiny offline fixture so tests + first-run UX work without network.
    Mirrors the shape of ConceptNet output.

    Returns a `SeedResult` like the other two, though its ten rows are
    hardcoded and pass the gate today. That is the reason to report rather
    than the reason to skip: if a future gate rule refuses one of them, the
    fixture would otherwise shrink silently and every caller that trusts the
    returned count would keep agreeing with itself.
    """
    if not confirmed:
        raise SeedRefused("seed_offline_fixture requires confirmed=True (Law 3)")

    fixture = [
        ("cat", "is_a", "mammal", 0.95),
        ("cat", "capable_of", "purr", 0.7),
        ("dog", "is_a", "mammal", 0.95),
        ("dog", "capable_of", "bark", 0.85),
        ("python", "is_a", "programming_language", 0.9),
        ("python", "used_for", "machine_learning", 0.8),
        ("fastapi", "is_a", "web_framework", 0.85),
        ("fastapi", "used_for", "api_serving", 0.8),
        ("ollama", "is_a", "model_server", 0.9),
        ("ollama", "hosts", "deepseek_r1", 0.7),
    ]
    n = 0
    refusals: list[str] = []
    for s, r, o, w in fixture:
        try:
            store.add(
                subject=s, relation=r, object=o, weight=w,
                tier=1, granularity=granularity, source="offline_fixture",
            )
        except BeliefRejected as rej:
            _record(refusals, "offline_fixture", rej)
            continue
        n += 1
    return SeedResult(n, [], _cap(refusals))


def seed_conceptnet_sync(*args, **kwargs) -> SeedResult:
    """Synchronous wrapper. Returns the same `SeedResult` as the async
    function (Fix 3)."""
    return asyncio.run(seed_conceptnet(*args, **kwargs))
