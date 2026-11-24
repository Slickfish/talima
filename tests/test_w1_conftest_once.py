"""W1 — the suite's conftest is one module, loaded once.

`tests/` is a package, so pytest loads `tests/conftest.py` as `tests.conftest`.
Before W1, `tests/test_derived_writer_guard.py` imported it again as a bare
`conftest` (twice). That executed the whole module body a second time: a
second `mkdtemp` session directory, and `NEXUS_CALIBRATION_LOG`,
`NEXUS_CITATION_CAPTURE_PATH` and `NEXUS_FIRE_CAPTURE_PATH` re-pointed at it.
The data stayed out of the repo, but every later test's
`tests.conftest.SESSION_TMP` disagreed with the environment it ran in, and
`tests/test_cp_fire_capture.py::test_the_suite_redirect_is_live` failed in any
order that ran the writer guard first (measured on `a2c1ab8`: derived writer
guard, then citation capture, then fire capture, 1 failed). The suite's
alphabetical order hid it.

G   No tracked test module imports conftest by its bare name, or loads it by
    name or by path through a loader call (`importlib.import_module`,
    `__import__`, `runpy.run_path`, `spec_from_file_location`, ...; W1's
    verifier, F11), derived by AST over every tracked test file. The
    subprocess strings that import it in a child process are not imports of
    this module and are not seen.
C   Companions: the two planted forms the guard's source does not spell out
    as literals in its check (`import conftest as c`, `from conftest import
    SESSION_TMP`) are named.
B   Behaviour: conftest.py is loaded as exactly one module in this process,
    and (F11: in the shared process the check depended on test order) in a
    child session that runs the writer guard and then a probe. The probe's
    verdict is read from the child's JUnit report with its rootdir fixed at
    the repo (CI run 20, windows: read from a node id's file name, it was
    missed where the node id had none); the companion plants a failing probe
    in that condition.
    (Asserting where the environment's log variables point here would be
    order-dependent: it was, and it named a second defect, a module fixture
    in tests/test_just_culture_guard.py that never restored them. conftest
    now checks every module's end for that, `_redirects_restored_per_module`;
    L below is its companion.)
L   A changed redirect is named by the per-module check's function, and
    (F10, the consequence) a child session over two planted modules errors
    at the first module's end, naming it, while the second sees the
    original value.
"""
from __future__ import annotations

import ast
import os
from tests import _proc
import sys
from pathlib import Path

import pytest

from tests._tracked import tracked

BARE = "conftest"
ROOT = Path(__file__).resolve().parent.parent
CONFTEST = Path(__file__).resolve().parent / "conftest.py"
# The calls that load a module by name or by path.
LOADERS = {"import_module", "__import__", "run_path", "run_module",
           "spec_from_file_location", "SourceFileLoader", "load_source"}


def _names_conftest(node) -> bool:
    return any(isinstance(c, ast.Constant) and isinstance(c.value, str)
               and (c.value == BARE or c.value.endswith("conftest.py"))
               for c in ast.walk(node))


def bare_conftest_imports(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            lines += [node.lineno for a in node.names if a.name == BARE]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == BARE:
            lines.append(node.lineno)
        elif isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1] in LOADERS \
                and any(_names_conftest(a) for a in [*node.args, *(k.value for k in node.keywords)]):
            lines.append(node.lineno)
    return lines


def test_g_no_test_imports_conftest_by_its_bare_name():
    hits = {}
    for path in tracked("tests"):
        if path.suffix != ".py":
            continue
        found = bare_conftest_imports(path.read_text(encoding="utf-8"))
        if found:
            hits[path.as_posix()] = found
    assert not hits, (
        f"bare `import conftest` re-executes the suite's conftest as a second "
        f"module (a second SESSION_TMP, the log variables re-pointed): {hits}. "
        f"Use `from tests import conftest`.")


def test_c_companions_the_planted_forms_are_named():
    for planted in ("import conftest as c\n", "from conftest import SESSION_TMP\n",
                    "def f():\n    import os, conftest\n",
                    # F11: loading it by name or by path
                    "import importlib\nc = importlib.import_module('conftest')\n",
                    "c = __import__('conftest')\n",
                    "import runpy\nns = runpy.run_path('tests/conftest.py')\n",
                    "import importlib.util as u\nfrom pathlib import Path\n"
                    "spec = u.spec_from_file_location('c', Path(__file__).parent / 'conftest.py')\n"):
        assert bare_conftest_imports(planted), planted
    assert not bare_conftest_imports("from tests import conftest\n")
    assert not bare_conftest_imports("from tests.conftest import SESSION_TMP\n")


PROBE = "test_probe_conftest_loaded_once"
PROBE_BODY = (
    "import sys\nfrom pathlib import Path\n"
    f"CONFTEST = Path({str(CONFTEST)!r}).resolve()\n"
    f"def {PROBE}():\n"
    "    loaded = sorted(n for n, m in list(sys.modules.items())\n"
    "                    if getattr(m, '__file__', None) and Path(m.__file__).resolve() == CONFTEST)\n"
    "    assert loaded == ['tests.conftest'], loaded\n")


def _junit_verdicts(report: Path) -> dict[str, list[str]]:
    """{test name: [verdict, ...]} from a JUnit report: passed, or the kind
    of each failure, error or skip the report records for the test."""
    import xml.etree.ElementTree as ET
    out: dict[str, list[str]] = {}
    for case in ET.parse(report).getroot().iter("testcase"):
        kinds = [child.tag for child in case if child.tag in ("failure", "error", "skipped")]
        out.setdefault(case.get("name"), []).extend(kinds or ["passed"])
    return out


def _child_session(tmp_path: Path, probe_body: str) -> tuple[dict[str, list[str]], str]:
    """The writer guard and then a probe, in one child pytest. Returns the
    probe's verdicts by test name, read from the session's JUnit report, and
    the session's output.

    CI run 20, windows: the verdict was read from the short summary by the
    probe's file name in its node id. A node id carries the file only when
    the file lies under the session's rootdir, and the rootdir was left to
    pytest: the arguments' common ancestor, `/` on POSIX, so every node id
    named its file. On windows the temp dir (C:) and the checkout (D:) have
    no common ancestor, the rootdir fell back to the checkout, and the probe
    outside it was reported as `PASSED ::test_probe`: it passed and the test
    found no line. The rootdir is now fixed at the repo, so the probe lies
    outside it on every platform, and the verdict is read from the report's
    structure, which does not depend on how a node id is spelled."""
    probe = tmp_path / "test_probe_conftest_once.py"
    probe.write_text(probe_body, encoding="utf-8")
    report = tmp_path / "child-junit.xml"
    proc = _proc.run(
        [sys.executable, "-m", "pytest", "tests/test_derived_writer_guard.py", str(probe), "-q",
         "-p", "no:cacheprovider", "--rootdir", str(ROOT), "--basetemp", str(tmp_path / "bt"),
         "--junitxml", str(report), "-rA"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    verdicts = _junit_verdicts(report) if report.exists() else {}
    return verdicts, proc.stdout + proc.stderr


def _assert_the_probe_passed(verdicts: dict[str, list[str]], output: str) -> None:
    # The probe's verdict alone: the writer guard's own tests are the main
    # suite's to judge.
    assert verdicts.get(PROBE) == ["passed"], (
        f"the probe's verdicts: {verdicts.get(PROBE)}\n{output[-2500:]}")


def test_b_in_a_child_session_after_the_writer_guard_conftest_is_still_one_module(tmp_path):
    """F11: the same claim, in a session whose order is fixed: the writer
    guard (the module that loaded conftest a second time before W1) and then
    a probe, in one child pytest."""
    verdicts, output = _child_session(tmp_path, PROBE_BODY)
    print(f"B child session: {output.strip().splitlines()[-1] if output.strip() else ''}; "
          f"probe: {verdicts.get(PROBE)}")
    _assert_the_probe_passed(verdicts, output)


def test_b_companion_a_failing_probe_outside_the_rootdir_fails_the_claim(tmp_path):
    """COMPANION (OQ-16 rules 1 and 3; CI run 20, windows): the same session
    with a probe that fails. Windows' condition holds on every platform now,
    planted rather than waited for: the probe lies outside the child's
    rootdir, so its node id has no file part, and the old reader's pattern
    appears nowhere in the output. The verdict is still read, failed, and
    the claim's own check fails on it.

    Pre-fix (law 16): the old reader, under the same condition (the rootdir
    fixed at the repo, the only change), found no line for a probe that
    passed: `probe: []`, `assert ([])`, the failure CI's windows log shows
    (RUN_LOG.md, W1)."""
    failing = PROBE_BODY.replace("['tests.conftest']", "['not.the.conftest']")
    verdicts, output = _child_session(tmp_path, failing)
    print(f"B companion: probe verdicts {verdicts.get(PROBE)}")
    assert "test_probe_conftest_once.py::" not in output, (
        "the probe's node id names its file: windows' condition is not planted here")
    assert f"::{PROBE}" in output, "the probe did not run"
    assert verdicts.get(PROBE) == ["failure"], output[-2500:]
    with pytest.raises(AssertionError, match="the probe's verdicts: \\['failure'\\]"):
        _assert_the_probe_passed(verdicts, output)


def test_b_conftest_is_loaded_as_one_module():
    loaded = [name for name, m in sys.modules.items()
              if getattr(m, "__file__", None)
              and Path(m.__file__).resolve() == (Path(__file__).resolve().parent / "conftest.py")]
    assert loaded == ["tests.conftest"], (
        f"conftest.py is loaded as {loaded}: a second load re-ran its body "
        f"(a second SESSION_TMP, the redirects re-pointed)")


def test_l_a_changed_redirect_is_named(monkeypatch, tmp_path):
    from tests import conftest
    before = conftest.redirect_snapshot()
    assert conftest.redirect_changes(before) == []
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "elsewhere.jsonl"))
    monkeypatch.delenv("NEXUS_FIRE_CAPTURE_PATH")
    changes = conftest.redirect_changes(before)
    assert [c.split("=")[0] for c in changes] == ["NEXUS_CALIBRATION_LOG",
                                                 "NEXUS_FIRE_CAPTURE_PATH"], changes


def test_l_the_consequence_a_leaking_module_errors_and_the_next_is_clean(tmp_path):
    """F10 (OQ-16 rule 3): not the helper's output, the suite's: one module
    leaks a redirect, the per-module check errors at that module's end and
    names it, and the module after it sees the original value."""
    leak = tmp_path / "test_leak_a.py"
    clean = tmp_path / "test_leak_b.py"
    leak.write_text("import os\ndef test_a():\n    os.environ['NEXUS_CALIBRATION_LOG'] = 'LEAKED'\n",
                    encoding="utf-8")
    clean.write_text("import os\ndef test_b():\n    assert os.environ.get('NEXUS_CALIBRATION_LOG') != 'LEAKED'\n",
                     encoding="utf-8")
    proc = _proc.run(
        [sys.executable, "-m", "pytest", str(leak), str(clean), "-p", "tests.conftest", "-q",
         "-p", "no:cacheprovider", "--basetemp", str(tmp_path / "bt"), "-rE"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    print(f"L child session: {tail}")
    assert "2 passed, 1 error" in tail, proc.stdout[-2500:] + proc.stderr[-1500:]
    assert "test_leak_a left a writer redirect changed" in proc.stdout
    assert "ERROR at teardown of test_a" in proc.stdout
