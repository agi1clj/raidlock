import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from raidlock import hooks
from raidlock.core import Board, clock

SRC = str(Path(__file__).resolve().parents[2] / "src")
FIXTURES = Path(__file__).resolve().parent / "fixtures"
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
    "CLAUDE_PLUGIN_DATA",
    "RAIDLOCK_CAP",
    "RAIDLOCK_LEAD_SECRET",
    "RAIDLOCK_LEAD_SECRET_FILE",
    "RAIDLOCK_LEAD_PID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_ENV_FILE",
)


def make_repo(path, remote):
    path = Path(path)
    (path / ".git").mkdir(parents=True, exist_ok=True)
    (path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (path / ".git" / "config").write_text(
        f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {remote}\n', encoding="utf-8"
    )
    return path


def assistant_line(model):
    return json.dumps(
        {
            "type": "assistant",
            "message": {"role": "assistant", "model": model, "content": [{"type": "text", "text": "ok"}]},
        }
    )


class Scene:
    def __init__(self, root):
        self.root = Path(root)
        self.ws = self.root / "ws"
        make_repo(self.ws / "app", "git@github.com:acme/app.git")
        (self.ws / "app" / "src").mkdir(parents=True)
        (self.ws / "app" / "docs").mkdir(parents=True)
        self.board, token = Board.init(self.ws, auth=True)
        self.lead_secret = token
        if "lead_secret" in inspect.signature(self.board.caller).parameters:
            self.lead = self.board.caller(agent=self.board.lead, lead_secret=token)
            self.lead_env = {
                "RAIDLOCK_AGENT": self.board.lead,
                "RAIDLOCK_LEAD_SECRET": token,
                "RAIDLOCK_LEAD_PID": str(self.board.leadlease().lease()["pid"]),
                "RAIDLOCK_SESSION": self.lead.session,
            }
        else:
            self.lead = self.board.caller(agent=self.board.lead, token=token)
            self.lead_env = {"RAIDLOCK_AGENT": self.board.lead, "RAIDLOCK_TOKEN": token}
        self.board.add_team(self.lead, "core", 3)
        self.tokens = {}
        for name in ("w-one", "w-two"):
            r = self.board.join(self.lead, agent=name, team="core", runtime="claude-code")
            self.tokens[name] = r["token"]
        self.board.new_card(self.lead, "c1", card_id="c1", team="core", locks="app/src")
        self.board.new_card(self.lead, "c2", card_id="c2", team="core", locks="app/docs")
        self.fence = self.board.claim("c1", self.caller("w-one"))["fence"]
        self.board.claim("c2", self.caller("w-two"))
        self.data = self.root / "plugin-data"
        self.env = {
            "RAIDLOCK_HOME": os.environ["RAIDLOCK_HOME"],
            "CLAUDE_PLUGIN_DATA": str(self.data),
            # Hook subprocesses: every simulated session shares this process tree.
            "RAIDLOCK_SAME_HOST_SESSIONS": "0",
            "PATH": os.environ.get("PATH", ""),
        }
        projects = self.root / "projects"
        self.transcript = projects / "s1.jsonl"
        self.agent_transcript = projects / "s1" / "subagents" / "agent-a-one.jsonl"
        self.agent_transcript.parent.mkdir(parents=True)
        self.transcript.write_text(assistant_line("claude-opus-5-5") + "\n", encoding="utf-8")
        self.agent_transcript.write_text(
            "\n".join(['{"type": "user"}', assistant_line("claude-sonnet-5-5"), '{"type": "user"}']) + "\n",
            encoding="utf-8",
        )

    def caller(self, name, fence=None):
        return self.board.caller(agent=name, token=self.tokens[name], fence=fence, env={})

    def fixture(self, name, **extra):
        text = (FIXTURES / name).read_text(encoding="utf-8")
        values = {
            "$WS": str(self.ws),
            "$TRANSCRIPT": str(self.transcript),
            "$AGENT_TRANSCRIPT": str(self.agent_transcript),
            "$TOKEN": self.tokens["w-one"],
            "$FENCE": str(self.fence),
        }
        for key, value in {**values, **extra}.items():
            text = text.replace(key, json.dumps(value)[1:-1])
        return text

    def run(self, event, name, **extra):
        return hooks.run(event, self.fixture(name, **extra), dict(self.env))

    def run_process(self, event, name, env=None, **extra):
        full = {**self.env, "PYTHONPATH": SRC, **(env or {})}
        return subprocess.run(
            [sys.executable, "-m", "raidlock.hooks", event],
            input=self.fixture(name, **extra).encode(),
            capture_output=True,
            env=full,
            cwd=SRC,
            timeout=60,
        )

    def bind_worker(self):
        hooks.run("post-tool", self.fixture("post_claim.json"), dict(self.env))

    def fresh(self):
        return Board.open(self.ws)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    for key in CLEAR:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("RAIDLOCK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("GEMINI_CONFIG_DIR", str(tmp_path / "gemini"))
    monkeypatch.chdir(tmp_path)
    clock.reset()
    try:
        from raidlock.core import lead
    except ImportError:
        pass
    else:
        table = lead.process_table()
        monkeypatch.setattr(lead, "process_table", lambda: table)
    yield
    clock.reset()


LEAD_SESSION = "s1"


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", LEAD_SESSION)
    return Scene(tmp_path)
