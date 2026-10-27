import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from raidlock.core import Board, lead
from raidlock.core.errors import IdentityError

NATIVE = sys.platform == "darwin" or os.path.isdir("/proc/self")
PS_ONLY = pytest.mark.skipif(
    os.name != "posix", reason="the ps fallback is POSIX-only; Windows process anchors are unverified"
)


@PS_ONLY
def test_ps_timeout_retries_once(monkeypatch):
    monkeypatch.setattr(lead.sys, "platform", "linux")
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout="    1 Thu Oct 22 15:00:00 2026 /bin/zsh\n")

    monkeypatch.setattr(lead.shutil, "which", lambda name: "/bin/ps")
    monkeypatch.setattr(lead.subprocess, "run", run)
    assert lead._ps_row(100) == (1, "Thu Oct 22 15:00:00 2026", "/bin/zsh")
    assert len(calls) == 2
    assert calls[0][0][0][-2:] == ["-p", "100"]
    assert all(kwargs["timeout"] == 5 for _, kwargs in calls)


@PS_ONLY
def test_ps_repeated_timeout_remains_unavailable(monkeypatch):
    monkeypatch.setattr(lead.sys, "platform", "linux")
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(lead.shutil, "which", lambda name: "/bin/ps")
    monkeypatch.setattr(lead.subprocess, "run", run)
    assert lead._ps_row(100) is None
    assert len(calls) == 2


@PS_ONLY
def test_ps_error_does_not_accept_partial_output(monkeypatch):
    monkeypatch.setattr(lead.sys, "platform", "linux")
    monkeypatch.setattr(lead.shutil, "which", lambda name: "/bin/ps")
    monkeypatch.setattr(
        lead.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout="1 Thu Oct 22 15:00:00 2026 /bin/zsh\n"),
    )
    assert lead._ps_row(100) is None


def broken_ps(monkeypatch, mode):
    real = subprocess.run

    def run(argv, *args, **kwargs):
        if argv and os.path.basename(str(argv[0])) == "ps":
            if mode == "hangs":
                time.sleep(0.2)
                raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 5))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(lead.subprocess, "run", run)


@pytest.mark.skipif(not NATIVE, reason="needs sysctl kern.proc or /proc")
@pytest.mark.parametrize("mode", ["empty", "hangs"])
def test_anchor_comes_from_the_kernel_when_ps_is_empty_or_slow(monkeypatch, mode):
    broken_ps(monkeypatch, mode)
    table = lead.process_table()
    assert table
    chain = lead.ancestors(table)
    assert chain[0][0] == os.getppid()
    sid, pid, start = lead.session_of({})
    assert sid and pid and start
    assert lead.alive(pid, start)
    assert not lead.alive(pid, start + "x")


@pytest.mark.skipif(not NATIVE, reason="needs sysctl kern.proc or /proc")
@pytest.mark.parametrize("mode", ["empty", "hangs"])
def test_lead_commands_work_when_ps_is_empty_or_slow(tmp_path, monkeypatch, mode):
    broken_ps(monkeypatch, mode)
    monkeypatch.setattr(lead.shutil, "which", lambda name: None)
    board, secret = Board.init(tmp_path / "ws")
    for _ in range(3):
        caller = Board.open(tmp_path / "ws").caller(lead_secret=secret)
        assert caller.is_lead
    board.add_team(caller, "core", 1)
    assert board.teams()["core"]["max"] == 1


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sysctl")
def test_sysctl_preserves_microseconds():
    row = lead._sysctl_row(os.getpid())
    assert row is not None and row[0] == os.getppid()
    assert row[1].startswith("darwin:")
    assert len(row[1].split(".")[1]) == 6
    assert lead._sysctl_row(2**31 - 2) is None


def test_proc_parser_reads_a_linux_tree(tmp_path):
    (tmp_path / "self").mkdir()
    (tmp_path / "42").mkdir()
    fields = ["S", "7"] + [str(n) for n in range(17)] + ["123456", "0"]
    (tmp_path / "42" / "stat").write_text("42 (my (odd) shell) " + " ".join(fields) + "\n")
    assert lead._proc_row(42, root=str(tmp_path)) == (7, "123456", "my (odd) shell")
    assert lead._proc_row(43, root=str(tmp_path)) is None


def test_live_table_falls_back_to_ps_only_without_a_kernel_source(monkeypatch):
    monkeypatch.setattr(lead, "_NATIVE", {})
    for name in ("_proc_row", "_sysctl_row", "_toolhelp_row"):
        monkeypatch.setattr(lead, name, lambda pid: None)
    assert lead.row_lookup() is lead._ps_row
    monkeypatch.setattr(lead, "_NATIVE", {})
    monkeypatch.setattr(lead, "_sysctl_row", lambda pid: (1, "start", "zsh"))
    assert lead.row_lookup() is lead._sysctl_row


def test_live_table_memoizes_rows():
    calls = []

    def lookup(pid):
        calls.append(pid)
        return {10: (5, "a", "zsh"), 5: (1, "b", "claude")}.get(pid)

    table = lead.LiveTable(lookup)
    assert lead.ancestors(table, 10) == [(5, "b", "claude")]
    assert lead.ancestors(table, 10) == [(5, "b", "claude")]
    assert 99 not in table
    assert calls.count(10) == 1 and calls.count(5) == 1


def test_unavailable_process_source_cannot_authorize_a_lead(world, monkeypatch):
    monkeypatch.setattr(lead, "process_table", lambda: lead.LiveTable(lambda pid: None))
    with pytest.raises(IdentityError, match="process anchor"):
        world.board.add_team(world.lead, "denied", 1)
    assert "denied" not in world.board.teams()


def test_init_without_process_anchor_can_be_retried(tmp_path, monkeypatch):
    workspace = tmp_path / "retry-init"
    with monkeypatch.context() as unavailable:
        unavailable.setattr(lead, "process_table", lambda: {})
        with pytest.raises(IdentityError, match="process anchor"):
            Board.init(workspace)
    assert not (workspace / ".board" / "board.json").exists()
    assert not (workspace / ".board" / "lead.json").exists()
    board, secret = Board.init(workspace)
    caller = board.caller(lead_secret=secret)
    board.add_team(caller, "ready", 1)
    assert board.teams()["ready"]["max"] == 1


def test_macos_pid_reuse_within_one_second_invalidates_lease(tmp_path, monkeypatch):
    import ctypes

    from raidlock.core.errors import StaleLead

    monkeypatch.setattr(lead.sys, "platform", "darwin")
    micros = [123456]

    class Kernel:
        def sysctl(self, mib, count, buf, size, new, length):
            raw = bytearray(lead.KINFO_SIZE)
            raw[:8] = (1800000000).to_bytes(8, sys.byteorder, signed=True)
            raw[8:16] = micros[0].to_bytes(8, sys.byteorder, signed=True)
            raw[lead.KINFO_PPID : lead.KINFO_PPID + 4] = (1).to_bytes(4, sys.byteorder)
            ctypes.memmove(buf, bytes(raw), len(raw))
            return 0

    monkeypatch.setattr(lead, "_libc", lambda: Kernel())
    original = lead._sysctl_row(42)
    manager = lead.Lead(str(tmp_path), str(tmp_path / "secret"), "test", 600, table={42: original})
    secret = manager.establish("session", 42, original[1])
    micros[0] += 1
    replacement = lead._sysctl_row(42)
    assert original[1] != replacement[1]
    manager._table = {42: replacement}
    with pytest.raises(StaleLead, match="expired"):
        manager.check(secret, "session", pid=42, start=replacement[1])
    lease = manager.lease()
    lease["pid_start"] = lead.lstart(1800000000)
    manager._save(lease)
    with pytest.raises(StaleLead, match="lacks microseconds.*re-establish.*takeover"):
        manager.check(secret, "session", pid=42, start=lease["pid_start"])
    assert manager.check(secret, "session", any_session=True) == 1
    manager.takeover("session", 42, replacement[1])
    assert manager.check(manager.current_secret(), "session", pid=42, start=replacement[1]) == 2


def test_macos_never_falls_back_to_second_resolution_ps(monkeypatch):
    monkeypatch.setattr(lead.sys, "platform", "darwin")
    assert lead._ps_row(42) is None
