import errno
import os
import shutil
import stat
import sys

NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
NONBLOCK = getattr(os, "O_NONBLOCK", 0)
BINARY = getattr(os, "O_BINARY", 0)


def _redirected(path):
    junction = getattr(os.path, "isjunction", None)
    return os.path.islink(path) or bool(junction and junction(path))


def open_nofollow(path, flags, mode=0o600):
    """Open a path without following a final symlink, with an lstat check where O_NOFOLLOW is missing."""
    if NOFOLLOW:
        return os.open(path, flags | NOFOLLOW | BINARY, mode)
    if _redirected(path):
        raise OSError(errno.ELOOP, "refusing to follow a symlink", path)
    fd = os.open(path, flags | BINARY, mode)
    try:
        opened = os.fstat(fd)
        named = os.lstat(path)
        if _redirected(path) or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise OSError(errno.ELOOP, "refusing to follow a symlink", path)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _writable_retry(function, path, exc):
    if isinstance(exc, tuple):
        exc = exc[1]
    if os.path.islink(path) or not isinstance(exc, PermissionError):
        raise exc
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    function(path)


def remove_tree(path, ignore_errors=False):
    """shutil.rmtree that also removes read-only files, such as git objects on Windows."""
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_writable_retry)
        else:
            shutil.rmtree(path, onerror=_writable_retry)
    except OSError:
        if not ignore_errors:
            raise
