"""W2 part 3 — the bundle (run/specs/W2.md S2, D1 to D3 and D6; S1's G2).

B1  The manifest is what the bundle needs (`manifest_problems` finds
    nothing), its tools are the server's, in its order, and the icon is
    `make_icon()`'s output.
B2  The win32 override repeats the whole env (D6: the MCPB reference
    implementation replaces `env` wholesale, so an override that names its
    own env loses every key it leaves out). Planted: an override missing a
    key, and one changing a key; each is named, `--check` refuses it with
    exit 2, and the launch the host builds from it has lost the key.
B3  The bundle fixes learning and the `.env` search (G2: a `.env` in the
    host's working directory turned a bundle's learning live). The source
    entry, launched the way the host launches the binary, beside a `.env`
    saying NEXUS_LEARNING=live, reads no `.env`, is frozen, records the
    configured owner and writes nothing in its working directory. Planted:
    the same launch with the search pointed at the working directory and
    learning not fixed turns live.
B4  The zip is the same bytes however its tree was made (order, times,
    umask); a file no bundle may carry stops the build by name, exit 5; a
    module no bundle may freeze (`pkg_resources`: PyInstaller's run-time
    hook for it needs an API setuptools >= 81 removed, and CI's macOS
    bundle died at launch on it three times) is excluded at analysis and,
    read from PyInstaller's PYZ table of contents, stops the build by name,
    exit 5; no table is a refusal (exit 3), never a clean bundle.
B5  Stamping serves one platform and changes nothing else.
B6  `host_launch` is the reference behaviour the smoke emulates: an
    override replaces each key it names, wholesale.
B7  The build tool is pinned, and installed at its pin.

The binary itself is built by `scripts/build_bundle.py` (minutes, about
260 MB), not here; part 4's smoke drives it, in CI's `bundle` job.
"""
from __future__ import annotations

import copy
import json
import os
import re
import stat
import subprocess
from tests import _proc
import sys
import zipfile
from pathlib import Path

import pytest

from tests._tracked import REPO

sys.path.insert(0, str(REPO / "scripts"))
import build_bundle as bb  # noqa: E402

OWNER = "smoke-tester@example.org"
EXIT_MANIFEST, EXIT_CARRIES = 2, 5            # the documented literals, not the module's names


def _manifest() -> dict:
    return json.loads((REPO / "backend" / "mcp" / "bundle" / "manifest.json").read_text(encoding="utf-8"))


def _check(tmp_path: Path, manifest: dict) -> subprocess.CompletedProcess:
    planted = tmp_path / "planted"
    planted.mkdir(exist_ok=True)
    (planted / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (planted / "icon.png").write_bytes(bb.make_icon())
    return _proc.run([sys.executable, str(REPO / "scripts" / "build_bundle.py"), "--check",
                           "--manifest", str(planted / "manifest.json")], cwd=tmp_path,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


# --- B1 -------------------------------------------------------------------

def test_b1_the_manifest_is_what_the_bundle_needs(tmp_path):
    m = _manifest()
    problems = bb.manifest_problems(m)
    print(f"B1 env {m['server']['mcp_config']['env']}")
    assert problems == [], problems
    assert bb.check_files(bb.MANIFEST, bb.ICON) == []
    proc = _check(tmp_path, m)
    assert proc.returncode == 0, proc.stderr


def test_b1_the_manifests_tools_are_the_servers(tmp_path, monkeypatch):
    import asyncio
    from backend.mcp.mcp_server import NexusRuntime, build_server
    monkeypatch.setenv("NEXUS_LEARNING", "frozen")
    monkeypatch.setenv("NEXUS_LEARNING_FROZEN_SINCE", "2026-11-01")
    runtime = NexusRuntime.open(root=tmp_path / "root")
    try:
        served = [t.name for t in asyncio.run(build_server(runtime).list_tools())]
    finally:
        runtime.services.store_lock.release()
    declared = [t["name"] for t in _manifest()["tools"]]
    print(f"B1 served {served}")
    assert declared == served


def test_b1_the_icon_is_make_icons_output():
    png = bb.ICON.read_bytes()
    assert png == bb.make_icon(), "the icon is not the build script's output (--write-icon)"
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR"
    assert int.from_bytes(png[16:20], "big") == int.from_bytes(png[20:24], "big") == 256


def test_b1_the_wrappers_run_the_build_script():
    for name in ("build_bundle.sh", "build_bundle.ps1"):
        assert "scripts/build_bundle.py" in (REPO / "scripts" / name).read_text(encoding="utf-8"), name


# --- B2 -------------------------------------------------------------------

def test_b2_the_win32_override_repeats_the_whole_env(tmp_path):
    m = _manifest()
    cfg = m["server"]["mcp_config"]
    assert cfg["platform_overrides"]["win32"]["env"] == cfg["env"]
    missing, changed = copy.deepcopy(m), copy.deepcopy(m)
    del missing["server"]["mcp_config"]["platform_overrides"]["win32"]["env"]["NEXUS_LEARNING"]
    changed["server"]["mcp_config"]["platform_overrides"]["win32"]["env"]["NEXUS_ENV_ROOT"] = "${HOME}"
    for planted, words in ((missing, "missing ['NEXUS_LEARNING']"), (changed, "changed ['NEXUS_ENV_ROOT']")):
        named = [p for p in bb.manifest_problems(planted) if "win32 override's env" in p]
        print(f"B2 {named}")
        assert named and words in named[0], named
        proc = _check(tmp_path, planted)
        assert proc.returncode == EXIT_MANIFEST and "REFUSED" in proc.stderr and words in proc.stderr, proc.stderr
    # The consequence the check stops: on Windows the host's launch has lost
    # the key, so learning is no longer fixed by the bundle.
    _, _, env = bb.host_launch(missing, "win32", "C:/b", {"owner": OWNER})
    assert "NEXUS_LEARNING" not in env
    _, _, env = bb.host_launch(m, "win32", "C:/b", {"owner": OWNER})
    assert env["NEXUS_LEARNING"] == "frozen"


# --- B3 -------------------------------------------------------------------

def _host_env(home: Path, launch_env: dict) -> dict:
    """What the host gives the binary: the user's environment (no NEXUS_*
    setting of this process's), a planted home, and the manifest's env."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEXUS_")}
    env.pop("XDG_DATA_HOME", None)
    env.update(HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(home / "AppData" / "Local"),
               PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               PYTHONPATH=os.pathsep.join(filter(None, [str(REPO), os.environ.get("PYTHONPATH")])))
    env.update(launch_env)
    return env


def _launch_source(manifest: dict, tmp_path: Path, label: str) -> tuple[subprocess.CompletedProcess, Path, Path]:
    """`python -m backend.mcp` standing in for the binary, with the env the
    host builds from `manifest`, in a working directory holding a `.env`
    that says learning is live and names another owner and folder."""
    base = tmp_path / label
    bundle, cwd, data, home = base / "bundle", base / "cwd", base / "data", base / "home"
    for d in (bundle, cwd, home):
        d.mkdir(parents=True)
    (cwd / ".env").write_text(f"NEXUS_LEARNING=live\nNEXUS_MCP_OWNER=intruder\nNEXUS_MCP_DATA_ROOT={cwd / 'data'}\n",
                              encoding="utf-8")
    plat = sys.platform if sys.platform in bb.PLATFORMS else "linux"
    _command, _args, env = bb.host_launch(manifest, plat, str(bundle), {"owner": OWNER, "data_folder": str(data)})
    # Never launched without the search pinned: the child would then read the
    # repository's own .env (the operator's, on their machine).
    assert env.get("NEXUS_ENV_ROOT"), "refusing to launch with no NEXUS_ENV_ROOT"
    proc = _proc.run([sys.executable, "-m", "backend.mcp"], cwd=cwd, env=_host_env(home, env),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=240)
    return proc, cwd, data


def _startup(stderr: str) -> str:
    lines = [line for line in stderr.splitlines() if "NEXUS MCP: env files" in line]
    assert lines, stderr[-1500:]
    return lines[-1]


def test_b3_the_bundle_fixes_learning_and_the_env_search(tmp_path):
    proc, cwd, data = _launch_source(_manifest(), tmp_path, "tracked")
    line = _startup(proc.stderr)
    print(f"B3 {line}")
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert "env files []" in line, line
    assert "learning frozen (environment)" in line and f"owner {OWNER}" in line, line
    assert (data / "kb.db").is_file(), f"no store in the configured folder: {line}"
    assert sorted(p.name for p in cwd.iterdir()) == [".env"], "the process wrote in its working directory"


def test_b3_planted_a_search_that_reaches_the_working_directory_turns_learning_live(tmp_path):
    """The class G2 names, planted without the bundle's two fixes: learning
    not fixed, and the `.env` search at the working directory (pointed there
    explicitly, never unset)."""
    planted = copy.deepcopy(_manifest())
    for env in (planted["server"]["mcp_config"]["env"],
                planted["server"]["mcp_config"]["platform_overrides"]["win32"]["env"]):
        del env["NEXUS_LEARNING"]
        env["NEXUS_ENV_ROOT"] = str(tmp_path / "planted" / "cwd")
    assert any("NEXUS_LEARNING" in p for p in bb.manifest_problems(planted))
    proc, cwd, _data = _launch_source(planted, tmp_path, "planted")
    line = _startup(proc.stderr)
    print(f"B3 planted {line}")
    assert "learning live" in line and (cwd / ".env").as_posix() in line, line


# --- B4 -------------------------------------------------------------------

def _tree(root: Path, order: list[str], mode_rw: int, mode_x: int, mtime: int) -> None:
    for rel in order:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(rel.encode() * 50)
        os.chmod(p, mode_x if rel.endswith("nexus-mcp") else mode_rw)
        os.utime(p, (mtime, mtime))


def test_b4_the_zip_is_the_same_bytes_however_the_tree_was_made(tmp_path):
    files = ["manifest.json", "icon.png", "server/nexus-mcp/nexus-mcp", "server/nexus-mcp/_internal/a.so",
             "server/nexus-mcp/_internal/b/c.txt"]
    a, b = tmp_path / "a", tmp_path / "b"
    _tree(a, files, 0o644, 0o755, 1_700_000_000)
    _tree(b, list(reversed(files)), 0o600, 0o700, 1_800_000_000)
    epoch = 1_762_500_000
    za, zb = tmp_path / "a.mcpb", tmp_path / "b.mcpb"
    bb.write_bundle(za, a, bb.bundle_entries(a), epoch)
    bb.write_bundle(zb, b, bb.bundle_entries(b), epoch)
    assert za.read_bytes() == zb.read_bytes(), "the zip depends on how its tree was made"
    with zipfile.ZipFile(za) as z:
        infos = z.infolist()
        assert [i.filename for i in infos] == sorted(files)
        assert {i.date_time for i in infos} == {tuple(__import__("time").gmtime(epoch)[:6])}
        modes = {i.filename: stat.S_IMODE(i.external_attr >> 16) for i in infos}
        if os.name != "nt":
            assert modes["server/nexus-mcp/nexus-mcp"] == 0o755
        assert modes["manifest.json"] == 0o644


def test_b4_a_file_no_bundle_may_carry_stops_the_build(tmp_path, monkeypatch):
    bad = [".env", "server/nexus-mcp/_internal/.env.local", "kb.db", "x/archive.db-journal",
           "kb.db.lock", "kb.db.lock.log", "turn_metrics.jsonl"]
    fine = ["manifest.json", "server/nexus-mcp/_internal/base_library.zip", "server/nexus-mcp/_internal/envs.py"]
    assert bb.never_carried(bad + fine) == bad
    # The consequence, through the build itself: a built tree carrying a
    # `.env` (PyInstaller is replaced by a planted tree; the commit's export
    # by the tracked bundle folder).

    def export(dest: Path, commit: str) -> None:
        target = dest / bb.BUNDLE_SRC
        target.mkdir(parents=True)
        for name in ("manifest.json", "icon.png", "entry.py"):
            (target / name).write_bytes((REPO / bb.BUNDLE_SRC / name).read_bytes())

    def pyinstaller(src: Path, work: Path, epoch: int, frozen=("json", "backend.mcp.server")) -> Path:
        built = work / "dist" / bb.NAME
        (built / "_internal").mkdir(parents=True)
        (built / bb.NAME).write_bytes(b"binary")
        (built / "_internal" / ".env").write_text("NEXUS_LEARNING=live\n", encoding="utf-8")
        toc = work / "build" / bb.NAME / "PYZ-00.toc"
        toc.parent.mkdir(parents=True)
        toc.write_text(repr((str(work / "build" / bb.NAME / "PYZ-00.pyz"),
                             [(name, f"/src/{name}.py", "PYMODULE") for name in frozen])), encoding="utf-8")
        return built

    monkeypatch.setattr(bb, "export_commit", export)
    monkeypatch.setattr(bb, "run_pyinstaller", pyinstaller)
    with pytest.raises(bb.BuildRefused) as refused:
        bb.build_once("linux", tmp_path / "work", "0" * 40, 1_762_500_000)
    print(f"B4 {refused.value.code} {refused.value}")
    assert refused.value.code == EXIT_CARRIES and "server/nexus-mcp/_internal/.env" in str(refused.value)


def test_b4_a_module_no_bundle_may_freeze_stops_the_build(tmp_path, monkeypatch):
    """The macOS bundle died at launch three times (bundle runs 5, 12, 13):
    PyInstaller's run-time hook for pkg_resources subclasses
    `pkg_resources.NullProvider`, which setuptools >= 81 no longer ships, and
    that runner's Python carried a pkg_resources, so the analysis froze it
    and attached the hook. The build excludes it at analysis and, since an
    exclusion is a request, reads PyInstaller's PYZ table of contents and
    refuses a bundle that froze one anyway; no table is a refusal too."""
    assert "--exclude-module" in bb.PYINSTALLER_ARGS
    assert bb.PYINSTALLER_ARGS[bb.PYINSTALLER_ARGS.index("--exclude-module") + 1] == "pkg_resources"
    assert bb.never_frozen(["json", "pkg_resources", "pkg_resources.extern.jaraco", "onnxruntime.capi"]) == \
        ["pkg_resources", "pkg_resources.extern.jaraco"]
    assert bb.never_frozen(["json", "pkg_resources_shim", "pkgutil"]) == []

    def export(dest: Path, commit: str) -> None:
        target = dest / bb.BUNDLE_SRC
        target.mkdir(parents=True)
        for name in ("manifest.json", "icon.png", "entry.py"):
            (target / name).write_bytes((REPO / bb.BUNDLE_SRC / name).read_bytes())

    def pyinstaller_with(frozen):
        def run(src: Path, work: Path, epoch: int) -> Path:
            built = work / "dist" / bb.NAME
            (built / "_internal").mkdir(parents=True)
            (built / bb.NAME).write_bytes(b"binary")
            if frozen is not None:
                toc = work / "build" / bb.NAME / "PYZ-00.toc"
                toc.parent.mkdir(parents=True)
                toc.write_text(repr((str(toc.with_suffix(".pyz")),
                                     [(name, f"/src/{name}.py", "PYMODULE") for name in frozen])), encoding="utf-8")
            return built
        return run

    monkeypatch.setattr(bb, "export_commit", export)
    # A planted table with a pkg_resources submodule (the exclusion's request not honoured): refused, with the reason.
    monkeypatch.setattr(bb, "run_pyinstaller", pyinstaller_with(["json", "pkg_resources.extern", "backend.mcp.server"]))
    with pytest.raises(bb.BuildRefused) as refused:
        bb.build_once("linux", tmp_path / "work", "0" * 40, 1_762_500_000)
    print(f"B4 frozen: {refused.value.code} {str(refused.value)[:160]}")
    assert refused.value.code == EXIT_CARRIES and "pkg_resources.extern" in str(refused.value) \
        and "NullProvider" in str(refused.value)
    # No table at all: could not look is a refusal, never a clean bundle.
    monkeypatch.setattr(bb, "run_pyinstaller", pyinstaller_with(None))
    with pytest.raises(bb.BuildRefused) as refused:
        bb.build_once("linux", tmp_path / "work", "0" * 40, 1_762_500_000)
    assert refused.value.code == bb.EXIT_BUILD and "no PYZ table" in str(refused.value)
    # A clean table: the build goes on to its other checks (nothing to carry here: it completes).
    monkeypatch.setattr(bb, "run_pyinstaller", pyinstaller_with(["json", "backend.mcp.server"]))
    stage, entries, manifest = bb.build_once("linux", tmp_path / "work", "0" * 40, 1_762_500_000)
    assert (stage / "manifest.json").is_file() and manifest["name"]


# --- B5, B6 ---------------------------------------------------------------

def test_b5_stamping_serves_one_platform_and_changes_nothing_else():
    m = _manifest()
    for plat in bb.PLATFORMS:
        s = bb.stamp(m, plat)
        assert s["compatibility"]["platforms"] == [plat]
        command, _args, _env = bb.host_launch(s, plat, "/b", {"owner": OWNER})
        assert command == "/b/" + s["server"]["entry_point"], (plat, command)
        rest = copy.deepcopy(s)
        rest["compatibility"]["platforms"] = m["compatibility"]["platforms"]
        rest["server"]["entry_point"] = m["server"]["entry_point"]
        assert rest == m, f"stamping {plat} changed more than the platform and the entry point"
    assert bb.stamp(m, "win32")["server"]["entry_point"].endswith(".exe")
    assert m == _manifest(), "stamping changed the manifest it was given"


def test_b6_host_launch_is_the_reference_behaviour():
    m = _manifest()
    command, args, env = bb.host_launch(m, "darwin", "/b", {"owner": OWNER, "data_folder": ""})
    assert command == "/b/server/nexus-mcp/nexus-mcp" and args == []
    assert env == {"NEXUS_MCP_OWNER": OWNER, "NEXUS_MCP_DATA_ROOT": "${user_config.data_folder}",
                   "NEXUS_LEARNING": "frozen", "NEXUS_ENV_ROOT": "/b"}, env
    planted = copy.deepcopy(m)
    planted["server"]["mcp_config"]["platform_overrides"]["win32"] = {"command": "${__dirname}/x.exe",
                                                                     "env": {"EXTRA": "1"}}
    command, _args, env = bb.host_launch(planted, "win32", "/b", {"owner": OWNER})
    assert command == "/b/x.exe" and env == {"EXTRA": "1"}, "an override's env replaces the base env wholesale"


# --- B7 -------------------------------------------------------------------

def test_b7_the_build_tool_is_pinned_and_installed_at_its_pin():
    from importlib import metadata
    text = (REPO / "requirements.txt").read_text(encoding="utf-8")
    pins = dict(re.findall(r"^(pyinstaller(?:-hooks-contrib)?)==(\S+)\s*$", text, re.M))
    assert pins == {"pyinstaller": "6.22.3", "pyinstaller-hooks-contrib": "2026.9"}, pins
    for name, version in pins.items():
        assert metadata.version(name) == version, (name, metadata.version(name))
