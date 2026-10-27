import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from raidlock.runners import isolation

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "examples" / "demo" / "run.sh"

pytestmark = [
    pytest.mark.skipif(not shutil.which("git") or not shutil.which("bash"), reason="needs git and bash"),
    pytest.mark.skipif(isolation.mode() is None, reason="raid verify needs bwrap or sandbox-exec"),
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
    env.update(PATH=f"{shim}{os.pathsep}{env.get('PATH', '')}", RAIDLOCK_AGENT="intruder", RAIDLOCK_TOKEN="stale")
    return env


def _show(env, ws):
    proc = subprocess.run(
        ["raidlock", "show", "--board", str(ws), "--json", "--fields", "all"],
        capture_output=True,
        text=True,
        env={**env, "RAIDLOCK_HOME": str(ws.parent / "home")},
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return {c["id"]: c for c in json.loads(proc.stdout)["cards"]}


def test_demo_raid_runs_end_to_end(tmp_path):
    env = _env(tmp_path)
    root = tmp_path / "demo"
    proc = subprocess.run(["bash", str(DEMO), str(root)], capture_output=True, text=True, env=env, timeout=600)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    assert "shell-worker-cart-discount handed cart-discount to review: 2 files changed" in out
    assert "astra-reviewer (gpt-6-astra) approved cart-discount" in out
    assert "denied (exit 2): Denied: sonnet-builder holds no lease covering this path." in out
    assert "claude-lead [forecast-cache]: Keep the TTL at 600 seconds" in out
    assert "lead session is now demo-lead-session-2" in out
    assert out.count("receipt signed") == 2
    assert "claude-sonnet-5-5: $0.1212, 127,300 tokens" in out
    assert "gpt-6-astra: $0.0748, 54,300 tokens" in out
    assert "Demo finished in" in out
    acme = _show(env, root.resolve() / "acme-shop")
    weather = _show(env, root.resolve() / "weather-api")
    assert acme["cart-discount"]["lane"] == "done"
    assert weather["forecast-cache"]["lane"] == "done"
    for card in (acme["cart-discount"], weather["forecast-cache"]):
        assert card["verifications"][-1]["passed"] is True
        assert card["verifications"][-1]["signature"]
        assert card["approvals"] == ["astra-reviewer"]
    again = subprocess.run(["bash", str(DEMO), str(root)], capture_output=True, text=True, env=env, timeout=60)
    assert again.returncode == 3 and "already holds a demo" in again.stderr
