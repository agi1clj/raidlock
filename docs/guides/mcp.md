# MCP server

`raidlock mcp` serves one board to an MCP client over stdio. It calls the same library as the CLI and takes the same locks. MCP is optional: an agent that can run shell commands spends fewer tokens calling `raidlock` directly. Use MCP when the agent has no shell, or when its sandbox blocks one.

## What it exposes

| Tool | Arguments | Returns |
|---|---|---|
| `claimable` | none | `{ready: [{id, title, takeover?}], blocked: [{id, why}]}` |
| `claim` | `id` | `{card, fence, expires, paths}` |
| `heartbeat` | `id`, `fence?` | `{card, expires, resumed?}` |
| `note` | `id`, `text`, `fence?` | `{card, noted}` |
| `lock` | `id`, `paths` (comma-separated `<repo>/<path>`), `fence?` | `{card, locks}` |
| `move` | `id`, `lane` (`review` or `blocked`), `note?`, `fence?` | `{card, lane}` |
| `msg` | `to`, `text`, `card?` | `{to, status, dropped?}` |
| `inbox` | none | `{messages: [{from, card, sent, text}], queued?}` |

Read-only resources:

- `raidlock://<workspace>/card/<id>`: the card brief, the same text as `raidlock brief <id>` (`Board.brief`, capped at `brief_max_tokens`).
- `raidlock://<workspace>/leases`: JSON list of `{card, agent, paths, expires, live}`. Fences are not included.

Each tool carries MCP `annotations`: `claimable` is `readOnlyHint` (Claude Code runs read-only calls in parallel), `heartbeat` is `idempotentHint` (safe to repeat after a dropped connection), and every tool is `destructiveHint: false` and `openWorldHint: false`. `inbox` is not read-only because it marks messages read. See [Mark tools that are safe to call twice](https://code.claude.com/docs/en/mcp#mark-tools-that-are-safe-to-call-twice).

The serialized tool list is about 2,400 characters (about 600 tokens). With Claude Code's tool search, which is on by default, only the tool names load at session start and a schema loads when Claude needs it. A test fails if the list goes over 1,500 tokens. `raidlock tokens` lists it as `mcp tool schemas`.

Outputs are compact JSON in one text block. A board refusal (lock held, stale fence, wrong token) comes back as a tool result with `isError: true` and the same body as `raidlock --json`: `{error, code, message, hint?, ...}`. An unknown tool or a malformed request is a JSON-RPC error.

## Identity

The server reads its identity from the environment only. Tool arguments never carry an agent name or a token. It serves the board named by `raidlock mcp --workspace PATH` (a workspace or `.board` folder; it refuses to start when there is none). Always pass it: clients start the server in their own working folder, which can be a worker checkout outside the workspace.

| Variable | Meaning |
|---|---|
| `RAIDLOCK_AGENT` (or `RAIDLOCK_AGENT_ID`) | agent name, from `raidlock join` |
| `RAIDLOCK_TOKEN` | that agent's token, printed once by `raidlock join` |
| `RAIDLOCK_HOME` | raidlock's state folder (tokens, index); default `~/.raidlock`. Pass it with `--env` when the board uses another home |
| `RAIDLOCK_BOARD` | workspace or `.board` folder when `--workspace` is not given; otherwise the nearest `.board` above the server's working folder |
| `RAIDLOCK_FENCE` | optional fallback fence for owner tools |

The server never acts as the lead. It rejects callers whose resolved name is the board's lead, including an authenticated lead. A name alone grants no lead authority; lead CLI writes require an explicitly presented secret and a current session lease. `RAIDLOCK_ROLE` is dropped from the server's environment. `move` accepts only `review` and `blocked`; closing a card stays with the lead's CLI (`raidlock --lead-secret FILE done`). Start the server with worker credentials and no inherited lead-secret variables.

Token authentication protects board writes. It does not restrict board reads: `claimable` and the read-only resources work without a valid token. Card briefs can include notes and the current fence. A client connected to this stdio server can read that board, so connect it only to clients trusted with those contents.

Fences: `claim` returns the fence, and the server remembers it for the rest of its process. After a server restart, pass `fence` explicitly or set `RAIDLOCK_FENCE`.

## Setup

Join the board first, as the worker that will use the client:

```bash
raidlock join --agent claude-worker-api --team core --runtime claude-code
```

### Claude Code

```bash
claude mcp add raidlock -e RAIDLOCK_AGENT=claude-worker-api -e RAIDLOCK_TOKEN=<token> -- raidlock mcp --workspace /absolute/path/to/workspace
```

Syntax checked against `claude mcp add --help` (Claude Code 2.1.308, 2026-10-22): `claude mcp add [options] <name> <commandOrUrl> [args...]`, `-e/--env KEY=value`, `-s/--scope local|user|project` (default `local`). An end-to-end run (`claude -p --mcp-config .mcp.json`) called `claimable` successfully.

Keep the default `local` scope, or `user`. `-s project` writes the token into `.mcp.json` in the repo, where it can be committed.

### Codex

```bash
codex mcp add raidlock --env RAIDLOCK_AGENT=codex-worker-api --env RAIDLOCK_TOKEN=<token> -- raidlock mcp --workspace /absolute/path/to/workspace
```

Syntax checked against `codex mcp add --help` (codex-cli 0.163.0, 2026-10-22): `codex mcp add <NAME> (--url <URL> | -- <COMMAND>...)`, `--env KEY=VALUE`. It writes `[mcp_servers.raidlock]` with an `env` table to `$CODEX_HOME/config.toml`. An end-to-end `codex exec` run called `claimable` successfully once `-c 'mcp_servers.raidlock.default_tools_approval_mode="approve"'` allowed the call: under `codex exec` (approval policy `never`), MCP tool calls that need approval fail. Set it per server in `config.toml` if needed.

To keep the token out of `config.toml`, Codex has `env_vars = ["RAIDLOCK_AGENT", "RAIDLOCK_TOKEN"]` under `[mcp_servers.raidlock]` to forward variables from the parent environment (documented at learn.chatgpt.com/docs/extend/mcp; not tested here).

What Codex gives the server, checked live with codex-cli 0.163.0 (2026-10-23) by registering a probe server with `codex mcp add`, confirming it with `codex mcp list` (Cwd column `-`, so no working folder is configured) and starting `codex exec` in a worker checkout:

- Working folder: the folder Codex runs in (`codex exec` or `codex` was started there, or `-C`), not the workspace. From a worker checkout outside the workspace, the nearest-`.board` lookup finds nothing, hence `--workspace`.
- Environment: only a fixed set from the parent (`HOME`, `LANG`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `TMPDIR`, `USER`, and a few platform variables) plus the server's `env` table. `RAIDLOCK_BOARD`, `RAIDLOCK_HOME` and other variables exported in your shell do not arrive; set them with `--env` or `env_vars`.

With `raidlock mcp --workspace` and those `--env` values, the probe answered `initialize` and `tools/list` from a worker checkout.

### Unverified

- Which working folder and environment Claude Code gives a stdio server; `--workspace` makes the folder irrelevant.
- Gemini CLI setup.

## Protocol

The server speaks MCP `2026-07-28` (stateless: every request carries `_meta["io.modelcontextprotocol/protocolVersion"]` and `clientCapabilities`; `server/discover` lists versions) and the legacy handshake versions `2025-11-25`, `2025-06-18` and `2025-03-26` (`initialize`, `notifications/initialized`, `ping`). Methods: `initialize`, `ping`, `server/discover`, `tools/list`, `tools/call`, `resources/list`, `resources/templates/list`, `resources/read`. A request without either an earlier `initialize` or modern `_meta` gets `-32602`; an unknown modern version gets `-32022` with the supported list. To poke at it by hand, use the MCP Inspector CLI: `npx @modelcontextprotocol/inspector --cli raidlock mcp --workspace /absolute/path/to/workspace --method tools/list`. `raidlock mcp` itself takes no protocol flag; it answers whichever handshake the client sends.
