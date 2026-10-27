import sys

import pytest

from raidlock.core.errors import Denied, IdentityError, Precondition


def test_move_to_doing_rejected(world):
    world.card("c1")
    with pytest.raises(Denied):
        world.board.move("c1", "doing", world.lead)


def test_todo_cannot_jump_to_review(world):
    world.card("c1")
    with pytest.raises(Precondition):
        world.board.move("c1", "review", world.lead)


def test_done_is_terminal_and_lead_only(world):
    w = world.agent("w-one")
    world.card("c1")
    world.claim("c1", w)
    world.board.move("c1", "review", w)
    with pytest.raises(IdentityError):
        world.board.move("c1", "done", w)
    world.board.move("c1", "done", world.lead)
    with pytest.raises(Precondition):
        world.claim("c1", w)
    with pytest.raises(Precondition):
        world.board.move("c1", "todo", world.lead)
    with pytest.raises(IdentityError):
        world.board.reopen("c1", w)
    world.board.reopen("c1", world.lead)
    assert world.board.card("c1")["lane"] == "todo"


def test_reviewer_cannot_claim(world):
    r = world.agent("rev-one", role="reviewer")
    world.card("c1")
    with pytest.raises(IdentityError):
        world.claim("c1", r)


def test_team_membership_and_cap(world):
    world.board.add_team(world.lead, "other", 1)
    outsider = world.agent("w-out", team="other")
    world.card("c1")
    with pytest.raises(IdentityError):
        world.claim("c1", outsider)
    a, b = world.agent("w-aaa", team="other"), world.agent("w-bbb", team="other")
    world.card("o1", team="other", locks="app/o1")
    world.card("o2", team="other", locks="app/o2")
    world.claim("o1", a)
    with pytest.raises(Denied):
        world.claim("o2", b)
    reasons = world.board.claimable(agent="w-bbb")["o2"]["reasons"]
    assert any("limit" in r for r in reasons)


def test_depends_must_be_done(world):
    w = world.agent("w-one")
    world.card("base", locks="app/base")
    world.board.new_card(world.lead, "next", card_id="next", team="core", locks="app/next", depends="base")
    with pytest.raises(Precondition):
        world.claim("next", w)


@pytest.mark.parametrize("bad", ["two\nlines", "tab\there", "nul\x00", "esc\x1b[31m", "sep x"])
def test_control_characters_rejected(world, bad):
    with pytest.raises(Precondition):
        world.board.new_card(world.lead, bad, card_id="x1")
    w = world.agent("w-one")
    world.card("c1")
    with pytest.raises(Precondition):
        world.board.note("c1", w, bad)
    with pytest.raises(Precondition):
        world.board.new_card(world.lead, "ok", card_id="x2", scope=bad)


def test_a2a_states(world):
    w = world.agent("w-one")
    world.card("c1")

    def view():
        return world.board.card_view(world.board.card("c1"))["a2a_state"]

    assert view() == "submitted"
    world.claim("c1", w)
    assert view() == "working"
    world.board.move("c1", "blocked", w)
    assert view() == "input-required"
    world.board.release("c1", w)
    world.claim("c1", w)
    world.board.release("c1", w)
    assert view() == "canceled"


def test_verify_gate(world, committed_workspace):
    w = world.agent("w-one")
    world.card("c1", locks="ws", verify_cmd='python3 -c "import sys; sys.exit(1)"')
    world.claim("c1", w)
    world.board.move("c1", "review", w)
    with pytest.raises(Precondition):
        world.board.move("c1", "done", world.lead)
    r = world.board.verify("c1", world.lead)
    assert r["passed"] is False and r["exit"] == 1
    with pytest.raises(Precondition):
        world.board.move("c1", "done", world.lead)


def test_verify_pass_then_done(world, committed_workspace):
    w = world.agent("w-one")
    world.card("c1", locks="ws", verify_cmd=f'"{sys.executable}" -c "print(\'ok token=abcd1234secret\')"')
    world.claim("c1", w)
    world.board.move("c1", "review", w)
    r = world.board.verify("c1", world.lead)
    assert r["passed"] and "abcd1234secret" not in r["tail"]
    world.board.move("c1", "done", world.lead)
    assert world.board.transcript.read(kind="verify")[0]["artifacts"][0]["metadata"]["kind"] == "verify"


def test_only_lead_sets_verify_cmd_and_runs_verify(world):
    w = world.agent("w-one")
    with pytest.raises(IdentityError):
        world.board.new_card(w, "x", card_id="x1", verify_cmd="rm -rf /")
    world.card("c1", verify_cmd="true")
    with pytest.raises(IdentityError):
        world.board.verify("c1", w)


def _to_review(world, owner, cid="c1"):
    world.card(cid, locks=f"app/{cid}")
    world.claim(cid, owner)
    world.board.move(cid, "review", owner)


def test_review_flow_and_require_review(world):
    world.board.set_setting(world.lead, "require_verify", False)
    owner = world.agent("w-owner", runtime="claude-code")
    rev = world.agent("rev-codex", role="reviewer", runtime="codex-cli")
    world.board.set_setting(world.lead, "require_review", "1")
    _to_review(world, owner)
    with pytest.raises(Precondition):
        world.board.move("c1", "done", world.lead)
    with pytest.raises(Denied):
        world.board.review("c1", owner, "approve")
    world.board.review("c1", rev, "approve", "looks right")
    world.board.move("c1", "done", world.lead)
    assert world.board.transcript.read(kind="review")[0]["artifacts"][0]["metadata"]["kind"] == "review"


def test_changes_verdict_sends_back_to_doing_and_stale_approvals_do_not_count(world):
    world.board.set_setting(world.lead, "require_verify", False)
    owner = world.agent("w-owner")
    rev = world.agent("rev-one", role="reviewer", runtime="codex-cli")
    world.board.set_setting(world.lead, "require_review.count", "1")
    _to_review(world, owner)
    world.board.review("c1", rev, "approve")
    world.board.review("c1", rev, "changes", "fix the edge case")
    card = world.board.card("c1")
    assert card["lane"] == "doing" and card["owner"] == "w-owner"
    world.board.heartbeat("c1", owner)
    world.board.move("c1", "review", owner)
    assert world.board.approvals(world.board.card("c1")) == []
    world.board.review("c1", rev, "approve")
    world.board.note("c1", owner, "one more change after approval")
    with pytest.raises(Precondition):
        world.board.move("c1", "done", world.lead)
    world.board.review("c1", rev, "approve")
    world.board.move("c1", "done", world.lead)


def test_other_provider_required(world):
    world.board.set_setting(world.lead, "require_verify", False)
    owner = world.agent("w-owner", runtime="claude-code")
    same = world.agent("rev-same", role="reviewer", runtime="claude-code")
    other = world.agent("rev-other", role="reviewer", runtime="gemini-cli")
    world.board.set_setting(world.lead, "require_review.count", "1")
    world.board.set_setting(world.lead, "require_review.other_provider", "true")
    _to_review(world, owner)
    world.board.review("c1", same, "approve")
    with pytest.raises(Precondition):
        world.board.move("c1", "done", world.lead)
    world.board.review("c1", other, "approve")
    world.board.move("c1", "done", world.lead)


def test_assigned_reviewers_and_review_queue(world):
    owner = world.agent("w-owner")
    world.agent("rev-aaa", role="reviewer", runtime="codex-cli")
    b = world.agent("rev-bbb", role="reviewer", runtime="codex-cli")
    _to_review(world, owner)
    r = world.board.assign_reviewers("c1", owner, "rev-aaa")
    assert r["reviewers"] == ["rev-aaa"] and r["warnings"] == []
    with pytest.raises(Denied):
        world.board.review("c1", b, "approve")
    assert "c1" in world.board.claimable(agent="rev-aaa", for_review=True)
    assert "c1" not in world.board.claimable(agent="rev-bbb", for_review=True)
    assert "c1" not in world.board.claimable(agent="w-owner", for_review=True)


def test_settings_lead_only(world):
    w = world.agent("w-one")
    with pytest.raises(IdentityError):
        world.board.set_setting(w, "auth", "off")
    with pytest.raises(Precondition):
        world.board.set_setting(world.lead, "ttl_minutes", "-1")
    world.board.set_setting(world.lead, "ttl_minutes", "10")
    assert world.board.get_setting("ttl_minutes") == 10


def test_brief_cap_cuts_notes_first_never_scope_or_locks(world):
    w = world.agent("w-one")
    world.board.new_card(
        world.lead,
        "Big",
        card_id="big",
        team="core",
        locks="app/big",
        scope="S" * 400,
        verify="run the tests",
        output="O" * 200,
    )
    world.claim("big", w)
    for i in range(40):
        world.board.note("big", w, f"note number {i} " + "x" * 80)
    full = world.board.brief("big", max_tokens=100000)
    from raidlock.core.brief import render

    card = world.board.card("big")
    budget = render(dict(card, notes=card["notes"][-1:]), max_tokens=100000)["tokens"]
    capped = world.board.brief("big", max_tokens=budget)
    assert capped["tokens"] <= budget and not capped["over_cap"]
    assert full["dropped"] == 0 and capped["dropped"] > 0
    assert "scope: " + "S" * 400 in capped["text"] and "locks: app/big" in capped["text"]
    assert "verify: run the tests" in capped["text"]
    assert "note number 39" in capped["text"] and "note number 0 " not in capped["text"]
    tiny = world.board.brief("big", max_tokens=10)
    assert tiny["over_cap"] and "locks: app/big" in tiny["text"]
    assert world.board.brief("big", max_tokens=budget)["text"] == capped["text"]


def test_claimable_survives_a_card_that_cannot_be_claimed(world, monkeypatch):
    world.agent("w-one")
    world.card("ok", locks="app/ok")
    world.card("odd", locks="app/odd")
    real = type(world.board)._claim_reasons

    def flaky(self, card, *args, **kwargs):
        if card["id"] == "odd":
            raise Precondition("lock aws/123456789012/sms matches no repo in workspace demo")
        return real(self, card, *args, **kwargs)

    monkeypatch.setattr(type(world.board), "_claim_reasons", flaky)
    listing = world.board.claimable(agent="w-one")
    assert listing["ok"]["ok"]
    assert not listing["odd"]["ok"]
    assert any("matches no repo" in r for r in listing["odd"]["reasons"])


def test_slug_cuts_at_a_word_boundary():
    from raidlock.core.model import slug

    assert slug("Dependencies view: make the graph reliable") == "dependencies-view-make-the-graph"
    assert slug("UX audit fixes: drawer opacity, tables, contrast") == "ux-audit-fixes-drawer-opacity-tables"
    assert slug("short title") == "short-title"
    assert slug("a" * 60) == "card"
    assert not slug("x " * 30).endswith("-")
