import os
import tempfile
from pathlib import Path

import pytest
from browser import no_hscroll
from test_focus_view import fleet, view

__all__ = ["fleet", "view"]
OUTPUT = Path(os.environ.get("RAIDLOCK_LAYOUT_OUT", Path(tempfile.gettempdir()) / "raidlock-ux-layout"))


@pytest.mark.parametrize("width", [1440, 1024, 390])
@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("path,kind", [("/orbit-ops", "busy"), ("/fresh-start", "empty")])
def test_layout_matrix(view, width, scheme, path, kind):
    p, page = view(path=path, width=width, color_scheme=scheme, reduced_motion="reduce")
    page.wait_for_timeout(300)
    fit = no_hscroll(page)
    assert fit["scroll"] <= fit["client"], fit
    assert fit["wide"] == [], fit
    assert page.locator("#needs").is_visible()
    assert page.locator("#todo").is_visible()
    assert page.locator("#doing").is_visible() == (width >= 768)
    assert page.locator("#donePanel").is_visible() == (width >= 768)
    assert page.locator("#chatter").is_hidden()
    assert page.locator("#agentsPanel").is_visible()
    assert page.locator("#agentsPanel details").count() == 0
    assert not page.locator("#logPanel").evaluate("e => e.open")
    if kind == "empty":
        assert page.locator("#needs").inner_text().strip() == "Nothing needs you"
    else:
        assert page.locator("#todoList > .row").count() == 5
        assert page.locator("#needsList button.primary").count() == 5
        assert page.locator("#needsMore").is_visible()
    if os.environ.get("RAIDLOCK_LAYOUT_SHOTS") == "1":
        OUTPUT.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(OUTPUT / f"{kind}-{scheme}-{width}.png"), full_page=True)
    assert p.errors == []


def test_mobile_tabs_keyboard_and_chatter(view):
    p, page = view(width=390)
    page.locator("#workTabs [data-tab=todo]").focus()
    page.keyboard.press("ArrowRight")
    assert page.locator("#doing").is_visible()
    assert page.locator("#todo").is_hidden()
    page.keyboard.press("End")
    assert page.locator("#donePanel").is_visible()
    assert page.locator("#workTabs [data-tab=chatter]").count() == 0
    assert page.locator("#chat").get_attribute("aria-live") == "polite"
    assert p.errors == []


def test_fold_choice_persists_and_done_preview(view):
    p, page = view()
    page.locator("#todo > summary").click()
    page.wait_for_function("() => localStorage.getItem('rl.open.todo') === 'false'")
    page.reload()
    page.wait_for_selector("#needsList .row")
    assert not page.locator("#todo").evaluate("e => e.open")
    page.locator("#donePanel > summary").click()
    page.locator("#doneList > .row").nth(4).wait_for()
    assert page.locator("#doneList > .row").count() == 5
    page.locator("#doneMore").click()
    assert page.locator("#doneList > .row").count() > 5
    assert p.errors == []


def test_drawer_escape_returns_focus(view):
    p, page = view()
    page.locator("#doingList button.details").first.click()
    assert page.locator("#cardDrawer").is_visible()
    page.keyboard.press("Escape")
    assert page.locator("#cardDrawer").is_hidden()
    assert page.evaluate("document.activeElement.matches('#doingList button.details')")
    assert p.errors == []


@pytest.mark.parametrize("width", [1440, 390])
def test_chatter_tab_gives_the_chat_the_main_area(view, width):
    p, page = view(width=width)
    assert page.locator("#summaryChips").count() == 0
    page.locator("#viewTabs a[data-sub=chat]").click()
    page.wait_for_function("() => document.getElementById('focus').dataset.filter === 'chatter'")
    assert page.locator("#chatter").is_visible()
    assert page.locator("#columns").is_hidden() and page.locator("#needs").is_hidden()
    assert page.locator("#viewTabs a[data-sub=chat]").get_attribute("aria-current") == "page"
    assert "view=chat" in page.url
    page.locator("#viewTabs a[data-sub=focus]").click()
    page.wait_for_function("() => document.getElementById('focus').dataset.filter === ''")
    assert page.locator("#columns").is_visible()
    assert p.errors == []


@pytest.mark.parametrize("width", [1440, 390])
def test_search_filters_cards_and_chat(view, width):
    p, page = view(width=width)
    page.wait_for_selector("#needsList .row")
    total = page.locator("#needsList .row, #todoList .row, #doingList .row").count()
    page.locator("#todoList").click(position={"x": 2, "y": 2})
    page.keyboard.press("/")
    assert page.evaluate("document.activeElement.id") == "q"
    page.keyboard.type("network")
    page.wait_for_function("() => document.getElementById('qCount').textContent.includes(' of ')")
    rows = page.locator("#needsList .row, #todoList .row, #doingList .row")
    assert 0 < rows.count() < total
    assert all("network" in t.lower() for t in rows.all_inner_texts())
    page.keyboard.press("Escape")
    page.wait_for_function("() => document.getElementById('qCount').textContent === ''")
    assert p.errors == []


def test_treasury_is_its_own_tab(view):
    p, page = view(width=1440)
    page.wait_for_selector("#needsList .row")
    assert page.locator("#overviewCosts").is_hidden()
    page.locator("#viewTabs a[data-sub=treasury]").click()
    page.wait_for_function("() => document.body.dataset.tab === 'treasury'")
    assert page.locator("#overviewCosts").is_visible()
    assert page.locator("#focus").is_hidden()
    assert page.locator("#overviewCosts #score table").count() > 0
    assert "view=treasury" in page.url
    assert p.errors == []


def test_question_mark_opens_keyboard_help(view):
    p, page = view(width=1440)
    page.wait_for_selector("#needsList .row")
    page.locator("#todoList").click(position={"x": 2, "y": 2})
    page.keyboard.press("?")
    assert page.locator("#kbdHelp").is_visible()
    assert "Search cards" in page.locator("#kbdHelp").inner_text()
    page.keyboard.press("Escape")
    assert page.locator("#kbdHelp").is_hidden()
    assert p.errors == []
