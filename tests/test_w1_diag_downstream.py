"""W1's close, must-fix M6 — `diag_downstream.py` reads the instance read-only.

The third verifier's lens 3 (F3, at b0ad516): the repo-root diagnostic says
"No mutations" and opened `data/kb.db` through `TripleStore`, a read-write
open (law 15: an instance is read-only to every script; law 2). On a store
older than the code the class recreates every table and index its DDL names,
and with no store it creates the folder and the file and reports "Total
triples: 0".

Each test runs the script as the operator does (`python diag_downstream.py`,
its default `--db data/kb.db`, from a directory holding `backend/` and
`scripts/`) in a scratch checkout, on a copy of the pre-run fixture, never
on the repo's `data/`. The environment is the suite's, so conftest's
redirects reach the child (W1's round-1 review, RV-MF5).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
from tests import _proc
import sys
from collections import Counter
from pathlib import Path

from tests._tracked import REPO, tracked
import pytest

FIXTURE = REPO / "tests" / "fixtures" / "pre_run_data" / "kb.db"
SCRIPT = "scripts/diag_downstream.py"                # W11c: moved from the root (the operator's answer)
# An index today's DDL creates (backend/kb/schema.py), dropped to make the
# fixture "a store older than the code".
DROPPED = "idx_triples_tier"


def _checkout(tmp_path: Path) -> Path:
    dest = tmp_path / "checkout"
    for src in tracked("backend", "scripts"):
        target = dest / src.relative_to(REPO)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    return dest


def _run(checkout: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return _proc.run([sys.executable, str(checkout / SCRIPT), *args], cwd=checkout, env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=240)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _store(checkout: Path, *, older: bool) -> Path:
    """The fixture's kb.db where the default --db names it; `older` drops an
    index the DDL creates."""
    db = checkout / "data" / "kb.db"
    db.parent.mkdir(parents=True)
    shutil.copy2(FIXTURE, db)
    if older:
        conn = sqlite3.connect(db)
        try:
            conn.execute(f"DROP INDEX {DROPPED}")
            conn.commit()
        finally:
            conn.close()
    return db


def test_m6_the_store_is_named_first_and_an_older_store_is_left_as_it_was(tmp_path):
    """Pre-fix (law 16), at 1a1a521: the first line was the banner's rule,
    and the store class's open recreated the dropped index, so the store's
    bytes changed under "No mutations"."""
    checkout = _checkout(tmp_path)
    db = _store(checkout, older=True)
    before = _md5(db)
    proc = _run(checkout)
    first = proc.stdout.splitlines()[0] if proc.stdout else ""
    print(f"M6 ({sys.platform}) exit {proc.returncode}; first line {first!r}; "
          f"bytes {'held' if _md5(db) == before else 'CHANGED'}; stderr {proc.stderr[-300:]!r}")
    assert first == (f"database: {Path(os.path.abspath(db)).as_posix()} "
                     f"(opened read-only: mode=ro + write authorizer)"), first
    assert _md5(db) == before, "the diagnostic changed the store's bytes"
    assert sorted(p.name for p in db.parent.iterdir()) == ["kb.db"], "a sidecar or a store was created"
    assert proc.returncode == 0, proc.stderr[-800:]


def test_m6_an_absent_store_is_named_and_nothing_is_created(tmp_path):
    """Pre-fix (law 16), at 1a1a521: data/ and data/kb.db were created and
    the run printed "Total triples: 0" and exited 0."""
    checkout = _checkout(tmp_path)
    proc = _run(checkout)
    print(f"M6 absent ({sys.platform}) exit {proc.returncode}; stderr {proc.stderr[-300:]!r}")
    assert not (checkout / "data").exists(), "the diagnostic created a store"
    assert proc.returncode == 2, (proc.returncode, proc.stdout[-400:], proc.stderr[-400:])
    assert "no such store" in proc.stderr and "Total triples" not in proc.stdout


@pytest.mark.sqlite_only("diag_downstream.py reads the SQLite file through the read-only open (law 15); the URL form is W3b part 3 (D9, T8)")
def test_m6_the_read_path_counts_what_the_store_class_counted(tmp_path):
    """The rewrite reads the rows in SQL where it called `TripleStore.all()`:
    the counts it prints are the class's own, on the fixture (counted here
    through the class on a second copy, never the one the script reads)."""
    from backend.kb.tiered_store import TripleStore
    checkout = _checkout(tmp_path)
    _store(checkout, older=False)
    other = tmp_path / "class_copy.db"
    shutil.copy2(FIXTURE, other)
    store = TripleStore(str(other))
    try:
        triples = store.all()
    finally:
        store.close()
    proc = _run(checkout)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert triples, "non-vacuity: the fixture holds no triple"
    assert f"Total triples: {len(triples)}" in proc.stdout, proc.stdout[-600:]
    for (tier, source), count in Counter((t.tier, t.source) for t in triples).items():
        assert f"  {tier:<6} {source:<25} {count:>8}" in proc.stdout, (tier, source, count)
