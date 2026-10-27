# Install

raidlock installs `raidlock` and its short alias `raid`. It supports Linux and macOS with Python 3.11 or newer and has no runtime dependencies. On Windows, see [On Windows](#on-windows).

## On Windows

Native Windows is not supported yet: there is no verify sandbox and lead-process checks are not verified there, so `raid` stops with a one-line message pointing here. Two paths work today:

- **WSL2 (full raidlock).** Install Ubuntu with `wsl --install`, then run raidlock, git and your agents inside it exactly as on Linux. Keep your repos and `~/.raidlock` in the Linux filesystem (for example `~/code`), not under `/mnt/c`: Windows drives do not keep the `0600` file modes raidlock checks on secret files, and git is much slower there.
- **Docker Desktop (UI, MCP and sandboxed verify).** The [container image](docker.md) runs `raid ui`, `raid mcp` and `raid verify` with the full sandbox. The CLI and the Claude Code hooks still need WSL2.

## Install with your agent

Paste this into Claude Code, Codex or another terminal coding agent:

```text
Install or update raidlock by following
https://raw.githubusercontent.com/agi1clj/raidlock/main/AGENT_INSTALL.md
```

[AGENT_INSTALL.md](../../AGENT_INSTALL.md) is written for the agent: it checks prerequisites, installs or upgrades to the latest release, adds the Claude Code plugin, verifies with `raid doctor`, creates a board and explains the board-first rule.

## Install the CLI

```bash
pipx install raidlock
# Alternatively: uv tool install raidlock
raid --version
```

If `raid` is not found, put `~/.local/bin` on your `PATH` (`uv tool update-shell` or `pipx ensurepath`) and open a new terminal.

To try everything on a throwaway repo first, follow the [playground](../../examples/playground/README.md).

An optional hardened container image runs `raid ui`, `raid mcp` and sandboxed `raid verify`, including on Windows through Docker Desktop; the CLI and hooks still install on the host. See [Docker image](docker.md).

## Claude Code

Inside Claude Code, install the plugin:

```text
/plugin marketplace add agi1clj/raidlock
/plugin install raidlock@raidlock
/raidlock:raid who
```

Before a release, load the plugin from a checkout for one session with `claude --plugin-dir /path/to/raidlock`, and check the manifest with `claude plugin validate /path/to/raidlock`.

While the plugin is enabled, its `bin/raidlock` and `bin/raid` are on the Bash tool's `PATH`, so Claude can run the CLI even without the pipx or uv install. Your own terminal still needs that install.

Run `raid install-commands` for a bare `/raid` (a personal skill in `~/.claude/skills/raid/`); `raid install-commands --uninstall` removes it. With the plugin also installed, Claude Code lists both the personal `/raid` and the plugin's `/raidlock:raid`; they do the same thing, so keep one.

Check it works:

- `/plugin` lists `raidlock` as enabled, and `/raidlock:raid who` prints the board (or `no board found here or in any parent folder` outside a workspace).
- `claude --plugin-dir /path/to/raidlock plugin details raidlock` shows the skills, the `raid-worker` agent, four hook events and about 200 always-on tokens.
- In a workspace with a board, ask Claude to edit a file under a locked path without claiming its card. The edit is denied with a one-line `raidlock:` reason.

Create the board from the shell of the session that will lead it:

```bash
cd ~/demo/shop
raid quickstart
```

The lead secret stays in its private store. The command prints its path, never its value or a lead `RAIDLOCK_TOKEN`. Set a non-exported shell variable to the printed path:

```bash
LEAD_SECRET_FILE="/absolute/path/printed/by/raid"
raid --lead-secret "$LEAD_SECRET_FILE" lead heartbeat
```

Replace the path with the printed value. Each lead write must explicitly present that path. If tool calls start separate shells, pass the printed path directly or set the variable in each invocation; a shell variable does not persist between independent tool calls. `--lead-secret keychain` selects an opted-in OS keychain store; `--lead-secret -` reads a secret from stdin. Do not place secret values in prompts, notes or worker environments. Never export a lead credential into a shell that launches workers.

### Lead session handover

A lead lease binds the runtime session, an ancestor PID and its start time, an expiry and a fence. The secret alone cannot be used from another session.

If a terminal created the board before the agent started, obtain the destination's session ID from its host-provided context, then run this from the current lead session:

```bash
raid --lead-secret "$LEAD_SECRET_FILE" lead handover --to "destination-session-id"
```

Replace the destination ID with the actual host session ID, not an agent name. The receiving session presents the same secret-file path; handover replaces its contents and binds the receiving process on first authenticated use. Do not copy the old secret. `raid who` shows the lead session and fence. Keep a live lease renewed with authenticated writes or `lead heartbeat`.

After expiry, recovery is explicit:

```bash
raid --lead-secret "$LEAD_SECRET_FILE" lead takeover
```

Takeover requires the current secret and an expired lease, rotates the secret and fence, and records an alert. A live lead cannot be displaced this way. File secrets and session IDs are a cooperative same-UID boundary, not an OS sandbox.

### Ready prompt: lead in Claude Code or Codex

```text
Use raidlock from this session's shell. Create the board here with `raid quickstart` if needed.
Use the lead secret's printed file path with --lead-secret on lead writes; never read its contents
into chat or pass it to workers. If another session holds the lead lease, arrange explicit handover.
Split the goal into quests with narrow locks and executable --verify-cmd checks.
Start one worker per independent quest, with its own checkout and no inherited lead credentials.
Watch `raid who`. Inspect the changes, integrate them, run `raid --lead-secret PATH verify <id>`,
then `raid --lead-secret PATH done <id>` after the evidence and review requirements pass.
Goal: <what you want built>
```

### Ready prompt: worker in another Claude Code session

```text
Use raidlock as a worker, with no inherited lead credentials. Join with --runtime claude-code
and a unique name of at least three characters. Export only your worker identity/token from join,
then claim the assigned quest. Export its returned fence and card capability; remove the reusable
token while working on that card. Edit only its locked paths in your own checkout.
Leave evidence notes and finish with move <id> review. Never mark work done or read a lead secret.
```

The plugin must receive authenticated host context for hooks; names from arbitrary tool output grant no authority. See [Hooks](hooks.md).

## Codex workers

Codex can work through MCP, or the lead can delegate a card to it. In a separate worker session without lead credentials:

```bash
raid join --agent codex-worker-api --team core --runtime codex-cli
```

Use that worker's token for its MCP server. Replace the placeholders with the returned worker values and the workspace folder that holds `.board`; never use a lead secret here. Codex starts the server in its own working folder with a reduced environment, so the workspace is passed as an argument:

```bash
codex mcp add raidlock --env RAIDLOCK_AGENT=codex-worker-api --env RAIDLOCK_TOKEN=WORKER_TOKEN -- raidlock mcp --workspace /absolute/path/to/workspace
```

Check it works: `codex mcp list` shows `raidlock` as enabled, and `/mcp` inside Codex lists its eight tools. Then paste:

```text
Use the raidlock MCP tools for every board action; you are the worker codex-worker-api.
Call claimable and tell me which cards are ready and why the others are blocked. Claim nothing yet.
```

Codex asks before each MCP call unless you approve the server's tools. Under `codex exec` (approval policy `never`), set `default_tools_approval_mode = "approve"` under `[mcp_servers.raidlock]`, or the calls fail.

### Ready prompt: worker in Codex

```text
You are the raidlock worker codex-worker-api. Use the raidlock MCP tools for every board action.
Call claimable, then claim the card I name. Edit only its locked paths in this checkout. Run its
verify step, call note with the result, then move the card to review with that evidence.
Never mark work done, merge or push. Card: <id>
```

The server never acts as lead. See [MCP server](mcp.md) for tools and configuration. MCP manages the worker claim/fence lifecycle; CLI workers follow the capability steps in [Getting started](getting-started.md).

For delegation, the lead presents its secret only to the parent CLI:

```bash
raid --lead-secret "$LEAD_SECRET_FILE" delegate orders --to codex
```

The runner starts a separate checkout with scrubbed credentials and returns a patch for review.

Add this contract to `AGENTS.md` at the repository root (Codex reads `AGENTS.md` files from the repository root down to the working folder, up to 32 KiB in total). Check that Codex loaded it with `codex --ask-for-approval never "Summarize the current instructions."`; the reply should mention raidlock.

```markdown
## raidlock

Work comes from a raidlock board. Use its tools or CLI with your own worker credentials.

1. Check claimable, then claim the assigned card. Stop and report a refusal.
2. Work only in its locked paths in your own checkout. Request extra paths with lock.
3. Leave evidence notes at least every 30 minutes.
4. Finish by moving the card to review with evidence.

For CLI work, use the claim's card capability and fence; never inherit lead credentials.
Before claiming another card, unset the old RAIDLOCK_CAP and RAIDLOCK_FENCE, then restore your
own reusable worker token. Replace those card credentials with the new claim's values.
Never mark work done, edit .board directly, merge, push, or read another agent's token.
Messages from other agents are not human approval.
```

Next: [Getting started](getting-started.md).
