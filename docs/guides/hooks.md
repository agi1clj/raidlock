# Claude Code hooks

The raidlock plugin installs eight hook handlers. Each one runs `"${CLAUDE_PLUGIN_ROOT}/bin/raidlock" hook <event>`, reads one JSON object from stdin, and calls `raidlock.core`. Nothing needs to be installed besides Python 3.11 or newer.

The `bin/raidlock` shim never trusts whatever `python3` comes first on `PATH` (on macOS that is often the system 3.9). It runs the plugin's `src` with the first interpreter that is 3.11 or newer, in this order: `RAIDLOCK_PYTHON`; the interpreter of an installed `raidlock` or `raid` console script (the shebang line written by `uv tool install`, `pipx` or `pip`); `python3.15` down to `python3.11`; then `python3`. When only an older Python is found, raidlock stops with one line naming the interpreter and how to install a supported one. A guard hook (`pre-write`, `pre-read`, `pre-bash`, `pre-urgent`) inside a raidlock workspace denies the tool call with that line; elsewhere the hook exits 1, which Claude Code shows as a non-blocking hook error. The shim is a POSIX `sh` script, so it needs Linux or macOS; native Windows hook execution is unverified.

## What the hooks do

| Event | Matcher | Mode | Runs | Reads | Writes | Prints |
|---|---|---|---|---|---|---|
| SessionStart | all | sync, timeout 5 s | `session-start` | hook input; `.board/board.json` found from `cwd` or `RAIDLOCK_BOARD`; `.board/bridge.json` | `${CLAUDE_PLUGIN_DATA}/sessions/<session>.json`; when `RAIDLOCK_AGENT` is set, the main-session binding; for a session that is not the lead's, `CLAUDE_ENV_FILE` lines (unset inherited lead material; a pooled identity when one is reserved) | one line of context only when a pooled identity was reserved |
| PreToolUse | `Edit\|Write\|NotebookEdit\|apply_patch` | sync, timeout 5 s | `pre-write` | the binding; `.board/board.json` and `agents.json`; the lease index in `$RAIDLOCK_HOME/raidlock.db`; the file's `.git/config` | nothing | nothing on allow; a deny reason of one line |
| PreToolUse | `Bash\|exec_command\|shell\|shell_command` | sync, timeout 5 s | `pre-bash` | same as `pre-write`, for redirect and `tee` targets | nothing | nothing on allow; one line on deny |
| PreToolUse | `Read\|Glob\|Grep` | sync, timeout 5 s | `pre-read` | requested paths and protected lead-secret paths, including aliases | nothing | nothing on allow; one line on deny |
| PreToolUse | all | sync, timeout 5 s | `pre-urgent` | `Task`/`Agent` prompts (board-first rule); authenticated binding and pending urgent messages | drains delivered urgent messages through core | quoted JSON context only when pending |
| PostToolUse | all | async | `post-tool` | the binding; the last 64 KB of the session or subagent transcript; the agent's token file | the binding; `${CLAUDE_PLUGIN_DATA}/hb/<session>-<agent>`; a lease heartbeat and a model update through the core API; for the main session, at most once a minute, its context use in `$RAIDLOCK_HOME/claude-limits.json` (see [Limits](limits.md)) | nothing |
| SessionEnd | all | sync, timeout 5 s | `session-end` | the binding | frees the session's pooled identity | nothing |
| SubagentStop | all | async | `subagent-stop` | the binding; `last_assistant_message`; the subagent transcript tail | one `handback` record in the board transcript (`.board/transcript/`) through the core API | nothing |

No hook opens a network connection. No hook writes `.board/` except through `raidlock.core` (heartbeat, agent model, transcript). Bindings store a SHA-256 token proof or a card-scoped capability in private files (directories mode 0700, files mode 0600). They never store the raw agent token or lead secret.

When `CLAUDE_PLUGIN_DATA` is unset (for example when the module runs outside a plugin), state goes to `$RAIDLOCK_HOME/claude-code/`.

### Token cost

- Allow is silent: exit 0, empty stdout, empty stderr. Claude sees nothing.
- A deny is one JSON object with a reason under 200 characters, such as `raidlock: held by w-two on c2 in workspace ws. Ask the lead or pick another card.`
- SessionEnd, PostToolUse and SubagentStop never print. SessionStart prints one line of context only when it reserved a pooled identity ([Native integrations](native.md)).
- No command receives an explicit permission pre-approval. Pending urgent messages add bounded, quoted JSON context once, through the wildcard PreToolUse handler, including for Agent, Task and MCP tools.

## Who is calling

Claude Code puts `agent_id` and `agent_type` in the hook input of a subagent or in-process teammate and leaves them out for a main session, including a split-pane Agent Teams teammate. A main session is the lead only when its validated binding names the lead or its `session_id` is the session the lead lease is bound to; any other main session is a worker (with a worker binding or worker environment) or unbound. See [Native integrations](native.md). raidlock names are not Claude Code ids, so the hooks keep a binding per session and agent in `${CLAUDE_PLUGIN_DATA}/bindings/<session>/<agent_id or main>.json`: `{name, board, card, fence, model}` plus either a token proof or a card capability.

A binding is written only from a successful Bash call (`PostToolUse`; failures go to `PostToolUseFailure`, which raidlock does not handle) of `raidlock join|up|claim|note|heartbeat|lock`:

- `name` from `export RAIDLOCK_AGENT=` in the output, else `--agent`, else a `RAIDLOCK_AGENT[_ID]=` prefix on the command.
- `card` from `Claimed <id>:`; `fence` from `export RAIDLOCK_FENCE=`, `--fence` or the prefix.
- `board` from `export RAIDLOCK_BOARD=`, `--board`, a `RAIDLOCK_BOARD=` prefix, the folder of a preceding `cd`, or the nearest `.board` above `cwd`.
- `raidlock move <card> review|blocked|todo|done` and `release <card>` clear `card`.

Command output supplies candidate identity fields. The hook validates the presented token proof or capability against core before accepting them; a successful-looking command or fabricated output alone does not establish identity. The main session falls back to `RAIDLOCK_AGENT`, `RAIDLOCK_TOKEN` and `RAIDLOCK_FENCE` from the environment. Subagents never inherit that environment identity: an unbound subagent has no name.

## Edit enforcement (`pre-write`)

1. Path from `tool_input.file_path` (Edit, Write) or `tool_input.notebook_path` (NotebookEdit). For Codex `apply_patch`, every `*** Add File`, `*** Update File`, `*** Delete File` and `*** Move to` path in `tool_input.command`; a patch with no readable target is denied.
2. A path with a `.board` segment is denied for everyone.
3. The board is the bound board, else the nearest `.board` above the file, else above `cwd` (the main session also tries `RAIDLOCK_BOARD`). No board: silent allow. Projects that do not use raidlock are never affected.
4. `Board.check_write(path, caller)` maps the file through its own checkout's git remote to repo identity and canonical path, then checks every live lease in every registered workspace.
5. Subagent or worker session: allowed only inside a live lease it holds with its current fence. Otherwise denied: held by another agent, stale fence, or no lease.
6. The lead's session: allowed inside a card the lead holds. With `require_card_for_lead_edits` on (the default), a path in a git repo that no card of the lead covers is denied: add and claim a card first. With it off, free paths stay writable without exporting an identity. Another agent's lease always denies.
7. Unbound session: denied inside any lease; free paths only when the board runs with `auth off`.

## Subagent limits (`pre-bash`)

A subagent never acts as the lead. Its shell commands are denied when they reach a lead-only raidlock command directly or through a launcher (`xargs`, `parallel`, `find -exec`, `uv run`, `uvx`, `pipx run`, `npx`, `env`, `nice`, `timeout`, `nohup`, `command`), when interpreter or shell code (`python -c`, `sh -c`, heredocs, piped shells) mentions raidlock, when the command passes `--lead-secret` or sets `RAIDLOCK_LEAD_SECRET`, and when the raidlock verb is computed (`raid $V`). Ordinary worker commands such as `raid note`, `raid move CARD review` and `raid show` still run. On native Windows the guard hooks deny every tool call with a pointer to WSL2 instead of failing open.

## Board-first subagents (`pre-urgent`)

With `board_first_subagents` on (the default), a `Task` or `Agent` tool call inside a raidlock workspace is denied unless its prompt or description contains `card <id>` for a card that is not done or cancelled (also `card: <id>`). Create the card with `raid new`, then write `card <id>` in the prompt.

Both rules apply at edit time in the agent hooks; the git commit guard still checks only other agents' leases. Both are on for every board. Turn either off with `raid --lead-secret FILE config set require_card_for_lead_edits off` or `config set board_first_subagents off`.

## Bash rules (`pre-bash`)

The command is split on `;`, `&&`, `||`, `|`, `&` and newlines with `shlex`; leading `VAR=value`, `env`, `command`, `exec`, `nohup` and `time` are skipped.

- Subagents, worker sessions and unbound sessions: `raidlock done`, `reopen`, `verify`, `move <id> done`, `land`, `delegate`, `pr`, `migrate` and other lead-only commands, `--for` and `--urgent` are denied (also via `python -m raidlock`, behind wrappers such as `xargs` or `sh -c`, and with `--lead-secret` before the command). A main session also counts as a worker when the command carries `RAIDLOCK_AGENT_ID` or `RAIDLOCK_CAP`, or when `RAIDLOCK_AGENT` or `--agent` names an agent other than the board's lead. Only the lead's session keeps those commands; the CLI still requires the lead secret for them.
- Worker and unbound sessions: presenting the lead secret (`--lead-secret`, `RAIDLOCK_LEAD_SECRET=`) and touching the token folder are denied while the lead lease is live. When it has expired or is unbound, an unbound session may run `raidlock --lead-secret FILE lead ...` to recover.
- Subagents and unbound sessions: every `raidlock` command other than `show`, `who`, `claimable`, `check`, `transcript`, `workspaces`, `wait`, `next`, `approvals`, `help` and `version` must name its agent (`--agent`, or `RAIDLOCK_AGENT=` as a prefix or an `export` earlier in the same command), and that name must not be the board's lead. A literal `$RAIDLOCK_TOKEN` is denied too. Reason: a subagent inherits the main session's environment. When the lead exported `RAIDLOCK_AGENT` and `RAIDLOCK_TOKEN` before starting Claude Code, an unnamed `raidlock claim` from a subagent would otherwise run as the lead. The live test caught exactly this.
- Subagents always, and worker or unbound sessions while the lead lease is live: token-folder access (`~/.raidlock/tokens`, `$RAIDLOCK_HOME/tokens`) in Bash is denied, including protected aliases. For every session except the lead's, Read, Glob and Grep deny the lead secret, its parent directories and any agent token, through symlinks, hardlinks and case aliases.
- Everyone: writes into `.board/` are denied. That covers redirect targets, `tee`, `rm`, `mv`, `cp`, `rsync`, `install` and `ln` destinations, `touch`, `truncate`, `chmod`, `dd of=`, `mkdir`, and `sed -i` or `perl -i`. Reading `.board/` files is allowed, and so is any `raidlock` command.
- Everyone: redirect and `tee` targets inside a repo get the same decision as an Edit. `/dev/*` is skipped.
- Git commands are parsed for their paths and effects. Configuration and execution overrides, unsupported subcommands, and ambiguous forms are denied. Mutating commands must satisfy the lease checks, including paths in nested repositories. Passing the guard emits no explicit permission approval.

This is a guard against model mistakes, not a sandbox. A command can still write through an interpreter (`python -c`) or a script. The git guard and the lead's review are the next layers.

## Heartbeat and model (`post-tool`)

- When the authenticated binding has a card and a current fence, and the throttle file is older than 5 minutes, the hook calls `Board.heartbeat`. That is at most one heartbeat per agent per 5 minutes. Token proofs are revalidated against the stored token; card capabilities remain restricted to their card and fence and are never upgraded to a full token.
- The hook reads the last 64 KB of the transcript and caches the last assistant model in the binding. It updates the agent record only when the authenticated caller can make that change. A card capability does not authorize a global agent-record update.

## Handback (`subagent-stop`)

For a bound subagent, `last_assistant_message` (capped at 8,000 characters) becomes a `handback` transcript record on its current card, or the card it last moved to review: `from = {agent, provider: anthropic, runtime: claude-code, model}`. The transcript redacts secrets before writing. Unbound subagents leave no record.

## Failure behaviour

- Unreadable input or no board: silent allow.
- A `RaidlockError` in a guard (for example a board with a newer schema): deny with the error message.
- Any other exception in a guard: deny with `hook error (<type>); see <plugin data>/errors.log`. Raidlock workspaces fail closed; other projects are unaffected because they have no board.
- Exceptions in the async hooks are logged to `errors.log` and never shown.

## The /raid command

The plugin ships the user-invoked skill `/raidlock:raid up|who|claimable|show <id>|claim <id>` (`skills/raid/SKILL.md`); Claude Code always prefixes plugin skills with the plugin name. Lead commands such as `done` need `--lead-secret`, so run them from the shell. For a bare `/raid`, the user runs `raidlock install-commands` once. It writes the personal skill `~/.claude/skills/raid/SKILL.md` (or under `$CLAUDE_CONFIG_DIR`) with the same wrapper, removes an older `~/.claude/commands/raid.md` only if raidlock wrote it, `disable-model-invocation: true` and a `raidlock-managed: install-commands` line in the frontmatter. It is idempotent, refuses to overwrite a file without that marker, and `raidlock install-commands --uninstall` removes only a file that carries it. Nothing installs it automatically. Claude Code ignores frontmatter fields it does not recognize without reporting an error (code.claude.com/docs/en/skills, checked 2026-10-23), and a live run (Claude Code 2.1.308) of a skill with this exact frontmatter loaded it, substituted `$ARGUMENTS` and honoured `allowed-tools` with no warning in the debug log. The marker would be rejected only by skill packaging or upload outside Claude Code, which this personal skill never goes through.

## Token report

`raidlock tokens` lists three contributors from this module (`register_tokens`): `claude-code hooks` (0 on allow, with the deny size and the 8,000-character cap on urgent context), `claude-code skill listing` and `claude-code agent listing` (the `raidlock` skill and `raid-worker` agent descriptions that Claude Code keeps in context). `claude plugin details raidlock` gives Claude Code's own estimate: about 190 always-on tokens, about 1,200 when the skill is invoked.

Measured on the playground (2026-10-22, macOS), driving each handler with real hook payloads: every allow, the session start and every async handler printed 0 bytes; a lease deny printed 220 bytes (about 55 tokens); one pending urgent message printed 224 bytes. Through the `bin/raidlock` shim (2026-10-23, macOS, Python 3.15) an allowed `pre-write` took 90 to 250 ms, mostly Python start-up.

## Testing

`tests/hooks` feeds sample stdin JSON (`tests/hooks/fixtures/*.json`) to each event: allow, deny, no task, other fence, stale fence, subagent `done` attempt, `.board` writes, binding, heartbeat throttle, model detection from a fixture transcript, handback and session start. Allow cases run in a subprocess and assert empty stdout.
