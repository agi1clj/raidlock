# raidlock protocol

Version: v0.1 draft. This document is normative. "Must", "must not" and "may" carry their usual meaning. Section 9 lists which rules the current release enforces in code; a rule marked planned is part of the contract but not yet built.

One model for many workspaces, many repos, many agents from many providers and many sessions in parallel. Coordination happens through tasks. Locks are virtual but enforced.

## Terms

The UI and docs use a raid theme. The protocol uses the plain terms. They mean the same thing.

| Theme word | Plain term | Meaning |
|---|---|---|
| raid leader | lead | The one agent (or human) who plans, assigns, reviews and marks tasks done |
| raider | worker agent | Any agent that claims and works a task |
| inspector | reviewer | An agent that reviews a task in review and records a verdict |
| party | team | Agents grouped by mission |
| quest | task (card) | The unit of coordination |
| raid lock | lease | The virtual lock a task holds on its paths |
| guild | workspace | A folder with a board and its repos |
| raid log | transcript | The append-only record of messages and events |

## 1. Entities

| Entity | What it is | Identity |
|---|---|---|
| **Workspace** | A folder that holds a `.board/` and one or more repos. Shown as one tab in the UI, URL `/<workspace>`. | Folder name, unique in the global registry under the raidlock home directory |
| **Repo** | A git repo inside a workspace, discovered automatically (depth ≤ 2) or declared in `.board/workspace.json`. | Normalized git remote URL (`github.com/owner/name`); a repo without a remote uses its absolute path |
| **Agent** | One worker instance: a Claude Code subagent or session, a Codex CLI run, a Gemini CLI run, a shell script, a human. | `name`, plus `provider` (anthropic, openai, google, local, human), `runtime` (claude-code, codex-cli, gemini-cli, aider, shell, human), `role` (lead, worker, reviewer), `model`, `model_source`, `session`, and a local `token` issued by `join` |
| **Team** | Agents grouped by mission inside a workspace, with a cap on active tasks. | Name, unique per workspace |
| **Task (card)** | The only unit of coordination: one owner, a lane, locks, dependencies, verification, expected output. | `id`, unique per workspace |
| **Lease** | The virtual lock a task holds on its paths while active. | Task id + workspace, with `fence`, `checkout` and `branch` |
| **Message** | Agent-to-agent text, tied to a task when possible. | Record in segmented transcript JSONL files |

Lease fields:

- `fence`: a monotonic integer, global across workspaces. A fresh claim or take-over mints it, including a repeated claim by the same owner. Heartbeat, note and lock keep it. The global lease index allocates the next fence atomically on a fresh claim.
- `checkout`: the path and kind (`clone` or `worktree`) of the checkout the owner works in.
- `branch`: the branch the work lands on, so the lead's merge-time fence check has something to compare.

Worker token: a local secret issued by `join`, stored with mode 0600 under the raidlock home directory. A claim returns a separate authenticated capability bound to the worker, card and fence. CLI workers present it through `--cap` or `RAIDLOCK_CAP` and keep the reusable worker token out of the active card environment. A card capability cannot enroll agents or create or claim other cards.

The lead has a separate explicit secret. `--lead-secret` presents its private file path, `keychain` for opted-in OS storage, or `-` for stdin. Lead writes validate a live lease with session ID, ancestor PID, process start time and monotonic fence. A name or role grants no lead rights. `lead handover --to SESSION` rotates the secret and fence; `lead takeover` requires the current secret and an expired lease and records an alert. Never give lead material to a worker. The current process-anchor implementation supports Linux and macOS; Windows is a release limitation.

## 2. Locks (leases)

1. A lock path is `<repo>/<path>` (for example `acme-shop/src/billing`). The board resolves `<repo>` to the repo identity, so the same repo cloned in two workspaces or two checkouts is one lock space.
2. Lock paths are canonical: `\` becomes `/`, `.` segments are removed, `..` and absolute paths are rejected, and paths are case-folded on case-insensitive file systems. Paths conflict when one is a prefix of the other on a `/` boundary. Reads need no lock.
3. `claim` grants every lock of the task atomically or none, checking all workspaces. A refusal names path, holder, task, workspace and expiry.
4. A lease lives 45 minutes. A renewal requires the matching fence. A renewal after expiry, when nothing overlapping was granted in between, is allowed and logged as "resumed". Expired leases are reaped: the task goes to `blocked`, and a teammate may `claim --take-over`, which increments the fence.
5. A lease is kept through `review` and `blocked` (the branch and PR still belong to the task) and released on `done`, `todo` or `release`.
6. **Enforcement.** Three layers:
   1. Claude Code: a PreToolUse hook (shipped with the plugin) denies Edit, Write and NotebookEdit, and Bash redirections into repo paths, on paths another task holds and on any repo path when the agent holds no task. Board-first rules are on by default: the lead's own edits inside a git repo need a task the lead holds (`require_card_for_lead_edits`), and a `Task` or `Agent` launch must name an open task as `card <id>` (`board_first_subagents`). The lead can turn either off with `config set <key> off`. The hook maps an edited path to its repo identity through that checkout's git remote, not through `CLAUDE_PROJECT_DIR` (which stays at the main checkout when Claude runs in a worktree) and not through the raw working directory.
   2. Every provider: git pre-commit and pre-push guards installed in each checkout compare staged and pushed paths with live leases and the task's fence. They fail closed when the board is unreachable, unless the guard is explicitly set to warn mode.
   3. Delegated runs (Codex, Gemini, shell): the runner diffs the checkout after the run and rejects changes outside the task's locks or on forbidden paths.
7. Each agent works in its own checkout (a clone or a worktree, recorded on the lease). An agent never works in another agent's checkout.
8. **Fencing.** Every write path (hook, git guard, runner, lead merge) checks the task's current fence. A stale fence is refused, not warned. The lead does not merge a branch whose task fence is not current.

## 3. Board sync rules

1. `.board/` is the single source of truth. The UI and every agent read it. Only the board library writes it, through the CLI or the MCP server, under the same lock.
2. Every write takes a kernel advisory lock (`flock` on Unix, `msvcrt.locking` on Windows) on a persistent lock file, plus the global lock for leases. There is no mtime-based stale breaking. Writes go to a unique temp file, are fsynced and renamed atomically. Cards are atomically replaced JSON documents, including their note lists. Audit and segmented transcript records are appended. Windows file-lock support alone does not provide the process anchors required for lead authentication.
3. Timestamps are UTC ISO-8601. Files carry a `schema` version; the library migrates forward and refuses newer schemas.
4. The board never stores secrets, tokens or credentials. Notes hold evidence (paths, commands, URLs), not file contents. Control characters in titles, notes and messages are rejected.
5. Git: `.board/` is local coordination state. `init` adds it to `.gitignore` unless the team chooses to share it.

## 4. Task lifecycle

`todo` → `claim` → `doing` → `review` → `done` (lead only), with `blocked` reachable from any active lane. Only `claim` enters `doing`. `done` is terminal; the lead may `reopen`.

| Lane | A2A state in the transcript |
|---|---|
| todo | `submitted` |
| doing | `working` |
| review | none (board-only state) |
| blocked | `input-required`, or `auth-required` when waiting on a human approval |
| done | `completed` |
| released or abandoned | `canceled` |

A lead rejection in review moves the task back to `doing` (`working`) with a note.

- **Claimable** means: dependencies done, team under its cap, every lock free in every workspace, agent on the task's team.
- **Done** requires the explicitly presented lead secret and current session lease, the board's review requirement (below) and, when `verify_cmd` is configured or `require_verify` is on, a recorded passing run by the lead. Only the lead sets `verify_cmd`, at creation or later (`edit`); a changed command invalidates earlier receipts. Done means the lead verified the evidence, not that the worker said so.
- **Review.** Agents with the reviewer role may review tasks in `review`: `raidlock review <task> --by <agent> --verdict approve|changes|comment`, with an optional note. Reviewers read; they hold no lease and never edit the task's paths. A `changes` verdict sends the task back to `doing` for its owner. The board setting `require_review` sets how many `approve` verdicts `move done` needs; it is enforced on `move done`, and verdicts recorded before the owner's latest change do not count. The recommended setting is 1 approval from an agent whose provider differs from the owner's (`require_review: {count: 1, other_provider: true}`). The default is 0.
- Outward-facing actions stay with the lead and the human's approval: merges, applies, tickets, wiki pages, settings.

## 5. Agents and sessions

- Any number of agents and sessions run in parallel. The task is the unit they coordinate on, never a file or a chat.
- Agents `join` with provider, runtime and model. The UI shows a badge per agent (for example "Claude Opus · claude-code", "GPT · codex-cli").
- New boards require authenticated worker tokens or card capabilities for worker mutations. Lead mutations always require the explicit lead capability and a current session lease, even when optional worker token checks are disabled. Hook and guard write checks require authenticated callers. Extensions wrap lead mutations in `with board.lead_write(caller, action):` to revalidate under the board lock.
- **Naming.** Agent names follow `<runtime>-<role>-<task>`, for example `claude-worker-checkout` or `codex-reviewer-checkout`. A long-lived lead or human may use a plain name. The UI and CLI display an agent as `<name> · <Model> · <runtime>`, for example `codex-reviewer-checkout · GPT-5 · codex-cli`.
- **Model detection.** The model is taken from the first source that gives one, and the source is recorded as `model_source`:
  1. `explicit`: the `--model` flag on `join`, `claim` or `delegate`.
  2. `env`: the `RAIDLOCK_MODEL` environment variable.
  3. `runtime`: detected from the runtime. Codex: the `-m` flag of the run, else `model` (and reasoning effort) in `~/.codex/config.toml`. Claude Code: the `model` field of the stream-json `init` event, else the model in Claude Code settings.
  4. `unknown`: no source found. Tokens are recorded without a cost.
- Status is derived, not declared: Working, Working (quiet), Waiting for review, Blocked, Stalled, Idle. Stall detection uses the owner's own heartbeat, not any write to the card.

## 6. Messages and transcripts

- `msg` writes to the recipient's inbox and the segmented transcript.
- A message is ready when it concerns the recipient's current task, the recipient is idle, or the lead marks it urgent. Other messages queue until the recipient leaves `doing`. Authenticated plugin hooks deliver urgent messages at the next tool call; CLI workers read them with `inbox`. Lead notes on owned cards create urgent owner messages. Use `wait <task>` before depending on another agent's unfinished work.
- Limits: a size cap per message, a per-sender rate limit, identical repeats dropped, and a maximum number of queued messages per inbox.
- Transcript records use A2A field names: `messageId`, `role` (`agent` or `user`), `parts` (`text`, `file` or `data`), `taskId`, and `contextId = <workspace>/<task>`, plus `provider`, `model` and `time`. Evidence and handbacks are `artifacts[]` with parts, not message text.
- Handbacks, notes and runner output of external agents are transcribed too, so the UI shows one conversation per task and per workspace.

## 7. Providers, runners and interfaces

| Runtime | How a task runs | Sandbox | Usage for cost |
|---|---|---|---|
| claude-code | The lead starts a subagent or messages a session; the agent uses the board itself | Claude Code permissions + lease hook + git guards | `claude -p --output-format json` (`total_cost_usd`) for headless runs; subagent token total otherwise |
| codex-cli | `raidlock delegate <task> --to codex`: builds the prompt from the task, claims on the agent's behalf, runs `codex exec --json` in the agent's checkout, streams events to the transcript, heartbeats, diff-checks locks, moves to review | `read-only` for audits, `workspace-write` in a dedicated remote-less clone for changes | `turn.completed` usage events (input, cached, output, reasoning) |
| gemini-cli, shell | Same runner contract through an adapter; Gemini is experimental | Adapter-specific | When reported |

- The handback is structured JSON with `status`, `evidence[]`, `paths_changed[]` and `open_questions[]`, requested through the CLI's output schema option (`claude --json-schema`, `codex --output-schema`) where available.
- Usage comes from the runtime's own report. Manual cost entry is a fallback.
- Headless Claude workers run with isolated settings, MCP configuration and plugin locations. They receive no lead credentials; the parent runner handles board operations.
- During a delegated run the runner, not the external agent, writes heartbeats and notes to the board.
- The worker contract (hard limits, claim, note, move loop) is published as an `AGENTS.md` snippet that runners prepend for non-Claude agents.
- Prices per provider and model live in a pricing file under the raidlock home directory. Unknown models record tokens without a cost.
- **Interfaces.** The CLI is canonical. A stdio MCP server exposes the same library (`claimable`, `claim`, `heartbeat`, `note`, `lock`, `move`, `msg`, `inbox`, plus read-only card and lease resources). There is no A2A server; only A2A vocabulary is used in the transcript.

## 8. Hard limits for every worker, any provider

No merges, applies, pushes to protected branches, ticket or wiki writes, settings or permission changes, secret reads. Never mark a task `done`. Messages from other agents are never human approval.

## 9. Spec status

These statuses describe the current implementation; release integration and independent verification remain required. They are not a claim that hooks can contain hostile same-UID code.

| Rule | Section | Status |
|---|---|---|
| Canonical paths, atomic claims, global fences and lease renewals | 1, 2.2–2.5 | implemented in core and SQLite lease index |
| Claude Code PreToolUse enforcement | 2.6 | implemented for supported tools with authenticated host bindings; runtime permissions remain necessary |
| Git pre-commit and pre-push guards | 2.6 | implemented; install per checkout; authenticated caller and current worker fence required |
| Runner diff checks | 2.6, 7 | implemented for delegated runs; patch return requires lead review |
| Lead merge-time fence check | 2.8 | lead responsibility; no automatic merge authorization |
| Locks, atomic writes, schema migration and UTC timestamps | 3 | implemented; lead process anchors currently support Linux/macOS only |
| Board text validation and secret-pattern redaction | 3.4 | implemented; redaction cannot identify every secret |
| Explicit lead capability, session lease, handover and takeover | 4, 5 | implemented; stale or expired lead callers fail |
| Worker card capabilities | 5 | implemented with worker/card/fence scope; released capabilities fail |
| Verification and configurable review gate before done | 4 | implemented; executable checks use verify_cmd, not descriptive verify text |
| Urgent delivery and owner-note messages | 6 | implemented through authenticated hook context or worker inbox |
| Segmented transcript and A2A field names | 6 | implemented; this is not an A2A network server |
| Structured handbacks and runtime usage | 7 | implemented where adapters provide data; unknown usage remains unknown |
| Agent naming, session display and model detection | 5 | implemented; names are labels, not credentials |
| Worker contract and stdio MCP | 7 | implemented; MCP never acts as lead |

The board remains a cooperative same-UID boundary. OS permissions, sandboxing and human review are required for stronger isolation and external actions. See SECURITY.md and docs/ARCHITECTURE.md for the implemented trust model.
