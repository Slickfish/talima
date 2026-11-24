"""Packet A sentinels — the suite must never touch the real data/ tree.

REGROUND_REPORT §3.2 measured the defect (8 calibration rows + 1 kb.db row
per suite run, both files gitignored and therefore invisible). These two
tests are the permanent invariant: A.1's redirect without them silently
regresses the next time someone adds a bare `create_app()`.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from backend.eval.calibration_log import log_path
from backend.server import create_app
from tests.conftest import GUARDED_REAL_PATHS, REAL_DATA, fingerprint


def _under(path, root) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def test_no_real_paths_under_test(session_tmp):
    """Every persistent path an app resolves must live in the session tmp
    tree, and none may resolve into the repo's data/. Deterministic — no
    ordering dependency, builds its own app."""
    app = create_app()
    s = app.state.services

    resolved = {
        "calibration_log": log_path(),
        "chroma_dir":      s.vector.persist_directory,
        "snapshots_dir":   s.kairos.snapshots_dir,
    }
    # W3b — a store is a path on SQLite and a schema under the Postgres
    # fixture; either way it must be the fixture's, never the real store.
    for name, st in {"kb_db": s.store, "archive_db": s.archive_chain, "lineage_db": s.lineage}.items():
        if st.db_path is not None:
            resolved[name] = st.db_path
        else:
            described = st.conn.describe()
            assert "schema nx_" in described, f"{name} is not the fixture's schema: {described}"

    for name, path in resolved.items():
        assert _under(path, session_tmp), f"{name} escaped the session tmp: {path}"
        assert not _under(path, REAL_DATA), f"{name} resolved into real data/: {path}"


def test_suite_does_not_mutate_real_data(real_data_baseline):
    """Mid-session check that real data/ is byte-identical to session start.

    The companion session-teardown assertion in conftest
    (`_assert_real_data_untouched`) is the one that catches late writers;
    this test makes the invariant visible in the suite report and names the
    offending path immediately.

    Paths absent at session start are skipped cleanly (baseline None), so a
    checkout without a populated data/ still runs.
    """
    drifted = []
    for name, path in GUARDED_REAL_PATHS.items():
        before = real_data_baseline[name]
        after = fingerprint(path)
        if before is None and after is None:
            continue                      # absent then, absent now
        if before != after:
            drifted.append(f"{name}: {before!r} -> {after!r}")

    assert not drifted, "real data/ mutated during the suite:\n" + "\n".join(drifted)


def test_calibration_log_is_redirected(session_tmp):
    """The accrual file specifically — the one the audit measured."""
    assert _under(log_path(), session_tmp)
    assert log_path() != REAL_DATA / "calibration_log.jsonl"
