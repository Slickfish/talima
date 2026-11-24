"""W10c (run/specs/W10c.md S9) — what a built interceptor holds, read from PyInstaller's own list.

    python tests/_archive_reach.py build/<name>/PYZ-00.toc

The release builds `talima-intercept` and then reads the table of contents PyInstaller wrote for the archive
inside it. Every `backend` module it names must be the interceptor's own or one of the standalone set, and the
set is read from `tests/test_w10c_standalone.py` (STANDALONE, by its literal, so nothing is imported): the same
set the import-graph guard holds the source to. A module that is not in the archive cannot be loaded from the
binary by any means, so this is the one reading of the three that does not depend on following imports. Exit 0
when the archive holds nothing else and does hold the interceptor's recorder; 1 with each module named.

Not a test and not collected (the name starts with an underscore); `tests/test_w10c_standalone.py` A1 holds it:
every module of the derived engine, named in a listing, is named here.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

GUARD = Path(__file__).resolve().parent / "test_w10c_standalone.py"
NAMED = re.compile(r"'(backend(?:\.[A-Za-z_][A-Za-z0-9_]*)*)'")


def _constants() -> dict:
    """STANDALONE, INTERCEPT and OPTIONAL_LINK as the guard's source writes them."""
    wanted, out = ("STANDALONE", "INTERCEPT", "OPTIONAL_LINK"), {}
    for node in ast.parse(GUARD.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in wanted:
            out[node.targets[0].id] = ast.literal_eval(node.value)
    assert set(out) == set(wanted), sorted(out)
    return out


def outside(listing: str) -> list:
    """The `backend` modules a listing names that a standalone interceptor may not hold."""
    c = _constants()
    own = lambda m: m == c["INTERCEPT"] or m.startswith(c["INTERCEPT"] + ".")     # noqa: E731
    return sorted(m for m in set(NAMED.findall(listing))
                  if m == c["OPTIONAL_LINK"] or not (m in c["STANDALONE"] or own(m)))


def problems(listing: str) -> list:
    c = _constants()
    out = [f"the archive holds {m}, which is outside the standalone set" for m in outside(listing)]
    if f"'{c['INTERCEPT']}.recorder'" not in listing:
        out.append("the archive does not name the interceptor's own recorder: this is a reading of nothing")
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    found = problems(Path(argv[0]).read_text(encoding="utf-8", errors="replace"))
    for line in found:
        print(f"archive-reach: {line}")
    print(f"archive-reach: {'FAIL' if found else 'ok'}: {argv[0]}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
