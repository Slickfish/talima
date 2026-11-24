# Contracts

What the code and its tests hold NEXUS to, in one place: the laws, the settings, the events a turn streams, the
record's contracts (how an answer is recorded, proved, read back and dated, and what passes the reference gateway),
and what is not built yet. Tests read this page: most compare a section with the code and fail when the two
disagree; the others read it for its encoding, for its wording (the gateway's copy rule; the statements that
mark something not built, as "Not built yet" below describes), for its list of what is not built, and, in the
suite's own setup, for the names of the settings.

The text names the development record's items (W5, W9), packets (AR, AS-1) and ledger lines (B-40) as it was
written; that record is not published, and `docs/GLOSSARY.md` says what each kind of name refers to.

## The laws

1. Frozen dataclasses. Replace whole state, never mutate.
2. Capabilities not model names in routing logic.
3. Confirm before: pull model, seed KB, delete data, archive triples.
4. Tier triples (0-4), never delete.
5. `num_ctx=8192` max.
6. Async generator loop, yield SSE events.
7. Stable system prefix, dynamic content appended.
8. Compaction cheapest-first: snip→micro→collapse→auto.

## Env (.env in repo root)

```
LLM_BACKEND=ollama                # ollama | lmstudio | stub | openai | anthropic (W3: stub is a deterministic in-process provider for CI and the stub compose profile, no model server; openai an OpenAI-compatible chat endpoint and anthropic the Messages API, the api compose profile); any other value is refused by name at startup, and so is an api backend whose model is unset
OLLAMA_URL=http://localhost:11434
LM_STUDIO_URL=http://localhost:1234
OPENAI_BASE_URL=https://api.openai.com/v1   # LLM_BACKEND=openai: the OpenAI-compatible endpoint (W3); /chat/completions and /models under it
OPENAI_API_KEY=                   # sent as Authorization: Bearer; read in one module (backend/providers/api_settings.py), never recorded; a local endpoint may need none
OPENAI_MODEL=                     # REQUIRED under openai: the chat model the endpoint serves (NEXUS names none); the record names the model the endpoint REPORTS, as openai:<name>
OPENAI_EXTRACTION_MODEL=          # the extraction model under openai; unset = OPENAI_MODEL
ANTHROPIC_BASE_URL=https://api.anthropic.com   # LLM_BACKEND=anthropic: the Messages API (W3); /v1/messages and /v1/models under it
ANTHROPIC_API_KEY=                # sent as x-api-key; read in one module, never recorded
ANTHROPIC_MODEL=                  # REQUIRED under anthropic: the Claude model asked for; the record names the model the API reports, as anthropic:<name>; the request's max_tokens is the caller's context size (8192 answering, 4096 extraction), no seed
ANTHROPIC_EXTRACTION_MODEL=       # the extraction model under anthropic; unset = ANTHROPIC_MODEL
NEXUS_EMBED_URL=                  # the api backends' embedder, an OpenAI-compatible base URL (/embeddings appended), with NEXUS_EMBED_MODEL: both or neither (W3; Anthropic offers no embedder); neither = the idle scans do not embed, said on /cortex/stats.embedder
NEXUS_EMBED_MODEL=                # the embedding model at NEXUS_EMBED_URL
NEXUS_EMBED_API_KEY=              # that endpoint's own key when it wants one; sent as Authorization: Bearer
SEARXNG_URL=http://localhost:8888  # optional, Phase 10a/b
NEXUS_STARTUP_PROBE=background     # sync | background | off  (Packet R.2)
NEXUS_PROBE_CONNECT_TIMEOUT=0.25   # seconds (Packet R.2); since W3 also every provider's health() connect, which /healthz awaits
NEXUS_RECALIBRATION=on             # on | off — kill switch (Packet S.2)
NEXUS_GOVERNANCE_SEAL=prompt       # prompt | snapshot | off  (Packet S.2b)
NEXUS_GOVERNANCE_SEAL_INTERVAL_MINUTES=0   # floor, 0 = every cycle (S.2b)
NEXUS_CONTRADICTION_PAIRS=                 # path to a pair file (Packet T.2)
NEXUS_EXTRACTION_VOCABULARY=on             # on | off — rollback (Packet V.1)
NEXUS_RELATION_VOCABULARY=                 # path to a relation list (V.1)
NEXUS_SEMANTIC_SLOTS=0                     # candidates the semantic channel may add (W.1)
NEXUS_SEMANTIC_MIN_SIMILARITY=0.35         # MEASURED (W.2); was 0.55, unmeasured
NEXUS_SEMANTIC_INDEX_BATCH=32              # beliefs embedded per KAIROS idle cycle (W.1)
NEXUS_SEMANTIC_INDEX_EXCLUDE=              # unset = substrate relations; "" = index all (W.2)
NEXUS_CONSOLIDATION_BATCH=on               # on | off — off restores per-row commits (Packet AA)
NEXUS_RELATION_TRAILS=on                   # on | off — off stops trail laying (Packet AB)
NEXUS_TRAIL_SLOTS=0                        # candidates the trail channel may admit; 0 = channel off (AB)
NEXUS_CITATION_FORECAST=on                 # on | off — off restores the pre-AF.1 turn exactly
NEXUS_SUBSTRATE_WEIGHT_RESTORE=off         # off | on — on is the rollback: pre-AK max() restoration
NEXUS_ARCHIVAL_LEDGER=on                   # on | off — off writes no archival events (pre-AL exactly)
NEXUS_REFUSAL_LEDGER=on                    # on | off — off writes no refused_requests rows (S.2's outcome_events rows are not behind it); every refusal is still declared (pre-AT exactly)
NEXUS_HEBBIAN_PAIRING=topk                 # topk | full — full is the rollback: the pre-AM pairing loop exactly
NEXUS_HEBBIAN_TOPK=20                      # nodes paired per turn (AM); PROVISIONAL, fitted to nothing
NEXUS_SUBSTRATE_CLOCK_REFRESH=off          # off | on — on is the rollback: re-promotion refreshes the decay clock (pre-AN.2)
NEXUS_CITATION_CAPTURE=off                 # off | on — on captures per-turn labelling rows (AO); off writes nothing
NEXUS_FIRE_CAPTURE=off                     # off | on — on captures one row per compaction fire and the next-turn check (CP); off writes nothing
NEXUS_REFERENCE_GATEWAY=off                # off | on — on judges, reduces and labels content from outside before it reaches the prompt (search results) and refuses an undeclared source at the store (W5); off is the request, the record and the store as before, byte for byte; any other value is off, and /cortex/stats.reference_gateway says so
NEXUS_CITATION_RULES=on                    # on | off — off is the F.5 citation test exactly (AU); the version on done/lineage/capture says which ran
NEXUS_PROMPT_RENDER=narrow                 # narrow | legacy — legacy is the rollback: the pre-AW dynamic block byte for byte; the record names which render it saw
NEXUS_CURIOSITY_PLACEMENT=idle             # idle | turn — turn is the rollback: curiosity called on every turn exactly as before AX.1; idle runs it on the KAIROS cycle
NEXUS_LEARNING=live                        # live | frozen — the freeze switch (FZ-2); live is the shipped default and the rollback literal NEXUS_LEARNING=live; frozen stops every automatic learning write at five store methods, declared on done and /cortex/stats
NEXUS_LEARNING_FROZEN_SINCE=               # YYYY-MM-DD the operator asserts when setting frozen; displayed as asserted; unset with frozen reads "frozen since: undeclared" (FZ-2)
NEXUS_HISTORY_WRAP=on                      # on | off — off is the rollback: the pre-A history path byte for byte (CHANGE A); the carrier is kept either way
NEXUS_MCP_OWNER=                           # the accountable identity on every MCP record, asserted never authenticated (MCP-2); unset = unattributed; still an unexpanded ${...} = unattributed too, and stderr says so (W2)
NEXUS_MCP_DATA_ROOT=                       # the MCP process's data folder, an absolute path (MCP-2; W2); kb.db, archive.db, chroma/, snapshots/, turn_metrics.jsonl and the three logs under it, a log's own variable winning; unset, empty, relative or an unexpanded ${...} = the per-user default (%LOCALAPPDATA%\NEXUS; ~/Library/Application Support/NEXUS; $XDG_DATA_HOME/nexus or ~/.local/share/nexus), never the working directory, said on stderr
NEXUS_DATA_ROOT=                           # the console's data directory (W1); unset = data/ under the working directory, exactly as before; set = kb.db, archive.db, chroma/, snapshots/, turn_metrics.jsonl and the three logs under it; start.py --data-root sets it
NEXUS_STORE=sqlite                         # sqlite | postgres (W3b): the store backend; unset or empty = sqlite, the files under the data root exactly as before; postgres needs NEXUS_DATABASE_URL; any other value is refused by name at startup, having opened nothing (start.py and MCP's main: exit 2; the console's factory run without start.py raises the refusal by name and leaves by its traceback, exit 1: BACKLOG W3b-D10)
NEXUS_DATABASE_URL=                        # under postgres: a postgresql:// URL naming the database that holds every table kb.db and archive.db hold (one database per store); the data root then keeps chroma/, snapshots/ and the logs; its secrets are never printed, recorded or put on a command line: every value libpq reads as one (the password in the userinfo or as ?password=, sslpassword, libpq 18's oauth_client_secret and SCRAM keys), raw and percent-decoded, is blanked on every line that names the store and in the driver's own error text (W3b close, F6); percent-encode @ / : ? # % & = and spaces in a password: a URL libpq would not read as it was written is refused by name before anything connects (start.py and MCP's main: exit 2; connect(); the backup's client tools) and no line shows any of it, only one fixed marker: an unencoded / or @ (F6b), and whatever the driver's own libpq refuses to parse, which NEXUS asks it (D5; nothing connects): a % that begins no percent-encoding, a raw space, an upper-case scheme, a parameter libpq does not know (a mistyped ?passwd=, a secret to no reading), a parameter with no = or two; the refusal names the class and none of libpq's text; a URL libpq reads is shown with libpq's secret values blanked and the rest as written, and where psycopg is not installed no part of any postgres URL is shown; another scheme is refused by name
NEXUS_MCP_HANDLE_TIMEOUT=600               # seconds a retrieval waits for its record before it counts as retrieved-without-record (MCP-2)
NEXUS_REPLY_SEPARATION=on                  # on | off — off is the rollback: the pre-C-38 reply path, content is the answer and nothing is parsed (C-38)
NEXUS_KEEP_REQUESTS=on                     # on | off — on keeps the exact text NEXUS sent the model so a decision can be re-run; off keeps none, and each record says so (W0b)
NEXUS_INFERENCE_SEED=on                    # on | off — off is the rollback: no seed sent, the pre-W0b request exactly; each record says why its seed is NULL (W0b)
NEXUS_ANCHOR=off                           # off | rfc3161 (W9) — off sends nothing; rfc3161 sends each signed block's 32-byte hash to an RFC 3161 authority on the idle cycle
NEXUS_ANCHOR_URL=                          # the authority's URL under rfc3161 (FreeTSA: https://freetsa.org/tsr)
NEXUS_SIGN_REQUESTS=off                    # off | on (W10b) — on signs each runtime action's request with the instance key (RFC 9421); off sends no signature header
NEXUS_ANSWER_RELEASE=after_record          # after_record | live (W6): after_record holds an answer's frames until its record is written, so no frame of an answer leaves the process without one (the answer itself still goes to the model server for extraction before the record); live is the rollback for the hold only: the answer's token frames go out as the model writes them; a record write that fails is declared (the record_failed frame, no done) in both; any value but live (in any case, spaces around it trimmed) is after_record, and /cortex/stats.recorder says so
NEXUS_AUTH_MODE=token                      # token | proxy (W4): token is the bearer gate exactly as before; proxy takes the login from NEXUS_IDENTITY_HEADER on a request from a loopback peer and answers only for NEXUS_OWNER_IDENTITY (the proxy authenticates the person, NEXUS does not; nothing is open but /health and /healthz; start.py adds --no-proxy-headers); any other value is refused by name at startup, having opened nothing (exit 2)
NEXUS_IDENTITY_HEADER=Tailscale-User-Login # proxy mode: the one header the login arrives in, exactly once and non-empty (Cf-Access-Authenticated-User-Email behind Cloudflare Access); a name no request could carry is refused at startup
NEXUS_OWNER_IDENTITY=                      # proxy mode: the login this process answers for, compared exactly; unset = every request is refused, 403 "owner not set", never adoption
NEXUS_INTERCEPT_UPSTREAM=                  # the interceptor (W10c, docs/INTERCEPTOR.md): the provider its exchanges are forwarded to, scheme, host, optional port and path prefix; required to serve (the commands that read a stopped store need none); plain http only to this machine
NEXUS_INTERCEPT_DATA_ROOT=                 # the interceptor's own folder (kb.db, archive.db, its key, its marker); required there; never the console's or MCP's root, and a folder holding a store it did not make is refused
NEXUS_INTERCEPT_OWNER=                     # the accountable identity on every intercepted record, asserted never authenticated; unset = unattributed; behind the identity proxy the proxy's login is recorded instead
NEXUS_INTERCEPT_INJECT=off                 # off | on — off is the shipped default and the rollback: a pure recorder, nothing added to a request, no memory engine loaded; on adds NEXUS's retrieval block as one delimited system block
NEXUS_INTERCEPT_PROXY_HEADERS=             # behind the identity proxy: the proxy's own headers, named, to keep from the provider (comma-separated; each exact, or a prefix ending in *); unset = guessed from the identity header's first word; the identity header and the forwarding headers stay here either way
```

**Four settings were removed from that block, not wired.** `DB_PATH`,
`ARCHIVE_DB` and `CHROMA_DIR` were documented and read by nothing —
`create_services()` takes its paths as arguments and `start.py` only wrote the
keys back into `.env` (until W1, which stopped `start.py` rewriting `.env`
at all). The console's one path setting is `NEXUS_DATA_ROOT`, a directory
for every console file, never a file name per store. `NUM_CTX` is not configuration at all: `NUM_CTX = 8192`
in `backend/providers/ollama.py` is Law 5's ceiling, enforced by
`stream_chat` raising above it, and making it read the environment would let a
`.env` line raise the cap the law exists to hold.

H.4's precedent decided this: when three declared SSE events turned out to
have no emitter, the contract was corrected to match reality rather than
emitters added to match the contract. A documented knob that does nothing is
the same defect, and the same answer applies — with the one difference that
`NUM_CTX` must stay unwired on purpose, which is why it is named here rather
than merely deleted. `tests/test_dotenv_settings_take_effect.py` holds all
four in `UNREAD_BY_BACKEND` and fails if any acquires a reader.

## SSE Events (backend emits → frontend handles)

```
turn_start            {turn}
model_routed          {model, capability, override_active}
                       # + reason | locked | fallback_used | note, by path (four emit sites)
triples_injected      [{s, r, o, w, g, tier}, …]
rag_injected          {source, n_results, results}
                       # results: [{title, url, snippet, engine}, …]; source is "web" (no kb producer exists)
                       # + gateway, only with NEXUS_REFERENCE_GATEWAY on (W5): {state, admitted, withheld,
                       #   by_rule, withheld_results}; n_results and results are then the admitted ones
prediction_generated  {expected_nodes, n_predicted}
token                 {t}
triples_extracted     [{subject, relation, object, confidence}, …]
prediction_error      {high_surprise, confirmed, missed, magnitude}
hebbian_update        {strengthened, weakened, formed, pruned, surprise_strengthened,
                       confirmed_strengthened, missed_weakened, pairing, protection}
contradiction_found   {subject, object, relation_a, relation_b, timestamp_a, timestamp_b,
                       source_a, source_b}
curiosity_surfaced    {node_id, score, question, epistemic_state, in_degree,
                       evidential_weight, confidence, confidence_deprecated}
compaction_fired      {tier, tokens_before, tokens_after, tokens_freed, items_removed, reason}
compaction_report     {pass_fail, violations, retention_rate, minority_loss_rate,
                       structural_summary, removed, removed_count}
                       # pass_fail is PASS | WARN (BD-3); removed is the full sorted
                       # list the vanished-concept rule computed, [] and 0 on PASS,
                       # never omitted; the same two fields ride that rule's
                       # violation, whose detail string is a five-name display slice (FC-0)
kairos_consolidation  {promoted}
chain_appended        {index, hash, sealed_count}
kb_pressure_alert     {type, action, ...type-specific fields}
                       # + domain, on every type but web_search
model_swap_starting   {from, to, capability, reason, fallback_used}
model_pull_proposal   {model, name, size_gb, improves, fallback}
eval_metric           {turn, answer_relevancy, faithfulness, context_precision,
                       composite, n_injected_triples}   # null = not computed (BB)
graph_activating      [{id, act}, …]
record_failed         {turn, reason, answer_released, claims_admitted, contract_version}
                       # W6: the record of this turn could not be written; the turn ends here with no
                       # `done`. reason is the error's class name. answer_released is false under the
                       # default release (the answer was held and is dropped) and true under `live`
done                  {turn, tokens, lineage_id, reinforcement, work_unit_id,
                       attribution_status, admitted_to_unit, unit_first_seen,
                       unit_closed, learning, contract_version, answer_chars,
                       reasoning_chars, answer_empty, extraction_input_chars,
                       extraction_input_truncated, extraction_gate}
                       # + unit_already_closed | unit_closed_at_time_of_turn | boundary_not_applied | unit_reopened | unit_not_closed | flags | answer_empty_reason, only when set
                       # + citation_forecast, only when the citation forecast ran (AF.1)
                       # learning = {state, since, since_source, display} on EVERY turn (FZ-2);
                       # reinforcement = {mode, citation_test, injected, cited, reinforced,
                       #   skipped, suppressed}: reinforced counts writes MADE (0 under frozen),
                       #   skipped the attempts the freeze refused (B-43)
                       # extraction_gate = what the ingest gate refused this turn and why (K.2, BA fix 6)
                       # contract_version = 4: done is a versioned contract, pinned three ways
                       # (tests/test_ba_fix2_request_hash.py, this note, INTEGRATION_REFERENCE);
                       # 1 = learning (FZ-2), 2 = reinforcement.skipped (B-43),
                       # 3 = the reply separated (C-38): answer_chars, reasoning_chars (null when
                       #   the reply carried none), answer_empty (+ answer_empty_reason when set),
                       #   extraction_input_chars and extraction_input_truncated (the window declared),
                       # 4 = the unit's reversal (W6): unit_reopened and unit_not_closed, only when set
```

**The block's grammar** (read by `tests/test_w1_sse_payloads.py`, W1). An
event's line declares an object payload `{keys}` or an array of objects
`[{keys}, …]`, and the keys on it are the ones every emit site sends. A
`# +` comment lists the keys only some sites send, separated by `|`; the
group ends at its first comma, and a comment line that starts with `# |`
continues it. A `# |` line that continues nothing — with no `# +` group
above it, or after one that has already ended at its comma — is a grammar
error the test names, never prose. `...type-specific fields` marks a
payload open. Each event is declared once. Only top-level keys are
reconciled with the code: the nested shapes and the prose qualifiers in
the comments are not checked.

**22 event types.** Declared here means: emitted by the backend on some
reachable path, and bound by a handler in at least one frontend. `eval_metric`
and `graph_activating` were emitted and handled from the start but never
written down — H.4 added them rather than marking them internal, because an
undeclared event is one no client is obliged to handle and no test covers,
which is exactly how `hebbian`/`hebbian_update` drifted apart.

Enforced by `tests/test_sse_contract.py`, which reconciles this list against
what the code can actually emit and what the frontends bind. **Reachability,
not grep:** `chain_appended` and `kairos_consolidation` reach a client only
via KAIROS's queue and the `BRIDGE_EVENTS` allow-list in `agentic_loop.py`,
never through a direct `sse_event(...)` call, so an emit-site search draws the
boundary wrong and passes while being wrong.

**`kb_pressure_alert` valid types:**
- `gap` — propagation density < 0.15, KB seeding proposed
- `staleness` — domain volatility × age × frequency exceeds threshold
- `dilution` — cold triples crowding active retrieval
- `semantic_duplicates` — near-duplicate triples found, user review required
- `cortex_drift` — fingerprint distance >= warn or alert threshold
- `web_search` — temporal/gap signal triggered, search proposed (Law 3); `action: not_recorded` when the
  consented search's action could not be recorded and it was not sent, or its result was withheld (W10b)
- `assertion` — the user stated something that could become a belief, offered
  for confirmation (Law 3). O.2b built the detector and `POST
  /kb/assert/confirm` and built no offer, so the feature was reachable only by
  someone who had read the source; S.4 is the offer. The payload declares what
  *would* be stored, including `shaped: false` when the sentence cannot be
  turned into a triple — an operator learning their claim is unstorable is
  more useful than silence.

**20 of the 22 declared SSE events are bound by a frontend handler. Two are
not: `rag_injected` and `model_pull_proposal`.**

This previously read *"All 19 SSE events update correct panel in real time"*,
which was false three ways — three declared events were never emitted, two
declared events had no handler anywhere, and three handled events were not in
the contract. A constitution asserting something false is worse than one that
is silent, so the claim is now exactly what is true, including the part that
is unflattering. `tests/test_sse_contract.py` fails if any of it stops being
true, and the two-event gap is pinned as an explicit exception that fails when
it **grows** — and also when it shrinks, so closing it is a deliberate act.

Two distinctions the wording is careful about, because the old claim collapsed
them:
- *Bound* is not *renders*. `hebbian_update` is bound and deliberately a
  no-op. Asserting "updates the correct panel" needs a rendering test, which
  does not exist — do not restore that wording without one.
- *Bound somewhere* is not *bound in the main UI*. `kairos_consolidation` is
  handled only in `nexus_memory.html`.

## The record's contracts

**A unit's close is a governance record, and it survives its reversal (W6 part 3, tier 1).** Before W6
`decision_boundary: close` set `closed_at` and `closed_by` on the unit's row and nothing else: no record of the act,
nothing listing the unit's lineage records, nothing sealed, nothing that could reverse it. Now each close appends
one row to `work_unit_events`, in the same transaction as the row's own change (both, or on any error neither): the
unit, the kind, the time, the actor fields and the accountable identity as the request asserted them, and
`lineage_ids`, the ids of the unit's lineage records at the act, oldest first (ids, never copies). **The act is made
after the turn's record is written**: it names that record (`asserted_on`), a close's list includes it, and it
carries how the turn's identity was established (`identity_mechanism`: `asserted`, stored NULL, or `proxy`, sealed
as on the record). A turn whose record cannot be written makes no act. The act, and the extractor's stated
confidences, are made right after the record and before any frame of the held answer is released, so a client
that stops reading during the release leaves the record with its act; an act that cannot be written is declared
on `done` (`boundary_not_applied: write_failed`) and logged (`work-unit boundary act failed`), never raised; a
client that stops reading before `done` misses the declaration, and the log still holds it.
`decision_boundary: reopen` is the reversal, asserted exactly as `close` is (body only, declared on `done` and on
the record's `boundary_asserted`, never refused): it clears the row's `closed_at` and `closed_by` and appends a
`reopen` act that names the close it reverses. **The close event is never amended**: it is the same row, byte for
byte, before and after its reversal, and it still verifies against the block that sealed it. The ledger is sealed
by riding the governance store's enumeration, as the archival ledger does (`unsealed()`, `mark_sealed()`,
`verify_governance_records()`, six tables now), so it is sealed on the governance cadence and an edited act is
named; the acts read in the order they were appended (an insertion ordinal, never the time), and the id list only
in the writer's own form. The verifier now also reads block to row, for all six governance tables: every hash a
block sealed must still be some row's, so a sealed act deleted, or rewritten with a recomputed hash and its seal
cleared, is named (`sealed_hash_without_row`; W6-B4 closed). `WorkUnitStore.verify_events()` checks the ledger
against the unit rows and against itself (a reopen names a close of its own unit; a unit whose last act is a close
is closed; a closed unit with no act after the ledger began is named). A backup copies kb.db and archive.db as a
consistent pair: when the archive's head moved across the two copies (a seal landed between them, sealing an act
the copy of kb.db lacks), it copies both again, up to five times, and otherwise fails by name (exit 6) and
leaves no folder, so a store restored from it verifies; a row a seal had not yet marked reads unsealed there and
is sealed again. **Not seen**: an act inserted, deleted or rewritten before its first seal in a way that
leaves the row and the references consistent; and, on the lineage record, the
same rewrite with its seal cleared (`verify_sealed_lineage` reads row to block only; BACKLOG W6-B6, for W8). A reopen of a unit that is not closed is declared (`unit_not_closed`) and writes nothing; `done` is
contract version 4 for the two declarations. `GET /work-units/events` is the read: gated, declared per-person in
all three places, and deliberately **not** on the governance router: a surface there is an authority surface,
where the just-culture guard allows an aggregate by person, and "units closed, by whom" is an aggregate of work by
the person doing it. **What a close event claims, and no more**: this actor asserted the boundary at this time,
and these were the unit's record ids then. Not that the work was finished; and the actor is asserted, labelled
per row by its identity mechanism, never verified by NEXUS. `tests/test_w6_close_record.py` holds it on both
backends, with a source scan that names an UPDATE or a DELETE spelled in one string constant, and a check of the
statements as they run on the paths it drives (every name resolved), which names anything but the writer's INSERT,
the seal's UPDATE of `sealed_block` and a read; neither sees a path no test drives. Ruling 6 covers `reopen`: the worklist's exit needs a reversal to
survive, and none existed.

**Content from outside passes a reference gateway, switched off by default; it reduces and measures (W5, tier 2).**
The establish found two flows from outside the runtime and nothing joining them. A consented search's results
were appended to the dynamic block as free text (the engine's name, the title, the whole url, 160 characters of
snippet): nothing judged them, because the gate judges triple terms, and no line carried a class, so a snippet
could tell the model what to do, close the runtime's own `<nexus-block>` tag, or carry a line that reads exactly
like an asserted KB line. Seeded rows (`wikidata`, `conceptnet`) reached the store through the gate's universal
rules and the prompt as KB lines labelled with their class. No route ingests a document. **The policy is data**:
`backend/kb/source_policy.py` holds one row per source (its declared class, asserted equal to `SOURCE_CLASS` by
test, its provenance, whether it may be stored, how it may reach the prompt), and `docs/REFERENCE_GATEWAY.md`
states that table as an information-flow rule, compared with the code row for row. **`NEXUS_REFERENCE_GATEWAY=off`
is the request, the record and the store as before, byte for byte**: three request hashes captured on the base
before any gateway code, through `/chat`, with a web block and a seeded external row
(`tests/_w5_gateway_pin.json`); only the literal `on` is on, and any other value is named on
`/cortex/stats.reference_gateway` and treated as off. **On:** `backend/loop/reference_gateway.py` is the one place
search results become prompt text (the loop hands it the results and reads no field itself; the off render is the
loop's old lines moved there unchanged). A result's address is cut to its host, its engine's name kept only as one
run of at most sixteen letters and digits once lower-cased (`google scholar` and `brave.images` read `unknown`),
its title cut at the gate's term length, each field folded to one line, and its title and
snippet judged by three rules: the gate's own `imperative_clause` (markers `ba5.1`), the gate's own markup
pattern, and `runtime_grammar` (the label's keys `src=` and `via=`, and the renderer's section headers, from the
renderer's constants). A result that breaks one is withheld whole, counted by rule and declared on the
`rag_injected` frame's `gateway` key. Web lines then end `[src=external via=web_search:<engine> host=<host>]`, and
under the narrow render KB and ASSOCIATIONS lines gain `via=<source>`; a cortex line keeps the class only (a concept
has no single source), and under `NEXUS_PROMPT_RENDER=legacy` the stored rows' lines carry no label at all. The
render has its own name on the record (`wg.1-narrow-src-via`; `legacy+wg.1-web` under the legacy render), and
`derive_block()` follows the first. At the store,
`TripleStore.add()` refuses by name a source the policy does not declare or does not permit (`web_search`), before
the gate; off, an undeclared source is warned about and stored as before. **The gateway holds no word list of its
own**, by a census of its strings and imports with planted companions. Two of its three rules are the gate's,
imported, written and measured in BA before this item's test set existed, so nothing in them was tuned on that
set. The third, `runtime_grammar`, is the gateway's own: it was written after the set's label seeds were
registered, and wider than first worded, and the document marks the figures it decides (without it, 221 of
344 plants would reach the prompt on, where 201 do). No sentence in the prompt asks
the model to ignore anything. **The measurement was registered first**: `tests/fixtures/W5_injection_set.json` (its sha256 pinned;
52 planted instructions in two lists and six shapes, 40 legitimate results and 30 legitimate labels, and an
adaptive arm's operators, order and budget) was pushed before any gateway code. `tests/fixtures/w5_gateway_harness.py`
plants each case through a real turn or a real seed route and reads the request at the turn's crossing point, which
a test compares with what a whole turn hands the provider. No model reads anything: "reached the prompt" says what
a model would be handed. On the second list, run after the build and again on the final tree with the same
figures: of 344 plants in search results, 341
reached the prompt off and 201 on; every instruction that opens with one of the gate's words as a whole title or
snippet was withheld (18 of 18), and the same hidden mid-sentence was not (27 of 27 reached); nothing planted in
an address's path or an engine's name reached (0 of 52); every line carrying the runtime's label was withheld (4
of 4, by the rule shaped after registration). **The cost and the limit are printed beside it**: 20 of 20
legitimate results that open with one of the gate's words are withheld on, and a search over a fixed list of
rewrites got 10 of the 12 withheld instructions through, at a median of one try (one polite word in front), and
12 of 12 with the label rule as first worded. The store's column is the same off and on, because
the rule there is the gate's in both. The suite prints list A's table and asserts only what must hold whatever
the rates are; a rate is a finding, not a pin. **Not built, and said:** the title's other reading, the source of
a request's attribution (the identity proxy's mode, built, and BACKLOG W4-B8; Ruling 5); a document-ingest route; any rule about
intent. The copy says "reduces and measures", and a test holds the stronger words (`FORBIDDEN_WORDS` in
`tests/test_w5_reference_gateway.py`) out of the two modules, the document, this paragraph, the changelog entry and
the example env's block.

**A record can be proved to a stranger, field by field, and disposed of without breaking its proof (W8, tier 1).**
`backend/memory/merkle.py` is RFC 6962's tree (leaf prefix 0x00, node 0x01, RFC 9162's verification); a Merkle
path does not prove the tree's size, so the size is sealed beside each root. **A block appended since W8 is layout
2**: its hash is over a header (index, time, previous hash, snapshot fields, kind, `fingerprint_hash`, and the
seals' roots `{name: {root, size}}`); every block before W8 is layout 1, recomputes and verifies unchanged, and has
a derived root, never stored back. **Each record's hashed fields are committed one by one**
(`backend/memory/commitments.py`): a 32-byte salt per field, `C_f = SHA-256(salt || canonical(value))`, the
commitment root over `name || 0x00 || C_f` in field order; the salts are kept beside the record (`record_salts`),
never in it or its hash, written in the record's own transaction, backfilled at open for earlier records. The
record's leaf in its seal's lineage root is `record_hash || commitment_root`. **A record whose seal claims no root
(sealed before W8, or as a bare hash) is bound by the next seal** (`binding_seal`, the header's `binding` root, at
most `BINDING_BATCH` a seal), and only to a root its values and 32-byte salts rebuild (`root_rebuilds`): the binding
claims that this instance bound these commitments to that hash at that block's time, never that the record held
these values when first sealed. `GET /lineage/{id}/proof` (gated: fourteen gated reads then, sixteen with W9's two) returns a proof that
`proofs.verify_proof` checks from itself alone (it imports `hashlib`, `json` and the tree only, for W9's
standalone verifier); `verify_sealed_lineage` gains a fourth layer that reads every sealed record's proof, counts
the bound and the unbound, names a stored root that does not rebuild, and reads block to row every lineage hash,
every layout-2 leaf (`leaf_mismatch` when its root is not the store's) and every binding leaf (W6-B6 closed).
**A tombstone**
(`LineageStore.tombstone`, no route: when and who confirms is W14's) keeps the record's id, time, learning mode,
injected set, hash, commitment root, every `C_f` and the kept fields' salts; every other hashed field holds the
mark `tombstoned` (NOT NULL) or NULL, each by its own spelled statement (a new hashed field with none is refused
by name), its salt, `context_json` and an unshared request body go; the record then verifies by its kept openings,
its root and its leaf, never by its hash. The act is a `record_tombstones` row, sealed with the governance tables
(seven now). Refused by name, writing nothing: no record, already tombstoned, a record that does not verify (its
fields, its seal's root, its binding or its salts), no commitments, no reason, unsealed, its block missing, and a
seal that claims no root and no binding yet. A backup copies the stores as W6's consistent pair, so a restored store's fourth
layer reads as the store's did. Credentials and a signed chain are W9's (next paragraph).

**A decision credential a stranger verifies offline (W9, tier 1).** Each instance has one Ed25519 key, made at
first use as `instance_key.json` beside `kb.db` (owner-only, created exclusively, with a fallback where the
filesystem refuses hard links), refused by name and never replaced when it does not read. A backup never copies
it: a restore makes a new key and records the rotation, and `GET /identity` (open) lists every key the instance
has used with its period as a block range (`announced_in`, `until_block`; each key announced in the chain by an
`identity_seal` block), so a credential signed under an earlier key verifies in that key's period; a period other than the current key's
ends where the next key was made (the archive's next index then, kept in its key file and its announcement), so
one appended unsigned never widens it and a restore keeps every one its archive holds, and a new key is listed
`pending` until the next block announces it; a block sealed after the backup a restore came from is outside the
old key's range, holding only for whoever received and checked its credential before the restore or holds an anchor
dating it before the restore (Ruling 7). Every block
appended is signed over `NEXUS-BLOCK-V1\x00 || index || block_hash`, in columns outside the hash (every earlier
block recomputes unchanged; a signing failure appends the block unsigned and is counted). `GET
/lineage/{id}/credential?disclose=` (gated; sixteen gated reads) issues one: the named fields with their values
and salts, every other field as its W8 commitment only, the proof, the block's header and signature (a block
with no signature of its own is signed at issue by the current key and says so), the keys, the scope declaration and the decision fields; the
record hash rides the proof's leaf (W8), so the lineage id is named only when disclosed: its random bits hide the
undisclosed fields together from whoever lacks the id, and with the id (disclosed, or read from a `done` frame)
small undisclosed fields can be guessed together. A tombstoned record discloses only what it kept (409 `dropped`).
`scripts/nexus_verify.py` checks it with no store and no NEXUS import (stdlib only: RFC 8032, RFC 9162, the
commitments, RFC 3161 tokens under RSA and ECDSA), exit 0 `VERIFIED` or 1 with each failure named
(`disclosure_mismatch`, `unsalted_hash`, `signature_invalid`, `signature_outside_key_period`, …; a run pinned to no
key fails `key_unpinned`); `release.yml`
packages it on Linux, Windows and macOS. **What it proves:** this instance's key sealed these commitments into
this block and the disclosed values open them. Never that the content is true, and never when: a signature
proves who sealed. **Commit now, reveal later:** `POST /commit` and `nexus_commit` (MCP contract 4) require
`confirmed=true`, never defaulted (unconfirmed: the offer, the refusal recorded by digest); confirmed, the note
and its salt stay in `held_commitments` (no prompt, retrieval or record), the salt is handed back once, the next
seal seals `SHA-256(salt || canonical(note))` as a leaf of the block's `commit` root, and `GET
/commitments/{id}/receipt` (gated) with `nexus-verify --reveal` checks a later disclosure exactly, CRLF kept.
**Dating** is anchoring: `NEXUS_ANCHOR=off` by default; `rfc3161` sends each signed block's 32-byte hash and
nothing else to `NEXUS_ANCHOR_URL` (any RFC 3161 authority) on the console's idle cycle (MCP mode never anchors:
its housekeeping tick runs the seal alone), keeps the token in
`block_anchors`, and a credential carries it for `nexus-verify --tsa-cert`; the live FreeTSA test runs only under
`NEXUS_ANCHOR_LIVE_TEST=freetsa`. The simulated UAT's verification section drives all of it from outside; its one
act off the entry points is the tombstone (`tests/_uat_tombstone.py`: no route until W14, W8-B3).

**Actions are recorded beside the answers (W10b, tier 1).** What NEXUS's own runtime sends on a person's confirmed
behalf (the web search, from a consented turn or `POST /search/execute`, and each request the seeder makes) is a
governed action, one row in `action_records`, written by `backend/memory/actions.py`'s `recorded_get`, the one
place such a request is sent: the row is committed before the request leaves, or nothing is sent; its outcome (the
status and the SHA-256 of the body received) is written before the result is used, or the result is withheld.
Either failure is said by name (`kb_pressure_alert web_search not_recorded` in the turn, `503 action_not_recorded`
on a route), never retried behind the caller's back. A key header is recorded by name and fingerprint, never its
value. A turn mints its lineage id at its start, so its search names the record it will carry, and
`/lineage/{id}` lists the turn's actions. Model calls, probes and anchoring are not actions, declared in the spec's
establish. The recorder owns the network: the search tool and the seeder import no network library and send
through its `session()`. Two guards hold it, each declaring what it cannot see. Statically, every use of
something that can reach outside the process (a socket library or a child process, under any alias, through a
re-export, or held by a local name) is counted per function and answered for, and the two modules take nothing
from outside themselves but names pinned one by one, read no private attribute and load no code by name. At run
time, the console driven through every route, a search turn and two anchoring cycles reaches outside the process
only by an answered (host, function) pair, every httpx request and audited socket or process attributed by
stack. Neither sees a path no driver reaches, nor a connection a C library opens without an audit event. Each action
is a leaf of its sealing block's `action` root; `GET /actions/{id}` (gated, declared per-person; seventeen gated
reads) is its receipt and `nexus-verify --action` checks it offline. Its accountable identity enters the hash as a
salted commitment, disclosed only by that gated receipt. An MCP client reports what it did with
`nexus_record_action` (contract 5; the instructions ask for it): stored `client_reported`, never counted as
runtime, and the coverage block says NEXUS sees only the actions it is told. `NEXUS_SIGN_REQUESTS=off` by
default; `on` signs each runtime request under RFC 9421 with the instance key (held to the RFC's ed25519 vector by
test; accepted by the published `http-message-signatures` 3.0.0 in the run's interop check, a
record in the development record's log, not a test), and the recipient of a signed request asks
`GET /identity/signed-action?signature=` (open; Ruling 8) for the sealed action behind it, which names no person.
The simulated UAT's actions section drives it from outside.

**NEXUS attributes cognition to work units; it never sequences, queues or
moves them (v3.1 Phase 1; the spec's Law XII); attribution is asserted by
the caller and sealed; it is never inferred and never confirmed (1b).** A
work unit is the application's unit of value — a claim, a contract, a
ticket — and it is treated exactly as `scope` is: an opaque, caller-supplied
label on each request, body only (`work_unit`, `work_unit_type`,
`accountable`, `decision_boundary`, and the actor fields `actor_id`,
`actor_role`, `actor_source` under the outcome route's vocabulary), the same
length cap and whitespace rule, refused in scope's position, recorded
literally. There is no header channel, no current unit on the loop state,
no one-open-unit rule, no restart re-read, no open/close route and no
governed act — attaching a label to a record changes no standing, and 1a's
confirm-gated open beside a first-mention path was a gate on one of two
doors. The lineage record carries `work_unit_id`, `accountable_identity`
(the request's, per record — accountability is a field in force and the
records are its history), `boundary_asserted` and `boundary_asserted_by`,
each hashed only when stated so every earlier record recomputes unchanged;
history is unattributed by absence, deliberately — no migration, no
historical pseudo-unit. A turn that names nothing is attributed to the
session pseudo-unit `session:<id>`, `unattributed`, created lazily and
counted apart (E.6: one row per session id; recorded on the register).
The unit row has exactly two writes: creation on first mention, with the
request's actor fields (never a literal such as `request`), and
`decision_boundary: close` on a request naming the unit, which sets
`closed_at` once; a second close, a turn naming a closed unit and a close
with no `accountable` are each declared on `done` and the record, never
refused. (Since W6's part 3 the row has a third write: `reopen` clears
`closed_at`, and each close and reopen is an act in a sealed ledger; see
"A unit's close is a governance record", above.) Attribution of claims is a **junction**,
`triple_work_unit(triple_id, work_unit_id, first_seen_turn, lineage_id)`,
written beside every `add()` during a turn's extraction and during the
assertion and seed routes (which take `work_unit` on the request too) —
admission, not use: `add()` upserts on the claim key, so a column on
`triples` would record the first unit and be silent on re-assertion (BC),
and writing at citation would bake the flag's ~22% under-report into
attribution. The junction is append-only by AST guard; the claim row is
untouched; archived claims keep their rows and the unit's claim list
carries the `archived` flag. `add()` stays the single chokepoint:
`TripleStore.admissions()` collects the ids it returns inside a scope and
nothing else. The retrieval boost is Phase 4 (measured first); the
compaction boundary rule and event-wide `work_unit_id` are deferred on
their own register lines; scope and work unit may be one concept and are
not merged (register line). Every new column is answered for in
`tests/test_scope_recorded.py` with this packet's name.

**Advice recall — the first backward read of the record (AR).** Given a
claim, every sealed answer that rested on it: `GET /belief/{belief_id}/recall`
(paginated, oldest first, contract version 2), built from the **injected**
set and never the cited flag — the flag under-reports by about a fifth,
measured, and a recall that misses affected answers is wrong in the unsafe
direction. Keyed on the claim **id**: `add()` returns the same id across
the monotone upsert and archival leaves it unchanged (S1.2, measured), so
the key is not needed. Document and revision keying is ingestion's job and
is not built. Supersede is a **reason on the existing outcome act**, not a
new act kind: `POST /outcome/confirm` with `notes` beginning
`superseded_by:` returns and records `recall_count` and `recall_route` on
the outcome row, hashed only when present so every earlier row recomputes
unchanged; standing moves only as the envelope already allows (on the
shipped `v0-immovable` envelope it does not move, and the count is still
recorded and returned). The backward index `lineage_injections` is
derived, written in the record's own transaction and backfilled once at
open (a derived write to `kb.db` on the first open after this commit;
`verify_recall_index` names any divergence either way); built because the
scan's p95 measured 201.5 ms on 100,000 records against a 200 ms rule
fixed before measuring. `/cortex/stats.recall` is a count derived from the
record on every read — never a stored number, never a rate.

**Classification and the just-culture line.** The recall route is
**gated** like every `/lineage/*` and `/belief/*` evidence read, and it is
the first entry in the ratchet's `PER_PERSON_ROWS`. The entry condition,
narrowed (A-2): *NEXUS never aggregates by person. A surface that returns
per-person rows declares that it does, in its payload note, in `docs/CONTRACTS.md` (until W11c, in the project's instructions),
and in its ratchet classification.* The recall route returns `accountable`
per row, labelled by the record's `identity_mechanism` (W4; version 1
printed `asserted` on every row): `asserted`, recorded and never
authenticated, or `proxy`, taken from a loopback identity proxy's header,
the proxy having authenticated the person and NEXUS not; nothing in the
system could label it `verified`. It carries that declaration in all three
places. Two derived guards hold the line
(`tests/test_just_culture_guard.py`): the person-field population is
derived — every DDL column ending `_by`, every actor-vocabulary field as
`actor_<field>`, every column or request field beginning `accountable`
(eight members today, printed) — and no exposed surface may group, count,
rank or order by one, in SQL-string or Python form, over every route
endpoint plus one resolved call level; and every GET whose response
carries a member must carry the three declarations, found by calling the
routes on a populated fixture. **The work/authority clause (B-40, ruled
by Fable, 30 September), verbatim:** *"Aggregates of cognition, turns,
citations or units by the person doing the work are forbidden. Governed
acts are attributable to their actor by construction; surfaces over them
are declared per-person and gated."* So `/governance/budget` stays per
actor — a steward's bound on governed adjustments is accountability by
design — and is declared per-person; `/governance/envelope`, whose
history names the actor who installed each policy, moved from an open
read to **gated** and declared; both are entries in `PER_PERSON_ROWS` and
carry the declaration on their payload (`per_person`). The guard's
exemption is **derived, not listed**: a surface is an *authority surface*
iff it is served by the governance router (the `APIRouter` the app
exposes as `app.state.governance_router` — the object, never a path
string), gated with the token dependency, and declared per-person; a
person-field aggregate is allowed there and forbidden everywhere else,
and no route path appears in the rule (the guard's own source is checked
for both by AST). The set derives to exactly those two today, printed.
Three companions plant the rule's edges — a work aggregate off the
router, gated and declared; an undeclared route on the router; a declared
but open route on the router — and each is named. **The edge the rule
cannot see is pinned as `xfail`:** a route on the governance router,
gated and declared, that aggregates `lineage_records` by `accountable` is
a work aggregate dressed as an authority surface and passes the router
rule; deriving authority from the tables a route reads (the governance
tables, FZ-2's confirmed-act set, against lineage, triples and work
units) is the structural fix, on its own register line. Pre-existing
per-person reads still exempt from the three declarations, each pinned
so the exemption cannot outlive the hit: `/lineage/{id}`,
`/lineage/recent`, `/belief/{id}/provenance`, `/governance/refusals`. The
guards prove *not computed here*; they do not prove *not derivable*: a
consumer can group the recall's rows in one line, and the declaration is
what makes that a choice made in the open.

**As-of on transaction time (AS-1).** `GET /kb/as-of?date=` is what the
store held at an instant: a filter over timestamps the store already
records, **gated** like every evidence read (A-2). The predicate, verbatim
on the surface: *a claim is held at D if it was first admitted on or before
D, and, reading its archival events up to D in order, its last event is not
an archival; a claim whose supersede act is dated on or before D is held
and superseded.* It reads `triples.created_at` (the first admission — the
monotone upsert's SET list does not name it, measured), `archival_events`
one row per belief per act in `(ts, rowid)` order (with `triples.archived_at`
as the fallback for a row that has no event at all: a pre-ledger archival,
or one made with the ledger off), and the sealed supersede selector
`recall_count IS NOT NULL` (A-3), never `notes`. **No standing at D**
(A-1): `weight` is a monotone MAX with no history, `tier` is overwritten by
`rebalance` with no event, `fire_count` is a counter with no event row; the
two tables that carry a weight or a tier beside a timestamp
(`recalibration_events.stored_weight`, the lineage record's
`weight_at_injection` / `tier_at_injection`) are sampled observations, not
change histories — AS-2 establishes whether standing is re-derivable. The
fixed sentence (shortened by AS-1b, below) rides every response.
**D must be an instant with an offset (AS-1b, ruled by
Fable, option b).** Every timestamp the store writes is a `time.time()`
epoch second with no zone stored. A bare date read as end-of-day UTC was an
honest, declared convention that answered a question the investigator did
not ask — eight hours off for an operator in UTC+8, and a zone setting breaks
the moment two sites share an instance — so a bare date (`YYYY-MM-DD`) is
**refused by name** (422, `refused: bare_date`, a reason and an example),
and so is a naive instant with no offset and no `Z` (`refused:
naive_instant`: the same convention in another spelling, Opus's amendment,
accepted). The conversion belongs in the client, where the zone is known;
recorded as a register line for whenever the console gains a date input.
The fixed sentence, shortened, rides every response: *Transaction time
only: when NEXUS admitted, archived or superseded a claim. Valid time is
not modelled. No standing at the date is shown, because standing has no
timed history yet. D must be an instant with an offset; timestamps are
stored as UTC epoch seconds.* Then the ledger sentence: *No archival event
is recorded before `<archival_ledger_from>`; a row archived before that by
the column path and unarchived after it leaves no event and is not
visible to as-of.* `archival_ledger_from` is the earliest
`archival_events.ts` — the ledger's first recorded act, **not its
beginning**: the table may have existed, empty, before it, and the record
does not say when it was created; on a store with no event it is `null`
with `archival_ledger_reason: "no archival events recorded"`, never blank
(the from-date pattern AS-2 will need). It rides the response and
`/cortex/stats.as_of`. Ordered by admitted-at then claim key, paginated,
`contract_version: 2` (1: AS-1; 2: the refusals and the ledger from-date),
pinned three ways; a date before the earliest admission is empty with a
declared reason, never a blank. `/cortex/stats.as_of` also carries the
earliest admission, the archival event count and the supersede act count,
derived on every read. Not a per-person surface: no row carries a member of
the derived population. Prominence, decay or any weight at D is AS-2's
question and appears nowhere here; `tests/test_as_of.py` derives the
projection closure from `tiered_store` and fails on any reach.

**The interceptor: an exchange recorded between a harness and its provider (W10c, tier 1).** `backend/intercept/`
(`python -m backend.intercept`, the binary `talima-intercept`; `docs/INTERCEPTOR.md`) is a standalone recorder: an
agent harness is pointed at it as its model endpoint (Anthropic Messages on `/v1/messages`, OpenAI-compatible Chat
Completions on `/v1/chat/completions`), and every exchange passes through on its way to the configured upstream.
**Standalone is held from the import graph**: `tests/test_w10c_standalone.py` reads every import that can run in
the tracked backend (function bodies and literal dynamic imports included) and fails if the interceptor's reach
holds anything outside the standalone set (the record, the seal, the chain, the store, the lock and four helpers
beside them: 26 modules, each with its reason), or uses a way to load code the graph cannot follow: the name
`importlib` in any use but one (`importlib.import_module` with one absolute literal), a star import, a string run
as code, `sys` under another name, its table of loaded modules and its lists of importers wherever they are read
from, a table of names or a module's own loader, the name of any of those attributes written as a string, and a
list of other loaders. It is a reading of source and it is not complete: it refuses the names, attributes and
strings it lists, as they are written; a name put together at run time is not seen, and what it does not see is for the other
two readings: a fresh interpreter that runs an exchange and is read for what it loaded, and the release job's
reading of the binary's own archive against the same set. The engine it must not reach is
derived (what the console reaches and that set does not hold), and companions plant each refused form. To get there the record's own tables, the
capture switches and the governance seal moved out of the knowledge store's schema, the providers and KAIROS
(`backend/memory/record_schema.py`, `capture_settings.py`, `seal.py`), each under its old name too and pinned
unchanged. **The request is recorded before it is sent**: its row (a runtime action, kind `model_exchange`), the
request's bytes and the outcome of each earlier tool use it reports commit in one transaction, or the harness gets
`talima_request_not_recorded` and the upstream is never called. **The response is relayed as it arrives and
recorded when complete**: the lineage record (scope `intercepted`), a row for each tool use the response asks for
and the exchange's outcome commit in one transaction; a stream's terminal event is held until then, and when the
write fails the harness gets an error in its place and the exchange reads `incomplete` (a new state). Bytes already
relayed are not recalled. **The scope `intercepted`** (the LS mechanism): `request_hash` over the bytes the harness
sent, `response_hash` over the bytes the upstream returned, `model_id` `upstream-reported:<name>` (the provider's
claim), the capture fields NULL with their reason, `learning_mode` always `frozen`; every field is classified in
`backend/mcp/scope.py`, with a verifier. **Tool use**: each tool use the model asks for is an action of a third
evidence class, `intercepted`, and the result the harness sends back is linked to it as reported, by the ask
the harness's own request repeats before the result (id, name and input, numbers by value), never by the id alone:
asks under one id in the order they were made; a result nothing of the assistant's follows, or the last a request
holds for its ask; to the tool use asked last that still waits under it; and what that cannot tell apart (two tool
uses with one id, name and input) is listed on the interceptor's page. A tool use still waiting for its report has
no seal of its own, and the sealed record of its exchange names it, which `verify` holds the rows to; an action
with its outcome is read both ways, from the row to the block that sealed it and from every sealed leaf to the row
the store holds now. The class says what was seen, never that the tool ran. Coverage: complete for every exchange that passed through the endpoint; a
harness pointed elsewhere is not visible; tools are recorded as requested and as reported, not as executed.
Credentials pass through and are stored by header name and fingerprint only; a request's `Accept-Encoding` is
narrowed to the encodings the interceptor reads, and nothing else of a request is changed. It listens on an
address, never a name, and on a loopback one unless it is behind the identity proxy, where the proxy's headers stay here (the identity header, the forwarding headers, and its others as `NEXUS_INTERCEPT_PROXY_HEADERS` names them or, unset, as guessed from the identity header's first word); it keeps its own store (a folder holding a store it did not make is refused) and seals
every five seconds and at shutdown. `NEXUS_INTERCEPT_INJECT=on`, off by default, is the one optional link to the
memory engine (NEXUS's retrieval block added as one delimited system block), loaded by name by a function that
refuses a configuration in which injection is not on, and left out of the standalone reach by rule. The project's suite calls no provider: a stub upstream speaking both protocols is its
truth, and `scripts/intercept_smoke.py` is the check an operator runs with their own key.

## Not built yet

What this release does not do, each named in plain words, with no id of the development record. A statement in a
page README.md points to that marks something not built, planned, queued, intended or drafted names no item id
(`tests/test_w11_documents.py` holds that, for those marks; it does not read other words, such as "deferred" or
"until"). The record's contracts above were written while the work went on, and name later work by its ids (W14,
AS-2, Phase 4) where they say what a contract does not yet cover.

- **The remote connector**: NEXUS on a server, reached by a desktop app over the network (MCP over streamable HTTP,
  behind OAuth). MCP is stdio only.
- **The work-unit ledger**: a ledger of each work unit's cognition, reconciled with the record and never aggregated
  by person.
- **The unfreeze decision**: the fix for consolidation's recurrence defect, measured before and after, and the
  operator's procedure for deciding whether an instance's learning is switched back on. This release unfreezes no
  instance.
- **Retiring the legacy prompt render**: whether the earlier prompt render (`NEXUS_PROMPT_RENDER=legacy`, kept today
  as the rollback) is retired, and where what it still carries goes.
- **Retention by policy, and what follows it**: disposal by policy under the tombstone rule (a disposed record keeps
  its cited set, its time and its learning mode), the acknowledged state and triage of contradictions, the
  work-unit retrieval boost measured against the ledger, and fidelity redefined over what compaction drops.
- **A chain document served by the console**: the console and the hosted server have no route that gives their
  chain as the one document `docs/TALIMA_SPEC.md` (section 11) defines. The interceptor writes its own
  (`talima-intercept chain`), and `backend/memory/credentials.py` makes one from any store.
