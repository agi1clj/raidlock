import json
import os

import pytest
from browser import assert_fits, call_cli, open_view, running_animations

from raidlock.core import store
from raidlock.frames import CRITICAL_PCT, DEFAULT_WINDOW, WARN_PCT, frames, level


def by_agent(board):
    return {f["agent"]: f for f in frames(board)["agents"]}


@pytest.fixture
def lab(tmp_path):
    import demo

    d = demo.Demo(tmp_path / "f", "frames-lab", "repo")
    d.team("core", 5)
    for name in ("ana", "ben", "cal"):
        d.agent(name, "core", "claude-code", "claude-sonnet-5-5")
    for cid in ("a", "b", "c"):
        d.card(cid, f"Card {cid}", "core", cid)
    return d


def test_levels():
    assert level(None) == "unknown"
    assert level(WARN_PCT - 0.1) == "ok"
    assert level(WARN_PCT) == "warning"
    assert level(CRITICAL_PCT) == "critical"


def test_context_from_recorded_usage(lab):
    lab.claim("a", "ana")
    lab.cost("a", "ana", 10000, 50000, 2000, ms=600000)
    lab.cost("a", "ana", 20000, 150000, 4000, ms=1200000)
    f = by_agent(lab.board)["ana"]
    assert f["context"]["source"] == "usage"
    assert f["context"]["tokens"] == 170000
    assert f["context"]["window"] == DEFAULT_WINDOW and f["context"]["window_source"] == "assumed"
    assert f["context"]["pct"] == 85.0 and f["context"]["level"] == "warning"
    assert f["handoff"] is True
    assert f["burn"]["runs"] == 2
    assert f["burn"]["tokens_per_hour"] == round((62000 + 174000) / 0.5)
    assert f["burn"]["share"] == 1


def test_window_from_pricing_file(lab):
    lab.claim("a", "ana")
    lab.cost("a", "ana", 20000, 150000, 4000)
    path = os.path.join(lab.board.home, "pricing.json")
    data = store.read_json(path)
    data["models"]["claude-sonnet-5-5"]["context_window"] = 1000000
    store.write_json(path, data)
    f = by_agent(lab.board)["ana"]
    assert f["context"]["window_source"] == "pricing" and f["context"]["pct"] == 17.0
    assert f["handoff"] is False and f["burn"]["tokens_per_hour"] is None


def test_estimate_without_usage_is_labelled(lab):
    lab.claim("b", "ben")
    lab.note("b", "ben", "x" * 1600)
    lab.note("b", "ben", "y" * 1600)
    f = by_agent(lab.board)["ben"]
    assert f["context"]["source"] == "estimate"
    assert f["context"]["tokens"] >= 800
    assert by_agent(lab.board)["cal"]["context"]["source"] == "estimate"


def test_critical_and_idle_agents(lab):
    lab.claim("c", "cal")
    lab.cost("c", "cal", 0, 195000, 1000, ms=60000)
    f = by_agent(lab.board)["cal"]
    assert f["context"]["level"] == "critical" and f["handoff"] is True
    lab.move("c", "cal", "review")
    lab.done("c")
    assert by_agent(lab.board)["cal"]["handoff"] is False
    assert frames(lab.board)["handoffs"] == []


def test_api_and_cli(server, tmp_path, capsys):
    import demo

    status, _, raw = server.get("/api/frames/acme-shop")
    d = json.loads(raw)
    assert status == 200
    agents = {f["agent"]: f for f in d["agents"]}
    assert agents["opus-checkout"]["handoff"] is True
    assert agents["haiku-inspector"]["context"]["source"] == "estimate"
    assert "opus-checkout" in d["handoffs"]
    board = demo.pocket_notes(tmp_path / "cli").board
    code, out = call_cli(capsys, board, "frames")
    assert code == 0 and {f["agent"] for f in out["agents"]} >= {"opus-sync", "codex-ui"}


@pytest.mark.parametrize("theme", ["raid", "plain"])
@pytest.mark.parametrize("width", [1280, 375])
def test_frames_view(browse, theme, width):
    p, page = open_view(browse, "/acme-shop?view=frames", ".frame", theme=theme, width=width)
    opus = page.locator('.frame[data-agent="opus-checkout"]')
    assert "Hand off now" in opus.locator(".handoff").inner_text()
    meter = opus.locator(".fbar.hp .track")
    assert meter.get_attribute("role") == "meter" and meter.get_attribute("aria-valuenow") == "100"
    assert "assumed window" in meter.get_attribute("aria-valuetext")
    page.locator("#viewPane .sub-fold > summary").click()
    inspector = page.locator('.frame[data-agent="haiku-inspector"]')
    assert "estimate" in inspector.locator(".fbar.hp .fv").inner_text()
    assert "near the context limit" in page.locator(".handoff-sum").inner_text()
    assert opus.locator(".fbar.hp .fl").inner_text() == "Context"
    assert_fits(p, page)


def test_frames_respect_reduced_motion(browse):
    p, page = open_view(browse, "/acme-shop?view=frames", ".frame", reduced_motion="reduce")
    assert running_animations(page) == 0
    assert page.evaluate("getComputedStyle(document.querySelector('.fbar .track i')).transitionDuration") == "0s"
    assert p.errors == []
