"""Packet N.1 — the graph panel's performance policy, executed not grepped.

THE DEFECT, MEASURED (N.0, headless Chromium over CDP against these exact
functions):

    render() ended with an UNCONDITIONAL `simulation.alpha(0.6).restart()`,
    and loadSnapshot() runs at the end of every turn. alpha decays per TICK,
    not per second, so settle is always 424 ticks — 7.1s at 100 nodes, 22.0s
    at 800/4485. With turns arriving every 3s the simulation never reached
    alphaMin at all:

        turn 1 alpha 0.154 | 2 0.104 | 3 0.091 | 4 0.087 | 5 0.089 | 6 0.091
        settled: never. 1009 ticks over 18s.

    That is the operator's "several chat iterations render the UI unusable".

WHY THESE TESTS RUN UNDER NODE AND NOT A BROWSER
    d3-force is pure maths: it runs under plain node with no DOM, ticks on
    its own timer, and fires `end`. So the settle-and-freeze behaviour — the
    actual fix — is asserted by RUNNING THE REAL d3 with the REAL policy
    values, on any machine with node. No browser, nothing to skip.

    The DOM-dependent halves (render/tick/loadSnapshot) are not executed here.
    They were executed in Chromium during N.0 and the numbers are quoted
    above; what this file pins is the policy that decides how often they run
    and over how much data, which is where the entire cost lived.

    Extraction is by name from the shipping file — the pattern
    test_provenance_surface.py established. Asserting a string appears in an
    HTML file is level-3 evidence and is what let four defects ship (§15.4).
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
D3 = ROOT / "frontend" / "vendor" / "d3.v7.min.js"
NODE = shutil.which("node") or "/opt/node22/bin/node"

# Pure, top-level, DOM-free. Everything the panel's cost and focus policy
# depends on. N.1b added the last four.
PURE_FUNCTIONS = ("nodeImportance", "capGraph", "labelledIds", "hashString",
                  "topologySignature", "graphMetaText",
                  "labelLimitForZoom", "focusOpacity", "edgeOpacity",
                  "neighboursOf")

# Constants the extracted functions close over. Emitted from the PAGE, never
# re-declared here: a duplicated threshold lets the file and the test drift
# apart while both keep passing (H.4's hebbian/hebbian_update).
PURE_CONSTANTS = ("GHOST_FLOOR", "REWARM_ALPHA", "ALPHA_DECAY",
                  "DRAG_RELEASE_ALPHA")


def _extract(names=PURE_FUNCTIONS) -> str:
    body = FRONTEND.read_text(encoding="utf-8")
    out = []
    for const in PURE_CONSTANTS:
        m = re.search(rf"^const {const}\s*=\s*[^;]+;", body, re.M)
        assert m, f"could not extract const {const} from nexus_ui.html"
        out.append(m.group(0))
    m = re.search(r"^const GRAPH_CAPS = \{[^}]*\};", body, re.M)
    assert m, "could not extract GRAPH_CAPS from nexus_ui.html"
    out.append(m.group(0))
    for name in names:
        m = re.search(r"^function " + name + r"\(.*?^\}", body, re.S | re.M)
        assert m, f"could not extract {name}() from nexus_ui.html"
        out.append(m.group(0))
    return "\n".join(out)


def _constants() -> dict:
    """The tuned numbers, read from the page rather than duplicated here.

    Duplicating them would let the file and the test drift apart and the test
    would still pass — the `hebbian`/`hebbian_update` failure (H.4).
    """
    body = FRONTEND.read_text(encoding="utf-8")
    caps = re.search(r"const GRAPH_CAPS = (\{[^}]*\});", body)
    assert caps, "GRAPH_CAPS not found"
    out = {"GRAPH_CAPS": json.loads(
        caps.group(1).replace("nodes:", '"nodes":')
                     .replace("edges:", '"edges":')
                     .replace("labels:", '"labels":'))}
    for name in ("REWARM_ALPHA", "ALPHA_DECAY"):
        m = re.search(rf"const {name}\s*=\s*([0-9.]+)", body)
        assert m, f"{name} not found"
        out[name] = float(m.group(1))
    return out


def _run_js(snippet: str, *, with_d3: bool = False) -> str:
    prelude = f"const d3 = require({str(D3)!r});\n" if with_d3 else ""
    proc = _proc.run(
        [NODE, "-e", prelude + _extract() + "\n" + snippet],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _js_json(snippet: str, *, with_d3: bool = False):
    return json.loads(_run_js(snippet, with_d3=with_d3).splitlines()[-1])


needs_node = pytest.mark.skipif(not Path(NODE).exists(),
                                reason="node not available")

SYNTH = """
function synth(n, e) {
  const ns = [], es = [];
  for (let i = 0; i < n; i++)
    ns.push({id: 'concept_' + i, fire_count: i % 9, activation: (i % 5) / 5,
             evidential_weight: 0.5, epistemic_state: 'known'});
  for (let i = 0; i < e; i++)
    es.push({source: 'concept_' + (i % n), target: 'concept_' + ((i * 7 + 3) % n),
             weight: (i % 10) / 10, current_weight: (i % 7) / 7});
  return {nodes: ns, edges: es};
}
"""


# ---------------------------------------------------------------------------
# Caps — the only lever that reduces per-frame DOM cost
# ---------------------------------------------------------------------------

@needs_node
def test_caps_bound_the_rendered_population():
    c = _constants()["GRAPH_CAPS"]
    got = _js_json(SYNTH + f"""
        const g = synth(800, 4485);
        const r = capGraph(g.nodes, g.edges, {json.dumps(c)});
        console.log(JSON.stringify({{n: r.nodes.length, e: r.links.length,
            droppedN: r.droppedNodes, droppedE: r.droppedEdges}}));
    """)
    assert got["n"] == c["nodes"], "node cap not enforced"
    # The edge cap is a CEILING, not a quota. Measured here: capping 800
    # nodes to 150 leaves 222 of 4485 edges, because an edge survives only if
    # BOTH endpoints did. Asserting equality would be asserting that the
    # graph is dense enough to fill the budget, which is a property of the
    # data, not of the code.
    assert 0 < got["e"] <= c["edges"], "edge cap not enforced"
    # Non-vacuity: the input must actually exceed the caps, or this proves
    # only that a small graph stays small.
    assert got["droppedN"] == 800 - c["nodes"] > 0
    assert got["droppedE"] > 0


@needs_node
def test_a_graph_under_the_cap_is_untouched():
    """The caps must not perturb the common case. Most sessions are small."""
    c = _constants()["GRAPH_CAPS"]
    got = _js_json(SYNTH + f"""
        const g = synth(40, 60);
        const r = capGraph(g.nodes, g.edges, {json.dumps(c)});
        console.log(JSON.stringify({{n: r.nodes.length, e: r.links.length,
            dn: r.droppedNodes, de: r.droppedEdges}}));
    """)
    assert got == {"n": 40, "e": 60, "dn": 0, "de": 0}


@needs_node
def test_no_edge_survives_whose_endpoint_was_dropped():
    """Not a tidiness point — d3's forceLink throws on an unresolvable id, so
    capping edges independently of nodes would break the panel outright."""
    c = _constants()["GRAPH_CAPS"]
    got = _js_json(SYNTH + f"""
        const g = synth(800, 4485);
        const r = capGraph(g.nodes, g.edges, {json.dumps(c)});
        const kept = new Set(r.nodes.map(n => n.id));
        const orphans = r.links.filter(e => !kept.has(e.source) || !kept.has(e.target));
        console.log(JSON.stringify({{orphans: orphans.length, links: r.links.length}}));
    """)
    assert got["orphans"] == 0, "an edge referenced a dropped node"
    assert got["links"] > 0, "non-vacuity: no edges survived at all"


@needs_node
def test_the_cap_keeps_the_most_important_nodes():
    """A cap that dropped the interesting half would be worse than no cap."""
    got = _js_json(SYNTH + """
        const g = synth(300, 500);
        const r = capGraph(g.nodes, g.edges, {nodes: 10, edges: 10, labels: 5});
        const keptMin = Math.min(...r.nodes.map(nodeImportance));
        const dropped = g.nodes.filter(n => !r.nodes.includes(n));
        const droppedMax = Math.max(...dropped.map(nodeImportance));
        console.log(JSON.stringify({keptMin, droppedMax}));
    """)
    assert got["keptMin"] >= got["droppedMax"], (
        "a dropped node outranked a kept one")


@needs_node
def test_labels_are_culled_below_the_node_cap():
    c = _constants()["GRAPH_CAPS"]
    got = _js_json(SYNTH + f"""
        const g = synth(800, 100);
        const r = capGraph(g.nodes, g.edges, {json.dumps(c)});
        const labels = labelledIds(r.nodes, {c["labels"]});
        console.log(JSON.stringify({{labels: labels.size, nodes: r.nodes.length}}));
    """)
    assert got["labels"] == c["labels"]
    assert got["labels"] < got["nodes"], (
        "labels are not culled relative to nodes, so ~1/3 of the per-frame "
        "DOM writes measured in N.0 are still there")


# ---------------------------------------------------------------------------
# The conditional restart — the defect itself
# ---------------------------------------------------------------------------

@needs_node
def test_an_unchanged_graph_produces_an_identical_signature():
    """The property the whole fix rests on. d3 resolves link source/target
    from ids to node objects IN PLACE, so a signature that did not normalise
    that would differ on every turn and the restart would stay unconditional
    while looking conditional."""
    got = _js_json(SYNTH + """
        const g = synth(60, 90);
        const before = topologySignature(g.nodes, g.edges);
        // simulate d3's in-place resolution
        const byId = new Map(g.nodes.map(n => [n.id, n]));
        g.edges.forEach(e => { e.source = byId.get(e.source); e.target = byId.get(e.target); });
        const after = topologySignature(g.nodes, g.edges);
        console.log(JSON.stringify({before, after, same: before === after}));
    """)
    assert got["same"], (
        f"the signature changed when d3 resolved link endpoints: "
        f"{got['before']} vs {got['after']}")


@needs_node
@pytest.mark.parametrize("mutation,should_differ", [
    ("g2.nodes.push({id: 'newcomer', fire_count: 1})", True),
    ("g2.edges.push({source: 'concept_1', target: 'concept_2'})", True),
    ("g2.nodes.pop()", True),
    ("g2.nodes[0].fire_count = 99", False),
    ("g2.nodes[0].activation = 0.9", False),
    ("g2.edges[0].current_weight = 0.01", False),
], ids=["node added", "edge added", "node removed",
        "fire_count changed", "activation changed", "weight changed"])
def test_the_signature_tracks_topology_and_nothing_else(mutation, should_differ):
    """Re-warm on a new node — that is a real layout change. Do NOT re-warm
    because a weight moved: nothing needs to be re-positioned, and re-warming
    on it would restore the unconditional restart under another name."""
    got = _js_json(SYNTH + f"""
        const g1 = synth(50, 80);
        const g2 = synth(50, 80);
        {mutation};
        console.log(JSON.stringify({{
            differ: topologySignature(g1.nodes, g1.edges)
                 !== topologySignature(g2.nodes, g2.edges)}}));
    """)
    assert got["differ"] is should_differ


# ---------------------------------------------------------------------------
# Settle and freeze — the real d3, the real constants, no DOM
# ---------------------------------------------------------------------------

@needs_node
def test_the_simulation_settles_and_fires_end():
    """Executed, not asserted about: a real forceSimulation with the page's
    own ALPHA_DECAY, run to completion."""
    k = _constants()
    got = _js_json(f"""
        const sim = d3.forceSimulation(
            Array.from({{length: 60}}, (_, i) => ({{id: 'n' + i}})))
          .force('charge', d3.forceManyBody().strength(-120))
          .force('collide', d3.forceCollide().radius(14))
          .alphaDecay({k["ALPHA_DECAY"]}).velocityDecay(0.25).stop();
        let ticks = 0, ended = false;
        sim.on('tick', () => ticks++).on('end', () => {{ ended = true; }});
        sim.alpha({k["REWARM_ALPHA"]}).restart();
        setTimeout(() => console.log(JSON.stringify({{
            ticks, ended, alpha: sim.alpha(), alphaMin: sim.alphaMin(),
            running: sim.alpha() > sim.alphaMin()}})), 6000);
    """, with_d3=True)

    assert got["ended"] is True, "the simulation never fired 'end'"
    assert got["running"] is False, "the simulation is still moving"
    assert got["alpha"] <= got["alphaMin"]


@needs_node
def test_settle_takes_far_fewer_ticks_than_before():
    """The before/after, computed from the alpha curve rather than a clock —
    wall-clock frame time is flaky and a threshold on it would be muted (the
    C10 lesson). Ticks are exact.

        before: alpha 0.6,  decay 0.015  ->  424 ticks, every turn
        after:  alpha 0.15, decay 0.05   ->   98 ticks, only when changed
    """
    k = _constants()
    got = _js_json(f"""
        const ticksFor = (a0, decay) =>
            Math.ceil(Math.log(0.001 / a0) / Math.log(1 - decay));
        console.log(JSON.stringify({{
            before: ticksFor(0.6, 0.015),
            after:  ticksFor({k["REWARM_ALPHA"]}, {k["ALPHA_DECAY"]})}}));
    """)
    assert got["before"] == 424, "the historical baseline changed"
    assert got["after"] <= 150, f"re-warm still takes {got['after']} ticks"
    assert got["after"] < got["before"] / 3


@needs_node
def test_repeated_identical_turns_rewarm_exactly_once():
    """THE REGRESSION TEST. Six turns with an unchanged graph, driving the
    real decision the page makes. Before N.1 this was six re-warms of 424
    ticks each and the simulation never settled.
    """
    k = _constants()
    got = _js_json(SYNTH + f"""
        const g = synth(80, 120);
        let lastTopology = null, rewarms = 0;
        for (let turn = 0; turn < 6; turn++) {{
            const capped = capGraph(g.nodes, g.edges, {json.dumps(k["GRAPH_CAPS"])});
            const sig = topologySignature(capped.nodes, capped.links);
            if (sig !== lastTopology) {{ lastTopology = sig; rewarms++; }}
        }}
        console.log(JSON.stringify({{rewarms}}));
    """)
    assert got["rewarms"] == 1, (
        f"{got['rewarms']} re-warms across six identical turns — the "
        f"unconditional restart is back")


@needs_node
def test_a_changed_graph_still_rewarms():
    """Non-vacuity for the above. A guard that never re-warms would pass the
    previous test and leave new nodes stacked at the origin."""
    k = _constants()
    got = _js_json(SYNTH + f"""
        const g = synth(80, 120);
        let lastTopology = null, rewarms = 0;
        for (let turn = 0; turn < 6; turn++) {{
            g.nodes.push({{id: 'arrival_' + turn, fire_count: 5}});
            const capped = capGraph(g.nodes, g.edges, {json.dumps(k["GRAPH_CAPS"])});
            const sig = topologySignature(capped.nodes, capped.links);
            if (sig !== lastTopology) {{ lastTopology = sig; rewarms++; }}
        }}
        console.log(JSON.stringify({{rewarms}}));
    """)
    assert got["rewarms"] == 6, "a growing graph stopped re-warming"


# ---------------------------------------------------------------------------
# I-6 — the cap is declared, never silent
# ---------------------------------------------------------------------------

@needs_node
def test_a_capped_graph_says_so_and_an_uncapped_one_does_not():
    k = _constants()
    got = _js_json(SYNTH + f"""
        const caps = {json.dumps(k["GRAPH_CAPS"])};
        const big = synth(800, 4485), small = synth(20, 30);
        const rb = capGraph(big.nodes, big.edges, caps);
        const rs = capGraph(small.nodes, small.edges, caps);
        console.log(JSON.stringify({{
            big: graphMetaText({{n_nodes: 800, n_edges: 4485}}, rb),
            small: graphMetaText({{n_nodes: 20, n_edges: 30}}, rs)}}));
    """)
    assert "of 800 nodes" in got["big"] and "capped" in got["big"], (
        f"the panel drew 150 of 800 nodes without saying so: {got['big']!r}")
    assert got["small"] == "20 nodes · 30 edges", (
        f"an uncapped graph gained noise: {got['small']!r}")


# ---------------------------------------------------------------------------
# Constraints that must survive this packet
# ---------------------------------------------------------------------------

def test_the_panel_loads_nothing_from_the_network():
    """P1 vendored the assets for air-gap sovereignty. Re-asserted here
    because this packet touches the file that loads them."""
    body = FRONTEND.read_text(encoding="utf-8")
    external = re.findall(r"""(?:src|href)\s*=\s*["'](https?://[^"']+)""", body)
    assert not external, f"external asset URL(s): {external}"


def test_labels_are_created_with_text_never_html():
    """SOURCE-LEVEL GUARD, and named as one rather than dressed up.

    The behavioural evidence is N.0's: `<script>window.__PWNED=1</script>` and
    `<img src=x onerror=...>` were fed as node ids through this exact render
    path in Chromium — `__PWNED` stayed null, zero script/img elements were
    created, and both strings appeared as literal textContent. `.text()`
    escapes by construction.

    That measurement needs a browser, which the operator's machine may not
    have, so it cannot live in this suite. What CAN live here is the guard
    that the escaping construct is not swapped for an unescaping one — which
    is the realistic regression, given pre-gate rows carrying markup are still
    in the store by design (I-9).
    """
    body = FRONTEND.read_text(encoding="utf-8")
    m = re.search(r"^function render\(\).*?^\}", body, re.S | re.M)
    assert m, "could not extract render() from nexus_ui.html"
    # Comments stripped first. The draft of this test failed on render()'s own
    # comment saying "NEVER use .html() or innerHTML here" — the identical
    # class M.5 ratcheted, where a regex flagged a docstring describing the
    # defect it was looking for. A guard that fires on a warning about itself
    # is the "cries wolf" failure, and it gets muted rather than fixed.
    render_src = re.sub(r"//[^\n]*", "", m.group(0))
    assert ".text(d => d.id)" in render_src, (
        "the label join no longer uses .text() — if it now builds markup, "
        "a stored belief containing <script> renders as an element")
    for unsafe in (".html(", "innerHTML", "insertAdjacentHTML"):
        assert unsafe not in render_src, (
            f"render() uses {unsafe}, which does not escape belief text")


# ---------------------------------------------------------------------------
# Packet N.1b — focus + context, zoom tiers, spring-back
# ---------------------------------------------------------------------------

@needs_node
def test_context_fades_to_a_floor_and_is_never_hidden():
    """The design's central constraint: continuous fade to a ghost floor,
    never hard hiding. A graph that hides its context becomes a list, and
    seeing what a belief sits NEXT to is the reason the panel exists.
    """
    got = _js_json("""
        const links = [{source: 'focus', target: 'near'}];
        const nb = neighboursOf('focus', links);
        console.log(JSON.stringify({
            focal:     focusOpacity('focus', 'focus', nb),
            neighbour: focusOpacity('near',  'focus', nb),
            distant:   focusOpacity('far',   'focus', nb),
            atRest:    focusOpacity('far',   null,    nb),
            floor:     GHOST_FLOOR}));
    """)
    assert got["distant"] == got["floor"] > 0, (
        "a non-focal node reached zero opacity — that is hiding, not fading")
    assert got["focal"] == 1
    assert got["floor"] < got["neighbour"] < got["focal"], (
        "the three tiers are not distinguishable, so focus conveys nothing")
    assert got["atRest"] == 1, "no focus must mean nothing is dimmed"


@needs_node
def test_an_edge_is_never_brighter_than_the_node_it_leads_to():
    got = _js_json("""
        const links = [{source: 'focus', target: 'near'}, {source: 'far', target: 'other'}];
        const nb = neighboursOf('focus', links);
        console.log(JSON.stringify({
            toNeighbour: edgeOpacity(links[0], 'focus', nb, 0.8),
            betweenFar:  edgeOpacity(links[1], 'focus', nb, 0.8),
            atRest:      edgeOpacity(links[1], null,    nb, 0.8),
            floor: GHOST_FLOOR}));
    """)
    assert got["betweenFar"] == pytest.approx(0.8 * got["floor"])
    assert got["betweenFar"] < got["toNeighbour"] < got["atRest"]
    assert got["atRest"] == pytest.approx(0.8), (
        "edge weight no longer drives opacity at rest — that is the "
        "current_weight() signal the panel is specified to carry")


@needs_node
def test_label_tiers_narrow_with_zoom_and_never_exceed_the_cap():
    c = _constants()["GRAPH_CAPS"]
    got = _js_json(f"""
        const caps = {json.dumps(c)};
        console.log(JSON.stringify({{
            out: labelLimitForZoom(0.3, caps),
            mid: labelLimitForZoom(1.0, caps),
            inn: labelLimitForZoom(4.0, caps),
            ceiling: caps.labels}}));
    """)
    assert got["out"] < got["mid"] < got["inn"], "the three tiers are not distinct"
    assert got["inn"] <= got["ceiling"], (
        "zooming in exceeded the N.1a label cap, restoring the per-frame "
        "DOM cost that cap exists to bound")


@needs_node
def test_the_spring_back_lands_inside_one_to_two_seconds():
    """The design said a slow 1-2s spring-back, not a snap. Delivered by the
    physics rather than a second animation clock, so it is asserted on the
    alpha curve — deterministic, unlike a wall-clock frame time (N.0.8)."""
    k = _constants()
    got = _js_json(f"""
        const ticks = Math.ceil(Math.log(0.001 / DRAG_RELEASE_ALPHA)
                              / Math.log(1 - ALPHA_DECAY));
        console.log(JSON.stringify({{ticks, seconds: ticks / 60,
                                     alpha: DRAG_RELEASE_ALPHA}}));
    """)
    assert 1.0 <= got["seconds"] <= 2.0, (
        f"spring-back settles in {got['seconds']:.2f}s, outside the 1-2s the "
        f"design specified")
    assert got["alpha"] < k["REWARM_ALPHA"] * 1.5, (
        "releasing a drag re-warms as hard as a topology change, which would "
        "re-lay-out the whole graph because one node moved")


def test_zoom_and_hover_never_wake_the_simulation():
    """THE INTERACTION BETWEEN THE TWO COMMITS, and the one most likely to be
    broken later.

    N.1a's whole fix is that the simulation settles and stays settled. Hover
    and zoom fire constantly while it is frozen, so if either called
    wakeSim()/restart() the panel would run continuously again — and it would
    look like a usability feature rather than the performance regression it
    is.

    Source-level, and named as such: these are DOM event handlers and cannot
    be executed without a browser. What is checked is that no wake path
    appears inside them.
    """
    body = FRONTEND.read_text(encoding="utf-8")
    for fn in ("applyFocus", "setFocus", "applyLabelTier"):
        m = re.search(r"^function " + fn + r"\(.*?^\}", body, re.S | re.M)
        assert m, f"could not extract {fn}() from nexus_ui.html"
        src = re.sub(r"//[^\n]*", "", m.group(0))
        for wake in ("wakeSim", ".restart()", "alphaTarget"):
            assert wake not in src, (
                f"{fn}() calls {wake} — hover or zoom would wake a frozen "
                f"simulation and N.1a's fix is undone by a mouse movement")

    m = re.search(r"const zoomBehaviour = d3\.zoom\(\).*?^svg\.call\(zoomBehaviour\);",
                  body, re.S | re.M)
    assert m, "could not extract the zoom behaviour"
    zoom_src = re.sub(r"//[^\n]*", "", m.group(0))
    for wake in ("wakeSim", ".restart()", "alphaTarget"):
        assert wake not in zoom_src, f"the zoom handler calls {wake}"


def test_the_reset_control_exists_and_is_bound():
    """An explicit reset was specified because a graph dragged into a knot has
    no other way back. A control nothing binds is the `rag_injected` defect —
    declared and unreachable (H.4)."""
    body = FRONTEND.read_text(encoding="utf-8")
    assert 'id="graph-reset"' in body, "the reset control is not in the markup"
    assert "$('#graph-reset').onclick" in body, "the reset control is not bound"
    m = re.search(r"^function resetGraphView\(\).*?^\}", body, re.S | re.M)
    assert m, "resetGraphView() is missing"
    src = m.group(0)
    assert "zoomIdentity" in src, "reset does not clear the view transform"
    assert "fx = null" in src, "reset does not release dragged nodes"
    assert "wakeSim" in src, "reset does not re-run the layout"


def test_zoom_transforms_one_group_not_every_node():
    """Zoom moves a single root <g>, so panning costs one attribute write per
    frame rather than re-projecting every node — which would reintroduce
    exactly the per-frame DOM cost N.0 measured at 22.89ms."""
    body = FRONTEND.read_text(encoding="utf-8")
    assert "const gRoot = svg.append('g').attr('class', 'viewport');" in body
    for child in ("gLinks", "gNodes", "gLabels"):
        assert f"const {child} = gRoot.append('g')" in body, (
            f"{child} is not inside the zoom viewport, so it would not pan")
