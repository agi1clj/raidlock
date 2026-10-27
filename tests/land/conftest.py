import sys
from pathlib import Path

import pytest

from raidlock.core import clock

RUNNERS = str(Path(__file__).resolve().parents[1] / "runners")
if RUNNERS not in sys.path:
    sys.path.insert(0, RUNNERS)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    from runhelpers import CLEAR

    for key in (*CLEAR, "RAIDLOCK_LEAD_SECRET", "RAIDLOCK_LEAD_SECRET_FILE", "RAIDLOCK_CAP"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("RAIDLOCK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("GEMINI_CONFIG_DIR", str(tmp_path / "gemini"))
    monkeypatch.chdir(tmp_path)
    clock.reset()
    yield
    clock.reset()


@pytest.fixture
def ws(tmp_path):
    from runhelpers import Ws, git

    found = Ws(tmp_path)
    for key, value in (("user.name", "t"), ("user.email", "t@t"), ("commit.gpgsign", "false")):
        git(found.repo, "config", key, value)
    return found
