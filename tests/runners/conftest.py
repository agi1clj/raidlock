import pytest
from runhelpers import CLEAR, Ws

from raidlock.core import clock


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
def ws(tmp_path):
    return Ws(tmp_path)
