import contextlib
import os
import shlex
import signal
import subprocess
import sys
import time

import pytest

from raidlock.runners import isolation


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX sessions")
@pytest.mark.parametrize("hold_pipes", [False, True])
def test_sid_escaped_verifier_fails_receipt_and_cleanup_is_bounded(tmp_path, hold_pipes):
    script = tmp_path / "escape.py"
    pidfile = tmp_path / "child.pid"
    script.write_text(
        "import os, time\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    os.setsid()\n"
        f"    open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        + ("" if hold_pipes else "    os.close(1)\n    os.close(2)\n")
        + "    time.sleep(30)\n"
        "else:\n"
        f"    while not os.path.exists({str(pidfile)!r}): time.sleep(0.01)\n"
    )
    started = time.monotonic()
    try:
        code, tail, _ = isolation.run(
            f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}", str(tmp_path), os.environ, 0.5
        )
        assert code is None
        assert "stray verification processes detected" in tail
        assert time.monotonic() - started < 6
    finally:
        if pidfile.exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(pidfile.read_text()), signal.SIGKILL)


def test_cleanup_bounds_even_unkillable_pipe_holders(tmp_path, monkeypatch):
    calls = []

    class Process:
        pid = 99999999
        stdout = None
        returncode = None

        def communicate(self, *, timeout):
            calls.append(("communicate", timeout))
            raise subprocess.TimeoutExpired("verify", timeout, output=b"partial")

        def wait(self, *, timeout):
            calls.append(("wait", timeout))
            raise subprocess.TimeoutExpired("verify", timeout)

    monkeypatch.setattr(isolation.subprocess, "Popen", lambda *a, **kw: Process())
    monkeypatch.setattr(isolation, "_descendants", lambda *a: {})
    monkeypatch.setattr(isolation, "_kill", lambda *a: None)
    code, tail, _ = isolation.run("true", str(tmp_path), {}, 0)
    assert code is None
    assert "stray verification processes detected" in tail
    assert calls == [("communicate", isolation.CLEANUP_TIMEOUT), ("wait", isolation.CLEANUP_TIMEOUT)]
