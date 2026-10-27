# Operations

Keeping a raidlock install healthy: checks, upgrades, lead sessions, budgets, quotas and patch safety.

## Doctor

```bash
raid doctor
raid doctor --json
```

Checks, in order:

| Check | Fails when |
|---|---|
| `python` | Python is older than 3.11 |
| `board` | `--board` or `RAIDLOCK_BOARD` names a folder that is not a board. A legacy v1 board is a warning, never changed. |
| `home` | `RAIDLOCK_HOME` (default `~/.raidlock`) is not writable |
| `database` | the workspace index fails SQLite's `quick_check`. Doctor checks a copy, so it never locks the live index. |
| `storage` | never fails; shows how much disk the run clones and the verify cache use |
| `path` | another program named `raid` shadows this install on `PATH`; `raidlock` keeps working either way |

It prints `Healthy.` and exits 0, or lists the problems and exits 3.

### Disk use

raidlock cleans up after itself. `done`, `close`, `cancel` and `pr-sync` delete a card's delegated clones and agent homes and keep only its logs, patches and run records. The verify cache keeps the 8 most recent checkouts. Clones of cards still open stay until the card closes; cancel abandoned cards to free them.

An `update` line reports whether a newer raidlock release exists; see [Upgrade](install.md#upgrade).

## Lead session handover

The lead lease binds the lead secret to one runtime session, an ancestor process and its start time. To move the lead to another session, run this from the current lead session:

```bash
raid --lead-secret "$LEAD_SECRET_FILE" lead handover --to "destination-session-id"
```

The receiving session presents the same secret file path; the handover rotated the secret inside it, and the new session binds on its first authenticated command. `raid lead status` and `raid who` show the current session and fence. After a crash, when the lease has expired, `raid --lead-secret "$LEAD_SECRET_FILE" lead takeover` recovers it, rotates the secret and fence, and records an alert. A live lead cannot be taken over. Details and the ready-made lead prompts are in [Install](install.md#lead-session-handover).

Session ids come from `RAIDLOCK_SESSION`, then the runtime's own variable (`CLAUDE_CODE_SESSION_ID`, `CODEX_SESSION_ID` and similar). The [demo](../../examples/demo/README.md) hands over between two scripted sessions.

## Migrate a legacy board

Legacy v1 `.board` directories are never migrated automatically. Commands that need schema 2 exit with code 3 and a `raid migrate` hint; `raid doctor` reports `legacy v1 board found (not migrated)` as a warning and leaves the board untouched.

Stop agents and older tools first. Then:

```bash
raid migrate --board PATH          # print the changes and confirm interactively
raid migrate --board PATH --yes    # for automation
```

Migration prints the selected lead, the private secret path and a takeover command. It issues exactly one lead secret. If a v1 agent named `lead` exists, it is preferred; otherwise, multiple lead-role agents require `--lead <name>`. Other imported agents receive no tokens.

After migration, run the printed command from the intended lead session:

```sh
raid --board PATH lead takeover --lead-secret /path/printed/by/migrate
```

Takeover binds the lead lease to that session and rotates the secret in the same file. Use that file for subsequent lead commands. An unidentified `raid up` on a migrated board exits without joining; use `raid up --join` only when you intend to join as a worker.

Without confirmation nothing changes. Migration converts cards, inbox messages, agents, teams and leases to schema 2 and creates `board.json`. It first copies the complete original board to a timestamped sibling, `.board.backup-<UTC timestamp>`. In git workspaces, migration adds `/.board.backup-*` to the workspace `.gitignore`. Keep that backup while you check the upgrade.

Every `LOG.md` line becomes a transcript record in its original order with source `v1-log`; the original file stays byte-identical. Imported records are marked untrusted. Parsed events retain their claimed timestamp, actor, verb and card; text is redacted before storage. Lines without those fields retain redacted text with empty metadata. Free-text card bodies are stored as descriptions alongside timestamped notes.

Compatible settings and team caps are preserved; settings without a v2 equivalent are listed in the result and retained in the backup.

Cards keep their lanes, owners and lock paths. Imported leases are inactive, untrusted history; every holder must claim again. Pre-existing lore is dropped with a report and kept in the original backup. Imported inbox and transcript records receive standard secret redaction; backups retain the original bytes.

To restore the latest backup, stop agents and run:

```bash
raid migrate --board PATH --rollback --yes --lead-secret /path/to/current-lead-secret
```

Rollback requires the current lead secret, live lease and fence. It restores the board contents under the existing write lock, discarding changes made since migration, and removes its schema 2 index entries. The backup is kept, and older tools can read the restored v1 files again.

## Workspaces

```bash
raid workspaces
raid workspaces --rm old-shop
```

Every board registers itself in the index under `RAIDLOCK_HOME`, so leases are checked across workspaces and `raid ui` can show them all. `--rm` drops a workspace from the index; it does not delete the folder.

## Delegation budgets

Budgets stop `raid delegate` from starting a run once recorded spend reaches a cap. They do not reserve money or stop a run that is already going.

```bash
raid --lead-secret "$LEAD_SECRET_FILE" budget set budget.workspace.daily_usd 10
raid --lead-secret "$LEAD_SECRET_FILE" budget set budget.workspace.monthly_usd 150
raid --lead-secret "$LEAD_SECRET_FILE" budget set budget.tokens_per_card 400000
raid --lead-secret "$LEAD_SECRET_FILE" budget set budget.card.checkout '{"usd": 3, "tokens": 200000}'
raid --lead-secret "$LEAD_SECRET_FILE" budget set budget.count_plan_usage false
raid budget show
raid budget show checkout
```

Values are JSON; `null` clears a cap. Days are UTC days and months are calendar months. `budget.count_plan_usage` (default `true`) counts the API-equivalent price of subscription-plan runs toward the USD caps. A per-card token cap overrides `budget.tokens_per_card`.

## Spend

```bash
raid spend --by model
raid spend --by provider --all --since 2026-10-01
raid cost
```

Spend comes from each runtime's own usage report: `claude -p` cost reports, Codex `--json` usage events, or `raid cost` lines that agents record themselves. Prices for API-equivalent estimates live in `pricing.json` under `RAIDLOCK_HOME`, in USD per million tokens. It is created from defaults on first use; edit it freely; models without a price show tokens only, unless the runner reported USD.

## Provider plan limits

```bash
raid limits
raid limits --json
```

| Provider | Source |
|---|---|
| Codex | rate-limit windows and context use in the newest Codex session logs under `CODEX_HOME` (default `~/.codex`) |
| Claude Code | context use from the session transcript (automatic with the plugin); plan usage only through the statusline: `raid limits --install-statusline` |
| Gemini CLI | not supported; no local source |

Every field, where it comes from and how fresh it stays: [Limits](limits.md). Readings show their age ("updated 4 min ago"); routing treats a reading older than six hours as unknown. Credentials never fail over; routing fallbacks and opt-in `resurrect --sweep` choose another provider only by rule.

## Patch safety scan

```bash
raid scan changes.patch
raid scan /path/to/clone --base main
```

Checks a patch file or a clone (against `--base`, default `HEAD`) for likely secrets, denied paths and binaries over 1 MiB. Findings print with the path, line and rule; the matched text is always `[REDACTED]`. Exit 2 when anything is found. `raid delegate` runs the same scan on every runner patch and blocks the card on a finding.

## Git guards

```bash
(cd acme-shop && raid guard install)
raid guard check
raid guard uninstall
```

`guard install` adds pre-commit and pre-push hooks to the Git checkout in the current folder and moves existing hooks aside as backups; `guard uninstall` restores them. `guard check` is what the hooks run (`--hook` says which). They refuse commits and pushes that touch paths outside the current card's lease, or carry a stale fence. They cover every provider and humans.

## Token overhead

```bash
raid tokens
```

Estimates how many tokens raidlock adds to an agent session: the skill and agent listing lines, hooks, typical CLI outputs and, when enabled, the MCP tool schemas.

When scanning a patch containing symlinks, use `raid scan PATCH --card CARD` to supply the card's lock paths. Targets must remain inside those paths and outside the denylist. Without a card, the scanner cannot authorize a symlink target and reports it as unsafe.

## Multi-agent locks and lead liveness

Review and blocked leases become advisory immediately. Their paths remain visible, and overlapping claims print a warning, including across registered workspaces. An advisory hold never authorizes hook-protected writes. Returning a card to doing reacquires its locks atomically; if another worker holds them, the card stays blocked.

`hold_minutes` limits advisory holds to 60 minutes by default. Heartbeats, lock edits, and moves between review and blocked do not extend that deadline. Expired holds no longer generate warnings; `raid reap` removes them while preserving the card's lane. Existing indefinite holds become advisory when their board opens, with their deadline measured from their last renewal.

```bash
raid --lead-secret "$LEAD_SECRET_FILE" config set hold_minutes 30
raid --lead-secret "$LEAD_SECRET_FILE" lock checkout --replace "acme-shop/docs/cart.md"
raid --lead-secret "$LEAD_SECRET_FILE" unlock checkout acme-shop/docs/cart.md
raid --lead-secret "$LEAD_SECRET_FILE" release checkout
```

`lock --replace` replaces the entire lock list. `unlock` removes exact listed paths; replace a directory lock with narrower paths to free part of it. Both require the current lead secret, session, and lead fence, and record the changes in the transcript. A conflicting replacement fails without changing the locks. Release works in review too. To give freed paths to another worker, claim a card covering those paths. Worker card fences remain required for owner actions.

Claims resolve locks against the board workspace's repositories. Repo-prefixed paths work from any directory. Unprefixed paths resolve against the workspace repository, or its sole discovered repository. With several child repositories, use a repo prefix. `--checkout <directory>` explicitly selects a clone or worktree and validates its provenance; cwd never selects it implicitly.

Every command presenting a valid lead secret from the bound session and process renews the lead lease, including read commands. The same session can renew for seven days after expiry. A different session, a changed process anchor, or expiry beyond that grace requires explicit takeover. Lead commands, `raid delegate` included, must run from the lead session's shell or a background job of it; `nohup`, `setsid` or a job whose shell has exited is detached from that process and is refused, by design. Retired secrets and stale fences remain invalid. `raid --lead-secret "$LEAD_SECRET_FILE" ui` renews every 30 seconds while running; it stops renewing if authentication fails. A UI started without the secret cannot renew the lead.

`raid up --join --agent worker-cedar --card checkout` inherits the card's team; `--team` overrides it. The worker may share the lead's Claude Code session ID but must present no lead secret. Without a team, it can claim unrestricted cards; restricted cards provide a `Next:` hint to join the appropriate team.

Each `up --join` writes `~/.raidlock/agents/<board-uid>/<agent>.env` with mode 0600 and prints the exact `source` command. `RAIDLOCK_HOME` changes the base directory. Workers must source their own files in their own environments. No shared env file is written, including `CLAUDE_ENV_FILE`.
