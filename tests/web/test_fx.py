import pytest
from browser import no_hscroll, running_animations, settle
from fxfeed import EVENTS, FX_LOG, POLL_MS, Feed, block_card, close_card, record_cost, refuse_lock, sign_receipt

pytest.importorskip("playwright.sync_api")

ALL = (lambda f: close_card(f, "refunds"), sign_receipt, refuse_lock, lambda f: block_card(f, "cart-api"), record_cost)


def fx_page(browse, server, path, theme="raid", panels=(), storage=None, width=1280, **ctx):
    p = browse(width=width, height=900, **ctx)
    p.page.add_init_script(FX_LOG)
    feed = Feed(p.page, server)
    store = {"rl.achievements": "true", **(storage or {})}
    page = p.open(path, theme=theme, panels=panels, storage=store)
    settle(page, "#board:not([hidden])", ms=POLL_MS + 400)
    return p, page, feed


def fired(page, kind=None):
    return page.evaluate("k => window.__fx.filter(e => !k || e.kind === k).length", kind)


@pytest.mark.parametrize("kind", list(EVENTS))
def test_event_fires_once_and_not_on_repeat_poll(browse, server, kind):
    path, change, selector, panels = EVENTS[kind]
    p, page, feed = fx_page(browse, server, path, panels=panels)
    assert fired(page) == 0
    for panel in panels:
        page.locator(f"#{panel}").evaluate("e => e.scrollIntoView({block: 'start'})")
    change(feed)
    page.wait_for_selector(selector, state="attached", timeout=POLL_MS * 3)
    page.wait_for_function("k => window.__fx.some(e => e.kind === k)", arg=kind, timeout=POLL_MS * 3)
    assert fired(page, kind) == 1
    page.wait_for_timeout(POLL_MS * 2 + 400)
    assert fired(page, kind) == 1
    assert p.errors == []


def test_blocked_pulse_stops_when_resolved(browse, server):
    p, page, feed = fx_page(browse, server, "/acme-shop?view=all")
    block_card(feed)
    page.wait_for_selector('.row[data-id="sitemap"].fx-pulse', timeout=POLL_MS * 3)
    feed.card("sitemap")["lane"] = "doing"
    page.wait_for_selector('.row[data-id="sitemap"]:not(.fx-pulse)', timeout=POLL_MS * 3)
    assert page.locator(".fx-pulse").count() == 0
    assert fired(page, "blocked") == 1


def test_failed_verify_interrupts_the_cast(browse, server):
    p, page, feed = fx_page(browse, server, "/acme-shop")
    c = feed.card("sitemap")
    c["notes"] = [*c["notes"], {"ts": "", "by": "raid-lead", "text": "verify failed (exit 1)"}]
    c["notes_count"] += 1
    cut = '.row[data-id="sitemap"] .fx-verify.fail .fx-cast.cut'
    page.wait_for_selector(cut, state="attached", timeout=POLL_MS * 3)
    assert page.locator(".fx-stamp").count() == 0
    events = page.evaluate("window.__fx")
    assert [e["passed"] for e in events if e["kind"] == "verify"] == [False]


def drive_all(feed, page):
    for change in ALL:
        change(feed)
    page.wait_for_timeout(POLL_MS + 800)


def assert_still(p, page):
    assert fired(page) == 0
    assert page.locator(".fx-layer, .fx-pulse").count() == 0
    assert running_animations(page) == 0
    assert p.errors == []


def test_plain_theme_has_no_animations(browse, server):
    p, page, feed = fx_page(browse, server, "/acme-shop?view=all", theme="plain")
    drive_all(feed, page)
    assert page.locator(".toast").count() > 0
    assert page.locator(".toast.quest").count() == 0
    assert_still(p, page)


def test_reduced_motion_disables_every_effect(browse, server):
    p, page, feed = fx_page(browse, server, "/acme-shop?view=all", reduced_motion="reduce")
    drive_all(feed, page)
    assert_still(p, page)


def test_settings_toggle_disables_every_effect_and_is_remembered(browse, server):
    p, page, feed = fx_page(browse, server, "/acme-shop?view=all")
    page.locator("#settings > summary").click()
    btn = page.locator("#animBtn")
    assert btn.inner_text() == "Animations: on"
    btn.click()
    assert btn.inner_text() == "Animations: off" and btn.get_attribute("aria-pressed") == "false"
    assert page.evaluate("document.documentElement.dataset.anim") == "off"
    assert page.evaluate("localStorage.getItem('rl.anim')") == "false"
    page.locator("#settings > summary").click()
    drive_all(feed, page)
    assert page.locator(".toast.quest").count() == 1
    assert_still(p, page)
    page.reload()
    settle(page, "#board:not([hidden])")
    assert page.evaluate("document.documentElement.dataset.anim") == "off"


def test_effects_fit_at_375px(browse, server):
    p, page, feed = fx_page(browse, server, "/acme-shop", width=375, is_mobile=True, has_touch=True)
    drive_all(feed, page)
    page.wait_for_function("() => window.__fx.length >= 4")
    r = no_hscroll(page)
    assert r["scroll"] <= r["client"], r
    assert r["wide"] == [], r
    assert p.errors == []


def test_diff_functions(browse, server):
    p, page, _ = fx_page(browse, server, "/acme-shop")
    out = page.evaluate(
        """() => {
      const F = window.RaidlockFx;
      const card = (o) => ({id: "a", lane: "doing", owner: "w", lease: {expires: "2999-01-01T00:00:00Z"},
        notes_count: 1,
        notes: [{text: "claimed"}], cost: {usd_equiv: 0.1, tokens: 100}, ...o});
      const snap = (c, claim, agents) => F.snap({cards: [card(c)], claimable: claim || {}, agents: agents || {}});
      const kinds = (a, b) => F.diff(a, b).map(e => e.kind).sort().join(",");
      const base = snap({});
      const overlap = n => ({a: {reasons: ["x overlaps y held by z on card q in workspace w until " + n]}});
      const passed = {text: "verify passed (exit 0)"};
      const frame = (pct, level) => F.frames({agents: [{agent: "w", context: {pct, level}}]});
      const ach = [{id: "a", earned: true}, {id: "b", earned: true}, {id: "c", earned: false}];
      return {
        same: kinds(base, snap({})),
        none: kinds(null, base),
        done: kinds(base, snap({lane: "done", lease: null})),
        verify: kinds(base, snap({notes_count: 2, notes: [{text: "claimed"}, passed]})),
        verifyOld: kinds(snap({notes_count: 2, notes: [passed]}), snap({notes_count: 2, notes: [passed]})),
        claim: kinds(snap({owner: "", lease: null, lane: "todo"}), base),
        renew: kinds(base, snap({lease: {expires: "2999-02-01T00:00:00Z"}})),
        refused: kinds(snap({}, overlap(1)), snap({}, overlap(2))),
        refusedNew: kinds(snap({}, {}), snap({}, overlap(1))),
        cost: F.diff(base, snap({cost: {usd_equiv: 0.22, tokens: 150}})).map(e => [e.kind, Math.round(e.usd * 100)]),
        stalled: kinds(snap({}, {}, {w: {status: "working"}}), snap({}, {}, {w: {status: "stalled"}})),
        pulses: [...F.pulses(new Map([["card:a", "blocked"]]), [], snap({lane: "doing"})).keys()],
        kept: [...F.pulses(new Map(), [{kind: "blocked", card: "a"}], snap({lane: "blocked"})).keys()],
        ctx: F.contextDiff(frame(30, "ok"), frame(80, "warning")),
        ctxSame: F.contextDiff(frame(30, "ok"), frame(30.2, "ok")).length,
        earned: F.newEarned(["a"], F.earned({achievements: ach})),
        earnedFirst: F.newEarned(null, ["a"]),
      };
    }"""
    )
    assert out["same"] == "" and out["none"] == ""
    assert out["done"] == "done"
    assert out["verify"] == "verify" and out["verifyOld"] == ""
    assert out["claim"] == "claim" and out["renew"] == ""
    assert out["refused"] == "" and out["refusedNew"] == "refused"
    assert out["cost"] == [["cost", 12]]
    assert out["stalled"] == "stalled"
    assert out["pulses"] == [] and out["kept"] == ["card:a"]
    assert out["ctx"] == [{"kind": "context", "agent": "w", "from": 30, "to": 80, "level": "warning", "was": "ok"}]
    assert out["ctxSame"] == 0
    assert out["earned"] == ["b"] and out["earnedFirst"] == []
