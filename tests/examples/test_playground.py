import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "examples" / "playground" / "setup.sh"

pytestmark = [
    pytest.mark.skipif(not shutil.which("git") or not shutil.which("bash"), reason="needs git and bash"),
    pytest.mark.skipif(os.name != "posix", reason="the playground is documented for Linux and macOS"),
]


def _env(tmp_path):
    shim = tmp_path / "shim"
    shim.mkdir()
    script = shim / "raidlock"
    script.write_text(
        f'#!/bin/sh\nPYTHONPATH="{ROOT / "src"}" exec "{sys.executable}" -m raidlock "$@"\n', encoding="utf-8"
    )
    script.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RAIDLOCK_", "CLAUDE_CODE_SESSION", "CODEX_"))}
    env.update(
        PATH=f"{shim}{os.pathsep}{env.get('PATH', '')}",
        RAIDLOCK_HOME=str(tmp_path / "home"),
        RAIDLOCK_AGENT="intruder",
        RAIDLOCK_TOKEN="stale",
    )
    return env


def _raid(env, ws, *args):
    proc = subprocess.run(
        ["raidlock", *args, "--board", str(ws), "--json"], capture_output=True, text=True, env=env, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_setup_creates_three_cards_a_live_lease_and_worker_checkouts(tmp_path):
    env = _env(tmp_path)
    ws = tmp_path / "ws"
    proc = subprocess.run(["bash", str(SETUP), str(ws)], capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "Playground ready in" in out and "claude --session-id" in out and "codex mcp add raidlock-playground" in out
    codex_line = next(line for line in out.splitlines() if "codex mcp add" in line)
    assert codex_line.endswith(f"-- raidlock mcp --workspace {ws.resolve()}")
    assert f"--env RAIDLOCK_HOME={env['RAIDLOCK_HOME']}" in codex_line and "RAIDLOCK_BOARD" not in codex_line
    for who in ("claude", "codex"):
        remote = subprocess.run(
            ["git", "-C", str(ws / "workers" / who / "acme-shop"), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert remote == "https://example.com/acme/acme-shop.git"
    board = _raid(env, ws, "show", "--fields", "all")
    text = json.dumps(board)
    for card in ("health", "cart", "readme"):
        assert card in text
    check = subprocess.run(
        ["raidlock", "check", "acme-shop/README.md", "--board", str(ws)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert "held by scout on card readme" in check.stdout + check.stderr
    again = subprocess.run(["bash", str(SETUP), str(ws)], capture_output=True, text=True, env=env, timeout=60)
    assert again.returncode == 3 and "already holds a playground" in again.stderr
