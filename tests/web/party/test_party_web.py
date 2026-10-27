import pytest
from browser import Page, assert_fits, open_view, settle
from serving import start

pytest.importorskip("playwright.sync_api")

ROLES = {"haiku-inspector": "Healer", "codex-payments": "DPS", "gemini-search": "Tank", "opus-checkout": "DPS"}


@pytest.mark.parametrize("width", [390, 1280])
def test_party_roles_in_raid_frames(browse, width):
    p, page = open_view(browse, "/acme-shop?view=frames", ".frame", theme="raid", width=width)
    page.locator("#viewPane .sub-fold > summary").click()
    for agent, role in ROLES.items():
        badge = page.locator(f'.frame[data-agent="{agent}"] .prole')
        assert badge.count() == 1
        assert badge.inner_text().strip().endswith(role)
        assert badge.locator(".sr-only").text_content() == "Party role: "
    assert page.locator('.frame[data-agent="raid-lead"] .prole').count() == 0
    assert_fits(p, page)


@pytest.fixture
def realm(tmp_path):
    import demo

    d = demo.Demo(tmp_path / "b", "boss-realm", "repo")
    d.team("core", 9)
    d.agent("ana", "core", "claude-code", "claude-sonnet-5-5")
    d.card("gate", "Open the gate", "core", "gate")
    for cid in ("wall", "tower", "keep"):
        d.card(cid, f"Take the {cid}", "core", cid, depends="gate")
    d.card("old", "Old foe", "core", "old")
    for cid in ("o1", "o2", "o3"):
        d.card(cid, f"Old {cid}", "core", cid, depends="old")
    for cid in ("gate", "old", "o1", "o2", "o3"):
        d.claim(cid, "ana")
        d.move(cid, "ana", "review")
        d.done(cid)
    running = start()
    yield running
    running.srv.shutdown()
    running.srv.server_close()


@pytest.mark.parametrize("width", [390, 1280])
def test_boss_board_and_loot(chromium, realm, width):
    p = Page(chromium, realm, width=width)
    page = p.open("/boss-realm?view=bosses", theme="raid")
    settle(page, ".boss")
    assert page.locator("#viewTabs a[data-sub=bosses]").inner_text() == "Boss board"
    gate = page.locator('.boss[data-id="gate"]')
    meter = gate.locator(".boss-hp")
    assert meter.get_attribute("role") == "meter" and meter.get_attribute("aria-valuenow") == "75"
    old = page.locator('.boss[data-id="old"]')
    assert "slain" in old.get_attribute("class") and old.locator(".slain-at time").count() == 1
    bag = page.locator('.bag[data-agent="ana"] .drop')
    assert bag.count() == 5
    assert "Rare" in page.locator('.drop[data-card="old"]').inner_text()
    first = bag.first.inner_text()
    page.reload()
    settle(page, ".drop")
    assert page.locator('.bag[data-agent="ana"] .drop').first.inner_text() == first
    assert_fits(p, page)
    p.close()


def test_boss_board_empty_state(browse):
    p, page = open_view(browse, "/acme-shop?view=bosses", "#viewPane .empty", theme="raid", width=390)
    assert "No bosses roam this realm yet" in page.locator("#viewPane").inner_text()
    assert page.locator('.bag[data-agent="codex-payments"] .drop').count() == 1
    assert_fits(p, page)


def test_plain_theme_hides_raid_only_ui(browse):
    p, page = open_view(browse, "/acme-shop?view=frames", ".frame", theme="plain")
    assert page.locator(".prole").count() == 0
    assert page.locator("#viewTabs a[data-sub=bosses]").count() == 0
    page.goto(p.server.url + "/acme-shop?view=bosses")
    page.wait_for_selector("#focus .row")
    assert page.locator("#viewPane").is_hidden()
    assert page.locator(".boss, .bag, .drop").count() == 0
    text = page.locator("body").inner_text()
    assert "Boss board" not in text and "Loot" not in text and "Tank" not in text
    assert p.errors == []
