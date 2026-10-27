import os
import subprocess
from types import SimpleNamespace

import pytest

from raidlock import guard


def test_guard_git_does_not_inherit_security_configuration(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_text("[alias]\n    unsafe = status\n")
    for key, value in {
        "GIT_NO_REPLACE_OBJECTS": "0",
        "GIT_CONFIG_NOSYSTEM": "0",
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "alias.unsafe",
        "GIT_CONFIG_VALUE_0": "status",
    }.items():
        monkeypatch.setenv(key, value)
    real = subprocess.run
    seen = []

    def capture(*args, **kwargs):
        seen.append(kwargs["env"])
        return real(*args, **kwargs)

    monkeypatch.setattr(guard.subprocess, "run", capture)
    with pytest.raises(subprocess.CalledProcessError):
        guard.git("config", "--global", "--get", "alias.unsafe")
    assert seen[0]["GIT_NO_REPLACE_OBJECTS"] == seen[0]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert seen[0]["GIT_CONFIG_GLOBAL"] == os.devnull
    assert "GIT_CONFIG_COUNT" not in seen[0]


def test_anonymous_deletion_is_denied_before_git(monkeypatch):
    board = SimpleNamespace(caller=lambda: SimpleNamespace(authenticated=False))
    monkeypatch.setattr(guard.Board, "open", lambda _: board)
    monkeypatch.setattr(guard, "root", lambda: pytest.fail("processed git before authentication"))
    monkeypatch.setenv("RAIDLOCK_GUARD", "warn")
    assert guard.check("pre-push", f"(delete) {'0' * 40} refs/heads/main {'a' * 40}") == 2


def test_worker_deletion_and_force_push_require_lead(monkeypatch):
    from raidlock.core.errors import IdentityError

    worker = SimpleNamespace(is_lead=False)
    with pytest.raises(IdentityError):
        guard.pushed_paths(f"(delete) {'0' * 40} refs/heads/main {'a' * 40}", worker)
    monkeypatch.setattr(guard, "git", lambda *args: b"c" * 40)
    with pytest.raises(IdentityError):
        guard.pushed_paths(f"refs/heads/main {'b' * 40} refs/heads/main {'a' * 40}", worker)


def test_replacement_object_cannot_erase_staged_diff(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.test", *args],
            env=env,
            capture_output=True,
            check=True,
        ).stdout.strip()

    git("init", "-q")
    target = tmp_path / "victim"
    target.write_text("original")
    git("add", "victim")
    tree = git("write-tree").decode()
    base = git("commit-tree", tree, "-m", "base").decode()
    git("update-ref", "HEAD", base)
    target.write_text("unauthorized")
    git("add", "victim")
    replacement = git("commit-tree", git("write-tree").decode(), "-m", "replacement").decode()
    git("replace", base, replacement)
    assert git("diff", "--cached", "--name-only") == b""
    monkeypatch.setenv("GIT_NO_REPLACE_OBJECTS", "0")
    assert guard.staged_paths() == {"victim"}


def test_push_warning_mode_cannot_bypass_unavailable_authentication(monkeypatch):
    def unavailable(_):
        raise OSError("board unavailable")

    monkeypatch.setattr(guard.Board, "open", unavailable)
    monkeypatch.setenv("RAIDLOCK_GUARD", "warn")
    assert guard.check("pre-push", f"(delete) {'0' * 40} refs/heads/main {'a' * 40}") == 2
