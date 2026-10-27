import json
import os
import subprocess
import sys

import pytest
from runhelpers import FIXTURES, fake, git, lines, read_json, run_cli

from raidlock.core.errors import Denied, IdentityError, Precondition
from raidlock.runners import isolation
from raidlock.runners.runner import Heartbeat, Relay, delegate

USAGE = '{"kind": "usage", "input": 100, "cached_input": 20, "output": 10}'
DONE = '{"status": "done", "evidence": ["wrote src/new.py"], "paths_changed": ["src/new.py"], "open_questions": []}'


def go(ws, cid, to="shell", **kw):
    kw.setdefault("heartbeat", 0.05)
    return delegate(ws.board, ws.lead, cid, to, **kw)


def runs(ws, cid):
    return os.path.join(os.environ["RAIDLOCK_HOME"], "runs", "ws", cid)


def run_dir(ws, cid, n=None):
    if n is None:
        n = max(int(x) for x in os.listdir(runs(ws, cid)))
    return os.path.join(runs(ws, cid), str(n))


def test_worker_success_moves_to_review_with_patch(ws, tmp_path):
    ws.card("api", scope="add a module")
    env_dump, prompt_dump = tmp_path / "env.json", tmp_path / "prompt.md"
    parent = {**os.environ, "RAIDLOCK_TOKEN": ws.token, "RAIDLOCK_AGENT": ws.board.lead, "CLAUDECODE": "1"}
    r = go(
        ws,
        "api",
        cmd=fake(
            "--write",
            "src/new.py=x = 1",
            "--say",
            "working on it",
            "--say",
            USAGE,
            "--say",
            DONE,
            "--dump-env",
            str(env_dump),
            "--dump-prompt",
            str(prompt_dump),
        ),
        env=parent,
    )
    assert r["lane"] == "review" and r["status"] == "done" and r["_exit"] == 0
    assert r["agent"] == "shell-worker-api" and r["paths_changed"] == ["src/new.py"]
    card = ws.board.card("api")
    assert card["lane"] == "review" and card["owner"] == "shell-worker-api"
    lease = ws.board.leases()["api"]
    assert lease["checkout"]["path"] == os.path.realpath(os.path.join(run_dir(ws, "api"), "repo"))
    child = json.loads(env_dump.read_text())
    assert {k for k in child if k.startswith("RAIDLOCK_")} == {"RAIDLOCK_AGENT_ID", "RAIDLOCK_CAP"}
    assert child["RAIDLOCK_AGENT_ID"] == "shell-worker-api" and child["RAIDLOCK_CAP"] != ws.token
    caller = ws.board.caller(agent=child["RAIDLOCK_AGENT_ID"], cap=child["RAIDLOCK_CAP"], env={})
    assert caller.scope_card == "api"
    assert "CLAUDECODE" not in child
    assert (
        prompt_dump.read_text().startswith("# Worker contract")
        and "Allowed paths in this clone: src" in prompt_dump.read_text()
    )
    d = run_dir(ws, "api")
    assert d == os.path.join(runs(ws, "api"), "1") and not d.startswith(str(ws.root))
    assert not os.path.exists(os.path.join(ws.board.dir, "runs"))
    for name in (
        "prompt.md",
        "schema.json",
        "events.jsonl",
        "stderr.log",
        "handback.json",
        "changes.patch",
        "run.json",
    ):
        assert os.path.exists(os.path.join(d, name)), name
    assert not os.path.exists(os.path.join(d, "repo", "prompt.md"))
    handback = read_json(os.path.join(d, "handback.json"))
    assert handback["paths_changed"] == ["src/new.py"] and handback["status"] == "done"
    assert len(json.dumps(handback).encode()) <= 4096
    check = subprocess.run(["git", "apply", "--check", os.path.join(d, "changes.patch")], cwd=ws.repo)
    assert check.returncode == 0
    summary = read_json(os.path.join(d, "run.json"))
    assert (
        summary["base"] == git(ws.repo, "rev-parse", "HEAD").strip()
        and summary["billing"] == "unknown"
        and summary["brief_tokens"] > 0
    )
    assert git(os.path.join(d, "repo"), "remote").strip() == ""
    assert len(lines(os.path.join(d, "events.jsonl"))) == 3
    rows = ws.board.transcript.read(task="api")
    kinds = [x["kind"] for x in rows]
    assert "handback" in kinds and "relay" in kinds and "cost" in kinds
    assert all("lead-secret" not in json.dumps(x) for x in rows)
    cost = card["costs"][-1]
    assert cost["by"] == "shell-worker-api" and cost["usage"] == {
        "input": 100,
        "cached_input": 20,
        "cache_write": 0,
        "output": 10,
        "reasoning_output": 0,
    }
    agent = ws.board.agents()["shell-worker-api"]
    assert agent["runtime"] == "shell" and agent["provider"] == "local" and agent["team"] == "core"
    assert any("billing unknown" in n["text"] for n in card["notes"])


def test_change_outside_locks_blocks(ws):
    ws.card("api")
    r = go(ws, "api", cmd=fake("--write", "src/ok.py=1", "--write", "README.md=changed", "--say", DONE))
    assert r["lane"] == "blocked" and r["_exit"] == 2 and r["outside"] == ["README.md"]
    assert "README.md" in r["reason"]
    d = run_dir(ws, "api")
    assert os.path.exists(os.path.join(d, "rejected.patch")) and not os.path.exists(os.path.join(d, "changes.patch"))
    card = ws.board.card("api")
    assert card["lane"] == "blocked" and "README.md" in card["notes"][-1]["text"]
    assert "api" in ws.board.leases()


def test_denied_path_inside_lock_blocks(ws):
    ws.card("api", locks="app")
    r = go(ws, "api", cmd=fake("--write", ".env=TOKEN=x", "--write", ".github/workflows/ci.yml=on: push"))
    assert r["lane"] == "blocked" and r["denied"] == [".env", ".github/workflows/ci.yml"] and r["outside"] == []


def test_ignored_files_are_not_changes(ws):
    ws.card("api")
    r = go(ws, "api", cmd=fake("--write", "debug.log=noise", "--write", "src/a.py=1", "--say", DONE))
    assert r["lane"] == "review" and r["paths_changed"] == ["src/a.py"]


def test_agent_failure_blocks(ws):
    ws.card("api")
    r = go(ws, "api", cmd=fake("--exit", "3"))
    assert r["lane"] == "blocked" and "agent exited 3" in r["reason"]


def test_timeout_blocks(ws):
    ws.card("api")
    r = go(ws, "api", cmd=fake("--sleep", "30"), timeout=0.5)
    assert r["lane"] == "blocked" and "timed out" in r["reason"]


def test_runner_runs_verify_after_agent(ws):
    ws.card("good", verify_cmd="test -f src/new.py")
    r = go(ws, "good", cmd=fake("--write", "src/new.py=1", "--say", DONE), unsafe_verify=isolation.mode() is None)
    assert r["lane"] == "review" and r["verify"]["passed"] is True
    assert any("runner verify" in e for e in r["handback"]["evidence"])
    last = ws.board.card("good")["verifications"][-1]
    assert last["passed"] is True and last["cwd"] == os.path.join(r["run_dir"], "repo")
    assert "signature" not in last
    with pytest.raises(Precondition, match="valid verification receipt"):
        ws.board.move("good", "done", ws.lead)
    ws.card("bad", locks="app/lib", verify_cmd="test -f src/missing.py")
    r = go(ws, "bad", cmd=fake("--write", "lib/x.py=1", "--say", DONE), unsafe_verify=isolation.mode() is None)
    assert r["lane"] == "blocked" and r["verify"]["passed"] is False and "verify failed" in r["reason"]


def test_agent_reported_blocked(ws):
    ws.card("api")
    r = go(
        ws,
        "api",
        cmd=fake(
            "--say", '{"status": "blocked", "evidence": [], "paths_changed": [], "open_questions": ["which db?"]}'
        ),
    )
    assert r["lane"] == "blocked" and "which db?" in r["handback"]["open_questions"]


def test_preflight_refuses_before_joining(ws):
    ws.card("first")
    ws.board.new_card(ws.lead, "second", card_id="second", team="core", locks="app/x", depends="first")
    with pytest.raises(Precondition, match="waits on first"):
        go(ws, "second", cmd=fake())
    assert "shell-worker-second" not in ws.board.agents()
    assert not os.path.exists(runs(ws, "second"))


def test_only_the_lead_delegates(ws):
    ws.card("api")
    joined = ws.board.join(ws.lead, agent="w-one", team="core")
    worker = ws.board.caller(agent="w-one", token=joined["token"])
    with pytest.raises(IdentityError, match="lead"):
        delegate(ws.board, worker, "api", "shell", cmd=fake())


def test_bad_options(ws):
    ws.card("api")
    with pytest.raises(Precondition, match="--cmd"):
        go(ws, "api")
    with pytest.raises(Precondition, match="read-only"):
        go(ws, "api", role="reviewer", sandbox="workspace-write", cmd=fake())
    with pytest.raises(Precondition, match="--to"):
        go(ws, "api", to="aider")


def test_codex_adapter_end_to_end_with_recorded_stream(ws, tmp_path, monkeypatch):
    ws.card("notes", locks="app/notes")
    argv_dump = tmp_path / "argv.json"
    prompt_dump = tmp_path / "stdin.txt"
    monkeypatch.setenv(
        "RAIDLOCK_CODEX_BIN",
        fake(
            "--replay",
            str(FIXTURES / "codex_workspace_write.jsonl"),
            "--write",
            "notes/hello.txt=hi",
            "--dump-argv",
            str(argv_dump),
            "--dump-prompt",
            str(prompt_dump),
        ),
    )
    r = go(ws, "notes", to="codex", model="gpt-6-astra")
    assert r["lane"] == "review" and r["agent"] == "codex-worker-notes" and r["paths_changed"] == ["notes/hello.txt"]
    argv = json.loads(argv_dump.read_text())
    assert prompt_dump.read_text().startswith("# Worker contract")
    assert "-s" in argv and argv[argv.index("-s") + 1] == "workspace-write" and argv[-1] == "-"
    assert not any(a.startswith("# Worker contract") for a in argv)
    cost = ws.board.card("notes")["costs"][-1]
    assert cost["usage"]["input"] == 62107 - 40320 and cost["usage"]["cached_input"] == 40320 and cost["usd"] is None
    assert cost["model"] == "gpt-6-astra"
    agent = ws.board.agents()["codex-worker-notes"]
    assert agent["runtime"] == "codex-cli" and agent["provider"] == "openai" and agent["model"] == "gpt-6-astra"
    assert r["handback"]["evidence"][0].startswith("Created notes/hello.txt")


def test_claude_adapter_read_only_worker(ws, monkeypatch):
    ws.card("audit", locks="app/src")
    monkeypatch.setenv("RAIDLOCK_CLAUDE_BIN", fake("--replay", str(FIXTURES / "claude_read_only.jsonl")))
    r = go(ws, "audit", to="claude", sandbox="read-only")
    assert r["lane"] == "review" and r["paths_changed"] == [] and r["billing"] == "plan"
    assert r["model"] == "claude-haiku-5-5" and abs(r["usd"] - 0.00700606) < 1e-6
    cost = ws.board.card("audit")["costs"][-1]
    assert cost["billing"] == "plan" and cost["usd"] == 0 and abs(cost["usd_equiv"] - 0.00700606) < 1e-6
    assert cost["model"] == "claude-haiku-5-5"


def test_reviewer_reads_patch_and_records_verdict(ws):
    ws.card("api")
    go(ws, "api", cmd=fake("--write", "src/new.py=x = 1", "--say", DONE))
    script = (
        "import subprocess, json, sys; base = sys.stdin.read().split('git diff ')[1].split()[0]; "
        "d = subprocess.run(['git', 'diff', '--name-only', base, 'HEAD'], capture_output=True, text=True).stdout; "
        "print(json.dumps({'verdict': 'changes', 'summary': 'saw ' + d.strip(), 'findings': ['src/new.py:1 x']}))"
    )
    other = ws.board.join(ws.lead, agent="human-rev", role="reviewer")
    ws.board.assign_reviewers("api", ws.lead, ["human-rev"])
    r = go(ws, "api", role="reviewer", cmd=fake.__globals__["shlex"].join([sys.executable, "-c", script]))
    assert other and ws.board.card("api")["reviewers"] == ["human-rev", "shell-reviewer-api"]
    assert r["verdict"] == "changes" and r["summary"] == "saw src/new.py" and r["lane"] == "doing"
    card = ws.board.card("api")
    assert card["reviews"][-1]["by"] == "shell-reviewer-api" and card["reviews"][-1]["verdict"] == "changes"
    assert ws.board.agents()["shell-reviewer-api"]["role"] == "reviewer"
    review_dir = r["run_dir"]
    assert review_dir == run_dir(ws, "api", 2)
    assert read_json(os.path.join(review_dir, "review.json"))["findings"] == ["src/new.py:1 x"]
    assert git(os.path.join(review_dir, "repo"), "status", "--porcelain").strip() == ""


def test_reviewer_uses_the_worker_base_when_the_repo_moved(ws):
    ws.card("api")
    go(ws, "api", cmd=fake("--write", "src/new.py=x = 1", "--say", DONE))
    base = read_json(os.path.join(run_dir(ws, "api"), "run.json"))["base"]
    (ws.repo / "OTHER.md").write_text("landed meanwhile\n")
    git(ws.repo, "add", "-A")
    git(ws.repo, "commit", "-q", "-m", "other work")
    script = "import json; print(json.dumps({'verdict': 'approve', 'summary': 'ok', 'findings': []}))"
    r = go(ws, "api", role="reviewer", cmd=fake.__globals__["shlex"].join([sys.executable, "-c", script]))
    assert r["verdict"] == "approve"
    clone = os.path.join(r["run_dir"], "repo")
    assert git(clone, "diff", "--name-only", base, "HEAD").split() == ["src/new.py"]


def test_reviewer_needs_review_lane(ws):
    ws.card("api")
    with pytest.raises(Precondition, match="not review"):
        go(ws, "api", role="reviewer", cmd=fake())


def test_rerun_after_block_keeps_previous_run(ws):
    ws.card("api")
    go(ws, "api", cmd=fake("--write", "README.md=x"))
    r = go(ws, "api", cmd=fake("--write", "src/a.py=1", "--say", DONE))
    assert r["lane"] == "review"
    assert sorted(os.listdir(runs(ws, "api"))) == ["1", "2"] and r["run_dir"] == run_dir(ws, "api", 2)
    assert ws.board.leases()["api"]["checkout"]["path"] == os.path.realpath(os.path.join(r["run_dir"], "repo"))


def test_planted_git_config_is_undone_before_diff(ws, tmp_path):
    ws.card("api")
    marker = tmp_path / "pwned"
    script = (
        "import subprocess; open('.gitattributes', 'w').write('* filter=evil\\n'); "
        f"subprocess.run(['git', 'config', 'filter.evil.clean', {f'touch {marker}; cat'!r}]); "
        "open('src/a.py', 'w').write('1')"
    )
    r = go(ws, "api", cmd=fake.__globals__["shlex"].join([sys.executable, "-c", script]))
    assert not marker.exists()
    assert r["outside"] == [".gitattributes"] and r["lane"] == "blocked"


class FakeBoard:
    name = "ws"

    def __init__(self):
        self.calls = 0

    def heartbeat(self, card, caller):
        self.calls += 1
        raise Denied("fence 3 for api is stale; someone claimed it after you")


def test_heartbeat_stops_agent_when_lease_is_lost():
    stopped = []
    relay = Relay.__new__(Relay)
    relay.flush = lambda: None
    beat = Heartbeat(FakeBoard(), "api", object(), 0.01, relay, stopped.append)
    beat.start()
    beat.join(2)
    assert beat.lost.startswith("fence 3") and stopped and stopped[0].startswith("lease lost")


def test_cli_delegate_json(ws, tmp_path):
    ws.card("api")
    env = {
        "RAIDLOCK_HOME": os.environ["RAIDLOCK_HOME"],
        "RAIDLOCK_BOARD": str(ws.root),
        "RAIDLOCK_LEAD_SECRET": ws.token,
    }
    r = run_cli(
        "delegate",
        "api",
        "--to",
        "shell",
        "--cmd",
        fake("--write", "src/a.py=1", "--say", DONE),
        "--json",
        env=env,
        cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["ok"] is True and data["lane"] == "review" and "_exit" not in data
    r = run_cli("delegate", "api", "--to", "shell", "--cmd", fake(), env=env, cwd=str(tmp_path))
    assert r.returncode == 3 and "review" in r.stderr and "Next:" in r.stderr
    ws.card("out", locks="app/other")
    r = run_cli("delegate", "out", "--to", "shell", "--cmd", fake("--write", "README.md=x"), env=env, cwd=str(tmp_path))
    assert r.returncode == 2 and r.stdout.strip().splitlines()[-1] == "Next: raidlock show out"
    assert "blocked out" in r.stdout


def test_codex_run_gets_an_ephemeral_home(ws, tmp_path, monkeypatch):
    user = tmp_path / "codex"
    (user / "skills").mkdir(parents=True)
    (user / "auth.json").write_text('{"tokens": {}}')
    env_dump = tmp_path / "env.json"
    ws.card("notes", locks="app/notes")
    monkeypatch.setenv(
        "RAIDLOCK_CODEX_BIN", fake("--replay", str(FIXTURES / "codex_read_only.jsonl"), "--dump-env", str(env_dump))
    )
    r = go(ws, "notes", to="codex", sandbox="read-only")
    home = json.loads(env_dump.read_text())["CODEX_HOME"]
    assert home == os.path.join(r["run_dir"], "codex-home") and "skills" not in os.listdir(home)
    with pytest.raises(Precondition, match="invalid model"):
        go(ws, "notes", to="codex", model='x"\nsandbox_mode = "danger-full-access')


def test_tools_only_for_claude(ws):
    ws.card("api")
    with pytest.raises(Precondition, match="claude only"):
        go(ws, "api", cmd=fake(), tools="Read")
    with pytest.raises(Precondition, match="workspace-write"):
        go(ws, "api", to="claude", sandbox="read-only", tools="Edit")


def test_multi_repo_card_is_refused(ws):
    from runhelpers import make_repo

    make_repo(ws.root / "lib", {"a.txt": "a"})
    ws.card("both", locks="app/src lib/x")
    with pytest.raises(Precondition, match="split the quest per repo"):
        go(ws, "both", cmd=fake())


def test_delegate_starts_at_requested_ref_without_switching_workspace(ws):
    ws.card("branch")
    base = git(ws.repo, "rev-parse", "HEAD").strip()
    git(ws.repo, "branch", "integration")
    (ws.repo / "src" / "main.py").write_text("later\n")
    git(ws.repo, "add", "-A")
    git(ws.repo, "commit", "-qm", "later")
    current = git(ws.repo, "rev-parse", "HEAD").strip()
    result = go(ws, "branch", ref="integration", cmd=fake("--say", DONE))
    assert read_json(os.path.join(result["run_dir"], "run.json"))["base"] == base
    assert git(ws.repo, "rev-parse", "HEAD").strip() == current


def test_lead_protected_exception_passes_scanner_and_requires_other_provider(ws):
    ws.card("workflow", locks="app/.github/workflows", allow_protected=".github/workflows")
    result = go(ws, "workflow", cmd=fake("--write", ".github/workflows/ci.yml=name: CI", "--say", DONE))
    assert result["lane"] == "review"
    ws.board.set_setting(ws.lead, "require_verify", "false")
    ws.board.set_setting(ws.lead, "require_review", 0)
    with pytest.raises(Precondition, match="another provider"):
        ws.board.move("workflow", "done", ws.lead)
    joined = ws.board.join(ws.lead, agent="reviewer", role="reviewer", provider="anthropic")
    ws.board.review("workflow", ws.board.caller(agent="reviewer", token=joined["token"]), "approve")
    ws.board.move("workflow", "done", ws.lead)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_delegate_refuses_unbounded_timeout(ws, timeout):
    ws.card("task")
    with pytest.raises(Precondition, match="positive finite"):
        go(ws, "task", cmd=fake(), timeout=timeout)


def test_delegate_rejects_changes_to_lead_excluded_file(ws):
    ws.card("docs", locks="app/docs")
    ws.board.edit_card(ws.lead, "docs", exclude_locks="app/docs/other.md")
    result = go(ws, "docs", cmd=fake("--write", "docs/other.md=unauthorized", "--say", DONE))
    assert result["lane"] == "blocked"
    assert "outside" in result["reason"]


def test_excluded_file_cannot_be_reached_through_symlink():
    from raidlock.runners.policy import check_links

    assert check_links({"docs/link": "other.md"}, ["docs"], excluded=["docs/other.md"]) == ["docs/link"]
