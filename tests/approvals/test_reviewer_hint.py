from types import SimpleNamespace

from raidlock.approvals import reviewer_runtime


def board_with(owner):
    return SimpleNamespace(agents=lambda: {"w": owner})


def test_reviewer_comes_from_another_provider():
    assert reviewer_runtime(board_with({"runtime": "codex-cli"}), {"owner": "w"}) == "claude"
    assert reviewer_runtime(board_with({"runtime": "claude-code"}), {"owner": "w"}) == "codex"
    assert reviewer_runtime(board_with({"provider": "openai"}), {"owner": "w"}) == "claude"


def test_unknown_owner_defaults_to_codex():
    assert reviewer_runtime(board_with({}), {"owner": ""}) == "codex"
