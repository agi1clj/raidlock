import pytest

from raidlock.core import Precondition


def test_blocked_done_lists_every_way_out(world):
    world.board.set_setting(world.lead, "require_verify", False)
    world.board.set_setting(world.lead, "require_review", "1")
    owner = world.agent("owner")
    world.card("c1")
    world.claim("c1", owner)
    world.board.move("c1", "review", owner)
    with pytest.raises(Precondition) as err:
        world.board.move("c1", "done", world.lead)
    assert "review c1 --assign <reviewer>" in err.value.hint
    assert "close c1 --evidence <PR URL or SHA>" in err.value.hint
    assert "config set require_review 0" in err.value.hint


def test_protected_card_does_not_suggest_turning_reviews_off(world):
    world.board.set_setting(world.lead, "require_verify", False)
    owner = world.agent("owner")
    world.card("c1")
    world.board.edit_card(world.lead, "c1", allow_protected=".github/workflows")
    world.claim("c1", owner)
    world.board.move("c1", "review", owner)
    with pytest.raises(Precondition) as err:
        world.board.move("c1", "done", world.lead)
    assert "close c1 --evidence" in err.value.hint
    assert "require_review 0" not in err.value.hint
