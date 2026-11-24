#!/usr/bin/env python3
"""W2 — build NEXUS's MCP bundle: `dist/nexus-<version>-<platform>.mcpb`.

    python scripts/build_bundle.py               build for this machine
    python scripts/build_bundle.py --twice       build twice in the same place: the same
                                                 hash, or the files that differ
    python scripts/build_bundle.py --check       check the manifest and the icon; build nothing
    python scripts/build_bundle.py --write-icon  regenerate backend/mcp/bundle/icon.png
    python scripts/build_bundle.py --ref REV     build a commit other than HEAD (the staged
                                                 tree, before committing: `git stash create`)

`scripts/build_bundle.sh` and `scripts/build_bundle.ps1` run this with the
repo's interpreter. run/specs/W2.md S2, D1 to D3 and D6. In order:

1. The manifest (`backend/mcp/bundle/manifest.json`, the commit's own) is
   checked.
   `manifest_problems` names every field the host reads that is wrong: the
   binary's command, the win32 override that must repeat the whole `env`
   (D6: the MCPB reference implementation replaces `env` wholesale), the
   values the bundle fixes (learning frozen; the `.env` search pinned to the
   bundle's own folder, which holds none: W2's G2), and every
   `${user_config.KEY}` declared and used. Any problem: exit 2, nothing
   built. The icon must be `make_icon()`'s bytes; its source is this file.
2. `backend/` is exported from the commit (`git archive`; HEAD unless
   `--ref`), so the binary carries the commit and nothing untracked, and the
   manifest and icon checked are the commit's. A working tree that differs
   from HEAD under `backend/` is said; it is never what is built.
3. PyInstaller (pinned in requirements.txt, law 16) builds one-dir (D1)
   from `backend/mcp/bundle/entry.py`, with every backend module and
   chromadb's three flags (the establish's trial build), `PYTHONHASHSEED=0`
   and `SOURCE_DATE_EPOCH` set to the commit's time (D3), in
   `build/bundle/<platform>/`, always the same place, so two builds on one
   machine can be compared; removed after a build that succeeds, kept after
   one that fails. Failure: exit 3 and the log's tail.
4. The bundle is written: `manifest.json` stamped for the one platform this
   build serves, `icon.png`, and `server/nexus-mcp/**`, as a zip with sorted
   entries, the commit's time, modes 0755 or 0644 and deflate level 9 (not
   `mcpb pack`, which stamps the pack time and drops files). A file no
   bundle may carry (a `.env`, a store, a lock, a log) stops it by name:
   exit 5.
5. It prints the commit, the platform, Python, PyInstaller, every installed
   distribution and its version with their hash (`pip freeze`'s content,
   read without pip), the bundle's sha256 and size, and every file's sha256
   and size, and writes `<bundle>.sha256` and `<bundle>.files.txt`.

"The same hash twice" holds within one environment: `requirements.txt`'s
ranges move the environment across weeks, so the environment's hash is
printed beside the bundle's, and two builds are comparable only when it
matches (D3). `--twice` exits 4 when the two builds differ, naming the
files.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import io
import json
import os
import platform as _platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import time
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from _console import force_utf8_stdio  # noqa: E402
from _env import load_env  # noqa: E402

BUNDLE_SRC = "backend/mcp/bundle"
MANIFEST = ROOT / BUNDLE_SRC / "manifest.json"
ICON = ROOT / BUNDLE_SRC / "icon.png"
ENTRY = f"{BUNDLE_SRC}/entry.py"
NAME = "nexus-mcp"
SERVER_DIR = f"server/{NAME}"
COMMAND = "${__dirname}/" + f"{SERVER_DIR}/{NAME}"
PLATFORMS = ("darwin", "win32", "linux")
# The values the bundle fixes (run/specs/W2.md D5): learning frozen, and the
# `.env` search pinned to the bundle's own folder, so no `.env` in the host's
# working directory is read (G2: one there turned learning live).
FIXED_ENV = {"NEXUS_LEARNING": "frozen", "NEXUS_ENV_ROOT": "${__dirname}"}
# What a tester sets, and the variable each reaches.
USER_ENV = {"NEXUS_MCP_OWNER": "owner", "NEXUS_MCP_DATA_ROOT": "data_folder"}
# The manifest's top-level keys MCPB 0.3 knows (@anthropic-ai/mcpb 2.1.2,
# MANIFEST.md, "Required Fields" and "Optional Fields"); its validator rejects
# any other.
KNOWN_KEYS = {"manifest_version", "name", "version", "description", "author", "server",
              "icon", "icons", "display_name", "long_description", "repository", "homepage",
              "documentation", "support", "screenshots", "tools", "tools_generated", "prompts",
              "prompts_generated", "keywords", "license", "privacy_policies", "compatibility",
              "user_config", "_meta", "localization"}
PYINSTALLER_ARGS = ("--noconfirm", "--clean", "--onedir", "--name", NAME, "--log-level", "WARN",
                    "--collect-submodules", "backend",
                    "--collect-submodules", "chromadb", "--collect-data", "chromadb",
                    "--hidden-import", "chromadb_rust_bindings",
                    # NEVER_FROZEN, below: excluded at analysis so PyInstaller
                    # attaches no run-time hook for it.
                    "--exclude-module", "pkg_resources",
                    # W3b D13 — the Postgres driver and its bundled libpq, OpenSSL
                    # and Kerberos: the bundle is SQLite only, and `backend.store`
                    # imports the driver inside a try/except for exactly this.
                    "--exclude-module", "psycopg", "--exclude-module", "psycopg_binary",
                    "--exclude-module", "psycopg_c")
# Names no bundle may carry: a configuration file, a store or its sidecars, a
# lock, a log the product writes.
NEVER_CARRIED = re.compile(r"(^|/)\.env[^/]*$|\.db$|\.db-(journal|wal|shm)$|\.lock$|\.lock\.log$|\.jsonl$")
# Modules no bundle may freeze, each with its reason. The build reads
# PyInstaller's PYZ table of contents after the analysis and refuses a bundle
# that froze one: an exclusion at analysis is a request, the table is what
# was done.
NEVER_FROZEN = {
    "pkg_resources": ("PyInstaller's run-time hook for it (pyi_rth_pkgres) subclasses pkg_resources.NullProvider, "
                      "which setuptools >= 81 no longer ships, so a bundle that froze a pkg_resources dies at launch "
                      "('module pkg_resources has no attribute NullProvider': CI's macOS bundle, runs 5, 12 and 13, "
                      "whose Python carried one); NEXUS needs nothing of it (onnxruntime's benchmark scripts import "
                      "it inside functions never called, and the Linux bundle, built where none exists, passes the "
                      "smoke 9 of 9)"),
    # W3b D13 — the Postgres driver (LGPL-3.0; its binary wheel carries libpq,
    # OpenSSL and the Kerberos and LDAP client libraries). The bundle's store
    # is SQLite (its manifest fixes it), so a frozen driver would be dead
    # weight under a licence the bundle has no reason to carry; backend.store
    # imports it inside a try/except and runs SQLite-only without it.
    "psycopg": "the Postgres driver: the desktop bundle is SQLite only (W3b D13)",
    "psycopg_binary": "the Postgres driver's bundled libpq, OpenSSL and Kerberos (W3b D13)",
    "psycopg_c": "the Postgres driver's C extension (W3b D13)",
}
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
USER_CONFIG_REF = re.compile(r"\$\{user_config\.([A-Za-z0-9_]+)\}")
ZIP_EPOCH_FLOOR = 315532800          # 1980-01-01, the earliest time a zip entry holds

EXIT_MANIFEST, EXIT_BUILD, EXIT_NOT_REPRODUCIBLE, EXIT_CARRIES = 2, 3, 4, 5


class BuildRefused(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


# --- 1. the manifest ------------------------------------------------------

def load_manifest(path: Path = MANIFEST) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def manifest_problems(m: dict) -> list[str]:
    """Every way the manifest differs from what this bundle needs, named."""
    out: list[str] = []

    def want(ok, message: str) -> None:
        if not ok:
            out.append(message)

    unknown = sorted(set(m) - KNOWN_KEYS)
    want(not unknown, f"top-level keys MCPB 0.3 does not know: {unknown}")
    want(m.get("manifest_version") == "0.3", f"manifest_version is {m.get('manifest_version')!r}, not '0.3'")
    for key in ("name", "version", "description"):
        want(isinstance(m.get(key), str) and m[key].strip(), f"{key} is missing or empty")
    want(SEMVER.match(str(m.get("version") or "")), f"version {m.get('version')!r} is not semver")
    want(isinstance(m.get("author"), dict) and str(m["author"].get("name") or "").strip(),
         "author.name is missing")
    want(m.get("icon") == "icon.png", f"icon is {m.get('icon')!r}, not 'icon.png'")
    server = m.get("server") if isinstance(m.get("server"), dict) else {}
    want(server.get("type") == "binary", f"server.type is {server.get('type')!r}, not 'binary'")
    want(server.get("entry_point") == f"{SERVER_DIR}/{NAME}",
         f"server.entry_point is {server.get('entry_point')!r}, not {SERVER_DIR}/{NAME!r}")
    cfg = server.get("mcp_config") if isinstance(server.get("mcp_config"), dict) else {}
    want(cfg.get("command") == COMMAND, f"mcp_config.command is {cfg.get('command')!r}, not {COMMAND!r}")
    want(cfg.get("args") == [], f"mcp_config.args is {cfg.get('args')!r}, not []")
    env = cfg.get("env") if isinstance(cfg.get("env"), dict) else {}
    want(env, "mcp_config.env is missing")
    for key, value in FIXED_ENV.items():
        want(env.get(key) == value, f"mcp_config.env.{key} is {env.get(key)!r}, not {value!r}")
    for key, setting in USER_ENV.items():
        want(env.get(key) == "${user_config.%s}" % setting,
             f"mcp_config.env.{key} is {env.get(key)!r}, not '${{user_config.{setting}}}'")
    extra = sorted(set(env) - set(FIXED_ENV) - set(USER_ENV))
    want(not extra, f"mcp_config.env carries {extra}, which nothing in this script answers for")
    overrides = cfg.get("platform_overrides") if isinstance(cfg.get("platform_overrides"), dict) else {}
    want(set(overrides) == {"win32"}, f"platform_overrides names {sorted(overrides)}, not ['win32']")
    win = overrides.get("win32") if isinstance(overrides.get("win32"), dict) else {}
    want(win.get("command") == COMMAND + ".exe",
         f"the win32 override's command is {win.get('command')!r}, not {COMMAND + '.exe'!r}")
    want(win.get("args") == cfg.get("args"), "the win32 override's args are not the base args")
    win_env = win.get("env") if isinstance(win.get("env"), dict) else {}
    if win_env != env:
        missing = sorted(set(env) - set(win_env))
        added = sorted(set(win_env) - set(env))
        changed = sorted(k for k in set(env) & set(win_env) if env[k] != win_env[k])
        out.append("the win32 override's env is not the base env (D6: the reference implementation "
                   f"replaces env wholesale): missing {missing}, added {added}, changed {changed}")
    user_config = m.get("user_config") if isinstance(m.get("user_config"), dict) else {}
    used = set(USER_CONFIG_REF.findall(json.dumps(cfg)))
    want(used <= set(user_config),
         f"mcp_config uses user_config keys {sorted(used - set(user_config))} that are not declared")
    want(set(user_config) <= used, f"user_config declares {sorted(set(user_config) - used)}, which nothing uses")
    owner = user_config.get("owner") if isinstance(user_config.get("owner"), dict) else {}
    want(owner.get("type") == "string", "user_config.owner is not a string")
    folder = user_config.get("data_folder") if isinstance(user_config.get("data_folder"), dict) else {}
    want(folder.get("type") == "directory", "user_config.data_folder is not a directory")
    want("default" not in folder,
         "user_config.data_folder has a default: one host expands it and another passes it through "
         "as a literal, so one install could get two folders; left empty, NEXUS chooses the per-user "
         "folder (W2, D5)")
    want(not folder.get("required"), "user_config.data_folder is required; empty must mean the per-user folder")
    tools = m.get("tools")
    want(isinstance(tools, list) and tools and all(
        isinstance(t, dict) and t.get("name") and t.get("description") for t in tools),
        "tools is not a list of named, described tools")
    platforms = (m.get("compatibility") or {}).get("platforms") if isinstance(m.get("compatibility"), dict) else None
    want(isinstance(platforms, list) and platforms and set(platforms) <= set(PLATFORMS),
         f"compatibility.platforms is {platforms!r}")
    return out


def stamp(m: dict, plat: str) -> dict:
    """The manifest a `plat` bundle carries: that platform alone, and on
    win32 the entry point the binary really has. Nothing else changes."""
    if plat not in PLATFORMS:
        raise ValueError(f"unknown platform {plat!r}")
    out = copy.deepcopy(m)
    out["compatibility"]["platforms"] = [plat]
    if plat == "win32":
        out["server"]["entry_point"] = f"{SERVER_DIR}/{NAME}.exe"
    return out


def host_launch(m: dict, plat: str, dirname: str, user_config: dict) -> tuple[str, list, dict]:
    """(command, args, env) as the MCPB reference implementation builds them:
    the platform's override replaces each key it names wholesale, then
    `${__dirname}` and `${user_config.KEY}` are substituted. A key the
    tester left empty is passed through as its literal placeholder, as the
    reference substitution does (the establish, S1 item 6)."""
    cfg = copy.deepcopy(m["server"]["mcp_config"])
    for key, value in (cfg.pop("platform_overrides", {}) or {}).get(plat, {}).items():
        cfg[key] = value

    def sub(text: str) -> str:
        text = text.replace("${__dirname}", dirname)
        return USER_CONFIG_REF.sub(lambda g: user_config[g.group(1)] if user_config.get(g.group(1))
                                   else g.group(0), text)

    return sub(cfg["command"]), [sub(a) for a in cfg.get("args", [])], {k: sub(v) for k, v in cfg.get("env", {}).items()}


# --- the icon (its source is this function) --------------------------------

def make_icon(size: int = 256) -> bytes:
    """A PNG drawn here, so the bundle carries no outside asset: the console's
    palette (CLAUDE.md, UI Spec), a ring and three joined nodes on the dark
    ground, two samples a side per pixel. Deterministic: the same bytes on
    every machine (zlib level 9 over the same input)."""
    bg, ring, edge = (0x0A, 0x0C, 0x0F), (0x00, 0xE5, 0xFF), (0x4A, 0x55, 0x68)
    nodes = [((0.50, 0.30), (0x00, 0xE5, 0xFF)), ((0.32, 0.64), (0x7C, 0x3A, 0xED)),
             ((0.68, 0.64), (0x22, 0xC5, 0x5E))]

    def colour(x: float, y: float):
        dx, dy = x - 0.5, y - 0.5
        if abs((dx * dx + dy * dy) ** 0.5 - 0.42) < 0.035:
            return ring
        for (cx, cy), c in nodes:
            if (x - cx) ** 2 + (y - cy) ** 2 < 0.075 ** 2:
                return c
        for i in range(3):
            (ax, ay), (bx, by) = nodes[i][0], nodes[(i + 1) % 3][0]
            t = max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)))
            if (x - ax - t * (bx - ax)) ** 2 + (y - ay - t * (by - ay)) ** 2 < 0.012 ** 2:
                return edge
        return bg

    rows = bytearray()
    for py in range(size):
        rows.append(0)                                   # filter: none
        for px in range(size):
            acc = [0, 0, 0]
            for sy in (0.25, 0.75):
                for sx in (0.25, 0.75):
                    c = colour((px + sx) / size, (py + sy) / size)
                    acc[0] += c[0]; acc[1] += c[1]; acc[2] += c[2]
            rows.extend(v // 4 for v in acc)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


# --- 2. the source -----------------------------------------------------------

def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout.strip()


def head(ref: str = "HEAD") -> tuple[str, int]:
    """The commit `ref` names and its commit time (SOURCE_DATE_EPOCH)."""
    return _git("rev-parse", "--verify", f"{ref}^{{commit}}"), int(_git("log", "-1", "--format=%ct", ref))


def worktree_differs() -> list[str]:
    """What under backend/ differs from HEAD (staged, unstaged, untracked)."""
    return [line for line in _git("status", "--porcelain", "--", "backend").splitlines() if line.strip()]


def export_commit(dest: Path, commit: str) -> None:
    """The commit's tracked backend/ into dest/backend, and nothing else."""
    data = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", commit, "backend"],
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        try:
            archive.extractall(dest, filter="data")
        except TypeError:                        # Python 3.11.0-3.11.3: no extraction filters
            archive.extractall(dest)


# --- 3. the binary -----------------------------------------------------------

def run_pyinstaller(src: Path, work: Path, epoch: int) -> Path:
    """One-dir build of the entry, run from `work` so nothing beside the
    exported tree can shadow `backend`, with no NEXUS_* setting of this
    process's (PyInstaller imports packages while it collects them). Returns
    the built folder."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONHOME") and not k.startswith("NEXUS_")}
    env.update(PYTHONHASHSEED="0", SOURCE_DATE_EPOCH=str(epoch))
    cmd = [sys.executable, "-m", "PyInstaller", *PYINSTALLER_ARGS, "--paths", str(src),
           "--distpath", str(work / "dist"), "--workpath", str(work / "build"),
           "--specpath", str(work), str(src / ENTRY)]
    proc = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    log = work / "pyinstaller.log"
    log.write_text(" ".join(cmd) + "\n\n" + proc.stdout + proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
        raise BuildRefused(EXIT_BUILD, f"PyInstaller exited {proc.returncode} (log: {log}):\n{tail}")
    return work / "dist" / NAME


# --- 4. the bundle -----------------------------------------------------------

def bundle_entries(stage: Path) -> list[str]:
    """Every file under stage, as sorted forward-slash paths."""
    return sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file())


def never_carried(entries: list[str]) -> list[str]:
    return [e for e in entries if NEVER_CARRIED.search(e)]


def frozen_modules(work: Path) -> set[str]:
    """The modules PyInstaller froze into the PYZ archive, read from the
    `PYZ-*.toc` tables it writes under the work path (a tuple of the archive's
    path and `(name, source, typecode)` entries). No table is a refusal: the
    check must look, so "could not look" is never "froze nothing"."""
    import ast
    tables = sorted((work / "build").rglob("PYZ*.toc"))
    if not tables:
        raise BuildRefused(EXIT_BUILD, f"no PYZ table of contents under {work / 'build'}: the frozen modules cannot be checked")
    names: set[str] = set()
    for table in tables:
        parsed = ast.literal_eval(table.read_text(encoding="utf-8"))
        entries = parsed[1] if isinstance(parsed, tuple) and len(parsed) == 2 else parsed
        names.update(str(entry[0]) for entry in entries)
    return names


def never_frozen(names) -> list[str]:
    """The frozen modules no bundle may carry: a NEVER_FROZEN name or one of
    its submodules."""
    return sorted(n for n in names if n.split(".")[0] in NEVER_FROZEN)


def write_bundle(out: Path, stage: Path, entries: list[str], epoch: int) -> None:
    """The zip: sorted entries, one fixed time, two modes, deflate level 9.
    Nothing in it depends on the order the files were made in, their times,
    or the umask."""
    date_time = time.gmtime(max(int(epoch), ZIP_EPOCH_FLOOR))[:6]
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".partial")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in entries:
            full = stage / rel
            executable = bool(stat.S_IMODE(full.stat().st_mode) & 0o111)
            info = zipfile.ZipInfo(rel, date_time=date_time)
            info.create_system = 3                       # Unix, so the modes are read
            info.external_attr = ((0o755 if executable else 0o644) | stat.S_IFREG) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, full.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    os.replace(partial, out)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def file_lines(stage: Path, entries: list[str]) -> list[str]:
    return [f"{sha256_file(stage / rel)}  {(stage / rel).stat().st_size:>10}  {rel}" for rel in entries]


def environment_lines() -> list[str]:
    """`pip freeze`'s content, read from the installed distributions."""
    from importlib import metadata
    return sorted({f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions()
                   if d.metadata["Name"]}, key=str.lower)


def platform_tag() -> str:
    return f"{sys.platform}-{_platform.machine().lower() or 'unknown'}"


def check_files(manifest_path: Path, icon_path: Path) -> list[str]:
    """The manifest's problems, and the icon's if it is not make_icon()'s."""
    problems = manifest_problems(load_manifest(manifest_path))
    if not icon_path.is_file() or icon_path.read_bytes() != make_icon():
        problems.append(f"{icon_path.name} is not make_icon()'s output (run with --write-icon)")
    return problems


def build_once(plat: str, work: Path, commit: str, epoch: int) -> tuple[Path, list[str], dict]:
    """One build of `commit` in `work` (emptied first). Returns (stage, its
    entries, the manifest it carries before stamping)."""
    if work.exists():
        shutil.rmtree(work)
    src, stage = work / "src", work / "stage"
    src.mkdir(parents=True)
    export_commit(src, commit)
    problems = check_files(src / BUNDLE_SRC / "manifest.json", src / BUNDLE_SRC / "icon.png")
    if problems:
        raise BuildRefused(EXIT_MANIFEST, f"the commit's manifest is not what this bundle needs: {problems}")
    manifest = load_manifest(src / BUNDLE_SRC / "manifest.json")
    built = run_pyinstaller(src, work, epoch)
    frozen = frozen_modules(work)
    bad_modules = never_frozen(frozen)
    if bad_modules:
        reasons = "; ".join(f"{m}: {NEVER_FROZEN[m.split('.')[0]]}" for m in bad_modules[:1])
        raise BuildRefused(EXIT_CARRIES, f"the bundle would freeze modules no bundle may: {bad_modules} ({reasons})")
    print(f"frozen: {len(frozen)} modules in the PYZ; none of {sorted(NEVER_FROZEN)}")
    shutil.copytree(built, stage / SERVER_DIR)
    (stage / "manifest.json").write_text(json.dumps(stamp(manifest, plat), indent=2, ensure_ascii=False) + "\n",
                                         encoding="utf-8", newline="\n")
    shutil.copyfile(src / BUNDLE_SRC / "icon.png", stage / "icon.png")
    entries = bundle_entries(stage)
    bad = never_carried(entries)
    if bad:
        raise BuildRefused(EXIT_CARRIES, f"the bundle would carry files no bundle may: {bad}")
    return stage, entries, manifest


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="check the manifest and the icon; build nothing")
    ap.add_argument("--twice", action="store_true", help="build twice in the same place and compare")
    ap.add_argument("--write-icon", action="store_true", help="regenerate the icon and stop")
    ap.add_argument("--ref", default="HEAD", help="the commit to build (default HEAD)")
    ap.add_argument("--manifest", type=Path, default=MANIFEST, help=argparse.SUPPRESS)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "dist")
    args = ap.parse_args(argv)
    force_utf8_stdio()
    # Every script loads .env (tests/test_packet_u.py's ratchet). Nothing in
    # it reaches the build: run_pyinstaller hands PyInstaller no NEXUS_*.
    load_env()
    if args.write_icon:
        ICON.write_bytes(make_icon())
        print(f"wrote {ICON.relative_to(ROOT).as_posix()} ({ICON.stat().st_size} bytes)")
        return 0
    if args.check:
        icon = ICON if args.manifest == MANIFEST else args.manifest.with_name("icon.png")
        problems = check_files(args.manifest, icon)
        if problems:
            print("REFUSED: the manifest is not what this bundle needs; nothing was built.", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return EXIT_MANIFEST
        print(f"manifest: {args.manifest.name} and {icon.name}: no problems")
        return 0
    plat = sys.platform if sys.platform in PLATFORMS else None
    if plat is None:
        print(f"REFUSED: no bundle platform for {sys.platform!r}", file=sys.stderr)
        return EXIT_MANIFEST
    try:
        import PyInstaller
    except ImportError:
        print("REFUSED: PyInstaller is not installed (pip install -r requirements.txt)", file=sys.stderr)
        return EXIT_BUILD
    try:
        commit, epoch = head(args.ref)
    except FileNotFoundError:
        print("REFUSED: git is not installed; the bundle is built from a commit", file=sys.stderr)
        return EXIT_BUILD
    except subprocess.CalledProcessError:
        print(f"REFUSED: {args.ref!r} names no commit", file=sys.stderr)
        return EXIT_BUILD
    env_lines = environment_lines()
    env_hash = hashlib.sha256("\n".join(env_lines).encode()).hexdigest()
    print(f"commit: {commit} (SOURCE_DATE_EPOCH {epoch}); building its backend/")
    differs = worktree_differs() if args.ref == "HEAD" else []
    if differs:
        print(f"note: the working tree differs from HEAD under backend/ ({len(differs)} paths); "
              "the bundle carries HEAD, not these:")
        for line in differs:
            print(f"  {line}")
    print(f"platform: {platform_tag()} / {_platform.platform()} / python {_platform.python_version()} "
          f"/ PyInstaller {PyInstaller.__version__}")
    print(f"environment: {len(env_lines)} distributions, sha256 {env_hash}")
    for name in NEVER_FROZEN:
        spec = importlib.util.find_spec(name)
        print(f"{name} in this environment: {spec.origin if spec and spec.origin else 'absent'} (never frozen either way)")
    for line in env_lines:
        print(f"  {line}")
    work = ROOT / "build" / "bundle" / platform_tag()
    try:
        started = time.monotonic()
        stage, entries, manifest = build_once(plat, work, commit, epoch)
        out = args.out_dir / f"nexus-{manifest['version']}-{platform_tag()}.mcpb"
        lines = file_lines(stage, entries)
        write_bundle(out, stage, entries, epoch)
        digest = sha256_file(out)
        print(f"built in {time.monotonic() - started:.0f} s: {len(entries)} files")
        if args.twice:
            first = {line.split()[-1]: line.split()[0] for line in lines}
            stage, entries, _ = build_once(plat, work, commit, epoch)
            second = {line.split()[-1]: line.split()[0] for line in file_lines(stage, entries)}
            again = out.with_name(out.name + ".second")
            write_bundle(again, stage, entries, epoch)
            digest_again = sha256_file(again)
            again.unlink()
            if digest_again != digest:
                differ = sorted(k for k in set(first) | set(second) if first.get(k) != second.get(k))
                print(f"NOT REPRODUCIBLE: {digest} then {digest_again}; {len(differ)} files differ"
                      f"{' (the zip alone)' if not differ else ':'}", file=sys.stderr)
                for name in differ[:50]:
                    print(f"  {name}", file=sys.stderr)
                return EXIT_NOT_REPRODUCIBLE
            print(f"reproducible: the second build gave the same {digest}")
    except BuildRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return e.code
    shutil.rmtree(work, ignore_errors=True)
    out.with_name(out.name + ".sha256").write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    out.with_name(out.name + ".files.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines:
        print(f"  {line}")
    shown = out.relative_to(ROOT).as_posix() if out.is_relative_to(ROOT) else str(out)
    print(f"bundle: {shown} {out.stat().st_size} bytes, sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
