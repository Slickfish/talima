# Your first hour with NEXUS

From nothing to a decision a stranger can check: install NEXUS, ask it one question, read back the sealed record
of that answer, hand someone a credential they verify without your store, and back the store up (steps 1 to 8). No
model server is needed: the stub backend gives one fixed answer, so this exercises the record, not a model. Then
the console with a real model (steps 9 to 15), and NEXUS inside the Claude desktop app (steps 16 to 19).

Every command in steps 1 to 8 was run on Linux on 18 November 2026, in a clean checkout and a fresh virtual environment, and
each block shows what it printed (some lines left out and long ones cut; your paths, ids and token will differ). The Windows lines are given
where they differ and were not run here; on Windows use PowerShell, and call `curl.exe`, not `curl`, which in
Windows PowerShell is another command.

## 1. Install (a few minutes)

You need Python 3.11 or later and git.

```bash
git clone <this repository's URL> nexus && cd nexus
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
sed -i 's/^LLM_BACKEND=.*/LLM_BACKEND=stub/' .env
```

On Windows: `py -3.11 -m venv .venv`, `.venv\Scripts\pip install -r requirements.txt`, `copy .env.example .env`,
then set `LLM_BACKEND=stub` in `.env` with any editor. On macOS, `sed -i ''` instead of `sed -i`.

## 2. Check, then start

```bash
.venv/bin/python start.py --check
```

```
NEXUS start.py --check (launches nothing, writes no data)
  packages    all 18 of requirements.txt import
  model       none: LLM_BACKEND=stub, the stub provider answers in-process (backend/providers/stub.py); no model server is checked
  store       sqlite: kb.db and archive.db under the data root data
  bind        127.0.0.1:8000
result: every check passed (exit 0)
```

```bash
.venv/bin/python start.py --serve
```

It runs the same checks, says learning is `frozen` (every new instance starts frozen), and serves on
http://127.0.0.1:8000. Leave it running and use a second terminal. Open http://127.0.0.1:8000 in a browser: the
first time the page needs a token, the server prints one in its own terminal:

```
NEXUS_API_TOKEN not set. Generated session token:
    <a token of 43 letters, digits, - and _>
```

That token lasts until the server stops. For one that stays, set `NEXUS_API_TOKEN` in `.env` and restart. In the
second terminal, keep it in a variable (PowerShell: `$TOKEN = "..."`):

```bash
TOKEN=<the token it printed>
```

## 3. Ask one question

Type a question in the console, or send one:

```bash
curl -N -X POST http://127.0.0.1:8000/chat -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{"message": "What does NEXUS record?"}'
```

The answer streams back as server-sent events (seven here, ending with `done`; their order is in
`docs/INTEGRATION_REFERENCE.md`). The stub's answer says
what it is: "This is the fixed answer of NEXUS's stub provider (LLM_BACKEND=stub) ... No language model ran". The
last frame, `done`, carries the record's id:

```
data: {"turn": 1, "tokens": 1, "lineage_id": "3b13f97c-b1ac-4fda-a0db-becb2a0fc247", "learning": {"state": "frozen", ...
```

## 4. Read the record back

```bash
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/lineage/3b13f97c-b1ac-4fda-a0db-becb2a0fc247
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/lineage/verify
```

The first returns the record: what the model was shown, which claims the answer cited, the hashes of the question
and the answer, who asked, and whether NEXUS was learning. Who asked is empty here: a request names its person
with `"accountable": "<name>"` in its body, or an identity proxy does (`docs/DEPLOY.md`), and a turn that names
no one is recorded against the session (`session:default`). Without the token it answers `401`. The second checks
every record against its hash: `"records_checked": 1, ... "verdict": "intact"`.

## 5. Seal it

A record is sealed into the archive chain by the next idle cycle (every five minutes while idle). To seal now:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/kairos/cycle
```

## 6. Give someone a credential they check without you

```bash
curl -H "Authorization: Bearer $TOKEN" \
     "http://127.0.0.1:8000/lineage/3b13f97c-b1ac-4fda-a0db-becb2a0fc247/credential?disclose=model_id,ts" > credential.json
curl http://127.0.0.1:8000/identity > identity.json
```

`credential.json` proves the record is in a block signed by this instance's key, and discloses only the fields you
named; `identity.json` is the instance's public key (open, no token). Whoever checks needs those two files and
`scripts/nexus_verify.py`, one file using only Python's standard library. Here it ran from an empty folder with the
system's Python and nothing installed:

```bash
python3.11 -I nexus_verify.py credential.json --identity identity.json
```

```
DISCLOSED model_id = "stub:primary"
DISCLOSED ts = 1795016225.5743263
nexus-verify: VERIFIED
```

Change a disclosed value and it says so: with `ts` altered by one second it printed `FAIL disclosure_mismatch: the
value and salt of ts do not open its commitment` and `nexus-verify: NOT VERIFIED (1 failure(s))`, exit 1. `ts` is
UTC epoch seconds.

## 7. Back up, and check the backup restores

With the server still running:

```bash
.venv/bin/python scripts/backup.py --data-root data --out backups
.venv/bin/python scripts/restore_verify.py --latest-under backups
```

```
chain: 1 blocks, head 0 f70466821cbf (snapshot)
lineage: 1 records
...
restore verification: 5 of 5 checks passed; taken 2026-11-18T15:37:37Z from .../data
```

A backup never copies the instance's private key (`data/instance_key.json`): a restored instance makes a new one,
and credentials issued under the old key still verify against the new `/identity` (`docs/OPERATOR_RUNBOOK.md`).

## 8. Stop

Ctrl+C in the server's terminal. What this hour made: under `data/`, `kb.db` (the claims and the records),
`archive.db` (the chain), `instance_key.json` (the instance's private key), `chroma/`, `snapshots/`,
`turn_metrics.jsonl`, and `kb.db.lock`, which stays after a clean stop and then reads `"released": true`; and in
the folder you ran the commands from, `backups/`, `credential.json` and `identity.json`.

## With a real model

The console on your own computer, with a real model. For a server, see `docs/DEPLOY.md`; for the Claude desktop
app, steps 16 to 19.

**How the commands below were checked.** Each block says whether it was run in the documentation session's sandbox
(Linux, Python 3.11.18, 16 November 2026) or not, and why not. That sandbox has no GPU, no Ollama and no outbound
access to the seed sources, so the model-server steps were not run there; NEXUS's own commands were, under
`LLM_BACKEND=stub` where a model would answer. On Python 3.12.3 the install and `start.py --check` passed there
too; the suite was not run on it.

## 9. A model server

NEXUS never starts a model server; start one yourself. Ollama is the default (`LLM_BACKEND=ollama`), LM Studio
the alternative (`LLM_BACKEND=lmstudio`). An API model instead of a local one is `LLM_BACKEND=openai` or
`anthropic`: see `docs/OPERATOR_RUNBOOK.md`, item 9.

Not run here (no GPU and no Ollama in the sandbox):

```bash
nvidia-smi
```

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

```bash
ollama serve
```

The models NEXUS routes to under Ollama are the tags in `backend/providers/model_map.py`. Pull them by exactly
these names: Ollama matches a tag by its full name, so a tag with another suffix is a different model to NEXUS.
Not run here (no Ollama):

```bash
ollama pull deepseek-r1:14b-qwen-distill-q4_K_M
```

```bash
ollama pull phi4-mini:3.8b-q4_K_M
```

```bash
ollama pull nomic-embed-text
```

```bash
ollama pull qwen2.5-coder:7b
```

The first answers, the second extracts claims from each answer, and the third is the embedder the idle cycle uses;
the compose `ollama` profile pulls these three. The fourth is the code model and is optional: when a turn routes
to a model that is not installed, NEXUS proposes the pull (`model_pull_proposal`) and answers that turn with an
installed general model. The multimodal model (`qwen3.5:9b-q4_K_M`) is pulled only when you confirm NEXUS's
proposal.

## 10. Settings: `.env`

Step 1 made `.env` from `.env.example` and set the stub; set `LLM_BACKEND` back to your model server now.
`.env.example` lists the settings an operator usually sets, each with its default and what it does; settings for
measurement, capture and rollback are not all in it (most are in the settings block of `docs/CONTRACTS.md`, and
no page lists all of them). `python start.py` offers to make the copy for
you when there is no `.env` (run here: it asks which model server, writes it on the `LLM_BACKEND` line, and puts
the day it made the file on the `NEXUS_LEARNING_FROZEN_SINCE` line). `start.py` reads `.env` and never writes it
otherwise.

**A new instance is frozen**: `.env.example` sets `NEXUS_LEARNING=frozen`, so NEXUS makes no
automatic learning write (the association graph, reinforcement, tier moves, promotion, trails) until you switch it
on. Every claim path, every confirmed act and every record still write, and every record says which state it was
written in; `/cortex/stats.learning` and every `done` frame show it. To switch learning on, set
`NEXUS_LEARNING=live` in `.env` and restart: that is your deliberate act. If you copied
the file by hand, put the date you froze (or unfroze) in `NEXUS_LEARNING_FROZEN_SINCE` (`YYYY-MM-DD`); frozen with
no date reads "frozen since: undeclared".

Two settings to look at now:

- `NEXUS_API_TOKEN`: the bearer token for every state-changing route and the gated reads. Left empty, the server
  generates one per process the first time a gated request or the console page needs it, and prints it in its
  console (run here); it changes with every restart, so set one. To make one (run here):

  ```bash
  python -c "import secrets;print(secrets.token_urlsafe(32))"
  ```

- `NEXUS_DATA_ROOT`: where every console file goes (kb.db, archive.db, chroma/, snapshots/, turn_metrics.jsonl and
  the logs). Empty means `data/` under the directory you start NEXUS from. Nothing needs initialising: the server
  creates its stores on first start.

`DB_PATH`, `ARCHIVE_DB`, `CHROMA_DIR` and `NUM_CTX` are read by nothing; `docs/CONTRACTS.md` says why.

## 11. Run

Run here (the checks pass under the stub; with Ollama down, `--check` exits 3 and names the reason):

```bash
python start.py --check
```

```bash
python start.py
```

```bash
python start.py --serve
```

`--check` runs the checks and launches nothing. `python start.py` is interactive: Enter is No at every prompt (type
`y` to begin and `y` to start); with no answer at all (`</dev/null`, a container) it exits 1 having launched
nothing. `--serve` asks nothing,
never writes `.env`, says whether the model server answered and starts regardless; it is the container's
entrypoint. All three name the database they read on their first line. `--port N` picks the port, `--data-root D`
the data directory, `--dev` turns on reloading (development only, never for the instance you keep). The exit codes
are in `docs/OPERATOR_RUNBOOK.md`.

**Launching without `start.py`.** Use the same command it runs: its argv fixes one worker and the trusted proxy
peers, because `start.py` also removes `UVICORN_*`, `WEB_CONCURRENCY` and `FORWARDED_ALLOW_IPS` from the server's
environment (`WEB_CONCURRENCY=2` would start two writers on one store, `UVICORN_RELOAD=true` would reload). The
quotes keep the last value one argument in cmd.exe and PowerShell as well as here. Run here, under the stub:

```bash
python -m uvicorn backend.server:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --forwarded-allow-ips "127.0.0.1,::1"
```

Run that way, unset those variables yourself, and set `NEXUS_LEARNING=frozen` unless your `.env` sets
`NEXUS_LEARNING`: `start.py` launches frozen when nothing sets it, while the server on its own defaults to live.
`--factory` is required: `backend.server` has no module-level `app`, so uvicorn calls `create_app()`.

> `--host 0.0.0.0` exposes the server on your network. The default posture is localhost only: read
> `THREAT_MODEL.md` and `SECURITY.md` first. `python start.py` binds `127.0.0.1` unless `NEXUS_HOST` says otherwise,
> and warns for any host that is not loopback.

Open http://localhost:8000 for the console; http://localhost:8000/memory is the memory view.

## 12. Check it works

Not run here (no Ollama); it lists the models Ollama has:

```bash
curl localhost:11434/api/tags
```

Run here. `/health` and `/healthz` are the same route: a JSON object naming the model server and whether it answers
(`model_server.reachable`), the store, the identity mode, the claim count and the embedder's state. It answers 200
while NEXUS serves, whatever the model server's state:

```bash
curl localhost:8000/health
```

One turn, streamed as server-sent events ending in a `done` frame that carries the turn's `lineage_id`. Run here,
under the stub, with `NEXUS_API_TOKEN` exported in the shell:

```bash
curl -X POST localhost:8000/chat -H "Authorization: Bearer $NEXUS_API_TOKEN" -H "Content-Type: application/json" -d '{"message":"hello"}' --no-buffer
```

The turn's sealed record, and the check of every record (run here; without the token both answer 401):

```bash
curl localhost:8000/lineage/<lineage_id> -H "Authorization: Bearer $NEXUS_API_TOKEN"
```

```bash
curl localhost:8000/lineage/verify -H "Authorization: Bearer $NEXUS_API_TOKEN"
```

## 13. Seed the store (optional)

Seeding fetches claims from ConceptNet or Wikidata and writes them, so it needs `"confirmed": true` (Law 3);
without it the route refuses, says why, and records the refusal. A fetch that fails is a warning in the response,
with nothing seeded. Each request a seed sends is a recorded action (W10b): its row is written before the request
leaves, and a request whose row cannot be written is not sent (the route answers `503 action_not_recorded`);
`/cortex/stats` counts the actions and `docs/OPERATOR_RUNBOOK.md` says how to read one. Run here: the routes
answered, the refusal without `confirmed` was returned and recorded, and both fetches failed because this sandbox's
proxy refused the two hosts, so the seeding itself was not run here.

```bash
curl -X POST localhost:8000/kb/seed/conceptnet -H "Authorization: Bearer $NEXUS_API_TOKEN" -H "Content-Type: application/json" -d '{"concepts":["health"],"confirmed":true}'
```

```bash
curl -X POST localhost:8000/kb/seed/wikidata -H "Authorization: Bearer $NEXUS_API_TOKEN" -H "Content-Type: application/json" -d '{"concepts":["software"],"confirmed":true}'
```

## 14. Run the tests

The whole suite, as `CONTRIBUTING.md` says it is run: about four thousand tests, in about twenty minutes on Linux;
a test that needs PostgreSQL runs when `NEXUS_TEST_STORE=postgres` names one (`docs/DEPLOY.md`):

```bash
python -m pytest tests/ -q
```

One phase's tests:

```bash
python -m pytest tests/test_phase_1.py -v
```

## 15. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `--check` exits 3: nothing is listening at `http://localhost:11434` | Ollama is not running: `ollama serve`, or the app |
| `--check` exits 4 | A line of `requirements.txt` does not import: `pip install -r requirements.txt` in the venv |
| `--check` exits 6 | Python older than 3.11 |
| exit 7: "this store is open in another NEXUS process" | Another NEXUS has this data root open; stop it, or use another `--data-root` |
| 401 on `/chat` or `/lineage/...` | The bearer token is missing or wrong (`NEXUS_API_TOKEN`) |
| VRAM out of memory | `ollama ps` shows what is loaded; `nvidia-smi` shows the card (an empty `ollama ps` while a model loads is not a liveness check) |
| No claims in the answer's context | The store is empty: seed it, or state something and confirm the offer |
| Slow first answer | Ollama loads the model on first use |

## In the Claude desktop app

NEXUS as a local extension of Claude Desktop: the app's model answers, NEXUS supplies the context and keeps the
record. Nothing is hosted, and the bundle makes no network calls (CI checks its binary for every connection it
makes).

## 16. Install the bundle

1. You need Claude Desktop on macOS or Windows, and the bundle for your
   computer, from a release's assets or built from the source
   (`scripts/build_bundle.py`):
   `nexus-<version>-darwin-arm64.mcpb`
   (a Mac with Apple silicon) or `nexus-<version>-win32-amd64.mcpb`
   (Windows). There is no bundle for a Mac with an Intel processor unless
   one is built on such a Mac.
2. Install it in Claude Desktop. Anthropic's help article
   ([Getting started with local MCP servers on Claude Desktop](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop),
   read on 16 November 2026) gives these steps: open **Settings > Desktop
   app** and choose "Manage" next to **Extensions** (in the older layout,
   **Settings > Extensions**); click "Advanced settings" and find the
   **Extension Developer** section; click "Install Extension…"; select the
   `.mcpb` file and follow the prompts. Menu names can change between
   versions of the app. These steps were not run for this guide: the
   desktop app cannot be driven where it was written.
3. The bundle is not signed. If macOS refuses to open it, allow it in
   System Settings → Privacy & Security. If Windows warns, choose to run it
   anyway. If either happens, say so in an issue on this repository.

## 17. Its settings

- **Owner** (required): your name or e-mail. Every record NEXUS keeps
  carries it as the accountable identity. You assert it; NEXUS never
  verifies it.
- **Data folder** (optional): where NEXUS keeps its store and logs. Leave
  it empty and NEXUS uses a folder of its own for your user account:
  `%LOCALAPPDATA%\NEXUS` on Windows, `~/Library/Application Support/NEXUS`
  on macOS. Do not choose a folder that syncs to other computers, because a
  store must be open on one computer at a time.
- **Learning is frozen**, and that is fixed by the bundle, not a setting:
  nothing NEXUS observes changes what it retrieves later. `nexus_status`
  says so on every call, and every record carries it.

Only one NEXUS process can open a store. If a second one tries (another
extension install, or the console on the same folder), it is refused with
"this store is open in another NEXUS process (pid N, since T)" and does not
start.

## 18. Three things to try

Before answering, Claude calls `nexus_retrieve` for the context NEXUS has
on your question. After answering, it calls `nexus_record`, which seals
what Claude was shown and what it said. Ask Claude to use the NEXUS tools
if it does not.

1. **Where an answer's claims came from.** Tell Claude something it can
   keep, for example "Remember: our Q3 target is 12 percent." It offers
   the claim (`nexus_assert` shows exactly what would be stored) and stores
   it only when you say yes (`nexus_assert_confirm`). Then ask a question
   that needs it, and ask which claims the answer rested on: the record
   names the claims it was shown.
2. **Pull back an earlier answer.** Later (a day or a week on, if you can),
   ask Claude for every answer that rested on that claim: `nexus_recall`
   lists them, oldest first, each with its owner.
3. **What NEXUS knew on a date.** Ask what NEXUS held at a moment, given
   with its time zone, for example "as of 2026-11-07T17:00:00+08:00":
   `nexus_asof` lists the claims held then. A date without a time and a
   zone is refused by name: say the time and the zone.

Two more tools are there if you want them. `nexus_commit` seals a
commitment to a note now, to reveal later and prove it was not changed;
`nexus_record_action` is how Claude reports something it did outside the
conversation (a request it sent, a change it made): NEXUS sees only the
actions it is told about, and stores those as `client_reported`.

## 19. Uninstall the bundle

Remove the extension on Claude Desktop's Extensions page (where you
installed it). Anthropic's help article gives no removal steps, so if you
cannot find the option, say what you saw in an issue. NEXUS never deletes a store,
so the data folder stays where it is. Keep it or delete it yourself.

## Next

- `docs/DEPLOY.md`: a server, the container image and the hosted profile for several testers.
- `docs/OPERATOR_RUNBOOK.md`: running an instance day to day, verifying, backing up and restoring, and the
  actions NEXUS records when it searches the web or seeds the store.
- `docs/CONTRACTS.md`: the settings, the events a turn streams, the record's contracts, and what is not built yet.
- `SECURITY.md` and `THREAT_MODEL.md`: what is and is not a security boundary.
