"""Amendment 11 and its addition (the operator, 24 and 26 November): the README's front, for the public repository.

WHAT IS HELD HERE
    F1  The README opens with the heading `# Talima: receipts for AI`; then one sentence; then the clay mark in the
        markup Amendment 11 gives (centred, 96 pixels wide); then three badges, in order the licence, the latest
        release and the specification's version (v1, which the specification's own title states); then a quick
        start of three commands under a heading, with one paragraph after it; then the comparison table under the
        heading "How it compares"; then a rule. After the rule comes the README as it stood before the amendment,
        byte for byte: NEXUS's section unchanged (OLD_README_SHA256 and OLD_README_LENGTH, written out). The README
        is read as git's index holds it (what a commit made now would hold, whatever the working copy's line ends),
        so a Windows checkout that wrote CRLF reads the same; and it holds no carriage return anywhere, in the
        front or in NEXUS's section.
    F2  The quick start: three commands. The first clones the public repository; the other two run talima-verify
        on files the specification lists as its test vectors (section 14). Each of the two is run here, by this
        Python, in a folder that holds what a clone puts at the paths it names, and must end with
        `talima-verify: VERIFIED` and exit 0.
    F3  The table: five rows, a header and four, the four in order ordinary logs, SCITT receipts, agent audit-trail
        drafts and Talima, each with what it records.
    F4  Every link and image of the front leads somewhere the front means: a path is a tracked file of this tree; a
        link outside goes to the public repository, the badge service, RFC 9943 or the agent audit-trail draft.
    F5  Companions, each planting what F1 to F4 do not name: another heading; a second sentence; the mark at another
        width; a badge left out, and two badges swapped; one byte of NEXUS's section changed; a fourth command; a
        command naming a file no clone holds, and one naming a file the specification does not list as a vector; a
        command that does not verify; a row left out, a row renamed and a row added; a link to another host; a
        CRLF line end in NEXUS's section, a lone CR inside one of its lines, and a CR in the front.

WHAT IS NOT HELD HERE
    What the badges show: the badge service reads the public repository when a page is shown. Whether the table is
    fair to the other three: each row says what that kind of record holds, read from its own document (RFC 9943
    for SCITT; `draft-sharif-agent-audit-trail` for the agent audit trail, an individual Internet-Draft), and a
    reader judges the rest. That `git clone` works: no host is called here.
"""
from __future__ import annotations

import ast
import hashlib
import os
import pathlib
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import pytest

from tests import _proc
from tests._tracked import REPO, tracked

README = REPO / "README.md"
SPEC = REPO / "docs" / "TALIMA_SPEC.md"
# The README as it stood before Amendment 11 (fc0fdbf), which follows the front unchanged.
OLD_README_SHA256 = "d2bf97ebf3f10d4f04f48d44ed06cddf06dff80700248286db3c8db106464e81"
OLD_README_LENGTH = 8934
HEADING = "# Talima: receipts for AI"
MARK = '<p align="center"><img src="docs/brand/talima-mark-clay.svg" width="96" alt="Talima"></p>'
BADGES = [
    ("Licence", "https://img.shields.io/github/license/Slickfish/talima",
     "https://github.com/Slickfish/talima/blob/main/LICENSE"),
    ("Latest release", "https://img.shields.io/github/v/release/Slickfish/talima",
     "https://github.com/Slickfish/talima/releases/latest"),
    ("Specification: Talima v1", "https://img.shields.io/badge/spec-v1-C2410C", "docs/TALIMA_SPEC.md"),
]
BADGE = re.compile(r"\[!\[([^\]]+)\]\(([^)\s]+)\)\]\(([^)\s]+)\)")
QUICK_START = "## Quick start: verify the test vectors"
COMPARES = "## How it compares"
CLONE = "git clone https://github.com/Slickfish/talima.git"
ROWS = ["Ordinary logs", "SCITT receipts", "Agent audit-trail drafts", "Talima"]
VERIFIED = "talima-verify: VERIFIED"
LINK = re.compile(r"\]\(([^)\s]+)\)|\b(?:src|href)=\"([^\"]+)\"")
# Where a link outside the tree may go: (scheme, host, the path's first two parts, or None for any path)
OUTSIDE = {("https", "github.com", ("Slickfish", "talima")), ("https", "img.shields.io", None),
           ("https", "www.rfc-editor.org", ("rfc", "rfc9943")),
           ("https", "datatracker.ietf.org", ("doc", "draft-sharif-agent-audit-trail"))}


def as_git_holds(path, root=REPO) -> bytes:
    """A tracked file's bytes as git's index holds them: what a commit made now would hold, whatever the working
    copy's line ends. Only where git cannot be run at all, the working copy's bytes (declared); where git runs and
    cannot give the file (an unmerged entry, a file not added), the test stops and says so."""
    try:
        done = _proc.run(["git", "cat-file", "blob", f":./{path.relative_to(root).as_posix()}"], cwd=root,
                         capture_output=True, timeout=30)
    except OSError:
        return path.read_bytes()
    assert done.returncode == 0, (f"git cannot give {path.relative_to(root).as_posix()} from its index: "
                                  f"{done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout


def the_readme() -> bytes:
    return as_git_holds(README)


def split(data: bytes) -> tuple:
    """(the front, as text; NEXUS's section, as bytes): the section is the README's last OLD_README_LENGTH bytes."""
    return data[:-OLD_README_LENGTH].decode("utf-8"), data[-OLD_README_LENGTH:]


def blocks_of(front: str) -> list:
    """The front's blocks, as a blank line separates them."""
    return [block for block in front.strip("\n").split("\n\n")]


def one_sentence(text: str) -> bool:
    """One line that ends a sentence, and no sentence ends inside it."""
    return ("\n" not in text and text.endswith((".", "!", "?"))
            and not re.search(r"[.!?][\"')\]]*\s", text[:-1]))


def table_rows(block: str) -> list:
    """The table's rows, each a list of its cells' text; the delimiter row is not a row."""
    lines = block.split("\n")
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    return [row for n, row in enumerate(rows) if n != 1] if len(lines) > 1 else rows


def links_of(front: str) -> list:
    return [a or b for a, b in LINK.findall(front)]


def link_problems(front: str, files: set) -> list:
    problems = []
    for target in links_of(front):
        parts = urlsplit(target)
        if parts.scheme:
            head = tuple(p for p in parts.path.split("/") if p)[:2]
            if not any(parts.scheme == s and parts.netloc == host and (path is None or head == path)
                       for s, host, path in OUTSIDE):
                problems.append(f"a link outside the tree to a place the front does not mean: {target}")
        elif posixpath.normpath(target) not in files:
            problems.append(f"a link to {target}, which is no tracked file")
    return problems


def front_problems(data: bytes, files: set, spec_title: "str | None" = None) -> list:
    """Every way the README's front is not F1's, F3's and F4's; empty when it is. The specification's title is
    read from the tree unless one is given."""
    front, section = split(data)
    problems = []
    returns = data.count(b"\r")
    if returns:
        problems.append(f"the README holds {returns} carriage return(s): it is committed with LF line ends alone")
    if hashlib.sha256(section).hexdigest() != OLD_README_SHA256:
        problems.append("NEXUS's section is not the README as it stood before Amendment 11, byte for byte")
    if not front.endswith("\n\n---\n\n"):
        problems.append("the front does not end with a rule, alone between blank lines, before NEXUS's section")
    blocks = blocks_of(front)
    shape = ["heading", "sentence", "mark", "badges", "quick start", "the commands", "a paragraph", "the table's "
             "heading", "the table", "a rule"]
    if len(blocks) != len(shape):
        return problems + [f"the front has {len(blocks)} blocks, not the {len(shape)} it is made of: "
                           f"{[b.split(chr(10))[0][:40] for b in blocks]}"]
    heading, sentence, mark, badges, quick, commands, paragraph, compares, table, _rule = blocks
    if heading != HEADING:
        problems.append(f"the first heading is {heading!r}, not {HEADING!r}")
    if not one_sentence(sentence):
        problems.append(f"after the heading is not one sentence: {sentence!r}")
    if mark != MARK:
        problems.append(f"after the sentence is not the clay mark in Amendment 11's markup: {mark!r}")
    found = BADGE.findall(badges)
    if found != BADGES or " ".join(f"[![{a}]({i})]({t})" for a, i, t in found) != badges:
        problems.append(f"the badges are not the licence, the latest release and the specification's version, in "
                        f"that order and alone: {found}")
    if quick != QUICK_START:
        problems.append(f"the quick start's heading is {quick!r}")
    lines = commands.split("\n")
    if lines[0] != "```sh" or lines[-1] != "```" or len(lines) != 5:
        problems.append(f"the quick start is not one block of three commands: {lines}")
    if paragraph.startswith(("#", "|", "```", "<")):
        problems.append(f"after the commands is not a paragraph: {paragraph[:60]!r}")
    if compares != COMPARES:
        problems.append(f"the table's heading is {compares!r}")
    rows = table_rows(table)
    if len(rows) != 5 or rows[0] != ["", "What it records"] or [r[0] for r in rows[1:]] != ROWS \
            or any(len(r) != 2 or not r[1] for r in rows):
        problems.append(f"the table is not a header and the four rows {ROWS}, each with what it records: "
                        f"{[r[:1] for r in rows]}")
    if spec_title is None:
        spec_title = SPEC.read_text(encoding="utf-8").split("\n", 1)[0]
    if spec_title.split(":")[0] != "# Talima v1":
        problems.append(f"the specification's title does not state version 1: {spec_title!r}")
    return problems + link_problems(front, files)


def tracked_files() -> set:
    return {p.relative_to(REPO).as_posix() for p in tracked()}


def the_vectors() -> set:
    """The files the specification's section 14 lists, as paths of this tree."""
    text = SPEC.read_text(encoding="utf-8")
    section = text.split("## 14. Conformance and test vectors", 1)[1].split("\n## ", 1)[0]
    names = re.findall(r"^\| `([^`]+)` \|", section, re.M)
    return {posixpath.normpath(posixpath.join("tests/fixtures/tb_golden", name)) for name in names}


def the_commands(data: bytes) -> list:
    front, _section = split(data)
    block = next(b for b in blocks_of(front) if b.startswith("```sh\n"))
    return block.split("\n")[1:-1]


def command_problems(commands: list, files: set, vectors: set) -> list:
    """F2 without running anything: three commands, a clone of the public repository and two that run talima-verify
    on vectors of the specification, every path a clone holds."""
    problems = []
    if len(commands) != 3:
        problems.append(f"{len(commands)} commands, not three")
    if not commands or commands[0] != CLONE:
        problems.append(f"the first command is not {CLONE!r}")
    for line in commands[1:]:
        argv = shlex.split(line)
        if argv[:2] != ["python3", "talima/scripts/talima_verify.py"]:
            problems.append(f"not talima-verify run from the clone: {line}")
            continue
        paths = [PurePosixPath(a) for a in argv[2:] if PurePosixPath(a).parts[0] == "talima"]
        for path in paths:
            inside = PurePosixPath(*path.parts[1:]).as_posix()
            if inside not in files:
                problems.append(f"{line}: names {path}, which a clone does not hold")
            elif inside not in vectors:
                problems.append(f"{line}: names {path}, which the specification does not list as a test vector")
        if not paths:
            problems.append(f"{line}: names no file of the clone")
    return problems


def as_a_clone(folder, commands: list) -> None:
    """Lay out under `folder/talima` what a clone holds at the paths the commands name, and the scripts beside the
    verifier (it loads its other half from its own folder)."""
    wanted = {p.relative_to(REPO).as_posix() for p in tracked("scripts/*.py")}
    for line in commands[1:]:
        wanted |= {PurePosixPath(*PurePosixPath(a).parts[1:]).as_posix() for a in shlex.split(line)[2:]
                   if PurePosixPath(a).parts[0] == "talima" and len(PurePosixPath(a).parts) > 1}
    for rel in sorted(wanted):
        source = REPO / rel
        if source.is_file():
            target = folder / "talima" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def run_problems(commands: list, folder) -> list:
    """F2, run: each command after the clone, by this Python, from `folder`; each must end with VERIFIED."""
    as_a_clone(folder, commands)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    problems = []
    for line in commands[1:]:
        argv = shlex.split(line)
        done = _proc.run([sys.executable, *argv[1:]], cwd=folder, env=env, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=120)
        last = (done.stdout.strip().splitlines() or [""])[-1]
        if done.returncode != 0 or last != VERIFIED:
            problems.append(f"{line}: exit {done.returncode}, last line {last!r}")
    return problems


def test_f1_the_front_is_the_amendments_and_nexus_section_follows_unchanged():
    data = the_readme()
    assert front_problems(data, tracked_files()) == []


def test_f2_the_quick_starts_three_commands_verify_the_specifications_vectors(tmp_path):
    commands = the_commands(the_readme())
    assert command_problems(commands, tracked_files(), the_vectors()) == []
    assert run_problems(commands, tmp_path) == []


# --- F5: the companions -----------------------------------------------------------------------------------------

def planted(old: str, new: str) -> bytes:
    data = the_readme()
    front, section = split(data)
    assert front.count(old) == 1, f"the plant's anchor is not in the front once: {old!r}"
    return front.replace(old, new, 1).encode("utf-8") + section


FRONT_PLANTS = [
    ("another heading", HEADING + "\n", "# Talima\n", "the first heading is"),
    ("a second sentence", "said.\n", "said. It is open.\n", "after the heading is not one sentence"),
    ("the mark at another width", 'width="96"', 'width="128"', "after the sentence is not the clay mark"),
    ("a badge left out", " [![Latest release](https://img.shields.io/github/v/release/Slickfish/talima)]"
     "(https://github.com/Slickfish/talima/releases/latest)", "", "the badges are not"),
    ("a row left out", "\n| Ordinary logs |", "\n| Ordinary logs (left out) |", "the table is not"),
    ("a row renamed", "| SCITT receipts |", "| Transparency receipts |", "the table is not"),
    ("a row added", "\n| Talima |", "\n| Signed logs | Lines, each signed. |\n| Talima |", "the table is not"),
    ("a link to another host", "https://github.com/Slickfish/talima/releases/latest",
     "https://example.invalid/talima/releases", "a link outside the tree to a place the front does not mean"),
    ("a link to no file", "](docs/TALIMA_SPEC.md)", "](docs/TALIMA_SPECIFICATION.md)", "which is no tracked file"),
    # the verifier's V7: a plant for each check the others did not reach
    ("another kind of rule before NEXUS's section", "\n---\n\n", "\n***\n\n", "the front does not end with a rule"),
    ("a second blank line before NEXUS's section", "\n---\n\n", "\n---\n\n\n", "the front does not end with a rule"),
    ("the rule against the table", "|\n\n---\n\n", "|\n---\n\n", "the front does not end with a rule"),
    ("another quick-start heading", QUICK_START + "\n", "## Quick start\n", "the quick start's heading is"),
    ("the commands in another kind of block", "```sh\n", "```bash\n", "is not one block of three commands"),
    ("a heading where the paragraph after the commands stands", "\nThe second and third commands",
     "\n## The second and third commands", "after the commands is not a paragraph"),
    ("another table heading", COMPARES + "\n", "## Comparison\n", "the table's heading is"),
]


@pytest.mark.parametrize("what, old, new, named", FRONT_PLANTS, ids=[p[0].replace(" ", "-") for p in FRONT_PLANTS])
def test_f5_companions_a_front_that_is_not_the_amendments_is_named(what, old, new, named):
    found = front_problems(planted(old, new), tracked_files())
    assert any(named in p for p in found), (what, found)


def test_f5_companion_a_specification_of_another_version_is_named():
    found = front_problems(the_readme(), tracked_files(), spec_title="# Talima v2: the record and verification "
                                                                     "specification")
    assert found == ["the specification's title does not state version 1: '# Talima v2: the record and "
                     "verification specification'"], found


def test_f5_companion_two_badges_swapped_are_named():
    data = the_readme()
    front, section = split(data)
    found = BADGE.findall(front)
    first, second = (f"[![{a}]({i})]({t})" for a, i, t in found[:2])
    swapped = front.replace(first + " " + second, second + " " + first, 1).encode("utf-8") + section
    assert any("the badges are not" in p for p in front_problems(swapped, tracked_files()))


def test_f5_companion_one_byte_of_nexus_section_changed_is_named():
    data = bytearray(the_readme())
    at = len(data) - OLD_README_LENGTH // 2
    data[at] = ord("X") if data[at] != ord("X") else ord("Y")
    found = front_problems(bytes(data), tracked_files())
    assert "NEXUS's section is not the README as it stood before Amendment 11, byte for byte" in found, found


def test_f5_companions_a_command_that_is_not_the_quick_starts_is_named(tmp_path):
    commands, files, vectors = the_commands(the_readme()), tracked_files(), the_vectors()
    assert command_problems(commands + [commands[1]], files, vectors) == ["4 commands, not three"]
    missing = [commands[0], commands[1].replace("credential.json", "credential_nope.json"), commands[2]]
    assert any("which a clone does not hold" in p for p in command_problems(missing, files, vectors))
    not_a_vector = [commands[0], commands[1].replace("tests/fixtures/tb_golden/credential.json",
                                                     "tests/fixtures/tb_golden/proof.json"), commands[2]]
    assert any("does not list as a test vector" in p for p in command_problems(not_a_vector, files, vectors))
    assert "tests/fixtures/tb_golden/proof.json" in files
    another_clone = ["git clone https://github.com/someone/else.git", commands[1], commands[2]]
    assert command_problems(another_clone, files, vectors) == [f"the first command is not {CLONE!r}"]
    other_program = [commands[0], commands[1].replace("talima_verify.py", "nexus_verify.py"), commands[2]]
    assert any("not talima-verify run from the clone" in p for p in command_problems(other_program, files, vectors))
    no_file = [commands[0], "python3 talima/scripts/talima_verify.py --help", commands[2]]
    assert command_problems(no_file, files, vectors) == ["python3 talima/scripts/talima_verify.py --help: names no "
                                                         "file of the clone"]
    unpinned = [commands[0], " ".join(shlex.split(commands[1])[:3]), commands[2]]
    assert command_problems(unpinned, files, vectors) == []
    found = run_problems(unpinned, tmp_path)
    assert len(found) == 1 and found[0].endswith("exit 1, last line 'talima-verify: NOT VERIFIED (1 failure(s))'"), \
        found


def test_f5_companions_a_carriage_return_anywhere_is_named():
    """Delta check 1's D6 and delta check 2's E1 and E2: a CRLF line end and a lone CR in NEXUS's section, and a CR
    in the front, each planted in the bytes read (the front's CR keeps the section's bytes where they were)."""
    data = the_readme()
    section_start = len(data) - OLD_README_LENGTH
    line_end = data.index(b"\n", section_start + OLD_README_LENGTH // 2)
    crlf = data[:section_start - 1] + data[section_start:line_end] + b"\r\n" + data[line_end + 1:]
    space = data.index(b" ", section_start + OLD_README_LENGTH // 2)
    lone = data[:space] + b"\r" + data[space + 1:]
    assert data[space - 1:space] not in (b"\n", b"\r") and data[space + 1:space + 2] not in (b"\n", b"\r")
    front_line_end = data.index(b"\n", data.index(b"The second and third commands"))
    in_front = data[:front_line_end] + b"\r" + data[front_line_end:]
    in_front = in_front[:-OLD_README_LENGTH - 1] + in_front[-OLD_README_LENGTH:]
    for planted, what in ((crlf, "a CRLF in NEXUS's section"), (lone, "a lone CR in NEXUS's section"),
                          (in_front, "a CR in the front")):
        assert len(planted) == len(data) and planted.count(b"\r") == 1, what
        found = front_problems(planted, tracked_files())
        assert any(p.startswith("the README holds 1 carriage return(s)") for p in found), (what, found)


def test_f5_companion_the_readme_is_read_as_git_holds_it():
    assert the_readme() == as_git_holds(README) and hashlib.sha256(split(the_readme())[1]).hexdigest() == \
        OLD_README_SHA256


def test_f5_companion_the_bytes_read_are_the_index_not_the_working_copy(tmp_path):
    """A repository of the test's own whose working copy differs from its index: the reader gives the index's bytes,
    not the working copy's however read (delta check 2's E3: a reader that turned the working copy's CRLF into LF
    passed the earlier witness); and a file git cannot give stops the test (E8)."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    clean.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    for argv in (["git", "init", "-q", str(tmp_path)],):
        assert _proc.run(argv, cwd=tmp_path, env=clean, capture_output=True, timeout=60).returncode == 0
    path = tmp_path / "file.txt"
    path.write_bytes(b"as staged\r\n")
    assert _proc.run(["git", "add", "file.txt"], cwd=tmp_path, env=clean, capture_output=True,
                     timeout=60).returncode == 0
    path.write_bytes(b"as the working copy has it\n")
    assert as_git_holds(path, root=tmp_path) == b"as staged\r\n", "the bytes read are not the index's"
    other = tmp_path / "not-added.txt"
    other.write_bytes(b"x\n")
    with pytest.raises(AssertionError, match="git cannot give not-added.txt from its index"):
        as_git_holds(other, root=tmp_path)
    # The last delta check's F3: the folder given may be below the repository's top; the path is read from it.
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "file.txt").write_bytes(b"inside\n")
    assert _proc.run(["git", "add", "inner/file.txt"], cwd=tmp_path, env=clean, capture_output=True,
                     timeout=60).returncode == 0
    assert as_git_holds(inner / "file.txt", root=inner) == b"inside\n"


def test_f5_companion_the_file_is_read_only_through_the_reader_of_the_index():
    """The last delta check's F4: the_readme read the working copy and every test passed on a checkout whose
    working copy is the index's. Read by AST: each reads through as_git_holds and touches no file itself."""
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    for site in ('the_readme',):
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == site)
        calls = {getattr(c.func, "attr", getattr(c.func, "id", "")) for c in ast.walk(function)
                 if isinstance(c, ast.Call)}
        assert "as_git_holds" in calls and not calls & {"read_bytes", "read_text", "open"}, (site, sorted(calls))
