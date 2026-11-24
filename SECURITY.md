# Security policy

## Reporting a vulnerability

Open a private security advisory on the GitHub repository (Security → Advisories → "Report a vulnerability"), or
contact the maintainer directly. Whether private vulnerability reporting is switched on for the repository was not
checked when this was written. Please do not file a public issue for an exploitable bug.

## Supported versions

Version 1.0.0 is the first public release (`CHANGELOG.md`), and `main` is the only supported branch. There are no
backports.

## What is a security boundary and what is not

`THREAT_MODEL.md` is the longer map. This table is the short answer, for the code as it stands (checked on
16 November 2026 against the code at `48ad15c`; the W9 rows on 17 November and the W10b row on 18 November).

| Mechanism | What it protects against | What it does not |
|---|---|---|
| **Loopback bind** (`NEXUS_HOST=127.0.0.1`, the default) | Other machines on the network: they cannot connect. `start.py` warns before launching on any host that is not loopback. | Anything on the same machine: every local process and user that can open a socket reaches the console. |
| **The bearer token** (`NEXUS_API_TOKEN`, token mode, the default) | A request without it, to a state-changing route or a gated read (constant-time comparison). With no token set, one is generated per process and printed once. | A local process: the console page at `/` is open and carries the token for its own requests, so anything that can fetch the page can read the token. It is one shared credential with no roles; whoever holds it may write everything. |
| **Proxy identity** (`NEXUS_AUTH_MODE=proxy`) | A request that does not come from a loopback peer with the identity header exactly once and the owner's exact login; a state-changing request a browser sent from another site. | NEXUS authenticates no one: the identity proxy in front does. Every process in NEXUS's network namespace is a loopback peer, so nothing else may run there. Details below. |
| **The single-writer lock** (`kb.db.lock`; a Postgres advisory lock) | Two NEXUS processes writing one store by accident. | Anyone or anything else: it is an integrity guard between cooperating NEXUS processes, not access control. It is per machine, and a store in a folder synced between computers is not protected across them. |
| **The hash chain and the sealed record** | Undetected edits: `/lineage/verify`, `/governance/verify` and `/chain/status` name a record, a governance row or an archive block whose content no longer matches its hash. | An owner with write access to the store, who can rewrite records and recompute the chain. A record's inclusion proof can be checked without the store (W8), and since W9 every block is signed: a credential verifies offline with `nexus-verify`. Whoever holds both the store and the key file can still write and sign a consistent history; anchoring, when on, bounds how far back. |
| **The instance key** (`instance_key.json` beside `kb.db`, W9) | A credential forged or altered without the key: `nexus-verify` names a signature that is not by a key `/identity` lists, or that falls outside that key's period. A stolen backup holds no key: a backup never copies it, and a restore makes a new one. | Anyone who can read the data folder can read the key and sign as the instance (owner-only on POSIX; on Windows the folder's permissions are its only protection). There is no revocation: a key `/identity` lists is trusted for its period, and a recipient who fetches `/identity` from the instance itself takes the instance's word for its own keys. |
| **Anchoring** (`NEXUS_ANCHOR=rfc3161`, off by default, W9) | Backdating: a block an RFC 3161 authority stamped cannot claim to be older than the stamp, checked offline with `nexus-verify --tsa-cert`. Only each signed block's 32-byte hash and a nonce leave the machine. | Whether the authority is trustworthy is the recipient's choice. A stamp dates a hash, never the truth of the content. Whoever watches the network sees when this instance stamps a block. |
| **Governed actions** (`action_records`, W10b) | A request NEXUS sends for a person going unrecorded: its row is written before it leaves or it is not sent, and its outcome before the result is used; a key header is recorded by name and fingerprint only. `NEXUS_SIGN_REQUESTS=on` (off by default) signs each one under RFC 9421 with the instance key. | An action a client takes is recorded only as the client reports it (`client_reported`). `/identity/signed-action` answers anyone holding a signature, with no token, with that action's receipt: its target, request hash, status and trigger, never the person, who is a salted commitment. A signed request tells its recipient which instance sent it. |
| **The container's network** (compose, `internal: true`, no gateway) | A NEXUS container reaching the internet: no default route, and `scripts/verify_deploy.py` proves a connect to a public address fails at once. The policy is the network's, not the application's (law 17). | The `api` profile, whose NEXUS has a route out to its model server (declared). A NEXUS run outside a container has no egress control. |
| **The admission gate** (every claim before it is stored) | Terms shaped like the runtime's own markup or like an imperative clause, from the model or from a seed. | Intent: it reads the shape of a term, not what the text means. |
| **The reference gateway** (`NEXUS_REFERENCE_GATEWAY`, off by default) | When on, it withholds a search result whose title or snippet breaks one of three rules, labels the rest, and cuts an address to its host. It reduces and measures what reaches the prompt (`docs/REFERENCE_GATEWAY.md` prints the measured rates, including how often a rewritten instruction got through). | It is off unless set to `on`. It does not judge intent, and the rates it measured are findings, not a bound. |
| **Confirmation** (`"confirmed": true`, Law 3) | A governed act (seeding, assertion, archival, search, an envelope) taken without the request saying so. No `confirmed` defaults to true; a refusal is recorded (`NEXUS_REFUSAL_LEDGER=on`, the default). | Whoever holds the token or the identity can confirm. |
| **The desktop bundle** (MCP over stdio) | The bundle opens no network listener and makes no network call: CI's smoke (`scripts/bundle_smoke.py`) records every bind, connect, name lookup and subprocess call of its binary and allows only three, each by its stack and shape: the standard library's own socket pair, urllib3's import-time IPv6 probe, and on Windows the `ver` query Python 3.11 runs for the OS version. | Its owner setting is asserted by the tester and never verified, and every record says so. |
| **The interceptor's credential pass-through** | **Not built**: the interceptor is not built yet (`docs/CONTRACTS.md`, "Not built yet"). | |
| **OAuth for a remote connector** | **Not built**: the remote connector is not built yet (`docs/CONTRACTS.md`, "Not built yet"). MCP is stdio only. | |

**Secrets NEXUS handles.** An API backend's key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) is read by one module
(`backend/providers/api_settings.py`), sent as a header and kept out of every record, log and request body.
A Postgres URL's secrets are blanked on every line that names the store (`docs/OPERATOR_RUNBOOK.md`, the hosted
store). The store itself is not encrypted at rest: `kb.db`, `archive.db`, the Chroma folder, the kept requests
(compressed, not encrypted) and every backup are plain files. Protect them as you would the conversations they hold.
A note held by `POST /commit` (W9) is kept with its salt in `kb.db` (`held_commitments`) and in every backup: its
commitment hides it from whoever sees a receipt or the chain, not from whoever reads the store.

## Deployment security checklist

1. Run on localhost (the default, `NEXUS_HOST=127.0.0.1`). Do not set `NEXUS_HOST=0.0.0.0` without a reverse
   proxy, TLS and real authentication in front; the compose deployment (`docs/DEPLOY.md`) is that shape.
2. Set `NEXUS_API_TOKEN` in `.env`. To generate one:
   `python -c "import secrets;print(secrets.token_urlsafe(32))"` (run here).
3. Keep `.env` and `data/` out of git (both are gitignored: check before pushing), and rotate the token at once if
   it ever leaks.
4. In token mode (the default), most GET endpoints are unauthenticated under the localhost posture: the evidence
   reads under `/lineage`, `/belief`, `/governance`, `/kb/as-of`, `/work-units`, `/commitments` and `/actions`, and
   `/whoami` (seventeen routes: A-2, W4, W6, W8's proof, W9's credential and commitment receipt, W10b's action
   receipt) require the token, and every other GET is open — see THREAT_MODEL.md before changing the bind.
5. Back up the stores (`scripts/backup.py`, then `scripts/restore_verify.py`: `docs/OPERATOR_RUNBOOK.md`); they are
   the system's memory and its audit record.
6. Read THREAT_MODEL.md before any deployment beyond a single local machine.

## Proxy mode (`NEXUS_AUTH_MODE=proxy`, W4)

For a process behind an identity proxy (Tailscale Serve, Cloudflare Access). The boundary is a loopback peer and one
header: a request is accepted only from a loopback address on a real port, carrying `NEXUS_IDENTITY_HEADER` exactly
once, and only when that login is `NEXUS_OWNER_IDENTITY`, compared exactly; the process answers for that one owner.
The proxy authenticated the person; NEXUS did not, and takes no bearer token. With no owner set, every request is
refused (403 "owner not set"); a login is never adopted.

Exactly means that only the spaces and tabs around the login and around `NEXUS_OWNER_IDENTITY` are trimmed. Nothing
is case-folded or normalised, so the owner's login in another case, or with a U+00A0 beside it, is refused.

The proxy attaches the login to every request the owner's browser sends, so a page from another site could post as
the owner. A request that could change state (any method but GET, HEAD and OPTIONS) is therefore refused with 403
when a browser sent it from another site: its `Sec-Fetch-Site` is present and is not `same-origin`, or it carries
`Origin` and no `Sec-Fetch-Site`. The console's own requests say `same-origin`. A client that is not a browser sends
neither header and is not refused. Serve the console over HTTPS or on loopback: a browser appends `Sec-Fetch-Site`
only to a request whose URL is "potentially trustworthy" (W3C Fetch Metadata Request Headers, §3, Working Draft of
21 September 2026), which W3C Secure Contexts §3.1 defines to include `https` and `wss` and the loopback ranges
`127.0.0.0/8` and `::1/128`. So over plain HTTP at another address the console's own writes would carry `Origin`
alone and be refused. That fails closed, and every documented
deployment path is HTTPS or loopback.

It rests on three facts the operator must make true:

- the proxy removes a client's own copy of the identity header before it sets its own, since NEXUS takes whatever a
  loopback peer sends;
- every process that shares the network namespace is a loopback peer, so nothing else may run there;
- the proxy passes the browser's `Sec-Fetch-Site` and `Origin` headers through as they came, since the cross-site
  rule reads them.

`start.py` launches uvicorn with `--no-proxy-headers`, so the peer is the socket's own. In proxy mode `/health` and
`/healthz` are the only paths open without an identity; they carry no belief content. See THREAT_MODEL.md.
