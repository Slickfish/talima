# Talima: receipts for AI

Prove what your AI was shown, and what it said.

<p align="center"><img src="docs/brand/talima-mark-clay.svg" width="96" alt="Talima"></p>

[![Licence](https://img.shields.io/github/license/Slickfish/talima)](https://github.com/Slickfish/talima/blob/main/LICENSE) [![Latest release](https://img.shields.io/github/v/release/Slickfish/talima)](https://github.com/Slickfish/talima/releases/latest) [![Specification: Talima v1](https://img.shields.io/badge/spec-v1-C2410C)](docs/TALIMA_SPEC.md)

## Quick start: verify the test vectors

```sh
git clone https://github.com/Slickfish/talima.git
python3 talima/scripts/talima_verify.py talima/tests/fixtures/tb_golden/credential.json --identity talima/tests/fixtures/tb_golden/identity.json
python3 talima/scripts/talima_verify.py --chain talima/tests/fixtures/w11d/chain.json --identity talima/tests/fixtures/tb_golden/identity.json
```

The second and third commands each end with `talima-verify: VERIFIED`: a credential, and a chain of sealed blocks,
from the specification's test vectors (`docs/TALIMA_SPEC.md`, section 14), each checked offline against the identity
it is pinned to. The verifier needs Python 3.11 or later and nothing else installed; on Windows, write `py -3` for
`python3`.

## How it compares

| | What it records |
|---|---|
| Ordinary logs | Lines an application chooses to write, usually unsealed: nothing in them shows later that a line is as it was first written, or that what the model was given is all there. |
| SCITT receipts | That an issuer's signed statement was registered in a transparency service's append-only log: proof of inclusion, for whatever the statement says ([RFC 9943](https://www.rfc-editor.org/rfc/rfc9943)). |
| Agent audit-trail drafts | Proposed, not standard: [draft-sharif-agent-audit-trail](https://datatracker.ietf.org/doc/draft-sharif-agent-audit-trail/), an individual Internet-Draft, hash-chains records of an agent's actions: who acted, what it did and with what outcome, with hashes of its input and output. |
| Talima | What a model was shown and what it said, as hashes written when it answered, then sealed into a signed hash chain, with a credential anyone can check offline (`talima-verify`). |

---

Talima is the open record and verification standard: a sealed record of what an AI model was shown and said,
and a credential anyone can check offline. NEXUS is its reference engine, and this page is about NEXUS
(`docs/TALIMA.md` describes the standard and `docs/TALIMA_SPEC.md` specifies it).

# NEXUS

**The receipt comes with the answer.**

NEXUS is a belief substrate that sits between a person and an AI model. It keeps a belief store, in which each
claim carries its source, its tier and an evidential weight (relative and uncalibrated, deliberately not called
confidence), and it puts the relevant claims into the model's prompt. When two claims contradict (detected over a
configurable set of opposed relations), both are kept and the contradiction is surfaced; neither is overwritten.
At the moment the model answers, NEXUS writes a hashed record of the turn: what the model was shown, which claims
the answer used, a hash of what it said, whether NEXUS was learning at the time, and who asked (when the request or
an identity proxy names someone). The next idle cycle seals it into a signed, hash-chained archive. Later
it can show which answers rested on a given claim and what the store held at any past instant. The memory is
governed: a claim is never deleted (archival is a recorded act), a weight adjustment is a recorded event within a
bound, and learning can be frozen, with the learning state sealed into every record. A new instance starts frozen.
NEXUS runs on your own machine or your own server, with a local model (Ollama or LM Studio), an API model
(OpenAI-compatible or Anthropic), or, from the Claude desktop app, the app's own model over MCP.

## Status

Version 1.0.0, the first public release (`CHANGELOG.md`); these pages describe it. The release workflow publishes
the desktop bundles and the verifier (`nexus-verify`, and `talima-verify`, the same program under the standard's
name) as a release's assets and pushes the container image; each can also be built from the source
(`scripts/build_bundle.py`, `scripts/nexus_verify.py`, `docs/DEPLOY.md`).

**Built and tested**: the suite and a simulated UAT (`scripts/uat_simulate.py`) on Linux at each item's close;
Windows by the maintainer's own run of the suite at each checkpoint (the last on 19 November 2026, at version 1.0
for user acceptance testing; the Talima name and this public snapshot came after it); macOS in CI up to W5, and for
the rest at version 1.0's dispatch. What is built: the console and its
sealed record; recall of every answer that rested on a claim; the store as of an instant; the freeze switch, with the
learning state on every record; replay capture (the seed, the model digest, the options and the exact request kept
beside each record); the desktop bundle; the container image and compose profiles; the Postgres store backend; the
hosted profile with identity from a proxy; backup with restore verification; and a reference gateway for search
results, built switched off (`docs/REFERENCE_GATEWAY.md`; W5).

The recorder fails closed (W6): the console releases an answer only after its record is written, so the answer
appears whole, not as the model writes it, and a record that cannot be written ends the turn with a declared
`record_failed` frame and no answer (`NEXUS_ANSWER_RELEASE=live` restores the earlier order). The record carries the
recalibration policy in force and the citation forecast's score, and a work unit's close is a sealed governance
record that a reopen reverses with a new record, never an edit.

**Proofs, credentials and actions** (W8, W9, W10b):

- W8: each sealing block carries a Merkle root over its records; `GET /lineage/{id}/proof` is an inclusion proof
  that verifies from itself alone, and a tombstoned record keeps its proof.
- W9: every block is signed with the instance's own Ed25519 key, listed at `GET /identity`; `GET
  /lineage/{id}/credential` is a decision credential that `scripts/nexus_verify.py` (`nexus-verify`) checks offline,
  with no access to the store; `POST /commit` seals a commitment to a note you reveal later; anchoring each block to
  an RFC 3161 time-stamp authority is off by default.
- W10b: every request NEXUS sends for a person (the web search, the seeder's requests) is recorded before it leaves
  and sealed (`GET /actions/{id}`, checked with `nexus-verify --action`); an MCP client reports its own actions with
  `nexus_record_action`; signing outbound requests under RFC 9421 is off by default (`NEXUS_SIGN_REQUESTS`).

- W10c, built after 1.0.0: the interceptor, a standalone recorder an agent harness is pointed at as its model
  endpoint (`python -m backend.intercept`, `talima-intercept`). Every exchange is recorded on its way to the
  provider, the request before it is sent, and verified offline like any other record (`docs/INTERCEPTOR.md`).

**Not built yet** (the list is in `docs/CONTRACTS.md`): the remote connector, the work-unit ledger, the unfreeze
decision, the legacy prompt render's retirement, retention by policy with what follows it, and a chain document
served by the console. Until the unfreeze decision is built, learning stays frozen on every new instance, and switching it on
is the operator's decision.

Replay capture keeps what a re-run needs. Whether a real model reproduces its answers from it has not been measured
yet, so no document claims that decisions can be re-run.

## Quickstart (no model server needed)

Python 3.11 or later (`start.py` refuses an older one; CI tests 3.11). The stub backend gives one fixed answer and runs no model, so this tries the record and the
console, not a model. Run on Linux, 20 November 2026; on Windows use `.venv\Scripts\python` and `copy`.
`docs/FIRST_RUN.md` takes it from here to a credential a stranger verifies.

```bash
git clone <this repository's URL> nexus && cd nexus
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
sed -i 's/^LLM_BACKEND=.*/LLM_BACKEND=stub/' .env
.venv/bin/python start.py --check
.venv/bin/python start.py --serve
```

Then open http://127.0.0.1:8000. With `NEXUS_API_TOKEN` empty in `.env`, the server makes a token and prints it in
its console the first time a gated request or the console page needs one. Stop it with Ctrl+C.
`docs/FIRST_RUN.md` has the next steps: a real model, the gated routes, seeding, and a credential a stranger
verifies. On macOS, `sed -i` needs an empty argument (`sed -i ''`), or edit the line by hand.

## Ways to engage

| Way | What it is | Status | Start here |
|---|---|---|---|
| **Self-run console** | NEXUS on your computer, with its web console at 127.0.0.1:8000 and a local or API model | Built | `docs/FIRST_RUN.md` |
| **Self-run desktop bundle** | NEXUS as a local MCP extension for the Claude desktop app (macOS, Windows): the app's model answers, NEXUS supplies the context and keeps the record | Built (`scripts/build_bundle.py`, CI's `bundle` workflow); the release workflow attaches the bundles to a release | `docs/FIRST_RUN.md` |
| **Hosted console** | One NEXUS per tester on a server, each behind an identity proxy that you run (Tailscale Serve or Cloudflare Access), each tester's store in Postgres | Built (the `hosted` compose profile); the identity proxy is yours to set up | `docs/DEPLOY.md` |
| **Remote connector** | NEXUS on a server, reached by a desktop app over the network (MCP over HTTP, OAuth) | **Not built** ("Not built yet" in [docs/CONTRACTS.md](docs/CONTRACTS.md)) | |
| **Interceptor** | A standalone recorder an agent harness is pointed at as its model endpoint: every exchange is recorded on its way to the provider. No memory engine | Built, after 1.0.0 (`python -m backend.intercept`); tested against a stub provider, not a live one | `docs/INTERCEPTOR.md` |

## Where to read next

- `docs/FIRST_RUN.md`: your first hour, step by step, every command shown with what it prints; then the console
  with a real model, and the desktop bundle.
- `docs/DEPLOY.md`: the container image, the compose profiles, a fresh host, the hosted profile.
- `docs/INTERCEPTOR.md`: the interceptor for agent harnesses: what it records and cannot see, how to point a
  harness at it, how to verify a session.
- `docs/OPERATOR_RUNBOOK.md`: start, stop, backup, restore, the instance scripts.
- `SECURITY.md` and `THREAT_MODEL.md`: what is and is not a security boundary.
- `docs/ARCHITECTURE.md`: how it is built. `docs/INTEGRATION_REFERENCE.md`: the HTTP and event surface.
- `docs/CONTRACTS.md`: the laws, the settings, the events a turn streams, the record's contracts, and what is not
  built yet.
- `CONTRIBUTING.md`: how a change is made and tested.
- `docs/STANDARDS_MAPPING.md`: the record beside published audit-log standards (no conformance claimed).
- `CHANGELOG.md`: what each release holds.
- `docs/GLOSSARY.md`: the ids the documents use (`W3b`, "law 6", `B-17`, `FZ-2`) and where to read about each.

## Licence

The terms are in `LICENSE`.
