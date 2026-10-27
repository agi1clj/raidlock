"""Built-in plans and an optional CLI registration hook."""

import json
import re
from importlib.resources import files

from raidlock.core import Precondition
from raidlock.core.paths import parse_list

_NAMES = ("docs-refresh", "multi-repo-refactor", "security-audit", "upgrade-deps")
_VARIABLE = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def list_recipes():
    return list(_NAMES)


def load_recipe(source, variables=None):
    name = source.removeprefix("recipe:")
    if name not in _NAMES:
        raise Precondition(f"unknown recipe: {name}", hint="raidlock recipes")
    values = dict(variables or {})
    text = files(__package__).joinpath(name + ".md").read_text(encoding="utf-8")
    required = set(_VARIABLE.findall(text))
    missing = required - values.keys()
    if missing:
        raise Precondition("missing recipe variables: " + ", ".join(sorted(missing)))
    for key in required:
        value = values[key]
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
            raise Precondition(f"invalid recipe variable {key}: use a repository name")
    return _VARIABLE.sub(lambda match: values[match[1]], text)


def read_plan(source, team=None, variables=None):
    from raidlock.cli import _md_item, _read_plan

    if not source.startswith("recipe:"):
        if variables:
            raise Precondition("--var is supported only for recipes")
        return _read_plan(source, team)
    items = []
    metadata = None
    seen = set()
    for line in load_recipe(source, variables).splitlines():
        if line.startswith("Quest: "):
            metadata = json.loads(line[7:])
        elif line.startswith("- [ ] "):
            if metadata is None:
                raise Precondition("recipe quest is missing metadata")
            item = {**_md_item(line), **metadata}
            if item["id"] in seen or not set(item.get("depends", [])) <= seen:
                raise Precondition("recipe dependencies must reference earlier unique quests")
            seen.add(item["id"])
            item["locks"] = parse_list(item["locks"])
            if team:
                item["team"] = team
            items.append(item)
            metadata = None
    return items


def parse_variables(assignments):
    variables = {}
    for assignment in assignments or []:
        key, separator, value = assignment.partition("=")
        if not separator or not key or key in variables:
            raise Precondition(f"invalid or duplicate --var: {assignment}")
        variables[key] = value
    return variables


def setup_plan(parser):
    parser.add_argument("action", choices=["import", "recipes"])
    parser.add_argument("file", nargs="?")
    parser.add_argument("--team", default=None)
    parser.add_argument("--var", action="append", default=[])


def handle_plan(ctx, args):
    if args.action == "recipes":
        if args.file or args.var or args.team:
            raise Precondition("plan recipes takes no import arguments")
        names = list_recipes()
        return {"recipes": names}, "\n".join(names)
    if not args.file:
        raise Precondition("plan import requires a file or recipe:<name>")
    variables = parse_variables(args.var)
    items = read_plan(args.file, args.team, variables)
    cards = ctx.board.import_plan(ctx.caller(), items)
    ids = [card["id"] for card in cards]
    return {"cards": ids}, f"Imported {len(ids)} card(s): {', '.join(ids)}.\nNext: raidlock show"


def setup_recipes(parser):
    pass


def handle_recipes(ctx, args):
    names = list_recipes()
    return {"recipes": names}, "\n".join(names)


def register_cli(add_command):
    add_command("recipes", "list built-in plan recipes", setup_recipes, handle_recipes)
