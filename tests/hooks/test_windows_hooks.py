import json

import pytest

from raidlock import cli


@pytest.mark.parametrize("event", ["pre-write", "pre-read", "pre-bash", "pre-urgent"])
def test_native_windows_guard_hooks_fail_closed(monkeypatch, capsys, event):
    monkeypatch.setattr(cli.sys, "platform", "win32")
    assert cli.main(["hook", event]) == 0
    decision = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"
    assert "WSL2" in decision["permissionDecisionReason"]


@pytest.mark.parametrize("event", ["post-tool", "subagent-stop", "session-start", "session-end"])
def test_native_windows_other_hooks_exit_quietly(monkeypatch, capsys, event):
    monkeypatch.setattr(cli.sys, "platform", "win32")
    assert cli.main(["hook", event]) == 0
    assert capsys.readouterr().out == ""
