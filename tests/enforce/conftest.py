import os
from types import SimpleNamespace

import pytest

from raidlock.core import Board, clock
from raidlock.core import lead as lead_module
from raidlock.runners import isolation


@pytest.fixture
def world(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(("RAIDLOCK_", "BOARD_", "TEAM_BOARD_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("RAIDLOCK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        lead_module,
        "process_table",
        lambda: {
            os.getpid(): (os.getppid(), "test-worker-start", "python"),
            os.getppid(): (0, "test-host-start", "zsh"),
        },
    )
    run = isolation.run
    monkeypatch.setattr(isolation, "mode", lambda: "test-isolation")
    monkeypatch.setattr(isolation, "run", lambda *args, **kwargs: run(*args, **{**kwargs, "kind": None}))
    clock.reset()
    workspace = tmp_path / "workspace"
    repo = workspace / "app"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src/task.py").write_text("value = 1\n")
    board, secret = Board.init(workspace, lead="lead")
    secret_file = tmp_path / "lead-secret"
    secret_file.write_text(secret)
    secret_file.chmod(0o600)
    lead = board.caller(lead_secret=secret)
    joined = board.join(lead, agent="worker", provider="openai", model_id="model-a")
    board.new_card(lead, "Task", card_id="task", locks="app/src")
    grant = board.claim("task", lead, for_agent="worker")
    worker = board.caller(agent="worker", token=joined["token"], fence=grant["fence"])
    yield SimpleNamespace(
        board=board, lead=lead, worker=worker, repo=repo, secret_file=secret_file, token=joined["token"], root=tmp_path
    )
    clock.reset()
