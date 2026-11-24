"""Packet I.1 — environment parity: text is UTF-8, explicitly, everywhere.

THE DEFECT THIS GUARDS
    The suite read 581 passed in the sandbox and 15 failed / 566 passed on the
    operator's Windows machine, at the same commit. Every failure was

        UnicodeDecodeError: 'charmap' codec can't decode byte 0x8f ...

    from a `read_text()` with no `encoding=`, which uses the platform locale
    default: UTF-8 in the sandbox, cp1252 on Windows.

    **The frontend files are valid UTF-8.** This was the tests' defect, not
    the files'. That distinction cost two diagnostic round trips and is the
    reason `test_text_assets_are_valid_utf8` below exists and phrases its
    failure the way it does: had the files genuinely held bad bytes, "fix the
    tests" would have made the suite pass over corrupt assets.

    It reached main because these sentinels were written and verified in one
    environment and never exercised in the other — pack §15.4, *presence
    verified, behaviour unverified*, at the environment level.

WHY THERE ARE TWO GUARDS AND NOT ONE
    A grep for `read_text()` found 27 call sites. Running the suite under
    `PYTHONWARNDEFAULTENCODING=1 -W error::EncodingWarning` found a site grep
    could not see: `subprocess.run(..., text=True)` decodes its pipes with the
    locale default too. So the static guard covers the forms we know to name,
    and the runtime sweep is what finds the forms we do not — the command is
    documented in `test_runtime_sweep_command_is_recorded` so it does not live
    only in a commit message.
"""
import ast
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARDED_DIRS = ("tests", "scripts")

# Every text asset the suite reads and asserts against. Listed explicitly
# rather than globbed: the point is that THESE files are known-good UTF-8, so
# a decode failure here means the file changed, not that a reader guessed.
TEXT_ASSETS = (
    "frontend/nexus_ui.html",
    "frontend/nexus_memory.html",
    "docs/CONTRACTS.md",
)

# Sites outside this packet's scope that still read or write text with the
# locale default. Pinned as an explicit exception — an INVERTED sentinel whose
# passing is the finding (pack §15.4 pattern). Fails when the set changes in
# either direction, so fixing one is a deliberate, recorded act.
#
# Both are WRITES, which is worse than a read: the content is model- and
# user-derived, and the endpoint audit (§3.6) proved that path is unvalidated.
# On Windows a belief containing an em dash raises UnicodeEncodeError and the
# write fails outright.
#
#   backend/eval/ragas.py:120   `open(self.log_path, "a")` — the eval metrics
#                               log. Found by the runtime sweep.
#   backend/memory/kairos.py:521 `path.write_text(json.dumps(snap, ...))` —
#                               the cortex snapshot that the archive chain
#                               hashes. Found by the STATIC guard, not the
#                               sweep: each tool caught something the other
#                               missed, which is the argument for keeping
#                               both rather than picking the better one.
KNOWN_LOCALE_DEPENDENT_OUTSIDE_SCOPE = frozenset({
    "backend/eval/ragas.py",
    "backend/memory/kairos.py",
})


def _sources(dirs=GUARDED_DIRS):
    for d in dirs:
        yield from sorted((ROOT / d).rglob("*.py"))


# ---------------------------------------------------------------------------
# The assets themselves
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel", TEXT_ASSETS)
def test_text_assets_are_valid_utf8(rel):
    """Assert on BYTES -> UTF-8, never via the locale default.

    Reading with `read_text(encoding="utf-8")` would also pass, but this
    decodes the raw bytes deliberately so the failure can distinguish the two
    causes that look identical from a stack trace:

      * decode fails HERE          -> the FILE has bad bytes. Fix the file.
                                      Do not touch the tests.
      * decode succeeds here but a
        test still UnicodeDecodeErrors -> that TEST omitted `encoding=` and
                                      is using the platform default. Fix the
                                      call site, not the file.
    """
    path = ROOT / rel
    assert path.exists(), f"{rel} is missing"
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        pytest.fail(
            f"{rel} IS NOT VALID UTF-8 — byte {e.start} ({raw[e.start]:#04x}).\n"
            f"This is a FILE defect, not a test defect. Do NOT 'fix' this by "
            f"changing how tests read it: that would make the suite pass over "
            f"a corrupt asset. Repair the file's encoding.")
    assert text, f"{rel} decoded to nothing"


def test_assets_actually_contain_non_ascii():
    """NON-VACUITY. If every asset were pure ASCII, the guards in this file
    would be protecting against nothing and would pass forever while the
    class of defect quietly returned.

    This is the assertion that keeps the rest of the file honest: it proves
    the assets contain exactly the bytes that make cp1252 fail.
    """
    offenders = {}
    for rel in TEXT_ASSETS:
        raw = (ROOT / rel).read_bytes()
        if any(b > 0x7F for b in raw):
            offenders[rel] = sum(1 for b in raw if b > 0x7F)
    assert offenders, (
        "no guarded asset contains a non-ASCII byte — these encoding guards "
        "are currently vacuous. Either the assets were flattened to ASCII "
        "(check why) or TEXT_ASSETS no longer names the right files.")


def test_cp1252_would_fail_on_these_assets():
    """The operator's failure, reproduced here rather than taken on trust.

    Running the real Windows codec against the real files is the only way to
    show, from a Linux sandbox, that the diagnosis is right and the fix is
    aimed at the right thing.
    """
    reproduced = []
    for rel in TEXT_ASSETS:
        raw = (ROOT / rel).read_bytes()
        try:
            raw.decode("cp1252")
        except UnicodeDecodeError as e:
            reproduced.append((rel, e.start))
    assert reproduced, (
        "cp1252 now decodes every guarded asset cleanly, so this test no "
        "longer reproduces the Windows failure it was written for")


# ---------------------------------------------------------------------------
# The static guard — a control, not a convention
# ---------------------------------------------------------------------------

# `.read_text()` / `.write_text(...)` with no `encoding=` in the call.
_UNENCODED_TEXT_IO = re.compile(
    r"\.(?:read_text|write_text)\((?![^)]*encoding\s*=)[^)]*\)")
# `open(...)` in text mode with no `encoding=`. Binary modes are exempt —
# they never decode.
_UNENCODED_OPEN = re.compile(
    r"(?<![.\w])open\((?![^)]*encoding\s*=)(?![^)]*['\"][rwax]b)[^)]*\)")
# `subprocess.*(text=True)` / `universal_newlines=True` with no `encoding=` —
# the form the grep missed and the runtime sweep caught.
_UNENCODED_SUBPROCESS = re.compile(
    r"(?:text\s*=\s*True|universal_newlines\s*=\s*True)")


def _logical_statement(lines: list[str], start: int) -> str:
    """The full call starting at `start`, following it across line breaks.

    A line-by-line scan false-positives on any call whose `encoding=` sits on
    a continuation line — which my own first fix in `verify_packet_f.py` did
    immediately. Balancing parentheses is the difference between a guard and
    a nuisance, and a guard that cries wolf gets disabled.
    """
    depth, out = 0, []
    for line in lines[start:]:
        out.append(line)
        depth += line.count("(") - line.count(")")
        if depth <= 0:
            break
    return "\n".join(out)


def _scan(pattern, *, roots=GUARDED_DIRS, skip_self=True):
    """Line-anchored scan. Correct ONLY when the matched line is also the
    call's opening line, which holds for `open(`/`read_text(`/`write_text(`.
    It is NOT correct for a kwarg on a continuation line — balancing from
    there sees depth 0 and returns one line. M.4 hit exactly that with
    `text=True`; use `_decoding_subprocess_calls()` for anything anchored on
    a kwarg rather than a call."""
    hits = []
    for path in _sources(roots):
        if skip_self and path.name == Path(__file__).name:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#"):
                continue
            if not pattern.search(line):
                continue
            if "encoding=" in _logical_statement(lines, i):
                continue        # the encoding is on a continuation line
            # M.5 — POSIX at the point of derivation. This used to render
            # `tests\\foo.py` on Windows and be repaired downstream with a
            # `.replace("\\", "/")` at the one comparison that noticed.
            hits.append(
                f"{path.relative_to(ROOT).as_posix()}:{i + 1}: {line.strip()}")
    return hits


# ---------------------------------------------------------------------------
# M.4 — the class, not the instance
# ---------------------------------------------------------------------------
# Packet I fixed one `subprocess.run(text=True)` and swept only file reads.
# M.1 then found `test_encoding_parity.py:276` was a sibling that sweep had
# missed. M.4 is the third recurrence, and the most expensive: on Windows a
# script printed a cp1252 em dash (byte 0x97), `_readerthread` died decoding
# it as UTF-8, `subprocess.run` swallowed the thread's exception, `proc.stdout`
# came back None, and EIGHT assertions failed with TypeError for reasons that
# had nothing to do with what they assert.
#
# So the rule is now stated over the whole class: any subprocess call that
# DECODES must say both how to decode and what to do when decoding fails.
# A pure-bytes call decodes nothing and is exempt — a guard that fires on
# correct code gets disabled, and this file's own docstring says so.

# W4b: a test starts its children through tests/_proc.py (`_proc.run`,
# `_proc.Child`), whose text mode decodes as Popen's does, so the same two
# rules hold there; without this a converted call would leave the ratchet.
_SUBPROCESS_CALL = re.compile(
    r"(?:subprocess\.(?:run|Popen|check_output|check_call|call)|_proc\.(?:run|Child))\s*\(")
_DECODING_KWARG = re.compile(
    r"text\s*=\s*True|universal_newlines\s*=\s*True|encoding\s*=")


def _decoding_subprocess_calls(roots=GUARDED_DIRS):
    """(location, statement) for every subprocess call that decodes output."""
    found = []
    for path in _sources(roots):
        if path.name == Path(__file__).name:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#") or not _SUBPROCESS_CALL.search(line):
                continue
            stmt = _logical_statement(lines, i)
            if not _DECODING_KWARG.search(stmt):
                continue                     # bytes mode — nothing to decode
            # M.5 — POSIX at derivation. M.4 wrote this line with the very
            # defect M.1 had just catalogued: its `loc` is compared against
            # KNOWN_STRICT_DECODE_SITES, a set of posix literals, so the
            # exemption would not have matched on Windows.
            found.append(
                (f"{path.relative_to(ROOT).as_posix()}:{i + 1}", stmt))
    return found



def test_no_unencoded_text_io_under_tests_or_scripts():
    """`read_text()`/`write_text()` without an explicit encoding.

    A test that reads a pure-ASCII file today fails the day someone adds an
    em dash — so the rule is every call site, not only the ones that have hurt
    us. Pack §4: *a documented rule is not a control.* This is the control.
    """
    hits = _scan(_UNENCODED_TEXT_IO)
    assert not hits, (
        "file read/write without an explicit encoding (uses the platform "
        "locale default — cp1252 on Windows):\n  " + "\n  ".join(hits))


def test_no_unencoded_open_under_tests_or_scripts():
    hits = _scan(_UNENCODED_OPEN)
    assert not hits, (
        "open() in text mode without an explicit encoding:\n  "
        + "\n  ".join(hits))


def test_no_subprocess_text_mode_without_encoding():
    """`text=True` decodes the child's pipes with the locale default.

    Invisible to a search for file reads, which is exactly how it survived
    the first pass of this packet: the runtime sweep found it, and encoding
    it here is how that knowledge stops being one person's memory.

    M.4 REMOVED THE FILE-LEVEL FALLBACK THAT USED TO LIVE HERE, and removing
    it exposed why it had been needed. It read `if "encoding=" not in body`
    — the whole file's text — so a module containing one correctly-encoded
    call exempted every other call in it.

    The reason someone reached for that: `_scan` anchors `_logical_statement`
    on the MATCHED line, and `text=True` usually sits on a continuation line.
    Balancing parentheses from there sees depth 0 immediately and returns one
    line, so an `encoding=` on the following line is invisible. The
    file-level check papered over that with something far too wide.

    Fixed properly instead: this guard now anchors on the `subprocess.…(`
    opening, so the balanced statement is the real one. `_scan` is unchanged
    and still correct for the file-read patterns, where the match line IS the
    call opening.
    """
    bad = [loc for loc, stmt in _decoding_subprocess_calls()
           if re.search(r"text\s*=\s*True|universal_newlines\s*=\s*True", stmt)
           and "encoding=" not in stmt]
    assert not bad, (
        "subprocess in text mode without an explicit encoding:\n  "
        + "\n  ".join(bad))


def test_every_decoding_subprocess_call_declares_an_encoding():
    bad = [loc for loc, stmt in _decoding_subprocess_calls()
           if "encoding=" not in stmt]
    assert not bad, (
        "subprocess call decodes the child's output with the platform "
        "default (cp1252 on Windows):\n  " + "\n  ".join(bad))


# The one place a strict decode is the POINT: M.4's reproduction of the
# Windows failure must be able to fail, so it deliberately omits `errors=`.
# Pinned as a set rather than exempted by filename, and asserted in BOTH
# directions — the KNOWN_LOCALE_DEPENDENT_OUTSIDE_SCOPE pattern. A second
# site appearing here is a claim someone has to make on purpose.
_STRICT_DECODE_MARKER = "ratchet: strict-decode-is-the-point"
KNOWN_STRICT_DECODE_SITES = frozenset({
    "tests/test_subprocess_encoding.py",
})


def test_the_strict_decode_exemption_is_exactly_where_we_think():
    """INVERTED sentinel: its passing is the finding. Fails if the exemption
    grows (someone silenced the ratchet) or shrinks (the reproduction was
    removed, and with it the proof that the byte is fatal)."""
    marked = {loc.split(":", 1)[0] for loc, stmt in _decoding_subprocess_calls()
              if _STRICT_DECODE_MARKER in stmt}
    assert marked == set(KNOWN_STRICT_DECODE_SITES), (
        f"the strict-decode exemption changed.\n"
        f"  expected: {sorted(KNOWN_STRICT_DECODE_SITES)}\n"
        f"  actual  : {sorted(marked)}")


def test_every_decoding_subprocess_call_declares_an_error_policy():
    """`errors=` is the half that the em-dash failure turned on.

    `encoding="utf-8"` alone still defaults to `errors="strict"`, so one
    stray byte raises inside `subprocess._readerthread`. The exception never
    reaches the caller — `run()` joins the thread and returns, and the
    captured stream is `None`. Every downstream assertion then fails with
    `TypeError: argument of type 'NoneType' is not iterable`, which reads
    like the thing under test broke.

    REPLACE, NOT STRICT, and the reason is a safety argument rather than a
    convenience one: in this repo the captured output is read by tests that
    assert on archival behaviour. A mangled character must never be able to
    fail a safety assertion, because an operator reading eight red tests in an
    archiving packet will assume the archiving is wrong. It was not — see
    tests/test_subprocess_encoding.py.
    """
    bad = [loc for loc, stmt in _decoding_subprocess_calls()
           if "errors=" not in stmt and _STRICT_DECODE_MARKER not in stmt]
    assert not bad, (
        "subprocess call decodes without an error policy, so one undecodable "
        "byte kills the reader thread and the captured output becomes None:"
        "\n  " + "\n  ".join(bad))


def test_the_subprocess_ratchet_can_see_a_real_call():
    """Non-vacuity. A ratchet over an empty set is a ratchet over nothing, and
    this one's scope was widened in the same commit that made it stricter."""
    calls = _decoding_subprocess_calls()
    assert len(calls) >= 4, (
        f"the scanner found {len(calls)} decoding subprocess call(s); the "
        f"repo has several, so the pattern has stopped matching")


# ---------------------------------------------------------------------------
# M.5 — the path-separator class, ratcheted at the point of derivation
# ---------------------------------------------------------------------------
# FOURTH OCCURRENCE. The history is the argument for a ratchet rather than a
# fifth fix:
#
#   Packet I   fixed one `subprocess.run(text=True)`, swept file reads only.
#   M.1        `str(Path.relative_to(root))` keyed the import-purity
#              fingerprint, so Windows produced `data\side_effect.txt` and the
#              assertion looking for `data/side_effect.txt` could not match its
#              own guard's output. Fixed at the derivation with `.as_posix()`.
#              The sweep then found `test_encoding_parity.py:203` had the same
#              defect, repaired downstream with a `.replace("\\", "/")` at the
#              one comparison that had noticed.
#   M.4        introduced `_decoding_subprocess_calls` WITH THE SAME DEFECT,
#              hours after cataloguing it — its `loc` is compared against
#              KNOWN_STRICT_DECODE_SITES, a set of posix literals, so the
#              exemption would not have matched on Windows.
#   M.5        every derivation normalised, both downstream repairs removed,
#              and this ratchet so the next one fails HERE rather than on a
#              machine none of us is sitting at.
#
# The rule is narrow on purpose: `relative_to()` used for CONTAINMENT
# (`conftest.py:84`, `test_accrual_integrity.py:19` — call it, catch
# ValueError, discard the value) is correct as written and must not be
# nagged at. Only a derivation that becomes a STRING is a candidate, because
# only a string can be compared against a `/` literal.

def _calls_method(node, name: str) -> bool:
    """True if the subtree calls `.name(...)` anywhere inside it."""
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == name
               for n in ast.walk(node))


def _stringified_path_derivations(roots=GUARDED_DIRS):
    """(location, source) for every `relative_to` result that becomes a string.

    AST, NOT REGEX, and the first draft proved why: a regex flagged
    `test_import_purity.py:81`, which is PROSE inside a docstring describing
    this very defect. A guard that fires on a comment about itself is the
    "cries wolf" failure `_logical_statement`'s docstring warns about, and it
    would have been silenced rather than fixed.

    Honest limit: a module that builds code as a string literal and runs it in
    a subprocess (`test_import_purity.py`'s `_PROBE`) is invisible here,
    because the AST sees a string constant. That probe already normalises,
    and it is covered by its own posix-keys sentinel; nothing else in the
    repo does this.
    """
    found = []
    for path in _sources(roots):
        found += _derivations_in(path.relative_to(ROOT).as_posix(),
                                 path.read_text(encoding="utf-8"))
    return found


def _derivations_in(label: str, source: str):
    """The scanner itself, over one named source. Split out so the ratchet can
    be pointed at a synthetic module and PROVEN to fail — see
    `test_the_path_derivation_ratchet_actually_fails_on_a_violation`."""
    found = []
    for node in ast.walk(ast.parse(source)):
        # `str(<expr containing .relative_to(...)>)`
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "str" and node.args):
            pass
        # an f-string replacement field, `f"...{p.relative_to(x)}..."`
        elif isinstance(node, ast.FormattedValue):
            pass
        else:
            continue
        if not _calls_method(node, "relative_to"):
            continue                    # containment use — nothing derived
        found.append((f"{label}:{node.lineno}", ast.unparse(node)))
    return found


def test_every_derived_path_string_is_posix():
    """The ratchet. A derived path string must be normalised where it is
    made, not where someone later notices it is wrong.

    Downstream repair is what failed three times: it fixes the one comparison
    that happened to break and leaves every other consumer of the same value
    holding a backslash.
    """
    bad = [loc for loc, src in _stringified_path_derivations()
           if "as_posix()" not in src]
    assert not bad, (
        "a Path is converted to a string without `.as_posix()`, so it renders "
        "with backslashes on Windows and will not match a `/` literal:\n  "
        + "\n  ".join(bad))


def test_the_path_derivation_ratchet_can_see_a_real_site():
    """Non-vacuity, part one: the scanner matches real code in this repo."""
    sites = _stringified_path_derivations()
    assert len(sites) >= 2, (
        f"the scanner found {len(sites)} Path->str derivation(s); the repo "
        f"has several, so the pattern has stopped matching")
    # The other swept sites — conftest.py:203 and test_import_purity.py:91 —
    # call `.as_posix()` directly with no `str()` or f-string around them, so
    # they are correct by construction and are not candidates. That is the
    # detector's scope working, not a gap: only a value that becomes a STRING
    # can be compared against a `/` literal.


def test_the_path_derivation_ratchet_actually_fails_on_a_violation():
    """Non-vacuity, part two — and the one that matters.

    Every site in the repo is already correct, so `test_every_derived_path
    _string_is_posix` passes today whether or not the scanner works. Pointing
    it at a synthetic module is the only way to show it can FAIL. Pack §15.4:
    presence verified, behaviour unverified, is how four of this project's
    defects shipped.

    Both violating FORMS are exercised, and both correct forms, because the
    detector treats `str(...)` and an f-string field differently.
    """
    violations = _derivations_in("synthetic.py", textwrap.dedent("""
        bad_str = str(p.relative_to(root))
        bad_fstring = f"{p.relative_to(root)}:1"
        ok_str = str(p.relative_to(root).as_posix())
        ok_fstring = f"{p.relative_to(root).as_posix()}:1"
        containment = p.relative_to(root)          # discarded, not a string
    """))
    # Asserted on CONTENT, not line numbers: the first draft of this test
    # asserted `synthetic.py:5` and was off by one, which would have made the
    # guard's own proof depend on the shape of its fixture.
    sources = [src for _loc, src in violations]
    bad = [src for src in sources if "as_posix()" not in src]

    assert len(violations) == 4, (
        f"the scanner should see four string-producing derivations, saw "
        f"{len(violations)}: {sources}")
    assert len(bad) == 2, (
        f"the scanner should flag exactly the two unnormalised forms, "
        f"flagged {len(bad)}: {bad}")
    assert not any("containment" in src for src in sources), (
        "a containment-only relative_to was flagged — this guard would nag "
        "at conftest.py:84 and test_accrual_integrity.py:19, which are both "
        "correct as written")



def test_no_backslash_survives_a_derivation_helper():
    """The contract, exercised rather than asserted about.

    On Linux `str()` and `as_posix()` agree, so this can only pass here — it
    is a real guard on Windows and documentation here. It runs the actual
    helpers rather than re-deriving their logic, because a re-derivation is
    a second implementation and this packet is about exactly that mistake.
    """
    produced = [loc for loc, _stmt in _stringified_path_derivations()]
    produced += [loc for loc, _stmt in _decoding_subprocess_calls()]
    produced += _scan(_UNENCODED_TEXT_IO, roots=("tests",))
    assert produced, "non-vacuity: no helper produced a location string"
    offenders = [p for p in produced if "\\" in p]
    assert not offenders, (
        f"a derivation helper emitted a non-posix path: {offenders}")


# ---------------------------------------------------------------------------
# The runtime sweep — what static analysis cannot see
# ---------------------------------------------------------------------------

def test_runtime_sweep_command_is_recorded():
    """The sweep that found what grep could not, recorded so it is runnable.

    Python's own tool for this defect class:

        PYTHONWARNDEFAULTENCODING=1 pytest tests/ -W error::EncodingWarning

    It turns every implicit use of the locale default into an error at the
    moment it happens, including inside the stdlib and third-party code —
    which is how `subprocess.run(text=True)` was found. It cannot be enabled
    from pytest config, because it is an interpreter flag that must be set
    before start-up; that is why the static guards above exist as well.

    Asserted as documentation-with-teeth: if the command drifts out of the
    docstring, the packet's method is lost with it.
    """
    doc = test_runtime_sweep_command_is_recorded.__doc__
    assert "PYTHONWARNDEFAULTENCODING=1" in doc
    assert "-W error::EncodingWarning" in doc


def test_known_locale_dependent_sites_outside_scope_are_pinned():
    """INVERTED sentinel: its passing is a finding, not a success.

    The runtime sweep found one site outside this packet's scope. It is
    recorded rather than fixed — Packet I may not touch `backend/` — and
    pinned in both directions so that fixing it is deliberate and removing
    the pin without fixing it is not possible.
    """
    # M.5 — the `.replace("\\", "/")` that used to sit here is gone: `_scan`
    # now normalises at the point of derivation, and two normalisation points
    # is how one of them drifts.
    found = {h.split(":", 1)[0]
             for pattern in (_UNENCODED_OPEN, _UNENCODED_TEXT_IO)
             for h in _scan(pattern, roots=("backend",), skip_self=False)}
    assert found == set(KNOWN_LOCALE_DEPENDENT_OUTSIDE_SCOPE), (
        f"the set of locale-dependent sites in backend/ changed.\n"
        f"  expected: {sorted(KNOWN_LOCALE_DEPENDENT_OUTSIDE_SCOPE)}\n"
        f"  actual  : {sorted(found)}\n"
        f"If it grew, new production code is using the platform default and "
        f"will misbehave on Windows. If it shrank, one was fixed — remove it "
        f"from KNOWN_LOCALE_DEPENDENT_OUTSIDE_SCOPE in the same commit.")


def test_the_guards_would_catch_a_new_offender(tmp_path):
    """Prove the regexes bite, rather than trusting that they do.

    A guard that cannot fail is the defect class this whole sequence of
    packets exists to stop, so it is checked against synthetic offenders and
    against forms that must NOT trip it.
    """
    offenders = [
        'body = PATH.read_text()',
        'PATH.write_text("x")',
        'with open(path) as f:',
        'with open(path, "r") as f:',
        'subprocess.run(cmd, text=True)',
    ]
    allowed = [
        'body = PATH.read_text(encoding="utf-8")',
        'PATH.write_text("x", encoding="utf-8")',
        'with open(path, encoding="utf-8") as f:',
        'with open(path, "rb") as f:',
        'raw = PATH.read_bytes()',
        'subprocess.run(cmd, text=True, encoding="utf-8")',
    ]
    for line in offenders:
        assert (_UNENCODED_TEXT_IO.search(line) or _UNENCODED_OPEN.search(line)
                or _UNENCODED_SUBPROCESS.search(line)), \
            f"guard failed to flag an offender: {line}"
    for line in allowed:
        tripped = (_UNENCODED_TEXT_IO.search(line) or _UNENCODED_OPEN.search(line))
        assert not tripped, f"guard false-positived on: {line}"
