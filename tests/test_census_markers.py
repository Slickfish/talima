"""The guard census reads a guard that reads the contracts page (BACKLOG W11c-B5).

`scripts/guard_estate_census.py` E.1 calls a test function a guard by markers in its source. One marker was the
constitution's file name, because the tests that hold the documented contracts read that file. W11c moved those
contracts to `docs/CONTRACTS.md` and repointed the tests, and the census then stopped counting them: 374 guard
functions became 355 with no guard removed. The marker follows the page. This holds it, derived: every tracked
test function whose source names the contracts page is a guard to E.1.
"""
from __future__ import annotations

import ast

from scripts.guard_estate_census import e1
from tests._tracked import REPO, tracked

PAGE = "CONTRACTS.md"


def readers() -> dict:
    """{tracked test file: the test functions whose own source names the contracts page}."""
    out = {}
    for path in tracked("tests/test_*.py"):
        if path.name == "test_census_markers.py":
            continue
        source = path.read_text(encoding="utf-8")
        if PAGE not in source:
            continue
        lines = source.splitlines()
        named = [fn.name for fn in ast.parse(source).body
                 if isinstance(fn, ast.FunctionDef) and fn.name.startswith("test_")
                 and PAGE in "\n".join(lines[fn.lineno - 1: fn.end_lineno])]
        if named:
            out[path] = named
    return out


def test_e1_counts_every_test_function_that_reads_the_contracts_page_as_a_guard():
    found = readers()
    assert len(found) >= 5, f"only {len(found)} test files read the contracts page: the derivation lost them"
    census = e1(sorted(found))
    missed = []
    for path, names in found.items():
        entry = census.get(path.relative_to(REPO).as_posix()) or {}
        guards = {e["name"] for e in entry.get("tests", []) if e["guard"]}
        missed += [f"{path.name}::{name}" for name in names if name not in guards]
    assert missed == [], f"E.1 does not count these readers of the contracts page as guards: {missed}"
