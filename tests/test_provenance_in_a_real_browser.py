"""The provenance expander, driven in a real browser against a real server.

WHY THIS EXISTS
    Four rounds went into "why is `.prov-toggle` missing", and each ended with
    a mechanism that was true and not operative. Every round shared one
    method: read the page, reconstruct the path, and ask the operator to
    instrument a page function from the console. That method cannot
    distinguish its three failure modes —

      1. the page in the browser is not the file in the checkout;
      2. the call site does not resolve through the binding that was replaced;
      3. the code ran and did nothing.

    — because all three produce silence. The last round's null result was
    partly mode 2 by construction: `send.onclick = sendMessage`
    (nexus_ui.html:1757) captures the function VALUE, so a later
    `window.sendMessage = wrapper` can never fire from the Send button. The
    experiment was invalid before it was run, and no amount of reading would
    have shown that.

    This test removes the reconstruction and the operator. It serves the real
    `frontend/nexus_ui.html` from the real app, drives a real Chromium, sends
    a real message, and asks the DOM. `.prov-toggle` is either there or it is
    not, and no interpretation stands between the question and the answer.

SKIPPED BY DEFAULT, DELIBERATELY
    Playwright is not a project dependency and a browser launch is ~2s, which
    is the kind of cost Packet R removed 143 outbound calls to avoid. It runs
    when the tooling is present:

        pip install playwright        # chromium is already on this image

    A skip is visible in the suite summary. A test that silently always
    skipped would be worse than no test, so the skip reason names what is
    missing rather than saying "unavailable".
"""
from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is not installed — `pip install playwright` to run the "
           "real-browser provenance sentinel")

from backend.kb.granularity import Granularity          # noqa: E402
from backend.server import create_app, create_services  # noqa: E402

TOKEN = "browser-provenance-token"
ANSWER = "The QTc change at 600mg was 11.4ms."


def _chromium_path() -> str | None:
    """Prefer the image's pinned build. Playwright's bundled version may not
    match the pip package's expected build number, and asking it to download
    one is forbidden on this image (PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1)."""
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    for candidate in sorted(root.glob("chromium-*/chrome-linux/chrome"),
                            reverse=True):
        return str(candidate)
    return None


class _Answering:
    """Streams a real answer. The turn must COMPLETE — `done` is the only
    frame that carries a lineage_id and the only place provenance attaches."""
    async def stream_chat(self, model, messages, num_ctx=8192):
        for chunk in ("The QTc change ", "at 600mg ", "was 11.4ms."):
            yield chunk

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server():
    import uvicorn
    os.environ["NEXUS_API_TOKEN"] = TOKEN
    tmp = Path(tempfile.mkdtemp())
    svc = create_services(
        kb_db_path=tmp / "kb.db", chroma_dir=tmp / "chroma",
        eval_log_path=tmp / "m.jsonl", archive_db_path=tmp / "a.db",
        snapshots_dir=tmp / "snap", provider=_Answering())
    svc.store.add(subject="mean placebo-corrected QTc change at 600mg",
                  relation="was", object="11.4ms", weight=0.8, tier=1,
                  granularity=Granularity.TOPIC, source="human_assertion")

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(
        create_app(svc), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "the test server never came up"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture(scope="module")
def page(live_server):
    from playwright.sync_api import sync_playwright
    exe = _chromium_path()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
        pg = browser.new_page()
        pg.errors = []                                   # type: ignore[attr-defined]
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.goto(live_server + "/", wait_until="load")
        yield pg
        browser.close()


def test_the_page_served_is_the_page_in_the_checkout(page):
    """NON-VACUITY, and the question four console rounds could not settle.

    If these markers are absent the browser is running some other build, and
    every experiment about the logic was measuring a page that does not
    contain the logic.
    """
    markers = page.evaluate("""() => {
      const h = document.documentElement.innerHTML;
      return {attachProvenance: h.includes('attachProvenance'),
              provToggleCss:    h.includes('prov-toggle'),
              frameGuard:       h.includes('ONE BAD FRAME MUST NOT COST THE TURN')};
    }""")
    assert all(markers.values()), f"the served page is not the checkout: {markers}"


def test_handleframe_is_a_global_but_sendmessage_is_captured(page):
    """THE TRAP FROM THE LAST ROUND, pinned so nobody re-runs that experiment.

    `handleFrame` is a top-level declaration in a classic script, so it IS a
    global and `window.handleFrame = wrapper` IS visible at the call site —
    which is why silence there means the page is wrong, not the wrapper.

    `send.onclick = sendMessage` captured the value at load, so a wrapper on
    `window.sendMessage` can NEVER fire from the Send button. That is not a
    defect; it is why the null result was uninformative.
    """
    facts = page.evaluate("""() => ({
      handleFrame: typeof window.handleFrame,
      buttonHoldsTheOriginal: (() => {
        const b = document.querySelector('#send');
        const original = window.sendMessage;
        window.sendMessage = function () { return 'wrapper'; };
        const stale = b.onclick === original && b.onclick !== window.sendMessage;
        window.sendMessage = original;
        return stale;
      })(),
    })""")
    assert facts["handleFrame"] == "function", (
        "handleFrame is not a global — a console wrapper on it could never "
        "have fired, and the last round's null result means nothing")
    assert facts["buttonHoldsTheOriginal"] is True, (
        "the Send button no longer holds a captured reference; if that is a "
        "deliberate change, the note in this file's docstring is now wrong")


def test_a_completed_turn_attaches_the_provenance_expander(page):
    """THE ANSWER. Real page, real server, real browser, real turn."""
    page.fill("#msg-input", "what was the QTc change at 600mg?")
    page.click("#send")
    page.wait_for_selector("#chat-log .prov-toggle", timeout=15_000)

    text = page.evaluate("() => document.querySelector('#chat-log').innerText")
    assert "11.4ms" in text, (
        f"the turn did not complete — nothing to attach provenance to: {text!r}")
    assert page.evaluate("() => document.querySelectorAll('.prov-toggle').length") == 1
    assert page.errors == [], f"the page threw during the turn: {page.errors}"


def test_the_expander_survives_the_next_turn(page):
    """The reported symptom was that it disappears for ALL SUBSEQUENT turns
    after one failure, so one turn proving it works is not the property that
    was in doubt."""
    page.fill("#msg-input", "and at 300mg?")
    page.click("#send")
    page.wait_for_function(
        "() => document.querySelectorAll('#chat-log .prov-toggle').length === 2",
        timeout=15_000)
    assert page.errors == [], f"the page threw on the second turn: {page.errors}"
