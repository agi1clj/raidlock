import json
import os
from pathlib import Path

import pytest
from runhelpers import FIXTURES

from raidlock.core.errors import Precondition
from raidlock.runners.base import Collector, decode
from raidlock.runners.claude_code import ClaudeCode
from raidlock.runners.codex import Codex
from raidlock.runners.gemini import Gemini
from raidlock.runners.runner import Run
from raidlock.runners.shell import Shell


def replay(adapter, name):
    c = Collector()
    with open(FIXTURES / name, encoding="utf-8") as f:
        for line in f:
            obj = decode(line)
            if obj:
                for event in adapter.parse(obj):
                    c.add(event)
    return c


def run(**kw):
    base = dict(
        clone="/w/repo",
        sandbox="read-only",
        model="",
        schema_path="/w/schema.json",
        last_path="/w/last.md",
        schema={"type": "object"},
        parent_env={},
        cmd=None,
    )
    base.update(kw)
    return Run(**base)


def test_codex_read_only_stream():
    c = replay(Codex(), "codex_read_only.jsonl")
    assert c.usage.input == 41882 - 18688 and c.usage.cached_input == 18688 and c.usage.output == 195
    assert c.usage.cache_write == 0 and c.usd is None and not c.error
    assert [x["exit"] for x in c.commands] == [0, 0]
    assert any("cat README.md" in x["command"] for x in c.commands)
    assert json.loads(c.final)["status"] == "done"


def test_codex_workspace_write_stream():
    c = replay(Codex(), "codex_workspace_write.jsonl")
    assert c.usage.input == 62107 - 40320 and c.usage.cached_input == 40320
    assert json.loads(c.final)["paths_changed"] == ["notes/hello.txt"]


def test_codex_failure_events():
    a = Codex()
    assert a.parse({"type": "turn.failed", "error": {"message": "boom"}}) == [{"kind": "error", "text": "boom"}]
    assert a.parse({"type": "error", "message": "reconnecting"})[0]["kind"] == "error"
    assert a.parse(
        {"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": "a", "kind": "add"}]}}
    ) == [{"kind": "files", "paths": ["a"]}]


def test_codex_command_line():
    argv = Codex().command(run(sandbox="workspace-write", model="gpt-6-astra"))
    assert argv[:2] == ["codex", "exec"] and argv[-1] == "-"
    joined = " ".join(argv)
    for flag in (
        "-C /w/repo",
        "-s workspace-write",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-rules",
        "--json",
        "--color never",
        "--output-schema /w/schema.json",
        "-o /w/last.md",
        "-m gpt-6-astra",
    ):
        assert flag in joined
    assert "dangerously" not in joined and "--full-auto" not in joined and "--worktree" not in joined
    assert "-m" not in Codex().command(run())


def test_codex_bin_override():
    assert Codex().executable({"RAIDLOCK_CODEX_BIN": "python3 fake.py --x"}) == ["python3", "fake.py", "--x"]
    assert Codex().executable({}) == ["codex"]


def test_claude_stream():
    c = replay(ClaudeCode(), "claude_read_only.jsonl")
    assert c.model == "claude-haiku-5-5" and c.billing == "plan"
    assert (
        c.usage.input == 4 and c.usage.cached_input == 32176 and c.usage.cache_write == 32502 and c.usage.output == 367
    )
    assert abs(c.usd - 0.00700606) < 1e-6
    assert c.structured["status"] == "done" and not c.error
    assert [x["command"] for x in c.commands] == ["Read /work/repo/README.md"]
    assert ClaudeCode().final(None, c) == c.structured


def test_claude_error_result():
    events = ClaudeCode().parse(
        {
            "type": "result",
            "subtype": "error_max_turns",
            "is_error": True,
            "total_cost_usd": 0.1,
            "usage": {"input_tokens": 1, "output_tokens": 2},
        }
    )
    assert events[0]["usd"] == 0.1 and events[1]["error"] == "error_max_turns"


def test_claude_command_line():
    ro = ClaudeCode().command(run(model="claude-sonnet-5-5"))
    assert ro[:2] == ["claude", "-p"]
    tools = ro[ro.index("--allowedTools") + 1]
    assert "Read" in tools and "Edit" not in tools and "Write" not in tools
    for flag in (
        "--output-format",
        "stream-json",
        "--verbose",
        "--safe-mode",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--json-schema",
    ):
        assert flag in ro
    assert ro[ro.index("--setting-sources") + 1] == ""
    assert ro[ro.index("--permission-mode") + 1] == "dontAsk" and ro[ro.index("--model") + 1] == "claude-sonnet-5-5"
    rw = ClaudeCode().command(run(sandbox="workspace-write"))
    assert "Edit(./**)" in rw[rw.index("--allowedTools") + 1]
    assert "--bare" not in rw and "bypassPermissions" not in rw


def test_gemini_minimal():
    a = Gemini()
    argv = a.command(run(sandbox="workspace-write", model="gemini-3-pro"))
    assert argv[0] == "gemini" and "auto_edit" in argv and argv[argv.index("-m") + 1] == "gemini-3-pro"
    c = Collector()
    for obj in (
        {"type": "init", "model": "gemini-3-pro"},
        {"type": "message", "role": "assistant", "content": '{"status":', "delta": True},
        {"type": "message", "role": "assistant", "content": '"done"}', "delta": True},
        {"type": "tool_use", "tool_name": "read_file", "parameters": {"path": "a"}},
        {"type": "result", "status": "success", "stats": {"input_tokens": 100, "output_tokens": 7, "cached": 40}},
    ):
        for e in a.parse(obj):
            c.add(e)
    assert c.model == "gemini-3-pro" and c.usage.input == 60 and c.usage.cached_input == 40 and c.usage.output == 7
    assert a.final(None, c) == '{"status":"done"}'


def test_shell_events():
    a = Shell()
    usage = a.parse({"kind": "usage", "input": 3, "cached_input": 2, "usd": 0.5})[0]
    assert usage["usage"].input == 3 and usage["usage"].cached_input == 2 and usage["usd"] == 0.5
    assert a.parse({"type": "raw", "text": "hello"}) == [{"kind": "message", "text": "hello"}]
    assert a.parse({"status": "done"})[0]["kind"] == "message"
    assert a.command(run(cmd="./agent.sh --fast")) == ["./agent.sh", "--fast"]


def test_child_env_drops_raidlock_and_session_markers():
    parent = {
        "PATH": "/bin",
        "HOME": "/h",
        "RAIDLOCK_TOKEN": "lead-secret",
        "RAIDLOCK_AGENT": "lead",
        "RAIDLOCK_FENCE": "4",
        "CLAUDECODE": "1",
        "CODEX_HOME": "/c",
        "AWS_SECRET_ACCESS_KEY": "x",
        "OPENAI_API_KEY": "sk",
        "ANTHROPIC_API_KEY": "ak",
    }
    codex = Codex().child_env(parent)
    assert codex["CODEX_HOME"] == "/c" and codex["OPENAI_API_KEY"] == "sk" and "ANTHROPIC_API_KEY" not in codex
    claude = ClaudeCode().child_env(parent)
    assert claude["ANTHROPIC_API_KEY"] == "ak" and "OPENAI_API_KEY" not in claude
    for env in (codex, claude, Shell().child_env(parent), Gemini().child_env(parent)):
        assert not any(k.startswith("RAIDLOCK_") for k in env)
        assert "CLAUDECODE" not in env and "AWS_SECRET_ACCESS_KEY" not in env


def test_register_tokens():
    import raidlock.runners as runners

    got = {}
    runners.register_tokens(lambda name, fn: got.update({name: fn(None)}))
    assert 0 < got["delegate output"]["tokens"] < 60


def test_codex_ephemeral_home(tmp_path):
    user = tmp_path / "user-codex"
    (user / "skills" / "humanizer").mkdir(parents=True)
    (user / "auth.json").write_text('{"tokens": {}}')
    (user / "config.toml").write_text(
        'model = "gpt-6-astra"\nmodel_reasoning_effort = "high"\n'
        'approval_policy = "never"\n[mcp_servers.x]\nmodel = "nope"\n'
    )
    r = run(run_dir=str(tmp_path / "run"), parent_env={"CODEX_HOME": str(user)}, model="")
    env = Codex().prepare(r)
    home = env["CODEX_HOME"]
    assert home == str(tmp_path / "run" / "codex-home") and sorted(os.listdir(home)) == ["auth.json", "config.toml"]
    assert os.path.realpath(os.path.join(home, "auth.json")) == os.path.realpath(user / "auth.json")
    config = Path(os.path.join(home, "config.toml")).read_text()
    assert config == 'model = "gpt-6-astra"\nmodel_reasoning_effort = "high"\n'
    r2 = run(run_dir=str(tmp_path / "run2"), parent_env={"CODEX_HOME": str(user)}, model="gpt-7")
    Codex().prepare(r2)
    assert 'model = "gpt-7"' in Path(tmp_path / "run2" / "codex-home" / "config.toml").read_text()


def test_claude_tools_override():
    a = ClaudeCode()
    assert a.check_tools("Read, Grep,Bash(pytest *)", "read-only") == ["Read", "Grep", "Bash(pytest *)"]
    assert a.check_tools("Edit,Write(./src/**)", "workspace-write") == ["Edit", "Write(./src/**)"]
    for bad, sandbox in (
        ("Bash", "read-only"),
        ("Edit", "read-only"),
        ("Task", "workspace-write"),
        ("Bash(rm -rf / ; echo)", "workspace-write"),
        ("mcp__x__y", "read-only"),
        ("", "read-only"),
    ):
        with pytest.raises(Precondition):
            a.check_tools(bad, sandbox)
    argv = a.command(run(tools=["Read", "Bash(pytest *)"]))
    assert argv[argv.index("--allowedTools") + 1] == "Read,Bash(pytest *)"
