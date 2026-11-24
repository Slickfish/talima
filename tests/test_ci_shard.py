"""CI: the suite's shard split (scripts/ci_shard.py; ci.yml's windows job).

The operator's instruction of 11 November: the windows suite runs in
parallel shards "with the same total test count as before", and "a test
that only passes in a particular order is a defect to fix at its cause, not
a reason to pin the order". The split is a script, so its rules are tested
here on every platform; the workflow's own shape is tests/test_w3_ci.py's.

S1  The script's file patterns are pytest's own `python_files`, read from
    the running pytest; every tracked file this session collected from is
    in the script's list, and the list equals the one the suite's own
    `tracked()` helper gives. Companion: a planted third pattern is named.
S2  Reading a collection: node ids are counted per file (a parametrised id
    holding `::` and a path counts once, for its own file), the count must
    equal pytest's own line, a skip reported while collecting counts one
    for its file (in the runner's separators), and a collection that did
    not finish, had errors, deselected tests or was miscounted is refused
    by name. So are a collected file git does not track and a skip at a
    path that is no test file.
S3  Over the REAL suite, collected once in a child pytest exactly as the
    workflow collects it: for K in 1, 2, 3, 4, 5, 8 the shards are disjoint,
    their union is every tracked test file, none is empty, their expected
    counts sum to the unsharded total, each is in path order, the loads
    differ by no more than the largest file, and the same input in any
    order gives the same partition. Every tracked test file is collected
    from or skipped by name (so the patterns list no file pytest ignores).
S4  Companions for the completeness check, through the partition function's
    own input: a file dropped from the input is in no shard and a file
    given twice is in two, and the split is REFUSED naming the file (exit
    2, nothing written). Hand-made partitions: an empty shard and a path
    that is no tracked test file are named.
S5  The count check: a summary line whose outcomes sum to the expected
    count is accepted (exit 0) and written into the shard's file; one short
    by one is refused (exit 1) naming both numbers; the line is the LAST of
    its shape (a child session's line printed earlier is not the run's); a
    line with errors is NOT PROVEN; an unknown word is refused; no line at
    all is a run that did not finish. The real windows line of ci run 68
    sums to 3234.
S6  The whole pipeline over the real collection, through `main`: four
    splits, four checks, the total: exit 0, and the table's sum row equals
    the unsharded total. Companions, each named and exit 1: a shard that
    did not report, a shard with no outcomes, a shard one short, shards
    that disagree on the unsharded count, and a file missing from every
    shard's list.
"""
from __future__ import annotations

import json
import os
import random
from tests import _proc
import sys
from pathlib import Path

import pytest

from scripts import ci_shard as cs
from tests._tracked import REPO, tracked

# The collection, as ci.yml's split step makes it.
COLLECT = ["tests", "--collect-only", "-q", "-rs", "-p", "no:cacheprovider"]
KS = [1, 2, 3, 4, 5, 8]


# --- S1 -----------------------------------------------------------------------------

def pattern_problems(live: list[str]) -> list[str]:
    if sorted(live) != sorted(cs.PYTHON_FILES):
        return [f"pytest's python_files is {sorted(live)}, the split lists {sorted(cs.PYTHON_FILES)}: the shards would "
                f"not run the files pytest collects"]
    return []


def test_s1_the_splits_patterns_are_the_running_pytests_own(request):
    live = list(request.config.getini("python_files"))
    print(f"S1 pytest's python_files: {live}; the split's: {list(cs.PYTHON_FILES)}")
    assert not pattern_problems(live), pattern_problems(live)


def test_s1_companion_a_third_pattern_is_named():
    planted = [*cs.PYTHON_FILES, "check_*.py"]
    named = pattern_problems(planted)
    assert len(named) == 1 and "check_*.py" in named[0], named


def test_s1_the_list_is_gits_and_holds_every_file_this_session_collected(request):
    files = cs.tracked_test_files()
    under_tests = {p.relative_to(REPO).as_posix() for p in tracked("tests")}
    by_helper = sorted(f for f in under_tests if cs.is_test_file(f))
    assert files == by_helper, sorted(set(files) ^ set(by_helper))
    assert len(files) > 150 and files == sorted(files), len(files)
    session = {item.nodeid.split("::", 1)[0] for item in request.session.items}
    assert session, "non-vacuity: this session collected nothing"
    missed = sorted((session & under_tests) - set(files))          # an untracked scratch file is not the repository's
    print(f"S1 {len(files)} tracked test files; this session collected from {len(session)}")
    assert not missed, f"pytest collected from tracked files the split does not list: {missed}"


def test_s1_a_tree_without_git_is_refused_never_guessed(tmp_path):
    with pytest.raises(cs.ShardRefused) as e:
        cs.tracked_test_files(tmp_path)
    assert "git" in str(e.value)


# --- S2 -----------------------------------------------------------------------------

TOY = "\n".join([
    "tests/test_a.py::test_one",
    "tests/test_a.py::test_p[a b]",
    "tests/test_a.py::test_p[tests/test_b.py::fake]",
    "tests/test_a.py::TestC::test_m",
    "tests/test_b.py::test_two",
    "",
    "=========================== short test summary info ============================",
    "SKIPPED [1] tests\\test_c.py:48: could not import 'playwright': No module named 'playwright'",
    "5 tests collected in 0.42s",
    "",
])
TOY_FILES = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]


def test_s2_a_collection_is_read_file_by_file():
    col = cs.parse_collection(TOY)
    assert (col.collected, col.tests, col.skips) == (5, {TOY_FILES[0]: 4, TOY_FILES[1]: 1}, {TOY_FILES[2]: 1})
    assert (col.skipped_at_collection, col.unsharded_total) == (1, 6)
    assert cs.expected_counts(TOY_FILES, col) == [(TOY_FILES[0], 4), (TOY_FILES[1], 1), (TOY_FILES[2], 1)]
    framed = TOY.replace("5 tests collected in 0.42s", "======== 5 tests collected in 61.20s (0:01:01) ========")
    assert cs.parse_collection(framed).collected == 5


@pytest.mark.parametrize("label, text, names", [
    ("it did not finish", TOY.replace("5 tests collected in 0.42s", ""), "no 'N tests collected' line"),
    ("it had errors", TOY.replace("5 tests collected in", "5 tests collected, 1 error in"), "had errors"),
    ("it deselected", TOY.replace("5 tests collected in", "4/5 tests collected (1 deselected) in"), "deselected"),
    ("a line was lost", TOY.replace("tests/test_b.py::test_two\n", ""), "counted 4 node ids"),
    ("nothing collected", "\nno tests collected in 0.01s\n", "collected nothing"),
])
def test_s2_a_collection_that_cannot_be_trusted_is_refused(label, text, names):
    with pytest.raises(cs.ShardRefused) as e:
        cs.parse_collection(text)
    assert names in str(e.value), (label, str(e.value))


def test_s2_a_collected_file_git_does_not_track_and_a_skip_at_no_test_file_are_named():
    col = cs.parse_collection(TOY)
    stranger = TOY_FILES[1]
    with pytest.raises(cs.ShardRefused) as e:                       # pytest collected from it, the list lacks it
        cs.expected_counts([TOY_FILES[0], TOY_FILES[2]], col)
    assert stranger in str(e.value) and "not a tracked test file" in str(e.value), str(e.value)
    helper = "tests/_helpers.py"
    moved = cs.parse_collection(TOY.replace("tests\\test_c.py:48", "tests\\_helpers.py:7"))
    with pytest.raises(cs.ShardRefused) as e:                       # a skip nobody can attribute to a test file
        cs.expected_counts(TOY_FILES, moved)
    assert helper in str(e.value), str(e.value)


# --- S3: the real suite -----------------------------------------------------------------

def _tracked_only(text: str, files: list[str]) -> tuple[str, list[str]]:
    """The collection with any UNTRACKED test file's lines taken out and
    pytest's count line rewritten to match: a scratch file on one machine is
    not the repository's suite (tests/_tracked.py's rule), and the split
    would, rightly, refuse it by name."""
    known, kept, dropped = set(files), [], []
    for line in text.splitlines():
        head, sep, _ = line.partition("::")
        if sep and head.endswith(".py") and " " not in head and head not in known:
            dropped.append(head)
            continue
        kept.append(line)
    if not dropped:
        return text, []
    n = sum(1 for line in kept if "::" in line and line.partition("::")[0] in known)
    kept = [f"{n} tests collected in 0.00s" if " collected" in line and "::" not in line else line for line in kept]
    return "\n".join(kept) + "\n", sorted(set(dropped))


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    """ONE unsharded collection of this checkout, in a child pytest, made as
    ci.yml's split step makes it. The child's own temporary folder is the
    test's, so nothing is left behind."""
    tmp = tmp_path_factory.mktemp("ci_shard_collect")
    env = dict(os.environ, TMPDIR=str(tmp), TEMP=str(tmp), TMP=str(tmp))
    proc = _proc.run([sys.executable, "-m", "pytest", *COLLECT], cwd=REPO, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=1200)
    assert proc.returncode == 0, f"the child collection exited {proc.returncode}:\n{proc.stdout[-3000:]}\n{proc.stderr[-1500:]}"
    files = cs.tracked_test_files()
    text, dropped = _tracked_only(proc.stdout, files)
    if dropped:
        print(f"S3: untracked test files left out of the collection: {dropped}")
    collected_file = tmp / "collect.txt"
    collected_file.write_text(text, encoding="utf-8")
    return {"files": files, "collection": cs.parse_collection(text), "path": collected_file}


@pytest.mark.parametrize("k", KS)
def test_s3_the_shards_partition_the_real_suite(real, k):
    files, col = real["files"], real["collection"]
    made = cs.plan(files, col, k)
    parts, counts = made["parts"], made["counts"]
    flat = [f for part in parts for f in part]
    print(f"S3 ({sys.platform}) K={k}: unsharded {col.collected} collected + {col.skipped_at_collection} skipped at "
          f"collection = {col.unsharded_total}; shards (expected / files): "
          f"{[(n, len(p)) for n, p in zip(counts, parts)]}; sum {sum(counts)}")
    assert len(parts) == k and all(parts), "a shard is empty"
    assert len(flat) == len(set(flat)), "a file is in two shards"
    assert set(flat) == set(files), sorted(set(flat) ^ set(files))
    assert all(n > 0 for n in counts) and sum(counts) == col.unsharded_total
    assert all(part == sorted(part) for part in parts), "a shard is not in path order"
    items = cs.expected_counts(files, col)
    assert max(counts) - min(counts) <= max(n for _f, n in items), "the loads differ by more than the largest file"
    shuffled = list(items)
    random.Random(k).shuffle(shuffled)
    assert cs.partition(items, k) == parts == cs.partition(shuffled, k), "the same input gave another partition"
    assert cs.partition_digest(parts) == made["digest"] == cs.plan(files, col, k)["digest"]


def test_s3_every_tracked_test_file_is_collected_from_or_skipped_by_name(real):
    files, col = real["files"], real["collection"]
    nothing = [f for f, n in cs.expected_counts(files, col) if n == 0]
    print(f"S3 ({sys.platform}): {len(files)} tracked test files; {len(col.tests)} collected from; skipped whole at "
          f"collection: {sorted(col.skips.items())}")
    assert len(col.tests) + len(col.skips) > 150, "non-vacuity"
    assert not nothing, (f"tracked test files pytest neither collects from nor skips: {nothing}. Either the split's "
                         f"patterns list a file pytest ignores, or the module holds no test")


# --- S4: the completeness check's companions ----------------------------------------------

def test_s4_a_file_dropped_from_the_partitions_input_is_named_and_the_split_is_refused(real, tmp_path, monkeypatch, capsys):
    files = real["files"]
    dropped = files[len(files) // 2]
    whole = cs.expected_counts
    monkeypatch.setattr(cs, "expected_counts", lambda f, c: [item for item in whole(f, c) if item[0] != dropped])
    out_json, out_files = tmp_path / "shard.json", tmp_path / "files.txt"
    code = cs.main(["split", "--shards", "4", "--index", "1", "--collected", str(real["path"]),
                    "--json", str(out_json), "--files", str(out_files)])
    said = capsys.readouterr().out
    print(said[-400:])
    assert code == cs.EXIT_REFUSED and f"{dropped} is in no shard" in said, (code, said[-600:])
    assert not out_json.exists() and not out_files.exists(), "a refused split wrote a shard"


def test_s4_a_file_given_twice_is_named_in_both_shards_and_the_split_is_refused(real, tmp_path, monkeypatch, capsys):
    files = real["files"]
    whole = cs.expected_counts

    def twice(f, c):
        items = whole(f, c)
        heaviest = max(items, key=lambda item: item[1])             # twice at the top of the order: two shards
        return [*items, heaviest]

    monkeypatch.setattr(cs, "expected_counts", twice)
    heaviest = max(whole(files, real["collection"]), key=lambda item: item[1])[0]
    code = cs.main(["split", "--shards", "4", "--index", "2", "--collected", str(real["path"]),
                    "--json", str(tmp_path / "shard.json"), "--files", str(tmp_path / "files.txt")])
    said = capsys.readouterr().out
    print(said[-400:])
    assert code == cs.EXIT_REFUSED and f"{heaviest} is in more than one place: shards 1, 2" in said, (code, said[-600:])
    assert not (tmp_path / "shard.json").exists()


def test_s4_hand_made_partitions():
    a, b, c = TOY_FILES
    assert cs.completeness_problems(TOY_FILES, [[a], [b], [c]]) == []
    assert cs.completeness_problems(TOY_FILES, [[a, b], [], [c]]) == ["shard 2 holds no file"]
    planted = "tests/test_never_tracked.py"
    assert cs.completeness_problems(TOY_FILES, [[a, planted], [b, c]]) == [
        f"{planted} is in shard 1 and is not a tracked test file"]
    assert cs.completeness_problems(TOY_FILES, [[a, b], [b]]) == [
        f"{b} is in more than one place: shards 1, 2", f"{c} is in no shard"]
    with pytest.raises(cs.ShardRefused) as e:                      # more shards than tests to give them
        cs.plan(TOY_FILES, cs.parse_collection(TOY), 4)
    assert "would run no test" in str(e.value) or "holds no file" in str(e.value), str(e.value)


# --- S5: the count check ------------------------------------------------------------------

WINDOWS_RUN_68 = "1 failed, 3119 passed, 111 skipped, 3 xfailed, 1 warning in 2871.70s (0:47:51)"


def _shard(tmp_path: Path, expected: int, index: int = 2) -> Path:
    path = tmp_path / f"shard-{index}.json"
    data = {"schema": cs.SCHEMA, "shards": 4, "index": index, "collected": 4 * expected - 1, "skipped_at_collection": 1,
            "unsharded_total": 4 * expected, "tracked_files": 8, "partition_sha256": "0" * 64,
            "shard_counts": [expected] * 4, "expected": expected, "files": [f"tests/test_{index}a.py", f"tests/test_{index}b.py"],
            "file_counts": {}, "platform": sys.platform, "python": "3.11"}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _check(tmp_path: Path, expected: int, output: str, capsys) -> tuple[int, str, dict]:
    shard = _shard(tmp_path, expected)
    run = tmp_path / "suite.txt"
    run.write_text(output, encoding="utf-8")
    summary = tmp_path / "summary.md"
    summary.unlink(missing_ok=True)
    code = cs.main(["check", "--json", str(shard), "--output", str(run), "--summary", str(summary)])
    said = capsys.readouterr().out
    assert summary.read_text(encoding="utf-8").count(said.strip().splitlines()[-1]) == 1, "the report is not in the summary"
    return code, said, json.loads(shard.read_text(encoding="utf-8"))


def test_s5_outcomes_that_sum_to_the_expected_count_are_accepted(tmp_path, capsys):
    progress = "........s.x                                                              [100%]\n"
    code, said, data = _check(tmp_path, 10, progress + "7 passed, 2 skipped, 1 xfailed, 3 warnings in 1.20s\n", capsys)
    assert code == cs.EXIT_OK and "COUNT MATCH: the outcomes sum to 10, the expected count" in said, said
    assert data["outcomes"] == {"passed": 7, "failed": 0, "errors": 0, "skipped": 2, "xfailed": 1, "xpassed": 0}
    assert (data["total"], data["verdict"]) == (10, "match")


def test_s5_one_short_is_refused_naming_both_numbers(tmp_path, capsys):
    code, said, data = _check(tmp_path, 10, "7 passed, 1 skipped, 1 xfailed in 1.20s\n", capsys)
    assert code == cs.EXIT_MISMATCH, said
    assert "COUNT MISMATCH: the outcomes sum to 9, expected 10: 1 short" in said, said
    assert (data["total"], data["verdict"]) == (9, "mismatch")
    code, said, _ = _check(tmp_path, 10, "9 passed, 1 deselected in 0.50s\n", capsys)
    assert code == cs.EXIT_MISMATCH and "sum to 9, expected 10" in said and "(1 deselected)" in said, said
    code, said, _ = _check(tmp_path, 10, "11 passed in 0.50s\n", capsys)
    assert code == cs.EXIT_MISMATCH and "sum to 11, expected 10: 1 more than collected" in said, said


def test_s5_the_line_is_the_last_of_its_shape_and_real_lines_are_read(tmp_path, capsys):
    childs = "----- Captured stdout call -----\n3 passed in 0.10s\n=== short test summary info ===\n"
    code, said, data = _check(tmp_path, 10, childs + "======== 1 failed, 9 passed in 61.20s (0:01:01) ========\n", capsys)
    assert code == cs.EXIT_OK and data["outcomes"]["failed"] == 1 and data["total"] == 10, said
    line = cs.parse_summary(WINDOWS_RUN_68 + "\n")
    assert sum(line["outcomes"].values()) == 3234 and line["warnings"] == 1, line
    assert cs.parse_summary("no tests ran in 0.01s\n")["outcomes"]["passed"] == 0
    assert cs.parse_summary("\x1b[32m\x1b[1m2 passed\x1b[0m\x1b[32m in 0.01s\x1b[0m\n")["outcomes"]["passed"] == 2


def test_s5_no_summary_line_is_a_run_that_did_not_finish(tmp_path, capsys):
    code, said, data = _check(tmp_path, 10, "....\nFatal Python error: Aborted\n", capsys)
    assert code == cs.EXIT_MISMATCH and "holds no pytest summary line" in said and "expected 10" in said, said
    assert data["outcomes"] is None and data["verdict"] == "no summary line"


def test_s5_errors_in_the_line_are_not_proven_and_an_unknown_word_is_refused(tmp_path, capsys):
    for line in ("9 passed, 1 error in 1.00s", "10 passed, 1 error in 1.00s", "8 passed, 2 errors in 1.00s"):
        code, said, data = _check(tmp_path, 10, line + "\n", capsys)        # in place of an outcome, or beside one
        assert code == cs.EXIT_MISMATCH and "COUNT NOT PROVEN" in said and data["verdict"] == "not proven", (line, said)
    code, said, data = _check(tmp_path, 10, "7 passed, 1 error in 1.00s\n", capsys)
    assert code == cs.EXIT_MISMATCH and "COUNT MISMATCH" in said and "outside what the errors could account for" in said
    shard = _shard(tmp_path, 10)
    run = tmp_path / "suite.txt"
    run.write_text("9 passed, 1 rerun in 1.00s\n", encoding="utf-8")
    code = cs.main(["check", "--json", str(shard), "--output", str(run)])
    said = capsys.readouterr().out
    assert code == cs.EXIT_REFUSED and "'rerun'" in said, said
    assert cs.main(["check", "--json", str(tmp_path / "absent.json"), "--output", str(run)]) == cs.EXIT_REFUSED
    assert "cannot be read" in capsys.readouterr().out


# --- S6: the pipeline and the total -------------------------------------------------------

def _pipeline(real, folder: Path, capsys) -> dict[int, Path]:
    """Four splits and four checks through `main`, over the real collection;
    each shard's run output is a line of its own expected count."""
    shards = {}
    for index in range(1, 5):
        shard, files = folder / f"shard-{index}.json", folder / f"files-{index}.txt"
        assert cs.main(["split", "--shards", "4", "--index", str(index), "--collected", str(real["path"]),
                        "--json", str(shard), "--files", str(files)]) == cs.EXIT_OK
        data = json.loads(shard.read_text(encoding="utf-8"))
        listed = files.read_bytes()
        assert b"\r" not in listed and listed.decode("utf-8").splitlines() == data["files"], "the files list"
        run = folder / f"suite-{index}.txt"
        run.write_text(f"{data['expected'] - 3} passed, 2 skipped, 1 xfailed in 9.99s\n", encoding="utf-8")
        assert cs.main(["check", "--json", str(shard), "--output", str(run)]) == cs.EXIT_OK
        shards[index] = shard
    capsys.readouterr()
    return shards


def _total(folder: Path, capsys) -> tuple[int, str]:
    code = cs.main(["total", "--shards", "4", "--dir", str(folder)])
    return code, capsys.readouterr().out


def _edit(path: Path, **changes) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_s6_four_shards_sum_to_the_unsharded_total(real, tmp_path, capsys):
    col = real["collection"]
    _pipeline(real, tmp_path, capsys)
    code, said = _total(tmp_path, capsys)
    print(said)
    assert code == cs.EXIT_OK and "TOTAL MATCH" in said, said
    sum_row = next(line for line in said.splitlines() if line.startswith("sum "))
    cells = sum_row.split()
    assert int(cells[1]) == len(real["files"]) and int(cells[2]) == int(cells[-1]) == col.unsharded_total, sum_row
    assert f"{col.collected} tests + {col.skipped_at_collection} skipped whole at collection = {col.unsharded_total}" in said


def test_s6_companions_each_way_the_total_can_be_wrong_is_named(real, tmp_path, capsys):
    col = real["collection"]

    def fresh(name: str) -> tuple[Path, dict[int, Path]]:
        folder = tmp_path / name
        folder.mkdir()
        return folder, _pipeline(real, folder, capsys)

    folder, shards = fresh("absent")
    shards[3].unlink()
    code, said = _total(folder, capsys)
    assert code == cs.EXIT_MISMATCH and "shard 3 of 4 did not report" in said, said

    folder, shards = fresh("unfinished")
    _edit(shards[2], outcomes=None, total=None, verdict="no summary line")
    code, said = _total(folder, capsys)
    assert code == cs.EXIT_MISMATCH and "shard 2 reported its split and no outcomes" in said, said

    folder, shards = fresh("short")
    data = json.loads(shards[4].read_text(encoding="utf-8"))
    outcomes = dict(data["outcomes"], passed=data["outcomes"]["passed"] - 1)
    _edit(shards[4], outcomes=outcomes, total=data["total"] - 1, verdict="mismatch")
    code, said = _total(folder, capsys)
    assert code == cs.EXIT_MISMATCH, said
    assert (f"the shards' outcomes sum to {col.unsharded_total - 1}, the unsharded total is {col.unsharded_total}: "
            f"1 short") in said, said

    folder, shards = fresh("disagree")
    _edit(shards[1], collected=col.collected + 7)
    code, said = _total(folder, capsys)
    assert code == cs.EXIT_MISMATCH and "the shards disagree on the unsharded collected count" in said, said
    _edit(shards[1], collected=col.collected, partition_sha256="f" * 64)
    code, said = _total(folder, capsys)
    assert code == cs.EXIT_MISMATCH and "the shards disagree on the partition" in said, said

    folder, shards = fresh("missing-file")
    data = json.loads(shards[2].read_text(encoding="utf-8"))
    lost = data["files"][0]
    _edit(shards[2], files=data["files"][1:])
    code, said = _total(folder, capsys)
    assert code == cs.EXIT_MISMATCH and f"{lost} is in no shard" in said, said

    empty = tmp_path / "nothing-here"
    code, said = _total(empty, capsys)
    assert code == cs.EXIT_MISMATCH and "no shard reported" in said, said
