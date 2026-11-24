# NEXUS operator runbook

For the person who runs a NEXUS instance: start and stop it, verify its
record, back it up and restore it, merge into
`main`, and the measurements that need the instance's own use. Each section
names the item that introduced it. What is not built is said where it
would be used, in the words of `docs/CONTRACTS.md`'s "Not built yet".

The ids the sections use (`W3b`, "law 6", `B-17`, `FZ-2`) are explained in `docs/GLOSSARY.md`. The
sections, in order: starting and stopping; verifying the record;
merging a checkpoint into `main`; the unfreeze decision; the daily use the
measurements need; deploying; backup, restore and the deploy verification;
the hosted store on Postgres; the simulated UAT; one process per store;
MCP mode's data folder; building the desktop bundle; what NEXUS keeps so a
decision can be re-run; checking that a decision can be re-run.

**How this page was checked** (16 November 2026, the code at `48ad15c`, on
Linux with Python 3.11): the console commands, the verification routes,
`scripts/uat_simulate.py` (84 of 86 steps passed, the two Postgres
migration steps not run and not required), `scripts/replay_check.py`'s dry
run and `scripts/cp_b49_tally.py` were run here under the stub model. Not
run here: anything that needs Ollama, a GPU, Docker, Postgres, Windows,
macOS or the desktop app; each such command below is CI's, the operator's,
or says so.

## Starting and stopping NEXUS (W1)

1. Start the model server yourself: `ollama serve` (or the Ollama app), or LM
   Studio's server with `LLM_BACKEND=lmstudio`. NEXUS never starts one.
2. From the repo root:

       python start.py --check      # the checks only: launches nothing, writes no data
       python start.py              # the checks, then the console on 127.0.0.1:8000

   Both name the database they read on their first line (`database: …/archive.db`,
   read only), after reading `.env`, so a `NEXUS_DATA_ROOT` set there is
   the one named.
   `--check` exits 0 when every check passes, and otherwise with the first
   failure's code: 3 the model server is not reachable (the reason is named:
   refused, timed out, does not resolve, or something else answering), 4 a
   dependency does not import (every line of `requirements.txt` is imported,
   and then `backend.server` itself, as uvicorn will import it: in a child of
   the same interpreter, in the directory you run `start.py` from, with the
   environment the launch would hand the server; run from anywhere but the
   repo root, that import fails and says so, because the launch would fail
   there too; the interactive run makes the same check before it offers the
   launch), 5 the archive
   chain does not verify or cannot be verified, 6 Python is older than 3.11,
   2 a bad port. The archive is
   verified on a temporary copy, by the product's own verifier, so the store
   is never written. The checks are of the interpreter you run `start.py`
   with, and `--check` names it; when it is not a virtual environment and the
   repo has a `.venv`, the launch would re-run inside `.venv`, and `--check`
   prints the command that checks that one. The model-server check connects
   directly and ignores `HTTP(S)_PROXY`; the server's own client honours
   them, so behind a proxy the two can disagree.
3. `--port N` serves on another port for that launch. `--data-root D` keeps
   every console file under `D` (kb.db, archive.db, chroma/, snapshots/,
   turn_metrics.jsonl and the three logs) instead of `data/` under the
   directory you start from. Use the same `D` every time, and an absolute
   path if you start NEXUS from more than one directory: each archive block
   seals its snapshot's path as given.
4. `start.py` reads your `.env` and never writes it. With no `.env` it offers
   to create one as a copy of `.env.example`, with your model server on the
   `LLM_BACKEND` line and the day of creation on the
   `NEXUS_LEARNING_FROZEN_SINCE` line: a new instance is frozen,
   `start.py` says so when it creates the file, and
   `NEXUS_LEARNING=live` in `.env` switches learning on (`docs/FIRST_RUN.md`,
   step 10).
5. Every question defaults to No (law 6), "Begin startup sequence?" and
   "Start NEXUS now?" included: every hint reads `[y/N]` and Enter does not
   begin, create, install or start anything; press `y` to agree. (Until
   W1's close the two flow prompts defaulted to Yes on a loopback bind; the
   launch's first act writes the store, so that was withdrawn.) Model
   pulls are printed for you to run (`ollama pull …`); `start.py` runs no
   `ollama` command. With `NEXUS_HOST` set to anything but loopback the
   server would be reachable beyond this machine, and `start.py` warns
   before the launch prompt.
   When nothing sets `NEXUS_LEARNING` (no line in `.env`, nothing in the
   environment, as after Enter at the offer to create `.env`), `start.py`
   hands the server `NEXUS_LEARNING=frozen` (law 12: a new
   instance is frozen) and prints a `Learning:` line saying so;
   `NEXUS_LEARNING=live` in `.env` switches learning on, and a value you set
   is handed over unchanged. The server started without `start.py` defaults
   to live, so `docs/FIRST_RUN.md`'s manual command (step 11) says to set it.
   The interactive run's exit codes: 0 when NEXUS was launched (on Windows,
   the server's own status once it ends), or when every check passed and you
   declined only the launch (the command to run it was printed); 1 when you
   declined, or gave no answer to (EOF: a pipe, a container), a step the
   launch needs — "Begin startup sequence?", the model-server choice when
   creating `.env`, the venv questions, or the launch after a check failed and
   you continued past it; 130 for Ctrl+C at any prompt (128 + SIGINT, the
   shell's convention); otherwise the failed check's code from the `--check`
   table above (2 to 6). A non-interactive run (`python start.py </dev/null`)
   therefore exits 1 having launched nothing; W3's entrypoint answers no
   prompt.
6. Stop with Ctrl+C. On Windows `start.py` waits for the server to finish
   shutting down; a second Ctrl+C ends it at once, because uvicorn exits on
   its own second Ctrl+C (start.py cannot interrupt the wait while the server
   runs).
7. `--dev` reloads the server on every source change. It is for development
   only: never use it for the instance you keep.
8. `--serve` (W3) is the non-interactive launch, a container's entrypoint
   (the deploy image runs `python start.py --serve --port 8000 --data-root
   /var/lib/nexus` as PID 1's command). It asks nothing and never writes
   `.env`; it runs the checks that need no answer and leaves with the first
   failure's code (2 a usage error, an unknown `LLM_BACKEND` or `--serve`
   with `--dev` among them; 4 a dependency or the launch target; 5 the
   archive chain; 6 Python), SAYS whether the model server answered and
   starts regardless (never 3: a compose profile starts the model server
   beside NEXUS, and `/healthz` reports whether it answers), sets
   `NEXUS_LEARNING=frozen` when nothing sets it (law 12), binds `NEXUS_HOST`
   (the image sets `0.0.0.0`, behind the container network), and becomes
   the server (exec), so in a container uvicorn is PID 1 and a `docker stop`
   releases the store's lock (measured: under `--init`, or any wrapper that
   does not exec, SIGTERM leaves the lock for the next start to take over).
   `LLM_BACKEND` names `ollama`, `lmstudio`, `stub`, `openai` or
   `anthropic`: the stub is NEXUS's deterministic in-process provider for CI
   and the `stub` compose profile (one fixed answer naming itself, no model
   server, no embedder, and every record under it reads `model_id`
   `stub:primary`); `openai` and `anthropic` are the `api` profile's two
   providers (item 9). Any other value is refused by name: `start.py` exits
   2 before launching, and the server itself (uvicorn's factory call, or
   `python -m backend.mcp`) prints the refusal and exits 2 having opened
   nothing. So is an api backend whose model is unset.
9. **The api backends (W3 part 2).** `LLM_BACKEND=openai` is an
   OpenAI-compatible chat endpoint (`OPENAI_BASE_URL`, default OpenAI's
   `/v1`; the key `OPENAI_API_KEY`, sent as `Authorization: Bearer`, which a
   local endpoint may not need); `LLM_BACKEND=anthropic` is the Messages
   API (`ANTHROPIC_BASE_URL`, `ANTHROPIC_API_KEY` as `x-api-key`). Each
   needs its model named, `OPENAI_MODEL` or `ANTHROPIC_MODEL`: NEXUS names
   no model a bill attaches to, and `--check`, `--serve` and the server
   refuse to start without one (exit 2, the setting named). Extraction may
   use a cheaper model (`OPENAI_EXTRACTION_MODEL`, `ANTHROPIC_EXTRACTION_MODEL`;
   unset, the answering model). The model-server check reaches the
   endpoint's model list through your proxy settings with the key as a
   header, and says when the key is refused (HTTP 401). Every record made
   under an api backend names the model the API REPORTED answered, prefixed
   `openai:` or `anthropic:` (`model_id: openai:gpt-4o-2024-08-06` for a
   request naming `gpt-4o`), so it can never be read as a local model's; the
   key is read by one module and reaches no record, log or request body.
   The embedder is separate: `NEXUS_EMBED_URL` (an OpenAI-compatible
   `/embeddings` endpoint) and `NEXUS_EMBED_MODEL`, both or neither, with
   `NEXUS_EMBED_API_KEY` when that endpoint wants its own key. Anthropic
   offers no embedding model and an OpenAI-compatible chat endpoint need
   not serve one; with neither named, the idle scans (the dedup scan, the
   semantic indexer) do not embed at all, `/cortex/stats.embedder` says why,
   and `/kairos/status.last_cycle.dedup_skipped_reason` carries it. A
   request the endpoint refuses (a bad key, an unknown model, a rate limit)
   is a named error carrying the status and the start of the body, never
   an answer. Law 5 holds at every provider: a context above 8192 is refused
   before any request leaves (LM Studio sent it before W3). The Messages
   API needs an output cap, so its `max_tokens` is the context size NEXUS
   stated for the call (8192 for an answer, 4096 for extraction); a model
   whose output limit is lower refuses by name, and you name another.

## Verifying the record (A-2, W0b, AS-1)

Four routes say whether what NEXUS sealed still matches its hashes. The
first three need the token (`-H "Authorization: Bearer $NEXUS_API_TOKEN"`);
`/chain/status` is open. Run here, under the stub:

    curl localhost:8000/lineage/verify -H "Authorization: Bearer $NEXUS_API_TOKEN"
    curl localhost:8000/lineage/<lineage_id>/verify -H "Authorization: Bearer $NEXUS_API_TOKEN"
    curl localhost:8000/governance/verify -H "Authorization: Bearer $NEXUS_API_TOKEN"
    curl localhost:8000/chain/status

- `/lineage/verify`: every record, in layers. `row`: each record
  recomputes to its `record_hash`. `seal`: each sealed record's hash is in
  the archive block that sealed it; a record not yet sealed is listed as
  `unsealed`, which is a state, not a failure. Beside the layers, and never
  in their verdict, `request_bodies` (each kept request still hashes to
  its record's `request_hash`; `none kept` is a true state) and
  `capture_rows` (the options and environment rows the records point to).
- `/lineage/<id>/verify`: one record.
- `/governance/verify`: every governance record's hash recomputed
  (envelopes, outcomes, recalibrations), and the sealed ones checked
  against their chain blocks.
- `/chain/status`: the archive chain's blocks recomputed and linked.
  `?audit=true` also re-reads the snapshot files, which the chain's hashes
  do not cover.

A record is sealed into the chain by the next KAIROS idle cycle (every five
minutes while idle). To seal now: `curl -X POST localhost:8000/kairos/cycle
-H "Authorization: Bearer $NEXUS_API_TOKEN"` (run here). Under frozen
learning the cycle runs and makes no learning write; it still seals.

What a verdict proves: that the store's records and blocks are as they were
sealed, read by the instance's own code. It does not prove it to someone
without the store, and an owner with write access could rewrite a record
and recompute the chain. Verification by a stranger, from a credential and
the instance's public key with no store, is W9's (below).
`start.py --check` verifies the archive chain on a copy before every
launch on SQLite (exit 5 if it fails).

## A record's proof, its binding and its tombstone (W8)

`GET /lineage/<id>/proof` (with the token) returns one record's inclusion proof: its leaf, the path to its
block's root, and the block's header (or, for a record sealed before W8, the block's whole body). Anyone with this
repository can check it without NEXUS's store, from the repository's root (the line imports
`backend.memory.proofs`; with no repository, use a credential and `nexus-verify`, below): `python -c "import json,sys; from backend.memory.proofs import verify_proof;
print(verify_proof(json.load(open(sys.argv[1]))) or 'holds')" proof.json` prints `holds` or the refusal's name.
A proof says the record's leaf is in that block's root and that the block hashes to that header (or body); that the
block is this instance's is its signature's to say (W9's credential, below). It says nothing about the content
beyond the fields a credential opens.

**After upgrading to W8** the first seals bind the records sealed before it (at most 2,000 a seal, one seal per
idle cycle when there is something to seal), so a large store is bound over several cycles. `GET /lineage/verify`
counts what is left in `proofs.unbound`; a binding says this instance bound the commitments when it did, not what
a record held when first sealed. A record whose stored root its values and salts do not rebuild is never bound and
is named in `proofs.failures`: its commitments were rewritten after it was written.

**A tombstone** disposes of one record's content and keeps what verifies it: the record's id, time, learning mode
and injected set, its hash, its commitment root and every field's commitment. It is a store method only in this
build (`LineageStore.tombstone`); no route reaches it, because when a record may be disposed of and who confirms
it is retention by policy, which is not built yet (`docs/CONTRACTS.md`, "Not built yet"). A tombstoned record reads `tombstoned` in its dropped fields, is counted in
`/lineage/verify`'s `tombstoned`, and still verifies.

## The instance's key, its credentials and held commitments (W9)

**The key.** The instance signs every archive block it appends with one Ed25519 key, `instance_key.json` beside
`kb.db` in the data root, made at first use: the first block signed, `/identity` or a credential (owner-only on POSIX; on Windows the data folder's permissions
are its only protection). `GET /identity` (open) serves its public half and every key the instance has used. **A
backup never copies it**: a backup restored into service makes a new key at its first use, and `/identity` closes
the old key's range where the new key was made, at once (the new key is listed `pending` until the next block
announces it), so
credentials for the blocks the backup holds still verify against `/identity`'s list (not one signed at issue by a
key no block had announced yet: BACKLOG W9-B9). A block sealed after the backup was taken is not in the restored
store, so the restored list closes the old key before it: its credential holds only for someone who received and
checked it before the restore, or whose anchor dates the block before it, and `nexus-verify` says so (Ruling 7,
in the development record).
After a rotation, give recipients the new `identity.json`: one taken before it leaves the old key's range open.
Putting a moved key file back after a new key was made reopens the old key's range, so hand out the identity served
after that too: one served in between closes it (BACKLOG W9-B12). A
key file that is not its seed's is refused by name (`503 key_mismatch` on `/identity`, the credential and the
receipt routes), one the process cannot read is `503 key_unreadable`, and either is left as it is;
moving it aside makes a new key at the next start, a rotation like a restore's (the chain keeps every announced
public key). `/identity.signing` counts blocks appended unsigned because signing failed; a credential for a record
in such a block is signed when it is issued, by the current key, and says so.

**A credential** for one decision, with the token:

    curl -H "Authorization: Bearer $NEXUS_API_TOKEN" "http://127.0.0.1:8000/lineage/<id>/credential?disclose=model_id,ts" > credential.json
    curl http://127.0.0.1:8000/identity > identity.json

Hand both to whoever checks the decision, with `scripts/nexus_verify.py` (one file, the standard library only)
or the `nexus-verify` binary a release builds: `python nexus_verify.py credential.json --identity identity.json`
prints `nexus-verify: VERIFIED` and the disclosed fields, or `FAIL <name>` for each failure. Send `identity.json`
by a channel the recipient trusts, or let them pin the current key with `--key` (a pinned key is taken as active at every block, so never pin a key
an instance has rotated away from); with neither, the check
fails `key_unpinned`. Undisclosed fields are commitments only, and the lineage id is named only when disclosed: the
record hash rides every credential, so whoever has the id (disclosed, or from a `done` frame or `/events/recent`)
can test guesses of small undisclosed fields together; disclose the id only when it is meant to be known. A tombstoned record's credential discloses only what it kept; one issued before the tombstone keeps
verifying. A verified credential proves the
record's integrity at its seal, never the truth of its content and never its time.

**Commit now, reveal later.** `POST /commit` with `{"content": "…", "confirmed": true}` holds a note and returns
its salt once: keep the salt with the note. After the next seal (or `POST /kairos/cycle`), `GET
/commitments/<id>/receipt` is the receipt; later, `python nexus_verify.py --reveal receipt.json --content note.txt
--salt <salt> --identity identity.json` shows the note was held when that block was sealed. The note and its salt
are in `kb.db` (`held_commitments`), and so in every backup.

**Dating.** A signature says who sealed a block, not when. `NEXUS_ANCHOR=rfc3161` with `NEXUS_ANCHOR_URL`
(FreeTSA's is `https://freetsa.org/tsr`) has each signed block's 32-byte hash time-stamped by that authority on
the console's idle cycle (MCP mode never anchors); nothing else leaves the machine, and `/cortex/stats.anchoring` reports what was stamped and the
last error. The recipient checks the stamps with `--tsa-cert <the authority's certificate>`. Off by default.

## Actions recorded beside the answers (W10b)

**What is recorded.** Every request NEXUS sends for a person is an action: the web search (a turn the person
consented to, or `POST /search/execute`) and each request the two online seeds send. Its row is written before the
request leaves, or the request is not sent; its outcome (status, and the SHA-256 of the body received) is written
before the result is used, or the result is withheld. Either failure is named: `503 action_not_recorded` on a
route (a seed says how much it admitted before it stopped, `seeded_before_the_stop`), and a
`kb_pressure_alert` `web_search` / `not_recorded` frame in a turn, which then answers without web results. A key
header is recorded by name and fingerprint, never its value. Model calls, probes and anchoring are not actions.

**Reading them.** `/lineage/{id}` lists a turn's actions; `GET /actions/{id}` (token) is an action's receipt,
naming who was accountable; `/cortex/stats.actions` counts them by class (runtime, client reported) and state. A
route's actions (a seed's requests, `POST /search/execute`) are listed by no route yet: their ids are in the store,
for example, with Python's own sqlite3 and the store opened read-only,
`python -c "import sqlite3; c = sqlite3.connect('file:data/kb.db?mode=ro', uri=True); print(c.execute('SELECT action_id, kind, trigger_route, state FROM action_records').fetchall())"`
(BACKLOG W11-B1).
After the next seal, `python nexus_verify.py --action action.json --identity identity.json` checks a receipt
offline. An MCP client reports its own actions with `nexus_record_action`; those are `client_reported`, never
counted as runtime: NEXUS did not see them happen.

**Signing requests** (`NEXUS_SIGN_REQUESTS=on`; off by default). Each action's request then carries RFC 9421
`Signature-Input` and `Signature` headers made with the instance key, so whoever receives it can check it against
`/identity` and ask `GET /identity/signed-action?signature=<the signature>` (open; percent-encode the signature, since
a `+` sent bare reads as a space and answers `404 no_action`) for the sealed action behind it.
That answer names no person: the accountable identity is in it as a salted commitment, disclosed only by the
token-gated receipt. With the key unreadable, a signed request is not sent at all.

## Merging into `main`

Nothing is committed to `main` directly. A branch is merged when the suite has
passed on a clean checkout of its head, on SQLite, and on Postgres when it
touches `backend/store/`, `backend/kb/` or `backend/memory/`, and on the
maintainer's own Windows machine, through the venv:

    .venv\Scripts\python.exe -m pytest -q

That command was not run here (Windows). After a merge, run
`python start.py --check` on the instance before the first launch (it names a
missing requirement, exit 4, and verifies the archive chain), then
`/lineage/verify` and `/governance/verify` once it serves; the changelog's
entry for what was merged says what to do first.

## The unfreeze decision (not built yet)

A new instance is frozen, and the switch is
`NEXUS_LEARNING` in `.env`: `frozen`, or `live` to switch learning on
(`docs/FIRST_RUN.md`, step 10; restart to apply). The project keeps every new
instance frozen until the recurrence defect B-17 is fixed and the operator
unfreezes deliberately, with a before/after script that shows what
switching on would change. **That script (`scripts/unfreeze_decision.py`)
and the fix are not built yet (the unfreeze decision, in
`docs/CONTRACTS.md`'s "Not built yet"): neither exists.** Until they do, nothing measures what an unfreeze would do to
this instance, and the run recommends leaving it frozen. The decision is
yours; the run never unfreezes an instance. Whatever you set, every record
carries the learning state it was written in, and `/cortex/stats.learning`
counts the writes the freeze stopped since the process started.

## The daily use the measurements need

Some questions can only be answered from the instance's own use, and the
run cannot reach the instance. These are the ones with a script today:

1. **Can a decision be re-run?** After a day of normal use, with NEXUS
   stopped, run `scripts/replay_check.py` (its section below) and send back
   `reports/replay_check_<date>.md`. Until that report exists, no document
   claims that decisions can be re-run.
2. **Do compaction's lower tiers change what the next turn sees (B-49)?**
   Set `NEXUS_FIRE_CAPTURE=on` in `.env` (one JSONL row per compaction fire,
   ids and numbers only, never text), use the instance, then run the tally
   read-only (run here on a scratch store):

       python scripts/cp_b49_tally.py --db data/kb.db

   It prints how many fires of tiers 1 to 3 it has seen and whether the ten
   the decision rule needs exist (the fire-capture packet's S5, in the development record). It
   reports; the decision is a register line, not the script's.

Every script that reads the instance opens it read-only and names the
database on its first line (law 15). Run them on the instance's
data root, or on a stopped copy of it.

## Deploying (W3)

`docs/DEPLOY.md` is the tested path: a fresh Ubuntu 24.04 host from
`deploy/cloud-init.yaml`, the compose profiles (`stub`, `ollama`, `api`,
`search`, `hosted`, `backup`), the image from GHCR or your own build, the egress policy, and
CI's `deploy-smoke`, `release.yml` and `deploy.yml`. Two credentials are per
job: the console token (`NEXUS_API_TOKEN`) is the instance's, and the
deploy key (`DEPLOY_KEY`, with `DEPLOY_HOST` and `DEPLOY_USER`, repository
secrets) is an SSH key for ONE host. Rotate it by generating a new pair,
adding the new public key to the host's `~/.ssh/authorized_keys`, replacing
the `DEPLOY_KEY` secret, running the deploy workflow once, then removing
the old public key from the host. With any of the three unset the workflow
runs one step that says so and ends green; it never deploys half-way.

## Backup, restore and the deploy verification (W3)

**A backup is one folder with a manifest.** `python scripts/backup.py
--data-root D --out BACKUPS [--keep N]` copies every store under the data
root into `BACKUPS/<UTC stamp>/`: kb.db first, then archive.db (records name
archive blocks, so the chain head is never older than the records it seals),
then chroma's database, each through SQLite's online backup API from a
read-only open (the running console holding W2's lock is never blocked and
never written; measured under a busy writer at a few milliseconds each),
and the snapshots and logs as plain copies. The lock file is not copied; the
holder at backup time is recorded. `MANIFEST.json` beside the files holds
when it was taken, the source, the row counts per table of both databases,
the chain's block count and head, the lineage count with a digest of its
record hashes, each copied database's `integrity_check` (run on the copy,
which the script owns) and every file's sha256. A copy that fails
`integrity_check` is kept for inspection, named, and exits 5: it is not a
backup to restore from. A backup that could not take a consistent pair
(below) exits 6 and leaves no folder at all. `--keep N` removes the oldest backup folders beyond
N after a successful backup, naming each; without it nothing is removed, and
a folder without a manifest is never touched.

**Under compose** the `backup` profile's service runs it on the internal
network only (no route out), the data volume mounted read-only, into
`NEXUS_BACKUP_DIR` on the host (`./backups` beside the compose file by
default; writable by the image's user, uid 10001, which
`deploy/cloud-init.yaml` arranges), keeping fourteen (`NEXUS_BACKUP_KEEP`):

    docker compose --profile backup run --rm backup

Under `hosted` each tester's store has a backup service of its own
(W4 part 3): `backup-t1` and so on, the tester's volume read-only, the
folder `NEXUS_BACKUP_DIR/t1` and so on, reaching that tester's database by
name. The daily timer `nexus-backup.timer` (cloud-init, 03:30, persistent)
runs `deploy/nexus-backup.sh`: the command above, or under `hosted` every
tester's backup service, each one even when another fails. The two SQLite
stores are copied as a consistent pair: when a seal lands between the copy
of kb.db and the copy of archive.db, the backup copies both again (the
manifest's `pair_attempts` says how many times), and after five attempts
it fails by name and removes its folder, so neither restore verification
nor `--keep` can take a pair whose restored store would name the act it
lacks as a forgery. A row a seal had not yet marked when kb.db was copied
reads unsealed in the restored store and is sealed again there.

**Restore verification is a product feature.** `python
scripts/restore_verify.py BACKUP_FOLDER` (or `--latest-under BACKUPS`; under
compose, `docker compose --profile backup run --rm --entrypoint python
backup scripts/restore_verify.py --latest-under /var/backups/nexus`)
copies the backup into a scratch folder, never opening the backup in place,
and runs five checks, each PASS or FAIL: R1 every file the manifest names
is present and hashes to its sha256, and nothing else is present; R2 every
table's row count equals the manifest's; R3 the chain's head equals the
manifest's and the chain verifies read-only; R4 the product's own verifiers
pass on a SECOND copy of the databases (they open read-write and migrate
older schemas): `verify_chain`, `verify_sealed_lineage`,
`verify_recall_index`, `verify_request_bodies` (`none kept` is a true
state) and `verify_governance_records`; R5 the restored databases' bytes
are unchanged by the checks. Exit 0 when all five pass, 1 otherwise, 2 for a
folder with no manifest. Snapshot files are verified by their hashes, not
by `audit_snapshots()`: snapshot paths are sealed as given, so on a restored
copy the audit would read the live instance's files. To restore for real,
stop NEXUS, move the live data root aside, copy the verified folder's
files (everything but `MANIFEST.json`) to the data root, and start NEXUS;
the first start takes over the copied lock file, if any, and says so in
`kb.db.lock.log`.

**The deploy verification** (`python scripts/verify_deploy.py --profile
stub --insecure`, run on the host beside the compose project) is the one
command that says a deployment works: V1 health through Caddy, naming the
profile's backend; V2 the token (the console is refused without it); V3 one
turn; V4 the record sealed (`/lineage/<id>`, `/lineage/verify`); V5 the
chain verified after a forced KAIROS cycle; V6 a backup taken through the
`backup` service; V7 that backup's restore verified; V8 egress refused
inside the NEXUS container: no default route, a connect to each of three
public addresses failing at once with ENETUNREACH (a timeout is not a
pass), a sibling reached by name, the internal network isolated with no
gateway address. Under the `api` profile V8 is skipped by name (the route
out is declared) and not counted. Under `hosted`, V9 reads the role NEXUS
connects as, from inside its container through the store setting it runs
with (nothing on a command line): it fails if that role is a superuser or
holds CREATEDB, CREATEROLE, REPLICATION or BYPASSRLS, if it is a member of
a predefined `pg_*` role (pg_database_owner, which a database's owner
belongs to by owning it, is accepted), or if PUBLIC may connect to its
database. The other profiles' stores are files, so V9 is skipped by name
there and not counted. Under `hosted` (W4 part 3) every check runs per
tester, and so do V10 (another tester's database refuses the role) and V14
(the tester's relay refuses a peer that is not the identity proxy); the
requests through the identity proxy run only against the deploy smoke's
stand-in (`--identity-sim`) and are otherwise skipped by name and not
counted (docs/DEPLOY.md). `--insecure` is for `localhost` (Caddy's internal
CA); never use it against a real domain. From another machine,
`--remote "ssh -i key user@host" --remote-dir /opt/nexus` runs V6 to V9
through `docker compose` on the host over SSH, which is how `deploy.yml`
verifies. Exit 0 when every counted check passes.

## The hosted store: Postgres (W3b)

**The store is a database.** With `NEXUS_STORE=postgres` and
`NEXUS_DATABASE_URL=postgresql://…` (the schema, when not `public`, named
in the URL's `options`: `?options=-c%20search_path%3D<schema>`), every
table kb.db and archive.db hold lives in that database. The data root keeps
chroma/, snapshots/ and the logs. Every line that names the store, and
every record, redacts the URL's secrets: the password, in the userinfo or
as `?password=`, and `?sslpassword=` (and libpq 18's
`oauth_client_secret`, `scram_client_key` and `scram_server_key`).
Percent-encode `@`, `/`, `:`, `?`, `#`, `%`, `&`, `=` and spaces in a
password (`@` is `%40`, `/` is `%2F`, `%` is `%25`, a space is `%20`). A
URL libpq would not read as it was written is refused by name before
anything connects (`start.py` and MCP: exit 2, nothing launched; an
instance script, the backup and the restore check: exit 2, nothing run),
and no line shows any of it: where the store is named, the line reads
`<a postgresql:// URL libpq would not read as it was written: not shown>`.
That is a base64 password's `/` left as it is, an `@` after the host, and
whatever libpq itself refuses to parse (NEXUS asks the driver's libpq;
nothing connects): a `%` that begins no percent-encoding, a space left as
it is, a parameter libpq does not know (a mistyped `?passwd=`, whose value
no reading knows as a secret; `PASSWORD` in upper case), a parameter with
no `=` or with two, a scheme in upper case. The refusal says which, in
NEXUS's words and never libpq's, whose own message quotes the URL. A URL
libpq does read is shown with each value libpq holds secret blanked and
the rest as written, so a secret put under a parameter libpq does not hold
secret (`application_name`, `options`) is shown. Where the psycopg driver
is not installed libpq cannot be asked, and no part of any postgres URL is
shown. A URL libpq reads whose store does not answer (a closed port, a
wrong password) still leaves `start.py --serve` by the server's own
traceback, exit 1, with the driver's line blanked. A
value NEXUS does not know, or `postgres` without a
URL, is refused by name with exit 2, having opened nothing. The compose
profile `hosted` runs this shape (docs/DEPLOY.md).

**The roles under compose (W4 part 0).** The superuser is the database
image's own `postgres`. Its password is used only when the database is
first initialised, and nothing connects as it after that. That first start
runs `deploy/postgres-init.sh`, which makes for each tester key in
`NEXUS_TESTERS` (`t1` unless set) a role `nexus_<key>` and a database
`nexus_<key>` that the role owns. The role can log in and holds no
superuser, CREATEDB, CREATEROLE, REPLICATION or BYPASSRLS. Each tester's
NEXUS connects as that tester's role and creates the store's tables at
first open (W4 part 3: one NEXUS per tester). CONNECT
on a tester's database belongs to its owner and the backup role alone, so
one tester's role cannot open another tester's database. The one backup
role, `nexus_backup`, reads every tester's store. A volume keeps the roles
it was first initialised with: docs/DEPLOY.md says how a volume made
before W4 moves to a new one.

**One process per store, on Postgres too.** The single-writer lock is a
session advisory lock. A second process is refused with the holder's pid
and exit 7, and a killed holder's lock ends with its session (TCP
keepalives: about a minute and a half for a vanished host). The refusal
prints the store's URL with one slash after the scheme
(`postgresql:/…`): the message passes the URL through a file-path type,
which collapses the double slash (and on Windows turns each slash into a
backslash); the URL in use is the one you set (BACKLOG W11-B2).

**The instance scripts take the URL where they took a path**
(`replay_check.py`, `cp_b49_tally.py`, `as2_instance_windows.py`,
`diag_downstream.py`, `archive_pregate.py`, `archive_consolidation.py`). They
read through a read-only session and a role that cannot write. A role that
could write is refused by name, and the refusal names what gives it the
write and the role that holds it. It is read for the login itself (a
`role` named in the URL's options or by `ALTER ROLE … SET role` does not
make a login read-only) and for every role it is a member of, across the
whole database and not the store's schema alone:
- a superuser; `pg_write_all_data`, `pg_write_server_files` or
  `pg_execute_server_program`; CREATEROLE; ALTER SYSTEM granted on a
  parameter;
- the owner of the database or of anything in it (a table, a function, a
  schema);
- CREATE on a schema or on the database. A database made before
  PostgreSQL 15, or upgraded from one, still grants CREATE on `public` to
  everyone: `REVOKE CREATE ON SCHEMA public FROM PUBLIC`;
- INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER or REFERENCES on any table or
  view, a column-level write, USAGE or UPDATE on a sequence, or a default
  privilege granting a table write on future tables;
- EXECUTE on any SECURITY DEFINER function anywhere in the database,
  whoever owns it and whatever it does (its body is not read), in a schema
  the role cannot name too: a view that calls it reaches it. EXECUTE is
  everyone's by default, so one such function, an extension's for one,
  refuses every read-only role: the refusal names the function; revoke
  EXECUTE on it from PUBLIC and grant it to the roles that need it.

Not read: PostgreSQL 17's MAINTAIN, `pg_signal_backend`, what a C or
untrusted-language function granted to the role does (`lo_export` for
one), a SECURITY DEFINER function that an aggregate or an event trigger
runs for the caller (someone with the right to write must have planted
it), a large object the role was granted UPDATE on and USAGE on a
foreign-data wrapper (writes in the database, not in the store), a
writer's credentials held by the role (a password, a foreign server's
user mapping), and what PostgreSQL lets every login do with no privilege
at all (its own settings and password, temporary and large objects of its
own, an advisory lock). Make the read-only role once:

    CREATE ROLE nexus_ro LOGIN PASSWORD '…';
    GRANT USAGE ON SCHEMA public TO nexus_ro;
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO nexus_ro;

Under compose, `deploy/postgres-init.sh` makes `nexus_backup` this way in
each tester's database, as default privileges for the tester's role so
that the tables NEXUS creates later are covered, with CREATEDB added for
the restore verification.

**Backup and restore.** `python scripts/backup.py --data-root D --out
BACKUPS --database-url postgresql://nexus_backup:…@host:5432/nexus_t1` copies
the data root's files as above and dumps the store with `pg_dump -Fc` into
`store.pgdump`:
- The dump comes from a snapshot exported inside one read-only
  transaction, and the manifest's counts, chain head and lineage digest are
  read in that transaction, so the manifest describes exactly the dump.
- `pg_dump` must be at least the server's major (the image carries
  postgresql-client-16); an older one is refused by name.
- The manifest says `format: 2` and names the store (the URL redacted, the
  database, the schema, both versions).
- `pg_restore --list` checks the dump where a SQLite backup runs
  `integrity_check`.
- The URL may instead be set as `NEXUS_BACKUP_DATABASE_URL` with
  `--database-url` left out, which keeps it off the command line another
  user's `ps` reads (`backup.sh` does this). `pg_dump` is given the URL
  without its secrets and the password in PGPASSWORD. A URL carrying
  `sslpassword` is refused by name: there is no environment variable to
  hand it over in.
- One backup holds one store. A hosted backup copies the data root's side
  files and never `kb.db` or `archive.db`. While the data root holds
  either, a URL read from `NEXUS_BACKUP_DATABASE_URL` (the environment, or
  a `.env` the script loads) is refused by name with exit 2, before
  anything connects and with nothing written; the refusal names both
  stores and where the setting was read. Given explicitly,
  `--database-url` takes the hosted backup and declares what it leaves
  out: a `not in this backup:` line, and the manifest's
  `data_root_sqlite_files_not_in_this_backup`, which `restore_verify.py`
  prints beside its verdict. To back up the SQLite store instead, unset
  the setting.

`python scripts/restore_verify.py BACKUP --database-url
postgresql://nexus_backup:…@host:5432/nexus_t1` (under compose the
tester's backup service's own NEXUS_BACKUP_DATABASE_URL, so `docker compose
--profile backup run --rm --entrypoint python backup-t1
scripts/restore_verify.py --latest-under /var/backups/nexus`) restores the
dump into two scratch databases on that server and drops both afterwards,
whatever the checks found:
- R1: the files' hashes, the dump included;
- R2: the counts on the first database;
- R3: the chain head there, and `verify_chain` on the second;
- R4: the product's verifiers on the second;
- R5: the first database's rows unchanged by the checks.

The drop is a plain `dropdb`, which ends an autovacuum worker on the
database itself. If that is refused, the backup role's own sessions on the
database are ended and the plain drop runs again. A scratch database still
there after that is named on its own line, `FAIL cleanup: the scratch
database nexus_restore_… is still on …: <the server's words>; the checks
above stand; drop it as a role that may end its sessions`, and the run
reads 5 of 6 and exits 1. The five checks above that line stand; the line
means a full copy of the store is still on the server: drop that database
by hand as a superuser (`DROP DATABASE … WITH (FORCE)`).
`dropdb --force` is never used by the script: as the backup role on
PostgreSQL 16 it is refused while an autovacuum worker is on the database.

To restore for real:
1. Stop NEXUS.
2. Make an empty database (or drop the store's schema).
3. `pg_restore --no-owner --dbname=… store.pgdump` as the owner.
4. Copy the folder's other files to the data root.
5. Start NEXUS.

Under compose, the owner is the tester's role, which cannot make a
database, and a database the init script did not make would lack the
backup role's CONNECT. So restore into the tester's own database, as the
tester's role: drop the schema `public` there (`DROP SCHEMA public
CASCADE`), then `pg_restore --no-owner --no-privileges --exit-on-error
--dbname=…` the dump. Dropping the schema also drops the backup role's
grants in it, and the backup role is refused until the tester's role
grants them again:

    GRANT USAGE ON SCHEMA public TO nexus_backup;
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO nexus_backup;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO nexus_backup;

Measured on a scratch PostgreSQL 16.15 server (W4 part 0): before the three
grants the backup role's read was refused; after them NEXUS opened the
restored store and `/lineage/verify` read intact, a new backup passed, and
the read-only open passed. `scripts/uat_simulate.py` restores a SQLite backup into
service on every CI run (its backup section); these Postgres steps are run by
hand only.

**Moving a store between the backends.** `python
scripts/migrate_sqlite_to_postgres.py --data-root D --to postgresql://…`
reads kb.db and archive.db read-only (a snapshot through the backup API,
opened and migrated in a scratch folder) and prints the plan. `--confirm`
then does three things:
- It copies every table, with ids, the two ordinal tables' order and the
  sequences, in one transaction, behind the target's lock (exit 7 while a
  NEXUS process holds it).
- Inside that transaction, before the commit, it compares the copy with
  the source: every table's rows, the record hashes, the chain, the graph's
  order, the as-of pages, the sequences. A copy that does not prove is
  rolled back and writes nothing (exit 6).
- After the commit it runs the product's verifiers on the target.

The migration leaves `kb.db` and `archive.db` in the data root. Once it is
verified, move both out of the data root and keep them with the SQLite
store's last backups. Until then a backup whose URL comes from
`NEXUS_BACKUP_DATABASE_URL` is refused by name (one backup holds one
store, above), so the daily timer's unit fails rather than writing a
backup without them.

A target that already holds a store is refused (exit 2): a migration fills
an empty store and never merges two. So is a URL whose schema does not
exist. `python scripts/export_postgres_to_sqlite.py --from postgresql://…
--out DIR [--confirm]` is the reverse:
- It reads the source through the read-only role.
- It refuses a folder that already holds kb.db or archive.db.
- It builds the two files in a scratch folder inside DIR and moves them into
  place only when the same checks pass.

A store exported back holds the same rows as the one migrated in. SQLite's
hidden physical order of the text-keyed tables is not restored, and
nothing reads it (docs/DEPLOY.md's declared differences).

## The simulated UAT (W4)

`python scripts/uat_simulate.py` runs, in a temporary folder of its own,
everything a tester and an operator do, against the product's own entry
points under the stub model: the console, MCP, three hosted testers in proxy
mode, backup and restore into service, the upgrade from the pre-run fixture,
uninstall and the read-only operator scripts; and (W9) credentials verified
by `nexus-verify` from an empty folder, tampered and unsalted ones named,
and a note committed in one session and revealed in another after a
restore. It never reads your `.env` or
touches `data/` or a real instance. It writes
`reports/uat_simulation_<platform>.md` (ignored by git): one row per step with
the scripted input and PASS, FAIL or NOT RUN, and exits non-zero on any FAIL or
any required step it could not run. `--sections console,mcp` runs some of it;
`--database-url postgresql://<superuser>@…` puts the hosted testers on
databases made by `deploy/postgres-init.sh` and drops them after. The run with
a real model is yours (`docs/FIRST_RUN.md`, steps 9 to 19).

## When an answer is withheld: "its record could not be written" (W6)

NEXUS releases an answer only after the record of it is written. If the write fails (a full disk, a locked or
read-only database, a lost connection to a hosted store), the console shows "This answer was withheld: its record
could not be written (<error class>)", the turn is not added to the conversation, the session is as it was before
you asked (an image you attached is not kept; a one-turn model override is still pending), and no part of the
answer was shown. What to read: the log line `lineage record failed: <class>: <text>` has the cause; `GET
/cortex/stats` counts the failed writes since the process started, under `recorder`. Ask again once the cause is
fixed. A close or reopen asked for on that turn was not made; the retry makes it. A web search you consented to
for that turn has run and its consent is spent: the retry runs no search until you consent to it again. What
stays: the claims
extraction read out of the withheld answer are already in the store (the frame's `claims_admitted` counts them),
a work unit named for the first time exists, the forecast made before the answer is in the calibration ledger,
unresolved, and the association graph keeps the question's activation, so a retry within a few seconds can be
given a slightly different context. **The cost of the default, said plainly (Ruling 6, in the development record):** the console no longer
shows an answer while the model writes it; it appears whole once extraction is done and its record is written.
`NEXUS_ANSWER_RELEASE=live` shows answers as the model writes them, as before; then a failed write is still
reported, but the answer has already been shown. Under MCP the client's `nexus_record` call fails with
`record_not_written` and can be made again with the same retrieval id; if the store refused even the count of that
failure, the message says so and `nexus_status.record_failed_uncounted` counts it until the process stops.

## One NEXUS process per store (W2)

Opening a store takes `kb.db.lock` beside `kb.db`: the console (`start.py`,
or uvicorn on `backend.server:create_app`), MCP mode (`python -m
backend.mcp`, or the bundle's binary), and the two scripts that write a
store under `--confirm` (`archive_pregate.py`, `archive_consolidation.py`).
A second process on the same store is refused, having opened nothing:

    this store is open in another NEXUS process (pid N, since T)

MCP mode and the console exit with 7, and the scripts print `REFUSED` and
return 7. Their dry runs only read, and are not refused. Stop the other
process, or point this one at another data root.

The lock is the operating system's (`flock` on Linux and macOS, a byte-range
lock on Windows), so a process that dies, however it dies, frees it. The
lock file itself stays and names its last holder. When a new process finds a
holder that did not release the lock, it takes the lock over and appends a
line to `kb.db.lock.log` (the old holder, the new one, the time), and logs it
at WARNING. Under `start.py --dev` every reload takes over the lock this way.
Never delete `kb.db.lock`: a process that still held the old file would then
not exclude one that opens a new file at the same path. A folder whose file
system cannot lock (some network shares) is refused by name; NEXUS never
opens a store unlocked. The lock is per machine: a store in a folder synced
between computers is not protected across them, so never sync one.

## MCP mode's data folder (W2)

`python -m backend.mcp` keeps its store, and its three logs, in
`NEXUS_MCP_DATA_ROOT`, an absolute path. Unset, empty, relative, or still an
unexpanded `${...}` (a desktop host's untouched placeholder), it is a
per-user folder: `%LOCALAPPDATA%\NEXUS` on Windows, `~/Library/Application
Support/NEXUS` on macOS, `$XDG_DATA_HOME/nexus` or `~/.local/share/nexus`
elsewhere. It is never the working directory. Its first line on stderr
names the folder and why. Before W2, unset meant `data/` under the working
directory: started from the repo, the console's own store. An MCP store
kept there is reached again by setting `NEXUS_MCP_DATA_ROOT` to its
absolute path. An owner (`NEXUS_MCP_OWNER`) that is still an unexpanded
`${...}` is recorded as unattributed, and stderr says so.

## Building the desktop bundle (W2)

From the repo root, on the platform the bundle is for (a Windows bundle is
built on Windows, a macOS one on a Mac):

    python scripts/build_bundle.py --check     # the manifest and the icon only
    python scripts/build_bundle.py             # build dist/nexus-<version>-<platform>-<machine>.mcpb
    python scripts/build_bundle.py --twice     # build it twice and compare

(`scripts/build_bundle.sh` and `scripts/build_bundle.ps1` run the same with
the repo's interpreter.) The bundle is built from the commit at HEAD (`git
archive`), never from files you have not committed; the build says when your
working tree differs. It needs PyInstaller, which `requirements.txt` pins;
the build prints the commit, the platform, every installed package with
their hash, every file's hash and the bundle's. Two builds of one commit on
one machine give the same bytes; a build next week may not, because
`requirements.txt`'s other lines are ranges, which is why the packages'
hash is printed. Exit codes: 2 the manifest is wrong (each problem named),
3 the build failed (the log's tail), 4 `--twice` found a difference (the
files named), 5 the tree would carry a `.env`, a store, a lock or a log.

The bundle fixes learning at frozen and reads no `.env` at all, not even
one beside it or in the desktop app's working directory. A tester sets two
things: the owner, and optionally the data folder. `docs/FIRST_RUN.md`,
steps 16 to 19, is the page to send with it.

CI's `bundle` workflow builds it twice and smokes its binary
(`scripts/bundle_smoke.py`) on Windows and macOS when it is started by hand
(since 17 November no push starts it). A run keeps the bundle as an artifact
for three days. The smoke
runs the binary the way the desktop app does and checks the tools, the
settings reaching it, the first record (frozen, with the owner), that it
makes no network call at all (a call to a service on this machine, such as
a model server or a proxy, included; only the event loop's own socket pair,
urllib3's import-time IPv6 probe and, on Windows, the `ver` query Python
3.11 runs for the OS version, each found by the call's stack and its
shape, are allowed), that nothing is written in its working
directory, that a second process is refused, and that a killed holder's lock
is taken over. For the network check, the smoke sets `NEXUS_AUDIT_NETWORK`
to a file, and the bundle's entry then records every connect, name lookup
and subprocess call into it. Nothing reads that variable unless the smoke
sets it, and it is not a `.env` setting.

## What NEXUS keeps so a decision can be re-run (W0b)

**NEXUS keeps the exact text it sent to the model, so a decision can be
re-run.** For every answer, it stores the request (the system prompt, the
retrieved context and the conversation: each message's role and text, in
order, the form the record's `request_hash` covers) next to the
answer's sealed record in `data/kb.db`, compressed. Nothing deletes it
automatically. Disposal will be the retention item's job: when the bytes are
deleted the hash stays, the record still verifies, and the record is marked
as no longer replayable.

With each answer it also records:

- **the seed**: a fresh random number sent with the request. Sampling is as
  random as it was before; the difference is that the seed is now known.
- **the model's content digest**: which weights answered, not just the tag
  name. A tag can move to new weights when it is pulled again. NEXUS reads
  the digest the first time a tag answers and keeps it until it restarts, so
  restart NEXUS after pulling a model under a tag it is already using.
- **the exact options**: the request body minus the messages and the seed.
- **the environment**: NEXUS's commit (and whether its files were modified),
  Python, the OS, the Ollama version, the httpx version (it decides how the
  request is encoded), and the GPU and driver where `nvidia-smi` exists. It is
  read once, at the first answer after NEXUS starts. A fact that cannot be
  observed is left out and the record says why. It is never guessed.

Each record carries a reason for every one of these that is missing. Records
written before W0b have none of them, and say so on `/lineage/{id}`
(`replay_capture.captured: false`).

**Where to look.** `GET /lineage/{id}` → `replay_capture` (the options and the
environment resolved, and whether the request is kept, with its size).
`GET /cortex/stats` → `replay_capture` (how many records carry each field,
and the kept bytes, raw and stored). Over MCP, `nexus_status` →
`replay_capture`. `GET /lineage/verify` → `request_bodies` checks every kept
request still hashes to the `request_hash` its record seals.

**Switches** (both read on every call; see `.env.example`):

- `NEXUS_KEEP_REQUESTS=off`: keep no request text. Records are otherwise
  unchanged, and each one notes `not kept: NEXUS_KEEP_REQUESTS=off`.
- `NEXUS_INFERENCE_SEED=off`: send no seed. This is the request exactly as it
  was before W0b. Each record notes why its seed is NULL.

**Under MCP** the client's model answers, not NEXUS's. Its seed, weights,
options and runtime cannot be observed, so those four fields are NULL with
that reason. What NEXUS keeps is the block it returned, which is the text
that entered the client's context.

**Under the api backends (W3 part 2)** the record's `model_id` is the model
the API reported answered, prefixed `openai:` or `anthropic:`. Under
`openai` the seed is sent (`seed`, best-effort at OpenAI's own API) and the
digest is the endpoint's `system_fingerprint`, its fingerprint of the
backend configuration the model ran with, absent with that reason when the
endpoint reports none; under `anthropic` there is no seed and no digest,
each absent with its reason (the API names no weights). The options are the
body minus the request's text (for Anthropic, minus the top-level `system`
string too, which the kept messages rebuild). The environment carries the
endpoint's host, never the URL as set. No key reaches any record.

## Checking that a decision can be re-run (W0b)

Run this once after a day of use, with NEXUS stopped. It answers one
question: is what the records keep enough to ask the model the same question
again and get the same answer?

1. Stop NEXUS. The check refuses to run while anything answers where NEXUS
   serves (`NEXUS_HOST:NEXUS_PORT`, default `127.0.0.1:8000`), because other
   requests change how the model batches and would make the comparison
   meaningless. It reads the port from `NEXUS_PORT` only: if you started
   NEXUS with `start.py --port N`, run the check with `NEXUS_PORT=N` set.
   Leave Ollama running, with nothing else using it.
2. Dry run (sends nothing, and shows which records it would re-send):

       python scripts/replay_check.py --db data/kb.db --last 20

3. The check itself, which re-sends each replayable record's kept request with
   its recorded seed and options:

       python scripts/replay_check.py --db data/kb.db --last 20 --confirm

   Each answer can take as long as it did the first time.
4. Send back `reports/replay_check_<date>.md`.

**What it reports.** Each record gets one verdict:

- **reproduced**: the model gave the same bytes, and the same answer once
  the reasoning was separated out.
- **diverged**: it did not. The report gives the position of the first
  differing character where the answer's text was captured
  (`NEXUS_CITATION_CAPTURE=on`), and says the position cannot be measured
  where it was not, because the record keeps hashes, not text. A divergence
  does not by itself mean the capture is insufficient. Ollama always turns on
  its prompt cache, and a cache can change a later run's arithmetic; whether
  it changes an answer here has not been measured.
- **not replayable**: one reason per record, counted apart and never folded
  into a rate. The reasons are: the record does not verify (its own hash);
  written before the capture; an MCP record (the client's model answered);
  request not kept; the kept copy corrupted or missing; no seed; no options,
  or an options row that does not hash to the record's key, is not text, or
  names another model; no digest (LM Studio names no weights); the tag now
  points at other weights (reported, never re-sent); the tag is not
  installed; Ollama refused the request. A stub record (W3: the stub ran no
  model, so there is nothing to re-run) and an api record (`openai`,
  `anthropic`: this check re-sends to Ollama only, the one provider that
  names the weights it serves) are classified by their provider first,
  before the seed or digest they also lack.

**It writes nothing to the store.** The database is opened read-only, with
every write refused, and the first line printed is the database path. The
only file written is the report. Exit codes: 0 done (whatever the counts),
2 no database, a database that is not NEXUS's, or `--last` below 1, 3 refused
because NEXUS is serving, 5 Ollama could not be reached.
