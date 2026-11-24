"""W3b — the tests that are SQLite's by nature, pinned (RUN_CHARTER law 5:
the subtraction is never silent).

Under the Postgres backend fixture (`NEXUS_TEST_STORE=postgres`;
`tests/conftest.py`) every store path maps to a schema in one test
database, so the suite exercises the Postgres backend. Two sets are
declared here, each module with its reason:

- `SQLITE_ONLY_MODULES` — modules SKIPPED under the fixture, whole: their
  tests drive a script that opens the SQLite file the rig built through the
  read-only open (law 15) or the lock (W2), or inspect that file with
  sqlite3. A single such test inside an otherwise portable module carries
  `@pytest.mark.sqlite_only("reason")` instead, and is skipped alone.
- `OPENS_SQLITE_UNDER_POSTGRES` — modules that RUN under the fixture and
  open a SQLite store on purpose: the backend twins and the SQLite backend's
  own tests. The fixture records every SQLite open under the Postgres run
  and fails the session for one whose module is not listed here (direction
  B of the pin, `tests/conftest.py`).

Both directions are checked: a listed module must touch SQLite by name
(`tests/test_w3b_dialect.py` S, by AST: `sqlite3` bound or referenced, a
SQLite-only product name, or a string that is a SQLite fact), and the
counts are a literal there, so adding or removing a module is an act.
The fixture prints how many tests it skipped, from how many modules and
marks, and how many SQLite opens it recorded. A module that fails under the
Postgres run for a SQLite reason is either made portable (the interface's
`columns()`, `has_table()`, `tables()`, or `backend.store.connect(
store_target_for(path))` where it opened sqlite3 on the path) or listed
here with its reason; nothing else is skipped.
"""
from __future__ import annotations

#: repo-relative test module -> why it is SQLite's alone (skipped whole under the fixture)
SQLITE_ONLY_MODULES: dict[str, str] = {
    "tests/test_w0b_replay_check.py":
        "replay_check.py opens the store through the read-only SQLite open (law 15) and these tests "
        "build the SQLite files it reads; the URL form is W3b part 3 (D9, T8)",
    "tests/test_l2b_consolidation_pass.py":
        "archive_consolidation.py --kb reads the SQLite file through the read-only open and takes "
        "its lock (W2) under --confirm; the URL form is W3b part 3",
    "tests/test_ba_fix8_pregate_archive.py":
        "archive_pregate.py --kb reads the SQLite file through the read-only open and takes its lock "
        "(W2) under --confirm; the URL form is W3b part 3",
    "tests/test_rejected_surfaces.py":
        "scripts/verify_packet_k.py reads the SQLite file the rig builds through the read-only open, "
        "and the tests inspect that file with sqlite3",
    # W10c — the interceptor's store is SQLite files under its own root, by design (it never opens Postgres). What
    # W10c added to the lineage store itself runs on both backends in tests/test_w10c_record_layer.py.
    "tests/test_w10c_interceptor.py":
        "the interceptor's own store: these tests build its folder, plant SQLite triggers in it to make a write "
        "fail inside its transaction, and read its files with sqlite3",
    "tests/test_w10c_interceptor_wire.py":
        "the interceptor's own store over real sockets and its command line: a child process writes the SQLite "
        "files, a planted SQLite trigger fails a write, and the test reads the store with sqlite3",
}

#: repo-relative test module -> why it opens a SQLite store under the Postgres run on purpose
OPENS_SQLITE_UNDER_POSTGRES: dict[str, str] = {
    "tests/test_w3b_dialect.py":
        "the backend twins: D opens the SQLite store beside the Postgres schema and compares the two "
        "column by column and table by table; the dialect rules run the same SQL on both",
    "tests/test_w3b_store_interface.py":
        "the SQLite backend's own tests (connect(path) is today's connection, wrap, tables(), the "
        "trace hook, the driver-absent path) run under the Postgres job too",
    "tests/test_w3b_parity.py":
        "the parity claims (P1 to P5, P9, P10): every test runs the same acts on a SQLite store and a "
        "Postgres schema in one process and compares what each holds",
    "tests/test_w3b_migration.py":
        "migration both ways: the SQLite side is the committed pre-run fixture's copy, the export's "
        "files and the source as the product reads it, opened beside the Postgres schema",
}

__all__ = ["OPENS_SQLITE_UNDER_POSTGRES", "SQLITE_ONLY_MODULES"]
