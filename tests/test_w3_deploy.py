"""W3 part 3 — the image, compose, Caddy, cloud-init, and the health route's
provider by role (run/specs/W3.md S2, D4 and D6; S1's H3 and H4).

T1  deploy/Dockerfile: two stages; the runtime stage runs as a non-root
    user, declares the data root a volume, freezes learning and binds
    0.0.0.0 in its environment, checks health with a Python request to
    /healthz (the image has no curl), and starts `python start.py --serve
    --port 8000 --data-root /var/lib/nexus` in EXEC form as PID 1's command
    (no ENTRYPOINT wrapper, no init), so a `docker stop` reaches uvicorn and
    releases the store's lock. The proxy CA, when one is needed to build, is
    a build secret, never a layer. A planted shell-form CMD is named.
T2  docker-compose.yml, read through `docker compose config` (client-side;
    skipped by name where the docker CLI is absent): one NEXUS service per
    profile, each on the `internal` network only (the api profile's also on
    `egress`, declared: its model server is on the internet), read-only
    root with a tmpfs /tmp, the data volume, LLM_BACKEND the profile's
    (the api service's one of the two api backends, read from
    NEXUS_API_BACKEND and never from the operator's LLM_BACKEND: T2d),
    the alias `nexus`; `internal` is `internal: true` with
    `gateway_mode_ipv4: isolated` (H4); Caddy is dual-homed and the one
    service publishing ports; Ollama is dual-homed (its pull) with the GPU
    reservation; every third-party image is pinned. A planted NEXUS
    service with a route out is named.
T3  deploy/Caddyfile: the console behind `reverse_proxy nexus:8000` with the
    SSE stream unbuffered and the domain from NEXUS_DOMAIN (localhost by
    default); `caddy validate` where caddy is on PATH, else skipped by name.
T4  deploy/cloud-init.yaml: a `#cloud-config` that writes the repository's
    docker-compose.yml and Caddyfile byte for byte (so the two cannot
    drift), installs the toolkit only when a GPU is on the bus, and enables
    the daily backup timer; `cloud-init schema` where cloud-init is on PATH,
    else skipped by name.
T5  H3: /healthz names the provider by role (`model_server`: backend,
    provider, reachable) with the old `ollama` key kept and equal; and
    /model/status reports the backend as NEXUS reads it (stripped, lowered),
    not the raw variable.

W4 part 3 (run/specs/W4.md D15 as refined, D17, D18; S3's H3): the hosted
profile is one pod per tester. T2's table names the three testers in place
of `nexus-hosted`; a tester's NEXUS joins its pod's namespace, so the
network, alias and `nexus_data` rules are for the other NEXUS services and
the pod's own rules are tests/test_w4_hosted.py's H2; every NEXUS service
drops every capability; caddy runs under the three console profiles only;
every backup, each tester's too, stays on the internal network. T3
validates the relays' and the stand-in's Caddyfiles as well. T4 is H3:
cloud-init writes every file the compose file mounts and the daily
backup's script byte for byte, derived from the files, and none of the
stand-in's; T4c plants a drifted relay file, the stand-in on a host and a
unit running a script nothing embeds.

The image's build and run are local evidence (the commit message), not a
test: the suite runs with no daemon.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from tests import _proc
import sys

import pytest

from tests._tracked import REPO, tracked

DOCKERFILE = REPO / "deploy" / "Dockerfile"
COMPOSE = REPO / "docker-compose.yml"
CADDYFILE = REPO / "deploy" / "Caddyfile"
CLOUD_INIT = REPO / "deploy" / "cloud-init.yaml"
DATA_ROOT = "/var/lib/nexus"
SERVE_CMD = ["python", "start.py", "--serve", "--port", "8000", "--data-root", DATA_ROOT]
# service -> (its profile, the backends its LLM_BACKEND may name): the api
# service's is the operator's choice between the two api backends.
PROFILE_BACKEND = {"nexus-stub": ("stub", ("stub",)), "nexus-ollama": ("ollama", ("ollama",)),
                   "nexus-api": ("api", ("openai", "anthropic")),
                   # W4 part 3 (run/specs/W4.md D15 as refined): the single
                   # `nexus-hosted` is retired; the hosted profile runs one
                   # NEXUS per tester, three written out, each on the hosted
                   # store with the stub provider.
                   "nexus-t1": ("hosted", ("stub",)), "nexus-t2": ("hosted", ("stub",)),
                   "nexus-t3": ("hosted", ("stub",))}
# W4 part 3 (D17): the generic Caddy publishes the console of the profiles
# that have one; under hosted each tester's relay and the identity proxy do.
CADDY_PROFILES = ["stub", "ollama", "api"]
# The stand-in for the identity proxy: the deploy smoke's, never a host's,
# so cloud-init writes none of its files (H3).
IDENTITY_SIM = REPO / "deploy" / "compose.identity-sim.yml"
# A bind mount's source in the repository, as the compose files spell it.
DEPLOY_SOURCE = "./deploy/"
# Where cloud-init writes the compose project, and the daily backup's unit.
PROJECT_DIR = "/opt/nexus"
BACKUP_UNIT = "/etc/systemd/system/nexus-backup.service"


# --- T1: the Dockerfile -------------------------------------------------------------

def dockerfile_instructions(text: str) -> list[tuple[str, str]]:
    """(INSTRUCTION, arguments) per logical line: continuations joined,
    comments and the syntax directive dropped."""
    out: list[tuple[str, str]] = []
    logical = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not logical and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.endswith("\\"):
            logical += line[:-1] + " "
            continue
        logical += line
        head, _, rest = logical.strip().partition(" ")
        out.append((head.upper(), rest.strip()))
        logical = ""
    return out


def dockerfile_problems(text: str) -> list[str]:
    """Why this Dockerfile is not the image D1 and D6 describe; [] when it is."""
    ins = dockerfile_instructions(text)
    problems: list[str] = []
    froms = [a for i, a in ins if i == "FROM"]
    if len(froms) < 2:
        problems.append(f"not a multi-stage build: {len(froms)} FROM")
    last_from = max((n for n, (i, _a) in enumerate(ins) if i == "FROM"), default=-1)
    runtime = ins[last_from + 1:]
    kinds = {i for i, _a in runtime}
    users = [a for i, a in runtime if i == "USER"]
    # `USER user[:group]`, by name or uid: the user part decides.
    user = users[-1].split(":", 1)[0].strip() if users else ""
    if not users or user in ("root", "0"):
        problems.append(f"the runtime stage does not run as a non-root user: USER {users}")
    cmds = [a for i, a in runtime if i == "CMD"]
    try:
        cmd = json.loads(cmds[-1]) if cmds else None
    except ValueError:
        cmd = None
    if cmd != SERVE_CMD:
        problems.append(f"CMD is not the exec-form `{' '.join(SERVE_CMD)}`: {cmds[-1:] or 'none'}")
    if "ENTRYPOINT" in kinds:
        problems.append("an ENTRYPOINT wraps the command: uvicorn would not be PID 1")
    checks = [a for i, a in runtime if i == "HEALTHCHECK"]
    if not checks or "/healthz" not in checks[-1] or 'CMD ["python"' not in checks[-1]:
        problems.append(f"HEALTHCHECK is not a Python request to /healthz: {checks[-1:] or 'none'}")
    env_text = " ".join(a for i, a in runtime if i == "ENV")
    for want in ("NEXUS_LEARNING=frozen", "NEXUS_HOST=0.0.0.0", f"NEXUS_DATA_ROOT={DATA_ROOT}"):
        if want not in env_text:
            problems.append(f"the runtime environment lacks {want}")
    if not any(i == "VOLUME" and DATA_ROOT in a for i, a in runtime):
        problems.append(f"{DATA_ROOT} is not declared a VOLUME")
    if not any(i == "COPY" and "requirements.txt" in a for i, a in ins[:last_from]):
        problems.append("the build stage does not install requirements.txt")
    if any(i == "COPY" and (".crt" in a or ".pem" in a) for i, a in ins):
        problems.append("a certificate is copied into a layer; a proxy CA is a build secret")
    # W6-B17 (the operator's decision of 17 November): every secret mount is
    # readable by the build's unprivileged users. BuildKit mounts a secret
    # root's and 0400 unless told otherwise, and apt fetches as `_apt`, so a CA
    # handed to it as `Acquire::https::CaInfo` must be readable by others.
    for kind, args in ins:
        if kind != "RUN":
            continue
        for flag in re.findall(r"--mount=(\S+)", args):
            options = dict(o.split("=", 1) if "=" in o else (o, "") for o in flag.split(","))
            if options.get("type") != "secret":
                continue
            mode = options.get("mode", "0400")
            try:
                readable = bool(int(mode, 8) & 0o004)
            except ValueError:
                problems.append(f"a secret mount's mode is not octal: {flag}")
                continue
            if not readable:
                problems.append(f"a secret mount an unprivileged build user cannot read (mode {mode}): {flag}")
    return problems


def test_t1_the_dockerfile_is_the_image_d1_and_d6_describe():
    assert DOCKERFILE.is_file(), "deploy/Dockerfile is missing"
    text = DOCKERFILE.read_text(encoding="utf-8")
    problems = dockerfile_problems(text)
    print(f"T1 {len(dockerfile_instructions(text))} instructions; problems {problems}")
    assert not problems, problems
    assert text.startswith("# syntax=docker/dockerfile:1"), "the secret mount needs the BuildKit syntax directive"
    assert "--mount=type=secret,id=proxyca" in text


def test_t1d_a_secret_mount_an_unprivileged_user_cannot_read_is_named():
    """W6-B17's pin, by plants the rule's source does not spell: a second secret with no mode, a mount that is
    group-readable only, options in another order, and a mode that is not a number. Before the fix the base
    itself was named (its two mounts carried no mode)."""
    base = DOCKERFILE.read_text(encoding="utf-8") if DOCKERFILE.is_file() else ""
    assert dockerfile_problems(base) == []
    plants = {
        "a second secret with no mode": base + "\nRUN --mount=type=secret,id=registry-token,target=/run/t true\n",
        "the CA readable by its group only": base.replace(",mode=0444", ",mode=0640", 1),
        "options in another order": base + "\nRUN --mount=target=/run/k,mode=0600,id=k,type=secret true\n",
        "a mode that is no number": base.replace(",mode=0444", ",mode=rw", 1),
        "the second mount, on its own": base.replace(",mode=0444", "", 2).replace(
            "target=/run/secrets/proxyca.crt \\", "target=/run/secrets/proxyca.crt,mode=0444 \\", 1),
    }
    for label, text in plants.items():
        assert text != base, label
        got = dockerfile_problems(text)
        assert any("secret mount" in p for p in got), (label, got)


def test_t1c_a_planted_wrapper_or_shell_form_is_named():
    base = DOCKERFILE.read_text(encoding="utf-8") if DOCKERFILE.is_file() else ""
    assert dockerfile_problems(base) == []
    shell_form = base.replace('CMD ["python", "start.py", "--serve", "--port", "8000", "--data-root", "/var/lib/nexus"]',
                              "CMD python start.py --serve --port 8000 --data-root /var/lib/nexus")
    assert any("exec-form" in p for p in dockerfile_problems(shell_form)), "a shell-form CMD passed"
    wrapped = base + '\nENTRYPOINT ["/bin/sh", "-c"]\n'
    assert any("ENTRYPOINT" in p for p in dockerfile_problems(wrapped))
    as_root = base.replace("USER nexus", "USER root:root")   # the user:group form, which the rule's source does not spell
    assert any("non-root" in p for p in dockerfile_problems(as_root))
    live = base.replace("NEXUS_LEARNING=frozen", "NEXUS_LEARNING=live")
    assert any("NEXUS_LEARNING=frozen" in p for p in dockerfile_problems(live))


# --- T2: compose -----------------------------------------------------------------------

def compose_config(extra_env: dict | None = None) -> dict | None:
    """The compose file as `docker compose config` resolves it (client-side,
    no daemon), or None when the docker CLI is absent. `extra_env` is what
    the operator's environment would add."""
    if shutil.which("docker") is None:
        return None
    proc = _proc.run(["docker", "compose", "-f", str(COMPOSE), "--profile", "*", "config", "--format", "json"],
                          cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "NEXUS_API_TOKEN": "t", **(extra_env or {})}, timeout=120)
    assert proc.returncode == 0, f"docker compose config refused the file:\n{proc.stderr[-2000:]}"
    return json.loads(proc.stdout)


def compose_problems(cfg: dict) -> list[str]:
    """Why this compose is not the deployment D4 describes; [] when it is."""
    problems: list[str] = []
    services = cfg.get("services", {})
    networks = cfg.get("networks", {})
    internal = networks.get("internal", {})
    if not internal.get("internal"):
        problems.append("the `internal` network is not `internal: true`")
    if (internal.get("driver_opts") or {}).get("com.docker.network.bridge.gateway_mode_ipv4") != "isolated":
        problems.append("the `internal` network's gateway is not isolated (H4: a host service is reachable at the gateway)")
    nexus = {n: s for n, s in services.items() if n.startswith("nexus-")}
    if set(nexus) != set(PROFILE_BACKEND):
        problems.append(f"the NEXUS services are {sorted(nexus)}, not one per profile {sorted(PROFILE_BACKEND)}")
    for name, svc in nexus.items():
        profile, backends = PROFILE_BACKEND.get(name, (None, ()))
        nets = svc.get("networks") or {}
        # W4 part 3 (D15 as refined): a hosted tester's NEXUS joins its pod's
        # network namespace (`network_mode: service:hosted-<key>`) and has
        # no networks, alias or `nexus_data` of its own; the pod's networks,
        # its own volume and its loopback bind are tests/test_w4_hosted.py's
        # H2. Every other NEXUS service keeps W3's rules here.
        in_pod = str(svc.get("network_mode") or "").startswith("service:")
        if in_pod and nets:
            problems.append(f"{name} joins a pod and is on networks {sorted(nets)} too")
        allowed = {"internal", "egress"} if name == "nexus-api" else {"internal"}
        if not in_pod and (set(nets) - allowed or "internal" not in nets):
            problems.append(f"{name} is on networks {sorted(nets)}; allowed {sorted(allowed)}")
        if not in_pod and "nexus" not in ((nets.get("internal") or {}).get("aliases") or []):
            problems.append(f"{name} lacks the alias `nexus` on the internal network (Caddy's upstream)")
        # W4 part 3 (D15 as refined): every NEXUS service drops every
        # capability; a raw socket is how a container forges a peer address
        # on a bridge (the image runs as uid 10001 and needs none).
        if svc.get("cap_drop") != ["ALL"] or svc.get("cap_add"):
            problems.append(f"{name} keeps capabilities: cap_drop {svc.get('cap_drop')}, cap_add {svc.get('cap_add')}")
        if svc.get("profiles") != [profile]:
            problems.append(f"{name} has profiles {svc.get('profiles')}, not [{profile}]")
        env = svc.get("environment") or {}
        if env.get("LLM_BACKEND") not in backends:
            problems.append(f"{name} runs LLM_BACKEND={env.get('LLM_BACKEND')!r}, not the profile's "
                            f"{' or '.join(repr(b) for b in backends)}")
        if env.get("NEXUS_LEARNING") != "frozen":
            problems.append(f"{name} does not freeze learning: NEXUS_LEARNING={env.get('NEXUS_LEARNING')!r}")
        if not svc.get("read_only"):
            problems.append(f"{name} has a writable root filesystem")
        if "/tmp" not in (svc.get("tmpfs") or []):
            problems.append(f"{name} has no tmpfs at /tmp (chromadb needs a writable /tmp)")
        mounts = {(v.get("source"), v.get("target")) for v in svc.get("volumes") or []}
        if not in_pod and ("nexus_data", DATA_ROOT) not in mounts:
            problems.append(f"{name} does not mount the nexus_data volume at {DATA_ROOT}: {sorted(mounts)}")
        if svc.get("ports"):
            problems.append(f"{name} publishes ports {svc['ports']}: only Caddy publishes")
        test = ((svc.get("healthcheck") or {}).get("test") or [])
        if not (len(test) >= 3 and test[1] == "python" and "/healthz" in " ".join(test)):
            problems.append(f"{name}'s healthcheck is not a Python request to /healthz: {test}")
    caddy = services.get("caddy")
    if caddy is None:
        problems.append("no caddy service")
    else:
        if set(caddy.get("networks") or {}) != {"internal", "egress"}:
            problems.append(f"caddy is not dual-homed: {sorted(caddy.get('networks') or {})}")
        if sorted(caddy.get("profiles") or []) != sorted(CADDY_PROFILES):      # W4 part 3, D17
            problems.append(f"caddy runs under {caddy.get('profiles')}, not exactly {CADDY_PROFILES}: under hosted "
                            f"its one upstream, the alias `nexus`, is no tester's")
        published = {int(p.get("target")) for p in caddy.get("ports") or []}
        if published != {80, 443}:
            problems.append(f"caddy publishes {sorted(published)}, not 80 and 443")
    ollama = services.get("ollama")
    if ollama is None:
        problems.append("no ollama service")
    else:
        if set(ollama.get("networks") or {}) != {"internal", "egress"}:
            problems.append("ollama is not dual-homed (its first-start pull needs egress, declared)")
        devices = ((ollama.get("deploy") or {}).get("resources") or {}).get("reservations", {}).get("devices") or []
        if not any(d.get("driver") == "nvidia" for d in devices):
            problems.append("ollama reserves no NVIDIA device")
        if ollama.get("profiles") != ["ollama"]:
            problems.append(f"ollama's profiles are {ollama.get('profiles')}")
    problems += hosted_problems(services)
    nexus_image = next((s.get("image") for n, s in services.items() if n.startswith("nexus-")), None)
    for name, svc in services.items():
        if name.startswith("nexus-") or (nexus_image and svc.get("image") == nexus_image):
            continue                      # the NEXUS image itself (part 4's `backup` runs it): its tag is the operator's
        image = svc.get("image") or ""
        tag = image.rsplit(":", 1)[-1] if ":" in image else ""
        if not tag or tag == "latest" or not any(ch.isdigit() for ch in tag):
            problems.append(f"{name}'s image is not pinned: {image!r}")
    return problems


def hosted_problems(services: dict) -> list[str]:
    """W3b part 5 (D7, D11): the hosted shape. The `postgres` service is on the
    internal network only, under the hosted profile, its data on a named
    volume, its health a pg_isready naming the superuser and its database
    (W4 part 0); every hosted NEXUS, one per tester since W4 part 3, opens
    the store there (NEXUS_STORE=postgres, a URL naming the service); every
    backup, the generic one and each tester's, reaches the database on the
    internal network and nothing else."""
    problems: list[str] = []
    pg = services.get("postgres")
    if pg is None:
        return ["no postgres service (the hosted store, W3b D11)"]
    if set(pg.get("networks") or {}) != {"internal"}:
        problems.append(f"postgres is on networks {sorted(pg.get('networks') or {})}, not the internal network only")
    if pg.get("ports"):
        problems.append(f"postgres publishes ports {pg['ports']}: only Caddy publishes")
    if pg.get("profiles") != ["hosted"]:
        problems.append(f"postgres has profiles {pg.get('profiles')}, not ['hosted']")
    if not any(v.get("target") == "/var/lib/postgresql/data" and v.get("type") == "volume" for v in pg.get("volumes") or []):
        problems.append("postgres's data is not on a named volume at /var/lib/postgresql/data")
    test = " ".join((pg.get("healthcheck") or {}).get("test") or [])
    if "pg_isready" not in test or " -U " not in test:
        problems.append(f"postgres's healthcheck is not a pg_isready naming its role: {test!r}")
    # W4 part 0 (run/specs/W4.md D14): the superuser is the image's own, used
    # at initialisation only, and NEXUS's role is a tester's that the init
    # script makes. The check names the superuser and its database, the one
    # role and the one database there from the first start: a role it names
    # that does not exist (W3b's `nexus`) is logged as a failed connection at
    # every interval.
    pg_env = pg.get("environment") or {}
    words = test.split()
    named = {flag: words[words.index(flag) + 1] for flag in ("-U", "-d") if flag in words[:-1]}
    if named != {"-U": pg_env.get("POSTGRES_USER"), "-d": pg_env.get("POSTGRES_DB")}:
        problems.append(f"postgres's healthcheck does not name its superuser {pg_env.get('POSTGRES_USER')!r} and "
                        f"database {pg_env.get('POSTGRES_DB')!r}: {test!r}")
    # W4 part 3 (D15 as refined): every hosted NEXUS, one per tester, opens
    # the store on the postgres service (its own database: H2's).
    hosted = sorted(n for n, s in services.items() if n.startswith("nexus-") and "hosted" in (s.get("profiles") or []))
    if not hosted:
        problems.append("no NEXUS service runs under the hosted profile")
    for name in hosted:
        env = services[name].get("environment") or {}
        if env.get("NEXUS_STORE") != "postgres" or "@postgres:5432/" not in str(env.get("NEXUS_DATABASE_URL") or ""):
            problems.append(f"{name} does not open the postgres service's store: NEXUS_STORE={env.get('NEXUS_STORE')!r}")
    # W4 part 3 (D18): the generic backup and each tester's own.
    for name in sorted(n for n in services if n == "backup" or n.startswith("backup-")):
        backup = services[name]
        if backup.get("network_mode") or set(backup.get("networks") or {}) != {"internal"}:
            problems.append(f"the backup {name} is not on the internal network only: network_mode="
                            f"{backup.get('network_mode')!r}, networks {sorted(backup.get('networks') or {})}")
    return problems


def test_t2_the_compose_is_the_deployment_d4_describes():
    assert COMPOSE.is_file(), "docker-compose.yml is missing"
    cfg = compose_config()
    if cfg is None:
        pytest.skip("the docker CLI is not on PATH: `docker compose config` is how the file is read")
    problems = compose_problems(cfg)
    print(f"T2 services {sorted(cfg.get('services', {}))}; problems {problems}")
    assert not problems, problems


def test_t2c_a_planted_route_out_is_named():
    cfg = compose_config()
    if cfg is None:
        pytest.skip("the docker CLI is not on PATH")
    assert compose_problems(cfg) == []
    planted = json.loads(json.dumps(cfg))
    planted["services"]["nexus-stub"]["networks"]["egress"] = None
    assert any("nexus-stub is on networks" in p for p in compose_problems(planted)), "a NEXUS service with a route out passed"
    planted = json.loads(json.dumps(cfg))
    del planted["networks"]["internal"]["driver_opts"]
    assert any("gateway is not isolated" in p for p in compose_problems(planted))
    planted = json.loads(json.dumps(cfg))
    planted["services"]["nexus-ollama"]["environment"]["LLM_BACKEND"] = "stub"
    assert any("nexus-ollama runs LLM_BACKEND='stub'" in p for p in compose_problems(planted))
    planted = json.loads(json.dumps(cfg))
    planted["services"]["caddy"]["image"] = "caddy:stable"   # a floating tag the rule's source does not spell
    assert any("caddy's image is not pinned" in p for p in compose_problems(planted))
    planted = json.loads(json.dumps(cfg))
    planted["services"]["nexus-api"]["environment"]["LLM_BACKEND"] = "lmstudio"
    assert any("nexus-api runs LLM_BACKEND='lmstudio'" in p for p in compose_problems(planted))
    # W3b part 5: the hosted shape's edges, under spellings hosted_problems does not write.
    planted = json.loads(json.dumps(cfg))
    planted["services"]["postgres"]["networks"]["egress"] = None              # a route out for the database
    assert any("postgres is on networks" in p for p in compose_problems(planted))
    planted = json.loads(json.dumps(cfg))
    planted["services"]["postgres"]["ports"] = [{"target": 5432, "published": "5432"}]
    assert any("postgres publishes ports" in p for p in compose_problems(planted))
    planted = json.loads(json.dumps(cfg))
    # W4 part 3: tester 1's NEXUS in place of the retired nexus-hosted.
    planted["services"]["nexus-t1"]["environment"]["NEXUS_STORE"] = "sqlite"
    assert any("nexus-t1 does not open the postgres service's store" in p for p in compose_problems(planted))
    planted = json.loads(json.dumps(cfg))
    planted["services"]["backup"]["networks"]["egress"] = None
    assert any("the backup backup is not on the internal network only" in p for p in compose_problems(planted))
    planted = json.loads(json.dumps(cfg))                 # W4 part 3 (D18): a tester's backup with a route out
    planted["services"]["backup-t2"]["networks"]["egress"] = None
    assert any("the backup backup-t2 is not on the internal network only" in p for p in compose_problems(planted))
    planted = json.loads(json.dumps(cfg))                 # W4 part 3 (D17): the generic caddy under hosted
    planted["services"]["caddy"]["profiles"] = [*CADDY_PROFILES, "hosted"]
    assert any("caddy runs under" in p for p in compose_problems(planted))
    planted = json.loads(json.dumps(cfg))                 # W4 part 3: a NEXUS service that keeps a capability
    planted["services"]["nexus-stub"]["cap_add"] = ["NET_RAW"]
    assert any("nexus-stub keeps capabilities" in p for p in compose_problems(planted))
    # W4 part 0: a health check naming a role that is not the superuser (W3b's own spelling).
    planted = json.loads(json.dumps(cfg))
    planted["services"]["postgres"]["healthcheck"]["test"] = ["CMD-SHELL", "pg_isready -U nexus -d nexus"]
    assert any("does not name its superuser" in p for p in compose_problems(planted))


def test_t2d_the_api_profiles_backend_is_not_the_operators_console_backend():
    """The W3 verifier's F6: `nexus-api` read `${LLM_BACKEND:-openai}`, and
    compose interpolates from the project's `.env`, which is NEXUS's own,
    where `.env.example` sets `LLM_BACKEND=ollama`: an Ollama-backend NEXUS
    with no Ollama, under the profile whose name says api. The api service
    reads `NEXUS_API_BACKEND` (openai by default), never `LLM_BACKEND`."""
    if shutil.which("docker") is None:
        pytest.skip("the docker CLI is not on PATH: `docker compose config` is how the file is read")
    cfg = compose_config({"LLM_BACKEND": "ollama"})
    backend = cfg["services"]["nexus-api"]["environment"].get("LLM_BACKEND")
    print(f"T2d nexus-api with LLM_BACKEND=ollama in the environment: {backend!r}")
    assert backend == "openai", "the api service's backend came from the operator's LLM_BACKEND"
    cfg = compose_config({"LLM_BACKEND": "ollama", "NEXUS_API_BACKEND": "anthropic"})
    assert cfg["services"]["nexus-api"]["environment"].get("LLM_BACKEND") == "anthropic"
    assert compose_problems(cfg) == [], compose_problems(cfg)


# --- T3: Caddy -------------------------------------------------------------------------

def caddyfiles() -> list:
    """Every Caddyfile the deployment mounts, the stand-in's among them (W4
    part 3): the generic console's, the testers' relay and the identity
    proxy's stand-in, as they sit in the repository."""
    return tracked("deploy/Caddyfile*")


def test_t3_the_caddyfile_serves_the_console_through_the_alias():
    assert CADDYFILE.is_file(), "deploy/Caddyfile is missing"
    text = CADDYFILE.read_text(encoding="utf-8")
    assert "reverse_proxy nexus:8000" in text and "flush_interval -1" in text
    assert "{$NEXUS_DOMAIN:localhost}" in text
    caddy = shutil.which("caddy")
    if caddy is None:
        pytest.skip("caddy is not on PATH: `caddy validate` is how the file is checked")
    # W4 part 3: the relay's and the stand-in's too, with the values compose hands them.
    for path in caddyfiles():
        proc = _proc.run([caddy, "validate", "--adapter", "caddyfile", "--config", str(path)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                              env={**os.environ, "NEXUS_DOMAIN": "localhost", "NEXUS_IDENTITY_PROXY_IP": "172.31.240.250",
                                   "NEXUS_IDENTITY_HEADER": "Tailscale-User-Login", "NEXUS_T1_OWNER": "t1@example.invalid",
                                   "NEXUS_T2_OWNER": "t2@example.invalid", "NEXUS_T3_OWNER": "t3@example.invalid"})
        print(f"T3 caddy validate {path.name}: {proc.returncode} {proc.stderr.strip()[-300:]}")
        assert proc.returncode == 0, proc.stderr[-2000:]


# --- T4: cloud-init --------------------------------------------------------------------

def written_file(cloud_init: str, path: str) -> str | None:
    """The content a `write_files` entry writes at `path`, read from the
    block scalar the file uses (`content: |`, the body indented six spaces)."""
    lines = cloud_init.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == f"- path: {path}")
    except StopIteration:
        return None
    body: list[str] = []
    collecting = False
    for line in lines[start + 1:]:
        if not collecting:
            if line.strip() == "content: |":
                collecting = True
            elif line.startswith("  - path:"):
                return None
            continue
        if line.startswith("      "):
            body.append(line[6:])
        elif not line.strip():
            body.append("")
        else:
            break
    return "\n".join(body).rstrip("\n") + "\n"


def deploy_mounts(cfg: dict) -> set:
    """The repository files a compose file's services bind-mount
    (`./deploy/...`), by YAML load, as repository paths."""
    out = set()
    for svc in (cfg.get("services") or {}).values():
        for v in svc.get("volumes") or []:
            source = str(v.get("source", "")) if isinstance(v, dict) else str(v).split(":", 1)[0]
            if source.startswith(DEPLOY_SOURCE):
                out.add(source[len("./"):])
    return out


def embedded_problems(cloud: str, main: dict, override: dict) -> list[str]:
    """H3 (W4 part 3, run/specs/W4.md S3): cloud-init writes the compose
    file, every file the compose file mounts from the repository, and the
    script the daily backup's unit runs, each byte for byte as the
    repository holds it, derived from the files rather than listed here; and
    it writes none of the stand-in's (the override and every file it
    mounts): a host runs no stand-in for its identity proxy."""
    problems = []
    wanted = {COMPOSE.name} | deploy_mounts(main)
    unit = written_file(cloud, BACKUP_UNIT) or ""
    starts = [line.split("=", 1)[1].split() for line in unit.splitlines() if line.startswith("ExecStart=")]
    run = [w for words in starts for w in words if w.startswith(PROJECT_DIR + "/")]
    if len(run) != 1:
        problems.append(f"the daily backup's unit runs {starts}, not one script of the compose project's")
    wanted |= {w[len(PROJECT_DIR) + 1:] for w in run}
    for rel in sorted(wanted):
        repo = REPO / rel
        if not repo.is_file():
            problems.append(f"{rel} is mounted or run, and the repository has no such file")
        elif written_file(cloud, f"{PROJECT_DIR}/{rel}") != repo.read_text(encoding="utf-8"):
            problems.append(f"cloud-init does not write {rel} byte for byte as the repository holds it")
    for rel in sorted({IDENTITY_SIM.relative_to(REPO).as_posix()} | deploy_mounts(override)):
        if written_file(cloud, f"{PROJECT_DIR}/{rel}") is not None:
            problems.append(f"cloud-init writes {rel}, the stand-in's: a host runs no stand-in for its identity proxy")
    return problems


def _compose_files() -> tuple[dict, dict]:
    import yaml
    override = yaml.safe_load(IDENTITY_SIM.read_text(encoding="utf-8")) if IDENTITY_SIM.is_file() else {}
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8")), override or {}


def test_t4_cloud_init_writes_the_repositorys_files_and_the_timer():
    assert CLOUD_INIT.is_file(), "deploy/cloud-init.yaml is missing"
    text = CLOUD_INIT.read_text(encoding="utf-8")
    assert text.startswith("#cloud-config\n"), "cloud-init refuses a file without the header"
    assert written_file(text, "/opt/nexus/docker-compose.yml") == COMPOSE.read_text(encoding="utf-8"), \
        "the compose file cloud-init writes differs from the repository's"
    assert written_file(text, "/opt/nexus/deploy/Caddyfile") == CADDYFILE.read_text(encoding="utf-8"), \
        "the Caddyfile cloud-init writes differs from the repository's"
    # W3b part 5: compose mounts the backup role's init script, so cloud-init writes it too.
    assert written_file(text, "/opt/nexus/deploy/postgres-init.sh") == \
        (REPO / "deploy" / "postgres-init.sh").read_text(encoding="utf-8"), \
        "the postgres init script cloud-init writes differs from the repository's"
    assert written_file(text, "/etc/systemd/system/nexus-backup.timer") is not None
    assert "OnCalendar=" in text and "nexus-backup.timer" in text
    assert "lspci | grep -qi nvidia" in text and "nvidia-ctk runtime configure --runtime=docker" in text
    assert text.count("FILL") >= 3, "the three values the operator fills are not marked"
    # W4 part 3 (H3): every file the compose file mounts and the daily backup's script, derived; no stand-in.
    main, override = _compose_files()
    problems = embedded_problems(text, main, override)
    print(f"T4 embedded: {sorted({COMPOSE.name} | deploy_mounts(main))}; problems {problems}")
    assert not problems, problems
    assert IDENTITY_SIM.is_file() and deploy_mounts(override), "non-vacuity: the stand-in's files are read"
    cloud_init = shutil.which("cloud-init")
    if cloud_init is None:
        pytest.skip("cloud-init is not on PATH: `cloud-init schema` is how the file is checked")
    proc = _proc.run([cloud_init, "schema", "--config-file", str(CLOUD_INIT)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    print(f"T4 cloud-init schema: {proc.returncode} {(proc.stdout + proc.stderr).strip()[-300:]}")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_t4c_a_drifted_embedded_file_is_named():
    text = CLOUD_INIT.read_text(encoding="utf-8")
    drifted = text.replace("reverse_proxy nexus:8000", "reverse_proxy nexus:8001", 1)
    assert written_file(drifted, "/opt/nexus/deploy/Caddyfile") != CADDYFILE.read_text(encoding="utf-8")
    assert written_file(text, "/opt/nexus/nowhere") is None
    # W4 part 3 (H3): a drifted copy of a file the rule's source does not
    # name (the relays' Caddyfile), the stand-in written to a host, and a
    # unit that runs a script nothing embeds, each named.
    main, override = _compose_files()
    assert embedded_problems(text, main, override) == []
    relay = sorted(deploy_mounts(main) - {CADDYFILE.relative_to(REPO).as_posix()})
    assert relay, "non-vacuity: the compose file mounts more than the console's Caddyfile"
    drifted = text.replace("reverse_proxy 127.0.0.1:8000", "reverse_proxy 127.0.0.1:8001", 1)
    assert drifted != text
    assert any("byte for byte" in p for p in embedded_problems(drifted, main, override))
    sim = IDENTITY_SIM.relative_to(REPO).as_posix()
    written = text.replace("\nruncmd:", f"\n  - path: {PROJECT_DIR}/{sim}\n    content: |\n      services: {{}}\nruncmd:", 1)
    assert any("the stand-in's" in p for p in embedded_problems(written, main, override))
    moved = re.sub(r"(ExecStart=\S*\s*\S*?)nexus-backup\.sh", r"\1nexus-backup-old.sh", text, count=1)
    assert moved != text
    assert any("nexus-backup-old.sh" in p for p in embedded_problems(moved, main, override))


# --- T5: H3 ------------------------------------------------------------------------------

def test_t5_healthz_names_the_provider_by_role_and_model_status_reads_the_backend(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.data_root import paths_under
    from backend.providers.stub import StubProvider
    from backend.server import create_app, create_services
    monkeypatch.setenv("LLM_BACKEND", " STUB ")
    services = create_services(**paths_under(tmp_path / "root"), provider=StubProvider())
    try:
        client = TestClient(create_app(services))
        health = client.get("/healthz").json()
        status = client.get("/model/status").json()
    finally:
        services.store_lock.release()
    print(f"T5 healthz.model_server {health.get('model_server')}, ollama {health.get('ollama')}; "
          f"model/status.backend {status.get('backend')!r}")
    assert health["ollama"] is True, "the old liveness key moved"
    assert health["model_server"] == {"backend": "stub", "provider": "StubProvider", "reachable": True}, \
        "/healthz does not name the provider by role (H3)"
    assert status["backend"] == "stub", "/model/status reports the raw variable, not the backend as read"
