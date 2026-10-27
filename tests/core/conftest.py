import os

import pytest
from corehelpers import World

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
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "RAIDLOCK_CAP",
    "RAIDLOCK_LEAD_SECRET",
    "RAIDLOCK_LEAD_SECRET_FILE",
    "RAIDLOCK_LEAD_PID",
    "CLAUDE_PID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_SESSION_ID",
    "CODEX_THREAD_ID",
    "GEMINI_SESSION_ID",
    "CLAUDE_ENV_FILE",
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


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RAIDLOCK_SLOW") or "slow" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="slow; set RAIDLOCK_SLOW=1 or pass -m slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def committed_workspace(world, monkeypatch):
    from raidlock.runners import isolation, workspace

    workspace.git(str(world.root), "init", "--quiet")
    (world.root / ".git" / "info" / "exclude").write_text("app/\n")
    workspace.git(str(world.root), *workspace.IDENT, "commit", "--quiet", "--allow-empty", "-m", "fixture")
    run = isolation.run
    monkeypatch.setattr(isolation, "mode", lambda: "test-isolation")

    def isolated_command(*args, **kwargs):
        kwargs["kind"] = None
        return run(*args, **kwargs)

    monkeypatch.setattr(isolation, "run", isolated_command)
