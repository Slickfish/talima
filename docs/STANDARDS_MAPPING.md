# NEXUS's record beside published audit-log standards

**NEXUS claims conformance to no standard.** This page sets the NEXUS turn record beside three texts a reader may
hold it against: the IETF Internet-Draft on agent audit trails (`draft-sharif-agent-audit-trail`), ISO/IEC 24970
(AI system logging), and the EU AI Act's logging duties for high-risk systems (Article 12 of Regulation (EU)
2024/1689). It says where NEXUS records more and where it records less. Being beside a standard is not meeting
it.

## Sources, and what was read

Read on 18 November 2026 (W3C's on 16 November); every statement about a source below quotes it or names its section.

| Source | Read | What |
|---|---|---|
| IETF `draft-sharif-agent-audit-trail-10` | **Yes** | https://datatracker.ietf.org/doc/html/draft-sharif-agent-audit-trail-10. The datatracker page: revision -10, last updated 2026-11-17, expires 2027-05-21, "No stream defined" (an individual submission). The draft's own header: "Intended status: Standards Track". An Internet-Draft is a work in progress, not a standard. Against -09 (read 16 November), -10 changes two things this page maps, by its own note: recording independence (§5.2) "from SHOULD to MUST at trust level L2 and above", and a non-null `authority_ref` (§3.1) at L2 and above. |
| Regulation (EU) 2024/1689 (the AI Act) | **Yes** | The Official Journal text, "Official Journal of the European Union, L series, 2024/1689, 12.7.2024", read from the Publications Office (`publications.europa.eu`, CELEX `32024R1689`, CELLAR `dc8116a1-3fe6-11ef-865a-01aa75ed71a1.0006.03`, XHTML). EUR-Lex answers automated reads with an AWS WAF challenge (`HTTP 202`). |
| Regulation (EU) 2026/1744 (it amends the AI Act) | **Yes** | The Official Journal text, "L series, 2026/1744, 24.7.2026", the same way (CELEX `32026R1744`, CELLAR `b459c07f-86fb-11f1-bf5e-01aa75ed71a1.0006.03`). |
| ISO/IEC 24970 | **No** | ISO's catalogue page (https://www.iso.org/standard/88723.html) answers automated reads, and a headless browser, with a Cloudflare bot check. What the IETF draft says of it is quoted as the draft's statement. |
| W3C Fetch Metadata Request Headers | **Yes** | https://www.w3.org/TR/fetch-metadata/, the Working Draft of 21 September 2026. Used for `SECURITY.md`'s proxy-mode sentence, not for the record. |
| W3C Secure Contexts | **Yes** | https://www.w3.org/TR/secure-contexts/, the Candidate Recommendation Draft of 10 November 2023, §3.1 (which origins are "potentially trustworthy"). |

The NEXUS half is checked against the code at `a261e1e` (W10b's closing commit; W11 changes no code): the record's
34 columns read from a store the code created, what is sealed into the record's hash read from
`backend/memory/lineage.py`.

## The NEXUS turn record, field by field

One row per answer in `lineage_records`. `record_hash` is SHA-256 over a JSON body with sorted keys and compact
separators. A field marked **sealed** is in that body; a field sealed "when set" enters the body only when it is
not the default, so every record written before the field existed recomputes unchanged (law 4).
Records are sealed into the archive chain by the next idle cycle: each chain block carries the digest of the
record hashes it seals and the previous block's hash.

| Field | What it holds | Sealed |
|---|---|---|
| `lineage_id` | The record's id (a UUID), returned on the turn's `done` frame | yes |
| `ts` | When the record was written: UTC epoch seconds | yes |
| `query_hash` | SHA-256 of the question | yes |
| `injected_json` | Each claim put into the prompt: its id, its weight and tier at injection, and whether the answer cited it | yes |
| `contradictions_json` | Each contradiction surfaced during the turn | yes |
| `model_id` | The model that answered, as NEXUS's provider names it; under an API backend, the model the API reported (`openai:…`, `anthropic:…`); under MCP, the model the client reported (`client-reported:…`) | yes |
| `prompt_prefix_hash` | SHA-256 of the stable system prefix | yes |
| `response_hash` | SHA-256 of the reply as delivered | yes |
| `answer_hash`, `reasoning_hash` | SHA-256 of the answer and of the model's reasoning, separated; no reasoning text is in the record | when set |
| `answer_chars`, `reasoning_chars`, `answer_empty_reason` | Their lengths, and why an answer was empty | no (re-derivable) |
| `request_hash` | SHA-256 of the whole request as sent (canonical messages) | when set |
| `context_json` | Which render built the prompt's dynamic block, its hash, and the concepts and activations it showed | no |
| `citation_test` | Which citation test produced the `cited` flags | no |
| `scope` | The process or programme the request named (`default` when none) | when set |
| `work_unit_id`, `boundary_asserted`, `boundary_asserted_by` | The work unit the request named, and a boundary it asserted and by whom | when set |
| `accountable_identity` | Who asked: the request's assertion, or the identity proxy's login | when set |
| `identity_mechanism` | How that identity was established: `asserted` (stored empty) or `proxy` | when set |
| `attribution_flags` | Declared attribution states | no (re-derivable) |
| `learning_mode` | `frozen` if learning was frozen when the record was written; empty when live | when set |
| `envelope_version` | The installed recalibration policy in force when the record was written (W6); empty when none was installed (the compiled default in force) and on every record before W6, which says nothing about the policy then | when set |
| `forecast` | The citation forecast's score for the turn (W6): how many claims it predicted and which, its coverage, whether the turn was scorable and why not, its precision and the null's; empty when no forecast ran | when set |
| `record_scope` | `mcp_block` when the client's model answered outside NEXUS; empty for the console's own turn | when set |
| `inference_seed`, `model_digest`, `inference_config_hash`, `environment_hash`, `capture_notes` | What a re-run needs: the seed, the weights' digest, the options and environment rows' keys, and why any is absent | when set |
| `sealed_block` | The archive block that sealed the record | no (it points at the seal) |

Beside the record, never in its hash: the exact request, compressed, in `request_bodies`, kept only if it hashes to
`request_hash`; the options and environment rows (`inference_configs`, `environments`); the backward index from a
claim to every record it was injected into (`lineage_injections`); each record's salted commitment per field and
their root (W8: `record_salts`, `record_commitments`, `record_field_commitments`), and its tombstone if one was
made (`record_tombstones`); and the actions NEXUS sent or a client reported (W10b: `action_records`, each with its
own sealed hash, its trigger and the turn's `lineage_id` when a turn sent it).

## Where NEXUS records more than a general audit log

These are facts about NEXUS; whether a given standard asks for them is for the standards half to say.

- **What the model was shown**, as claims by id with their standing at that moment, the dynamic block's hash, and
  the whole request's hash with the request itself kept beside the record.
- **What the answer used**: a cited flag per injected claim (a lexical test, which measurement puts at about a fifth
  under-reported: `docs/ARCHITECTURE.md`).
- **What the system was learning**: the learning state sealed per record.
- **What the store held at any instant** (`GET /kb/as-of`), and every answer that rested on a claim
  (`GET /belief/{id}/recall`).
- **The model's reasoning kept apart** from its answer, by hash and length only.
- **Proofs a stranger checks without the store**: a record's inclusion proof (`GET /lineage/{id}/proof`, W8), a
  decision credential and an action's receipt (`GET /lineage/{id}/credential`, `GET /actions/{id}`), each checked
  offline by `scripts/nexus_verify.py` against the instance's published key (W9, W10b).

## Where NEXUS records less

- **Actions.** NEXUS records the requests its own runtime sends for a person (W10b: the web search and the
  seeder's requests), each before it leaves, with its trigger, a key's name and fingerprint but never its value, the
  response's hash, and the accountable identity as a salted commitment. A client's own actions are recorded only as
  the client reports them (`nexus_record_action`, stored `client_reported`); a tool call a model makes elsewhere is
  not seen.
- **Signatures and an external time.** Archive blocks are signed with the instance's own Ed25519 key (W9), made by
  the instance and attested by nothing outside it; records are not signed one by one, and a record's inclusion proof
  binds it to a signed block (W8). Anchoring a block's hash to an RFC 3161 time-stamp authority is off by default
  (`NEXUS_ANCHOR`); without it, the times are the instance's own clock, and an owner with write access could rewrite
  the store and sign a new chain with the same key.
- **Identity.** The accountable identity is asserted by the request, or taken from an identity proxy that NEXUS
  trusts on loopback; NEXUS authenticates no one, and the record says which (`identity_mechanism`). OAuth and signed
  identity are not built (the remote connector, `docs/CONTRACTS.md`, "Not built yet").
- **Independent recording.** On the console, the runtime writes the record, outside the model's reach. Under MCP,
  recording is the client's choice: a turn the client does not record is not visible to NEXUS, and the coverage
  counters say so. The interceptor records every model exchange of a harness that is pointed at it, whatever the
  harness chooses to report (`docs/INTERCEPTOR.md`); a harness pointed elsewhere is not visible to it.
- **Content.** The record keeps hashes of the question, the reply, the answer and the reasoning, not their text
  (the kept request holds the prompt's text; the citation capture, off by default, holds answer text).

## IETF `draft-sharif-agent-audit-trail-10`, field by field

The draft defines a JSON audit record "with mandatory fields for agent identity, the authorizing principal, action
classification, outcome tracking, and trust level reporting", linked "via tamper-evident hash chaining using
SHA-256 per RFC 8785, with optional ECDSA signatures" (Abstract). Its unit is an agent's action; NEXUS's is a
model's answer. So the columns below compare a record of an action with a record of an answer, and a "none" often
means the two describe different events, not that NEXUS omits something the draft would want of an answer.

**The mandatory fields (§3.1, each REQUIRED):**

| The draft's field | What the draft says | NEXUS |
|---|---|---|
| `record_id` | "A UUID version 4 ... uniquely identifying this record" | `lineage_id`, a UUID version 4 (`backend/memory/lineage.py`) |
| `timestamp` | "formatted per RFC 3339 ... with mandatory UTC offset" | `ts`, UTC epoch seconds as a number, not an RFC 3339 string |
| `agent_id` | "A URI ... uniquely identifying the agent instance" | None. `environment_hash` names the NEXUS commit the answer ran under; no URI names the instance |
| `principal` | "the party on whose behalf and under whose authority the agent performed this action", with `principal_type`, `principal_id` (a URI) and `authority_ref`; at trust level L2 and above (new in -10) "authority_ref" "MUST be non-null and MUST reference an authorization artifact that is verifiable independently of the agent" | `accountable_identity` (a login or an asserted name, not a URI) with `identity_mechanism` (`asserted` or `proxy`). No principal type and no reference to an authorisation artifact: a confirmed act's confirmation is a field of the request itself (`confirmed=true`), not an artifact verifiable apart from NEXUS |
| `agent_version` | "The semantic version ... of the agent software" | None. The commit is in the environment row; no semantic version is recorded |
| `session_id` | "A UUID version 4 identifying the current session" | None as such. `work_unit_id` is the unit the request named, or the session pseudo-unit `session:<id>` (`session:default` on a console turn that names neither) |
| `action_type`, `action_detail` | One of "tool_call", "tool_response", "decision", "delegation", "escalation", "error", "lifecycle" (§7), with type-specific detail | The turn record is one kind, an answer; its nearest type is "decision" (§7.3), whose `decision_type` examples include "generate". Beside it, W10b's action records: a `kind` (`web_search`, `seed_conceptnet`, `seed_wikidata`, or a client's own word) and an evidence class (`runtime`, or `client_reported`); an outbound request's nearest type is "tool_call" |
| `outcome` | One of "success", "failure", "timeout", "denied", "escalated" | None per turn record. An empty answer is a declared state (`answer_empty_reason`); a record that could not be written is a declared `record_failed` frame with no `done` (W6), and by definition no record carries it. An action record carries `state` (`requested`, `completed`, `failed`) and `status` (the HTTP status or the error's class) |
| `trust_level` | "L0" to "L4", the agent's identity: L0 "No verification. The agent has no cryptographic identity."; L1 "Self-signed identity. The agent possesses a key pair but no external attestation." | None for the agent. The instance, the recorder rather than the agent, holds an Ed25519 key it made (W9, `GET /identity`) that nothing outside it attests: for the recorder the nearest description is L1's |
| `parent_record_id`, `prev_hash` | The previous record's `record_id`, and "The SHA-256 hash of the canonical JSON representation (per RFC 8785 ...) of the previous record" | None per record. NEXUS chains archive blocks, not records: each block carries its predecessor's hash and the digest of the record hashes it seals |
| `record_phase` | "pre_execution", "post_execution" or "concurrent" | None as a field. A console record is written after the answer has been generated and, by default, before any of it is released (W6, `NEXUS_ANSWER_RELEASE=after_record`); under `live` the answer is streamed first, as before W6 |

**Optional fields the record has a counterpart for (§3.2, §7.3, §13.3):**

| The draft's field | What the draft says | NEXUS |
|---|---|---|
| `model_id` (§3.2) | "The identifier of the language model used for the decision" | `model_id`, as the provider names it; under an API, the model the API reported; under MCP, `client-reported:<name>` |
| `input_hash`, `output_hash` (§3.2) | SHA-256 of the agent's input and output, "Used instead of raw input to preserve privacy" | `query_hash` (the question), `request_hash` (the whole request as sent), `response_hash` (the reply as delivered), `answer_hash` |
| `reasoning_hash` (§7.3) | "SHA-256 hash of the agent's reasoning chain or chain-of-thought" | `reasoning_hash`, the model's reasoning separated from its answer |
| `model_weights_digest` (§13.3) | "The SHA-256 digest of the model weight file(s)" | `model_digest`: the digest Ollama reports for the tag that answered, read from `/api/tags` (`OPERATOR_RUNBOOK.md`, W0b). Whether that equals a digest of the weight files was not checked here |
| `inference_config` (§13.3) | "The decoding parameters used", with the seed | `inference_config_hash` (the options kept beside the record) and `inference_seed` |
| `environment` (§13.3) | "The pinned execution environment" | `environment_hash` (commit, Python, OS, Ollama version, GPU and driver, httpx) |
| `recording_component` (§3.2) | "A URI identifying the component that wrote this audit record" | None. `record_scope` says whether NEXUS's own turn (empty) or a client's model (`mcp_block`) produced the answer |
| `signature`, `external_timestamp`, `batch` (§3.2, §6.4) | A signature over the record, a time-stamp authority's token, a Merkle batch root | Per archive block, not per record: an Ed25519 signature over each block (W9); an RFC 3161 time-stamp token over a signed block's hash when anchoring is on (`NEXUS_ANCHOR=rfc3161`, off by default; W9); a Merkle root over the block's records, with an inclusion proof per record (W8) |

The rest of §3.2 (`human_override`, `risk_score`, `latency_ms`, `cost_estimate`, `sanctions_check`,
`jurisdiction`, `deny_reasons`, `nonce`, `content_fingerprint`, `sequence_number`, `trust_assignment`,
`platform_attestation` and the signature fields) has no counterpart in the record.

**The draft's requirements, beside NEXUS:**

- **Hash computation (§6.1).** "Implementations MUST use JCS (RFC 8785) for canonicalization." NEXUS hashes a
  JSON body with sorted keys and compact separators; whether that agrees with JCS byte for byte was not tested.
- **Pre-execution recording (§4).** A "denied" or "escalated" decision record "MUST have record_phase set to"
  `pre_execution` (§4.2); for an Annex III system, an action that "modifies external state" "MUST be preceded by
  a record" (§4.3). NEXUS writes an action's row (`requested`) before its request leaves and sends nothing it
  cannot record (W10b); its record of an answer is written after the answer.
- **Recording independence (§5).** At L0 and L1 an agent "MAY write its own audit records" (§5.1). At trust level
  L2 and above, records "MUST be written by a component independent of the agent" (§5.2; -09 said SHOULD), which
  "MUST execute under a distinct operating-system security principal" and sign with "its own key". On the console, NEXUS's runtime writes the record in the same process that calls the model: in the
  draft's terms that is self-recording, though the model itself cannot write or edit it. Under MCP, NEXUS is a
  separate process, but it runs as the same user as the client, holds no key, and records only what the client
  sends to `nexus_record`. The interceptor (`docs/INTERCEPTOR.md`) is the component the draft asks for in kind: it sits between a
  harness and its model, records what passes, and signs each block with its own key. Whether it runs under a
  distinct operating-system principal is how it is deployed; started by the same user as the harness, it does not.
- **Privacy (§12.1).** "Raw input and output data MUST NOT be stored in audit records." NEXUS's record holds
  hashes. The exact request text is kept beside it, in the same store (`request_bodies`, on by default:
  `NEXUS_KEEP_REQUESTS`); the draft asks that raw data kept for debugging be stored "in a separate system with
  appropriate access controls, linked to the audit trail via record_id".
- **Erasure (§9.3, §12.2).** The draft deletes by tombstone records that keep the chain intact. NEXUS deletes no
  claim; a record can be tombstoned (W8): its content is disposed of, its hash, commitment root and inclusion proof
  stay, and the proof still verifies. Disposal of kept requests by policy is not built yet.
- **Its own regulatory mapping (§11).** The draft states that its mappings "are INFORMATIVE" and "are not a
  compliance assessment".

## ISO/IEC 24970, AI system logging (not read)

Not read here (see "Sources"). The only statement this page can quote about it is the IETF draft's, which is that
draft's account and not ISO's: ISO/IEC 24970 (Artificial intelligence -- AI system logging), "at FDIS stage in
ISO/IEC JTC 1/SC 42 at the time of writing, defines common capabilities, requirements, and an information model
for logging events in AI systems" (draft -10, §11.4). Nothing is mapped to it.

## EU AI Act: Article 12 and the logs' keeping, with its dates as amended

Quoted from the Official Journal texts (see "Sources"). Nothing here says whether a NEXUS deployment is a high-risk
AI system, or part of one: that is a question about the deployment, not about NEXUS, and this page does not answer
it. Being beside these articles is not meeting them.

**What the Act requires of the logs** (Regulation (EU) 2024/1689):

- Article 12(1): "High-risk AI systems shall technically allow for the automatic recording of events (logs) over the
  lifetime of the system."
- Article 12(2): "In order to ensure a level of traceability of the functioning of a high-risk AI system that is
  appropriate to the intended purpose of the system, logging capabilities shall enable the recording of events
  relevant for: (a) identifying situations that may result in the high-risk AI system presenting a risk within the
  meaning of Article 79(1) or in a substantial modification; (b) facilitating the post-market monitoring referred
  to in Article 72; and (c) monitoring the operation of high-risk AI systems referred to in Article 26(5)."
- Article 12(3) sets a minimum for the systems of Annex III, point 1(a): the period of each use, the reference
  database the input was checked against, the input data that led to a match, and the natural persons who verified
  the results.
- Article 19(1), providers: "Providers of high-risk AI systems shall keep the logs referred to in Article 12(1),
  automatically generated by their high-risk AI systems, to the extent such logs are under their control. Without
  prejudice to applicable Union or national law, the logs shall be kept for a period appropriate to the intended
  purpose of the high-risk AI system, of at least six months, unless provided otherwise in the applicable Union or
  national law, in particular in Union law on the protection of personal data."
- Article 26(6), deployers: "Deployers of high-risk AI systems shall keep the logs automatically generated by that
  high-risk AI system to the extent such logs are under their control, for a period appropriate to the intended
  purpose of the high-risk AI system, of at least six months, unless provided otherwise in applicable Union or
  national law, in particular in Union law on the protection of personal data."

**When it applies.** Articles 12, 19 and 26 are in Chapter III: Article 12 in Section 2, Articles 19 and 26 in
Section 3. Article 113 as published applied the Act from 2 August 2026, and Article 6(1) with its obligations from 2
August 2027. Regulation (EU) 2026/1744, Article 1, point (40)(b), replaced Article 113's third paragraph, point (c):
"Chapter III, Sections 1, 2, and 3, with the exception of Article 6(5), shall apply from: (i) 2 December 2027 as
regards AI systems classified as high-risk pursuant to Article 6(2) and Annex III; and (ii) 2 August 2028 as regards
AI systems classified as high-risk pursuant to Article 6(1) and Annex I". That regulation entered into force "on the
third day following that of its publication in the Official Journal", published 24.7.2026.

**Beside NEXUS**, as facts about NEXUS and not a reading of the Act: a record is written automatically for every
answer and sealed (the field table above); every request NEXUS sends for a person is recorded (W10b); NEXUS deletes
no record, so keeping logs for at least six months is a matter of not tombstoning them; the record says which
identity asked and how it was established, and holds the claims the model was shown. Article 12(3)'s minimum
concerns a kind of system (remote biometric identification) that NEXUS is not built for.
