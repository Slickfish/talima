# CHANGELOG

## Since 1.0.0

- **The Talima v1 specification.** `docs/TALIMA_SPEC.md` states the standard so that it can be implemented without
  reading NEXUS: the record's fields, the canonical encoding, each hash, the seal, the chain's links, the Merkle
  proofs, the receipts, the credential and each verification rule, with the 1.0.0 fixtures as its test vectors. A second
  producer written from it alone (`conformance/minimal_producer.py`, the Python standard library only) makes
  records and a chain that `talima-verify` accepts, and the tests break each of its 48 rules in turn and require
  the failure the document names. The verifier gained what that needed: `--chain` for a chain document, the
  record recomputed when every field is disclosed, a header's shape and a key's id. It is also stricter than
  1.0.0's where a document or a command line was wrong: a `--key` or a `--reveal` salt that is not one ends with
  status 2 and no verdict, and an identity whose `keys` is not a list with status 2; a receipt whose block is not
  labelled layout 2, and a proof whose block is labelled neither 2 nor the older 1, are refused
  (`block_malformed`); a document that holds a NaN, a name given twice, upper-case hexadecimal where a byte
  string is read, or `true` where an integer belongs is refused. The 1.0.0 fixtures get the verdicts they got.
  `talima-intercept chain` writes the interceptor's chain.
- **The public repository and its image.** The project is published as `Slickfish/talima`
  (https://github.com/Slickfish/talima) and its image is `ghcr.io/slickfish/talima`. The compose file's default
  image names it (so do `deploy/cloud-init.yaml`, `.env.example` and `docs/DEPLOY.md`), and the image's source
  label and the contact the seeder sends to Wikidata name the repository. The release workflow names its image
  after the repository it runs in (`ghcr.io/<owner>/<repository>`, in lower case) and writes no name of its own,
  so a fork's release pushes the fork's image. A deployment that pulled the earlier default sets `NEXUS_IMAGE`
  to the image it wants.
- **The interceptor.** A standalone recorder an agent harness is pointed at as its model endpoint
  (`python -m backend.intercept`, the binary `talima-intercept`): every exchange passes through on its way to the
  provider and is recorded, the request before it is sent and the response when it is complete, and sealed into a
  chain `talima-verify` checks offline. It speaks the Anthropic Messages and the OpenAI-compatible Chat Completions
  protocols, relays a stream event by event and byte for byte, fails closed (a harness holds a completed response
  only if its record is written), stores a credential by its header's name and a fingerprint only, and records each
  tool use the model asks for and each result the harness reports as seen, never as executed. It loads no memory
  engine, and a guard read from the import graph holds that. Tested against a stub provider; the check against a
  live one is `scripts/intercept_smoke.py`, run with your own key. `docs/INTERCEPTOR.md`.

## 1.0.0 (20 November 2026)

The first public release of NEXUS, the reference engine of the Talima record and verification standard
(`docs/TALIMA.md`).

- **A sealed record of every answer.** When the model answers, NEXUS writes a hashed record of the turn: what the
  model was shown, which claims the answer used, a hash of what it said, whether NEXUS was learning, and who asked,
  when the request or an identity proxy names someone. The console releases an answer only after its record is
  written, and the idle cycle seals the records into a signed, hash-chained archive.
- **Proofs and credentials a stranger checks offline.** Each sealing block carries a Merkle root over its records
  and is signed with the instance's own Ed25519 key (`GET /identity`). A decision credential discloses only the
  fields you choose, and `nexus-verify` (also named `talima-verify`) checks it with no access to the store. A
  commitment seals a note now, to reveal later.
- **Actions recorded beside the answers.** Every request NEXUS sends for a person is recorded before it leaves and
  sealed; signing those requests under RFC 9421 is off by default.
- **The record read back.** Every answer that rested on a given claim, oldest first; and what the store held at any
  past instant.
- **Governed memory.** A claim is never deleted (archival is a recorded act), a weight adjustment is a recorded event
  within a bound, contradictions are kept and surfaced, and learning can be frozen, with the learning state sealed
  into every record. A new instance starts frozen.
- **The reference gateway, switched off by default**, for search results on their way into the prompt: it reduces
  what reaches the model and measures what it withheld (`docs/REFERENCE_GATEWAY.md`).
- **Ways to run it.** The console on your own computer with a local model (Ollama or LM Studio) or an API model; a
  desktop bundle for the Claude desktop app, over MCP; a container image and compose profiles, with a Postgres store
  and a hosted profile behind an identity proxy; backup with a restore check.
- **Replay capture.** The seed, the model digest, the options and the exact request are kept beside each record.

What this release does not do is listed in `docs/CONTRACTS.md`, "Not built yet".
