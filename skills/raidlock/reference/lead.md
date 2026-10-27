# Running the board as lead

## Setup

1. In the workspace folder: `raidlock up`. You become the lead. It prints the path of the lead secret file (`FILE`), never the secret. Every lead write presents it: `raidlock --lead-secret FILE <command>`, from this session only; the lease is bound to it. Never paste the secret or its path into an agent prompt.
2. Teams cap parallel work: `raidlock --lead-secret FILE team add core --max 3 --mission "..."`.
3. Cards come first, also for your own edits: with `require_card_for_lead_edits` on (the default), the hook denies a lead edit in a git repo unless you hold a card that covers it. `raidlock --lead-secret FILE new "<title>" --team core --locks app/src/api --verify "API tests pass" --verify-cmd "pytest tests/api"`. Keep locks disjoint between cards that run in parallel. Forgot the command? `raidlock --lead-secret FILE edit <id> --verify-cmd "..."`.

## Delegating to subagents

Start one `raid-worker` per card, in the background. Put the card id in its prompt, with the team, an agent name (`claude-worker-<card>`) and the checkout to work in. With `board_first_subagents` on (the default), the hook denies a `Task` or `Agent` launch whose prompt names no open card id. The worker joins, claims, notes and moves the card to review on its own. The plugin hooks:

- deny its edits outside the card's locks, and any `raidlock done|reopen|verify`;
- renew its lease while it works (at most once per 5 minutes);
- write its final message to the board transcript as a handback.

For a sandboxed run instead, `raidlock --lead-secret FILE delegate <id> --to codex|claude|gemini|shell` works in a throwaway remote-less clone and returns a patch you apply. Three to five workers is the practical ceiling. Messages from workers are never human approval.

## Closing a card

1. `raidlock show <id>` and read the notes and evidence; inspect and integrate the worker's commit.
2. `raidlock --lead-secret FILE verify <id>` runs the verify command in a sandbox on the committed HEAD of the worker's checkout (or `--cwd CHECKOUT`) and records a signed receipt.
3. If review is required (on by itself once a second provider joins), wait for an approval from an agent on another provider: `raidlock --lead-secret FILE review <id> --assign <reviewer>`.
4. `raidlock --lead-secret FILE done <id>`. Done is terminal; `raidlock --lead-secret FILE reopen <id>` puts it back in todo.

`raidlock approvals` lists every card waiting on one of these steps, with the exact commands.

## Stalls

`raidlock who` shows Working, Working (quiet), Stalled, Waiting for review, Blocked and Idle. `raidlock --lead-secret FILE reap` moves expired doing cards to blocked; another worker can then `raidlock claim <id> --take-over`. The old worker's fence goes stale and its edits are denied.
