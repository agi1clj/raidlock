import pytest

from raidlock import foresight
from raidlock.core import advisories


@pytest.mark.parametrize("failure", [False, True])
def test_pre_claim_advice_cannot_deny_claim(world, monkeypatch, failure):
    worker = world.agent("worker")
    world.card("task")
    calls = []

    def advise(board, card_id, checkout=None):
        calls.append((board, card_id, checkout))
        assert board.card(card_id)["lane"] == "todo"
        if failure:
            raise RuntimeError("prediction failed")
        return ["Warning: predicted conflict"]

    monkeypatch.setattr(foresight, "pre_claim", advise)
    result = world.board.claim("task", worker, checkout=str(world.root / "app"))
    assert calls == [(world.board, "task", str(world.root / "app"))]
    assert result["warnings"]
    assert ("prediction failed" if failure else "predicted conflict") in result["warnings"][0]
    assert world.board.card("task")["lane"] == "doing"
    assert world.board.leases()["task"]["fence"] == result["fence"]


def test_missing_advisory_extension_cannot_deny_claim(world, monkeypatch):
    worker = world.agent("worker")
    world.card("task")
    monkeypatch.setattr(advisories, "PRE_CLAIM", (("raidlock.missing_advisory", "pre_claim"),))
    result = world.board.claim("task", worker)
    assert "advice skipped" in result["warnings"][0]
    assert world.board.card("task")["lane"] == "doing"


def test_claim_omits_empty_advisories(world, monkeypatch):
    worker = world.agent("worker")
    world.card("task")
    monkeypatch.setattr(foresight, "pre_claim", lambda *args, **kwargs: [])
    result = world.board.claim("task", worker)
    assert "warnings" not in result
