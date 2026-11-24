"""The Windows gate of 24 November: no collected node id is longer than 1,000 characters.

THE FAILURE (the operator's gate on `b045d93`: 0 failed, 2 errors)
    `tests/test_w10c_interceptor.py::test_t7d_a_note_of_tool_uses_that_cannot_be_read_is_named_not_raised` was
    parametrized with `"[" * 100000 + "]" * 100000` and no ids. The pytest on the operator's machine spells a
    string parameter's id as the string itself, so that case's node id was 200,102 characters. pytest puts the
    running test's node id in the environment variable PYTEST_CURRENT_TEST at each stage, and Windows caps an
    environment variable at 32,767 characters: the case errored at setup and at teardown, on Windows only. A newer
    pytest (the sandbox's, 9.2.0) replaces a string value over 100 characters by the argument's name and index
    unless it is told otherwise (`parametrize_long_str_id_strategy`, `short` by default), which is why the
    sandbox's runs never made that id: on that pytest the same case is `[note6]`.

WHAT IS HELD HERE
    N1  pytest's own collection of this checkout's suite, made in a child pytest with ids spelled as an older
        pytest spells them (the `legacy` strategy, asked for where the running pytest has the setting; a pytest
        without the setting spells them so already): no node id of a tracked test file is longer than LIMIT.
        The population is pytest's and nobody else's: a parameter's id is made by pytest from the value, from the
        ids given and by its own version's rules, and no reading of the source gives it.
    N2  Companions. Each plants a test file, collects it the same way, and the guard's own check must FAIL on it,
        naming the planted case and not its short neighbour: the gate's own case (a long string and no ids); an
        id given explicitly; short values joined into one id; a class's long name. The last three are long
        whatever the strategy, so they are the class on every pytest.
    N3  The boundary: a node id of exactly LIMIT characters passes, and one character more fails.
    N4  The reading itself: the node ids read from pytest's output are held to pytest's own count (a line lost, or
        no count at all, is refused and nothing is built on it; an id that holds `::` and a path counts once), and
        an untracked file's ids are left out by the file they belong to.
    N5  The child's lines are pytest's and not the caller's shell's: options added through PYTEST_ADDOPTS do not
        reach it, and colour forced in the environment is stripped by the reader.
    N6  A planted folder is its own root: a configuration file above the temporary folder does not move its ids.
    N7  A pytest that has the setting and refuses the value asked for (pytest calls `legacy` temporary) is read
        with its own default, the reading says it was not as asked, and N2's first companion, which then has no
        case to plant, is skipped by name. Played with a value no pytest has; the skip itself is made here, by
        N2's own body given the refusal, and a skip of any companion that has its case fails here.
    N8  When this file itself is not tracked, N1 says that, and not that the collection is another suite's.

WHAT IS NOT HELD HERE
    An untracked scratch test file on one machine is not the repository's suite (`tests/_tracked.py`'s rule): its
    ids are left out, and printed. And the ids are the ones the pytest that runs this test makes: a pytest that
    spells an id some third way is not asked.

LAW 16. Seen failing at `b045d93`, before the fix: N1 named `test_t7d_...` at 200,102 characters and nothing else
(the next longest id of the suite is 600). The fix is that test's explicit ids; its nested case is `nested-200000`.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

from tests import _proc
from tests._tracked import REPO, tracked

LIMIT = 1000
STRATEGY = "parametrize_long_str_id_strategy"
COLLECTED = re.compile(r"^(?:=+ )?(\d+) tests? collected in ", re.M)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
HERE = Path(__file__).resolve().relative_to(REPO).as_posix()


def as_an_older_pytest(config) -> list:
    """The option that makes this pytest spell a long string value's id in full, as pytest did before it had the
    setting; nothing on a pytest that has no such setting (it spells them in full already)."""
    try:
        config.getini(STRATEGY)
    except (ValueError, KeyError):
        return []
    return ["-o", f"{STRATEGY}=legacy"]


def _is_node_id(line: str) -> bool:
    head, sep, _rest = line.partition("::")
    return bool(sep) and head.endswith(".py") and " " not in head


def collected_node_ids(target: str, cwd: Path, tmp: Path, options: list) -> tuple:
    """(pytest's own node ids for `target`, whether they are spelled as asked), collected in a child pytest
    started in `cwd`. The lines read are held to pytest's own count, or the output was not read as pytest wrote it
    and nothing is built on it. The child's temporary folder is the test's, so nothing is left behind, and the
    options a caller's shell adds to every pytest (PYTEST_ADDOPTS: `-v` writes other lines) do not reach it. A
    pytest that has the setting and refuses the value in `options` is read with its own default, and the second
    value says so."""
    env = dict(os.environ, TMPDIR=str(tmp), TEMP=str(tmp), TMP=str(tmp))
    env.pop("PYTEST_ADDOPTS", None)
    argv = [sys.executable, "-m", "pytest", target, "--collect-only", "-q", "-p", "no:cacheprovider"]

    def child(extra: list):
        return _proc.run([*argv, *extra], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=1200)
    proc, as_asked = child(options), True
    if proc.returncode != 0 and options and STRATEGY in proc.stdout + proc.stderr:
        print(f"this pytest refused {' '.join(options)}; collected with its own default")
        proc, as_asked = child([]), False
    assert proc.returncode == 0, \
        f"the child collection exited {proc.returncode}:\n{proc.stdout[-3000:]}\n{proc.stderr[-1500:]}"
    return read_collection(proc.stdout), as_asked


def read_collection(text: str) -> list:
    """The node ids in a collection's output, one a line, held to pytest's own count line. Colour, which a
    caller's environment can force on the child, is taken out first."""
    text = ANSI.sub("", text)
    ids = [line for line in text.splitlines() if _is_node_id(line)]
    said = COLLECTED.search(text)
    assert said and int(said.group(1)) == len(ids), \
        f"read {len(ids)} node ids and pytest's own line says {said.group(0) if said else 'nothing'}"
    return ids


def population_problem(files: set, heads: set) -> "str | None":
    """Why what was collected is not this checkout's suite; None when it is."""
    if HERE not in files:
        return (f"{HERE} is not tracked by git (git add it): the guard reads the tracked test files, and it is "
                "one of them")
    if HERE not in heads or len(heads) <= 150:
        why = ("" if heads else " (no id is under a tracked path: a configuration file above the checkout makes its "
               "own folder pytest's root, and every id then begins with the path between them)")
        return (f"the collection is not this suite's: {len(heads)} tracked file(s) collected from, {HERE} "
                f"{'among them' if HERE in heads else 'not among them'}{why}")
    return None


def of_tracked(ids: list, files: set) -> list:
    """The node ids that belong to a tracked test file: a scratch file on one machine is not the suite."""
    return [i for i in ids if i.partition("::")[0] in files]


def too_long(ids: list, limit: int = LIMIT) -> list:
    """Each node id over the limit, as its length and its first 120 characters: the whole of one is what a
    console line, and an environment variable, cannot hold."""
    return [f"{len(i)} characters: {i[:120]}" for i in ids if len(i) > limit]


def hold(ids: list, limit: int = LIMIT) -> None:
    """The guard's check: fail, naming each, if a collected node id is longer than the limit."""
    long = too_long(ids, limit)
    assert not long, (
        f"{len(long)} collected node id(s) longer than {limit} characters. pytest sets PYTEST_CURRENT_TEST to the "
        "running test's node id, and Windows refuses an environment variable over 32,767 characters; give the "
        "parametrize explicit short ids: " + "; ".join(long))


def test_n1_no_collected_node_id_is_longer_than_the_limit(request, tmp_path):
    files = {p.relative_to(REPO).as_posix() for p in tracked("tests")}
    options = as_an_older_pytest(request.config)
    ids, as_asked = collected_node_ids("tests", REPO, tmp_path, options)
    ours = of_tracked(ids, files)
    scratch = sorted({i.partition("::")[0] for i in ids} - files)
    heads = {i.partition("::")[0] for i in ours}
    problem = population_problem(files, heads)
    assert problem is None, problem
    longest = max(ours, key=len)
    spelled = " ".join(options) if as_asked else "this pytest's own default: it refused the older spelling"
    print(f"\nN1 ({sys.platform}, pytest {pytest.__version__}, {spelled or 'no strategy setting'}): "
          f"{len(ours)} node ids from {len(heads)} tracked files; the longest is {len(longest)} characters "
          f"({longest[:80]}); untracked files left out: {scratch}")
    hold(ours)


# --- N2, N3: the companions --------------------------------------------------------------------------------------

NAMES = ", ".join(f"a{n}" for n in range(12))
PLANTS = {
    "a long string and no ids, the gate's own case":
        '@pytest.mark.parametrize("note", ["5", "[" * 600 + "]" * 600])\n'
        'def test_planted(note):\n    pass\n',
    "an id given explicitly":
        '@pytest.mark.parametrize("note", ["5", "6"], ids=["short", "x" * 1001])\n'
        'def test_planted(note):\n    pass\n',
    "short values joined into one id":
        f'@pytest.mark.parametrize("{NAMES}", [("5",) * 12, ("v" * 90,) * 12])\n'
        f'def test_planted({NAMES}):\n    pass\n',
    "the long name of a class":
        '@pytest.mark.parametrize("kind", [int, type("K" * 1001, (), {})])\n'
        'def test_planted(kind):\n    pass\n',
}


def has_no_case(as_asked: bool, what: str) -> bool:
    """Whether a companion has nothing to plant on this pytest: only the gate's own case, and only on a pytest
    that refused the older spelling (it then gives a long string a short id, whatever is asked)."""
    return not as_asked and what.endswith("the gate's own case")


def write_planted(folder: Path, body: str) -> None:
    """One planted test file in a folder that is its own root: its own configuration file, so that one above it
    (in the temporary folder's parents, on somebody's machine) cannot make itself the root and lengthen every id."""
    folder.mkdir(parents=True)
    (folder / "pytest.ini").write_text("[pytest]\n", encoding="utf-8", newline="")
    (folder / "test_planted.py").write_text("import pytest\n\n\n" + body, encoding="utf-8", newline="")


def planted(folder: Path, body: str, config) -> tuple:
    write_planted(folder, body)
    return collected_node_ids("test_planted.py", folder, folder, as_an_older_pytest(config))


def companion_fails(ids: list, as_asked: bool, what: str) -> None:
    """N2's body: the guard's own check fails on a planted file's two ids, naming the planted case and not its
    short neighbour. Skipped by name where this pytest cannot make the case (has_no_case)."""
    assert len(ids) == 2, ids
    if has_no_case(as_asked, what):
        pytest.skip("this pytest refuses the older spelling: it cannot spell a long string's id in full, so the "
                    "gate's own case cannot arise on it")
    with pytest.raises(AssertionError) as refused:
        hold(ids)
    said = str(refused.value)
    assert "1 collected node id(s) longer than 1000 characters" in said and "test_planted" in said, said
    short = min(ids, key=len)
    assert len(short) < 60 and too_long([short]) == [], "the short neighbour is not named"


@pytest.mark.parametrize("what", sorted(PLANTS), ids=[w.partition(",")[0].replace(" ", "-") for w in sorted(PLANTS)])
def test_n2_companions_a_planted_long_id_fails_the_guards_own_check(request, tmp_path, what):
    ids, as_asked = planted(tmp_path / "planted", PLANTS[what], request.config)
    companion_fails(ids, as_asked, what)


def test_n3_the_boundary_is_the_limit_itself(request, tmp_path):
    room = LIMIT - len("test_planted.py::test_planted[]")
    body = (f'@pytest.mark.parametrize("note", ["5", "6"], ids=["a" * {room}, "b" * {room + 1}])\n'
            'def test_planted(note):\n    pass\n')
    ids, _as_asked = planted(tmp_path / "planted", body, request.config)
    assert sorted(len(i) for i in ids) == [LIMIT, LIMIT + 1], [len(i) for i in ids]
    named = too_long(ids)
    assert len(named) == 1 and named[0].startswith(f"{LIMIT + 1} characters: ") and "[bbb" in named[0], named
    hold([i for i in ids if len(i) == LIMIT])
    with pytest.raises(AssertionError):
        hold(ids)


TOY = "\n".join([
    "tests/test_a.py::test_one",
    "tests/test_a.py::test_p[tests/test_b.py::fake]",
    "tests/test_b.py::test_two[" + "x" * 2000 + "]",
    "",
    "3 tests collected in 0.42s",
    "",
])


def test_n4_the_collection_is_read_as_pytest_wrote_it_and_an_untracked_file_is_left_out():
    ids = read_collection(TOY)
    assert len(ids) == 3 and len(too_long(ids)) == 1
    framed = TOY.replace("3 tests collected in 0.42s", "===== 3 tests collected in 61.20s (0:01:01) =====")
    assert len(read_collection(framed)) == 3
    for broken in (TOY.replace("tests/test_a.py::test_one\n", ""), TOY.replace("3 tests collected in 0.42s", ""),
                   TOY.replace("3 tests collected", "4 tests collected")):
        with pytest.raises(AssertionError):
            read_collection(broken)
    kept = of_tracked(ids, {"tests/test_a.py"})                    # tests/test_b.py is someone's scratch file
    assert kept == ids[:2] and too_long(kept) == []
    with pytest.raises(AssertionError):
        hold(of_tracked(ids, {"tests/test_a.py", "tests/test_b.py"}))


BOUNDARY_BODY = ('@pytest.mark.parametrize("note", ["5", "6"], ids=["a" * {room}, "b" * {more}])\n'
                 'def test_planted(note):\n    pass\n')


def boundary_body() -> str:
    room = LIMIT - len("test_planted.py::test_planted[]")
    return BOUNDARY_BODY.format(room=room, more=room + 1)


def test_n5_the_callers_colour_and_added_options_do_not_reach_the_reading(request, tmp_path, monkeypatch):
    """The verifier's V2 and V3: with colour forced, pytest's count line is wrapped in escapes and was read as
    nothing; with `-v` added through PYTEST_ADDOPTS the ids were not one a line. The child is started without
    the added options, and the reader strips the colour."""
    for name, value in (("FORCE_COLOR", "1"), ("PY_COLORS", "1"), ("PYTEST_ADDOPTS", "-v")):
        monkeypatch.setenv(name, value)
    ids, _as_asked = planted(tmp_path / "planted", boundary_body(), request.config)
    assert sorted(len(i) for i in ids) == [LIMIT, LIMIT + 1], [len(i) for i in ids]
    coloured = TOY.replace("3 tests collected in 0.42s",
                           "\x1b[32m\x1b[32m3 tests collected\x1b[0m\x1b[32m in 0.42s\x1b[0m")
    assert len(read_collection(coloured)) == 3


def test_n6_a_configuration_above_the_planted_folder_does_not_move_its_ids(request, tmp_path):
    """The verifier's V4: a pytest.ini in a folder above the temporary one made that folder the root, and every
    planted id grew by the path between them. The planted folder carries its own."""
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -v\n", encoding="utf-8")
    ids, _as_asked = planted(tmp_path / "below" / "planted", boundary_body(), request.config)
    assert sorted(len(i) for i in ids) == [LIMIT, LIMIT + 1], [len(i) for i in ids]


def test_n7_a_pytest_that_refuses_the_spelling_asked_for_is_read_by_its_own_and_says_so(request, tmp_path):
    """The verifier's V5: pytest calls `legacy` temporary. A pytest that has the setting and refuses a value is
    read with its own default, and the reading says it was not as asked; N2's first companion then has no case
    to plant and is skipped by name. Played here with a value no pytest has."""
    folder = tmp_path / "planted"
    write_planted(folder, PLANTS["a long string and no ids, the gate's own case"])
    ids, as_asked = collected_node_ids("test_planted.py", folder, folder, ["-o", f"{STRATEGY}=no-such-spelling"])
    assert len(ids) == 2
    if as_an_older_pytest(request.config):          # this pytest has the setting, and no such value
        assert as_asked is False, "the reading says the option was refused"
    else:                                           # no setting: the option is not read, and the value is spelled
        assert as_asked is True and len(too_long(ids)) == 1
    gate, other = "a long string and no ids, the gate's own case", "an id given explicitly"
    assert has_no_case(False, gate) and not has_no_case(True, gate) and not has_no_case(False, other), \
        "N2 skips the gate's own case, and no other, when the spelling was refused"
    assert gate in PLANTS and other in PLANTS
    # the skip itself (the second delta check's E10: with the `if` gone, nothing here failed): N2's body, given
    # the refusal, skips the gate's own case by name whatever ids it was handed, and skips no other companion
    with pytest.raises(pytest.skip.Exception, match="refuses the older spelling"):
        companion_fails(ids, False, gate)
    # and no other (the third delta check's F1: a body that skipped every companion ended this test as skipped,
    # which is not a failure): a skip raised where a companion has its case is turned into one
    two = ["test_planted.py::test_planted[short]", "test_planted.py::test_planted[" + "x" * 1001 + "]"]
    try:
        companion_fails(two, False, other)
        companion_fails(two, True, gate)
    except pytest.skip.Exception as skipped:
        pytest.fail(f"a companion that has its case was skipped: {skipped}")


def test_n8_a_guard_file_git_does_not_track_is_said_to_be_that(request):
    """The verifier's V6: with this file copied in and not added, N1 said the collection was not the suite's."""
    assert "is not tracked by git" in population_problem(set(), set())
    assert "not this suite's" in population_problem({HERE}, {HERE})
    many = {f"tests/test_{n}.py" for n in range(200)} | {HERE}
    assert population_problem(many, many) is None
    assert "not this suite's" in population_problem(many, many - {HERE})
    assert "above the checkout" in population_problem(many, set()), "no id under a tracked path: the root moved"
