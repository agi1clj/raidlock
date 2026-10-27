import argparse
import base64
import datetime
import os
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from raidlock import notify, snapshot
from raidlock.core import Board, Usage, clock


@pytest.fixture
def board(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("RAIDLOCK_") or name in ("BOARD_DIR", "BOARD_PROJECT"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("RAIDLOCK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("GEMINI_CONFIG_DIR", str(tmp_path / "gemini"))
    monkeypatch.chdir(tmp_path)
    clock.reset()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result, token = Board.init(workspace, lead="lead", auth=False)
    monkeypatch.setenv("RAIDLOCK_LEAD_SECRET", token)
    result.env["RAIDLOCK_LEAD_SECRET"] = token
    yield result
    clock.reset()


def ctx(board, agent=None):
    return SimpleNamespace(board=board, caller=lambda: board.caller(agent=agent))


def test_events_and_debounce(board):
    lead = board.caller(agent="lead")
    board.new_card(lead, "An example", card_id="example")
    lead.fence = board.claim("example", lead, ttl_minutes=6)["fence"]
    os_call = Mock(return_value=True)
    watcher = notify.Watcher(board, os_call)
    assert watcher.poll() == 0
    board.move("example", "review", lead)
    assert watcher.poll() == 1
    assert "entered review" in os_call.call_args.args[0]
    assert watcher.poll() == 0
    board.move("example", "blocked", lead)
    watcher.poll()
    board.move("example", "review", lead)
    assert watcher.poll() == 0
    assert watcher.poll(monotonic=time.monotonic() + 120) == 0
    board.send(lead, "lead", "Please check this")
    assert watcher.poll() == 1
    assert os_call.call_args.args[1] == "Please check this"
    assert watcher.poll() == 0


def test_time_events_without_version_change(board):
    lead = board.caller(agent="lead")
    board.new_card(lead, "Timer", card_id="timer")
    board.claim("timer", lead, ttl_minutes=6)
    os_call = Mock(return_value=True)
    watcher = notify.Watcher(board, os_call)
    now = clock.now()
    assert watcher.poll(now=now) == 0
    version = board.version()
    assert watcher.poll(now=now + datetime.timedelta(seconds=61)) == 1
    assert "Lease expires" in os_call.call_args.args[0]
    assert watcher.poll(now=now + datetime.timedelta(seconds=62)) == 0
    clock.travel(361)
    assert board.version() == version
    assert watcher.poll(now=clock.now()) == 1
    assert "Agent stalled" in os_call.call_args.args[0]
    assert watcher.poll(now=clock.now()) == 0


@pytest.mark.parametrize(
    "platform,available,expected",
    [
        ("darwin", {"terminal-notifier", "osascript"}, "terminal-notifier"),
        ("darwin", {"osascript"}, "osascript"),
        ("linux", {"notify-send"}, "notify-send"),
        ("win32", {"powershell"}, "powershell"),
    ],
)
def test_notification_backends(monkeypatch, platform, available, expected):
    monkeypatch.setattr(notify.sys, "platform", platform)
    monkeypatch.setattr(notify.shutil, "which", lambda name: name if name in available else None)
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(notify.subprocess, "run", run)
    title, body = 'title "quoted"', '<tag> "hello"; $(bad)'
    assert notify.send_notification(title, body)
    command = run.call_args.args[0]
    assert command[0] == expected
    assert run.call_args.kwargs["timeout"] == 10
    assert not run.call_args.kwargs.get("shell")
    if platform == "win32":
        script = base64.b64decode(command[-1]).decode("utf-16le")
        assert "ToastNotificationManager" in script
        assert body not in script
    else:
        assert body in command


def test_unavailable_and_failed_notifications(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setattr(notify.shutil, "which", lambda name: None)
    run = Mock(side_effect=OSError("no desktop"))
    monkeypatch.setattr(notify.subprocess, "run", run)
    assert not notify.send_notification("title", "body")
    run.assert_not_called()
    monkeypatch.setattr(notify.shutil, "which", lambda name: name)
    assert not notify.send_notification("title", "body")


def test_snapshot_and_redaction(board, tmp_path, monkeypatch):
    instant = clock.now()
    monkeypatch.setattr(clock, "now", lambda: instant)
    lead = board.caller(agent="lead")
    board.new_card(
        lead, "Fix <script>alert(1)</script> user@example.com /home/private src/app.py C:\\private\\file", card_id="fix"
    )
    lead.fence = board.claim("fix", lead)["fence"]
    board.note("fix", lead, "PRIVATE NOTE with user@example.com and /private/file")
    board.record_cost("fix", lead, usage=Usage(input=10, output=2), usd=1.25)
    data = board.snapshot()
    raw = snapshot.render_snapshot(data)
    assert "PRIVATE NOTE" in raw
    assert "<script>" not in raw
    assert "&lt;script&gt;" in raw
    redacted = snapshot.render_snapshot(data, redact=True)
    for section in ("Cards", "Todo", "Doing", "Review", "Blocked", "Done", "Agents", "Spend", "1.25"):
        assert section in redacted
    for private in (
        "PRIVATE NOTE",
        "user@example.com",
        "/home/private",
        "src/app.py",
        "C:\\private\\file",
        str(board.dir),
    ):
        assert private not in redacted
    out = tmp_path / "snapshot.html"
    result, text = snapshot._handle(ctx(board), SimpleNamespace(out=str(out), redact=True))
    assert result == {"out": str(out), "redacted": True}
    assert out.read_text() == redacted
    assert "http://" not in redacted and "https://" not in redacted


def test_register_cli_and_once(board, monkeypatch, tmp_path):
    registered = {}

    def add_command(name, help_text, setup, handler):
        parser = argparse.ArgumentParser()
        setup(parser)
        registered[name] = (parser, handler)

    notify.register_cli(add_command)
    snapshot.register_cli(add_command)
    parser, handler = registered["notify"]
    monkeypatch.setattr(notify, "send_notification", Mock(return_value=True))
    monkeypatch.setattr(notify.time, "sleep", Mock(side_effect=AssertionError("once must not sleep")))
    assert handler(SimpleNamespace(board=board), parser.parse_args(["--once"])) == ({"notifications": 0}, "")
    parser, handler = registered["snapshot"]
    out = tmp_path / "export.html"
    result, _ = handler(ctx(board), parser.parse_args(["--out", str(out), "--redact"]))
    assert out.exists() and result["redacted"]


def test_entry_point_discovery(monkeypatch):
    import importlib.metadata

    from raidlock import cli

    points = [
        SimpleNamespace(name="notify", load=lambda: notify.register_cli),
        SimpleNamespace(name="snapshot", load=lambda: snapshot.register_cli),
    ]
    select = Mock(return_value=points)
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda: SimpleNamespace(select=select))
    monkeypatch.setattr(cli, "EXTENSION_MODULES", ())
    monkeypatch.setattr(cli, "COMMANDS", {})
    cli.load_extensions()
    select.assert_called_once_with(group="raidlock.commands")
    assert set(cli.COMMANDS) == {"notify", "snapshot"}
    assert all(not command["mutating"] for command in cli.COMMANDS.values())


def test_transcript_cursor_and_recipient(board):
    from raidlock.core.transcript import make

    send = Mock(return_value=True)
    watcher = notify.Watcher(board, send)
    assert watcher.poll() == 0
    for i in range(205):
        board.transcript.append(make("note", {"agent": "lead"}, f"note {i}"))
    board.transcript.append(make("msg", {"agent": "lead"}, "worker only", to="worker"))
    board.transcript.append(make("msg", {"agent": "worker"}, "lead only", to="lead"))
    assert watcher.poll() == 1
    assert send.call_args.args[1] == "lead only"
    assert watcher.poll() == 0
    board.transcript.append(make("msg", {"agent": "worker"}, "new message", to="lead"))
    assert watcher.poll() == 1
    assert send.call_args.args[1] == "new message"


def test_snapshot_write_error(board, tmp_path):
    from raidlock.core import Precondition

    with pytest.raises(Precondition, match="cannot write snapshot"):
        snapshot._handle(ctx(board), SimpleNamespace(out=str(tmp_path / "missing" / "out.html"), redact=True))


@pytest.fixture
def worker_env(board, monkeypatch):
    monkeypatch.delenv("RAIDLOCK_LEAD_SECRET")
    monkeypatch.setenv("RAIDLOCK_AGENT", "worker")
    board.env = dict(os.environ)
    return board


def test_snapshot_outside_requires_lead(worker_env, tmp_path):
    from raidlock.core import IdentityError

    out = tmp_path / "outside.html"
    with pytest.raises(IdentityError, match="only the lead"):
        snapshot._handle(ctx(worker_env), SimpleNamespace(out=str(out), redact=True))
    assert not out.exists()


def test_snapshot_unleased_home_and_workspace_denied(worker_env):
    from raidlock.core import Precondition

    for folder in (os.path.join(worker_env.home, "exports"), worker_env.workspace):
        os.makedirs(folder, exist_ok=True)
        out = os.path.join(folder, "snap.html")
        with pytest.raises(Precondition, match="current lease"):
            snapshot._handle(ctx(worker_env), SimpleNamespace(out=out, redact=True))
        assert not os.path.exists(out)


def test_snapshot_escape_refused(worker_env, tmp_path):
    from raidlock.core import IdentityError

    dotdot = os.path.join(worker_env.workspace, "..", "escape.html")
    with pytest.raises(IdentityError):
        snapshot._handle(ctx(worker_env), SimpleNamespace(out=dotdot, redact=True))
    link = os.path.join(worker_env.workspace, "link")
    os.symlink(tmp_path / "elsewhere", link)
    (tmp_path / "elsewhere").mkdir()
    with pytest.raises(IdentityError):
        snapshot._handle(ctx(worker_env), SimpleNamespace(out=os.path.join(link, "x.html"), redact=True))
    assert not (tmp_path / "escape.html").exists()
    assert not (tmp_path / "elsewhere" / "x.html").exists()


def test_snapshot_lead_may_write_outside(board, tmp_path):
    out = tmp_path / "anywhere" / "lead.html"
    out.parent.mkdir()
    result, _ = snapshot._handle(ctx(board), SimpleNamespace(out=str(out), redact=True))
    assert result["out"] == os.path.realpath(out)
    assert out.exists()


@pytest.mark.parametrize(
    "location", ["board", "git", "token", "board-link", "token-link", "board-case", "git-case", "token-case"]
)
def test_snapshot_cannot_overwrite_protected_state(board, tmp_path, location):
    from pathlib import Path

    from raidlock.core import Precondition

    if location == "board-case":
        target = Path(board.workspace) / ".BOARD" / "board.json"
    elif location == "git-case":
        target = Path(board.workspace) / "app" / ".GIT" / "config"
    elif location == "token-case":
        target = Path(board.home) / "TOKENS" / "lead.token"
    elif location.startswith("board"):
        target = Path(board.dir) / "board.json"
    elif location == "git":
        target = Path(board.workspace) / "app" / ".git" / "config"
    else:
        target = Path(board.home) / "tokens" / "lead.token"
    target.parent.mkdir(parents=True, exist_ok=True)
    original = target.read_bytes() if target.exists() else b"protected state\n"
    target.write_bytes(original)
    output = target
    if location.endswith("-link"):
        output = Path(board.workspace) / "export.html"
        output.symlink_to(target)
    with pytest.raises(Precondition, match="cannot overwrite"):
        snapshot._handle(ctx(board), SimpleNamespace(out=str(output), redact=True))
    assert target.read_bytes() == original


@pytest.mark.parametrize("name", ["raidlock.db", "raidlock.db-wal", "raidlock.db-shm", "registry.json"])
def test_snapshot_cannot_overwrite_home_state(worker_env, name):
    from pathlib import Path

    from raidlock.core import Precondition, db

    db.close_all()
    target = Path(worker_env.home) / name
    original = target.read_bytes() if target.exists() else b"protected state\n"
    target.write_bytes(original)
    with pytest.raises(Precondition, match="must be under exports/"):
        snapshot._handle(ctx(worker_env), SimpleNamespace(out=str(target), redact=True))
    assert target.read_bytes() == original


def test_snapshot_atomic_replace_preserves_hardlinked_credentials(board):
    worker_env = board
    from pathlib import Path

    token = Path(worker_env.home) / "tokens" / "lead.token"
    token.parent.mkdir(parents=True, exist_ok=True)
    original = b"test-only-private-token\n"
    token.write_bytes(original)
    output = Path(worker_env.workspace) / "export.html"
    os.link(token, output)
    snapshot._handle(ctx(worker_env), SimpleNamespace(out=str(output), redact=True))
    assert token.read_bytes() == original
    assert output.read_text().startswith("<!doctype html>")
    assert not os.path.samefile(token, output)
