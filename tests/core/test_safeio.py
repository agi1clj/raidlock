import os
import tempfile

import pytest

from raidlock.core import safeio
from raidlock.core.errors import Precondition


def test_system_temp_dir_is_accepted_and_resolved():
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "tokens", "a.token")
        safeio.write_text(target, "x\n")
        assert safeio.read(target) == b"x\n"
        assert str(safeio.check(target)) == os.path.join(os.path.realpath(d), "tokens", "a.token")


def test_user_symlink_below_the_root_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    with pytest.raises(Precondition, match="unsafe filesystem"):
        safeio.check(tmp_path / "link" / "a.token")


@pytest.mark.parametrize(
    "alias,target",
    [("/incoming", "/private/tmp"), ("/tmp", "/incoming"), ("/tmp", "/incoming/../private/tmp")],
)
def test_root_owned_unapproved_alias_rejected(monkeypatch, alias, target):
    import stat
    from pathlib import Path
    from types import SimpleNamespace

    original = Path.lstat

    def fake_stat(path):
        if str(path) == alias:
            return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0)
        return original(path)

    monkeypatch.setattr(Path, "lstat", fake_stat)
    monkeypatch.setattr(os, "readlink", lambda path: target)
    with pytest.raises(Precondition, match="unsafe filesystem"):
        safeio.check(alias + "/board")


def test_os_alias_rejects_redirected_target_chain(monkeypatch):
    import stat
    from pathlib import Path
    from types import SimpleNamespace

    original = Path.lstat

    def fake_stat(path):
        if str(path) in ("/tmp", "/private"):
            return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0)
        return original(path)

    monkeypatch.setattr(Path, "lstat", fake_stat)
    monkeypatch.setattr(os, "readlink", lambda path: "/private/tmp" if str(path) == "/tmp" else "/elsewhere")
    with pytest.raises(Precondition, match="unsafe filesystem"):
        safeio.check("/tmp/board")


@pytest.mark.skipif(not hasattr(os, "O_SEARCH") or not safeio._DIR_FD, reason="needs O_SEARCH and dir_fd support")
def test_directory_walk_survives_read_protected_ancestor(tmp_path, monkeypatch):
    protected = tmp_path / "Desktop"
    target = protected / "repo" / ".board"
    target.mkdir(parents=True)
    (target / "agents.json").write_text("{}")
    real_open = os.open

    def guarded(path, flags, *args, dir_fd=None, **kwargs):
        if path == "Desktop" and dir_fd is not None and flags & os.O_SEARCH != os.O_SEARCH:
            raise PermissionError(1, "Operation not permitted", path)
        return real_open(path, flags, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(safeio.os, "open", guarded)
    assert safeio.read(target / "agents.json") == b"{}"
    safeio.write_text(str(target / "new.json"), "[]")
    assert (target / "new.json").read_text() == "[]"
