# Built-in recipes

The loader uses stdlib `importlib.resources` to read the packaged Markdown.
The existing wheel configuration includes this directory as package data.

The package exposes `read_plan(source, team=None, variables=None)`,
`parse_variables(assignments)` and `list_recipes()`. The core CLI owns
`plan import`, dispatches `recipe:` sources through `read_plan`, and retains
ordinary file imports and their mutating authorization. This package's
`register_cli(add_command)` registers only the read-only `recipes` listing.

```sh
raidlock recipes
raidlock --lead-secret /printed/path plan import recipe:upgrade-deps --var repo=acme-shop
raidlock --lead-secret /printed/path plan import recipe:security-audit --var repo=acme-shop
raidlock --lead-secret /printed/path plan import recipe:docs-refresh --var repo=pocket-notes
raidlock --lead-secret /printed/path plan import recipe:multi-repo-refactor --var repo=acme-shop --var other_repo=pocket-notes
```

Recipe imports require repository variables; ordinary file imports reject
`--var`. Recipe IDs are stable, so importing the same recipe twice into a
board requires resolving its existing cards first.

Each checklist uses the existing `- [ ] Title | locks: repo/path, repo/path`
format. The loader reads each preceding `Quest:` JSON paragraph for its stable
ID, dependencies and scope. Ordinary Markdown import ignores those paragraphs;
use the recipe loader to retain dependencies and assignment instructions.

Variables are required repository names consisting of letters, digits, hyphens
or underscores. Paths, whitespace and Markdown syntax are rejected. Adjust
recipe lock paths to the target repository layout before assigning work.
Security review quests require a different provider from their build quest;
the lead must enforce the assignments described in scope. They produce separate
reports after builds finish and do not substitute for board approval gates.
