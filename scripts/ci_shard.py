#!/usr/bin/env python3
"""CI: the suite in parallel shards, the same tests in total (ci.yml's
windows job; the operator's instruction of 11 November).

    python -m pytest tests --collect-only -q -rs -p no:cacheprovider > collect.txt
    python scripts/ci_shard.py split --shards 4 --index 2 --collected collect.txt
                                     --json shard-2.json --files shard-2.txt
    python -m pytest <the files in shard-2.txt> -q -p no:cacheprovider -rfE | tee suite-2.txt
    python scripts/ci_shard.py check --json shard-2.json --output suite-2.txt [--summary FILE]
    python scripts/ci_shard.py total --shards 4 --dir DIR [--summary FILE]

Three modes, one per step of the workflow. The same five lines run a shard
on any machine, from the repository root.

split   Reads ONE unsharded collection made on this runner (the output of
        the first line above), so the unsharded count is measured where the
        shard runs and never carried from another platform. It lists the
        suite's test files from git (`git ls-files tests`, the names
        pytest's `python_files` setting collects: the repository tracks no
        pytest configuration file, so the setting is pytest's default,
        `test_*.py` and `*_test.py`, and tests/test_ci_shard.py compares it
        with the running pytest's own), gives every file its expected
        count, partitions the files into K shards, and for shard --index
        prints the files with their counts, writes them one a line for the
        shell to hand to pytest (to a file, in bytes, LF on every platform:
        a shell that captures a Windows Python's stdout keeps its CR), and
        writes a small JSON: the unsharded count, this shard's expected
        count, its files. It refuses, by name, a collection that did not finish, a
        file pytest collected that git does not track (the shards run
        tracked files only, so the sum could not equal the unsharded
        count), a shard that would run nothing, and a partition in which a
        file is in no shard or in two.

        A file's expected count is the tests pytest collected from it, plus
        one for every skip pytest reported while collecting it: a module
        that skips itself whole (an `importorskip` at its top) is no test
        in a collection and one `skipped` in a run's summary line, which is
        why the collection is made with `-rs`. So an unsharded run's
        outcomes sum to `collected + skipped_at_collection`, and so must
        the shards'.

        The partition: files sorted by expected count, largest first, ties
        by path; each goes to the shard whose load is lightest so far, ties
        to the lowest index; a shard's files are then run in path order,
        which is the order the unsharded suite runs them in. The result is
        the same on every runner for the same tree because nothing else
        enters it: the file list is git's, the counts are a collection's
        (pytest importing the tree with the installed requirements; no
        clock, no random, no runner name), every order has a total key
        (paths are unique, shard indices are unique), `sorted` on `str` is
        by code point on every platform, and no set or dict is iterated for
        an order. Each shard records the sha256 of the whole partition it
        computed, and `total` refuses shards that disagree, so the claim is
        checked on every run and never assumed.

check   Reads a finished shard run's output, takes pytest's summary line
        (the last line of that shape), and requires passed + failed +
        errors + skipped + xfailed + xpassed to equal the shard's expected
        count, naming both numbers when they differ: a file pytest did not
        run, or a test that was deselected, fails here. It refuses a
        summary line with a count it does not know. An error in a test's
        teardown is counted beside that test's own outcome, so with errors
        in the line the sum cannot prove the count and the verdict says
        NOT PROVEN (the run has failed already). It writes the outcomes
        into the shard's JSON for `total`.

total   Reads the K shards' JSON files and prints each shard's counts by
        outcome, their sum, and the unsharded count beside it. It fails
        unless every shard reported its outcomes, all agree on the
        unsharded count, the partition and K, every tracked test file of
        THIS checkout is in exactly one shard, and the outcomes sum to the
        unsharded total.

Order. A partition changes which files run in one process. A test that
passes only after another file has run is a defect, to be fixed at its
cause (RUN_CHARTER.md law 10); this script pins no order and knows no
dependency between files.

Exit codes: 0 the mode's check held; 1 a count did not match or was not
proven; 2 refused (an input that cannot be read, a collection that did not
finish, a partition that is not complete). Standard library only: it runs
on a runner before pytest does, and on the bare runner of the total job.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402

EXIT_OK, EXIT_MISMATCH, EXIT_REFUSED = 0, 1, 2
SCHEMA = 1
TEST_ROOT = "tests"
# pytest's default `python_files`, matched against a file's name. The
# repository tracks no pytest configuration file, so the default applies;
# tests/test_ci_shard.py fails if the running pytest's own setting differs.
PYTHON_FILES = ("test_*.py", "*_test.py")
# The words of pytest's summary line that are a test's outcome, in the order
# they are printed here. `error` and `errors` are one outcome.
OUTCOMES = ("passed", "failed", "errors", "skipped", "xfailed", "xpassed")
# What split writes into a shard's file, and check and total read back.
SHARD_KEYS = ("shards", "index", "collected", "skipped_at_collection", "unsharded_total", "tracked_files",
              "partition_sha256", "expected", "files")

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_COLLECTED = re.compile(r"^=*\s*(?P<n>\d+) tests? collected(?P<rest>.*?) in \d+(?:\.\d+)?s\b")
_DESELECTED = re.compile(r"^=*\s*\d+/\d+ tests? collected \(\d+ deselected\)")
_NOTHING_COLLECTED = re.compile(r"^=*\s*no tests collected\b")
_SKIP = re.compile(r"^SKIPPED \[(?P<n>\d+)\] (?P<where>.+?):(?P<line>\d+): (?P<reason>.*)$")
_SUMMARY = re.compile(r"^=*\s*(?P<body>\d+ [a-z]+(?: [a-z]+)*(?:, \d+ [a-z]+(?: [a-z]+)*)*) in \d+(?:\.\d+)?s"
                      r"(?: \([0-9:]+\))?\s*=*$")
_NO_TESTS_RAN = re.compile(r"^=*\s*no tests ran in \d+(?:\.\d+)?s(?: \([0-9:]+\))?\s*=*$")


class ShardRefused(Exception):
    """An input this script will not work from. The message names what and
    why; `main` prints it and exits 2."""


# --- the files ------------------------------------------------------------------

def is_test_file(path: str) -> bool:
    """A path pytest's `python_files` collects: the patterns hold no
    separator, so pytest matches them against the file's name."""
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in PYTHON_FILES)


def tracked_test_files(root: Path = ROOT) -> list[str]:
    """Every tracked test file under tests/, as git spells it (forward
    slashes on every platform), in path order. From git, never from the
    filesystem: an untracked scratch file must not change the split
    (tests/_tracked.py's rule). No fallback: without git there is no list,
    and a guessed one would be a different suite."""
    try:
        proc = subprocess.run(["git", "ls-files", "-z", "--", TEST_ROOT], cwd=root, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise ShardRefused(f"git ls-files could not run ({type(e).__name__}: {e}); the split needs git's list of "
                           f"the tracked test files") from e
    if proc.returncode != 0:
        raise ShardRefused(f"git ls-files exited {proc.returncode} in {root}: "
                           f"{proc.stderr.decode('utf-8', errors='replace').strip()[:300]}")
    names = [n for n in proc.stdout.decode("utf-8", errors="replace").split("\0") if n]
    files = sorted(n for n in names if is_test_file(n))
    if not files:
        raise ShardRefused(f"git tracks no test file under {TEST_ROOT}/ in {root}")
    return files


# --- the collection -------------------------------------------------------------

@dataclass(frozen=True)
class Collection:
    """One unsharded `pytest tests --collect-only -q -rs`, read."""
    collected: int                                   # pytest's own "N tests collected"
    tests: dict = field(default_factory=dict)        # file -> node ids counted
    skips: dict = field(default_factory=dict)        # file -> skips reported while collecting it

    @property
    def skipped_at_collection(self) -> int:
        return sum(self.skips.values())

    @property
    def unsharded_total(self) -> int:
        """What an unsharded run's outcomes sum to."""
        return self.collected + self.skipped_at_collection


def parse_collection(text: str) -> Collection:
    """Read a collection's output. A node id line is `<file>.py::<name>`,
    forward slashes on every platform; a skip reported while collecting is
    `SKIPPED [n] <file>:<line>: <reason>` (the file in the runner's own
    separators); pytest's count is its own last line. The node ids counted
    must equal pytest's count, or the output was not read correctly and
    nothing is built on it."""
    tests: dict[str, int] = {}
    skips: dict[str, int] = {}
    collected = None
    for raw in text.splitlines():
        line = _ANSI.sub("", raw).rstrip()
        if not line:
            continue
        if _DESELECTED.match(line):
            raise ShardRefused(f"the collection deselected tests, so it is not the unsharded suite: {line!r}")
        if _NOTHING_COLLECTED.match(line):
            raise ShardRefused(f"pytest collected nothing: {line!r}")
        m = _COLLECTED.match(line)
        if m:
            if "error" in m.group("rest"):
                raise ShardRefused(f"the collection had errors, so its count is not the suite's: {line!r}")
            collected = int(m.group("n"))
            continue
        m = _SKIP.match(line)
        if m:
            where = m.group("where").replace("\\", "/")
            skips[where] = skips.get(where, 0) + int(m.group("n"))
            continue
        head, sep, _rest = line.partition("::")
        if sep and head.endswith(".py") and " " not in head:
            tests[head] = tests.get(head, 0) + 1
    if collected is None:
        raise ShardRefused("the collection output has no 'N tests collected' line: pytest did not finish collecting "
                           "(run `python -m pytest tests --collect-only -q -rs -p no:cacheprovider` and read its end)")
    counted = sum(tests.values())
    if counted != collected:
        raise ShardRefused(f"counted {counted} node ids in the collection output and pytest's own line says "
                           f"{collected}: the output was not read as pytest wrote it")
    return Collection(collected=collected, tests=tests, skips=skips)


def expected_counts(files: list[str], collection: Collection) -> list[tuple[str, int]]:
    """(file, expected count) for every tracked test file, in path order:
    the tests collected from it plus the skips reported while collecting
    it. A file the collection names and git does not track is refused by
    name: the shards run tracked files only."""
    known = set(files)
    strangers = sorted((set(collection.tests) | set(collection.skips)) - known)
    if strangers:
        raise ShardRefused("pytest collected from, or skipped at, a path that is not a tracked test file: "
                           + ", ".join(strangers) + ". The shards run git's tracked test files only, so their sum "
                           "could not equal the unsharded count. Track the file, or move it out of tests/.")
    return [(f, collection.tests.get(f, 0) + collection.skips.get(f, 0)) for f in files]


# --- the partition --------------------------------------------------------------

def partition(items: list[tuple[str, int]], shards: int) -> list[list[str]]:
    """K lists of paths. Largest count first, ties by path; each to the
    lightest shard so far, ties to the lowest index; each shard then in
    path order. A pure function of `items` and `shards`."""
    if shards < 1:
        raise ShardRefused(f"--shards must be at least 1, not {shards}")
    loads = [0] * shards
    parts: list[list[str]] = [[] for _ in range(shards)]
    for path, count in sorted(items, key=lambda item: (-item[1], item[0])):
        lightest = min(range(shards), key=lambda i: (loads[i], i))
        parts[lightest].append(path)
        loads[lightest] += count
    return [sorted(part) for part in parts]


def completeness_problems(files: list[str], parts: list[list[str]]) -> list[str]:
    """What stops `parts` being a partition of `files`: a file in no shard,
    a file in more than one place, a path that is not one of the files, a
    shard with nothing in it. Set arithmetic only; it does not use the
    partition's own rule, so it is a check on it."""
    problems: list[str] = []
    places: dict[str, list[int]] = {}
    for index, part in enumerate(parts, start=1):
        if not part:
            problems.append(f"shard {index} holds no file")
        for path in part:
            places.setdefault(path, []).append(index)
    known = set(files)
    for path in files:
        where = places.get(path, [])
        if not where:
            problems.append(f"{path} is in no shard")
        elif len(where) > 1:
            problems.append(f"{path} is in more than one place: shards {', '.join(map(str, where))}")
    for path in sorted(set(places) - known):
        problems.append(f"{path} is in shard {', '.join(map(str, places[path]))} and is not a tracked test file")
    return problems


def partition_digest(parts: list[list[str]]) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def plan(files: list[str], collection: Collection, shards: int) -> dict:
    """The whole split for one tree and one collection: the K shards, their
    expected counts, and the digest every runner must agree on."""
    items = expected_counts(files, collection)
    parts = partition(items, shards)
    problems = completeness_problems(files, parts)
    if problems:
        raise ShardRefused("the partition is not complete: " + "; ".join(problems))
    count_of = dict(items)
    counts = [sum(count_of[f] for f in part) for part in parts]
    empty = [str(i) for i, part in enumerate(parts, start=1) if not sum(collection.tests.get(f, 0) for f in part)]
    if empty:
        raise ShardRefused(f"shard {', '.join(empty)} would run no test (K={shards} over {len(files)} files): "
                           f"pytest exits 5 when it collects nothing")
    if sum(counts) != collection.unsharded_total:
        raise ShardRefused(f"the shards' expected counts sum to {sum(counts)}, the unsharded total is "
                           f"{collection.unsharded_total}")
    return {"shards": shards, "parts": parts, "counts": counts, "count_of": count_of,
            "digest": partition_digest(parts)}


# --- a run's summary line -------------------------------------------------------

def parse_summary(text: str) -> "dict | None":
    """pytest's summary line, the last line of that shape in a run's
    output: {"line", "outcomes": {passed, failed, errors, skipped, xfailed,
    xpassed}, "deselected", "warnings"}. None when the output holds no such
    line (the run did not finish). A count under a word this script does
    not know is refused: it could be a test's outcome."""
    found = None
    for raw in text.splitlines():
        line = _ANSI.sub("", raw).strip()
        if _SUMMARY.match(line) or _NO_TESTS_RAN.match(line):
            found = line
    if found is None:
        return None
    outcomes = {name: 0 for name in OUTCOMES}
    other = {"deselected": 0, "warnings": 0}
    m = _SUMMARY.match(found)
    if m:
        for part in m.group("body").split(", "):
            number, _, word = part.partition(" ")
            if word in ("error", "errors"):
                outcomes["errors"] += int(number)
            elif word in outcomes:
                outcomes[word] += int(number)
            elif word in ("warning", "warnings"):
                other["warnings"] += int(number)
            elif word == "deselected":
                other["deselected"] += int(number)
            else:
                raise ShardRefused(f"pytest's summary line counts {number} {word!r}, a word this script does not "
                                   f"know to be a test's outcome or not: {found!r}")
    return {"line": found, "outcomes": outcomes, **other}


def count_verdict(expected: int, summary: dict) -> tuple[str, str]:
    """("match" | "mismatch" | "not proven", the sentence that says so)."""
    outcomes = summary["outcomes"]
    total = sum(outcomes[name] for name in OUTCOMES)
    errors = outcomes["errors"]
    deselected = summary.get("deselected", 0)
    tail = f" ({deselected} deselected)" if deselected else ""
    if errors:
        # A setup error stands in place of its test's outcome; a teardown
        # error stands beside it. So the tests reported lie between
        # total - errors and total, and equality cannot be read off the sum.
        if total - errors <= expected <= total:
            return "not proven", (f"the outcomes sum to {total} with {errors} error(s), expected {expected}: an error "
                                  f"in a teardown is counted beside its test's own outcome, so the sum cannot prove "
                                  f"the count until the errors are gone{tail}")
        return "mismatch", (f"the outcomes sum to {total} with {errors} error(s), expected {expected}: outside what "
                            f"the errors could account for ({total - errors} to {total}){tail}")
    if total == expected:
        return "match", f"the outcomes sum to {total}, the expected count{tail}"
    if total < expected:
        return "mismatch", (f"the outcomes sum to {total}, expected {expected}: {expected - total} short. A file pytest "
                            f"did not run, or a test that was deselected{tail}")
    return "mismatch", f"the outcomes sum to {total}, expected {expected}: {total - expected} more than collected{tail}"


# --- the modes ------------------------------------------------------------------

def _read(path: str, what: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise ShardRefused(f"{what} {path} cannot be read ({type(e).__name__}: {e})") from e


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ShardRefused(f"the shard file {path} cannot be read ({type(e).__name__}: {e})") from e
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ShardRefused(f"{path} is not a shard file this script wrote (schema {SCHEMA})")
    missing = [key for key in SHARD_KEYS if key not in data]
    if missing:
        raise ShardRefused(f"the shard file {path} lacks {', '.join(missing)}: it is not as split wrote it")
    return data


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii"))


def _emit(lines: list[str], title: str, summary: "str | None") -> None:
    """Print the report; append it to the job summary file when one is given."""
    print("\n".join(lines))
    if summary:
        with open(summary, "a", encoding="utf-8", newline="\n") as out:
            out.write(f"### {title}\n\n```\n" + "\n".join(lines) + "\n```\n")


def _outcome_words(outcomes: dict) -> str:
    return ", ".join(f"{name} {outcomes[name]}" for name in OUTCOMES)


def run_split(args) -> int:
    if not 1 <= args.index <= args.shards:
        raise ShardRefused(f"--index {args.index} is not one of 1..{args.shards}")
    collection = parse_collection(_read(args.collected, "the collection output"))
    files = tracked_test_files()
    made = plan(files, collection, args.shards)
    mine = made["parts"][args.index - 1]
    data = {
        "schema": SCHEMA, "shards": args.shards, "index": args.index,
        "collected": collection.collected,
        "skipped_at_collection": collection.skipped_at_collection,
        "unsharded_total": collection.unsharded_total,
        "tracked_files": len(files),
        "partition_sha256": made["digest"],
        "shard_counts": made["counts"],
        "expected": made["counts"][args.index - 1],
        "files": mine,
        "file_counts": {f: made["count_of"][f] for f in mine},
        "platform": sys.platform, "python": platform.python_version(),
    }
    _write_json(Path(args.json), data)
    Path(args.files).parent.mkdir(parents=True, exist_ok=True)
    Path(args.files).write_bytes(("\n".join(mine) + "\n").encode("utf-8"))      # LF on every platform: the shell reads it

    skipped = ", ".join(f"{f} ({n})" for f, n in sorted(collection.skips.items())) or "none"
    nothing = [f for f in files if made["count_of"][f] == 0]
    print(f"ci_shard split: shard {args.index} of {args.shards} ({sys.platform}, python {platform.python_version()})")
    print(f"the unsharded collection on this runner: {collection.collected} tests in {len(collection.tests)} files; "
          f"skipped whole at collection: {skipped}")
    print(f"an unsharded run's outcomes sum to {collection.collected} + {collection.skipped_at_collection} = "
          f"{collection.unsharded_total}")
    print(f"tracked test files (git ls-files {TEST_ROOT}; {', '.join(PYTHON_FILES)}): {len(files)}"
          + (f"; collecting nothing: {', '.join(nothing)}" if nothing else ""))
    print("the shards (expected count / files): "
          + "; ".join(f"{i}: {n} / {len(p)}" for i, (n, p) in enumerate(zip(made["counts"], made["parts"]), start=1))
          + f"; sum {sum(made['counts'])}")
    print(f"partition sha256: {made['digest']}")
    print(f"this shard: {len(mine)} files, {data['expected']} expected")
    for f in mine:
        print(f"  {made['count_of'][f]:>5}  {f}")
    return EXIT_OK


def run_check(args) -> int:
    path = Path(args.json)
    data = _read_json(path)
    label = f"shard {data['index']} of {data['shards']}"
    title = f"suite, {label} ({data.get('platform', 'platform not recorded')}, python {data.get('python', 'not recorded')})"
    summary = parse_summary(_read(args.output, "the run's output"))
    lines = [f"ci_shard check: {label} ({len(data['files'])} files, {data['expected']} expected)"]
    if summary is None:
        data.update({"outcomes": None, "total": None, "summary_line": None, "verdict": "no summary line"})
        _write_json(path, data)
        lines.append("COUNT MISMATCH: the run's output holds no pytest summary line, so the run did not finish; "
                     f"expected {data['expected']}")
        _emit(lines, title, args.summary)
        return EXIT_MISMATCH
    verdict, sentence = count_verdict(data["expected"], summary)
    outcomes = summary["outcomes"]
    total = sum(outcomes[name] for name in OUTCOMES)
    data.update({"outcomes": outcomes, "total": total, "summary_line": summary["line"], "verdict": verdict,
                 "deselected": summary["deselected"]})
    _write_json(path, data)
    lines.append(f"pytest's summary line: {summary['line']}")
    lines.append(f"outcomes: {_outcome_words(outcomes)}; sum {total}")
    lines.append({"match": "COUNT MATCH", "mismatch": "COUNT MISMATCH", "not proven": "COUNT NOT PROVEN"}[verdict]
                 + f": {sentence}")
    _emit(lines, title, args.summary)
    return EXIT_OK if verdict == "match" else EXIT_MISMATCH


def total_report(shards: int, reports: list[dict], files: list[str]) -> tuple[list[str], list[str]]:
    """(the table's lines, the problems). `reports` are the shard files
    read; `files` the tracked test files of this checkout."""
    problems: list[str] = []
    by_index: dict[int, dict] = {}
    for r in reports:
        if r.get("shards") != shards:
            problems.append(f"shard file {r.get('index')} was split for K={r.get('shards')}, not {shards}")
        if r.get("index") in by_index:
            problems.append(f"shard {r.get('index')} reported twice")
        by_index[r.get("index")] = r
    for index in range(1, shards + 1):
        if index not in by_index:
            problems.append(f"shard {index} of {shards} did not report (no shard file: its job did not reach the split)")
    for index in sorted(set(by_index) - set(range(1, shards + 1)), key=str):
        problems.append(f"a shard file names index {index}, which is not one of 1..{shards}")
    present = [by_index[i] for i in range(1, shards + 1) if i in by_index]

    for key, what in (("collected", "the unsharded collected count"),
                      ("skipped_at_collection", "the skips at collection"),
                      ("partition_sha256", "the partition"),
                      ("tracked_files", "the number of tracked test files")):
        values = {r["index"]: r.get(key) for r in present}
        if len(set(values.values())) > 1:
            problems.append(f"the shards disagree on {what}: " + ", ".join(f"shard {i}: {v}" for i, v in sorted(values.items())))

    parts = [list(by_index[i].get("files") or []) if i in by_index else [] for i in range(1, shards + 1)]
    if len(present) == shards:
        problems += [f"against this checkout's tracked test files: {p}" for p in completeness_problems(files, parts)]

    header = f"{'shard':<6}{'files':>6}{'expected':>10}" + "".join(f"{name:>9}" for name in OUTCOMES) + f"{'total':>8}"
    lines = [header]
    sums = {name: 0 for name in OUTCOMES}
    sum_files = sum_expected = sum_total = 0
    for index in range(1, shards + 1):
        r = by_index.get(index)
        if r is None:
            lines.append(f"{index:<6}{'did not report':>24}")
            continue
        sum_files += len(r.get("files") or [])
        sum_expected += r.get("expected") or 0
        outcomes = r.get("outcomes")
        if not outcomes:
            problems.append(f"shard {index} reported its split and no outcomes: its suite did not finish "
                            f"({r.get('verdict') or 'the count check did not run'})")
            lines.append(f"{index:<6}{len(r.get('files') or []):>6}{r.get('expected'):>10}{'no outcomes':>20}")
            continue
        for name in OUTCOMES:
            sums[name] += outcomes[name]
        sum_total += r["total"]
        lines.append(f"{index:<6}{len(r['files']):>6}{r['expected']:>10}" + "".join(f"{outcomes[name]:>9}" for name in OUTCOMES)
                     + f"{r['total']:>8}")
        if r.get("verdict") != "match":
            problems.append(f"shard {index}: its own count is {r.get('verdict')} (expected {r['expected']}, "
                            f"outcomes sum {r['total']})")
    lines.append(f"{'sum':<6}{sum_files:>6}{sum_expected:>10}" + "".join(f"{sums[name]:>9}" for name in OUTCOMES)
                 + f"{sum_total:>8}")

    if present:
        first = present[0]
        collected, skipped = first.get("collected"), first.get("skipped_at_collection")
        unsharded = first.get("unsharded_total")
        lines.append(f"unsharded, collected on the shards' own runners: {collected} tests + {skipped} skipped whole at "
                     f"collection = {unsharded}")
        lines.append("runners: " + ", ".join(sorted({f"{r.get('platform')} python {r.get('python')}" for r in present}))
                     + f"; partition sha256 {first.get('partition_sha256')}")
        if len(present) == shards and not any(p.startswith("the shards disagree") for p in problems):
            if sum_expected != unsharded:
                problems.append(f"the shards' expected counts sum to {sum_expected}, the unsharded total is {unsharded}")
            if all(r.get("outcomes") for r in present) and sum_total != unsharded:
                problems.append(f"the shards' outcomes sum to {sum_total}, the unsharded total is {unsharded}: "
                                f"{abs(unsharded - sum_total)} {'short' if sum_total < unsharded else 'more'}")
    else:
        problems.append("no shard reported")
    return lines, problems


def run_total(args) -> int:
    folder = Path(args.dir)
    paths = sorted(folder.glob("*.json")) if folder.is_dir() else []
    reports = [_read_json(p) for p in paths]
    files = tracked_test_files()
    lines, problems = total_report(args.shards, reports, files)
    out = [f"ci_shard total: {len(reports)} shard file(s) of {args.shards} in {folder}; this checkout tracks "
           f"{len(files)} test files", *lines]
    if problems:
        out.append("TOTAL MISMATCH:")
        out += [f"  {p}" for p in problems]
    else:
        out.append(f"TOTAL MATCH: every shard reported, all agree on the unsharded count and the partition, every "
                   f"tracked test file is in exactly one shard, and the outcomes sum to {reports[0]['unsharded_total']}, "
                   f"the unsharded total")
    _emit(out, f"suite, the {args.shards} shards' total against the unsharded count", args.summary)
    return EXIT_MISMATCH if problems else EXIT_OK


def main(argv: "list[str] | None" = None) -> int:
    force_utf8_stdio()
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("split", help="one shard's files and expected count, from an unsharded collection")
    s.add_argument("--shards", type=int, required=True, help="K, the number of shards")
    s.add_argument("--index", type=int, required=True, help="this shard, 1..K")
    s.add_argument("--collected", required=True, help="the output of `pytest tests --collect-only -q -rs`")
    s.add_argument("--json", required=True, help="where to write this shard's file")
    s.add_argument("--files", required=True, help="where to write this shard's test files, one a line")
    c = sub.add_parser("check", help="a finished shard run's outcomes against its expected count")
    c.add_argument("--json", required=True, help="this shard's file, as split wrote it; the outcomes are added")
    c.add_argument("--output", required=True, help="the shard run's output (pytest's, teed)")
    c.add_argument("--summary", default=None, help="a file to append the report to (the job summary)")
    t = sub.add_parser("total", help="the shards' counts, their sum, and the unsharded count")
    t.add_argument("--shards", type=int, required=True, help="K, the number of shards that must have reported")
    t.add_argument("--dir", required=True, help="the folder holding the shards' files")
    t.add_argument("--summary", default=None, help="a file to append the report to (the job summary)")
    args = ap.parse_args(argv)
    try:
        return {"split": run_split, "check": run_check, "total": run_total}[args.mode](args)
    except ShardRefused as e:
        print(f"REFUSED: {e}")
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
