# Command reference

`raid` and `raidlock` are the same program. `raid` lists the everyday commands, `raid help advanced` the rest, and `raid <command> --help` the exact flags. This page lists every shipped command with the flags it takes beyond the common ones. Lead-only commands need `--lead-secret FILE` from the lead's session; hints and copy-ready commands include it.

## Common flags

Every command accepts these:

| Flag | Meaning |
|---|---|
| `--board PATH` | workspace or `.board` folder (default: the nearest `.board` above the current folder) |
| `--json` | machine-readable output |
| `--fields all` or `--fields a,b` | which fields `--json` returns (default: a minimal set) |
| `--agent NAME` | agent name (default: `RAIDLOCK_AGENT_ID` or `RAIDLOCK_AGENT`) |
| `--token TOKEN` | worker token from `join` (default: `RAIDLOCK_TOKEN`) |
| `--cap CAP` | card-scoped capability from `claim` (default: `RAIDLOCK_CAP`) |
| `--fence N` | card fence (default: `RAIDLOCK_FENCE`) |
| `--lead-secret SRC` | present the lead secret: a 0600 file path, `-` for stdin, or `keychain` |
| `--debug` | show tracebacks |

A token or capability can start with `-`. In scripts, write `--token="$TOKEN"` and `--cap="$CAP"` so the shell value is never parsed as a flag.

Exit codes: 0 ok, 2 denied or conflict, 3 precondition or usage error, 4 timeout or stale lead fence, 5 identity or role.

Runtime names accept a short form wherever a runtime is named: `claude` or `claude-code`, `codex` or `codex-cli`, `gemini` or `gemini-cli`.

## Start

| Command | What it does | Flags |
|---|---|---|
| `up` | Get going: create the board, join it, or show what you are on. | `--join`, `--card`, `--team`, `--role {worker,reviewer}`, `--runtime`, `--model` |
| `quickstart [PATH]` | New board with a `core` team and an example card; you become the lead. `--demo` also creates `acme-shop/`, a small git repo for the [getting-started guide](getting-started.md). | `--lead`, `--runtime`, `--team`, `--locks`, `--demo` |
| `init [PATH]` | Create an empty board. Prints the lead secret's file path, never the secret. | `--lead`, `--runtime`, `--model`, `--no-auth` |
| `doctor` | Check Python, the board, `RAIDLOCK_HOME`, the database and `PATH`. See [Operations](operations.md#doctor). | |
| `version` | Print the version. | |
| `install-commands` | Add a personal `/raid` skill to Claude Code. | `--uninstall` |
| `migrate` | Upgrade a legacy v1 board, or restore its backup. See [Operations](operations.md#migrate-a-legacy-board). | `--lead`, `--allow-auth-downgrade`, `--yes`, `--rollback` |

## Plan

| Command | What it does | Flags |
|---|---|---|
| `new TITLE` | Create a card. `--suggest-locks` previews lock paths without creating anything. | `--id`, `--team`, `--locks`, `--depends`, `--scope`, `--verify`, `--verify-cmd`, `--output`, `--ticket`, `--reviewers`, `--repo`, `--allow-protected`, `--suggest-locks` |
| `plan import FILE` | Import cards from a JSON or Markdown plan, or a recipe (`recipe:NAME`). | `--team`, `--var NAME=VALUE` |
| `recipes` | List built-in plan recipes. See [Planning](planning.md#recipes). | |
| `suggest-locks TEXT` | Rank narrow lock paths for a card description. Read-only. | `--repos REPO...`, `--limit N` |
| `foresight CARD` | Predict merge conflicts before claiming or committing. See [Foresight](foresight.md). | |
| `lock ID PATHS` | Add paths to a card's locks. | `--replace PATHS` (lead: replace all locks) |
| `unlock ID PATHS` | Lead: remove exact lock paths from a card. | |
| `team {add,rm,list} [NAME]` | Manage teams. | `--max`, `--mission`, `--repos` |
| `lore VERB [ARG]` | Lessons vault: `list`, `show`, `search`, `pending`, `propose`, `approve`, `reject`, `retire`, `distill`, `capture`, `for CARD`. See [Lore](lore.md). | `--paths`, `--card`, `--local`, `--budget`, `-n` |
| `budget [{set,show}] [KEY] [VALUE]` | Set or show delegation budgets. See [Operations](operations.md#delegation-budgets). | |
| `config [{get,set}] [KEY] [VALUE]` | Board settings: `auth`, `ttl_minutes`, `hold_minutes`, `require_review`, `require_review.count`, `require_review.other_provider`, `require_verify`, `require_card_for_lead_edits`, `board_first_subagents`, `brief_max_tokens`, `lead_store`, `pr.redact`, `pr.terms`, `pr.base`, `pr.remote`. | |

## Work

| Command | What it does | Flags |
|---|---|---|
| `join` | Join as an agent; prints its token once. | `--team`, `--role {lead,worker,reviewer}`, `--runtime`, `--provider`, `--model`, `--runner-model`, `--reported-model`, `--effort`, `--session`, `--card`, `--billing {api,plan,unknown}` |
| `claimable` | Cards you can claim, and why the others are blocked. | `--for-review` |
| `claim ID` | Take a card: lease its paths and start work. Prints a capability and fence. | `--take-over`, `--ttl`, `--for`, `--checkout` |
| `brief ID` | The card brief for an agent prompt, with its token estimate. | `--max-tokens`, `--quiet` |
| `heartbeat ID` | Renew your lease. | `--ttl` |
| `note ID TEXT` | Add a progress note; renews your lease. | |
| `move ID LANE` | Move a card to `review`, `blocked` or `todo`. | `--note` |
| `release ID` | Drop the card's lease; a `doing` card goes back to `todo`. | `--note` |
| `check [PATHS]` | Check paths for lock conflicts, or a file write with `--write FILE`. Exit 2 when denied. | `--id`, `--write` |
| `msg TEXT` | Send a message. `--urgent` (lead only) delivers at the worker's next tool call. | `--to`, `--from`, `--task`/`--card`, `--urgent` |
| `inbox` | Read your messages. | `--all`, `--wait [SECONDS]`, `--interval` |
| `cost [ID]` | Record a run's usage on a card, or show totals without an id. | `--model`, `--input`, `--cached-input`, `--cache-write`, `--output`, `--reasoning-output`, `--usd`, `--billing`, `--for`, `--duration-ms` |
| `wait ID` | Wait until a card leaves `doing`; exit 4 on timeout. | `--timeout`, `--interval` |

## Review and close

| Command | What it does | Flags |
|---|---|---|
| `review ID` | Record a verdict, or (lead) assign reviewers. | `--verdict {approve,changes,comment}`, `--note`, `--by`, `--assign` |
| `edit ID` | Lead: edit card fields and held locks; changes invalidate earlier receipts and approvals. | `--verify-cmd`, `--verify`, `--repo`, `--locks`, `--depends`, `--scope`, `--boss on|off`, `--allow-protected`, `--exclude-locks` |
| `verify ID` | Lead: run the card's `--verify-cmd` in a sandbox and record a signed receipt. See [Verification](verification.md). | `--timeout`, `--cwd` / `--checkout` |
| `land ID` | Lead: apply the newest handed-back delegated patch in a clean checkout of the card's repo, commit it, run `verify`, note the sha and result on the card in review, and print the next step. Refuses a dirty tree, a patch outside the card's locks or a HEAD that does not contain the run's base; a failed apply or commit leaves no commit. See [Orchestration](orchestration.md#sandboxed-agents). | `--cwd` / `--checkout` (default: current folder), `--message` / `-m`, `--timeout` |
| `done ID` | Lead: close a card after its verify and review gates pass. Warns when the card was never claimed or has no verify command. | `--note` |
| `close ID` | Lead: verify and record cross-provider approval, or close with external evidence without verification or review. | `--reviewer` or `--evidence` (HTTPS URL or SHA), required `--note`, `--cwd` / `--checkout`, `--timeout` |
| `cancel ID` | Lead: cancel without completion gates and release the lease. | `--reason` |
| `reopen ID` | Lead: move a done or cancelled card back to `todo`. | `--note` |
| `reap` | Lead: move cards whose `doing` lease expired to `blocked`, and drop expired review and blocked holds without changing their lane. | |
| `pr ID` | Lead: push the card's verified commit to a branch and open a pull request with `gh`. Dry run when `gh` is not authenticated. See [Pull requests](pr.md). | `--dry-run`, `--base`, `--remote`, `--branch`, `--redact`, `--redact-terms`, `--draft` |
| `pr-link ID REF` | Lead: link a card to an existing pull request (number or URL). | |
| `pr-sync [ID ...]` | Lead: poll linked pull requests; a merged card closes only through the verify and review gates. | `--min-interval`, `--force`, `--watch SECONDS`, `--max-polls` |

## Agents

| Command | What it does | Flags |
|---|---|---|
| `delegate CARD` | Lead: run a card with Codex, Claude Code, Gemini or a shell command in its own remote-less clone. | `--to {codex,claude,gemini,shell}`, `--as {worker,reviewer}`, `--sandbox {read-only,workspace-write}`, `--model`, `--cmd`, `--tools`, `--unsafe-verify`, `--raw-capture`, `--ref` / `--base`, `--timeout` |
| `lead {status,heartbeat,handover,takeover}` | Inspect or transfer the lead session lease. See [Install](install.md#lead-session-handover). | `--to SESSION` |
| `who` | Who is on the board and what they are doing. | `--brief` |
| `guard {install,uninstall,check}` | Git pre-commit and pre-push lease guards for the checkout in the current folder. See [Operations](operations.md#git-guards). | `--hook {pre-commit,pre-push}` |
| `scan SOURCE` | Check a patch file or clone for secrets, denied paths and large binaries. | `--card` (locks that authorize symlink targets), `--base` |
| `bridge [{show,pool}] [NAMES]` | Native host bridge for Agent Teams and Codex hooks: show its config, or set (lead) the comma-separated pool of joined workers that teammates auto-join as. See [Agent Teams and Codex hooks](native.md). | |
| `mcp` | Serve this board to MCP clients over stdio. See [MCP server](mcp.md). | `--workspace PATH` |

## Orchestrate

| Command | What it does | Flags |
|---|---|---|
| `next` | Suggest the next card for you and the provider to run it on, from the routing rules and plan headroom. Read-only. See [Orchestration](orchestration.md#raid-next-and-routing-rules). | `--provider {claude,codex,gemini,shell}`, `--for-review` |
| `routing [{show,set,explain}] [KEY] [VALUE]` | Show the routing rules, set one (lead; JSON value, `null` clears), or explain the route for a card. | |
| `resume CARD` | Build a token-capped resume packet for a card a dead or expired worker left, with its validated partial work. See [Orchestration](orchestration.md#raid-resume). | `--max-tokens`, `--context-fill PERCENT`, `--continue` |
| `duel CARD` | Lead: run a card with two providers and pick the winner on signed verify receipts. See [Orchestration](orchestration.md#raid-duel). | `--vs RUNTIME,RUNTIME`, `--model M1,M2`, `--cmd` (repeat per shell contestant), `--reviewer RUNTIME`, `--winner {a,b,AGENT}` |
| `resurrect [CARD]` | Lead: hand a stalled card to a fresh agent with its resume packet, within attempt and budget limits. See [Orchestration](orchestration.md#raid-resurrect). | `--to {codex,claude,gemini,shell}`, `--model`, `--cmd`, `--max-attempts`, `--budget USD`, `--dry-run`, `--sweep`, `--watch SECONDS` |
| `resurrect-policy [{show,set}] [KEY] [VALUE]` | Show or set automatic resurrection (lead; off by default; JSON value). | |
| `approvals` | Everything waiting for a human, with copy-ready commands. See [Orchestration](orchestration.md#raid-approvals). | `--kind {close,apply,review,duel,decision,lore}`, `--no-apply-check` |

## Watch

| Command | What it does | Flags |
|---|---|---|
| `show [ID]` | The board, or one card with its notes. | `--all`, `--brief` |
| `ui` | Serve the live board on `127.0.0.1`. See [Web UI](ui.md). | `--port`, `--only`, `--open`, `--host` (container image only) |
| `watch` | Live terminal board. | `--workspace NAME`, `--interval` |
| `notify` | Desktop notifications for review hand-ins, expiring leases, stalled agents and messages to the lead. | `--once` |
| `snapshot` | Export a self-contained HTML board. | `--out FILE`, `--redact` |
| `transcript` | The conversation log, newest first, filterable. | `--task`, `--kind`, `--who`, `-n`/`--tail` |
| `spend` | Spend by provider, model, agent, team, card, billing, workspace, day or week. | `--by`, `--since`, `--until`, `--all` |
| `limits` | Plan limits and context use per provider. See [Limits](limits.md). | `--install-statusline`, `--uninstall-statusline`, `--statusline`, `--ingest-claude` |
| `reputation` | Experimental trust scores and their recorded inputs. See [Web UI](ui.md#reputation-experimental). | `--by {agent,model,provider}` |
| `map` | Territory map: locked paths by repo, who holds each, and where they clash. See [Web UI](ui.md#map). | `--at SEQ` |
| `replay` | Board state rebuilt from the transcript at one record. See [Web UI](ui.md#timeline). | `--at SEQ` |
| `graph` | Card dependency graph and lock-contention heatmap. See [Web UI](ui.md#dependencies). | |
| `frames` | Context fill and burn rate per agent, with handoff warnings. See [Web UI](ui.md#context-and-burn-rate). | |
| `achievements` | Achievements earned from recorded events. See [Web UI](ui.md#achievements). | |
| `treasury` | Burn rate, month-end forecast and budget alerts. See [Web UI](ui.md#spend-forecast-and-budget-alerts). | `--all` |
| `tokens` | Estimated per-session token overhead of raidlock. | |
| `bench` | Measure idle and per-action overhead offline in a temporary board; exit 1 when a budget is exceeded. See [Overhead benchmark](benchmark.md). | `--runs N`, `--budget FILE`, `--plugin-root PATH`, `--tokenizer {estimate,auto}` |
| `workspaces` | Registered workspaces on this machine. | `--rm NAME...` |
| `help` | `raid help`, `raid help advanced`, `raid help COMMAND`. | |

### Managing lessons

Proposing, approving, rejecting and retiring lessons: [Lore](lore.md#managing-lessons).

### Card maintenance

- `raid new "Title" --repo REPO` names a workspace repository even without locks.
- `raid edit CARD --repo REPO --locks PATHS --depends IDS --scope "Work"` replaces
  the named fields. Only the lead can edit them; changes are recorded. Narrow a
  held directory lock to a file before assigning unrelated files to another card.
- `raid cancel CARD --reason "Superseded"` is lead-only, releases its lease and
  marks it Cancelled without verification or review gates. Cancelled work is excluded
  from reputation metrics. `raid reopen CARD` returns it to To do.
- `raid verify CARD --cwd /clean/checkout` verifies that checkout's committed state;
  `--checkout` is an alias. The checkout must belong to the card's repository.
- `raid delegate CARD --to codex --ref integration --timeout 3600` starts from a
  branch or commit (`--base` is an alias for `--ref`) and stops a hung worker after
  the timeout. Codex receives its prompt as an argument with stdin closed.
- `raid new "Workflow fix" --locks REPO/.github/workflows --allow-protected .github/workflows`
  records a lead-approved exception. `raid edit CARD --allow-protected PATHS` replaces
  exceptions. Only `.github/workflows` and `.claude` can be allowed; `.board` stays
  denied. Exceptions appear on the card and require cross-provider review. Secret
  scanning and lock checks still apply.
- `raid close CARD --reviewer REVIEWER --note "Review evidence" --cwd /clean/checkout`
  verifies, records approval using the existing reviewer credential, and closes the
  card. The reviewer must use another provider. Re-joining points to an existing
  credential when no new token is issued.

`raid claim CARD --for AGENT` lets the lead register and claim for a worker in one step.

`raid edit DIRECTORY_CARD --exclude-locks REPO/docs/one.md` lets the lead hand one
file to another card while retaining the directory reservation. The exclusion is
recorded and shown on the card. The directory holder loses write access to the file;
the other card claims it normally. `--exclude-locks ""` clears exclusions, provided
no other live lease conflicts. Delegated patches and symlink targets respect exclusions.

`raid close CARD --evidence https://github.com/org/repo/pull/123 --note "Merged externally"`
closes a todo, doing, review or blocked card as Done and releases its lease. A commit SHA also works when it exists in the card's repo (stored as `commit:<full sha>`). The HTTPS
URL and required note are recorded in card notes and the transcript; evidence also
appears in `raid show`, JSON and web data. This does not count as a verified pass.
`--evidence` and `--reviewer` are mutually exclusive.
