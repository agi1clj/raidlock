# Orchestration

How one lead runs many agents across sessions, projects and providers, and the routing and recovery commands on top of `raid delegate`.

## How coordination works

1. The lead creates cards with narrow, disjoint `--locks` and a `--verify-cmd`.
2. Each worker claims one card from its own checkout. The claim leases every lock path at once, or none.
3. Workers post `raid note` evidence and read `raid inbox`; the lead sends `raid --lead-secret FILE msg --to WORKER --card CARD "..."`.
4. A worker hands back with `raid move CARD review --note "<evidence>"`.
5. The lead integrates the change, runs `verify` (signed receipt on the commit) and `done`.

Many projects. Each workspace has its own `.board/`. Every board registers in one index under `RAIDLOCK_HOME` (default `~/.raidlock`), and every claim checks leases in all of them, so a path locked from one workspace is locked everywhere. `raid workspaces` lists them, and `raid ui` shows them all on the realm page, with tabs for Guilds, Tavern chatter and Treasury.

Many sessions. Separate Claude Code, Codex or human sessions join one board with `raid up --join`, each in its own clone or worktree (see [Workstreams](#workstreams)).

Mixed providers. When agents from a second provider join, raidlock turns on `require_verify` and a one-approval, other-provider review gate, unless you set those values yourself. See [Verification](verification.md#settings-and-defaults).

Board first. The Claude Code hooks deny edits outside a card the agent holds, including the lead's edits inside a git repo (`require_card_for_lead_edits`), and deny a `Task` or `Agent` launch whose prompt has no `card <id>` for an open card (`board_first_subagents`). Both are on by default; turn either off with `raid --lead-secret FILE config set <key> off`. See [Hooks](hooks.md#board-first-subagents-pre-urgent).

## Workstreams and sandboxed agents

There are two ways to put an agent on a card. Use a workstream for a session you watch, and a sandboxed agent for unattended or less trusted runs.

### Workstreams

A workstream is a session you run yourself: Claude Code, Codex or you. Give each one its own clone or worktree, join, and claim with the checkout recorded on the lease:

```bash
git -C ~/code/shop/storefront worktree add ../storefront-checkout -b checkout   # or: git clone <remote> ...
cd ~/code/shop/storefront-checkout
raid up --join --agent claude-worker-checkout --card checkout
raid claim checkout --checkout "$PWD"
```

Leases map through the git remote, so every clone and worktree of a repo shares one lock space, across workspaces too. A repo with no remote is identified by its path, so its clones do not share locks. The lease, the Claude Code hooks and the git guards apply in every checkout. Worktrees share the repository's git config and hooks; use a separate clone when you want them apart.

### Sandboxed agents

`raid delegate CARD --to codex|claude|gemini|shell` claims the card for the agent and runs its CLI in a throwaway clone with no remote, committed files only and a cleared environment. The clone shares git objects, so it costs only the checkout. The agent runs with its own sandbox mode (`--sandbox workspace-write` for workers, `read-only` for reviewers) and never gets your push credentials or git hooks. Afterwards raidlock rejects any change outside the card's locks or anything that looks like a secret, and hands back a patch.

The lead lands the patch, verifies that commit (the verify command runs under bubblewrap or `sandbox-exec`) and closes the card:

```bash
raid --lead-secret "$LEAD_SECRET_FILE" delegate checkout --to codex
git -C ~/code/shop/storefront apply --index ~/.raidlock/runs/shop/checkout/1/changes.patch
git -C ~/code/shop/storefront commit -m "Reject empty carts"
raid --lead-secret "$LEAD_SECRET_FILE" verify checkout --cwd ~/code/shop/storefront
raid --lead-secret "$LEAD_SECRET_FILE" done checkout
```

The handback prints the patch path. Once a second provider has joined, `done` also needs an approval from a reviewer on another provider, for example `raid --lead-secret "$LEAD_SECRET_FILE" delegate checkout --to claude --as reviewer`. On `done`, `close` or `cancel`, raidlock deletes the card's clones and keeps the logs and patch under `~/.raidlock/runs/`.

## Routing and recovery commands

These five commands sit on top of the runner (`raid delegate`). None of them weakens a gate: every run still goes
through a claim with its own fence and lease, the runner's path policy and secret scan, verification receipts, review
and `raid done`, which only the lead runs. Examples assume `LEAD_SECRET_FILE` holds the path `raid quickstart`
printed, as in [Claude Code plus Codex](multi-provider.md).

| Command | Who | Changes the board |
|---|---|---|
| `raid next` | anyone | no |
| `raid routing show / explain` | anyone | no |
| `raid routing set` | lead | routing rules only |
| `raid duel` | lead | yes |
| `raid resume` | lead, or the card owner with its fence | no (writes a packet under `$RAIDLOCK_HOME`) |
| `raid resurrect`, `raid resurrect-policy set` | lead | yes |
| `raid approvals` | anyone | no |

## raid next and routing rules

`raid next` suggests the next card for the caller and the provider and model to run it on. It only reads: it never
claims, delegates or touches a lease, so the card still has to pass `raid claim` or `raid delegate` like any other.

```bash
raid next                        # a worker: the next card for its team, routed to its own runtime
raid --lead-secret "$LEAD_SECRET_FILE" next                # the lead: next card and the best provider for it
raid --lead-secret "$LEAD_SECRET_FILE" next --provider codex
raid --lead-secret "$LEAD_SECRET_FILE" next --for-review   # a review card, routed away from the owner's provider
```

Candidates come from `raid claimable`, so dependencies, locks held anywhere, team limits and lanes all apply. Cards
in todo come before blocked ones, oldest first.

### Rules

Rules live in `.board/routing.json`; only the lead writes them. The first rule that matches a card wins. A rule
matches on a label, a lock path (a prefix or a glob) or a team, and names a provider, an optional model and
fallback providers.

```bash
raid --lead-secret "$LEAD_SECRET_FILE" routing set rules '[
  {"name": "docs", "label": "docs", "provider": "codex", "model": "small-model", "fallback": ["claude"]},
  {"name": "security", "label": "security", "provider": "claude", "model": "large-model"},
  {"name": "web", "path": "acme-shop/web/**", "provider": "claude", "fallback": ["codex"]}
]'
raid --lead-secret "$LEAD_SECRET_FILE" routing set label.fix-login '["security"]'
raid --lead-secret "$LEAD_SECRET_FILE" routing set default '{"provider": "", "fallback": ["claude", "codex"]}'
raid routing explain fix-login
```

Providers are runtimes: `claude`, `codex`, `gemini`, `shell`. The rule's model applies to its own provider only; a
fallback runs with that provider's default model. Labels are routing data, so they are set with
`routing set label.<card>` and do not change the card. Without a rule or default provider, the provider with the
most 5h headroom wins.

### Headroom

Headroom comes from `raid limits` (Codex session logs and the Claude statusline snapshot, see
[provider plan limits](operations.md#provider-plan-limits)). A provider gets no new work when:

- its weekly window is at or above `weekly_max` (default 90%),
- its 5h window is at or above `five_hour_max` (default 95%); this is transient, `retry_after` says when it resets,
- the provider reports its limit as reached (exhausted, not transient),
- its limits are unknown or stale. Unknown counts as no headroom.

When the primary provider has no headroom, the rule's fallbacks are tried in order and the result says
`fallback: true`. Fallback never changes credentials: each runtime keeps whatever login or key its own CLI uses, so a
plan login is never silently swapped for a paid API key. Mark a provider that has no plan quota with
`routing set quota.<provider> '"none"'` (the shell runtime is `none` by default) and `'"plan"'` to put it back.

## raid duel

A duel runs one card with two providers and lets the lead pick the winner from evidence.

```bash
raid --lead-secret "$LEAD_SECRET_FILE" duel fix-login --vs claude,codex --reviewer gemini
raid duel fix-login                                   # the comparison table again
raid --lead-secret "$LEAD_SECRET_FILE" duel fix-login --winner a
```

`--model m1,m2` sets one model per side; `--cmd` (once per shell side) sets the command for `shell` contestants.
Two identical contestants are refused. The card must be in todo and must have a verify command, and
the host needs a verification sandbox: without one no contestant gets a receipt and every `--winner` is refused.

How it runs:

1. The card holds one lease at a time, so the contestants run one after the other, each through the normal runner
   in its own clone, as its own agent (`duel-a-...`, `duel-b-...`) with its own claim and fence. Between runs the
   lead parks the card in todo, which makes the first contestant's fence stale.
2. Each accepted patch is committed in a lead-owned clone at the run's base commit, and `raid verify` runs there.
   That gives a signed receipt for that exact commit.
3. With `--reviewer`, a reviewer of that runtime reviews each contestant's patch while it is the newest one.
4. The last contestant's card is parked in blocked so nobody claims it while the lead decides. The record goes to
   `$RAIDLOCK_HOME/duels/<workspace>/<card>/duel.json`, and a `duel` artifact goes to the transcript.

The table compares the receipt result, the review verdict, files and bytes changed, run time and cost. Both runs
record their cost on the card, so both count toward [budgets](operations.md#delegation-budgets); when the budget
runs out after the first contestant, the duel stops as `incomplete` and the card goes back to todo.

`--winner` accepts `a`, `b` or the contestant's agent name. It is refused unless that contestant has a receipt that
is on the card, carries this board's signature, matches the card's current verify command, passed, and names the
commit its clone still has checked out. Self-reported handbacks and edits to `duel.json` count for nothing. On a
valid pick the loser's patch and clone are deleted, the card is claimed for the winner with its clone as the
checkout, moved to review and verified again, so `raid done` sees a receipt for the current commit.
The reclaim starts a new card revision, so reviews from the duel no longer count toward `require_review`; the
winner gets a normal review before `raid done`. A pick is refused when someone outside the duel holds the card.

## raid resume

A resume packet is what a fresh agent needs to carry on a card whose worker died, ran out of credit or let its lease
expire.

```bash
raid --lead-secret "$LEAD_SECRET_FILE" resume fix-login
raid --lead-secret "$LEAD_SECRET_FILE" resume fix-login --max-tokens 2000 --json
```

It contains, in priority order: a header (lane, previous owner, base commit, changed paths, problems), the card brief,
the latest notes, open questions from the last handback and the checks to run, the work so far as a patch, and the
transcript tail. The budget defaults to three times `brief_max_tokens`; sections are cut from the bottom to fit.

The work so far comes from the newest runner run of the card: its accepted patch, or, when the run never finished,
its clone, read with tracked and untracked files. For a worker in its own session it comes from the checkout recorded
on the lease. Files are hashed into a lead-owned object store against the recorded base commit; the worker's `.git`
(refs, config, hooks, commits) is never trusted. A patch that leaves the card's locks, touches a denied path or trips
the secret scan is reported as rejected and is not written. Everything is redacted, including the lead secret and the
worker tokens. Packets are saved under `$RAIDLOCK_HOME/resume/<workspace>/<card>/<n>/`.

Only the lead or the card's owner, with its current fence, can read a packet.

### Context handoff

A worker can ask whether to keep going:

```bash
raid resume fix-login --context-fill 62 --continue     # ok
raid resume fix-login --context-fill 80 --continue     # refused: hand off to a fresh agent
```

At 80% context fill or more, `--continue` is refused. Without `--continue`, the packet is built and marked
`handoff: true`.

## raid resurrect

Resurrect hands a stalled card to a fresh agent with its resume packet.

```bash
raid --lead-secret "$LEAD_SECRET_FILE" resurrect fix-login --dry-run
raid --lead-secret "$LEAD_SECRET_FILE" resurrect fix-login --to codex
raid --lead-secret "$LEAD_SECRET_FILE" resurrect-policy show
```

Checks run in this order, and any failure leaves the card as it was:

1. **Attempts.** At `max_attempts` (default 2, override per call with `--max-attempts`) it refuses.
2. **Budget.** The card's spend against `budget_usd` (or `--budget`) and every delegation budget.
3. **State.** A card in doing with a live lease is refused: there is never a second worker. Review, done and todo
   cards are not stalled, and a card in an undecided duel is left to the lead. A recent attempt within `cooldown_minutes` (default 10) is refused.
4. **Evidence.** The reason comes from runner records only: a run that failed on a quota or credit error, a
   transient rate limit, a timeout, a lost lease, a runner that stopped mid-run, or a reaped lease. Task text never
   counts as evidence.
5. **Provider.** `--to`, else the policy's `providers` list, else the provider that failed. After a quota failure
   the failed provider is skipped unless `--to` names it. Each candidate needs headroom (see above). With no candidate the card stays
   blocked with a `resurrect stuck` note.

Then it builds the resume packet, moves the card to todo (the old lease and fence are gone), records the attempt
and delegates to a new agent named `res<n>-<runtime>-<card>`. The runner applies the packet's validated patch to the
fresh clone at the recorded base, uncommitted, so the new handback holds both the old and the new work, and adds the
packet text to the prompt as quoted, untrusted data. The worker's environment loses every `RAIDLOCK_*` variable and
the runner refuses one that still holds lead material, so the lead secret never reaches the replacement. The new run
goes through verify and review like any other; resurrect never marks a card done.

Each attempt is an `ALERT RESURRECT` record in the transcript and a row in `.board/resurrect.json` with the old and
new agent and provider, the reason, the packet, the outcome and the cost.

### Automatic resurrection

It is off by default.

```bash
raid --lead-secret "$LEAD_SECRET_FILE" resurrect-policy set providers '["codex", "claude"]'
raid --lead-secret "$LEAD_SECRET_FILE" resurrect-policy set enabled true
raid --lead-secret "$LEAD_SECRET_FILE" resurrect --sweep --watch 300
```

A sweep reaps expired leases, then resurrects blocked cards whose evidence is a quota or rate-limit failure, a
timeout, a lost lease, a stopped runner or an expired lease. Cards the agent itself reported blocked, failed verify
or broke the path policy wait for the lead. A second sweep does nothing for a card that already moved on, so a
restarted supervisor is safe.

The supervisor needs the current lead lease: run it in its own session and hand it the lead with
`raid lead handover --to <supervisor session>`. A lead that is paused or dead cannot arrange its own replacement,
because every sweep checks the lead secret and the lead fence. When the lease is gone the sweep stops with an
identity error and stalled cards stay blocked until a lead takes over.

## raid approvals

One inbox for everything that waits for a human, each item with commands to copy.

```bash
raid approvals
raid approvals --kind close --json
```

| Kind | When | Commands |
|---|---|---|
| `close` | a review card passes the done gate | `raid --lead-secret FILE done <card>` |
| `apply` | a done card's runner patch still applies to its repo | `git -C <repo> apply --index <patch>` |
| `review` | a review card lacks approvals or a valid receipt | `raid --lead-secret FILE verify` (or `edit --verify-cmd` when the card has none), `raid --lead-secret FILE delegate ... --as reviewer`, `raid review --verdict approve` for a reviewer agent |
| `duel` | a duel waits for the lead | `raid --lead-secret FILE duel <card> --winner <side>` for verified sides |
| `decision` | a card is blocked | `raid show`, `raid --lead-secret FILE resume`, `raid --lead-secret FILE resurrect --dry-run`, `raid claim --take-over` for another worker |
| `lore` | a lesson waits for approval | `raid --lead-secret FILE lore approve <id>` |

Items come from board state on each read, so an item disappears as soon as its cause is gone. Commands use card ids,
which are restricted to `[a-z0-9-]`, and quoted paths, so card titles never end up in a command. The apply check runs
`git apply --check` for the ten most recent done cards; skip it with `--no-apply-check`. Lead commands carry
`--lead-secret` with the path of this board's lead secret file (or `keychain`), so they run as copied in the lead's
session; the path is not the secret, but never give it to an agent.

The web UI shows the same items in **Needs you** (Raid leader's call), with a copy button per command. It is read-only
like the rest of the UI. The web board never shows private paths, so its lead commands use `--lead-secret
"$LEAD_SECRET_FILE"`; set that variable in the lead's shell as in [Getting started](getting-started.md).
