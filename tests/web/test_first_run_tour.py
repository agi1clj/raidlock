import pytest
from browser import Page, no_hscroll, settle
from serving import isolate_env, start

pytest.importorskip("playwright.sync_api")


def _serve(tmp_path_factory, build):
    import demo

    root = tmp_path_factory.mktemp("tour")
    mp = pytest.MonkeyPatch()
    isolate_env(mp, root)
    from raidlock.core import lead

    table = lead.process_table()
    mp.setattr(lead, "process_table", lambda: table)
    running = start()
    running.demos = build(demo, root / "ws")
    return running, mp


@pytest.fixture(scope="module")
def tour_server(tmp_path_factory):
    def build(demo, ws):
        busy = demo.empty_board(ws, "quest-busy")
        busy.card("seed", "Seed card", None, "src")
        return {"steps": demo.empty_board(ws, "quest-steps"), "busy": busy}

    running, mp = _serve(tmp_path_factory, build)
    yield running
    running.srv.shutdown()
    running.srv.server_close()
    mp.undo()


@pytest.fixture(scope="module")
def quiet_server(tmp_path_factory):
    def build(demo, ws):
        return {name: demo.empty_board(ws, name) for name in ("quest-one", "quest-two")}

    running, mp = _serve(tmp_path_factory, build)
    yield running
    running.srv.shutdown()
    running.srv.server_close()
    mp.undo()


@pytest.fixture
def pages(chromium):
    made = []

    def make(server, width=1280, **kw):
        p = Page(chromium, server, width=width, height=900, tour=True, **kw)
        made.append(p)
        return p

    yield make
    for p in made:
        p.close()


def marks(page):
    return page.locator("#tour .tour-step").evaluate_all("els => els.map(e => e.classList.contains('done'))")


def wait_marks(page, expected):
    page.wait_for_function(
        "want => JSON.stringify([...document.querySelectorAll('#tour .tour-step')]"
        ".map(e => e.classList.contains('done'))) === want",
        arg=str(expected).lower().replace(" ", ""),
        timeout=15000,
    )


def test_tour_progresses_with_the_board_and_leaves_after_done(pages, tour_server):
    d = tour_server.demos["steps"]
    p = pages(tour_server)
    page = p.open("/quest-steps", theme="raid")
    settle(page, "#tour:not([hidden]) .tour-step")
    assert page.locator("#tourTitle").inner_text().lower() == "first quest"
    assert marks(page) == [False, False, False, False]
    assert page.locator("#tour .tour-step.next").get_attribute("aria-current") == "step"
    first = page.locator("#tour .tour-step").first.inner_text()
    assert "raid --lead-secret" in first and " new " in first and "--verify-cmd" in first and "<repo>/<path>" in first
    d.team("core")
    d.agent("quest-worker", "core", "claude-code", "claude-x")
    d.card("quest-one", "First card", "core", "src")
    wait_marks(page, [True, False, False, False])
    assert "raid claim quest-one" in page.locator("#tour").inner_text()
    d.claim("quest-one", "quest-worker")
    wait_marks(page, [True, True, False, False])
    d.move("quest-one", "quest-worker", "review")
    wait_marks(page, [True, True, True, False])
    assert page.locator("#tour .tour-count").inner_text() == "3 of 4"
    d.done("quest-one")
    page.wait_for_selector("#tour", state="hidden", timeout=15000)
    page.reload()
    settle(page, "#focus:not([hidden])")
    assert page.locator("#tour").is_hidden()
    assert p.errors == []


def test_tour_is_hidden_on_a_board_with_cards(pages, tour_server):
    p = pages(tour_server)
    page = p.open("/quest-busy", theme="raid")
    settle(page, "#focus:not([hidden]) #todoList .row")
    page.wait_for_timeout(500)
    assert page.locator("#tour").count() == 0 or page.locator("#tour").is_hidden()
    assert p.errors == []


def test_dismiss_is_remembered_per_workspace(pages, quiet_server):
    p = pages(quiet_server, width=390)
    page = p.open("/quest-one", theme="plain")
    settle(page, "#tour:not([hidden]) .tour-step")
    assert page.locator("#tourTitle").inner_text() == "Your first card"
    fit = no_hscroll(page)
    assert fit["scroll"] <= fit["client"] and fit["wide"] == [], fit
    page.get_by_role("button", name="Dismiss the first card guide").click()
    assert page.locator("#tour").is_hidden()
    page.reload()
    settle(page, "#focus:not([hidden])")
    page.wait_for_timeout(500)
    assert page.locator("#tour").is_hidden()
    page = p.open("/quest-two", theme=None)
    settle(page, "#tour:not([hidden]) .tour-step")
    assert p.errors == []


def test_realm_shows_the_tour_when_every_guild_is_empty(pages, quiet_server):
    p = pages(quiet_server)
    page = p.open("/", theme="raid")
    settle(page, "#overview:not([hidden]) #tour:not([hidden]) .tour-step")
    assert marks(page) == [False, False, False, False]
    assert "<card>" in page.locator("#tour").inner_text()
    assert p.errors == []
