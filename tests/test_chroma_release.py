"""W0b part 5d — the Chroma systems a test builds are released when it ends.

Pins `conftest.release_chroma_systems_created_since`, which the autouse
`_release_chroma_systems` fixture calls after every test. Without it every
`create_services` left a Chroma system (a Rust runtime's threads and open
files) in chromadb's process-wide cache, and on CI's macos runner a long suite
reached the per-process limits: runs 9 and 12 hung, and run 11 failed with
"Resource temporarily unavailable" (os error 35).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.conftest import release_chroma_systems_created_since


def _threads() -> int | None:
    """The process's thread count where the OS shows it (Linux), else None."""
    status = Path("/proc/self/status")
    if not status.is_file():
        return None
    for line in status.read_text(encoding="utf-8").splitlines():
        if line.startswith("Threads:"):
            return int(line.split()[1])
    return None


def _build(root: Path, n: int) -> None:
    from backend.server import create_services
    for i in range(n):
        create_services(kb_db_path=root / f"{i}/kb.db", chroma_dir=root / f"{i}/chroma",
                        eval_log_path=root / f"{i}/m.jsonl", archive_db_path=root / f"{i}/a.db",
                        snapshots_dir=root / f"{i}/snap")


def test_services_leave_chroma_systems_behind_until_released(tmp_path):
    """The consequence and the cure in one test. Three builds add three
    systems to the cache; releasing them returns the cache to the membership
    it had before. On Linux the threads a build adds are counted, to show the
    leak is real, and printed across two build-and-release cycles."""
    from chromadb.api.client import SharedSystemClient
    before = set(SharedSystemClient._identifier_to_system)
    t0 = _threads()
    _build(tmp_path / "a", 3)
    grown = set(SharedSystemClient._identifier_to_system) - before
    t1 = _threads()
    released = release_chroma_systems_created_since(before)
    t2 = _threads()
    _build(tmp_path / "b", 3)
    release_chroma_systems_created_since(before)
    t3 = _threads()
    print(f"\nCHR-1 ({sys.platform}) systems added {len(grown)}, released {released}; threads {t0} -> {t1} "
          f"-> {t2} (first release) -> {t3} (second cycle)")
    assert len(grown) == 3 and released == 3, "each build adds one system, and each is released"
    assert set(SharedSystemClient._identifier_to_system) == before
    # The threads are printed, never asserted: they wind down asynchronously
    # after stop() (measured 4 to 11 after a release, run to run), and an
    # assertion on them would be a flaky test. What is asserted is the
    # mechanism, deterministic on every platform: nothing a test built stays
    # in the cache, and so nothing it built is kept alive by it.
    if t0 is not None:
        assert t1 - t0 > 9, "the leak this releases is real here"


def test_a_system_from_before_is_never_released(tmp_path):
    """COMPANION: only systems created since the snapshot go. A client set up
    earlier (a module-scoped fixture's) keeps working after a release."""
    from chromadb.api.client import SharedSystemClient
    _build(tmp_path / "early", 1)
    snapshot = set(SharedSystemClient._identifier_to_system)
    _build(tmp_path / "late", 2)
    assert release_chroma_systems_created_since(snapshot) == 2
    assert snapshot <= set(SharedSystemClient._identifier_to_system), "an earlier system was released"
