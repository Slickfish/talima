"""Every documented `.env` setting must take effect when set ONLY in `.env`.

THE DEFECT CLASS
    U.2 fixed `.env` loading and proved it with ONE key. `test_dotenv_loading`
    exercises `NEXUS_API_TOKEN` end to end through a real file; every other
    configurable value in the project has only ever been tested with
    `monkeypatch.setenv`, which writes `os.environ` directly.

    **A `monkeypatch.setenv` test cannot see this bug.** It sets the variable
    before calling the reader, so a call-time read passes and a module-scope
    read passes too — the module was imported long before, but the test never
    re-imports it, so nothing notices. Only a real file, read at the moment
    the loader reads it, exposes the ordering.

    And the ordering is the whole defect. `backend/server.py` imports its
    dependency graph at lines 49-91 and calls `load_env_files()` at line 109.
    Any module that resolves an environment variable at MODULE SCOPE has
    already run by then, so it takes the compiled default and the file is
    silently ignored. Measured, with all three set in a `.env` the loader did
    read:

        LM_STUDIO_URL   .env http://from-dotenv:9999   module http://localhost:1234
        NUM_CTX         .env 4096                      module 8192
        SEARXNG_URL     .env http://from-dotenv:8888    module (default)

WHY THE LIST IS DERIVED
    From docs/CONTRACTS.md's `## Env (.env in repo root)` block, parsed. A setting
    documented to the operator without a probe here fails
    `test_every_documented_setting_has_a_probe` — so a new knob cannot be
    added to the documentation without someone deciding how it is verified.
    The probe EXPRESSIONS are per-key and hand-written, because "takes effect"
    means something different for each consumer; what is derived is the
    obligation to have one.

WHY A SUBPROCESS, AND WHY IT REFUSES RATHER THAN SKIPS
    The reads under test happen at import, so they cannot be re-run in this
    process. Each case launches a fresh interpreter rooted in a temp cwd that
    holds the `.env`.

    The repo root beats the cwd (`backend/env.py`), so a repo-root `.env` that
    sets the same key would shadow the fixture. Rather than skip — which is
    how a guard passes on the operator's machine while testing nothing — the
    precondition FAILS and names the key to remove. That trap has already been
    sprung once in this project, on `test_dotenv_loading` itself.
"""
from __future__ import annotations

import os
import re
from tests import _proc
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPO_ENV = ROOT / ".env"

# --- the derived obligation ------------------------------------------------


def documented_settings() -> list[str]:
    """The keys docs/CONTRACTS.md tells an operator to put in `.env`."""
    md = (ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
    block = md.split("## Env (.env in repo root)")[1].split("```")[1]
    return [m.group(1) for m in re.finditer(r"^([A-Z_][A-Z0-9_]*)=", block, re.M)]


# key -> (value to write into .env, python expression that must equal it as a
#         string, or None to assert the key is READ SOMEWHERE at all).
#
# `xfail` entries are settings that genuinely do not work today. They are
# marked, not deleted: an xfail is the record of a defect, and deleting the
# case would delete the finding.
PROBES: dict[str, tuple[str, str]] = {
    # Probed by the provider it selects, not by echoing the variable back.
    # An echo would pass even if nothing consumed the setting — which is the
    # failure mode this whole file exists to catch.
    "LLM_BACKEND": (
        "lmstudio",
        "('lmstudio' if type(__import__('backend.providers.factory', "
        "fromlist=['x']).get_provider()).__name__ == 'LMStudioProvider' "
        "else 'ollama')"),
    "OLLAMA_URL": (
        "http://from-dotenv:9999",
        "__import__('backend.providers.ollama', fromlist=['OllamaProvider'])"
        ".OllamaProvider().BASE_URL"),
    "LM_STUDIO_URL": (
        "http://from-dotenv:9999",
        "__import__('backend.providers.lmstudio', fromlist=['LMStudioProvider'])"
        ".LMStudioProvider().BASE_URL"),
    # W3 part 2 — the api backends' settings, probed through the one reader
    # (`api_settings`), each at call time; the keys included, because a key
    # read before the file is loaded is a key the provider never sends.
    "OPENAI_BASE_URL": (
        "http://from-dotenv:9999/v1",
        "__import__('backend.providers.api_settings', fromlist=['x']).openai_settings().base_url"),
    "OPENAI_API_KEY": (
        "from-dotenv-key",
        "__import__('backend.providers.api_settings', fromlist=['x']).openai_settings().api_key"),
    "OPENAI_MODEL": (
        "from-dotenv-model",
        "__import__('backend.providers.api_settings', fromlist=['x']).openai_settings().model"),
    "OPENAI_EXTRACTION_MODEL": (
        "from-dotenv-extractor",
        "__import__('backend.providers.api_settings', fromlist=['x']).openai_settings().extraction_model"),
    "ANTHROPIC_BASE_URL": (
        "http://from-dotenv:9998",
        "__import__('backend.providers.api_settings', fromlist=['x']).anthropic_settings().base_url"),
    "ANTHROPIC_API_KEY": (
        "from-dotenv-key",
        "__import__('backend.providers.api_settings', fromlist=['x']).anthropic_settings().api_key"),
    "ANTHROPIC_MODEL": (
        "from-dotenv-model",
        "__import__('backend.providers.api_settings', fromlist=['x']).anthropic_settings().model"),
    "ANTHROPIC_EXTRACTION_MODEL": (
        "from-dotenv-extractor",
        "__import__('backend.providers.api_settings', fromlist=['x']).anthropic_settings().extraction_model"),
    "NEXUS_EMBED_URL": (
        "http://from-dotenv:9997/v1",
        "__import__('backend.providers.api_settings', fromlist=['x']).embed_url()"),
    "NEXUS_EMBED_MODEL": (
        "from-dotenv-embedder",
        "__import__('backend.providers.api_settings', fromlist=['x']).embed_model()"),
    "NEXUS_EMBED_API_KEY": (
        "from-dotenv-key",
        "__import__('backend.providers.api_settings', fromlist=['x']).embed_api_key()"),
    "SEARXNG_URL": (
        "http://from-dotenv:8888",
        "__import__('backend.tools.search', fromlist=['x'])"
        ".WebSearchTool().base_url"),
    "NEXUS_STARTUP_PROBE": (
        "off",
        "__import__('backend.server', fromlist=['x']).startup_probe_mode()"),
    "NEXUS_PROBE_CONNECT_TIMEOUT": (
        "0.75",
        "str(__import__('backend.providers.base', fromlist=['x'])"
        ".probe_connect_timeout())"),
    # Boolean and enum knobs are probed by their CONSEQUENCE mapped back to
    # the value written, not by echoing os.environ — an echo would pass even
    # if nothing consumed it.
    "NEXUS_RECALIBRATION": (
        "off",
        "('off' if not __import__('backend.kb.recalibration', fromlist=['x'])"
        ".recalibration_enabled() else 'on')"),
    "NEXUS_GOVERNANCE_SEAL": (
        "snapshot",
        "__import__('backend.memory.kairos', fromlist=['x'])"
        ".governance_seal_mode()"),
    "NEXUS_GOVERNANCE_SEAL_INTERVAL_MINUTES": (
        "7",
        "str(int(__import__('backend.memory.kairos', fromlist=['x'])"
        "._governance_interval_seconds() // 60))"),
    # A REAL file, so the probe tests the success path. Pointing at a missing
    # file would exercise the fallback and pass whether or not the setting was
    # ever consulted.
    "NEXUS_CONTRADICTION_PAIRS": (
        "{tmp}/pairs.json",
        "'{tmp}/pairs.json' if list(__import__("
        "'backend.cortex.contradiction', fromlist=['x']).load_pairs()) == "
        "[('alpha', 'omega')] else 'NOT-LOADED'"),
    "NEXUS_EXTRACTION_VOCABULARY": (
        "off",
        "('off' if not __import__('backend.kb.vocabulary', fromlist=['x'])"
        ".vocabulary_enabled() else 'on')"),
    "NEXUS_CONSOLIDATION_BATCH": (
        "off",
        "('off' if not __import__('backend.memory.neocortex', fromlist=['x'])"
        ".consolidation_batch_enabled() else 'on')"),
    "NEXUS_RELATION_TRAILS": (
        "off",
        "('off' if not __import__('backend.kb.trails', fromlist=['x'])"
        ".trails_enabled() else 'on')"),
    "NEXUS_TRAIL_SLOTS": (
        "3",
        "str(__import__('backend.kb.trails', fromlist=['x']).trail_slots())"),
    "NEXUS_RELATION_VOCABULARY": (
        "{tmp}/relations.json",
        "'{tmp}/relations.json' if list(__import__("
        "'backend.kb.vocabulary', fromlist=['x']).load_relations()) == "
        "['binds_to'] else 'NOT-LOADED'"),
    "NEXUS_SEMANTIC_SLOTS": (
        "5",
        "str(__import__('backend.kb.semantic_index', fromlist=['x'])"
        ".semantic_slots())"),
    "NEXUS_SEMANTIC_MIN_SIMILARITY": (
        "0.42",
        "str(__import__('backend.kb.semantic_index', fromlist=['x'])"
        ".semantic_min_similarity())"),
    "NEXUS_SEMANTIC_INDEX_BATCH": (
        "17",
        "str(__import__('backend.kb.semantic_index', fromlist=['x'])"
        ".index_batch_size())"),
    "NEXUS_SEMANTIC_INDEX_EXCLUDE": (
        "only_this_one",
        "','.join(__import__('backend.kb.semantic_index', fromlist=['x'])"
        ".excluded_relations())"),
    "NEXUS_CITATION_FORECAST": (
        "off",
        "('off' if not __import__('backend.cortex.forecast', fromlist=['x'])"
        ".forecast_enabled() else 'on')"),
    "NEXUS_SUBSTRATE_WEIGHT_RESTORE": (
        "on",
        "('on' if __import__('backend.loop.agentic_loop', fromlist=['x'])"
        ".substrate_weight_restore_enabled() else 'off')"),
    # CHANGE A — probed by the consequence the loop reads, not by an echo.
    "NEXUS_HISTORY_WRAP": (
        "off",
        "('off' if not __import__('backend.loop.history', fromlist=['x'])"
        ".history_wrap_enabled() else 'on')"),
    # C-38 — the reply separation at the provider boundary, probed the same way.
    "NEXUS_REPLY_SEPARATION": (
        "off",
        "('off' if not __import__('backend.providers.base', fromlist=['x'])"
        ".reply_separation_enabled() else 'on')"),
    "NEXUS_ARCHIVAL_LEDGER": (
        "off",
        "('off' if not __import__('backend.kb.tiered_store', fromlist=['x'])"
        ".archival_ledger_enabled() else 'on')"),
    "NEXUS_REFUSAL_LEDGER": (
        "off",
        "('off' if not __import__('backend.kb.recalibration', fromlist=['x'])"
        ".refusal_ledger_enabled() else 'on')"),
    "NEXUS_HEBBIAN_PAIRING": (
        "full",
        "__import__('backend.cortex.hebbian', fromlist=['x'])"
        ".pairing_mode()"),
    "NEXUS_HEBBIAN_TOPK": (
        "7",
        "str(__import__('backend.cortex.hebbian', fromlist=['x'])"
        ".pairing_topk())"),
    "NEXUS_SUBSTRATE_CLOCK_REFRESH": (
        "on",
        "('on' if __import__('backend.kb.tiered_store', fromlist=['x'])"
        ".substrate_clock_refresh_enabled() else 'off')"),
    "NEXUS_CITATION_CAPTURE": (
        "on",
        "('on' if __import__('backend.eval.citation_capture', fromlist=['x'])"
        ".capture_enabled() else 'off')"),
    "NEXUS_FIRE_CAPTURE": (
        "on",
        "('on' if __import__('backend.eval.fire_capture', fromlist=['x'])"
        ".capture_enabled() else 'off')"),
    # W5 — the reference gateway's switch, read at call time.
    "NEXUS_REFERENCE_GATEWAY": (
        "on",
        "('on' if __import__('backend.kb.source_policy', fromlist=['x'])"
        ".gateway_on() else 'off')"),
    # W6 — when an answer is released, read at call time.
    "NEXUS_ANSWER_RELEASE": (
        "live",
        "__import__('backend.loop.agentic_loop', fromlist=['x']).answer_release()"),
    # MCP-2 — the MCP process's settings, read at call time.
    "NEXUS_MCP_OWNER": (
        "alice@example",
        "__import__('backend.mcp.config', fromlist=['x']).owner()"),
    # W2 — an absolute folder: a relative one is the per-user default in MCP
    # mode (run/specs/W2.md D5), so `mcpdata` no longer resolves to itself.
    "NEXUS_MCP_DATA_ROOT": (
        "{tmp}/mcpdata",
        "__import__('backend.mcp.config', fromlist=['x']).data_root().as_posix()"),
    "NEXUS_MCP_HANDLE_TIMEOUT": (
        "42",
        "str(int(__import__('backend.mcp.handles', fromlist=['x']).handle_timeout_seconds()))"),
    # W1 — the console's data root, probed through the paths `create_app()`
    # passes to `create_services` when uvicorn calls it with no arguments.
    "NEXUS_DATA_ROOT": (
        "rootdir",
        "__import__('backend.data_root', fromlist=['x'])"
        ".console_kwargs()['kb_db_path'].parent.as_posix()"),
    # W3b part 3 — the store selection, probed through the one reader every
    # entry point uses (`backend/store/target.py`): the backend name as the
    # factory resolves it, and the URL as the Postgres target carries it.
    "NEXUS_STORE": (
        "postgres",
        "__import__('backend.store.target', fromlist=['x']).store_backend_name()"),
    "NEXUS_DATABASE_URL": (
        "postgresql://probe@db.invalid:5432/probe_test",
        "__import__('backend.store.target', fromlist=['x']).database_url()"),
    # W0b — the replay capture's two switches, probed by the predicates the
    # one writer of request_bodies and the reply stream read at call time.
    "NEXUS_KEEP_REQUESTS": (
        "off",
        "('off' if not __import__('backend.providers.replay_capture', fromlist=['x'])"
        ".keep_requests_enabled() else 'on')"),
    "NEXUS_INFERENCE_SEED": (
        "off",
        "('off' if not __import__('backend.providers.replay_capture', fromlist=['x'])"
        ".seed_enabled() else 'on')"),
    # W9 (D7) — anchoring, probed by the two readers the idle cycle calls.
    "NEXUS_ANCHOR": (
        "rfc3161",
        "__import__('backend.memory.anchor', fromlist=['x']).anchor_mode()"),
    "NEXUS_ANCHOR_URL": (
        "https://tsa.example/tsr",
        "__import__('backend.memory.anchor', fromlist=['x']).anchor_url()"),
    # W10b (D10) — request signing, probed by the reader the recorder calls before each request.
    "NEXUS_SIGN_REQUESTS": (
        "on",
        "__import__('backend.memory.http_signatures', fromlist=['x']).mode()"),
    "NEXUS_CITATION_RULES": (
        "off",
        "('off' if not __import__('backend.kb.citation', fromlist=['x'])"
        ".citation_rules_enabled() else 'on')"),
    "NEXUS_PROMPT_RENDER": (
        "legacy",
        "__import__('backend.loop.prompt_render', fromlist=['x'])"
        ".prompt_render_mode()"),
    "NEXUS_CURIOSITY_PLACEMENT": (
        "turn",
        "__import__('backend.cortex.curiosity_placement', fromlist=['x'])"
        ".curiosity_placement()"),
    # FZ-2 — the freeze switch, probed by its consequence (the mode the
    # learning layer reads), and the operator-asserted from-date, probed as
    # the value the surfaces would display.
    "NEXUS_LEARNING": (
        "frozen",
        "__import__('backend.learning', fromlist=['x']).learning_mode()"),
    "NEXUS_LEARNING_FROZEN_SINCE": (
        "2026-09-05",
        "__import__('backend.learning', fromlist=['x']).frozen_since()"),
    # W4 part 1 — the identity boundary's three settings, probed through the
    # one reader the factory and start.py both call (`backend/identity.py`),
    # at call time: the mode as decided, the header name as written, the owner.
    "NEXUS_AUTH_MODE": (
        "proxy",
        "__import__('backend.identity', fromlist=['x']).identity_config().mode"),
    "NEXUS_IDENTITY_HEADER": (
        "Cf-Access-Authenticated-User-Email",
        "__import__('backend.identity', fromlist=['x']).identity_config().header"),
    "NEXUS_OWNER_IDENTITY": (
        "owner@example.test",
        "__import__('backend.identity', fromlist=['x']).identity_config().owner"),
    # W10c — the interceptor's five settings, each probed through the reader `load()` composes, at call time.
    "NEXUS_INTERCEPT_UPSTREAM": (
        "https://provider.example/base",
        "__import__('backend.intercept.config', fromlist=['x']).upstream()"),
    "NEXUS_INTERCEPT_DATA_ROOT": (
        "{tmp}/intercepted",
        "__import__('backend.intercept.config', fromlist=['x']).data_root().as_posix()"),
    "NEXUS_INTERCEPT_OWNER": (
        "owner@example.test",
        "__import__('backend.intercept.config', fromlist=['x']).owner()"),
    "NEXUS_INTERCEPT_INJECT": (
        "on",
        "('on' if __import__('backend.intercept.config', fromlist=['x']).inject() else 'off')"),
    "NEXUS_INTERCEPT_PROXY_HEADERS": (
        "x-example-user,x-example-*",
        "','.join(__import__('backend.intercept.config', fromlist=['x']).proxy_headers())"),
}

# Read by nothing under `backend/`, measured by AST over every tracked file —
# and now REMOVED from CLAUDE.md's block rather than wired, on H.4's
# precedent: when three declared SSE events turned out to have no emitter, the
# contract was corrected to match reality instead of emitters being added to
# match the contract.
#
# The set outlives the documentation on purpose. Three of these are simply
# absent from the code; the fourth, NUM_CTX, must STAY absent — `NUM_CTX =
# 8192` in `backend/providers/ollama.py` is Law 5's ceiling, and a reader here
# would let a `.env` line raise the cap the law exists to hold. The guard
# below fails the moment any of them acquires one.
UNREAD_BY_BACKEND = {"DB_PATH", "ARCHIVE_DB", "CHROMA_DIR", "NUM_CTX"}

# Resolved at MODULE SCOPE, therefore before `load_env_files()` at
# `backend/server.py:109`. Each is xfail(strict) below: the sentinel states
# the rule, the mark states that today's code breaks it, and a fix flips the
# mark rather than editing the rule.
# EMPTY, and that is the finding this file was built to reach. The two real
# instances — LM_STUDIO_URL and SEARXNG_URL — now resolve in `__init__`.
#
# NUM_CTX was never here: the first sweep matched every string constant equal
# to a key name, which caught `__all__ = ["NUM_CTX", ...]` in
# `backend/ollama_client.py` and reported it as an import-time read. It is not
# read from the environment anywhere. The measurement could not tell the two
# apart — "resolved before the file was read" and "never read" both leave the
# module holding its default — so the classification was wrong until the sweep
# was re-derived to match only `os.getenv` / `os.environ` sites.
IMPORT_TIME_READS: set[str] = set()


# --- machinery -------------------------------------------------------------


def repo_env_value(key: str) -> "str | None":
    if not REPO_ENV.is_file():
        return None
    from dotenv import dotenv_values
    return dotenv_values(REPO_ENV).get(key)


# Files a probe needs beside its `.env`. Keyed by setting, so the fixture a
# probe depends on lives next to the probe rather than in a shared pile.
SIDE_FILES: dict[str, dict[str, str]] = {
    "NEXUS_CONTRADICTION_PAIRS": {"pairs.json": '[["alpha", "omega"]]'},
    "NEXUS_RELATION_VOCABULARY": {"relations.json": '["binds_to"]'},
}


def _resolve_in_child(tmp_path: Path, key: str, value: str, expr: str) -> str:
    """Write `.env`, launch a fresh interpreter there, evaluate `expr`."""
    for name, body in SIDE_FILES.get(key, {}).items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    (tmp_path / ".env").write_text(f"{key}={value}\n", encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        f"sys.path.insert(0, r{str(ROOT)!r})\n"
        "import backend.server            # loads .env at import, as production does\n"
        f"print(repr({expr}))\n",
        encoding="utf-8")
    # The key must NOT be in the child's real environment, or `override=False`
    # would make this test pass without the file being read at all.
    env = {k: v for k, v in os.environ.items() if k != key}
    # THE ISOLATION. `NEXUS_ENV_ROOT` makes `tmp_path` the one `.env` location
    # consulted, so the operator's repo-root file is neither read nor required
    # to be absent. Before this, five of these cases failed on their machine
    # purely because their working configuration set those keys, and the only
    # remedy on offer was deleting it — which is how a guard gets muted.
    #
    # It is NOT the scrub that fixes them. The scrub clears `os.environ`;
    # `load_env_files()` reads the FILE at import of `backend.server`, long
    # after, and would have found the repo root regardless.
    env["NEXUS_ENV_ROOT"] = str(tmp_path)
    proc = _proc.run([sys.executable, str(probe)], cwd=tmp_path, env=env,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)
    assert proc.returncode == 0, f"probe failed:\n{proc.stderr[-2000:]}"
    return proc.stdout.strip().splitlines()[-1]



def _env_reads(tree) -> list:
    """ACTUAL environment reads only: `os.getenv(K)`, `os.environ.get(K)`,
    `os.environ[K]`.

    The first version of this sweep matched every string constant equal to a
    key name. That caught `__all__ = ["NUM_CTX", ...]` in
    `backend/ollama_client.py` and reported NUM_CTX as an import-time read
    when it is not read from the environment at all — and the runtime
    measurement could not correct it, because "resolved before the file was
    read" and "never read" both leave the module holding its default. A guard
    that answers a different question from the one it is named for is how a
    fix gets applied to the wrong file.
    """
    import ast
    out = []
    for n in ast.walk(tree):
        key = None
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr in ("getenv", "get") and n.args:
                arg = n.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    owner = ast.unparse(n.func.value)
                    if "environ" in owner or owner == "os":
                        key = arg.value
        elif (isinstance(n, ast.Subscript)
              and isinstance(n.value, ast.Attribute)
              and n.value.attr == "environ"
              and isinstance(n.slice, ast.Constant)):
            key = n.slice.value
        if key:
            out.append((key, n.lineno))
    return out


# --- the obligation --------------------------------------------------------


def test_every_documented_setting_has_a_probe():
    """DERIVED, so a new knob cannot be documented without being covered."""
    documented = set(documented_settings())
    covered = set(PROBES) | UNREAD_BY_BACKEND
    missing = sorted(documented - covered)
    assert not missing, (
        f"docs/CONTRACTS.md documents {missing} with no probe in this file. Add one — "
        f"a setting an operator is told to set and nothing verifies is how "
        f"NEXUS_SEMANTIC_SLOTS came to be reported as not taking effect.")
    # Only PROBES is checked for staleness. UNREAD_BY_BACKEND deliberately
    # outlives the documentation: its four members were REMOVED from CLAUDE.md
    # rather than wired, and the set is what stops them being quietly wired
    # afterwards — NUM_CTX especially, where a reader would let a `.env` line
    # raise Law 5's ceiling.
    stale = sorted(set(PROBES) - documented)
    assert not stale, (
        f"probed but no longer documented: {stale}. Remove the probe or "
        f"restore the documentation — a guard for a setting nobody is told "
        f"about is a guard nobody maintains.")


def test_the_documented_list_is_not_empty():
    """NON-VACUITY. If the docs/CONTRACTS.md heading is renamed the parser returns []
    and every assertion above passes while covering nothing."""
    assert len(documented_settings()) >= 15


@pytest.mark.parametrize("key", sorted(UNREAD_BY_BACKEND))
def test_settings_documented_but_read_by_nothing_are_declared(key):
    """These are promised to the operator and consumed by no backend module.

    Pinned so the set cannot grow silently. If one acquires a reader, move it
    into PROBES; if one is dropped from docs/CONTRACTS.md, remove it here.
    """
    import ast
    sys.path.insert(0, str(ROOT / "tests"))
    from _tracked import tracked_backend
    for path in tracked_backend():
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        for node, lineno in _env_reads(tree):
            if node == key:
                pytest.fail(
                    f"{key} now has a reader at {path}:{lineno} — move it "
                    f"from UNREAD_BY_BACKEND into PROBES and verify it")


# --- the rule ---------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(PROBES))
def test_the_setting_takes_effect_from_dotenv_alone(key, tmp_path, request):
    """THE SENTINEL. Set only in `.env`; the consumer must see it."""
    if key in IMPORT_TIME_READS:
        request.node.add_marker(pytest.mark.xfail(
            strict=True,
            reason=(f"{key} is resolved at MODULE SCOPE, which runs before "
                    f"backend/server.py:109 calls load_env_files(). The file "
                    f"is read and the value is already gone. Fix by moving "
                    f"the read into a function, then delete this mark.")))
    # The repo-root shadow precondition is GONE, and its removal is the point.
    # It used to fail and tell the operator to delete a line from their own
    # `.env`; `NEXUS_ENV_ROOT` relocates discovery instead, so a configured
    # machine and a bare checkout now run this identically.

    value, expr = PROBES[key]
    # `{tmp}` lets a probe name a file it needs; forward slashes so the value
    # is identical in the .env, in the expression and in the assertion on
    # Windows as well as here.
    tmp_str = tmp_path.as_posix()
    value = value.format(tmp=tmp_str)
    expr = expr.format(tmp=tmp_str)
    got = _resolve_in_child(tmp_path, key, value, expr)
    assert got == repr(value), (
        f"{key} set to {value!r} in .env, but the consumer resolved {got}. "
        f"The file was read — NEXUS_API_TOKEN from the same file works — so "
        f"this key is read somewhere the loaded value cannot reach it.")


# ---------------------------------------------------------------------------
# Every documented rollback must be tested IN the position it exists for
# ---------------------------------------------------------------------------
#
# MEASURED, and it is why this section exists: with every documented setting at
# a plausible deployment value, 47 tests failed — 39 from `NEXUS_RECALIBRATION`
# alone. Each of those rollbacks was built so a nervous operator can turn a
# feature off, and the suite proved the feature only in the ON position. A
# rollback nobody has run is a rollback nobody should trust.


def rollback_candidates() -> dict[str, str]:
    """Documented settings whose own comment says they can be turned off.

    Derived from the comment text in docs/CONTRACTS.md's block, so a new rollback
    cannot ship without landing here. `OFF_VALUES` must then say what "off"
    MEANS for it — that part is per-setting and cannot be derived, because
    `off`, `snapshot`, `sync` and `""` are all somebody's off position.
    """
    md = (ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
    block = md.split("## Env (.env in repo root)")[1].split("```")[1]
    out = {}
    for line in block.splitlines():
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=([^#]*)(#.*)?$", line)
        if not m:
            continue
        key, comment = m.group(1), (m.group(3) or "")
        if re.search(r"\boff\b|rollback|kill switch", comment, re.I):
            out[key] = comment.strip()
    return out


# What "off" is, per setting. Not derivable — `NEXUS_GOVERNANCE_SEAL`'s
# rollback is `snapshot` and `NEXUS_STARTUP_PROBE`'s is `sync`, both of which
# are named in docs/CONTRACTS.md's prose rather than in the block's comment, and
# neither is the literal string "off" even though "off" is also a valid mode.
OFF_VALUES = {
    "NEXUS_RECALIBRATION":          "off",       # S.2's hard kill switch
    "NEXUS_EXTRACTION_VOCABULARY":  "off",       # V.1's byte-identical rollback
    "NEXUS_GOVERNANCE_SEAL":        "snapshot",  # S.2b — pre-S.2b behaviour
    "NEXUS_STARTUP_PROBE":          "sync",      # R.2 — pre-R.2 behaviour
    "NEXUS_CONSOLIDATION_BATCH":    "off",       # AA — per-row commits back
    "NEXUS_RELATION_TRAILS":        "off",       # AB — no trail laying
    "NEXUS_TRAIL_SLOTS":            "0",         # AB — channel applied nowhere
    "NEXUS_CITATION_FORECAST":      "off",       # AF.1 — pre-AF.1 turn exactly
    # AK — the rollback POSITION is "on" (the pre-AK restoration), the same
    # inversion NEXUS_STARTUP_PROBE=sync and NEXUS_GOVERNANCE_SEAL=snapshot
    # already are: "off" is the shipped default, not the rollback.
    "NEXUS_SUBSTRATE_WEIGHT_RESTORE": "on",
    "NEXUS_ARCHIVAL_LEDGER":        "off",       # AL — no events, pre-AL store
    "NEXUS_REFUSAL_LEDGER":         "off",       # AT — declared only, pre-AT store
    "NEXUS_HEBBIAN_PAIRING":        "full",      # AM — pre-AM pairing loop
    # AN.2 — rollback position "on" (pre-AN.2 clock refresh), the standing
    # inversion: "off" is the shipped default.
    "NEXUS_SUBSTRATE_CLOCK_REFRESH": "on",
    "NEXUS_CITATION_CAPTURE":       "off",       # AO — off writes nothing
    "NEXUS_FIRE_CAPTURE":           "off",       # CP — off writes nothing, walks nothing
    "NEXUS_REFERENCE_GATEWAY":      "off",       # W5 — the request, the record and the store as before, byte for byte
    "NEXUS_ANSWER_RELEASE":         "live",      # W6 — frames released as they arrive, the delivery before W6
    "NEXUS_CITATION_RULES":         "off",       # AU — the F.5 test exactly
    "NEXUS_PROMPT_RENDER":          "legacy",    # AW — the pre-AW block byte for byte
    "NEXUS_CURIOSITY_PLACEMENT":    "turn",      # AX.1 — the per-turn call and frame exactly
    # FZ-2 — the rollback POSITION is `live`, the shipped default: the switch
    # exists to be set to `frozen`, and turning it "off" is going back to
    # live. The same inversion as NEXUS_SUBSTRATE_WEIGHT_RESTORE.
    "NEXUS_LEARNING":               "live",
    "NEXUS_HISTORY_WRAP":           "off",       # CHANGE A — the pre-A history path byte for byte
    "NEXUS_REPLY_SEPARATION":       "off",       # C-38 — the pre-C-38 reply path: content is the answer, nothing parsed
    "NEXUS_KEEP_REQUESTS":          "off",       # W0b — no request bytes kept; records otherwise unchanged
    "NEXUS_INFERENCE_SEED":         "off",       # W0b — no seed sent: the pre-W0b request exactly
    "NEXUS_ANCHOR":                 "off",       # W9 — nothing leaves the machine: the shipped default
    "NEXUS_SIGN_REQUESTS":          "off",       # W10b — no signature header: the shipped default
    "NEXUS_INTERCEPT_INJECT":       "off",       # W10c — a pure recorder: nothing added, no engine loaded; the shipped default
}


def test_every_documented_rollback_has_an_off_value():
    """The derived obligation. A new rollback cannot be documented without
    someone stating what turning it off looks like."""
    missing = sorted(set(rollback_candidates()) - set(OFF_VALUES))
    assert not missing, (
        f"docs/CONTRACTS.md documents {missing} as having an off position and nothing "
        f"here says what that position is. Add it to OFF_VALUES — and then a "
        f"test has to exercise it.")
    stale = sorted(set(OFF_VALUES) - set(rollback_candidates()))
    assert not stale, f"no longer documented as a rollback: {stale}"


def test_the_rollback_set_is_not_empty():
    """NON-VACUITY. If the comment style changes the regex returns {} and the
    obligation above passes while covering nothing."""
    assert len(rollback_candidates()) >= 3


@pytest.mark.parametrize("key", sorted(OFF_VALUES))
def test_some_test_runs_with_this_rollback_off(key):
    """THE SENTINEL. At least one test must exercise the off position.

    AST over tracked test files, not a text search: the value has to be the
    one `OFF_VALUES` names, set on that key, in a call that actually sets an
    environment variable. A docstring mentioning `NEXUS_RECALIBRATION=off`
    would satisfy grep and prove nothing — and every file here has such a
    docstring.
    """
    import ast
    sys.path.insert(0, str(ROOT / "tests"))
    from _tracked import tracked
    want = OFF_VALUES[key]
    sites = []
    for path in tracked("tests"):
        f = Path(path)
        if f.suffix != ".py":
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            k = v = None
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setenv" and len(node.args) >= 2):
                k, v = node.args[0], node.args[1]
            elif (isinstance(node, ast.Assign) and len(node.targets) == 1
                  and isinstance(node.targets[0], ast.Subscript)
                  and isinstance(node.targets[0].value, ast.Attribute)
                  and node.targets[0].value.attr == "environ"):
                k, v = node.targets[0].slice, node.value
            if (isinstance(k, ast.Constant) and k.value == key
                    and isinstance(v, ast.Constant) and v.value == want):
                sites.append(f"{f.name}:{node.lineno}")
    assert sites, (
        f"{key} is documented as a rollback with an off position of {want!r}, "
        f"and no test ever runs with it there. It exists for the operator who "
        f"needs to turn the feature off; shipping it unexercised in that "
        f"position is shipping a promise nobody has kept.")
