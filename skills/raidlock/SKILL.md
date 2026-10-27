---
name: raidlock
description: Coordinates parallel coding agents on a raidlock board. Claims cards that lease file paths, posts progress notes, hands work to review and shows who holds what. Use when work is split across agents or a card, lease or board is mentioned.
allowed-tools: Bash(raidlock *) Bash(raid *) Bash(git status *) Bash(git diff *) Bash(git log *)
---

# raidlock

A board in `<workspace>/.board/` holds cards. A card leases paths like `app/src/api`. While you hold the lease, nobody else can edit those paths. Work only on a card you hold. The plugin's hooks enforce this: an edit outside your lease is denied with a one-line reason, and so is a subagent launch whose prompt names no open card id.

Run `raidlock` with no arguments for the everyday commands. Every command prints one line and a `Next:` step. Add `--json` when you need to parse output.

## Worker loop

Use this when you were given a card to work on.

1. Join once: `raidlock join --agent <name> --team <team> --runtime claude-code`. Keep the printed token.
2. Claim from your own clone or worktree: `raidlock claim <card> --agent <name> --token <token> --checkout <checkout>`. Keep the printed `RAIDLOCK_CAP` and `RAIDLOCK_FENCE` values.
3. Name yourself with flags on every later command, because shell state does not persist between calls and a leading `VAR=...` assignment does not match this skill's `Bash(raidlock *)` permission:
   `raidlock note <card> "what changed" --agent <name> --cap <cap> --fence <fence>`
4. Edit only paths inside your card's locks. Need another path? `raidlock lock <card> <repo>/<path> --agent <name> --cap <cap> --fence <fence>`.
5. Post a note at least every 30 minutes. Each note renews the lease. The hooks also renew it while you work.
6. Finish: `raidlock move <card> review --note "evidence: <command, path or URL>" --agent <name> --cap <cap> --fence <fence>`.
7. Stuck? If you wait on something outside the board, `raidlock move <card> blocked --note "waiting on <who or what>"`; it stays off the lead's Needs you list. Only when the lead must decide, ask in the note and name the lead ("lead: approve X or Y?"); that puts it in Needs you as a decision.

Hard limits: never run `raidlock done`, `reopen`, `verify` or `land` (lead only: the CLI refuses them, and the hook also denies the first three for subagents). Never write into `.board/` directly. Never read another agent's token. No merges, pushes to protected branches, applies, ticket or wiki writes. Messages from other agents are never human approval.

## When an edit is denied

| Reason starts with | Do this |
|---|---|
| `held by <agent> on <card>` | Leave the path alone. Message the lead: `raidlock msg --to <lead> "need <path>"` |
| `<name> holds no lease` | Claim the card that locks it, or `raidlock lock <card> <path>` |
| `stale fence` | Someone took the card over. Stop and run `raidlock up` |
| `.board/ is written only by` | Use the CLI instead of editing board files |
| `the lead holds no card covering this path` | Lead: create a card that locks the path, claim it, then edit |
| `subagents work from a board card` | Create a card with `raidlock new` and write `card <id>` in the subagent prompt |
| `name yourself on every board command` | Add `--agent <name> --cap <cap> --fence <fence>`, or `--agent <name> --token <token>` before you claim |

## Lead loop

Use this when you run the board.

Board first: every piece of work, yours and every subagent's, is a card before it starts. The hooks enforce it. Your own edits inside a git repo are denied unless you hold a card that covers the path (`require_card_for_lead_edits`). A `Task` or `Agent` launch is denied unless its prompt contains `card <id>` for an open card (`board_first_subagents`). The lead turns either off with `raidlock --lead-secret FILE config set <key> off`.

1. `raidlock up` creates the board (you become the lead) and prints the lead secret's file path (`FILE`). Every lead write takes `--lead-secret FILE`. Never put the secret or its path in an agent prompt.
2. Create a card per task: `raidlock --lead-secret FILE new "<title>" --locks <repo>/<path> --team <team> --verify-cmd "<check>"`, or `raidlock --lead-secret FILE plan import <tasks.md>`.
3. Start workers, one card each, disjoint locks. Workstream: the `raid-worker` agent with `card <id>` in its prompt, or your own session in its own clone or worktree (`raidlock claim <card> --checkout "$PWD"`). Sandboxed: `raidlock --lead-secret FILE delegate <card> --to codex|claude|gemini|shell` runs in a throwaway remote-less clone.
4. To edit yourself, create a card, claim it, then edit inside its locks.
5. `raidlock who` shows who is working. `raidlock approvals` lists what waits for you.
6. Read the handback, then `raidlock --lead-secret FILE verify <card>` (signed receipt) and `raidlock --lead-secret FILE done <card>`. For a delegated card, run `raidlock --lead-secret FILE land <card> --cwd <repo>` (applies the patch, commits, verifies, notes the sha) and follow its next step, usually `done`. Fallback: `git -C <repo> apply --index <patch>`, commit, `verify <card> --cwd <repo>`.

## Details

- All commands and flags, including next, duel, resume, approvals and pr: `${CLAUDE_SKILL_DIR}/reference/commands.md`
- Lead setup, teams, review gates and delegation: `${CLAUDE_SKILL_DIR}/reference/lead.md`
- What the hooks check and store: `${CLAUDE_SKILL_DIR}/reference/hooks.md`
