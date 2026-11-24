"""W11b (run/specs/W11b.md D1 to D3; RUN_WORKLIST W11b step 2): the frozen identifiers, as a ratchet.

Talima names the standard and its verifier; NEXUS stays the engine's name, and every name the engine's formats,
stores and clients read is frozen: a change is a new format version, with the old version still verified.

WHAT IS HELD HERE
    T1 Every `str` and `bytes` constant in the tracked backend and in every tracked `scripts/*.py` (the `_*.py`
       helpers too) that contains `nexus` in any case, outside docstrings (comments are not in the AST), and every
       string in the bundle's manifest, is a key of FROZEN_IDENTIFIERS, each with a class and a reason; every key is
       found; and each file holds the frozen literals FROZEN_SITES lists for it, in the same source order. A `bytes`
       constant is keyed by its `repr`; a literal over 100 characters or with a line break by the first 16 hex
       digits of its sha256. So a literal added, removed or respelled fails, and so does a rename at one site while
       the spelling survives at another, or into a spelling with no `nexus` in it (by the count it moved), or two
       literals swapped between sites in one file (by the order), naming the literal and its file, until the set and
       the sites are edited with a reason. The population is derived; only the answers are listed.
    T2 (companion a) A copy with `nexus.credential` respelled in the producer and in the verifier: the verdict on
       the copy fails, naming both spellings, and the copy's verifier refuses step 1's credential (exit 1, NOT
       VERIFIED), while the verifier as it is verifies it: renaming a wire identifier breaks every credential
       already issued.
    T2b The kind respelled `talima.credential` in the verifier alone, and T2c the signed statement prefix
       `b"NEXUS-BLOCK-V1\\x00"` respelled there alone: each passes a verdict keyed by value (asserted: the plant is
       the class that check misses), fails this one by the site it moved, and the copy's verifier refuses step 1's
       credential.
    T2d The Windows data folder `NEXUS` and the Linux `nexus` swapped in `backend/mcp/config.py`: no literal added
       or removed and no count moved (asserted), the verdict fails by the order, and the Linux default folder
       becomes `NEXUS`, another folder on a case-sensitive file system: every existing Linux store is orphaned.
    T5 An f-string's pieces read in one order on every Python (3.11, 3.12, 3.13), pinned on a sample.
    T3 (companion b) A scratch module holding a literal containing `Nexus` that the set does not name: the verdict
       fails, naming it and its file.
    T4 (companion c) One entry removed from the set: the verdict fails, naming the entry.

WHAT IS NOT HELD HERE (said where the ratchet is described, `docs/TALIMA.md`)
    - Docstrings, though FastMCP sends the tools' docstrings as their descriptions.
    - Python identifiers: function, keyword and field names. The MCP tools are registered by their functions' names
      (`backend/mcp/mcp_server.py`); `tests/test_w2_bundle.py` holds the served tools equal to the bundle manifest's,
      whose strings this pass holds. Settings passed as keyword names (`scripts/uat_simulate.py`,
      `scripts/bundle_smoke.py`) and the checkpoint's field `nexus_blob` are not held.
    - Names a program builds at run time (`NEXUS_<KEY>_OWNER`).
    - Files that are not Python, inside the scripts or outside (`scripts/*.sh`, `*.ps1`, `*.js`, `.env.example`,
      `docker-compose.yml`, `deploy/`, the workflows), and Python outside the backend and the scripts (`start.py`,
      and the golden fixtures' generator kept with the run's records): each names settings this pass does not read.
      Every `NEXUS_*` setting in the project's settings block (the one `documented_settings()` in
      `tests/test_dotenv_settings_take_effect.py` reads) is also a held literal, and that test holds the block both
      ways to its probes. A setting named only in those files is outside the block and has no probe: it is held
      only where another test reads it (`tests/test_w4_hosted.py` reads `NEXUS_PROFILE`), or by nothing.
    - The order is kept among the held literals only: a held literal exchanged with a literal that names no nexus,
      or moved within its file without passing another held literal, is not seen (the two arguments of
      `os.environ.get("NEXUS_CONTRADICTION_PAIRS", "")` exchanged pass; that setting's probe is what fails).

LAW 16. A new guard over unchanged code: its witnesses are T2 to T4, each failing the verdict by what it plants, T2
to T2d also by their consequence (the copy's verifier refusing the base's credential; the Linux store's folder
moving); T2b and T2c were seen failing against the value-keyed verdict this file first held, and T2d against the
count-keyed one that followed it. At the base the set differs by what W11b added or changed (the verifier's two
result-line prefixes and its module name, and `talima_verify.py`'s own literals), which is the ratchet working.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

from tests import _proc
from tests._tracked import REPO, tracked, tracked_backend

MANIFEST = "backend/mcp/bundle/manifest.json"
CLASSES = ("wire", "schema", "contract", "engine_name", "cosmetic")
GOLDEN = REPO / "tests" / "fixtures" / "tb_golden"

REASONS = {
    'verifier': ('contract',
            "the verifier's name, its command and its file: a release asset, the documents and a stranger's command "
            'name it; W11b adds talima-verify beside it, never in its place'),
    'kind': ('wire',
            'a credential or receipt kind: signed into it, and checked by every verifier'),
    'block': ('wire',
            'the block tag: in the request bytes, so in every request_hash, and refused by the gate by '
            'name'),
    'prefix': ('wire',
            'the stable system prefix: in every prefix hash and every replay'),
    'capture': ('wire',
            'a replay capture key, inside the stored captures W0b replays'),
    'store_name': ('schema',
            'a persisted store name: two versions must read one store'),
    'lock': ('schema',
            "the single-writer lock's key: two versions must agree on the lock"),
    'collection': ('schema',
            "a persisted vector collection's name"),
    'blob': ('schema',
            'the checkpoint blob tag, persisted'),
    'setting': ('contract',
            "a setting an operator's .env names; no TALIMA_* alias is added"),
    'tool': ('contract',
            'an MCP tool, prompt or route name a client calls'),
    'scheme': ('contract',
            'the MCP resource scheme a client reads'),
    'event': ('contract',
            'the MCP event a client reads'),
    'entry': ('contract',
            "the bundle's entry point or server name, which a client launches"),
    'logger': ('contract',
            "a logger name an operator's logging configuration reads"),
    'manifest': ('contract',
            'the bundle manifest a desktop client reads'),
    'install': ('contract',
            "an install path or file name an operator's host or the console reads"),
    'class': ('engine_name',
            "an engine class's name in code"),
    'message': ('engine_name',
            'a message or sentence naming the engine, which stays NEXUS'),
    'contact': ('cosmetic',
            "the seeder's contact URL (W11b step 6): the public repository's since the operator's ruling of 24 "
            'November on W11c-B1, because Wikidata wants a contact a stranger can reach'),
    'nexus_bare': ('wire',
            "the RFC 9421 signature label in every signed request; also the MCP server's name, the "
            "Postgres application name, the Linux data folder's name and the compose project's name"),
    'nexus_upper': ('schema',
            "the default data folder's name on Windows and macOS (renaming it orphans every existing store); also "
            "the bundle manifest's author name, the console API's title (served in /openapi.json) and the `/` "
            "route's status"),
    'commit_key': ('wire',
            "a replay capture key inside the stored captures W0b replays; also an MCP tool's name"),
    'sql': ('schema',
            "the store's SQL, which the database itself holds (DDL, function bodies, the lock's "
            'statement)'),
    'scratch': ('schema',
            "the restore check's scratch-database prefix, which its cleanup finds on the server"),
    'temp': ('engine_name',
            "a temporary folder's or file's prefix in a script"),
    'pinned': ('contract',
            'a sentence a test pins verbatim: a client or an operator reads it, and it stays true'),
    'mcp_surface': ('contract',
            "text an MCP client reads (the server's instructions and the tools' replies)"),
    'statement': ('wire',
            'the statement prefix every block signature signs, so every credential and receipt'),
    'label': ('wire',
            "the RFC 9421 signature label as a request's Signature header carries it"),
    'sealed_note': ('wire',
            "a note sealed into every MCP record's capture_notes, so into its record_hash"),
    'tester_names': ('schema',
            'the prefix of the Postgres tester databases and roles deploy/postgres-init.sh creates'),
    'service': ('contract',
            "a compose service's name (or the prefix of one, or of the bundle's asset name): an operator's "
            'commands and the deploy name it'),
    'namespace': ('contract',
            "the settings namespace a name is built in at run time (NEXUS_<KEY>_OWNER, _PASSWORD), which an "
            "operator's .env names"),
}

FROZEN_IDENTIFIERS = {
    # contract: entry
    'nexus-mcp': 'entry',
    # contract: event
    'nexus.audit_started': 'event',
    # contract: verifier
    'nexus-verify': 'verifier',
    'nexus_verify.py': 'verifier',
    'nexus_verify': 'verifier',
    # contract: install
    '/opt/nexus': 'install',
    '<head><script>window.NEXUS_TOKEN=': 'install',
    'nexus_memory.html': 'install',
    'nexus_ui.html': 'install',
    # contract: logger
    'nexus.calibration': 'logger',
    'nexus.capture': 'logger',
    'nexus.contradiction': 'logger',
    'nexus.cortex': 'logger',
    'nexus.embeddings': 'logger',
    'nexus.env': 'logger',
    'nexus.forecast': 'logger',
    'nexus.graph_store': 'logger',
    'nexus.hebbian': 'logger',
    'nexus.identity': 'logger',
    'nexus.kairos': 'logger',
    'nexus.kb.citation': 'logger',
    'nexus.kb.schema': 'logger',
    'nexus.kb.store': 'logger',
    'nexus.kb.trails': 'logger',
    'nexus.loop': 'logger',
    'nexus.mcp': 'logger',
    'nexus.neocortex': 'logger',
    'nexus.recalibration': 'logger',
    'nexus.retrieval': 'logger',
    'nexus.semantic': 'logger',
    'nexus.server': 'logger',
    'nexus.tools.search': 'logger',
    'nexus.vocabulary': 'logger',
    # contract: manifest
    '${__dirname}/server/nexus-mcp/nexus-mcp': 'manifest',
    '${__dirname}/server/nexus-mcp/nexus-mcp.exe': 'manifest',
    'NEXUS (learning frozen)': 'manifest',
    'Records an action you report for a turn, as client_reported: NEXUS did not see it happen.': 'manifest',
    'server/nexus-mcp/nexus-mcp': 'manifest',
    'sha256:39bac342fc22a465': 'manifest',    # NEXUS keeps a store on this computer and makes no network calls. Befor
    'sha256:66dbf103ba512831': 'manifest',    # Where NEXUS keeps its store and logs. Leave it empty for the per-user 
    'sha256:6af61cd903e6e06c': 'manifest',    # NEXUS memory for Claude: retrieval from a store on this computer, and 
    'sha256:874c1d4a38dfab2e': 'manifest',    # Call before answering: the context block NEXUS would inject for the qu
    # contract: mcp_surface
    " is set on an mcp_block record (the client's model ran outside NEXUS)": 'mcp_surface',
    '; the retrieval stays open, so nexus_record may be called again with the same retrieval_id': 'mcp_surface',
    "NULL — the client's model ran outside NEXUS; the reason is in capture_notes (W0b)": 'mcp_surface',
    'failed: record_not_written — NEXUS could not write the record of this turn (': 'mcp_surface',
    'nexus_assert_confirm(sentence, confirmed=true) after the user confirms': 'mcp_surface',
    "prompt_prefix_hash is not sha256('') on an mcp_block record (NEXUS sent no prompt)": 'mcp_surface',
    'refused: unknown_lineage_id — report an action against the lineage_id nexus_record returned': 'mcp_surface',
    "sha256 of the answer text as the client sent it — the client's claim, not NEXUS's observation": 'mcp_surface',
    'sha256 of the empty string: NEXUS sent no prompt, only the block it returned': 'mcp_surface',
    'sha256:2da1f9e0f70fd22c': 'mcp_surface',    # complete for every turn the client recorded; retrievals without a reco
    'sha256:2ff08f3ae09c1824': 'mcp_surface',    # over NEXUS's block only (equal to context.hash); never the client's pr
    'sha256:36a6ba5f02eeb975': 'mcp_surface',    # the client reported this action and NEXUS did not see it happen; once 
    'sha256:397eb5ac13bf3339': 'mcp_surface',    # a turn split at the model call across a client's model: NEXUS returned
    'sha256:53db4ee572f25d93': 'mcp_surface',    # NEXUS is a local, persistent memory with a sealed record. Use it in tw
    'sha256:6bcee8b234da5c2e': 'mcp_surface',    # actions are recorded only as a client reports them (nexus_record_actio
    'sha256:d8745aca39ab6ca8': 'mcp_surface',    # the client's model as the client named it (`client-reported:<name>`), 
    'sha256:d91757b18def8204': 'mcp_surface',    # keep the salt: it is never shown again; once a seal covers the commitm
    'sha256:dccca613ba09195e': 'mcp_surface',    # the coverage counter could not be written either (the same store refus
    'sha256:f5287373bbc2257c': 'mcp_surface',    # no HTTP server runs in this process, no bearer token exists, and NEXUS
    "the console's full turn: NEXUS ran the model and every field is NEXUS's own observation": 'mcp_surface',
    # contract: pinned
    ' is not a store backend NEXUS knows (': 'pinned',
    ' is not an auth mode NEXUS knows (': 'pinned',
    'NEXUS_SIGN_REQUESTS=on': 'pinned',
    'nexus-verify: VERIFIED': 'pinned',
    'no `triples` table — is this a NEXUS kb.db?': 'pinned',
    'seed off (NEXUS_INFERENCE_SEED=off)': 'pinned',
    'this store is open in another NEXUS process (pid ': 'pinned',
    # contract: scheme
    'nexus://lineage/': 'scheme',
    'nexus://lineage/{lineage_id}': 'scheme',
    # contract: setting
    'NEXUS_ANCHOR': 'setting',
    'NEXUS_ANCHOR_URL': 'setting',
    'NEXUS_ANSWER_RELEASE': 'setting',
    'NEXUS_API_BACKEND': 'setting',
    'NEXUS_API_TOKEN': 'setting',
    'NEXUS_ARCHIVAL_LEDGER': 'setting',
    'NEXUS_ARRIVAL_': 'setting',
    'NEXUS_AUDIT_NETWORK': 'setting',
    'NEXUS_AUTH_MODE': 'setting',
    'NEXUS_BACKUP_DATABASE_URL': 'setting',
    'NEXUS_BACKUP_DIR': 'setting',
    'NEXUS_BACKUP_PASSWORD': 'setting',
    'NEXUS_BACKUP_ROLE': 'setting',
    'NEXUS_CALIBRATION_LOG': 'setting',
    'NEXUS_CITATION_CAPTURE': 'setting',
    'NEXUS_CITATION_CAPTURE_PATH': 'setting',
    'NEXUS_CITATION_FORECAST': 'setting',
    'NEXUS_CITATION_RULES': 'setting',
    'NEXUS_CONSOLIDATION_BATCH': 'setting',
    'NEXUS_CONTRADICTION_PAIRS': 'setting',
    'NEXUS_CURIOSITY_PLACEMENT': 'setting',
    'NEXUS_DATABASE_URL': 'setting',
    'NEXUS_DATA_ROOT': 'setting',
    'NEXUS_DECAY_': 'setting',
    'NEXUS_DOMAIN': 'setting',
    'NEXUS_EMBED_API_KEY': 'setting',
    'NEXUS_EMBED_MODEL': 'setting',
    'NEXUS_EMBED_URL': 'setting',
    'NEXUS_ENV_ROOT': 'setting',
    'NEXUS_EXTRACTION_MIN_CONFIDENCE': 'setting',
    'NEXUS_EXTRACTION_VOCABULARY': 'setting',
    'NEXUS_FIRE_CAPTURE': 'setting',
    'NEXUS_FIRE_CAPTURE_PATH': 'setting',
    'NEXUS_GOVERNANCE_SEAL': 'setting',
    'NEXUS_GOVERNANCE_SEAL_INTERVAL_MINUTES': 'setting',
    'NEXUS_HEBBIAN_PAIRING': 'setting',
    'NEXUS_HEBBIAN_TOPK': 'setting',
    'NEXUS_HISTORY_WRAP': 'setting',
    'NEXUS_HOST': 'setting',
    'NEXUS_HTTPS_PORT': 'setting',
    'NEXUS_IDENTITY_HEADER': 'setting',
    'NEXUS_INFERENCE_SEED': 'setting',
    'NEXUS_INGEST_GATE': 'setting',
    # W10c — the interceptor's five settings (docs/CONTRACTS.md, docs/INTERCEPTOR.md).
    'NEXUS_INTERCEPT_DATA_ROOT': 'setting',
    'NEXUS_INTERCEPT_INJECT': 'setting',
    'NEXUS_INTERCEPT_OWNER': 'setting',
    'NEXUS_INTERCEPT_PROXY_HEADERS': 'setting',
    'NEXUS_INTERCEPT_UPSTREAM': 'setting',
    'NEXUS_KEEP_REQUESTS': 'setting',
    'NEXUS_LEARNING': 'setting',
    'NEXUS_LEARNING_FROZEN_SINCE': 'setting',
    'NEXUS_MCP_DATA_ROOT': 'setting',
    'NEXUS_MCP_HANDLE_TIMEOUT': 'setting',
    'NEXUS_MCP_OWNER': 'setting',
    'NEXUS_OWNER_IDENTITY': 'setting',
    'NEXUS_PORT': 'setting',
    'NEXUS_PROBE_CONNECT_TIMEOUT': 'setting',
    'NEXUS_PROMPT_RENDER': 'setting',
    'NEXUS_RECALIBRATION': 'setting',
    'NEXUS_RECURRENCE_THRESHOLD': 'setting',
    'NEXUS_REFERENCE_GATEWAY': 'setting',
    'NEXUS_REFRACTORY_HOURS': 'setting',
    'NEXUS_REFUSAL_LEDGER': 'setting',
    'NEXUS_REINFORCE_MODE': 'setting',
    'NEXUS_RELATION_TRAILS': 'setting',
    'NEXUS_RELATION_VOCABULARY': 'setting',
    'NEXUS_REPLY_SEPARATION': 'setting',
    'NEXUS_SEMANTIC_INDEX_BATCH': 'setting',
    'NEXUS_SEMANTIC_INDEX_EXCLUDE': 'setting',
    'NEXUS_SEMANTIC_MIN_SIMILARITY': 'setting',
    'NEXUS_SEMANTIC_SLOTS': 'setting',
    'NEXUS_SIGN_REQUESTS': 'setting',
    'NEXUS_SNAPSHOT_INTERVAL_DAYS': 'setting',
    'NEXUS_STARTUP_PROBE': 'setting',
    'NEXUS_STORE': 'setting',
    'NEXUS_SUBSTRATE_CLOCK_REFRESH': 'setting',
    'NEXUS_SUBSTRATE_WEIGHT_RESTORE': 'setting',
    'NEXUS_TESTERS': 'setting',
    'NEXUS_TRAIL_SLOTS': 'setting',
    'env_NEXUS_RECURRENCE_THRESHOLD': 'setting',
    # contract: tool
    'mcp:nexus_assert_confirm': 'tool',
    'mcp:nexus_commit': 'tool',
    'mcp:nexus_record_action': 'tool',
    'nexus_asof': 'tool',
    'nexus_assert': 'tool',
    'nexus_assert_confirm': 'tool',
    'nexus_recall': 'tool',
    'nexus_record': 'tool',
    'nexus_record_action': 'tool',
    'nexus_retrieve': 'tool',
    'nexus_status': 'tool',
    # cosmetic: contact
    'sha256:d4c511d99e48a7cf': 'contact',    # NEXUS-Cognitive-Substrate/1.0 (https://github.com/Slickfish/talima; op
    # engine_name: class
    'NexusArchiveChain | None': 'class',
    'NexusLoopState': 'class',
    'NexusRuntime': 'class',
    'NexusServices': 'class',
    # engine_name: message
    'sha256:74e1b29a53026e9e': 'message',    # W10c: the simulated UAT's interceptor section
    'sha256:7c5890a07d647340': 'message',    # W10c: the simulated UAT's interceptor section
    'the record, the tool use and the exchange, verified by nexus-verify with no store access': 'message',    # W10c: the simulated UAT's interceptor section
    'sha256:d27f90468b0337cf': 'message',    # W10c: the injection link refuses the console's Postgres store
    ' (NEXUS_': 'message',
    ' (NEXUS_SEMANTIC_SLOTS; the quota this is about)': 'message',
    ' (the password: NEXUS_': 'message',
    ' NEXUS action receipt': 'message',
    ' NEXUS commitment receipt': 'message',
    ' NEXUS credential': 'message',
    ' has no lineage_records table: not a NEXUS store': 'message',
    ' holds no triples or lineage_records table: not a NEXUS store': 'message',
    ' is not a NEXUS store (': 'message',
    ' is not a NEXUS store (no triples or lineage_records table)': 'message',
    ' is not a backend NEXUS knows (': 'message',
    ' settings set; NEXUS_LEARNING=': 'message',
    '# NEXUS simulated UAT: ': 'message',
    "'s NEXUS serves in proxy mode": 'message',
    '); set it in .env or the environment. NEXUS names no model for you.': 'message',
    ', NEXUS_HOST=': 'message',
    '.nexus-export-': 'message',
    '1. capture gate NEXUS_FIRE_CAPTURE in .env: ': 'message',
    '5. AO (S1.5): NEXUS_CITATION_CAPTURE in .env: ': 'message',
    '; NEXUS_STORE=postgres, NEXUS_DATABASE_URL per tester': 'message',
    '; the client reported it: NEXUS did not see it happen': 'message',
    '<h1>nexus_memory.html not found</h1>': 'message',
    'Callable[[NexusLoopState, Hippocampus | None], str] | None': 'message',
    'GET /identity/signed-action?signature=… (no token); nexus-verify --action': 'message',
    'NEXUS MCP: ': 'message',
    'NEXUS MCP: %s=%r is an unexpanded placeholder, not an identity: records are unattributed': 'message',
    'NEXUS MCP: data folder %s (%s)': 'message',
    'NEXUS MCP: env files %s; store %s; owner %s; learning %s (%s); contract v%s': 'message',
    'NEXUS/0.10 (+local synthetic cortex; SearXNG client)': 'message',
    'NEXUS: ': 'message',
    'NEXUS_': 'namespace',
    'NEXUS_ARRIVAL_%s=%r is not a number; using default': 'message',
    'NEXUS_DECAY_%s=%r is not a number; using default': 'message',
    'NEXUS_GOVERNANCE_SEAL=%r is not one of %s; using %r': 'message',
    'NEXUS_GOVERNANCE_SEAL_INTERVAL_MINUTES=%r is not a number — falling back to %s': 'message',
    'NEXUS_HEBBIAN_TOPK=%r is not an integer — using %s': 'message',
    'NEXUS_HOST=': 'message',
    'NEXUS_LEARNING=frozen as .env.example ships it': 'message',
    'NEXUS_RECALIBRATION=off — no weight may move': 'message',
    'NEXUS_RECURRENCE_THRESHOLD=%r is not an integer — falling back to %s': 'message',
    'NEXUS_REFRACTORY_HOURS=%r is not a number — falling back to %s h': 'message',
    'NEXUS_REINFORCE_MODE=%r is not one of %s; using %r': 'message',
    'NEXUS_SEMANTIC_MIN_SIMILARITY=%r is not a number; using %.2f': 'message',
    'NEXUS_SEMANTIC_SLOTS=%r is not an integer; using %d': 'message',
    'NEXUS_SNAPSHOT_INTERVAL_DAYS=%r is not a number — falling back to %s': 'message',
    'NEXUS_STARTUP_PROBE=%r is not one of %s; using %r': 'message',
    'NEXUS_TRAIL_SLOTS=%r is not an integer — using 0': 'message',
    'REFUSED: no token (--token or NEXUS_API_TOKEN); the gated checks need it': 'message',
    'REFUSED: the source is not a NEXUS store (no triples or lineage_records table)': 'message',
    'SELECT holder FROM nexus_store_lock ORDER BY lock_key LIMIT 1': 'sql',
    'SELECT holder FROM nexus_store_lock WHERE lock_key = ?': 'sql',
    "This is the fixed answer of NEXUS's stub provider (LLM_BACKEND=stub), asked for model ": 'message',
    'UPDATE nexus_store_lock SET holder = ? WHERE lock_key = ?': 'sql',
    "a credential for the console's first turn, verified by nexus-verify with no store access": 'message',
    'action_not_recorded: NEXUS_SIGN_REQUESTS=on and the ': 'message',
    'defaults to NEXUS_CALIBRATION_LOG or data/calibration_log.jsonl': 'message',
    'mcp: nexus_status, coverage': 'message',
    "mcp_block: the client's model ran outside NEXUS": 'message',
    'nexus-': 'service',
    'nexus-api': 'service',
    'nexus-bundle-smoke': 'message',
    'nexus-bundle-smoke-': 'message',
    'nexus-cycle-cost-': 'message',
    'nexus-migrate-': 'message',
    'nexus-ollama': 'service',
    'nexus-stub': 'service',
    'nexus-t*': 'service',
    'nexus-trails-': 'message',
    'nexus-uat-': 'message',
    'nexus-verify (': 'message',
    'nexus-verify: ': 'verifier',
    'nexus=:': 'label',
    'nexus_': 'tester_names',
    'nexus_asof at a bare date, refused by name': 'message',
    'nexus_asof at an instant with an offset': 'message',
    'nexus_assert: the offer, which stores nothing': 'message',
    'nexus_assert_confirm confirmed': 'message',
    'nexus_assert_confirm unconfirmed': 'message',
    'nexus_recall on the confirmed claim': 'message',
    'nexus_record: the record sealed for the answer': 'message',
    'nexus_record_action: an action the client reports': 'message',
    'nexus_retrieve: the block to answer from': 'message',
    'nexus_status: the coverage counters': 'message',
    'nexus_v31_fixture_': 'message',
    'not observable from NEXUS (a daemon-side setting)': 'message',
    'python -I nexus_verify.py credential.json --identity identity.json': 'message',
    'python start.py --serve (NEXUS_DATA_ROOT=': 'message',
    'refused: a process with no owner (NEXUS_OWNER_IDENTITY unset)': 'message',
    'sh deploy/postgres-init.sh (NEXUS_TESTERS=': 'message',
    'sha256:0801c63a1f9c7788': 'message',    # NEXUS keeps the exact text it sent to the model so a decision can be r
    'sha256:0c43b4c2953fb780': 'message',    # , where NEXUS serves. Stop NEXUS first: requests it sends while this r
    'sha256:0f9ca92f273bcbcf': 'message',    # NEXUS_LEARNING=live NEXUS_MCP_OWNER=intruder
    'sha256:1133b3f83a4181e9': 'message',    # python-dotenv is not installed, so .env is not loaded. Set NEXUS_API_T
    'sha256:2181ca8f01bf1918': 'message',    # This token is per-PROCESS. Under `uvicorn --reload` it is regenerated 
    'sha256:24363c54899af996': 'message',    # a substrate row's stored weight is the record layer's high-water mark;
    'sha256:357715ce8cc69de6': 'message',    # has no embedder: Anthropic offers no embedding model, and NEXUS_EMBED_
    'sha256:362d8a009d100527': 'message',    # per-person rows: each act carries the actor (id, role, source) and the
    'sha256:37376fca1d596f4e': 'message',    # each scripted value on its line, every other line as .env.example ship
    'sha256:468ea51acaafd1d1': 'message',    # user_config.data_folder has a default: one host expands it and another
    'sha256:4afc5284a37185b5': 'message',    # a refused row written after its rule shipped entered through a lenient
    'sha256:5f7fbf5d867715e4': 'message',    # a per-person record: the action names the identity accountable for it,
    'sha256:68cd72c1d381fbb2': 'message',    # NEXUS_RELATION_VOCABULARY=%s yielded no usable relations (%d malformed
    'sha256:6d417ddc1a992682': 'message',    # python -I nexus_verify.py --reveal receipt.json --content note.txt --s
    'sha256:734d4774198d2b15': 'message',    # direction sets the sign; magnitude is bounded by the active envelope. 
    'sha256:7727edd214beb5d3': 'sealed_note',    # not observable: the client's model ran outside NEXUS (its seed, weight
    'sha256:7c587dc4f3bec4bf': 'message',    # POST /kairos/cycle; GET /actions/{id}; python -I nexus_verify.py --act
    'sha256:7d102a08454be766': 'message',    # is not on PATH: the hosted store's backup needs the PostgreSQL client 
    'sha256:7fb69c66c4853807': 'message',    # proxy mode: this process took the login from the identity header of a 
    'sha256:7fecbc03da30076a': 'message',    # per-person rows: governed acts are attributable to their actor by cons
    'sha256:84e4094b65bcdd83': 'message',    # owner not set: NEXUS_OWNER_IDENTITY is unset, so this process answers 
    'sha256:8f5ce54f70218fa1': 'message',    # PyInstaller's run-time hook for it (pyi_rth_pkgres) subclasses pkg_res
    'sha256:8f60a8024a23db15': 'message',    # import json, sys def emit(out): print(json.dumps(out), flush=True) sys
    'sha256:9a1901e5e17d0ca2': 'message',    # names no embedder: NEXUS_EMBED_URL and NEXUS_EMBED_MODEL are unset (an
    'sha256:9f95fcac1baec968': 'message',    # NEXUS never deletes a store: the data roots below were kept by every s
    'sha256:a23dc5a04b55014e': 'message',    # NEXUS_CONTRADICTION_PAIRS=%s yielded no usable pairs (%d malformed); f
    'sha256:a52e45e1a3812d76': 'message',    # NEXUS_CONTRADICTION_PAIRS=%s could not be read as JSON (%s: %s); falli
    'sha256:a7c4925052cb9166': 'message',    # the hosted store (NEXUS_STORE=postgres): a postgresql:// URL for a rol
    'sha256:a88fad8aa706ddbd': 'message',    # taken from the identity header of a loopback peer by a process that an
    'sha256:af4982e6c695c0ae': 'message',    # live rows; a query reaching either spelling can now admit same-subject
    'sha256:b30610077fe0c2cb': 'message',    # NEXUS_RELATION_VOCABULARY=%s could not be read as JSON (%s: %s); falli
    'sha256:c18619bd958a6b20': 'setting',    # # verify_deploy's relay probe (V14). import json, os, urllib.error, ur
    'sha256:c523ecb253cad68e': 'message',    # SKIP V8 egress: not applicable: the api profile's NEXUS container has 
    'sha256:c918b1e308f82480': 'message',    # ================================================================ NEXUS
    'sha256:cd3093e54ca0298c': 'message',    # # verify_deploy's other-database probe (V10). import json, sys CONFIG 
    'sha256:d561406f5ba922ec': 'message',    # the tombstoned record's credential, issued in the other session, verif
    'sha256:e371c3fe645c8267': 'message',    # REFUSED: this is a hosted store's backup (its manifest names postgres)
    'sha256:f0f005812f7682d9': 'message',    # per-person rows: each record names the identity that was answerable fo
    'sha256:f9ccd2636f95bf3d': 'message',    # NEXUS resolves lineage_id and belief_ids. There is no claim registry (
    'sha256:fbb82ffdd757b563': 'message',    # NEXUS: proxy mode with no owner (NEXUS_OWNER_IDENTITY is unset): every
    'sha256:febb4182ea30479d': 'pinned',    # Transaction time only: when NEXUS admitted, archived or superseded a c
    'stored client_reported, completed: NEXUS did not see it happen, and says so': 'message',
    "tester 1's NEXUS did not serve": 'message',
    'the AO capture file; default NEXUS_CITATION_CAPTURE_PATH, else data/citation_capture.jsonl': 'message',
    "the action's receipt, verified by nexus-verify with no store access": 'message',
    'the console through Caddy (default https://<NEXUS_DOMAIN or localhost>[:NEXUS_HTTPS_PORT])': 'message',
    'the console token (default NEXUS_API_TOKEN)': 'message',
    'the embedder named by NEXUS_EMBED_URL and NEXUS_EMBED_MODEL (': 'message',
    'the fire capture file; default NEXUS_FIRE_CAPTURE_PATH, else data/fire_capture.jsonl': 'message',
    'the late nexus_record refused by name (unknown_retrieval_id)': 'message',
    "the operator's asserted date (YYYY-MM-DD); default from NEXUS_LEARNING_FROZEN_SINCE, else 2026-10-01": 'message',
    'the record read back: resources/read nexus://lineage/{id}': 'message',
    'the source Postgres URL, as NEXUS_DATABASE_URL; opened read-only': 'message',
    'the target Postgres URL (postgresql://...), as NEXUS_DATABASE_URL': 'message',
    'the target already holds NEXUS rows (': 'message',
    'uninstall: the data roots kept (NEXUS never deletes a store)': 'message',
    'verification (decision credentials, nexus-verify, commit now and reveal later)': 'message',
    'verification: credentials, nexus-verify, commit now and reveal later': 'message',
    'where the backup service writes on the host (default NEXUS_BACKUP_DIR or ./backups)': 'message',
    # engine_name: temp
    'nexus_audit_': 'temp',
    'nexus_backup_': 'temp',
    'nexus_cite_': 'temp',
    'nexus_history_': 'temp',
    'nexus_local_ragas': 'temp',
    'nexus_measure_': 'temp',
    'nexus_pairing_': 'temp',
    'nexus_preddist_': 'temp',
    'nexus_predsim_': 'temp',
    'nexus_probe_': 'temp',
    'nexus_render_': 'temp',
    'nexus_replay_': 'temp',
    'nexus_verify_': 'temp',
    'nexus_vocabeffect_': 'temp',
    'nexus_who_': 'temp',
    # schema: blob
    'nexus_blob': 'blob',
    # schema: collection
    'nexus_coarse': 'collection',
    'nexus_full': 'collection',
    # schema: lock
    'nexus-store:': 'lock',
    'nexus_store_lock': 'lock',
    'nexus_store_lock_log': 'lock',
    # schema: nexus_upper
    'NEXUS': 'nexus_upper',
    # schema: scratch
    'nexus_restore_': 'scratch',
    # schema: sql
    'INSERT INTO nexus_store_lock_log (at, event) VALUES (?, ?)': 'sql',
    'sha256:0ae9dd6911cd9667': 'sql',    # INSERT INTO nexus_store_lock (lock_key, holder) VALUES (?, ?) ON CONFL
    'sha256:362c276032bf88bb': 'sql',    # (t text) RETURNS text LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $
    'sha256:524d8272a921838f': 'sql',    # .seq, EXCLUDED.seq); END IF; RETURN NEW; END $nexus$; CREATE OR REPLAC
    'sha256:8149c238f49bfc45': 'sql',    # () RETURNS trigger LANGUAGE plpgsql AS $nexus$ DECLARE mx bigint; nxt 
    'sha256:942a42ceff557e38': 'sql',    # () RETURNS trigger LANGUAGE plpgsql AS $nexus$ BEGIN IF NEW.rowid IS N
    'sha256:b77c336c22611ec9': 'sql',    # ; END IF; RETURN NEW; END $nexus$; CREATE OR REPLACE TRIGGER
    'sha256:bc419cbd8f8c9572': 'sql',    # CREATE TABLE IF NOT EXISTS lineage_records ( lineage_id TEXT PRIMARY K
    # schema: store_name
    'nexus_autoinc__': 'store_name',
    'nexus_lower': 'store_name',
    'nexus_rowid__': 'store_name',
    'nexus_sequence': 'store_name',
    # wire: block
    '</nexus-block>': 'block',
    '<nexus-block source="compaction">': 'block',
    '<nexus-block source="prior_answer"': 'block',
    '<nexus-block source="prior_answer" turn={turn} truncated={truncated}>': 'block',
    # W10c — the optional injection link's block (NEXUS_INTERCEPT_INJECT=on): in the bytes forwarded.
    '<nexus-block source="retrieval">': 'block',
    'sha256:813716cfb65de467': 'block',    # <nexus-block source="prior_answer" turn=(\d+) truncated=(\d+)(?: kept=
    # wire: capture
    'nexus_commit_absent': 'capture',
    'nexus_reply_separation': 'capture',
    'nexus_tree_modified': 'capture',
    # wire: commit_key
    'nexus_commit': 'commit_key',
    # wire: kind
    'nexus.action': 'kind',
    'nexus.chain': 'kind',            # W11d: the chain document (docs/TALIMA_SPEC.md, section 11)
    'nexus.commitment': 'kind',
    'nexus.credential': 'kind',
    # wire: nexus_bare
    'nexus': 'nexus_bare',
    # wire: prefix
    'sha256:d5cb5bba3d4a4632': 'prefix',    # You are NEXUS, a local synthetic cortex with a persistent memory subst
    # W11b fix round: the scripts' `_*.py` helpers and `bytes` constants, now in the population.
    ': the wrong database or schema, or not a NEXUS store': 'message',
    ': the wrong file, or not a NEXUS store': 'message',
    'SELECT name, seq FROM nexus_sequence': 'sql',
    'sha256:f863ed0888e2fc2c': 'sql',    # INSERT INTO nexus_sequence (name, seq) VALUES (?, ?) ON CONFLICT ...
    'nexus-open-rows-': 'temp',
    'sha256:db936b3714afbf39': 'message',    # : talima-verify is nexus-verify under the standard's name and runs it
    "b'NEXUS-BLOCK-V1\\x00'": 'statement',
}


# Each file's frozen literals, in source order: the verdict's third part. A rename at one site changes a count here
# even while its spelling survives at another site; a new spelling with no `nexus` in it changes the count of the
# one it replaced; and two literals swapped between sites change the order. Generated from the tree once, then
# read; edited with FROZEN_IDENTIFIERS, never alone.
FROZEN_SITES = {
    'backend/cortex/contradiction.py': (
        'nexus.contradiction',
        'NEXUS_CONTRADICTION_PAIRS',
        'sha256:a52e45e1a3812d76',
        'sha256:a23dc5a04b55014e',
    ),
    'backend/cortex/curiosity_placement.py': (
        'NEXUS_CURIOSITY_PLACEMENT',
    ),
    'backend/cortex/edges.py': (
        'nexus.cortex',
        'NEXUS_REFRACTORY_HOURS',
        'NEXUS_REFRACTORY_HOURS=%r is not a number — falling back to %s h',
    ),
    'backend/cortex/forecast.py': (
        'nexus.forecast',
        'NEXUS_CITATION_FORECAST',
    ),
    'backend/cortex/hebbian.py': (
        'nexus.hebbian',
        'NEXUS_HEBBIAN_PAIRING',
        'NEXUS_HEBBIAN_TOPK',
        'NEXUS_HEBBIAN_TOPK=%r is not an integer — using %s',
    ),
    'backend/data_root.py': (
        'NEXUS_DATA_ROOT',
    ),
    'backend/env.py': (
        'nexus.env',
        'NEXUS_ENV_ROOT',
        'NEXUS_ENV_ROOT',
        'sha256:1133b3f83a4181e9',
    ),
    'backend/eval/calibration_log.py': (
        'nexus.calibration',
        'NEXUS_CALIBRATION_LOG',
    ),
    'backend/eval/citation_capture.py': (
        'nexus.capture',
        'NEXUS_CITATION_CAPTURE',
        'NEXUS_CITATION_CAPTURE_PATH',
    ),
    'backend/eval/fire_capture.py': (
        'nexus.capture',
        'NEXUS_FIRE_CAPTURE',
        'NEXUS_FIRE_CAPTURE_PATH',
    ),
    'backend/eval/ragas.py': (
        'nexus_local_ragas',
    ),
    'backend/identity.py': (
        'NEXUS_AUTH_MODE',
        'NEXUS_IDENTITY_HEADER',
        'NEXUS_OWNER_IDENTITY',
        'nexus.identity',
        'sha256:84e4094b65bcdd83',
        'sha256:7fb69c66c4853807',
        ' is not an auth mode NEXUS knows (',
    ),
    'backend/intercept/config.py': (
        'NEXUS_INTERCEPT_UPSTREAM',
        'NEXUS_INTERCEPT_DATA_ROOT',
        'NEXUS_INTERCEPT_OWNER',
        'NEXUS_INTERCEPT_INJECT',
        'NEXUS_INTERCEPT_PROXY_HEADERS',
        'NEXUS_MCP_DATA_ROOT',
    ),
    'backend/intercept/inject.py': (
        '<nexus-block source="retrieval">',
        '</nexus-block>',
        'NEXUS_STORE',
        'sha256:d27f90468b0337cf',
    ),
    'backend/kb/as_of.py': (
        'sha256:febb4182ea30479d',
    ),
    'backend/kb/citation.py': (
        'nexus.kb.citation',
        'NEXUS_CITATION_RULES',
        'NEXUS_REINFORCE_MODE',
        'NEXUS_REINFORCE_MODE=%r is not one of %s; using %r',
    ),
    'backend/kb/gate.py': (
        'NEXUS_INGEST_GATE',
        'NEXUS_EXTRACTION_MIN_CONFIDENCE',
    ),
    'backend/kb/recalibration.py': (
        'nexus.recalibration',
        'NEXUS_RECALIBRATION',
        'NEXUS_REFUSAL_LEDGER',
        'NEXUS_RECALIBRATION=off — no weight may move',
    ),
    'backend/kb/retriever.py': (
        'nexus.retrieval',
    ),
    'backend/kb/schema.py': (
        'nexus.kb.schema',
    ),
    'backend/kb/seeder.py': (
        'sha256:d4c511d99e48a7cf',
    ),
    'backend/kb/semantic_index.py': (
        'nexus.semantic',
        'NEXUS_SEMANTIC_SLOTS',
        'NEXUS_SEMANTIC_SLOTS=%r is not an integer; using %d',
        'NEXUS_SEMANTIC_MIN_SIMILARITY',
        'NEXUS_SEMANTIC_MIN_SIMILARITY=%r is not a number; using %.2f',
        'NEXUS_SEMANTIC_INDEX_EXCLUDE',
        'NEXUS_SEMANTIC_INDEX_BATCH',
    ),
    'backend/kb/source_policy.py': (
        'NEXUS_REFERENCE_GATEWAY',
    ),
    'backend/kb/tiered_store.py': (
        'nexus.kb.store',
        'NEXUS_SUBSTRATE_CLOCK_REFRESH',
        'NEXUS_ARCHIVAL_LEDGER',
        'NEXUS_DECAY_',
        'NEXUS_DECAY_%s=%r is not a number; using default',
        'NEXUS_ARRIVAL_',
        'NEXUS_ARRIVAL_%s=%r is not a number; using default',
    ),
    'backend/kb/trails.py': (
        'nexus.kb.trails',
        'NEXUS_RELATION_TRAILS',
        'NEXUS_TRAIL_SLOTS',
        'NEXUS_TRAIL_SLOTS=%r is not an integer — using 0',
    ),
    'backend/kb/vocabulary.py': (
        'nexus.vocabulary',
        'NEXUS_EXTRACTION_VOCABULARY',
        'NEXUS_RELATION_VOCABULARY',
        'sha256:b30610077fe0c2cb',
        'sha256:68cd72c1d381fbb2',
    ),
    'backend/learning.py': (
        'NEXUS_LEARNING',
        'NEXUS_LEARNING_FROZEN_SINCE',
    ),
    'backend/loop/agentic_loop.py': (
        'nexus.loop',
        'NEXUS_ANSWER_RELEASE',
        'NEXUS_SUBSTRATE_WEIGHT_RESTORE',
        'sha256:7727edd214beb5d3',
    ),
    'backend/loop/compaction.py': (
        '<nexus-block source="compaction">',
        '</nexus-block>',
        'Callable[[NexusLoopState, Hippocampus | None], str] | None',
    ),
    'backend/loop/history.py': (
        '<nexus-block source="prior_answer"',
        '</nexus-block>',
        'sha256:813716cfb65de467',
        'NEXUS_HISTORY_WRAP',
    ),
    'backend/loop/prefix.py': (
        'sha256:d5cb5bba3d4a4632',
    ),
    'backend/loop/prompt_render.py': (
        'NEXUS_PROMPT_RENDER',
    ),
    'backend/mcp/__main__.py': (
        'nexus.mcp',
        'NEXUS MCP: data folder %s (%s)',
        'NEXUS MCP: %s=%r is an unexpanded placeholder, not an identity: records are unattributed',
        'nexus.mcp',
        'NEXUS MCP: env files %s; store %s; owner %s; learning %s (%s); contract v%s',
        'NEXUS MCP: ',
        'NEXUS MCP: ',
        'NEXUS MCP: ',
    ),
    'backend/mcp/bundle/entry.py': (
        'NEXUS_AUDIT_NETWORK',
        'nexus.audit_started',
    ),
    'backend/mcp/bundle/manifest.json': (
        'nexus',
        'NEXUS (learning frozen)',
        'sha256:6af61cd903e6e06c',
        'sha256:39bac342fc22a465',
        'NEXUS',
        'server/nexus-mcp/nexus-mcp',
        '${__dirname}/server/nexus-mcp/nexus-mcp',
        'NEXUS_MCP_OWNER',
        'NEXUS_MCP_DATA_ROOT',
        'NEXUS_LEARNING',
        'NEXUS_ENV_ROOT',
        '${__dirname}/server/nexus-mcp/nexus-mcp.exe',
        'NEXUS_MCP_OWNER',
        'NEXUS_MCP_DATA_ROOT',
        'NEXUS_LEARNING',
        'NEXUS_ENV_ROOT',
        'nexus_retrieve',
        'sha256:874c1d4a38dfab2e',
        'nexus_record',
        'nexus_recall',
        'nexus_asof',
        'nexus_assert',
        'nexus_assert_confirm',
        'nexus_commit',
        'nexus_record_action',
        'Records an action you report for a turn, as client_reported: NEXUS did not see it happen.',
        'nexus_status',
        'sha256:66dbf103ba512831',
    ),
    'backend/mcp/config.py': (
        'NEXUS_MCP_OWNER',
        'NEXUS_MCP_DATA_ROOT',
        'NEXUS_CALIBRATION_LOG',
        'NEXUS_CITATION_CAPTURE_PATH',
        'NEXUS_FIRE_CAPTURE_PATH',
        'NEXUS',
        'NEXUS',
        'nexus',
    ),
    'backend/mcp/coverage.py': (
        'sha256:2da1f9e0f70fd22c',
        'sha256:6bcee8b234da5c2e',
    ),
    'backend/mcp/handles.py': (
        'NEXUS_MCP_HANDLE_TIMEOUT',
    ),
    'backend/mcp/instructions.py': (
        'sha256:53db4ee572f25d93',
    ),
    'backend/mcp/mcp_server.py': (
        'nexus.mcp',
        'nexus',
        'sha256:f5287373bbc2257c',
        'NexusRuntime',
        'sha256:dccca613ba09195e',
        'failed: record_not_written — NEXUS could not write the record of this turn (',
        '; the retrieval stays open, so nexus_record may be called again with the same retrieval_id',
        'nexus_assert_confirm(sentence, confirmed=true) after the user confirms',
        'mcp:nexus_assert_confirm',
        'mcp:nexus_commit',
        'sha256:d91757b18def8204',
        'refused: unknown_lineage_id — report an action against the lineage_id nexus_record returned',
        'sha256:36a6ba5f02eeb975',
        'nexus://lineage/{lineage_id}',
    ),
    'backend/mcp/scope.py': (
        'sha256:d8745aca39ab6ca8',
        'sha256 of the empty string: NEXUS sent no prompt, only the block it returned',
        "sha256 of the answer text as the client sent it — the client's claim, not NEXUS's observation",
        'sha256:2ff08f3ae09c1824',
        "NULL — the client's model ran outside NEXUS; the reason is in capture_notes (W0b)",
        'sha256:397eb5ac13bf3339',
        "the console's full turn: NEXUS ran the model and every field is NEXUS's own observation",
        "prompt_prefix_hash is not sha256('') on an mcp_block record (NEXUS sent no prompt)",
        " is set on an mcp_block record (the client's model ran outside NEXUS)",
    ),
    'backend/memory/actions.py': (
        'mcp:nexus_record_action',
        'action_not_recorded: NEXUS_SIGN_REQUESTS=on and the ',
    ),
    'backend/memory/anchor.py': (
        'NEXUS_ANCHOR',
        'NEXUS_ANCHOR_URL',
    ),
    # W10c — the two switches moved here from providers/replay_capture.py, their spellings unchanged.
    'backend/memory/capture_settings.py': (
        'NEXUS_INFERENCE_SEED',
        'NEXUS_KEEP_REQUESTS',
    ),
    'backend/memory/checkpoint.py': (
        'nexus_blob',
        'nexus_blob',
    ),
    'backend/memory/credentials.py': (
        'nexus.credential',
        'nexus.commitment',
        'nexus.action',
        'sha256:5f7fbf5d867715e4',
        'nexus.chain',
    ),
    'backend/memory/graph_store.py': (
        'nexus.graph_store',
    ),
    'backend/memory/http_signatures.py': (
        'NEXUS_SIGN_REQUESTS',
        'nexus',
    ),
    'backend/memory/instance_key.py': (
        "b'NEXUS-BLOCK-V1\\x00'",
    ),
    'backend/memory/kairos.py': (
        'nexus.kairos',
        'NEXUS_SNAPSHOT_INTERVAL_DAYS',
        'NEXUS_SNAPSHOT_INTERVAL_DAYS=%r is not a number — falling back to %s',
        'NEXUS_GOVERNANCE_SEAL',
        'NEXUS_GOVERNANCE_SEAL=%r is not one of %s; using %r',
        'NEXUS_GOVERNANCE_SEAL_INTERVAL_MINUTES',
        'NEXUS_GOVERNANCE_SEAL_INTERVAL_MINUTES=%r is not a number — falling back to %s',
        'NexusArchiveChain | None',
    ),
    'backend/memory/lineage.py': (
        'sha256:bc419cbd8f8c9572',
        'sha256:a88fad8aa706ddbd',
        'sha256:f0f005812f7682d9',
        'sha256:0801c63a1f9c7788',
    ),
    'backend/memory/neocortex.py': (
        'nexus.neocortex',
        'NEXUS_CONSOLIDATION_BATCH',
        'NEXUS_RECURRENCE_THRESHOLD',
        'NEXUS_RECURRENCE_THRESHOLD=%r is not an integer — falling back to %s',
    ),
    'backend/providers/anthropic.py': (
        'sha256:357715ce8cc69de6',
    ),
    'backend/providers/api_common.py': (
        'the embedder named by NEXUS_EMBED_URL and NEXUS_EMBED_MODEL (',
    ),
    'backend/providers/api_settings.py': (
        'NEXUS_EMBED_API_KEY',
    ),
    'backend/providers/backends.py': (
        'NEXUS_EMBED_URL',
        'NEXUS_EMBED_MODEL',
        ' is not a backend NEXUS knows (',
        '); set it in .env or the environment. NEXUS names no model for you.',
    ),
    'backend/providers/base.py': (
        'NEXUS_PROBE_CONNECT_TIMEOUT',
        'NEXUS_REPLY_SEPARATION',
        'seed off (NEXUS_INFERENCE_SEED=off)',
    ),
    'backend/providers/openai_compat.py': (
        'sha256:9a1901e5e17d0ca2',
    ),
    'backend/providers/replay_capture.py': (
        'nexus_reply_separation',
        'nexus_commit',
        'nexus_tree_modified',
        'nexus_commit_absent',
        'not observable from NEXUS (a daemon-side setting)',
    ),
    'backend/providers/stub.py': (
        "This is the fixed answer of NEXUS's stub provider (LLM_BACKEND=stub), asked for model ",
    ),
    'backend/server.py': (
        'nexus_ui.html',
        'nexus.server',
        'NEXUS_API_TOKEN',
        'sha256:c918b1e308f82480',
        'sha256:2181ca8f01bf1918',
        'NexusServices',
        'NEXUS_STARTUP_PROBE',
        'NEXUS_STARTUP_PROBE=%r is not one of %s; using %r',
        'NexusServices',
        'NexusServices',
        'NEXUS: ',
        'NEXUS: ',
        'NEXUS: ',
        'NEXUS',
        'sha256:fbb82ffdd757b563',
        'sha256:7fecbc03da30076a',
        '<head><script>window.NEXUS_TOKEN=',
        'NEXUS',
        'sha256:24363c54899af996',
        'nexus_memory.html',
        '<h1>nexus_memory.html not found</h1>',
        'sha256:734d4774198d2b15',
        'sha256:362d8a009d100527',
        'sha256:f9ccd2636f95bf3d',
    ),
    'backend/store/dialect.py': (
        'nexus_sequence',
        'nexus_lower',
        'nexus_store_lock',
        'nexus_store_lock_log',
        'nexus_autoinc__',
        'sha256:8149c238f49bfc45',
        'sha256:524d8272a921838f',
        'nexus_rowid__',
        'sha256:942a42ceff557e38',
        'sha256:b77c336c22611ec9',
        'sha256:362c276032bf88bb',
    ),
    'backend/store/postgres_backend.py': (
        'nexus',
    ),
    'backend/store/target.py': (
        'NEXUS_STORE',
        'NEXUS_DATABASE_URL',
        ' is not a store backend NEXUS knows (',
    ),
    'backend/store_lock.py': (
        'this store is open in another NEXUS process (pid ',
        'nexus_store_lock',
        'nexus-store:',
        'nexus_store_lock',
        'nexus_store_lock_log',
        'SELECT holder FROM nexus_store_lock WHERE lock_key = ?',
        'sha256:0ae9dd6911cd9667',
        'INSERT INTO nexus_store_lock_log (at, event) VALUES (?, ?)',
        'UPDATE nexus_store_lock SET holder = ? WHERE lock_key = ?',
    ),
    'backend/tools/search.py': (
        'nexus.tools.search',
        'NEXUS/0.10 (+local synthetic cortex; SearXNG client)',
    ),
    'backend/vector/chroma.py': (
        'nexus_coarse',
        'nexus_full',
    ),
    'backend/vector/embeddings.py': (
        'nexus.embeddings',
    ),
    'scripts/_env.py': (
        'NEXUS_API_TOKEN',
    ),
    'scripts/_readonly.py': (
        ': the wrong file, or not a NEXUS store',
        ': the wrong database or schema, or not a NEXUS store',
    ),
    'scripts/_store_copy.py': (
        'nexus_sequence',
        'nexus_store_lock',
        'nexus_store_lock_log',
        'nexus-open-rows-',
        'SELECT name, seq FROM nexus_sequence',
        'sha256:f863ed0888e2fc2c',
    ),
    'scripts/as2_instance_windows.py': (
        'NEXUS_LEARNING_FROZEN_SINCE',
        "the operator's asserted date (YYYY-MM-DD); default from NEXUS_LEARNING_FROZEN_SINCE, else 2026-10-01",
    ),
    'scripts/audit_pairs.py': (
        'nexus_audit_',
    ),
    'scripts/backup.py': (
        'NEXUS_BACKUP_DATABASE_URL',
        ' has no lineage_records table: not a NEXUS store',
        'sha256:7d102a08454be766',
        'nexus_store_lock',
        'SELECT holder FROM nexus_store_lock ORDER BY lock_key LIMIT 1',
        ' holds no triples or lineage_records table: not a NEXUS store',
        'sha256:a7c4925052cb9166',
    ),
    'scripts/build_bundle.py': (
        'nexus-mcp',
        'NEXUS_LEARNING',
        'NEXUS_ENV_ROOT',
        'NEXUS_MCP_OWNER',
        'NEXUS_MCP_DATA_ROOT',
        'sha256:8f5ce54f70218fa1',
        'sha256:468ea51acaafd1d1',
        'NEXUS_',
        'nexus-',
    ),
    'scripts/bundle_smoke.py': (
        'nexus_retrieve',
        'nexus_record',
        'nexus_recall',
        'nexus_asof',
        'nexus_assert',
        'nexus_assert_confirm',
        'nexus_commit',
        'nexus_record_action',
        'nexus_status',
        'nexus-bundle-smoke',
        'nexus.audit_started',
        'NEXUS_',
        'sha256:0f9ca92f273bcbcf',
        'NEXUS_ENV_ROOT',
        'nexus_status',
        'nexus_retrieve',
        'nexus_record',
        'NEXUS MCP: ',
        'this store is open in another NEXUS process (pid ',
        'nexus_status',
        'nexus_status',
        'nexus-bundle-smoke-',
    ),
    'scripts/cp_b49_tally.py': (
        'the fire capture file; default NEXUS_FIRE_CAPTURE_PATH, else data/fire_capture.jsonl',
        '1. capture gate NEXUS_FIRE_CAPTURE in .env: ',
        'NEXUS_FIRE_CAPTURE',
        '5. AO (S1.5): NEXUS_CITATION_CAPTURE in .env: ',
        'NEXUS_CITATION_CAPTURE',
        'NEXUS_FIRE_CAPTURE',
        'NEXUS_CITATION_CAPTURE',
    ),
    'scripts/curiosity_pace_probe.py': (
        'defaults to NEXUS_CALIBRATION_LOG or data/calibration_log.jsonl',
    ),
    'scripts/export_postgres_to_sqlite.py': (
        'the source Postgres URL, as NEXUS_DATABASE_URL; opened read-only',
        'REFUSED: the source is not a NEXUS store (no triples or lineage_records table)',
        '.nexus-export-',
    ),
    'scripts/forecast_target_probe.py': (
        'defaults to NEXUS_CALIBRATION_LOG or data/calibration_log.jsonl',
    ),
    'scripts/guard_estate_census.py': (
        'NexusLoopState',
        'NexusLoopState',
        'NexusLoopState',
    ),
    'scripts/history_shape_harness.py': (
        '<nexus-block source="prior_answer" turn={turn} truncated={truncated}>',
        '</nexus-block>',
        'NEXUS_HISTORY_WRAP',
        'NEXUS_HISTORY_WRAP',
        'NEXUS_HISTORY_WRAP',
        'NEXUS_HISTORY_WRAP',
        '<nexus-block source="prior_answer"',
        '<nexus-block source="prior_answer"',
        'nexus_history_',
        'NEXUS_CALIBRATION_LOG',
    ),
    'scripts/history_shape_replay.py': (
        '<nexus-block source="prior_answer"',
        '<nexus-block source="prior_answer"',
        'nexus_replay_',
        'NEXUS_CALIBRATION_LOG',
    ),
    'scripts/measure_cycle_cost.py': (
        'nexus-cycle-cost-',
    ),
    'scripts/measure_retrieval.py': (
        'nexus_measure_',
        'NEXUS_SEMANTIC_SLOTS',
        'NEXUS_SEMANTIC_MIN_SIMILARITY',
    ),
    'scripts/measure_trails.py': (
        'nexus-trails-',
        'sha256:af4982e6c695c0ae',
    ),
    'scripts/migrate_sqlite_to_postgres.py': (
        'the target already holds NEXUS rows (',
        'the target Postgres URL (postgresql://...), as NEXUS_DATABASE_URL',
        ' is not a NEXUS store (no triples or lineage_records table)',
        'nexus-migrate-',
    ),
    'scripts/nexus_verify.py': (
        "b'NEXUS-BLOCK-V1\\x00'",
        'nexus.chain',
        'nexus.credential',
        ' NEXUS credential',
        'nexus.commitment',
        ' NEXUS commitment receipt',
        'nexus.action',
        ' NEXUS action receipt',
        '; the client reported it: NEXUS did not see it happen',
        'nexus-verify',
        'nexus-verify',
    ),
    'scripts/pairing_exclusion_probe.py': (
        'nexus_pairing_',
    ),
    'scripts/predicate_distribution.py': (
        'nexus_preddist_',
    ),
    'scripts/predicate_similarity.py': (
        'nexus_predsim_',
    ),
    'scripts/pregate_rows_probe.py': (
        'sha256:4afc5284a37185b5',
    ),
    'scripts/render_block_probe.py': (
        'nexus_render_',
        'NEXUS_CALIBRATION_LOG',
    ),
    'scripts/replay_check.py': (
        "mcp_block: the client's model ran outside NEXUS",
        'nexus_reply_separation',
        'NEXUS_HOST',
        'NEXUS_PORT',
        ' is not a NEXUS store (',
        'sha256:0c43b4c2953fb780',
    ),
    'scripts/restore_verify.py': (
        'nexus_restore_',
        'NEXUS_BACKUP_DATABASE_URL',
        'sha256:e371c3fe645c8267',
        'nexus_restore_',
    ),
    'scripts/talima_verify.py': (
        'nexus_verify.py',
        'sha256:db936b3714afbf39',
        'nexus_verify',
    ),
    'scripts/uat_simulate.py': (
        'nexus_verify.py',
        'NEXUS_',
        'NEXUS_API_TOKEN',
        'NEXUS_DATA_ROOT',
        'NEXUS_PORT',
        'NEXUS_LEARNING_FROZEN_SINCE',
        'NEXUS_API_TOKEN',
        'sha256:37376fca1d596f4e',
        'NEXUS_LEARNING',
        ' settings set; NEXUS_LEARNING=',
        'NEXUS_LEARNING',
        ', NEXUS_HOST=',
        'NEXUS_HOST',
        'NEXUS_LEARNING=frozen as .env.example ships it',
        'NEXUS_LEARNING_FROZEN_SINCE',
        'nexus-verify (',
        'nexus-verify: ',
        'verification: credentials, nexus-verify, commit now and reveal later',
        'python -I nexus_verify.py credential.json --identity identity.json',
        "a credential for the console's first turn, verified by nexus-verify with no store access",
        'nexus-verify: VERIFIED',
        'nexus-verify: VERIFIED',
        'sha256:d561406f5ba922ec',
        'nexus-verify: VERIFIED',
        'sha256:6d417ddc1a992682',
        'nexus-verify: VERIFIED',
        'NEXUS_API_TOKEN',
        'NEXUS_DATA_ROOT',
        'NEXUS_PORT',
        'NEXUS_SIGN_REQUESTS',
        'NEXUS_API_TOKEN',
        'NEXUS_SIGN_REQUESTS=on',
        'NEXUS_SIGN_REQUESTS=on',
        'nexus=:',
        "the action's receipt, verified by nexus-verify with no store access",
        'sha256:7c587dc4f3bec4bf',
        'nexus-verify: VERIFIED',
        'nexus=:',
        'GET /identity/signed-action?signature=… (no token); nexus-verify --action',
        'nexus-verify: VERIFIED',
        'NEXUS_INTERCEPT_OWNER',
        'sha256:74e1b29a53026e9e',
        'the record, the tool use and the exchange, verified by nexus-verify with no store access',
        'sha256:7c5890a07d647340',
        'nexus-verify: VERIFIED',
        'NEXUS_MCP_OWNER',
        'NEXUS_MCP_DATA_ROOT',
        'NEXUS_MCP_HANDLE_TIMEOUT',
        'nexus_status',
        'nexus_status',
        'nexus_assert',
        'nexus_asof',
        'nexus_assert: the offer, which stores nothing',
        'nexus_assert_confirm',
        'nexus_assert_confirm unconfirmed',
        'nexus_assert_confirm',
        'nexus_assert_confirm confirmed',
        'nexus_retrieve',
        'nexus_retrieve: the block to answer from',
        'nexus_record',
        'nexus_record: the record sealed for the answer',
        'nexus_record_action',
        'nexus_record_action: an action the client reports',
        'stored client_reported, completed: NEXUS did not see it happen, and says so',
        'nexus_recall',
        'nexus_recall on the confirmed claim',
        'nexus_recall on the confirmed claim',
        'nexus_asof',
        'nexus_asof at an instant with an offset',
        'nexus_asof',
        'nexus_asof at a bare date, refused by name',
        'nexus_retrieve',
        'nexus_record',
        'the late nexus_record refused by name (unknown_retrieval_id)',
        'nexus_status',
        'mcp: nexus_status, coverage',
        'nexus_status: the coverage counters',
        'nexus://lineage/',
        'the record read back: resources/read nexus://lineage/{id}',
        'nexus_backup_',
        'nexus_',
        'NEXUS_TESTERS',
        'NEXUS_BACKUP_ROLE',
        'NEXUS_BACKUP_PASSWORD',
        'NEXUS_',
        'NEXUS_',
        'sh deploy/postgres-init.sh (NEXUS_TESTERS=',
        'NEXUS_AUTH_MODE',
        'NEXUS_OWNER_IDENTITY',
        'NEXUS_DATA_ROOT',
        'NEXUS_PORT',
        'NEXUS_STORE',
        'nexus_',
        'NEXUS_DATABASE_URL',
        'NEXUS_DATABASE_URL',
        ' (the password: NEXUS_',
        '; NEXUS_STORE=postgres, NEXUS_DATABASE_URL per tester',
        'NEXUS_AUTH_MODE',
        'NEXUS_OWNER_IDENTITY',
        'NEXUS_AUTH_MODE',
        'NEXUS_DATA_ROOT',
        'NEXUS_PORT',
        'NEXUS_AUTH_MODE',
        'NEXUS_OWNER_IDENTITY',
        'NEXUS_HOST',
        'NEXUS_DATA_ROOT',
        'NEXUS_PORT',
        "'s NEXUS serves in proxy mode",
        "tester 1's NEXUS did not serve",
        "tester 1's NEXUS did not serve",
        'refused: a process with no owner (NEXUS_OWNER_IDENTITY unset)',
        'refused: a process with no owner (NEXUS_OWNER_IDENTITY unset)',
        'NEXUS_HOST=',
        'NEXUS_API_TOKEN',
        'NEXUS_DATA_ROOT',
        'NEXUS_PORT',
        'NEXUS_API_TOKEN',
        'python start.py --serve (NEXUS_DATA_ROOT=',
        'uninstall: the data roots kept (NEXUS never deletes a store)',
        'NEXUS_API_TOKEN',
        'NEXUS_DATA_ROOT',
        'NEXUS_PORT',
        'NEXUS_API_TOKEN',
        'NEXUS_DATA_ROOT',
        'NEXUS_PORT',
        'verification (decision credentials, nexus-verify, commit now and reveal later)',
        '# NEXUS simulated UAT: ',
        'sha256:9f95fcac1baec968',
        'nexus-uat-',
        'NEXUS_ENV_ROOT',
    ),
    'scripts/v31_phase0_probe.py': (
        'env_NEXUS_RECURRENCE_THRESHOLD',
        'NEXUS_RECURRENCE_THRESHOLD',
        'nexus_v31_fixture_',
    ),
    'scripts/verify_deploy.py': (
        'nexus-stub',
        'nexus-ollama',
        'nexus-api',
        'nexus-t*',
        'nexus-',
        'nexus_',
        'sha256:8f60a8024a23db15',
        'sha256:cd3093e54ca0298c',
        'sha256:c18619bd958a6b20',
        'sha256:c523ecb253cad68e',
        'NEXUS_',
        'NEXUS_IDENTITY_HEADER',
        ' (NEXUS_',
        'NEXUS_BACKUP_DIR',
        'the console through Caddy (default https://<NEXUS_DOMAIN or localhost>[:NEXUS_HTTPS_PORT])',
        'the console token (default NEXUS_API_TOKEN)',
        'nexus',
        '/opt/nexus',
        'where the backup service writes on the host (default NEXUS_BACKUP_DIR or ./backups)',
        'NEXUS_API_TOKEN',
        'REFUSED: no token (--token or NEXUS_API_TOKEN); the gated checks need it',
        'NEXUS_DOMAIN',
        'NEXUS_HTTPS_PORT',
        'NEXUS_API_BACKEND',
        'NEXUS_BACKUP_DIR',
    ),
    'scripts/verify_packet_f.py': (
        'nexus_verify_',
        'nexus_probe_',
        'nexus_cite_',
        'no `triples` table — is this a NEXUS kb.db?',
    ),
    'scripts/verify_packet_k.py': (
        'no `triples` table — is this a NEXUS kb.db?',
    ),
    'scripts/vocab_effect.py': (
        'nexus_vocabeffect_',
    ),
    'scripts/wc_window_count.py': (
        'the AO capture file; default NEXUS_CITATION_CAPTURE_PATH, else data/citation_capture.jsonl',
    ),
    'scripts/who_outranks.py': (
        ' (NEXUS_SEMANTIC_SLOTS; the quota this is about)',
        'nexus_who_',
    ),
}

def key_of(literal: str) -> str:
    """The set's key for a literal: itself, or its sha256's first 16 hex digits when it is long or has a newline."""
    if len(literal) <= 100 and "\n" not in literal:
        return literal
    return "sha256:" + hashlib.sha256(literal.encode("utf-8")).hexdigest()[:16]


def _docstrings(tree) -> set:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _in_order(tree, skip: set) -> list:
    """Every `str` and `bytes` constant outside docstrings, in one order on every Python: by position, except that
    each constant inside an f-string (its pieces, and the constants in its expressions, nested f-strings included)
    is read at the outermost f-string's position, in the tree's own order within it. From Python 3.12 (PEP 701)
    those pieces carry their own positions and before it they carried the f-string's, so their positions are not
    used (`test_t5_an_f_strings_pieces_are_in_one_order_on_every_python`)."""
    keyed = []

    def visit(node, place):
        if place is None and isinstance(node, ast.JoinedStr):
            place = [node.lineno, node.col_offset, 0]
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)) and id(node) not in skip:
            if place is None:
                keyed.append(((node.lineno, node.col_offset, 0), node))
            else:
                place[2] += 1
                keyed.append(((place[0], place[1], place[2]), node))
        for child in ast.iter_child_nodes(node):
            visit(child, place)

    visit(tree, None)
    return [node for _key, node in sorted(keyed, key=lambda kv: kv[0])]


def literals(rel: str, text: str) -> list:
    """Each string in a file that contains `nexus` in any case, once per occurrence and in source order (`_in_order`:
    an f-string's pieces at the f-string's place): `str` and `bytes` constants outside docstrings in Python (a
    `bytes` constant as its `repr`, so `b'NEXUS-BLOCK-V1\\x00'`), every string in JSON in document order."""
    if rel.endswith(".json"):
        found = list(_strings(json.loads(text)))
    else:
        tree = ast.parse(text)
        found = [n.value if isinstance(n.value, str) else repr(n.value) for n in _in_order(tree, _docstrings(tree))]
    return [s for s in found if "nexus" in s.lower()]


def tracked_texts() -> dict:
    """{path: text} for the tracked backend's and scripts' Python files (`scripts/_*.py` included), and the bundle's
    manifest."""
    out = {}
    for path in list(tracked_backend()) + list(tracked("scripts/*.py")):
        if path.suffix == ".py":
            rel = (path.relative_to(REPO) if path.is_absolute() else path).as_posix()
            out[rel] = path.read_text(encoding="utf-8")
    out[MANIFEST] = (REPO / MANIFEST).read_text(encoding="utf-8")
    return out


def population(texts: dict) -> dict:
    """{key: {path: occurrences}} over every literal of every file."""
    where: dict = {}
    for rel, text in texts.items():
        for key, n in Counter(key_of(s) for s in literals(rel, text)).items():
            where.setdefault(key, {})[rel] = n
    return where


def sites_of(texts: dict) -> dict:
    """{path: (key, ...)}: each file's frozen literals in source order, the shape FROZEN_SITES is written in."""
    out = {rel: tuple(key_of(s) for s in literals(rel, text)) for rel, text in texts.items()}
    return {rel: keys for rel, keys in out.items() if keys}


def verdict(texts: dict, frozen: dict, sites: dict | None) -> list:
    """What does not hold: a literal the set does not name (with its files), an entry nothing holds, and (unless
    `sites` is None) every file whose count of a frozen literal moved, so a rename at one site fails even while the
    spelling survives at another, and a new spelling without `nexus` in it fails by the one it replaced; and, in a
    file whose counts all hold, the first place its frozen literals' order differs from FROZEN_SITES's (a swap)."""
    found = population(texts)
    added = [f"not in FROZEN_IDENTIFIERS: {k!r} in {', '.join(sorted(found[k]))}" for k in sorted(set(found) - set(frozen))]
    gone = [f"in FROZEN_IDENTIFIERS and found nowhere: {k!r}" for k in sorted(set(frozen) - set(found))]
    moved = []
    if sites is not None:
        now = sites_of(texts)
        for rel in sorted(set(now) | set(sites)):
            a, b = tuple(sites.get(rel, ())), now.get(rel, ())
            ca, cb = Counter(a), Counter(b)
            counted = [f"a site moved: {k!r} in {rel}: frozen {ca[k]}, found {cb[k]}"
                       for k in sorted(set(ca) | set(cb)) if ca[k] != cb[k]]
            if counted:
                moved += counted
            elif a != b:                 # the same literals, in another order: a swap between sites
                i = next(n for n, (x, y) in enumerate(zip(a, b)) if x != y)
                moved.append(f"the order of the frozen literals moved in {rel}: at {i}, frozen {a[i]!r}, found {b[i]!r}")
    return added + gone + moved


def test_t1_every_literal_naming_nexus_is_answered_for_and_every_answer_is_found():
    texts = tracked_texts()
    found = population(texts)
    classes = Counter(REASONS[r][0] for r in FROZEN_IDENTIFIERS.values())
    print(f"\nT1 {len(found)} literals in {len({f for v in found.values() for f in v})} files: {dict(classes)}")
    assert set(FROZEN_IDENTIFIERS.values()) <= set(REASONS), "an entry names a reason that does not exist"
    assert {c for c, _why in REASONS.values()} <= set(CLASSES), "a reason names a class that does not exist"
    assert len(found) >= 300 and all(classes[c] for c in CLASSES), "non-vacuity: the population and every class"
    assert set(FROZEN_SITES) <= set(texts), "FROZEN_SITES names a file the population does not hold"
    assert verdict(texts, FROZEN_IDENTIFIERS, FROZEN_SITES) == []


def _verify_as_a_stranger(where: Path, verifier_text: str) -> "_proc.subprocess.CompletedProcess":
    (where / "nexus_verify.py").write_text(verifier_text, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    return _proc.run([sys.executable, "-I", "nexus_verify.py", "credential.json", "--identity", "identity.json"],
                     cwd=where, env=env, timeout=120, capture_output=True, text=True, encoding="utf-8",
                     errors="replace")


def test_t2_a_respelled_credential_kind_fails_the_verdict_and_its_verifier_refuses_the_base_credential(tmp_path):
    texts = tracked_texts()
    respelled = dict(texts)
    for rel in ("backend/memory/credentials.py", "scripts/nexus_verify.py"):
        assert '"nexus.credential"' in respelled[rel], f"non-vacuity: {rel} spells the kind"
        respelled[rel] = respelled[rel].replace('"nexus.credential"', '"nexus.Credential"')
    problems = verdict(respelled, FROZEN_IDENTIFIERS, FROZEN_SITES)
    assert any("'nexus.Credential'" in p for p in problems), problems
    assert any("'nexus.credential'" in p for p in problems), problems
    where = tmp_path / "stranger"
    where.mkdir()
    for name in ("credential.json", "identity.json"):
        shutil.copy2(GOLDEN / name, where / name)
    refused = _verify_as_a_stranger(where, respelled["scripts/nexus_verify.py"])
    assert refused.returncode == 1 and "NOT VERIFIED" in refused.stdout, refused.stdout + refused.stderr
    held = _verify_as_a_stranger(where, texts["scripts/nexus_verify.py"])
    assert held.returncode == 0, "non-vacuity: the verifier as it is verifies the base credential\n" + held.stdout
    print(f"\nT2 {problems}; the copy's verifier: {refused.stdout.strip().splitlines()[-1]}")



def _one_site(tmp_path, old: str, new: str, what: str):
    """The verifier respelled at one site only, the way the old value-keyed check could not see: the spelling
    survives in the producer, or the new one has no `nexus` in it. Returns the verdict with and without the sites,
    and the copy's verifier run on step 1's credential."""
    texts = tracked_texts()
    rel = "scripts/nexus_verify.py"
    assert texts[rel].count(old) == 1, f"non-vacuity: {rel} spells {what} once"
    assert any(old in texts[r] for r in texts if r != rel), f"non-vacuity: {what} survives at another site"
    planted = dict(texts, **{rel: texts[rel].replace(old, new)})
    where = tmp_path / "stranger"
    where.mkdir()
    for name in ("credential.json", "identity.json"):
        shutil.copy2(GOLDEN / name, where / name)
    return (verdict(planted, FROZEN_IDENTIFIERS, None), verdict(planted, FROZEN_IDENTIFIERS, FROZEN_SITES),
            _verify_as_a_stranger(where, planted[rel]))


def test_t2b_a_kind_respelled_talima_at_one_site_fails_the_verdict_and_its_verifier_refuses_the_base_credential(
        tmp_path):
    by_value, by_site, refused = _one_site(tmp_path, '"nexus.credential"', '"talima.credential"', "the kind")
    assert by_value == [], f"the plant is the class the value-keyed check misses: {by_value}"
    assert by_site == ["a site moved: 'nexus.credential' in scripts/nexus_verify.py: frozen 1, found 0"], by_site
    assert refused.returncode == 1 and "NOT VERIFIED" in refused.stdout, refused.stdout + refused.stderr
    print(f"\nT2b {by_site}; the copy's verifier: {refused.stdout.strip().splitlines()[-1]}")


def test_t2c_the_signed_statement_prefix_respelled_fails_the_verdict_and_its_verifier_refuses_the_base_credential(
        tmp_path):
    by_value, by_site, refused = _one_site(tmp_path, 'b"NEXUS-BLOCK-V1\\x00"', 'b"TALIMA-BLOCK-V1\\x00"',
                                           "the bytes every block signature signs")
    assert by_value == [], f"the plant is the class the value-keyed check misses: {by_value}"
    assert by_site == ["a site moved: \"b'NEXUS-BLOCK-V1\\\\x00'\" in scripts/nexus_verify.py: frozen 1, found 0"], by_site
    assert refused.returncode == 1 and "NOT VERIFIED" in refused.stdout, refused.stdout + refused.stderr
    print(f"\nT2c {by_site}; the copy's verifier: {refused.stdout.strip().splitlines()[-1]}")


def _linux_data_folder(source: str) -> Path:
    """`default_data_root()` from `source` (a text of `backend/mcp/config.py`), loaded as a module of its own and run
    as on Linux, whatever this machine is: `os.name`, the environment and `sys.platform` are stood in for."""
    import types
    module = types.ModuleType("planted_config")
    exec(compile(source, "planted_config.py", "exec"), module.__dict__)
    module.os = types.SimpleNamespace(name="posix", environ={})
    module.sys = types.SimpleNamespace(platform="linux")
    return module.default_data_root()


def test_t2d_two_data_folders_swapped_fails_the_verdict_and_moves_every_linux_store():
    """The Windows folder `NEXUS` and the Linux folder `nexus` swapped in `backend/mcp/config.py`: no literal is
    added or removed and no count moves (asserted), so only the order sees it; and the Linux default moves from
    `nexus` to `NEXUS`, which on a case-sensitive file system is another folder: every existing Linux store is
    orphaned."""
    rel = "backend/mcp/config.py"
    texts = tracked_texts()
    windows, linux = '        return Path(base) / "NEXUS"\n', '    return Path(base) / "nexus"\n'
    assert texts[rel].count(windows) == 1 and texts[rel].count(linux) == 1, "non-vacuity: the two folders"
    swapped = (texts[rel].replace(windows, windows.replace('"NEXUS"', '"<swap>"'))
               .replace(linux, linux.replace('"nexus"', '"NEXUS"')).replace('"<swap>"', '"nexus"'))
    assert swapped.count('        return Path(base) / "nexus"\n') == 1, "the Windows folder now reads nexus"
    assert swapped.count('    return Path(base) / "NEXUS"\n') == 1, "the Linux folder now reads NEXUS"
    planted = dict(texts, **{rel: swapped})
    assert verdict(planted, FROZEN_IDENTIFIERS, None) == [], "no literal is added or removed"
    assert Counter(sites_of(planted)[rel]) == Counter(FROZEN_SITES[rel]), "no count moves: the class counts miss"
    problems = verdict(planted, FROZEN_IDENTIFIERS, FROZEN_SITES)
    assert len(problems) == 1 and problems[0].startswith(f"the order of the frozen literals moved in {rel}: "), problems
    assert _linux_data_folder(texts[rel]).name == "nexus", "non-vacuity: the Linux default as it is"
    assert _linux_data_folder(swapped).name == "NEXUS", "the consequence: the Linux store's folder moves"
    print(f"\nT2d {problems}")


F_STRING_SAMPLE = (
    "x = f\"nexus-a {d['nexus-k']} nexus-b\"\n"
    "y = (f\"nexus-1 {a}\"\n"
    "     f\" nexus-2 {b['nexus-3']}\")\n"
    "w = f\"nexus-o {f'nexus-i {v}'}\"\n"
    "z = 'nexus-after'\n")


def test_t5_an_f_strings_pieces_are_in_one_order_on_every_python():
    """From Python 3.12 (PEP 701) each piece of an f-string carries its own position; before it, each carried the
    f-string's. The order FROZEN_SITES holds must not depend on which: every piece and every constant inside an
    f-string's expressions is read at the outermost f-string's place, in the tree's own order. Pinned on a sample of
    three shapes, on whichever interpreter runs the suite: an expression naming nexus between two pieces and a
    concatenation across lines, which the tree holds, and a nested f-string, which it does not hold today."""
    assert literals("sample.py", F_STRING_SAMPLE) == [
        "nexus-a ", "nexus-k", " nexus-b", "nexus-1 ", " nexus-2 ", "nexus-3", "nexus-o ", "nexus-i ", "nexus-after"]


def test_t3_a_planted_literal_the_set_does_not_name_fails_the_verdict():
    texts = dict(tracked_texts())
    texts["backend/zz_planted.py"] = 'BANNER = "Nexus Nightly"\n'
    problems = verdict(texts, FROZEN_IDENTIFIERS, FROZEN_SITES)
    assert any("'Nexus Nightly'" in p and "backend/zz_planted.py" in p for p in problems), problems


def test_t4_an_entry_removed_from_the_set_fails_the_verdict():
    frozen = dict(FROZEN_IDENTIFIERS)
    del frozen["nexus.action"]
    problems = verdict(tracked_texts(), frozen, FROZEN_SITES)
    assert any("'nexus.action'" in p for p in problems), problems
