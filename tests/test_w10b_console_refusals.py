"""W10b (the verifier's MF-2) — the console says a refused action by name, never as "no results" or a new offer.

WHAT IS HELD HERE (the page's own functions, run under node in the style of test_sse_stream_resilience.py)
    C1 A turn's `kb_pressure_alert {type: web_search, action: not_recorded}` renders a card naming the refusal with
       the server's words, and no search proposal.
    C2 `POST /search/execute` answering 503 `{refused: action_not_recorded, detail}` turns the card into the refusal,
       by name, with the server's words; never "no results found".
    C3 A non-OK answer that names no refusal says its HTTP status; never "no results found" (law 5: a default does
       not stand in for a result).
    C4 The ordinary paths are unchanged: a proposal frame renders a proposal; results render as results; a search
       that ran and found nothing still says so.
    C5 The server's words reach the card escaped (the delta check's SF5).
    C6 The refusal card adds itself and removes nothing: an offer already showing, such as the assertion offer the
       loop sends before the search, stays (the delta check's SF4).

LAW 16. Run on c4ab8d0 (the console before the fix): C1 to C3 fail there (C1: the frame is rendered as a proposal;
C2 and C3: the card says "no results found"); C4 passes there and here. Run on 40bc2ee: C5 and C6 fail there (the
delta check's SF5 and SF4: the error went in unescaped, and the card removed the first `.search-proposal`, an
assertion offer included). C1's on-page count and C3's heading pass there; they kill the delta check's F7 and F4.
"""
from __future__ import annotations

import json
import re

import pytest

from tests.test_sse_stream_resilience import FRONTEND, _frame, _node, pytestmark  # noqa: F401 — node or skip

DETAIL = "action_not_recorded: the web_search request record could not be written (OperationalError); it was not sent"
REFUSAL = {"refused": "action_not_recorded", "detail": DETAIL}


def _functions(*names: str) -> str:
    """The page's own functions by name; one the page does not define is left out (the pre-fix page)."""
    page, out = FRONTEND.read_text(encoding="utf-8"), []
    for name in names:
        m = re.search(r"^(?:async )?function " + name + r"\(.*?^\}", page, re.S | re.M)
        if m:
            out.append(m.group(0))
    return "\n".join(out)


HARNESS = _functions("_esc", "confirmSearch", "renderSearchNotSent") + r"""
globalThis.window = globalThis;
function _sendWithSearchContext() {}
renderSearchProposal = function (q) { globalThis.__proposed = q; };
globalThis.__created = [];
globalThis.document = { createElement() { const e = mkEl(); e.querySelector = () => mkEl();
                                          globalThis.__created.push(e); return e; } };
function cards() { return globalThis.__created.map(e => e.innerHTML + ' ' + e.textContent); }
function onPage() { return globalThis.__created.filter(e => e.parentElement).length; }
function removedOffers(fn) {
  const real = mkEl; let removed = 0;
  mkEl = function (cls) { const e = real(cls);
    e.querySelector = (sel) => sel === '.search-proposal' ? { remove() { removed += 1; } } : real();
    return e; };
  try { fn(); } finally { mkEl = real; }
  return removed;
}
function report(extra) { console.log(JSON.stringify(Object.assign({
    proposed: globalThis.__proposed === undefined ? null : globalThis.__proposed, cards: cards()}, extra))); }
async function clicked(status, body) {
  globalThis.fetch = async () => ({ ok: status < 400, status, json: async () => body });
  const card = mkEl('search-proposal'); card.dataset.query = 'q'; card.querySelector = () => mkEl();
  await confirmSearch(card);
  return card.innerHTML;
}
"""


def _run(js: str) -> dict:
    code, out, err = _node(HARNESS + js)
    assert code == 0, err[-2000:]
    return json.loads(out.splitlines()[-1])


def test_c1_a_turns_unrecorded_search_is_said_by_name_and_offers_nothing():
    frame = _frame("kb_pressure_alert", {"type": "web_search", "action": "not_recorded", "query": "q",
                                         "detail": DETAIL})
    seen = _run(f"handleFrame({json.dumps(frame)}, mkBody()); report({{onPage: onPage()}});")
    print(f"\nC1 {seen}")
    assert seen["proposed"] is None, "a refusal is not an offer to search"
    said = [c for c in seen["cards"] if "it was not sent" in c]
    assert said and "not recorded" in said[0], seen["cards"]
    assert seen["onPage"] == 1, "the card is on the page"


def test_c2_the_routes_refusal_is_said_by_name_never_as_no_results():
    seen = _run(f"clicked(503, {json.dumps(REFUSAL)}).then(card => report({{card}}));")
    print(f"\nC2 {seen['card'].split()}")
    assert "action_not_recorded" in seen["card"] and "it was not sent" in seen["card"], seen["card"]
    assert "no results found" not in seen["card"] and "No search results" not in seen["card"], seen["card"]


@pytest.mark.parametrize("status", [502, 503])
def test_c3_a_failure_that_names_no_refusal_says_its_status(status):
    seen = _run(f"clicked({status}, {{}}).then(card => report({{card}}));")
    assert f"HTTP {status}" in seen["card"] and "no results found" not in seen["card"], seen["card"]
    assert "Search failed" in seen["card"] and "No search results" not in seen["card"], seen["card"]


def test_c5_the_servers_words_reach_the_card_escaped():
    seen = _run("clicked(502, {error: '<img src=x onerror=alert(1)>'}).then(card => report({card}));")
    assert "&lt;img" in seen["card"] and "<img" not in seen["card"], seen["card"]


def test_c6_the_refusal_card_removes_no_offer_already_showing():
    frame = _frame("kb_pressure_alert", {"type": "web_search", "action": "not_recorded", "query": "q",
                                         "detail": DETAIL})
    seen = _run(f"report({{removed: removedOffers(() => handleFrame({json.dumps(frame)}, mkBody()))}});")
    assert seen["removed"] == 0, seen


def test_c4_the_ordinary_paths_are_unchanged():
    frame = _frame("kb_pressure_alert", {"type": "web_search", "query": "latest python", "reasons": ["temporal"]})
    seen = _run(f"handleFrame({json.dumps(frame)}, mkBody());"
                "clicked(200, {count: 1, results: [{title: 't', url: 'u', snippet: 's', engine: 'e'}]})"
                ".then(found => clicked(200, {count: 0, results: []}).then(none => report({found, none})));")
    assert seen["proposed"] == "latest python", seen
    assert "1 results found" in seen["found"] and "no results found" in seen["none"], seen
