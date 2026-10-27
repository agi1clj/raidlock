import json
import tomllib

from raidlock import cli
from raidlock.recipes import read_plan


def test_recipe_registration_keeps_core_plan_and_mutation_metadata(monkeypatch):
    monkeypatch.setattr(cli, "_entry_points", lambda: [])
    cli.load_extensions()
    cli.load_extensions()
    assert cli.COMMANDS["plan"]["fn"] is cli.cmd_plan
    assert cli.COMMANDS["plan"]["mutating"] is True
    assert cli.COMMANDS["recipes"]["mutating"] is False


def test_core_plan_import_accepts_recipe_and_file(world, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("RAIDLOCK_LEAD_SECRET", world.lead_token)
    common = ["--board", str(world.root), "--json"]
    expected = read_plan("recipe:docs-refresh", "core", {"repo": "app"})
    assert cli.main(["plan", "import", "recipe:docs-refresh", "--var", "repo=app", "--team", "core", *common]) == 0
    imported = json.loads(capsys.readouterr().out)["cards"]
    assert imported == [item["id"] for item in expected]
    for item in expected:
        card = world.board.card(item["id"])
        assert card["locks"] == item["locks"]
        assert card["depends"] == item.get("depends", [])
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([{"id": "ordinary", "title": "Ordinary file", "locks": "app/src"}]))
    assert cli.main(["plan", "import", str(plan), *common]) == 0
    assert json.loads(capsys.readouterr().out)["cards"] == ["ordinary"]


def test_file_plan_rejects_recipe_variables_without_creating_cards(world, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RAIDLOCK_LEAD_SECRET", world.lead_token)
    plan = tmp_path / "plan.json"
    plan.write_text('[{"id":"ordinary","title":"Ordinary file"}]')
    assert cli.main(["plan", "import", str(plan), "--var", "repo=app", "--board", str(world.root)]) == 3
    assert "only to recipe imports" in capsys.readouterr().err
    assert world.board.cards() == []


def test_new_suggestions_are_read_only_until_explicit_locks(world, monkeypatch, capsys):
    from raidlock import infer

    suggestions = [{"path": "app/src", "score": 100, "reason": "Path mentioned in text"}]
    monkeypatch.setattr(infer, "suggest_locks", lambda board, text: suggestions)
    common = ["--board", str(world.root), "--json"]
    assert cli.main(["new", "Fix checkout", "--suggest-locks", *common]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"ok": True, "suggestions": suggestions, "created": False}
    assert world.board.cards() == []
    monkeypatch.setenv("RAIDLOCK_LEAD_SECRET", world.lead_token)
    assert cli.main(["new", "Fix checkout", "--id", "checkout", "--locks", "app/src", *common]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "checkout"
    assert world.board.card("checkout")["locks"] == ["app/src"]


def test_new_suggestions_reject_ambiguous_creation(world, capsys):
    assert cli.main(["new", "Fix checkout", "--suggest-locks", "--locks", "app/src", "--board", str(world.root)]) == 3
    assert "do not create a card" in capsys.readouterr().err
    assert world.board.cards() == []


def test_all_declared_extension_entry_points_load():
    from importlib.metadata import EntryPoint
    from pathlib import Path

    project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(project.read_text())
    for name, value in data["project"]["entry-points"]["raidlock.commands"].items():
        assert callable(EntryPoint(name=name, value=value, group="raidlock.commands").load()), name
