import pytest
from lorehelpers import CLEAR, World

from raidlock.core import clock
from raidlock.runners import isolation


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
    run = isolation.run
    monkeypatch.setattr(isolation, "mode", lambda: "test-isolation")
    monkeypatch.setattr(isolation, "run", lambda *args, **kwargs: run(*args, **{**kwargs, "kind": None}))
    clock.reset()
    yield
    clock.reset()


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)
