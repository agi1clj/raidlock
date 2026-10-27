import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from raidlock.core import Board

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
FAKE = HERE / "fake_agent.py"
SRC = str(HERE.parents[1] / "src")
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
    "RAIDLOCK_CODEX_BIN",
    "RAIDLOCK_CLAUDE_BIN",
    "RAIDLOCK_GEMINI_BIN",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
)


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def make_repo(path, files):
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    for rel, content in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    return path


class Ws:
    def __init__(self, root):
        self.root = root / "ws"
        self.repo = make_repo(
            self.root / "app", {"README.md": "app\n", "src/main.py": "print('hi')\n", ".gitignore": "*.log\n"}
        )
        self.board, self.token = Board.init(self.root)
        self.lead = self.board.caller(lead_secret=self.token)
        self.board.add_team(self.lead, "core", 2)

    def card(self, cid, locks="app/src", **kw):
        return self.board.new_card(self.lead, cid, card_id=cid, team="core", locks=locks, **kw)

    def reopen(self):
        self.board = Board.open(self.root)
        self.lead = self.board.caller(lead_secret=self.token)
        return self.board


def fake(*args):
    return shlex.join([sys.executable, str(FAKE), *args])


def lines(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_cli(*args, env=None, cwd=None):
    full = {k: v for k, v in os.environ.items() if k not in CLEAR}
    full["PYTHONPATH"] = SRC + os.pathsep + full.get("PYTHONPATH", "")
    full.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "raidlock", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=full,
        cwd=cwd,
        timeout=120,
    )
