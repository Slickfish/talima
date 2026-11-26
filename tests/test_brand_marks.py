"""Amendment 11 (the operator, 24 November; the operator's answer of 26 November): the Talima marks in docs/brand/.

WHAT IS HELD HERE
    B1  docs/brand/README.md lists every tracked file of the folder but itself, each once, and nothing else, and the
        sha256 its table gives for each is the file's own as git's index holds it (what a commit made now would
        hold, whatever the working copy's line ends), so a Windows checkout that wrote CRLF reads the same bytes,
        and a mark committed with CRLF is named (delta check 1's D2).
        `.gitattributes` keeps the marks exactly as committed on every checkout (`docs/brand/*.svg -text`).
    B2  The three marks Amendment 11 adds (clay, ink and paper) are the operator's files of 26 November less their
        C2PA manifest: the operator's answer of 26 November, as for the first three marks on 20 November, removes
        the `<metadata>` element and the manifest's namespace declaration and keeps every other byte as supplied.
        Each is held by its sha256 here (THE_THREE, each written out), and the sha256 of the file as supplied is
        written beside it for the record.
    B3  No mark in the folder holds more than a drawing, read as an allow-list over the parsed SVG (the verifier's
        V2: a substring search passed `svg:script` and its kin under a prefix): the elements, attributes and value
        characters the six marks use and nothing else, in any namespace or under any prefix; no markup that is not
        an element; no text; and every byte printable ASCII or a line end or a tab (delta check 1's D1: a mark in
        UTF-16 carried markup past a search made in UTF-8). A mark is drawn by a stranger's browser, and the README
        shows the clay one on the public repository's front page. Each parses as SVG with the 64 x 64 view box.
    B4  Companions, each planting what B1 to B3 do not name: a file with no row, a row with no file, a byte moved, a
        row given twice; the manifest left in, a script under the SVG namespace's own prefix, a foreign object and
        a style under a prefix, an event handler, an outside reference by xlink, a fill that fetches, a comment,
        an entity declared, a style sheet called in, text inside, a drawing that is not SVG, a mark in UTF-16 with
        a byte-order mark, either order, carrying a style sheet; and a mark committed with CRLF line ends, which is
        named, beside the rule that keeps a checkout from writing them.

WHAT IS NOT HELD HERE
    How a mark looks: the drawing and its colours are the operator's, and the table says what each shows. That the
    supplied files less their manifest are these bytes was checked when they were added (RUN_LOG, Amendment 11):
    the uploads are not in the repository, so a test cannot read them again.
"""
from __future__ import annotations

import ast
import hashlib
import os
import pathlib
import re
import subprocess
import xml.etree.ElementTree as ET

import pytest

from tests import _proc
from tests._tracked import REPO, tracked

BRAND = REPO / "docs" / "brand"
README = BRAND / "README.md"
ROW = re.compile(r"^\| `([^`/]+)` \| ([^|]+?) \| `([0-9a-f]{64})` \|$", re.M)
# The three Amendment 11 adds: (the file, its sha256 as published, the sha256 of the file as the operator supplied it)
THE_THREE = {
    "talima-mark-clay.svg": ("e7285eb8ca9e0ed1da6bb1e94b243659d8e4eb1d407158366f344743ca3d4686",
                             "eb9a3350779e45059f9c0ba97b2a7283e238ed2c4e76b469c411ab274b12937a"),
    "talima-mark-ink.svg": ("4d74908db24559988a05166b1b4a800a2a4c0cc2e81477c8c555bef56ac85d42",
                            "a66b47c31ce2ce846a2799a6125401715edc250864bb283e52dc079930aeb9c6"),
    "talima-mark-paper.svg": ("4d1fb5bd56c6e9717c7a4cb9bddf058df25653cc795f300635dae72aab9cf52a",
                              "ac98982f6ebef0527c32cee0182d3bda6374d293e3383c698a4cf13a9e8acaad"),
}
SVG_NS = "http://www.w3.org/2000/svg"
# What a mark may hold, read off the six marks and nothing more: these SVG elements, these attributes, values of
# these characters, and no text. Everything else, whatever its name or prefix, is refused.
ELEMENTS = {"svg", "circle", "polygon", "path"}
ATTRIBUTES = {"viewBox", "width", "height", "role", "aria-label", "fill", "fill-rule", "stroke", "stroke-width",
              "cx", "cy", "r", "points", "d"}
VALUE = re.compile(r"[A-Za-z0-9#., -]*")
TEXT_BYTES = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}


def listed(readme: str) -> dict:
    """The README's table: file -> (what it shows, sha256)."""
    rows = ROW.findall(readme)
    return {name: (what, sha) for name, what, sha in rows}


def listing_problems(files: dict, readme: str) -> list:
    """Every way the folder and its README's table disagree; `files` is name -> bytes, the README itself left out."""
    problems = []
    names = [name for name, _what, _sha in ROW.findall(readme)]
    problems += [f"{name}: listed {names.count(name)} times" for name in sorted(set(names)) if names.count(name) > 1]
    table = listed(readme)
    problems += [f"{name}: in the folder, and no row of the README lists it" for name in sorted(set(files) - set(table))]
    problems += [f"{name}: a row of the README, and no such file" for name in sorted(set(table) - set(files))]
    for name in sorted(set(files) & set(table)):
        got = hashlib.sha256(files[name]).hexdigest()
        if got != table[name][1]:
            problems.append(f"{name}: its sha256 is {got}, and the README gives {table[name][1]}")
    return problems


def mark_problems(name: str, data: bytes) -> list:
    """Every way one mark holds more than a drawing: markup that is not an element (a document type, an entity, a
    comment, a processing instruction), an element or an attribute the allow-list does not hold, in any namespace
    or under any prefix, a value of other characters, or text."""
    problems = []
    if not set(data) <= TEXT_BYTES:
        return [f"{name}: not ASCII text (a byte outside printable ASCII, a line end and a tab)"]
    text = data.decode("ascii")
    problems += [f"{name}: markup that is not an element ({marker}...)" for marker in ("<!", "<?") if marker in text]
    try:
        root = ET.fromstring(data)
    except ET.ParseError as err:
        return problems + [f"{name}: does not parse as XML ({err})"]
    for element in root.iter():
        namespace, _, local = element.tag[1:].partition("}") if element.tag.startswith("{") else ("", "", element.tag)
        if namespace != SVG_NS or local not in ELEMENTS:
            problems.append(f"{name}: an element that is not a drawing's: {element.tag}")
        for attribute, value in element.attrib.items():
            if attribute not in ATTRIBUTES:
                problems.append(f"{name}: an attribute that is not a drawing's: {attribute} on {local}")
            elif not VALUE.fullmatch(value):
                problems.append(f"{name}: a value that is not a drawing's: {attribute}={value!r}")
        if (element.text or "").strip() or (element.tail or "").strip():
            problems.append(f"{name}: text inside the drawing, at {local}")
    if root.tag != "{" + SVG_NS + "}svg" or root.get("viewBox") != "0 0 64 64":
        problems.append(f"{name}: not an SVG drawing with the 64 x 64 view box ({root.tag}, {root.get('viewBox')})")
    return problems


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


def the_folder() -> dict:
    return {p.name: as_git_holds(p) for p in tracked("docs/brand") if p != README}


def test_b1_the_readme_lists_every_mark_once_with_its_sha256():
    files = the_folder()
    assert len(files) >= 6, f"non-vacuity: the folder holds {sorted(files)}"
    assert listing_problems(files, README.read_text(encoding="utf-8")) == []


def test_b2_the_three_marks_of_amendment_11_are_the_supplied_files_less_their_manifest():
    files = the_folder()
    for name, (published, supplied) in THE_THREE.items():
        assert name in files, f"{name} is not in docs/brand/"
        assert hashlib.sha256(files[name]).hexdigest() == published, f"{name} is not the file published"
        assert published != supplied
    table = listed(README.read_text(encoding="utf-8"))
    assert {name: table[name][1] for name in THE_THREE} == {name: pair[0] for name, pair in THE_THREE.items()}


def test_b3_no_mark_carries_a_manifest_a_script_or_an_outside_reference():
    files = the_folder()
    marks = {name: data for name, data in files.items() if name.endswith(".svg")}
    assert len(marks) >= 6, sorted(marks)
    assert [p for name, data in sorted(marks.items()) for p in mark_problems(name, data)] == []


# --- B4: the companions -----------------------------------------------------------------------------------------

def test_b4_companions_the_folder_and_the_table_disagreeing_is_named():
    files, readme = the_folder(), README.read_text(encoding="utf-8")
    extra = dict(files, **{"talima-mark-unlisted.svg": files["talima-mark-ink.svg"]})
    assert listing_problems(extra, readme) == ["talima-mark-unlisted.svg: in the folder, and no row of the README "
                                               "lists it"]
    fewer = {name: data for name, data in files.items() if name != "talima-mark-paper.svg"}
    assert listing_problems(fewer, readme) == ["talima-mark-paper.svg: a row of the README, and no such file"]
    moved = dict(files, **{"talima-mark-clay.svg": files["talima-mark-clay.svg"].replace(b"#C2410C", b"#C2410D", 1)})
    named = listing_problems(moved, readme)
    assert len(named) == 1 and named[0].startswith("talima-mark-clay.svg: its sha256 is "), named
    row = next(line for line in readme.splitlines() if line.startswith("| `talima-mark-ink.svg` |"))
    twice = readme.replace(row, row + "\n" + row, 1)
    assert listing_problems(files, twice) == ["talima-mark-ink.svg: listed 2 times"]


PLANTED = [
    ("the manifest left in", b"<circle", b'<metadata><c2pa:manifest xmlns:c2pa="http://c2pa.org/manifest">AAAA'
     b"</c2pa:manifest></metadata><circle", "an element that is not a drawing's"),
    ("a script under the SVG namespace's own prefix", b"<circle",
     b'<svg:script xmlns:svg="http://www.w3.org/2000/svg">alert(1)</svg:script><circle',
     "an element that is not a drawing's"),
    ("a foreign object under a prefix", b"<circle",
     b'<s:foreignObject xmlns:s="http://www.w3.org/2000/svg" width="1" height="1"/><circle',
     "an element that is not a drawing's"),
    ("a style under a prefix", b"<circle", b'<s:style xmlns:s="http://www.w3.org/2000/svg">circle{fill:red}</s:style>'
     b"<circle", "an element that is not a drawing's"),
    ("an event handler", b'role="img"', b'role="img" onload="alert(1)"', "an attribute that is not a drawing's"),
    ("an outside reference by xlink", b"<circle", b'<use xmlns:xlink="http://www.w3.org/1999/xlink" '
     b'xlink:href="https://example.invalid/x.svg#a"/><circle', "an element that is not a drawing's"),
    ("a fill that fetches", b'fill="none"', b'fill="url(https://example.invalid/p.svg#g)"',
     "a value that is not a drawing's"),
    ("a comment", b"<circle", b"<!-- a note --><circle", "markup that is not an element"),
    ("an entity declared", b"<svg", b'<!DOCTYPE svg [<!ENTITY e "x">]><svg', "markup that is not an element"),
    ("a style sheet called in", b"<svg", b'<?xml-stylesheet href="https://example.invalid/a.css"?><svg',
     "markup that is not an element"),
    ("text inside the drawing", b"/><polygon", b"/>words<polygon", "text inside the drawing"),
]


@pytest.mark.parametrize("what, old, new, named", PLANTED, ids=[p[0].replace(" ", "-") for p in PLANTED])
def test_b4_companions_a_mark_that_carries_more_than_its_drawing_is_named(what, old, new, named):
    data = (BRAND / "talima-mark-clay.svg").read_bytes()
    assert data.count(old) >= 1, f"the plant's anchor is not in the mark: {old!r}"
    found = mark_problems("planted.svg", data.replace(old, new, 1))
    assert any(p.startswith(f"planted.svg: {named}") for p in found), (what, found)


def test_b4_companion_a_mark_committed_with_crlf_is_named_and_the_rule_keeps_a_checkout_from_writing_them():
    files, readme = the_folder(), README.read_text(encoding="utf-8")
    crlf = dict(files, **{"talima-mark-ink.svg": files["talima-mark-ink.svg"].replace(b"\n", b"\r\n")})
    named = listing_problems(crlf, readme)
    assert len(named) == 1 and named[0].startswith("talima-mark-ink.svg: its sha256 is "), named
    rules = (REPO / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "docs/brand/*.svg -text" in rules, "no rule keeps the marks as committed on a checkout"
    clay = REPO / "docs" / "brand" / "talima-mark-clay.svg"
    assert as_git_holds(clay) == files["talima-mark-clay.svg"] and hashlib.sha256(files["talima-mark-clay.svg"]) \
        .hexdigest() == THE_THREE["talima-mark-clay.svg"][0], "the bytes read are not git's"


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_b4_companion_a_mark_in_utf16_carrying_a_style_sheet_is_named(encoding):
    """Delta check 1's D1: ElementTree reads a byte-order mark; a search made in UTF-8 did not."""
    text = (BRAND / "talima-mark-clay.svg").read_bytes().decode("ascii")
    planted = '<?xml-stylesheet href="https://example.invalid/a.css"?>' + text
    data = ("\ufeff" + planted).encode(encoding)
    found = mark_problems("planted.svg", data)
    assert found == ["planted.svg: not ASCII text (a byte outside printable ASCII, a line end and a tab)"], found


def test_b4_companion_a_drawing_that_is_not_svg_is_named():
    data = (BRAND / "talima-mark-ink.svg").read_bytes()
    plain = data.replace(b'xmlns="http://www.w3.org/2000/svg"', b"", 1)
    found = mark_problems("planted.svg", plain)
    assert "planted.svg: not an SVG drawing with the 64 x 64 view box (svg, 0 0 64 64)" in found, found
    assert mark_problems("planted.svg", data[:-20])[0].startswith("planted.svg: does not parse as XML")


def test_b4_companion_the_bytes_read_are_the_index_not_the_working_copy(tmp_path):
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


def test_b4_companion_the_file_is_read_only_through_the_reader_of_the_index():
    """The last delta check's F4: the_folder read the working copy and every test passed on a checkout whose
    working copy is the index's. Read by AST: each reads through as_git_holds and touches no file itself."""
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    for site in ('the_folder',):
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == site)
        calls = {getattr(c.func, "attr", getattr(c.func, "id", "")) for c in ast.walk(function)
                 if isinstance(c, ast.Call)}
        assert "as_git_holds" in calls and not calls & {"read_bytes", "read_text", "open"}, (site, sorted(calls))
