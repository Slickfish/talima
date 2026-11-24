"""Packet U — three defects from the acceptance run, and one found on the way.

U.1  The cycle sealed three records and reported that it had sealed none.
U.2  Scripts did not read `.env`, so a token already in the file needed
     `--token` and a non-default `OLLAMA_URL` was invisible to them.
U.3  An empty token produced `LocalProtocolError: Illegal header value
     b'Bearer '` from httpx — a transport error from a script whose stated
     purpose is documenting the auth flow.
U.0  Adjacent: `scripts/measure_derivations.py` has not compiled since Packet
     I.1, and every source guard in this repo missed it, because they all use
     `ast.parse` and a repeated keyword argument is a COMPILE-time check.
"""
from __future__ import annotations

import asyncio
import ast
import os
from tests import _proc
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.server import create_app, create_services
from tests._tracked import tracked

ROOT = Path(__file__).resolve().parent.parent
# U.2/M.5 — TRACKED, not globbed. The filesystem version invented six
# failures on the operator's machine from scratch files that do not exist
# in the repository. A guard's population is a property of the codebase.
# W9 (run/specs/W9.md D6) — a script a stranger runs with nothing but Python:
# the standard library only and no NEXUS import, so it reads no .env. Held to
# that by test_the_standalone_exemption_is_only_for_standalone_scripts below.
STANDALONE_SCRIPTS = {
    "nexus_verify.py": "W9's verifier, run offline by whoever holds a credential, on any machine",
    "talima_verify.py": "W11b's second name for the same verifier (run/specs/W11b.md D4), run the same way",
}
SCRIPTS = [p for p in tracked("scripts/*.py") if p.name not in STANDALONE_SCRIPTS]
TOKEN = "packet-u-token"

FILE_TOKEN = "token-from-the-dotenv-file"


def _services(tmp_path):
    return create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "turn_metrics.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots")


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def _a_pending_recalibration(services, client) -> int:
    """Envelope + outcome + recalibration — three unsealed governance rows."""
    bid = services.store.add(subject="QTc", relation="stands_for",
                             object="corrected QT", weight=0.55, tier=2,
                             granularity=Granularity.TOPIC, source="extraction")
    client.post("/governance/envelope/confirm", headers=_auth(), json={
        "version": "u1", "max_delta": 0.05, "movable_classes": ["extraction"],
        "reason": "packet U sentinel", "confirmed": True})
    client.post("/outcome/confirm", headers=_auth(), json={
        "event_type": "review", "decision": "approved",
        "direction": "confirming", "references": {"belief_ids": [bid]},
        "magnitude": 0.05, "confirmed": True})
    return bid


# ===========================================================================
# U.1 — the seal is reported by whichever block carried it
# ===========================================================================

def test_a_forced_cycle_reports_the_records_it_sealed(tmp_path, monkeypatch, recalibration_on):
    """THE DEFECT, reproduced and inverted.

    `POST /kairos/cycle` forces a snapshot by default. `run_one_cycle` runs the
    snapshot FIRST, and the snapshot path seals whatever is pending — that is
    S.2's design and it is correct. `maybe_seal_governance()` then finds
    nothing left and reports `None` / `0` / "nothing_pending", which is also
    correct ABOUT THE CADENCE and was being read as "nothing was sealed".

    Measured on the operator's store: three records sealed, `0 record(s)`
    printed. A governance action reported as not taken is the I-6 defect the
    reporting exists to prevent.
    """
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", "prompt")
    services = _services(tmp_path)
    services.hippocampus.ingest_triple("QTc", "stands_for", "corrected QT")

    with TestClient(create_app(services)) as client:
        bid = _a_pending_recalibration(services, client)
        pending = len(services.recalibration.unsealed())
        assert pending == 3, f"the fixture left {pending} rows, expected 3"

        cyc = client.post("/kairos/cycle", headers=_auth()).json()

        # The act, whichever block carried it.
        assert cyc["sealed_records"] == pending, (
            f"the cycle sealed {pending} record(s) and reported "
            f"{cyc['sealed_records']}")
        assert cyc["sealed_block_index"] is not None
        assert cyc["sealed_block_kind"] == "snapshot", (
            "the forced snapshot did not carry the seal; this test is no "
            "longer exercising the path that produced the contradiction")

        # ...and the cadence fields still say what they said, because they are
        # about the cadence and were never wrong.
        assert cyc["governance_block_index"] is None
        assert cyc["governance_skipped_reason"] == "nothing_pending"

        trace = client.get(f"/belief/{bid}/provenance", headers=_auth()).json()

    assert all(s["sealed"] for s in trace["steps"]), (
        "the report claimed a seal the provenance does not show")
    assert {s["sealed_block"] for s in trace["steps"]} == {cyc["sealed_block_index"]}


def test_the_governance_path_also_reports_through_the_same_field(
        tmp_path, monkeypatch):
    """The other carrier. One field answers "was it sealed" for both, or the
    caller has to know which mechanism ran — which is the knowledge the
    acceptance script did not have and should not need."""
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", "prompt")
    services = _services(tmp_path)
    services.hippocampus.ingest_triple("QTc", "stands_for", "corrected QT")
    asyncio.run(services.kairos.maybe_snapshot_and_fingerprint(force=True))

    with TestClient(create_app(services)) as client:
        _a_pending_recalibration(services, client)
        pending = len(services.recalibration.unsealed())
        # force_snapshot=false — the snapshot is not due, so the governance
        # cadence is the only thing that can seal.
        cyc = client.post("/kairos/cycle?force_snapshot=false",
                          headers=_auth()).json()

    assert cyc["snapshot_taken"] is False
    assert cyc["sealed_records"] == pending
    assert cyc["sealed_block_kind"] == "governance"
    assert cyc["sealed_block_index"] == cyc["governance_block_index"], (
        "the two reports disagree about which block carried the seal")


def test_a_cycle_that_seals_nothing_says_zero(tmp_path, monkeypatch):
    """NON-VACUITY for the two above. If `sealed_records` were always non-zero
    they would pass on a field that means nothing."""
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    services = _services(tmp_path)
    services.hippocampus.ingest_triple("a", "is_a", "b")
    with TestClient(create_app(services)) as client:
        cyc = client.post("/kairos/cycle", headers=_auth()).json()
    assert cyc["sealed_records"] == 0
    assert cyc["sealed_block_index"] is None
    assert cyc["sealed_block_kind"] == ""


def test_the_acceptance_script_reads_the_act_not_the_cadence():
    """AST. The script printed `governance_sealed_records`, which is why it
    said "0 record(s)" for a cycle that had just sealed three. A future edit
    that reaches back for the cadence field fails here."""
    src = (ROOT / "scripts" / "acceptance_governed_adaptation.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    read = {n.args[0].value for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "get" and n.args
            and isinstance(n.args[0], ast.Constant)
            and isinstance(n.args[0].value, str)}
    assert "sealed_records" in read, "the script does not read the seal count"
    assert "governance_sealed_records" not in read, (
        "the script reads the cadence field again; that is what made it "
        "report 0 for a cycle that sealed three")


# ===========================================================================
# U.2 — scripts read the same `.env` the server does
# ===========================================================================

def test_the_loader_is_one_implementation_shared_by_both_sides():
    """`backend.env` exists so the script side does not need a second copy —
    a second copy is exactly how `.env` came to be read by the launcher and
    ignored by the application."""
    import backend.env
    import backend.server
    import scripts._env

    assert backend.server.load_env_files is backend.env.load_env_files
    assert scripts._env.load_env_files is backend.env.load_env_files


def test_no_script_imports_backend_server():
    """H.2 — a scratchpad script imported it and migrated the operator's live
    kb.db. `backend.env` was created so `.env` loading did not force that."""
    for path in SCRIPTS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            mod = (getattr(node, "module", "") or "") if isinstance(
                node, ast.ImportFrom) else ""
            names = [a.name for a in getattr(node, "names", [])] if isinstance(
                node, ast.Import) else []
            assert not mod.startswith("backend.server"), f"{path.name}:{node.lineno}"
            assert not any(n.startswith("backend.server") for n in names), (
                f"{path.name}:{node.lineno}")


@pytest.mark.parametrize("script", SCRIPTS,
                         ids=lambda p: p.name)
def test_every_script_loads_dotenv_in_main(script):
    """RATCHET, the sibling of M.4's `force_utf8_stdio` guard and for the same
    reason: an import alone does nothing. Scoped to `main()`'s own body, so a
    script that imports the helper and forgets to call it still fails.

    Every script, not only the ones that read a NEXUS_* variable today — the
    next one to grow an env read should not have to rediscover this.
    """
    if script.name.startswith("_"):
        pytest.skip("shared helper, not an entry point")
    tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    main = next((n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == "main"), None)
    assert main is not None, f"{script.name} has no main()"
    called = {n.func.id for n in ast.walk(main)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert called & {"load_env", "require_token_or_exit"}, (
        f"{script.name}'s main() never loads .env, so a value the server reads "
        f"from the file is invisible to it")


def test_a_token_in_dotenv_is_the_token_a_script_uses(tmp_path):
    """SENTINEL — same subprocess/temp-cwd discipline as `test_dotenv_loading`,
    and precedence-aware for the same reason: the repo root is consulted first,
    so a machine with a real `.env` must not make this pass or fail by
    accident.
    """
    from dotenv import dotenv_values

    repo_env = ROOT / ".env"
    from_repo = (dotenv_values(repo_env).get("NEXUS_API_TOKEN")
                 if repo_env.is_file() else None)
    expected = from_repo or FILE_TOKEN

    (tmp_path / ".env").write_text(f"NEXUS_API_TOKEN={FILE_TOKEN}\n",
                                   encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        f"sys.path.insert(0, r{str(ROOT)!r})\n"
        "from scripts._env import require_token_or_exit\n"
        "print(require_token_or_exit(''))\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "NEXUS_API_TOKEN"}
    # conftest points NEXUS_ENV_ROOT at an empty sandbox so the suite never
    # reads the operator's `.env`. These cases test DEFAULT discovery — repo
    # root, then cwd — so the child must not inherit it, or there is nothing
    # left to discover.
    env.pop("NEXUS_ENV_ROOT", None)
    proc = _proc.run([sys.executable, str(probe)], cwd=tmp_path, env=env,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == expected


# ===========================================================================
# U.3 — no token stops with a named error, not a transport error
# ===========================================================================

def test_a_script_with_no_token_exits_with_a_named_error(tmp_path):
    """NON-VACUITY IS THE POINT: the error path must actually be REACHED, and
    the failure must not be httpx's.

    The subprocess runs with no `NEXUS_API_TOKEN` and a cwd whose `.env` does
    not set one. If the repo-root `.env` supplies a token this cannot be
    reached from here, and the test says so rather than passing hollowly.
    """
    from dotenv import dotenv_values

    repo_env = ROOT / ".env"
    if repo_env.is_file() and dotenv_values(repo_env).get("NEXUS_API_TOKEN"):
        pytest.skip("the repo-root .env supplies a token, so 'no token "
                    "anywhere' cannot be reached without moving it")

    (tmp_path / ".env").write_text("LLM_BACKEND=ollama\n", encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        f"sys.path.insert(0, r{str(ROOT)!r})\n"
        "from scripts._env import require_token_or_exit\n"
        "require_token_or_exit('')\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "NEXUS_API_TOKEN"}
    # conftest points NEXUS_ENV_ROOT at an empty sandbox so the suite never
    # reads the operator's `.env`. These cases test DEFAULT discovery — repo
    # root, then cwd — so the child must not inherit it, or there is nothing
    # left to discover.
    env.pop("NEXUS_ENV_ROOT", None)
    proc = _proc.run([sys.executable, str(probe)], cwd=tmp_path, env=env,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)

    assert proc.returncode != 0, "a missing token exited cleanly"
    combined = proc.stdout + proc.stderr
    assert "No API token found" in combined
    assert "Looked in, in order" in combined, (
        "the error does not name where it looked, which is the whole fix")
    assert "NEXUS_API_TOKEN" in combined and ".env" in combined
    assert "Traceback" not in combined, (
        f"it raised rather than exiting:\n{combined[-1500:]}")
    assert "LocalProtocolError" not in combined and "httpx" not in combined, (
        "the failure is still coming from the transport layer")


def test_auth_header_refuses_an_empty_token():
    """The exact input that produced `Illegal header value b'Bearer '`."""
    from scripts._env import TokenNotFound, auth_header

    assert auth_header("abc") == {"Authorization": "Bearer abc"}
    for empty in ("", "   ", None):
        with pytest.raises(TokenNotFound):
            auth_header(empty)


def test_no_script_builds_a_bearer_header_by_hand():
    """The class, not the instance. An f-string `Bearer {x}` cannot know
    whether `x` is empty; `auth_header` can and does."""
    for path in SCRIPTS:
        if path.name == "_env.py":
            continue                     # the one place that may build it
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                literal = "".join(v.value for v in node.values
                                  if isinstance(v, ast.Constant)
                                  and isinstance(v.value, str))
                assert not literal.strip().startswith("Bearer"), (
                    f"{path.name}:{node.lineno} builds a bearer header by "
                    f"hand; use scripts._env.auth_header, which refuses an "
                    f"empty token instead of handing httpx a malformed one")


# ===========================================================================
# U.0 — every script COMPILES, which ast.parse does not check
# ===========================================================================

@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_every_script_compiles(script):
    """FOUND WHILE FIXING U.2, and the sharper lesson in this packet.

    `scripts/measure_derivations.py` had `write_text(report, encoding="utf-8",
    encoding="utf-8")` — Packet I.1's encoding sweep appending a keyword to a
    call that already had it. It has not run since e36efb3 (2026-08-10).

    EVERY source guard in this repo uses `ast.parse`, and a repeated keyword
    argument is not a parse error — it is caught later, by `compile()`. So the
    encoding ratchet, the AST guards and the purity sentinels all walked over
    a script that could not execute. `compile()` is one word longer and closes
    the whole class.
    """
    src = script.read_text(encoding="utf-8")
    try:
        compile(src, str(script), "exec")
    except SyntaxError as e:
        pytest.fail(f"{script.name} does not compile: {e.msg} (line {e.lineno})")


def test_ast_parse_would_not_have_caught_it():
    """The guard on the guard — proof that the test above is not redundant
    with every other AST check in the suite."""
    bad = 'f(x, encoding="utf-8", encoding="utf-8")\n'
    ast.parse(bad)                       # parses happily
    with pytest.raises(SyntaxError):
        compile(bad, "<probe>", "exec")


def test_the_standalone_exemption_is_only_for_standalone_scripts():
    """The exemption above is for scripts that cannot read .env because they
    import nothing of NEXUS: each one passes W9's standalone rule (every import
    the standard library's, by AST), and is a tracked script."""
    from tests.test_w9_nexus_verify import standalone_problems
    tracked_names = {p.name for p in tracked("scripts/*.py")}
    for name in STANDALONE_SCRIPTS:
        assert name in tracked_names, name
        source = (Path(__file__).resolve().parent.parent / "scripts" / name).read_text(encoding="utf-8")
        assert standalone_problems(source) == [], (name, standalone_problems(source))

