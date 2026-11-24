# Contributing to NEXUS

This page is for whoever changes the code or the documents, including an AI coding session. The terms on which the
code may be used and changed are in `LICENSE`.

## Where changes go

- Nothing is pushed to `main` directly: a change is made on a branch of its own and merged by the maintainer, after
  the suite has passed on a clean checkout of it.
- Never force-push. Never rewrite a sealed record or history someone else wrote.

## The laws a change must keep

The product's laws are in `docs/CONTRACTS.md` ("The laws"): frozen dataclasses replaced whole; capabilities, never
model names, in routing; confirmation before a pull, a seed, a deletion or an archival; claims tiered and never
deleted; `num_ctx` at most 8192; the async generator loop yielding SSE events; a stable system prefix; compaction
cheapest first. The project's working rules add these, the ones a contributor meets first, by the numbers the
documents cite:

  - `data/` in the repository holds only `.gitkeep` files, before and after every test run (law 2).
  - Every change to the record keeps every existing record recomputing unchanged: a new field enters the hash only
    when it is not the default (law 4). `tests/fixtures/pre_run_data/` and the LS fixture literal prove it.
  - No false zero, no false blank, no silent subtraction: a number that cannot be measured is reported as
    unmeasurable, and a default never stands in for a measurement (law 5).
  - Consent and confirmation never default to true (law 6).
  - Similarity never ranks; order is standing with a total order (law 7).
  - A flaky test is a defect: find the cause; never retry to green, mark it flaky or widen a tolerance (law 10).
  - Anything touching paths, clocks, encodings, sockets or line endings must pass on Windows and macOS in CI
    (law 11).
  - Learning is frozen by default on every new instance (console and MCP) until the recurrence defect is fixed and an
    operator unfreezes deliberately with the before/after script (law 12).
  - No secrets in the repository: `.env.example` only (law 13).
  - The operator's instance is never the test bed: scripts that read it are read-only (`mode=ro` plus a
    write-refusing authorizer, the database's path on the first line) (law 15).
  - No fix is a fix until its companion test has been seen failing on the code before the fix (law 16).
    Dependencies enter only through `requirements.txt`, and code copied from outside names its source in the
    commit.
  - Anything that limits the model is enforced by the runtime, the container or the proxy, never by asking the
    model (law 17).

## Tests

```bash
python -m pytest tests/ -q
```

A change is tested on a clean checkout on Linux with Python 3.11, on SQLite, and on Postgres when it touches
`backend/store/`, `backend/kb/` or `backend/memory/`; on Windows and macOS before a release. To run it on Postgres
yourself, set `NEXUS_TEST_STORE=postgres` and `NEXUS_TEST_DATABASE_URL` to a `postgresql://` URL of a scratch
database whose name ends in `_test` (`tests/conftest.py` refuses any other name, because the fixture creates and
drops schemas in it).

The test discipline, in short:

- `tests/test_phase_N.py` per phase; a failure means the phase is incomplete.
- A test that enumerates files asks git, never the filesystem (`tests/_tracked.py`).
- A child process a test starts goes through `tests/_proc.py`, and every wait on it is bounded.
- A guard that inspects source uses the AST, never a text search.
- Every guard ships with a companion that plants a member of the class it forbids that its own source does not
  name, and the companion asserts the consequence (the build fails, the request is refused), never a flag
  (OQ-16 rules 1 and 3).
- A measurement's fixture is calibrated against something the measurement did not produce.

Some tests read the documents: changing `README.md`, `SECURITY.md`, `THREAT_MODEL.md`, `.env.example`,
`CHANGELOG.md`, `docs/FIRST_RUN.md`, `docs/DEPLOY.md`, `docs/REFERENCE_GATEWAY.md`, `docs/INTEGRATION_REFERENCE.md`
or `docs/CONTRACTS.md` can fail one.

## What a change carries

- A new setting goes into `.env.example` with its default, what it does, its off position and the probe that
  reports it, and into the settings block of `docs/CONTRACTS.md` with its probe.
- `CHANGELOG.md` gets an entry, and each finding the change does not fix is recorded as an issue, with a one-line
  fix sketch.
- The relevant `docs/` page is updated in the same commit.
- A document describes only what exists, with its status; what is not built is named as `docs/CONTRACTS.md`'s "Not
  built yet" names it. Every command a document gives has been run, or says it was not and why.

## CI

Every workflow is started by hand (`workflow_dispatch`): no push, tag or pull request starts one, which
`tests/test_w3_ci.py` (C18) holds. A dispatch runs a workflow's full coverage, macOS included. Every job has a time
limit of its own. `docs/DEPLOY.md`, "CI", has the rest.

## Reporting a security problem

Not in an issue: see `SECURITY.md`.
