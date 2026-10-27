# Concepts

The normative rules are in [PROTOCOL.md](../../PROTOCOL.md). This page explains them in plain words. Theme words are in brackets.

## Workspace (guild)

A folder with a `.board/` and the repos you work on. Each workspace is one tab in the UI. A global registry lists every workspace on your machine so leases can be checked across all of them.

## Repo identity

A lock is written `<repo>/<path>`. raidlock resolves `<repo>` to the repo's normalized remote URL, such as `github.com/acme-demo/acme-shop`. Two clones, a worktree, or the same repo in two workspaces all share one lock space. A repo with no remote is identified by its absolute path.

## Agent (raider)

One running worker: a Claude Code session or subagent, a Codex or Gemini run, a shell script, or a person. Each agent records its provider, runtime and model, so the UI and cost reports can tell them apart. `join` gives a worker its own local token. Claims return a capability scoped to that worker, card and fence. Lead writes, including marking work done, require an explicitly presented lead secret and a live lease bound to the current session and process. Naming the lead or possessing a worker token grants no lead rights. See [Install](install.md#lead-session-handover) for session handover.

### Names and models

Name agents `<runtime>-<role>-<quest>`, for example `claude-worker-checkout`. The UI shows `<name> · <Model> · <runtime>`. raidlock records the model from the first source that has one: the `--model` flag, then `RAIDLOCK_MODEL`, then the runtime itself (Codex `-m` or `~/.codex/config.toml`; Claude Code's stream-json `init` event or settings). It also records where the model came from (`model_source`), so you can tell a guess from a fact.

## Team (party)

Agents grouped by mission, with a cap on how many quests they work at once. Start with three to five workers; more usually costs more than it saves.

## Quest (task, card)

The only unit agents coordinate on. A quest has one owner, a lane, a scope in words, locks, dependencies, a verify step and an expected output. Lanes: `todo`, `doing`, `review`, `blocked`, `done`. Only `claim` puts a quest in `doing`. Only the lead moves it to `done`.

## Review (inspector)

A reviewer reads a quest in `review` and records a verdict: `approve`, `changes` or `comment`. Reviewers hold no lease and edit nothing. The board setting `require_review` sets how many approvals `raid done` needs. The recommended setting is one approval from a different provider than the owner, so a Claude-built change is checked by Codex and the other way round.

## Lease (raid lock)

Claiming a quest leases all its lock paths at once, or none. Paths are canonicalized first, so `repo/./src`, `REPO/src` on a case-insensitive disk and `repo\src` are the same path. Two paths conflict when one contains the other. A lease lasts 45 minutes (`ttl_minutes`) and is renewed by the owner's activity. In `review` and `blocked` it becomes an advisory hold for `hold_minutes` (default 60): its paths stay visible and overlapping claims print a warning, but it never authorizes writes. `raid reap` drops expired holds and keeps the quest's lane. The lease is released on `done`, `todo` or `release`.

## Fencing

Every change of owner (claim, take-over) gives the lease a new, higher fence number. The owner's hook, git guards and runner present that number on every write. If a worker stalls, loses its lease and comes back after someone else took over, its fence is old and its writes and commits are refused. The lead also checks the fence before merging a quest's branch.

## Transcript (raid log)

An append-only log of messages, notes, handbacks and runner events, from every provider. Records use A2A field names (`messageId`, `role`, `parts`, `taskId`, `contextId`), so the UI shows one conversation per quest. Messages about a busy worker's current quest are ready immediately; other messages queue until it leaves `doing`. The lead can send an urgent message, and lead notes on an owned card also notify its owner urgently. Authenticated hooks deliver urgent context at the next tool call; CLI workers can read their inbox.
