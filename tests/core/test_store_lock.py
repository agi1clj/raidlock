import json
import os
import subprocess
import sys
import textwrap

import pytest
from corehelpers import SRC

from raidlock.core import store
from raidlock.core.errors import Precondition, Timeout
from raidlock.core.lock import FileLock


def test_atomic_write_utf8_and_no_temp_left(tmp_path):
    p = tmp_path / "d" / "x.json"
    store.write_json(str(p), {"schema": 2, "t": "héllo ✓"})
    assert json.loads(p.read_text(encoding="utf-8"))["t"] == "héllo ✓"
    assert [f for f in os.listdir(p.parent)] == ["x.json"]


def test_newer_schema_refused(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"schema": 99, "cards": {}}), encoding="utf-8")
    with pytest.raises(Precondition):
        store.read_doc(str(p), "cards")


def test_corrupt_json_is_precondition(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{", encoding="utf-8")
    with pytest.raises(Precondition):
        store.read_json(str(p))


def test_lock_is_reentrant(tmp_path):
    lock = str(tmp_path / ".lock")
    with FileLock(lock):
        with FileLock(lock, timeout=0.2):
            pass
        with FileLock(lock, timeout=0.2):
            pass


def test_lock_excludes_other_process(tmp_path):
    lock = str(tmp_path / ".lock")
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {SRC!r})
        from raidlock.core.lock import FileLock
        with FileLock({lock!r}):
            print("held", flush=True)
            time.sleep(3)
    """)
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(Timeout), FileLock(lock, timeout=0.3):
            pass
    finally:
        proc.wait(10)
    with FileLock(lock, timeout=1):
        pass


def test_lock_released_when_holder_dies(tmp_path):
    lock = str(tmp_path / ".lock")
    code = (
        f"import sys,os; sys.path.insert(0,{SRC!r}); from raidlock.core.lock import FileLock; "
        f"held=FileLock({lock!r}); held.__enter__(); os._exit(0)"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
    with FileLock(lock, timeout=1):
        pass


def test_lock_rejects_symlink_and_nonregular_file(tmp_path):
    target = tmp_path / "victim"
    target.write_text("untouched")
    link = tmp_path / "lock"
    link.symlink_to(target)
    with pytest.raises(OSError), FileLock(link):
        pytest.fail("symlink lock acquired")
    link.unlink()
    if hasattr(os, "mkfifo"):
        os.mkfifo(link)
        with pytest.raises(OSError), FileLock(link):
            pytest.fail("FIFO lock acquired")
    assert target.read_text() == "untouched"


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot unlink a lock file another handle holds open")
def test_lock_retries_replaced_inode_after_acquisition(tmp_path, monkeypatch):
    from raidlock.core import lock

    path = tmp_path / "lock"
    original = lock._try_lock
    calls = []

    def replace_once(fd):
        acquired = original(fd)
        calls.append(os.fstat(fd).st_ino)
        if len(calls) == 1:
            path.unlink()
            path.write_text("")
        return acquired

    monkeypatch.setattr(lock, "_try_lock", replace_once)
    with FileLock(path):
        assert len(calls) == 2
        assert calls[0] != calls[1] == path.stat().st_ino
