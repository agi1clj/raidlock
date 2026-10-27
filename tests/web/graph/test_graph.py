import json

import pytest
from browser import Page, assert_fits, call_cli, open_view, running_animations
from serving import start

from raidlock.web.graph import dependency_graph, layers


def body(resp):
    return json.loads(resp[2])


def card(cid, *deps, lane="todo"):
    return {"id": cid, "title": cid.upper(), "lane": lane, "owner": "", "team": "", "depends": list(deps)}


def test_layers_order_dependencies():
    depth, cycles = layers([card("a"), card("b", "a"), card("c", "a", "b"), card("d")])
    assert depth == {"a": 0, "b": 1, "c": 2, "d": 0}
    assert cycles == []


def test_cycles_and_missing_dependencies_are_reported():
    g = dependency_graph([card("a", "c"), card("b", "a"), card("c", "b"), card("x", "ghost")])
    assert g["cycles"] and sorted(g["cycles"][0]) == ["a", "b", "c"]
    assert {"from": "ghost", "to": "x", "missing": True} in g["edges"]
    assert len(g["nodes"]) == 4
    rows = {(n["depth"], n["row"]) for n in g["nodes"]}
    assert len(rows) == 4


def test_api_graph_and_heatmap(server):
    status, headers, raw = server.get("/api/graph/acme-shop")
    assert status == 200
    d = json.loads(raw)
    assert {"from": "cart-api", "to": "promo-codes", "missing": False} in d["edges"]
    nodes = {n["id"]: n for n in d["nodes"]}
    assert nodes["promo-codes"]["depth"] == nodes["cart-api"]["depth"] + 1
    assert d["columns"] == 2
    heat = {r["path"]: r for r in d["heat"]["rows"]}
    cart = heat["storefront/api/cart"]
    assert cart["total"] == 2
    assert cart["cells"][-1]["lock_violation"] == 1 and cart["cells"][-1]["lock_denied"] == 1
    assert heat["storefront/api/cart/rounding"]["waiting"] == 1
    assert "web/jobs/sitemap" not in heat
    assert server.get("/api/graph/acme-shop", headers={"If-None-Match": headers["ETag"]})[0] == 304


def test_takeovers_count_as_contention(tmp_path):
    import demo

    from raidlock.web.graph import build

    d = demo.Demo(tmp_path / "t", "takeover", "repo")
    d.team("core", 3)
    d.agent("ana", "core", "claude-code", "claude-sonnet-5-5")
    d.agent("ben", "core", "claude-code", "claude-haiku-5-5")
    d.card("one", "One", "core", "src")
    d.claim("one", "ana")
    d.move("one", "ana", "blocked")
    d.board.claim("one", d.board.caller(agent="ben", token=d.tokens["ben"], env={}), take_over=True)
    rows = {r["path"]: r for r in build(d.board)["heat"]["rows"]}
    assert rows["repo/src"]["cells"][-1]["takeover"] == 1


def test_cli_graph(tmp_path, capsys):
    import demo

    d = demo.acme_shop(tmp_path / "cli")
    code, out = call_cli(capsys, d.board, "graph")
    assert code == 0
    assert out["heat"]["rows"][0]["path"].startswith("storefront/api/cart")


@pytest.mark.parametrize("theme", ["raid", "plain"])
@pytest.mark.parametrize("width", [375, 768, 1280, 1440])
def test_graph_view_draws_inline_svg(browse, theme, width):
    p, page = open_view(browse, "/acme-shop?view=graph", ".dep-svg", theme=theme, width=width)
    assert page.locator(".dep-svg .gnode").count() == 2
    assert "6 cards have no dependencies" in page.locator("#viewPane").inner_text()
    assert page.locator('.dep-svg .edge[data-from="cart-api"][data-to="promo-codes"]').count() == 1
    assert page.locator(".heat-svg rect.heat-0, .heat-svg rect.heat-1, .heat-svg rect.heat-4").count() > 0
    assert page.locator('.heat-svg rect[data-path="storefront/api/cart"]:not(.heat-0)').count() == 1
    assert page.evaluate(
        "[...document.querySelectorAll('svg')].every(s => s.namespaceURI === 'http://www.w3.org/2000/svg')"
    )
    assert page.locator("script[src*='//']").count() == 0
    page.locator(".as-text summary").click()
    assert (
        "cart-api: Cart API returns totals with tax → promo-codes: Promo codes at checkout"
        in page.locator(".as-text").inner_text()
    )
    assert_fits(p, page)


def test_graph_titles_render_as_text(chromium, tmp_path):
    import demo

    d = demo.Demo(tmp_path / "x", "xss-graph", "repo")
    d.team("core", 3)
    d.card("evil", demo.XSS, "core", "src")
    d.card("next", "Next", "core", "next", depends="evil")
    running = start()
    try:
        p = Page(chromium, running)
        page = p.open("/xss-graph?view=graph")
        page.wait_for_selector(".dep-svg .gnode")
        page.wait_for_timeout(300)
        assert page.evaluate("window.__pwned") is None
        assert page.locator("img").count() == 0
        assert "<img" in page.locator('.gnode[data-id="evil"] title').text_content()
        assert p.errors == []
        p.close()
    finally:
        running.srv.shutdown()
        running.srv.server_close()


def test_graph_respects_reduced_motion(browse):
    p, page = open_view(browse, "/acme-shop?view=graph", ".dep-svg", reduced_motion="reduce")
    assert running_animations(page) == 0
    assert p.errors == []


def test_deep_graph_and_bounds(monkeypatch):
    import raidlock.web.graph as graph

    cards = [card(str(i), *([str(i - 1)] if i else [])) for i in range(1200)]
    assert len(dependency_graph(cards)["nodes"]) == 1200
    cards[0]["depends"] = ["1199"]
    assert dependency_graph(cards)["cycles"]
    monkeypatch.setattr(graph, "MAX_NODES", 10)
    with pytest.raises(graph.GraphTooLarge, match="graph too large"):
        dependency_graph(cards)


def test_graph_http_errors_are_json(server, monkeypatch):
    import raidlock.web.graph as graph

    monkeypatch.setattr(graph, "MAX_NODES", 0)
    response = server.get("/api/graph/acme-shop")
    assert response[0] == 413 and body(response)["error"] == "graph too large"
    monkeypatch.setattr(graph, "payload", lambda *args: 1 / 0)
    response = server.get("/api/graph/acme-shop")
    assert response[0] == 500 and body(response)["error"]


@pytest.mark.parametrize("error,code", [("large", 413), ("unexpected", 500)])
def test_graph_handler_errors_without_socket(error, code):
    from raidlock.web.graph import GraphTooLarge
    from raidlock.web.server import Handler

    class Request:
        path = "/api/graph/example"
        result = None

        def _host_ok(self):
            return True

        def _api(self, path, query):
            if error == "large":
                raise GraphTooLarge("graph too large")
            raise RuntimeError("private traceback")

        def _error(self, status, message):
            self.result = status, json.dumps({"error": message})

    request = Request()
    Handler.do_GET(request)
    assert request.result[0] == code
    assert "private traceback" not in request.result[1]
    assert json.loads(request.result[1])["error"]


def test_cancelled_and_unknown_lanes():
    g = dependency_graph(
        [
            card("done", lane="done"),
            card("cancelled", lane="cancelled"),
            card("waiting", "done", "cancelled", lane="custom"),
        ]
    )
    assert len(g["nodes"]) == 3
    assert len(g["edges"]) == 2


@pytest.mark.parametrize("width", [375, 768, 1280, 1440])
def test_large_graph_missing_cycles_bounds_and_poll(browse, width):
    cards = [card(f"c{i}", *([f"c{i - 1}"] if i else ["c2"])) for i in range(155)]
    cards[3]["depends"].append("ghost")
    cards[-1]["depends"] = []
    cards[2]["lane"] = "cancelled"
    for i, item in enumerate(cards):
        item["title"] = "Long title <safe> " * 30
        item["locks"] = [f"repo{i % 3}/src"]
    payload = {**dependency_graph(cards), "heat": {}}
    p = browse(width=width)
    page = p.page
    page.route("**/api/graph/acme-shop*", lambda route: route.fulfill(json=payload))
    p.open("/acme-shop?view=graph", theme="raid")
    page.wait_for_selector(".dep-svg .gnode")
    assert page.locator(".gnode").count() == 155
    assert "1 card has no dependencies" in page.locator("#viewPane").inner_text()
    assert page.locator(".edge").count() == len(payload["edges"])
    assert page.locator('.gnode[data-id="ghost"]').count() == 1
    assert page.evaluate("""() => {
      const svg = document.querySelector('.dep-svg');
      const boxes = [...svg.querySelectorAll('.gnode')].map(n => {
        const r = n.getBBox(), m = n.transform.baseVal.consolidate().matrix;
        return {x: m.e, y: m.f, w: r.width, h: r.height};
      });
      return boxes.every((r, i) => r.x >= 0 && r.y >= 0 &&
        r.x + r.w <= svg.viewBox.baseVal.width && r.y + r.h <= svg.viewBox.baseVal.height &&
        boxes.every((q, j) => i === j || r.x + r.w <= q.x || q.x + q.w <= r.x || r.y + r.h <= q.y || q.y + q.h <= r.y));
    }""")
    page.locator(".as-text summary").click()
    assert "ghost: Missing card" in page.locator(".as-text").inner_text()
    assert "(no dependencies)" in page.locator(".as-text").inner_text()
    node = page.locator('.gnode[data-id="c1"]')
    node.focus()
    next(n for n in payload["nodes"] if n["id"] == "c1")["title"] = "Updated after polling"
    page.wait_for_function(
        "() => document.querySelector('.dep-svg').textContent.includes('Updated after polling')", timeout=10000
    )
    assert page.evaluate("document.activeElement.dataset.id") == "c1"
    assert_fits(p, page)


def test_graph_keyboard_opens_card(browse):
    p, page = open_view(browse, "/acme-shop?view=graph", ".dep-svg")
    nodes = page.locator(".gnode[data-card]")
    assert nodes.count() == 2
    assert nodes.evaluate_all("nodes => nodes.every(n => n.getAttribute('tabindex') === '0')")
    page.locator('.gnode[data-id="cart-api"]').focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("#cardDrawer[open]")
    assert p.errors == []


def test_graph_repo_filter_and_theme(browse):
    p, page = open_view(browse, "/acme-shop?view=graph", ".dep-svg")
    page.locator('#repos [data-repo="web"]').click()
    assert page.locator('.gnode[data-id="cart-api"]').count() == 0
    assert page.locator(".gnode").count() > 0 or "No quest chains yet" in page.locator("#viewPane").inner_text()
    assert page.locator(".edge").count() == page.locator(".as-text li").filter(has_text="→").count()
    page.locator('#repos [data-repo=""]').click()
    assert page.locator(".gnode").count() == 2
    page.locator('a[data-nav="pocket-notes"]').first.click()
    page.wait_for_url("**/pocket-notes")
    page.wait_for_function("() => !document.querySelector('#viewPane:not([hidden]) .gnode[data-id=cart-api]')")
    assert page.locator('.gnode[data-id="cart-api"]').count() == 0
    assert p.errors == []
