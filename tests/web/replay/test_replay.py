import json

import pytest
from browser import assert_fits, call_cli, open_view, running_animations

from raidlock.core import Precondition
from raidlock.web.replay import fold


def body(resp):
    return json.loads(resp[2])


@pytest.fixture
def raid(tmp_path):
    import demo

    d = demo.Demo(tmp_path / "r", "replay-lab", "repo")
    d.team("core", 3)
    d.agent("ana", "core", "claude-code", "claude-sonnet-5-5")
    d.agent("ben", "core", "claude-code", "claude-haiku-5-5")
    d.card("a", "Card A", "core", "a")
    d.card("b", "Card B", "core", "b")
    d.claim("a", "ana")
    d.cost("a", "ana", 1000, 0, 500)
    d.move("a", "ana", "review")
    d.claim("b", "ben")
    d.board.release("b", d.callers["ben"])
    d.done("a")
    d.board.reopen("a", d.lead)
    return d


def lanes(state):
    return {c["id"]: (c["lane"], c["owner"]) for c in state["cards"]}


def seq_of(state, text):
    return next(m["seq"] for m in state["marks"] if text in m["text"])


def test_fold_rebuilds_each_point(raid):
    end = fold(raid.board)
    assert lanes(end) == {"a": ("todo", ""), "b": ("todo", "")}
    assert end["at"] == end["last"] and end["untracked"] == 0
    claimed = fold(raid.board, seq_of(end, "claimed by ana"))
    assert lanes(claimed)["a"] == ("doing", "ana")
    assert claimed["holders"] == [{"agent": "ana", "card": "a", "lane": "doing", "paths": ["repo/a"]}]
    review = fold(raid.board, seq_of(end, "a -> review"))
    assert lanes(review)["a"] == ("review", "ana")
    assert next(c for c in review["cards"] if c["id"] == "a")["tokens"] == 1500
    done = fold(raid.board, seq_of(end, "a -> done"))
    assert lanes(done) == {"a": ("done", "ana"), "b": ("todo", "")}
    assert done["counts"]["done"] == 1
    taken = fold(raid.board, seq_of(end, "claimed by ben"))
    assert lanes(taken)["b"] == ("doing", "ben")


def test_fold_matches_current_board(server):
    board_cards = {c["id"]: (c["lane"], c["owner"]) for c in body(server.get("/api/board/acme-shop"))["cards"]}
    end = body(server.get("/api/replay/acme-shop"))
    assert lanes(end) == board_cards


def test_fold_clamps_and_validates(raid):
    end = fold(raid.board)
    assert fold(raid.board, 10**9)["at"] == end["last"]
    assert fold(raid.board, 0)["at"] == end["first"]
    with pytest.raises(Precondition):
        fold(raid.board, -1)


def test_empty_transcript(tmp_path):
    from raidlock.core import Board

    board, _ = Board.init(tmp_path / "empty", lead="lead", runtime="human")
    state = fold(board)
    assert state["at"] >= 0 and state["counts"]["todo"] == 0


def test_api_marks_are_optional(server):
    status, headers, raw = server.get("/api/replay/acme-shop")
    full = json.loads(raw)
    assert status == 200 and full["marks"] and full["marks"][-1]["seq"] == full["last"]
    point = body(server.get(f"/api/replay/acme-shop?at={full['first'] + 5}&marks=0"))
    assert point["marks"] == [] and point["at"] == full["first"] + 5
    assert len(point["recent"]) <= 8 and point["recent"][0]["seq"] == point["at"]
    assert server.get("/api/replay/acme-shop?at=nope")[0] == 404
    assert server.get("/api/replay/acme-shop", headers={"If-None-Match": headers["ETag"]})[0] == 304


def test_cli_replay(raid, capsys):
    end = fold(raid.board)
    code, out = call_cli(capsys, raid.board, "replay", "--at", str(seq_of(end, "claimed by ana")))
    assert code == 0
    assert lanes(out)["a"] == ("doing", "ana") and out["marks"] == []


@pytest.mark.parametrize("theme", ["raid", "plain"])
@pytest.mark.parametrize("width", [1280, 375])
def test_replay_view_scrubs(browse, theme, width):
    p, page = open_view(browse, "/acme-shop?view=replay", ".rp-lane", theme=theme, width=width)
    page.wait_for_selector(".rp-body .tile")
    done = page.locator('.rp-lane[data-lane="done"] .count')
    assert done.inner_text() == "2"
    page.locator("#replayRange").focus()
    page.keyboard.press("Home")
    page.wait_for_function("() => document.querySelector('.rp-lane[data-lane=done] .count').textContent === '0'")
    assert page.locator(".rp-out").inner_text().startswith("#")
    assert page.locator("#replayRange").get_attribute("aria-valuetext").startswith("record ")
    page.locator(".rp-controls button", has_text="Next").click()
    page.wait_for_function("() => document.getElementById('replayRange').value === '1'")
    page.locator("#replayRange").focus()
    page.keyboard.press("End")
    page.wait_for_function("() => document.querySelector('.rp-lane[data-lane=done] .count').textContent === '2'")
    assert_fits(p, page)


def test_replay_play_pause_and_leave(browse):
    p, page = open_view(browse, "/acme-shop?view=replay", ".rp-lane")
    page.locator("#replayRange").focus()
    page.keyboard.press("Home")
    play = page.locator(".rp-controls button", has_text="Play")
    play.click()
    assert page.locator(".rp-controls [aria-pressed=true]").count() == 1
    page.wait_for_function("() => +document.getElementById('replayRange').value >= 2", timeout=10000)
    page.locator(".rp-controls [aria-pressed=true]").click()
    value = page.locator("#replayRange").input_value()
    page.wait_for_timeout(1000)
    assert page.locator("#replayRange").input_value() == value
    page.locator("#viewTabs a[data-sub=focus]").click()
    page.wait_for_selector("#focus .row")
    assert page.locator("#viewPane").is_hidden()
    assert p.errors == []


def test_replay_respects_reduced_motion(browse):
    p, page = open_view(browse, "/acme-shop?view=replay", ".rp-lane", reduced_motion="reduce")
    assert running_animations(page) == 0
    assert p.errors == []


def test_unverified_history_and_sources(raid):
    from raidlock.core.transcript import make

    raid.board.transcript.append(make("status", {"agent": "fake"}, task="a", src="external", data={"lane": "done"}))
    result = fold(raid.board)
    assert result["trust"] == "unverified history"
    assert result["non_core_records"]
    assert result["marks"][-1]["source"] == "external"
    assert all(c["trust"] == "unverified history" for c in result["cards"])
    assert lanes(result)["a"][0] == "todo"


def test_history_cap_reports_true_latest(raid, monkeypatch):
    import raidlock.web.replay as replay

    latest = raid.board.transcript.read(limit=1)[0]["seq"]
    monkeypatch.setattr(replay, "MAX_RECORDS", 2)
    result = fold(raid.board)
    assert result["incomplete_history"] and result["last"] == latest
    assert result["at"] < latest


def test_history_budget_cache_and_shared_fold(raid, monkeypatch):
    import raidlock.web.replay as replay

    monkeypatch.setattr(replay, "MAX_BYTES", 1)
    assert fold(raid.board)["incomplete_history"]
    monkeypatch.setattr(raid.board.transcript, "_load", lambda rows: pytest.fail("cache missed"))
    assert replay.payload(raid.board, {})["territory"]["incomplete_history"]


def test_historical_metadata_does_not_gain_later_locks(raid):
    state = fold(raid.board)
    point = seq_of(state, "claimed by ana")
    raid.board.lock_paths("a", raid.lead, ["repo/later"])
    earlier = fold(raid.board, point)
    assert earlier["holders"][0]["paths"] == ["repo/a"]


def test_release_clears_review_lease():
    from raidlock.core.transcript import make
    from raidlock.web.replay import State

    state = State({})
    for text, data in [
        ("claimed by ana", {"a2a_state": "working", "fence": 1, "locks": ["repo/a"]}),
        ("a -> review", {"lane": "review"}),
        ("lease released", {}),
    ]:
        state.apply(make("status", {"agent": "ana"}, task="a", text=text, data=data))
    assert state.cards["a"]["owner"] == "ana"
    assert state.cards["a"]["lease_holder"] == ""


def test_playback_single_request_and_no_inline_view_styles():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "src/raidlock/web"
    js = (root / "replay/view.js").read_text()
    assert "if (!m || inFlight) return;" in js
    assert 'c.api("raidmap"' not in js
    for name in ("raidmap", "frames", "achievements"):
        assert ".style" not in (root / name / "view.js").read_text()
    assert "i.style.height" not in (root / "static/app.js").read_text()


def test_history_cache_invalidates_on_workspace_version(raid):
    from raidlock.core.transcript import make

    before = fold(raid.board)
    raid.board.transcript.append(make("note", {"agent": "ana"}, task="a", text="new note"))
    assert fold(raid.board)["last"] > before["last"]


def test_legacy_locks_are_current_metadata_without_ownership(raid):
    from raidlock.core.transcript import make
    from raidlock.web.raidmap import territory
    from raidlock.web.replay import State

    state = State({"old": "Current title"})
    state.apply(
        make("status", {"agent": "ana"}, task="old", text="claimed by ana", data={"a2a_state": "working", "fence": 1})
    )
    assert state.cards["old"]["title_source"] == "current metadata"
    assert "old" not in state.locks
    result = territory(
        raid.board,
        at=1,
        state={
            "cards": [{**state.cards["old"], "locks": ["repo/current"]}],
            "holders": [],
            "incomplete_history": False,
        },
    )
    path = result["repos"][0]["paths"][0]
    assert result["approximate"] and path["metadata_source"] == "current metadata"
    assert path["holder"] == ""


def test_historical_territory_work_budget(raid, monkeypatch):
    import raidlock.web.raidmap as raidmap

    point = seq_of(fold(raid.board), "claimed by ana")
    monkeypatch.setattr(raidmap, "MAX_HISTORY_WORK", 0)
    result = raidmap.territory(raid.board, point)
    assert result["incomplete_history"] and result["error"] == "history work budget exceeded"
