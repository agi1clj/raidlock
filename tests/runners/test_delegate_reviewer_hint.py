from types import SimpleNamespace

import pytest

from raidlock.core.errors import Precondition
from raidlock.runners.runner import preflight


def hint_for(owner):
    board = SimpleNamespace(agents=lambda: {"w": owner})
    with pytest.raises(Precondition) as caught:
        preflight(board, {"id": "api", "lane": "review", "owner": "w"}, "codex-worker-api")
    return caught.value.hint


def test_review_hint_names_a_runtime_from_another_provider():
    assert hint_for({"runtime": "codex-cli"}) == "raidlock delegate api --to claude --as reviewer"
    assert hint_for({"runtime": "claude-code"}) == "raidlock delegate api --to codex --as reviewer"
    assert hint_for({"provider": "openai"}) == "raidlock delegate api --to claude --as reviewer"
    assert hint_for({}) == "raidlock delegate api --to codex --as reviewer"
