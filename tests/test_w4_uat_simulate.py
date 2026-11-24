"""W4 part 4 — the simulated UAT, `scripts/uat_simulate.py` (run/specs/W4.md
S2's D20 to D23; S3's U1 to U3; RUN_CHARTER D6).

The script drives the product from outside, as a tester and an operator
would: it starts `start.py --serve`, `python -m backend.mcp`,
`scripts/backup.py`, `scripts/restore_verify.py` and the operator scripts as
child processes and talks HTTP and stdio to them. Each step is one row of its
report: who, what they do, the scripted input, what was expected, what was
seen, PASS, FAIL or NOT RUN with its reason.

U1  The simulation's verdicts, from the script's own machinery run as a
    process (a wrapper that adds a planted section to the script's own
    section list and calls its `main`): a planted step whose expected value
    cannot hold fails the run (exit 1) and is named in the report, on its
    row and in the report's last lines; a section that raises fails the run
    and names the exception; a REQUIRED step that could not run fails the
    run, and a step that is not required and could not run does not. And
    the report lists every scripted input the run used, DERIVED FROM THE
    RUN, never from a second list here: every setting the run wrote into its
    install's `.env` (read back from the kept work folder) is listed, with
    its value unless the run generated a secret; every question a tester
    asked is listed, read back from the product's own store (each record's
    `query_hash` is the sha256 of a listed question, and each listed
    question is a record's); and every claim the run stored is the shape
    of a listed sentence.
U2  The console section end to end in the suite: `--sections console` on a
    real `start.py --serve` child under the stub, the report in a temporary
    folder; exit 0; every console step PASS; the report names the routes
    D20's console list names; the checkout's worktree (git status) and
    `data/` as they were.
D22 The report is not left in a checkout: a report the script writes at its
    default path (`reports/uat_simulation_<platform>.md`) is ignored by git
    and leaves `git status --porcelain` as it was.
D20 The script imports no store module and opens no store: it is in neither
    `tests/_fz_trace.py`'s freeze-driver population nor law 15's
    population, both derived; the companion plants a store import into a
    copy of its source and the freeze-driver derivation finds it.

LAW 16. Every test here was run on the base (53227c1) before the script
existed; the output is SP/w4/p4/prefix.txt. On the base each fails only
because the script does not exist yet (`scripts/uat_simulate.py`: the new
name), declared here; D22's failure on its consequence (the report shown by
`git status`) was then shown with the script written and the `.gitignore`
rule not yet added (SP/w4/p4/prefix_d22.txt).
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
from tests import _proc
import sys
import textwrap
from pathlib import Path

import pytest

from tests._tracked import REPO
from tests._worktree import UNCHANGED, compare, describe, porcelain

SCRIPT = REPO / "scripts" / "uat_simulate.py"
ENV_EXAMPLE = REPO / ".env.example"
ROW = re.compile(r"^\| (?P<id>[a-z_]+\.\d+) \| (?P<who>[^|]*) \| (?P<what>(?:[^|\\]|\\.)*) \| "
                 r"(?P<input>(?:[^|\\]|\\.)*) \| (?P<expected>(?:[^|\\]|\\.)*) \| (?P<seen>(?:[^|\\]|\\.)*) \| "
                 r"(?P<result>PASS|FAIL|NOT RUN)(?: \((?P<why>[^)]*)\))? \|$")
INPUT_ROW = re.compile(r"^\| (?P<who>tester|operator) \| (?P<name>(?:[^|\\]|\\.)*) \| (?P<value>(?:[^|\\]|\\.)*) \| "
                       r"(?P<used>[^|]*) \|$")
NOT_SHOWN = "not shown"


def _run_env() -> dict:
    env = dict(os.environ)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return env


def _data_listing() -> list[str]:
    return sorted(p.relative_to(REPO).as_posix() for p in (REPO / "data").rglob("*"))


def _unescape(cell: str) -> str:
    return cell.replace("\\|", "|").strip()


def step_rows(report: str) -> list[dict]:
    return [{k: (_unescape(v) if isinstance(v, str) else v) for k, v in m.groupdict().items()}
            for m in map(ROW.match, report.splitlines()) if m]


def input_rows(report: str) -> list[dict]:
    """The rows of the report's one inputs section."""
    section = report.split("\n## Scripted inputs\n", 1)[1].split("\n## ", 1)[0]
    return [{k: _unescape(v) for k, v in m.groupdict().items()}
            for m in map(INPUT_ROW.match, section.splitlines()) if m]


def last_lines(report: str, n: int = 4) -> str:
    return "\n".join([line for line in report.splitlines() if line.strip()][-n:])


# ---------------------------------------------------------------------------
# U2 — the console section end to end, once per module (U1's inputs read it)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def console_run(tmp_path_factory):
    """`--sections console`, the report and the kept work folder under a
    temporary folder; the checkout's status and data/ taken around it."""
    base = tmp_path_factory.mktemp("uat_console")
    report, work = base / "report.md", base / "work"
    status_before, data_before = porcelain(), _data_listing()
    proc = _proc.run([sys.executable, str(SCRIPT), "--sections", "console", "--report", str(report),
                           "--work", str(work), "--keep-work"], cwd=REPO, env=_run_env(),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=1500)
    return {"proc": proc, "report": report, "work": work, "status_before": status_before,
            "status_after": porcelain(), "data_before": data_before, "data_after": _data_listing()}


#: D20's console list, by the route or the event each act reaches (the
#: brief's words, run/specs/W4.md S2 D20 and the part's brief).
CONSOLE_REACHES = {"GET /healthz", "POST /chat", "kb_pressure_alert assertion", "POST /kb/assert/confirm",
                   "GET /governance/refusals", "GET /belief/{id}/recall", "GET /kb/as-of", "GET /lineage/verify",
                   "GET /lineage/{id}/verify", "GET /chain/status", "GET /governance/verify",
                   "GET /cortex/stats", "GET /kairos/status", "GET /model/status"}
REACH = re.compile(r"\b(?:(?:GET|POST) /[A-Za-z0-9_{}./-]*[A-Za-z0-9_}]|kb_pressure_alert assertion)")


def test_u2_the_console_section_runs_end_to_end_and_leaves_the_checkout_as_it_was(console_run):
    """Base: the new name (the script does not exist yet)."""
    proc, report = console_run["proc"], console_run["report"]
    out = proc.stdout + proc.stderr
    print(f"\nU2 ({sys.platform}) exit {proc.returncode}; {[l for l in proc.stdout.splitlines() if l.startswith('uat-simulate')]}")
    assert proc.returncode == 0, out[-4000:]
    text = report.read_text(encoding="utf-8")
    rows = step_rows(text)
    console = [r for r in rows if r["id"].startswith("console.")]
    print(f"U2 {len(console)} console steps: {[(r['id'], r['result']) for r in console]}")
    assert console and all(r["result"] == "PASS" for r in console), [r for r in console if r["result"] != "PASS"]
    assert len(console) == len(rows), "a section that was not selected ran"
    reached = set(REACH.findall(text))
    assert CONSOLE_REACHES <= reached, f"the report names no step reaching {sorted(CONSOLE_REACHES - reached)}"
    assert cmp_unchanged(console_run), describe(compare(console_run["status_before"], console_run["status_after"]))
    assert console_run["data_after"] == console_run["data_before"], "the run wrote under the checkout's data/"
    assert (console_run["work"] / "console" / "kb.db").is_file(), "--keep-work kept no console store"


def cmp_unchanged(run: dict) -> bool:
    return compare(run["status_before"], run["status_after"])["verdict"] == UNCHANGED


def _env_lines(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.split(" #", 1)[0].strip()
    return out


def test_u1_the_report_lists_every_scripted_input_the_run_used(console_run):
    """Derived from the run's own artifacts, never from a list here: the
    install `.env` it wrote, and the product's store it kept. Base: the new
    name."""
    from scripts._readonly import open_read_only
    assert console_run["proc"].returncode == 0, console_run["proc"].stdout[-3000:]
    text = console_run["report"].read_text(encoding="utf-8")
    inputs = input_rows(text)
    by_name = {}
    for row in inputs:
        by_name.setdefault(row["name"], []).append(row)
    print(f"\nU1 inputs listed: {len(inputs)}: {[(r['who'], r['name']) for r in inputs]}")
    # (a) every setting the run put in its install's .env
    written, example = _env_lines(console_run["work"] / "install" / ".env"), _env_lines(ENV_EXAMPLE)
    set_by_run = {k: v for k, v in written.items() if example.get(k) != v}
    assert set_by_run, "non-vacuity: the run set no setting"
    unlisted = sorted(k for k in set_by_run if k not in by_name)
    assert not unlisted, f"settings the run wrote into .env that the report does not list: {unlisted}"
    for key, value in set_by_run.items():
        shown = [r["value"] for r in by_name[key]]
        assert value in shown or any(NOT_SHOWN in s for s in shown), (key, value, shown)
    # (b) every question asked, read back from the product's store
    con = open_read_only(console_run["work"] / "console" / "kb.db", row_factory=None)
    try:
        asked = {r[0] for r in con.execute("SELECT query_hash FROM lineage_records")}
        claims = {tuple(r) for r in con.execute(
            "SELECT subject, relation, object FROM triples WHERE source = 'human_assertion'")}
    finally:
        con.close()
    listed = {hashlib.sha256(r["value"].encode("utf-8")).hexdigest(): r["value"] for r in inputs}
    assert asked, "non-vacuity: the store holds no record"
    assert asked <= set(listed), f"{len(asked - set(listed))} record(s) asked a question the report does not list"
    questions = [r["value"] for r in by_name.get("question", [])]
    assert questions and {hashlib.sha256(q.encode('utf-8')).hexdigest() for q in questions} <= asked, \
        "a listed question is no record's"
    # (c) every claim stored is the shape of a listed sentence
    from backend.kb.assertion import assertion_triples
    from backend.kb.tiered_store import TripleStore
    shaped = {TripleStore.stored_form(t.subject, t.relation, t.object)
              for r in by_name.get("sentence", []) for t in assertion_triples(r["value"])}
    assert claims and claims <= shaped, f"stored claims {claims} are not the shape of a listed sentence {shaped}"


# ---------------------------------------------------------------------------
# U1 — the verdicts, from the script's own machinery
# ---------------------------------------------------------------------------

WRAPPER = textwrap.dedent('''
    import sys
    sys.path.insert(0, {scripts!r})
    import uat_simulate as u

    def planted(run):
    {body}

    u.SECTIONS = u.SECTIONS + (("planted", "a planted section", planted),)
    sys.exit(u.main(["--sections", "planted", "--report", {report!r}, "--work", {work!r}]))
''')


def _planted_run(tmp_path: Path, body: str) -> tuple[int, str, str]:
    wrapper = tmp_path / "planted_run.py"
    report, work = tmp_path / "report.md", tmp_path / "work"
    wrapper.write_text(WRAPPER.format(scripts=str(REPO / "scripts"), report=str(report), work=str(work),
                                      body=textwrap.indent(textwrap.dedent(body).strip(), "    ")),
                       encoding="utf-8")
    proc = _proc.run([sys.executable, str(wrapper)], cwd=REPO, env=_run_env(), stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    text = report.read_text(encoding="utf-8") if report.is_file() else ""
    return proc.returncode, proc.stdout + proc.stderr, text


def test_u1_a_planted_failing_step_fails_the_run_and_is_named(tmp_path):
    """Base: the new name."""
    code, out, report = _planted_run(tmp_path, '''
        run.check("tester", "a planted step whose expected value cannot hold",
                  run.inp("tester", "planted operand", "1 + 1"), expected="3", ok=(1 + 1 == 3), seen=str(1 + 1))
        run.check("tester", "a planted step that holds", run.inp("tester", "planted operand", "2 + 2"),
                  expected="4", ok=(2 + 2 == 4), seen=str(2 + 2))
    ''')
    rows = {r["id"]: r for r in step_rows(report)}
    print(f"\nU1 planted failing step: exit {code}; rows {[(k, v['result']) for k, v in rows.items()]}; "
          f"last lines {last_lines(report)!r}")
    assert code == 1, out[-2000:]
    assert rows["planted.1"]["result"] == "FAIL" and rows["planted.1"]["expected"] == "3" \
        and rows["planted.1"]["seen"] == "2", rows
    assert rows["planted.2"]["result"] == "PASS", rows
    tail = last_lines(report)
    assert "FAIL" in tail and "planted.1" in tail and "planted.2" not in tail, tail
    assert re.search(r"^FAIL planted\.1\b", out, re.M), out[-2000:]


def test_u1_a_section_that_raises_fails_the_run_and_names_the_exception(tmp_path):
    """A crash is a failed step, said with its cause, never a short report
    that reads as a pass. Base: the new name."""
    code, out, report = _planted_run(tmp_path, '''
        raise RuntimeError("planted: the section broke before its first step")
    ''')
    rows = step_rows(report)
    print(f"\nU1 a raising section: exit {code}; rows {[(r['id'], r['result'], r['seen'][:80]) for r in rows]}")
    assert code == 1, out[-2000:]
    assert [r["result"] for r in rows] == ["FAIL"], rows
    assert "RuntimeError" in rows[0]["seen"] and "planted: the section broke" in rows[0]["seen"], rows
    assert "planted.1" in last_lines(report), last_lines(report)


def test_u1_a_required_step_that_could_not_run_fails_the_run_and_one_not_required_does_not(tmp_path):
    """Base: the new name."""
    required = tmp_path / "required"
    required.mkdir()
    code, out, report = _planted_run(required, '''
        run.not_run("operator", "a planted step whose precondition is absent", "none",
                    expected="the precondition holds", reason="planted: the precondition is absent here")
    ''')
    rows = step_rows(report)
    print(f"\nU1 required NOT RUN: exit {code}; rows {[(r['id'], r['result'], r['why']) for r in rows]}; "
          f"last lines {last_lines(report)!r}")
    assert code == 1, out[-2000:]
    assert rows and rows[0]["result"] == "NOT RUN" and "planted: the precondition is absent here" in rows[0]["why"]
    tail = last_lines(report)
    assert "planted.1" in tail and "required" in tail and "not run" in tail.lower(), tail
    optional = tmp_path / "optional"
    optional.mkdir()
    code, out, report = _planted_run(optional, '''
        run.not_run("operator", "a planted step that is not required", "none", expected="nothing",
                    reason="planted: not applicable here", required=False)
        run.check("operator", "a planted step that holds", "none", expected="ok", ok=True, seen="ok")
    ''')
    rows = step_rows(report)
    print(f"U1 optional NOT RUN: exit {code}; rows {[(r['id'], r['result'], r['why']) for r in rows]}")
    assert code == 0, out[-2000:]
    assert [r["result"] for r in rows] == ["NOT RUN", "PASS"], rows


# ---------------------------------------------------------------------------
# D22 — the report at its default path is not left in a checkout
# ---------------------------------------------------------------------------

def _script_module():
    sys.path.insert(0, str(REPO / "scripts"))
    import uat_simulate
    return uat_simulate


def test_d22_a_report_at_the_default_path_is_ignored_and_leaves_git_status_as_it_was(tmp_path):
    """The script writes its report at the default path (a planted section,
    no --report), and git status is what it was. A report already there (an
    operator's) is never overwritten or removed: it is checked as it is.
    Base: the new name; its consequence (the report shown by git status)
    with the script written and no ignore rule: SP/w4/p4/prefix_d22.txt."""
    u = _script_module()
    default = u.DEFAULT_REPORT
    assert default.parent == REPO / "reports" and default.name == f"uat_simulation_{sys.platform}.md", default
    snapshot = porcelain()
    assert snapshot["state"] == "ok", snapshot
    ours = not default.exists()
    made_folder = not default.parent.is_dir()      # W11c: the public tree has no reports/; leave none behind
    try:
        if ours:
            wrapper = tmp_path / "default_report.py"
            wrapper.write_text(textwrap.dedent(f'''
                import sys
                sys.path.insert(0, {str(REPO / "scripts")!r})
                import uat_simulate as u
                def planted(run):
                    run.check("operator", "a planted step", "none", expected="ok", ok=True, seen="ok")
                u.SECTIONS = u.SECTIONS + (("planted", "a planted section", planted),)
                sys.exit(u.main(["--sections", "planted", "--work", {str(tmp_path / "work")!r}]))
            '''), encoding="utf-8")
            proc = _proc.run([sys.executable, str(wrapper)], cwd=REPO, env=_run_env(), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=300)
            assert proc.returncode == 0 and default.is_file(), proc.stdout + proc.stderr
        ignored = _proc.run(["git", "check-ignore", "-q", str(default)], cwd=REPO, timeout=120).returncode == 0
        cmp = compare(snapshot, porcelain())
    finally:
        if ours:
            default.unlink(missing_ok=True)
        if made_folder and default.parent.is_dir() and not any(default.parent.iterdir()):
            default.parent.rmdir()
    print(f"\nD22 ({sys.platform}) {default.relative_to(REPO).as_posix()}: written by this test {ours}; "
          f"ignored {ignored}; status {cmp['verdict']} {cmp['added']}")
    assert ignored, f"{default.relative_to(REPO).as_posix()} is not ignored by git"
    assert cmp["verdict"] == UNCHANGED, describe(cmp)


# ---------------------------------------------------------------------------
# D20 — no store module, no store
# ---------------------------------------------------------------------------

def test_d20_the_script_is_no_freeze_driver_and_opens_no_store():
    """Both populations derived by their own guards' functions. Base: the
    new name."""
    from tests._fz_trace import _STORE_MODULES, _imports, store_scripts
    from tests.test_w1_migrated_scripts_smoke import migrated_scripts
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    drivers = {s["name"] for s in store_scripts()}
    print(f"\nD20 store modules the script imports: {sorted(_imports(tree) & set(_STORE_MODULES))}; "
          f"freeze drivers {len(drivers)}")
    assert SCRIPT.stem not in drivers and not _imports(tree) & set(_STORE_MODULES)
    assert SCRIPT.name not in migrated_scripts()


def test_d20_companion_a_store_import_planted_in_a_copy_is_found():
    """The plant is a store module the test's own rule above does not name;
    the derivation's import check finds it."""
    from tests._fz_trace import _STORE_MODULES, _imports
    planted = SCRIPT.read_text(encoding="utf-8") + "\n\nfrom backend.memory.lineage import LineageStore\n"
    found = _imports(ast.parse(planted)) & set(_STORE_MODULES)
    assert found == {"backend.memory.lineage"}, found
