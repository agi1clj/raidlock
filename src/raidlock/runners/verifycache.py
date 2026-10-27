"""Cached Git object stores with a unique disposable worktree for every verification."""

import os
import tempfile
from contextlib import contextmanager

from ..core.errors import Precondition, Timeout
from ..core.lock import FileLock
from ..core.portable import remove_tree
from . import workspace

KEEP = 8
SHA = frozenset("0123456789abcdef")


def _remove(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        remove_tree(path)


def _git(cwd, *args):
    return workspace.git(cwd, *args, timeout=30)


def _usable(entry):
    gitdir = os.path.join(entry, "git")
    return all(os.path.isdir(path) and not os.path.islink(path) for path in (entry, gitdir)) and not os.path.lexists(
        os.path.join(entry, "checkout")
    )


def _fresh(src, sha, entry):
    _remove(entry)
    os.makedirs(entry, mode=0o700)
    gitdir = os.path.join(entry, "git")
    with workspace.object_view(src) as source:
        _git(
            entry, "clone", "--quiet", "--bare", "--no-local", "--no-hardlinks", source or os.path.realpath(src), gitdir
        )
    for remote in _git(gitdir, "remote").split():
        _git(gitdir, "remote", "remove", remote)


def _prune(root, keep, current):
    try:
        names = [n for n in os.listdir(root) if set(n) <= SHA and n != current]
    except OSError:
        return
    entries = sorted(
        (os.path.join(root, n) for n in names if os.path.isdir(os.path.join(root, n))),
        key=os.path.getmtime,
        reverse=True,
    )
    for entry in entries[max(0, keep - 1) :]:
        try:
            with FileLock(entry + ".lock", timeout=0):
                _remove(entry)
        except (Timeout, OSError):
            continue


@contextmanager
def checkout(src, sha, root, timeout=600.0, keep=KEEP, stats=None):
    """Yield a fresh worktree and its cached Git directory while holding the entry lock."""
    sha = (sha or "").strip().lower()
    if len(sha) < 40 or not set(sha) <= SHA:
        raise Precondition(f"verify needs a full commit SHA, got {sha[:80]!r}")
    os.makedirs(root, mode=0o700, exist_ok=True)
    entry = os.path.join(root, sha)
    gitdir = os.path.join(entry, "git")
    with FileLock(entry + ".lock", timeout=timeout):
        reused = False
        if _usable(entry):
            try:
                _git(gitdir, "cat-file", "-e", sha + "^{commit}")
                reused = True
            except (Precondition, OSError):
                pass
        if not reused:
            _fresh(src, sha, entry)
        if stats is not None:
            stats["reused"] = reused
        os.utime(entry)
        worktree = tempfile.mkdtemp(prefix="raidlock-checkout-")
        try:
            _git(gitdir, "worktree", "add", "--quiet", "--detach", worktree, sha)
            yield worktree, gitdir
        finally:
            _remove(worktree)
            _git(gitdir, "worktree", "prune", "--expire=now")
    _prune(root, keep, sha)
