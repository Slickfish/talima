"""W11 (run/specs/W11.md D9, C1): a document calls an item not built only if the worklist defers it.

WHAT IS HELD HERE
    D1 Over the documents a cold reader is pointed to (README.md and the repository documents it names, the run's
       records and the changelog's dated entries apart), each statement that marks something planned or not built
       names no item id. W11c (the operator's answer of 20 November): what is not built is listed in
       `docs/CONTRACTS.md`'s "Not built yet" section, in plain words and with no item ids, so an id in such a
       statement could not be answered from anything published; before W11c the test read the ids the worklist
       deferred, and the plants below keep that set (PLANTED_DEFERRED), because they test the reading. A statement is a sentence of a paragraph or of a list item, or a table
       row whole; a marked paragraph that ends in a colon marks each item of the list it introduces whole.
    D2 Planted, each named. Witnesses (OQ-16 rule 1), each in a form the source does not write out: a negation and
       a future reached through the pattern's free words ("not yet fully built", "will be properly implemented",
       "will soon be built"), a mark broken across a paragraph's lines, a mark in underscore emphasis, a mark split
       by bold ("**not** built"), an abbreviation inside the marked sentence ("e.g."), and an item in a
       parenthesis after the marked sentence's full stop. Checks of the reading and of the word list, each planting
       a mark MARK writes out, so proving the reading and the list and not the rule: a built item called planned in
       a sentence, a built item in the list under a "not built" heading, a range of items that reaches a built one,
       a table row, a row whose item and mark sit in different cells, a queue, an intention, a draft, a word of its
       own, and an abbreviation the list names ("etc."). Honest, each passing: a deferred item called not built, a
       built item beside no mark, a ledger line's id (`W6-B3`), a built item in another sentence of a list item,
       and a built item after a sentence that ends in bold.

    Not seen, declared: a mark that is none of these patterns (another word for "not built", such as "pending"); a
    mark split by markup other than emphasis or a code mark (a link); a sentence that an abbreviation outside the
    list cuts in two before a capital; and a statement that names no item (ARCHITECTURE and ROADMAP were found
    stale that way, by reading).

LAW 16. Run on 372df8c (the documents before W11): D1 fails there, naming README's "Planned for v1.0" list (W8, W9,
W10b), STANDARDS_MAPPING's lines that call W8, W9 and W10b planned, the runbook's head, and the status page's six
lines (11, 15, 16, 18, 26, 102) that did the same.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import pytest

from tests._tracked import REPO, tracked

# A mark that something is not built, by pattern rather than by literal: a negated build or implementation (up to
# three words between "not" and the build), or a future one (will, shall, is to, are to, yet to, still to, remains
# to; up to two words before "be" and three after it); a plan, a queue, an intention, a draft. Marks are read with
# emphasis and code marks (`*`, `_`, a backtick) taken out, so they hide none; an item's boundary counts `_` too.
_B, _E, _DONE = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])", r"(?:built|implemented)"
MARK = re.compile(rf"{_B}not\s+(?:\w+\s+){{0,3}}{_DONE}{_E}"
                  rf"|{_B}(?:will|shall|(?:is|are|yet|still|remains?)\s+to)\s+(?:\w+\s+){{0,2}}be\s+(?:\w+\s+){{0,3}}"
                  rf"{_DONE}{_E}"
                  rf"|{_B}un{_DONE}{_E}|{_B}plann(?:ed|ing){_E}|{_B}queued{_E}|{_B}intended{_E}|{_B}drafted{_E}",
                  re.I)
ITEM = re.compile(rf"{_B}W(\d+)([a-z]?){_E}(?!-[A-Z])")          # not a BACKLOG id, `W6-B3`
# A full stop that ends an abbreviation (dotted letters, as "e.g."; or cf, vs, viz, approx, incl, etc, al) ends no
# sentence.
ABBREV = re.compile(r"(?:(?<![A-Za-z])[a-z](?:\.[a-z])+|\b(?:cf|vs|viz|approx|incl|etc|al))\.$", re.I)
PLAIN = re.compile(r"[*_`]+")                                  # emphasis and code marks, taken out to read a mark


def _marked(text: str) -> bool:
    return bool(MARK.search(PLAIN.sub("", text)))
RANGE = re.compile(r"\bW(\d+) to W(\d+)\b")
LINK = re.compile(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)|`([A-Za-z0-9_./-]+\.md)`")
# The run's records, its ledgers and the changelog: dated entries, each true of its day, not a statement of what is
# built now (CI's records rule names RUN_*.md; the ledgers and the changelog's entries are the same kind).
RECORDS = re.compile(r"^(RUN_[A-Z_]+\.md|BACKLOG\.md|RULINGS_TAKEN_BY_THE_RUN\.md|CHANGELOG\.md)$")
LIST = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")


def not_built_yet() -> list:
    """What `docs/CONTRACTS.md`'s "Not built yet" section names, in plain words: each list item's bold name."""
    text = (REPO / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
    section = text.split("\n## Not built yet\n", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^- \*\*([^*]+)\*\*", section, re.M)


# The items the worklist deferred when D2's plants were written (W7, W10, W10c, W12 to W14). The plants test the
# reading, not a list, so they keep the set they were written against; D1 reads the published list instead.
PLANTED_DEFERRED = frozenset({"W7", "W10", "W10c", "W12", "W13", "W14"})


def deferred() -> set:
    """The ids D2's plants may call not built (PLANTED_DEFERRED)."""
    return set(PLANTED_DEFERRED)


def reached() -> list:
    """README.md and the repository documents it names (a Markdown link or a path in backticks), the run's
    records and the changelog apart: what a cold reader starting at README is pointed to."""
    known = {(Path(t).relative_to(REPO).as_posix() if Path(t).is_absolute() else PurePosixPath(t).as_posix())
             for t in tracked("*.md", "docs/*.md", "docs/**/*.md")}
    out = ["README.md"]
    for linked, named in LINK.findall((REPO / "README.md").read_text(encoding="utf-8")):
        rel = PurePosixPath(linked or named).as_posix()
        if not urlsplit(rel).scheme and rel in known and not RECORDS.match(rel) and rel not in out:
            out.append(rel)
    return out


def _sentences(text: str) -> list:
    """Sentences: a full stop, a question mark or an exclamation mark ends one when a capital, `*` or a backtick
    follows (after any closing markup: `**`, a backtick, a bracket), but not after an abbreviation ABBREV names; an
    abbreviation outside it, before a capital, ends a sentence there."""
    out = []
    for piece in re.split(r"(?<=[.!?])[*_`\")\]]*\s+(?=[A-Z*`])", text):
        if out and ABBREV.search(out[-1]):
            out[-1] += " " + piece
        else:
            out.append(piece)
    return out


def statements(text: str) -> list:
    """(line, marked, text) for each sentence of a paragraph and of a list item, and for each table row whole; a
    marked paragraph that ends in a colon marks each item of the list after it whole; fenced code is skipped."""
    units, para, para_line, fenced = [], [], 0, False

    def flush():
        nonlocal para
        if para:
            units.append([para_line, "para", " ".join(para)])
            para = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            flush()
            fenced = not fenced
            continue
        if fenced:
            continue
        if not line.strip():
            flush()
            continue
        if line.lstrip().startswith("|"):
            flush()
            units.append([n, "row", line])
            continue
        if LIST.match(line):
            flush()
            units.append([n, "item", line])
            continue
        if not para and units and units[-1][1] == "item" and line.startswith(" "):
            units[-1][2] += " " + line.strip()          # a list item's continuation line
            continue
        if not para:
            para_line = n
        para.append(line.strip())
    flush()
    out, carry = [], False
    for line, kind, body in units:
        if kind == "item" and carry:
            out.append((line, True, body))               # under a marked heading: the whole item is marked
            continue
        if kind == "para":
            carry = _marked(body) and body.rstrip().rstrip("*_").rstrip().endswith(":")
        elif kind == "row":
            carry = False
        if kind == "row":                                # a row's cells describe one thing: one statement
            out.append((line, _marked(body), body))
            continue
        for sentence in _sentences(body):
            out.append((line, _marked(sentence), sentence))
    return out


def items_in(text: str) -> set:
    found = {f"W{n}{s}" for n, s in ITEM.findall(text)}
    for a, b in RANGE.findall(text):
        found |= {f"W{k}" for k in range(int(a), int(b) + 1)}
    return found


def unanswered(docs: dict, allowed: set) -> list:
    """(document, line, the items a not-built statement names that the worklist does not defer)."""
    out = []
    for rel, text in docs.items():
        for line, marked, statement in statements(text):
            if marked and (named := sorted(items_in(statement) - allowed)):
                out.append((rel, line, named))
    return out


def test_d1_a_not_built_statement_names_no_item_id_and_the_published_list_exists():
    names = not_built_yet()
    docs = {rel: (REPO / rel).read_text(encoding="utf-8") for rel in reached()}
    print(f"\nD1 not built yet: {names}; {len(docs)} documents reached from README: {sorted(docs)}")
    # W10c: the interceptor is built and off the list (six entries became five).
    assert len(names) >= 5 and {"The remote connector", "The work-unit ledger"} <= set(names), "non-vacuity: the list"
    assert "The interceptor" not in names, "the interceptor is built (W10c): docs/INTERCEPTOR.md"
    assert {"README.md", "SECURITY.md", "docs/CONTRACTS.md"} <= set(docs), "non-vacuity: what README reaches"
    assert unanswered(docs, set()) == [], unanswered(docs, set())


PLANTS = {
    # Witnesses: each in a form the source does not write out.
    "a negation with words inside it": ("The recorder (W10b) is not yet fully built.\n", ["W10b"]),
    "a future with a word inside it": ("The verifier (W9) will be properly implemented after the UAT.\n", ["W9"]),
    "a mark broken across a paragraph's lines": ("The recorder (W10b) is not\nyet built.\n", ["W10b"]),
    "a mark in underscore emphasis": ("The credentials (W9) are _planned_.\n", ["W9"]),
    "a mark split by bold": ("The recorder (W10b) is **not** built.\n", ["W10b"]),
    "a future with a word before be": ("The verifier (W9) will soon be built.\n", ["W9"]),
    "an abbreviation inside the marked sentence": ("Planned for later (e.g. W9).\n", ["W9"]),
    "an item in a parenthesis after the full stop": ("The proofs are planned. (W8)\n", ["W8"]),
    # Checks of the reading and of the word list: each mark is one MARK writes out.
    "a built item called planned": ("The verifier (W9) is planned for v1.0.\n", ["W9"]),
    "a built item under a not-built heading": ("**Not built yet:**\n\n- the recorder (W10b)\n- the proofs (W8)\n",
                                               ["W10b", "W8"]),
    "a range that reaches a built item": ("The stages W8 to W10 are planned.\n", ["W8", "W9"]),
    "a table row": ("| proofs | W8 and W9, planned for v1.0 |\n", ["W8", "W9"]),
    "a row whose item and mark sit in different cells": ("| the proofs (built in W8) | Drafted (Phase 3) |\n", ["W8"]),
    "a queue": ("Queued: the proofs (W8) and the credentials (W9).\n", ["W8", "W9"]),
    "an intention": ("The deployment (W3) is intended, design and not code.\n", ["W3"]),
    "a draft": ("The proofs (W8) are drafted, not shipped.\n", ["W8"]),
    "a word of its own": ("The credentials (W9) are unbuilt.\n", ["W9"]),
    "an abbreviation the list names": ("Planned: the proofs, the credentials, etc. W8 and W9.\n", ["W8", "W9"]),
}
HONEST = {
    "a deferred item called not built": "The interceptor (W10c) is not built yet, and the remote connector (W10).\n",
    "a ledger line's id": "The confirmation reference is not built (BACKLOG W6-B3).\n",
    "a built item in another sentence of a list item": "- W8 built the proofs. The next phases are planned.\n",
    "a built item after a sentence that ends in bold": "**The section is intended.** The image (W3) is built.\n",
    "a built item beside no mark": "W9 signs every block; W10b records every action.\n",
}


@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_d2_a_built_item_called_not_built_is_named(plant):
    text, expect = PLANTS[plant]
    found = unanswered({"planted.md": text}, deferred())
    assert sorted({i for _d, _l, named in found for i in named}) == sorted(expect), found


@pytest.mark.parametrize("honest", sorted(HONEST))
def test_d2b_an_honest_statement_passes(honest):
    assert unanswered({"honest.md": HONEST[honest]}, deferred()) == []
