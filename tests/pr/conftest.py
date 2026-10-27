import os

import pytest
from prhelpers import PRWorld

from raidlock.core import clock


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
    return PRWorld(tmp_path, monkeypatch)
