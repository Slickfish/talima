"""One throwing frame handler takes the whole turn down with it.

THE SYMPTOM
    The provenance expander disappeared for every turn after a web search
    failed. The expander is attached in exactly one place — `case 'done'` in
    `handleFrame`, which calls `attachProvenance(body, data.lineage_id)`.

THE FRONTEND MECHANISM, EXECUTED RATHER THAN READ
    `nexus_ui.html`'s read loop calls `handleFrame(frame, body)` with NO
    try/catch. A throw exits both loops and rejects `sendMessage`, so:

      * every remaining frame of that turn is dropped, `done` among them —
        no expander for that turn;
      * `send.disabled = false` at the end of `sendMessage` never runs, and
        the button stays disabled. `sendMessage` has no `disabled` guard, so
        Enter still sends: the operator can keep talking to a UI that has
        quietly stopped finishing turns.

    Six handlers throw on a plausible-but-wrong payload — measured by running
    the real `handleFrame` under node against each one, not by reading it:

        model_swap_starting   data.to.split          undefined `to`
        triples_injected      data.forEach           the {count, concepts}
                                                     shape CLAUDE.md declared
                                                     until W1
        graph_activating      data.forEach           non-array
        prediction_generated  expected_nodes.join    field absent
        prediction_error      high_surprise.join     field absent
        triples_extracted     data.forEach           non-array

    A seventh — `chain_appended` — threw in the first draft of the probe and
    is NOT a defect: `refreshChainStatus` exists at `nexus_ui.html:1195` and
    the failure was the stub's, not the page's. Recorded because a probe
    artefact reported as a finding is the same error as a measurement
    measuring itself, and this file is where it would have been believed.

WHAT THIS DOES *NOT* EXPLAIN, AND THE BACKEND PATH THAT DOES
    The frontend mechanism costs ONE turn. The alert queue drains
    (`agentic_loop.py:336`, `pop(0)` until empty), so nothing replays.

    `agentic_loop.py:783-805` is the path that survives restarts of the
    symptom: `lineage.record_turn(...)` is wrapped in `except Exception`,
    logs a WARNING, and leaves `lineage_id = None`. `done` then fires
    normally carrying `lineage_id: null`, and `attachProvenance` returns
    early on a falsy id. **The stream completes, nothing errors on screen,
    and the expander is simply absent** — for as long as whatever broke
    `record_turn` stays broken.

    So the two candidate causes are distinguishable and need different
    fixes, which is why both are pinned here:

        record NOT written  -> `lineage_records` stops growing; the server log
                               carries "lineage record failed: ..."
        record written, not shown -> rows keep arriving and the expander is
                               still missing, i.e. the frame handler died
"""
from __future__ import annotations

import json
import re
import shutil
from tests import _proc
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend" / "nexus_ui.html"
NODE = shutil.which("node") or "/opt/node22/bin/node"

pytestmark = pytest.mark.skipif(not Path(NODE).exists(),
                                reason="node not available")

# The smallest DOM that lets the real `handleFrame` run. `attachProvenance`
# is instrumented rather than stubbed away — it is the thing under test.
STUB = r"""
function mkEl(cls) {
  const e = { textContent: '', innerHTML: '', className: cls || '', dataset: {},
    style: {}, children: [], parentElement: null,
    classList: { add(){}, remove(){}, contains(){return false;} },
    appendChild(c){ c.parentElement = this; this.children.push(c); return c; },
    // Real enough for attachProvenance's "already attached?" check, which is
    // what stops a second expander appearing on the same message.
    querySelector(sel){ const want = String(sel).replace(/^\./, '');
      return this.children.find(c => c.className === want) || null; },
    remove(){}, addEventListener(){}, scrollTop: 0, scrollHeight: 0 };
  return e;
}
// A message body inside its wrapper, as appendMsg builds it.
function mkBody() {
  const wrap = mkEl('msg'); const body = mkEl('body');
  wrap.appendChild(body); globalThis.__wrap = wrap; return body;
}
const $ = () => mkEl();
const el = (t, c, x) => { const e = mkEl(); e.className = c||''; e.textContent = x||''; return e; };
const fmt = v => String(v);
const log = mkEl();
let _domainFilter = null;
function pulseNode(){}
function refreshChainStatus(){}          // exists at nexus_ui.html:1195
function renderSearchProposal(){}
function renderAssertionProposal(){}
function showKBAlert(){}
function updateSystemStats(){}
function renderKBList(){}
globalThis.__attached = [];
// `attachProvenance` is the REAL function, extracted from the page — see
// _handle_frame_source. A stub would have to reproduce its `if (!msgBody ||
// !lineageId) return` guard, and a stub that reproduces the logic under test
// is not evidence about it. The observation is on the DOM instead: the
// expander exists when a `.prov-toggle` was appended to the message wrapper.
function attached() { return globalThis.__wrap.children
    .filter(c => c.className === 'prov-toggle').length; }
"""

# Each is a shape the system can actually produce — a field the server omits
# on some path, or the payload CLAUDE.md documented (until W1 aligned the
# declarations to emission) rather than the one the backend emits. Not
# fuzzing.
THROWING_FRAMES = {
    "model_swap_starting":  {"capability": "code"},
    "triples_injected":     {"count": 3, "concepts": ["a"]},
    "graph_activating":     {"nodes": []},
    "prediction_generated": {"nodes": ["a"]},
    "prediction_error":     {"magnitude": 0.5},
    "triples_extracted":    {"count": 2},
}


def _page_source() -> str:
    """The real `handleFrame`, the real `attachProvenance`, and the real
    DISPATCH — the guarded call site out of `sendMessage`'s read loop.

    The dispatch matters as much as the other two. The resilience fix lives in
    the CALLER, not in `handleFrame`, so a sentinel that called `handleFrame`
    directly would exercise the unguarded path forever and report the fix as
    absent. The first version of this file did exactly that.

    Reproducing the try/catch in the fixture instead would be the W.2 defect-C
    shape one layer out: a harness agreeing with itself about code nobody
    runs. So the block is lifted from the page verbatim and wrapped in a
    function that takes the two names it closes over.
    """
    body = FRONTEND.read_text(encoding="utf-8")
    out = []
    for name in ("handleFrame", "attachProvenance"):
        m = re.search(r"^function " + name + r"\(.*?^\}", body, re.S | re.M)
        assert m, f"could not extract {name} from nexus_ui.html"
        out.append(m.group(0))

    # Anchored on the catch, not on the first closing brace — a non-greedy
    # `.*?` stops at the try block's own `}` and silently extracts a dispatch
    # with no guard in it, which would then "pass" the sentinels for the wrong
    # reason. The assertion below is what caught that.
    m = re.search(
        r"( *try \{\s*\n\s*handleFrame\(frame, body\);\s*\n\s*\} catch "
        r"\(err\) \{.*?\n( *)\})", body, re.S)
    assert m, ("could not extract the guarded dispatch from sendMessage's read "
               "loop — if the loop moved, this sentinel is testing nothing")
    dispatch = m.group(1)
    assert "catch" in dispatch, "the extracted dispatch has no catch"
    out.append("function dispatch(frame, body) {\n" + dispatch + "\n}")
    return "\n".join(out)


def _node(js: str) -> tuple[int, str, str]:
    proc = _proc.run([NODE, "-e", STUB + "\n" + _page_source() + "\n" + js],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _frame(ev: str, data: dict) -> str:
    return f"event: {ev}\ndata: {json.dumps(data)}"


# ---------------------------------------------------------------------------
# NON-VACUITY — the failure must really happen
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("event", sorted(THROWING_FRAMES))
def test_the_handler_really_throws_on_this_payload(event):
    """Without this, the sentinel below passes on a stream that never errored.

    Each case is asserted to throw TODAY. If one stops throwing — because the
    handler grew a guard — this fails and the sentinel's fixture must be
    re-derived rather than silently weakened.
    """
    code, _out, err = _node(
        f"handleFrame({json.dumps(_frame(event, THROWING_FRAMES[event]))}, mkBody());")
    assert code != 0, (
        f"{event} no longer throws on {THROWING_FRAMES[event]} — good, but the "
        f"resilience sentinel now proves nothing with it. Pick a payload that "
        f"still fails, or drop the case.")
    assert "Error" in err


def test_chain_appended_does_not_throw_and_never_did():
    """A probe artefact, pinned so it is not rediscovered as a defect.

    The first draft of the discovery probe omitted `refreshChainStatus` from
    its stub and reported `chain_appended` as a seventh throwing handler. The
    function exists in the page. The stub was wrong, not the page.
    """
    assert "function refreshChainStatus" in FRONTEND.read_text(encoding="utf-8")
    code, _out, err = _node(
        f"handleFrame({json.dumps(_frame('chain_appended', {}))}, mkBody());")
    assert code == 0, f"chain_appended threw: {err}"


# ---------------------------------------------------------------------------
# THE SENTINEL
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("event", sorted(THROWING_FRAMES))
def test_a_turn_that_fails_mid_stream_still_renders_provenance(event):
    """THE SENTINEL. One bad frame must not cost the turn its provenance."""
    frames = [
        _frame("turn_start", {"turn": 7}),
        _frame(event, THROWING_FRAMES[event]),          # the failure
        _frame("token", {"t": "hello"}),
        _frame("done", {"turn": 7, "tokens": 12, "lineage_id": "L-THIS-TURN"}),
    ]
    code, out, err = _node(
        "const body = mkBody();\n"
        f"for (const f of {json.dumps(frames)}) dispatch(f, body);\n"
        "console.log(attached());\n")
    assert code == 0, f"the stream died on a single bad frame:\n{err[-600:]}"
    assert out == "1", (
        f"`done` was never processed after {event} threw, so the answer on "
        f"screen has no link to the record that produced it")


def test_the_next_turn_renders_provenance_too():
    """The operator's actual complaint: not one turn, every turn after.

    Two turns through one loop. The first fails mid-stream; the second is
    clean. Both must attach.
    """
    frames = [
        _frame("turn_start", {"turn": 1}),
        _frame("prediction_error", {"magnitude": 0.5}),        # the failure
        _frame("done", {"turn": 1, "tokens": 5, "lineage_id": "L-TURN-1"}),
        _frame("turn_start", {"turn": 2}),
        _frame("token", {"t": "fine"}),
        _frame("done", {"turn": 2, "tokens": 6, "lineage_id": "L-TURN-2"}),
    ]
    code, out, err = _node(
        "let body = mkBody();\n"
        f"const fr = {json.dumps(frames)};\n"
        "for (const f of fr.slice(0, 3)) dispatch(f, body);\n"
        "const first = attached();\n"
        "body = mkBody();          // a fresh message, as the next turn builds\n"
        "for (const f of fr.slice(3)) dispatch(f, body);\n"
        "console.log(first + ',' + attached());\n")
    assert code == 0, f"stream died:\n{err[-600:]}"
    assert out == "1,1", (
        "the second turn lost its expander too — the operator's actual "
        "complaint, not a one-turn glitch")


# ---------------------------------------------------------------------------
# the other half — a null lineage_id is silent, and must not be
# ---------------------------------------------------------------------------

def test_a_null_lineage_id_attaches_nothing_and_says_nothing():
    """The BACKEND path, pinned as a distinct failure from the one above.

    `agentic_loop.py` catches a `record_turn` failure, logs a warning and
    leaves `lineage_id = None`. `done` then arrives intact, the stream
    completes, and the expander is silently absent. Currently indistinguishable
    on screen from "this build has no provenance surface".

    Asserted as CURRENT BEHAVIOUR, not as desired behaviour: a fix would make
    the absence visible, and this test is what such a fix has to change.
    """
    code, out, err = _node(
        "const body = mkBody();\n"
        f"handleFrame({json.dumps(_frame('done', {'turn': 3, 'tokens': 9, 'lineage_id': None}))}, body);\n"
        "console.log(attached());\n")
    assert code == 0, err
    assert out == "0", (
        "attachProvenance now runs on a null id — if that is deliberate the "
        "silent-absence finding needs re-deriving")


def test_the_loop_guards_handleFrame_and_says_so():
    """The mechanism itself, so a fix elsewhere cannot look like a fix here.

    Reads the call site — the one place grep is the right tool, because the
    claim is about the presence of syntax. The `console.error` matters as much
    as the `catch`: a silent catch would hide a payload contract breaking
    rather than fix it, which is why the six throwing handlers are
    deliberately left unguarded.
    """
    body = FRONTEND.read_text(encoding="utf-8")
    m = re.search(r"const frame = buf\.slice\(0, idx\);.*?\n      \}", body, re.S)
    assert m, "the read loop moved — re-derive this guard"
    block = m.group(0)
    assert "handleFrame(frame, body)" in block
    assert "try {" in block and "catch" in block, (
        "handleFrame is unguarded again — one bad frame costs the turn its "
        "`done`, and `done` is the only place provenance is attached")
    assert "console.error" in block, (
        "the catch is silent. A swallowed handler exception hides a contract "
        "break; it must be named in the log with its event.")


def test_send_cannot_be_bypassed_while_disabled():
    """The other half of the wedge: `send.onclick` respects `disabled`, the
    Enter binding calls `sendMessage` directly and did not. A turn that died
    mid-stream left the button disabled while Enter kept sending."""
    body = FRONTEND.read_text(encoding="utf-8")
    m = re.search(r"async function sendMessage\(\) \{.*?\n  send\.disabled = true;",
                  body, re.S)
    assert m, "sendMessage's prologue moved — re-derive this guard"
    assert "if (send.disabled) return;" in m.group(0), (
        "Enter can still send while the button is disabled")
    assert "} finally {" in body and "send.disabled = false; input.focus();" in body, (
        "the button is not re-enabled in a finally — a network drop would now "
        "be a hard lockout rather than a silent bypass")
