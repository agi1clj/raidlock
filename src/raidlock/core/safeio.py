import contextlib
import errno
import os
import secrets
import stat
from pathlib import Path

from . import portable
from .errors import Precondition

_DIR_FD = os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd


def _system_link(component):
    aliases = {Path("/", name): Path("/private", name) for name in ("var", "tmp", "etc")}
    if component not in aliases:
        return False
    try:
        link = component.lstat()
        parent = component.parent.lstat()
    except OSError:
        return False
    return (
        stat.S_ISLNK(link.st_mode)
        and os.readlink(component) in (str(aliases[component]), str(aliases[component]).lstrip("/"))
        and link.st_uid == 0
        and parent.st_uid == 0
        and not parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        and component.parent == Path(component.anchor)
    )


def check(path, tree=False):
    path = Path(os.path.abspath(path))
    if len(path.parts) > 1:
        top = Path(path.anchor, path.parts[1])
        if _system_link(top):
            path = Path("/private", top.name, *path.parts[2:])
    for component in (*reversed(path.parents), path):
        try:
            mode = component.lstat().st_mode
        except FileNotFoundError:
            continue
        if portable._redirected(component) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise Precondition(f"unsafe filesystem entry: {component}")
        if component != path and not stat.S_ISDIR(mode):
            raise Precondition(f"not a directory: {component}")
    if tree and path.is_dir():
        for child in path.iterdir():
            check(child, tree=True)
    return path


def _directory(path, create=False):
    path = check(path)
    nofollow = getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags = os.O_RDONLY | nofollow
    search = (getattr(os, "O_SEARCH", 0) | nofollow) if hasattr(os, "O_SEARCH") else flags
    parts = path.parts[1:]
    parent = os.open(path.anchor, search if parts else flags)
    try:
        for index, part in enumerate(parts):
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, mode=0o700, dir_fd=parent)
            if index < len(parts) - 1:
                fd = os.open(part, search, dir_fd=parent)
            else:
                try:
                    fd = os.open(part, flags, dir_fd=parent)
                except PermissionError:
                    if search == flags:
                        raise
                    fd = os.open(part, search, dir_fd=parent)
            os.close(parent)
            parent = fd
        return parent
    except BaseException:
        os.close(parent)
        raise


def read(path):
    path = check(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if not _DIR_FD:
        fd = portable.open_nofollow(path, flags)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise Precondition(f"not a regular file: {path}")
            check(path)
            return stream.read()
    parent = _directory(path.parent)
    try:
        fd = os.open(path.name, flags, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise Precondition(f"not a regular file: {path}")
            return stream.read()
    finally:
        os.close(parent)


def mkdir(path):
    path = check(path)
    if not _DIR_FD:
        os.mkdir(path, mode=0o700)
        check(path)
        return
    parent = _directory(path.parent)
    try:
        os.mkdir(path.name, mode=0o700, dir_fd=parent)
    finally:
        os.close(parent)


def copytree(source, target):
    source, target = check(source, tree=True), check(target)
    mkdir(target)
    for child in source.iterdir():
        dest = target / child.name
        if child.is_dir():
            copytree(child, dest)
        else:
            write_text(dest, read(child))


def write_text(path, text, mode=0o600):
    path = check(path)
    if not _DIR_FD:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        check(path)
        temporary = path.parent / (".migration-" + secrets.token_hex(16))
        try:
            fd = portable.open_nofollow(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            with os.fdopen(fd, "wb") as stream:
                stream.write(text.encode("utf-8") if isinstance(text, str) else text)
                stream.flush()
                os.fsync(stream.fileno())
            check(path)
            check(temporary)
            os.replace(temporary, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                unlink(temporary)
        return
    parent = _directory(path.parent, create=True)
    temporary = ".migration-" + secrets.token_hex(16)
    try:
        try:
            existing = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(existing.st_mode):
                raise Precondition(f"not a regular file: {path}")
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode, dir_fd=parent
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(text.encode("utf-8") if isinstance(text, str) else text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        try:
            os.fsync(parent)
        except OSError as exc:
            if exc.errno not in (errno.EBADF, errno.EPERM):
                raise
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=parent)
        os.close(parent)


def replace(source, target):
    source, target = check(source), check(target)
    if not _DIR_FD:
        os.replace(source, target)
        return
    source_parent = _directory(source.parent)
    try:
        target_parent = _directory(target.parent)
        try:
            os.replace(source.name, target.name, src_dir_fd=source_parent, dst_dir_fd=target_parent)
        finally:
            os.close(target_parent)
    finally:
        os.close(source_parent)


def unlink(path):
    path = check(path)
    if not _DIR_FD:
        os.unlink(path)
        return
    parent = _directory(path.parent)
    try:
        os.unlink(path.name, dir_fd=parent)
    finally:
        os.close(parent)
