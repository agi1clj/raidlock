import os
import stat

import pytest

from raidlock.core import portable


def test_remove_tree_removes_read_only_files(tmp_path):
    tree = tmp_path / "tree"
    (tree / "objects").mkdir(parents=True)
    locked = tree / "objects" / "blob"
    locked.write_text("x")
    locked.chmod(stat.S_IREAD)
    portable.remove_tree(tree)
    assert not tree.exists()


def test_remove_tree_refuses_a_symlinked_root(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    victim = target / "keep"
    victim.write_text("keep")
    victim.chmod(stat.S_IREAD)
    before = stat.S_IMODE(os.stat(victim).st_mode)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        portable.remove_tree(link)
    assert victim.read_text() == "keep"
    assert stat.S_IMODE(os.stat(victim).st_mode) == before
    assert stat.S_IMODE(os.stat(target).st_mode) == stat.S_IMODE(os.lstat(target).st_mode)
    portable.remove_tree(link, ignore_errors=True)
    assert link.is_symlink() and victim.exists()


def test_open_nofollow_fallback_refuses_a_symlink(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.write_text("secret")
    link = tmp_path / "link"
    link.symlink_to(target)
    monkeypatch.setattr(portable, "NOFOLLOW", 0)
    with pytest.raises(OSError):
        portable.open_nofollow(str(link), os.O_RDONLY)
    fd = portable.open_nofollow(str(target), os.O_RDONLY)
    os.close(fd)


def test_safeio_without_directory_descriptors(tmp_path, monkeypatch):
    from raidlock.core import safeio

    monkeypatch.setattr(safeio, "_DIR_FD", False)
    monkeypatch.setattr(portable, "NOFOLLOW", 0)
    source = tmp_path / "source"
    safeio.mkdir(source)
    safeio.write_text(source / "nested" / "data", b"\x00\xff\r\n")
    assert safeio.read(source / "nested" / "data") == b"\x00\xff\r\n"
    safeio.copytree(source, tmp_path / "copy")
    safeio.replace(tmp_path / "copy", tmp_path / "moved")
    assert safeio.read(tmp_path / "moved" / "nested" / "data") == b"\x00\xff\r\n"
    safeio.unlink(tmp_path / "moved" / "nested" / "data")
    target = tmp_path / "target"
    target.write_text("keep")
    link = source / "link"
    link.symlink_to(target)
    from raidlock.core.errors import Precondition

    for operation in (lambda: safeio.read(link), lambda: safeio.write_text(link, "overwrite")):
        with pytest.raises(Precondition):
            operation()
    assert target.read_text() == "keep"
