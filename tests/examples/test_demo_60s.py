import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "scripts" / "demo-60s.sh"


def _sandbox():
    if sys.platform == "darwin":
        return os.access("/usr/bin/sandbox-exec", os.X_OK)
    return sys.platform.startswith("linux") and bool(shutil.which("bwrap"))


pytestmark = [
    pytest.mark.skipif(not shutil.which("git") or not shutil.which("bash"), reason="needs git and bash"),
    pytest.mark.skipif(os.name != "posix", reason="the demo is documented for Linux and macOS"),
    pytest.mark.skipif(not _sandbox(), reason="raid verify needs bubblewrap or sandbox-exec"),
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
    env.update(PATH=f"{shim}{os.pathsep}{env.get('PATH', '')}", RAIDLOCK_TOKEN="stale", RAIDLOCK_AGENT="intruder")
    return env


def _show(env, root, card):
    proc = subprocess.run(
        ["raidlock", "show", card, "--board", str(root / "acme-shop"), "--json", "--fields", "all"],
        capture_output=True,
        text=True,
        env={**env, "RAIDLOCK_HOME": str(root / "home")},
        timeout=60,
        check=True,
    )
    return json.loads(proc.stdout)["card"]


def test_demo_routes_on_quota_hands_off_and_gates_done_on_a_receipt(tmp_path):
    env = _env(tmp_path)
    root = tmp_path / "demo"
    proc = subprocess.run(["bash", str(DEMO), str(root)], capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    assert "raid next: cart-discount -> claude" in out
    assert "blocked: agent exited 1: Claude usage limit reached" in out
    assert "cart-discount: quota on claude -> codex as res1-codex-cart-discount, now in review" in out
    assert "raid next: receipt-tax -> codex, fallback=True" in out
    assert "raid done -> exit 3: raidlock: cart-discount needs a valid verification receipt" in out
    assert "verify passed=True" in out and "cart-discount is done" in out
    rows = {line.split()[0]: line.split() for line in out.splitlines() if line.strip().startswith(("gpt-", "claude-"))}
    assert rows["gpt-6-astra"][5] == "1" and rows["claude-sonnet-5-5"][5] == "0"
    card = _show(env, root, "cart-discount")
    assert card["lane"] == "done"
    assert card["approvals"] == ["claude-reviewer"]
    assert _show(env, root, "receipt-tax")["lane"] == "todo"


def test_demo_refuses_a_folder_that_already_holds_one(tmp_path):
    env = _env(tmp_path)
    root = tmp_path / "demo"
    (root / "acme-shop").mkdir(parents=True)
    proc = subprocess.run(["bash", str(DEMO), str(root)], capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 3 and "already holds a demo" in proc.stderr
