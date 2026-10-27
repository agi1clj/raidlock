import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from raidlock import __version__, _python, cli

ROOT = Path(__file__).resolve().parents[2]
SHIM = ROOT / "bin" / "raidlock"
SYSTEM = "/usr/bin/python3"
POSIX_SHIM = pytest.mark.skipif(os.name != "posix", reason="the bin shim is a POSIX sh script; Windows is unverified")


def old_system_python():
    if not os.path.exists(SYSTEM):
        return False
    out = subprocess.run([SYSTEM, "-c", "import sys; print(sys.version_info >= (3, 11))"], capture_output=True)
    return out.returncode == 0 and out.stdout.strip() == b"False"


def shim(*args, path, env=None, stdin=b""):
    full = {"PATH": path, "HOME": os.environ.get("HOME", "/"), **(env or {})}
    return subprocess.run(["/bin/sh", str(SHIM), *args], input=stdin, capture_output=True, env=full, timeout=60)


def link(folder, name, target):
    folder.mkdir(exist_ok=True)
    os.symlink(target, folder / name)


def test_every_hook_runs_through_the_bin_shim():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    commands = [h["command"] for groups in hooks.values() for group in groups for h in group["hooks"]]
    assert commands
    for command in commands:
        assert command.startswith('"${CLAUDE_PLUGIN_ROOT}/bin/raidlock" hook ')
        assert "python" not in command
    assert (ROOT / "bin" / "raid").read_text() == SHIM.read_text()
    assert os.access(SHIM, os.X_OK)


@POSIX_SHIM
def test_shim_prefers_the_installed_tools_interpreter(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "raidlock").write_text(f"#!{sys.executable}\nraise SystemExit(9)\n")
    (tools / "raidlock").chmod(0o755)
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "python3").write_text("#!/bin/sh\nexit 1\n")
    (bad / "python3").chmod(0o755)
    out = shim("--version", path=f"{tools}:{bad}:/usr/bin:/bin")
    assert out.returncode == 0, out.stderr
    assert out.stdout.decode().strip() == f"raidlock {__version__}"


@POSIX_SHIM
def test_shim_skips_an_old_python3_for_a_versioned_one(tmp_path):
    bins = tmp_path / "bins"
    link(bins, "python3.12", sys.executable)
    (bins / "python3").write_text("#!/bin/sh\nexit 1\n")
    (bins / "python3").chmod(0o755)
    out = shim("hook", "pre-write", path=f"{bins}:/usr/bin:/bin", stdin=b'{"cwd": "/"}')
    assert out.returncode == 0 and out.stdout == b"" and out.stderr == b""


@POSIX_SHIM
def test_shim_honours_raidlock_python(tmp_path):
    out = shim("--version", path="/usr/bin:/bin", env={"RAIDLOCK_PYTHON": sys.executable})
    assert out.returncode == 0 and f"raidlock {__version__}".encode() in out.stdout


@POSIX_SHIM
def test_shim_without_any_python_says_what_to_install(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    for tool in ("head", "dirname"):
        found = subprocess.run(["/bin/sh", "-c", f"command -v {tool}"], capture_output=True).stdout.decode().strip()
        link(empty, tool, found)
    out = shim("--version", path=str(empty))
    assert out.returncode == 1
    assert b"Python 3.11 or newer" in out.stderr and b"uv tool install raidlock" in out.stderr


@pytest.mark.skipif(not old_system_python(), reason="needs a system python3 older than 3.11")
def test_system_python_gets_a_clear_message_and_guards_fail_closed(tmp_path):
    ws = tmp_path / "ws" / ".board"
    ws.mkdir(parents=True)
    (ws / "board.json").write_text("{}")
    payload = json.dumps({"cwd": str(tmp_path / "ws"), "tool_input": {"file_path": "x"}}).encode()
    denied = shim("hook", "pre-write", path="/usr/bin:/bin", stdin=payload)
    assert denied.returncode == 0, denied.stderr
    reason = json.loads(denied.stdout)["hookSpecificOutput"]
    assert reason["permissionDecision"] == "deny" and "Python 3.11 or newer" in reason["permissionDecisionReason"]
    plain = shim("show", path="/usr/bin:/bin")
    assert plain.returncode == 1 and b"Python 3.11 or newer" in plain.stderr and b"Traceback" not in plain.stderr
    module = subprocess.run(
        [SYSTEM, "-m", "raidlock.hooks", "pre-write"],
        input=payload,
        capture_output=True,
        cwd=str(ROOT / "src"),
        env={"PATH": "/usr/bin:/bin"},
    )
    assert module.returncode == 0 and b"deny" in module.stdout and b"Traceback" not in module.stderr


def run_refuse(monkeypatch, argv, payload):
    out, err = io.StringIO(), io.StringIO()

    class Input(io.StringIO):
        def isatty(self):
            return False

    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(sys, "stdin", Input(payload))
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    with pytest.raises(SystemExit) as stop:
        _python.refuse((99, 0))
    return stop.value.code, out.getvalue(), err.getvalue()


def test_refuse_denies_guards_inside_a_workspace(tmp_path, monkeypatch):
    (tmp_path / ".board").mkdir()
    (tmp_path / ".board" / "board.json").write_text("{}")
    code, out, _ = run_refuse(monkeypatch, ["-c", "hook", "pre-bash"], json.dumps({"cwd": str(tmp_path)}))
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "Python 99.0 or newer" in out


def test_refuse_outside_a_workspace_exits_with_the_message(tmp_path, monkeypatch):
    code, out, err = run_refuse(monkeypatch, ["-c", "hook", "pre-write"], json.dumps({"cwd": str(tmp_path)}))
    assert code == 1 and out == "" and "Python 99.0 or newer" in err
    code, out, err = run_refuse(monkeypatch, ["-c", "show"], "")
    assert code == 1 and "uv tool install raidlock" in err


def test_hook_subcommand_is_silent_outside_a_board(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(tmp_path), "tool_input": {}})))
    assert cli.main(["hook", "pre-write"]) == 0
    assert capsys.readouterr() == ("", "")
    assert cli.main(["hook"]) == 3
