#!/usr/bin/env python3
"""W3 part 4 — verify a NEXUS deployment end to end (run/specs/W3.md D5
and D7; RUN_WORKLIST W3 item 6).

    python scripts/verify_deploy.py --profile stub [--base-url https://localhost]
                                    [--token T] [--project nexus] [--insecure]
                                    [--backup-dir ./backups] [--skip-backup]
    python scripts/verify_deploy.py --profile hosted [--identity-sim] [--backup-dir ./backups]

Run on the host that runs `docker compose`, against the compose project
this repository's docker-compose.yml started (`docker compose --profile
<profile> up -d`). It speaks to the console THROUGH Caddy (the one
published service) and to the containers through `docker compose exec` and
`run`. Each check prints PASS or FAIL with what it saw:

  V1  health: GET /healthz answers 200, names the profile's backend in
      `model_server` and its store in `store` (postgres under `hosted`,
      W3b part 5; sqlite under the others), and the liveness key is there;
  V2  the token: a POST /chat and a GET /lineage/recent without the token
      are refused (401), so the console is not open through the proxy;
  V3  one turn: POST /chat with the token streams to a `done` frame with a
      lineage id, and says which learning state it was written in;
  V4  the record sealed: GET /lineage/<id> carries the record's hash and
      GET /lineage/verify finds every record intact;
  V5  the chain verified: a forced KAIROS cycle seals, then GET /chain/status
      reports the chain valid;
  V6  a backup taken: `docker compose run --rm backup` (the `backup` profile
      service: scripts/backup.py on the data volume, read-only) writes a
      folder with a manifest under the backup directory. The manifest is
      read on the host as whoever runs this; the directory is the image's
      uid 10001's, mode 0750, so a user who may not look inside it has the
      manifest read through the backup service's own mount, and the check
      says which (cannot look is never reported as missing);
  V7  the restore verified: scripts/restore_verify.py, run through the same
      service with `--entrypoint python`, passes every check on that folder;
  V8  egress refused: inside the NEXUS container, no default route, a
      connect to each of three public addresses fails at once with
      ENETUNREACH (a timeout is not a pass), a sibling by name connects
      (the positive control), and the internal network has no gateway
      address. Under the `api` profile the NEXUS container has a route out
      by design (its model server is on the internet; declared in the
      compose file), so this check does not apply: SKIPPED by name, never
      counted;
  V9  the store's role (W4 part 0, run/specs/W4.md D14; BACKLOG W3b-F8),
      under `hosted`: inside the NEXUS container, a short Python opens the
      store through the setting NEXUS runs with (NEXUS_STORE and
      NEXUS_DATABASE_URL from the container's environment; nothing on a
      command line) on a read-only session and reads the role it connects
      as, the session user and the current user: none of superuser,
      CREATEDB, CREATEROLE, REPLICATION and BYPASSRLS, a member of no
      predefined `pg_*` role (pg_database_owner is the owner's implicit
      membership, and NEXUS's role owns its database by design), and PUBLIC
      may not connect to the database. It prints the role and each
      attribute, and fails naming each one held. Under the SQLite profiles
      the store is a file with no role: SKIPPED by name, never counted.

UNDER `hosted` (W4 part 3, run/specs/W4.md D19) there is no console Caddy
and no token: one NEXUS per tester, each in a pod of its own, in proxy mode,
behind the operator's identity proxy. The tester services are the compose
file's `nexus-t*` under the hosted profile, asked of compose (`docker
compose config --services`) and never listed here, and the checks run per
tester tN, the docker ones on any host:

  V1  health, inside the pod: /healthz from 127.0.0.1:8000, as the
      HEALTHCHECK reads it, is 200 with the stub backend, the postgres store
      and proxy mode with an owner set;
  V6, V7  the tester's own backup (backup-tN) taken and its restore verified;
  V8  egress refused inside the pod, the database the sibling, both of the
      pod's networks (internal and front) internal and isolated;
  V9  the store's role, as above;
  V10 another tester's database refuses this tester's role, for the CONNECT
      privilege (W4 part 0 revoked it from PUBLIC);
  V14 the relay refuses a peer that is not the identity proxy: from another
      tester's pod (from the tester's own backup container where there is
      one tester), a request carrying the owner's login, to each address the
      relay has, is answered by the relay's own 403;
  and the sessions each tester's process holds, read from pg_stat_activity
  as the superuser and printed MEASURED: measured, never judged or counted.

With --identity-sim (the deploy smoke: deploy/compose.identity-sim.yml laid
over the compose file) the requests through the identity proxy run against
its stand-in's ports on 127.0.0.1 (SIM_TESTER_PORTS), per tester:
  V11 GET /whoami names the tester's login (NEXUS_TN_OWNER where the
      verifier runs) and `proxy`, though the request carried another login
      of its own, which the stand-in replaces;
  V3  one turn through POST /chat;
  V4  its record carries the login as accountable_identity and
      identity_mechanism `proxy`, sealed, and /lineage/verify is intact;
  V5  the chain verified after a KAIROS cycle;
and across the testers:
  V12 as many distinct accountable_identity values as testers, each the
      tester's own;
  V13 for each tester, its own /lineage/recent holds its own turn and no
      record of another tester's (another's turn, or another's login);
  V2  the mismatch port (tester 2's login to tester 1's pod) is NEXUS's 403
      and the no-identity port NEXUS's 401 (the client's own copy of the
      header planted, for the stand-in to remove).
Without --identity-sim, on a real host whose identity proxy a verifier
cannot pass as a person, V2, V3, V4, V5, V11, V12 and V13 are SKIPPED by
name and never counted. With --remote, --identity-sim is refused (exit 2):
the stand-in's ports are on the deploy host's loopback.

The docker calls (V6 to V9, and under hosted every check but those through
the stand-in) run here, or on the deploy host over SSH with
`--remote "ssh -i key user@host" --remote-dir /opt/nexus` (deploy.yml's
way: the console checks go to --base-url, the docker ones to the host), so
a deployment verified from a runner is verified by the same checks.

--insecure accepts Caddy's certificate without verifying it: for
`localhost`, Caddy issues one from its own internal CA. Never use it
against a real domain. Exit codes: 0 every check passed; 1 a check failed;
2 the deployment could not be reached or an argument is bad.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.identity import DEFAULT_HEADER, NO_IDENTITY, NOT_OWNER, PROXY   # noqa: E402  (stdlib only)
from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import auth_header, load_env                   # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_UNREACHABLE = 0, 1, 2
PROFILE_BACKEND = {"stub": "stub", "ollama": "ollama", "api": None,      # api: NEXUS_API_BACKEND, openai by default
                   "hosted": "stub"}                                     # W3b part 5: the hosted store, the stub provider
API_BACKENDS = ("openai", "anthropic")
#: The NEXUS service each profile runs, as a pattern: under hosted one per
#: tester, `nexus-t1` and so on, read from the compose file when the
#: verifier runs (hosted_services; W4 part 3). tests/test_w3_ci.py's C9 holds
#: the patterns against compose.
PROFILE_SERVICE = {"stub": "nexus-stub", "ollama": "nexus-ollama", "api": "nexus-api", "hosted": "nexus-t*"}
# The store each profile opens (W3b: /healthz.store.backend); V1 checks it.
PROFILE_STORE = {"stub": "sqlite", "ollama": "sqlite", "api": "sqlite", "hosted": "postgres"}
PUBLIC_ADDRESSES = (("1.1.1.1", 443), ("8.8.8.8", 53), ("151.101.0.223", 443))
QUESTION = "what does interval training improve?"

# --- the hosted profile's names (W4 part 3) ----------------------------------
NEXUS_PREFIX, POD_PREFIX, BACKUP_PREFIX = "nexus-", "hosted-", "backup-"
#: A tester key k's role and database (deploy/postgres-init.sh): nexus_<k>.
TESTER_DATABASE_PREFIX = "nexus_"
#: The database service, and its superuser (the image's own, POSTGRES_USER:
#: W4 part 0), as which the sessions are read inside its container.
DATABASE_SERVICE, SUPERUSER = "postgres", "postgres"
#: V8's sibling and networks per profile (the positive control and the
#: networks whose isolation it reads); every other profile: Caddy, internal.
EGRESS_SIBLING = {"hosted": ("postgres", 5432)}
EGRESS_NETWORKS = {"hosted": ("internal", "front")}
#: Each tester's relay: the port it serves, and its fixed refusal to any
#: immediate peer but the identity proxy (deploy/Caddyfile.relay;
#: tests/test_w4_hosted.py holds the two equal).
RELAY_PORT = 8100
RELAY_REFUSAL = "forbidden: this relay answers only the identity proxy"
#: The deploy smoke's stand-in for the identity proxy (D16), and its ports
#: on 127.0.0.1: one per tester (that tester's login, to that tester's
#: pod); the mismatch (its port, the tester whose login it presents, the
#: tester whose pod it reaches); no identity (its port, the pod it reaches).
#: deploy/compose.identity-sim.yml and deploy/Caddyfile.identity-sim publish
#: and route exactly these (tests/test_w4_hosted.py).
SIM_SERVICE = "identity-sim"
SIM_FILE = "deploy/compose.identity-sim.yml"
SIM_TESTER_PORTS = {"t1": 18101, "t2": 18102, "t3": 18103}
SIM_MISMATCH = (18111, "t2", "t1")
SIM_NO_IDENTITY = (18112, "t1")
#: The login a probe or a planted header carries when no tester's is known.
PLANTED_LOGIN = "planted@verifier.invalid"
#: The checks that go through the identity proxy: run only against the
#: stand-in (--identity-sim), and otherwise SKIPPED by name, never counted.
THROUGH_THE_PROXY = (("V2", "the mismatch and the missing identity refused"), ("V3", "a turn, per tester"),
                     ("V4", "the record attributed, per tester"), ("V5", "the chain verified, per tester"),
                     ("V11", "whoami, per tester"), ("V12", "distinct identities across the testers"),
                     ("V13", "no record of one tester in another's store"))

EGRESS_PROBE = r'''
# verify_deploy's egress probe (V8).
import errno, json, socket, time
rows = [r.split() for r in open("/proc/net/route", encoding="utf-8").read().splitlines()[1:] if r.strip()]
ip = lambda h: socket.inet_ntoa(bytes.fromhex(h)[::-1])
routes = [(f[0], ip(f[1]), ip(f[2]), ip(f[7])) for f in rows]
def tcp(host, port):
    t = time.monotonic()
    try:
        s = socket.create_connection((host, port), timeout=5); s.close()
        return {"result": "connected", "seconds": round(time.monotonic() - t, 3)}
    except OSError as e:
        return {"result": type(e).__name__, "errno": e.errno, "name": errno.errorcode.get(e.errno),
                "seconds": round(time.monotonic() - t, 3)}
out = {"routes": routes, "default_route": any(d == "0.0.0.0" for _i, d, _g, _m in routes),
       "public": {f"{h}:{p}": tcp(h, p) for h, p in PUBLIC}, "sibling": tcp(SIBLING, SIBLING_PORT)}
print(json.dumps(out))
'''

#: V9: the role attributes the role NEXUS connects as must not hold.
ROLE_ATTRIBUTES = ("superuser", "createdb", "createrole", "replication", "bypassrls")
#: The one predefined role PostgreSQL gives a database's owner by owning it
#: (implicit, never granted; PostgreSQL 14 and later). NEXUS's role owns its
#: database by design (D14), so this membership is accepted, and only where
#: the role owns the database the probe is on.
OWNER_ROLE = "pg_database_owner"
#: V9's probe, run in the NEXUS container (`python -`, this text on stdin,
#: the store settings in the container's environment): it opens the store as
#: NEXUS does, on a read-only session that creates nothing, reads the catalog
#: with `search_path` pinned to pg_catalog, and prints one JSON line. A
#: failure prints its class and the driver's text with every secret of the
#: URL blanked (`redacted_text`, W3b's F6), never the URL.
ROLE_PROBE = r'''
import json, sys
def emit(out):
    print(json.dumps(out), flush=True)
    sys.exit(0)
try:
    from backend.store import configured_store_target, connect, redacted_text
except Exception as e:
    emit({"error": f"the store interface did not import: {type(e).__name__}"})
try:
    target = configured_store_target()
except Exception as e:                      # StoreConfigError: its text names no secret by design
    emit({"error": f"{type(e).__name__}: {e}"})
if target is None:
    emit({"error": "NEXUS_STORE does not name postgres in this container: the store is a file, with no role"})
try:
    db = connect(target, read_only=True)
except Exception as e:
    emit({"error": f"{type(e).__name__}: {redacted_text(str(e), target.url)}"})
ROLES = """
SELECT r.rolname::text, r.rolname = session_user, r.rolname = current_user,
       r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolreplication, r.rolbypassrls,
       ARRAY(SELECT p.rolname::text FROM pg_roles p
             WHERE p.rolname ~ '^pg_' AND p.oid <> r.oid AND pg_has_role(r.oid, p.oid, 'MEMBER') ORDER BY 1),
       EXISTS (SELECT 1 FROM pg_database d WHERE d.datname = current_database() AND d.datdba = r.oid)
FROM pg_roles r WHERE r.rolname IN (session_user, current_user) ORDER BY 1
"""
try:
    raw = db.raw
    with raw.transaction():
        raw.execute("SET LOCAL search_path = pg_catalog, pg_temp")
        database = raw.execute("SELECT current_database()::text").fetchone()[0]
        public = raw.execute("SELECT has_database_privilege('public', current_database(), 'CONNECT')").fetchone()[0]
        rows = raw.execute(ROLES).fetchall()
except Exception as e:
    emit({"error": f"{type(e).__name__}: {redacted_text(str(e), target.url)}"})
finally:
    db.close()
keys = ("role", "is_session_user", "is_current_user", "superuser", "createdb", "createrole", "replication",
        "bypassrls", "predefined", "owns_database")
emit({"database": database, "public_connect": public, "roles": [dict(zip(keys, list(r))) for r in rows]})
'''

#: V1's probe under hosted, run in the tester's NEXUS container: /healthz as
#: the image's HEALTHCHECK reads it, from inside the pod.
HEALTH_PROBE = r'''
# verify_deploy's health probe (V1, hosted): /healthz from inside the pod.
import json, urllib.request
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:8000/healthz", timeout=10) as r:
        print(json.dumps({"status": r.status, "body": json.loads(r.read() or b"null")}))
except Exception as e:
    print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
'''

#: V10's probe, run in a tester's NEXUS container: that tester's role, as
#: NEXUS connects (the container's own NEXUS_DATABASE_URL, never on a
#: command line), tries another tester's database, named in __CONFIG__.
#: A refusal's text is printed with the URL's secrets blanked.
OTHER_DATABASE_PROBE = r'''
# verify_deploy's other-database probe (V10).
import json, sys
CONFIG = json.loads(__CONFIG__)
def emit(out):
    print(json.dumps(out), flush=True)
    sys.exit(0)
try:
    import psycopg
    from backend.store import configured_store_target, redacted_text
except Exception as e:
    emit({"error": f"the driver or the store interface did not import: {type(e).__name__}"})
try:
    target = configured_store_target()
except Exception as e:                      # StoreConfigError: its text names no secret by design
    emit({"error": f"{type(e).__name__}: {e}"})
if target is None:
    emit({"error": "NEXUS_STORE does not name postgres in this container"})
database = CONFIG["database"]
try:
    conn = psycopg.connect(target.url, dbname=database, connect_timeout=10)
except psycopg.Error as e:
    emit({"database": database, "refused": True, "message": redacted_text(str(e), target.url)[:400]})
try:
    who = conn.execute("SELECT current_user::text, current_database()::text").fetchone()
finally:
    conn.close()
emit({"database": database, "refused": False, "connected_as": list(who)})
'''

#: V14's probe, run in a container that is not the identity proxy: a GET of
#: /whoami at each address of a tester's relay (__CONFIG__), carrying the
#: owner's login under the identity header; each answer's status and body.
RELAY_PROBE = r'''
# verify_deploy's relay probe (V14).
import json, os, urllib.error, urllib.request
CONFIG = json.loads(__CONFIG__)
header = os.environ.get("NEXUS_IDENTITY_HEADER") or CONFIG["header"]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
attempts = []
for network, address in CONFIG["addresses"]:
    seen = {"network": network, "address": address}
    req = urllib.request.Request(f"http://{address}:{CONFIG['port']}/whoami", headers={header: CONFIG["login"]})
    try:
        with opener.open(req, timeout=10) as r:
            seen.update(status=r.status, body=r.read(400).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        seen.update(status=e.code, body=e.read(400).decode("utf-8", "replace"))
    except Exception as e:
        seen.update(status=None, error=f"{type(e).__name__}: {e}")
    attempts.append(seen)
print(json.dumps({"attempts": attempts}))
'''

#: The sessions each process holds, as the superuser sees them: client
#: backends by database and role, the reading session itself left out.
SESSIONS_SQL = ("SELECT datname, usename, count(*) FROM pg_stat_activity WHERE backend_type = 'client backend' "
                "AND datname IS NOT NULL AND pid <> pg_backend_pid() GROUP BY datname, usename ORDER BY datname, usename")
#: The server's words when a role may not CONNECT to a database.
CONNECT_REFUSED = "permission denied for database"


class Console:
    """The console through Caddy, with the token."""

    def __init__(self, base_url: str, token: str | None, insecure: bool):
        self.base, self.token = base_url.rstrip("/"), token
        self.ctx = ssl.create_default_context()
        if insecure:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE

    def fetch(self, method: str, path: str, body: dict | None = None, *, auth: bool = True,
              timeout: float = 60, headers: dict | None = None) -> tuple[int, bytes]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        sent = {"Content-Type": "application/json", **(headers or {})}
        if auth and self.token:
            sent.update(auth_header(self.token))              # U.3: never `Bearer ` with nothing after it
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method, headers=sent)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def json(self, method: str, path: str, body: dict | None = None, *, auth: bool = True,
             headers: dict | None = None) -> tuple[int, dict]:
        status, raw = self.fetch(method, path, body, auth=auth, headers=headers)
        try:
            return status, json.loads(raw or b"null")
        except ValueError:
            return status, {"_raw": raw[:300].decode("utf-8", "replace")}


def say(check: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'} {check}: {detail}", flush=True)
    return ok


def sse_done(raw: bytes) -> dict | None:
    """The `done` frame of a /chat stream, or None."""
    events = [l[len("event: "):] for l in raw.decode("utf-8", "replace").splitlines() if l.startswith("event: ")]
    frames = [l.split("data: ", 1)[1] for l in raw.decode("utf-8", "replace").splitlines() if l.startswith("data: ")]
    if "done" not in events or len(frames) < len(events):
        return None
    try:
        return json.loads(frames[events.index("done")])
    except ValueError:
        return None


def remote_prefix(args) -> list[str]:
    """The SSH command the docker calls go through (`--remote`), split as a
    shell would; [] when the stack runs on this machine."""
    remote = getattr(args, "remote", None)
    return shlex.split(remote) if remote else []


def docker_argv(args, *cmd: str) -> list[str]:
    """One docker invocation, here or on the deploy host. Remote: the command
    runs in the compose project's directory over SSH (`cd <dir> && docker
    ...`, each word quoted for the host's shell; stdin passes through, which
    the probes' text needs), so V6 to V9 run from deploy.yml's runner
    are the checks the smoke runs beside the stack, not a probe of their own."""
    local = ["docker", *cmd]
    prefix = remote_prefix(args)
    if not prefix:
        return local
    return [*prefix, f"cd {shlex.quote(args.remote_dir)} && {shlex.join(local)}"]


def docker(args, *cmd: str, input_text: str | None = None, timeout: float = 600) -> subprocess.CompletedProcess:
    """One docker invocation, here or on the deploy host (docker_argv), its
    output captured. Every docker call the verifier makes is this one."""
    return subprocess.run(docker_argv(args, *cmd), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", input=input_text, timeout=timeout, cwd=str(ROOT))


def compose_files(args) -> list[str]:
    """The compose files, the main one first: under --identity-sim the
    stand-in's override is laid over it (W4 part 3)."""
    return list(getattr(args, "compose_files", None) or [args.compose_file])


def compose(args, *rest: str, profile: str | None = None, input_text: str | None = None,
            timeout: float = 600) -> subprocess.CompletedProcess:
    cmd = ["compose"]
    for f in compose_files(args):
        cmd += ["-f", f]
    cmd += ["-p", args.project]
    for p in ([profile] if profile else []) + list(getattr(args, "extra_profiles", None) or []):
        cmd += ["--profile", p]
    cmd += list(rest)
    return docker(args, *cmd, input_text=input_text, timeout=timeout)


def host_manifest_state(host_folder: Path) -> bool | None:
    """Whether the backup folder's MANIFEST.json is on the host, as this
    user: True, False, or None when this user may not look (the backups
    directory is the image's uid 10001's, mode 0750: cloud-init's and the
    smoke's `install -d`)."""
    try:
        return (host_folder / "MANIFEST.json").is_file()
    except PermissionError:
        return None


def manifest_on_host(args, folder: str, host_folder: Path, service: str = "backup") -> tuple[bool, str]:
    """V6's second half: the manifest where the operator expects it. Read on
    the host when this user can; through the backup service's mount when it
    may not, or when the host is another machine (`--remote`), said so (law
    5: cannot look is not nothing found). `folder` is the path the backup
    printed (inside the service's mount); `service` the backup service that
    wrote it (under hosted, the tester's own: W4 part 3)."""
    if remote_prefix(args):
        state, why = None, "the host is remote"
    else:
        state = host_manifest_state(host_folder)
        why = f"this user may not read {host_folder.parent}, the image's uid 10001's, mode 0750"
    if state is not None:
        return state, f"manifest on the host {'present' if state else 'MISSING'} ({host_folder}, read as this user)"
    proc = compose(args, "run", "--rm", "--entrypoint", "python", service, "-c",
                   "import os, sys; sys.exit(0 if os.path.isfile(sys.argv[1]) else 3)", f"{folder}/MANIFEST.json",
                   profile="backup", timeout=300)
    present = proc.returncode == 0
    return present, f"manifest on the host {'present' if present else 'MISSING'} (read through the backup service's mount: {why})"


def egress_verdict(out: dict, net_words: list[str]) -> tuple[bool, str]:
    """The V8 verdict over the probe's result and `docker network inspect`'s
    three words (internal, gateway mode, gateway address). Refused means
    ENETUNREACH at once at EVERY public address, so any other result there,
    a timeout among them, is not a pass; the sibling must connect (else the
    probe proved a dead network, not a policy); the network must be internal
    with its gateway isolated and no gateway address."""
    public = out.get("public") or {}
    refused = {k: v for k, v in public.items() if v.get("name") == "ENETUNREACH"}
    sibling = (out.get("sibling") or {}).get("result")
    ok = (not out.get("default_route") and public and len(refused) == len(public)
          and sibling == "connected"
          and len(net_words) >= 2 and net_words[0] == "true" and net_words[1] == "isolated"
          and (len(net_words) < 3 or net_words[2] in ("", "<no", "invalid", "none")))
    detail = (f"default route: {out.get('default_route')}; public connects: "
              + ", ".join(f"{k} {v.get('result')}{'/' + str(v.get('name')) if v.get('name') else ''} in {v.get('seconds')}s"
                          for k, v in public.items())
              + f"; the sibling by name: {sibling}; network internal={net_words[0] if net_words else '?'} "
              f"gateway_mode={net_words[1] if len(net_words) > 1 else '?'} gateway={net_words[2] if len(net_words) > 2 else 'none'}")
    return bool(ok), detail


def check_egress(args, service: str, profile: str, label: str = "V8 egress") -> bool | None:
    """V8's verdict, or None under the api profile, where the check does not
    apply and is SKIPPED by name, never counted (a PASS for a check that
    did not run is law 5's false pass: the W3 verifier's F2). Under hosted
    (W4 part 3) the probe runs in the tester's pod, its sibling is the
    database, and both of the pod's networks must be isolated."""
    if profile == "api":
        print("SKIP V8 egress: not applicable: the api profile's NEXUS container has a route out by design (its model "
              "server is on the internet; declared in docker-compose.yml and docs/DEPLOY.md), so nothing is refused "
              "and nothing is counted", flush=True)
        return None
    sibling, port = EGRESS_SIBLING.get(profile, ("caddy", 80))
    probe = (EGRESS_PROBE.replace("PUBLIC", repr(PUBLIC_ADDRESSES)).replace("SIBLING_PORT", str(port))
             .replace("SIBLING", repr(sibling)))
    proc = compose(args, "exec", "-T", service, "python", "-", profile=profile, input_text=probe, timeout=120)
    if proc.returncode != 0:
        return say(label, False, f"the probe could not run in {service}: {proc.stderr.strip()[-300:]}")
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return say(label, False, f"the probe printed no result: {proc.stdout[-300:]}")
    verdicts = []
    for network in EGRESS_NETWORKS.get(profile, ("internal",)):
        net = docker(args, "network", "inspect", f"{args.project}_{network}", "-f",
                     "{{.Internal}} {{index .Options \"com.docker.network.bridge.gateway_mode_ipv4\"}} "
                     "{{(index .IPAM.Config 0).Gateway}}", timeout=60)
        verdicts.append((network, *egress_verdict(out, net.stdout.split())))
    ok = all(v for _n, v, _d in verdicts)
    detail = verdicts[0][2] + "".join(f"; {n}: {d.rsplit('; network ', 1)[-1]}" for n, _v, d in verdicts[1:])
    return say(label, ok, f"{service}: {detail}" if profile == "hosted" else detail)


def _listed(names: list, limit: int = 5) -> str:
    return ", ".join(names[:limit]) + (f" and {len(names) - limit} more" if len(names) > limit else "")


def store_role_verdict(out: dict) -> tuple[bool, str]:
    """V9's verdict over the role probe's result: for each role the session
    acts as (the session user and the current user), every attribute in
    ROLE_ATTRIBUTES off, where a value that is not false, an absent one
    included, is held (what the probe did not read is never taken for off);
    no predefined role but OWNER_ROLE, and that one only where the role owns
    the database; and PUBLIC without CONNECT on the database. The detail
    names the role and each attribute, held ones first."""
    if not isinstance(out, dict) or out.get("error") or not out.get("roles"):
        why = out.get("error") if isinstance(out, dict) else None
        return False, f"the probe read no role: {why or 'nothing came back'}"
    held: list[str] = []
    shown: list[str] = []
    for row in out["roles"]:
        name = row.get("role")
        acts = " and ".join(w for w, k in (("session", "is_session_user"), ("current", "is_current_user"))
                            if row.get(k) is True) or "neither"
        attributes = {a: row.get(a) for a in ROLE_ATTRIBUTES}
        for a, value in attributes.items():
            if value is not False:
                held.append(f"{name} holds {a.upper()}" if value is True else f"{name}'s {a} was not read")
        members = list(row.get("predefined") or [])
        implicit = [m for m in members if m == OWNER_ROLE and row.get("owns_database") is True]
        granted = [m for m in members if m not in implicit]
        if granted:
            held.append(f"{name} is a member of {_listed(granted)}")
        shown.append(f"role {name} (the {acts} user): "
                     + ", ".join(f"{a} {'off' if v is False else ('ON' if v is True else 'unread')}"
                                 for a, v in attributes.items())
                     + f"; predefined roles: {_listed(granted) if granted else 'none granted'}"
                     + (f" ({OWNER_ROLE} implicit: it owns the database)" if implicit else ""))
    public = out.get("public_connect")
    if public is not False:
        held.append("PUBLIC may connect to the database" if public is True else "PUBLIC's CONNECT was not read")
    where = f"database {out.get('database')}; PUBLIC {'may not connect' if public is False else 'MAY CONNECT'}"
    detail = "; ".join(shown) + f"; {where}"
    return (not held), (f"{'; '.join(held)}. {detail}" if held else detail)


def check_store_role(args, service: str, profile: str) -> bool | None:
    """V9's verdict, or None under a SQLite profile, where the store is a
    file with no role and the check is SKIPPED by name, never counted. The
    probe's text goes to `docker compose exec` on stdin; the URL and its
    password stay in the container's environment."""
    if PROFILE_STORE.get(profile) != "postgres":
        print(f"SKIP V9 the store's role: not applicable: the {profile} profile's store is SQLite, a file with "
              f"no role, so nothing is read and nothing is counted", flush=True)
        return None
    proc = compose(args, "exec", "-T", service, "python", "-", profile=profile, input_text=ROLE_PROBE, timeout=120)
    if proc.returncode != 0:
        return say("V9 the store's role", False, f"the probe could not run in {service}: {proc.stderr.strip()[-300:]}")
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return say("V9 the store's role", False, f"the probe printed no result: {proc.stdout[-300:]}")
    ok, detail = store_role_verdict(out)
    return say("V9 the store's role", ok, f"{service}: {detail}")


# --- the hosted profile: one pod per tester (W4 part 3, run/specs/W4.md D19) ---------------

def _last_json(proc: subprocess.CompletedProcess):
    """The JSON a probe printed on its last line, or None."""
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def _natural(name: str) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def tester_key(service: str) -> str:
    """A tester's key from its NEXUS service's name: nexus-t1 -> t1."""
    return service[len(NEXUS_PREFIX):]


def expected_login(key: str) -> "str | None":
    """The login tester `key`'s NEXUS answers for, as the operator set it
    where the verifier runs (NEXUS_<KEY>_OWNER, which compose hands that
    tester's NEXUS and the stand-in), or None."""
    return (os.environ.get(f"NEXUS_{key.upper()}_OWNER") or "").strip() or None


def hosted_services(args) -> list[str]:
    """The hosted profile's NEXUS services, from the compose file: every
    service it runs under hosted that PROFILE_SERVICE's pattern matches."""
    proc = compose(args, "config", "--services", profile="hosted", timeout=120)
    if proc.returncode != 0:
        return []
    names = {line.strip() for line in proc.stdout.splitlines()
             if fnmatch.fnmatchcase(line.strip(), PROFILE_SERVICE["hosted"])}
    return sorted(names, key=_natural)


def hosted_health_verdict(out) -> tuple[bool, str]:
    """V1 under hosted: /healthz inside the pod answers 200, names the stub
    backend and the postgres store, and says proxy mode with an owner set
    (with none, every request would be refused), the liveness key there."""
    if not isinstance(out, dict) or out.get("error") or not isinstance(out.get("body"), dict):
        why = out.get("error") if isinstance(out, dict) else None
        return False, f"/healthz did not answer inside the pod: {why or out or 'nothing came back'}"
    body, status = out["body"], out.get("status")
    backend = (body.get("model_server") or {}).get("backend")
    store = (body.get("store") or {}).get("backend")
    identity = body.get("identity") or {}
    held = []
    if status != 200:
        held.append(f"HTTP {status}")
    if backend != PROFILE_BACKEND["hosted"]:
        held.append(f"backend {backend!r}, not {PROFILE_BACKEND['hosted']!r}")
    if store != PROFILE_STORE["hosted"]:
        held.append(f"store {store!r}, not {PROFILE_STORE['hosted']!r}")
    if identity.get("mode") != PROXY:
        held.append(f"identity mode {identity.get('mode')!r}, not {PROXY!r}")
    if identity.get("owner_set") is not True:
        held.append("owner not set: this process would refuse every request")
    if "ollama" not in body:
        held.append("the liveness key is missing")
    detail = f"HTTP {status}; backend {backend}; store {store}; identity {identity}"
    return (not held), (f"{'; '.join(held)}. {detail}" if held else detail)


def whoami_verdict(status, body, login) -> tuple[bool, str]:
    """V11: GET /whoami through the tester's port is 200 in proxy mode and
    names the expected login and the mechanism `proxy`. With no expected
    login there is nothing to compare, and no pass."""
    body = body if isinstance(body, dict) else {}
    detail = (f"HTTP {status}; auth_mode {body.get('auth_mode')!r}; login {body.get('login')!r} (expected {login!r}); "
              f"identity_mechanism {body.get('identity_mechanism')!r}")
    if not login:
        return False, f"no expected login to compare with. {detail}"
    ok = (status == 200 and body.get("auth_mode") == PROXY and body.get("login") == login
          and body.get("identity_mechanism") == PROXY)
    return ok, detail


def record_identity_verdict(record, login) -> tuple[bool, str]:
    """V4's attribution: the turn's record is sealed and carries the
    tester's login as accountable_identity and identity_mechanism `proxy`
    (W4 parts 2 and 1b: a record without the field, or with the login
    missing, is named)."""
    record = record if isinstance(record, dict) else {}
    held = []
    if not record.get("record_hash"):
        held.append("no record_hash")
    who = record.get("accountable_identity")
    if who != login:
        held.append(f"accountable_identity {who!r}, not the tester's login {login!r}")
    if "identity_mechanism" not in record:
        held.append("identity_mechanism is absent from the record")
    elif record.get("identity_mechanism") != PROXY:
        held.append(f"identity_mechanism {record.get('identity_mechanism')!r}, not {PROXY!r}")
    detail = (f"record {str(record.get('lineage_id'))[:12]}: record_hash {str(record.get('record_hash'))[:16]}; "
              f"accountable_identity {who!r}; identity_mechanism {record.get('identity_mechanism', '(absent)')!r}")
    return (not held), (f"{'; '.join(held)}. {detail}" if held else detail)


def distinct_identities_verdict(found: dict, expected: dict) -> tuple[bool, str]:
    """V12: one accountable_identity per tester, every one set and the
    tester's own, so as many distinct values as testers."""
    values = [found.get(k) for k in expected]
    ok = (set(found) == set(expected) and all(values) and len(set(values)) == len(expected)
          and all(found.get(k) == expected[k] for k in expected))
    shown = ", ".join(f"{k} {found.get(k)!r}" for k in expected)
    return ok, f"{len({v for v in values if v})} distinct accountable_identity values over {len(expected)} testers: {shown}"


def isolation_verdict(key: str, login, records: list, own_id, others: dict) -> tuple[bool, str]:
    """V13: tester `key`'s own store (read through its own port) holds its
    own turn and no record of another tester's: no other tester's turn
    (`others`: key -> (login, lineage id)) and no record carrying another
    tester's login."""
    records = [r for r in records if isinstance(r, dict)]
    foreign = []
    for r in records:
        rid, who = r.get("lineage_id"), r.get("accountable_identity")
        for other, (their_login, their_id) in sorted(others.items()):
            theirs_by_turn = their_id is not None and rid == their_id
            theirs_by_login = who is not None and who == their_login
            if theirs_by_turn or theirs_by_login:
                foreign.append(f"{str(rid)[:12]} ({other}'s {'turn' if theirs_by_turn else 'login'})")
    own = own_id is not None and any(r.get("lineage_id") == own_id for r in records)
    detail = (f"{len(records)} records in {key}'s store; its own turn {'present' if own else 'ABSENT'}; "
              + (f"another tester's: {', '.join(foreign)}" if foreign else "none of another tester's"))
    return (own and not foreign), detail


def refused_verdict(status, body, want_status: int, want_detail: str) -> tuple[bool, str]:
    """V2: a refusal that is NEXUS's own (its status and its detail), never
    the relay's or another's."""
    detail = body.get("detail") if isinstance(body, dict) else None
    seen = detail if detail is not None else (body.get("_raw") if isinstance(body, dict) else body)
    return (status == want_status and detail == want_detail), f"HTTP {status} {seen!r} (expected {want_status} {want_detail!r})"


def relay_refusal_verdict(out) -> tuple[bool, str]:
    """V14: every address of the relay the probe tried answered with the
    relay's own refusal (403, RELAY_REFUSAL). Anything else there is named:
    a pass-through to NEXUS (its own 403 is NOT the relay's), and an address
    the probe could not reach, which proves nothing."""
    attempts = out.get("attempts") if isinstance(out, dict) else None
    if not isinstance(out, dict) or out.get("error") or not attempts:
        why = out.get("error") if isinstance(out, dict) else None
        return False, f"the probe reached no address of the relay: {why or 'nothing came back'}"
    held, shown = [], []
    for a in attempts:
        where = f"{a.get('network')} {a.get('address')}"
        if a.get("status") == 403 and a.get("body") == RELAY_REFUSAL:
            shown.append(f"{where}: 403, the relay's own refusal")
        elif a.get("status") is None:
            held.append(f"{where}: {a.get('error')}: nothing was reached, so nothing is proved")
        else:
            held.append(f"{where}: HTTP {a.get('status')} {str(a.get('body'))[:80]!r}, not the relay's refusal")
    return (not held), "; ".join(held + shown)


def cross_database_verdict(out) -> tuple[bool, str]:
    """V10: another tester's database refused this tester's role for the
    CONNECT privilege. A connection is named, and so is a refusal for any
    other reason (a wrong password proves nothing about CONNECT)."""
    if not isinstance(out, dict) or out.get("error") or "refused" not in out:
        why = out.get("error") if isinstance(out, dict) else None
        return False, f"the probe could not try the database: {why or 'nothing came back'}"
    if out["refused"] is not True:
        return False, f"{out.get('database')} let this tester's role in, connected as {out.get('connected_as')}"
    message = str(out.get("message") or "")
    if CONNECT_REFUSED not in message:
        return False, f"{out.get('database')} refused, though not for the CONNECT privilege: {message[:200]!r}"
    said = message[message.find("FATAL:"):] if "FATAL:" in message else message
    return True, f"{out.get('database')} refused it for the CONNECT privilege: {said.splitlines()[0][:110]!r}"


def sessions_line(rows: list, max_connections) -> str:
    """The sessions line: measured, never a verdict, never counted."""
    total = sum(int(n) for _d, _u, n in rows)
    shown = "; ".join(f"{d} as {u}: {n}" for d, u, n in rows) or "none"
    return (f"MEASURED sessions in pg_stat_activity (client backends, read as the superuser; measured, not judged): "
            f"{shown}; {total} in all, of max_connections {max_connections}")


def check_hosted_health(args, service: str) -> bool:
    proc = compose(args, "exec", "-T", service, "python", "-", profile="hosted", input_text=HEALTH_PROBE, timeout=120)
    ok, detail = hosted_health_verdict(_last_json(proc) or {"error": proc.stderr.strip()[-300:] or "no output"})
    return say(f"V1 health {tester_key(service)}", ok, f"{service}, inside its pod: {detail}")


def check_other_database(args, service: str, other_key: str) -> bool:
    key = tester_key(service)
    database = TESTER_DATABASE_PREFIX + other_key
    probe = OTHER_DATABASE_PROBE.replace("__CONFIG__", repr(json.dumps({"database": database})))
    proc = compose(args, "exec", "-T", service, "python", "-", profile="hosted", input_text=probe, timeout=120)
    ok, detail = cross_database_verdict(_last_json(proc) or {"error": proc.stderr.strip()[-300:] or "no output"})
    return say(f"V10 another tester's database {key}", ok, f"{service}, as {TESTER_DATABASE_PREFIX + key}: {detail}")


def relay_addresses(args, pod: str) -> list:
    """[(network, address)] of a tester's relay, read from docker."""
    proc = compose(args, "ps", "-q", pod, profile="hosted", timeout=60)
    ids = proc.stdout.split() if proc.returncode == 0 else []
    if not ids:
        return []
    inspected = docker(args, "inspect", "-f", "{{json .NetworkSettings.Networks}}", ids[0], timeout=60)
    try:
        networks = json.loads(inspected.stdout.strip() or "{}")
    except ValueError:
        return []
    return sorted([name, (spec or {}).get("IPAddress")] for name, spec in networks.items()
                  if (spec or {}).get("IPAddress"))


def check_relay(args, key: str, prober: str, in_pod: bool) -> bool:
    """V14: tester `key`'s relay refuses a request from `prober` (another
    tester's NEXUS container, which shares that tester's pod; or, where
    there is one tester, the tester's own backup container on internal)."""
    pod = POD_PREFIX + key
    label = f"V14 the relay refuses another peer {key}"
    addresses = relay_addresses(args, pod)
    if not addresses:
        return say(label, False, f"{pod}'s addresses could not be read from docker")
    probe = RELAY_PROBE.replace("__CONFIG__", repr(json.dumps({
        "addresses": addresses, "login": expected_login(key) or PLANTED_LOGIN, "port": RELAY_PORT,
        "header": DEFAULT_HEADER})))
    if in_pod:
        proc = compose(args, "exec", "-T", prober, "python", "-", profile="hosted", input_text=probe, timeout=120)
    else:
        proc = compose(args, "run", "--rm", "-T", "--entrypoint", "python", prober, "-", profile="backup",
                       input_text=probe, timeout=300)
    ok, detail = relay_refusal_verdict(_last_json(proc) or {"error": proc.stderr.strip()[-300:] or "no output"})
    return say(label, ok, f"{pod}, asked by {prober} (not the identity proxy) with the owner's login: {detail}")


def check_tester_backup(args, key: str, backup_dir: Path) -> list[bool]:
    """V6 and V7 for one tester: its own backup service, its own folder."""
    service = BACKUP_PREFIX + key
    proc = compose(args, "run", "--rm", service, profile="backup")
    folder = next((l.split("backup: ", 1)[1].strip() for l in proc.stdout.splitlines() if l.startswith("backup: ")), None)
    stamp = Path(folder).name if folder else None
    if folder and stamp:
        on_host, where = manifest_on_host(args, folder, backup_dir / key / stamp, service=service)
    else:
        on_host, where = False, "no folder named by the backup's output"
    tail = (proc.stdout + proc.stderr).strip()
    results = [say(f"V6 a backup taken {key}", proc.returncode == 0 and on_host,
                   f"compose run {service} exit {proc.returncode}; folder {folder}; {where} "
                   f"({tail.splitlines()[-1][:200] if tail else 'no output'})")]
    if folder:
        proc = compose(args, "run", "--rm", "--entrypoint", "python", service, "scripts/restore_verify.py", folder,
                       profile="backup")
        lines = [l for l in proc.stdout.splitlines() if l.startswith(("PASS", "FAIL", "restore verification"))]
        results.append(say(f"V7 the restore verified {key}", proc.returncode == 0,
                           f"restore_verify through {service} exit {proc.returncode}; " + "; ".join(lines[-6:])))
    else:
        results.append(say(f"V7 the restore verified {key}", False, "no backup folder to restore"))
    return results


def measure_sessions(args) -> None:
    """The sessions each tester's process holds, read in the database's own
    container as its superuser and printed: measured, not judged."""
    proc = compose(args, "exec", "-T", DATABASE_SERVICE, "psql", "-U", SUPERUSER, "-d", "postgres", "-At", "-F", "|",
                   "-c", SESSIONS_SQL, "-c", "SHOW max_connections", profile="hosted", timeout=120)
    if proc.returncode != 0:
        print(f"MEASURED sessions: not read: psql in {DATABASE_SERVICE} exit {proc.returncode}: "
              f"{proc.stderr.strip()[-200:]}", flush=True)
        return
    rows = [tuple(l.split("|")) for l in proc.stdout.splitlines() if l.count("|") == 2]
    limit = next((l.strip() for l in proc.stdout.splitlines() if l.strip().isdigit()), "unread")
    print(sessions_line(rows, limit), flush=True)


def stand_in_checks(args, keys: list[str]) -> list[bool]:
    """The requests through the identity proxy, against the stand-in's
    ports (--identity-sim): V11, V3, V4 and V5 per tester, then V12, V13
    and V2. A request to a tester's port carries another login of its own
    under the identity header, which the stand-in replaces; the no-identity
    request carries the owner's, which the stand-in removes."""
    header = (os.environ.get("NEXUS_IDENTITY_HEADER") or "").strip() or DEFAULT_HEADER
    logins = {k: expected_login(k) for k in keys}
    consoles: dict = {}

    def console_at(port) -> "Console | None":
        if port is None:
            return None
        if port not in consoles:
            proc = compose(args, "port", SIM_SERVICE, str(port), profile="hosted", timeout=60)
            address = proc.stdout.split()[-1] if proc.returncode == 0 and proc.stdout.split() else ""
            consoles[port] = Console(f"http://{address}", None, False) if address else None
        return consoles[port]

    results, turns, records = [], {}, {}
    for key in keys:
        console = console_at(SIM_TESTER_PORTS.get(key))
        if console is None:
            for check in ("V11 whoami", "V3 a turn", "V4 the record", "V5 the chain verified"):
                results.append(say(f"{check} {key}", False, f"the stand-in publishes no port for {key}"))
            continue
        unset = "" if logins[key] else f" (NEXUS_{key.upper()}_OWNER is unset where the verifier runs)"
        planted = next((logins[o] for o in keys if o != key and logins[o]), PLANTED_LOGIN)
        status, who = console.json("GET", "/whoami", headers={header: planted})
        ok, detail = whoami_verdict(status, who, logins[key])
        results.append(say(f"V11 whoami {key}", ok, f"{console.base}, the request carrying {planted!r} of its own: "
                                                    f"{detail}{unset}"))
        status, raw = console.fetch("POST", "/chat", {"message": QUESTION}, timeout=600)
        done = sse_done(raw) if status == 200 else None
        turns[key] = done.get("lineage_id") if done else None
        results.append(say(f"V3 a turn {key}", bool(turns[key]),
                           f"HTTP {status}; done frame {'present' if done else 'MISSING'}; lineage {turns[key]}; "
                           f"learning {done.get('learning', {}).get('display') if done else '?'}"))
        if turns[key]:
            s_record, record = console.json("GET", f"/lineage/{turns[key]}")
            s_verify, verify = console.json("GET", "/lineage/verify")
            records[key] = record if isinstance(record, dict) else {}
            ok, detail = record_identity_verdict(record, logins[key])
            verdict = verify.get("verdict") if isinstance(verify, dict) else None
            results.append(say(f"V4 the record {key}", s_record == 200 and ok and s_verify == 200 and verdict == "intact",
                               f"{detail}; /lineage/verify {s_verify}: {verdict}{unset}"))
        else:
            results.append(say(f"V4 the record {key}", False, "no record to read: V3 produced no lineage id"))
        s_cycle, cycle = console.json("POST", "/kairos/cycle")
        s_chain, chain = console.json("GET", "/chain/status")
        verification = chain.get("verification") if isinstance(chain, dict) else None
        valid = verification.get("valid") if isinstance(verification, dict) else None
        results.append(say(f"V5 the chain verified {key}", s_cycle == 200 and s_chain == 200 and valid is True,
                           f"kairos cycle {s_cycle}; /chain/status {s_chain}: verification.valid={valid}, "
                           f"blocks={chain.get('n_blocks') if isinstance(chain, dict) else '?'}"))
    found = {k: (records.get(k) or {}).get("accountable_identity") for k in keys}
    ok, detail = distinct_identities_verdict(found, logins)
    results.append(say("V12 distinct identities", ok, detail))
    for key in keys:
        console = console_at(SIM_TESTER_PORTS.get(key))
        if console is None:
            results.append(say(f"V13 no other tester's record {key}", False, f"the stand-in publishes no port for {key}"))
            continue
        status, recent = console.json("GET", "/lineage/recent?limit=200")
        others = {o: (logins[o], turns.get(o)) for o in keys if o != key}
        ok, detail = isolation_verdict(key, logins[key], (recent or {}).get("records") or [], turns.get(key), others)
        results.append(say(f"V13 no other tester's record {key}", status == 200 and ok, f"HTTP {status}; {detail}"))
    port, presented, reached = SIM_MISMATCH
    mismatch, missing = console_at(port), console_at(SIM_NO_IDENTITY[0])
    if mismatch is None or missing is None:
        results.append(say("V2 the identity refused", False, "the stand-in does not publish the mismatch and "
                                                             "no-identity ports"))
        return results
    s_m, b_m = mismatch.json("GET", "/whoami")
    ok_m, d_m = refused_verdict(s_m, b_m, 403, NOT_OWNER)
    s_n, b_n = missing.json("GET", "/whoami", headers={header: logins.get(SIM_NO_IDENTITY[1]) or PLANTED_LOGIN})
    ok_n, d_n = refused_verdict(s_n, b_n, 401, NO_IDENTITY)
    results.append(say("V2 the identity refused", ok_m and ok_n,
                       f"the mismatch ({presented}'s login to {reached}'s pod): {d_m}; no identity (the client's own "
                       f"{header} removed by the stand-in): {d_n}"))
    return results


def run_hosted(args) -> int:
    """Every check of the hosted profile, per tester (the module's docstring)."""
    if args.identity_sim and remote_prefix(args):
        print("REFUSED: --identity-sim reaches the stand-in's ports on 127.0.0.1 where the stack runs; with --remote "
              "that is the deploy host's loopback, not this machine's: run the verifier on the deploy host")
        return EXIT_UNREACHABLE
    if args.identity_sim:
        args.compose_files = [args.compose_file, SIM_FILE if remote_prefix(args) else str(ROOT / SIM_FILE)]
    services = hosted_services(args)
    if not services:
        print(f"UNREACHABLE: compose runs no {PROFILE_SERVICE['hosted']} service under the hosted profile "
              f"(docker compose config --services, project {args.project})")
        return EXIT_UNREACHABLE
    keys = [tester_key(s) for s in services]
    where = f"docker over {args.remote!r} in {args.remote_dir}" if args.remote else "docker on this machine"
    identity = (f"the deploy smoke's stand-in for the identity proxy ({SIM_SERVICE}, {SIM_FILE})" if args.identity_sim
                else "the operator's identity proxy: the requests through it are skipped by name (--identity-sim runs "
                     "them against the deploy smoke's stand-in)")
    unused = [flag for flag, value in (("--base-url", args.base_url), ("--token", args.token),
                                       ("--insecure", args.insecure)) if value]
    print(f"deployment: the hosted profile, {len(services)} testers ({', '.join(services)}), project {args.project}; "
          f"{where}; identity: {identity}" + (f"; not used under hosted: {', '.join(unused)}" if unused else ""),
          flush=True)
    results: list[bool] = []
    skipped: list[str] = []
    for service in services:
        results.append(check_hosted_health(args, service))
    if args.identity_sim:
        results += stand_in_checks(args, keys)
    else:
        for check, what in THROUGH_THE_PROXY:
            print(f"SKIP {check} {what}: not run: it goes through the identity proxy, which a verifier cannot pass as "
                  f"a person; --identity-sim runs it against the deploy smoke's stand-in", flush=True)
            skipped.append(check)
    if args.skip_backup:
        print("SKIP V6 and V7: --skip-backup")
        skipped += ["V6", "V7"]
    else:
        backup_dir = Path(args.backup_dir or os.environ.get("NEXUS_BACKUP_DIR") or (ROOT / "backups")).resolve()
        for key in keys:
            results += check_tester_backup(args, key, backup_dir)
    if args.skip_egress:
        print("SKIP V8 and V14: --skip-egress (the network probes run where docker compose is, or over --remote)")
        skipped += ["V8", "V14"]
    else:
        for service in services:
            results.append(check_egress(args, service, "hosted", f"V8 egress {tester_key(service)}"))
        for i, key in enumerate(keys):
            other = services[(i + 1) % len(services)]
            results.append(check_relay(args, key, other, True) if len(services) > 1
                           else check_relay(args, key, BACKUP_PREFIX + key, False))
    if args.skip_role:
        print("SKIP V9 and V10: --skip-role (the role is read where docker compose is, or over --remote)")
        skipped += ["V9", "V10"]
    else:
        for i, service in enumerate(services):
            results.append(check_store_role(args, service, "hosted"))
            if len(services) > 1:
                results.append(check_other_database(args, service, keys[(i + 1) % len(keys)]))
            else:
                print(f"SKIP V10 another tester's database {keys[i]}: not run: there is one tester, so no other "
                      f"database to try", flush=True)
                skipped.append("V10")
    measure_sessions(args)
    passed = sum(results)
    print(f"verify_deploy: {passed} of {len(results)} checks passed (hosted profile, {len(services)} testers)"
          + (f"; skipped, not counted: {', '.join(skipped)}" if skipped else ""))
    return EXIT_OK if passed == len(results) else EXIT_FAIL


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=sorted(PROFILE_SERVICE), required=True, help="the compose profile that is up")
    ap.add_argument("--base-url", default=None,
                    help="the console through Caddy (default https://<NEXUS_DOMAIN or localhost>[:NEXUS_HTTPS_PORT])")
    ap.add_argument("--token", default=None, help="the console token (default NEXUS_API_TOKEN)")
    ap.add_argument("--project", default="nexus", help="the compose project name")
    ap.add_argument("--compose-file", default=None,
                    help="the compose file (default: this repository's; with --remote, docker-compose.yml in --remote-dir)")
    ap.add_argument("--remote", default=None,
                    help="run the docker calls (V6 to V9) on the deploy host through this SSH command, e.g. "
                         "\"ssh -i key user@host\"; the console checks still go to --base-url (deploy.yml's way)")
    ap.add_argument("--remote-dir", default="/opt/nexus", help="the compose project's directory on the remote host")
    ap.add_argument("--extra-profiles", nargs="*", default=[], help="further profiles that are up (search)")
    ap.add_argument("--backup-dir", default=None, help="where the backup service writes on the host (default NEXUS_BACKUP_DIR or ./backups)")
    ap.add_argument("--skip-backup", action="store_true", help="skip V6 and V7 (no docker compose where this runs)")
    ap.add_argument("--skip-egress", action="store_true", help="skip V8 (no docker compose where this runs)")
    ap.add_argument("--skip-role", action="store_true", help="skip V9 (no docker compose where this runs)")
    ap.add_argument("--insecure", action="store_true", help="accept Caddy's certificate unverified (localhost's internal CA)")
    ap.add_argument("--identity-sim", action="store_true",
                    help="under hosted: run the requests through the identity proxy against the deploy smoke's "
                         f"stand-in ({SIM_FILE}, laid over the compose file); never on a host that serves testers")
    args = ap.parse_args(argv)
    if args.compose_file is None:
        args.compose_file = "docker-compose.yml" if args.remote else str(ROOT / "docker-compose.yml")
    if args.identity_sim and args.profile != "hosted":
        print("REFUSED: --identity-sim is the hosted profile's: the stand-in for its identity proxy")
        return EXIT_UNREACHABLE
    if args.profile == "hosted":
        return run_hosted(args)
    token = args.token or os.environ.get("NEXUS_API_TOKEN") or ""
    if not token:
        print("REFUSED: no token (--token or NEXUS_API_TOKEN); the gated checks need it")
        return EXIT_UNREACHABLE
    domain = os.environ.get("NEXUS_DOMAIN") or "localhost"
    port = os.environ.get("NEXUS_HTTPS_PORT") or "443"
    base = args.base_url or f"https://{domain}" + (f":{port}" if port != "443" else "")
    console = Console(base, token, args.insecure)
    service = PROFILE_SERVICE[args.profile]
    where = f"docker over {args.remote!r} in {args.remote_dir}" if args.remote else "docker on this machine"
    print(f"deployment: {base} (profile {args.profile}, service {service}, project {args.project}; {where})", flush=True)
    results: list[bool] = []
    skipped: list[str] = []

    try:
        status, health = console.json("GET", "/healthz", auth=False)
    except (urllib.error.URLError, OSError, ssl.SSLError) as e:
        print(f"UNREACHABLE: {base}/healthz: {type(e).__name__}: {e}")
        return EXIT_UNREACHABLE
    # The api profile's backend is the compose file's NEXUS_API_BACKEND
    # (openai by default), never the operator's LLM_BACKEND (the W3
    # verifier's F6), and it must be one of the two api backends.
    want = PROFILE_BACKEND[args.profile] or os.environ.get("NEXUS_API_BACKEND", "").strip().lower() or "openai"
    model_server = health.get("model_server") if isinstance(health, dict) else None
    reported = model_server.get("backend") if isinstance(model_server, dict) else None
    backend_ok = reported == want and (args.profile != "api" or reported in API_BACKENDS)
    store = health.get("store") if isinstance(health, dict) else None
    store_backend = store.get("backend") if isinstance(store, dict) else None
    store_ok = store_backend == PROFILE_STORE[args.profile]
    results.append(say("V1 health", status == 200 and isinstance(model_server, dict) and "ollama" in health and backend_ok
                       and store_ok,
                       f"HTTP {status}; model_server {model_server} (expected backend {want!r}); store "
                       f"{store_backend!r} (expected {PROFILE_STORE[args.profile]!r}); liveness key "
                       f"{'present' if 'ollama' in health else 'MISSING'}"))

    s1, _ = console.json("POST", "/chat", {"message": QUESTION}, auth=False)
    s2, _ = console.json("GET", "/lineage/recent?limit=1", auth=False)
    results.append(say("V2 token", s1 == 401 and s2 == 401, f"without the token: POST /chat {s1}, GET /lineage/recent {s2}"))

    status, raw = console.fetch("POST", "/chat", {"message": QUESTION}, timeout=600)
    done = sse_done(raw) if status == 200 else None
    lineage_id = done.get("lineage_id") if done else None
    results.append(say("V3 a turn", bool(lineage_id),
                       f"HTTP {status}; done frame {'present' if done else 'MISSING'}; lineage {lineage_id}; "
                       f"learning {done.get('learning', {}).get('display') if done else '?'}; "
                       f"extracted {done.get('extraction_gate', {}).get('extracted') if done else '?'}"))

    if lineage_id:
        s3, record = console.json("GET", f"/lineage/{lineage_id}")
        s4, verify = console.json("GET", "/lineage/verify")
        verdict = verify.get("verdict") if isinstance(verify, dict) else None
        results.append(say("V4 the record sealed", s3 == 200 and bool(record.get("record_hash")) and s4 == 200
                           and verdict == "intact",
                           f"record_hash {str(record.get('record_hash'))[:16]}; model_id {record.get('model_id')}; "
                           f"/lineage/verify {s4}: {verdict}"))
    else:
        results.append(say("V4 the record sealed", False, "no record to read: V3 produced no lineage id"))

    s5, cycle = console.json("POST", "/kairos/cycle")
    s6, chain = console.json("GET", "/chain/status")
    verification = chain.get("verification") if isinstance(chain, dict) else None
    valid = verification.get("valid") if isinstance(verification, dict) else None
    results.append(say("V5 the chain verified", s5 == 200 and s6 == 200 and valid is True,
                       f"kairos cycle {s5} (sealed {cycle.get('sealed_records') if isinstance(cycle, dict) else '?'}); "
                       f"/chain/status {s6}: verification.valid={valid}, "
                       f"blocks={chain.get('n_blocks') if isinstance(chain, dict) else '?'}"))

    if args.skip_backup:
        print("SKIP V6 and V7: --skip-backup")
    else:
        backup_dir = Path(args.backup_dir or os.environ.get("NEXUS_BACKUP_DIR") or (ROOT / "backups")).resolve()
        proc = compose(args, "run", "--rm", "backup", profile="backup")
        folder = next((l.split("backup: ", 1)[1].strip() for l in proc.stdout.splitlines() if l.startswith("backup: ")), None)
        stamp = Path(folder).name if folder else None
        if folder and stamp:
            on_host, where = manifest_on_host(args, folder, backup_dir / stamp)
        else:
            on_host, where = False, "no folder named by the backup's output"
        results.append(say("V6 a backup taken", proc.returncode == 0 and on_host,
                           f"compose run backup exit {proc.returncode}; folder {folder}; {where} "
                           f"({(proc.stdout + proc.stderr).strip().splitlines()[-1][:200] if (proc.stdout + proc.stderr).strip() else 'no output'})"))
        if folder:
            # The service's entrypoint is backup.sh, so the restore verification
            # names its own: `--entrypoint python`, then the script and the folder.
            proc = compose(args, "run", "--rm", "--entrypoint", "python", "backup", "scripts/restore_verify.py", folder,
                           profile="backup")
            tail = [l for l in proc.stdout.splitlines() if l.startswith(("PASS", "FAIL", "restore verification"))]
            results.append(say("V7 the restore verified", proc.returncode == 0,
                               f"restore_verify exit {proc.returncode}; " + "; ".join(tail[-6:])))
        else:
            results.append(say("V7 the restore verified", False, "no backup folder to restore"))

    if args.skip_egress:
        print("SKIP V8: --skip-egress (the egress proof runs where docker compose is, or over --remote)")
        skipped.append("V8")
    else:
        verdict = check_egress(args, service, args.profile)
        if verdict is None:
            skipped.append("V8")
        else:
            results.append(verdict)
    if args.skip_role:
        print("SKIP V9: --skip-role (the role is read where docker compose is, or over --remote)")
        skipped.append("V9")
    else:
        verdict = check_store_role(args, service, args.profile)
        if verdict is None:
            skipped.append("V9")
        else:
            results.append(verdict)
    if args.skip_backup:
        skipped.extend(["V6", "V7"])

    passed = sum(results)
    print(f"verify_deploy: {passed} of {len(results)} checks passed ({args.profile} profile at {base})"
          + (f"; skipped, not counted: {', '.join(skipped)}" if skipped else ""))
    return EXIT_OK if passed == len(results) else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
