import importlib.util
import os
import sys
from pathlib import Path

import pytest

from raidlock.core import clock

_spec = importlib.util.spec_from_file_location("prhelpers", Path(__file__).resolve().parents[1] / "pr" / "prhelpers.py")
prhelpers = sys.modules.get("prhelpers") or importlib.util.module_from_spec(_spec)
if "prhelpers" not in sys.modules:
    sys.modules["prhelpers"] = prhelpers
    _spec.loader.exec_module(prhelpers)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(("RAIDLOCK_", "CLAUDE_", "CODEX_", "GEMINI_", "GH_", "GITHUB_")) or key == "BOARD_DIR":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("RAIDLOCK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TEAM_BOARD_HOME", str(tmp_path / "legacy-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.chdir(tmp_path)
    clock.reset()
    yield
    clock.reset()


@pytest.fixture
def prw(tmp_path, monkeypatch):
    return prhelpers.PRWorld(tmp_path, monkeypatch)
