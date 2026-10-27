import json

import pytest
from browser import Page, assert_fits, call_cli, open_view, running_animations
from serving import start

from raidlock.core import clock
from raidlock.web.raidmap import territory


def body(resp):
    return json.loads(resp[2])


def paths_of(result):
    return {e["path"]: e for repo in result["repos"] for e in repo["paths"]}


def test_api_shows_holders_and_contested_paths(server):
    status, headers, raw = server.get("/api/raidmap/acme-shop")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    d = json.loads(raw)
    p = paths_of(d)
    assert p["storefront/api/cart"]["holder"] == "opus-checkout"
    assert p["storefront/api/cart"]["card"] == "cart-api"
    assert p["storefront/api/cart"]["violations"] == 1
    assert p["storefront/api/cart"]["denials"] == 1
    assert p["storefront/api/cart"]["contested"] is True
    assert p["storefront/api/cart/rounding"]["waiting"] == ["cart-rounding"]
    assert p["storefront/api/cart/rounding"]["holder"] == ""
    assert p["web/jobs/sitemap"]["contested"] is False
    assert p["admin/refunds"]["lane"] == "review"
    assert {r["name"] for r in d["repos"]} == {"admin", "storefront", "web"}
    assert d["contested"] == 2 and d["held"] == 4
    assert d["events"] and all(not x.startswith("/") for ev in d["events"] for x in ev["paths"])
    assert server.get("/api/raidmap/acme-shop", headers={"If-None-Match": headers["ETag"]})[0] == 304


def test_api_rejects_bad_input(server):
    assert server.get("/api/raidmap/acme-shop?at=x")[0] == 404
    assert server.get("/api/raidmap/acme-shop?at=-1")[0] == 404
    assert server.get("/api/raidmap/no-such")[0] == 404
    assert server.get("/api/raidmap/acme-shop/extra")[0] == 404


def test_history_point_uses_replay(server):
    first = body(server.get("/api/replay/acme-shop?at=1&marks=0"))["at"]
    early = body(server.get(f"/api/raidmap/acme-shop?at={first}"))
    assert early["held"] == 0 and early["at"] == first


def test_expired_doing_lease_is_not_held(tmp_path):
    import demo

    d = demo.Demo(tmp_path / "x", "expiry", "repo")
    d.team("core", 3)
    d.agent("ana", "core", "claude-code", "claude-sonnet-5-5")
    d.card("one", "One", "core", "src")
    d.claim("one", "ana")
    assert paths_of(territory(d.board))["repo/src"]["holder"] == "ana"
    clock.travel(3 * 3600)
    assert paths_of(territory(d.board))["repo/src"]["holder"] == ""


def test_cli_map(tmp_path, capsys):
    import demo

    d = demo.acme_shop(tmp_path / "cli")
    code, out = call_cli(capsys, d.board, "map")
    assert code == 0
    assert paths_of(out)["storefront/api/cart"]["holder"] == "opus-checkout"
    assert paths_of(out)["storefront/api/cart"]["contested"] is True


@pytest.mark.parametrize("theme", ["raid", "plain"])
@pytest.mark.parametrize("width", [1280, 375])
def test_map_view_renders_in_both_themes(browse, theme, width):
    p, page = open_view(browse, "/acme-shop?view=map", ".tile", theme=theme, width=width)
    cart = page.locator('.tile[data-path="storefront/api/cart"]')
    assert cart.get_attribute("data-holder") == "opus-checkout"
    assert "contested" in cart.get_attribute("class").split()
    assert any(c.startswith("ag-") for c in cart.get_attribute("class").split())
    assert "free" in page.locator('.tile[data-path="storefront/api/promo"]').get_attribute("class").split()
    assert page.locator("#viewTabs a[aria-current=page]").get_attribute("data-sub") == "map"
    assert page.locator("#focus").is_hidden()
    assert_fits(p, page)


def test_map_is_live_and_links_to_replay(chromium, tmp_path):
    import demo

    d = demo.Demo(tmp_path / "live", "live-map", "repo")
    d.team("core", 3)
    d.agent("ana", "core", "claude-code", "claude-sonnet-5-5")
    d.card("one", "One", "core", "src")
    running = start()
    try:
        p = Page(chromium, running)
        page = p.open("/live-map?view=map")
        page.wait_for_selector('.tile[data-path="repo/src"]')
        assert page.locator('.tile[data-path="repo/src"]').get_attribute("data-holder") == ""
        d.claim("one", "ana")
        page.wait_for_function(
            "() => document.querySelector('.tile[data-path=\"repo/src\"]').dataset.holder === 'ana'", timeout=15000
        )
        page.locator("[data-go=replay]").click()
        page.wait_for_url("**/live-map?view=replay")
        page.wait_for_selector(".rp-lane")
        page.go_back()
        page.wait_for_selector('.tile[data-path="repo/src"]')
        assert p.errors == []
        p.close()
    finally:
        running.srv.shutdown()
        running.srv.server_close()


def test_map_respects_reduced_motion(browse):
    p, page = open_view(browse, "/acme-shop?view=map", ".tile", reduced_motion="reduce")
    assert running_animations(page) == 0
    assert p.errors == []


def test_agents_keep_distinct_colours_across_map_and_frames(browse):
    p, page = open_view(browse, "/acme-shop?view=map", ".legend-row span")
    spans = page.locator(".legend-row > span[class^='ag-']")
    legend = {}
    for i in range(spans.count()):
        legend[spans.nth(i).inner_text().strip()] = spans.nth(i).get_attribute("class").split()[0]
    assert len(legend) >= 2
    assert len(set(legend.values())) == len(legend)
    held = {e["holder"] for e in paths_of(body(p.server.get("/api/raidmap/acme-shop"))).values() if e["holder"]}
    assert held == set(legend)
    page.goto(page.url.replace("view=map", "view=frames"))
    settle_frames = page.locator(".frame[data-agent]")
    settle_frames.first.wait_for()
    for i in range(settle_frames.count()):
        frame = settle_frames.nth(i)
        name = frame.get_attribute("data-agent")
        if name in legend:
            assert legend[name] in frame.get_attribute("class").split()
    assert p.errors == []
