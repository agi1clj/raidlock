import json
import os
import sys
from pathlib import Path

from raidlock.core import Board, lead
from raidlock.runners import isolation
from raidlock.runners import workspace as W

FAKE_GH = """#!{python}
import json, os, sys
state_path = os.environ["FAKE_GH_STATE"]
with open(state_path) as f:
    state = json.load(f)
argv = sys.argv[1:]
entry = {{"argv": argv, "raidlock_env": sorted(k for k in os.environ if k.startswith("RAIDLOCK_"))}}
if argv[:2] == ["pr", "create"]:
    entry["body"] = open(argv[argv.index("--body-file") + 1]).read()
with open(os.environ["FAKE_GH_LOG"], "a") as f:
    f.write(json.dumps(entry) + "\\n")
def reply(spec):
    sys.stdout.write(spec.get("stdout", ""))
    sys.stderr.write(spec.get("stderr", ""))
    sys.exit(spec.get("exit", 0))
if argv[:2] == ["auth", "status"]:
    reply({{"exit": 0 if state.get("auth", True) else 1, "stderr": "" if state.get("auth", True) else "not logged in"}})
if argv[:2] == ["pr", "create"]:
    reply(state.get("create", {{"stdout": "https://github.com/acme/app/pull/7\\n"}}))
if argv[:2] == ["pr", "view"]:
    views = state.get("views") or [state.get("view", {{"exit": 1, "stderr": "no view"}})]
    n = state.get("view_calls", 0)
    state["view_calls"] = n + 1
    with open(state_path, "w") as f:
        json.dump(state, f)
    spec = views[min(n, len(views) - 1)]
    if "json" in spec:
        spec = {{"stdout": json.dumps(spec["json"])}}
    reply(spec)
reply({{"exit": 2, "stderr": "unexpected gh call"}})
"""


def git(cwd, *args):
    return W.git(str(cwd), *W.IDENT, *args) if args and args[0] == "commit" else W.git(str(cwd), *args)


def commit(repo, rel, text, message="change"):
    path = Path(repo, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", message)
    return W.head(str(repo))


class PRWorld:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = Path(tmp_path)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
        gh.chmod(0o755)
        self.state_file = self.tmp / "gh-state.json"
        self.log = self.tmp / "gh-log.jsonl"
        self.gh_state()
        monkeypatch.setenv("PATH", str(self.bin) + os.pathsep + os.environ.get("PATH", ""))
        monkeypatch.setenv("FAKE_GH_STATE", str(self.state_file))
        monkeypatch.setenv("FAKE_GH_LOG", str(self.log))
        monkeypatch.setenv("GH_TOKEN", "")
        monkeypatch.setattr(
            lead,
            "process_table",
            lambda: {os.getpid(): (os.getppid(), "test-process", "python"), os.getppid(): (0, "test-parent", "zsh")},
        )
        monkeypatch.setattr(isolation, "mode", lambda: "test-isolation")
        monkeypatch.setattr(isolation, "run", lambda cmd, cwd, env, timeout, **kw: (0, "ok", 5))
        self.remote = self.tmp / "remote.git"
        W.git(str(self.tmp), "init", "--quiet", "--bare", "--initial-branch=main", str(self.remote))
        self.ws = self.tmp / "ws"
        self.repo = self.ws / "app"
        self.repo.mkdir(parents=True)
        git(self.repo, "init", "--quiet", "--initial-branch=main")
        commit(self.repo, "README.md", "app\n", "initial")
        git(self.repo, "remote", "add", "origin", str(self.remote))
        git(self.repo, "push", "--quiet", "origin", "main")
        self.board, token = Board.init(self.ws)
        self.lead_token = token
        self.lead = self.board.caller(lead_secret=token)
        self.board.add_team(self.lead, "core", 3)
        self.worker = self.join("builder-one", provider="anthropic", model="claude-opus-5-5", runtime="claude-code")
        self.reviewer = self.join("checker-two", provider="openai", model="gpt-5.5", runtime="codex-cli")

    def join(self, name, provider, model, runtime, role="worker"):
        r = self.board.join(
            self.lead, agent=name, team="core", role=role, runtime=runtime, provider=provider, model_id=model
        )
        caller = self.board.caller(agent=name, token=r["token"])
        caller.token = r["token"]
        return caller

    def gh_state(self, **state):
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

    def calls(self, prefix=None):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line]
        return [r for r in rows if prefix is None or r["argv"][: len(prefix)] == prefix]

    def card(self, cid="c1", locks="app/src", title="Add parser", **kw):
        return self.board.new_card(self.lead, title, card_id=cid, team="core", locks=locks, verify_cmd="check", **kw)

    def build(self, cid="c1", files=None, message="add parser", review=True, verify=True, **card_kw):
        self.card(cid, **card_kw)
        r = self.board.claim(cid, self.worker)
        self.worker.fence = r["fence"]
        for rel, text in (files or {"src/parser.py": "VALUE = 1\n"}).items():
            commit(self.repo, rel, text, message)
        if review:
            self.board.move(cid, "review", self.worker)
        if verify:
            self.board.verify(cid, self.lead, cwd=str(self.repo))
        return self.board.card(cid)

    def remote_branches(self):
        out = W.git(str(self.remote), "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads")
        return dict(line.split(" ", 1) for line in out.splitlines() if line)
