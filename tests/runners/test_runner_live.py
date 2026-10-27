import os
import shutil

import pytest

from raidlock.runners.runner import delegate

pytestmark = pytest.mark.skipif(os.environ.get("RAIDLOCK_LIVE") != "1", reason="live CLI run; set RAIDLOCK_LIVE=1")

HOME_KEYS = ("CODEX_HOME", "CLAUDE_CONFIG_DIR")


@pytest.fixture
def real_cli_homes(monkeypatch):
    for key in HOME_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.skipif(shutil.which("codex") is None, reason="codex not installed")
def test_live_codex_read_only_then_workspace_write(real_cli_homes, ws):
    ws.card("audit", locks="app/src", scope="Read src/main.py and report what it prints. Change nothing.")
    r = delegate(ws.board, ws.lead, "audit", "codex", sandbox="read-only", timeout=600)
    assert r["lane"] == "review" and r["paths_changed"] == [], r
    assert r["usage"]["input"] > 0
    ws.card(
        "notes",
        locks="app/notes",
        scope="Create notes/hello.txt containing the single line 'hi'.",
        verify_cmd="grep -qx hi notes/hello.txt",
    )
    r = delegate(ws.board, ws.lead, "notes", "codex", timeout=600)
    assert r["lane"] == "review" and r["paths_changed"] == ["notes/hello.txt"] and r["verify"]["passed"], r
    assert os.path.getsize(r["patch"]) > 0


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude not installed")
def test_live_claude_workspace_write(real_cli_homes, ws):
    ws.card("notes", locks="app/notes", scope="Create notes/hello.txt containing the single line 'hi'.")
    r = delegate(ws.board, ws.lead, "notes", "claude", model="claude-haiku-5-5", timeout=600)
    assert r["lane"] == "review" and r["paths_changed"] == ["notes/hello.txt"], r
    with open(os.path.join(r["run_dir"], "events.jsonl"), encoding="utf-8") as f:
        events = f.read()
    assert "hook_started" not in events
