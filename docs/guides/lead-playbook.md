# Lead playbook

The lead plans, assigns, reviews and closes work. Every lead write presents `--lead-secret FILE` from the session that holds the lead lease. Keep that path in an unexported variable and never give it to a worker. Before you change sessions, read [Lead session handover](install.md#lead-session-handover).

## Board first

Every piece of work, yours and every subagent's, is a card before it starts. The Claude Code hooks enforce it by default:

- A lead edit inside a git repo is denied unless the lead holds a card whose locks cover the path (`require_card_for_lead_edits`). Create the card, claim it, then edit.
- A `Task` or `Agent` launch is denied unless its prompt or description contains `card <id>` for a card that is not done or cancelled (`board_first_subagents`).

Turn either off with `raid --lead-secret FILE config set <key> off`. The git commit guard is unaffected and keeps checking other agents' leases. Details: [Hooks](hooks.md#edit-enforcement-pre-write).

## Plan

- Split work by path, not by step. Two cards that touch the same files serialize behind one lease.
- Keep locks narrow: `acme-shop/src/checkout`, not `acme-shop`.
- Give every card an executable `--verify-cmd`. `--verify` describes the check in words for the worker.
- Say in `--output` what the handback should contain: files changed, test output, open questions.
- Read-only cards (audits, maps) may have no locks.
- Start with three to five workers. Parallel runs cost far more tokens than one chat, so add workers only for independent cards.
- Larger plans: `raid --lead-secret FILE plan import tasks.md`. See [Planning](planning.md).

## Assign

- `raid claimable` explains why each card can or cannot be claimed.
- Name workers `<runtime>-<role>-<card>`, for example `claude-worker-checkout`.
- Pick the model per card: a cheap one for audits, a strong one for design, another provider where it is better or cheaper.
- Workstream: start a session (or a `raid-worker` subagent with `card <id>` in its prompt) in its own clone or worktree; it claims with `raid claim CARD --checkout "$PWD"`.
- Sandboxed: `raid --lead-secret FILE delegate CARD --to codex|claude|gemini|shell` claims for the agent and runs it in a remote-less clone. See [Orchestration](orchestration.md#workstreams-and-sandboxed-agents).

## Watch

- Read the board, not worker output: `raid show`, `raid who`, `raid approvals`, or `raid ui --open`.
- A stalled agent has an expired lease or no heartbeat for 45 minutes. Message it, `raid --lead-secret FILE reap`, and let another worker `raid claim CARD --take-over`.
- `raid --lead-secret FILE msg --to WORKER --card CARD --urgent "..."` reaches a Claude Code worker at its next tool call. CLI workers read `raid inbox`.

## Review and close

- Read the notes and handback. Re-run the check with `verify`; do not trust "tests pass".
- Integrate the change. Land a delegated card with `raid --lead-secret FILE land CARD --cwd REPO`, then follow the next step it prints. Manual fallback: `git -C REPO apply --index PATCH`, commit, then `verify`.
- `raid --lead-secret FILE verify CARD [--cwd CHECKOUT]` records a signed receipt for the committed `HEAD`.
- With review required, wait for the approvals, or assign a reviewer: `raid --lead-secret FILE review CARD --assign REVIEWER`.
- Check the card's fence before merging its branch. A stale fence means someone else owns the card now.
- `raid --lead-secret FILE done CARD` closes the card and releases its lease.
- Merges, applies, tickets, wiki pages and settings are yours, with the human's approval. Never delegate them.

Editing, cancelling, protected-path exceptions and external closes: [Card maintenance](commands.md#card-maintenance).
