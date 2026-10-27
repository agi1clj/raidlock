import subprocess
from pathlib import Path

from raidlock.core import Board
from raidlock.core import transcript as T
from raidlock.lore import Vault, approve, capture

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
)


def make_repo(path, remote):
    path = Path(path)
    (path / ".git").mkdir(parents=True, exist_ok=True)
    (path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (path / ".git" / "config").write_text(
        f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {remote}\n', encoding="utf-8"
    )
    return path


class World:
    def __init__(self, root, name="ws"):
        self.root = Path(root) / name
        make_repo(self.root / "app", "git@github.com:acme/app.git")
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "--quiet",
                "--allow-empty",
                "-m",
                "fixture",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "remote", "add", "origin", "git@github.com:acme/app.git"],
            check=True,
            capture_output=True,
        )
        self.board, token = Board.init(self.root)
        self.lead_token = token
        self.lead = self.board.caller(lead_secret=token)
        self.board.add_team(self.lead, "core", 4)

    def agent(self, name, provider=None):
        r = self.board.join(self.lead, agent=name, team="core", runtime="claude-code", provider=provider)
        c = self.board.caller(agent=name, token=r["token"])
        c.token = r["token"]
        return c

    def card(self, cid, locks="app/src", **kw):
        return self.board.new_card(self.lead, cid, card_id=cid, team="core", locks=locks, **kw)

    def claim(self, cid, caller):
        caller.fence = self.board.claim(cid, caller)["fence"]

    def changes(self, cid, worker, reviewer, note):
        self.claim(cid, worker)
        self.board.move(cid, "review", worker, note="ready")
        self.board.review(cid, reviewer, "changes", note=note)

    def append(self, kind, agent, text, task="", **kw):
        with self.board.lock():
            return self.board.transcript.append(
                T.make(kind, self.board._from(agent), text=text, task=task, context=f"{self.board.name}/{task}", **kw)
            )

    def rejected_handback(self, cid, agent, changed):
        hb = {"status": "blocked", "evidence": [], "paths_changed": changed, "open_questions": []}
        return self.append(
            "handback",
            agent,
            f"blocked: 1 path(s) changed; changes outside the card's locks: {changed[0]}",
            task=cid,
            src="runner",
            artifacts=[
                T.artifact("handback", data=hb, kind="handback"),
                T.artifact("rejected.patch", data={"rejected": True, "files": 1}, kind="patch"),
            ],
        )

    def approve_all(self):
        capture(self.board)
        ids = [x["id"] for x in Vault.for_board(self.board).pending()]
        return approve(self.board, self.lead, ids) if ids else []
