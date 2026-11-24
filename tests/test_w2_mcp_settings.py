"""W2 part 2 — MCP mode's settings (run/specs/W2.md S2, D5; S1's G1, G3, G4).

S1  NEXUS_MCP_DATA_ROOT unset or empty is a per-user application-data
    folder, never the working directory (before W2: `data`, relative, so a
    process started in a checkout used the repo's data/).
S2  A value the bundle host left unexpanded (`${HOME}/NEXUS`) is not a
    folder: the per-user default (before W2: a store under
    `<cwd>/${HOME}/NEXUS`).
S2b Each choice says why, in the words the startup line prints (new API).
S3  An owner the host left unexpanded (`${user_config.owner}`) is not an
    identity: records are unattributed (before W2 it was sealed as the
    accountable identity).
S4  An absolute folder that is set is used as given; a relative one is read
    against the host's working directory, so it is the per-user default too
    (D5).
S5  MCP's three logs follow its own root (before W2 they followed the
    console's NEXUS_DATA_ROOT, else the working directory's data/); a
    writer's own variable still wins.
S6  Through the real entry: a fresh process given both placeholders makes
    its store in the per-user default, writes nothing in its working
    directory, and says both on stderr.

The per-user folder is planted (HOME, USERPROFILE, LOCALAPPDATA under
tmp_path, XDG_DATA_HOME removed), so nothing here touches the real one, and
the folder each OS should get is written out here (`_declared_default`)
rather than imported, so the code is not graded by itself.
"""
from __future__ import annotations

import os
import subprocess
from tests import _proc
import sys
from pathlib import Path

import pytest

from tests._tracked import REPO

LOGS = ("NEXUS_CALIBRATION_LOG", "NEXUS_CITATION_CAPTURE_PATH", "NEXUS_FIRE_CAPTURE_PATH")


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("LOCALAPPDATA", str(h / "AppData" / "Local"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("NEXUS_MCP_DATA_ROOT", raising=False)
    monkeypatch.delenv("NEXUS_MCP_OWNER", raising=False)
    return h


def _declared_default(home: Path) -> Path:
    """D5's folder for this OS under the planted home: %LOCALAPPDATA%\\NEXUS,
    ~/Library/Application Support/NEXUS, else ~/.local/share/nexus."""
    if os.name == "nt":
        return home / "AppData" / "Local" / "NEXUS"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "NEXUS"
    return home / ".local" / "share" / "nexus"


def _unset_writers(monkeypatch) -> None:
    """Each log's own variable removed through monkeypatch. Set first, so the
    removal is recorded even where it was absent: the runtime sets each one
    that is absent, and that value would otherwise outlive the test."""
    for name in LOGS:
        monkeypatch.setenv(name, "set-by-the-test")
        monkeypatch.delenv(name)


def test_s1_unset_is_a_per_user_folder_never_the_working_directory(home, tmp_path, monkeypatch):
    from backend.mcp import config
    monkeypatch.chdir(tmp_path)
    root = config.data_root()
    print(f"S1 ({sys.platform}) unset -> {root}")
    assert root == _declared_default(home), root
    monkeypatch.setenv("NEXUS_MCP_DATA_ROOT", "   ")
    assert config.data_root() == root, "an empty setting is not a folder"


@pytest.mark.skipif(os.name == "nt" or sys.platform == "darwin",
                    reason="XDG_DATA_HOME is the rule only outside Windows and macOS")
def test_s1_xdg_data_home_is_honoured_where_it_is_the_rule(home, tmp_path, monkeypatch):
    from backend.mcp import config
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert config.data_root() == tmp_path / "xdg" / "nexus"


def test_s2_an_unexpanded_folder_placeholder_is_the_per_user_default(home, tmp_path, monkeypatch):
    from backend.mcp import config
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEXUS_MCP_DATA_ROOT", "${HOME}/NEXUS")
    root = config.data_root()
    print(f"S2 ${{HOME}}/NEXUS -> {root}")
    assert root == _declared_default(home), root


def test_s2b_each_choice_says_why(home, tmp_path, monkeypatch):
    """New API (`data_root_choice`), so it fails on its import before W2."""
    from backend.mcp import config
    cases = {"": "unset", "${HOME}/NEXUS": "unexpanded placeholder", "data": "is relative",
             str(tmp_path / "chosen"): None}
    for value, words in cases.items():
        monkeypatch.setenv("NEXUS_MCP_DATA_ROOT", value)
        root, why = config.data_root_choice()
        print(f"S2b {value!r} -> {root} ({why})")
        if words is None:
            assert root == tmp_path / "chosen" and why == "NEXUS_MCP_DATA_ROOT"
        else:
            assert root == _declared_default(home) and words in why, (value, why)
            assert "per-user default" in why


def test_s3_an_unexpanded_owner_is_unattributed(home, monkeypatch):
    from backend.mcp import config
    monkeypatch.setenv("NEXUS_MCP_OWNER", "${user_config.owner}")
    assert config.owner() is None
    monkeypatch.setenv("NEXUS_MCP_OWNER", "tester@example.org")
    assert config.owner() == "tester@example.org"


def test_s4_an_absolute_folder_that_is_set_is_used(home, tmp_path, monkeypatch):
    from backend.mcp import config
    chosen = tmp_path / "chosen"
    monkeypatch.setenv("NEXUS_MCP_DATA_ROOT", str(chosen))
    assert config.data_root() == chosen


def test_s4_a_relative_folder_is_the_per_user_default(home, tmp_path, monkeypatch):
    """`data` was the documented value (CLAUDE.md's block, before W2): read
    against the host's working directory, it is G4 in another spelling."""
    from backend.mcp import config
    monkeypatch.chdir(tmp_path)
    for value in ("data", "./NEXUS", "~/NEXUS"):
        monkeypatch.setenv("NEXUS_MCP_DATA_ROOT", value)
        root = config.data_root()
        print(f"S4 {value!r} -> {root}")
        assert root == _declared_default(home), (value, root)


def test_s5_the_three_logs_follow_mcps_root_and_a_writers_own_setting_wins(home, tmp_path, monkeypatch):
    from backend.eval import calibration_log, citation_capture, fire_capture
    from backend.mcp.mcp_server import NexusRuntime
    _unset_writers(monkeypatch)
    monkeypatch.setenv("NEXUS_DATA_ROOT", str(tmp_path / "console"))
    monkeypatch.setenv("NEXUS_LEARNING", "frozen")
    monkeypatch.setenv("NEXUS_LEARNING_FROZEN_SINCE", "2026-11-01")
    root = tmp_path / "mcp"
    runtime = NexusRuntime.open(root=root)
    try:
        logs = (calibration_log.log_path(), citation_capture.capture_path(), fire_capture.capture_path())
        print(f"S5 logs {[str(p) for p in logs]}")
        assert logs == (root / "calibration_log.jsonl", root / "citation_capture.jsonl",
                        root / "fire_capture.jsonl")
    finally:
        runtime.services.store_lock.release()
    _unset_writers(monkeypatch)
    own = tmp_path / "own.jsonl"
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(own))
    runtime = NexusRuntime.open(root=tmp_path / "mcp2")
    try:
        assert calibration_log.log_path() == own, "a writer's own setting lost to MCP's root"
        assert fire_capture.capture_path() == tmp_path / "mcp2" / "fire_capture.jsonl"
    finally:
        runtime.services.store_lock.release()


def test_s6_the_real_entry_with_both_placeholders(home, tmp_path):
    spawn = tmp_path / "spawn"
    spawn.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in LOGS}
    env.update(PYTHONDONTWRITEBYTECODE="1", NEXUS_ENV_ROOT=str(spawn), PYTHONIOENCODING="utf-8",
               NEXUS_MCP_DATA_ROOT="${HOME}/NEXUS", NEXUS_MCP_OWNER="${user_config.owner}",
               PYTHONPATH=os.pathsep.join(filter(None, [str(REPO), os.environ.get("PYTHONPATH")])))
    proc = _proc.run([sys.executable, "-m", "backend.mcp"], cwd=spawn, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
    expected = _declared_default(home)
    print(f"S6 ({sys.platform}) exit {proc.returncode}; stderr head: {proc.stderr[:900]}")
    assert proc.returncode == 0, proc.stderr[-1200:]
    assert (expected / "kb.db").is_file(), f"no store in the per-user default {expected}"
    assert sorted(p.name for p in spawn.iterdir()) == [], "the process wrote in its working directory"
    assert "unexpanded placeholder" in proc.stderr and "NEXUS_MCP_OWNER" in proc.stderr, proc.stderr[-1200:]
    assert str(expected) in proc.stderr, "the startup line does not name the data folder"
