"""W1 part 5f (N2) — every script that opens a store through the shared
read-only open runs its read path on a copy of the pre-run fixture.

W1 part 3 migrated 38 scripts onto `scripts/_readonly.py` and reported
"behaviour preserved, measured" from a before/after run that was not
committed; twenty of the scripts were named by no test. This is that run,
kept: the population is DERIVED (every tracked script whose AST calls
`open_read_only`, through any alias — `tests/_readonly_opens.py`), each
member has a recipe (its minimal arguments, the exit code the fixture gives,
and, where that is not 0, the contract that makes it so) or a declared
reason it cannot run here, and the two lists are frozen against the
population both ways: a script in neither fails, and a recipe for a script
that no longer opens a store fails too.

Each run is a subprocess in its own directory (`data/` holds copies of the
fixture's stores, including the names `seal_check.py` reads relative to its
CWD), with the provider URLs pointed at a closed port and NEXUS_HOST and
NEXUS_PORT at a loopback port bound and closed (W1's close, M7: the console's
own port was the host's, so `replay_check.py`, which refuses to run while
something listens where NEXUS serves, failed on a machine running NEXUS on
8000 — the third verifier's lens 3, F1; law 10), the suite's capture
redirects pruned from the environment, and every capture or log the script
would read named explicitly as an absent file under the run's directory, so
nothing outside it is read and nothing a sibling test wrote can reach it.
Asserted: the exit code, the stores' bytes unchanged, and nothing new under
`data/`. Outputs the script writes go under `out/`.

Skipped by name, never silently: `aq_score.py` and `aq_verbalise.py` take
AQ's labelled corpus files (`citation_stripped.key.json`, the stripped and
vmap files), which the fixture does not carry.

W4 part 4 — LAW 15'S FIRST LINE (the W1 register line addressed to W4).
Each run's first line of output names the store the recipe handed the
script: its absolute path, as `scripts/_readonly.py`'s `store_label` spells
it (a URL would carry its secrets blanked by the same helper:
`tests/test_w3b_secrets.py` P2 holds that form on the hosted store). The
store is derived from the recipe, never listed: the `{kb}` or `{data}` its
run step passes, the fixture copy's folder for the scripts that read
`data/` relative to their working directory, and a recipe's own `names`
where the script is handed something else (`restore_verify.py`: the backup
folder it copies, never opening the store in place). On the base (53227c1)
the line named the store in 8 of the 39 runs (W1 counted 7 of 38;
`backup.py` came later and named its data root first):
SP/w4/p4/prefix.txt. The companion plants three first lines the rule's
source does not name.
"""
from __future__ import annotations

import ast
import hashlib
import os
import shutil
import socket
import subprocess
from tests import _proc
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._readonly_opens import OPENER, store_opens
from tests._tracked import tracked_scripts

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "pre_run_data"
CLOSED_PORT = "http://127.0.0.1:9"
# The suite's writer redirects: pruned so a capture a sibling test wrote in
# this session cannot reach a script here, and the scripts read the absent
# files they are given instead.
REDIRECTS = ("NEXUS_CALIBRATION_LOG", "NEXUS_CITATION_CAPTURE_PATH", "NEXUS_FIRE_CAPTURE_PATH")


@dataclass(frozen=True)
class Recipe:
    steps: tuple                 # each step: argv for the script, or (producer script, argv) for an input it needs
    exit_code: int = 0
    contract: str = ""           # required when exit_code != 0: why the fixture gives that exit
    names: str = ""              # W4: the store the first line names, when it is not the run step's {kb} or {data}


R = Recipe
ABSENT = "{out}/absent.jsonl"    # a capture or log that does not exist
EMPTY = "{out}/empty.jsonl"      # a capture with no rows (created before the run)

RECIPES: dict[str, Recipe] = {
    "archive_consolidation.py": R((["--kb", "{kb}", "--source", "hippocampus_consolidation", "--report", "{out}/record.json"],)),
    "archive_pregate.py": R((["--kb", "{kb}", "--report", "{out}/record.json"],)),
    "as2_instance_windows.py": R((["--db", "{kb}"],)),
    # W3 part 4 — the backup takes the DATA ROOT (every store under it), the
    # first recipe to; `{data}` is the fixture copy's folder. The restore
    # verification runs on the backup the first step wrote, in a scratch
    # folder under TMPDIR, never on the store.
    "backup.py": R((["--data-root", "{data}", "--out", "{out}/backups"],)),
    "restore_verify.py": R(((" backup.py", ["--data-root", "{data}", "--out", "{out}/backups"]),
                            ["--latest-under", "{out}/backups"]), names="{out}/backups"),
    "audit_pairs.py": R(((" measure_retrieval.py", ["--db", "{kb}", "--write-template", "{out}/pairs.json"]),
                         ["--db", "{kb}", "--pairs", "{out}/pairs.json"]), 1,
                        "the template's fragments are absent from the fixture's store, and an absent fragment "
                        "exits 1 so a re-baseline can gate on it (W.4); the store is read in full first"),
    "citation_fp_probe.py": R((["--kb", "{kb}", "--capture", ABSENT, "--out", "{out}/probe.json"],)),
    "citation_judge_extract.py": R((["--kb", "{kb}", "--capture", EMPTY, "--out", "{out}/extract.json"],), 2,
                                   "a capture with no rows is 'nothing to extract', exit 2; the store is opened "
                                   "and its md5 printed before the verdict"),
    "claim_graph_probe.py": R((["--kb", "{kb}", "--out", "{out}/probe.json"],)),
    "coactivation_eligibility.py": R((["--kb", "{kb}"],)),      # the forecast path, on planted rows: test_w1_readonly_open.py (C)
    "consolidation_feedback.py": R((["--db", "{kb}"],)),
    "consolidation_sample.py": R((["--db", "{kb}", "--out", "{out}/sample.json"],)),
    "consolidation_separation.py": R(((" consolidation_sample.py", ["--db", "{kb}", "--out", "{out}/sample.json"]),
                                      ["--db", "{kb}", "{out}/sample.json"]), 4,
                                     "the sample is unlabelled (labels are a human's), so scoring is REFUSED with exit 4; "
                                     "the sample's md5 and the store's are read and printed first"),
    "count_traces.py": R(([],)),                                 # data/kb.db relative to the CWD
    "count_traces2.py": R(([],)),
    "cp_b49_tally.py": R((["--db", "{kb}", "--capture", ABSENT],)),
    # W11c: moved from the repository root into scripts/ (the operator's answer), so it joins the population.
    "diag_downstream.py": R((["--db", "{kb}"],)),
    "curiosity_pace_probe.py": R((["--kb", "{kb}", "--calibration", ABSENT, "--out", "{out}/probe.json"],)),
    "epistemic_derivation_probe.py": R((["--kb", "{kb}", "--out", "{out}/probe.json"],)),
    "forecast_target_probe.py": R((["--kb", "{kb}", "--calibration", ABSENT, "--capture", ABSENT, "--out", "{out}/probe.json"],)),
    "framework_probe.py": R((["--kb", "{kb}", "--out", "{out}/probe.json"],)),
    "gate_population_probe.py": R((["--kb", "{kb}", "--out", "{out}/probe.json"],)),
    "measure_derivations.py": R((["--kb", "{kb}", "--out", "{out}/derivations.md"],)),
    "measure_retention_cost.py": R((["--kb", "{kb}", "--tokeniser", "stub", "--capture", ABSENT, "--out", "{out}/cost.json"],), 4,
                                   "the stub tokeniser is declared NOT_A_MEASUREMENT (exit 4); the hf and ollama "
                                   "tokenisers need the network or a model"),
    "measure_retrieval.py": R(((" measure_retrieval.py", ["--db", "{kb}", "--write-template", "{out}/pairs.json"]),
                               ["--db", "{kb}", "--pairs", "{out}/pairs.json"]), 5,
                              "a measurement may not fall back (W.3): with no embedding model it aborts by name, "
                              "exit 5, after opening the store; the template step runs its read path to exit 0"),
    "pairing_exclusion_probe.py": R((["--kb", "{kb}", "--out", "{out}/probe.json"],)),
    "predicate_distribution.py": R((["--db", "{kb}"],), 1,
                                   "every predicate in the fixture appears once: 'nothing to cluster' is the script's "
                                   "verdict, exit 1 by contract (OQ-2)"),
    "predicate_similarity.py": R((["--db", "{kb}"],), 5,
                                 "a measurement may not fall back (W.3): with no embedding model it aborts by name, exit 5"),
    "pregate_rows_probe.py": R((["--kb", "{kb}", "--tokeniser", "stub", "--out", "{out}/probe.json"],)),
    "propose_contradiction_pairs.py": R((["--db", "{kb}"],)),
    "propose_relation_vocabulary.py": R((["--db", "{kb}"],)),
    "render_block_probe.py": R((["--kb", "{kb}", "--query", "what improves recovery?", "--capture", ABSENT, "--out", "{out}/probe.json"],)),
    "replay_check.py": R((["--db", "{kb}", "--report-dir", "{out}", "--capture", ABSENT],)),   # dry run: nothing is sent
    "seal_check.py": R(([],)),                                   # data/kb_measure3.db and data/archive_measure3.db under the CWD
    "term_identity_probe.py": R((["--kb", "{kb}", "--capture", ABSENT, "--out", "{out}/probe.json"],)),
    "v31_phase0_probe.py": R((["--kb", "{kb}", "--archive", "{ar}", "--calibration", ABSENT, "--out", "{out}/probe.json"],)),
    "verify_packet_f.py": R((["--kb", "{kb}"],)),               # its ten rebalances run on its own temp copy
    "verify_packet_k.py": R((["--kb", "{kb}"],)),
    "vocab_effect.py": R((["--db", "{kb}"],)),
    "wc_window_count.py": R((["--db", "{kb}", "--capture", ABSENT],)),
}

SKIPPED: dict[str, str] = {
    "aq_score.py": "takes AQ's labelled corpus (the stripped and vmap files as positional arguments), "
                   "which the fixture does not carry",
    "aq_verbalise.py": "reads AQ's key file citation_stripped.key.json beside the CWD, which the fixture does not carry",
    # W2 part 4: it reads only the store its own launch creates, in its own
    # temporary folder, never one it is given; its run is K1 in
    # tests/test_w2_bundle_smoke.py (`--source`).
    "bundle_smoke.py": "reads only the store the process it launches creates in the smoke's own folder, "
                       "never a given store; run by tests/test_w2_bundle_smoke.py",
    # W3b part 4: the two migration scripts need a Postgres database (the
    # migration's target, the export's source), which the fixture does not
    # carry; tests/test_w3b_migration.py runs both as the operator would,
    # in the Postgres job.
    "export_postgres_to_sqlite.py": "its source is a Postgres database, which the fixture does not carry; run by "
                                    "tests/test_w3b_migration.py",
    "migrate_sqlite_to_postgres.py": "its target is a Postgres database, which the fixture does not carry; run by "
                                     "tests/test_w3b_migration.py",
}


def migrated_scripts() -> list[str]:
    """Every tracked script whose AST calls the shared read-only open."""
    out = []
    for p in tracked_scripts():
        kinds = {kind for kind, _line in store_opens(ast.parse(p.read_text(encoding="utf-8")))}
        if OPENER in kinds:
            out.append(p.name)
    return sorted(out)


def assert_answered_for(population: list[str], recipes: dict, skipped: dict) -> None:
    """The population guard's assertions, shared with its companion: frozen
    both ways, disjoint, and a non-zero exit carries its contract."""
    assert not set(recipes) & set(skipped), f"a script is both runnable and skipped: {sorted(set(recipes) & set(skipped))}"
    missing = sorted(set(population) - set(recipes) - set(skipped))
    assert not missing, f"migrated scripts with neither a recipe nor a declared reason: {missing}"
    stale = sorted((set(recipes) | set(skipped)) - set(population))
    assert not stale, f"recipes or skips for scripts that no longer open a store read-only: {stale}"
    for name, recipe in recipes.items():
        assert (recipe.exit_code == 0) == (not recipe.contract), (
            f"{name}: a non-zero exit needs its contract stated, and a zero exit needs none")


def test_the_population_is_derived_and_every_member_is_answered_for():
    population = migrated_scripts()
    print(f"N2 population ({len(population)} scripts calling {OPENER}): {population}")
    assert len(population) >= 38, "non-vacuity: W1 part 3 migrated 38"
    assert {"coactivation_eligibility.py", "count_traces.py", "seal_check.py", "wc_window_count.py"} <= set(population)
    assert_answered_for(population, RECIPES, SKIPPED)


def test_companion_an_unanswered_script_a_stale_entry_and_a_bare_non_zero_exit_fail_by_name():
    """COMPANION (OQ-16 rules 1 and 3): a migrated script in neither list, a
    recipe for a script that no longer opens a store, a script in both, and
    a non-zero exit with no contract, each fail the guard's own assertion
    naming the plant; none of these names appears in the guard's source."""
    with pytest.raises(AssertionError, match=r"neither a recipe nor a declared reason: \['planted_probe.py'\]"):
        assert_answered_for(["planted_probe.py", "as2_instance_windows.py"], {"as2_instance_windows.py": R((["--db", "{kb}"],))}, {})
    with pytest.raises(AssertionError, match=r"no longer open a store read-only: \['gone_probe.py'\]"):
        assert_answered_for(["as2_instance_windows.py"], {"as2_instance_windows.py": R((["--db", "{kb}"],))},
                            {"gone_probe.py": "it was here once"})
    with pytest.raises(AssertionError, match=r"both runnable and skipped: \['twice_probe.py'\]"):
        assert_answered_for(["twice_probe.py"], {"twice_probe.py": R(([],))}, {"twice_probe.py": "and skipped"})
    with pytest.raises(AssertionError, match="bare_exit_probe.py: a non-zero exit needs its contract stated"):
        assert_answered_for(["bare_exit_probe.py"], {"bare_exit_probe.py": R(([],), 3)}, {})
    assert_answered_for(["as2_instance_windows.py", "aq_score.py"], {"as2_instance_windows.py": R((["--db", "{kb}"],))},
                        {"aq_score.py": SKIPPED["aq_score.py"]})


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


STORE_PLACEHOLDERS = ("{kb}", "{data}")


def named_store(recipe: Recipe, data: Path, out: Path) -> Path:
    """The store the recipe hands the script, which its first line must
    name: the recipe's own `names`, else the first store placeholder its run
    step passes, else the fixture copy's folder (a script that reads `data/`
    relative to its working directory names the store under it)."""
    last = recipe.steps[-1]
    argv = last[1] if isinstance(last, tuple) else last
    template = recipe.names or next((a for a in argv if a in STORE_PLACEHOLDERS), "{data}")
    return Path(template.format(kb=str(data / "kb.db"), ar=str(data / "archive.db"), data=str(data), out=str(out)))


def assert_first_line_names_the_store(name: str, first: str, store: Path) -> None:
    """Law 15 (W4 part 4): the first line of a run's output names the store
    it opened, as `store_label` spells a path (absolute, forward slashes)."""
    want = Path(os.path.abspath(store)).as_posix()
    assert want in first, f"{name}: its first line does not name the store it opened ({want}): {first[:200]!r}"


def _closed_port() -> int:
    """A loopback port bound and then closed: nothing listens there. (Another
    process could bind it before the child probes; not seen, and the same
    window as start.py's S1, law 10.)"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_dir(tmp_path: Path) -> tuple[Path, Path, Path]:
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy2(FIXTURE / "kb.db", data / "kb.db")
    shutil.copy2(FIXTURE / "archive.db", data / "archive.db")
    shutil.copytree(FIXTURE / "snapshots", data / "snapshots")
    shutil.copy2(FIXTURE / "kb.db", data / "kb_measure3.db")            # seal_check.py's relative names
    shutil.copy2(FIXTURE / "archive.db", data / "archive_measure3.db")
    out = tmp_path / "out"
    out.mkdir()
    (out / "empty.jsonl").write_text("", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return data, out, scratch


@pytest.mark.parametrize("name", sorted(RECIPES) + sorted(SKIPPED))
def test_the_read_path_runs_on_a_copy_of_the_fixture_and_the_store_holds(tmp_path, name):
    if name in SKIPPED:
        pytest.skip(f"{name}: {SKIPPED[name]}")
    recipe = RECIPES[name]
    data, out, scratch = _run_dir(tmp_path)
    stores = {p: _md5(p) for p in data.rglob("*") if p.is_file()}
    listing = sorted(p.relative_to(data).as_posix() for p in data.rglob("*"))
    env = dict(os.environ)                                              # copy, prune, add
    for var in REDIRECTS:
        env.pop(var, None)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", OLLAMA_URL=CLOSED_PORT,
               LM_STUDIO_URL=CLOSED_PORT, NEXUS_ENV_ROOT=str(tmp_path),
               NEXUS_HOST="127.0.0.1", NEXUS_PORT=str(_closed_port()),
               TMPDIR=str(scratch), TEMP=str(scratch), TMP=str(scratch))
    scripts_dir = ROOT / "scripts"
    rc = None
    for i, step in enumerate(recipe.steps):
        script, argv = (step[0].strip(), step[1]) if isinstance(step, tuple) else (name, step)
        args = [a.format(kb=str(data / "kb.db"), ar=str(data / "archive.db"), out=str(out), data=str(data))
                for a in argv]
        proc = _proc.run([sys.executable, str(scripts_dir / script), *args], cwd=tmp_path, env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=600, stdin=subprocess.DEVNULL)
        lines = [l for l in (proc.stdout + proc.stderr).splitlines() if l.strip()]
        last = step is recipe.steps[-1]
        print(f"N2 {script} ({'input step' if not last else 'the run'}): exit {proc.returncode}; "
              f"last line: {lines[-1][:140] if lines else '(no output)'}")
        if not last:
            assert proc.returncode == 0, f"{script} (an input step for {name}) exited {proc.returncode}:\n" + "\n".join(lines[-15:])
        rc = proc.returncode
        output = "\n".join(lines[-15:])
        first = (proc.stdout.splitlines() or [""])[0]
    assert rc == recipe.exit_code, (
        f"{name} exited {rc}, the recipe says {recipe.exit_code}"
        f"{' (' + recipe.contract + ')' if recipe.contract else ''}:\n{output}")
    print(f"N2 {name}: first line {first[:160]!r}")
    assert_first_line_names_the_store(name, first, named_store(recipe, data, out))
    changed = [p.relative_to(data).as_posix() for p, h in stores.items() if _md5(p) != h]
    assert not changed, f"{name} changed the bytes of {changed}"
    after = sorted(p.relative_to(data).as_posix() for p in data.rglob("*"))
    assert after == listing, f"{name} left new files under data/: {sorted(set(after) - set(listing))}"


def test_companion_a_first_line_that_does_not_name_the_store_fails_by_name(tmp_path):
    """COMPANION (OQ-16 rules 1 and 3; W4 part 4): three first lines of the
    class the rule forbids, none of which its source names, each fail the
    rule's own assertion naming the script: a banner before the store (the
    archivers' shape on the base), the store named only after the first line
    (a header first), and a line naming another store (a decoy beside the
    copy). The store's own line, as `store_label` and `read_only_how` make
    it, passes; so does the store named from a relative path under the CWD."""
    from scripts._readonly import read_only_how, store_label
    store = tmp_path / "data" / "kb.db"
    decoy = tmp_path / "data-elsewhere" / "kb.db"
    plants = {"a banner": "=" * 74, "a header first": "Which predicates does this store use that it should not?",
              "another store": f"database: {store_label(decoy)} (opened read-only: {read_only_how(decoy)})"}
    for label, first in plants.items():
        with pytest.raises(AssertionError, match=r"planted_probe\.py: its first line does not name the store it opened"):
            assert_first_line_names_the_store("planted_probe.py", first, store)
        print(f"W4 companion: {label} refused: {first[:60]!r}")
    assert_first_line_names_the_store("planted_probe.py", f"database: {store_label(store)} (opened read-only: "
                                      f"{read_only_how(store)})", store)
    # The scripts print the line through `store_line`, which names a URL with
    # its secrets blanked by the existing redaction (store_label): a password
    # never reaches the first line.
    from scripts._readonly import store_line
    line = store_line("postgresql://reader:Pl4nted-pw@127.0.0.1:1/x_test")
    assert line.startswith("database: postgres ") and "Pl4nted-pw" not in line, line
    assert store_line(store) == f"database: {store_label(store)} (opened read-only: {read_only_how(store)})"
    rule = Path(__file__).read_text(encoding="utf-8").split("def assert_first_line_names_the_store", 1)[1]
    rule = rule.split("\ndef ", 1)[0]
    for plant in ("=" * 74, "Which predicates", "data-elsewhere"):
        assert plant not in rule, f"the rule's source names the plant {plant!r}"


def test_companion_a_console_where_the_suite_says_nexus_serves_does_not_move_a_recipe(tmp_path, monkeypatch):
    """COMPANION (OQ-16 rules 1 and 3; W1's close, M7): a listener planted
    where the suite's own environment says NEXUS serves, then the recipe
    test itself, driven for `replay_check.py`, the recipe that probes that
    address. The premise first: with the environment the recipe inherited
    before M7, the script refuses (exit 3), so the plant is one the recipe
    would see. Then the recipe passes: its child probes a closed port.
    Pre-fix (law 16), at 1a1a521: the recipe exited 3, "REFUSED: something is
    listening on 127.0.0.1:<port>, where NEXUS serves"."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    try:
        port = listener.getsockname()[1]
        monkeypatch.setenv("NEXUS_HOST", "127.0.0.1")
        monkeypatch.setenv("NEXUS_PORT", str(port))
        premise = tmp_path / "premise"
        premise.mkdir()
        data, out, _scratch = _run_dir(premise)
        env = dict(os.environ, NEXUS_ENV_ROOT=str(premise), PYTHONDONTWRITEBYTECODE="1")
        proc = _proc.run([sys.executable, str(ROOT / "scripts" / "replay_check.py"), "--db", str(data / "kb.db"),
                               "--report-dir", str(out), "--capture", str(out / "absent.jsonl")],
                              cwd=premise, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=600, stdin=subprocess.DEVNULL)
        print(f"M7 premise: exit {proc.returncode}; {proc.stdout.strip().splitlines()[-1][:120] if proc.stdout.strip() else ''}")
        assert proc.returncode == 3 and f"listening on 127.0.0.1:{port}" in proc.stdout, (
            f"premise: the planted listener is not what replay_check sees: exit {proc.returncode}\n{proc.stdout[-400:]}")
        recipe = tmp_path / "recipe"
        recipe.mkdir()
        test_the_read_path_runs_on_a_copy_of_the_fixture_and_the_store_holds(recipe, "replay_check.py")
    finally:
        listener.close()
