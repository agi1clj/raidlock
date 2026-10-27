from types import SimpleNamespace

import pytest
from portable import assert_mode

from raidlock.runners.runner import _dispose_sensitive


@pytest.mark.parametrize("raw", [False, True])
def test_sensitive_patch_object_store_and_clone_disposal(tmp_path, raw):
    secret = b"ghp_" + b"a1B2c3D4" * 5
    patch = tmp_path / "changes.patch"
    store = tmp_path / "lead.git"
    clone = tmp_path / "repo"
    patch.write_bytes(secret)
    for directory in (store, clone):
        directory.mkdir()
        (directory / "object").write_bytes(secret)
    run = SimpleNamespace(run_dir=str(tmp_path), clone=str(clone), raw=raw)
    _dispose_sensitive(run, str(patch), str(store))
    assert not any(path.exists() for path in (patch, store, clone))
    if raw:
        quarantine = tmp_path / "quarantine"
        assert_mode(quarantine, 0o700)
        assert (quarantine / "changes.patch").read_bytes() == secret
    else:
        assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("raw", [False, True])
def test_secret_scan_disposes_real_patch_and_git_objects(tmp_path, raw):
    from raidlock.runners import workspace as W
    from raidlock.runners.base import Redactor
    from raidlock.runners.runner import _finish

    source = tmp_path / "source"
    source.mkdir()
    W.git(str(source), "init", "-q")
    (source / "src").mkdir()
    (source / "src/file").write_text("safe")
    W.git(str(source), "add", "-A")
    W.git(str(source), *W.IDENT, "commit", "-qm", "base")
    folder = tmp_path / "run"
    clone = folder / "repo"
    base, _ = W.clone(str(source), str(clone))
    config = W.snapshot_config(str(clone))
    secret = "ghp_" + "a1B2c3D4" * 5
    (clone / "src/file").write_text(secret)
    run = SimpleNamespace(
        card={"id": "secret", "locks": ["source/src"]},
        run_dir=str(folder),
        clone=str(clone),
        raw=raw,
        source=str(source),
        base=base,
        rels=["src"],
        ci=False,
        redactor=Redactor(),
    )

    class AfterScan(Exception):
        pass

    def after_scan(*args):
        raise AfterScan

    board = SimpleNamespace(workspace=str(tmp_path), dir=str(tmp_path / ".board"), card=lambda cid: run.card)
    with pytest.raises(AfterScan):
        _finish(board, None, SimpleNamespace(final=after_scan), run, None, SimpleNamespace(final=""), config)
    for name in ("changes.patch", "rejected.patch", "lead.git", "repo"):
        assert not (folder / name).exists()
    if raw:
        assert_mode(folder / "quarantine", 0o700)
        assert secret in (folder / "quarantine/changes.patch").read_text()
    else:
        assert not list(folder.iterdir())
