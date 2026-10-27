import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from raidlock.core.portable import remove_tree
from raidlock.runners import isolation, verifycache
from raidlock.runners import workspace as W

SANDBOX = isolation.mode()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "src-repo"
    path.mkdir()
    W.git(str(path), "init", "--quiet")
    (path / "check.txt").write_text("committed")
    (path / ".gitignore").write_text("build/\n")
    W.git(str(path), "add", ".")
    W.git(str(path), *W.IDENT, "commit", "--quiet", "-m", "fixture")
    return path


def counting(monkeypatch):
    calls = []
    fresh = verifycache._fresh

    def counted(*args):
        calls.append(args)
        return fresh(*args)

    monkeypatch.setattr(verifycache, "_fresh", counted)
    return calls


def tamper(tree):
    (tree / "check.txt").write_text("dirty")
    (tree / "untracked.txt").write_text("x")
    (tree / "build").mkdir()
    (tree / "build" / "out.o").write_text("ignored")
    nested = tree / "nested"
    nested.mkdir()
    W.git(str(nested), "init", "--quiet")
    pointer = tree / ".git"
    pointer.unlink()
    pointer.mkdir()
    (pointer / "config").write_text("[core]\n\tfsmonitor = touch planted\n")


def test_second_use_of_a_sha_reuses_objects_in_a_fresh_checkout(repo, tmp_path, monkeypatch):
    calls = counting(monkeypatch)
    sha = W.head(str(repo))
    root = tmp_path / "cache"
    stats = {}
    with verifycache.checkout(str(repo), sha, str(root), stats=stats) as (tree, gitdir):
        assert stats == {"reused": False}
        tree_path = Path(tree)
        tamper(tree_path)
    with verifycache.checkout(str(repo), sha, str(root), stats=stats) as (tree, gitdir):
        assert stats == {"reused": True}
        assert W.head(tree) == sha
        assert tree != str(tree_path)
        assert not tree_path.exists()
        assert (Path(tree) / "check.txt").read_text() == "committed"
        assert sorted(os.listdir(tree)) == [".git", ".gitignore", "check.txt"]
        assert os.path.isfile(os.path.join(tree, ".git"))
        assert not W.git(tree, "status", "--porcelain", "--ignored", "--untracked-files=all").strip()
        assert not os.path.exists(os.path.join(tree, "planted"))
    assert len(calls) == 1


@pytest.mark.parametrize("damage", ["gitdir", "symlink", "objects"])
def test_a_damaged_entry_is_rebuilt(repo, tmp_path, monkeypatch, damage):
    calls = counting(monkeypatch)
    sha = W.head(str(repo))
    root = tmp_path / "cache"
    with verifycache.checkout(str(repo), sha, str(root)) as (tree, gitdir):
        pass
    entry = root / sha
    if damage == "gitdir":
        remove_tree(gitdir)
    elif damage == "symlink":
        remove_tree(gitdir)
        os.symlink(str(repo), gitdir, target_is_directory=True)
    else:
        remove_tree(entry / "git" / "objects")
        (entry / "git" / "objects").mkdir()
    with verifycache.checkout(str(repo), sha, str(root)) as (tree, gitdir):
        assert not os.path.islink(tree)
        assert W.head(tree) == sha
    assert len(calls) == 2


def test_short_or_odd_shas_are_refused(repo, tmp_path):
    with (
        pytest.raises(Exception, match="full commit SHA"),
        verifycache.checkout(str(repo), "HEAD", str(tmp_path / "cache")),
    ):
        pass


def test_old_entries_are_pruned(repo, tmp_path):
    root = tmp_path / "cache"
    shas = []
    for i in range(4):
        (repo / "check.txt").write_text(str(i))
        W.git(str(repo), *W.IDENT, "commit", "--quiet", "-am", str(i))
        shas.append(W.head(str(repo)))
        with verifycache.checkout(str(repo), shas[-1], str(root), keep=2):
            pass
        time.sleep(0.01)
    kept = sorted(n for n in os.listdir(root) if not n.endswith(".lock"))
    assert kept == sorted(shas[-2:])


def test_bwrap_mounts_the_git_dir_read_only(tmp_path):
    clone, home, gitdir, secret = (tmp_path / n for n in ("clone", "home", "gitdir", "state"))
    for p in (clone, home, gitdir, secret):
        p.mkdir()
    argv = isolation.wrap("bwrap", ["true"], str(clone), str(home), [str(secret)], [str(gitdir)])
    real = os.path.realpath(gitdir)
    at = argv.index(real)
    assert argv[at - 1 : at + 2] == ["--ro-bind", real, real]
    assert argv.index(f"{os.path.realpath(secret)}") < at


@pytest.mark.skipif(SANDBOX != "sandbox-exec", reason="needs sandbox-exec")
def test_sandboxed_verify_reads_but_cannot_write_the_cached_git_dir(repo, tmp_path):
    root = tmp_path / "home" / "verify"
    sha = W.head(str(repo))
    env = {**os.environ, "RAIDLOCK_HOME": str(tmp_path / "home")}
    with verifycache.checkout(str(repo), sha, str(root)) as (tree, gitdir):
        before = Path(os.path.join(gitdir, "config")).read_bytes()
        code, tail, _ = isolation.run(
            "git rev-parse HEAD && git config core.fsmonitor 'touch planted'; touch ok",
            tree,
            env,
            30,
            kind=SANDBOX,
            deny_read=isolation.hidden(env, [str(tmp_path / "home")]),
            readable=[gitdir],
        )
        assert sha in tail
        assert os.path.exists(os.path.join(tree, "ok"))
        assert Path(os.path.join(gitdir, "config")).read_bytes() == before


def test_verify_receipts_reuse_one_clone_within_a_time_budget(world, committed_workspace, monkeypatch):
    calls = counting(monkeypatch)
    trees = []
    run = isolation.run

    def recorded(cmd, cwd, *args, **kwargs):
        trees.append(cwd)
        assert not (Path(cwd) / "marker").exists()
        return run(cmd, cwd, *args, **kwargs)

    monkeypatch.setattr(isolation, "run", recorded)
    world.card("c1", locks="ws", verify_cmd="test ! -f marker && touch marker")
    started = time.monotonic()
    first = world.board.verify("c1", world.lead)
    first_s = time.monotonic() - started
    started = time.monotonic()
    runs = [world.board.verify("c1", world.lead) for _ in range(5)]
    reuse_s = (time.monotonic() - started) / len(runs)
    assert first["passed"] and all(r["passed"] and r["sha"] == first["sha"] for r in runs)
    assert len(calls) == 1
    assert len(set(trees)) == 6
    assert all(not Path(tree).exists() for tree in trees)
    assert reuse_s < max(2.0, first_s * 3)


@pytest.mark.skipif(os.name != "posix", reason="requires inherited descriptors")
def test_survivor_cannot_poison_the_next_checkout(repo, tmp_path):
    sha = W.head(str(repo))
    root = str(tmp_path / "cache")
    with verifycache.checkout(str(repo), sha, root) as (tree, gitdir):
        old = Path(tree)
        descriptor = os.open(old / "check.txt", os.O_WRONLY)
        try:
            survivor = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import os, sys; print('ready', flush=True); sys.stdin.read(); "
                    f"os.write({descriptor}, b'poisoned!')",
                ],
                cwd=tree,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                pass_fds=(descriptor,),
            )
        finally:
            os.close(descriptor)
        ready, _, _ = select.select([survivor.stdout], [], [], 5)
        if not ready:
            survivor.kill()
            survivor.wait(timeout=5)
            pytest.fail("survivor did not start")
        assert survivor.stdout.readline() == b"ready\n"
    try:
        with verifycache.checkout(str(repo), sha, root) as (tree, gitdir):
            survivor.communicate(input=b"write now", timeout=5)
            assert survivor.returncode == 0
            assert Path(tree) != old
            assert not old.exists()
            assert (Path(tree) / "check.txt").read_text() == "committed"
            assert sorted(p.name for p in (Path(root) / sha).iterdir()) == ["git"]
    finally:
        survivor.kill()
        survivor.wait(timeout=5)
