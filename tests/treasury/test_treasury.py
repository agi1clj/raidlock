import datetime
import json

import pytest
from browser import call_cli, open_view

from raidlock import budgets
from raidlock.core import clock
from raidlock.treasury import ALERT_PCT, TRAIL_DAYS, combine, forecast


@pytest.fixture
def lab(tmp_path):
    import demo

    d = demo.Demo(tmp_path / "t", "coin-lab", "repo")
    d.team("core", 5)
    d.agent("api-agent", "core", "codex-cli", "gpt-6-astra", billing="api")
    d.agent("plan-agent", "core", "claude-code", "claude-sonnet-5-5", billing="plan")
    d.card("a", "A", "core", "a")
    d.card("b", "B", "core", "b")
    d.claim("a", "api-agent")
    d.claim("b", "plan-agent")
    return d


def test_burn_and_forecast(lab):
    lab.cost("a", "api-agent", 1000, 0, 100, usd=7.0)
    now = clock.now() + datetime.timedelta(seconds=1)
    f = forecast(lab.board, now)
    assert f["month"] == 7.0 and f["today"] == 7.0
    assert f["burn"]["per_day_trailing"] == pytest.approx(7.0 / TRAIL_DAYS)
    assert f["forecast"] == pytest.approx(7.0 + 7.0 / TRAIL_DAYS * f["period"]["days_left"], abs=0.01)
    assert f["period"]["start"].endswith("-01T00:00:00Z") and f["period"]["end"] > f["now"]
    assert len(f["by_day"]) == 14 and f["by_day"][-1]["usd"] == 7.0
    assert f["alerts"] == [] and f["caps"] == {"daily": None, "monthly": None}


def test_plan_usage_counts_like_budget_checks(lab):
    lab.cost("b", "plan-agent", 100000, 0, 10000)
    equiv = lab.board.card("b")["costs"][0]["usd_equiv"]
    assert equiv > 0
    assert forecast(lab.board)["month"] == pytest.approx(equiv)
    budgets.set_budget(lab.board, lab.lead, budgets.COUNT_PLAN, False)
    f = forecast(lab.board)
    assert f["month"] == 0 and f["count_plan"] is False
    assert f["month_api_equiv"] == pytest.approx(equiv)


def test_alerts_at_thresholds(lab):
    budgets.set_budget(lab.board, lab.lead, budgets.DAILY, 10)
    budgets.set_budget(lab.board, lab.lead, budgets.MONTHLY, 1000)
    lab.cost("a", "api-agent", 1000, 0, 100, usd=ALERT_PCT / 10 - 1)
    assert forecast(lab.board)["alerts"] == []
    lab.cost("a", "api-agent", 1000, 0, 100, usd=1.5)
    alerts = forecast(lab.board)["alerts"]
    assert [(a["level"], a["kind"]) for a in alerts] == [("warning", "daily")]
    lab.cost("a", "api-agent", 1000, 0, 100, usd=2)
    assert ("critical", "daily") in [(a["level"], a["kind"]) for a in forecast(lab.board)["alerts"]]


def test_forecast_and_monthly_alerts(lab):
    lab.cost("a", "api-agent", 1000, 0, 100, usd=50)
    now = clock.now() + datetime.timedelta(seconds=1)
    left = forecast(lab.board, now)["period"]["days_left"]
    budgets.set_budget(lab.board, lab.lead, budgets.MONTHLY, 50.0 + 0.5 * 50 / TRAIL_DAYS * left)
    assert [(a["level"], a["kind"]) for a in forecast(lab.board, now)["alerts"]] == [("warning", "forecast")]
    budgets.set_budget(lab.board, lab.lead, budgets.MONTHLY, 50)
    assert [(a["level"], a["kind"]) for a in forecast(lab.board, now)["alerts"]] == [("critical", "monthly")]
    end = datetime.datetime.fromisoformat(forecast(lab.board, now)["period"]["end"].replace("Z", "+00:00"))
    late = end - datetime.timedelta(seconds=1)
    budgets.set_budget(lab.board, lab.lead, budgets.MONTHLY, 60)
    f = forecast(lab.board, late)
    assert f["forecast"] <= 60 and [a["kind"] for a in f["alerts"]] == ["monthly"]


def test_combine_across_workspaces(lab, tmp_path):
    import demo

    other = demo.Demo(tmp_path / "o", "coin-two", "repo")
    other.team("core", 2)
    other.agent("solo", "core", "codex-cli", "gpt-6-astra", billing="api")
    other.card("x", "X", "core", "x")
    other.claim("x", "solo")
    other.cost("x", "solo", 10, 0, 10, usd=3)
    lab.cost("a", "api-agent", 10, 0, 10, usd=4)
    budgets.set_budget(other.board, other.lead, budgets.DAILY, 2)
    both = combine([forecast(lab.board), forecast(other.board)])
    assert both["month"] == 7 and both["today"] == 7
    assert [a["workspace"] for a in both["alerts"]] == ["coin-two"]
    assert {w["workspace"] for w in both["workspaces"]} == {"coin-lab", "coin-two"}
    assert both["by_day"][-1]["usd"] == 7


def test_api_payloads(server):
    status, _, raw = server.get("/api/board/acme-shop")
    f = json.loads(raw)["forecast"]
    assert status == 200 and f["workspace"] == "acme-shop" and f["month"] > 0
    o = json.loads(server.get("/api/overview")[2])["forecast"]
    assert {w["workspace"] for w in o["workspaces"]} >= {"acme-shop", "pocket-notes"}
    assert o["month"] >= f["month"]


def test_cli(lab, capsys):
    lab.cost("a", "api-agent", 10, 0, 10, usd=1)
    code, out = call_cli(capsys, lab.board, "treasury")
    assert code == 0 and out["month"] == 1
    code, out = call_cli(capsys, lab.board, "treasury", "--all")
    assert code == 0 and out["workspace"] == "*" and "coin-lab" in [w["workspace"] for w in out["workspaces"]]


@pytest.mark.parametrize("theme", ["raid", "plain"])
@pytest.mark.parametrize("width", [1280, 375])
def test_forecast_in_treasury_panel(browse, server, theme, width):
    p = browse(width=width, height=900)
    page = p.open("/acme-shop?view=treasury", theme=theme, panels=("overviewCosts",))
    page.wait_for_selector("#treasury .forecast")
    text = page.locator("#treasury .forecast").inner_text()
    assert "a day over the last 7 days" in text and "by month end" in text
    assert page.locator("#treasury .spark i").count() == 14
    assert page.locator("#treasury .spark").get_attribute("role") == "img"
    page = p.open("/?view=treasury", theme=theme, panels=("overviewCosts",))
    page.wait_for_selector("#treasury .forecast")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert p.errors == []


def test_budget_alert_reaches_the_ui(browse, server):
    board = server.demos["pocket-notes"]
    budgets.set_budget(board.board, board.lead, budgets.DAILY, 0.01)
    p, page = open_view(browse, "/pocket-notes?view=treasury", "#treasury", storage={"rl.open.overviewCosts": "true"})
    page.wait_for_selector("#treasury .alert.critical")
    assert "daily cap" in page.locator("#treasury .alert").inner_text()
    assert "1 budget alert" in page.locator("#costHint").inner_text()
    page = p.open("/?view=treasury", storage={"rl.open.overviewCosts": "true"})
    page.wait_for_selector("#treasury .alert.critical")
    assert "pocket-notes" in page.locator("#treasury .alert").inner_text()
    assert p.errors == []
