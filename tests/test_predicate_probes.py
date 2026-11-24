"""Packet X.0 — the two probes that decide whether X.1 happens.

WHY THEY ARE TWO SCRIPTS AND NOT ONE

    `predicate_distribution.py` answers the POPULATION question — how many
    predicates have more than one row, and therefore whether cluster-by-usage
    has anything to cluster. Pure SQL, no model.

    `predicate_similarity.py` answers the SEPARATION question — does any
    threshold divide the motivating pair from the antonym pairs. Needs a live
    embedder.

    Keeping them apart means the population answer survives with no daemon,
    and it keeps a check from sharing machinery with the thing it checks. That
    rule was earned twice in this series: the threshold every test overrode,
    and the retrieval harness that scored `expect` as a conjunction and
    reported the result as a fact about the store.

WHAT THIS FILE GUARDS, AND THE ERROR IT ALREADY CAUGHT
    The control arm is DERIVED from `CONTRADICTION_PAIRS`, never hand-written.
    That is not tidiness. The X.0 report claimed "token overlap separates the
    motivating pair from every antonym perfectly, jaccard 0.29 against 0.00" —
    measured against five antonym pairs picked by hand. Against the seven that
    actually ship, `is_a` / `is_not_a` scores **0.67**, above the motivating
    pair's 0.29, and the lexical baseline does not separate either. A negation
    formed by inserting a word is the case where surface overlap is highest
    and meaning is inverted, and hand-picking the control had hidden it.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROBES = ["predicate_distribution.py", "predicate_similarity.py",
          "vocab_effect.py"]

# The subset that must never reach an embedder. Their whole value is being
# evidence ABOUT the measurement rather than part of it — `audit_pairs.py`
# carries the same guard, and W.4 is why: the retrieval harness scored
# `expect` as a conjunction and reported the result as a fact about the store.
NO_MODEL_PROBES = ["predicate_distribution.py", "vocab_effect.py"]


def _load(name):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name[:-3]] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def dist():
    return _load("predicate_distribution.py")


@pytest.fixture(scope="module")
def sim():
    return _load("predicate_similarity.py")


@pytest.mark.parametrize("name", PROBES)
def test_the_probes_import(name):
    """U.0 — `ast.parse` accepts what `compile()` rejects, so importing is the
    check that catches both."""
    assert _load(name) is not None


# ---------------------------------------------------------------------------
# the population probe must stay independent of the measurement
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", NO_MODEL_PROBES)
def test_the_evidence_probes_have_no_embedder(name):
    """These are the evidence that would say "there is nothing to cluster" and
    "the vocabulary is not reaching production". If either reaches for the
    embedder it stops being independent of what it is used to judge —
    `audit_pairs.py` carries the same guard for the same reason."""
    tree = ast.parse((ROOT / "scripts" / name).read_text(encoding="utf-8"))
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for forbidden in ("get_embedding", "embed_via_provider", "get_provider",
                      "SemanticIndex", "cosine"):
        assert forbidden not in names, f"{name} reaches for {forbidden}"


def test_the_vocabulary_effect_probe_partitions_on_the_governed_source():
    """The whole point of the script in one assertion: the vocabulary governs
    `source='extraction'` and nothing else, so a count that blends seeded and
    asserted rows cannot indict it. If GOVERNED_SOURCE ever drifts from what
    `agentic_loop` writes, the partition is measuring the wrong population."""
    mod = _load("vocab_effect.py")
    assert mod.GOVERNED_SOURCE == "extraction"
    loop = ast.parse((ROOT / "backend" / "loop" / "agentic_loop.py")
                     .read_text(encoding="utf-8"))
    written = set()
    for node in ast.walk(loop):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add"):
            continue
        for kw in node.keywords:
            if kw.arg == "source" and isinstance(kw.value, ast.Constant):
                written.add(kw.value.value)
    assert mod.GOVERNED_SOURCE in written, (
        f"agentic_loop writes sources {sorted(written)} and the probe "
        f"partitions on {mod.GOVERNED_SOURCE!r} — they have drifted")


def test_the_boundary_is_a_declared_lower_bound():
    """V.1's commit timestamp is when the CODE landed, not when the running
    server restarted onto it. The script must say so where a reader will see
    it, because a boundary quietly treated as exact would attribute
    pre-vocabulary rows to the vocabulary."""
    src = (ROOT / "scripts" / "vocab_effect.py").read_text(encoding="utf-8")
    assert "LOWER BOUND" in src
    assert "5b21539" in src, "the boundary does not cite the commit it came from"
    assert "--since" in src and "--daily" in src, (
        "the script offers no way to locate or move the boundary")
    assert "decompose" in src, (
        "the decomposition confound is not named, and it is the one thing "
        "that could make a post-boundary rise look like the prompt working")


def test_the_distribution_probe_gates_on_the_population(dist, tmp_path):
    """Exit 1 means "nothing to cluster" — a caller can branch on it rather
    than reading a paragraph."""
    rows = [{"subject": f"s{i}", "relation": "seen_once_%d" % i,
             "object": "o", "source": "extraction"} for i in range(5)]
    assert dist.report(rows, top=5) == 1
    rows = [{"subject": f"s{i}", "relation": f"rel{i % 25}",
             "object": "o", "source": "extraction"} for i in range(60)]
    assert dist.report(rows, top=5) == 0


def test_the_distribution_probe_excludes_substrate_relations(dist, capsys):
    """`co_activated` is two thirds of the operator's store and is not
    extraction output. Counting it would put the mean rows-per-predicate an
    order of magnitude out."""
    rows = [{"subject": "a", "relation": "co_activated", "object": "b",
             "source": "consolidation"} for _ in range(50)]
    rows.append({"subject": "x", "relation": "binds_to", "object": "y",
                 "source": "extraction"})
    dist.report(rows, top=5)
    out = capsys.readouterr().out
    assert "claim rows                 : 1" in out
    assert "substrate rows excluded    : 50" in out


# ---------------------------------------------------------------------------
# the control arm
# ---------------------------------------------------------------------------

def test_the_antonym_control_is_derived_not_hand_written(sim):
    """The guard that caught the X.0 report's own error."""
    from backend.cortex.contradiction import CONTRADICTION_PAIRS
    assert tuple(sim.ANTONYM) == tuple(CONTRADICTION_PAIRS)
    assert len(sim.ANTONYM) >= 7


def test_the_lexical_baseline_does_not_separate_either(sim):
    """Recorded because the X.0 report claimed it did.

    Six of the seven shipped antonym pairs score 0.00 on token overlap, and
    the seventh — a negation — scores 0.67, above the motivating pair. If this
    ever starts passing as "separated", the pair set has changed and the
    report's correction needs re-deriving rather than quietly inheriting.
    """
    target_min = min(sim.jaccard(a, b) for a, b in sim.TARGET)
    antonym_max = max(sim.jaccard(a, b) for a, b in sim.ANTONYM)
    assert antonym_max >= target_min, (
        "token overlap now separates target from antonym — re-derive X.0.4")
    assert sim.jaccard("is_a", "is_not_a") > sim.jaccard(
        "primarily_metabolized_by", "is_metabolized_by_primary_enzyme_of")


def test_jaccard_is_the_crudest_thing_that_could_work(sim):
    assert sim.jaccard("causes", "prevents") == 0.0
    assert sim.jaccard("metabolized_by", "metabolized_by") == 1.0
    assert 0.0 < sim.jaccard("metabolized_by", "is_metabolized_by") < 1.0


def test_anchors_come_from_the_live_vocabulary(sim, monkeypatch):
    """Map-to-known maps ONTO these. Hand-writing them would let the anchor
    set drift from the vocabulary the extractor is actually offered."""
    from backend.cortex.contradiction import CONTRADICTION_PAIRS
    from backend.kb.vocabulary import load_relations
    anc = set(sim.anchors())
    assert set(load_relations()) <= anc
    assert {r for pair in CONTRADICTION_PAIRS for r in pair} <= anc


# ---------------------------------------------------------------------------
# measurement discipline
# ---------------------------------------------------------------------------

def test_the_similarity_probe_embeds_strictly():
    """W.3 — a measurement may not fall back. A similarity table built on hash
    vectors separates nothing and looks exactly like one that did."""
    tree = ast.parse((ROOT / "scripts" / "predicate_similarity.py")
                     .read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", getattr(n.func, "attr", ""))
             == "embed_via_provider"]
    assert calls, "the probe no longer embeds — re-derive this guard"
    for call in calls:
        strict = next((kw.value for kw in call.keywords if kw.arg == "strict"),
                      None)
        assert isinstance(strict, ast.Constant) and strict.value is True, (
            f"predicate_similarity.py:{call.lineno} embeds without strict=True")


def test_the_similarity_probe_names_its_failure_and_exits_5():
    src = (ROOT / "scripts" / "predicate_similarity.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)
                and n.type is not None
                and "EmbeddingUnavailable" in ast.dump(n.type)]
    assert handlers, "EmbeddingUnavailable is not caught"
    returns = [n.value.value for h in handlers for n in ast.walk(h)
               if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)]
    assert 5 in returns
    assert "ABORTED" in src


@pytest.mark.parametrize("name", PROBES)
def test_every_sqlite_connection_is_closed_in_the_function_that_opens_it(name):
    """The Windows lock, guarded for scripts that never build a TripleStore.

    `measure_retrieval.py` and `who_outranks.py` hold a store open across the
    whole run and are guarded by the close-before-rmtree ordering test. These
    two open a bare connection inside a helper and must close it there, before
    the temp directory is removed by the caller's `finally`.
    """
    # W1 — the shared read-only open counts as an open: keyed on `.connect`
    # alone, this guard went silent once the probes moved to
    # scripts/_readonly.py's open_read_only. The key is tests/_readonly_opens.py's
    # (either kind of open, through any import alias); its companion follows.
    tree = ast.parse((ROOT / "scripts" / name).read_text(encoding="utf-8"))
    _assert_every_open_is_closed_where_it_is_opened(name, tree)


def _assert_every_open_is_closed_where_it_is_opened(name: str, tree: ast.AST) -> None:
    """The close guard's assertion, shared with its companion."""
    from tests._readonly_opens import unclosed_opens
    leaks = unclosed_opens(tree)
    assert not leaks, (
        f"{name}: {leaks} open a sqlite connection and never close it — "
        f"on Windows the temp directory cannot then be removed")


@pytest.mark.parametrize("planted", [
    # the opener by its plain name, which the pre-W1 key (`.connect`) never saw
    "def f(p):\n    c = open_read_only(p)\n    return c.execute('SELECT 1').fetchall()\n",
    # ...and through an alias the guard's source does not spell
    "from scripts._readonly import open_read_only as ro\ndef f(p):\n    return ro(p).execute('SELECT 1').fetchall()\n",
    "import sqlite3 as s\ndef f(p):\n    return s.connect(p).execute('SELECT 1').fetchall()\n",
    "from sqlite3 import Connection as C\ndef f(p):\n    return C(p).execute('SELECT 1').fetchall()\n",
    "import scripts._readonly as R\nasync def f(p):\n    return R.open_read_only(p).execute('SELECT 1').fetchall()\n",
], ids=lambda s: s.strip().splitlines()[-1][:48])
def test_companion_a_function_that_opens_and_never_closes_fails_the_close_guard(planted):
    """COMPANION (W1 part 5f, SF5), through the guard's own assertion: a leak
    through either kind of open, under any alias, fails it naming the
    function; the same open with a close in the function passes."""
    with pytest.raises(AssertionError, match=r"planted: \['f \(line \d+\)'\] open a sqlite connection"):
        _assert_every_open_is_closed_where_it_is_opened("planted", ast.parse(planted))
    closing = planted.rstrip("\n") + "\n    c.close()\n" if "c = " in planted else \
        planted.replace("    return ", "    c = ").rstrip("\n") + "\n    c.close()\n    return c\n"
    _assert_every_open_is_closed_where_it_is_opened("planted", ast.parse(closing))


def _assert_opens_the_copy_read_only(name: str, tree: ast.AST) -> None:
    """The read-only check `test_the_probes_propose_nothing` makes, shared
    with its companion."""
    from tests._readonly_opens import read_only_problems
    problems = read_only_problems(tree)
    assert not problems, f"{name} opens the copy writable: {problems}"


@pytest.mark.parametrize("planted,expected", [
    ("from sqlite3 import connect as c\nconn = c(copy)\n", "line 2: a direct connect (c), not open_read_only"),
    ("import sqlite3.dbapi2 as d\nconn = d.connect(copy)\n", "line 2: a direct connect (d.connect), not open_read_only"),
    ("rows = json.load(f)\n", "the script never calls open_read_only"),
], ids=lambda s: s.strip().splitlines()[-1][:40])
def test_companion_the_read_only_check_fails_on_an_aliased_connect_and_a_script_that_never_opens(planted, expected):
    """COMPANION (SF5) for the read-only check `test_the_probes_propose_nothing`
    makes, through its own assertion: the connect is found through the import
    that bound its name, not by the spelling `sqlite3.connect`, and a script
    that opens nothing read-only fails it too."""
    import re
    with pytest.raises(AssertionError, match=re.escape(expected)):
        _assert_opens_the_copy_read_only("planted", ast.parse(planted))


@pytest.mark.parametrize("name", PROBES)
def test_the_probes_check_the_original_rather_than_promising(name):
    src = (ROOT / "scripts" / name).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fns = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_md5" in fns, f"{name} does not hash the store it reads"
    assert "MUTATED" in src, f"{name} does not report whether it changed it"
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "copy2" in calls, f"{name} reads the ORIGINAL rather than a copy"


@pytest.mark.parametrize("name", PROBES)
def test_the_probes_propose_nothing(name):
    """Same discipline as propose_relation_vocabulary.py: a probe that decided
    would be deciding on evidence nobody had read."""
    src = (ROOT / "scripts" / name).read_text(encoding="utf-8")
    tree = ast.parse(src)
    # `add` is deliberately NOT in this set: the first draft forbade it and
    # fired on `set.add`, which is the guard being cleverer than it can afford
    # to be. These three belong only to the store and the index.
    forbidden = {"upsert", "commit", "executescript"}
    hits = [f"{n.func.attr}:{n.lineno}" for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in forbidden]
    assert not hits, f"{name} writes: {hits}"
    # And neither may construct a store — that is what migrates a file (H.2).
    built = [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "TripleStore"]
    assert not built, f"{name} constructs a TripleStore at line(s) {built}"
    # W1 — by AST: predicate_distribution's docstring alone satisfied the
    # old text check `"mode=ro" in src`.
    _assert_opens_the_copy_read_only(name, tree)
