import io
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from raidlock import tui
from raidlock.core import Board, clock

POSIX_TERMINAL = pytest.mark.skipif(
    os.name != "posix", reason="raid watch terminal handling is unverified on native Windows terminals"
)


@pytest.fixture
def board(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("RAIDLOCK_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("GEMINI_CONFIG_DIR", str(tmp_path / "gemini"))
    monkeypatch.setenv("RAIDLOCK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 1, 1, tzinfo=UTC))
    board, secret = Board.init(tmp_path / "demo", lead="lead", auth=True, env={})
    board.test_lead = board.caller(lead_secret=secret, env={})
    return board


def snapshot(name, actual):
    assert actual == (Path(__file__).parent / (name + ".txt")).read_text()


def test_empty(board):
    snapshot("empty", tui.render(board.snapshot()))


def test_populated(board, monkeypatch):
    lead = board.test_lead
    for name, lane in (("worker", "doing"), ("waiter", "review"), ("blocked", "blocked"), ("stalled", "doing")):
        joined = board.join(lead, agent=name, model_id="test-model")
        board.new_card(lead, "A task " + "x" * 50, card_id=name)
        worker = board.caller(agent=name, token=joined["token"], env={})
        claim = board.claim(name, worker, ttl_minutes=1 if name == "stalled" else 45)
        worker = board.caller(agent=name, cap=claim["cap"], env={})
        if lane != "doing":
            board.move(name, lane, worker)
        if name == "worker":
            board.record_cost(name, worker, usd=1.25)
    board.new_card(lead, "Ready", card_id="ready")
    board.new_card(lead, "Finished", card_id="finished")
    board.move("finished", "done", lead)
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=2))
    snapshot("populated", tui.render(board.snapshot()))


def test_no_color(board, monkeypatch):
    data = board.snapshot()
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert "\033[" in tui.render(data, color=True)
    monkeypatch.setenv("NO_COLOR", "")
    assert tui.render(data, color=True) == tui.render(data)


def test_safe_text(board):
    data = board.snapshot()
    data["workspace"] = "bad\033[2J\nname"
    assert "\033" not in tui.render(data)
    assert "bad [2J name" in tui.render(data)


def test_poll_changes_only(board, monkeypatch):
    output = io.StringIO()
    original = board.snapshot
    calls = []

    def read(**kwargs):
        calls.append(1)
        return original(**kwargs)

    monkeypatch.setattr(board, "snapshot", read)
    polls = []

    def poll(stream, delay):
        polls.append(delay)
        if len(polls) == 2:
            board.new_card(board.test_lead, "New", card_id="new")
        return len(polls) == 3

    monkeypatch.setattr(tui, "_quit", poll)
    assert tui.watch(board, output=output, stdin=io.StringIO()) == 0
    assert len(calls) == 2
    assert output.getvalue().count("Raidlock |") == 2
    assert "\033" not in output.getvalue()


@POSIX_TERMINAL
def test_interrupt_restores_cursor(board, monkeypatch):
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    output = Terminal()

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(tui, "_quit", interrupt)
    assert tui.watch(board, output=output, stdin=io.StringIO()) == 0
    assert output.getvalue().endswith("\033[0m\033[?25h")


def test_workspace(board, monkeypatch):
    monkeypatch.chdir(Path(board.workspace).parent)
    assert tui._open("demo").dir == board.dir


def test_cli_registration(board, monkeypatch):
    monkeypatch.chdir(Path(board.workspace).parent)
    calls = []
    monkeypatch.setattr(tui, "watch", lambda board, interval: calls.append((board.name, interval)) or 0)
    assert tui.main(["watch", "--workspace", "demo", "--interval", "0.2"]) == 0
    assert calls == [("demo", 0.2)]


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "bad"])
def test_invalid_interval(value):
    assert tui.main(["--interval", value]) == 3


@POSIX_TERMINAL
def test_q_pipe():
    import os

    reader, writer = os.pipe()
    with os.fdopen(reader) as stream, os.fdopen(writer, "w") as sink:
        sink.write("q")
        sink.flush()
        assert tui._quit(stream, 0.1)


@POSIX_TERMINAL
def test_q_pipe_above_select_fd_limit():
    import fcntl
    import os

    reader, writer = os.pipe()
    try:
        high_reader = fcntl.fcntl(reader, fcntl.F_DUPFD, 1100)
        with os.fdopen(high_reader) as stream:
            os.write(writer, b"q")
            assert tui._quit(stream, 0.1)
    finally:
        os.close(reader)
        os.close(writer)


def test_board_fields_cannot_inject_terminal_controls(board):
    data = board.snapshot()
    hostile = "name\x1b[2J\r\n\x07"
    data["cards"] = [{"id": hostile, "title": hostile, "lane": "todo", "owner": hostile}]
    data["agents"] = {hostile: {"status": "working", "model": hostile, "card": hostile}}
    rendered = tui.render(data)
    assert "\x1b" not in rendered and "\x07" not in rendered and "\r" not in rendered


@POSIX_TERMINAL
def test_render_failure_restores_cursor(board, monkeypatch):
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    output = Terminal()

    def fail(*args, **kwargs):
        raise RuntimeError("disposable render failure")

    monkeypatch.setattr(board, "snapshot", fail)
    with pytest.raises(RuntimeError, match="render failure"):
        tui.watch(board, output=output, stdin=io.StringIO())
    assert output.getvalue().endswith("\x1b[0m\x1b[?25h")


@POSIX_TERMINAL
def test_keyboard_restores_mode_after_exception(monkeypatch):
    import sys
    from types import SimpleNamespace

    calls = []
    saved = ["dummy terminal mode"]
    terminal = SimpleNamespace(tcgetattr=lambda fd: saved, TCSADRAIN=1, tcsetattr=lambda *args: calls.append(args))
    monkeypatch.setitem(sys.modules, "termios", terminal)
    monkeypatch.setitem(sys.modules, "tty", SimpleNamespace(setcbreak=lambda fd: calls.append(("cbreak", fd))))
    stream = SimpleNamespace(isatty=lambda: True, fileno=lambda: 123)
    with pytest.raises(RuntimeError), tui._keyboard(stream):
        raise RuntimeError("disposable input failure")
    assert calls == [("cbreak", 123), (123, terminal.TCSADRAIN, saved)]
