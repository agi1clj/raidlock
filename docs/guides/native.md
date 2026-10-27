# Native integrations: Agent Teams and Codex

raidlock runs under the host's own multi-agent features instead of replacing them. This page says, per host, which API raidlock uses, what it enforces, and what it cannot do because the host has no API for it. Versions checked on 2026-10-23: Claude Code 2.1.308, codex-cli 0.163.0.

## Who counts as whom

Every hook call is classified before any rule runs:

| Role | How the hook decides |
|---|---|
| subagent | the payload carries `agent_id` |
| lead | no `agent_id`, and the payload `session_id` matches the live lead lease; a stored lead binding must also match its session and fence (`raidlock lead status`) |
| worker | no `agent_id`, and the session has a validated worker binding, or its environment names a joined worker (`RAIDLOCK_AGENT`, `RAIDLOCK_AGENT_ID`, `RAIDLOCK_CAP`) |
| unbound | any other session inside a raidlock workspace |

Claude Code exports `CLAUDE_CODE_SESSION_ID` to the shell, and raidlock binds the lead lease to that id. The lead's own session is therefore recognized from its first lead write without exporting anything. Naming the lead in the environment (`RAIDLOCK_AGENT=<lead>`) does not make a session the lead.

If the lease was bound somewhere else, for example `raid quickstart` or the first `lead heartbeat` ran in a plain terminal, the lead's Claude Code session is unbound to the hooks and gets `only the lead session may present the lead secret`. `raid lead status` shows the bound session. Fix it from the holder of the lease with `raid --lead-secret "$LEAD_SECRET_FILE" lead handover --to <Claude Code session id>`, or wait for the lease to expire and run `lead takeover` from the Claude Code session.

What an unbound session may do:

- Write inside a git repo: never inside another agent's lease. Unleased paths only when the board runs with `auth off`; on a token board (the default) every repo write is denied until the session claims a card.
- Lead actions: none. `done`, `reopen`, `verify`, `move <id> done`, `--for` and `--urgent` are denied, and so is presenting the lead secret (`--lead-secret`, `RAIDLOCK_LEAD_SECRET`) while the lead lease is live.
- Secrets: reading the lead secret, the token folder or any agent token is denied, through Read, Glob, Grep and Bash.
- Board commands other than the read-only ones must name an agent that is not the lead, and a literal `$RAIDLOCK_TOKEN` is denied, so an inherited token cannot be borrowed.

Recovery: when the lead lease has expired or was never bound to a session, an unbound session may run `raidlock --lead-secret FILE lead ...` (for example `lead takeover`). The CLI still checks the secret, the session and the process.

For supported file tools and parsed shell writes, bound worker sessions follow the subagent write rule: inside their own lease with the current fence, nowhere else. Only `cd` and `pushd` may name ancestors of credentials without triggering the ancestor read guard. Shell reads use each command segment's working directory; recursive reads and globs that can reach credentials are denied. Read, Glob and Grep also deny the folders that contain the secret.

## Claude Code Agent Teams

Agent Teams is experimental and off by default (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`). Teammates are not spawned in `-p` mode.

### In-process teammates

The hooks reference defines `agent_id` as the identifier "for the subagent or in-process teammate the hook fires in". An in-process teammate is therefore a subagent to raidlock, with the existing subagent rules: no write outside a lease it holds, no lead commands, no token reads, and every board command must name its agent. It joins the way a `raid-worker` subagent does: the lead runs `raidlock join --agent <name>` and puts the name and token in the spawn prompt; the teammate's first `raidlock claim` binds it.

Using `raid-worker` as a teammate type has two limits from the Agent Teams docs. A teammate spawned from a subagent definition does not get that definition's `skills`, so it starts without the raidlock skill preloaded; put the worker loop in the spawn prompt or let it load the skill on demand. And `raid-worker` sets `background: true`, which an in-process teammate cannot spawn, so only the lead starts `raid-worker` subagents, never a teammate.

Not available: automatic identity for in-process teammates. `SubagentStart` can add context but cannot set environment variables, and raidlock does not put tokens into model context on its own.

### Split-pane teammates (tmux, iTerm2)

Each split-pane teammate is a separate Claude Code process with its own session id. In 2.1.308 the hook input schema says `agent_id` is "absent for the main thread", so these teammates arrive as main sessions. Before this change a main session without a binding could write every unleased path in the workspace and read the lead secret; it is now an unbound worker (see above).

Auto-join, opt in:

```bash
raid --lead-secret "$LEAD_SECRET_FILE" join --agent mate-1 --team core --runtime claude-code
raid --lead-secret "$LEAD_SECRET_FILE" join --agent mate-2 --team core --runtime claude-code
raid --lead-secret "$LEAD_SECRET_FILE" bridge pool mate-1,mate-2
raid bridge show
```

`bridge pool` is lead-only and accepts only joined workers that are not the lead. It is stored in `.board/bridge.json`. When a main session that is not the lead starts in the workspace, the `SessionStart` hook:

1. Writes `unset` for any inherited `RAIDLOCK_LEAD_*` or `RAIDLOCK_ROLE` variable to `CLAUDE_ENV_FILE`, so the teammate's shell commands no longer carry lead material.
2. If the session is unbound and a pool identity is free, reserves one for this session id (an exclusive file under the plugin data folder), stores a token proof binding, appends `export RAIDLOCK_AGENT`, `RAIDLOCK_TOKEN` and `RAIDLOCK_BOARD` to `CLAUDE_ENV_FILE`, and adds one line of context naming the worker. The token goes to the environment file, not into model context.
3. Any main session that is not the lead's takes a slot, including a second session you open yourself in the workspace, so size the pool for that. A resumed session exports credentials only while its reservation matches its binding. `SessionEnd` clears the binding and reservation. Workers with live leases are never reassigned, including review and blocked leases. Reservations older than 12 hours may be reclaimed only without a live lease. Allocation, reclamation and release share a per-board pool lock, and tokens rotate before allocation. Removing a name from the pool stops it being handed out or re-exported.

The teammate then runs `raidlock claimable` and `raidlock claim <id>`; the `PostToolUse` hook records the card and fence, and edits inside that lease are allowed.

What this relies on and what was not verified:

- `CLAUDE_ENV_FILE` is documented for `SessionStart` hooks. Without it (other hosts, PowerShell hooks) nothing is reserved.
- The Agent Teams page says a teammate loads CLAUDE.md, MCP servers and skills. It does not say that split-pane teammates run plugin hooks. raidlock was not run against a live split-pane team. If a teammate does not run the hooks, raidlock enforces nothing in that session; the board's leases still stop that agent's claims from overlapping, but its edits are not fenced.

### Shared task list

Not integrated. `TaskCreated` and `TaskCompleted` carry `task_id`, `task_subject`, `teammate_name` and `team_name` and can block, and `TeammateIdle` can keep a teammate working, but no event fires when a teammate takes a task, the payloads carry no raidlock identity, and the task file format under `~/.claude/tasks/` is not documented. Cards are claimed with `raidlock claim`; a task is not a lease. raidlock does not wire these events.

## Codex

`codex features list` on 0.163.0 reports `hooks` as stable and enabled. The hook input schemas inside that binary match Claude Code's shape: `session_id`, `cwd`, `transcript_path` (nullable), `hook_event_name`, `model`, `permission_mode`, `turn_id`, and for tool events `tool_name`, `tool_input`, `tool_use_id`, with optional `agent_id` and `agent_type`. A deny uses the same `hookSpecificOutput.permissionDecision: "deny"` object raidlock already prints.

What raidlock enforces under Codex:

- Shell commands (`Bash`, `exec_command`, `shell`, and `shell_command`): the same `pre-bash` rules. The adapter normalizes string `command`, `cmd`, and `input` fields and the `exec_command` `workdir`. Missing, malformed, or conflicting commands are denied.
- `apply_patch` (`tool_input.command`, `tool_input.input`, or a raw string carries the patch): every `*** Add File`, `*** Update File`, `*** Delete File` and `*** Move to` path gets the Edit decision. A patch whose targets cannot be read is denied. Codex matches `apply_patch` against `Edit` and `Write` matchers, so if Codex loads the plugin's `hooks.json` its `Edit|Write|NotebookEdit|apply_patch` group routes it to `pre-write`. Loading the plugin under Codex was not verified; the configuration below does not depend on it.
- Subagents (`agent_id` present; Codex subagent hooks use the parent session id) follow the subagent rules.
- A Codex main session counts as the lead only if its hook `session_id` matches the live lead lease and any stored lead binding matches that session and fence. That match was not verified for Codex, so run lead commands from the lead's Claude Code session or a terminal.

Codex runs `SessionEnd` hooks with a 1 second default and accepts at most 3 seconds, so the example below sets 3. Install in `~/.codex/hooks.json` or a trusted project's `.codex/hooks.json` (Codex asks you to review non-managed hooks with `/hooks` first). This needs `raidlock` on `PATH`:

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "raidlock hook session-start", "timeout": 5}]}],
    "PreToolUse": [
      {"matcher": "^(apply_patch|Edit|Write)$", "hooks": [{"type": "command", "command": "raidlock hook pre-write", "timeout": 5}]},
      {"matcher": "^(Bash|exec_command|shell|shell_command)$", "hooks": [{"type": "command", "command": "raidlock hook pre-bash", "timeout": 5}]},
      {"matcher": "*", "hooks": [{"type": "command", "command": "raidlock hook pre-urgent", "timeout": 5}]}
    ],
    "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "raidlock hook post-tool", "timeout": 5}]}],
    "SubagentStop": [{"hooks": [{"type": "command", "command": "raidlock hook subagent-stop", "timeout": 5}]}],
    "SessionEnd": [{"hooks": [{"type": "command", "command": "raidlock hook session-end", "timeout": 3}]}]
  }
}
```

Joining: Codex hooks have no environment file, so there is no auto-join. Start Codex with the worker's identity in its environment (`RAIDLOCK_AGENT=<name> RAIDLOCK_TOKEN=<token> codex`); `SessionStart` validates the token and binds the session. A claim with an explicit token also binds it, but the Codex docs do not describe the `PostToolUse` `tool_response` shape for shell commands. If raidlock cannot read the claim output there, the session stays unbound and its repo writes stay denied: it fails closed.

MCP: `raidlock mcp` works from Codex (`[mcp_servers.raidlock]` in `config.toml`, see [MCP](mcp.md)). It gives the agent claim, heartbeat, note, lock, move and messages. It does not fence file writes. Hooks check supported tool targets; the git guard and runner sandbox enforce writes by arbitrary programs.

## Limits

- The hooks guard tool calls. A process the agent starts can still write files through an interpreter. See [Hooks](hooks.md).
- Role detection trusts the host's `session_id` and `agent_id`. A host that reuses one session id for several independent agents without `agent_id` would make them look like one session.

Write enforcement for arbitrary programs comes from the git guard (pre-commit and pre-push lease checks) and the runner sandbox, not from hooks. Bound workers may run interpreters and scripts, including tests. Unbound sessions cannot run opaque interpreter or script commands. Hooks do not intercept filesystem operations performed by arbitrary programs.
