"""W3 part 4 — backup, restore verification, the deploy verifier and the
compose `backup` service (run/specs/W3.md S2, D5; RUN_WORKLIST W3 item 6:
restore verification is a product feature).

B1  scripts/backup.py on a copy of the fixture store: one folder per run
    with kb.db, archive.db, the snapshots and a manifest; the manifest's
    counts, chain head and lineage digest equal the source's (read through
    the read-only open); every copied database passes integrity_check; the
    source's bytes are unchanged and its lock file is not copied; a second
    backup in the same second gets a suffix, never an overwrite; the first
    line names the data root as read-only; a folder that is not a NEXUS
    store is refused (exit 2).
B2  A backup of a RUNNING store: with the services open and holding W2's
    lock, and a claim written between, the backup copies and the manifest
    records the holder.
B3  scripts/restore_verify.py on that backup passes its five checks (exit
    0) and leaves the backup's bytes as they were; a backup whose kb.db was
    altered after the manifest is named by R1 (exit 1); a folder with no
    manifest is refused (exit 2); `--latest-under` picks the newest folder.
B4  `--keep N` removes the oldest backup folders beyond N and names them; a
    folder without a manifest is never removed; `--keep 0` is refused.
B5  docker-compose.yml's `backup` service (through `docker compose config`,
    skipped by name without the docker CLI): the `backup` profile, no
    network, a read-only root, the data volume mounted READ-ONLY, the
    backup directory mounted, `scripts/backup.sh` as its entrypoint, the
    NEXUS image; the deploy test's pin rule exempts the NEXUS image.
B6  scripts/verify_deploy.py's verdicts without a deployment: the done
    frame parsed from an SSE body; the egress verdict over a planted probe
    result (ENETUNREACH everywhere, the sibling connected and the network
    isolated passes; a default route, a timeout or an un-isolated gateway
    is named); the probe text runs and reports the shape it declares.
B6b The store's role (V9; W4 part 0, run/specs/W4.md D14, BACKLOG W3b-F8)
    without a deployment: the verdict over the role probe's rows. A tester
    role that holds none of superuser, CREATEDB, CREATEROLE, REPLICATION
    and BYPASSRLS, belongs to no predefined role but pg_database_owner (the
    owner's implicit membership, accepted only where it owns the database)
    and whose database PUBLIC may not connect to passes; a planted row is
    named for each way it can fail: the superuser W3b's deployment
    connected as, each attribute, a predefined role the verdict's source
    does not name, pg_database_owner without the ownership, an attribute
    the probe did not read (unknown is never off), a current user apart
    from the session user, PUBLIC's CONNECT, and a probe that could not
    read. Under a SQLite profile V9 is SKIPPED by name and never counted;
    under hosted its result is read from `docker compose exec` (planted
    here). Law 16: V9 does not exist before (a new name; declared).
B7  scripts/backup.sh passes the data root, the destination and the
    retention through to backup.py (a stand-in `python` on PATH records
    the arguments), and an empty NEXUS_BACKUP_KEEP passes no --keep; `sh -n`
    parses it and, where shellcheck is on PATH (CI's ubuntu runner; the run
    fetched it), shellcheck finds nothing (its findings are the message).
B8  verify_deploy's V6 reads the backup's manifest on the host as whoever
    runs the verifier. The backups directory is the image's uid 10001's,
    mode 0750 (the deploy's own posture, cloud-init's `install -d`), so a
    user who is not root may not look inside it: that is `None`, never
    False, and V6 then reads the manifest through the backup service's own
    mount and says so (law 5: cannot look is not nothing found). Found by
    CI's first deploy-smoke run, which ran as the runner user where the
    sandbox's proof had run as root.

Every store here is a copy of tests/fixtures/pre_run_data under tmp_path;
nothing under data/ is read or written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
from tests import _proc
import sys
from pathlib import Path

import pytest

from tests._tracked import REPO
from tests.test_w3_deploy import compose_config

FIXTURE = REPO / "tests" / "fixtures" / "pre_run_data"
SCRIPTS = REPO / "scripts"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _store(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    shutil.copytree(FIXTURE, root)
    (root / "MANIFEST.json").unlink()                 # the fixture's own manifest is not the instance's
    return root


def _run(script: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return _proc.run([sys.executable, str(SCRIPTS / script), *args], cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300,
                          env={**os.environ, "NEXUS_ENV_ROOT": str(REPO / "tests" / "fixtures"), **(env or {})})


# --- B1, B2: the backup ------------------------------------------------------------

def test_b1_a_backup_is_a_folder_with_a_manifest_that_describes_the_source(tmp_path):
    from scripts import backup
    from scripts._readonly import open_read_only
    root = _store(tmp_path)
    before = {p.name: _sha(p) for p in root.glob("*.db")}
    out = tmp_path / "backups"
    proc = _run("backup.py", "--data-root", str(root), "--out", str(out))
    print(f"B1 exit {proc.returncode}\n{proc.stdout}")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.splitlines()[0].startswith(f"data root: {root.as_posix()} (read only")
    (folder,) = [p for p in out.iterdir() if p.is_dir()]
    manifest = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
    assert {"kb.db", "archive.db", "MANIFEST.json", "snapshots"} <= {p.name for p in folder.iterdir()}
    assert "kb.db.lock" not in manifest["files"] and not (folder / "kb.db.lock").exists(), "the running process's lock was copied"
    assert set(manifest["integrity_check"].values()) == {"ok"}
    for name in ("kb.db", "archive.db"):
        src = open_read_only(root / name)
        try:
            assert backup.table_counts(src) == manifest["counts"][name], name
            if name == "archive.db":
                assert backup.chain_head(src) == manifest["chain"]
            else:
                assert backup.lineage_digest(src) == manifest["lineage"]
        finally:
            src.close()
    for rel, digest in manifest["files"].items():
        assert _sha(folder / rel) == digest, rel
    assert {p.name: _sha(p) for p in root.glob("*.db")} == before, "the backup changed the source"
    assert manifest["lineage"]["count"] >= 1 and manifest["chain"]["blocks"] >= 1
    # The same second: a suffix, never an overwrite.
    f1, _m1, _ = backup.take_backup(root, out, now=1_700_000_000.0)       # 2023-11-14T22:13:20Z
    f2, _m2, _ = backup.take_backup(root, out, now=1_700_000_000.0)
    assert f1.name == "20231114T221320Z" and f2.name == "20231114T221320Z-2" and f1 != f2
    # Not a store: refused by name, nothing written.
    proc = _run("backup.py", "--data-root", str(tmp_path / "nowhere"), "--out", str(tmp_path / "b2"))
    assert proc.returncode == 2 and "REFUSED" in proc.stdout and not (tmp_path / "b2").exists()


def test_b2_a_running_store_is_backed_up_and_the_holder_recorded(tmp_path):
    from backend.data_root import paths_under
    from backend.kb.granularity import Granularity
    from backend.server import create_services
    from scripts import backup
    root = _store(tmp_path)
    services = create_services(**paths_under(root))
    try:
        services.store.add(subject="interval training", relation="improves", object="VO2 max", weight=0.9, tier=1,
                           granularity=Granularity.TOPIC, source="test")
        folder, manifest, failures = backup.take_backup(root, tmp_path / "backups")
    finally:
        services.store_lock.release()
    print(f"B2 holder {manifest['lock_holder_at_backup']}, triples {manifest['counts']['kb.db'].get('triples')}")
    assert failures == [] and manifest["lock_holder_at_backup"]["pid"] == os.getpid()
    assert manifest["lock_holder_at_backup"]["released"] is False
    assert (folder / "kb.db").is_file() and manifest["counts"]["kb.db"]["triples"] >= 1


# --- B3, B4: the restore verification and the retention -----------------------------

def test_b3_restore_verify_passes_on_a_backup_and_names_an_altered_one(tmp_path):
    root = _store(tmp_path)
    out = tmp_path / "backups"
    assert _run("backup.py", "--data-root", str(root), "--out", str(out)).returncode == 0
    (folder,) = [p for p in out.iterdir() if p.is_dir()]
    before = {p.relative_to(folder).as_posix(): _sha(p) for p in folder.rglob("*") if p.is_file()}
    proc = _run("restore_verify.py", str(folder), "--into", str(tmp_path / "scratch"))
    print(f"B3 exit {proc.returncode}\n{proc.stdout}")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0].startswith(f"backup: {folder.as_posix()} (read only")
    assert sum(l.startswith("PASS R") for l in lines) == 5 and not any(l.startswith("FAIL") for l in lines)
    assert "5 of 5 checks passed" in proc.stdout
    assert {p.relative_to(folder).as_posix(): _sha(p) for p in folder.rglob("*") if p.is_file()} == before, \
        "the verification changed the backup"
    assert (tmp_path / "scratch" / "restored" / "kb.db").is_file()
    # --latest-under picks the newest folder.
    proc = _run("restore_verify.py", "--latest-under", str(out))
    assert proc.returncode == 0 and f"backup: {folder.as_posix()}" in proc.stdout
    # An altered copy: a byte flipped in the backup's kb.db after the manifest.
    altered = tmp_path / "altered"
    shutil.copytree(folder, altered)
    data = bytearray((altered / "kb.db").read_bytes())
    data[-1] ^= 0xFF
    (altered / "kb.db").write_bytes(bytes(data))
    proc = _run("restore_verify.py", str(altered))
    print(f"B3 altered: exit {proc.returncode}: {[l for l in proc.stdout.splitlines() if l.startswith('FAIL')]}")
    assert proc.returncode == 1 and "FAIL R1 files" in proc.stdout and "differing ['kb.db']" in proc.stdout
    # No manifest: refused.
    proc = _run("restore_verify.py", str(root))
    assert proc.returncode == 2 and "REFUSED" in proc.stdout


def test_b4_keep_removes_the_oldest_manifest_folders_only(tmp_path):
    from scripts import backup
    root = _store(tmp_path)
    out = tmp_path / "backups"
    for n in range(4):
        backup.take_backup(root, out, now=1_700_000_000.0 + 60 * n)   # 2023-11-14T22:13:20Z and the three minutes after
    (out / "20231114T230000Z").mkdir()                 # a folder this script did not write: no manifest
    (out / "20231114T230000Z" / "note.txt").write_text("not a backup", encoding="utf-8")
    removed = backup.prune(out, 2)
    names = sorted(p.name for p in out.iterdir())
    print(f"B4 removed {[p.name for p in removed]}, left {names}")
    assert [p.name for p in removed] == ["20231114T221320Z", "20231114T221420Z"]
    assert names == ["20231114T221520Z", "20231114T221620Z", "20231114T230000Z"]
    proc = _run("backup.py", "--data-root", str(root), "--out", str(out), "--keep", "0")
    assert proc.returncode == 2 and "at least one backup is kept" in proc.stdout
    proc = _run("backup.py", "--data-root", str(root), "--out", str(out), "--keep", "2")   # a new folder, stamped now
    assert proc.returncode == 0 and "removed: 20231114T221520Z" in proc.stdout, proc.stdout


# --- B5: the compose backup service ------------------------------------------------------

def test_b5_the_compose_backup_service_reads_the_volume_read_only_on_the_internal_network():
    from tests.test_w3_deploy import compose_problems
    cfg = compose_config()
    if cfg is None:
        pytest.skip("the docker CLI is not on PATH: `docker compose config` is how the file is read")
    svc = cfg["services"].get("backup")
    assert svc is not None, "no `backup` service"
    print(f"B5 backup service: {json.dumps(svc, sort_keys=True)[:400]}")
    # W3b part 5 (D11): the internal network only, where it had none: a hosted
    # store's backup reaches the database by name; no route out either way.
    assert svc.get("profiles") == ["backup"] and set(svc.get("networks") or {}) == {"internal"} \
        and not svc.get("network_mode") and svc.get("read_only") is True
    mounts = {(v.get("source"), v.get("target"), bool(v.get("read_only"))) for v in svc.get("volumes") or []}
    assert ("nexus_data", "/var/lib/nexus", True) in mounts, mounts
    assert any(t == "/var/backups/nexus" and not ro for _s, t, ro in mounts), mounts
    assert svc.get("entrypoint") == ["sh", "scripts/backup.sh"]
    assert svc.get("image") == cfg["services"]["nexus-stub"]["image"], "the backup runs the NEXUS image"
    assert compose_problems(cfg) == [], compose_problems(cfg)


# --- B6: the deploy verifier's verdicts --------------------------------------------------

def test_b6_the_deploy_verifiers_verdicts_without_a_deployment(tmp_path, capsys):
    from scripts import verify_deploy as vd
    body = ("event: turn_start\ndata: {\"turn\": 1}\n\nevent: token\ndata: {\"t\": \"x\"}\n\n"
            "event: done\ndata: {\"lineage_id\": \"abc\", \"learning\": {\"state\": \"frozen\"}}\n\n").encode()
    assert vd.sse_done(body) == {"lineage_id": "abc", "learning": {"state": "frozen"}}
    assert vd.sse_done(b"event: token\ndata: {\"t\": \"x\"}\n\n") is None
    refused = {"result": "OSError", "errno": 101, "name": "ENETUNREACH", "seconds": 0.0}
    good = {"routes": [["eth0", "172.19.0.0", "0.0.0.0", "255.255.0.0"]], "default_route": False,
            "public": {"1.1.1.1:443": refused, "8.8.8.8:53": refused}, "sibling": {"result": "connected", "seconds": 0.001}}
    ok, detail = vd.egress_verdict(good, ["true", "isolated", "invalid"])
    assert ok, detail
    with_route = {**good, "default_route": True}
    assert not vd.egress_verdict(with_route, ["true", "isolated", "invalid"])[0]
    timed_out = {**good, "public": {**good["public"], "8.8.8.8:53": {"result": "TimeoutError", "errno": None, "name": None, "seconds": 5.0}}}
    assert not vd.egress_verdict(timed_out, ["true", "isolated", "invalid"])[0], "a timeout passed as a refusal"
    assert not vd.egress_verdict(good, ["true", "nat", "172.19.0.1"])[0], "an un-isolated gateway passed"
    assert not vd.egress_verdict({**good, "sibling": {"result": "gaierror"}}, ["true", "isolated", "invalid"])[0]
    # Under the api profile V8 does not run: SKIP, said, and not a verdict in
    # the tally (the W3 verifier's F2: it was a PASS counted among N of N).
    args = argparse.Namespace(compose_file="docker-compose.yml", project="nexus", extra_profiles=[], remote=None,
                              remote_dir="/opt/nexus")
    verdict = vd.check_egress(args, "nexus-api", "api")
    said = capsys.readouterr().out
    assert verdict is None and said.startswith("SKIP V8 egress") and "declared" in said, (verdict, said)
    assert "PASS" not in said
    # The probe text runs: a closed loopback port in the public set, a listening one as the sibling.
    # It reads the kernel's route table at /proc/net/route, because it runs
    # inside the Linux container; off Linux its local run has nothing to read
    # and is not attempted (CI's windows and macos runners, found by their
    # first run of this test).
    if not Path("/proc/net/route").is_file():
        print("B6 the probe text's own run is Linux-only (/proc/net/route): not attempted here, by name")
        return
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        closed_port = closed.getsockname()[1]
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        probe = (vd.EGRESS_PROBE.replace("PUBLIC", repr((("127.0.0.1", closed_port),)))
                 .replace("SIBLING_PORT", str(listener.getsockname()[1])).replace("SIBLING", repr("127.0.0.1")))
        proc = _proc.run([sys.executable, "-"], input=probe, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
    finally:
        listener.close()
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    print(f"B6 probe: {out}")
    assert set(out) == {"routes", "default_route", "public", "sibling"} and out["sibling"]["result"] == "connected"
    assert out["public"][f"127.0.0.1:{closed_port}"]["name"] == "ECONNREFUSED"


# --- B6b: the store's role (V9) ------------------------------------------------------------

def test_b6b_the_store_role_check_v9_without_a_deployment(capsys, monkeypatch):
    import inspect
    from scripts import verify_deploy as vd
    tester = {"role": "nexus_t1", "is_session_user": True, "is_current_user": True, "superuser": False,
              "createdb": False, "createrole": False, "replication": False, "bypassrls": False,
              "predefined": ["pg_database_owner"], "owns_database": True}
    good = {"database": "nexus_t1", "roles": [tester], "public_connect": False}
    ok, detail = vd.store_role_verdict(good)
    print(f"B6b the tester role: {ok}: {detail}")
    assert ok, detail
    assert "nexus_t1" in detail and "pg_database_owner" in detail, detail

    def planted(**row) -> dict:
        return {**good, "roles": [{**tester, **row}]}

    # The role W3b's hosted profile connected as: POSTGRES_USER `nexus`, which the
    # image makes SUPERUSER (initdb gives it every attribute), a member of every role.
    w3b = planted(role="nexus", superuser=True, createdb=True, createrole=True, replication=True, bypassrls=True,
                  predefined=["pg_read_server_files", "pg_signal_backend", "pg_write_server_files"])
    ok, detail = vd.store_role_verdict({**w3b, "database": "nexus"})
    print(f"B6b W3b's role: {ok}: {detail}")
    assert not ok and "nexus" in detail and "superuser" in detail.lower(), detail
    for attribute in vd.ROLE_ATTRIBUTES:
        ok, detail = vd.store_role_verdict(planted(**{attribute: True}))
        assert not ok and attribute in detail.lower(), (attribute, detail)
    for member in ("pg_write_server_files", "pg_execute_server_program", "pg_monitor"):
        ok, detail = vd.store_role_verdict(planted(predefined=["pg_database_owner", member]))
        assert not ok and member in detail, (member, detail)
    ok, detail = vd.store_role_verdict(planted(owns_database=False))
    assert not ok and "pg_database_owner" in detail, detail
    unknown = planted()
    del unknown["roles"][0]["bypassrls"]
    ok, detail = vd.store_role_verdict(unknown)
    assert not ok and "bypassrls" in detail.lower(), detail
    current = {**tester, "role": "nexus_admin", "is_session_user": False, "createrole": True, "predefined": [],
               "owns_database": False}
    ok, detail = vd.store_role_verdict({**good, "roles": [{**tester, "is_current_user": False}, current]})
    assert not ok and "nexus_admin" in detail and "createrole" in detail.lower(), detail
    ok, detail = vd.store_role_verdict({**good, "public_connect": True})
    assert not ok and "PUBLIC" in detail, detail
    for broken in ({"error": "OperationalError: connection refused"}, {"database": "x", "roles": []}, {}):
        ok, detail = vd.store_role_verdict(broken)
        assert not ok, (broken, detail)
    source = inspect.getsource(vd.store_role_verdict)
    for plant in ("pg_write_server_files", "pg_execute_server_program", "pg_monitor", "nexus_admin"):
        assert plant not in source, f"the verdict's source names the plant {plant!r}"

    # Under a SQLite profile: SKIP, said, and not a verdict.
    args = argparse.Namespace(compose_file="docker-compose.yml", project="nexus", extra_profiles=[], remote=None,
                              remote_dir="/opt/nexus")
    capsys.readouterr()
    assert vd.check_store_role(args, "nexus-stub", "stub") is None
    said = capsys.readouterr().out
    assert said.startswith("SKIP V9") and "PASS" not in said and "FAIL" not in said, said
    # Under hosted: the probe's text goes to `docker compose exec` on stdin, nothing else on its command line.
    calls: list[tuple] = []

    def fake(result, code=0):
        def run(_args, *rest, **kw):
            calls.append((rest, kw.get("input_text")))
            return subprocess.CompletedProcess(rest, code, stdout=json.dumps(result) + "\n", stderr="")
        return run

    monkeypatch.setattr(vd, "compose", fake(good))
    assert vd.check_store_role(args, "nexus-hosted", "hosted") is True
    (rest, fed), = calls
    assert rest == ("exec", "-T", "nexus-hosted", "python", "-") and fed == vd.ROLE_PROBE, rest
    assert capsys.readouterr().out.startswith("PASS V9")
    monkeypatch.setattr(vd, "compose", fake({**w3b, "database": "nexus"}))
    assert vd.check_store_role(args, "nexus-hosted", "hosted") is False
    said = capsys.readouterr().out
    assert said.startswith("FAIL V9") and "superuser" in said.lower(), said
    monkeypatch.setattr(vd, "compose", fake({}, code=1))
    assert vd.check_store_role(args, "nexus-hosted", "hosted") is False
    assert capsys.readouterr().out.startswith("FAIL V9")


# --- B7: backup.sh ------------------------------------------------------------------------

def test_b7_backup_sh_passes_the_root_the_destination_and_the_retention(tmp_path):
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("no sh on this platform")
    stand_in = tmp_path / "bin"
    stand_in.mkdir()
    (stand_in / "python").write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$RECORD\"\n", encoding="utf-8")
    (stand_in / "python").chmod(0o755)
    record = tmp_path / "argv.txt"
    extra = {"PATH": f"{stand_in}{os.pathsep}{os.environ['PATH']}", "RECORD": str(record),
             "NEXUS_DATA_ROOT": "/var/lib/nexus", "NEXUS_BACKUP_DIR": "/var/backups/nexus"}
    proc = _proc.run([sh, str(SCRIPTS / "backup.sh"), "--extra"], env={**os.environ, **extra}, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=60)
    argv = record.read_text(encoding="utf-8").split("\n")
    print(f"B7 argv {argv}")
    assert proc.returncode == 0 and argv[0].endswith("scripts/backup.py")
    assert argv[1:9] == ["--data-root", "/var/lib/nexus", "--out", "/var/backups/nexus", "--extra", "--keep", "14", ""]
    proc = _proc.run([sh, str(SCRIPTS / "backup.sh")], env={**os.environ, **extra, "NEXUS_BACKUP_KEEP": ""},
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    argv = record.read_text(encoding="utf-8").split("\n")
    assert proc.returncode == 0 and "--keep" not in argv
    assert _proc.run([sh, "-n", str(SCRIPTS / "backup.sh")], capture_output=True, timeout=600).returncode == 0
    checker = shutil.which("shellcheck")
    if checker:
        lint = _proc.run([checker, str(SCRIPTS / "backup.sh")], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
        assert lint.returncode == 0, f"shellcheck exit {lint.returncode}:\n{lint.stdout}{lint.stderr}"


# --- B8: V6 reads the host folder as whoever runs the verifier ----------------------------

def test_b8_v6_cannot_look_is_never_missing(tmp_path, monkeypatch):
    from scripts import verify_deploy as vd
    folder = tmp_path / "backups" / "20261108T030000Z"
    folder.mkdir(parents=True)
    container_folder = "/var/backups/nexus/20261108T030000Z"
    args = argparse.Namespace(compose_file="docker-compose.yml", project="nexus", extra_profiles=[])
    calls: list[tuple] = []

    def fake_compose(_args, *rest, **_kw):
        calls.append(rest)
        return subprocess.CompletedProcess(rest, 0 if (folder / "MANIFEST.json").is_file() else 3, stdout="", stderr="")

    monkeypatch.setattr(vd, "compose", fake_compose)
    # This user can look: the host answers, the service is not asked.
    assert vd.host_manifest_state(folder) is False
    ok, detail = vd.manifest_on_host(args, container_folder, folder)
    assert not ok and "MISSING" in detail and "on the host" in detail and calls == [], (detail, calls)
    (folder / "MANIFEST.json").write_text("{}", encoding="utf-8")
    assert vd.host_manifest_state(folder) is True
    ok, detail = vd.manifest_on_host(args, container_folder, folder)
    assert ok and "present" in detail and "MISSING" not in detail and calls == [], (detail, calls)
    print(f"B8 readable: {detail}")

    # This user may not look (the directory is uid 10001's, mode 0750): None,
    # and the manifest is read through the backup service's mount.
    def forbidden(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "is_file", forbidden)
    assert vd.host_manifest_state(folder) is None
    monkeypatch.setattr(vd, "compose", lambda _a, *rest, **_k: (calls.append(rest),
                                                                subprocess.CompletedProcess(rest, 0, stdout="", stderr=""))[1])
    ok, detail = vd.manifest_on_host(args, container_folder, folder)
    print(f"B8 unreadable: {detail}")
    assert ok and "through the backup service" in detail and "may not read" in detail and "MISSING" not in detail, detail
    assert len(calls) == 1 and "--entrypoint" in calls[0] and f"{container_folder}/MANIFEST.json" in calls[0], calls
    monkeypatch.setattr(vd, "compose", lambda _a, *rest, **_k: subprocess.CompletedProcess(rest, 3, stdout="", stderr=""))
    ok, detail = vd.manifest_on_host(args, container_folder, folder)
    assert not ok and "MISSING" in detail and "through the backup service" in detail, detail


# --- B9: the docker calls on the deploy host over SSH ----------------------------------------

def test_b9_the_docker_calls_go_to_the_host_over_ssh_when_asked(tmp_path, monkeypatch):
    """deploy.yml verifies a host from the runner: V1 to V5 over HTTPS, V6 to
    V8 through `docker compose` on the host over SSH, the same code CI's
    smoke runs beside the stack. `--remote` is the SSH command, `--remote-dir`
    the compose project's directory there; every docker call is one remote
    command, each word quoted for the host's shell; with no `--remote` the
    argv is the local one. The host's backups directory is not this
    machine's, so V6 reads the manifest through the service and says why."""
    from scripts import verify_deploy as vd
    local = argparse.Namespace(remote=None, remote_dir="/opt/nexus", compose_file="docker-compose.yml",
                               project="nexus", extra_profiles=[])
    assert vd.docker_argv(local, "compose", "ps") == ["docker", "compose", "ps"]
    remote = argparse.Namespace(remote="ssh -i /keys/deploy key user@host.example", remote_dir="/opt/nexus",
                                compose_file="docker-compose.yml", project="nexus", extra_profiles=[])
    template = '{{.Internal}} {{index .Options "x"}}'
    argv = vd.docker_argv(remote, "network", "inspect", "nexus_internal", "-f", template)
    print(f"B9 {argv}")
    assert argv[:5] == ["ssh", "-i", "/keys/deploy", "key", "user@host.example"]
    assert argv[5] == "cd /opt/nexus && docker network inspect nexus_internal -f '{{.Internal}} {{index .Options \"x\"}}'"
    assert len(argv) == 6
    calls: list[list] = []

    def fake_run(argv, **kw):
        calls.append((argv, kw.get("input")))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(vd.subprocess, "run", fake_run)
    vd.compose(remote, "exec", "-T", "nexus-stub", "python", "-", profile="stub", input_text="print(1)")
    (argv, fed), = calls
    assert argv[:5] == ["ssh", "-i", "/keys/deploy", "key", "user@host.example"] and fed == "print(1)"
    assert argv[5] == "cd /opt/nexus && docker compose -f docker-compose.yml -p nexus --profile stub exec -T nexus-stub python -"
    calls.clear()
    ok, detail = vd.manifest_on_host(remote, "/var/backups/nexus/20261108T030000Z", tmp_path / "never-here")
    assert ok and "through the backup service" in detail and "remote" in detail and "MISSING" not in detail, detail
    assert calls and "/var/backups/nexus/20261108T030000Z/MANIFEST.json" in calls[0][0][5], calls
