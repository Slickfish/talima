"""W4 part 0 — W3b-F8: the hosted NEXUS no longer connects as a Postgres
superuser (run/specs/W4.md S2, D14; S3, H1; BACKLOG W3b-F8).

The defect: the `postgres` service's POSTGRES_USER was `nexus`, which the
official image creates SUPERUSER, and `nexus-hosted` connected as it, so the
console held COPY ... PROGRAM, the server-file roles and the right to signal
backends; with a database per tester it would be cross-tenant. The superuser
is now the image's own `postgres`, used at initialisation only, and
`deploy/postgres-init.sh` makes a non-superuser role and a database it owns
per tester key, CONNECT on it for the owner and the backup role alone.

H1a  STATIC, by YAML load of docker-compose.yml (no docker CLI): for every
     service whose name starts `nexus-` or `backup`, the role in any
     postgresql:// value of its environment is not the `postgres` service's
     POSTGRES_USER; no value of its environment interpolates the variable
     the `postgres` service's POSTGRES_PASSWORD interpolates; and it loads no
     env_file (which would hand it every variable in the file, that one
     among them). Seen failing on the pre-fix file, whose URL names
     POSTGRES_USER (`nexus`) and interpolates NEXUS_POSTGRES_PASSWORD.
     Companions, each on a modified copy of the loaded YAML and each named
     by the guard's own assertion: a backup service whose URL, under a key
     the guard does not spell, names the superuser; a NEXUS service handed
     the superuser's password under another key name (braced, bare, and
     nested in another variable's default); an env_file.
H1b  THE INIT SCRIPT AGAINST A REAL SERVER, skipped by name where
     NEXUS_TEST_DATABASE_URL is unset or psql is not on PATH (the SQLite
     jobs). `sh deploy/postgres-init.sh` runs with PGHOST, PGPORT,
     PGPASSWORD, POSTGRES_USER and POSTGRES_DB taken from that URL, two
     tester keys and a backup role whose names carry a unique tag, and
     passwords holding quotes, a backslash, a dollar and a space. Read back:
     each tester role's attributes are all off, it is a member of no role,
     and it logs in with its own password over TCP; each database's owner
     is its tester and PUBLIC holds no CONNECT on it, the backup role does;
     tester A is refused by tester B's database and B by A's; the product
     opens a store in A's database as A and writes a claim; the backup role
     passes scripts/_readonly.py's read-only open there and reads the
     claim; on an ordinary session it cannot write the table (42501). A
     finalizer drops the databases and the roles as the superuser and
     asserts they are gone. Law 16: on the pre-fix script the tester roles
     do not exist (a new interface; declared as that).
     Companions: a tester's password variable unset stops by name (exit 1)
     and makes no role, for that tester or any other; a key with an
     upper-case letter, and one with a hyphen, are refused by name and make
     nothing.
H1c  THE INIT SCRIPT WITHOUT A SERVER (sh only; skipped by name without
     it): a stand-in `psql` first on PATH records every invocation's argv
     and the SQL it was fed. No password reaches a command line or the SQL
     text (the pre-fix script passed the backup password as `--set=pw=`:
     law 16, on its consequence); the role names reach psql as variables,
     never in the SQL text; every invocation connects as
     `--username $POSTGRES_USER --dbname $POSTGRES_DB`; the last line names
     what was made and no password. Every stop condition is refused by name
     on one stderr line, exit 1, with psql never run.
H1d  THE FILES THAT STATE THE HOSTED SETTINGS AGREE (no server): the
     `postgres` service hands the init script NEXUS_TESTERS and each key's
     password variable; every NEXUS URL names the role and the database the
     script makes for one key (`nexus_<key>`, both) with that key's password
     variable; cloud-init's .env and .env.example name each key's password
     variable, and every backup URL they document (and the deploy smoke's)
     names the backup role and a tester's database; the smoke makes every
     password the hosted profile reads, fresh and masked. On the pre-fix
     files the `postgres` service names no tester at all. Companions: a key
     without its password variable, a URL naming another key's database,
     cloud-init's .env without the tester's line, a smoke password not
     masked.
V9   THE VERIFIER'S ROLE PROBE ON A REAL SERVER (the H1b fixture's roles):
     run as scripts/verify_deploy.py runs it inside the NEXUS container,
     with NEXUS_STORE and NEXUS_DATABASE_URL in its environment and nothing
     on its command line, the tester role's verdict passes (every attribute
     off, pg_database_owner the implicit membership of the owner, PUBLIC
     without CONNECT) and the superuser's fails by name; no password is
     printed, and the probe creates nothing in a database with no store
     (it opens read-only). Its verdict on planted rows, without a
     deployment, is tests/test_w3_backup.py's B6b. Law 16: the probe does
     not exist before (a new name; declared).

W4 part 3 (run/specs/W4.md D15 as refined before part 3, D16 to D19; S3's
H2 to H5): one pod per tester behind the identity proxy. The single
`nexus-hosted` is retired, so H1a's and H1d's plants go to tester 1's
service, and H1d reads each tester's backup URL from that tester's backup
service, where compose now builds it (no file documents it as a setting).

H2   THE POD, by YAML load of docker-compose.yml with
     deploy/compose.identity-sim.yml laid over it (the merge is the test's
     own, declared in `merged`; test_h2_the_cli_merge_agrees compares it with
     `docker compose config` where the CLI exists): every hosted NEXUS joins
     its own pod's namespace and no other's, binds loopback, has its own
     volume, URL and owner variable, is in proxy mode and drops every
     capability; every network a pod is on is internal; every relay keeps
     its admin endpoint off and trusts exactly the identity proxy's one
     address, front's one fixed address, outside its dynamic pool; nothing
     else on front has a route out; caddy does not run under hosted; the main
     file sets the identity header nowhere. Companions on modified copies:
     two testers in one pod, a shared volume, a pod on egress, a relay
     trusting front's subnet, a relay's admin endpoint on, the identity
     header set in the main file's Caddyfile and removed in a relay's, a
     relay holding a login, a NEXUS bound beyond loopback (two spellings),
     the identity proxy inside the pool, a second service in a pod, caddy
     under hosted. The stand-in agrees with the verifier's table (its ports
     on 127.0.0.1, each route's relay and login, the mismatch, the
     no-identity port, the relay's refusal text).
D18  One backup per tester: backup-<key> beside each tester, the tester's
     volume read-only, a host folder of its own; the generic backup takes no
     hosted URL. deploy/nexus-backup.sh, the daily timer's command, runs
     every tester's backup, each one even when another fails, with a
     stand-in docker on PATH.
H3   cloud-init carries every file the main compose file mounts byte for
     byte, and not the stand-in's: tests/test_w3_deploy.py's T4, extended.
H4   The verifier's hosted verdicts without a deployment: each check's
     function on planted rows, and the run itself against a fake deployment
     (docker and the stand-in answered in process): without --identity-sim
     the tester requests are skipped by name, send nothing, and are never
     counted; with it they run; a relay that lets another tester's process
     through, and a store holding another tester's record, are each named.
H5   The deploy smoke's hosted job, read from the workflow: the override in
     COMPOSE_FILE, each tester's login, each tester's backup folder, a health
     wait over every NEXUS service the profile runs, --identity-sim. The run
     itself is CI's.
"""
from __future__ import annotations

import argparse
import inspect
import ipaddress
import json
import os
import re
import secrets
import shutil
import subprocess
from tests import _proc
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import pytest
import yaml

from tests._tracked import REPO
from tests.conftest import POSTGRES_TEST_URL as PG_URL  # noqa: E402

COMPOSE = REPO / "docker-compose.yml"
INIT = REPO / "deploy" / "postgres-init.sh"
CLOUD_INIT = REPO / "deploy" / "cloud-init.yaml"
ENV_EXAMPLE = REPO / ".env.example"
SMOKE = REPO / ".github" / "workflows" / "deploy-smoke.yml"
NO_PG = "no Postgres named: NEXUS_TEST_DATABASE_URL is unset (the Postgres job sets it; windows and macos run SQLite)"
NO_PSQL = "psql is not on PATH: the init script is psql's"
NO_SH = "no sh on this platform: the init script is a POSIX shell script"

#: What deploy/postgres-init.sh names a tester's role and database: the key
#: behind this prefix. H1b and H1c show the script uses it.
TESTER_PREFIX = "nexus_"
#: The backup role's name when NEXUS_BACKUP_ROLE is unset.
BACKUP_ROLE_DEFAULT = "nexus_backup"
#: The attributes a tester role must not hold.
ATTRIBUTES = ("rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls")


# --- compose, read by YAML load ------------------------------------------------------

_ESCAPED_DOLLAR = "\x00"
_INTERPOLATION = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<op>:?[-?+])(?P<arg>[^{}]*))?\}"
                            r"|\$(?P<bare>[A-Za-z_][A-Za-z0-9_]*)")
_REFERENCE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
_URL = re.compile(r"postgres(?:ql)?://[^\s\"']*")


def interpolated(value) -> set[str]:
    """The variables a compose value interpolates (`${A}`, `$A`, `${A:-…}`,
    and one nested in another's default); `$$` is compose's literal `$`."""
    return set(_REFERENCE.findall(str(value).replace("$$", _ESCAPED_DOLLAR)))


def resolved(value) -> str:
    """A compose value as compose resolves it in an EMPTY environment: every
    variable unset, so `${A:-d}` and `${A-d}` give `d` and the rest give
    nothing; `$$` is a literal `$`."""
    text = str(value).replace("$$", _ESCAPED_DOLLAR)

    def one(m) -> str:
        defaulted = (m.group("op") or "").endswith("-")       # `:-` and `-`: the default when unset
        return (m.group("arg") or "") if defaulted else ""
    while True:                                               # a default may itself interpolate
        out = _INTERPOLATION.sub(one, text)
        if out == text:
            return out.replace(_ESCAPED_DOLLAR, "$")
        text = out


def environment(svc: dict) -> dict:
    """A service's environment as a mapping (compose takes a list of
    `KEY=value` too)."""
    env = svc.get("environment") or {}
    if isinstance(env, list):
        return dict(item.split("=", 1) if "=" in item else (item, "") for item in map(str, env))
    return {str(k): ("" if v is None else v) for k, v in env.items()}


def urls_in(value) -> list[str]:
    """The postgres URLs a value spells, as compose would resolve them."""
    return _URL.findall(resolved(value))


def url_role(url: str) -> str:
    return unquote(urlsplit(url).username or "")


def url_database(url: str) -> str:
    return unquote(urlsplit(url).path.lstrip("/"))


def superuser_problems(cfg: dict) -> list[str]:
    """H1a: why a NEXUS or backup service could connect as the database's
    superuser or holds its password; [] when none can."""
    services = cfg.get("services") or {}
    pg = environment(services.get("postgres") or {})
    superuser = resolved(pg.get("POSTGRES_USER", "")) or "postgres"      # the image's own when unset
    password = pg.get("POSTGRES_PASSWORD", "")
    secret_vars = interpolated(password)
    literal = resolved(password) if not secret_vars else ""
    problems = []
    for name, svc in sorted(services.items()):
        if not (name.startswith("nexus-") or name.startswith("backup")):
            continue
        if svc.get("env_file"):
            problems.append(f"{name} loads an env_file ({svc['env_file']}), which hands it every variable in the "
                            f"file, the superuser's password among them where it is there")
        for key, value in sorted(environment(svc).items()):
            for url in urls_in(value):
                if url_role(url) == superuser:
                    problems.append(f"{name}'s {key} connects as {superuser!r}, the postgres service's POSTGRES_USER "
                                    f"(a superuser)")
            held = sorted(interpolated(value) & secret_vars)
            if held:
                problems.append(f"{name}'s {key} interpolates {', '.join(held)}, the superuser's password")
            if literal and literal in resolved(value):
                problems.append(f"{name}'s {key} holds the superuser's password as written")
    return problems


def hold(problems: list[str]) -> None:
    """The guards' one assertion, shared with their companions: a companion
    shows this assertion failing on its plant, never a flag."""
    assert not problems, "; ".join(problems)


def refused(problems: list[str]) -> str:
    with pytest.raises(AssertionError) as e:
        hold(problems)
    return str(e.value)


def compose_file() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def planted_copy(cfg: dict) -> dict:
    return json.loads(json.dumps(cfg))


def test_h1a_no_nexus_or_backup_service_connects_as_the_superuser_or_holds_its_password():
    cfg = compose_file()
    services = cfg.get("services") or {}
    checked = sorted(n for n in services if n.startswith("nexus-") or n.startswith("backup"))
    pg = environment(services.get("postgres") or {})
    print(f"\nH1a services {checked}; postgres POSTGRES_USER {pg.get('POSTGRES_USER')!r}, password from "
          f"{sorted(interpolated(pg.get('POSTGRES_PASSWORD', '')))}; problems {superuser_problems(cfg)}")
    # W4 part 3 (D15 refined): the single `nexus-hosted` is retired; the hosted
    # services are one per tester, `nexus-t<n>`, each with its `backup-t<n>`.
    assert any(n.startswith("nexus-t") for n in checked) and any(n.startswith("backup-") for n in checked) \
        and "backup" in checked, "non-vacuity: the testers' services and the backups are read"
    assert any(urls_in(v) for n in checked for v in environment(services[n]).values()), \
        "non-vacuity: some NEXUS service's environment spells a postgres URL"
    hold(superuser_problems(cfg))


def test_h1a_companions_the_superuser_in_any_spelling_is_named():
    cfg = compose_file()
    hold(superuser_problems(cfg))                     # the file as it is passes (and so the plants alone are named)
    pg = environment(cfg["services"]["postgres"])
    su = resolved(pg["POSTGRES_USER"])
    var = sorted(interpolated(pg["POSTGRES_PASSWORD"]))[0]

    planted = planted_copy(cfg)                       # a URL naming the superuser, under a key the guard does not spell
    planted["services"]["backup"]["environment"]["REPLICA_DSN"] = f"postgresql://{su}:x@postgres:5432/{su}"
    said = refused(superuser_problems(planted))
    assert "backup's REPLICA_DSN connects as" in said and repr(su) in said, said

    # W4 part 3: the plants go to tester 1's service, `nexus-hosted` being retired (D15 refined).
    tester = sorted(n for n in cfg["services"] if n.startswith("nexus-t"))[0]
    for spelling in (f"${{{var}}}", f"${var}", f"${{PLANTED_UNSET:-${{{var}}}}}"):   # braced, bare, nested
        planted = planted_copy(cfg)
        planted["services"][tester]["environment"]["PGPASSWORD"] = spelling
        said = refused(superuser_problems(planted))
        assert f"{tester}'s PGPASSWORD interpolates {var}" in said, (spelling, said)

    planted = planted_copy(cfg)
    planted["services"][tester]["env_file"] = [".env"]
    assert f"{tester} loads an env_file" in refused(superuser_problems(planted))

    rule = inspect.getsource(superuser_problems)
    for plant in ("REPLICA_DSN", "PGPASSWORD", "PLANTED_UNSET", tester, "backup's"):
        assert plant not in rule, f"the rule's source names the plant {plant!r}"


# --- the init script, as a process ------------------------------------------------------

def _run_init(extra: dict, cwd: Path, path_first: "Path | None" = None) -> subprocess.CompletedProcess:
    """`sh deploy/postgres-init.sh` in this environment without any libpq,
    NEXUS or image setting of the caller's (each would change what the
    script reads), plus the test's."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "NEXUS_", "POSTGRES_"))}
    env.update(extra)
    if path_first is not None:
        env["PATH"] = f"{path_first}{os.pathsep}{os.environ.get('PATH', '')}"
    return _proc.run([shutil.which("sh") or "sh", str(INIT)], cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=300)


def _password_variable(key: str) -> str:
    return f"NEXUS_{key.upper()}_PASSWORD"


# --- H1c: without a server ---------------------------------------------------------------

STAND_IN = """#!/bin/sh
{
  echo '--- argv'
  printf '%s\\n' "$@"
  echo '--- sql'
  cat
  echo '--- end'
} >> "$PSQL_RECORD"
"""


def _stand_in(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "psql").write_text(STAND_IN, encoding="utf-8", newline="\n")
    (bin_dir / "psql").chmod(0o755)
    return bin_dir, tmp_path / "psql_record.txt"


def _invocations(record: Path) -> list[tuple[list[str], str]]:
    """(argv, sql) per psql invocation the stand-in recorded."""
    if not record.is_file():
        return []
    out = []
    for block in record.read_text(encoding="utf-8").split("--- argv\n")[1:]:
        argv_text, _, rest = block.partition("--- sql\n")
        sql, _, _ = rest.partition("--- end\n")
        out.append((argv_text.splitlines(), sql))
    return out


def _no_server_settings(tag: str) -> dict:
    keys = [f"{tag}a", f"{tag}b"]
    settings = {"POSTGRES_USER": "postgres", "POSTGRES_DB": "postgres", "NEXUS_TESTERS": " ".join(keys),
                "NEXUS_BACKUP_ROLE": f"{BACKUP_ROLE_DEFAULT}_{tag}",
                "NEXUS_BACKUP_PASSWORD": f"Bk'{tag} \\$x",
                _password_variable(keys[0]): f"Ta'{tag}\"q $y",
                _password_variable(keys[1]): f"Tb{tag}{secrets.token_hex(6)}"}
    return settings


def test_h1c_no_password_reaches_psqls_command_line_and_the_names_go_as_variables(tmp_path):
    if shutil.which("sh") is None:
        pytest.skip(NO_SH)
    tag = "c" + secrets.token_hex(4)
    settings = _no_server_settings(tag)
    bin_dir, record = _stand_in(tmp_path)
    proc = _run_init({**settings, "PSQL_RECORD": str(record)}, tmp_path, path_first=bin_dir)
    calls = _invocations(record)
    passwords = {k: v for k, v in settings.items() if k.endswith("_PASSWORD")}
    keys = settings["NEXUS_TESTERS"].split()
    roles = [TESTER_PREFIX + k for k in keys] + [settings["NEXUS_BACKUP_ROLE"]]
    print(f"\nH1c exit {proc.returncode}; {len(calls)} psql invocations; argv "
          f"{[[a for a in argv if not any(p in a for p in passwords.values())] for argv, _ in calls]}; "
          f"stdout {proc.stdout.strip().splitlines()[-1:]}; stderr {proc.stderr.strip()[-300:]!r}")
    assert calls, "non-vacuity: the script ran psql"
    on_argv = sorted(k for k, pw in passwords.items() for argv, _ in calls if any(pw in a for a in argv))
    assert not on_argv, f"a password is on psql's command line: {on_argv}"
    in_sql = sorted(k for k, pw in passwords.items() for _, sql in calls if pw in sql)
    assert not in_sql, f"a password is in the SQL text psql was fed: {in_sql}"
    assert proc.returncode == 0, proc.stdout + proc.stderr
    named_in_sql = sorted(r for r in roles for _, sql in calls if re.search(rf"\b{re.escape(r)}\b", sql))
    assert not named_in_sql, f"a role's name is spelled in the SQL text rather than passed as a variable: {named_in_sql}"
    for role in roles:
        assert any(any(a.startswith("--set=") and a.endswith(f"={role}") for a in argv) for argv, _ in calls), \
            f"{role} does not reach psql as a variable"
    for argv, _ in calls:
        joined = " ".join(argv)
        assert "--username postgres" in joined and "--dbname postgres" in joined, argv
    last = proc.stdout.strip().splitlines()[-1]
    assert last.startswith("postgres-init:") and all(r in last for r in roles), last
    out = proc.stdout + proc.stderr
    assert not [k for k, pw in passwords.items() if pw in out], "a password is printed"
    # It parses, and where shellcheck is on PATH (CI's ubuntu runner) it finds nothing, as B7 holds backup.sh.
    assert _proc.run([shutil.which("sh"), "-n", str(INIT)], capture_output=True, timeout=600).returncode == 0
    checker = shutil.which("shellcheck")
    if checker:
        lint = _proc.run([checker, str(INIT)], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
        assert lint.returncode == 0, f"shellcheck exit {lint.returncode}:\n{lint.stdout}{lint.stderr}"


#: (id, what the run's settings change, the text the refusal must name). A
#: value of None removes the setting. `{a}` is the first key, `{tag}` the tag.
REFUSALS = [
    ("backup password unset", {"NEXUS_BACKUP_PASSWORD": None}, "NEXUS_BACKUP_PASSWORD"),
    ("backup password empty", {"NEXUS_BACKUP_PASSWORD": ""}, "NEXUS_BACKUP_PASSWORD"),
    ("testers unset", {"NEXUS_TESTERS": None}, "NEXUS_TESTERS"),
    ("testers blank", {"NEXUS_TESTERS": "  \t "}, "NEXUS_TESTERS"),
    ("key upper case", {"NEXUS_TESTERS": "{a} Tq{tag}"}, "'Tq{tag}'"),
    ("key hyphen", {"NEXUS_TESTERS": "{a} q-{tag}"}, "'q-{tag}'"),
    ("key a glob", {"NEXUS_TESTERS": "*"}, "'*'"),
    ("key too long", {"NEXUS_TESTERS": "{a} " + "k" * 58}, "'" + "k" * 58 + "'"),
    ("key repeated", {"NEXUS_TESTERS": "{a} {a}"}, "nexus_{a}"),
    ("backup role is a tester's", {"NEXUS_BACKUP_ROLE": "nexus_{a}"}, "nexus_{a}"),
    ("backup role is the superuser", {"NEXUS_BACKUP_ROLE": "postgres"}, "NEXUS_BACKUP_ROLE"),
    ("backup role not a name", {"NEXUS_BACKUP_ROLE": "Bad-{tag}"}, "NEXUS_BACKUP_ROLE"),
    ("tester password unset", {"PASSWORD_OF_A": None}, "NEXUS_{A}_PASSWORD"),
    ("tester password empty", {"PASSWORD_OF_A": ""}, "NEXUS_{A}_PASSWORD"),
]


@pytest.mark.parametrize("case", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_h1c_every_stop_condition_is_refused_by_name_with_psql_never_run(case, tmp_path):
    if shutil.which("sh") is None:
        pytest.skip(NO_SH)
    _id, changes, token = case
    tag = "r" + secrets.token_hex(4)
    settings = _no_server_settings(tag)
    a = settings["NEXUS_TESTERS"].split()[0]
    fill = {"a": a, "A": a.upper(), "tag": tag}
    for key, value in changes.items():
        key = _password_variable(a) if key == "PASSWORD_OF_A" else key
        if value is None:
            settings.pop(key, None)
        else:
            settings[key] = value.format(**fill)
    bin_dir, record = _stand_in(tmp_path)
    proc = _run_init({**settings, "PSQL_RECORD": str(record)}, tmp_path, path_first=bin_dir)
    lines = proc.stderr.strip().splitlines()
    print(f"\nH1c {_id}: exit {proc.returncode}; stderr {lines}; psql runs {len(_invocations(record))}")
    assert proc.returncode == 1, f"not refused: exit {proc.returncode}\n{proc.stdout}{proc.stderr}"
    assert _invocations(record) == [], "psql ran: something could have been made before the refusal"
    assert len(lines) == 1 and lines[0].startswith("postgres-init:") and token.format(**fill) in lines[0], lines
    assert not [v for k, v in settings.items() if k.endswith("_PASSWORD") and v and v in proc.stderr + proc.stdout]


# --- H1b: against a real server ------------------------------------------------------------

def _server() -> dict:
    """The named server's connection parameters, as libpq reads its URL."""
    from psycopg.conninfo import conninfo_to_dict
    p = conninfo_to_dict(PG_URL)
    return {"host": p.get("host") or "localhost", "port": str(p.get("port") or "5432"),
            "user": p.get("user") or "postgres", "password": p.get("password") or "",
            "dbname": p.get("dbname") or p.get("user") or "postgres"}


def _superuser():
    import psycopg
    return psycopg.connect(PG_URL, autocommit=True)


def _login(role: str, password: str, database: str):
    import psycopg
    s = _server()
    return psycopg.connect(host=s["host"], port=s["port"], user=role, password=password, dbname=database,
                           autocommit=True, connect_timeout=10)


def _url(role: str, password: str, database: str) -> str:
    s = _server()
    host = f"[{s['host']}]" if ":" in s["host"] else s["host"]
    return f"postgresql://{quote(role, safe='')}:{quote(password, safe='')}@{host}:{s['port']}/{quote(database, safe='')}"


def _present(roles, databases) -> tuple[list[str], list[str]]:
    with _superuser() as c:
        r = [x[0] for x in c.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s) ORDER BY 1",
                                     (sorted(roles),))]
        d = [x[0] for x in c.execute("SELECT datname FROM pg_database WHERE datname = ANY(%s) ORDER BY 1",
                                     (sorted(databases),))]
    return r, d


def _drop(roles, databases, existed: set) -> None:
    """Drop what a run made, as the superuser: every database first (each is
    owned by a tester role), then what each role holds in the database the
    script connected to, then the roles. A name that existed before the run
    is not this run's and is left alone. The pre-W4 script made
    `nexus_backup` whatever NEXUS_BACKUP_ROLE said, and granted it in
    POSTGRES_DB: that is dropped here too when the run made it."""
    from psycopg import sql
    roles = sorted(set(roles) - existed)
    databases = sorted(set(databases) - existed)
    with _superuser() as c:
        for d in databases:
            c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(d)))
        present = [x[0] for x in c.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", (roles,))]
        for r in present:
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s", (r,))
            c.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(r)))
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(r)))


def _server_names(tag: str) -> dict:
    keys = [f"{tag}a", f"{tag}b"]
    return {"keys": keys, "roles": [TESTER_PREFIX + k for k in keys], "backup": f"{BACKUP_ROLE_DEFAULT}_{tag}",
            "passwords": {keys[0]: f"A'q\\b {tag} $x\"y%z", keys[1]: secrets.token_hex(12)},
            "backup_password": f"Bk'{secrets.token_hex(6)} \\$"}


def _server_settings(names: dict) -> dict:
    s = _server()
    out = {"PGHOST": s["host"], "PGPORT": s["port"], "PGPASSWORD": s["password"], "POSTGRES_USER": s["user"],
           "POSTGRES_DB": s["dbname"], "NEXUS_BACKUP_ROLE": names["backup"],
           "NEXUS_BACKUP_PASSWORD": names["backup_password"], "NEXUS_TESTERS": " ".join(names["keys"])}
    for key, pw in names["passwords"].items():
        out[_password_variable(key)] = pw
    return out


def _needs_a_server():
    if not PG_URL:
        pytest.skip(NO_PG)
    if shutil.which("psql") is None:
        pytest.skip(NO_PSQL)
    if shutil.which("sh") is None:
        pytest.skip(NO_SH)


def _every_name(names: dict) -> tuple[list[str], list[str]]:
    """The roles and databases a run could make: the testers', the backup
    role, and the pre-W4 script's fixed `nexus_backup`."""
    return names["roles"] + [names["backup"], BACKUP_ROLE_DEFAULT], list(names["roles"])


@pytest.fixture(scope="module")
def initialised(tmp_path_factory):
    """The init script run once against the named server; every name it
    could make is dropped afterwards and asserted gone."""
    _needs_a_server()
    names = _server_names("w4p0" + secrets.token_hex(4))
    roles, databases = _every_name(names)
    existed = {x for found in _present(roles, databases) for x in found}
    assert not existed - {BACKUP_ROLE_DEFAULT}, f"the unique names already exist: {sorted(existed)}"
    proc = _run_init(_server_settings(names), tmp_path_factory.mktemp("init"))
    try:
        yield {**names, "proc": proc}
    finally:
        _drop(roles, databases, existed)
        left = _present(set(roles) - existed, set(databases) - existed)
        assert left == ([], []), f"left on the server: {left}"


def test_h1b_the_init_script_makes_non_superuser_owners_and_a_read_only_backup_role(initialised):
    import psycopg
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    from backend.store import StoreTarget
    from scripts._readonly import open_read_only_postgres
    n = initialised
    proc = n["proc"]
    a, b = n["roles"]
    pw = {TESTER_PREFIX + k: v for k, v in n["passwords"].items()}
    print(f"\nH1b exit {proc.returncode}; last line {proc.stdout.strip().splitlines()[-1:]}; "
          f"stderr {proc.stderr.strip()[-400:]!r}")
    secrets_given = [*pw.values(), n["backup_password"]]
    assert not [s for s in secrets_given if s in proc.stdout + proc.stderr], "a password is printed"
    with _superuser() as c:
        rows = {r[0]: r[1:] for r in c.execute(
            f"SELECT rolname, rolcanlogin, {', '.join(ATTRIBUTES)}, "
            f"(SELECT count(*) FROM pg_auth_members m WHERE m.member = r.oid) "
            f"FROM pg_roles r WHERE rolname = ANY(%s)", ([a, b, n["backup"]],))}
        dbs = {r[0]: r[1:] for r in c.execute(
            "SELECT datname, pg_get_userbyid(datdba), has_database_privilege('public', datname, 'CONNECT'), "
            "has_database_privilege(%s, datname, 'CONNECT') FROM pg_database WHERE datname = ANY(%s)",
            (n["backup"], [a, b]))}
    print(f"H1b roles {rows}; databases {dbs}")
    assert set(rows) == {a, b, n["backup"]}, f"the roles made: {sorted(rows)} (a new interface before W4: declared)"
    for role in (a, b):
        canlogin, *attributes, memberships = rows[role]
        assert canlogin is True and attributes == [False] * len(ATTRIBUTES) and memberships == 0, (role, rows[role])
    bk_login, bk_super, bk_createdb, bk_createrole, _repl, _bypass, bk_members = rows[n["backup"]]
    assert (bk_login, bk_super, bk_createdb, bk_createrole, bk_members) == (True, False, True, False, 0), rows
    for role in (a, b):
        owner, public_connect, backup_connect = dbs[role]
        assert owner == role and public_connect is False and backup_connect is True, (role, dbs[role])
        with _login(role, pw[role], role) as c:                       # its own password, over TCP
            assert c.execute("SELECT current_user, current_database()").fetchone() == (role, role)
        with psycopg.connect(_url_for_superuser(role), autocommit=True) as c:
            acl = [str(x[0]) for x in c.execute(
                "SELECT unnest(defaclacl)::text FROM pg_default_acl d JOIN pg_namespace s ON s.oid = d.defaclnamespace "
                "WHERE d.defaclrole = %s::regrole AND s.nspname = 'public' AND d.defaclobjtype = 'r'", (role,))]
        assert any(x.startswith(f"{n['backup']}=r/") for x in acl), (role, acl)
    for role, other in ((a, b), (b, a)):
        with pytest.raises(psycopg.OperationalError) as refusal:
            _login(role, pw[role], other)
        assert "permission denied for database" in str(refusal.value), str(refusal.value)
    with _superuser() as c:                            # the database the script connected to holds nothing of it
        there = [str(x[0]) for x in c.execute(
            "SELECT unnest(defaclacl)::text FROM pg_default_acl") if str(x[0]).split("=", 1)[0] == n["backup"]]
    assert there == [], f"the backup role is granted in {_server()['dbname']}: {there}"

    store = TripleStore(StoreTarget.postgres(_url(a, pw[a], a)))
    try:
        store.add(subject="callisto", relation="orbits", object="jupiter", weight=0.6, tier=1,
                  granularity=Granularity.TOPIC, source="asserted")
    finally:
        store.close()
    db = open_read_only_postgres(_url(n["backup"], n["backup_password"], a))
    try:
        claim = db.execute("SELECT subject, relation, object FROM triples WHERE subject = ?", ("callisto",)).fetchall()
    finally:
        db.close()
    assert [tuple(r) for r in claim] == [("callisto", "orbits", "jupiter")], claim
    with _login(n["backup"], n["backup_password"], a) as c:            # an ordinary session: no read-only setting
        for statement in ("UPDATE triples SET weight = weight", "DELETE FROM triples",
                          "INSERT INTO triples (subject) VALUES ('planted')"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(statement)
    last = proc.stdout.strip().splitlines()[-1]
    assert last.startswith("postgres-init:") and all(x in last for x in (a, b, n["backup"])), last


def _url_for_superuser(database: str) -> str:
    """The named server's own URL, on another database."""
    parts = urlsplit(PG_URL)
    return parts._replace(path="/" + database).geturl()


@pytest.mark.parametrize("plant", ["a tester's password unset", "a key in upper case", "a key with a hyphen"])
def test_h1b_companions_a_refused_run_makes_nothing(plant, tmp_path):
    _needs_a_server()
    tag = "w4p0" + secrets.token_hex(4)
    names = _server_names(tag)
    settings = _server_settings(names)
    if plant == "a tester's password unset":
        settings.pop(_password_variable(names["keys"][1]))
        token = _password_variable(names["keys"][1])
    else:
        bad = f"Up{tag}" if plant == "a key in upper case" else f"hy-{tag}"
        settings["NEXUS_TESTERS"] = f"{names['keys'][0]} {bad}"
        token = f"'{bad}'"
    roles, databases = _every_name(names)
    roles = roles + [TESTER_PREFIX + w for w in settings["NEXUS_TESTERS"].split()]
    databases = databases + [TESTER_PREFIX + w for w in settings["NEXUS_TESTERS"].split()]
    existed = set(_present(roles, databases)[0]) | set(_present(roles, databases)[1])
    try:
        proc = _run_init(settings, tmp_path)
        made = _present(set(roles) - existed, set(databases) - existed)
        print(f"\nH1b companion ({plant}): exit {proc.returncode}; stderr {proc.stderr.strip().splitlines()}; made {made}")
        assert proc.returncode == 1 and token in proc.stderr and "postgres-init:" in proc.stderr, \
            f"not refused by name: exit {proc.returncode}\n{proc.stdout}{proc.stderr}"
        assert made == ([], []), f"a refused run made {made}"
    finally:
        _drop(roles, databases, existed)
    assert _present(set(roles) - existed, set(databases) - existed) == ([], [])


# --- H1d: the files that state the hosted settings agree --------------------------------------

def keys_handed_over(cfg: dict) -> list[str]:
    pg = environment((cfg.get("services") or {}).get("postgres") or {})
    return resolved(pg.get("NEXUS_TESTERS", "")).split()


def _setting_lines(text: str) -> dict:
    """`NAME=value` lines of an env file, commented or not."""
    return {m.group(1): m.group(2) for m in re.finditer(r"(?m)^\s*#?\s*([A-Z][A-Z0-9_]*)=(.*)$", text)}


def _backup_urls(text: str) -> list[str]:
    return re.findall(r"NEXUS_BACKUP_DATABASE_URL=([^\s\"']+)", text)


def smoke_password_step(wf: dict) -> str:
    smoke = (wf.get("jobs") or {}).get("smoke") or {}
    texts = [str(s.get("run") or "") for s in smoke.get("steps") or []
             if "hosted" in str(s.get("if") or "") and "GITHUB_ENV" in str(s.get("run") or "")]
    return "\n".join(texts)


def hosted_settings_problems(cfg: dict, cloud_env: str, env_example: str, smoke: dict) -> list[str]:
    """H1d: where the files that state the hosted settings disagree with one
    another or with the init script's names; [] when they agree."""
    services = cfg.get("services") or {}
    pg = environment(services.get("postgres") or {})
    keys = keys_handed_over(cfg)
    problems = []
    if not keys:
        problems.append("the postgres service hands the init script no tester (NEXUS_TESTERS resolves to nothing), "
                        "so no role NEXUS could connect as is made")
    for key in keys:
        if not re.fullmatch(r"[a-z0-9_]+", key):
            problems.append(f"the tester key {key!r} is one the init script refuses")
        var = _password_variable(key)
        if var not in interpolated(pg.get(var, "")):
            problems.append(f"the postgres service does not hand the init script {var}, tester {key}'s password")
    roles = {TESTER_PREFIX + k: k for k in keys}
    for name, svc in sorted(services.items()):
        if not name.startswith("nexus-"):
            continue
        for setting, value in sorted(environment(svc).items()):
            for url in urls_in(value):
                role, database = url_role(url), url_database(url)
                key = roles.get(role)
                if key is None:
                    problems.append(f"{name}'s {setting} connects as {role!r}, which the init script makes for no key "
                                    f"compose hands it ({sorted(roles)})")
                    continue
                if database != role:
                    problems.append(f"{name}'s {setting} opens the database {database!r}, not {role!r}, the one "
                                    f"{role} owns")
                if _password_variable(key) not in interpolated(value):
                    problems.append(f"{name}'s {setting} does not take {_password_variable(key)}, {role}'s password")
    hosted_passwords = sorted({v for svc in services.values() if "hosted" in (svc.get("profiles") or [])
                               for value in environment(svc).values() for v in interpolated(value)
                               if v.endswith("_PASSWORD")})
    step = smoke_password_step(smoke)
    for label, text in (("cloud-init's /opt/nexus/.env", cloud_env), (".env.example", env_example)):
        lines = _setting_lines(text)
        for key in keys:
            if _password_variable(key) not in lines:
                problems.append(f"{label} does not name {_password_variable(key)}")
    # W4 part 3 (D18): one backup per tester. compose builds each tester's
    # backup URL in that tester's own backup service from
    # NEXUS_BACKUP_PASSWORD, so no file documents the URL as a setting any
    # more (part 0 required one); a file that still does must still name the
    # backup role and a tester's database.
    for label, text in (("cloud-init's /opt/nexus/.env", cloud_env), (".env.example", env_example),
                        ("the deploy smoke", step)):
        for url in _backup_urls(text):
            if url_role(url) != BACKUP_ROLE_DEFAULT or url_database(url) not in roles:
                problems.append(f"{label}'s backup URL connects as {url_role(url)!r} to {url_database(url)!r}, not "
                                f"as {BACKUP_ROLE_DEFAULT} to a tester's database ({sorted(roles)})")
    backups = {n: s for n, s in services.items() if n.startswith("backup-")}
    if keys and not backups:
        problems.append("no tester has a backup service of its own (backup-<key>): one backup holds one store")
    for name, svc in sorted(backups.items()):
        key = name[len("backup-"):]
        values = list(environment(svc).values())
        found = [(url_role(u), url_database(u)) for v in values for u in urls_in(v)]
        if found != [(BACKUP_ROLE_DEFAULT, TESTER_PREFIX + key)]:
            problems.append(f"{name}'s backup URL is {found}, not the backup role {BACKUP_ROLE_DEFAULT} on "
                            f"{TESTER_PREFIX + key}, its own tester's database")
        if not any("NEXUS_BACKUP_PASSWORD" in interpolated(v) for v in values):
            problems.append(f"{name}'s backup URL does not take NEXUS_BACKUP_PASSWORD, the backup role's password")
        if key not in keys:
            problems.append(f"{name} backs up tester {key!r}, for whom the postgres service makes no role")
    for var in hosted_passwords:
        written = re.search(rf'echo "{var}=\$(\w+)"', step)
        if written is None:
            problems.append(f"the deploy smoke does not make {var}, which the hosted profile reads")
            continue
        shell = written.group(1)
        if not re.search(rf"(?m)^\s*{shell}=\$\(openssl rand ", step):
            problems.append(f"the deploy smoke's {var} is not made fresh (openssl rand) per run")
        if f'echo "::add-mask::${shell}"' not in step:
            problems.append(f"the deploy smoke does not mask {var}")
    return problems


def _hosted_inputs() -> tuple[dict, str, str, dict]:
    from tests.test_w3_deploy import written_file
    cloud = written_file(CLOUD_INIT.read_text(encoding="utf-8"), "/opt/nexus/.env") or ""
    return (compose_file(), cloud, ENV_EXAMPLE.read_text(encoding="utf-8"),
            yaml.safe_load(SMOKE.read_text(encoding="utf-8")))


def test_h1d_the_files_that_state_the_hosted_settings_agree():
    cfg, cloud, example, smoke = _hosted_inputs()
    print(f"\nH1d tester keys {keys_handed_over(cfg)}; problems {hosted_settings_problems(cfg, cloud, example, smoke)}")
    assert cloud, "non-vacuity: cloud-init writes /opt/nexus/.env"
    hold(hosted_settings_problems(cfg, cloud, example, smoke))


def test_h1d_companions_each_disagreement_is_named():
    cfg, cloud, example, smoke = _hosted_inputs()
    hold(hosted_settings_problems(cfg, cloud, example, smoke))
    key = keys_handed_over(cfg)[0]

    planted = planted_copy(cfg)                       # a second key with no password handed over
    planted["services"]["postgres"]["environment"]["NEXUS_TESTERS"] = f"${{NEXUS_TESTERS:-{key} zz9}}"
    assert "does not hand the init script NEXUS_ZZ9_PASSWORD" in refused(
        hosted_settings_problems(planted, cloud, example, smoke))

    planted = planted_copy(cfg)                       # a URL on another key's database
    tester = sorted(n for n in planted["services"] if n.startswith("nexus-t"))[0]    # W4 part 3: nexus-hosted is retired
    env = planted["services"][tester]["environment"]
    env["NEXUS_DATABASE_URL"] = re.sub(r"/[^/]+$", "/nexus_zz9", env["NEXUS_DATABASE_URL"])
    assert "opens the database 'nexus_zz9'" in refused(hosted_settings_problems(planted, cloud, example, smoke))

    # W4 part 3 (D18): a tester's backup service on another tester's database.
    planted = planted_copy(cfg)
    names = sorted(n for n in planted["services"] if n.startswith("backup-"))
    first, second = names[0], names[1]
    env = planted["services"][second]["environment"]
    env["NEXUS_BACKUP_DATABASE_URL"] = re.sub(r"/[^/]+$", "/" + TESTER_PREFIX + first[len("backup-"):],
                                             env["NEXUS_BACKUP_DATABASE_URL"])
    said = refused(hosted_settings_problems(planted, cloud, example, smoke))
    assert f"{second}'s backup URL is" in said and "its own tester's database" in said, said

    var = _password_variable(key)
    dropped = "\n".join(line for line in cloud.splitlines() if var not in line)
    assert f"cloud-init's /opt/nexus/.env does not name {var}" in refused(
        hosted_settings_problems(cfg, dropped, example, smoke))

    planted = json.loads(json.dumps(smoke))                    # the tester's password made, and not masked
    for step in planted["jobs"]["smoke"]["steps"]:
        run = str(step.get("run") or "")
        holder = re.search(rf'echo "{var}=\$(\w+)"', run)
        if holder:
            step["run"] = "\n".join(line for line in run.splitlines()
                                    if line.strip() != f'echo "::add-mask::${holder.group(1)}"')
    said = refused(hosted_settings_problems(cfg, cloud, example, planted))
    assert f"the deploy smoke does not mask {var}" in said, said

    rule = inspect.getsource(hosted_settings_problems)
    for plant in ("zz9", "ZZ9", "nexus_zz9"):
        assert plant not in rule, f"the rule's source names the plant {plant!r}"


# --- V9: the verifier's role probe on a real server ------------------------------------------

def _probe(url: str, tmp_path: Path) -> tuple[subprocess.CompletedProcess, "dict | None"]:
    """scripts/verify_deploy.py's ROLE_PROBE as V9 runs it in the NEXUS
    container: `python -`, the text on stdin, the store settings in the
    environment, nothing on the command line."""
    from scripts import verify_deploy as vd
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "NEXUS_", "POSTGRES_"))}
    env.update(NEXUS_STORE="postgres", NEXUS_DATABASE_URL=url, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               PYTHONPATH=os.pathsep.join(filter(None, [str(REPO), os.environ.get("PYTHONPATH")])))
    proc = _proc.run([sys.executable, "-"], input=vd.ROLE_PROBE, cwd=REPO, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)
    try:
        return proc, json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return proc, None


def test_v9_the_role_probe_passes_a_tester_role_and_names_the_superuser(initialised, tmp_path):
    from scripts import verify_deploy as vd
    n = initialised
    a, b = n["roles"]
    pw = {TESTER_PREFIX + k: v for k, v in n["passwords"].items()}
    proc, out = _probe(_url(b, pw[b], b), tmp_path)
    print(f"\nV9 as {b}: exit {proc.returncode}; {out}; stderr {proc.stderr.strip()[-300:]!r}")
    assert out is not None, proc.stdout + proc.stderr
    ok, detail = vd.store_role_verdict(out)
    print(f"V9 verdict: {ok}: {detail}")
    assert ok, detail
    assert out["database"] == b and [r["role"] for r in out["roles"]] == [b], out
    assert pw[b] not in proc.stdout + proc.stderr, "the role's password is printed"
    import psycopg
    with psycopg.connect(_url_for_superuser(b), autocommit=True) as c:   # read-only: nothing made where no store is
        made = c.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace "
                         "WHERE s.nspname = 'public'").fetchone()[0]
    assert made == 0, f"the probe made {made} relations in {b}"

    # The superuser, as W3b's deployment connected (POSTGRES_USER, which the image makes SUPERUSER).
    proc, out = _probe(PG_URL, tmp_path)
    ok, detail = vd.store_role_verdict(out or {})
    print(f"V9 as the superuser: {ok}: {detail}")
    assert out is not None and not ok and "superuser" in detail.lower(), (detail, proc.stdout + proc.stderr)


# =============================================================================================
# W4 part 3: one pod per tester behind the identity proxy (run/specs/W4.md D15 as refined
# before part 3, D16 to D19; S3's H2 to H5)
# =============================================================================================

OVERRIDE = REPO / "deploy" / "compose.identity-sim.yml"
#: A bind mount's source inside the repository, as the compose files spell it.
DEPLOY_SOURCE = "./deploy/"
#: Where the Caddy image reads its configuration.
CADDYFILE_AT = "/etc/caddy/Caddyfile"
#: The NEXUS image's data root, and the backup service's destination.
DATA_ROOT = "/var/lib/nexus"
BACKUPS_AT = "/var/backups/nexus"
#: Where the Caddy image writes under a read-only root (its XDG_DATA_HOME and XDG_CONFIG_HOME).
CADDY_WRITES = ("/data", "/config")
FRONT, INTERNAL = "front", "internal"
POD_PREFIX, NEXUS_PREFIX, BACKUP_PREFIX = "hosted-", "nexus-", "backup-"
GATEWAY_MODE = "com.docker.network.bridge.gateway_mode_ipv4"
#: The one site every relay serves; the stand-in's routes forward to it.
RELAY_LISTEN = ":8100"
#: The Caddy directives that set or remove a request header on its way upstream.
REQUEST_HEADER_DIRECTIVES = ("header_up", "request_header")
NO_DOCKER = "the docker CLI is not on PATH: `docker compose config` is how compose itself merges the two files"
#: The deploy verifier, as a workflow step runs it.
VERIFIER = "scripts/verify_deploy.py"


def override_file() -> dict:
    """deploy/compose.identity-sim.yml by YAML load; {} where it is missing."""
    if not OVERRIDE.is_file():
        return {}
    return yaml.safe_load(OVERRIDE.read_text(encoding="utf-8")) or {}


def merged(main: dict, override: dict) -> dict:
    """The main file with the override laid over it the way compose lays an
    override that only ADDS: mappings merged key by key, any other value
    replaced. Declared: this merge is the test's own, and compose's rules
    for the sequences it merges element-wise (ports, volumes) are not
    reproduced, which an override that adds a service and changes nothing
    never exercises. `override_problems` holds that it only adds, and where
    the docker CLI exists test_h2_the_cli_merge_agrees compares the result
    with `docker compose config` over the two files."""
    out = json.loads(json.dumps(main))

    def lay(into: dict, top: dict) -> None:
        for k, v in top.items():
            if isinstance(v, dict) and isinstance(into.get(k), dict):
                lay(into[k], v)
            else:
                into[k] = json.loads(json.dumps(v))
    lay(out, override or {})
    return out


def override_problems(main: dict, override: dict) -> list[str]:
    """D16: the override is the stand-in and nothing else: it adds services
    under the hosted profile and changes nothing the main file defines."""
    problems = []
    for key in sorted(set(override) - {"services"}):
        problems.append(f"the override sets {key!r} at its top level: it may only add the stand-in's service")
    main_services = main.get("services") or {}
    added = override.get("services") or {}
    if not added:
        problems.append("the override adds no service")
    for name, svc in sorted(added.items()):
        if name in main_services:
            problems.append(f"the override changes {name}, which the main file defines")
        if (svc or {}).get("profiles") != ["hosted"]:
            problems.append(f"the override's {name} runs under {(svc or {}).get('profiles')}, not the hosted profile alone")
    return problems


def mounts(svc: dict) -> list[tuple[str, str, bool]]:
    """(source, target, read-only) per volume of a service. A short-syntax
    entry is resolved as compose resolves it with nothing set before it is
    split, because a source may interpolate a variable whose default holds
    a colon."""
    out = []
    for v in svc.get("volumes") or []:
        if isinstance(v, dict):
            out.append((resolved(v.get("source", "")), str(v.get("target", "")), bool(v.get("read_only"))))
            continue
        parts = resolved(v).split(":")
        if len(parts) >= 2:
            out.append((parts[0], parts[1], "ro" in parts[2:]))
    return out


def repo_file_of(source: str) -> "Path | None":
    """The repository file a bind mount's source names, or None (a named
    volume, or a host folder outside the repository)."""
    return REPO / source[len("./"):] if source.startswith(DEPLOY_SOURCE) else None


def _read_source(source: str) -> str:
    path = repo_file_of(source)
    return path.read_text(encoding="utf-8") if path is not None and path.is_file() else ""


def service_networks(svc: dict) -> dict:
    nets = svc.get("networks") or {}
    if isinstance(nets, list):
        return {n: {} for n in nets}
    return {n: (c or {}) for n, c in nets.items()}


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def hosted_testers(cfg: dict) -> dict:
    """The NEXUS services that run under the hosted profile."""
    return {n: s for n, s in (cfg.get("services") or {}).items()
            if n.startswith(NEXUS_PREFIX) and "hosted" in (s.get("profiles") or [])}


# --- a Caddyfile, read as Caddy reads it ------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{\$([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


@dataclass(frozen=True)
class Directive:
    """One Caddyfile line's tokens and the block that follows it (empty for
    a line with none). The global options block is a directive with no tokens."""
    tokens: tuple
    block: tuple


def caddy_lines(text: str, env: "dict | None") -> list[list[str]]:
    """The file's lines as Caddy's lexer reads them: its environment
    placeholders `{$VAR}` and `{$VAR:default}` replaced first from `env`
    (unset or empty: the default, else nothing; `env` None keeps them as
    written), then each line split on whitespace, a double-quoted string one
    token, and a `#` that begins a token a comment to the line's end."""
    if env is not None:
        text = _PLACEHOLDER.sub(lambda m: str(env.get(m.group(1)) or m.group(2) or ""), text)
    lines = []
    for raw in text.splitlines():
        tokens, current, quoted, i = [], "", False, 0
        while i < len(raw):
            ch = raw[i]
            if quoted:
                if ch == "\\" and i + 1 < len(raw):
                    current += raw[i + 1]
                    i += 2
                    continue
                if ch == '"':
                    tokens.append(current)
                    current, quoted = "", False
                else:
                    current += ch
            elif ch == '"' and not current:
                quoted = True
            elif ch.isspace():
                if current:
                    tokens.append(current)
                current = ""
            elif ch == "#" and not current:
                break
            else:
                current += ch
            i += 1
        if current:
            tokens.append(current)
        if tokens:
            lines.append(tokens)
    return lines


def caddy_tree(text: str, env: "dict | None" = None) -> tuple:
    lines = caddy_lines(text, env)

    def block(i: int) -> tuple[list, int]:
        out = []
        while i < len(lines):
            tokens = lines[i]
            if tokens == ["}"]:
                return out, i + 1
            if tokens[-1] == "{":
                inner, i = block(i + 1)
                out.append(Directive(tuple(tokens[:-1]), tuple(inner)))
            else:
                out.append(Directive(tuple(tokens), ()))
                i += 1
        return out, i
    return tuple(block(0)[0])


def walk(directives):
    for d in directives:
        yield d
        yield from walk(d.block)


def global_options(tree: tuple) -> list[Directive]:
    return [d for o in tree if not o.tokens for d in o.block]


def site_blocks(tree: tuple) -> list[Directive]:
    return [d for d in tree if d.tokens]


def relay_caddyfile_problems(text: str, env: dict, proxy: "str | None", port: str) -> list[str]:
    """A relay's Caddyfile: its admin endpoint off; one site on RELAY_LISTEN
    that answers 403 to every immediate peer but one address, the identity
    proxy's (`not remote_ip <one address>`), forwards the rest to its
    tester's process at 127.0.0.1:<port>, unbuffered, and sets or removes no
    request header, so the identity header passes as it arrives. `env` is
    the relay's environment as compose resolves it with nothing set."""
    tree = caddy_tree(text, env)
    problems = []
    if ("admin", "off") not in {d.tokens for d in global_options(tree)}:
        problems.append("its admin endpoint is on (no `admin off` among its global options): any process in the "
                        "pod could reconfigure it")
    sites = site_blocks(tree)
    if [s.tokens for s in sites] != [(RELAY_LISTEN,)]:
        problems.append(f"it serves {[' '.join(s.tokens) for s in sites]}, not one site on {RELAY_LISTEN}")
    for site in sites:
        matchers = {d.tokens[0]: d.tokens[1:] for d in site.block if d.tokens and d.tokens[0].startswith("@")}
        trusted = [matchers[d.tokens[1]][2:] for d in site.block
                   if d.tokens[:1] == ("respond",) and len(d.tokens) >= 3 and d.tokens[-1] == "403"
                   and d.tokens[1] in matchers and matchers[d.tokens[1]][:2] == ("not", "remote_ip")]
        if len(trusted) != 1:
            problems.append("it does not answer 403 to every immediate peer but the one a `not remote_ip` matcher names")
        elif len(trusted[0]) != 1:
            problems.append(f"it trusts {list(trusted[0])}: exactly one address may be trusted, the identity proxy's")
        else:
            try:
                address = str(ipaddress.ip_address(trusted[0][0]))
            except ValueError:
                problems.append(f"it trusts {trusted[0][0]!r}, a range or a name, not one address: every pod is on "
                                f"front, so a relay that trusted front's subnet would trust every other tester's process")
            else:
                if address != proxy:
                    problems.append(f"it trusts {address}, not the identity proxy's address {proxy}")
        forwards = [d for d in site.block if d.tokens[:1] == ("reverse_proxy",)]
        if [d.tokens[1:] for d in forwards] != [(f"127.0.0.1:{port}",)]:
            problems.append(f"it does not forward to its tester's process at 127.0.0.1:{port} alone: "
                            f"{[' '.join(d.tokens) for d in forwards]}")
        for d in forwards:
            if ("flush_interval", "-1") not in {x.tokens for x in d.block}:
                problems.append("it buffers the stream: its reverse_proxy has no `flush_interval -1`")
        for d in walk(site.block):
            if d.tokens[:1] and d.tokens[0] in REQUEST_HEADER_DIRECTIVES:
                problems.append(f"it sets or removes a request header ({' '.join(d.tokens)}): a relay passes the "
                                f"request's headers as they arrive")
    return problems


# --- H2: the pod per tester, by YAML load of the two files ---------------------------------------

def network_problems(name: str, networks: dict) -> list[str]:
    """A network with no route out: defined, `internal: true`, its gateway isolated."""
    spec = networks.get(name)
    if spec is None:
        return [f"the network {name!r} is not defined"]
    problems = []
    if not spec.get("internal"):
        problems.append(f"the network {name!r} is not `internal: true`: it has a route out")
    if (spec.get("driver_opts") or {}).get(GATEWAY_MODE) != "isolated":
        problems.append(f"the network {name!r}'s gateway is not isolated")
    return problems


def front_ranges(networks: dict) -> tuple[str, str]:
    """`front`'s subnet and its dynamic pool (`ip_range`), resolved."""
    config = (((networks.get(FRONT) or {}).get("ipam") or {}).get("config") or [{}])[0] or {}
    return resolved(config.get("subnet", "")), resolved(config.get("ip_range", ""))


def fixed_on_front(services: dict) -> dict:
    """{service: address} of every service with a fixed address on `front`."""
    return {name: resolved(cfg.get("ipv4_address")) for name, svc in services.items()
            for net, cfg in service_networks(svc).items() if net == FRONT and cfg.get("ipv4_address")}


def relay_problems(name: str, svc: "dict | None", networks: dict, proxy: "str | None", read, port: str) -> list[str]:
    """A tester's pod, `hosted-<key>`: the relay that owns the namespace."""
    if svc is None:
        return [f"there is no pod {name}"]
    problems = []
    nets = service_networks(svc)
    for net in sorted(nets):
        if network_problems(net, networks):
            problems.append(f"{name} is on {net}, which has a route out: every network a pod is on is internal")
    for need in (INTERNAL, FRONT):
        if need not in nets:
            problems.append(f"{name} is not on {need} (the database is on internal, the identity proxy on front)")
    if svc.get("cap_drop") != ["ALL"] or svc.get("cap_add"):
        problems.append(f"{name} keeps capabilities (cap_drop {svc.get('cap_drop')}, cap_add {svc.get('cap_add')}): "
                        f"a raw socket is how a container forges a peer on a bridge")
    if not svc.get("read_only") or not set(CADDY_WRITES) <= set(svc.get("tmpfs") or []):
        problems.append(f"{name} is not a read-only root with a tmpfs where Caddy writes: read_only "
                        f"{svc.get('read_only')}, tmpfs {svc.get('tmpfs')}")
    if svc.get("ports"):
        problems.append(f"{name} publishes ports {svc['ports']}: the identity proxy is a pod's one way in")
    files = [s for s, t, _ro in mounts(svc) if t == CADDYFILE_AT]
    if len(files) != 1 or repo_file_of(files[0]) is None:
        problems.append(f"{name} mounts no Caddyfile from the repository at {CADDYFILE_AT}: {files}")
        return problems
    env = {k: resolved(v) for k, v in environment(svc).items()}
    problems += [f"{name}: {p}" for p in relay_caddyfile_problems(read(files[0]), env, proxy, port)]
    return problems


def pod_problems(cfg: dict, read=None) -> list[str]:
    """H2: why the hosted profile, the stand-in laid over it, is not one pod
    per tester behind the identity proxy; [] when it is. `read(source)` gives
    a bind mount's file (a companion plants by passing its own)."""
    read = read or _read_source
    services = cfg.get("services") or {}
    networks = cfg.get("networks") or {}
    problems: list[str] = []
    testers = hosted_testers(cfg)
    if not testers:
        problems.append("no NEXUS service runs under the hosted profile")
    caddy = services.get("caddy")
    if caddy is not None and (not caddy.get("profiles") or "hosted" in caddy["profiles"]):
        problems.append(f"the generic caddy runs under hosted (profiles {caddy.get('profiles')}): its one upstream, the "
                        f"alias `nexus`, is no tester's (D17)")
    problems += network_problems(FRONT, networks) + network_problems(INTERNAL, networks)
    subnet, pool = front_ranges(networks)
    fixed = fixed_on_front(services)
    proxy = None
    if len(fixed) != 1:
        problems.append(f"the services with a fixed address on front are {sorted(fixed)}: exactly one, the identity "
                        f"proxy, has one")
    else:
        (proxy,) = fixed.values()
        try:
            inside = ipaddress.ip_address(proxy) in ipaddress.ip_network(subnet)
            pooled = bool(pool) and ipaddress.ip_address(proxy) in ipaddress.ip_network(pool)
        except ValueError as e:
            problems.append(f"front's subnet {subnet!r}, its pool {pool!r} or the identity proxy's address {proxy!r} does "
                            f"not parse: {e}")
        else:
            if not inside:
                problems.append(f"the identity proxy's address {proxy} is outside front's subnet {subnet}")
            if not pool:
                problems.append("front has no dynamic pool (ip_range): a container created while the identity proxy is "
                                "down could be handed its address")
            elif pooled:
                problems.append(f"the identity proxy's address {proxy} is inside front's dynamic pool {pool}: a container "
                                f"created while the identity proxy is down could be handed it")
    for name, svc in sorted(services.items()):
        nets = service_networks(svc)
        if FRONT in nets and name not in fixed:
            for other in sorted(set(nets) - {FRONT}):
                if network_problems(other, networks):
                    problems.append(f"{name} is on front and on {other}, which has a route out: only the identity proxy "
                                    f"may be")
    joined: dict[str, list[str]] = {}
    for name, svc in sorted(services.items()):
        mode = str(svc.get("network_mode") or "")
        if mode.startswith("service:"):
            joined.setdefault(mode.split(":", 1)[1], []).append(name)
    volumes: dict[str, list[str]] = {}
    databases: dict[tuple, list[str]] = {}
    owners: dict[str, list[str]] = {}
    for name, svc in sorted(testers.items()):
        key = name[len(NEXUS_PREFIX):]
        pod = POD_PREFIX + key
        mode = str(svc.get("network_mode") or "")
        if mode != f"service:{pod}":
            problems.append(f"{name} does not join its own pod's namespace (service:{pod}): network_mode {mode!r}")
        if svc.get("networks") or svc.get("ports"):
            problems.append(f"{name} has networks or ports of its own: its pod's are its only ones")
        env = environment(svc)
        host = resolved(env.get("NEXUS_HOST", ""))
        if not is_loopback(host):
            problems.append(f"{name} binds {host!r}, not a loopback address: in a pod any other bind is reachable from "
                            f"another container, past the relay")
        if resolved(env.get("NEXUS_AUTH_MODE", "")) != "proxy":
            problems.append(f"{name} is not in proxy mode: NEXUS_AUTH_MODE={resolved(env.get('NEXUS_AUTH_MODE', ''))!r}")
        header = env.get("NEXUS_IDENTITY_HEADER", "")
        if "NEXUS_IDENTITY_HEADER" not in interpolated(header) or not resolved(header):
            problems.append(f"{name}'s identity header is not the setting NEXUS_IDENTITY_HEADER with a default: {header!r}")
        owner = env.get("NEXUS_OWNER_IDENTITY", "")
        variable = f"NEXUS_{key.upper()}_OWNER"
        if interpolated(owner) != {variable}:
            problems.append(f"{name}'s owner is not its own variable {variable}: NEXUS_OWNER_IDENTITY={owner!r}")
        elif resolved(owner):
            problems.append(f"{name}'s owner defaults to {resolved(owner)!r}: unset, a tester's process answers for no one")
        owners.setdefault(owner, []).append(name)
        if resolved(env.get("NEXUS_STORE", "")) != "postgres":
            problems.append(f"{name} does not open the hosted store: NEXUS_STORE={env.get('NEXUS_STORE')!r}")
        found = [u for v in env.values() for u in urls_in(v)]
        if not found:
            problems.append(f"{name} names no database URL")
        for url in found:
            databases.setdefault((url_role(url), url_database(url)), []).append(name)
            if (url_role(url), url_database(url)) != (TESTER_PREFIX + key, TESTER_PREFIX + key):
                problems.append(f"{name} connects as {url_role(url)!r} to {url_database(url)!r}, not as its own role to "
                                f"its own database ({TESTER_PREFIX + key})")
        data = [s for s, t, _ro in mounts(svc) if t == DATA_ROOT]
        if len(data) != 1 or repo_file_of(data[0]) is not None or data[0] == "nexus_data":
            problems.append(f"{name} does not mount a volume of its own at {DATA_ROOT}: {data}")
        for source in data:
            volumes.setdefault(source, []).append(name)
        if svc.get("cap_drop") != ["ALL"] or svc.get("cap_add"):
            problems.append(f"{name} keeps capabilities (cap_drop {svc.get('cap_drop')}, cap_add {svc.get('cap_add')}): "
                            f"a raw socket is how a container forges a peer on a bridge")
        deps = svc.get("depends_on") or {}
        deps = {d: {} for d in deps} if isinstance(deps, list) else deps
        if pod not in deps or (deps.get("postgres") or {}).get("condition") != "service_healthy":
            problems.append(f"{name} does not wait for its pod and a healthy postgres: depends_on {sorted(deps)}")
        problems += relay_problems(pod, services.get(pod), networks, proxy, read,
                                   resolved(env.get("NEXUS_PORT", "")))
    for label, table in (("the volume", volumes), ("the database", databases), ("the owner", owners)):
        for value, names in sorted(table.items(), key=lambda kv: str(kv[0])):
            if len(names) > 1:
                problems.append(f"{' and '.join(names)} share {label} {value}: each tester's is its own")
    for pod, members in sorted(joined.items()):
        if len(members) > 1:
            problems.append(f"{' and '.join(members)} join one pod, {pod}: each would be the other's loopback peer, "
                            f"and a tester's process could reach another's port with any identity")
        for member in members:
            if member not in testers:
                problems.append(f"{member} joins {pod}'s namespace: nothing but that tester's NEXUS may run in a pod "
                                f"(every process there is a loopback peer)")
    return problems


def identity_header_names(cfg: dict) -> set[str]:
    """The identity header the NEXUS services read, lower-cased, as compose
    resolves it with nothing set."""
    return {resolved(environment(s).get("NEXUS_IDENTITY_HEADER", "")).lower()
            for n, s in (cfg.get("services") or {}).items() if n.startswith(NEXUS_PREFIX)} - {""}


def header_set_problems(main: dict, read=None) -> list[str]:
    """D16: nothing in the main compose file hands out identity. No Caddyfile
    it mounts sets or removes the identity header, in any spelling (the
    name the NEXUS services read, in any case, or the placeholder that
    carries it), and no service but a tester's own NEXUS holds a tester's
    login (a NEXUS_<KEY>_OWNER value, or NEXUS_OWNER_IDENTITY), nor names the
    identity header as a variable: the preconditions for presenting one."""
    read = read or _read_source
    services = main.get("services") or {}
    names = identity_header_names(main)
    problems = set()
    if not names:
        problems.add("no NEXUS service names an identity header: there is nothing to hold")
    for name, svc in sorted(services.items()):
        env = environment(svc)
        for source, target, _ro in mounts(svc):
            if target != CADDYFILE_AT or repo_file_of(source) is None:
                continue
            text = read(source)
            for substitute in (None, {k: resolved(v) for k, v in env.items()}):
                for d in walk(caddy_tree(text, substitute)):
                    if d.tokens[:1] and d.tokens[0] in REQUEST_HEADER_DIRECTIVES and len(d.tokens) > 1:
                        field = d.tokens[1].lstrip("+-?>").lower()
                        if field in names or "nexus_identity_header" in field:
                            problems.add(f"{name}'s Caddyfile {source} sets or removes the identity header "
                                         f"({' '.join(d.tokens)}): no service in the main file hands out identity")
        for key, value in sorted(env.items()):
            held = sorted(v for v in interpolated(value) if re.fullmatch(r"NEXUS_\w+_OWNER", v))
            if name.startswith(NEXUS_PREFIX) and key == "NEXUS_OWNER_IDENTITY":
                continue
            if held or key == "NEXUS_OWNER_IDENTITY":
                problems.add(f"{name}'s {key} holds a tester's login ({', '.join(held) or key}): only a tester's own "
                             f"NEXUS may, as its owner")
            if key.lower().replace("_", "-") in names:
                problems.add(f"{name}'s environment names the identity header as a variable ({key})")
    return sorted(problems)


def test_h2_one_pod_per_tester_behind_the_identity_proxy():
    """H2 (run/specs/W4.md S3; D15 as refined, D16, D17), by YAML load of
    docker-compose.yml with deploy/compose.identity-sim.yml laid over it
    (the merge is the test's own, declared in `merged`): every hosted NEXUS
    joins its own pod's namespace and no other's, binds a loopback address,
    has its own volume, database URL and owner variable (whose default is
    empty), is in proxy mode, drops every capability and waits for its pod
    and a healthy database; every network a pod is on is internal; every
    relay has its admin endpoint off and trusts exactly the identity proxy's
    one address, which is front's one fixed address, inside its subnet and
    outside its dynamic pool; nothing else on front has a route out; the
    generic caddy does not run under hosted; the main file sets the identity
    header nowhere; the override only adds. Law 16: on the pre-part-3 file
    the hosted NEXUS joins no pod and the override does not exist."""
    main, override = compose_file(), override_file()
    cfg = merged(main, override)
    testers = sorted(hosted_testers(cfg))
    problems = override_problems(main, override) + pod_problems(cfg) + header_set_problems(main)
    print(f"\nH2 testers {testers}; front {front_ranges(cfg.get('networks') or {})}; fixed on front "
          f"{fixed_on_front(cfg.get('services') or {})}; problems {problems}")
    hold(problems)
    assert OVERRIDE.is_file(), "deploy/compose.identity-sim.yml is missing"
    assert len(testers) >= 3, f"non-vacuity: three testers are written out, found {testers}"


def test_h2_companions_each_planted_problem_is_named():
    """H2's companions, each on a modified copy of the loaded YAML or of a
    Caddyfile's text, each asserting the rule names the problem: two testers
    in one pod; a tester sharing another's volume; a pod on egress; a relay
    that trusts front's subnet; a relay with its admin endpoint on; a
    header_up of the identity header in the main file's Caddyfile (lower
    case) and in a relay's (by its placeholder); a relay holding a tester's
    login in its environment; a NEXUS bound beyond loopback in a pod (two
    spellings); the identity proxy's address inside the dynamic pool; a
    second service joining a pod. None of the planted values is spelled in
    the rules' source."""
    main, override = compose_file(), override_file()
    cfg = merged(main, override)
    hold(pod_problems(cfg) + header_set_problems(main))        # the files as they are pass, so a plant alone is named
    testers = sorted(hosted_testers(cfg))
    one, two = testers[0], testers[1]
    pod_one, pod_two = POD_PREFIX + one[len(NEXUS_PREFIX):], POD_PREFIX + two[len(NEXUS_PREFIX):]

    planted = planted_copy(cfg)                                 # two testers in one pod
    planted["services"][two]["network_mode"] = f"service:{pod_one}"
    said = refused(pod_problems(planted))
    assert f"{two} does not join its own pod's namespace" in said and f"join one pod, {pod_one}" in said, said

    planted = planted_copy(cfg)                                 # a tester sharing another's volume
    theirs = [s for s, t, _ro in mounts(cfg["services"][one]) if t == DATA_ROOT][0]
    planted["services"][two]["volumes"] = [f"{theirs}:{DATA_ROOT}"]
    assert f"share the volume {theirs}" in refused(pod_problems(planted))

    planted = planted_copy(cfg)                                 # a pod on egress
    nets = service_networks(planted["services"][pod_one])
    planted["services"][pod_one]["networks"] = {**nets, "egress": {}}
    said = refused(pod_problems(planted))
    assert f"{pod_one} is on egress, which has a route out" in said, said

    relay_source = [s for s, t, _ro in mounts(cfg["services"][pod_one]) if t == CADDYFILE_AT][0]
    relay_text = _read_source(relay_source)
    subnet_value = cfg["networks"][FRONT]["ipam"]["config"][0]["subnet"]

    def reading(text: str):
        return lambda source: text if source == relay_source else _read_source(source)

    planted = planted_copy(cfg)                                 # a relay that trusts front's subnet
    for name in (pod_one, pod_two):
        planted["services"][name]["environment"]["PLANTED_TRUST"] = subnet_value
    trusting = re.sub(r"\{\$NEXUS_IDENTITY_PROXY_IP[^}]*\}", "{$PLANTED_TRUST}", relay_text)
    said = refused(pod_problems(planted, reading(trusting)))
    assert "a range or a name, not one address" in said, said

    opened = relay_text.replace("admin off", "admin 127.0.0.1:2019")    # a relay with its admin endpoint on
    assert opened != relay_text
    assert "admin endpoint is on" in refused(pod_problems(cfg, reading(opened)))

    header = sorted(identity_header_names(main))[0]
    main_caddyfile = [s for s, t, _ro in mounts(main["services"]["caddy"]) if t == CADDYFILE_AT][0]
    console = _read_source(main_caddyfile)
    setting = re.sub(r"(flush_interval -1\n)", r"\1\t\theader_up " + header.lower() + " planted@example.invalid\n",
                     console, count=1)
    assert setting != console
    said = refused(header_set_problems(main, lambda s: setting if s == main_caddyfile else _read_source(s)))
    assert f"caddy's Caddyfile {main_caddyfile} sets or removes the identity header" in said, said
    removing = re.sub(r"(flush_interval -1\n)", "\\1\t\theader_up -{$NEXUS_IDENTITY_HEADER}\n", relay_text, count=1)
    assert removing != relay_text
    said = refused(header_set_problems(main, reading(removing)))
    assert "sets or removes the identity header" in said, said
    assert "sets or removes a request header" in refused(pod_problems(cfg, reading(removing)))

    planted_main = planted_copy(main)                           # a relay holding a tester's login
    planted_main["services"][pod_one].setdefault("environment", {})["PRESENTS"] = \
        "${NEXUS_" + one[len(NEXUS_PREFIX):].upper() + "_OWNER:-}"
    assert f"{pod_one}'s PRESENTS holds a tester's login" in refused(header_set_problems(planted_main))

    for spelling in ("0.0.0.0", "::"):                         # a NEXUS bound beyond loopback in a pod
        planted = planted_copy(cfg)
        planted["services"][one]["environment"]["NEXUS_HOST"] = spelling
        assert f"{one} binds {spelling!r}, not a loopback address" in refused(pod_problems(planted)), spelling

    planted = planted_copy(cfg)                                 # the identity proxy inside the dynamic pool
    _subnet, pool = front_ranges(cfg["networks"])
    (proxy_service,) = fixed_on_front(cfg["services"])
    inside_pool = str(next(ipaddress.ip_network(pool).hosts()))
    planted["services"][proxy_service]["networks"][FRONT]["ipv4_address"] = inside_pool
    said = refused(pod_problems(planted))
    assert "inside front's dynamic pool" in said and f"it trusts" in said, said

    planted = planted_copy(cfg)                                 # a second service in a pod
    first_backup = sorted(n for n in cfg["services"] if n.startswith(BACKUP_PREFIX))[0]
    planted["services"][first_backup]["network_mode"] = f"service:{pod_one}"
    assert f"{first_backup} joins {pod_one}'s namespace" in refused(pod_problems(planted))

    planted = planted_copy(cfg)                                 # D17: the generic caddy under hosted
    planted["services"]["caddy"]["profiles"] = sorted(set(planted["services"]["caddy"].get("profiles") or []) | {"hosted"})
    assert "the generic caddy runs under hosted" in refused(pod_problems(planted))

    rules = "".join(inspect.getsource(f) for f in (pod_problems, relay_problems, relay_caddyfile_problems,
                                                    header_set_problems, network_problems))
    for plant in ("PLANTED_TRUST", "2019", "planted@example.invalid", "PRESENTS", "0.0.0.0", '"::"', header.lower()):
        assert plant not in rules, f"the rules' source names the plant {plant!r}"


def test_h2_the_cli_merge_agrees(tmp_path):
    """Where the docker CLI exists (client-side, no daemon), `docker compose
    config` over the two files agrees with the test's own merge on what H2
    reads: the services the hosted profile runs, each tester's network mode,
    and the identity proxy's address on front. Skipped by name without it."""
    if shutil.which("docker") is None:
        pytest.skip(NO_DOCKER)
    main, override = compose_file(), override_file()
    ours = merged(main, override)
    testers = sorted(hosted_testers(ours))
    owners = {f"NEXUS_{n[len(NEXUS_PREFIX):].upper()}_OWNER": f"{n}@cli.example.invalid" for n in testers}
    empty = tmp_path / "empty.env"                    # not the repository's own .env, which compose would read
    empty.write_text("", encoding="utf-8")
    proc = _proc.run(["docker", "compose", "--env-file", str(empty), "-f", str(COMPOSE), "-f", str(OVERRIDE),
                           "--profile", "hosted", "config", "--format", "json"], cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env={**os.environ, **owners}, timeout=120)
    assert proc.returncode == 0, f"docker compose config refused the two files:\n{proc.stderr[-2000:]}"
    cli = json.loads(proc.stdout)
    theirs = cli.get("services") or {}
    expected = {n for n, s in (ours.get("services") or {}).items()
                if not s.get("profiles") or "hosted" in s.get("profiles")}
    print(f"\nH2 CLI services {sorted(theirs)}; ours under hosted {sorted(expected)}")
    assert set(theirs) == expected, (sorted(theirs), sorted(expected))
    for name in testers:
        assert theirs[name].get("network_mode") == ours["services"][name]["network_mode"], name
    assert fixed_on_front(theirs) == fixed_on_front(ours["services"]), (fixed_on_front(theirs), fixed_on_front(ours["services"]))


# --- the stand-in agrees with the verifier's table -----------------------------------------------

def sim_problems(cfg: dict, read=None) -> list[str]:
    """D16 and D19: the stand-in is what the verifier's table says. It is
    on front at the identity proxy's address and on egress; it publishes
    each of the verifier's ports, on 127.0.0.1 only; each tester's port sets
    the identity header to that tester's login (the variable that tester's
    NEXUS answers for) and forwards to that tester's relay; the mismatch
    port presents the second tester's login to the first tester's relay;
    the no-identity port removes the header and forwards to the first
    tester's relay; and the relay's fixed refusal is the one the verifier
    reads."""
    from scripts import verify_deploy as vd
    read = read or _read_source
    services = cfg.get("services") or {}
    sim = services.get(vd.SIM_SERVICE)
    if sim is None:
        return [f"there is no {vd.SIM_SERVICE} service"]
    problems = []
    fixed = fixed_on_front(services)
    if list(fixed) != [vd.SIM_SERVICE]:
        problems.append(f"{vd.SIM_SERVICE} is not front's one fixed address: {fixed}")
    if set(service_networks(sim)) != {FRONT, "egress"}:
        problems.append(f"{vd.SIM_SERVICE} is on {sorted(service_networks(sim))}, not front and egress")
    published = {}
    for p in sim.get("ports") or []:
        if isinstance(p, dict):
            host_ip, target = str(p.get("host_ip", "")), str(p.get("target", ""))
        else:
            parts = resolved(p).split(":")
            host_ip, target = (parts[0], parts[-1]) if len(parts) == 3 else ("", parts[-1])
        published[target] = host_ip
    want = {str(port) for port in [*vd.SIM_TESTER_PORTS.values(), vd.SIM_MISMATCH[0], vd.SIM_NO_IDENTITY[0]]}
    if set(published) != want or set(published.values()) != {"127.0.0.1"}:
        problems.append(f"{vd.SIM_SERVICE} publishes {published}, not each of {sorted(want)} on 127.0.0.1 alone")
    files = [s for s, t, _ro in mounts(sim) if t == CADDYFILE_AT]
    if len(files) != 1:
        return problems + [f"{vd.SIM_SERVICE} mounts no Caddyfile at {CADDYFILE_AT}"]
    tree = caddy_tree(read(files[0]), None)
    sites = {s.tokens: s for s in site_blocks(tree)}
    env = environment(sim)
    testers = hosted_testers(cfg)
    owner_of = {n[len(NEXUS_PREFIX):]: sorted(interpolated(environment(s).get("NEXUS_OWNER_IDENTITY", "")))
                for n, s in testers.items()}

    def route(port: int) -> "tuple | None":
        site = sites.get((f":{port}",))
        forwards = [d for d in (site.block if site else ()) if d.tokens[:1] == ("reverse_proxy",)]
        if len(forwards) != 1:
            return None
        headers = [d.tokens[1:] for d in walk(forwards[0].block) if d.tokens[:1] == ("header_up",)]
        return forwards[0].tokens[1:], headers, {d.tokens for d in forwards[0].block}

    def sets_login_of(headers, key) -> bool:
        variables = owner_of.get(key) or ["?"]
        return any(len(h) == 2 and "NEXUS_IDENTITY_HEADER" in h[0] and h[1] == "{$" + variables[0] + "}"
                   and variables[0] in interpolated(env.get(variables[0], "")) for h in headers)

    for key, port in sorted(vd.SIM_TESTER_PORTS.items()):
        got = route(port)
        if got is None or got[0] != (f"{POD_PREFIX}{key}{RELAY_LISTEN}",) or not sets_login_of(got[1], key) \
                or ("flush_interval", "-1") not in got[2]:
            problems.append(f"the stand-in's port {port} does not set tester {key}'s login and forward to "
                            f"{POD_PREFIX}{key}{RELAY_LISTEN} unbuffered: {got}")
    port, presented, reached = vd.SIM_MISMATCH
    got = route(port)
    if got is None or got[0] != (f"{POD_PREFIX}{reached}{RELAY_LISTEN}",) or not sets_login_of(got[1], presented):
        problems.append(f"the mismatch port {port} does not present tester {presented}'s login to {POD_PREFIX}{reached}: {got}")
    port, reached = vd.SIM_NO_IDENTITY
    got = route(port)
    removes = got is not None and any(len(h) == 1 and h[0].startswith("-") and "NEXUS_IDENTITY_HEADER" in h[0]
                                      for h in got[1])
    if got is None or got[0] != (f"{POD_PREFIX}{reached}{RELAY_LISTEN}",) or not removes or len(got[1]) != 1:
        problems.append(f"the no-identity port {port} does not remove the identity header and forward to "
                        f"{POD_PREFIX}{reached}: {got}")
    if ("admin", "off") not in {d.tokens for d in global_options(tree)}:
        problems.append(f"{vd.SIM_SERVICE}'s admin endpoint is on")
    for name in sorted(testers):
        relay = services.get(POD_PREFIX + name[len(NEXUS_PREFIX):]) or {}
        for source, target, _ro in mounts(relay):
            if target == CADDYFILE_AT:
                bodies = [d.tokens[2] for d in walk(caddy_tree(read(source), {})) if d.tokens[:1] == ("respond",)]
                if bodies != [vd.RELAY_REFUSAL]:
                    problems.append(f"the relay's refusal {bodies} is not the one the verifier reads ({vd.RELAY_REFUSAL!r})")
    return sorted(set(problems))


def test_h2_the_stand_in_is_the_verifiers_table():
    """The override's stand-in and its Caddyfile agree with
    scripts/verify_deploy.py's table (SIM_TESTER_PORTS, SIM_MISMATCH,
    SIM_NO_IDENTITY, RELAY_REFUSAL), so the smoke's checks reach what they
    name. Law 16: neither the stand-in nor the table exists before (new
    names; declared)."""
    cfg = merged(compose_file(), override_file())
    problems = sim_problems(cfg)
    print(f"\nH2 the stand-in: problems {problems}")
    hold(problems)


def test_h2_the_stand_in_companions():
    cfg = merged(compose_file(), override_file())
    hold(sim_problems(cfg))
    from scripts import verify_deploy as vd
    sim = cfg["services"][vd.SIM_SERVICE]
    source = [s for s, t, _ro in mounts(sim) if t == CADDYFILE_AT][0]
    text = _read_source(source)
    swapped = text.replace("hosted-t1:8100", "hosted-t9:8100", 1)            # a route to the wrong relay
    assert swapped != text
    assert "does not set tester" in refused(sim_problems(cfg, lambda s: swapped if s == source else _read_source(s)))
    planted = planted_copy(cfg)                                              # a port published beyond loopback
    planted["services"][vd.SIM_SERVICE]["ports"] = [p.replace("127.0.0.1:", "0.0.0.0:", 1) if isinstance(p, str) else p
                                                     for p in planted["services"][vd.SIM_SERVICE]["ports"]]
    assert "on 127.0.0.1 alone" in refused(sim_problems(planted))


# --- D18: one backup per tester -----------------------------------------------------------------------

def backup_problems(cfg: dict) -> list[str]:
    """D18: one backup holds one store, per tester. Beside each tester's
    NEXUS a `backup-<key>` service under the backup profile, on the internal
    network only, the tester's own volume mounted read-only at the data
    root, a host folder of its own (so retention prunes within a tester and
    --latest-under reads one tester's backups), a read-only root, every
    capability dropped; the generic `backup` takes no hosted store's URL,
    since no store under compose answers to one."""
    services = cfg.get("services") or {}
    problems = []
    folders: dict[str, list[str]] = {}
    for name, svc in sorted(hosted_testers(cfg).items()):
        backup_name = BACKUP_PREFIX + name[len(NEXUS_PREFIX):]
        backup = services.get(backup_name)
        if backup is None:
            problems.append(f"{name} has no backup service of its own ({backup_name})")
            continue
        volume = [s for s, t, _ro in mounts(svc) if t == DATA_ROOT]
        theirs = {(s, ro) for s, t, ro in mounts(backup) if t == DATA_ROOT}
        if not volume or theirs != {(volume[0], True)}:
            problems.append(f"{backup_name} does not mount {name}'s volume {volume} read-only at {DATA_ROOT}: {sorted(theirs)}")
        out = [s for s, t, _ro in mounts(backup) if t == BACKUPS_AT]
        if len(out) != 1:
            problems.append(f"{backup_name} has no host folder of its own at {BACKUPS_AT}: {out}")
        for s in out:
            folders.setdefault(s, []).append(backup_name)
        if backup.get("profiles") != ["backup"] or set(service_networks(backup)) != {INTERNAL} or backup.get("network_mode"):
            problems.append(f"{backup_name} is not a backup-profile service on the internal network only: profiles "
                            f"{backup.get('profiles')}, networks {sorted(service_networks(backup))}")
        if backup.get("cap_drop") != ["ALL"] or not backup.get("read_only"):
            problems.append(f"{backup_name} keeps capabilities or a writable root: cap_drop {backup.get('cap_drop')}, "
                            f"read_only {backup.get('read_only')}")
    for folder, names in sorted(folders.items()):
        if len(names) > 1:
            problems.append(f"{' and '.join(names)} write one host folder {folder}: retention would prune across "
                            f"testers, and --latest-under would read another tester's backup")
    generic = environment(services.get("backup") or {})
    if any("NEXUS_BACKUP_DATABASE_URL" in interpolated(v) or urls_in(v) for v in generic.values()):
        problems.append("the generic backup takes a hosted store's URL: under compose no store answers to it (one "
                        "backup per tester, backup-<key>)")
    return problems


def test_d18_one_backup_per_tester():
    cfg = compose_file()
    problems = backup_problems(cfg)
    print(f"\nD18 backups {sorted(n for n in cfg.get('services') or {} if n.startswith('backup'))}; problems {problems}")
    assert hosted_testers(cfg), "non-vacuity: the hosted testers are read"
    hold(problems)


def test_d18_companions():
    cfg = compose_file()
    hold(backup_problems(cfg))
    names = sorted(n for n in cfg["services"] if n.startswith(BACKUP_PREFIX))
    planted = planted_copy(cfg)                       # two testers' backups in one host folder
    folder = [v for v in planted["services"][names[0]]["volumes"] if ":" + BACKUPS_AT in v][0]
    planted["services"][names[1]]["volumes"] = [v if ":" + BACKUPS_AT not in v else folder
                                                for v in planted["services"][names[1]]["volumes"]]
    assert "write one host folder" in refused(backup_problems(planted))
    planted = planted_copy(cfg)                       # a backup that could write the tester's volume
    planted["services"][names[0]]["volumes"] = [v.replace(":ro", "") if ":" + DATA_ROOT in v else v
                                                for v in planted["services"][names[0]]["volumes"]]
    assert f"{names[0]} does not mount" in refused(backup_problems(planted))
    planted = planted_copy(cfg)                       # the generic backup handed a hosted URL again
    planted["services"]["backup"]["environment"]["NEXUS_BACKUP_DATABASE_URL"] = "${NEXUS_BACKUP_DATABASE_URL:-}"
    assert "the generic backup takes a hosted store's URL" in refused(backup_problems(planted))


DAILY = REPO / "deploy" / "nexus-backup.sh"
STAND_IN_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "$DOCKER_RECORD"
case "$*" in
  *"config --services"*) printf '%s\\n' postgres hosted-t1 nexus-t1 backup backup-t1 backup-t2 backup-t3 ;;
  *"run --rm $FAIL_SERVICE") exit 3 ;;
esac
exit 0
"""


def _daily(tmp_path: Path, env_lines: str, fail: str = "") -> tuple[subprocess.CompletedProcess, list[str]]:
    """deploy/nexus-backup.sh as the systemd unit runs it, from a copy of the
    project folder (the script and its .env), with a stand-in `docker` first
    on PATH that records each invocation."""
    project = tmp_path / "project"
    (project / "deploy").mkdir(parents=True)
    shutil.copy(DAILY, project / "deploy" / DAILY.name)
    (project / ".env").write_text(env_lines, encoding="utf-8", newline="\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(STAND_IN_DOCKER, encoding="utf-8", newline="\n")
    (bin_dir / "docker").chmod(0o755)
    record = tmp_path / "docker_record.txt"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NEXUS_", "COMPOSE_"))}
    env.update(PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", DOCKER_RECORD=str(record), FAIL_SERVICE=fail)
    proc = _proc.run([shutil.which("sh") or "sh", str(project / "deploy" / DAILY.name)], cwd=tmp_path, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    calls = record.read_text(encoding="utf-8").splitlines() if record.is_file() else []
    return proc, calls


def test_d18_the_daily_backup_runs_every_testers_backup(tmp_path):
    """deploy/nexus-backup.sh, the daily timer's command on a compose host
    (cloud-init's nexus-backup.service): under hosted it runs every
    `backup-<key>` the compose file names (asked of compose, so a tester
    added to the file is backed up with no edit here), each one even when
    another fails, and fails naming the failed ones; under every other
    profile it runs the one `backup`. It reads NEXUS_PROFILE from the
    project's .env as cloud-init's own commands read it (a shell: an inline
    comment is a comment), and stops by name without one. Law 16: the
    script does not exist before (a new file; declared): the timer ran the
    generic backup, which under the per-tester hosted shape would back up
    no tester."""
    if shutil.which("sh") is None:
        pytest.skip(NO_SH)
    assert DAILY.is_file(), "deploy/nexus-backup.sh is missing"
    proc, calls = _daily(tmp_path / "hosted", "NEXUS_PROFILE=hosted                    # FILL: stub | ollama | api | hosted\n")
    print(f"\nD18 daily, hosted: exit {proc.returncode}; calls {calls}; stderr {proc.stderr.strip()!r}")
    runs = [c for c in calls if " run --rm " in c]
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert runs == [f"compose --profile hosted --profile backup run --rm backup-{k}" for k in ("t1", "t2", "t3")], runs
    proc, calls = _daily(tmp_path / "failing", "NEXUS_PROFILE=hosted\n", fail="backup-t2")
    runs = [c for c in calls if " run --rm " in c]
    print(f"D18 daily, backup-t2 failing: exit {proc.returncode}; runs {runs}; stderr {proc.stderr.strip()!r}")
    assert proc.returncode == 1 and len(runs) == 3 and runs[-1].endswith("backup-t3"), (proc.returncode, runs)
    assert "backup-t2" in proc.stderr and "backup-t1" not in proc.stderr, proc.stderr
    proc, calls = _daily(tmp_path / "stub", "NEXUS_PROFILE=stub\n")
    assert proc.returncode == 0 and calls == ["compose --profile stub --profile backup run --rm backup"], calls
    proc, calls = _daily(tmp_path / "unset", "NEXUS_DOMAIN=example.org\n")
    assert proc.returncode != 0 and "NEXUS_PROFILE" in proc.stderr and calls == [], (proc.returncode, proc.stderr, calls)
    assert _proc.run([shutil.which("sh"), "-n", str(DAILY)], capture_output=True, timeout=600).returncode == 0
    checker = shutil.which("shellcheck")
    if checker:
        lint = _proc.run([checker, str(DAILY)], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
        assert lint.returncode == 0, f"shellcheck exit {lint.returncode}:\n{lint.stdout}{lint.stderr}"


# --- H4: the verifier's hosted verdicts without a deployment ------------------------------------------

def test_h4_the_hosted_verdicts_on_planted_rows():
    """H4 (S3), the existing B6 shape: each hosted check's verdict function
    on planted rows. V1 health inside the pod (proxy mode, an owner set, the
    hosted store); V11 whoami names the expected login and `proxy`; V4 the
    record carries the login as accountable_identity and identity_mechanism
    `proxy` (planted rows of the record as it is before parts 1b and 2
    fail, by name); V12 three distinct identities; V13 no record of one
    tester in another's store; V2 NEXUS's own refusals, never the relay's;
    V14 the relay's fixed refusal at every address it has, a connection that
    failed proving nothing; V10 another tester's database refuses this
    role for the CONNECT privilege, no other reason; the sessions line is
    MEASURED, never a verdict. Law 16: none of these exists before (new
    names; declared)."""
    from scripts import verify_deploy as vd
    from backend.identity import NO_IDENTITY, NOT_OWNER
    body = {"ollama": True, "model_server": {"backend": "stub", "provider": "StubProvider", "reachable": True},
            "store": {"backend": "postgres", "target": "postgres nexus_t1 schema public"},
            "identity": {"mode": "proxy", "header": "Tailscale-User-Login", "owner_set": True}}
    ok, detail = vd.hosted_health_verdict({"status": 200, "body": body})
    print(f"\nH4 V1: {ok}: {detail}")
    assert ok, detail
    for planted, word in (({"status": 200, "body": {**body, "identity": {**body["identity"], "owner_set": False}}}, "owner"),
                          ({"status": 200, "body": {**body, "identity": {**body["identity"], "mode": "token"}}}, "proxy"),
                          ({"status": 200, "body": {**body, "store": {"backend": "sqlite"}}}, "postgres"),
                          ({"status": 503, "body": body}, "503"),
                          ({"error": "URLError: [Errno 111] Connection refused"}, "URLError")):
        ok, detail = vd.hosted_health_verdict(planted)
        assert not ok and word in detail, (planted, detail)

    who = {"auth_mode": "proxy", "login": "t1@sim.example", "identity_mechanism": "proxy",
           "identity_header": "Tailscale-User-Login", "owner_set": True}
    assert vd.whoami_verdict(200, who, "t1@sim.example")[0]
    for status, planted, login in ((200, {**who, "login": "t2@sim.example"}, "t1@sim.example"),
                                   (200, {**who, "identity_mechanism": "asserted"}, "t1@sim.example"),
                                   (200, {**who, "auth_mode": "token"}, "t1@sim.example"),
                                   (401, {"detail": NO_IDENTITY}, "t1@sim.example"),
                                   (200, who, None)):
        ok, detail = vd.whoami_verdict(status, planted, login)
        assert not ok, (status, planted, login, detail)

    record = {"lineage_id": "L1", "record_hash": "ab" * 32, "accountable_identity": "t1@sim.example",
              "identity_mechanism": "proxy"}
    assert vd.record_identity_verdict(record, "t1@sim.example")[0]
    before_parts = {k: v for k, v in record.items() if k != "identity_mechanism"}
    for planted, word in (({**before_parts, "accountable_identity": None}, "accountable_identity"),   # before 1b
                          (before_parts, "identity_mechanism"),                                     # before part 2
                          ({**record, "identity_mechanism": "asserted"}, "identity_mechanism"),
                          ({**record, "record_hash": None}, "record_hash"),
                          ({**record, "accountable_identity": "t2@sim.example"}, "t2@sim.example")):
        ok, detail = vd.record_identity_verdict(planted, "t1@sim.example")
        assert not ok and word in detail, (planted, detail)

    expected = {"t1": "t1@sim.example", "t2": "t2@sim.example", "t3": "t3@sim.example"}
    assert vd.distinct_identities_verdict(dict(expected), expected)[0]
    for planted in ({**expected, "t2": expected["t1"]}, {**expected, "t3": None}, {"t1": expected["t1"]}):
        ok, detail = vd.distinct_identities_verdict(planted, expected)
        assert not ok, (planted, detail)

    own = {"lineage_id": "L1", "accountable_identity": "t1@sim.example"}
    others = {"t2": ("t2@sim.example", "L2"), "t3": ("t3@sim.example", "L3")}
    assert vd.isolation_verdict("t1", "t1@sim.example", [own], "L1", others)[0]
    for records, own_id in (([own, {"lineage_id": "L2", "accountable_identity": "t2@sim.example"}], "L1"),
                            ([own, {"lineage_id": "LX", "accountable_identity": "t3@sim.example"}], "L1"),
                            ([own, {"lineage_id": "L3", "accountable_identity": None}], "L1"),
                            ([], "L1")):
        ok, detail = vd.isolation_verdict("t1", "t1@sim.example", records, own_id, others)
        assert not ok, (records, detail)

    assert vd.refused_verdict(403, {"detail": NOT_OWNER}, 403, NOT_OWNER)[0]
    assert vd.refused_verdict(401, {"detail": NO_IDENTITY}, 401, NO_IDENTITY)[0]
    for status, planted in ((200, who), (403, {"_raw": vd.RELAY_REFUSAL}), (401, {"detail": NOT_OWNER})):
        assert not vd.refused_verdict(status, planted, 403, NOT_OWNER)[0], (status, planted)

    refusal = {"status": 403, "body": vd.RELAY_REFUSAL}
    good = {"attempts": [{"network": "nexus_front", "address": "172.31.240.2", **refusal},
                         {"network": "nexus_internal", "address": "172.18.0.4", **refusal}]}
    ok, detail = vd.relay_refusal_verdict(good)
    print(f"H4 V14: {ok}: {detail}")
    assert ok, detail
    for planted in ({"attempts": [good["attempts"][0], {**good["attempts"][1], "status": 200, "body": "{}"}]},
                    {"attempts": [{**good["attempts"][0], "body": json.dumps({"detail": NOT_OWNER})}]},
                    {"attempts": [{**good["attempts"][0], "status": None, "error": "TimeoutError: timed out"}]},
                    {"attempts": []}, {"error": "the probe failed"}):
        ok, detail = vd.relay_refusal_verdict(planted)
        assert not ok, (planted, detail)

    denied = ('connection failed: connection to server at "172.18.0.2", port 5432 failed: FATAL:  permission denied '
              'for database "nexus_t2"\nDETAIL:  User does not have CONNECT privilege.')
    assert vd.cross_database_verdict({"database": "nexus_t2", "refused": True, "message": denied})[0]
    for planted in ({"database": "nexus_t2", "refused": False, "connected_as": ["nexus_t1", "nexus_t2"]},
                    {"database": "nexus_t2", "refused": True, "message": 'password authentication failed for user "nexus_t1"'},
                    {"error": "OperationalError: could not translate host name"}, {}):
        ok, detail = vd.cross_database_verdict(planted)
        assert not ok, (planted, detail)

    line = vd.sessions_line([("nexus_t1", "nexus_t1", 8), ("nexus_t2", "nexus_t2", 9)], "100")
    print(f"H4 {line}")
    assert line.startswith("MEASURED") and "nexus_t1" in line and "17" in line and "100" in line, line
    assert not re.match(r"(PASS|FAIL|SKIP)\b", line)

    rules = "".join(inspect.getsource(f) for f in (vd.whoami_verdict, vd.record_identity_verdict,
                                                    vd.isolation_verdict, vd.relay_refusal_verdict,
                                                    vd.cross_database_verdict, vd.distinct_identities_verdict))
    for plant in ("t1@sim.example", "t2@sim.example", "L2", "LX", "172.18.0.4", "nexus_t2"):
        assert plant not in rules, f"a verdict's source names the plant {plant!r}"


# A fake deployment for the verifier's orchestration: every docker call and
# every request through the stand-in answered as three healthy testers would.

KEYS = ("t1", "t2", "t3")
LOGINS = {k: f"{k}@sim.example" for k in KEYS}


def _rest(cmd: tuple) -> tuple:
    """A compose invocation's words after its global options (-f, -p, --profile)."""
    words, i = list(cmd[1:]), 0
    while i < len(words) and words[i] in ("-f", "-p", "--profile"):
        i += 2
    return tuple(words[i:])


class FakeDeployment:
    def __init__(self, vd, *, relay_status: int = 403, cross_attributed: bool = False):
        self.vd, self.relay_status, self.cross_attributed = vd, relay_status, cross_attributed
        self.calls: list[tuple] = []
        self.fetches: list[tuple] = []

    def done(self, code: int = 0, out: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(["docker"], code, stdout=out, stderr="")

    def docker(self, _args, *cmd, input_text=None, timeout=600):
        vd = self.vd
        self.calls.append(cmd)
        if cmd[:2] == ("network", "inspect"):
            return self.done(0, "true isolated invalid IP\n")
        if cmd[:1] == ("inspect",):
            pod = cmd[-1].split("cid-", 1)[1]
            return self.done(0, json.dumps({"nexus_front": {"IPAddress": f"172.31.240.{KEYS.index(pod[-2:]) + 2}"},
                                            "nexus_internal": {"IPAddress": f"172.18.0.{KEYS.index(pod[-2:]) + 4}"}}))
        rest = _rest(cmd)
        if rest[:2] == ("config", "--services"):
            return self.done(0, "\n".join(["postgres", "backup", *[f"{p}{k}" for k in KEYS
                                                                  for p in (POD_PREFIX, NEXUS_PREFIX, BACKUP_PREFIX)]]))
        if rest[:2] == ("ps", "-q"):
            return self.done(0, f"cid-{rest[2]}\n")
        if rest[:1] == ("port",):
            return self.done(0, f"127.0.0.1:{rest[2]}\n")
        if rest[:3] == ("exec", "-T", "postgres"):
            return self.done(0, "".join(f"{TESTER_PREFIX}{k}|{TESTER_PREFIX}{k}|8\n" for k in KEYS) + "100\n")
        if rest[:2] == ("exec", "-T") and input_text is not None:
            key = rest[2][len(NEXUS_PREFIX):]
            if input_text == vd.HEALTH_PROBE:
                return self.done(0, json.dumps({"status": 200, "body": {
                    "ollama": True, "model_server": {"backend": "stub"}, "store": {"backend": "postgres"},
                    "identity": {"mode": "proxy", "header": "Tailscale-User-Login", "owner_set": True}}}))
            if input_text == vd.ROLE_PROBE:
                return self.done(0, json.dumps({"database": f"nexus_{key}", "public_connect": False, "roles": [
                    {"role": f"nexus_{key}", "is_session_user": True, "is_current_user": True, "superuser": False,
                     "createdb": False, "createrole": False, "replication": False, "bypassrls": False,
                     "predefined": ["pg_database_owner"], "owns_database": True}]}))
            if "egress probe" in input_text:
                refused = {"result": "OSError", "errno": 101, "name": "ENETUNREACH", "seconds": 0.0}
                return self.done(0, json.dumps({"routes": [], "default_route": False, "sibling": {"result": "connected"},
                                                "public": {f"{h}:{p}": refused for h, p in vd.PUBLIC_ADDRESSES}}))
            if "other-database probe" in input_text:
                return self.done(0, json.dumps({"database": "nexus_x", "refused": True,
                                                "message": 'FATAL:  permission denied for database "nexus_x"'}))
            if "relay probe" in input_text:
                body = vd.RELAY_REFUSAL if self.relay_status == 403 else "{}"
                return self.done(0, json.dumps({"attempts": [{"network": "nexus_front", "address": "172.31.240.9",
                                                              "status": self.relay_status, "body": body}]}))
            return self.done(1, "")
        if rest[:2] == ("run", "--rm") and rest[2:3] != ("--entrypoint",):
            return self.done(0, "backup: /var/backups/nexus/20261112T033000Z\n")
        if rest[:4] == ("run", "--rm", "--entrypoint", "python"):
            return self.done(0, "PASS R1\nrestore verification: 5 of 5 checks passed\n")
        return self.done(1, f"the fake has no answer for {cmd}")

    def fetch(self, console, method, path, body=None, *, auth=True, timeout=60, headers=None):
        from backend.identity import NO_IDENTITY, NOT_OWNER
        vd = self.vd
        self.fetches.append((console.base, method, path, dict(headers or {})))
        port = int(console.base.rsplit(":", 1)[1])
        if port == vd.SIM_MISMATCH[0]:
            return 403, json.dumps({"detail": NOT_OWNER}).encode()
        if port == vd.SIM_NO_IDENTITY[0]:
            return 401, json.dumps({"detail": NO_IDENTITY}).encode()
        key = next(k for k, p in vd.SIM_TESTER_PORTS.items() if p == port)
        record = {"lineage_id": f"L-{key}", "record_hash": "ab" * 32, "accountable_identity": LOGINS[key],
                  "identity_mechanism": "proxy"}
        if (method, path) == ("GET", "/whoami"):
            return 200, json.dumps({"auth_mode": "proxy", "login": LOGINS[key], "identity_mechanism": "proxy"}).encode()
        if (method, path) == ("POST", "/chat"):
            return 200, (f'event: done\ndata: {{"lineage_id": "L-{key}", "learning": {{"display": "frozen"}}}}\n\n').encode()
        if (method, path) == ("GET", f"/lineage/L-{key}"):
            return 200, json.dumps(record).encode()
        if (method, path) == ("GET", "/lineage/verify"):
            return 200, json.dumps({"verdict": "intact"}).encode()
        if (method, path) == ("POST", "/kairos/cycle"):
            return 200, json.dumps({"sealed_records": 1}).encode()
        if (method, path) == ("GET", "/chain/status"):
            return 200, json.dumps({"verification": {"valid": True}, "n_blocks": 2}).encode()
        if method == "GET" and path.split("?", 1)[0] == "/lineage/recent":
            records = [record]
            if self.cross_attributed and key == "t2":
                records.append({"lineage_id": "L-t1", "accountable_identity": LOGINS["t1"]})
            return 200, json.dumps({"records": records, "total": len(records)}).encode()
        return 404, b"{}"


def _run_verifier(monkeypatch, capsys, tmp_path, argv, fake) -> tuple[int, list[str]]:
    from scripts import verify_deploy as vd
    for key in KEYS:
        (tmp_path / "backups" / key / "20261112T033000Z").mkdir(parents=True, exist_ok=True)
        (tmp_path / "backups" / key / "20261112T033000Z" / "MANIFEST.json").write_text("{}", encoding="utf-8")
        monkeypatch.setenv(f"NEXUS_{key.upper()}_OWNER", LOGINS[key])
    monkeypatch.setattr(vd, "docker", fake.docker)
    monkeypatch.setattr(vd.Console, "fetch", lambda self, *a, **k: fake.fetch(self, *a, **k))
    capsys.readouterr()
    code = vd.main(["--profile", "hosted", "--backup-dir", str(tmp_path / "backups"), *argv])
    return code, capsys.readouterr().out.splitlines()


#: The checks that go through the identity proxy, which only the stand-in lets the verifier pass.
THROUGH_THE_PROXY = ("V2", "V3", "V4", "V5", "V11", "V12", "V13")


def test_h4_without_the_stand_in_the_tester_requests_are_skipped_by_name_and_never_counted(monkeypatch, capsys,
                                                                                           tmp_path):
    """H4: hosted without --identity-sim (a real host, whose identity proxy
    the verifier cannot pass as a person): the tester services come from the
    compose file; every request through the identity proxy is SKIPPED by
    name, sends nothing, and is never counted; the docker-side checks run
    per tester; the sessions are MEASURED and not counted; exit 0. The
    companion: one relay that lets another tester's process through is
    named and fails the run. Law 16: the verifier has no hosted path before
    (it reads one service, `nexus-hosted`, and wants a token)."""
    from scripts import verify_deploy as vd
    code, lines = _run_verifier(monkeypatch, capsys, tmp_path, [], fake := FakeDeployment(vd))
    print("\n".join(lines))
    verdicts = [l for l in lines if l.startswith(("PASS", "FAIL"))]
    skipped = [l for l in lines if l.startswith("SKIP")]
    tally = lines[-1]
    assert code == 0, lines
    assert fake.fetches == [], f"a request went out with no stand-in: {fake.fetches}"
    for check in THROUGH_THE_PROXY:
        assert any(l.startswith(f"SKIP {check} ") for l in skipped), f"{check} is not skipped by name"
        assert not any(l.split(":")[0].split()[1] == check for l in verdicts), f"{check} reached the tally"
    assert re.fullmatch(rf"verify_deploy: {len(verdicts)} of {len(verdicts)} checks passed .*", tally), tally
    assert all(c in tally.split("skipped, not counted:")[1] for c in THROUGH_THE_PROXY), tally
    for key in KEYS:
        for check in ("V1", "V6", "V7", "V8", "V9", "V10", "V14"):
            assert any(l.startswith(f"PASS {check} ") and key in l for l in verdicts), (check, key)
    assert any(l.startswith("MEASURED sessions") for l in lines)

    code, lines = _run_verifier(monkeypatch, capsys, tmp_path, [], FakeDeployment(vd, relay_status=200))
    failed = [l for l in lines if l.startswith("FAIL")]
    print("\n".join(failed + lines[-1:]))
    assert code == 1 and failed and all(l.startswith("FAIL V14") for l in failed), failed
    assert re.match(rf"verify_deploy: {sum(l.startswith('PASS') for l in lines)} of "
                    rf"{sum(l.startswith(('PASS', 'FAIL')) for l in lines)} checks passed", lines[-1]), lines[-1]


def test_h4_with_the_stand_in_every_tester_request_runs_and_a_cross_attribution_is_named(monkeypatch, capsys,
                                                                                          tmp_path):
    """H4 with --identity-sim (the deploy smoke): through the stand-in's
    ports, per tester, whoami, a turn, the record's login and mechanism, the
    chain; across them three distinct identities and no record of one tester
    in another's store; the mismatch and no-identity refusals. The client's
    own copy of the identity header is planted on the tester and no-identity
    requests, so the stand-in replacing and removing it is part of what
    passes. The companion: tester 2's store holding tester 1's record fails
    V13 for tester 2 by name. With --remote the stand-in's loopback ports
    are not reachable, and the run is refused by name (exit 2)."""
    from scripts import verify_deploy as vd
    code, lines = _run_verifier(monkeypatch, capsys, tmp_path, ["--identity-sim"], fake := FakeDeployment(vd))
    print("\n".join(lines))
    verdicts = [l for l in lines if l.startswith(("PASS", "FAIL"))]
    assert code == 0 and not [l for l in verdicts if l.startswith("FAIL")], verdicts
    for check in THROUGH_THE_PROXY:
        assert any(l.startswith(f"PASS {check} ") for l in verdicts), f"{check} did not run"
    assert not [l for l in lines if l.startswith("SKIP V")], lines
    planted_header = [h for _b, _m, _p, h in fake.fetches if h]
    assert planted_header, "no request carried a planted identity header for the stand-in to replace or remove"

    code, lines = _run_verifier(monkeypatch, capsys, tmp_path, ["--identity-sim"],
                                FakeDeployment(vd, cross_attributed=True))
    failed = [l for l in lines if l.startswith("FAIL")]
    print("\n".join(failed))
    assert code == 1 and len(failed) == 1 and failed[0].startswith("FAIL V13") and "t2" in failed[0], failed

    capsys.readouterr()
    code = vd.main(["--profile", "hosted", "--identity-sim", "--remote", "ssh deploy@host.example"])
    said = capsys.readouterr().out
    assert code == 2 and said.startswith("REFUSED") and "--identity-sim" in said, said


# --- H5: the deploy smoke's hosted job, read from the workflow --------------------------------------------

def smoke_hosted_problems(wf: dict, main: dict, override: dict) -> list[str]:
    """H5's shape, read from deploy-smoke.yml (the run itself is CI's): its
    hosted job brings the stack up with the override (COMPOSE_FILE names
    both files, for that job alone), gives every tester's login the override
    presents (each NEXUS_<KEY>_OWNER, distinct and non-empty), makes each
    tester's backup folder for the image's user, waits for every NEXUS
    service the profile runs (asked of compose, never one container by
    name), and runs the verifier with --identity-sim."""
    smoke = (wf.get("jobs") or {}).get("smoke") or {}
    steps = smoke.get("steps") or []
    texts = [str(s.get("run") or "") for s in steps]
    hosted_texts = [str(s.get("run") or "") for s in steps if "hosted" in str(s.get("if") or "")]
    problems = []
    compose_files = {m for t in hosted_texts for m in re.findall(r'COMPOSE_FILE=([^\s"]+)', t)}
    want = f"{COMPOSE.name}:{OVERRIDE.relative_to(REPO).as_posix()}"
    if compose_files != {want}:
        problems.append(f"the hosted job's COMPOSE_FILE is {sorted(compose_files)}, not {want} (written for the hosted "
                        f"job alone)")
    env = {str(k): str(v) for k, v in (smoke.get("env") or {}).items()}
    for text in hosted_texts:
        for m in re.finditer(r'echo "([A-Z0-9_]+)=([^"$]+)"', text):
            env.setdefault(m.group(1), m.group(2))
    owners = {}
    for name in sorted(hosted_testers(main)):
        variable = f"NEXUS_{name[len(NEXUS_PREFIX):].upper()}_OWNER"
        owners[variable] = env.get(variable, "").strip()
        if not owners[variable]:
            problems.append(f"the smoke gives no login for {variable}, which the stand-in presents")
        key = name[len(NEXUS_PREFIX):]
        if not any(f'"$NEXUS_BACKUP_DIR/{key}"' in t and "install -d" in t for t in texts):
            problems.append(f"the smoke makes no backup folder for tester {key} ($NEXUS_BACKUP_DIR/{key})")
    given = [v for v in owners.values() if v]
    if len(set(given)) != len(given):
        problems.append(f"the smoke's logins are not distinct: {owners}")
    if not any("config --services" in t and "healthy" in t for t in texts):
        problems.append("the smoke does not wait for every NEXUS service the profile runs (config --services)")
    if not any(VERIFIER in t and "--identity-sim" in t for t in texts):
        problems.append("the smoke does not run the verifier with --identity-sim under hosted")
    return problems


def test_h5_the_smoke_brings_the_testers_up_behind_the_stand_in():
    """H5's shape, static (the run is CI's deploy-smoke on ubuntu-24.04,
    and the local proof in the build's evidence). Law 16: the pre-part-3
    smoke waits for one container by name and knows no stand-in."""
    wf = yaml.safe_load(SMOKE.read_text(encoding="utf-8"))
    problems = smoke_hosted_problems(wf, compose_file(), override_file())
    print(f"\nH5 problems {problems}")
    hold(problems)


def test_h5_companions():
    wf = yaml.safe_load(SMOKE.read_text(encoding="utf-8"))
    main, override = compose_file(), override_file()
    hold(smoke_hosted_problems(wf, main, override))
    planted = json.loads(json.dumps(wf))
    for step in planted["jobs"]["smoke"]["steps"]:
        step["run"] = str(step.get("run") or "").replace("--identity-sim", "")
    assert "--identity-sim" in refused(smoke_hosted_problems(planted, main, override))
    planted = json.loads(json.dumps(wf))
    variable = sorted(k for k in planted["jobs"]["smoke"].get("env") or {} if k.endswith("_OWNER"))[-1]
    planted["jobs"]["smoke"]["env"][variable] = planted["jobs"]["smoke"]["env"][sorted(
        k for k in planted["jobs"]["smoke"]["env"] if k.endswith("_OWNER"))[0]]
    assert "not distinct" in refused(smoke_hosted_problems(planted, main, override))
