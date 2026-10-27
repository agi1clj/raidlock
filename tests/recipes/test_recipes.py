import argparse
from types import SimpleNamespace

import pytest

from raidlock.cli import _read_plan
from raidlock.core import Board, Precondition
from raidlock.core.paths import canonical, overlaps
from raidlock.recipes import handle_plan, list_recipes, load_recipe, read_plan, register_cli, setup_plan


@pytest.mark.parametrize("name", list_recipes())
def test_recipes_parse_and_import(name, tmp_path):
    variables = {"repo": "acme-shop", "other_repo": "pocket-notes"}
    text = load_recipe("recipe:" + name, variables)
    assert "{repo}" not in text and "{other_repo}" not in text
    markdown = tmp_path / "plan.md"
    markdown.write_text(text)
    plain = _read_plan(str(markdown), None)
    items = read_plan("recipe:" + name, variables=variables)
    assert len(items) == len(plain) >= 3
    seen = set()
    for item, original in zip(items, plain, strict=True):
        assert item["title"] == original["title"]
        assert item["id"] not in seen
        assert set(item["depends"]) <= seen
        seen.add(item["id"])
        assert item["locks"]
        assert all(canonical(lock) == lock for lock in item["locks"])
        assert all(lock.split("/")[0] in variables.values() for lock in item["locks"])
    board, token = Board.init(tmp_path / "workspace", home=tmp_path / "home", env={}, lead="lead")
    cards = board.import_plan(board.caller(lead_secret=token, env={}), items)
    assert [c["depends"] for c in cards] == [i["depends"] for i in items]
    assert [c["locks"] for c in cards] == [i["locks"] for i in items]


def test_security_reviews_are_independent():
    items = read_plan("recipe:security-audit", variables={"repo": "acme-shop"})
    builds = {i["id"]: i for i in items if i["id"].startswith("build-")}
    reviews = [i for i in items if i["id"].startswith("review-")]
    assert len(builds) == len(reviews) == 3
    assert {r["depends"][0] for r in reviews} == set(builds)
    for review in reviews:
        assert len(review["depends"]) == 1
        build = builds[review["depends"][0]]
        assert "Assign to provider openai." in build["scope"]
        assert "Assign to provider anthropic" in review["scope"]
        assert not any(overlaps(a, b) for a in build["locks"] for b in review["locks"])


@pytest.mark.parametrize(
    "source,variables",
    [
        ("recipe:missing", {}),
        ("recipe:../docs-refresh", {}),
        ("recipe:docs-refresh", {}),
        ("recipe:docs-refresh", {"repo": "../acme-shop"}),
        ("recipe:docs-refresh", {"repo": "acme-shop\n- [ ] injected"}),
        ("recipe:docs-refresh", {"repo": ""}),
        ("recipe:multi-repo-refactor", {"repo": "acme-shop"}),
    ],
)
def test_invalid_recipes(source, variables):
    with pytest.raises(Precondition):
        read_plan(source, variables=variables)


def test_registered_cli_lists_without_board_and_imports(tmp_path):
    registrations = []
    register_cli(lambda *args: registrations.append(args))
    name, description, setup, handler = registrations[0]
    assert name == "recipes"
    parser = argparse.ArgumentParser()
    setup(parser)
    result, text = handler(None, parser.parse_args([]))
    assert result["recipes"] == list_recipes() == text.splitlines()
    board, token = Board.init(tmp_path / "workspace", home=tmp_path / "home", env={}, lead="lead")
    caller = board.caller(lead_secret=token, env={})
    ctx = SimpleNamespace(board=board, caller=lambda: caller)
    import_parser = argparse.ArgumentParser()
    setup_plan(import_parser)
    result, text = handle_plan(
        ctx, import_parser.parse_args(["import", "recipe:docs-refresh", "--var", "repo=pocket-notes"])
    )
    assert len(result["cards"]) == 3
    assert all(c["locks"][0].startswith("pocket-notes/") for c in board.cards())


@pytest.mark.parametrize(
    "argv",
    [
        ["import"],
        ["import", "recipe:docs-refresh", "--var", "repo"],
        ["import", "recipe:docs-refresh", "--var", "repo=acme-shop", "--var", "repo=pocket-notes"],
        ["recipes", "--var", "repo=acme-shop"],
    ],
)
def test_bad_cli_arguments(argv):
    parser = argparse.ArgumentParser()
    setup_plan(parser)
    with pytest.raises(Precondition):
        handle_plan(None, parser.parse_args(argv))


def test_file_import_and_team(tmp_path):
    path = tmp_path / "plan.md"
    path.write_text("- [ ] Refresh docs | locks: pocket-notes/docs\n")
    assert read_plan(str(path), "docs") == _read_plan(str(path), "docs")
    items = read_plan("recipe:docs-refresh", "docs", {"repo": "pocket-notes"})
    assert all(item["team"] == "docs" for item in items)


def test_variables_rejected_for_ordinary_file(tmp_path):
    path = tmp_path / "plan.md"
    path.write_text("- [ ] Refresh docs | locks: pocket-notes/docs\n")
    with pytest.raises(Precondition, match="only for recipes"):
        read_plan(str(path), variables={"repo": "pocket-notes"})


def test_recipe_import_requires_explicit_lead_secret(tmp_path):
    from raidlock.core.errors import IdentityError

    board, _ = Board.init(tmp_path / "workspace", home=tmp_path / "home", env={}, lead="lead")
    ctx = SimpleNamespace(board=board, caller=lambda: board.caller(agent="lead", env={}))
    parser = argparse.ArgumentParser()
    setup_plan(parser)
    with pytest.raises(IdentityError):
        handle_plan(ctx, parser.parse_args(["import", "recipe:docs-refresh", "--var", "repo=pocket-notes"]))
    assert board.cards() == []


def test_registration_preserves_core_plan_handler():
    from raidlock import cli

    original = cli.COMMANDS["plan"].copy()
    try:
        register_cli(cli.add_command)
        assert cli.COMMANDS["plan"] == original
        assert cli.COMMANDS["plan"]["mutating"] is True
        assert cli.COMMANDS["recipes"]["mutating"] is False
    finally:
        cli.COMMANDS.pop("recipes", None)
