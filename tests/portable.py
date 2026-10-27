import os
import shutil
import stat

SH = "/bin/sh" if os.name == "posix" else (shutil.which("sh") or "sh")


def assert_mode(path, mode, follow=True):
    """Exact POSIX permission bits; on Windows only the owner-write bit exists, so check that instead."""
    info = os.stat(path) if follow else os.lstat(path)
    if os.name == "posix":
        assert stat.S_IMODE(info.st_mode) == mode
    else:
        assert bool(info.st_mode & stat.S_IWRITE) == bool(mode & 0o200)
