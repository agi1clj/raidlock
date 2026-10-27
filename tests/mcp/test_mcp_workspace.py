import json
import os
import subprocess
import sys

from corehelpers import SRC

CODEX_PASSES = ("HOME", "LANG", "LOGNAME", "PATH", "SHELL", "TERM", "TMPDIR", "USER")


def codex_like_env(world, token):
    env = {k: os.environ[k] for k in CODEX_PASSES if k in os.environ}
    env.update(
        PYTHONPATH=SRC,
        RAIDLOCK_AGENT="w-mcp",
        RAIDLOCK_TOKEN=token,
        RAIDLOCK_HOME=os.environ["RAIDLOCK_HOME"],
    )
    return env


def serve(args, cwd, env):
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "claimable", "arguments": {}}},
    ]
    return subprocess.run(
        [sys.executable, "-m", "raidlock", "mcp", *args],
        input="".join(json.dumps(m) + "\n" for m in msgs),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=60,
    )


def test_workspace_argument_serves_the_board_from_a_foreign_cwd(world, tmp_path):
    worker = world.agent("w-mcp")
    world.card("c1")
    outside = tmp_path / "elsewhere" / "checkout"
    outside.mkdir(parents=True)
    proc = serve(["--workspace", str(world.root)], str(outside), codex_like_env(world, worker.token))
    assert proc.returncode == 0, proc.stderr
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    result = replies[-1]["result"]
    assert result["isError"] is False
    assert "c1" in result["content"][0]["text"]


def test_without_workspace_a_foreign_cwd_finds_no_board(world, tmp_path):
    worker = world.agent("w-mcp")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    proc = serve([], str(outside), codex_like_env(world, worker.token))
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert "no board found" in json.dumps(replies[-1])


def test_a_workspace_without_a_board_is_refused_at_start(world, tmp_path):
    proc = serve(["--workspace", str(tmp_path / "missing")], str(tmp_path), codex_like_env(world, "x"))
    assert proc.returncode == 3 and "no board at" in proc.stderr
