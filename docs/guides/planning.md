# Planning quests

Good quests are small, have narrow locks and carry a verify command. These tools help the lead write them.

## Create one quest

```bash
raid --lead-secret "$LEAD_SECRET_FILE" new "Validate cart at checkout" --id checkout --team core \
  --scope "Reject empty carts at POST /checkout" \
  --locks "acme-shop/src/checkout,acme-shop/tests/checkout" \
  --verify "Checkout tests pass" --verify-cmd "uv run pytest tests/checkout"
```

On macOS, `raid verify` runs a `uv run` command offline; warm its cache once as shown in [Verification](verification.md#the-verify-gate).

`--depends a,b` holds the quest until those quests are done, `--reviewers` names reviewers up front and `--ticket` links an issue. See [Command reference](commands.md#plan) for every flag.

## Suggest locks

```bash
raid suggest-locks "Fix rounding in src/cart/discount.py"
raid suggest-locks "Fix rounding in the discount code" --repos acme-shop --limit 3
raid new "Fix discount rounding" --suggest-locks
```

`suggest-locks` ranks narrow `<repo>/<path>` locks for a quest description. Paths named in the text come first, then files that often change together with them in recent Git history, then files that share CODEOWNERS owners. Commits touching more than 50 paths, protected files and symlinks are ignored, and a suggested directory never covers tracked files the evidence did not point at. It only reads: nothing is created. `raid new ... --suggest-locks` prints the same preview instead of creating the card; review the paths, then create the card with explicit `--locks`.

## Import a plan

```bash
raid --lead-secret "$LEAD_SECRET_FILE" plan import plan.md --team core
raid --lead-secret "$LEAD_SECRET_FILE" plan import plan.json
```

A Markdown plan is a checklist, one quest per item:

```markdown
- [ ] Reject empty carts | locks: acme-shop/src/cart, acme-shop/tests/test_cart.py
- [ ] Document local setup | locks: acme-shop/README.md
```

When the file has checkboxes, only unchecked `- [ ]` items become cards; checked items and plain bullets are skipped. Without checkboxes, every bullet is a card.

### From a spec tool

Spec tools such as OpenSpec and GitHub Spec Kit decide what to build and write a `tasks.md`; raidlock runs those tasks. Import the file, or the change folder that holds it, as it is:

```bash
raid --lead-secret "$LEAD_SECRET_FILE" plan import openspec/changes/add-cart --team core
raid --lead-secret "$LEAD_SECRET_FILE" plan import specs/001-orders/tasks.md --team core
```

Task numbers such as `1.2` or `T001` move to the card's ticket, and markers such as `[P]` or `[US1]` are dropped from the title. A task with no locks gets the file paths it names as locks (`Create the model in src/models/user.py` locks `src/models/user.py`) when the board maps each path to one repo; paths outside the workspace or in more than one possible repo are left out, and the import says which cards got inferred locks. Add `| locks: <repo>/<path>` to a task to set its locks on import, or set them afterwards with `raid --lead-secret "$LEAD_SECRET_FILE" edit CARD --locks <repo>/<path>`. An import is all or nothing: if any task's card id already exists, nothing is created, so import later tasks from a file that holds only the new ones.

A JSON plan is a list of card objects, or `{"cards": [...]}`, with the same fields as `raid new` (`title`, `id`, `team`, `locks`, `depends`, `scope`, `verify`, `verify_cmd` and so on). `--team` applies to items that name no team.

## Recipes

```bash
raid recipes
raid --lead-secret "$LEAD_SECRET_FILE" plan import recipe:docs-refresh --var repo=acme-shop
raid --lead-secret "$LEAD_SECRET_FILE" plan import recipe:multi-repo-refactor --var repo=acme-shop --var other_repo=pocket-notes
```

Built-in plans for common jobs:

| Recipe | Use it for |
|---|---|
| `docs-refresh` | documentation that drifted from current behavior |
| `multi-repo-refactor` | a shared contract change across two repos; the producer stays backward compatible until the consumer migrates |
| `security-audit` | dependencies, authentication and input handling, each with one build quest and one independent review quest on another provider |
| `upgrade-deps` | dependency upgrades that need compatibility checks before release |

Recipe quests have stable ids and dependencies. Variables are repository names (letters, digits, hyphens and underscores); paths and Markdown are rejected. Adjust the lock paths to your repo layout before you assign the quests. Importing the same recipe twice into one board needs the first set resolved first, since the ids repeat. Recipe review quests are separate reports; they do not replace the board's [review gate](verification.md#the-review-gate).

## Before claiming

- [Foresight](foresight.md) predicts merge conflicts with work held on other leases of the same repo, across workspaces. Claims run it automatically.
- [Lore](lore.md) attaches approved lessons from earlier quests to a card's brief, matched by lock paths.
- `raid brief ID` prints the exact brief a worker gets, with a token estimate. `raid claimable` shows what is ready and why the rest is blocked.
