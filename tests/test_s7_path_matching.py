"""S7 — a path-shaped comparison is tested on the platform that separates the cases.

THE RULE (Fable, 1 October; applied once here, binding from now): a build
stamps the platform beside the environment, and every test helper that
matches a file path against a `/`-string gets a companion run with
Windows-style paths. The FZ-2 trace was the fifth instance of the class —
`"/backend/" in filename` matched nothing on the operating instance and
seven tests failed there while passing here — and the sweep this file holds
is what should have run before it shipped.

DERIVED, THEN CLASSIFIED. The population is every string constant containing
`/` used with `in`/`not in` or `.startswith`/`.endswith` in a tracked test
file, found by AST (40 sites in 20 files when written, printed). Each must
be classified with a reason — a route path, a URL or a content type is
forward-slash by specification and is not a filename; a literal searched in
source text is text; the three that ARE filesystem-shaped carry companions
below. A new site fails until it is classified; a stale entry fails too.

Platform stamp on every run: `sys.platform`, `os.sep`.
"""
from __future__ import annotations

import ast
import os
import platform
import re
import sys
from pathlib import PureWindowsPath

from tests._tracked import REPO, tracked, tracked_backend

ROUTE, URL, CONTENT_TYPE, TEXT, SOURCE_TEXT = "route", "url", "content type", "text", "source text"
SOURCE_LITERAL, POSIX_BY_CONTRACT, COMPANION = "source literal", "posix by contract", "companion"

# (file, constant) -> category. Keyed by the constant, not the line, so an
# edit that moves a line does not rot the table.
CLASSIFIED: dict[tuple[str, str], str] = {
    ("tests/_writers.py", "data/"): SOURCE_LITERAL,         # `"data/..."` literals in backend SOURCE — companion below
    ("tests/test_as_retention_cost.py", "mean / 2"): TEXT,
    ("tests/test_assertion_alert.py", "/kb/assert/confirm"): ROUTE,
    ("tests/test_ax_curiosity_placement.py", "backend/loop/"): POSIX_BY_CONTRACT,          # keys are `.as_posix()` — companion below
    ("tests/test_ax_curiosity_placement.py", "backend/cortex/curiosity_placement.py"): POSIX_BY_CONTRACT,
    ("tests/test_ax_curiosity_placement.py", "backend/server.py"): POSIX_BY_CONTRACT,
    ("tests/test_w5_reference_gateway.py", "backend/loop/"): POSIX_BY_CONTRACT,            # `_backend_sources()` keys are `.as_posix()`;
    ("tests/test_w5_reference_gateway.py", "backend/mcp/"): POSIX_BY_CONTRACT,             #   its companion is
    ("tests/test_w5_reference_gateway.py", "backend/server.py"): POSIX_BY_CONTRACT,        #   test_p4b_the_sources_are_keyed_by_posix_paths
    ("tests/test_w5_reference_gateway.py", "/"): COMPANION,                                # is itself the posix companion
    ("tests/test_ba_fix1_summary_role.py", "[Auto-summary stub: 2 user / 1 assistant turns compacted.]"): TEXT,
    ("tests/test_bind_conditional_routes.py", "/lineage"): ROUTE,
    ("tests/test_w6_close_record.py", "/work-units/events"): ROUTE,                         # a route's path among the app's served paths
    ("tests/test_derived_writer_guard.py", "/"): COMPANION,                                # is itself the M.5 Windows companion
    ("tests/test_governance_c.py", "/secret"): ROUTE,
    ("tests/test_import_purity.py", "data/side_effect.txt"): POSIX_BY_CONTRACT,            # `snap()` keys are posix; its own companion is
    ("tests/test_import_purity.py", "data/notes.txt"): POSIX_BY_CONTRACT,                  #   test_probe_keys_are_posix_on_every_platform
    ("tests/test_import_purity.py", "/"): COMPANION,
    ("tests/test_just_culture_guard.py", "/docs"): ROUTE,
    ("tests/test_just_culture_guard.py", "/redoc"): ROUTE,
    ("tests/test_just_culture_guard.py", "/openapi"): ROUTE,
    ("tests/test_just_culture_guard.py", "application/json"): CONTENT_TYPE,
    ("tests/test_just_culture_guard.py", "/belief/{belief_id}/recall"): ROUTE,
    ("tests/test_law3_discoverability.py", "/confirm"): ROUTE,
    ("tests/test_phase_10a.py", "/search"): URL,
    ("tests/test_phase_10a.py", "https://docs.python.org"): URL,
    ("tests/test_phase_10b.py", "https://a.example"): URL,
    ("tests/test_phase_8.py", "/vendor/d3.v7.min.js"): URL,                                # a script src inside served HTML
    ("tests/test_phase_8.py", "text/html"): CONTENT_TYPE,
    ("tests/test_provenance_surface.py", "http://www.w3.org/"): URL,
    ("tests/test_sse_and_health_hygiene.py", "fetch('/health')"): SOURCE_TEXT,
    ("tests/test_sse_and_health_hygiene.py", "fetch('/healthz')"): SOURCE_TEXT,
    ("tests/test_sse_and_health_hygiene.py", '@app.get("/health")'): SOURCE_TEXT,
    ("tests/test_sse_and_health_hygiene.py", "/health'"): SOURCE_TEXT,
    ("tests/test_sse_and_health_hygiene.py", "GET /health "): SOURCE_TEXT,
    ("tests/test_startup_contract.py", "/api/tags"): URL,
    ("tests/test_fz_freeze.py", "/planted/write"): ROUTE,                                 # T1e's planted route
    ("tests/test_s7_path_matching.py", "/"): COMPANION,                                   # this derivation's own test
    ("tests/test_s7_path_matching.py", "backend/loop/"): COMPANION,                        # the replay below
    ("tests/test_b44_worktree_sentinel.py", "tests/conftest.py"): POSIX_BY_CONTRACT,       # `.replace("\\", "/")` one call earlier
    ("tests/test_b44_worktree_sentinel.py", "data/kb.db"): POSIX_BY_CONTRACT,              # a dict key the conftest spells posix
    ("tests/test_as2_reconstruction.py", "backend/kb/booster.py"): POSIX_BY_CONTRACT,      # T2c's planted caller; keys are `.as_posix()`
    ("tests/test_c38_reply_separation.py", "backend/providers/"): POSIX_BY_CONTRACT,       # the producer's path, excluded from the readers; keys are `.as_posix()`
    ("tests/test_c38_reply_separation.py", "backend/cortex/extraction.py"): POSIX_BY_CONTRACT,   # T5c's planted reader; keys are `.as_posix()`
    ("tests/test_mcp1_split.py", "/chat"): POSIX_BY_CONTRACT,                                # a ROUTE path from `app.routes` (HTTP, never a filesystem path)
    ("tests/test_mcp2_build.py", "backend/mcp/planted.py"): POSIX_BY_CONTRACT,               # planted source keys beside `.as_posix()` keys (companions)
    ("tests/test_w1_ignore_rules.py", "data/citation_capture.jsonl"): POSIX_BY_CONTRACT,    # W1 — the writer ratchet's default paths are posix strings; git check-ignore takes them as given
    ("tests/test_w1_ignore_rules.py", "data/kb.db-journal"): POSIX_BY_CONTRACT,
    ("tests/test_w1_ignore_rules.py", "data/probe_log.jsonl"): POSIX_BY_CONTRACT,           # the planted writer's constant, read back by the same derivation
    ("tests/test_w1_readonly_open.py", "tests/"): POSIX_BY_CONTRACT,                        # W1 — a prefix of `.relative_to(ROOT).as_posix()` keys
    ("tests/test_w1_shim_retired.py", "tests/_tracked.py:"): POSIX_BY_CONTRACT,             # W1 part 5h — the shim guard's hit text begins with its `.as_posix()` key and a colon
    ("tests/test_w1_readonly_open.py", "scripts/"): POSIX_BY_CONTRACT,                      # W1 part 5f — the same keys, narrowed to scripts and root for G's connect rule
    ("tests/test_w1_readonly_open.py", "/"): POSIX_BY_CONTRACT,                             # W1 part 5f — a root file is a key with no separator, in `.as_posix()` form
    ("tests/test_w1_readonly_open.py", "scripts/coactivation_eligibility.py"): POSIX_BY_CONTRACT,   # W1 part 5f — non-vacuity: MF1's script is in the population
    ("tests/test_w1_ignore_rules.py", "data/tester1/kb.db"): POSIX_BY_CONTRACT,             # W1 part 5f — the nested data root's paths, derived posix, given to git check-ignore as such
    ("tests/test_w1_ignore_rules.py", "data/tester1/kb.db-journal"): POSIX_BY_CONTRACT,
    ("tests/test_w1_ignore_rules.py", "data/tester1/chroma/anything"): POSIX_BY_CONTRACT,
    ("tests/test_w1_ignore_rules.py", "data/tester1/citation_capture.jsonl"): POSIX_BY_CONTRACT,
    ("tests/test_mcp2_build.py", "backend/mcp/p1.py"): POSIX_BY_CONTRACT,
    ("tests/test_mcp2_build.py", "backend/mcp/p2.py"): POSIX_BY_CONTRACT,
    ("tests/test_c38_reply_separation.py", "\n\n</nexus-block>"): TEXT,                    # the wrapper's close around an empty answer
    ("tests/test_w0b_capture.py", "/api/tags"): URL,                                       # W0b-11's fake daemon routes by URL
    ("tests/_w0b_wire.py", "/api/tags"): URL,                                              # W0b part 4: the fake transport routes by URL
    ("tests/_w0b_wire.py", "/api/version"): URL,
    ("tests/_w0b_wire.py", "/v1/chat/completions"): URL,
    ("tests/_w0b_wire.py", "/v1/models"): URL,
    ("tests/test_w1_start.py", "[y/N]"): TEXT,                                            # W1 part 5g (C6): confirm()'s hint, read from the prompt input() received
    ("tests/test_w3_serve_stub.py", "[y/N]"): TEXT,                                       # W3 part 1 (V5): the first prompt's hint, read from a child's output
    ("tests/_w3_api_wire.py", "/models"): URL,                                            # W3 part 2: the fake api transport routes by URL
    ("tests/_w3_api_wire.py", "/embeddings"): URL,
    ("tests/_w3_api_wire.py", "/v1/messages"): URL,
    ("tests/_w3_api_wire.py", "/chat/completions"): URL,
    ("tests/test_w3_api_providers.py", "/chat/completions"): URL,                         # W3 part 2: the thread listener routes by path
    ("tests/test_w3_api_providers.py", "/v1/messages"): URL,
    ("tests/test_w3_api_providers.py", "/v1/models"): URL,                                # the model-server check's URL, asserted
    ("tests/test_w3_deploy.py", "# syntax=docker/dockerfile:1"): SOURCE_TEXT,               # W3 part 3: the Dockerfile's BuildKit directive, searched in its text
    ("tests/test_w3_deploy.py", "/healthz"): URL,                                          # W3 part 3: the HEALTHCHECK's request path, inside the instruction's text
    ("tests/test_w3_deploy.py", "/tmp"): POSIX_BY_CONTRACT,                                # W3 part 3: a mount path inside the Linux container (compose's tmpfs list), never a host path
    ("tests/test_w3_backup.py", "/var/lib/nexus"): POSIX_BY_CONTRACT,                      # W3 part 4: the data volume's mount path inside the container (the backup service's volumes)
    ("tests/test_w3_backup.py", "scripts/backup.py"): POSIX_BY_CONTRACT,                   # W3 part 4: the path backup.sh (a POSIX shell script, skipped without sh) hands python
    ("tests/test_w3_ci.py", "deploy/Dockerfile"): SOURCE_TEXT,                              # W3 part 5: a workflow step's `run` text, searched
    ("tests/test_w3_ci.py", "scripts/verify_deploy.py"): SOURCE_TEXT,
    ("tests/test_w3_ci.py", "run --rm --entrypoint python backup scripts/restore_verify.py"): SOURCE_TEXT,   # the compose header's command, searched
    ("tests/test_w3_ci.py", "third-party action someone/else@v1"): TEXT,                    # a rule's message, asserted on a plant
    ("tests/test_w3_ci.py", "actions/upload-artifact@"): TEXT,                             # W9: an action reference, matched by its prefix
    ("tests/test_w9_uat_verification.py", "tests/_uat_tombstone.py"): TEXT,               # W9: words in a report row, read as text
    ("tests/test_w9_verifier_fixes.py", "/a/private/place"): TEXT,                        # W9: a planted path, searched for in a response
    ("tests/test_w9_delta_fixes.py", "/a/private/place"): TEXT,                           # W9: the same plant, on the receipt route
    ("tests/test_w10b_network_trace.py", "/c/en/"): TEXT,                             # W10b: a URL's path, matched as text by a stand-in
    ("tests/test_w3_ci.py", "a file under backend/memory/: the plan says postgres=false"): TEXT,      # C16 (Amendment 6): a rule's message, asserted on a plant
    ("tests/test_w3_ci.py", "backend/"): POSIX_BY_CONTRACT,                                           # C16: a case's file name as git lists it, read to show each watched directory has a case
    ("tests/test_w2_bundle_smoke.py", "Library not loaded: @rpath/Nothing"): TEXT,          # W3 fixes 1: a planted stderr line, asserted in the smoke's EOFError text
    ("tests/test_w3_backup.py", "/var/backups/nexus/20261108T030000Z/MANIFEST.json"): POSIX_BY_CONTRACT,   # W3 fixes 2: the manifest's path inside the backup service's mount, asserted in the remote argv
    ("tests/test_w1_start.py", "[Y/n]"): TEXT,
    ("tests/test_w1_start.py", "/kb/seed/"): TEXT,                                         # W1 part 5g (C10): the seed tip's route, searched in captured stdout
    ("tests/test_tb_frozen_identifiers.py", "backend/zz_planted.py"): POSIX_BY_CONTRACT,   # W11b T3's planted module, a key beside `.as_posix()` keys
    ("tests/test_w2_store_lock.py", "scripts/planted_archiver.py"): POSIX_BY_CONTRACT,     # W2 part 1 — G's planted writer, a key beside the population's `.as_posix()` keys
    ("tests/test_w2_store_lock.py", "tests/"): POSIX_BY_CONTRACT,                          # W2 part 1 — a prefix of `.relative_to(REPO).as_posix()` keys (G's population leaves tests out)
    ("tests/test_w2_bundle.py", "scripts/build_bundle.py"): SOURCE_TEXT,                   # W2 part 3 — searched in the two wrappers' text
    ("tests/test_w2_bundle.py", "server/nexus-mcp/_internal/.env"): POSIX_BY_CONTRACT,     # W2 part 3 — a bundle entry: `bundle_entries` names them `.as_posix()`, zip names on every platform
    ("tests/test_w3b_dialect.py", "backend/kb/tiered_store.py"): POSIX_BY_CONTRACT,       # W3b part 2 — G's population keys are `.relative_to(REPO).as_posix()`; non-vacuity names two
    ("tests/test_w3b_dialect.py", "backend/memory/lineage.py"): POSIX_BY_CONTRACT,
    ("tests/test_w3b_dialect.py", "backend/store/"): POSIX_BY_CONTRACT,                    # W3b part 2 — a prefix of the same keys (the interface's own SQL is the driver's)
    ("tests/test_w3b_parity.py", "backend/store/"): POSIX_BY_CONTRACT,                     # W3b close (F3) — C1's text-order keys are `.relative_to(REPO).as_posix()`; the same prefix T3 leaves out
    ("tests/test_w3b_parity.py", "backend/kb/tiered_store.py"): POSIX_BY_CONTRACT,         # W3b close (F3) — C1's non-vacuity names two of those keys
    ("tests/test_w3b_parity.py", "backend/memory/lineage.py"): POSIX_BY_CONTRACT,
    ("tests/test_w4_identity_record.py", "always <code>asserted</code>"): TEXT,             # W4 part 2 — an HTML fragment searched in the integration reference's text (R7)
    ("tests/test_w3b_selection.py", "postgresql://"): URL,                                 # W3b part 3 — the URL scheme start.py's lines are asserted to carry
    ("tests/test_w3b_selection.py", "database: postgres postgresql://"): TEXT,              # W3b part 3 — start.py's first line, searched in a child's output
    ("tests/test_w3b_selection.py", "  store       postgres postgresql://"): TEXT,          # W3b part 3 — start.py's store line, searched in a child's output
    ("tests/test_w3b_readonly.py", "postgresql://"): URL,                                  # W3b part 3b — the scheme the scripts' first line is asserted to carry
    ("tests/test_w3b_readonly.py", "postgres postgresql://"): TEXT,                        # W3b part 3b — `store_label`'s prefix, asserted
    ("tests/test_w3b_readonly.py", "database: postgres postgresql://"): TEXT,              # W3b part 3b — a script's first line, searched in a child's output
    ("tests/test_w3b_backup.py", "postgresql://"): URL,                                    # W3b part 5 — the scheme the backup's first line is asserted to carry
    ("tests/test_w3_deploy.py", "@postgres:5432/"): TEXT,                                  # W3b part 5 — the compose URL's host, searched in the service's environment
    ("tests/test_w3b_secrets.py", "postgresql://"): URL,                                   # W3b close (F6) — the scheme a redacted URL and a refusal are asserted to carry
    ("tests/test_w3b_secrets.py", "postgres postgresql://"): TEXT,                         # W3b close (F6) — `store_label`'s prefix in a script's first line, searched in a child's output
    ("tests/test_w3b_secrets.py", "database: postgres postgresql://"): TEXT,               # W3b close (F6) — start.py's first line, searched in a child's output
    ("tests/test_w3b_secrets.py", "database: postgres postgresql://f6:***@"): TEXT,        # W3b close (F6) — start.py's and backup.py's first lines, the password redacted
    ("tests/test_w3b_secrets.py", "scripts/backup.py"): POSIX_BY_CONTRACT,                 # W3b close (F6) — B1: the path backup.sh (a POSIX shell script, skipped without sh) hands python
    ("tests/test_w4_identity.py", "/whoami"): ROUTE,                                       # W4 part 1 — the twelfth gated read, in the derived gated set
    ("tests/test_w4_identity.py", "/vendor/"): ROUTE,                                      # W4 part 1 — the mount's prefix, in the targets derived from app.routes
    ("tests/test_w4_identity.py", "/planted/after-the-factory"): ROUTE,                    # W4 part 1 — I3's planted route, in the derived targets
    ("tests/test_w4_identity.py", "text/event-stream"): CONTENT_TYPE,                      # W4 part 1 — /chat's stream through the gate
    ("tests/test_w11d_conformance.py", "tests/fixtures/tb_golden/** -text"): TEXT,         # W11d — a line of .gitattributes (git's own pattern, posix on every platform), searched in the file's text
    ("tests/test_w11d_conformance.py", "tests/fixtures/w11d/** -text"): TEXT,              # W11d — the same, for the chain vector's folder
}
FILESYSTEM_SHAPED = {SOURCE_LITERAL, POSIX_BY_CONTRACT}


def _slash_constants(node) -> list[str]:
    out = []
    for c in node:
        if isinstance(c, ast.Constant) and isinstance(c.value, str) and "/" in c.value:
            out.append(c.value)
        elif isinstance(c, ast.Tuple):
            out += _slash_constants(c.elts)
    return out


def path_shaped_sites() -> dict[tuple[str, str], list[int]]:
    """{(file, constant): [lines]} over every tracked test file, by AST."""
    sites: dict[tuple[str, str], list[int]] = {}
    for p in tracked("tests"):
        if p.suffix != ".py":
            continue
        rel = p.relative_to(REPO).as_posix()
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            consts: list[str] = []
            if isinstance(n, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in n.ops):
                consts = _slash_constants([n.left, *n.comparators])
            elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr in ("startswith", "endswith")):
                consts = _slash_constants(n.args)
            for c in consts:
                sites.setdefault((rel, c), []).append(n.lineno)
    return sites


def test_every_path_shaped_site_is_classified():
    sites = path_shaped_sites()
    print(f"\nS7 platform {sys.platform} ({platform.system()}, sep {os.sep!r}): "
          f"{sum(len(v) for v in sites.values())} sites, {len({f for f, _ in sites})} files; "
          f"filesystem-shaped: {sorted(k for k in sites if CLASSIFIED.get(k) in FILESYSTEM_SHAPED)}")
    assert sites, "non-vacuity: the derivation found nothing"
    unclassified = sorted(k for k in sites if k not in CLASSIFIED)
    assert not unclassified, f"path-shaped sites with no classification: {unclassified}"
    stale = sorted(k for k in CLASSIFIED if k not in sites)
    assert not stale, f"classified sites that no longer exist: {stale}"
    assert not [k for k in sites if k[0] == "tests/_fz_trace.py"], "the trace matches a path again"


# ---------------------------------------------------------------------------
# companions — each filesystem-shaped site, run against Windows-style paths
# ---------------------------------------------------------------------------

def test_writers_source_literal_companion():
    """`_writers._data_literal` reads the posix form `data/...` of a literal in backend
    SOURCE. A Windows-style literal is not recognised — declared here — and the reason that
    is safe is a derived guard: no tracked backend file writes one, so the helper's
    assumption is held, not assumed."""
    from tests._writers import _data_literal
    assert _data_literal(ast.Constant("data/kb.db")) == "data/kb.db"
    assert _data_literal(ast.Constant("data\\kb.db")) is None       # the gap, declared
    offenders = []
    for p in tracked_backend():
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and re.search(r"data\\", n.value):
                offenders.append((p.relative_to(REPO).as_posix(), n.lineno, n.value))
    assert not offenders, f"backend source carries a backslash data/ literal the derivation cannot see: {offenders}"


def test_curiosity_guard_key_companion():
    """tests/test_ax_curiosity_placement.py keys its sites `path.relative_to(ROOT).as_posix()`.
    The same expression over a Windows path yields the posix key the assertions compare
    against — the contract, replayed."""
    root = PureWindowsPath(r"C:\Users\op\Nexus")
    key = PureWindowsPath(r"C:\Users\op\Nexus\backend\loop\agentic_loop.py").relative_to(root).as_posix()
    assert key == "backend/loop/agentic_loop.py" and key.startswith("backend/loop/")
    assert PureWindowsPath(r"C:\Users\op\Nexus\backend\server.py").relative_to(root).as_posix() == "backend/server.py"


def test_the_other_companions_exist():
    """The trace's companion (T11) and the import-purity probe's own posix companion are
    tests in the suite, named here so the classification above points at something real."""
    from tests import test_fz_freeze, test_import_purity
    assert callable(test_fz_freeze.test_frame_named_by_module_not_path)
    assert callable(test_import_purity.test_probe_keys_are_posix_on_every_platform)
