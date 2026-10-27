import os
import subprocess
import sys
from pathlib import Path

import pytest
from portable import SH, assert_mode

from raidlock import guard
from raidlock.core import Board
from raidlock.core.lead import SESSION_VARS


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("RAIDLOCK_") or key == "BOARD_DIR":
            monkeypatch.delenv(key)
    for key in SESSION_VARS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("RAIDLOCK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("RAIDLOCK_SESSION", "guard-test-session")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    path = tmp_path / "app"
    path.mkdir()
    monkeypatch.chdir(path)
    git("init", "-q")
    git("config", "user.name", "Guard Test")
    git("config", "user.email", "guard@example.test")
    git("remote", "add", "origin", "https://github.com/example/app.git")
    board, token = Board.init(tmp_path)
    monkeypatch.setenv("RAIDLOCK_LEAD_PID", str(board.leadlease().lease()["pid"]))
    lead = board.caller(lead_secret=token)
    worker = board.join(lead, agent="worker-one")
    monkeypatch.setenv("RAIDLOCK_AGENT", board.lead)
    monkeypatch.setenv("RAIDLOCK_TOKEN", token)
    monkeypatch.setenv("RAIDLOCK_LEAD_SECRET", token)
    board._guard_worker_token = worker["token"]
    board.new_card(lead, "Change source", card_id="source", locks=["app/src"])
    guard.install()
    return path, board, lead


def git(*args, **kwargs):
    return subprocess.run(["git", *args], capture_output=True, text=True, **kwargs)


def stage(path="src/file.txt", content="changed"):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    assert git("add", "--", path).returncode == 0


def claim(repo):
    _, board, lead = repo
    return board.claim("source", lead, for_agent="worker-one")


def commit():
    return git("commit", "-qm", "test")


def test_free_commit(repo):
    stage()
    result = commit()
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_held_refused(repo):
    claim(repo)
    stage()
    result = commit()
    assert result.returncode != 0
    assert "holder=worker-one" in result.stderr
    assert "raidlock claim source" in result.stderr
    assert len(result.stderr.splitlines()) == 1


@pytest.mark.parametrize("fence", ["current", "stale", "0", "invalid"])
def test_own_lease_and_fence(repo, monkeypatch, fence):
    monkeypatch.delenv("RAIDLOCK_LEAD_SECRET")
    grant = claim(repo)
    monkeypatch.setenv("RAIDLOCK_AGENT", "worker-one")
    monkeypatch.setenv("RAIDLOCK_TOKEN", repo[1]._guard_worker_token)
    value = str(grant["fence"]) if fence == "current" else str(grant["fence"] + 1) if fence == "stale" else fence
    monkeypatch.setenv("RAIDLOCK_FENCE", value)
    stage()
    result = commit()
    assert (result.returncode == 0) == (fence == "current")
    if fence != "current":
        assert "stale fence" in result.stderr


def test_agent_without_lease(repo, monkeypatch):
    monkeypatch.delenv("RAIDLOCK_LEAD_SECRET")
    monkeypatch.setenv("RAIDLOCK_AGENT", "worker-one")
    monkeypatch.setenv("RAIDLOCK_TOKEN", repo[1]._guard_worker_token)
    stage()
    assert "holds no lease" in commit().stderr


@pytest.mark.parametrize("identity", ["anonymous", "worker", "lead", "role-lead"])
def test_unauthenticated_identity_cannot_commit(repo, monkeypatch, identity):
    monkeypatch.delenv("RAIDLOCK_LEAD_SECRET")
    claim(repo)
    monkeypatch.delenv("RAIDLOCK_TOKEN")
    if identity == "anonymous":
        monkeypatch.delenv("RAIDLOCK_AGENT")
    elif identity == "worker":
        monkeypatch.setenv("RAIDLOCK_AGENT", "worker-one")
    elif identity == "role-lead":
        monkeypatch.setenv("RAIDLOCK_ROLE", "lead")
    stage("src/file.txt" if identity == "worker" else "unleased.txt")
    result = commit()
    assert result.returncode != 0
    assert "no authenticated agent" in result.stderr or "RAIDLOCK_ROLE=lead is refused" in result.stderr


@pytest.mark.parametrize("token", ["missing", "wrong", "other-agent"])
def test_impersonating_lease_holder_cannot_commit(repo, monkeypatch, token):
    monkeypatch.delenv("RAIDLOCK_LEAD_SECRET")
    grant = claim(repo)
    monkeypatch.setenv("RAIDLOCK_AGENT", "worker-one")
    monkeypatch.setenv("RAIDLOCK_FENCE", str(grant["fence"]))
    if token == "missing":
        monkeypatch.delenv("RAIDLOCK_TOKEN")
    elif token == "wrong":
        monkeypatch.setenv("RAIDLOCK_TOKEN", "invalid-token")
    stage()
    result = commit()
    assert result.returncode != 0
    assert "no authenticated agent" in result.stderr


def test_worker_must_present_fence(repo, monkeypatch):
    monkeypatch.delenv("RAIDLOCK_LEAD_SECRET")
    claim(repo)
    monkeypatch.setenv("RAIDLOCK_AGENT", "worker-one")
    monkeypatch.setenv("RAIDLOCK_TOKEN", repo[1]._guard_worker_token)
    stage()
    result = commit()
    assert result.returncode != 0
    assert "stale fence" in result.stderr


def test_warn_cannot_bypass_invalid_lead_identity(repo, monkeypatch):
    monkeypatch.setenv("RAIDLOCK_GUARD", "warn")
    monkeypatch.setenv("RAIDLOCK_ROLE", "lead")
    stage()
    assert commit().returncode != 0


def test_anonymous_cannot_push(repo, monkeypatch):
    stage("unleased.txt")
    assert commit().returncode == 0
    tip = git("rev-parse", "HEAD").stdout.strip()
    monkeypatch.delenv("RAIDLOCK_AGENT")
    monkeypatch.delenv("RAIDLOCK_LEAD_SECRET")
    monkeypatch.delenv("RAIDLOCK_TOKEN")
    result = push(push_input(tip, "0" * 40))
    assert result.returncode != 0
    assert "no authenticated agent" in result.stderr


@pytest.mark.parametrize("warn", [False, True])
def test_unreachable_board(repo, monkeypatch, warn):
    monkeypatch.setenv("RAIDLOCK_BOARD", str(repo[0] / "missing"))
    if warn:
        monkeypatch.setenv("RAIDLOCK_GUARD", "warn")
    stage()
    result = commit()
    assert (result.returncode == 0) == warn
    assert len(result.stderr.splitlines()) == 1
    assert ("warning" in result.stderr) == warn


@pytest.mark.parametrize("custom", [False, True])
def test_existing_hook_chained_restored(repo, custom):
    guard.uninstall()
    if custom:
        git("config", "core.hooksPath", "custom hooks")
    folder = guard.hook_dir()
    folder.mkdir(exist_ok=True)
    path = folder / "pre-commit"
    original = "#!/bin/sh\nprintf chained > hook-output\nexit 1\n"
    path.write_text(original)
    path.chmod(0o751)
    guard.install()
    guard.install()
    stage()
    assert commit().returncode != 0
    assert Path("hook-output").read_text() == "chained"
    guard.uninstall()
    assert path.read_text() == original
    assert_mode(path, 0o751)
    assert not (folder / "pre-push").exists()


def push_input(local, remote):
    return f"refs/heads/main {local} refs/heads/main {remote}\n"


def push(data):
    shell = [] if os.name == "posix" else [SH]
    return subprocess.run(
        [*shell, str(guard.hook_dir() / "pre-push"), "origin", "url"], input=data, capture_output=True, text=True
    )


def test_pre_push_range_includes_reverted_path(repo):
    stage("readme")
    assert commit().returncode == 0
    base = git("rev-parse", "HEAD").stdout.strip()
    stage()
    assert commit().returncode == 0
    git("rm", "src/file.txt")
    assert commit().returncode == 0
    tip = git("rev-parse", "HEAD").stdout.strip()
    claim(repo)
    assert push(push_input(tip, base)).returncode != 0
    assert push(push_input(tip, tip)).returncode == 0
    assert push(push_input("0" * 40, tip)).returncode == 0
    assert push(push_input(tip, "0" * 40)).returncode != 0


def test_pre_push_chained_stdin(repo):
    guard.uninstall()
    path = guard.hook_dir() / "pre-push"
    path.write_text('#!/bin/sh\ncat > hook-input\nprintf "%s\\n" "$@" > hook-args\n')
    path.chmod(0o755)
    guard.install()
    stage("readme")
    assert commit().returncode == 0
    tip = git("rev-parse", "HEAD").stdout.strip()
    data = push_input(tip, "0" * 40)
    assert push(data).returncode == 0
    assert Path("hook-input").read_text() == data
    assert Path("hook-args").read_text() == "origin\nurl\n"


def test_extension_registration(repo):
    from raidlock import cli

    previous = dict(cli.COMMANDS)
    try:
        guard.register_cli(cli.add_command)
        assert cli.main(["guard", "check"]) == 0
    finally:
        cli.COMMANDS.clear()
        cli.COMMANDS.update(previous)


def test_module_entrypoint(repo):
    env = dict(os.environ, PYTHONPATH=str(Path(guard.__file__).resolve().parent.parent))
    result = subprocess.run([sys.executable, "-m", "raidlock.guard", "check"], env=env, capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == result.stderr == ""


def test_expired_lease_is_free(repo):
    _, board, lead = repo
    board.claim("source", lead, for_agent="worker-one", ttl_minutes=-1)
    stage()
    assert commit().returncode == 0


def test_warn_does_not_bypass_conflicts(repo, monkeypatch):
    claim(repo)
    monkeypatch.setenv("RAIDLOCK_GUARD", "warn")
    stage("src/file with spaces.txt")
    assert commit().returncode != 0


def test_remote_identity_across_clones(repo, monkeypatch):
    path, board, _ = repo
    claim(repo)
    other = path.parent / "other-clone"
    other.mkdir()
    monkeypatch.chdir(other)
    assert git("init", "-q").returncode == 0
    git("remote", "add", "origin", "git@github.com:example/app.git")
    stage()
    assert guard.check(board_path=board.dir) == 2


def test_replacement_ref_cannot_hide_staged_write(repo, monkeypatch):
    stage()
    assert commit().returncode == 0
    original = git("rev-parse", "HEAD").stdout.strip()
    stage(content="stolen change")
    tree = git("write-tree").stdout.strip()
    replacement = git("commit-tree", tree, "-m", "replacement").stdout.strip()
    assert git("replace", original, replacement).returncode == 0
    claim(repo)
    monkeypatch.setenv("GIT_NO_REPLACE_OBJECTS", "0")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "0")
    malicious = repo[0] / "global-config"
    malicious.write_text("[alias]\n    hidden = status\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(malicious))
    assert "src/file.txt" in guard.staged_paths()
    assert guard.check() == 2
    with pytest.raises(subprocess.CalledProcessError):
        guard.git("config", "--global", "--get", "alias.hidden")


@pytest.mark.parametrize("identity", ["anonymous", "worker"])
def test_deletion_authenticates_before_paths(repo, monkeypatch, identity):
    monkeypatch.delenv("RAIDLOCK_LEAD_SECRET")
    monkeypatch.setenv("RAIDLOCK_AGENT", "worker-one")
    monkeypatch.setenv("RAIDLOCK_TOKEN", repo[1]._guard_worker_token if identity == "worker" else "")
    monkeypatch.setenv("RAIDLOCK_GUARD", "warn")
    monkeypatch.setattr(guard, "root", lambda: repo[0])
    assert guard.check("pre-push", push_input("0" * 40, "a" * 40)) == 2


def test_force_push_requires_lead(repo, monkeypatch):
    stage("readme", "base")
    assert commit().returncode == 0
    base = git("rev-parse", "HEAD").stdout.strip()
    stage("readme", "next")
    assert commit().returncode == 0
    tip = git("rev-parse", "HEAD").stdout.strip()
    assert guard.check("pre-push", push_input(base, tip)) == 0
    monkeypatch.delenv("RAIDLOCK_LEAD_SECRET")
    monkeypatch.setenv("RAIDLOCK_AGENT", "worker-one")
    monkeypatch.setenv("RAIDLOCK_TOKEN", repo[1]._guard_worker_token)
    monkeypatch.setenv("RAIDLOCK_GUARD", "warn")
    assert guard.check("pre-push", push_input(base, tip)) == 2
