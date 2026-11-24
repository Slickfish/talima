"""Packet M.4 — a script's captured output must be readable.

THE FAILURE THIS EXISTS FOR, AND WHAT IT WAS NOT
================================================
On the operator's Windows machine, eight tests failed at once:

    UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97 in position 90
      in subprocess.py _readerthread  ->  captured output is None

`0x97` is the cp1252 em dash. Scripts print em dashes; when stdout is a PIPE,
CPython encodes it with the platform default, so the child wrote one cp1252
byte. The parent decoded that pipe as UTF-8 with the default
`errors="strict"`, the reader thread raised, `subprocess.run` joined it and
returned anyway, and `proc.stdout` came back `None`. Every assertion touching
it then failed with `TypeError`/`AttributeError`.

**NO SAFETY ASSERTION FAILED.** Eight red tests in an archiving packet invite
a much worse assumption than the truth, so the truth is recorded here: in
`test_dry_run_writes_nothing_to_the_store`, `kb.read_bytes() == before`
PASSED, the md5s matched, and `archived_ids == []`. The scripts were correct
and remained correct on both platforms. What broke was the tests' ability to
READ what the scripts print — a reporting channel, not a behaviour.

WHY THIS FILE CAN TEST A WINDOWS BUG FROM LINUX
===============================================
`PYTHONIOENCODING=cp1252` in the child's environment reproduces the exact
byte. That is the whole failure, on any platform, and it means these guards
are not documentation waiting for a Windows run — they fail here today if the
fix is removed.

BOTH ENDS, AND WHY NEITHER IS SUFFICIENT
========================================
  writer  `scripts/_console.py::force_utf8_stdio()` makes the child emit
          UTF-8 regardless of console or locale. Without it, output is
          correct-but-mojibake under `errors="replace"`.
  reader  `errors="replace"` on every capture. Without it, one stray byte
          from any *future* script, or from a tool this repo does not own,
          is fatal again.
"""
from __future__ import annotations

import os
from tests import _proc
import sys
from pathlib import Path

import pytest
from tests._tracked import tracked

REPO = Path(__file__).resolve().parent.parent


# U.2 — `_tracked_scripts()` lived here and was the ONLY git-sourced
# enumerator in the suite. Keeping a private copy is how the next
# enumerator came to glob the directory again six weeks later, so the
# implementation moved to `tests/_tracked.py` and this imports it.
# M.5's reasoning, unchanged, now lives in that module.
SCRIPTS = tracked("scripts/*.py")   # M.5's answer, now shared (U.2)

# The character that caused it: U+2014 EM DASH, which cp1252 encodes as the
# single byte 0x97 and UTF-8 encodes as three bytes.
EM_DASH = "—"


def _run(argv, *, env=None, cwd=REPO):
    return _proc.run(argv, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          cwd=cwd, env=env, timeout=120)


# ---------------------------------------------------------------------------
# The failure, reproduced — and the fix, demonstrated
# ---------------------------------------------------------------------------

def test_a_cp1252_child_would_kill_a_strict_reader(tmp_path):
    """NON-VACUITY for everything below: the byte really is fatal.

    A child forced to cp1252 output, read with `errors="strict"` — the
    default, and what every capture in this repo used before M.4.

    THE SYMPTOM DIFFERS BY PLATFORM AND THE DIFFERENCE MATTERS, because it is
    why this arrived as eight confusing failures rather than one obvious one:

      POSIX    `Popen._communicate` decodes inline via `_translate_newlines`,
               so `UnicodeDecodeError` propagates to the caller with a stack
               that names the real cause. Measured here.
      Windows  `Popen._communicate` reads each pipe on a `_readerthread`. The
               exception dies inside that thread, `run()` joins it and returns
               normally, and `proc.stdout` is `None`. The traceback the
               operator saw named `_readerthread`, and every downstream
               assertion then failed with `TypeError` on a `None`.

    So the assertion was on the CLASS — strict decoding of this byte cannot
    succeed — rather than on either platform's particular way of losing.

    W4b: the child is started through tests/_proc.py, which decodes on its
    own drain threads and raises the strict decoding error from the call on
    every platform (POSIX subprocess's way), so `raised:0x97` is now the
    only outcome; the Windows `None` above is subprocess's, kept as the
    history of why the class matters.
    """
    script = tmp_path / "emit.py"
    script.write_text(f'print("head {EM_DASH} tail")\n', encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="cp1252")

    outcome = None
    try:
        proc = _proc.run([sys.executable, str(script)],
                         capture_output=True, text=True,
                         # ratchet: strict-decode-is-the-point — this
                         # call MUST omit errors=, because omitting it
                         # is the defect under reproduction.
                         encoding="utf-8",
                         cwd=tmp_path, env=env, timeout=60)
        outcome = proc.stdout
    except UnicodeDecodeError as e:
        outcome = f"raised:{e.object[e.start]:#04x}"

    assert outcome == "raised:0x97", (
        f"the strict reader survived a cp1252 em dash, so this test no longer "
        f"reproduces the Windows failure: {outcome!r}")


def test_errors_replace_keeps_the_output_readable(tmp_path):
    """The reader-side half of the fix, against the same child."""
    script = tmp_path / "emit.py"
    script.write_text(f'print("head {EM_DASH} tail")\n', encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="cp1252")

    proc = _run([sys.executable, str(script)], env=env, cwd=tmp_path)

    assert proc.stdout is not None, "capture still died"
    assert "head" in proc.stdout and "tail" in proc.stdout, (
        f"the surrounding text was lost, not just the character: "
        f"{proc.stdout!r}")


def test_force_utf8_stdio_makes_the_child_emit_utf8(tmp_path):
    """The writer-side half. With it, the character SURVIVES rather than
    being replaced — which is the difference between output a test can assert
    on and output an operator can only squint at."""
    script = tmp_path / "emit.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, r{str(REPO / 'scripts')!r})\n"
        "from _console import force_utf8_stdio\n"
        "force_utf8_stdio()\n"
        f'print("head {EM_DASH} tail")\n', encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="cp1252")

    proc = _run([sys.executable, str(script)], env=env, cwd=tmp_path)

    assert EM_DASH in proc.stdout, (
        f"force_utf8_stdio() did not override the forced cp1252 output: "
        f"{proc.stdout!r}")
    assert "�" not in proc.stdout, "characters were replaced, not preserved"


# ---------------------------------------------------------------------------
# The real scripts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("script", [p for p in SCRIPTS if not p.name.startswith("_")],
                         ids=lambda p: p.name)
def test_every_script_calls_force_utf8_stdio(script):
    """Scoped to `main()`'s own call graph, not a grep over the file.

    An import alone does nothing, so a script that imports the helper and
    forgets to call it must still fail — a file-wide search for the name
    would pass on the import line and prove nothing. The AST is parsed and
    only `Call` nodes inside `main()` count.

    Honest limit: this reads structure, not behaviour. It cannot catch a call
    placed after the first `print`. The end-to-end guard below is what
    actually exercises the output, on real scripts, under a forced cp1252
    environment.
    """
    import ast
    tree = ast.parse(script.read_text(encoding="utf-8"))
    # T.1 — `ast.AsyncFunctionDef` is a SEPARATE node type, so this used to be
    # blind to `async def main()`. It failed closed ("has no main()"), which is
    # the safe direction, but the message named the wrong defect — and the
    # first async script in the repo is what found it.
    main = next((n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == "main"), None)
    assert main is not None, f"{script.name} has no main()"
    called = [n.func.id for n in ast.walk(main)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "force_utf8_stdio" in called, (
        f"{script.name}'s main() does not call force_utf8_stdio(), so its "
        f"piped output is encoded with the platform default and a UTF-8 "
        f"reader will fail on the first em dash it prints")


@pytest.mark.parametrize("script", [
    "verify_packet_k.py", "archive_consolidation.py",
])
def test_a_real_scripts_captured_output_is_decodable(tmp_path, script):
    """End to end, on a real script, with a forced cp1252 environment.

    `--help` is used deliberately: it prints the module docstring, which is
    where this repo's em dashes live, and it touches no store.
    """
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    proc = _run([sys.executable, str(REPO / "scripts" / script), "--help"],
                env=env)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout is not None, (
        f"{script} --help produced an undecodable stream")
    # NON-VACUITY: if the help text were pure ASCII this would prove nothing.
    assert EM_DASH in proc.stdout, (
        f"{script}'s help text contains no em dash, so this test is not "
        f"exercising the failing byte any more")
    assert "�" not in proc.stdout, (
        f"{script}'s output needed replacement characters — the writer-side "
        f"fix is not in effect: {proc.stdout[:200]!r}")


def test_the_scripts_print_the_byte_that_caused_this(tmp_path):
    """NON-VACUITY across the whole packet: the repo's scripts really do emit
    the character that cp1252 renders as 0x97. If they stopped, every guard in
    this file would pass while protecting nothing."""
    offenders = {p.name: p.read_text(encoding="utf-8").count(EM_DASH)
                 for p in SCRIPTS
                 if EM_DASH in p.read_text(encoding="utf-8")}
    assert offenders, (
        "no script contains an em dash — these guards are now vacuous")
