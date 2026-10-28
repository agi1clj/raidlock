import datetime
import json

from raidlock import cli
from raidlock.core import clock


def _tidy(world, capsys):
    code = cli.main(["--lead-secret", str(world.secret_file), "tidy", "--board", str(world.board.dir), "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_tidy_reaps_expired_work_and_lists_every_open_decision(world, capsys, monkeypatch):
    old = clock.iso(clock.now() - datetime.timedelta(days=20))
    world.board.new_card(world.lead, "Old idea", card_id="old", locks="app/old")
    monkeypatch.setattr(clock, "_offset", datetime.timedelta(days=-20))
    with world.board.lock():
        world.board._save_card(world.board.card("old"))
    monkeypatch.setattr(clock, "_offset", datetime.timedelta(0))
    world.board.join(world.lead, agent="other", provider="openai")
    world.board.new_card(world.lead, "Gone quiet", card_id="gone", locks="app/gone")
    world.board.claim("gone", world.lead, for_agent="other")
    world.board.index.update_lease(world.board.uid, "gone", expires=old)
    world.board.move("task", "review", world.worker)
    world.board.new_card(world.lead, "Fresh", card_id="fresh", locks="app/fresh")

    code, out = _tidy(world, capsys)
    assert code == 0
    assert out["reaped"] == ["gone"]
    reasons = {d["card"]: d["reason"] for d in out["decide"]}
    assert reasons == {
        "gone": "blocked",
        "task": "waiting in review",
        "old": "untouched in todo for over 14 days",
    }
    assert world.board.card("gone")["lane"] == "blocked"
    review = next(d for d in out["decide"] if d["card"] == "task")
    assert any("close task --evidence" in c for c in review["commands"])


def test_tidy_reports_a_clean_board(world, capsys):
    world.board.move("task", "done", world.lead)
    code, out = _tidy(world, capsys)
    assert code == 0 and out["decide"] == [] and out["reaped"] == []


def test_tidy_is_lead_only(world, capsys, monkeypatch):
    monkeypatch.setenv("RAIDLOCK_AGENT", "worker")
    monkeypatch.setenv("RAIDLOCK_TOKEN", world.token)
    code = cli.main(["tidy", "--board", str(world.board.dir), "--json"])
    assert code != 0
    assert json.loads(capsys.readouterr().out)["message"] == "only the lead can tidy the board"


def test_ui_reaper_moves_expired_work_to_blocked(world, monkeypatch):
    import pytest

    from raidlock import tidy

    world.board.index.update_lease(
        world.board.uid, "task", expires=clock.iso(clock.now() - datetime.timedelta(hours=2))
    )
    catalog = type("Catalog", (), {"workspaces": lambda self: [type("W", (), {"board": lambda self: world.board})()]})()

    def stop(seconds):
        raise SystemExit

    monkeypatch.setattr(tidy.time, "sleep", stop)
    with pytest.raises(SystemExit):
        tidy.reaper(catalog)
    assert world.board.card("task")["lane"] == "blocked"


def test_lead_session_start_names_waiting_cards_and_stays_silent_when_clean(world, monkeypatch):
    from raidlock import hooks

    monkeypatch.setattr(hooks, "session_role", lambda *a, **k: "lead")
    env = {"RAIDLOCK_HOME": str(world.root / "home")}
    data = {"session_id": "s1", "cwd": str(world.board.workspace)}
    assert hooks._native_join(data, env, world.board.dir, world.board) is None
    world.board.move("task", "review", world.worker)
    out = hooks._native_join(data, env, world.board.dir, world.board)
    text = out["hookSpecificOutput"]["additionalContext"]
    assert "1 card(s)" in text and "task" in text and "tidy" in text
    assert str(world.secret_file) not in text
