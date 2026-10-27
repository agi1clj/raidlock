import re

import pytest

from raidlock.frames import frames
from raidlock.party import ADJECTIVES, BOSS_DEPENDENTS, RARITIES, dependents, item_name, payload, roles, tier


@pytest.fixture
def lab(tmp_path):
    import demo

    d = demo.Demo(tmp_path / "p", "party-lab", "repo")
    d.team("core", 9)
    for name in ("ana", "ben"):
        d.agent(name, "core", "claude-code", "claude-sonnet-5-5")
    d.agent("rev", "", "codex-cli", "gpt-6-astra", role="reviewer")
    d.board.set_setting(d.lead, "require_review.count", 0)
    return d


def finish(d, cid, who, **kw):
    d.card(cid, f"Card {cid}", "core", cid, **kw)
    d.claim(cid, who)
    d.move(cid, who, "review")
    d.done(cid)


def card(cid, *deps, lane="todo"):
    return {"id": cid, "lane": lane, "depends": list(deps)}


def test_dependents_are_transitive_and_survive_cycles():
    got = dependents([card("a", "c"), card("b", "a"), card("c", "b"), card("d", "a"), card("x", "ghost", "x")])
    assert got["a"] == {"b", "c", "d"}
    assert got["d"] == set() and got["x"] == set()


def test_no_agents_means_no_roles(tmp_path):
    import demo

    d = demo.Demo(tmp_path / "e", "empty", "repo")
    assert {k: v["role"] for k, v in roles(d.board).items()} == {"raid-lead": ""}


def test_roles_follow_recorded_activity(lab):
    finish(lab, "one", "ana")
    finish(lab, "two", "ana")
    lab.card("hard", "Hard", "core", "hard")
    lab.claim("hard", "ben")
    lab.move("hard", "ben", "blocked", note="stuck")
    lab.card("rev-me", "Review me", "core", "rev-me")
    lab.claim("rev-me", "ana")
    lab.move("rev-me", "ana", "review")
    lab.review("rev-me", "rev", "changes", "again")
    got = roles(lab.board)
    assert got["ana"]["role"] == "dps" and got["ana"]["dps"] == 3
    assert got["ben"]["role"] == "tank" and got["ben"]["tank"] == 2
    assert got["rev"]["role"] == "healer"
    assert {f["agent"]: f["party"] for f in frames(lab.board)["agents"]}["ben"] == "tank"
    assert roles(lab.board) == got


def test_an_open_lease_is_dps_and_idle_agents_get_none(lab):
    finish(lab, "one", "ana")
    lab.card("two", "Two", "core", "two")
    lab.claim("two", "ana")
    got = roles(lab.board)
    assert got["ana"]["dps"] == 2 and got["ana"]["tank"] == 0 and got["ana"]["role"] == "dps"
    assert got["ben"]["role"] == ""


def test_cancelled_cards_do_not_count(lab):
    finish(lab, "one", "ana")
    card_ = lab.board.card("one")
    card_["lane"] = "cancelled"
    lab.board._save_card(card_)
    assert roles(lab.board)["ana"]["role"] == ""


def chain(d):
    d.card("root", "Root", "core", "root")
    d.card("a", "A", "core", "a", depends="root")
    d.card("b", "B", "core", "b", depends="a")
    d.card("c", "C", "core", "c", depends="root")


def close(d, cid, who):
    d.claim(cid, who)
    d.move(cid, who, "review")
    d.done(cid)


def test_no_cards_no_bosses_no_loot(lab):
    assert payload(lab.board, {}) == {"bosses": [], "bags": [], "boss_dependents": BOSS_DEPENDENTS}


def test_boss_hp_falls_and_boss_is_slain(lab):
    chain(lab)
    lab.card("solo", "Solo", "core", "solo", depends="a")
    first = payload(lab.board, {})["bosses"]
    assert [b["id"] for b in first] == ["root"]
    assert first[0]["dependents"] == 4 and first[0]["hp"] == 100 and not first[0]["slain"]
    close(lab, "root", "ana")
    close(lab, "a", "ana")
    assert payload(lab.board, {})["bosses"][0]["hp"] == 60
    for cid in ("b", "c", "solo"):
        close(lab, cid, "ben")
    boss = payload(lab.board, {})["bosses"][0]
    assert boss["slain"] and boss["hp"] == 0 and boss["slain_at"]


def test_cycles_and_cancelled_cards(lab):
    lab.card("x", "X", "core", "x")
    lab.card("y", "Y", "core", "y", depends="x")
    lab.card("z", "Z", "core", "z", depends="y")
    lab.board.edit_card(lab.lead, "x", depends="z")
    lab.card("w", "W", "core", "w", depends="z")
    got = {b["id"]: b for b in payload(lab.board, {})["bosses"]}
    assert set(got) == {"x", "y", "z"} and got["x"]["total"] == 4
    w = lab.board.card("w")
    w["lane"] = "cancelled"
    lab.board._save_card(w)
    assert payload(lab.board, {})["bosses"] == []


def test_loot_is_deterministic_and_rated(lab):
    chain(lab)
    close(lab, "root", "ana")
    finish(lab, "easy", "ben")
    first = payload(lab.board, {})["bags"]
    assert [g["agent"] for g in first] == ["ana", "ben"]
    root, easy = first[0]["items"][0], first[1]["items"][0]
    assert root["rarity"] == "Rare" and easy["rarity"] == "Common"
    assert root["item"] == item_name("root", 2) and payload(lab.board, {})["bags"] == first
    assert item_name("root", 2).split()[0] in ADJECTIVES[2]


def test_rarity_signals():
    hard = {
        "reviews": [{"verdict": "changes"}, {"verdict": "changes"}, {"verdict": "approve"}],
        "verifications": [{"passed": True}],
        "costs": [{"duration_ms": 5 * 3600000}],
    }
    assert tier({}, 0) == 0
    assert tier(hard, 4) == 4
    assert tier({**hard, "verifications": [{"passed": False}, {"passed": True}]}, 4) == 4
    assert tier({"costs": [{"duration_ms": 3600000}]}, 0) == 1
    assert RARITIES[tier(hard, 0)] == "Epic"


def test_item_names_are_original():
    banned = re.compile(r"(?i)warcraft|azeroth|ashbringer|frostmourne|thunderfury|sulfuras|atiesh|shadowmourne|trophy")
    names = {item_name(f"c{i}", r) for i in range(200) for r in range(len(RARITIES))}
    assert not any(banned.search(n) for n in names)


def test_lead_marks_a_large_quest_as_boss(lab):
    lab.card("epic", "Epic", "core", "epic")
    assert payload(lab.board, {})["bosses"] == []
    lab.board.edit_card(lab.lead, "epic", boss=True)
    boss = payload(lab.board, {})["bosses"][0]
    assert boss["id"] == "epic" and boss["dependents"] == 0 and boss["hp"] == 100
    lab.board.edit_card(lab.lead, "epic", boss=False)
    assert payload(lab.board, {})["bosses"] == []


def test_only_the_lead_marks_a_boss(lab):
    from raidlock.core.errors import IdentityError

    lab.card("epic", "Epic", "core", "epic")
    with pytest.raises(IdentityError):
        lab.board.edit_card(lab.board.caller("ana", lab.tokens["ana"]), "epic", boss=True)


def test_marking_a_boss_keeps_reviews_current(lab):
    lab.card("epic", "Epic", "core", "epic")
    rev = lab.board.card("epic")["rev"]
    lab.board.edit_card(lab.lead, "epic", boss=True)
    assert lab.board.card("epic")["rev"] == rev
    lab.board.edit_card(lab.lead, "epic", scope="bigger")
    assert lab.board.card("epic")["rev"] == rev + 1


def test_streak_counts_consecutive_days_with_a_done_card():
    import datetime

    from raidlock.party import streak

    today = datetime.date(2026, 10, 27)

    def done(day):
        return {"lane": "done", "updated": f"2026-10-{day:02d}T12:00:00Z"}

    assert streak([done(27), done(26), done(25), done(23)], today) == 3
    assert streak([done(26), done(25)], today) == 2
    assert streak([done(24)], today) == 0
    assert streak([{"lane": "todo", "updated": "2026-10-27T12:00:00Z"}], today) == 0


def test_slain_bosses_name_the_final_blow(lab):
    from raidlock.party import slain_bosses

    chain(lab)
    lab.card("solo", "Solo", "core", "solo", depends="a")
    assert slain_bosses(lab.board.cards()) == []
    for cid in ("root", "a", "b", "c", "solo"):
        close(lab, cid, "ana")
    slain = slain_bosses(lab.board.cards())
    assert [b["id"] for b in slain] == ["root"] and slain[0]["final"] == "ana" and slain[0]["total"] == 5
