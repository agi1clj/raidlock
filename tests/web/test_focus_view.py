import re

import pytest
from browser import Page, no_hscroll
from serving import isolate_env, start

pytest.importorskip("playwright.sync_api")

UNKNOWN = ("unknown model", "unknown runtime", "Billing ?", "Trust 50/100", "$0.00", "API-equiv", "stale", "unknown")
CONTRAST_JS = """() => {
  const lum = c => {
    const v = c.match(/[\\d.]+/g).slice(0, 3).map(Number).map(x => x / 255)
      .map(x => x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4));
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  };
  const ratio = (a, b) => {
    const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m);
    return (x + 0.05) / (y + 0.05);
  };
  const bg = el => {
    for (let e = el; e; e = e.parentElement) {
      const c = getComputedStyle(e).backgroundColor;
      if (c && !c.endsWith(', 0)') && c !== 'transparent') return c;
    }
    return getComputedStyle(document.body).backgroundColor;
  };
  const picks = ['#needsList button.primary', '#needsList .row-title .t', '#needsList .reason', '#wsStatus',
    '#todoList .pill.claim'];
  return picks.map(s => document.querySelector(s)).filter(Boolean).map(e => ratio(getComputedStyle(e).color, bg(e)));
}"""


PANELS = ("agentsPanel", "logPanel")


@pytest.fixture(scope="module")
def fleet(tmp_path_factory):
    import demo

    root = tmp_path_factory.mktemp("fleet")
    mp = pytest.MonkeyPatch()
    isolate_env(mp, root)
    from raidlock.core import lead

    table = lead.process_table()
    mp.setattr(lead, "process_table", lambda: table)
    demo.fleet_board(root / "ws")
    demo.empty_board(root / "ws")
    running = start()
    yield running
    running.srv.shutdown()
    running.srv.server_close()
    mp.undo()


@pytest.fixture
def view(chromium, fleet):
    pages = []

    def make(path="/orbit-ops", width=1280, panels=(), **ctx):
        p = Page(chromium, fleet, width=width, height=900, **ctx)
        pages.append(p)
        page = p.open(path, panels=panels)
        page.wait_for_selector("#focus:not([hidden]) #needsList > li", state="attached")
        page.evaluate("document.getElementById('soundBanner').hidden = true")
        return p, page

    yield make
    for p in pages:
        p.close()


def expand_everything(page):
    if page.locator("#needsMore").is_visible():
        page.locator("#needsMore").click()
    page.evaluate(
        "() => document.querySelectorAll('#focus details, #panels details').forEach(d => {"
        " if (!d.closest('[hidden]')) d.open = true; })"
    )
    page.wait_for_timeout(500)
    page.evaluate(
        "() => document.querySelectorAll('details').forEach(d => { if (!d.closest('[hidden]')) d.open = true; })"
    )
    page.wait_for_timeout(300)


def visible_text(page):
    return page.evaluate(
        "() => document.body.innerText + '\\n' + [...document.querySelectorAll('[title],[aria-label]')]"
        ".filter(e => e.offsetParent !== null).map(e => (e.getAttribute('title') || '') + ' ' +"
        " (e.getAttribute('aria-label') || '')).join('\\n')"
    )


def test_focus_view_is_the_default(view):
    p, page = view()
    assert page.url.endswith("/orbit-ops")
    assert page.locator("#viewTabs a[aria-current=page]").inner_text() == "War room"
    assert page.locator("#lanes").is_hidden() and page.locator("#viewPane").is_hidden()
    needs = page.locator("#needsList > li.row")
    assert needs.count() == 5
    assert page.locator("#needsMore").inner_text().startswith("Unroll all ")
    assert "to close" in page.locator("#needsSub").inner_text()
    page.locator("#needsMore").click()
    page.wait_for_function("() => document.querySelectorAll('#needsList > li.row').length >= 14")
    assert needs.count() >= 14
    assert needs.locator("button.primary").count() == needs.count()
    assert page.locator("#needsList .row[data-id=blk-billing] .reason").text_content().startswith("lead: pick one")
    assert page.locator("#needsList .row[data-id=blk-secrets]").count() == 0
    assert page.locator("#waitingNote").inner_text().startswith("4 cursed quests wait on others")
    assert page.locator("#needsList .row[data-lane=review]:not([data-kind=question])").count() == 10
    assert page.locator("#focus > details:not([hidden])").count() == 1
    doing = page.locator("#doingList > li.row")
    assert doing.count() == 6
    for row in doing.all():
        assert row.locator(".pill.ow").count() == 1 and row.locator(".pill.lock").count() == 1
    assert page.locator("#todoList > li.row").count() == 5
    assert page.locator("#todoMore").inner_text() == "Unroll all 12 quests"
    page.locator("#todoMore").click()
    page.wait_for_function("() => document.querySelectorAll('#todoList > li.row').length === 12")
    for panel in PANELS:
        assert not page.evaluate(f"document.getElementById('{panel}').open")
    assert p.errors == []


def test_decision_has_primary_and_recovery_commands(view):
    p, page = view()
    page.locator("#needsMore").click()
    row = page.locator("#needsList .row[data-id=blk-billing]")
    row.wait_for()
    assert row.locator("button.primary").count() == 1
    body = row.locator(".more-body")
    assert body.is_hidden()
    row.locator(".more-menu > summary").click()
    body.locator(".cmd").first.wait_for()
    text = body.inner_text()
    assert "resume blk-billing" in text and "resurrect blk-billing" in text and "--take-over" in text
    page.keyboard.press("Escape")
    assert body.is_hidden()
    assert p.errors == []


def test_merge_never_labels_a_close(view):
    p, page = view()
    expand_everything(page)
    labels = page.evaluate(
        "() => [...document.querySelectorAll('button, summary, a, [role=button]')]"
        ".map(e => (e.textContent + ' ' + (e.getAttribute('aria-label') || '')).trim())"
    )
    assert not [x for x in labels if re.search(r"merge", x, re.I)]
    primaries = set(page.locator("button.primary").all_inner_texts())
    assert primaries and primaries <= {
        "Turn in quest",
        "Approve",
        "Apply enchant",
        "Hold council",
        "Inspect loot",
        "Inspect",
        "Reply",
    }
    assert p.errors == []


def test_close_copies_the_done_command(view, fleet):
    p, page = view(permissions=["clipboard-read", "clipboard-write"])
    button = page.locator("#needsList .row[data-id=rev-billing] button.primary")
    button.click()
    page.wait_for_function("() => document.querySelector('#toasts').textContent.includes('Copied')")
    copied = page.evaluate("navigator.clipboard.readText()")
    assert re.fullmatch(r"raid --lead-secret \S+ done rev-billing", copied), copied
    assert "~raid" not in copied
    assert p.errors == []


def test_keyboard_reaches_the_primary_action(view):
    p, page = view()
    for _ in range(40):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.matches('#needsList button.primary')"):
            break
    else:
        pytest.fail("the first Close button is not reachable with Tab")
    assert page.evaluate("document.activeElement.closest('.row').dataset.id").startswith("rev-")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.matches('button.details')")
    page.keyboard.press("Enter")
    assert page.locator("#cardDrawer").is_visible()
    assert page.evaluate("document.activeElement.id") == "drawerClose"
    assert p.errors == []


def test_raid_wording_leads(view):
    p, page = view()
    assert "The raid leader's call" in page.locator("#needsTitle").inner_text()
    assert "Quest board" in page.locator("#todoTitle").inner_text()
    assert page.locator("#todoTitle .raid-sub").inner_text().lower() == "to do"
    assert page.locator("#costTitle .raid-sub").text_content() == "Cost"
    assert page.locator("#needsList button.primary").first.inner_text() == "Turn in quest"
    assert page.locator("#viewTabs a[aria-current=page]").inner_text() == "War room"
    assert "Guild level" in page.locator("#hudLevel").inner_text()
    assert page.locator("#brandText").inner_text() == "raidlock"
    assert p.errors == []


def test_unknown_fields_are_hidden(view):
    p, page = view()
    expand_everything(page)
    page.wait_for_selector(".agent")
    text = visible_text(page)
    found = [w for w in UNKNOWN if w in text]
    assert found == [], found
    stripped = page.locator(".agent[data-agent=worker-network-00]")
    assert stripped.count() == 1 and stripped.locator(".who").count() == 0
    assert stripped.locator(".bill").count() == 0
    for badge in page.locator(".reputation").all():
        assert badge.inner_text() != "Trust 50/100" or "0 passed, 0 failed; reviews: 0" not in badge.get_attribute(
            "title"
        )
    assert p.errors == []


def test_idle_agents_are_collapsed_and_active_sorted(view):
    p, page = view(panels=("agentsPanel",))
    page.wait_for_selector(".agent")
    hint = page.locator("#agentsHint").inner_text()
    counts = re.fullmatch(r"(\d+) working, (\d+) recently active", hint)
    assert counts and sum(map(int, counts.groups())) == 8
    fold = page.locator("#idleFold")
    assert fold.locator("h3").inner_text() == "59 not seen for 2 hours"
    assert page.locator("#idleFold .chip").first.is_visible()
    states = page.locator(".agents .agent").evaluate_all(
        "els => els.map(e => [...e.classList].find(c => c.startsWith('st-')))"
    )
    order = ["st-blocked", "st-stalled", "st-working", "st-quiet", "st-review", "st-idle"]
    ranks = [order.index(s) for s in states]
    assert ranks == sorted(ranks), states
    assert states[0] in ("st-blocked", "st-stalled")
    assert page.locator("#idleFold .chip").count() == 8
    page.locator("#idleMore").click()
    page.wait_for_function("() => document.querySelectorAll('#idleFold .chip').length === 59")
    assert p.errors == []


def test_takeover_only_on_an_expired_worker_lease(view):
    p, page = view()
    pills = page.locator("#doingList .pill.takeover")
    assert pills.count() == 1
    assert pills.first.evaluate("e => e.closest('.row').dataset.id") == "wip-expired"
    assert page.locator(".row[data-id=lead-own] .pill.takeover").count() == 0
    page.goto(p.server.url + "/orbit-ops?view=all")
    page.wait_for_selector("#lanes .row")
    assert page.locator(".lane[data-lane=blocked] .pill.takeover").count() == 0
    assert page.locator(".lane[data-lane=blocked] .pill.claim").count() == 0
    assert p.errors == []


def test_375px_has_no_horizontal_scroll(view):
    p, page = view(width=375, is_mobile=True, has_touch=True)
    expand_everything(page)
    page.locator("#needsList .more-menu > summary").first.click()
    page.wait_for_timeout(200)
    r = no_hscroll(page)
    assert r["scroll"] <= r["client"], r
    assert r["wide"] == [], r
    assert p.errors == []


def test_contrast_of_primary_text(view):
    p, page = view()
    ratios = page.evaluate(CONTRAST_JS)
    assert ratios and min(ratios) >= 4.5, ratios
    assert p.errors == []


def test_empty_board_says_what_to_do(view):
    p, page = view()
    page.goto(p.server.url + "/fresh-start")
    page.wait_for_selector("#todoList .empty", state="attached")
    assert page.evaluate("() => ['needs', 'todo', 'doing'].every(id => document.getElementById(id).open)")
    assert page.locator("#needs").inner_text().strip() == "All is quiet in the realm"
    assert "raid claim" in page.locator("#doingList").inner_text()
    todo = page.locator("#todoList").inner_text()
    assert "raid --lead-secret" in todo and " new " in todo
    expand_everything(page)
    assert (
        "raid up --join" in page.locator("#agents").inner_text() or "No agent" in page.locator("#agents").inner_text()
    )
    assert "No usage recorded yet" in page.locator("#treasury").inner_text()
    assert "No legends yet" in page.locator("#doneList").inner_text()
    text = visible_text(page)
    assert not [w for w in UNKNOWN if w in text]
    assert no_hscroll(page)["scroll"] <= no_hscroll(page)["client"]
    assert p.errors == []


def test_offline_server_says_how_to_recover(view):
    p, page = view()
    page.route("**/api/**", lambda route: route.abort())
    page.wait_for_function("() => document.querySelector('#crumbs').textContent.includes('raid ui')", timeout=10000)
    assert page.locator("#crumbs [role=alert]").count() == 1
    page.unroute("**/api/**")
    page.wait_for_function("() => document.querySelector('#crumbs [aria-current]')", timeout=20000)


def test_limits_strip_shows_only_real_providers(view):
    p, page = view()
    assert page.locator("#limits").is_hidden()
    assert "Codex: unavailable" in page.locator("#providerInfo").text_content()
    assert "Gemini CLI: not supported" in page.locator("#providerInfo").text_content()


def test_limits_strip_renders_context_and_plan_usage(chromium, fleet):
    p = Page(chromium, fleet, width=375, height=900)

    def overview(route):
        response = route.fetch()
        payload = response.json()
        payload["limits"] = {
            "providers": [
                {
                    "provider": "claude",
                    "status": "ok",
                    "observed_at": "2026-10-23T13:00:00Z",
                    "context": {
                        "used_percent": 23.4,
                        "used_tokens": 46800,
                        "window_tokens": 200000,
                        "assumed_window": False,
                    },
                    "session": {"cost_usd": 1.25},
                    "windows": [
                        {"window": "5h", "used_percent": 30, "resets_at": "2026-10-23T18:00:00Z"},
                        {"window": "weekly", "used_percent": 55, "resets_at": "2026-10-27T10:00:00Z"},
                    ],
                },
                {"provider": "codex", "status": "unavailable", "detail": "no Codex sessions found"},
                {"provider": "gemini", "status": "not supported"},
            ]
        }
        route.fulfill(response=response, json=payload)

    p.page.route("**/api/overview", overview)
    page = p.open("/orbit-ops")
    page.wait_for_selector("#limits .limit")
    text = page.locator("#limits").inner_text()
    for want in ("Claude Code", "Mana 23%", "5h 30%", "Weekly 55%", "resets", "Session $1.25", "updated"):
        assert want in text, (want, text)
    assert page.locator("#limits .limit").count() == 1
    assert "stale" not in text and "not exposed" not in text and "not supported" not in text
    r = no_hscroll(page)
    assert r["scroll"] <= r["client"], r
    assert p.errors == []
    p.close()


@pytest.mark.parametrize("width", [1280, 375])
def test_waiting_times_are_readable(view, width):
    p, page = view(width=width)
    page.clock.install(time="2026-10-25T11:00:00Z")

    def board(route):
        response = route.fetch()
        data = response.json()
        card = next(c for c in data["cards"] if c["lane"] == "todo")
        data["claimable"][card["id"]] = {
            "ok": False,
            "reasons": [
                "apps/cart overlaps apps held by opus-checkout on card cart-api "
                "in workspace orbit-ops until 2026-10-25T11:38:00Z"
            ],
        }
        route.fulfill(json=data)

    page.route("**/api/board/orbit-ops", board)
    page.goto(p.server.url + "/orbit-ops?view=all")
    pill = page.locator(".noclaim").filter(has_text="Waiting for cart-api")
    pill.wait_for()
    assert "lock frees in 38 min" in pill.inner_text()
    assert "apps/cart overlaps apps" in pill.get_attribute("title")
    assert "2026-10-25T11:38:00Z" in pill.get_attribute("title")
    pill.locator("xpath=ancestor::li").locator("button.details").click()
    assert page.locator("time[datetime='2026-10-25T11:38:00Z']").count() > 0
    assert "2026-10-25T11:38:00Z" not in page.locator("#lanes").inner_text()
    assert no_hscroll(page)["scroll"] <= width
    assert p.errors == []


def test_agents_use_two_hour_activity_window_and_live_leases(view):
    p, page = view(panels=("agentsPanel",))
    page.clock.install(time="2026-10-25T11:00:00Z")

    def board(route):
        response = route.fetch()
        data = response.json()
        data["agents"] = {
            "holder": {"card": "held", "last_seen": "2026-10-24T00:00:00Z"},
            "lease-only": {"last_seen": "2026-10-24T00:00:00Z"},
            "recent": {"last_seen": "2026-10-25T09:01:00Z"},
            "boundary": {"last_seen": "2026-10-25T09:00:00Z"},
            "stale-status": {"status": "working", "last_seen": "2026-10-25T08:00:00Z"},
            "expired": {"last_seen": "2026-10-24T00:00:00Z"},
        }
        for c in data["cards"]:
            c["owner"] = ""
            c["lease"] = None
        data["cards"][0]["lease"] = {
            "agent": "lease-only",
            "expires": "2026-10-25T12:00:00Z",
            "live": True,
        }
        data["cards"][1]["lease"] = {
            "agent": "expired",
            "expires": "2026-10-25T10:00:00Z",
            "live": False,
        }
        route.fulfill(json=data)

    page.route("**/api/board/orbit-ops", board)
    page.reload()
    page.wait_for_function("() => document.querySelector('#agentsHint').textContent === '2 working, 1 recently active'")
    assert page.locator("#idleFold .chip").count() == 3
    assert page.locator(".agents .agent").count() == 3
    assert p.errors == []


@pytest.mark.parametrize("width", [1280, 375])
@pytest.mark.parametrize("sub", ["focus", "all", "map", "replay", "graph", "frames", "cost", "activity"])
def test_views_fit_at_both_widths(view, width, sub):
    p, page = view(width=width)
    if sub in ("cost", "activity"):
        panel = "agentsPanel" if sub == "cost" else "logPanel"
        page.locator(f"#{panel} > summary").click()
    else:
        page.goto(p.server.url + f"/orbit-ops?view={sub}")
        selector = {
            "focus": "#focus:not([hidden])",
            "all": "#lanes:not([hidden])",
        }.get(sub, "#viewPane .view-head")
        page.wait_for_selector(selector)
    page.wait_for_timeout(300)
    result = no_hscroll(page)
    assert result["scroll"] <= result["client"], result
    assert result["wide"] == [], result
    assert p.errors == []


def test_guild_level_up_shows_a_toast(view):
    p, page = view()
    bump = {"done": 0}

    def board(route):
        data = route.fetch().json()
        data["counts"] = dict(data["counts"], done=data["counts"].get("done", 0) + bump["done"])
        route.fulfill(json=data)

    page.route("**/api/board/orbit-ops", board)
    page.reload()
    page.wait_for_selector("#wsName")
    page.wait_for_timeout(3500)
    assert page.locator("#toasts .toast.levelup").count() == 0
    bump["done"] = 500
    page.locator("#toasts .toast.levelup").first.wait_for(timeout=15000)
    assert "Guild level" in page.locator("#toasts .toast.levelup").first.inner_text()
    assert p.errors == []
