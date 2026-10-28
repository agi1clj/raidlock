import json
import re

import pytest
from browser import assert_fits, call_cli, open_view

from raidlock.achievements import CATALOG, CLEAN_CARDS, evaluate
from raidlock.core import receipts
from raidlock.core import transcript as T


def earned(board):
    return {a["id"]: a for a in evaluate(board)["achievements"] if a["earned"]}


def rows(board):
    return {a["id"]: a for a in evaluate(board)["achievements"]}


@pytest.fixture
def lab(tmp_path):
    import demo

    d = demo.Demo(tmp_path / "a", "trophy-lab", "repo")
    d.team("core", 60)
    d.agent("ana", "core", "claude-code", "claude-sonnet-5-5")
    d.agent("ben", "core", "claude-code", "claude-haiku-5-5")
    return d


def finish(d, cid, who):
    d.card(cid, f"Card {cid}", "core", cid)
    d.claim(cid, who)
    d.move(cid, who, "review")
    d.done(cid)


def record_verify(d, cid, passed=True, sign=True):
    card = d.board.card(cid)
    card["verify_cmd"] = "true"
    d.board._save_card(card)
    rec = {
        "ts": "2026-10-23T10:00:00Z",
        "by": "raid-lead",
        "cmd": "true",
        "exit": 0 if passed else 1,
        "passed": passed,
        "card": cid,
        "board": d.board.uid,
        "command_hash": receipts.command_hash("true"),
    }
    rec = receipts.sign(d.board, rec) if sign else {**rec, "signature": "0" * 64}
    with d.board.lock():
        d.board._record(
            "verify", "raid-lead", "verify", task=cid, artifacts=[T.artifact("verify", "", data=rec, kind="verify")]
        )


def test_nothing_is_earned_on_a_fresh_board(lab):
    result = evaluate(lab.board)
    assert result["earned"] == 0 and result["total"] == len(CATALOG)


def test_first_light_and_seal_need_real_events(lab):
    finish(lab, "one", "ana")
    assert set(earned(lab.board)) == {"first-light", "swift-blade"}
    assert earned(lab.board)["first-light"]["by"] == "ana"
    record_verify(lab, "one", sign=False)
    assert "first-seal" not in earned(lab.board)
    record_verify(lab, "one", passed=False)
    assert "first-seal" not in earned(lab.board)
    record_verify(lab, "one")
    assert earned(lab.board)["first-seal"]["detail"] == "one"


def test_second_draft_needs_changes_then_approval(lab):
    lab.agent("rev", "", "claude-code", "claude-opus-5-5", role="reviewer")
    lab.card("two", "Two", "core", "two")
    lab.claim("two", "ana")
    lab.move("two", "ana", "review")
    lab.review("two", "rev", "approve")
    assert "second-draft" not in earned(lab.board)
    lab.review("two", "rev", "changes", "fix it")
    assert "second-draft" not in earned(lab.board)
    lab.move("two", "ana", "review")
    lab.review("two", "rev", "approve")
    assert earned(lab.board)["second-draft"]["by"] == "ana"
    assert "second-opinion" not in earned(lab.board)


def test_clean_hands_counts_done_cards_without_violations(lab):
    for i in range(CLEAN_CARDS - 1):
        finish(lab, f"c{i:02d}", "ana")
    assert rows(lab.board)["clean-hands"]["progress"] == {"value": CLEAN_CARDS - 1, "goal": CLEAN_CARDS}
    lab.card("bad", "Bad", "core", "bad")
    lab.claim("bad", "ben")
    lab.board.check_write(str(lab.root / "repo" / "c00" / "x.py"), lab.callers["ben"])
    finish(lab, "last", "ana")
    assert earned(lab.board)["clean-hands"]["by"] == "ana"
    lab.board.check_write(str(lab.root / "repo" / "bad" / "x.py"), lab.callers["ana"])
    assert rows(lab.board)["clean-hands"]["earned"] is True


def test_violation_blocks_clean_hands(lab):
    lab.card("own", "Own", "core", "own")
    lab.claim("own", "ana")
    lab.board.check_write(str(lab.root / "repo" / "elsewhere.py"), lab.callers["ana"])
    lab.move("own", "ana", "review")
    lab.done("own")
    assert rows(lab.board)["clean-hands"]["progress"]["value"] == 0


def test_demo_events(server):
    status, _, raw = server.get("/api/achievements/acme-shop")
    d = json.loads(raw)
    got = {a["id"] for a in d["achievements"] if a["earned"]}
    assert status == 200
    assert got == {"first-light", "second-opinion", "second-draft", "many-voices", "swift-blade"}
    opinion = next(a for a in d["achievements"] if a["id"] == "second-opinion")
    assert opinion["by"] == "haiku-inspector" and "anthropic reviewed openai" in opinion["detail"]


def test_names_and_art_are_original():
    banned = re.compile(r"(?i)warcraft|azeroth|xbox|playstation|steam|nintendo|zelda|pokemon|leeroy|gamerscore|trophy")
    for key, name, desc, art in CATALOG:
        assert not banned.search(" ".join((key, name, desc, art)))
    assert len({name for _, name, _, _ in CATALOG}) == len(CATALOG)


def test_cli(tmp_path, capsys):
    import demo

    d = demo.acme_shop(tmp_path / "cli")
    code, out = call_cli(capsys, d.board, "achievements")
    assert code == 0 and out["earned"] == 5


def test_on_by_default_and_off_stops_asking_the_server(browse):
    p = browse()
    page = p.open("/acme-shop")
    page.wait_for_selector("#viewTabs a[data-sub=achievements]")
    assert page.locator("#achieveBtn").get_attribute("aria-pressed") == "true"
    page.locator("#settings summary").click()
    page.locator("#achieveBtn").click()
    assert page.locator("#achieveBtn").get_attribute("aria-pressed") == "false"
    calls = []
    p.page.on("request", lambda r: calls.append(r.url) if "/api/achievements" in r.url else None)
    page.reload()
    page.wait_for_selector("#viewTabs a")
    assert page.locator("#viewTabs a[data-sub=achievements]").count() == 0
    page.goto(p.server.url + "/acme-shop?view=achievements")
    page.wait_for_selector("#focus .row")
    assert page.locator("#viewPane").is_hidden()
    page.wait_for_timeout(500)
    assert calls == []
    assert p.errors == []


@pytest.mark.parametrize("width", [1280, 375])
def test_badges_show_and_turn_off(browse, width):
    p, page = open_view(browse, "/acme-shop", "#todoList .row", width=width)
    assert page.locator("#achieveBtn").get_attribute("aria-pressed") == "true"
    page.locator("#viewTabs a[data-sub=achievements]").click()
    page.wait_for_selector(".ach")
    assert page.locator(".ach.earned").count() == 5
    assert page.locator(".ach.locked .ach-meter[role=meter]").count() == 1
    assert page.locator(".ach svg.ach-art").count() == len(CATALOG)
    assert page.locator(".ach img").count() == 0
    assert_fits(p, page)
    page.reload()
    page.wait_for_selector(".ach")
    page.locator("#settings summary").click()
    page.locator("#achieveBtn").click()
    page.wait_for_selector("#focus .row")
    assert page.locator("#viewTabs a[data-sub=achievements]").count() == 0
    assert p.errors == []


def test_new_feats_and_titles_are_in_the_catalog():
    from raidlock.achievements import CATALOG, TITLES

    ids = {row[0] for row in CATALOG}
    assert {"clean-run", "peacekeeper", "swift-blade", "night-watch"} <= ids
    assert set(TITLES) <= ids


def test_swift_blade_and_its_title(lab):
    finish(lab, "fast", "ana")
    got = evaluate(lab.board)
    assert "swift-blade" in {a["id"] for a in got["achievements"] if a["earned"]}
    assert got["titles"]["ana"] == "the Swift"


@pytest.mark.parametrize("first_pass,expected", [(True, True), (False, False)])
def test_clean_run_needs_the_first_verify_to_pass(lab, first_pass, expected):
    lab.card("run", "Card run", "core", "run")
    lab.claim("run", "ana")
    record_verify(lab, "run", passed=first_pass)
    if not first_pass:
        record_verify(lab, "run", passed=True)
    lab.move("run", "ana", "review")
    lab.done("run")
    assert ("clean-run" in earned(lab.board)) is expected
