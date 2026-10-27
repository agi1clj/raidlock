import os
import stat
import threading
import time
from typing import Self

from .errors import Timeout
from .portable import NONBLOCK, open_nofollow

try:
    import fcntl
except ImportError:
    fcntl = None
    import msvcrt

_held = {}
_guard = threading.Lock()


def _try_lock(fd):
    try:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def _unlock(fd):
    if fcntl:
        fcntl.flock(fd, fcntl.LOCK_UN)
    else:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


class FileLock:
    """Exclusive OS lock on a persistent file, reentrant within one process and thread."""

    def __init__(self, path, timeout=30.0):
        self.path = os.path.abspath(path)
        self.timeout = timeout

    def __enter__(self) -> Self:
        key = (self.path, threading.get_ident())
        with _guard:
            entry = _held.get(key)
            if entry:
                entry[1] += 1
                return self
        fd = self._acquire()
        with _guard:
            _held[key] = [fd, 1]
        return self

    def _acquire(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        deadline = time.monotonic() + self.timeout
        delay = 0.005
        while True:
            fd = open_nofollow(self.path, os.O_RDWR | os.O_CREAT | NONBLOCK, 0o600)
            acquired = False
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode):
                    raise OSError("lock must be a regular file")
                acquired = _try_lock(fd)
                if acquired:
                    opened = os.fstat(fd)
                    try:
                        named = os.lstat(self.path)
                    except FileNotFoundError:
                        named = None
                    if named and (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino):
                        return fd
            except BaseException:
                os.close(fd)
                raise
            if acquired:
                _unlock(fd)
            os.close(fd)
            if time.monotonic() >= deadline:
                raise Timeout(f"lock busy: {self.path}", lock=self.path)
            time.sleep(delay)
            delay = min(delay * 2, 0.1)

    def __exit__(self, *exc):
        key = (self.path, threading.get_ident())
        with _guard:
            entry = _held[key]
            entry[1] -= 1
            if entry[1]:
                return
            del _held[key]
        _unlock(entry[0])
        os.close(entry[0])
