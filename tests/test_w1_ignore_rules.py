"""W1 part 3 — every file the product writes under data/ is ignored by git,
and the local data sentinel watches every one.

The worklist asked for `.gitignore` lines for `data/citation_capture.jsonl`,
`data/fire_capture.jsonl` and `data/calibration_log.jsonl`. W1's establish
found all three already ignored (`data/*.jsonl`, and `citation_*.jsonl` for
the first), so no fix is claimed for them. It found two things that were
not covered:
- SQLite's sidecars beside each store (`data/kb.db-journal` exists during
  every write transaction), now ignored;
- tests/conftest.py's local data sentinel, a hand list of six paths that
  missed both captures. It is now derived from the writer ratchet.

I   Every default output path under data/ is ignored by git: each class
    writer's default and each acknowledged function writer's constant
    (tests/_writers.py's derivation), and each store's sidecars. The tracked
    `.gitkeep` files are not ignored.
In  The same paths under a data root INSIDE data/ (`start.py --data-root
    data/<name>`, W1 part 1) are ignored at any depth, and the two `.gitkeep`
    negations still hold (W1 part 5f, N3: the rules were one level deep).
Ic  Companions: a planted default path that no rule covers is named, at the
    top level and under a nested root.
S   The local sentinel's population is that same derivation, and holds the
    two captures the hand list missed.
Sc  Companion: a function writer added to a synthetic backend module joins
    the population the sentinel is built from.
Sw  Companion (OQ-16 rule 3, the consequence; W1 part 5f, N4): a child
    pytest session whose test writes a planted guarded path fails at
    teardown, and the sentinel names the path.
"""
from __future__ import annotations

from tests import _proc
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _writers import derive_writers, unconsumed_data_constants  # noqa: E402

# W2 adds the single-writer lock and its takeover log (backend/store_lock.py).
SIDECARS = ("-journal", "-wal", "-shm", ".lock", ".lock.log")


def writer_defaults() -> set[str]:
    """Every default path a writer has: class writers and acknowledged
    function writers, as tests/_writers.py derives them."""
    paths = {info["default_path"] for info in derive_writers().values()}
    return paths | set(unconsumed_data_constants().values())


def default_data_paths() -> list[str]:
    """The paths git must ignore: each file and each store's sidecars, and for
    a directory store (chroma/, snapshots/) what is written inside it (the
    directory itself holds a tracked .gitkeep and cannot be ignored)."""
    out = set()
    for p in writer_defaults():
        if "." not in Path(p).name:
            out.add(p.rstrip("/") + "/anything")
            continue
        out.add(p)
        if p.endswith(".db"):
            out |= {p + s for s in SIDECARS}
    return sorted(out)


def not_ignored(paths: list[str]) -> list[str]:
    missing = []
    for p in paths:
        r = _proc.run(["git", "check-ignore", "-q", "--no-index", p], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if r.returncode != 0:
            missing.append(p)
    return missing


def test_i_every_default_data_path_is_ignored():
    paths = default_data_paths()
    print(f"I default data paths, derived: {paths}")
    assert "data/citation_capture.jsonl" in paths and "data/kb.db-journal" in paths
    assert not_ignored(paths) == [], "a file the product writes under data/ would show in git status"
    assert not_ignored(["data/chroma/.gitkeep", "data/snapshots/.gitkeep"]) == [
        "data/chroma/.gitkeep", "data/snapshots/.gitkeep"], "the tracked .gitkeep files became ignored"


def test_ic_a_planted_default_path_no_rule_covers_is_named():
    assert not_ignored(["data/new_capture.csv"]) == ["data/new_capture.csv"]


NESTED_ROOT = "tester1"          # `start.py --data-root data/tester1`


def nested_data_paths(root: str = NESTED_ROOT) -> list[str]:
    """Every default data path, written under a data root inside data/."""
    return sorted("data/" + root + "/" + p[len("data/"):] for p in default_data_paths())


def test_in_a_data_root_inside_data_is_ignored_at_any_depth():
    """N3: before part 5f `git check-ignore` matched `data/tester1/kb.db`,
    its journal, its logs and its chroma/ contents by no rule; after
    `start.py --data-root data/<name>` every store sat in `git status`."""
    nested = nested_data_paths()
    print(f"In nested data paths, derived: {nested}")
    assert "data/tester1/kb.db" in nested and "data/tester1/kb.db-journal" in nested
    assert "data/tester1/chroma/anything" in nested and "data/tester1/citation_capture.jsonl" in nested
    assert not_ignored(nested) == [], "a file the product writes under a data root inside data/ would show in git status"
    deeper = nested_data_paths("a/b")
    assert not_ignored(deeper) == [], "two levels down the same files show in git status"
    assert not_ignored(["data/chroma/.gitkeep", "data/snapshots/.gitkeep"]) == [
        "data/chroma/.gitkeep", "data/snapshots/.gitkeep"], "the nested rules swallowed the tracked .gitkeep files"


def test_ic_a_planted_nested_path_no_rule_covers_is_named():
    assert not_ignored(["data/tester1/new_capture.csv"]) == ["data/tester1/new_capture.csv"]


def test_the_ignore_file_ends_with_a_newline():
    """Part 3 edited a file with no final newline; a rule appended by the
    next hand would have joined the last line."""
    raw = (ROOT / ".gitignore").read_bytes()
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n"), raw[-40:]


def test_s_the_local_sentinel_watches_every_derived_writer():
    from tests import conftest
    watched = set(conftest.GUARDED_REAL_PATHS)
    assert watched == writer_defaults(), watched ^ writer_defaults()
    assert {"data/citation_capture.jsonl", "data/fire_capture.jsonl"} <= watched


def test_sc_a_new_function_writer_joins_the_population(tmp_path):
    planted = tmp_path / "planted_writer.py"
    planted.write_text('DEFAULT_PROBE_PATH = "data/probe_log.jsonl"\n', encoding="utf-8")
    found = unconsumed_data_constants([planted])
    assert "data/probe_log.jsonl" in found.values(), found


PLANTED_GUARD = "data/planted_capture.jsonl"


def test_sw_companion_a_write_to_a_guarded_path_fails_the_session_naming_it(tmp_path):
    """COMPANION (OQ-16 rule 3; N4). S and Sc check the sentinel's
    population; this checks its consequence. A child pytest session loads
    the suite's conftest, a planted module points `GUARDED_REAL_PATHS` at a
    planted file under tmp (never the real data/) before the session's
    baseline is taken, and its one test appends to that file. The session
    errors at teardown, `TEST SUITE MUTATED REAL DATA`, and names the path.
    Modelled on tests/test_w1_conftest_once.py's L test."""
    planted_file = tmp_path / "planted_capture.jsonl"
    planted_file.write_text("baseline row\n", encoding="utf-8")
    module = tmp_path / "test_writes_a_guarded_path.py"
    module.write_text(
        "from pathlib import Path\n"
        "from tests import conftest\n"
        f"conftest.GUARDED_REAL_PATHS = {{{PLANTED_GUARD!r}: Path({str(planted_file)!r})}}\n"
        "def test_write():\n"
        f"    with open({str(planted_file)!r}, 'a', encoding='utf-8') as f:\n"
        "        f.write('a row the suite wrote\\n')\n",
        encoding="utf-8")
    proc = _proc.run(
        [sys.executable, "-m", "pytest", str(module), "-p", "tests.conftest", "-q",
         "-p", "no:cacheprovider", "--basetemp", str(tmp_path / "bt"), "-rE"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    print(f"Sw child session: {tail}")
    assert "1 passed, 1 error" in tail, proc.stdout[-2500:] + proc.stderr[-1500:]
    assert "TEST SUITE MUTATED REAL DATA" in proc.stdout
    assert PLANTED_GUARD in proc.stdout, proc.stdout[-2500:]
    assert planted_file.read_text(encoding="utf-8").count("\n") == 2, "the planted write did not land"
