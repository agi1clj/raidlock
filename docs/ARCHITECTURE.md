# Architecture

raidlock is a Python package with no runtime dependencies (Python 3.11 or newer; sqlite3 from the standard library). `raidlock.core` is the only code that writes board state. Everything else calls it:

```
raidlock.cli      the `raidlock` command (humans, agents, hooks, git guards)
raidlock.web      local UI (reads Board.snapshot(), polls with Board.version())
raidlock.runners  codex-cli / claude -p / gemini / shell delegation
raidlock.hooks    Claude Code PreToolUse / PostToolUse / SubagentStop hooks
raidlock.mcp      stdio MCP server (same library, same locks)
        \            |            /
              raidlock.core   (Board + small modules)
               /                   \
   <workspace>/.board/          $RAIDLOCK_HOME/raidlock.db (+ tokens, pricing)
   cards, agents, teams,        leases, fences, registry, card mirror,
   inbox, transcript segments   transcript index, spend ledger, versions
```

## Storage

### Per workspace: `<workspace>/.board/`

These are human-readable files. `init` writes a `.gitignore` containing `*`, so git ignores the folder.

| Path | Content |
|---|---|
| `board.json` | `{schema, uid, workspace, created, lead, settings{auth, ttl_minutes, require_review{count, other_provider}, brief_max_tokens}}` |
| `cards/<id>.json` | `{schema, card}`, one file per card; the source of truth for card content |
| `agents.json`, `teams.json` | `{schema, agents\|teams}` |
| `inbox/<agent>.jsonl` | messages for one agent |
| `transcript/NNNNNN.jsonl` | append-only transcript segments, rotated at 10 MB |
| `workspace.json` (optional) | `{schema, repos: {name: path}}` declares repos that discovery misses |
| `.lock` | persistent OS lock file for the board lock |
| `v1/` | conversion source for an explicitly migrated team-board v1 board; complete rollback backup is a timestamped sibling `.board.backup-*` directory |

### Global: `$RAIDLOCK_HOME` (default `~/.raidlock`)

| Path | Content |
|---|---|
| `raidlock.db` | SQLite in WAL mode, `busy_timeout` 15 s. The source of truth for leases and fences; also the workspace registry and indexes (tables below) |
| `tokens/<board uid>/<agent>.token` | agent secrets, file mode 0600, directory 0700; never under `.board/` |
| `pricing.json`, `models.json` | user-editable price and display-name maps, created from defaults |

| Table | Purpose |
|---|---|
| `meta` | `schema`, global `fence` counter, global `version` |
| `workspaces` | registry: uid, name, board path, last_seen, per-workspace `version` |
| `leases` | one row per (workspace, card): agent, fence, paths, keys, since, renewed, expires (NULL = held), checkout, branch |
| `lease_keys` | one row per lock key: (ident, frel = casefolded relative path), indexed on (ident, frel) |
| `cards` | mirror of each card: lane, owner, team, plus the card JSON with its file's mtime and size |
| `agents` | registry of agents per workspace with last_seen |
| `transcript` | index of transcript records: seq, messageId (unique per workspace), ts, kind, task, agent, recipient, segment, offset, length |
| `costs` | spend ledger: one row per recorded run with split usage, usd, usd_equiv, billing, provider, model, agent, team, card, ts |

Every JSON file carries `"schema": 2`, and the DB carries `meta.schema`. Opening anything newer fails with exit 3. Board files and the DB never hold secrets.

**Migration of a team-board v1 `.board`** (markdown cards, unversioned JSON) happens on first open, under the board lock:

1. The v1 files are copied to `.board/v1/` through a staging folder, so the backup is either complete or absent. A board uid is generated and kept in `v1/uid`, so a re-run reuses it.
2. Each live v1 lease is inserted into `raidlock.db` with a fresh fence.
3. v2 cards, agents and teams are written and `leases.json` is removed.
4. `board.json` is written last; this is the commit point.
5. The v1 `cards/*.md` files are removed.

Migrated boards get `auth: off`: v1 agents have no tokens, so the lead turns auth on and agents re-join. `LOG.md` stays as history; raidlock no longer writes it, because the transcript is the log.

## Concurrency

- **Board lock**: `fcntl.flock` (POSIX) or `msvcrt.locking` (Windows) on `.board/.lock`. Every mutation of a board's files holds it. Nothing breaks a lock because of its age; the OS releases it when the holder dies. The lock is reentrant within one process and thread. While it is held, `agents.json` and `teams.json` are read once and cached.
- **Cross-workspace**: SQLite. A grant runs in one `BEGIN IMMEDIATE` transaction: conflict lookup, fence mint and lease write. Two grants in any two workspaces serialize on the database write lock and never both see a free path. There is no global file mutex.
- **Lock order**: board lock, then a DB transaction. Code never takes a board lock inside a transaction.
- **File writes**: a temp file in the same directory (`mkstemp`), then `fsync`, `os.replace`, and `fsync` of the directory. Transcript and inbox appends open with `a` and `fsync`. All text is UTF-8. Reads take no lock.
- A board inside an iCloud, Dropbox, OneDrive or Google Drive folder gets a warning (`init`, `snapshot()["warnings"]`), because sync can replay old files.

## Scale design

| Hot path | How it scales |
|---|---|
| Conflict check (claim, lock, renew after expiry) | For each requested key, two indexed lookups on `lease_keys(ident, frel)`: holders that contain it (`frel IN ancestors(path)`) and holders inside it (`frel >= path || '/' AND frel < path || '0'`), joined to live leases. Cost is O(paths × depth × log n), independent of how many leases or workspaces exist. Exact overlap with case rules is then checked in Python on the few candidates. |
| `check_write` (hook) | Ancestor lookup only (containment), same index. |
| Team cap, depends, `_busy`, `who` | Queries on the `cards` mirror; no card files are read. |
| `cards()` / `show` / `snapshot` | One `stat` per card file plus one query. A file whose mtime and size match the mirror is served from the stored JSON, otherwise it is read and the mirror refreshed. A hand-edited card always wins. |
| Transcript | Segments rotate at 10 MB. Reads go through the DB index by seq, task, kind or agent, then seek to offset and length. `read(since=seq, limit=n)` pages forward with a cursor and never scans a whole file. `Transcript.reindex()` rebuilds the index from the segments. |
| Polling | `Board.version()` returns `{workspace, global}` counters, bumped by every write. `snapshot()["etag"]` is `"<workspace>-<global>"`, so the UI can answer `If-None-Match` without building a snapshot. |
| Spend | `costs` table with GROUP BY. |

**Benchmark.** `tests/core/test_scale.py` is marked `slow`. It runs with `RAIDLOCK_SLOW=1` (nightly CI); the p95 threshold comes from `RAIDLOCK_SCALE_P95_MS`, default 100.

- Setup: 50 workspaces × 20 agents × 1,000 cards, with 500 pre-existing leases.
- Load: 200 claim attempts from 20 concurrent processes. 20 conflict groups of 5 cards each, spread over 5 workspaces that share one remote and use lock-path variants (`./`, `\`, `//`, trailing `/`, child path). 100 disjoint cards.
- Checks: exactly one winner per group, every disjoint claim wins, unique fences, and each card's fence equals its lease fence.

Results on the development laptop (Apple Silicon). The machine was shared with other agent sessions and runs endpoint scanning that costs about 3.5 ms per `open()` of a recently written file:

| Run | seed | claim p50 | claim p95 | `who --json` | `show --json` | correctness |
|---|---|---|---|---|---|---|
| 1 | 22 s | 46 ms | 77 ms | 466 ms (before the cost ledger) | 155 ms | pass |
| 2 | 51 s | 70 ms | 206 ms | 69 ms | 3,032 ms (before the doc cache) | pass |
| 3 | 43 s | 97 ms | 280 ms | 56 ms | 79 ms | pass |
| 4 | 122 s | 52 ms | 168 ms | 58 ms | 83 ms | pass |

Seed time is the load indicator: the same code took 22 s and 122 s. A single uncontended claim takes about 10 ms. Python start-up is about 50 ms of each CLI time. The p95 < 100 ms claim target was met in run 1 only. The read targets are met.

## Lock paths

A lock path is `<repo>/<path>`. A single segment (`app`) locks the whole repo.

- **Canonical form** (`paths.canonical`): NFC, trimmed, `\` becomes `/`. A `..` segment, an absolute path (`/x`, `C:`, `~`), a control character, or a path that resolves to the root is rejected with exit 3. After the checks, `posixpath.normpath` removes `.` and `//`.
- **Repo resolution** (`repos.Resolver`): repos are the workspace folder itself (depth 0), git repos at depth 1 and 2, and repos declared in `workspace.json`. The longest repo name that prefixes the path wins.
- **Identity**: the normalized `origin` remote (else the first remote), e.g. `github.com/acme/app`. Scheme, user, port and `.git` are stripped and the result is lowercased. A repo without a remote, or an unknown first segment, gets `path:<absolute path>`. Clones and worktrees of one remote therefore share one lock space in every workspace.
- **Case**: when the workspace's file system is case-insensitive (probed on `.board` versus `.BOARD`), the relative path and path identities are casefolded. Each key records `ci`.
- **Claims use overlap** (either path contains the other on a `/` boundary). **Enforcement uses containment**: `check_write` maps an absolute file path through the git checkout that contains it (its own remote, not cwd or `CLAUDE_PROJECT_DIR`).

## Leases and fencing

| Event | Effect |
|---|---|
| `claim` | Checks team, role, depends, team cap and locks against every live lease in every workspace. Then, in one transaction, all locks are granted or none are. Mints a new fence (global, monotonic) and puts the card in `doing`. TTL is `ttl_minutes` (default 45). |
| `claim` by the same owner | Mints a new fence, so the earlier session's fence goes stale. |
| `claim --take-over` | Allowed when the owner's doing lease has expired, or the card is `blocked`. Mints a new fence. |
| owner `heartbeat` / `note` / `lock` | Requires the current fence (missing: exit 3, stale: exit 2). Renews the TTL and keeps the fence. |
| renew after expiry, no take-over | Re-granted with the same fence if nothing overlapping was granted meanwhile. Logged as "lease resumed after expiry". |
| `move review` / `move blocked` | The lease becomes *held*: no expiry while the card waits. |
| `review --verdict changes` | The card returns to `doing` with the same owner and fence, and the TTL starts again. |
| `move todo`, `release` (from doing) | Lease dropped; the card goes to `todo` with no owner (A2A `canceled`). |
| `move done` | Lease dropped. |
| `reap` | Only `doing` cards whose lease expired: the card goes to `blocked` and the lease is dropped. Held review/blocked leases are never reaped. |

**Deviation from PROTOCOL 2.5.** A reaped card keeps its owner but loses its lease. Once a doing lease has expired, its paths were already unprotected and someone may have claimed them. Converting it to a held lease at reap time could create two live overlapping leases. `claimable` still offers the reaped card as a take-over.

## Identity and trust model

Agents `join` with a name (`[a-z0-9-]{3,40}`, unique per workspace; default `<runtime>-<role>-<card or n>`, e.g. `claude-worker-core`, `codex-reviewer-kek`, `human-lead`), a provider, a runtime, a model and a billing mode. `join` issues a token in `$RAIDLOCK_HOME/tokens/<board uid>/<name>.token` (0600) and prints a worker's token once as `RAIDLOCK_TOKEN`. The lead's token is never printed or exported: `init`, `up`, `quickstart` and `join` for the lead print only the file's path (`token_file` in JSON).

The caller is resolved by `Board.caller()`:

- Worker names come from `RAIDLOCK_AGENT_ID`, else `--agent`, else `RAIDLOCK_AGENT`. A pinned agent cannot select a different name. Worker tokens authenticate that name; `RAIDLOCK_ROLE=lead` is refused.
- Lead rights require an explicitly presented secret: CLI `--lead-secret <0600 file|keychain|->`, `RAIDLOCK_LEAD_SECRET`, or `RAIDLOCK_LEAD_SECRET_FILE`. A name, role or existing token file grants no lead rights. Worker credentials combined with a lead secret are refused.
- `.board/lead.json` stores the secret hash and a single lead lease: session ID, ancestor PID and process start time, expiry and monotonic fence. Every lead mutation validates and renews that lease under the same board lock as its writes. Expiry requires explicit takeover. Missing process-anchor information fails closed.
- `raidlock lead handover --to <session>` rotates the secret and bumps the fence. The receiving session binds its process on first use. `raidlock lead takeover` requires the current secret and an expired lease, rotates it, and emits an alert. Cached callers and retired secrets fail after either operation. Different-session attempts are logged as alerts.
- `raidlock who` shows each agent's recorded session and the lead's live session, fence and expiry. Lead mutations have session-attributed audit events.
- New boards require worker tokens by default. `RAIDLOCK_REQUIRE_TOKEN=1` can enable checks; an environment variable cannot disable them. A migrated board's explicitly presented legacy secret is rotated into a session lease on first lead write.

Lead-only actions include closing and reopening cards, verification, team and settings changes, urgent messages, acting for another agent, setting verification commands and rotating worker tokens.

A claim also returns a card capability (`cap` in JSON; `--cap` or `RAIDLOCK_CAP` when presenting it). Its authenticated MAC binds the worker, card and fence. It permits owner actions on that card and inbox/message access; it cannot enroll agents or create or claim other cards. Released or superseded capabilities fail. Launchers should pass this capability without the reusable worker token.

Extensions must wrap lead mutations in `with board.lead_write(caller, "action description"):`. This revalidates the presented secret, current fence, session, process and expiry and holds the board lock until the extension finishes. `identity.require_lead` alone only checks the resolved caller flag and is insufficient for cached callers.

The default secret store is a private file outside the board. `config set lead_store keychain` opts into the OS keychain; unavailable keychain support falls back to a 0600 file. Secrets are never printed in normal CLI results. Keep keychain access under the same host-user trust assumptions as file access.

All agents run as the same OS user. This is a cooperative boundary: same-UID code can read private files, inspect environments, alter board state or import the library and construct caller objects. Session IDs and hook identity must come from the host integration. Strong isolation requires separate OS users or a sandbox with separate credentials.

`core.lead_env` supplies environment scrubbing and refusal helpers for launchers. `core.lead_hook` supplies protected-secret path checks and one-time urgent delivery as quoted JSON context for a validated worker. Urgent messages and lead notes on an owned card are delivered even while its owner is doing another operation. These helpers do not install runtime hooks: the plugin and runner security branches must wire and test them before release. Hooks must guard shell reads as well as structured file tools, and resolve worker identity from host-controlled context.

## Git in worker checkouts

Every git command the lead or a runner runs goes through `runners.workspace.git_run`, including `raid land`. It applies the same safe overrides everywhere, disposable stores included: no system or global config, hooks, fsmonitor, pagers, external diff, textconv, SSH, credential helpers or signing programs. Reads and index writes run against a disposable object view: copied `HEAD`, index, packed refs and `info/exclude`, the repo's objects as an alternate, and a synthesized config with no URLs or executable keys. Remotes become inert fetch refspecs only, so a checkout can still infer a local branch from a remote-tracking ref. Attributes come from the empty tree, so a worker's `.gitattributes` filters never run. `apply` always uses `--index`: it checks both the index and the worktree, and worktree-only apply with an attribute source crashes Git 2.56.

Receipt signing keys live per board under `RAIDLOCK_HOME` and are covered by the hook read guard. A key found in an old workspace location is never trusted; it is rotated, and receipts signed with it are invalid.

## Cards

```
id, title, lane, owner, team, ticket, scope, output, verify, verify_cmd, locks[], depends[], reviewers[],
fence, rev, released, created, updated, notes[{ts, by, model, text}], reviews[{ts, by, verdict, note, rev,
provider, model}], verifications[{ts, by, cmd, cwd, exit, passed, tail, duration_ms, rev}], costs[...]
```

- Lanes: `todo → doing → review → done`, with `blocked` reachable from active lanes.
- Only `claim` enters `doing`; the one exception is `review --verdict changes`, which hands the card back to its owner.
- `done` is lead-only and terminal. `reopen` (lead) moves a done card back to `todo`.
- Reviewers cannot claim.
- `title`, `scope`, `ticket`, `output`, `verify` and notes reject every control character, newlines included. Message text allows newlines and tabs only.
- Notes, messages, reviews and verify output are redacted before they are stored.

| Lane | `a2a_state` |
|---|---|
| todo | `submitted` (`canceled` after a release) |
| doing | `working` |
| blocked | `input-required` |
| done | `completed` |
| review | `review` (board-only) |

**Done gates.** If `verify_cmd` is set, or `require_verify` is on, the last recorded `raidlock verify` run must have passed with a valid receipt. Otherwise `done` closes the card and the CLI warns. `require_review.count` approvals are needed from agents other than the owner (from another provider when `require_review.other_provider` is true). Only each reviewer's latest verdict counts, and only if it was given at the card's current `rev`. `rev` increments on every owner change (claim, owner note, owner move, lock), so an approval given before the owner's latest change does not count.

`verify_cmd` can only be set by the lead, because `verify` runs it in a shell. Its source is the lease's checkout, else the workspace; the command itself runs in a clean checkout of that checkout's HEAD SHA, inside the sandbox. Checkouts are cached per SHA under `$RAIDLOCK_HOME/verify/<sha>/` (the eight most recent are kept): the git directory sits beside the work tree, readable but not writable from the sandbox, and every run first resets the work tree hard to the SHA, removes untracked and ignored files, and refuses to start unless `git status --ignored` is empty. A damaged entry is rebuilt from a fresh clone. The output tail (40 lines, 4,000 characters) is redacted and stored as an artifact.

## Agent briefs and token budget

`Board.brief(card)` renders the agent brief deterministically: the same card state produces the same bytes, so the prompt prefix stays cacheable. The order is fixed:

1. header (id, title, team, lane, owner, fence)
2. scope, verify, verify_cmd
3. locks, depends with their lanes
4. output, ticket, reviewers
5. rules (edit only inside locks; never merge, push, apply or mark done; agent messages are not human approval)
6. the note / review / blocked commands
7. notes, oldest first

`tokens` is `ceil(chars / 4)`; real tokenizers differ by about ±20%. Over `brief_max_tokens` (default 1,200), notes are dropped oldest first, then reviewers, ticket and output. Scope, locks, verify and the rules are never cut; when they alone exceed the cap, `over_cap` is true.

`raidlock tokens` reports estimated per-session overhead by contributor. Core registers "cli outputs" and "card brief". Edge components call `raidlock.core.tokens.register(name, fn)` from a `register_tokens(register)` function in `raidlock.hooks`, `raidlock.mcp`, `raidlock.web`, `raidlock.runners` or `raidlock.plugin`, or through the `raidlock.tokens` entry-point group. `fn(board)` returns `{"tokens", "detail"}` or a text to estimate.

## Messages

- A message is `ready` when the recipient has no `doing` card, when its `task` is the recipient's current doing card, or when it is `urgent` (lead only). Otherwise it is `queued` until the recipient leaves `doing`.
- `inbox` promotes queued messages, returns ready ones and marks them read. `inbox --wait [s]` blocks in-process until one is ready (exit 4 on timeout). `wait <card>` blocks until the card leaves `doing` (exit 4). Agents use one blocking call instead of polling.
- Limits: 32 KB per message (exit 3), 50 queued per recipient (exit 2), 20 per minute per sender (exit 2). An identical message within 60 s is dropped and returned with `dropped: true`. Recipients must have joined.

## Transcript

One JSON object per line, A2A Message vocabulary plus board fields. `seq` is added on read.

```json
{"messageId": "...", "ts": "2026-10-22T12:00:00Z", "kind": "msg|relay|handback|note|status|cost|artifact|review|verify",
 "role": "agent", "taskId": "fix-login", "contextId": "<workspace>/<task>",
 "from": {"agent": "w-one", "provider": "openai", "runtime": "codex-cli", "model": "gpt-6-astra"},
 "to": "", "parts": [{"kind": "text", "text": "..."}, {"kind": "data", "data": {}}],
 "artifacts": [{"artifactId": "...", "name": "review", "parts": [], "metadata": {"kind": "review"}}], "src": "board"}
```

Denials are recorded as `status` records with a data part: a refused claim writes `{"event": "lock_denied", "agent", "reasons", "paths"}`, and a denied `check_write` for a named caller writes `{"event": "lock_violation", "agent", "path", "holder", "reason"}`. Reputation counts them.

Appends are deduplicated by `messageId` (unique index). Before writing, every string is redacted: private keys, AWS key ids, `sk-` keys, GitHub/GitLab/Slack tokens, Google API keys, JWTs, bearer tokens, and `password|secret|token|api_key = value` assignments (the key name stays).

## Spend

**Billing mode per agent:**

| Mode | Meaning | Detected from |
|---|---|---|
| `api` | pay per token | `OPENAI_API_KEY` / `CODEX_API_KEY` (codex-cli); `ANTHROPIC_API_KEY`, Bedrock or Vertex (claude-code); `GEMINI_API_KEY` / `GOOGLE_API_KEY` (gemini-cli) |
| `plan` | subscription such as a ChatGPT or Claude plan | `$CODEX_HOME/auth.json` with `auth_mode: chatgpt`; `oauthAccount` in `.claude.json` |
| `unknown` | nothing detected | |

`join --billing` and `RAIDLOCK_BILLING` override detection. Only the auth mode is read, never a key.

**Usage per run:** `Usage(input, cached_input, cache_write, output, reasoning_output)`. `input` is uncached input only. `reasoning_output` is the reasoning part of `output` (informational, priced inside `output`). `Usage.from_codex(turn.completed.usage)` subtracts `cached_input_tokens` from `input_tokens`. `Usage.from_anthropic(usage)` maps the cache-read and cache-creation fields. Runs accumulate per card.

**Price:** `$RAIDLOCK_HOME/pricing.json` (USD per million tokens) ships these Anthropic defaults, plus OpenAI and Google placeholder entries whose rates are `null` and whose `source` is "fill in from your provider's pricing page". No prices are guessed.

| model | input | output | cache_read | cache_write |
|---|---|---|---|---|
| claude-opus-5-5 | 4 | 20 | 0.20 | 5 |
| claude-sonnet-5-5 | 2 | 10 | 0.10 | 2.5 |
| claude-haiku-5-5 | 0.10 | 0.50 | 0.01 | 0.125 |

**Recorded USD per mode:**

| Mode | `usd` | `usd_equiv` |
|---|---|---|
| `api` | runner-reported USD, else the priced value | same as `usd` |
| `plan` | 0 | API-equivalent estimate |
| `unknown` | reported USD, else `null` | priced estimate |

With no price, tokens are recorded and USD is `null` (shown as "USD —").

**Totals:** `card_view()["cost"]` per card. `Board.spend(by=provider|model|agent|team|card|billing|workspace|day|week, since, until, everywhere)` for this workspace or every workspace. `raidlock spend` and `raidlock cost` (no id) print the same.

## Status

Status is derived from the owner's own lease heartbeat (`lease.renewed`), never from `card.updated`:

| Status | Rule |
|---|---|
| Working | heartbeat within 15 min |
| Working (quiet) | heartbeat 15–45 min ago |
| Stalled | lease expired, or no heartbeat for over 45 min |
| Waiting for review | the agent's card is in `review` |
| Blocked | the agent's card is in `blocked` |
| Idle | no active card |

## Models

An agent's model comes from the first of:

1. `join --model` (`declared`)
2. `RAIDLOCK_MODEL` (`env`)
3. a runner's `-m` (`--runner-model`, `flag`)
4. the runtime's report (`--reported-model`, e.g. the `claude -p` stream-json init event; `reported`)
5. `ANTHROPIC_MODEL` / `GEMINI_MODEL` (`env`)
6. the runtime's config: `$CODEX_HOME/config.toml` `model` and `model_reasoning_effort`, `~/.claude/settings.json` `model`, `~/.gemini/settings.json` (`config`)

The agent stores `model`, `model_version`, `effort`, `model_source` and `display` (`name · Model display · runtime`, names from `models.json`; unknown ids show raw). Every note and transcript record carries the author's model at that moment.

## Public API

Import from `raidlock.core`. Everything else is internal.

```python
from raidlock.core import Board, Caller, Usage, RaidlockError, Denied, Precondition, Timeout, IdentityError
```

Errors carry `.code` (2 denied/conflict, 3 precondition, 4 timeout, 5 identity/role), `.message`, `.hint` (the next command to run) and `.details`; `.to_dict()` is the `--json` error body.

```python
Board.init(workspace=".", lead=None, runtime="human", provider=None, model_id=None, auth=True, env=None, home=None)
    -> (Board, lead_token | None)
Board.open(path=None, env=None, home=None) -> Board      # path, RAIDLOCK_BOARD, BOARD_DIR, or nearest .board above cwd
board.caller(agent=None, token=None, fence=None, env=None, lead_secret=None, cap=None, session=None) -> Caller(name, authenticated, is_lead, subagent, fence, ...)

board.join(caller, agent=None, team=None, role="worker", runtime="shell", provider=None, model_id=None,
           flag_model=None, reported_model=None, effort=None, session="", card="", billing=None) -> dict (+ token once)
board.add_team(caller, name, max_active=2, mission="", repos_list=None) / board.remove_team(caller, name)
board.team_list() / board.get_setting(key=None) / board.set_setting(caller, key, value)

board.new_card(caller, title, card_id=None, team="", locks=None, depends=None, scope="", verify="", verify_cmd="",
               output="", ticket="", reviewers=None) -> card
board.import_plan(caller, items) -> [card]
board.cards() / board.card(id) / board.card_view(card, leases=None, agents=None)
board.brief(id, max_tokens=None) -> {card, text, tokens, max_tokens, dropped, over_cap}
board.claimable(agent=None, for_review=False) -> {id: {ok, reasons, takeover, title, lane, ...}}

board.claim(id, caller, take_over=False, ttl_minutes=None, for_agent=None, checkout=None)
    -> {card, agent, fence, expires, paths, checkout, branch, taken_over_from}
board.heartbeat(id, caller, ttl_minutes=None) -> {card, fence, expires, resumed}
board.note(id, caller, text) / board.lock_paths(id, caller, paths) / board.release(id, caller, note=None)
board.move(id, lane, caller, note=None) / board.reopen(id, caller, note=None)
board.review(id, caller, verdict, note="") / board.assign_reviewers(id, caller, names) / board.approvals(card)
board.verify(id, caller, timeout=600, cwd=None) / board.reap()

board.check_paths(paths, card_id=None) -> [conflict{path, held, card, agent, workspace, expires}]
board.check_write(path, caller) -> {allowed, reason, card, holder}

board.send(caller, to, text, task=None, urgent=False) -> message (+ dropped)
board.inbox(caller, show_all=False, wait=0.0, interval=1.0) -> {agent, messages, queued, busy}
board.wait(id, timeout=1800.0, interval=5.0) -> {card, lane}

board.record_cost(id, caller, model_id=None, usage=Usage(...), usd=None, duration_ms=0, billing=None, agent=None)
board.spend(by="provider", since=None, until=None, everywhere=False) -> {by, rows, total}
board.costs() -> {cards, teams, workspace}
board.statuses() / board.who()
board.version() -> {workspace, global}
board.snapshot(transcript_limit=200, messages_limit=50, transcript_since=None) -> dict with version and etag
board.transcript.append(record) -> record with seq | None
board.transcript.read(task=None, kind=None, agent=None, limit=None, since=None) -> [record with seq]
board.leases(), board.agents(), board.teams(), board.settings, board.lead, board.name, board.dir, board.workspace
```

Helpers:

- `raidlock.core.transcript.make(kind, sender, text, task, context, to, src, parts, artifacts, data, message_id)` and `.artifact(name, text, data, kind)` build records.
- `raidlock.core.redact(text)`; `raidlock.core.canonical / contains / overlaps`.
- `raidlock.core.identity.detect_runtime(env, interactive)`; `raidlock.core.models.detect(...)`; `raidlock.core.billing.detect(runtime, declared, env)`.
- `raidlock.core.repos.checkout_info(path)`; `raidlock.core.tokens.register(name, fn)`.

**Example: PreToolUse hook**

```python
board = Board.open(hook_input["cwd"])
caller = board.caller(agent=hook_input.get("agent_id") or None)
d = board.check_write(hook_input["tool_input"]["file_path"], caller)
decision = "allow" if d["allowed"] else "deny"  # reason: d["reason"]
```

**Example: Codex runner**

```python
from raidlock.core import Board, Usage, transcript as T

board = Board.open(workspace)
lead = board.caller(lead_secret=presented_secret)  # explicitly supplied by the lead session
r = board.join(lead, agent="codex-worker-api", team="core", runtime="codex-cli", flag_model="gpt-6-astra")
grant = board.claim("api", lead, for_agent="codex-worker-api", checkout=clone_dir)
worker = board.caller(agent="codex-worker-api", token=r["token"], fence=grant["fence"])
board.heartbeat("api", worker)  # on each event batch
board.record_cost("api", worker, usage=Usage.from_codex(event["usage"]))
board.transcript.append(
    T.make(
        "handback",
        {"agent": "codex-worker-api", "provider": "openai", "runtime": "codex-cli", "model": "gpt-6-astra"},
        summary,
        task="api",
        context=f"{board.name}/api",
        artifacts=[T.artifact("changes.patch", data=meta)],
    )
)
board.move("api", "review", worker, note="patch at <run>/changes.patch")
```

**Example: UI polling**

```python
board = Board.open(path)
v = board.version()  # compare with If-None-Match before doing work
snap = board.snapshot(transcript_since=last_seq)  # snap["etag"], snap["transcript"][-1]["seq"]
```

## CLI

```
raidlock <command> [--board PATH] [--json] [--fields all|a,b] [--agent NAME] [--token T] [--fence N] [--debug]
```

- `raid` is the same program. Usage, help and `Next:` hints use the name that was invoked; `raid doctor` reports when another `raid` earlier on `PATH` shadows this install.
- Global flags may come before or after the command, and command flags may sit between positionals.

- `raidlock` or `--help` shows the everyday commands; `raidlock help advanced` shows the rest.
- Human output is one or two lines. Mutating commands prefix `[<workspace>]`, and add `Next: <command>` where useful.
- Errors print `raidlock: <what happened>` and `Next: <exact fix>` on stderr; tracebacks only with `--debug`.
- `--json` prints compact JSON with the command's minimal fields; `--fields all` adds everything, including `board`.
- Exit codes: 0 ok, 2 denied or conflict, 3 precondition (including usage errors), 4 timeout, 5 identity or role, 1 internal error.

**Environment:**

| Variable | Use |
|---|---|
| `RAIDLOCK_BOARD` | board (`BOARD_DIR` also accepted) |
| `RAIDLOCK_HOME` | global home |
| `RAIDLOCK_AGENT` | agent name |
| `RAIDLOCK_AGENT_ID` | pinned name, set by hooks and runners |
| `RAIDLOCK_TOKEN` | worker token; ignored for the lead |
| `RAIDLOCK_FENCE` | card fence |
| `RAIDLOCK_ROLE` | refused when set to `lead` (exit 5) |
| `RAIDLOCK_REQUIRE_TOKEN` | turns token checks on |
| `RAIDLOCK_MODEL`, `RAIDLOCK_BILLING`, `RAIDLOCK_RUNTIME`, `RAIDLOCK_SESSION` | override detection |
| `RAIDLOCK_SLOW`, `RAIDLOCK_SCALE_P95_MS` | scale tests |

| Group | Command and flags |
|---|---|
| Start | `up [--team] [--role worker\|reviewer] [--runtime] [--model]` |
| | `quickstart [PATH] [--lead] [--runtime] [--team core] [--locks]` |
| | `doctor` (Python, board, `RAIDLOCK_HOME` writable, database, `raid` on `PATH`; exit 3 on a problem) |
| Plan | `new TITLE [--id --team --locks --depends --scope --verify --verify-cmd --output --ticket --reviewers]` |
| | `plan import FILE [--team]` (JSON list or `{"cards": [...]}`; Markdown `- [ ] title \| locks: a/b`) |
| Work | `brief ID [--max-tokens N] [--quiet]` |
| | `claim ID [--take-over] [--ttl MIN] [--for AGENT] [--checkout PATH]` |
| | `note ID TEXT` |
| | `move ID todo\|review\|blocked\|done [--note]` |
| | `done ID [--note]` |
| Agents | `who [--brief]` (`delegate` from the runners card) |
| Board | `show [ID] [--all] [--brief]` (`ui` from the web card) |
| Advanced | `init [PATH] [--lead --runtime --model --no-auth]` |
| | `join [--team --role --runtime --provider --model --runner-model --reported-model --effort --session --card --billing]` |
| | `team add\|rm\|list [NAME] [--max --mission --repos]` |
| | `config get [KEY]`, `config set KEY VALUE` (auth, ttl_minutes, require_review, require_review.count, require_review.other_provider, brief_max_tokens) |
| | `claimable [--for-review]` |
| | `heartbeat ID [--ttl]` |
| | `lock ID PATHS` |
| | `release ID [--note]` |
| | `reopen ID [--note]` |
| | `review ID --verdict approve\|changes\|comment [--by AGENT] [--note]` or `review ID --assign a,b` |
| | `verify ID [--timeout S] [--cwd]` (exit 2 when it fails) |
| | `msg TEXT --to AGENT [--from AGENT] [--task ID] [--urgent]` |
| | `inbox [--all] [--wait [S]] [--interval S]` |
| | `wait ID [--timeout S] [--interval S]` |
| | `cost [ID] [--model --input --cached-input --cache-write --output --reasoning-output --usd --billing --for --duration-ms]` |
| | `spend [--by provider\|model\|agent\|team\|card\|billing\|workspace\|day\|week] [--since] [--until] [--all]` |
| | `reap` |
| | `workspaces [--rm NAME...]` (alias `projects`) |
| | `check [PATHS] [--id] [--write FILE]` (exit 2 on conflict or deny) |
| | `transcript [--task --kind --who] [-n N]` |
| | `tokens` |
| | `help [advanced\|COMMAND]` |
| | `version` |

**Extension point.** A module among `raidlock.web.cli`, `raidlock.runners.cli`, `raidlock.hooks` and `raidlock.mcp`, or an entry point in the group `raidlock.commands`, exposes `register_cli(add_command)`. `pyproject.toml` declares entry points for notify, snapshot, reputation, tui, recipes, guard, mcp, delegate, ui, suggest-locks and foresight; one whose module is not installed yet is skipped (a note under `--debug`). It calls:

```python
add_command(name, help, setup(parser), handler(ctx, args) -> (result_dict, text), mutating=False, aliases=())
```

`ctx.board` opens the board lazily, `ctx.caller()` resolves the agent, and `_exit` in the result sets the exit code. The help groups already list `delegate` and `ui`; a group shows a command once it is registered.

## Spec status

Which PROTOCOL.md rules the core enforces today.

| Rule | Status | Where |
|---|---|---|
| 2.1 lock path resolves to repo identity | enforced | `repos.Resolver`, `repos.locate` |
| 2.2 overlap on `/` boundary; reads need no lock | enforced | `paths.overlaps`, `db.Index.conflicts` |
| 2.3 all-or-nothing grant across workspaces; refusal names path, holder, card, workspace, expiry | enforced | `db.Index.grant` (one `BEGIN IMMEDIATE` transaction) |
| 2.4 45-min TTL, renew on owner actions, reap, take-over | enforced, with fence check and resume logging | `claim`, `heartbeat`, `note`, `lock_paths`, `reap` |
| 2.5 lease kept through review/blocked, released on done/todo/release | enforced (reaped doing cards lose their lease, see the deviation) | `move`, `release`, `reap` |
| 2.6 enforcement on writes | library decision only (`check_write`); the hook, git guard and runner diff belong to other cards | `Board.check_write` |
| 2.7 own checkout per agent (clone or worktree) | recorded on the lease, not enforced | `repos.checkout_info` |
| 2.8 fencing | enforced on every owner command and in `check_write` | `Board._owner_or_lead` |
| 3.1 only the library writes `.board/` | raidlock code only; `check_write` denies `.board/` paths for hooks | |
| 3.2 kernel lock, atomic writes, append-only logs | enforced (board flock + SQLite transactions; transcript segments append-only) | `lock.FileLock`, `store`, `db` |
| 3.3 UTC timestamps, schema version, forward migration, refuse newer | enforced | `clock`, `store.check_schema`, `migrate`, `db` |
| 3.4 no secrets in the board | redaction of notes, messages, reviews, verify output and the transcript; tokens outside `.board/` | `transcript.redact` |
| 3.5 `.board/` gitignored | enforced on `init` | `.board/.gitignore` |
| 4 claimable rules; only claim enters doing; done lead-only; verify and review gates | enforced | `claimable`, `claim`, `move`, `_done_gate` |
| 5 identity with provider, runtime, model; derived status | enforced (token auth, see the trust model) | `identity`, `models`, `billing`, `status` |
| 6 messages: queue, same-task immediate, urgent, caps, transcript | enforced | `messages`, `Board.send`, `Board.inbox` |
| 7 runners and pricing | pricing, billing modes and the spend ledger enforced; runners belong to the runners card | `cost`, `db.Index.spend` |
| 8 hard limits for workers | lead-only commands enforced; merges, pushes and settings are outside raidlock's reach | `identity.require_lead` |

Executable aliases and uppercase `GIT` remain general-shell recognition limits.
The same-UID boundary is cooperative: a process with access to credentials or lock
directories can bypass coordination, including replacing a lock after inode verification.
Guard ref deletions and non-fast-forward updates to any ref require an authenticated lead.
