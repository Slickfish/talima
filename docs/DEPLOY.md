# Deploying NEXUS (W3)

The tested path, end to end: the image `deploy/Dockerfile` builds, the
compose project `docker-compose.yml` runs it, Caddy publishes the console
over HTTPS, the NEXUS container sits on an internal network with no route
out, a daily timer backs the stores up, and `scripts/verify_deploy.py` says
whether all of it works. Every file named here is validated by the test
suite (`tests/test_w3_deploy.py`, `tests/test_w3_backup.py`,
`tests/test_w3_ci.py`) and was run on the run's own Docker daemon; CI's
`deploy-smoke` job runs the same smoke on an `ubuntu-24.04` runner.

## What runs

| service | profile | network | published | runs as |
|---|---|---|---|---|
| `nexus-stub` | `stub` | `internal` only | nothing | the image's user, uid 10001 |
| `nexus-ollama` | `ollama` | `internal` only | nothing | uid 10001 |
| `nexus-api` | `api` | `internal` and `egress` (its model server is on the internet) | nothing | uid 10001 |
| `ollama` | `ollama` | `internal` and `egress` (its first-start pull) | nothing | the image's default (root) |
| `searxng` | `search` | `internal` and `egress` | nothing | the image's default |
| `hosted-t1`, `hosted-t2`, `hosted-t3` | `hosted` | `internal` and `front`, both without a route out | nothing | the Caddy image's default (root), every capability dropped |
| `nexus-t1`, `nexus-t2`, `nexus-t3` | `hosted` | none of its own: it joins its tester's pod (`hosted-tN`) | nothing | uid 10001 |
| `postgres` | `hosted` | `internal` only | nothing | the image's default (`postgres`) |
| `caddy` | `stub`, `ollama` and `api` | `internal` and `egress` | 80 and 443 | the image's default |
| `backup` | `backup` | `internal` only (no route out) | nothing | uid 10001, the data volume read-only |
| `backup-t1`, `backup-t2`, `backup-t3` | `backup` | `internal` only (it reaches the database by name) | nothing | uid 10001, its tester's volume read-only |

Each profile has NEXUS services of its own, so the profile *is* the
backend: `--profile stub` can never start Ollama beside a stub. Every NEXUS
service runs `python start.py --serve --port 8000 --data-root
/var/lib/nexus` as PID 1, with learning frozen (law 12), a
read-only root filesystem, a tmpfs `/tmp`, every capability dropped, and a
data volume (`nexus_data`; under `hosted`, each tester's own). Caddy
reaches the console profiles' NEXUS by the alias `nexus`. The `internal`
network is `internal: true` with `gateway_mode_ipv4: isolated`: from
inside the NEXUS container there is no default route, a connect to any
public address fails at once with ENETUNREACH, and no gateway address
exists at which a service on the host could be reached. Under `hosted` the
same holds for each tester's pod, whose two networks, `internal` and
`front`, are both such networks. The policy is the network's, not the
application's (law 17); `verify_deploy.py`'s V8 proves it on
every run of the `stub`, `ollama` and `hosted` profiles (per tester under
`hosted`), and is skipped by name under `api`.

**What `stub` is for.** `LLM_BACKEND=stub` runs NEXUS with no model server:
a deterministic in-process provider whose every record says so (`model_id`
`stub:primary`). It is CI's smoke, the first thing to run on a new host
(does the stack come up, does Caddy serve, does a backup land?), and never
a model. Switch profiles once it does.

## A fresh host (cloud-init, Ubuntu 24.04)

1. Copy `deploy/cloud-init.yaml` and fill the three values marked `FILL`
   in its `/opt/nexus/.env`: `NEXUS_PROFILE` (`stub`, `ollama`, `api` or
   `hosted`, whose further values are in the hosted section below),
   `NEXUS_DOMAIN` (the host name Caddy serves; its DNS record must point at
   the instance before first boot, or Caddy cannot obtain a certificate)
   and `NEXUS_API_TOKEN` (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).
   Under the `api` profile add the provider's lines (`NEXUS_API_BACKEND=openai`
   or `anthropic`, the key, the model: `.env.example`; the console's own
   `LLM_BACKEND` is not read by the api service).
2. Validate it: `cloud-init schema --config-file deploy/cloud-init.yaml`.
3. Launch the instance with the file as its user data (every cloud and
   Multipass take one). First boot installs Docker from Docker's repository,
   the NVIDIA container toolkit only if an NVIDIA device is on the PCI bus,
   writes the compose file and the Caddyfile byte for byte as this
   repository holds them, creates `/opt/nexus/backups` for the backup
   service's user, pulls and starts the chosen profile, and enables the
   daily backup timer (03:30, persistent).
4. On the host: `cd /opt/nexus && docker compose --profile "$NEXUS_PROFILE" ps`;
   `docker compose logs nexus-$NEXUS_PROFILE` shows the token the server
   generated if you left `NEXUS_API_TOKEN` empty (set one; the generated one
   changes with every process). Under `hosted` there is no token, and the
   services are `nexus-t1` and so on.
5. From any machine with the repository and docker:
   `NEXUS_DOMAIN=<domain> NEXUS_API_TOKEN=<token> python scripts/verify_deploy.py --profile <profile>`
   runs the checks that go through HTTPS; on the host itself, with the
   compose project beside it, the same command runs all eight (the backup,
   its restore and the egress proof need `docker compose`). Under `hosted`
   it runs on the host, per tester, every check that needs only `docker
   compose`, and skips by name the requests that go through your identity
   proxy (the hosted section below).

The image the compose file pulls is `ghcr.io/slickfish/talima:latest`
(`NEXUS_IMAGE`, `NEXUS_TAG`); a release (`release.yml`, dispatched by hand
with a `v*` tag) pushes it, as `:latest` and as the release's tag (for
example `:v1.0.0`). The workflow names the image after the repository it
runs in (`ghcr.io/<owner>/<repository>`, in lower case), so a release made
in a fork pushes the fork's image, and a compose file that should pull it
sets `NEXUS_IMAGE` to that name. Whether an anonymous pull works is the package's
visibility, which its owner sets: when the image is private, on the host,
`docker login ghcr.io -u <your GitHub login>` with a GitHub token that can read
packages (`read:packages`), then pull and start; or build the image from
`deploy/Dockerfile`. cloud-init runs its pull and `up` unattended at first
boot and logs in to nothing, so while the image is private both fail on a
fresh host: log in after the
boot, then `cd /opt/nexus && docker compose --profile <profile> pull &&
docker compose --profile <profile> up -d`. If no release has pushed the
image yet, build your own from a checkout of the source on the host
(cloud-init does not put one there): `docker build -f deploy/Dockerfile -t
nexus:local .`, set `NEXUS_IMAGE=nexus NEXUS_TAG=local` in
`/opt/nexus/.env`, and run the same `up` without the pull.
Behind a TLS-intercepting proxy, pass its CA to the build as a secret
(`--secret id=proxyca,src=ca.pem`), never as a layer. The build reads from
Docker Hub (`registry-1.docker.io` and `auth.docker.io`, and the CDN its
layers are served from, which was `production.cloudfront.docker.com` on
15 November 2026 and can change, for the BuildKit syntax frontend and
`python:3.11-slim`), PyPI
(`pypi.org` and `files.pythonhosted.org`, for `requirements.txt`),
`deb.debian.org`, and `www.postgresql.org` and `apt.postgresql.org` (the
PostgreSQL client's signing key and repository), so a host whose egress is
filtered must allow those.

## The GPU variant (`ollama`)

The `ollama` profile runs `ollama/ollama` with every NVIDIA device
reserved (`deploy.resources.reservations.devices`), which needs the
container toolkit cloud-init installs when a GPU is present. On first start
the service pulls the model tags `NEXUS_OLLAMA_MODELS` names (the measured
model, the extraction model and the embedder; pinned in the compose file,
never in the application), which takes as long as the download does; NEXUS
starts beside it and says on `/healthz.model_server` whether Ollama answers
yet. The Ollama container is dual-homed for that pull (declared); the NEXUS
container is not. No script checks a real model through the stack yet:
`scripts/real_model_smoke.py`, which earlier pages named, was never written
(BACKLOG W3b-E1). Until it is, the check is one turn through the console on
the host (`docs/FIRST_RUN.md`, step 12) with `verify_deploy.py`'s checks. CI has
no GPU and never runs this profile (it validates the compose file under it).

## The API variant (`api`)

`NEXUS_API_BACKEND=openai` (an OpenAI-compatible chat endpoint: OpenAI, vLLM,
llama.cpp's server, a gateway; the default) or `anthropic` (the Messages
API) names the api service's backend (the console's `LLM_BACKEND` in the
same `.env` is not read by it: `.env.example` sets that to `ollama`), with the
key and the model in `.env` (`OPENAI_BASE_URL`, `OPENAI_API_KEY`,
`OPENAI_MODEL`, `OPENAI_EXTRACTION_MODEL`; `ANTHROPIC_BASE_URL`,
`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_EXTRACTION_MODEL`). NEXUS
names no model for you and refuses to start with the model unset. The key
is read by one module, sent as a header, and reaches no record. Every record
names the model the API reported, prefixed `openai:` or `anthropic:`. The
embedder is separate (`NEXUS_EMBED_URL`, `NEXUS_EMBED_MODEL`,
`NEXUS_EMBED_API_KEY`; both or neither): Anthropic offers none, and with
none named the idle scans do not embed and `/cortex/stats.embedder` says why.
**This is the one profile whose NEXUS container has a route out**, because
its model server is on the internet; `verify_deploy.py`'s V8 is skipped by
name under it and not counted (V1 checks the service reports an api
backend). An egress proxy with an allowlist (the API's host only) is the
stricter alternative, a line in the development record's register.

## The hosted profile (`hosted`, W3b and W4)

`docker compose --profile hosted up -d` runs one NEXUS per tester, three
written out (`t1`, `t2` and `t3`), each on `NEXUS_STORE=postgres`. Every
table kb.db and archive.db hold lives in that tester's own database on the
`postgres` service. Each tester's data volume keeps chroma/, snapshots/
and the logs. The profile runs the stub provider: it is the deploy smoke's
shape and the hosted UAT's.

### One pod per tester, behind your identity proxy (W4 part 3)

NEXUS authenticates nobody. Under `hosted` each NEXUS runs in proxy mode
(`NEXUS_AUTH_MODE=proxy`, THREAT_MODEL.md): an identity proxy in front
authenticates the person and sends their login in one header, and NEXUS
accepts a request only from a loopback peer that carries the header once,
and only for its own owner. So each tester has a pod of their own:

- `hosted-tN` is tester N's relay, a small Caddy (`deploy/Caddyfile.relay`)
  that owns the pod's network namespace. It is on `internal`, where the
  database is, and on `front`, where the identity proxy is; neither network
  has a route out. It answers 403 to every immediate peer except the
  identity proxy's one fixed address. It forwards the rest to tester N's
  NEXUS unbuffered, with the request's headers as they arrived. Its admin
  endpoint is off, because any process in the pod could reach it.
- `nexus-tN` is tester N's NEXUS. It joins the pod's namespace and nothing
  else, and listens on 127.0.0.1:8000, so only its own relay can reach it.
  Its owner is `NEXUS_TN_OWNER`. With no owner set it refuses every request
  (403 "owner not set"): it never adopts a login.
- A tester's NEXUS lives in the network namespace its pod had when the
  NEXUS started. If the pod's container is restarted in place (`docker
  restart nexus-hosted-tN-1`, or its restart policy after a crash), the
  NEXUS is left with no network but its own loopback: the relay answers
  502, the NEXUS still reports healthy, and `docker compose up -d` changes
  nothing. Run `docker compose restart nexus-tN` to join it to the pod
  again. If the pod's container is recreated (a new image, or `docker
  compose up -d --force-recreate hosted-tN`), the next `docker compose up
  -d` recreates the NEXUS into it. Either way the tester is refused, never
  exposed, in the meantime (measured in W4's local proof).

Why a pod per tester, and not one pod for all: the processes in one network
namespace are each other's loopback peers, so in a shared pod tester 1's
process could call tester 2's port with any login. Why each relay trusts
one address and not the `front` subnet: every pod is on `front`, so a relay
that trusted the subnet would trust every other tester's process. Every
NEXUS service drops every capability, because a raw socket is how a
container would forge a peer address on a bridge.

**The identity proxy is yours to run.** The compose file sets the identity
header nowhere. Put your identity proxy on `front` at
`NEXUS_IDENTITY_PROXY_IP` (172.31.240.250 unless set), give it a route
out, and have it send each tester's login in `NEXUS_IDENTITY_HEADER`
(`Tailscale-User-Login` unless set) to that tester's relay,
`hosted-tN:8100`. It must replace any copy of the header a client sends.
`deploy/tailscale.md` and `deploy/cloudflare-access.md` describe the two
the project names. Neither vendor's pages could be read when they were
written, so each note ends with what to confirm on the vendor's own page.
Until your identity proxy is there, the hosted profile has no way in at
all. To check a tester's process, `GET /whoami` through the identity proxy
names their login and `proxy`. Serve the console to testers over HTTPS:
the cross-site rule reads `Sec-Fetch-Site`, which a browser appends only
to a request whose URL is "potentially trustworthy" (W3C Fetch Metadata
Request Headers, §3; W3C Secure Contexts, §3.1: `https`, `wss` and the
loopback ranges), so over plain HTTP at another address the console's own
writes would be refused (it fails closed; `SECURITY.md`, proxy mode;
BACKLOG W4-D4).

`front` has a fixed subnet, `NEXUS_FRONT_SUBNET` (172.31.240.0/24), and a
pool Docker hands dynamic addresses from, `NEXUS_FRONT_POOL`
(172.31.240.0/25), which the identity proxy's address lies outside. So a
container created while the identity proxy is down can never be handed its
address. If your host's own networks use these addresses, set all three
together.

**The deploy smoke's stand-in.** `deploy/compose.identity-sim.yml` is an
override file that adds one service, `identity-sim`: a Caddy at the
identity proxy's address that presents a fixed login per port, on
127.0.0.1, and also a port that presents tester 2's login to tester 1 and
one that sends no login. CI uses it to run the hosted checks
(`verify_deploy.py --identity-sim`). Never run it on a host that serves
testers: anyone who can reach those ports is taken for a tester. cloud-init
does not write it.

**The single `nexus-hosted` is retired.** W3b and W4 part 0 ran one
token-mode NEXUS on Postgres under this profile. A token-mode NEXUS on
Postgres is still `start.py --serve` with `NEXUS_STORE=postgres` and
`NEXUS_DATABASE_URL` (W3b's U and V series test it); it is no longer a
compose service.

**More testers.** The file writes out three. For a fourth, `t4`:

1. Add `t4` to `NEXUS_TESTERS` and `NEXUS_T4_PASSWORD` to the `postgres`
   service's environment, and set both in `.env`. The init script makes the
   role and the database only on a new volume (below).
2. Copy `hosted-t3`, `nexus-t3` and `backup-t3`, replacing `t3` with `t4`
   in each (the pod, the volume `nexus_t4_data`, the database URL, the
   owner variable `NEXUS_T4_OWNER`, the backup folder), and declare the
   volume.
3. Make the backup folder for the image's user, as cloud-init makes the
   first three: `install -d -o 10001 -g 10001 -m 0750
   /opt/nexus/backups/t4`. The owner is what matters: the image's user is
   uid 10001 (`docker run --rm --entrypoint id <NEXUS_IMAGE>:<NEXUS_TAG>`
   prints it).
4. Route your identity proxy to `hosted-t4:8100` for the new tester.

The daily backup and `verify_deploy.py` find the new services themselves.
The stand-in, and so `--identity-sim`, knows three testers. Each tester's
NEXUS holds about eight database sessions (8 each, measured on three
testers in W4's local proof; `verify_deploy.py` counts them every run and
prints the count as MEASURED, never as a verdict). The server's
`max_connections` is the image's default (100 there), so it bounds the
number of testers, with room left for the backups and their restore
checks.

### The database's roles

The database has three kinds of role (W4 part 0, its spec's D14):

- The superuser is the image's own `postgres`. Its password,
  `NEXUS_POSTGRES_PASSWORD`, is used only when the database is first
  initialised. Nothing connects as it after that, and no NEXUS service
  and no backup service is given its password.
- Each tester key in `NEXUS_TESTERS` (`t1 t2 t3` unless you set it) has a
  role `nexus_<key>` and a database `nexus_<key>` that the role owns. The
  role can log in and has no other attribute: no superuser, no CREATEDB, no
  CREATEROLE, no replication, no BYPASSRLS. Its password is
  `NEXUS_<KEY>_PASSWORD`, the key in upper case. Each tester's NEXUS
  connects as that tester's role and creates the store's tables in its
  database at first open.
- The read-only backup role `nexus_backup` can log in and create databases
  (the restore verification's scratch databases). In each tester's database
  it may read every table that tester's role creates there, now or later.
  Nothing it holds writes a store. One backup role reads every tester's
  store: it is the operator's backup.

CONNECT on a tester's database belongs to its owner and the backup role
alone. PUBLIC has none. So one tester's role cannot open another tester's
database: `verify_deploy.py`'s V10 tries it from inside each tester's
container. V9 reads the role each NEXUS connects as, and fails if it holds
any of those attributes or a predefined `pg_*` role, or if PUBLIC may
connect to its database (pg_database_owner, which a database's owner
belongs to by owning it, is accepted).

Set these values in `/opt/nexus/.env` first. The passwords go into URLs,
so use URL-safe characters only (`openssl rand -hex 24`). Each login is
compared exactly with what your identity proxy sends:

    NEXUS_POSTGRES_PASSWORD=<the superuser's password>
    NEXUS_T1_PASSWORD=<tester 1's password>
    NEXUS_T2_PASSWORD=<tester 2's password>
    NEXUS_T3_PASSWORD=<tester 3's password>
    NEXUS_BACKUP_PASSWORD=<the backup role's password>
    NEXUS_T1_OWNER=<tester 1's login>
    NEXUS_T2_OWNER=<tester 2's login>
    NEXUS_T3_OWNER=<tester 3's login>

Each tester's backup service builds its URL from `NEXUS_BACKUP_PASSWORD`;
there is no backup URL to set.

The database's first start runs `deploy/postgres-init.sh`, which makes
these roles and databases. Before it makes anything, it stops and names
what is wrong: a password that is unset, an empty `NEXUS_TESTERS`, or a key
that is not lower-case letters, digits and underscores. The script reads
only the `postgres` service's environment, so a key added to
`NEXUS_TESTERS` needs its own `NEXUS_<KEY>_PASSWORD` line there in the
compose file. Passwords reach `psql` through its environment, never on a
command line. The database is on the internal network only and publishes
nothing. TCP keepalives make a vanished client's session, and the
single-writer lock it held, end in about a minute and a half (W3b D8: the
lock is a session advisory lock).

**An existing SQLite instance moves over** with
`scripts/migrate_sqlite_to_postgres.py`, and back with
`scripts/export_postgres_to_sqlite.py`. Each is a dry run unless given
`--confirm`. Each refuses a target that already holds a store, and proves
its copy before it commits (every table's rows, the record hashes, the
chain, the graph's order, the as-of pages, the sequences), so a copy that
does not prove writes nothing. The migration leaves `kb.db` and
`archive.db` in the data root: move them out once it is verified, before
the next backup. One backup holds one store: while a tester's data volume
(`nexus_t1_data` and so on) still holds either file, that tester's backup
is refused by name with exit 2, writes nothing, and the timer's unit
fails; with both moved out, the same command backs up the hosted store.
`docs/OPERATOR_RUNBOOK.md` has the commands.

**What differs from SQLite, declared** (W3b's spec, D15). Nothing
else does, and `tests/test_w3b_parity.py` measures the rest on both
backends: the same records, hashes, injected ids, cascade, as-of pages,
search candidates, graph order and sums.

- `start.py`'s check of the archive chain before it launches reads SQLite
  files, so on Postgres it is skipped and its line says so ("the archive
  chain check reads SQLite files and does not apply to a hosted store"); `/chain/status` and `/lineage/verify` check the
  hosted store once it serves (BACKLOG W3b-F9).
- `-0.0` keeps its sign on Postgres; SQLite stores it as `0.0`. A recomputed
  hash does not move (a stored zero verifies under either sign).
- `INSERT OR IGNORE` becomes `ON CONFLICT DO NOTHING`, which is narrower:
  SQLite's form also swallows a NOT NULL or CHECK violation, and Postgres
  refuses one.
- `REFERENCES` constraints are enforced on neither backend.
- A writer is never blocked behind a reader on Postgres. On both, a writer
  behind another writer fails after 5 s with the same neutral error.
- The read-only role may create a temporary table, which is not the store.
- A round trip through Postgres returns the same rows, but not SQLite's
  hidden physical order for the tables keyed by text. Nothing reads that
  order, and the two tables whose order SQL reads keep theirs.
- Postgres plans a read from table statistics, and after a large write they
  are stale until autovacuum next analyzes the table (within about a
  minute by default; `ANALYZE` by hand at once). Measured at 10,000 claims
  after one archival act over half the store: an as-of read took between
  0.1 and 0.8 s until the refresh (0.6 to 0.8 s where the statistics had
  been taken just before the act) and 0.1 s after it. It moves how long a
  read takes and nothing a read returns where its order is total (the two
  statements whose order is not are BACKLOG W3b-F7). SQLite keeps no
  statistics, so it has no such state.

A backslash in a search term used to be a difference: Postgres's LIKE
escapes with it. It is not one now: the dialect appends `ESCAPE ''`, and
the same rows come back on both.

### An initialised volume keeps its roles

The image runs the script only on an empty data directory, so a volume
keeps the roles it has. Three things follow.

- If the script stopped on the first start, the volume is initialised
  without the roles, and the script will not run again on it. Read the
  line it printed (`docker compose logs postgres`) and fix that setting.
  Then stop the stack (`docker compose --profile hosted down`), remove the
  volume, which holds no store yet (`docker volume rm nexus_postgres_data`:
  the compose project is named `nexus`), and start again.
- A password changed in `.env` later changes nothing in the database.
- A volume made before W4 keeps W3b's roles: the superuser `nexus`, which
  NEXUS connected as, and the store in the database `nexus`. Do not edit
  its roles in place. Nothing here tests that.

The supported way off such a volume is a new volume, with the store moved
by W3b's two tested scripts. They need the database, which publishes no
port, so run them inside the compose project: `docker compose --profile
backup run --rm --entrypoint python backup scripts/<script> …`, with the
folder under `/var/backups/nexus`.

1. Take a backup (below) and copy it off the host.
2. Export the store: `scripts/export_postgres_to_sqlite.py --from
   postgresql://nexus_backup:…@postgres:5432/nexus --out <folder>
   --confirm`. It reads through the read-only backup role, which the old
   volume has, and moves the two files into place only when its checks
   pass.
3. Stop the stack (`docker compose --profile hosted down`). Remove the old
   volume (`docker volume rm nexus_postgres_data`) only once the backup and
   the export are safe: this deletes the old store.
4. Start the database alone on the new volume, with the values above:
   `docker compose --profile hosted up -d postgres`. Its first start makes
   the roles. NEXUS must not start yet: it would make an empty store, and
   the import refuses a database that holds one.
5. Import the files as tester 1: `scripts/migrate_sqlite_to_postgres.py
   --data-root <folder> --to postgresql://nexus_t1:…@postgres:5432/nexus_t1
   --confirm`. It proves its copy before it commits.
6. Start the rest (`docker compose --profile hosted up -d`) and run
   `verify_deploy.py --profile hosted`.

The two scripts are tested both ways by `tests/test_w3b_migration.py`.
The compose steps around them are the operator's, and CI does not run
them.

## Backup and restore

The `backup` profile's service runs `scripts/backup.py` on the data volume,
mounted read-only, on the internal network only, writing one folder per run under
`NEXUS_BACKUP_DIR` (`/opt/nexus/backups` on a cloud-init host; `./backups`
beside the compose file otherwise, owned by uid 10001) and keeping
`NEXUS_BACKUP_KEEP` (14). A running console is never blocked or written:
the copies go through SQLite's online backup API from read-only opens,
kb.db before archive.db. Each folder carries `MANIFEST.json` (counts, the
chain head, the lineage digest, every file's sha256, each copy's
`integrity_check`). The timer runs it daily; by hand:

    docker compose --profile backup run --rm backup
    docker compose --profile backup run --rm --entrypoint python backup scripts/restore_verify.py --latest-under /var/backups/nexus

The second command restores the newest backup into a scratch folder and
runs five checks (the manifest's hashes and counts, the chain, the
product's own verifiers on a second copy, the restored bytes unchanged);
`docs/OPERATOR_RUNBOOK.md` says what each is and how to restore for real.

Under `hosted`, one backup holds one store, so each tester's store has a
backup service of its own, `backup-tN`: tester N's volume read-only, the
folder `NEXUS_BACKUP_DIR/tN` (so retention prunes within a tester and
`--latest-under` reads one tester's backups), and the read-only backup
role's URL on tester N's database, which compose builds from
`NEXUS_BACKUP_PASSWORD` and hands over in the environment (`backup.sh` and
`backup.py` read it there, never from a command line):

    docker compose --profile backup run --rm backup-t1
    docker compose --profile backup run --rm --entrypoint python backup-t1 scripts/restore_verify.py --latest-under /var/backups/nexus

The daily timer runs `deploy/nexus-backup.sh`, which runs every tester's
backup service (it asks compose which there are), each one even when
another fails, and fails naming the ones that did. The backup dumps the
store with the image's `pg_dump` (postgresql-client-16, the server's major)
through the read-only backup role. The dump comes from a snapshot the manifest's counts,
chain head and lineage digest are read in, so the manifest describes
exactly the dump. The restore verification restores it into two scratch
databases on the same server, runs the five checks there, and drops both,
whatever the checks found. If a scratch database cannot be dropped (another
role's session is on it, which the backup role may not end), it is named on
a `FAIL cleanup` line and the run fails (exit 1) though the five checks
passed: drop that database by hand as a role that may end its sessions.
Copy the backup directory off the host (it is the only copy the stack does
not hold). The directory is the image's user's (uid 10001, mode 0750), so
a user who is not root cannot list it: `verify_deploy.py`'s V6 then reads
the manifest through the backup service's own mount and says so, and
copying it off the host takes `sudo`.

## Credentials, per job

- The console token (`NEXUS_API_TOKEN`) is the instance's; it guards every
  state-changing route and every evidence read behind Caddy, which adds
  transport only. Under `hosted` there is no token: your identity proxy
  authenticates each tester, and each tester's NEXUS answers only its
  owner's login from its own relay.
- The deploy key (`deploy.yml`: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_KEY`
  as repository secrets, with that instance's `NEXUS_API_TOKEN` for the
  verification) is an SSH key for ONE host and nothing else. To rotate it:
  generate a new key pair, add the public key to the host's
  `~/.ssh/authorized_keys`, replace the `DEPLOY_KEY` secret, run the deploy
  workflow once, then remove the old public key from the host. With any of
  the three secrets unset, `deploy.yml` runs one step that says so and ends
  green: a deploy that could not happen is reported, never mistaken for one
  that did.
- Under the `api` profile, the provider's key lives in the host's
  `/opt/nexus/.env` (mode 0600, root) and the compose file hands it to the
  `nexus-api` container only.

## CI

Every workflow is started by hand (`workflow_dispatch`): since 17 November
(the operator's Amendment 7) no push, tag or pull request starts one, and an
item's evidence is run on a clean checkout outside GitHub Actions. A
dispatch runs a workflow's full coverage.

- `ci.yml`: the suite on ubuntu and macos, on windows in four parallel
  shards whose counts are added up against the unsharded count, through a
  windows venv, and on Postgres on ubuntu; the simulated UAT on the three
  systems (`uat-simulate`) and its hosted section on Postgres
  (`uat-simulate-postgres`).
- `bundle.yml`: the desktop bundles (W2).
- `deploy-smoke.yml`: the image built from the tree, the `stub` and
  `hosted` profiles up on `ubuntu-24.04`, one job each, and
  `verify_deploy.py`'s checks: eight under `stub`; under `hosted`, three
  testers behind the stand-in (`--identity-sim`), every check per tester
  (38 in all).
- `release.yml`: dispatched with an existing `v*` tag, the image to GHCR
  and the bundles and the `nexus-verify` binaries (Linux, Windows, macOS;
  W9's standalone verifier, each run once before upload) as release assets;
  a `*-dryrun` tag builds everything and publishes nothing.
- `deploy.yml`: manual dispatch; pull and restart on the one host, then
  `verify_deploy.py` from the runner: the console checks over HTTPS, the
  backup, its restore and the egress proof through `docker compose` on the
  host over SSH (`--remote`); under `hosted` every per-tester check that
  needs only `docker compose`, the requests through your identity proxy
  skipped by name; a named no-op without its secrets.

## How this page was checked

On 16 November 2026, by the documentation session, against the code at
`48ad15c`: `docker compose --profile <p> config -q` for `stub`, `ollama`,
`api`, `hosted` and `backup`, with no Docker daemon. On 18 November 2026,
with a Docker daemon, at `a261e1e`: the image built from `deploy/Dockerfile`
(`--network host`, behind this sandbox's TLS-inspecting proxy with its CA as
the build secret); the `stub` profile verified by `scripts/verify_deploy.py`
(8 of 8 checks, V9 not applicable) and the `hosted` profile with three
testers (38 of 38); and the image run on its own (uid 10001, uvicorn as PID
1, `/healthz` on the stub, one turn, `docker stop` in a second). Not run
here: the GPU profile (no GPU), cloud-init's schema check and a fresh host
(no `cloud-init`, no cloud), a pull from GHCR (no registry login here), and a
real identity proxy (Tailscale or Cloudflare). The backup scripts were run
outside compose, on SQLite and on Postgres.
