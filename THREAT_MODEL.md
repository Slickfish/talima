# NEXUS Threat Model

Honest map of the current security posture. This document describes what IS
implemented, not aspirations; what is not built is said where it would be
used.

## What NEXUS is (security-relevant)

A FastAPI server on hardware its operator controls. Its stores are SQLite
files on local disk or, for the hosted profile, a Postgres database
(`NEXUS_STORE=postgres`). It answers with a local model (Ollama or LM
Studio) or an API model (OpenAI-compatible or Anthropic), in which case the
prompt, with the claims put into it, and the API key leave the machine for
that API; embeddings go to the same backend or to `NEXUS_EMBED_URL`. Every
outbound request it makes for a person is recorded before it leaves: the
seeds from Wikidata and ConceptNet and the web search through SearXNG
(`SEARXNG_URL`), each confirmed (Law 3). One more outbound path is off by
default: anchoring to an RFC 3161 authority. Signing the seed and search
requests under RFC 9421 (also off by default) adds headers to them and opens
no path of its own. Identity is
one of two modes: a bearer token (one operator), or an identity proxy in
front that names the person, one owner per process; the hosted profile runs
one process and one database per tester. The desktop bundle runs the MCP
server over stdio, with the client's own model.

## Assets

- `data/kb.db` — accumulated knowledge base (potentially sensitive).
- `data/archive.db` / archive chain — integrity-critical audit record.
- The live chat stream (SSE) — conversational content in transit.
- `NEXUS_API_TOKEN` — the single API credential.
- `data/chroma/` — vector store derived from KB content.
- `instance_key.json` beside `kb.db` — the instance's Ed25519 signing key (W9): whoever holds it can sign blocks
  and credentials as the instance. Never in a backup.
- `held_commitments` in `kb.db` — notes a person committed to with `POST /commit` (W9), kept with their salts.
- `request_bodies` in `kb.db` — the exact text of each request sent to the model, kept beside its record
  (`NEXUS_KEEP_REQUESTS`, on by default): the prompt, with the claims and the history it carried.
- `action_records` in `kb.db` — each outbound request NEXUS sent for a person: its target, its hash, a key header's
  name and fingerprint (never its value), and the accountable identity, in clear, with a salted commitment of it in
  the record's hash (so the open lookup by signature names no person, and the token-gated receipt does).
- Backups (`scripts/backup.py`) — copies of the stores and the files beside them: every asset above but the
  instance key and `.env`, which holds the token.
- The identity proxy's header — in proxy mode, whoever can reach the loopback port with that header is the owner.

## Trust boundaries

```
user/browser ─(token, or an identity proxy in front)─ NEXUS API (127.0.0.1:8000 by default)
   NEXUS API ↔ the model: Ollama / LM Studio (local), or an API (prompt and key leave the machine)
             ↔ the store: SQLite on local disk, or Postgres (the hosted profile)
             ↘ seed sources (Wikidata/ConceptNet) and SearXNG: confirmed, each request recorded first
             ↘ an RFC 3161 authority (NEXUS_ANCHOR=rfc3161, off by default): each signed block's hash
MCP client (Claude Desktop) ─stdio─ the bundle's NEXUS (no network listener)
```

## Current protections (verified in code)

- **Default bind 127.0.0.1** (`start.py`'s `resolve_bind`). LAN exposure
  requires an explicit `NEXUS_HOST` opt-in, and `start.py` warns for any host
  that is not loopback (since W1; before it only `0.0.0.0` warned, not `::`
  or a LAN address).
- **Bearer-token auth on every state-changing endpoint** (POST/PUT/DELETE,
  including `/chat` — it writes to the substrate via extraction and
  consolidation). Constant-time comparison (`secrets.compare_digest`).
  Token from `NEXUS_API_TOKEN`, or generated once per process the first time
  it is needed (a gated request, or the console page) and printed once.
- **All SQL parameterised** — audit found zero string-built queries
  (`backend/kb/tiered_store.py` and all other DB code use `?` placeholders).
- **No eval / exec in `backend/`, and one subprocess site**: W0b's environment
  read (`backend/providers/replay_capture.py`) runs `git` and `nvidia-smi` as
  argument lists, no shell, a 5 s timeout, once per process. (This line said
  "no subprocess anywhere" until W1 found the W0b site.) `start.py` creates a
  process at four places and never through a shell (W1, by AST guard): two
  children of its own interpreter (`-m venv`, `-m pip install -r
  requirements.txt`, each only on a yes) and the one hand-over, whose argv is
  either this interpreter running uvicorn or the repo's `.venv` interpreter
  re-running `start.py`.
- **Law-gated destructive operations** additionally require `confirmed=true`
  in the request body (seeding, dedup-archival, web search).
- **Signed blocks and offline credentials (W9).** Every archive block appended
  is signed with the instance's Ed25519 key (`backend/memory/instance_key.py`);
  `GET /identity` serves the public keys, each with its period as a block
  range. `scripts/nexus_verify.py` checks a credential or a reveal with no
  store and no NEXUS import (an AST guard holds it to the standard library),
  and names a signature by a key the pinned identity does not list, or
  outside that key's period, and any disclosed value its commitment does not
  open.

## Known limitations (honest)

- **In token mode (the default), most GET endpoints are unauthenticated**:
  the evidence reads (`/lineage/*`, `/belief/*`, `/governance/*`,
  `/kb/as-of`, `/work-units/*`, `/commitments/*`, `/actions/*`) and `/whoami`,
  seventeen routes (A-2, W4, W6, W8's proof, W9's credential and commitment
  receipt, W10b's action receipt), require the token,
  and every other GET (`/kb/stats`, `/cortex/*`, `/events/recent`,
  `/memory/column`, the UI pages) is open. Mitigated by the localhost bind;
  MUST be revisited before any LAN exposure (proxy mode, below, closes them).
  `tests/test_auth_coverage.py` derives the gated reads from the router and
  checks this sentence, SECURITY.md's and `.env.example`'s against them.
- **No TLS.** Localhost assumption. TLS via reverse proxy is REQUIRED if the
  service is ever exposed.
- **Single shared token, no per-user identity or roles.** Anyone with the
  token has full write access.
- **The token is served to the browser page** (the console page at `/`
  embeds it for same-origin fetches). Any local process that can fetch the
  page can obtain the token.
  Acceptable under the single-operator localhost posture; not acceptable on
  a shared host.
- **No encryption at rest** for `kb.db`, `archive.db`, or the Chroma store.
- **The instance key sits beside the store (W9).** Owner-only on POSIX; on
  Windows the data folder's permissions are its only protection. Whoever can
  read the data root can sign as the instance. There is no revocation, and
  `/identity` is the instance's own word for its keys: a recipient should
  take it by a channel they trust, or pin a key with `--key` (a pinned key is
  taken as active at every block, so pinning an old key after a rotation accepts
  its copy's signatures anywhere), and take it again
  after a rotation the operator announces: an identity taken before one leaves
  the old key's range open, which is what a copy of that key is checked against.
- **An undisclosed field is hidden jointly, not alone (W9).** The record hash
  rides every credential's proof (W8's leaf) and is over every field, so a
  credential that discloses every field but one, the lineage id included,
  lets that one be tested against guesses. A credential names the lineage id
  only when it is disclosed, so its random bits hide the rest from whoever lacks
  it; the id is no secret from anyone who reaches the instance (it rides every
  `done` frame and the open `/events/recent`), and with it small undisclosed
  fields can be guessed together.
- **A signature proves who sealed, not when (W9).** With the store and the key
  file, an owner can rewrite a history and sign it consistently; anchoring
  (`NEXUS_ANCHOR=rfc3161`, off by default) dates each block by an authority,
  so a rewrite cannot claim to be older than the stamps it lacks.
- **A signed request names its sender (W10b).** With `NEXUS_SIGN_REQUESTS=on`
  every request NEXUS sends for a person carries the instance key's id, and
  `/identity/signed-action` gives whoever holds a signature, with no token, that
  action's receipt: its kind, target, request hash, status, trigger and seal,
  with the accountable identity only as a salted commitment. The signature
  value is the capability; it travels in the request's own headers. Off by
  default.
- **SSE chat stream** is plaintext HTTP on localhost.

## Proxy mode (`NEXUS_AUTH_MODE=proxy`, W4)

For a process behind an identity proxy (Tailscale Serve, Cloudflare Access,
a pod's reverse proxy), one process per person. Decided once at startup;
any value but `token` and `proxy` is refused by name, having opened nothing.

- **The boundary is a loopback peer and one header, and the process answers
  for one owner.** One middleware (`backend/identity.py`, `IdentityGate`)
  takes every request the app serves (the routes, `/vendor`, the framework's
  `/docs`, `/redoc` and `/openapi.json`) except `/health` and `/healthz`,
  and applies, in order: the peer is a loopback address with a real port
  and not an address the request's `X-Forwarded-For` names (else 401); the
  identity header (`NEXUS_IDENTITY_HEADER`, default `Tailscale-User-Login`)
  arrives exactly once and non-empty (else 401); an owner is set (else 403
  "owner not set", never adoption); the login is the owner's, compared
  exactly (else 403); a request that could change state did not come from
  another site (else 403, below). `require_token` requires the middleware's
  verdict, so a gated route refuses even if the middleware were missing.
- **Exactly means exactly.** Only the spaces and tabs around the header's
  value and around `NEXUS_OWNER_IDENTITY` are trimmed. Nothing is
  case-folded, normalised or split at a comma. So the owner's login in
  another case, with a U+00A0, U+2003 or U+3000 beside it, in fullwidth
  letters or with a lookalike letter is another login, and is refused.
- **A request from another site is refused.** The proxy attaches the login
  to every request the person's browser sends, so a page from another site
  that they open could post to NEXUS as them. So the last step, after the
  owner checks, refuses with 403 any method but GET, HEAD and OPTIONS whose
  `Sec-Fetch-Site` header is present and is not `same-origin`, and one that
  carries `Origin` with no `Sec-Fetch-Site`. The refusal is one fixed
  sentence. It names neither the origin nor the login. The console's own
  requests say `same-origin`. A request with neither header comes from a
  client that is not a browser (curl, the operator's scripts, the simulated
  UAT), and is not refused.
- **What the cross-site rule rests on.** NEXUS cannot compare `Origin` with
  its own origin, because it reads no `Host` and a proxy may rewrite it. The
  browser can, and says what it found in `Sec-Fetch-Site` (W3C Fetch
  Metadata Request Headers, §2.3). A browser that predates the header sends
  `Origin` on a POST and is refused, so the rule fails closed for it. A
  browser that sends neither header is not refused. The proxy must pass
  both headers through as the browser sent them. Serve the console over
  HTTPS or on loopback: a browser appends `Sec-Fetch-Site` only to a
  request whose URL is "potentially trustworthy" (W3C Fetch Metadata
  Request Headers, §3; W3C Secure Contexts, §3.1: `https`, `wss` and the
  loopback ranges `127.0.0.0/8` and `::1/128`), so over plain HTTP at another address the console's own writes
  carry `Origin` alone and are refused. That fails closed, and every
  documented path is HTTPS or loopback (BACKLOG W4-D4). GET and HEAD are not
  checked: no GET route writes, and no response carries CORS headers, so a
  page from another site cannot read one. The rule does not cover a program
  on the person's own device that is not a browser. Behind Tailscale, which
  identifies the device, such a program is the person as far as NEXUS can
  tell.
- **The proxy authenticates the person; NEXUS does not.** A bearer token is
  no credential in proxy mode: none is accepted, minted, printed or served
  to the console page, which shows "running as <login>" from `/whoami`.
  Each turn's record carries the login as `accountable_identity` and
  `identity_mechanism` `proxy` (W4 part 1b); a request body naming another
  `accountable` is refused (422 `accountable_mismatch`) and writes nothing.
  Governed acts' `actor_id` and the work units' actor fields stay what the
  body asserts (a register line).
- **What the identity rests on.** The proxy removes a client's own copy of the
  identity header before setting its own: NEXUS takes whatever a loopback
  peer sends. Every process in the network namespace is a loopback peer, so
  nothing else may run in it. uvicorn runs with `--no-proxy-headers`
  (`start.py` passes it in proxy mode): with proxy headers on, uvicorn
  replaces a trusted peer with an address from `X-Forwarded-For` and keeps
  the original nowhere, and uvicorn 0.54.0, the version installed here,
  carries an entry's port too (`_parse_host_port` in
  `uvicorn/middleware/proxy_headers.py`), so a launch that trusts every
  address would let a remote peer write
  itself in as `127.0.0.1:4444`. The peer rule refuses port 0 and any peer
  an `X-Forwarded-For` entry names, so that launch fails closed.
- **The only open paths are `/health` and `/healthz`**: liveness for a
  container's HEALTHCHECK and a monitor, with no belief content. `/healthz`
  says the mode, the header and whether an owner is set, never the login.
- **Not built, and said:** a governed act's `actor_id` is still the body's
  assertion; the real identity providers are documented, not exercised;
  each owner's process must have its own data root (nothing refuses two
  sharing one under Postgres).

## Deployment posture

- **Default: localhost only.** This is the supported configuration for one
  operator.
- For testers on a server: the hosted profile (`docs/DEPLOY.md`), proxy mode
  behind an identity proxy you run (Tailscale Serve or Cloudflare Access),
  which terminates HTTPS; Caddy only relays to each tester's NEXUS there, on
  plain HTTP inside the host. Do **not** simply set `NEXUS_HOST=0.0.0.0`.
- Keep `.env` (token) out of git — it is gitignored; verify before pushing.

## Hardening: what is built and what is not

1. **Reads that return belief content need the token** (built): seventeen gated reads in token mode, listed in
   `SECURITY.md`; in proxy mode every path but `/health` and `/healthz` needs the proxy's identity.
2. **Per-user tokens and roles** (none): one owner per process; the hosted profile isolates testers by process and
   database, not by role.
3. **Encryption at rest** (none of NEXUS's own): the disk's or the database's encryption is the protection.
4. **TLS** (a proxy's, never NEXUS's own): under the hosted profile the identity proxy terminates HTTPS; under the
   `stub`, `ollama` and `api` profiles Caddy serves it (`docs/DEPLOY.md`); the console itself serves plain HTTP on
   loopback.
