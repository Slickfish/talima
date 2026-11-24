# NEXUS Integration Reference

*Derived from the running router at branch tip `a266cae`. Every path, field, status code and response key below was read from the live application — the route table was enumerated from `app.routes`, and every response shape was captured from a request against an isolated instance — not from memory and not by patching the previous snapshot (`eedc391`).*

> **This file can drift.** It began as a snapshot of `a266cae`, and each
> item since has updated the parts it changed, through W10b. Seven test files
> read parts of it and fail when those parts drift (`tests/test_advice_recall.py`, `test_as_of.py`, `test_ba_fix2_request_hash.py`, `test_ba_packet_sentinels.py`, `test_fz_freeze.py`, `test_w1_sse_payloads.py` and `test_w4_identity_record.py`);
> the rest fails nothing when a route is added, renamed or reshaped. If
> `git log backend/server.py` is newer than `git log docs/INTEGRATION_REFERENCE.md`,
> verify against `/docs` (the live OpenAPI) before trusting a detail here.

NEXUS is a belief substrate: your workflow sends it questions and facts, it answers from an auditable store, and every answer leaves a sealed lineage record you can verify later. Your integration touches two surfaces — the **write path** (ask, confirm outcomes, install policy, assert facts, pin a model, archive a duplicate) and the **read path** (lineage, provenance, verification, the governance budget).

---

## 1. Basics

- **Base URL**: the NEXUS host, default `http://localhost:8000`. Interactive OpenAPI lives at `/docs`.
- **Auth**: every write, and the whole lineage / provenance / verification / budget / refusals read path, requires `Authorization: Bearer <token>`. The token is `NEXUS_API_TOKEN` on the server (or a generated per-process session token printed at startup). A missing or wrong token returns `401 {"detail": "unauthorized"}`. §5 lists every route with its auth posture.
- **Proxy mode (W4)**: with `NEXUS_AUTH_MODE=proxy` the bearer token is no credential. Every request but `/health` and `/healthz` must come from a loopback peer (the identity proxy beside NEXUS) carrying the identity header (`NEXUS_IDENTITY_HEADER`, default `Tailscale-User-Login`) exactly once: `401` otherwise; `403` when the process has no owner set (`owner not set`) or the login is not its owner's. The login is compared exactly: only the spaces and tabs around it are trimmed. An unsafe request from a browser (any method but `GET`, `HEAD` and `OPTIONS`) must be same-origin: one whose `Sec-Fetch-Site` is present and is not `same-origin`, or one that carries `Origin` and no `Sec-Fetch-Site`, is `403`. A client that is not a browser sends neither header and is not refused. `GET /whoami` says the mode and the login. The login is each record's `accountable_identity`, with `identity_mechanism: "proxy"`.
- **Content type**: JSON bodies, `Content-Type: application/json` — except `/chat`, which **responds** with `text/event-stream` (§3.1).
- **Two different 422 shapes** ⚠️ — you will meet both:
  - **Schema errors** (missing/mistyped field) return FastAPI's shape: `{"detail": [{"type": "missing", "loc": ["body", "confirmed"], "msg": "Field required", …}]}`.
  - **Domain refusals** return a named error: `{"error": "scope_too_long", …}` / `{"error": "claim_id_unresolvable", …}`. Branch on `error` when present; fall back to `detail`.
- **Two other status codes carry named errors**: `409` for conflicts (`version_reused`, `ambiguous archive target`) and `503 {"error": "provider_unreachable", …}` when the model backend is down (§3.5). Neither is a 422.

## 2. Scope — read this before anything else

`scope` is how your workflow tells NEXUS which process step (or programme, or both — the value is opaque to NEXUS, structure it as you wish, e.g. `"trial-alpha/step-3"`) a record belongs to.

- Carried by **three** endpoints: `POST /chat`, `POST /outcome/confirm`, `POST /governance/envelope/confirm`. Optional string on each.
- **Unsupplied scope records as the literal string `"default"` — it is never null, and NEXUS never infers it** from message content, session, or timing. If you do not send it, it was not recorded. There is no retroactive scoping. Whitespace-only is unsupplied.
- **Length cap: 256 characters.** Longer returns `422 {"error": "scope_too_long", "max_chars": 256, "got": <n>}`. It is the first check each of the three endpoints makes, before the Law 3 gate.
- A **stated** scope is sealed into the record's hash (tamper-evident); records written before scope existed read `"default"` forever — `GET /cortex/stats` → `scope.recordable_since` is the timestamp that makes the two cases distinguishable.
- `POST /kb/assert/confirm` and `POST /kb/dedup/confirm` deliberately do **not** take scope: they act on beliefs, and beliefs are unscoped by design (scoping them is a partitioning decision NEXUS has not taken).

---

## 3. Write path

### 3.1 `POST /chat` — ask a question *(auth required)*

One conversational turn. Retrieval, answer, extraction, and the lineage record all happen here.

**Request**

| field | type | required | notes |
|---|---|---|---|
| `message` | string | **yes** | the user/workflow question |
| `scope` | string | no | see §2 |
| `attachments` | array of `{name, type, data}` | no | `data` is base64, `type` a MIME; an image attachment forces multimodal routing. Single-use, cleared at turn end |
| `work_unit` | string | no | v3.1 Phase 1/1b — the unit of value this turn is applied to; a row is created on first mention with this request's actor fields. **Body only, sent on every request like `scope`** — nothing is remembered between turns, no header is read, no route opens or closes a unit. Same cap and whitespace rule as `scope`; `422 work_unit_too_long` |
| `work_unit_type` | string | no | application vocabulary; recorded at creation |
| `accountable` | string | no | the answerable human for THIS record, recorded and never authenticated; may change mid-unit. **Proxy mode (W4):** the established login is the value; send none or the login: another value is `422 {"error": "accountable_mismatch"}` and writes nothing |
| `decision_boundary` | string | no | 1b — `close` sets the named unit's `closed_at` once (`done.unit_closed: true`); a second close is declared `unit_already_closed`, not refused; without `work_unit` it is recorded and `done.boundary_not_applied: "no_unit_named"`. W6 — `reopen` reverses a close (`done.unit_reopened: true`; on a unit that is not closed, `unit_not_closed`). Each act is made after the turn's record is written and appended to the boundary ledger (`GET /work-units/events`), naming that record; a turn whose record cannot be written makes none, and an act that cannot be written after its record is declared `boundary_not_applied: "write_failed"`. Any other value is recorded as asserted and has no effect |
| `actor_id`, `actor_role`, `actor_source` | string | no | who asserts this request (the `/outcome/confirm` vocabulary: role `person \| source_system`, source `local \| idp`; `422 unknown_actor_role` / `unknown_actor_source`); recorded as `opened_by` / `closed_by` on the unit and `boundary_asserted_by` on the record; defaults `operator` / `local_operator` / `local` |

**Response — Server-Sent Events, not JSON** ⚠️

The body is `text/event-stream; charset=utf-8`: frames of `event: <name>\ndata: <json>` separated by blank lines. Read it with an SSE client or split on `\n\n`.

⚠️ **`turn_start` is not the first frame.** Routing, background-cycle relays and any compaction all precede it. Start reading at the first byte, not at `turn_start`. The order in which frames can appear:

| # | event | `data` | when |
|---|---|---|---|
| 1 | `kb_pressure_alert`, `chain_appended`, `kairos_consolidation`, `curiosity_surfaced` | object | relayed from the background cycle — anything queued since the last turn is flushed first (`curiosity_surfaced` arrives this way since AX.1) |
| 2 | `kb_pressure_alert` `{type: "assertion", sentence, shaped, triples[], endpoint: "/kb/assert/confirm", …}` | object | the message looks like a statement of fact that could become a belief (Law 3 offer) |
| 3 | `kb_pressure_alert` `{type: "web_search", action, query, reasons[], endpoint}` | object | a search is proposed; fires only with `/search/confirm` consent for this exact message |
| 4 | `rag_injected` `{source: "web", n_results, results[]}` | object | only after a consented search |
| 5 | `prediction_generated` `{expected_nodes[], n_predicted}` | object | |
| 6 | `model_routed` `{model, capability, override_active, …}` | object | every turn — `locked` under an override; `reason` on the image path (`image_attachment_present`), on the router's path with `fallback_used`, and on the general-reasoning fallback with `fallback_used` and `note` |
| 7 | `model_pull_proposal` `{model, name, size_gb, improves, fallback}` | object | the routed model is not installed; a fallback `model_routed` follows |
| 8 | `model_swap_starting` `{from, to, capability, reason, fallback_used}` | object | the active model is changing |
| 9 | `compaction_fired` `{tier, tokens_before, tokens_after, tokens_freed, items_removed, reason}` then `compaction_report` `{pass_fail, violations[], retention_rate, minority_loss_rate, structural_summary, removed[], removed_count}` | object | context exceeded budget. `pass_fail` is `PASS` or `WARN`; nothing halts and no state is marked (BD-3) |
| 10 | `turn_start` `{turn}` | object | |
| 11 | `triples_injected` | **array** of `{s, r, o, w, g, tier}` | beliefs placed in front of the model |
| 12 | `graph_activating` | **array** of `{id, act}` | |
| 13 | `token` `{t}` | object | many — `t` is the text chunk. Since W6 they are released after the turn's record is written (the default), so they arrive together, after extraction; `NEXUS_ANSWER_RELEASE=live` releases them as the model writes |
| 14 | `triples_extracted` | **array** of `{subject, relation, object, …}` | what extraction proposed, before the ingest gate |
| 15 | `contradiction_found` `{subject, object, relation_a, relation_b, timestamp_a, timestamp_b, source_a, source_b}` | object | zero or more |
| 16 | `curiosity_surfaced` `{node_id, score, question, epistemic_state, in_degree, evidential_weight, confidence, confidence_deprecated}` | object | up to three per KAIROS idle cycle, bridged onto the next `/chat` (AX.1); per turn only under `NEXUS_CURIOSITY_PLACEMENT=turn` |
| 17 | `eval_metric` `{turn, answer_relevancy, faithfulness, context_precision, composite, n_injected_triples}` | object | `faithfulness`, `context_precision` and `composite` are `null` when nothing was injected — not computed, not a score (BB Part 1); render null as words, never as `0.00`. `answer_relevancy` is a brevity proxy, see `backend/eval/ragas.py` |
| 18 | `prediction_error` `{high_surprise[], confirmed[], missed[], magnitude}` | object | |
| 19 | `hebbian_update` `{strengthened, weakened, formed, pruned, surprise_strengthened, confirmed_strengthened, missed_weakened, pairing: {mode, k, nodes_paired, fired_events}, protection}` | object | `protection: "unavailable"` means the prune failed closed and `pruned` is `null` |
| 20 | `done` | object | the completion marker; carries `work_unit_id`, `attribution_status` (`attributed` \| `unattributed`), `admitted_to_unit` (junction rows written this turn), `unit_first_seen` and `unit_closed` (booleans, always) and, only when set, `unit_already_closed`, `unit_closed_at_time_of_turn`, `boundary_not_applied`, `flags` (`accountability.missing`) — v3.1 Phase 1/1b. **FZ-2:** `learning` `{state: live \| frozen, since, since_source: "asserted", display}` on every turn (`display` reads `live`, `frozen since <date>` or `frozen since: undeclared`), and `contract_version: 4` — `done` is a versioned contract from this field on; a change to its key set is a bump (1: `learning`; 2: `reinforcement.skipped`, B-43 — `reinforced` counts writes made, 0 under frozen, and `skipped` the attempts the freeze refused; 3: C-38 — the reply separated at the provider boundary: `answer_chars`, `reasoning_chars` (null when the reply carried no reasoning), `answer_empty` (true when the reply carried reasoning and no stated answer, with `answer_empty_reason` riding only then; the console is sent the line *no stated answer; the model's reasoning was cut off* as a token frame, and nothing is extracted from the reasoning), `extraction_input_chars` and `extraction_input_truncated` — the 4,000-character extraction window, declared, not changed; 4: W6 — the unit's reversal: `unit_reopened` and `unit_not_closed`, each only when set, beside the other declarations) |
| 21 | `record_failed` `{turn, reason, answer_released, claims_admitted, contract_version}` | object | instead of `done`, and only when the turn's record could not be written: the turn ends there and is not added to the conversation. `reason` is the error's class name. Under the default release no `token`, `triples_extracted` or `contradiction_found` frame of that turn was sent (`answer_released: false`); under `live` the answer's `token` frames were, and no other frame of the answer, which is built only after the record in both positions. The session is left as it was before the request; `claims_admitted` counts the distinct claims extraction had admitted, which stay (Law 4) |

That is all 22 declared events (`tests/test_sse_contract.py` reconciles the list against the code). Three payloads are JSON **arrays**, not objects — a parser that assumes `data` is always an object breaks on `triples_injected`. Of `kb_pressure_alert`'s seven declared `type` values, four have a producer (`semantic_duplicates` and `cortex_drift` via the relay, `web_search` and `assertion` on the turn); `gap`, `staleness` and `dilution` are declared and never emitted today.

**`done` is the completion marker and the only frame that carries `lineage_id`:**

```json
{"turn": 1, "tokens": 241, "lineage_id": "…uuid…",
 "reinforcement": {"mode": "citation", "citation_test": "f5.2-informative-no_substrate",
                   "injected": 3, "cited": 1, "reinforced": 1, "skipped": 0, "suppressed": 2},   // skipped: attempts the freeze refused (B-43); reinforced + skipped + suppressed == injected
 "extraction_gate": {"extracted": 2, "extraction_failed": false, "stored": 1, "decomposed": 0, "rejected": 1, "reasons": ["too_many_tokens"], "storage_failed": 0, "outcomes": {"stored": 1, "split": 0, "rejected": 1, "storage_failed": 0}},   // reasons are gate rule names; BA added `imperative_shape`. BA fix 6: `extracted` is null with `extraction_failed: true` when the extractor FAILED (not measured), 0 when it answered with nothing (measured); `storage_failed` counts beliefs the gate admitted that could not be written; `outcomes` are per-candidate and sum to `extracted`
 "citation_forecast": {"forecast_id": "…uuid…", "k": 5, "predicted": [12, 7, 3, 9, 41],
                       "coverage": {"forecastable": 2, "total": 3},
                       "scorable": true, "reason": null,
                       "precision": 0.4, "null_precision": 0.2, "resolved": 5}}
```

- `citation_forecast` is a **measurement, not a control**: before retrieval NEXUS commits a prediction of which beliefs the answer will cite, and after the turn scores it. `precision` is precision@k of that prediction against the cited set; `null_precision` is the same score for the trivial predictor (the k most prominent injected beliefs). Compare the two — `precision` alone means nothing. When the turn cannot be scored, `scorable` is `false`, `precision` and `null_precision` are `null`, and `reason` names why: `empty_forecast` (no query term mapped to a stored belief), `flags_absent`, `no_injected`, `none_cited`. The key is **absent entirely** (not null) when `NEXUS_CITATION_FORECAST=off`.
- When the turn's record cannot be written there is no `done`: the turn ends with `record_failed` (W6). `lineage_id` is `null` only for a loop composed with no recorder, which the server and MCP never are.

⚠️ **Capture `done.lineage_id` at turn time.** It is the join key for outcomes, provenance and verification. If the stream ends without a `done` frame, the turn did not complete — treat it as failed and retry; do not fabricate a lineage reference.

### 3.2 `POST /outcome/confirm` — the world answered *(auth required)*

Records that reality confirmed or contradicted an answer (a QC result, a review verdict, an agency response), applies any weight recalibrations the installed policy permits, and resolves the calibration entries the turn stated.

**Request**

| field | type | required | notes |
|---|---|---|---|
| `event_type` | string | **yes** | e.g. `review`, `qc`, `agency_response`, `manual` (free text) |
| `decision` | string | **yes** | e.g. `approved`, `rejected` (free text) |
| `direction` | string | **yes** | one of `confirming` \| `contradicting` \| `neutral` — anything else: `422 {"error": "unknown_direction", "direction", "valid": [...]}` |
| `references` | object | **yes** | `{"lineage_id": "<uuid>"}` **or** `{"belief_ids": [1, 2]}` — `belief_ids` wins if both are present. ⚠️ `claim_id` is refused by design: `422 claim_id_unresolvable` |
| `confirmed` | bool | **yes — no default** | ⚠️ omit it and you get a schema 422 naming `body.confirmed`; send `false` and the attempt is **recorded** and refused with `422 {"error": "Law 3 …", "event_id": …, "recorded": true}` |
| `magnitude` | float | no (0.0) | size of the weight move; sign comes from `direction`; the absolute value is used |
| `scope` | string | no | see §2 — lands on the outcome **and** every recalibration it authorises |
| `workflow_ref` | string | no | your system's reference (e.g. `"LIMS/batch-47"`) |
| `notes` | string | no | free text — ⚠️ not covered by the record hash; do not put load-bearing facts here |
| `actor_id` | string | no (`"operator"`) | who/what supplied this |
| `actor_role` | string | no | closed vocabulary: `person` \| `source_system` — **send `source_system` when a system, not a person, is reporting**. Unstated records as `local_operator` (what silence has always meant). Anything else: `422 {"error": "unknown_actor_role", "role", "valid": [...]}` |
| `actor_source` | string | no | closed vocabulary: `local` \| `idp` (how the identity was established). Unstated records as `local`. Anything else: `422 unknown_actor_source` |

**Validation order, and what gets recorded.** Checks run in this order: `scope_too_long` → `unknown_direction` → `unknown_actor_role` → `unknown_actor_source` → `claim_id_unresolvable` → the Law 3 gate → reference resolution. **Which refusals are recorded follows one rule, the same on every route (§4.6):** a well-formed, in-vocabulary request that asked for a governed act and was declined is recorded — here the Law 3 gate and `no_beliefs_resolved`, which write an outcome row (`accepted: false`, `refusal_reason` naming the rule) and answer with `"event_id"` and `"recorded": true` beside the error. A request that was never valid is a client error: the five vocabulary refusals above, and a schema 422, are declared on the response and recorded nowhere. ⚠️ Rows written under the earlier, wider rule remain on the ledger; nothing stored is altered.

**Response `200`**

```json
{"event_id": "…", "resolution": "lineage_id -> 2 cited belief(s)",
 "envelope": { …the policy in force… },
 "applied":  [{"belief_id": 7, "delta": 0.05, "prominence_before": 0.61, "prominence_after": 0.66, "envelope_version": "v1", "event_id": "…"}],
 "refused":  [{"belief_id": 9, "rule": "class_not_movable", "detail": "evidence class 'asserted' is not movable under envelope v0-immovable (movable: [])"}],
 "calibration_resolved": 2, "calibration_note": ""}
```

Things that surprise people, all deliberate:

- **`applied` can be empty on a 200.** The default policy permits no movement; the outcome is still recorded, and so is each refusal (they appear later under `refused` in `/belief/{id}/provenance`). Check `refused[].rule` (`class_not_movable`, `max_delta`, `frozen`, `cumulative_max`, …).
- A `lineage_id` reference fans out to the **cited** beliefs of that turn (`cited: false` items are excluded). If none resolve: `422 {"error": "no_beliefs_resolved", "detail", "event_id", "recorded": true}`.
- `direction: "neutral"` and `belief_ids`-only references resolve **zero** calibration entries — `calibration_note` says why. Under a permitting envelope a `neutral` outcome still writes a recalibration event with `delta: 0.0` — an adjudication on the record: it appears in `applied` here, as a step in provenance, and in the budget report under `zero_delta`, never under `movements` (§4.5).
- **Calibration entries resolve once, first outcome standing.** The first adjudicating outcome for a lineage reports `calibration_resolved: n`; a second outcome for the same lineage reports `0` with an empty note, even if the first was refused by the envelope. Calibration is epistemics, the envelope is policy, and they are deliberately independent.
- **Supersede is a reason on this act, not a new act (AR).** When `notes` begins `superseded_by:` (for example `superseded_by: rev4`), the response and the recorded outcome row carry `recall_count` — how many sealed answers *injected* the referenced claims, a measured `0` when none did, never absent — and `recall_route` (`/belief/{id}/recall`, §4.3b). Both are hashed into the row only when present, so earlier rows recompute unchanged. Nothing else changes: standing moves only as the envelope allows (on the shipped `v0-immovable` envelope it does not move and the count is still recorded), and the recall itself is a read.

### 3.3 `POST /governance/envelope/confirm` — install policy *(auth required)*

Installs the versioned policy that bounds how much any outcome may move a belief's weight. Until one is installed, the default policy `v0-immovable` permits **nothing** — outcomes record but move nothing.

**Request**

| field | type | required | notes |
|---|---|---|---|
| `version` | string | **yes** | immutable name — reusing one: ⚠️ **`409`** `{"error": "version_reused", "detail": "envelope version 'v1' already exists; policy versions are immutable", "event_id", "recorded": true}` (a conflict, not a 422; recorded on the refusal ledger, §4.6, because the system declined to overwrite) |
| `reason` | string | **yes, non-empty** | empty or whitespace: `422 reason_required` |
| `confirmed` | bool | **yes** | Law 3 gate, as above — refused with `422 {"error": "Law 3 …", "event_id", "recorded": true}` and recorded on the refusal ledger (§4.6) |
| `max_delta` | float | no (0.0) | per-event bound |
| `movable_classes` | string[] | no ([]) | evidence classes: `asserted`, `external`, `fixture`, `procedural`, `consolidation`, `extraction` |
| `frozen_belief_ids` | int[] | no | never movable regardless |
| `cumulative_max` | float\|null | no | total absolute movement per belief, lifetime; `null` = uncapped — a real choice, made explicitly |
| `scope` | string | no | records the context of installation; **not** enforced against outcomes |
| `actor_id` | string | no (`"operator"`) | ⚠️ no `actor_role`/`actor_source` here — an installation always records `local_operator`/`local` |

Checks run in this order: `scope_too_long` → Law 3 → `reason_required` → `409 version_reused`.

**Response `200`**: `{"installed": true, "version": "v1", "ts": …, "record_hash": "…", "policy": {"version", "max_delta", "movable_classes", "frozen_belief_ids", "cumulative_max", "floor": 0.0, "ceiling": 1.0}}`

⚠️ **`POST /governance/envelope` (without `/confirm`) is `405`.** The read is `GET /governance/envelope` (bearer required since B-40: its history names the actor who installed each policy, and it declares per-person rows) — its response includes a `confirm` block that names the write route and an example body. When in doubt, read routes tell you their write route: `/kb/dedup/pending`, `/kb/index/pending`, `/kb/trails`, `/search/pending` and `/lineage/{id}` all carry one.

### 3.4 `POST /kb/assert/confirm` — state a fact *(auth required)*

Turns a human/system-stated sentence into a stored belief, source `human_assertion`, evidence class `asserted` (the slowest-decaying class in the store, and the one the default envelope cannot move).

**Request**: `{"sentence": "TAK-441 is metabolized by CYP3A4.", "confirmed": true}`

**Response `200`**: `{"sentence": …, "stored": 1, "beliefs": [{"belief_id": 1, "subject": "TAK-441", "relation": "is_a", "object": "metabolized by CYP3A4", "as_parsed": {"subject": "TAK-441", "relation": "is", "object": "metabolized by CYP3A4"}}], "shaped": true, "source": "human_assertion", "detected_as_assertion": true, "detection_reason": "finite verb"}`

⚠️ **A 200 does not mean something was stored.** A sentence that cannot be shaped into a subject–relation–object triple returns `200` with `"shaped": false, "stored": 0, "beliefs": []` and a `hint` — deliberately, so you learn the claim is unstorable rather than getting silence. **Always check `stored`.** An empty sentence is `422 empty_sentence`; a gate refusal of one triple appears inside `beliefs` as `{"refused": "<reason>", "subject": …}` and is not counted in `stored`. `confirmed: false` is `422` with `event_id` and `recorded: true` (§4.6); the sentence is not stored on the ledger, only a digest of the request.

**`beliefs[].subject/relation/object` are the values as stored** — read back from the row, so they are what `/lineage/{id}` and `triples_injected` will show and what a join can use. Relations are normalised on ingest (`is` → `is_a`, and other aliases), and `as_parsed` carries the triple as the shaper read it, named separately. The assertion offer on the `/chat` stream (`kb_pressure_alert{type: "assertion"}.triples[]`) has the same shape: stored form under the plain keys, `as_parsed` beside it. No `scope` field here (see §2).

### 3.5 `POST /model/override` and `DELETE /model/override` — pin the answering model *(auth required)*

**Request**: `{"model": "<role or raw model name>", "lock": false}`. `lock: false` pins for the next turn only and clears at its end; `lock: true` persists until `DELETE /model/override`. Extraction always uses its own model regardless.

- A **role** needs no backend to be pinned: `code` \| `extraction` \| `general` \| `multimodal` \| `primary` \| `reasoning`. Which roles can answer is derived from the model registry's capability declarations, not listed: `embed` resolves to a model that declares only `embedding`, so it is refused — `422 {"error": "model_cannot_answer", "model": "embed", "resolved_model": "nomic-embed-text", "capabilities": ["embedding"], "valid": [...], "hint"}`. `extraction` stays pinnable because its model also declares general reasoning.
- A **raw model name** is validated against the backend's installed list: unknown → `422 {"error": "unknown_model", "model", "hint"}`; backend down → **`503 {"error": "provider_unreachable", "backend": "ollama", "detail": "ConnectionRefusedError: …", "hint": "…"}`** (a named error, never a raw 500). A raw name the registry knows (same tag, or the same base before the colon) gets the same `model_cannot_answer` check; a tag the registry has never heard of is accepted as experimental.

**Response `200`**: `{"override_active": true, "resolved_model": "qwen3.5:9b-q4_K_M", "locked": false}`; `DELETE` → `{"override_active": false}`. `GET /model/status` (open) shows `active_model`, `override`, `locked`, `available` (empty when the backend is down), `backend`.

### 3.6 `POST /kb/dedup/confirm` — archive one side of a duplicate pair *(auth required)*

Candidates come from `GET /kb/dedup/pending` (open) → `{"candidates": [...], "confirm": {…example body…}}`. Body: the two triples (`subject_a`/`relation_a`/`object_a`, `_b` likewise), `keep: "a" | "b"`, `confirmed: true`, optional `source_a`/`source_b` naming the evidence class to archive, and optional `actor_id` (`"operator"`), `actor_role`, `actor_source` with the same closed vocabularies as §3.2 — refused as `422 {"error": "unknown_actor_role", "role", "valid": [...]}` (the permitted list is `valid` on every route; a vocabulary refusal is a client error and is not recorded). `confirmed: false` is recorded on the refusal ledger with the stated actor (§4.6).

- Archiving never deletes (the row stays, `archived: true`, `archive_reason: "dedup"`, visible on `/lineage/{id}`).
- If the triple exists under several sources and none was named: `409 {"error": "ambiguous archive target", "sources": [...], "hint", "event_id", "recorded": true}` — recorded on the refusal ledger (§4.6): the system could have archived both and declined to guess which copy you judged.
- **Response `200`**: `{"archived": [s, r, o], "kept": "a", "rows_archived": 1, "tier": 4, "tier_deprecated": true}`.
- Every archival writes an **archival act** (who, when, why, which direction) to the ledger in §4.6.

---

### 3.7 `POST /commit` — commit now, reveal later *(auth required)*

Body `{"content": "<the note>", "confirmed": true}`; `confirmed` defaults to `false`, never `true` (Law 3). Unconfirmed: `422 {"error", "hint", "offer": {"content_chars", "salt_bytes": 32, "sealed_by"}, "event_id", "recorded": true}`, the refusal recorded by the request's digest, never its text (§4.6); empty content is `422 empty_content`. Confirmed: `200 {"commitment_id", "commitment", "salt", "ts", "sealed": false, "note"}`. **Keep the salt**: it is handed back once, no receipt carries it, and the reveal needs it. The note and its salt stay in this store's `held_commitments` (and so in its backups); they enter no prompt, retrieval or record. The next seal seals `SHA-256(salt || canonical(content))` as a leaf of its block's `commit` root; §4.2d reads the receipt and §9 checks a later reveal. Under MCP the same act is `nexus_commit` (§8).

## 4. Read path — all of these require auth except where marked

### 4.1 `GET /lineage/{lineage_id}` — what an answer rested on

Response keys: `lineage_id`, `ts`, `scope`, `query_hash`, `injected`, `contradictions`, `model_id`, `prompt_prefix_hash`, `response_hash`, `record_hash`, `sealed_block`, `learning_mode` (LS: `"frozen"` when FZ-2's switch was on at the moment the record was written, sealed into `record_hash`; `null` when learning was live or the record predates the field — records from 2026-10-01 to the LS merge are frozen by the operator's declaration, not by the record), `citation_test` (which citation test produced the `cited` flags; `null` on records older than the versioned instrument), `context` (AW: `{render, hash, activation}` — which prompt render produced the turn's dynamic block, sha256 of the block the model was sent, and the `[concept, activation]` pairs its cortex section rendered; `null` on records older than AW; outside the hashed body), `request_hash` (BA fix 2: sha256 of the whole request as sent — roles, content, order; sealed into the record hash when present; `null` on older records. The dynamic block RE-DERIVES from the record and the store; the history only HASHES, because it is not retained beyond an in-memory checkpoint — so the request is auditable, not verifiable, and the two are different claims), `work_unit_id` and `accountable_identity` (v3.1 Phase 1: which work unit the turn was attributed to and who was answerable on THIS request, recorded not authenticated; hashed into the record when stated; `null` on records older than the fields — history is unattributed by absence), `identity_mechanism` (W4: how `accountable_identity` was established — `asserted`, the default, stored as NULL and named here, or `proxy`, taken from a loopback identity proxy's header; sealed into `record_hash` when not the default, so every older record recomputes unchanged; `asserted` on every `mcp_block` record, where any other value is named in `scope_declaration.violations`), `boundary_asserted`, `boundary_asserted_by` and `attribution_flags` (1b: the boundary the request asserted and its actor fields, hashed when stated; the flags declared — `accountability.missing` — unhashed and re-derivable; `null`/`[]` on older records), `answer_hash`, `reasoning_hash`, `answer_chars`, `reasoning_chars` and `answer_empty_reason` (C-38: the reply separated at the provider boundary — `response_hash` stays the hash of the content stream as delivered; `answer_hash` is sha256 of the stated answer and `reasoning_hash` of the reasoning, both sealed into `record_hash` when present, so every older record recomputes unchanged; the two lengths are unhashed; `reasoning_hash` and `reasoning_chars` are `null` when the reply carried no reasoning; `answer_empty_reason` is set only when the reply carried reasoning and no stated answer; `null` across the five on records older than C-38; no reasoning text is in the record), `envelope_version` and `forecast` (W6: the installed recalibration policy in force when the record was written, `null` when none was installed, and the citation forecast's turn-level score, `null` when no forecast ran; each sealed when not `null`), plus `verify` (`{exists, record_intact, sealed, sealed_block}`), `weight_vocabulary`, `provenance_coverage` (`{records_begin_at, note}`) and a `confirm_outcome` block naming `/outcome/confirm` with a body pre-filled with this record's `lineage_id`.

Each `injected[]` item carries the sealed values `{belief_id, weight_at_injection, tier_at_injection, cited}` and, resolved live: `resolved`, `subject`, `relation`, `object`, `source`, `current_tier`, `current_prominence`, `high_water_weight` (`stored_weight` is a deprecated alias), `archived`, `archive_reason`, `archived_at`, and a compact `provenance` `{effective_weight, recalibration_adjustment, recalibrations, refused, sealed, detail}`. `resolved: false` means the belief no longer exists in the store — reported, never hidden.

**`replay_capture` (W0b)** — what a re-run of this answer would need: `captured` (false, with a `note`, on a record written before W0b), `inference_seed`, `model_digest`, `inference_config` and `environment` resolved from the hashes the record seals, `absent` (the reason for each field that is NULL), and `request_kept` with `request_encoding`, `request_bytes_raw` and `request_bytes_stored` — sizes only; the request text is never on this page. The record itself gains `inference_seed`, `model_digest`, `inference_config_hash`, `environment_hash` and `capture_notes`, each sealed into `record_hash` only when present, so every earlier record recomputes unchanged.

Unknown id: `404 {"error": "unknown lineage_id"}`. `GET /lineage/recent?limit=20` (1–200) → `{"records": [...], "total": n}` with the sealed fields only.

### 4.2 `GET /lineage/verify` and `GET /lineage/{lineage_id}/verify` — tamper check

⚠️ `/lineage/verify` is a literal route registered before `/lineage/{id}` — it verifies **all** records (`?limit=` caps at 1000); the per-record form takes the id and 404s if unknown. Response:

```json
{"records_checked": 41, "verdict": "intact",
 "layers": {"row":   {"ok": 41, "failed": 0, "failures": []},
            "seal":  {"ok": 39, "failed": 0, "failures": [], "unsealed": 2, "unsealed_ids": […], "missing_block": []},
            "chain": {"ok": true, "n_blocks": 5, "broken_at": null, "reason": ""}},
 "request_bodies": {"checked": 41, "failed": 0, "failures": [], "verdict": "intact",
                    "records_with_body": 41, "records_without_body": 0, "orphans": 0},
 "capture_rows": {"checked": 2, "failed": 0, "failures": [], "verdict": "intact"},
 "tombstoned": 0,
 "proofs": {"checked": 39, "failed": 0, "failures": [], "bound": 12, "unbound": 0},
 "sealed_without_row": 0, "sealed_without_row_failures": []}
```

Since W8 a fourth layer sits beside the three: `proofs` builds each sealed record's inclusion proof (§4.2b) and verifies it from the proof alone; `bound` and `unbound` count the records whose seal claims no root (sealed before W8, or as a bare hash) whose commitments a later seal has bound, or not yet; a stored root that the record's values and salts do not rebuild is named instead (`binding_malformed`), and never bound. The layer also reads every layout-2 lineage leaf and every binding leaf block to row, for every record or, per record, for that one: a leaf whose root is not the store's is named in whichever block it is (`leaf_mismatch`, `binding_malformed`). `sealed_without_row` reads block to row: a hash a block's lineage seal holds that no record holds (a sealed record deleted, or rewritten with its seal cleared). `tombstoned` counts the records verified by their kept openings and root instead of their hash.

`request_bodies` (W0b) checks the kept request copies: each one must decompress to the bytes that hash to the `request_hash` its record seals. It sits **beside** the three layers and never inside them or their verdict. A corrupted copy (`verdict: "copy_corrupted"`, each failure naming its `request_hash`, the `lineage_ids` resting on it and a `reason`) is not a tampered record; it is a record that can no longer be re-run. The per-record form checks only that record's copy and omits the three store-wide counts; a record with nothing kept reads `none kept` with its reason, never `intact`. `capture_rows` checks the options and environment rows the records key the same way (each must hash to its key, and every key must have its row: `row_corrupted` names the table, the key and the records).

Read the layers separately — that is the point. `row` failed = content edited in place; `seal` failed = a consistent forgery; `missing_block` = a record naming a block the chain does not have; **`unsealed` is not a failure** — a record written since the last background cycle (≤ ~5 min) is simply awaiting its seal.

### 4.2b `GET /lineage/{lineage_id}/proof` — a record's inclusion proof *(auth required)*

`{"proof_version": 1, "lineage_id", "record_hash", "state": "sealed" | "unsealed" | "block_missing" | "not_in_block", "commitment_root", "leaf", "leaf_index", "tree_size", "path": [hex, …], "seal": "lineage", "block": {"layout", "index", "block_hash", "header" | "body"}, "binding"?}`, every hash hex; 404 for an unknown id. A record sealed since W8 has `leaf = record_hash + commitment_root` under its block header's lineage root; a record sealed before W8 has its block's whole body (its hash covers the list) and, once bound, a `binding` carrying the same leaf under a later block's `binding` root. `backend/memory/proofs.verify_proof(proof)` returns `null` when the proof holds and a refusal by name otherwise. A proof claims the leaf is in that block's root and the block hashes to that header; a binding, that this instance bound the commitments at that block's time, not what the record held when first sealed.

### 4.2c `GET /lineage/{lineage_id}/credential?disclose=f1,f2` — a decision credential *(auth required)*

`{"credential", "version", "disclosed": {field: {"value", "salt"}}, "commitments": {field: hex}, "commitment_root", "proof", "signatures": [{"signed": "seal" | "issue", "block_index", "block_hash", "key_id", "value"}], "keys", "scope", "decision_fields", "anchors"}`. The fields named in `disclose` come with their values and 32-byte salts; every other field appears only as its salted commitment (W8). The record hash rides the proof's leaf (W8's design) and is over every field, the lineage id among them, so the id appears only when disclosed: undisclosed, its random bits hide every undisclosed field together from whoever lacks the id; with the id (disclosed, or read from a `done` frame or the open `/events/recent`) the undisclosed fields can be tested against guesses together, which is practical when they are few and small. `proof` is §4.2b's; `signatures` holds the instance's Ed25519 signature over `NEXUS-BLOCK-V1\x00 || index (8 bytes) || block_hash` — `seal` when the block was signed as it was appended, `issue` (and said so) for a block with no signature of its own (sealed before W9, or appended while the key could not be read), by the current key; `keys` is `/identity`'s list (§4.8); `anchors` the block's RFC 3161 tokens, when anchoring is on. `scope` and `decision_fields` (a list of field names) are the issuer's description of the record; no signature covers them. A tombstoned record discloses only what it kept; a credential issued before the tombstone keeps verifying. Refused by name, never issued: `404 no_record`; `409` `unknown_field`, `dropped` (disposed of by a tombstone), `unsealed`, `unbound`, `no_commitments`, `does_not_verify`; `503 key_mismatch` (the key file is not its seed's) or `key_unreadable` (the process cannot read it). Verify it with `nexus-verify` (§9), offline, with no store.

### 4.2d `GET /commitments/{commitment_id}/receipt` — a held commitment's receipt *(auth required)*

`{"receipt": "nexus.commitment", "version", "commitment_id", "commitment", "held_at", "leaf_index", "tree_size", "path", "block": {"layout", "index", "block_hash", "header"}, "signatures", "keys", "anchors"}` — the commitment's place in its block's `commit` root and the block's signature; never the salt or the note. `404 no_commitment`; `409 unsealed` (ask after the next seal) or `does_not_verify`; `503 key_mismatch` or `key_unreadable`.

### 4.2e `GET /actions/{action_id}` — a governed action's receipt *(auth required; per-person)*

`{"receipt": "nexus.action", "version", "action_id", "fields", "record_hash", "leaf_index", "tree_size", "path", "block": {"layout", "index", "block_hash", "header"}, "signatures", "keys", "anchors", "disclosed": {"accountable_identity": {"value", "salt"}}, "per_person"}` (W10b). An action is a request NEXUS's runtime sent for a person (`evidence_class: "runtime"`: `web_search`, `seed_conceptnet`, `seed_wikidata`), recorded before it left, or one an MCP client reported (`client_reported`, §8). `fields` are the eighteen its record hash is over: `action_id`, `kind`, `evidence_class`, `method`, `target` (scheme, host and path; the query is in the hash), `request_hash` (SHA-256 of the canonical method, URL and parameters), `credentials_json` (each key header by name and the first 16 hex of its value's SHA-256, never the value), `lineage_id` (the turn's) or `trigger_route`, `identity_commitment` (the accountable identity's salted commitment, W9's form) and `identity_mechanism`, `signature_key_id` and `signature` (when signed), `requested_at`, `state`, `status`, `response_hash` (SHA-256 of the body received) and `completed_at`. The accountable identity is disclosed, with its salt, here only: declared per-person (A-2). `404 no_action`; `409 incomplete` (the outcome was not recorded), `unsealed` (ask after the next seal) or `does_not_verify`; `503 key_mismatch` or `key_unreadable`. `/lineage/{id}` lists a turn's actions as `actions` (`action_id`, `kind`, `target`, `evidence_class`, `state`), and `/cortex/stats.actions` counts them by class and state, with `signing` (§4.8). Verify it with `nexus-verify --action` (§9).

### 4.3 `GET /belief/{belief_id}/provenance` — why a belief weighs what it weighs

⚠️ **Singular `/belief/`, not `/beliefs/`** (plural is `404 {"detail": "Not Found"}`). Response: the weight product (`stored_weight`, `current_weight`, `arrival_scale`, `recalibration_adjustment`, `effective_weight`, with a `weight_vocabulary` explaining each), `steps` (each applied recalibration: `event_id`, `ts`, `delta`, `prominence_before/after`, `envelope_version`, `actor {id, role, source}` — so you can tell a person's judgement from a system's report — `outcome_ref`, the resolved `outcome` or `outcome_resolved: false`, `sealed`, `sealed_block`), `refused` (the same shape with `applied: false`, `refusal_rule`, `refusal_detail`), `records_begin_at`, `recalibration_enabled`, and a `coverage` sentence: an empty history means *not recorded*, never *never moved*.

⚠️ An unknown belief is **`200` with `resolved: false`** and zeros, not a 404 — an unresolved belief in an answer's evidence is a finding, not an error.

### 4.3b `GET /belief/{belief_id}/recall` — every sealed answer that rested on a claim *(auth required; per-person rows)*

The advice recall (AR): the record read backwards. Given a claim id, every lineage record that **injected** it, oldest first, paginated (`?limit=` 1–500, default 100; `?offset=`). Built from the injected set and never the cited flag — the flag under-reports by about a fifth (measured), and a recall that misses affected answers is wrong in the unsafe direction; over-inclusion is the safe direction. `contract_version: 2` (1: AR; 2: W4 — each row's `identity_label` reads the record's `identity_mechanism`, where version 1 printed `asserted` on every row, and `per_person.labels` says what each label means; the keys did not change).

```json
{"belief_id": 1, "resolved": true,
 "claim": {"subject": "…", "relation": "…", "object": "…", "source": "human_assertion", "archived": false},
 "records": [{"lineage_id": "…", "ts": 1760000000.0, "work_unit_id": "WU-A", "accountable": "alice",
              "identity_label": "asserted", "scope": "default", "weight_at_injection": 0.71,
              "cited": true, "tier_at_injection": 1, "response_hash": "…", "sealed_block": 0},
             {"lineage_id": "…", "ts": 1760000600.0, "work_unit_id": "WU-B", "accountable": "bob@example.com",
              "identity_label": "proxy", "scope": "default", "weight_at_injection": 0.64,
              "cited": false, "tier_at_injection": 1, "response_hash": "…", "sealed_block": null}],
 "total": 7, "limit": 100, "offset": 0, "sorted_by": "ts ascending", "read_from": "injected",
 "per_person": {"declared": true, "field": "accountable",
                "label": "per row: identity_label, read from the record's identity_mechanism",
                "labels": {"asserted": "…", "proxy": "…"}, "note": "…"},
 "note": "…", "contract_version": 2}
```

- **Per-person rows, declared.** Each row names the identity recorded as answerable for that answer (`accountable`), and `identity_label` says how it was established, read from the record's `identity_mechanism` (W4): `asserted` — recorded as stated, never authenticated (the default, and every record written before W4); `proxy` — taken from the identity header of a loopback identity proxy by a process that answers only for its configured owner: the proxy authenticated the person, NEXUS did not. `per_person.labels` carries both meanings. Nothing in the system could label it `verified`. NEXUS never aggregates by person; a consumer that groups these rows does so on its own account. The declaration rides the payload (`per_person`), `docs/CONTRACTS.md`, and the auth ratchet's `PER_PERSON_ROWS`.
- **Read-only, and gated** like every `/lineage/*` and `/belief/*` evidence read. Fifty recalls leave every lineage row byte-identical (pinned).
- **Index.** A derived table `lineage_injections` (claim → lineage id) is written in the record's own transaction and backfilled once at open; `GET /cortex/stats` → `recall.index` reports its coverage. It is verifiable against the record (`verify_recall_index`); the sealed `injected_json` stays the source.
- An unknown belief is `200` with `resolved: false`, `claim: null` and an empty list — the §4.3 rule.

### 4.3c `GET /kb/as-of` — what the store held at an instant, on transaction time *(auth required)*

AS-1 / AS-1b. `?date=` is an **ISO 8601 instant with an offset** (`2026-03-12T23:59:59+08:00`). A bare date (`YYYY-MM-DD`) is refused by name — `422 {"error": "bare_date", "refused": "bare_date", "reason": "…", "example": "2026-03-12T23:59:59+08:00"}` — because a date has no zone, so the record cannot know whose end of day is meant; a naive instant (no offset, no `Z`) is refused the same way as `naive_instant`, and anything else as `not_an_instant`. The conversion belongs in the client, where the zone is known. `?subject=`, `?relation=`, `?source=` filter by exact stored form; `?limit=` 1–500 (default 100), `?offset=`. Predicate: *a claim is held at D if it was first admitted on or before D, and, reading its archival events up to D in order, its last event is not an archival; a claim whose supersede act is dated on or before D is held and superseded.* Ordered by admitted-at then claim key, so pages partition the result. `contract_version: 2` (1: AS-1; 2: the refusals and `archival_ledger_from`).

```json
{"as_of": {"ts": 1773532799.999999, "iso": "2026-03-14T23:59:59.999999+00:00", "requested": "2026-03-14T23:59:59.999999+00:00",
           "interpretation": "instant with offset: read as given"},
 "claims": [{"belief_id": 3, "subject": "…", "relation": "…", "object": "…", "source": "human_assertion",
             "source_class": "asserted", "admitted_at": 1773396000.0, "admitted_at_iso": "2026-03-11T10:00:00+00:00",
             "status_at": "held", "superseded_at": null, "superseded_at_iso": null,
             "archival_at_d": {"last_event": "unarchived", "ts": 1773658800.0, "read_from": "ledger"}}],
 "total": 9, "limit": 100, "offset": 0,
 "sorted_by": "admitted_at ascending, then claim key (subject, relation, object, source)",
 "filters": {"subject": null, "relation": null, "source": null},
 "earliest_admitted_at": 1773309600.0, "reason": null,
 "archival_ledger_from": "2026-03-11T11:00:00+00:00", "archival_ledger_reason": null,
 "predicate": "a claim is held at D if …", "read_from": "triples.created_at (first admission); archival_events …",
 "note": "Transaction time only: when NEXUS admitted, archived or superseded a claim. Valid time is not modelled. No standing at the date is shown, because standing has no timed history yet. D must be an instant with an offset; timestamps are stored as UTC epoch seconds. No archival event is recorded before `2026-03-11T11:00:00+00:00`; a row archived before that by the column path and unarchived after it leaves no event and is not visible to as-of.",
 "contract_version": 2}
```

- **The fixed sentence, verbatim on every response:** *Transaction time only: when NEXUS admitted, archived or superseded a claim. Valid time is not modelled. No standing at the date is shown, because standing has no timed history yet. D must be an instant with an offset; timestamps are stored as UTC epoch seconds.* Then the ledger sentence: *No archival event is recorded before `<archival_ledger_from>`; a row archived before that by the column path and unarchived after it leaves no event and is not visible to as-of.*
- **`archival_ledger_from`** is the earliest `archival_events.ts` (ISO UTC) — the ledger's first recorded act, not its beginning: the table may have existed, empty, before it, and the record does not say when it was created. On a store with no event it is `null` with `archival_ledger_reason: "no archival events recorded"`, never blank. Also on `GET /cortex/stats` → `as_of`.
- **No standing at D.** No weight, tier, fire count or prominence key appears on a row: none of those has a timed history (the high-water is a monotone MAX, `tier` is overwritten by `rebalance`, `fire_count` is a counter). Prominence at D is AS-2's question.
- `status_at` is `held` or `held_superseded` (the supersede act's time rides `superseded_at`); `archival_at_d` says which event decided the verdict and where it was read from — `ledger` (the archival events), `column` (a row with no event at all, decided by `triples.archived_at`), or `none`.
- A date before the earliest admission is `200` with `total: 0` and `reason` naming the earliest admission — never a blank. A bare date, a naive instant or anything that is not an instant is `422` with the named refusal and an example.
- Read-only and gated like every evidence read; fifty reads leave every store table byte-identical (pinned). `GET /cortex/stats` → `as_of` carries `earliest_admitted_at`, `archival_events` and `supersede_acts`, derived on every read.

### 4.4 `GET /governance/verify` — the governance records' tamper check

Recomputes every governance record's hash and checks sealed ones against the chain, over **six tables**: `envelope_versions`, `outcome_events`, `recalibration_events`, `archival_events` (§4.7), `refused_requests` and, since W6, `work_unit_events` (a unit's closes and reopens). A rewritten field on any of them — including `scope`, `actor_role` and an archival's `reason` — fails here by table and key.

```json
{"verdict": "intact",
 "row":  {"checked": 8, "failed": 0, "failures": [{"table": "…", "key": "…", "layer": "row"}], "zero_sign_legacy": 0},
 "seal": {"sealed": 6, "ok": 6, "failed": 0, "failures": [{"table": "…", "key": "…", "sealed_block": 3}], "unsealed": 2,
          "sealed_without_row": 0}}
```

Since W6 the check also reads block to row: every hash a block sealed must still be some row's. A sealed row deleted, or rewritten with a recomputed hash and its seal cleared, appears as a seal failure `{"sealed_block": 3, "record_hash": "…", "problem": "sealed_hash_without_row"}`, with no `table` or `key` (no row holds the hash, so none can be named), and is counted in `seal.sealed_without_row`.

⚠️ The shape differs from §4.2: there is no `layers` wrapper and **no `chain` layer** — check the chain itself with `GET /chain/status` (open). `verdict` is one of `intact` \| `row_hash_mismatch` \| `seal_mismatch`.

### 4.5 `GET /governance/budget` — what the recalibration budget was spent on *(auth required)*

Read-only aggregate of governed movement: per belief against the active `cumulative_max`, per actor keyed by `(id, role, source)` (a person and a system sharing an id are separate rows), and store-wide with refusal counts — each anchored to the envelope version the event itself recorded. Nothing acts on it.

```json
{"note": "Contamination is attributable and findable afterwards at the governance layer; at the belief layer it is attributable only to an evidence class; and nothing at either layer would notice it accumulating. This report aggregates governed movement only.",
 "declarations": {"cap_scope": "…", "governed_movement_only": "…", "attribution_limit": "…",
                  "coverage": {"records_begin_at": 1788960598.72, "note": "…"}},
 "active_envelope": {…}, "envelope_versions": [{"version", "ts", "actor", "reason", "cumulative_max", "max_delta"}],
 "store_wide": {"applied": 2, "movements": 1, "zero_delta": 1, "refused": 1, "total_movement": 0.05,
                "refusal_reasons": {"class_not_movable": 1}, "counting": "applied counts recorded events; movements counts the events with a non-zero delta; …",
                "by_version": {"v0-immovable": {"applied": 0, "refused": 1, "consumed": 0.0, "movements": 0, "zero_delta": 0}, "v1": {…}}},
 "per_belief": [{"belief_id": 1, "by_version": {"v1": 0.05}, "consumed_lifetime": 0.05, "events": 2, "movements": 1, "zero_delta": 1,
                 "spans_versions": false, "active_cumulative_max": 0.2, "remaining_under_active_cap": 0.15}],
 "per_actor": [{"actor_id": "lims", "actor_role": "source_system", "actor_source": "local", "consumed": 0.05,
                "events": 1, "movements": 1, "zero_delta": 0, "refused": 1, "first_ts": …, "last_ts": …, "versions": ["v0-immovable", "v1"]}]}
```

**Events and movement are counted apart.** `applied` (and `events` per actor and per belief) counts recorded recalibration events; `movements` counts those with a non-zero delta; `zero_delta` counts the rest — a `neutral` adjudication under a permitting envelope, kept on the record. `applied == movements + zero_delta` at every level, and the consumed figures sum `|delta|`. The rule rides the payload as `store_wide.counting`.

⚠️ **The payload declares its own four limits; read them before reading the numbers.**

1. `cap_scope` — `cumulative_max` caps movement **per belief only**. There is no per-actor or store-wide budget, so an actor spreading small deltas across many beliefs consumes nothing any cap watches: the report can show that concentration; the policy cannot refuse it.
2. `governed_movement_only` — only recalibration events appear. Claims entering the store (extraction, assertion, seeding) are not recalibrations and are invisible here.
3. `attribution_limit` — **beliefs carry an evidence class, not an identity**, so contribution cannot be attributed to a person at all; only governed movement can.
4. `coverage` — movement before `records_begin_at` was never recorded; stored weight is a monotone high-water mark with no recoverable history.

It is token-gated and deliberately not an alert: it names actors, and in a partner deployment the chat surface belongs to an end user.

### 4.6 `GET /governance/refusals` — what the system declined *(auth required)*

Every confirm route records its Law 3 refusal here, under **one rule**: *a refusal is recorded when a well-formed, in-vocabulary request asked for a governed act and the system declined — "would not" is a governance act. A request that was never valid (a schema error, a value outside a closed vocabulary, an unresolvable reference) is a client error, and a backend or index that could not is system state; both are declared on the response and recorded nowhere. Ingest-gate refusals are counted per pass and never ledgered.* The rule rides the payload as `coverage.rule`.

```json
{"refusals": [{"ledger": "refused_requests", "event_id": "…", "ts": …, "route": "/kb/index/confirm",
               "rule": "law3_unconfirmed", "detail": "confirmed=true required",
               "actor": {"id": "operator", "role": "local_operator", "source": "local"},
               "scope": "default", "request_sha256": "…", "record_hash": "…", "sealed_block": 12},
              {"ledger": "outcome_events", "event_id": "…", "route": "/outcome/confirm",
               "rule": "Law 3 — confirmed=true required", "request_sha256": null, …}],
 "coverage": {"enabled": true, "events": 2, "by_ledger": {"refused_requests": 1, "outcome_events": 1},
              "records_begin_at": …, "rule": "…", "note": "…"},
 "verify": "/governance/verify"}
```

- Two ledgers, one list: `/outcome/confirm` keeps its richer `outcome_events` rows (§3.2); every other gate writes `refused_requests`. Each row says which. `?limit=` caps at 500, newest first.
- A refusing response carries `event_id` and `recorded: true` **iff** a row with that id exists; with `NEXUS_REFUSAL_LEDGER=off` the generic ledger writes nothing and neither key appears (only `/outcome/confirm`, whose rows predate the switch, still records). `version_reused` on `/governance/envelope/confirm` and `ambiguous archive target` on `/kb/dedup/confirm` are recorded as well: in both the system could have acted and declined.
- `request_sha256` identifies the attempt (sha256 over the canonical request body) without the ledger becoming a store of client text.
- Rows are hashed and sealed with the other governance records; `GET /governance/verify` covers the table by name. Coverage is forward-only: an empty ledger means nothing recorded yet, never nothing ever refused.

### 4.7 The archival act ledger — no read surface of its own ⚠️

Every archival and unarchival (dedup confirmation today) writes a sealed row: `event_id`, `ts`, `belief_id`, `direction`, `reason`, `actor_id`, `actor_role`, `actor_source`, `record_hash`. Two surfaces touch it, and neither returns its rows:

- `GET /cortex/stats` → `archival_ledger` `{enabled, events, records_begin_at, note}` — coverage only. An empty ledger means *nothing recorded yet*, never *nothing ever archived*: acts before the ledger shipped were never recorded.
- `GET /governance/verify` — integrity, with failures naming `"table": "archival_events"`.

There is **no endpoint that lists archival acts or answers "who archived belief N"**. `/lineage/{id}` shows `archived`, `archive_reason` and `archived_at` on a cited belief, but not the actor. If your audit needs the actor, that is a gap today, not a route you have missed.

### 4.8 Open reads (no auth, useful for dashboards)

- `GET /healthz` (alias `/health`) — `ollama` (the model backend's reachability, under that name whichever backend serves: `true` under the stub), `kb_triples`, `embedding_degraded` / `embedding_fallbacks` (monotone), `model_probe {mode, state, flagged, models, error}`, and the served `frontend` digest.
- `GET /cortex/stats` — `scope.recordable_since` (§2), `relation_vocabulary`, `contradiction_pairs`, `semantic_index`, `relation_trails`, `citation_capture`, `replay_capture` (W0b: `keep_requests` and `inference_seed` — the switches in force — `records`, `with_seed`, `with_digest`, `with_config`, `with_request_kept`, `request_bodies`, `request_bytes_raw`, `request_bytes_stored`, `configs`, `environments`, and a `note` saying NEXUS keeps the exact text it sent to the model; counts derived on every read), `fire_capture` (CP: `enabled`, `fire_rows`, `last_fire_turn`, `check_rows`, `b49` — `removed_total`, `present_next`, `absent_next`, `demoted_next`, `unresolved_ids`, `fires_tier_1_to_3`, `fires_needed_for_the_rule` — counts read from the capture file on every call, never stored, never a verdict; `note`), `mcp` (MCP-2, §8: `contract_version`, `coverage` — `present`, `recorded`, `retrieved_without_record`, `recorded_without_retrieve`, `sentence` — read from the `mcp_coverage` table the MCP process writes beside the record, `present: false` on a store it never opened; `first_run`; `record_scopes`; `note`), `recall` (§4.3b: `superseded_claims`, `records_resting`, `since`, `supersede_events`, `index` — counts derived from the record on every read, never stored, never a rate), `learning` (FZ-2: `state` `live` \| `frozen`, `since` as the operator asserted it, `since_source: "asserted"`, `display`, `skipped_writes_since_start` and `by_method` — in-process, reset on restart — `checked_at`, the five store methods (six names: the neocortex's promotion entry is two), and `rollback: NEXUS_LEARNING=live`; frozen stops every automatic learning write and nothing else, measured), `archival_ledger` (§4.7), `refusal_ledger` (§4.6), `weight_restoration`.
- `GET /identity` (W9) — the instance's public key: `{"algorithm": "ed25519", "public_key", "key_id" (SHA-256 of the public key), "created_at", "keys": [{"key_id", "public_key", "algorithm", "reason": "first" | "rotation", "previous_key_id", "announced_in", "until_block", "active_from", "active_to", "pending", "from_block"}], "signing": {"unsigned_appends_since_start", "last_error"}}`. `keys` is every key the instance has used, each with its period as a block range (a restore makes a new key, because a backup never copies the private one); the private half appears nowhere. Periods are block ranges, `announced_in` up to, not including, `until_block`: the current key's is open, and any other ends where the next key took over, the archive's next block when that key was made (kept in its key file and its announcement), so a block appended unsigned afterwards (the new key unreadable) never widens one and every block the archive held when the key was replaced stays inside it, a restore's included. `active_from` is the announcing block's time and `active_to` the time of the block at `until_block` (null while there is none), for reading only. A new key (a restore, or a key file moved aside) is listed `pending`, with `announced_in: null` and `from_block` the next block, until the next block announces it. A block sealed after the backup a restore came from is outside the old key's range in the restored list: its credential holds only for someone who received and checked it before the restore, or whose anchor dates the block before it (Ruling 7). `503 key_mismatch` (not its seed's) or `key_unreadable` (the process cannot read it), declared and never replaced. `/cortex/stats.anchoring` says whether blocks are dated by an RFC 3161 authority and what leaves the machine.
- `GET /identity/signed-action?signature=<base64, percent-encoded>` (W10b; Ruling 8, in the development record) — for whoever received a request this instance signed (`NEXUS_SIGN_REQUESTS=on`: RFC 9421, `Signature-Input` and `Signature` under the label `nexus`, covering `@method`, `@authority`, `@path` and `@query`, `keyid` the instance key's id at `/identity`): the §4.2e receipt of the action behind that signature, its accountable identity committed and never disclosed, and nothing else (no turn's content). Percent-encode the signature: a `+` sent bare reads as a space and answers `404 no_action`. `404 no_action`; `409 incomplete` (its outcome not recorded) or `409 unsealed`; `503 key_unreadable` or `key_mismatch`. Check it with `nexus-verify --action` against `/identity`.
- `GET /governance/envelope` — `enabled` (the kill switch) and `active` (the policy) reported separately, `is_default`, `history`, its `confirm` block, and `per_person` (B-40: the history names actors; gated, declared).
- `GET /kb/stats` (`total` / `retrievable` / `archived` / `by_granularity`), `GET /chain/status` (`?audit=true` re-reads snapshots), `GET /kairos/status`, `GET /state/summary`, `GET /model/status`, `GET /kb/dedup/pending`, `GET /kb/index/pending`, `GET /kb/trails`, `GET /search/pending`, `GET /router/preview?q=`, `GET /router/registry`.
- ⚠️ `GET /events/recent?since=<unix ts>` — the last 200 loop events with their payloads, **including every `token` frame's text and `done` payload**. It is open by the localhost-bind posture. If NEXUS is reachable beyond localhost, this route replays answer text to anyone; put it behind your proxy's auth or block it.

---

## 5. Route inventory

Every route the application registers, with its auth posture, enumerated from `app.routes` (the static mount `/vendor`, the consoles' vendored scripts from `frontend/vendor/`, open, is a mount and not a route) (rechecked at W9's close; FastAPI's own `/docs/oauth2-redirect` aside). Routes not described above are operator or UI surfaces.

| method | path | auth | purpose |
|---|---|---|---|
| POST | `/chat` | bearer | §3.1 |
| POST | `/outcome/confirm` | bearer | §3.2 |
| POST | `/governance/envelope/confirm` | bearer | §3.3 |
| POST | `/kb/assert/confirm` | bearer | §3.4 |
| POST, DELETE | `/model/override` | bearer | §3.5 |
| POST | `/kb/dedup/confirm` | bearer | §3.6 |
| GET | `/lineage/{lineage_id}` | bearer | §4.1 |
| GET | `/lineage/recent` | bearer | §4.1 |
| GET | `/lineage/verify`, `/lineage/{lineage_id}/verify` | bearer | §4.2 |
| GET | `/lineage/{lineage_id}/proof` | bearer | §4.2b |
| GET | `/lineage/{lineage_id}/credential` | bearer | §4.2c — W9 |
| POST | `/commit` | bearer | §3.7 — W9; Law 3 `confirmed`, no default |
| GET | `/commitments/{commitment_id}/receipt` | bearer | §4.2d — W9 |
| GET | `/actions/{action_id}` | bearer | §4.2e — W10b: a governed action's receipt, per-person |
| GET | `/belief/{belief_id}/provenance` | bearer | §4.3 |
| GET | `/belief/{belief_id}/recall` | bearer | §4.3b — per-person rows, declared |
| GET | `/kb/as-of` | bearer | §4.3c — as-of on transaction time; instants with an offset only; no standing at D |
| GET | `/whoami` | bearer (proxy mode: the identity) | W4 — `{auth_mode, login, identity_mechanism, identity_header, owner_set, note}`; `login` is null in token mode |
| GET | `/governance/verify` | bearer | §4.4 |
| GET | `/governance/budget` | bearer | §4.5 — per-person rows, declared (B-40: an authority surface) |
| GET | `/governance/envelope` | bearer | §3.3 — per-person rows, declared (B-40: gated, was open) |
| GET | `/governance/refusals` | bearer | §4.6 |
| GET | `/work-units/events` | bearer | a unit's closes and reopens, oldest first (`?work_unit=`; every unit's when omitted), each close listing the unit's lineage ids at the act; per-person rows, declared (W6) |
| POST | `/kb/seed/offline`, `/kb/seed/conceptnet`, `/kb/seed/wikidata` | bearer | seed the store (Law 3 `confirmed`; unconfirmed is `422` and recorded); each request the two online seeds send is a recorded action (§4.2e), and one whose record cannot be written is not sent: `503 action_not_recorded` with `seeded_before_the_stop` |
| POST | `/kb/index/confirm` | bearer | backfill the semantic index (`{"confirmed": true, "limit": 200}`) |
| POST | `/kb/trails/backfill/confirm` | bearer | lay relation trails over historic lineage (`?confirmed=true`) |
| POST | `/search/confirm`, `/search/execute` | bearer | web-search consent / immediate search — `confirmed` is required on both, no default; on `/search/confirm` `false` revokes consent and is an act, not a refusal; each search is a recorded action (§4.2e), and one whose record cannot be written is not sent, or its result is withheld: `503 action_not_recorded` (in a turn, `kb_pressure_alert {type: web_search, action: not_recorded}`) |
| POST | `/kairos/cycle` | bearer | force one background cycle (`?force_snapshot=`) |
| GET | `/healthz`, `/health`, `/cortex/stats`, `/kb/stats`, `/chain/status`, `/kairos/status`, `/state/summary`, `/model/status` | open | §4.8 |
| GET | `/identity` | open | §4.8 — W9: public keys only |
| GET | `/identity/signed-action` | open | §4.8 — W10b: the sealed action behind a signed request; names no person |
| GET | `/kb/dedup/pending`, `/kb/index/pending`, `/kb/trails`, `/search/pending` | open | read half of a Law 3 pair; each carries its `confirm` block |
| GET | `/events/recent` | open | §4.8 — replays payloads |
| GET | `/router/preview?q=`, `/router/registry`, `/eval/recent`, `/vector/stats`, `/graph/snapshot`, `/memory/column`, `/cortex/drift`, `/curiosity/recent`, `/workers/active` | open | UI and diagnostics |
| GET | `/`, `/memory` | open | the two HTML frontends |
| GET | `/docs`, `/redoc`, `/openapi.json` | open | live OpenAPI |

---

## 6. The minimal integration, end to end

1. **Turn**: `POST /chat` with `message` and your `scope`. Stream the SSE from the first byte; render `token` frames; on `done`, store `lineage_id` against your process step, and keep `citation_forecast` only if you are measuring.
2. **Later, the world answers**: `POST /outcome/confirm` with `references: {"lineage_id": …}`, `direction`, the same `scope`, `actor_role: "source_system"` if a system is reporting, and `confirmed: true`. Expect `applied: []` until a policy is installed.
3. **When anyone asks why**: `GET /lineage/{id}` for what the answer rested on, `GET /belief/{id}/provenance` for why the evidence weighs what it does, `GET /lineage/verify` and `GET /governance/verify` to prove none of it was altered, and `GET /governance/budget` for how much the policy let anyone move.

## 7. Gotcha summary

| # | Trap | What actually happens |
|---|---|---|
| 1 | `POST /governance/envelope` | `405` — the write is `/governance/envelope/confirm` |
| 2 | `/beliefs/{id}/provenance` | `404` — the route is singular: `/belief/{id}/provenance` |
| 3 | Omitting `confirmed` | schema `422` (`body.confirmed` in `detail`) — it has **no default**, by design |
| 4 | `confirmed: false` on any confirm route | `422`, and the attempt **is recorded** with an `event_id` on every gate (§4.6). A vocabulary or schema refusal is a client error: declared, never recorded |
| 5 | `references: {"claim_id": …}` | `422 claim_id_unresolvable` — NEXUS has no claim registry; use `lineage_id` or `belief_ids` |
| 6 | Expecting JSON from `/chat` | it is an SSE stream; `done` is the completion marker and sole carrier of `lineage_id` |
| 7 | Waiting for `turn_start` before parsing | relays, routing and compaction frames precede it — read from the first byte |
| 8 | Assuming every `data` is an object | `triples_injected`, `graph_activating`, `triples_extracted` are arrays |
| 9 | `200` from `/kb/assert/confirm` | may have stored nothing — check `shaped` / `stored`. The plain keys are the stored (normalised) values; the parsed form is under `as_parsed` |
| 10 | `200` from `/outcome/confirm` with empty `applied` | policy refused the movement; the outcome is still recorded — read `refused[].rule` |
| 11 | Reusing an envelope `version` | **`409`** `version_reused`, not a 422 |
| 12 | Two 422 body shapes | named `error` for domain refusals, `detail[]` for schema errors |
| 13 | Assuming GETs are open | lineage / provenance / verify / governance-verify / budget / refusals reads require the bearer token |
| 14 | `unsealed` in a verify response | not a failure — awaiting the next background seal (≤ ~5 min) |
| 15 | Scope after the fact | impossible by design — unsupplied records `"default"` forever; send it on every request |
| 16 | `citation_forecast.precision` on its own | meaningless without `null_precision`; and both are `null` with a named `reason` when the turn is not scorable |
| 17 | `/model/override` with `embed`, or with the backend down | `embed` (and any tag the registry knows as embedding-only) is `422 model_cannot_answer`; raw names with the backend down are `503 provider_unreachable`; the six answering roles still pin without a backend |
| 18 | Expecting to read who archived a belief | no route returns archival acts — only their count and their integrity (§4.7) |
| 19 | `GET /events/recent` behind a proxy | open, and it replays answer tokens — gate it yourself if NEXUS is not localhost-only |
| 20 | `VERIFIED` from `nexus-verify` | proves this instance's key sealed the record's commitments in that block and the disclosed values open them; never that the content is true, and never when (a signature says who; an anchor dates it) |
| 21 | Losing the salt `POST /commit` handed back | no receipt carries it; it stays only in the store's `held_commitments` (the operator can read it there) |

## 8. NEXUS as an MCP server — the `mcp` contract, version 5 (MCP-2; W0b; W6; W9; W10b)

`python -m backend.mcp` serves NEXUS over stdio to an MCP client (one
process per person, their own store). The HTTP app is never constructed and
the bearer token has no role; nothing in the tool layer names a transport,
so the SDK's streamable-HTTP transport stays reachable for a later runner.
The SDK is `mcp>=2.3,<3`.

**The turn under MCP is split at the model call.** `nexus_retrieve` runs
the first half of the loop (`prepare_turn`) and returns the block and a
`retrieval_id`; the client's model answers; `nexus_record` runs the second
half (`complete_turn`) on the answer the client sent and seals the record.
Pinned: the two-call turn equals the one-call turn on the rig (same block
bytes, same record on every field MCP-1 marked unchanged), and the console
path, which now calls both halves, is pinned byte for byte against the
pre-split base (`tests/test_mcp2_build.py`).

**Tools** (names final; input schemas from the signatures, output schemas
where typed; `contract(server)` lists them from the server object):

| Tool | Arguments | Returns |
|---|---|---|
| `nexus_retrieve` | `question`, `scope="default"`, `work_unit?` | `block`, `retrieval_id`, `injected[]`, `learning_mode`, `record_scope`, `expires_in_seconds` |
| `nexus_record` | `retrieval_id`, `answer`, `model?` | `lineage_id`, `record_hash`, `cited[]`, `injected[]`, `record_scope`, `learning_mode`, `coverage` — refused by name (`unknown_retrieval_id`) and counted when NEXUS holds no such retrieval |
| `nexus_recall` | `belief_id`, `limit`, `offset` | the `/belief/{id}/recall` payload (§4.3b), per-person rows declared |
| `nexus_asof` | `date` (an instant with an offset), filters, paging | the `/kb/as-of` payload; a bare date is refused by name |
| `nexus_assert` | `sentence` | what WOULD be stored, `shaped`, and the next step; stores nothing |
| `nexus_assert_confirm` | `sentence`, `confirmed`, `scope`, `work_unit?` | Law 3: stored only with `confirmed=true`; a call without it is refused and recorded as a declined governed act. A belief the gate refuses is listed in `beliefs` and counted in `refused`; it never ends the call |
| `nexus_status` | — | learning mode and how it was set, owner, the coverage counters, open retrievals, the handle timeout, `first_run`, the instructions' version and hash, the daemon-free statement, `replay_capture` (W0b, contract v2: the two switches, the records carrying each capture field, and the kept request bytes raw and stored), and `identity` (W9, contract v4: `/identity`'s body) |
| `nexus_record_action` | `lineage_id`, `kind`, `target`, `request_hash`, `status` | W10b, contract v5: an action the client says it took for the turn `lineage_id` names, stored `client_reported` (NEXUS did not see it happen, and the record says so), never counted as runtime; `request_hash` is a SHA-256 (64 lower-case hex); an unknown turn is refused (`unknown_lineage_id`). Returns `action_id`, `evidence_class`, `state`, `record_hash`, `sealed: false`; the receipt is read over HTTP (§4.2e). The coverage block's `actions_sentence` says NEXUS sees only the actions it is told; the instructions (`w10b.1`) ask a client to report them |
| `nexus_commit` | `content`, `confirmed=false` | W9, contract v4: §3.7's act. Unconfirmed it is refused (Law 3), recorded by the content's digest, and names the offer; confirmed it returns `commitment_id`, `commitment`, `salt` (once), `ts`, `sealed: false`. The receipt is read over HTTP (§4.2d) |

**Resource:** `nexus://lineage/{lineage_id}` — one sealed record, its
verification, and its scope declaration with any violations named.

**The record under MCP.** `record_scope = mcp_block` on the record, sealed
by the LS mechanism (hashed only when non-default; every pre-MCP record
recomputes unchanged). The scope declares: `model_id` is
`client-reported:<name>` or `client-reported:unknown`; `prompt_prefix_hash`
is sha256 of the empty string (NEXUS sent no prompt); `response_hash` and
`answer_hash` are over the answer as the client sent it; `request_hash`
covers NEXUS's block only and equals `context.hash`; `reasoning_hash`,
`reasoning_chars`, `answer_empty_reason` are NULL and `contradictions`
empty — undefined, declared, never defaulted. Since W0b the four replay-capture
fields (`inference_seed`, `model_digest`, `inference_config_hash`,
`environment_hash`) are undefined too, NULL with the reason in
`capture_notes` (changed in meaning: it names the client's model), and NEXUS's
block, what entered the client's context, is kept under `request_hash` when
`NEXUS_KEEP_REQUESTS` is on. No extraction runs under MCP
(decision (c)). `GET /lineage/{id}` carries `record_scope` and a
`scope_declaration` block with `violations`.

**The record under the interceptor.** `record_scope = intercepted` (W10c; `docs/INTERCEPTOR.md`), sealed the same
way. The scope declares: `request_hash` is over the request body exactly as the harness sent it and
`response_hash` over the response body exactly as the upstream returned it; `model_id` is
`upstream-reported:<name>` or `upstream-reported:unknown`; `query_hash` and `prompt_prefix_hash` are sha256 of the
empty string; `learning_mode` is always `frozen`; the answer and reasoning fields, the replay-capture fields and the
work-unit fields are NULL. Each exchange also has a runtime action of kind `model_exchange`, written before the
request is forwarded (states `requested`, `completed`, `failed`, `incomplete`), and each tool use the model asks
for is an action of evidence class `intercepted`, completed with status `reported` or `reported_error` when the
harness sends its result.

**Coverage.** *Complete for every turn the client recorded; retrievals
without a record are counted; turns with neither are not visible to
NEXUS.* Counted in `mcp_coverage` beside the record, read by
`nexus_status` and `/cortex/stats.mcp`. A retrieval not recorded within
`NEXUS_MCP_HANDLE_TIMEOUT` seconds (default 600) expires as
`retrieved_without_record`.

**Learning.** Frozen is MCP mode's code default; `NEXUS_LEARNING=live` is
the opt-in; the from-date is the first run on the store. The console's
default is unchanged. Records seal in the MCP process: the governance seal
runs alone on a housekeeping tick (`KairosProcess.maybe_seal_governance`),
never the idle cycle's learning passes.

**Configuration** (environment or `.env`, read at call time):
`NEXUS_MCP_OWNER` (the accountable identity on every MCP record, asserted),
`NEXUS_MCP_DATA_ROOT` (an absolute folder holding the store and the three
logs; unset, empty, relative or an unexpanded `${...}`, the per-user
application-data folder, never the working directory: W2),
`NEXUS_MCP_HANDLE_TIMEOUT`,
`NEXUS_LEARNING`, `NEXUS_KEEP_REQUESTS` (W0b; whether the block is kept).
One process opens a store (W2): a second, in MCP mode or the console, is
refused with "this store is open in another NEXUS process (pid N, since
T)" and exit 7, having opened nothing. The desktop bundle
(`scripts/build_bundle.py`, `docs/FIRST_RUN.md` steps 16 to 19) is this server as a
standalone binary, with learning fixed at frozen and no `.env` read.

## 9. Verifying a credential, a reveal or an action: `nexus-verify` (W9, W10b)

`scripts/nexus_verify.py` is one file with no NEXUS import (stdlib only), so a stranger can check a credential
with no store and no install; `release.yml` also packages it as a binary for Linux, Windows and macOS.

    nexus-verify credential.json --identity identity.json      # pinned to the instance's GET /identity body
    nexus-verify credential.json --key <64 hex>                # or one public key, active at any time
    nexus-verify --reveal receipt.json --content note.txt --salt <64 hex> --identity identity.json
    nexus-verify --action action.json --identity identity.json   # a governed action's receipt (W10b, §4.2e)
    nexus-verify ... --tsa-cert authority.pem                  # also check the RFC 3161 anchors
    nexus-verify ... --json                                    # the result as JSON

It checks each disclosed value against its commitment, the commitment root, the proof to the block's root
(RFC 9162), the block's hash, the instance's Ed25519 signature (RFC 8032) by a pinned key within that key's
period, and, with `--tsa-cert`, each anchor's token (RSA PKCS#1 v1.5 or ECDSA P-256/384/521). Exit 0 prints
`nexus-verify: VERIFIED` and each disclosed field; exit 1 prints `FAIL <name>: …` for every failure
(`disclosure_mismatch`, `commitments_mismatch`, `unsalted_hash`, `root_mismatch`, `root_not_sealed`,
`block_hash_mismatch`, `signature_invalid`, `signature_outside_key_period`, `unsigned`, `key_unpinned`,
`reveal_mismatch`, `action_mismatch`, `anchor_invalid`, …); exit 2 is input it cannot read. `--action` checks
that the fields hash to the record hash, its path to the block's `action` root, the block's hash and signature,
and, when the receipt discloses the accountable identity, that the value and salt open its commitment; it notes
`client_reported` actions as the client's word. A run given neither `--identity` nor `--key`
fails `key_unpinned`: a document's own key list proves nothing. A note is read exactly as UTF-8, line endings
kept: a reveal of the same text with other line endings fails.
