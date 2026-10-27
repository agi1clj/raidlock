import os
import subprocess
import sys
from pathlib import Path

from raidlock.core import Board

SRC = str(Path(__file__).resolve().parents[2] / "src")


def make_repo(path, remote=None, branch="main"):
    path = Path(path)
    (path / ".git").mkdir(parents=True, exist_ok=True)
    (path / ".git" / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")
    config = "[core]\n\tbare = false\n"
    if remote:
        config += f'[remote "origin"]\n\turl = {remote}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
    (path / ".git" / "config").write_text(config, encoding="utf-8")
    return path


class World:
    def __init__(self, root, name="ws", remote="git@github.com:acme/app.git", auth=True):
        self.root = Path(root) / name
        make_repo(self.root / "app", remote)
        self.board, token = Board.init(self.root, auth=auth)
        self.lead_token = token
        self.lead = self.board.caller(lead_secret=token)
        self.board.add_team(self.lead, "core", 2)

    def agent(self, name, team="core", role="worker", runtime="claude-code", **kw):
        r = self.board.join(self.lead, agent=name, team=team, role=role, runtime=runtime, **kw)
        caller = self.board.caller(agent=name, token=r["token"])
        caller.token = r["token"]
        return caller

    def card(self, cid, locks="app/src", team="core", **kw):
        return self.board.new_card(self.lead, cid, card_id=cid, team=team, locks=locks, **kw)

    def claim(self, cid, caller, **kw):
        r = self.board.claim(cid, caller, **kw)
        caller.fence = r["fence"]
        return r


def run_cli(*args, env=None, cwd=None, timeout=60):
    full = dict(os.environ)
    full["PYTHONPATH"] = SRC + os.pathsep + full.get("PYTHONPATH", "")
    full["PYTHONIOENCODING"] = "utf-8"
    full.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "raidlock", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=full,
        cwd=cwd,
        timeout=timeout,
    )
