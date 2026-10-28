import os
from pathlib import Path

import pytest
from browser import Page, no_hscroll
from serving import start

expect = pytest.importorskip("playwright.sync_api").expect

ASSETS = Path(__file__).resolve().parents[2] / "docs" / "assets"


def open_score(page):
    page.wait_for_selector("#overviewCosts #score")


def test_model_reputation_evidence_is_readable_at_375px(browse):
    p = browse(width=375)
    page = p.open("/acme-shop?view=treasury", panels=("overviewCosts",))
    open_score(page)
    page.wait_for_selector("#score .model-trust > h3")
    assert page.locator("#score .model-trust tbody tr").count() > 0
    summary = page.locator("#score .model-trust tbody section").first
    assert summary.get_attribute("aria-label").startswith("verify ")
    evidence = page.locator("#score .model-trust .evid").first
    assert "Verify runs passed" in evidence.inner_text() and "pts" in evidence.inner_text()
    assert page.locator("#score .model-trust pre").count() == 0
    assert "{" not in evidence.inner_text()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert p.errors == []


@pytest.mark.parametrize("width", [375, 1280])
def test_scorecard_fits_without_clipping(browse, width):
    p = browse(width=width)
    page = p.open("/acme-shop?view=treasury", panels=("overviewCosts",))
    open_score(page)
    page.wait_for_selector("#score table.stack tbody tr")
    fits = page.evaluate(
        """() => [...document.querySelectorAll('#score table.stack')].every(t => {
            const box = t.closest('.tbl');
            return t.scrollWidth <= box.clientWidth + 1;
        })"""
    )
    assert fits
    if width == 375:
        cell = page.locator("#score table.stack tbody td").nth(3)
        assert cell.evaluate("e => getComputedStyle(e).display") == "flex"
        assert cell.evaluate("e => getComputedStyle(e, '::before').content").strip('"') == "Done"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert p.errors == []


def test_overview_renders_without_errors(browse):
    p = browse()
    page = p.open("/", panels=("chatter",))
    page.wait_for_selector(".proj")
    names = " ".join(page.locator(".proj h3").all_inner_texts())
    assert page.locator("main").count() == 1
    for ws in ("acme-shop", "pocket-notes", "weather-api"):
        assert ws in names
    assert page.locator("#tabs a").count() == 5
    order = [t.split()[0] for t in page.locator("#projects .proj h3").all_inner_texts()]
    assert order[0] == "acme-shop" and order[1] == "pocket-notes"
    assert "acme-shop" in page.locator("#projects .proj.att-0").inner_text()
    assert "docs-site" in page.locator("#idle").inner_text()
    assert page.locator("#projects .proj", has_text="docs-site").count() == 0
    acme = page.locator("#projects .proj", has_text="acme-shop")
    assert acme.locator(".repo").count() == 3
    line = " ".join(acme.locator(".status-line").text_content().split())
    assert "2 on a quest" in line and "1 cursed" in line and "today" in line
    assert page.locator("#chatter").is_hidden()
    page.locator('#realmTabs a[data-sub="chat"]').click()
    page.locator("#chatter .msg").first.wait_for()
    assert page.locator("#chatter").is_visible() and page.locator("#projects").is_hidden()
    assert page.locator("#donePanel").is_hidden() and page.locator("#agentsPanel").is_hidden()
    assert page.locator("#logPanel").is_hidden()
    assert p.errors == []


def test_workspace_route_focus_and_all_cards(browse):
    p = browse()
    page = p.open("/acme-shop")
    page.wait_for_selector("#doingList .row", state="attached")
    assert page.locator("#tabs a[aria-current=page]").inner_text().startswith("acme-shop")
    assert page.locator("#viewTabs a[aria-current=page]").inner_text() == "War room"
    page.wait_for_selector("#needsList .row[data-id=refunds]")
    assert page.locator("#needsList .row[data-id=search-typo]").count() == 0
    assert "on others" in page.locator("#waitingNote").inner_text()
    pill = page.locator("#needsList .row[data-id=refunds] .pill.appr")
    assert pill.text_content().strip() == "Approvals 0/1"
    assert pill.get_attribute("title") == "This board needs 1 approving review before a card closes"
    assert page.locator("#todoList .row[data-id=promo-codes] .pill.noclaim").count() == 1
    assert page.evaluate("getComputedStyle(document.getElementById('wsbar')).position") == "sticky"
    page.locator("#viewTabs a[data-sub=all]").click()
    page.wait_for_url("**/acme-shop?view=all")
    page.wait_for_selector("#lanes .lane")
    assert page.locator("#lanes .lane").count() == 5
    assert page.locator(".lane[data-lane=done] .row").count() == 2
    page.locator("#repos button[data-repo=web]").click()
    page.wait_for_function("() => !document.querySelector('#lanes .row[data-id=cart-api]')")
    ids = page.locator("#lanes .row").evaluate_all("els => els.map(e => e.dataset.id)")
    assert sorted(ids) == ["pdp-images", "search-typo", "sitemap"]
    assert "1 cursed" in " ".join(page.locator("#wsStatus").text_content().split())
    page.locator("#repos button[data-repo='']").click()
    page.wait_for_selector("#lanes .row[data-id=cart-api]")
    page.go_back()
    page.wait_for_selector("#focus:not([hidden])")
    assert p.errors == []


def test_agents_are_one_click_away(browse):
    p = browse(width=375, height=800)
    page = p.open("/acme-shop")
    page.wait_for_selector("#doingList .row", state="attached")
    assert not page.evaluate("document.getElementById('agentsPanel').open")
    page.locator("#agentsPanel > summary").click()
    page.wait_for_selector(".agent[data-agent=opus-checkout]")
    assert "Claude Opus 5.5 · claude-code" in page.locator(".agent[data-agent=opus-checkout]").inner_text()
    assert page.locator(".agent[data-agent=opus-checkout] .bill.plan").count() == 1
    assert p.errors == []


def test_navigation_between_routes(browse):
    p = browse()
    page = p.open("/")
    page.locator("#tabs a[data-nav=pocket-notes]").click()
    page.wait_for_url("**/pocket-notes")
    page.wait_for_selector(".row[data-id=sync-merge]")
    page.go_back()
    page.wait_for_selector(".proj")
    page.goto(p.server.url + "/no-such-guild")
    page.wait_for_selector("#notice:not([hidden])")
    assert "no-such-guild" in page.locator("#notice").inner_text()


def test_xss_payload_renders_inert(browse):
    p = browse()
    page = p.open("/acme-shop", panels=("logPanel",))
    row = page.locator("#doingList .row[data-id=cart-api]")
    row.wait_for()
    row.locator("button.details").click()
    page.wait_for_function("() => document.querySelector('#drawerBody').textContent.includes('onerror')")
    page.wait_for_timeout(300)
    assert page.evaluate("window.__pwned") is None
    assert page.locator("img").count() == 0
    assert page.locator("body script:not([src])").count() == 0
    assert "<img src=x" in page.locator("#drawerBody").inner_text()
    assert p.errors == []


def test_keyboard_expands_cards(browse):
    p = browse()
    page = p.open("/acme-shop")
    page.wait_for_selector("#doingList .row", state="attached")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.classList.contains('skip')")
    for _ in range(80):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.matches('.row button.details')"):
            break
    else:
        pytest.fail("no card reachable with Tab")
    cid = page.evaluate("document.activeElement.closest('.row').dataset.id")
    button = page.locator(f".row[data-id='{cid}'] button.details")
    assert button.get_attribute("aria-expanded") == "false"
    page.keyboard.press("Enter")
    assert button.get_attribute("aria-expanded") == "true"
    assert page.locator(f".row[data-id='{cid}']").get_attribute("class").split().count("open") == 1
    assert page.locator(f"#{button.get_attribute('aria-controls')}").is_visible()
    assert page.evaluate("document.activeElement.id") == "drawerClose"
    page.keyboard.press(" ")
    assert button.get_attribute("aria-expanded") == "false"
    assert page.evaluate("document.activeElement.matches('.row button.details:focus-visible')")
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none"
    page.keyboard.press(" ")
    expect(button).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Escape")
    page.wait_for_function("() => !document.getElementById('cardDrawer').open")
    assert button.get_attribute("aria-expanded") == "false"
    assert page.evaluate("document.activeElement.matches('.row button.details')")
    assert p.errors == []


def test_menus_close_on_escape_and_return_focus(browse):
    p = browse()
    page = p.open("/acme-shop")
    page.wait_for_selector("#doingList .row", state="attached")
    page.locator("#soundMenu summary").focus()
    page.keyboard.press("Enter")
    assert page.evaluate("document.getElementById('soundMenu').open")
    page.keyboard.press("Tab")
    page.keyboard.press("Escape")
    assert not page.evaluate("document.getElementById('soundMenu').open")
    assert page.evaluate("document.activeElement.id") == "soundSummary"
    page.locator("#settings summary").click()
    page.locator("#soundMenu summary").click()
    assert page.evaluate("[document.getElementById('settings').open, document.getElementById('soundMenu').open]") == [
        False,
        True,
    ]


def test_board_titles_use_raid_wording(browse):
    p = browse()
    page = p.open("/acme-shop")
    page.wait_for_selector("#doingList .row", state="attached")
    assert page.locator("#todoTitle .count").inner_text() == "2"
    title = page.locator("#todoTitle").inner_text()
    assert title.startswith("\U0001f4dc") and "Quest board" in title
    assert page.locator("#todoTitle .raid-sub").inner_text().lower() == "to do"
    assert page.locator("#logTitle").inner_text().startswith("Raid log")


def test_sound_on_by_default_and_unlocks_on_first_click(browse):
    p = browse()
    page = p.open("/acme-shop")
    page.wait_for_selector("#doingList .row", state="attached")
    assert page.locator("#sound").get_attribute("aria-pressed") == "true"
    assert page.locator("#vol").input_value() == "80"
    assert page.locator("#soundBanner").is_hidden()
    page.locator("body").click(position={"x": 5, "y": 5})
    page.wait_for_function("() => document.getElementById('soundBanner').hidden")
    page.locator("#soundMenu summary").click()
    page.locator("#sound").click()
    assert page.locator("#sound").get_attribute("aria-pressed") == "false"
    page.reload()
    page.wait_for_selector("#doingList .row", state="attached")
    assert page.locator("#sound").get_attribute("aria-pressed") == "false"
    assert page.locator("#soundBanner").is_hidden()
    assert p.errors == []


def test_raid_log_is_virtualized(browse):
    p = browse()
    page = p.open("/acme-shop", panels=("logPanel",))
    page.wait_for_selector("#log .lrow")
    page.wait_for_function("() => parseInt(document.getElementById('logHint').innerText) > 20")
    total = int(page.locator("#logHint").inner_text().split()[0])
    rows = page.locator("#log .lrow").count()
    assert total > 20 and rows < total
    page.locator("#log .lrow").first.click()
    assert not page.locator("#logDetail").is_hidden()
    page.select_option("#logFilter", "pay-retry")
    page.wait_for_timeout(100)
    assert "on pay-retry" in page.locator("#logHint").inner_text()


def test_scorecard_and_treasury(browse):
    p = browse()
    page = p.open("/acme-shop?view=treasury", panels=("overviewCosts", "agentsPanel"))
    open_score(page)
    page.wait_for_selector("#score table")
    assert "GPT-6 Astra" in page.locator("#score").inner_text()
    assert "50%" in page.locator("#score").inner_text()
    t = page.locator("#treasury").inner_text()
    assert "at API prices" in t and "Plan" in t and "API" in t
    assert "Billing ?" not in t and "API-equiv" not in t
    page.select_option("#treasuryBy", "by_agent")
    page.wait_for_function("() => document.querySelector('#treasury').textContent.includes('opus-checkout')")


def test_reduced_motion_stops_animations(browse):
    p = browse(reduced_motion="reduce")
    page = p.open("/acme-shop")
    page.wait_for_selector("#doingList .row", state="attached")
    page.wait_for_timeout(300)
    running = page.evaluate("document.getAnimations().filter(a => a.playState === 'running').length")
    assert running == 0


@pytest.mark.parametrize("path", ["/", "/acme-shop"])
def test_mobile_375_has_no_horizontal_scroll(browse, path):
    p = browse(width=375, height=800, is_mobile=True, has_touch=True)
    page = p.open(path, panels=("donePanel", "agentsPanel", "logPanel", "chatter"))
    page.wait_for_selector(".proj" if path == "/" else "#doingList .row", state="attached")
    page.wait_for_timeout(300)
    tabs = [None] if path == "/" else ["todo", "doing", "done"]
    for tab in tabs:
        if tab:
            page.locator(f"#workTabs [data-tab={tab}]").click()
            page.wait_for_timeout(100)
        r = no_hscroll(page)
        assert r["scroll"] <= r["client"], (tab, r)
        assert r["wide"] == [], (tab, r)
    assert p.errors == []


def test_scale_caps_dom_nodes(chromium, tmp_path):
    import demo

    d = demo.bulk_board(tmp_path / "big", "big-board", 1000)
    for extra in range(3):
        demo.bulk_board(tmp_path / f"more{extra}", f"more-{extra}", 50, agents=2)
    running = start()
    try:
        p = Page(chromium, running)
        page = p.open("/" + d.board.name, panels=("logPanel",))
        page.wait_for_selector("#todoList .row", timeout=60000)
        assert page.locator("#todoList .row").count() == 5
        assert page.locator("#todoMore").inner_text().startswith("Unroll all")
        assert page.evaluate("document.getElementsByTagName('*').length") < 6000
        page.goto(running.url + "/" + d.board.name + "?view=all")
        page.wait_for_selector("#lanes .row", timeout=60000)
        assert page.locator("#lanes .row").count() <= 5 * 40
        assert page.locator(".lane[data-lane=todo] button.lane-more").count() == 1
        assert page.evaluate("document.getElementsByTagName('*').length") < 8000
        page.locator(".lane[data-lane=todo] button.lane-more").click()
        page.wait_for_function("() => document.querySelectorAll('.lane[data-lane=todo] .row').length === 80")
        assert p.errors == []
        p.close()
    finally:
        running.srv.shutdown()
        running.srv.server_close()


@pytest.mark.skipif(
    os.environ.get("RAIDLOCK_SHOTS") != "1", reason="set RAIDLOCK_SHOTS=1 to refresh README screenshots"
)
def test_screenshots(browse):
    ASSETS.mkdir(parents=True, exist_ok=True)
    for width, label in ((1280, "desktop"), (375, "mobile")):
        p = browse(width=width, height=900)
        page = p.open("/acme-shop", panels=("agentsPanel",))
        page.wait_for_selector("#doingList .row", state="attached")
        page.evaluate("document.getElementById('soundBanner').hidden = true")
        page.wait_for_timeout(800)
        page.screenshot(path=str(ASSETS / f"ui-raid-{label}.png"), full_page=True)
        page = p.open("/")
        page.wait_for_selector(".proj")
        page.evaluate("document.getElementById('soundBanner').hidden = true")
        page.wait_for_timeout(800)
        page.screenshot(path=str(ASSETS / f"ui-raid-overview-{label}.png"), full_page=label == "mobile")
        views = (("map", ".tile"), ("replay", ".rp-body .tile"), ("graph", ".heat-svg"), ("frames", ".frame"))
        for view, ready in (*views, ("achievements", ".ach")):
            page = p.open(f"/acme-shop?view={view}", storage={"rl.achievements": "true"})
            page.wait_for_selector(ready)
            page.evaluate("document.getElementById('soundBanner').hidden = true")
            page.wait_for_timeout(800)
            page.locator("#viewPane").screenshot(path=str(ASSETS / f"ui-raid-{view}-{label}.png"))


def test_limits_and_reputation_at_375px(browse):
    p = browse(width=375, height=800)
    page = p.page

    def overview(route):
        response = route.fetch()
        payload = response.json()
        payload["limits"] = {
            "providers": [
                {
                    "provider": "codex",
                    "plan": "prolite",
                    "status": "ok",
                    "observed_at": "2026-10-23T13:00:00Z",
                    "windows": [{"window": "weekly", "used_percent": 75, "resets_at": "2026-10-23T14:00:00Z"}],
                },
                {
                    "provider": "claude",
                    "status": "stale",
                    "windows": [{"window": "5h", "used_percent": 95, "resets_at": "2026-10-23T14:00:00Z"}],
                },
                {"provider": "gemini", "status": "not supported", "detail": "no local source"},
            ]
        }
        route.fulfill(response=response, json=payload)

    def board(route):
        response = route.fetch()
        payload = response.json()
        payload["agents"]["opus-checkout"].update(reputation=75, reputation_inputs={"verify_passed": 1})
        route.fulfill(response=response, json=payload)

    page.route("**/api/overview", overview)
    page.route("**/api/board/acme-shop", board)
    p.open("/acme-shop")
    page.wait_for_selector("#limits .limit")
    assert page.locator('#limits [data-provider="codex"]').get_attribute("class").startswith("limit warning")
    assert page.locator('#limits [data-provider="claude"]').get_attribute("class") == "limit critical"
    text = page.locator("#limits").inner_text()
    assert "Weekly 75%" in text and "resets" in text and "updated" in text
    assert "stale" not in text and "gemini" not in text.lower()
    assert "Gemini CLI: not supported" in page.locator("#providerInfo").text_content()
    page.locator("#agentsPanel > summary").click()
    badge = page.locator('.agent[data-agent="opus-checkout"] .reputation')
    assert badge.inner_text() == "Trust 75/100"
    assert "1 passed" in badge.get_attribute("title")
    assert no_hscroll(page)["scroll"] <= no_hscroll(page)["client"]
    assert p.errors == []


def test_browser_alerts_require_opt_in_and_follow_polled_changes(browse):
    p = browse()
    page = p.page
    page.add_init_script("""
      window.__notifications = [];
      window.__permissionRequests = 0;
      class FakeNotification {
        static permission = 'default';
        static async requestPermission() {
          window.__permissionRequests++; this.permission = 'granted'; return 'granted';
        }
        constructor(title, options) { window.__notifications.push({title, body: options.body}); }
      }
      window.Notification = FakeNotification;
      Object.defineProperty(document, 'hidden', {get: () => true});
    """)
    p.open("/weather-api")
    page.wait_for_selector("#doingList .row", state="attached")
    assert page.locator("#notify").get_attribute("aria-pressed") == "false"
    assert page.evaluate("window.__permissionRequests") == 0
    demo = p.server.demos["weather-api"]
    demo.note("cache-ttl", "gemini-cache", "Alerts remain off until explicitly enabled")
    page.wait_for_function(
        "() => document.querySelector('#toasts').textContent.includes('Alerts remain off')", timeout=10000
    )
    assert page.evaluate("window.__notifications.length") == 0
    page.locator("#settings summary").click()
    page.locator("#notify").click()
    assert page.locator("#notify").get_attribute("aria-pressed") == "true"
    assert page.evaluate("window.__permissionRequests") == 1
    demo.note("cache-ttl", "gemini-cache", "Notification integration probe")
    page.wait_for_function(
        "() => window.__notifications.some(n => n.body.includes('Notification integration probe'))", timeout=10000
    )
    page.locator("#notify").click()
    assert page.locator("#notify").get_attribute("aria-pressed") == "false"
    assert p.errors == []


CUE_PROBE = """
  window.__cues = [];
  window.__voices = 0;
  document.addEventListener('raidlock:cue', e => window.__cues.push(e.detail));
  const osc = BaseAudioContext.prototype.createOscillator;
  BaseAudioContext.prototype.createOscillator = function () { window.__voices++; return osc.call(this); };
"""
ALL_CUES = (
    "sent",
    "queued",
    "delivered",
    "read",
    "note",
    "claim",
    "review",
    "blocked",
    "done",
    "stalled",
    "expiring",
    "expired",
    "posted",
    "coin",
    "levelup",
)


@pytest.fixture
def cue_lab(chromium, tmp_path):
    import demo

    d = demo.Demo(tmp_path / "lab", "cue-lab", "repo")
    d.team("core", 5)
    for name in ("ana", "ben", "cal", "dee"):
        d.agent(name, "core", "claude-code", "claude-sonnet-5-5")
    for cid in ("c1", "c2", "c3", "c4"):
        d.card(cid, f"Cue card {cid}", "core", cid)
    d.claim("c2", "cal")
    d.claim("c3", "ben")
    running = start()
    pages = []

    def open_page(**kw):
        pg = Page(chromium, running, **kw)
        pages.append(pg)
        pg.page.add_init_script(CUE_PROBE)
        return pg

    yield d, open_page
    for pg in pages:
        pg.page.unroute_all(behavior="ignoreErrors")
        pg.close()
    running.srv.shutdown()
    running.srv.server_close()


def cue_counts(page):
    cues = page.evaluate("window.__cues")
    return {c: cues.count(c) for c in set(cues)}


def unlock_audio(page):
    page.locator("body").click(position={"x": 5, "y": 5})
    page.wait_for_function("() => document.getElementById('soundBanner').hidden")


def test_each_event_fires_its_cue_once(cue_lab):
    import time

    from raidlock.core import clock

    d, open_page = cue_lab
    p = open_page()
    page = p.page
    tweak = {}

    def board(route):
        headers = {k: v for k, v in route.request.headers.items() if k.lower() != "if-none-match"}
        payload = route.fetch(headers=headers).json()
        for card in payload["cards"]:
            if card["id"] == "c3" and card.get("lease") and "lease" in tweak:
                card["lease"].update(expires=tweak["lease"], live=tweak["lease"] > clock.iso())
        if "heartbeat" in tweak:
            payload["agents"]["ben"]["heartbeat"] = tweak["heartbeat"]
        route.fulfill(status=200, json=payload, headers={"Cache-Control": "no-cache"})

    page.route("**/api/board/cue-lab", board)
    p.open("/cue-lab")
    page.wait_for_selector(".row[data-id=c1]")
    unlock_audio(page)
    page.wait_for_timeout(3500)
    assert cue_counts(page) == {}
    expected = {}

    def step(*cues):
        for c in cues:
            expected[c] = expected.get(c, 0) + 1
        page.wait_for_function(
            "want => Object.entries(want).every(([k, n]) => window.__cues.filter(c => c === k).length >= n)",
            arg=expected,
            timeout=15000,
        )
        page.wait_for_timeout(400)
        assert cue_counts(page) == expected

    d.card("c5", "Cue card c5", "core", "c5")
    step("posted")
    d.claim("c1", "ana")
    d.msg("dee", "ana", "queued while ana works on c1")
    d.msg("ana", "dee", "dee is idle so this is ready")
    step("claim", "queued", "sent")
    d.note("c1", "ana", "halfway there")
    d.board.inbox(d.callers["dee"])
    step("note", "read")
    d.move("c1", "ana", "review")
    step("review", "delivered")
    d.move("c2", "cal", "blocked")
    step("blocked")
    d.done("c1")
    step("done", "levelup")
    d.cost("c3", "ben", 1000, 0, 500)
    step("coin")
    tweak["lease"] = clock.later(120)
    step("expiring")
    tweak["heartbeat"] = clock.later(-3 * 3600)
    step("stalled")
    tweak["lease"] = clock.later(-60)
    step("expired")
    time.sleep(3.5)
    assert cue_counts(page) == expected
    assert set(expected) == set(ALL_CUES)
    assert page.evaluate("window.__voices") > 0
    assert p.errors == []


def test_mute_silences_every_cue(cue_lab):
    d, open_page = cue_lab
    p = open_page()
    page = p.page
    p.open("/cue-lab")
    page.evaluate("localStorage.setItem('rl.sound', 'false')")
    page.reload()
    page.wait_for_selector(".row[data-id=c1]")
    page.locator("body").click(position={"x": 5, "y": 5})
    assert page.locator("#soundBanner").is_hidden()
    page.locator("#soundMenu summary").click()
    page.locator("[data-try=done]").click()
    page.locator("#soundMenu summary").click()
    page.wait_for_timeout(3500)
    d.claim("c1", "ana")
    d.msg("dee", "ana", "muted message")
    d.note("c3", "ben", "muted note probe")
    d.move("c2", "cal", "blocked")
    d.card("c6", "Muted new card", "core", "c6")
    page.wait_for_function(
        "() => document.querySelector('#toasts').textContent.includes('muted note probe')", timeout=15000
    )
    page.wait_for_timeout(3500)
    assert page.evaluate("window.__cues") == []
    assert page.evaluate("window.__voices") == 0
    assert p.errors == []


def test_sound_settings_persist_and_gate_each_cue(cue_lab):
    d, open_page = cue_lab
    p = open_page()
    page = p.page
    p.open("/cue-lab")
    page.wait_for_selector(".row[data-id=c1]")
    unlock_audio(page)
    page.locator("#soundMenu summary").click()
    assert page.locator("#cueList input[type=checkbox]").count() == len(ALL_CUES)
    assert page.locator("#cueList input:checked").count() == len(ALL_CUES)
    page.locator("#cue-note").uncheck()
    page.locator("#vol").fill("35")
    page.reload()
    page.wait_for_selector(".row[data-id=c1]")
    unlock_audio(page)
    page.locator("#soundMenu summary").click()
    assert page.locator("#vol").input_value() == "35"
    assert not page.locator("#cue-note").is_checked()
    assert page.locator("#cue-claim").is_checked()
    page.locator("#soundMenu summary").click()
    page.wait_for_timeout(3500)
    d.note("c3", "ben", "gated note probe")
    d.claim("c1", "ana")
    page.wait_for_function("() => window.__cues.includes('claim')", timeout=15000)
    page.wait_for_function(
        "() => document.querySelector('#toasts').textContent.includes('gated note probe')", timeout=15000
    )
    page.wait_for_timeout(500)
    assert "note" not in page.evaluate("window.__cues")
    assert p.errors == []


def test_csp_violations_are_reported(browse):
    p = browse()
    page = p.open("/")
    page.wait_for_selector(".proj")
    assert p.errors == []
    page.evaluate(
        "() => { const i = document.createElement('img'); i.src = 'https://example.invalid/x.png'; "
        "document.body.append(i); }"
    )
    page.wait_for_function("() => true")
    page.wait_for_timeout(300)
    assert any(e.startswith("csp violation: img-src") for e in p.errors), p.errors


def test_axe_free_markup_basics(browse):
    p = browse()
    page = p.open("/acme-shop", panels=("logPanel", "agentsPanel"))
    page.wait_for_selector("#log .lrow")
    assert page.locator("[role=listitem]").count() == 0
    details = page.locator(".row button.details")
    assert details.count() == page.locator(".row:not(.one)").count()
    assert all(b.get_attribute("aria-controls") for b in details.all())
    assert page.locator("#chat").get_attribute("tabindex") == "0"
    for heading in page.locator("h2, h3").all():
        assert heading.evaluate("e => e.closest('main, header, nav') !== null")
    assert p.errors == []


def test_a_slain_boss_brings_the_victory_screen_and_fanfare(cue_lab):
    d, open_page = cue_lab
    p = open_page()
    page = p.page
    slain = []

    def board(route):
        headers = {k: v for k, v in route.request.headers.items() if k.lower() != "if-none-match"}
        payload = route.fetch(headers=headers).json()
        payload["bosses_slain"] = list(slain)
        route.fulfill(status=200, json=payload, headers={"Cache-Control": "no-cache"})

    page.route("**/api/board/cue-lab", board)
    p.open("/cue-lab")
    page.wait_for_selector(".row[data-id=c1]")
    unlock_audio(page)
    page.wait_for_timeout(3500)
    assert page.locator("#victory").count() == 0
    slain.append({"id": "c1", "title": "The Lich of Legacy", "total": 4, "final": "ana", "slain_at": ""})
    page.wait_for_selector("#victory", timeout=15000)
    text = page.locator("#victory").inner_text()
    assert "boss slain" in text.lower() and "The Lich of Legacy" in text and "final blow by ana" in text
    assert cue_counts(page).get("levelup") == 1
    page.keyboard.press("Escape")
    page.wait_for_function("() => !document.getElementById('victory')")
    page.wait_for_timeout(3500)
    assert cue_counts(page).get("levelup") == 1
    assert p.errors == []


def test_re_render_keeps_keyboard_focus(browse):
    p = browse()
    page = p.open("/weather-api")
    page.wait_for_selector("#doingList .row[data-id='cache-ttl'] button.details")
    page.locator("#doingList .row[data-id='cache-ttl'] button.details").focus()
    old = page.evaluate_handle("document.activeElement")
    p.server.demos["weather-api"].note("cache-ttl", "gemini-cache", "Focus survives this re-render")
    page.wait_for_function("old => !old.isConnected", arg=old, timeout=10000)
    assert page.evaluate("document.activeElement.closest('.row')?.dataset.id") == "cache-ttl"
    assert page.evaluate("document.activeElement.matches('button.details')")
    assert p.errors == []
