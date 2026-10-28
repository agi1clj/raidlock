from pathlib import Path

import pytest

from raidlock import gitguard, hooks


@pytest.mark.parametrize(
    "command",
    [
        "cd victim && printf corrupt > owned.txt",
        "cd victim; tee owned.txt",
        "pushd victim && cp ../source owned.txt",
        "(cd victim && mv owned.txt ../stolen)",
        "cd victim && rm owned.txt",
        "cd -- victim && touch owned.txt",
        "cp --target-directory=victim source",
        "sh -c 'cd victim && printf corrupt > owned.txt'",
        "printf x | cd victim; rm owned.txt",
    ],
)
def test_compound_targets_cannot_use_starting_directory(tmp_path, monkeypatch, command):
    (tmp_path / "victim").mkdir()
    checked = []

    def check(data, env, target):
        checked.append(target)
        return hooks.deny("victim") if Path(target).is_relative_to(tmp_path / "victim") else None

    monkeypatch.setattr(hooks, "git_decision", lambda *args: (None, False))
    monkeypatch.setattr(hooks, "write_decision", check)
    result = hooks.pre_bash({"cwd": str(tmp_path), "tool_input": {"command": command}}, {})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert checked


@pytest.mark.parametrize(
    "command",
    [
        "cd $TARGET && tee owned.txt",
        "cd -P victim && rm owned.txt",
        "popd; printf corrupt > owned.txt",
        "eval 'cd victim'; rm owned.txt",
        "source script; rm owned.txt",
    ],
)
def test_unknown_write_directory_fails_closed(tmp_path, monkeypatch, command):
    monkeypatch.setattr(hooks, "git_decision", lambda *args: (None, False))
    result = hooks.pre_bash({"cwd": str(tmp_path), "tool_input": {"command": command}}, {})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_absolute_write_target_survives_unknown_directory(tmp_path, monkeypatch):
    checked = []

    def check(data, env, target):
        checked.append(target)
        return hooks.deny("victim") if target.endswith("owned.txt") else None

    monkeypatch.setattr(hooks, "git_decision", lambda *args: (None, False))
    monkeypatch.setattr(hooks, "write_decision", check)
    result = hooks.pre_bash({"cwd": str(tmp_path), "tool_input": {"command": "source s.sh; echo x 2>/dev/null"}}, {})
    assert (result or {}).get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
    assert checked == ["/dev/null"]
    command = f"source s.sh; echo x > {tmp_path}/owned.txt"
    result = hooks.pre_bash({"cwd": str(tmp_path), "tool_input": {"command": command}}, {})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "--icase-pathspecs", "restore", "owned.txt"],
        ["git", "--literal-pathspecs", "--glob-pathspecs", "restore", "owned.txt"],
        ["git", "restore", ":(icase)owned.txt"],
        ["git", "restore", ":(glob)*"],
        ["git", "restore", ":/owned.txt"],
    ],
)
def test_pathspec_broadening_is_rejected(argv):
    with pytest.raises(gitguard.Forbidden):
        _, sub, args = gitguard.parse_git(argv)
        gitguard.classify(sub, args)


@pytest.mark.parametrize(
    ("path", "expected"),
    [("/*/secret", "/"), ("/repo/*/secret", "/repo"), ("*/secret", "."), ("repo/*", "repo")],
)
def test_literal_prefix_preserves_absolute_root(path, expected):
    assert hooks.literal_prefix(path) == expected
