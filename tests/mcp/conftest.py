import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from corehelpers import World
from mcphelpers import Client

from raidlock.core import clock

CLEAR = (
    "RAIDLOCK_BOARD",
    "BOARD_DIR",
    "RAIDLOCK_AGENT",
    "RAIDLOCK_AGENT_ID",
    "RAIDLOCK_TOKEN",
    "RAIDLOCK_FENCE",
    "RAIDLOCK_ROLE",
    "RAIDLOCK_REQUIRE_TOKEN",
    "RAIDLOCK_MODEL",
    "RAIDLOCK_SESSION",
    "RAIDLOCK_RUNTIME",
    "ANTHROPIC_MODEL",
    "GEMINI_MODEL",
    "BOARD_PROJECT",
    "RAIDLOCK_BILLING",
)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    for key in CLEAR:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("RAIDLOCK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TEAM_BOARD_HOME", str(tmp_path / "legacy-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("GEMINI_CONFIG_DIR", str(tmp_path / "gemini"))
    monkeypatch.chdir(tmp_path)
    clock.reset()
    yield
    clock.reset()


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


@pytest.fixture
def worker(world):
    w = world.agent("w-one")
    world.card("api", locks="app/src")
    return Client(world, "w-one", w.token)
