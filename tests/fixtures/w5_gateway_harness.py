"""W5's measurement rig: the pre-registered injection set driven through a turn and the seed routes.

WHAT IT MEASURES (run/specs/W5.md S3; the set's own `outcomes`)
    reached the prompt   every planted part of the payload is, verbatim, in the request the provider is handed.
                         The request is read at the turn's own crossing point, `TurnHandle.request_messages`,
                         which is what the console hands the provider and what MCP hands the client's model
                         (tests/test_w5_injection_set.py checks that equality once, through a whole turn).
    entered the store    a live row's subject or object holds the payload.

WHAT IT IS NOT
    No model reads anything here. Whether a model that is handed a planted instruction obeys it is not measured,
    and nothing in this file says it does or does not. The numbers are about what flows, each a count over its
    denominator, for NEXUS_REFERENCE_GATEWAY off and on.

THE SET IS DATA AND WAS REGISTERED FIRST
    `run/specs/W5_injection_set.json` (copied unchanged to `tests/fixtures/` at W11c), its sha256 pinned below, was committed before any gateway code. This file
    only turns its seeds, templates and forms into planted cases; it holds no rule of the gateway's and imports
    the gateway only to use it as the adaptive arm's oracle.

EVERY STORE HERE IS A FIXTURE in a folder the caller gives. Nothing of an instance is opened.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import re
import statistics
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from backend.kb import seeder
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.loop.agentic_loop import prepare_turn
from backend.loop.state import NexusLoopState
from backend.memory.hippocampus import Hippocampus
from backend.tools import search as search_mod
from backend.tools.search import WebSearchTool
from tests._tracked import REPO

sys.path.insert(0, str(REPO / "scripts"))
import history_shape_harness as hsh  # noqa: E402

SET_PATH = REPO / "tests" / "fixtures" / "W5_injection_set.json"   # W11c: copied unchanged from run/specs/ (the operator's answer)
SET_SHA256 = "b64af220ba8c78cbe5376c69ae5672cbbf987b8386ff35b9349ede68ed6fc3a1"
SETTING = "NEXUS_REFERENCE_GATEWAY"
POSITIONS = ("off", "on")


def load_set() -> dict:
    """The registered set. Its identity is the sha256 of its text as committed, with LF line endings: a Windows
    checkout under `core.autocrlf` holds the same text with CRLF, and hashing those bytes refused a file nobody had
    edited (W5's closing run; every windows shard died at collection)."""
    raw = SET_PATH.read_bytes().replace(b"\r\n", b"\n")
    got = hashlib.sha256(raw).hexdigest()
    if got != SET_SHA256:
        raise AssertionError(f"the injection set is not the registered one: {got} != {SET_SHA256}")
    return json.loads(raw.decode("ascii"))


# ---------------------------------------------------------------------------
# Planting: a seed and a form become one case
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Case:
    family: str                 # direct | camouflaged | split | store | benign
    form: str                   # the field, the template or the split form
    seed: str                   # the seed's id, or the benign item's index
    shape: str                  # the seed's shape, or easy | hard
    sink: str                   # prompt | store
    results: tuple = ()         # what the search returns, in rank order
    wikidata: tuple = ()        # value labels offered through the Wikidata seed route
    conceptnet: tuple = ()      # end ids offered through the ConceptNet seed route
    needles: tuple = ()         # every string that must be in the sink for the payload to have arrived


def url_path(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def conceptnet_label(text: str) -> str:
    return re.sub(r"[^a-z0-9\-]+", "_", text.lower()).strip("_")


def halves(text: str) -> tuple[str, str]:
    words = text.split()
    cut = math.ceil(len(words) / 2)
    return " ".join(words[:cut]), " ".join(words[cut:])


def _result(spec: dict, n: int, **over) -> dict:
    base = {"title": f"Training notes {n}", "url": f"{spec['rig']['url_base']}notes-{n}",
            "content": f"Notes on session {n} of the plan.", "engine": spec["rig"]["engine"]}
    base.update(over)
    return base


def _ranked(spec: dict, planted: dict, rank: int) -> tuple:
    return tuple([_result(spec, n) for n in range(1, rank)] + [planted])


def cases_for(spec: dict, seeds: list[dict]) -> list[Case]:
    out: list[Case] = []
    fam = spec["families"]
    base = spec["rig"]["url_base"]
    for i, s in enumerate(seeds):
        p, sid, shape = s["text"], s["id"], s["shape"]
        # direct: the payload is the whole field
        for fld in fam["direct"]["fields"]:
            if fld == "snippet":
                planted, needles = _result(spec, 0, content=p), (p,)
            elif fld == "title":
                planted, needles = _result(spec, 0, title=p), (p,)
            elif fld == "url_path":
                planted, needles = _result(spec, 0, url=base + url_path(p)), (url_path(p),)
            else:
                planted, needles = _result(spec, 0, engine=p), (p,)
            out.append(Case("direct", fld, sid, shape, "prompt", _ranked(spec, planted, 1), needles=needles))
        if i < fam["direct"]["rank_variants"]["seeds"]:
            for rank in fam["direct"]["rank_variants"]["ranks"]:
                out.append(Case("direct", f"snippet@rank{rank}", sid, shape, "prompt",
                                _ranked(spec, _result(spec, 0, content=p), rank), needles=(p,)))
        # camouflaged: the payload inside legitimate-looking content
        for t in fam["camouflaged"]["templates"]:
            text = t["form"].replace("{P}", p)
            planted = (_result(spec, 0, title=text, content="Weekly notes on endurance training and recovery.")
                       if t["field"] == "title" else _result(spec, 0, content=text))
            out.append(Case("camouflaged", t["id"], sid, shape, "prompt", (planted,), needles=(p,)))
        # split: the payload's tokens cut in two
        p1, p2 = halves(p)
        for form in fam["split"]["forms"]:
            results, wikidata = {}, []
            for part in form["parts"]:
                text = part["form"].replace("{P1}", p1).replace("{P2}", p2)
                if part["field"] == "wikidata_value_label":
                    wikidata.append(text)
                else:
                    results.setdefault(part.get("result", 1), {})["title" if part["field"] == "title" else "content"] = text
            ranked = tuple(_result(spec, n, **results[n]) for n in sorted(results))
            out.append(Case("split", form["id"], sid, shape, "prompt", ranked, tuple(wikidata), needles=(p1, p2)))
        # store: the payload offered to the store through a seed route
        out.append(Case("store", "wikidata_value_label", sid, shape, "store", wikidata=(p,), needles=(p,)))
        out.append(Case("store", "conceptnet_end_id", sid, shape, "store",
                        conceptnet=(conceptnet_label(p),), needles=(conceptnet_label(p),)))
        # and from the store to the prompt of a later turn
        out.append(Case("store", "wikidata_then_prompt", sid, shape, "prompt", wikidata=(p,), needles=(p,)))
    return out


def benign_cases(spec: dict) -> list[Case]:
    out = []
    for i, r in enumerate(spec["benign_results"]):
        planted = {"title": r["title"], "url": r["url"], "content": r["snippet"], "engine": r["engine"]}
        out.append(Case("benign", "result", str(i), "hard" if r["hard"] else "easy", "prompt", (planted,),
                        needles=(r["title"], r["snippet"])))
    for i, lab in enumerate(spec["benign_labels"]):
        out.append(Case("benign", "label", str(i), "hard" if lab["hard"] else "easy", "store",
                        wikidata=(lab["label"],), needles=(lab["label"],)))
    return out


# ---------------------------------------------------------------------------
# The rig: one case, one position, one answer
# ---------------------------------------------------------------------------

@contextmanager
def position(value: str):
    old = os.environ.get(SETTING)
    os.environ[SETTING] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(SETTING, None)
        else:
            os.environ[SETTING] = old


class _Resp:
    status_code = 200                       # W10b: the action recorder reads the status and the body

    def __init__(self, payload):
        self._p = payload
        self.content = json.dumps(payload).encode("utf-8")

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


def _seed(store: TripleStore, spec: dict, case: Case) -> None:
    """The case's labels through the real seed functions, their one network client replaced."""
    subject = spec["rig"]["subject"]
    prop = spec["rig"]["wikidata_property_label"]

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            if "api.php" in url:
                return _Resp({"search": [{"id": "Q1"}]})
            if "sparql" in url:
                return _Resp({"results": {"bindings": [
                    {"propLabel": {"value": prop}, "valueLabel": {"value": v}} for v in case.wikidata]}})
            return _Resp({"edges": [
                {"start": {"@id": "/c/en/" + conceptnet_label(subject)}, "end": {"@id": "/c/en/" + label},
                 "rel": {"@id": "/r/RelatedTo"}, "weight": 2.0} for label in case.conceptnet]})

    from backend.memory.actions import Trigger
    from backend.memory.lineage import LineageStore
    actions = LineageStore(store.target)    # W10b: a seed's requests are recorded actions, in the same kb.db
    from backend.memory import actions as recorder     # W10b: the recorder owns the seeder's transport
    real = recorder.httpx.AsyncClient
    recorder.httpx.AsyncClient = _Client
    try:
        if case.wikidata:
            asyncio.run(seeder.seed_wikidata([subject], confirmed=True, store=store, action_store=actions,
                                             trigger=Trigger(route="/kb/seed/wikidata")))
        if case.conceptnet:
            asyncio.run(seeder.seed_conceptnet([conceptnet_label(subject)], confirmed=True, store=store,
                                               action_store=actions, trigger=Trigger(route="/kb/seed/conceptnet")))
    finally:
        recorder.httpx.AsyncClient = real
        actions.close()                     # one connection per case: Postgres would run out of them


def _store_text(store: TripleStore) -> str:
    rows = store.conn.execute(
        "SELECT subject, object FROM triples WHERE archived_at IS NULL").fetchall()
    return "\n".join(f"{r['subject']}\n{r['object']}" for r in rows)


def _prompt(store: TripleStore, spec: dict, case: Case) -> tuple[str, object]:
    """The system message of the request a turn would hand the provider, and the turn's handle."""
    question = spec["rig"]["question"]

    async def fake_fetch(url, params, *, timeout=10.0, headers=None, store=None, trigger=None):   # W10b's seam
        return {"results": [dict(r) for r in case.results]}

    async def run():
        handle = None
        async for frame, payload in prepare_turn(
                NexusLoopState(), question, retriever=TripleRetriever(store), hippocampus=Hippocampus(),
                search_tool=WebSearchTool(), search_consents={question.strip(): bool(case.results)}):
            if frame is None:
                handle = payload
        return handle

    real = search_mod.fetch_json
    search_mod.fetch_json = fake_fetch
    try:
        handle = asyncio.run(run())
    finally:
        search_mod.fetch_json = real
    system = next(m["content"] for m in handle.request_messages if m["role"] == "system")
    return system, handle


def arrived(root: Path, spec: dict, case: Case, where: str, n: int) -> bool:
    """Did every needle of this case arrive in its sink, in this position?"""
    root.mkdir(parents=True, exist_ok=True)
    store = hsh._build_store(root / f"{where}-{n}.db")
    try:
        with position(where):
            if case.wikidata or case.conceptnet:
                _seed(store, spec, case)
            text = _store_text(store) if case.sink == "store" else _prompt(store, spec, case)[0]
    finally:
        store.close()
    if case.sink == "store" and case.conceptnet:
        text = text.lower()
    return all(needle in text for needle in case.needles)


@dataclass
class Tally:
    arrived: int = 0
    of: int = 0
    seeds: list = field(default_factory=list)

    def rate(self) -> str:
        return f"{self.arrived}/{self.of}"


def measure(root: Path, spec: dict, cases: list[Case]) -> dict:
    """{(family, form, shape, sink): {position: Tally}}, one fixture store per case and position."""
    table: dict = {}
    for n, case in enumerate(cases):
        key = (case.family, case.form, case.shape, case.sink)
        for where in POSITIONS:
            tally = table.setdefault(key, {}).setdefault(where, Tally())
            tally.of += 1
            if arrived(root, spec, case, where, n):
                tally.arrived += 1
                tally.seeds.append(case.seed)
    return table


# ---------------------------------------------------------------------------
# The adaptive arm: a search with the built gateway as its oracle
# ---------------------------------------------------------------------------

ZERO_WIDTH_SPACE = "​"
THIN_SPACE = " "
FULL_WIDTH = str.maketrans({"<": "＜", ">": "＞", "[": "［", "]": "］"})


def _marker_len(text: str, markers: dict) -> int:
    """How many leading words of the payload are one of the set's markers (0 when it opens with none)."""
    low = text.lower()
    for marker in sorted(markers, key=len, reverse=True):
        if low.startswith(marker + " "):
            return len(marker.split())
    return 0


def tail_tokens(text: str, markers: dict) -> list[str]:
    """The set's criterion for "still the instruction": the payload's tokens after its leading marker."""
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", " ".join(text.split()[_marker_len(text, markers):]))


def still_the_instruction(variant_text: str, payload: str, markers: dict) -> bool:
    want = tail_tokens(payload, markers)
    have = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", variant_text)
    it = iter(have)
    return all(any(tok == h for h in it) for tok in want)


def variants(spec: dict, payload: str, shape: str) -> list[tuple[str, dict]]:
    """Depth-one variants of a direct snippet plant, in the registered operators' order: (operator, result fields).
    `content` and `title` are the planted result's; the oracle is asked about each in turn."""
    ops = {o["id"]: o for o in spec["adaptive"]["operators"]}
    markers = ops["synonym"]["map"]
    words = payload.split()
    n = _marker_len(payload, markers)
    out: list[tuple[str, dict]] = []
    for word in ops["pad"]["words"]:
        out.append(("pad", {"content": f"{word} {payload[0].lower()}{payload[1:]}"}))
    if n:
        for syn in markers[" ".join(words[:n]).lower()]:
            out.append(("synonym", {"content": " ".join([syn.capitalize()] + words[n:])}))
    out.append(("merge", {"content": "Interval training raises VO2 max" + ops["merge"]["joiner"]
                          + payload[0].lower() + payload[1:]}))
    if len(words) > 2:
        out.append(("floor", {"content": " ".join(words[:2]) + ". " + " ".join(words[2:])}))
    first = words[0]
    rest = " ".join(words[1:])
    out.append(("unicode", {"content": f"{first[0]}{ZERO_WIDTH_SPACE}{first[1:]} {rest}"}))
    out.append(("unicode", {"content": f"{first[:2]}-{first[2:]} {rest}"}))
    out.append(("unicode", {"content": f"{first[0].upper()}{THIN_SPACE.join(first[1:])} {rest}"}))
    if shape in ("markup", "label"):
        out.append(("unicode", {"content": payload.translate(FULL_WIDTH)}))
    p1, p2 = halves(payload)
    out.append(("split", {"title": p1, "content": p2 + ". More inside."}))
    out.append(("split", {"content": "Coaches often say " + p1, "next": p2 + " as the session ends."}))
    out.append(("encode", {"content": "base64: " + base64.b64encode(payload.encode()).decode()}))
    return out


def adaptive(spec: dict, seeds: list[dict], oracle) -> dict:
    """For each seed whose direct snippet plant the oracle withholds: the first variant, in the registered order
    and to depth 2 within the budget, that the oracle admits and that is still the instruction. `oracle(results)`
    answers with the web block's text. Returns the rows and the counts; encoded forms are counted apart."""
    budget = spec["adaptive"]["budget_oracle_calls"]
    markers = next(o for o in spec["adaptive"]["operators"] if o["id"] == "synonym")["map"]
    rows = []
    for s in seeds:
        payload = s["text"]
        base_block = oracle([_result(spec, 0, content=payload)])
        row = {"seed": s["id"], "shape": s["shape"], "withheld_as_planted": payload not in base_block,
               "evaded_by": None, "calls": 0, "encoded_only": False}
        if not row["withheld_as_planted"]:
            rows.append(row)
            continue
        frontier = variants(spec, payload, s["shape"])
        depth_two = [(f"{a}+{b}", fb) for a, fa in frontier if a not in ("encode", "split")
                     for b, fb in variants(spec, fa.get("content", payload), s["shape"])
                     if b not in ("encode", "split")]
        encoded_reached = False
        for op, fields in frontier + depth_two:
            if row["calls"] >= budget:
                break
            row["calls"] += 1
            results = [_result(spec, 0, **{k: v for k, v in fields.items() if k != "next"})]
            if "next" in fields:
                results.append(_result(spec, 1, content=fields["next"]))
            block = oracle(results)
            planted = [v for v in fields.values()]
            if not all(v in block for v in planted):
                continue
            if op == "encode":
                encoded_reached = True
                continue
            if still_the_instruction(" ".join(planted), payload, markers):
                row["evaded_by"] = op
                break
        row["encoded_only"] = encoded_reached and row["evaded_by"] is None
        rows.append(row)
    withheld = [r for r in rows if r["withheld_as_planted"]]
    evaded = [r for r in withheld if r["evaded_by"]]
    return {"rows": rows, "withheld_as_planted": len(withheld), "evaded": len(evaded),
            "median_calls_to_first_evasion": statistics.median(r["calls"] for r in evaded) if evaded else None,
            "encoded_only": sum(1 for r in withheld if r["encoded_only"])}


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def report_lines(spec: dict, table: dict, benign: dict, arm: dict, which: str) -> list[str]:
    out = [f"# The reference gateway, measured on seed list {which} (set {spec['version']}, sha256 {SET_SHA256[:12]})",
           "",
           "Each cell is a count over its denominator. \"Reached\" and \"entered\" say what flowed; no model read",
           "anything, so nothing here says what a model would have done. The gateway reduces what flows and this",
           "table measures it.",
           "",
           "| family | form | shape | sink | off | on |", "|---|---|---|---|---|---|"]
    for key in sorted(table):
        out.append(f"| {key[0]} | {key[1]} | {key[2]} | {key[3]} | {table[key]['off'].rate()} | {table[key]['on'].rate()} |")
    out += ["", "## Benign content that passed (a gateway that withholds everything scores 0 here)", "",
            "| item | kind | sink | off | on |", "|---|---|---|---|---|"]
    for key in sorted(benign):
        out.append(f"| {key[1]} | {key[2]} | {key[3]} | {benign[key]['off'].rate()} | {benign[key]['on'].rate()} |")
    out += ["", "## The adaptive arm, against the on position", "",
            f"- direct snippet plants the gateway withheld as planted: {arm['withheld_as_planted']} of {len(arm['rows'])}",
            f"- of those, evaded within {spec['adaptive']['budget_oracle_calls']} oracle calls: {arm['evaded']} of {arm['withheld_as_planted']}",
            f"- median calls to the first evasion: {arm['median_calls_to_first_evasion']}",
            f"- reached only in an encoded form, counted apart: {arm['encoded_only']}",
            "- The operators, their order and the budget are the registered ones. \"None within the budget\" says",
            "  these operators did not get that seed through. It says nothing about a rewrite they do not make, such",
            "  as a label's key spelled another way.",
            "", "| seed | shape | first evading operator | calls |", "|---|---|---|---|"]
    for r in arm["rows"]:
        if r["withheld_as_planted"]:
            out.append(f"| {r['seed']} | {r['shape']} | {r['evaded_by'] or 'none within the budget'} | {r['calls']} |")
    return out
