# Overview: providers, interfaces, FAQ and alternatives

Background the README leaves out: limits, providers, interfaces, how raidlock compares with other tools, and the FAQ.

## Security and limits

raidlock keeps cooperating agents out of each other's way. It is not a sandbox, and it is honest about what it does not do.

- **Cooperative boundary.** Worker tokens, card capabilities, the lead secret and the verify signing key are files under your user account. Any process running as you can read them. Contain agents with their own sandbox and permission settings. Threat model and reporting: [SECURITY.md](../../SECURITY.md).
- **What is enforced.** Writes outside a lease are refused by the Claude Code hook, the git guards and the runner diff. Delegated runs happen in a clone with no remote, committed files only and a cleared environment, and come back as a patch the lead reviews. Workers cannot mark work done; the MCP server never acts as lead.
- **What is not.** An agent that ignores the hook and edits files directly is caught at commit, push or patch time, not at the moment it writes. Quests in `todo` are not locked.
- **Verification needs a sandbox.** `raid verify` runs only under bubblewrap on Linux or `sandbox-exec` on macOS, and checks committed code only. A receipt proves the command passed, not that it tests the right thing.
- **Platforms.** Linux and macOS. On Windows, use WSL2 (expected to work, not yet tested). Native Windows is not supported: lead process-anchor verification is not verified and `raid verify` has no sandbox there.
- **Providers.** Gemini delegation is experimental. Claude Code plan limits are visible only if you pipe its statusline JSON to `raid limits --ingest-claude`; Gemini limits are not available. raidlock moves work between providers on its own only through `raid resurrect --sweep`, which is off until the lead enables it.
- **Numbers.** Cost comes from what each runtime reports, priced from a local file you edit; some default prices are placeholders. Reputation scores are experimental and grant no permissions.
- **The UI** listens on `127.0.0.1` only (in the [container image](docker.md), `0.0.0.0` inside the container, published on the host's loopback), checks the `Host` header, serves a fixed file list and answers `GET` only. It has no login; anyone with a shell on your machine can read it.
- **Not a session manager or tracker.** raidlock does not start terminals or agents for you, and a quest can point at a ticket but does not replace your issue tracker.

## Enforcement

Three layers, so the locks hold whatever the agent's vendor:

1. **Claude Code hooks.** PreToolUse hooks deny file edits on paths the agent does not hold, lead edits in a git repo without a held card, and subagent launches that name no open card (the last two are board-first rules, on by default).
2. **Git guards.** Pre-commit and pre-push hooks in each checkout refuse commits and pushes that touch paths outside the quest, or carry a stale fence. These cover every provider and humans.
3. **Runner diff.** Delegated runs (Codex, Gemini, shell) happen in a remote-less clone. Afterwards the runner diffs it and rejects changes outside the quest's locks.

## Interfaces: CLI and MCP

- **CLI** (`raid ...`, same as `raidlock ...`): used by humans, hooks, git guards and runners. Every command except `watch` has `--json` and stable exit codes. `raid` shows the everyday commands; `raid help advanced` the rest.
- **MCP server** (`raidlock mcp`, stdio): the same library as tools (`claimable`, `claim`, `heartbeat`, `note`, `lock`, `move`, `msg`, `inbox`) and read-only resources. It never acts as lead. See [MCP server](mcp.md).

There is no network API.

## Providers

| Runtime | How it joins | Enforcement | Cost data |
|---|---|---|---|
| Claude Code | Plugin: skill, `raid-worker` agent, hooks, `/raidlock:raid` | Hook + git guards | `claude -p` cost report, or subagent token totals |
| Codex CLI | MCP server, or `raid delegate <quest> --to codex` | Git guards + runner diff | `codex exec --json` usage events |
| Gemini CLI | `raid delegate <quest> --to gemini` | Git guards + runner diff | When reported |
| Shell / humans | CLI | Git guards (+ runner diff when delegated) | None |

## Claude Code and Codex

Plugin, MCP and ready-made lead and worker prompts: [Install](install.md). A worked two-provider run: [Claude Code plus Codex](multi-provider.md).

## Fewer tokens

- **Scoped context.** A quest carries what a worker needs: scope, locks, verify, hard limits. Workers don't re-read the whole conversation or repo.
- **Summaries, not transcripts.** Handbacks and notes are short evidence lines. The lead reads the board, a few hundred tokens, not each worker's full output.
- **Right model per task.** Cheap models for audits, strong models for design, other vendors where they are cheaper or better. Cost per quest shows what each choice cost.
- **No duplicate work.** Leases and claimable checks stop two agents from solving the same thing.
- **Cache-friendly.** Stable quest prompts and a fixed worker definition keep prompt prefixes identical, so provider caching applies.
- **Compact interfaces.** `--json` and MCP tools return small structured answers instead of prose.

## Compared with alternatives

These tools overlap with raidlock, and several do some things better. Each row was checked against the project's own README or docs on 2026-10-23.

| Tool | What it does well | Where raidlock differs |
|---|---|---|
| [mcp_agent_mail](https://github.com/Dicklesworthstone/mcp_agent_mail) | Agent mail with threads, search, acknowledgements and contact policies; file reservations with a TTL; an optional pre-commit guard; MCP configs for many agent CLIs. Its messaging is richer than ours. The Python version is no longer maintained in favor of a Rust rewrite. | raidlock centers on tasks with a review lane and lead-only done, leases keyed by repo identity across workspaces, and cost per task. |
| [beads](https://github.com/gastownhall/beads) | A dependency-aware issue graph, atomic claims and hash-based IDs that avoid merge collisions, synced through a git remote with Dolt. A better issue tracker for agents. | raidlock has a simpler task model and adds path leases, enforcement and a review gate. |
| [Claude Code agent teams](https://code.claude.com/docs/en/agent-teams) | Native to Claude Code: a mailbox per agent, a shared task list with dependencies, file-locked task claims, no extra install. Experimental and off by default. | Claude only, and it locks task claims, not code paths. raidlock spans providers and enforces paths. |
| [vibe-kanban](https://github.com/BloopAI/vibe-kanban), [claude-squad](https://github.com/smtg-ai/claude-squad), [ccmanager](https://github.com/kbwo/ccmanager) | Start and manage many agent CLIs in parallel: claude-squad with tmux and a git worktree per task, ccmanager without tmux, vibe-kanban with a kanban board, diff review and a preview browser. Much better at running sessions. vibe-kanban's README says it is sunsetting. | raidlock does not run sessions; it coordinates whatever sessions you run. They can be used together. |
| [container-use](https://github.com/dagger/container-use) | A fresh container and git branch per agent, served over MCP to any MCP-compatible agent. Stronger isolation than raidlock gives. Early development, marked experimental. | raidlock is about who may write which paths and when work is accepted, not about isolating the process. |

## FAQ

**Do I need a board for one agent?** No. A single session never needs a quest. raidlock pays off from two agents up.

**Does it work without Claude Code?** Yes. The CLI, MCP server and git guards work with any agent or a human. Claude Code adds the edit hook.

**Clones or worktrees?** Either. Each agent works in its own checkout; leases are keyed by repo identity, so clones and worktrees of the same repo share one lock space. Delegated external runs always use a fresh remote-less clone.

**What happens if an agent goes quiet?** Its lease expires after 45 minutes. The lead can run `raid --lead-secret FILE reap` to move stalled work to blocked, and another authenticated worker can claim it with `--take-over`. The fence changes, so the old owner's later writes are refused. `raid resurrect` hands the card to a fresh agent with a resume packet; automatic resurrection is off until you turn it on with `raid resurrect-policy`.

**Is `.board/` committed?** No by default. `init` gitignores it. Review the transcript before you choose to share it.

**Where does cost come from?** From the runtime's own usage report when it gives one. Prices live in a local pricing file you can edit.

**Is the theme required?** The web UI always uses the raid theme, and each section keeps its plain name as a subtitle. The CLI, protocol and copied commands use plain terms.

## Roadmap

Not in 0.1. Ideas, not promises:

- `raid agents-md`, a generator for the AGENTS.md worker snippet (today you paste it from [Install](install.md)).
- Gemini CLI setup through MCP (delegation with `--to gemini` exists).
- A bridge that exposes quests and transcripts to remote agents in A2A form (the transcript already uses A2A field names).
- Stronger OS isolation for worker processes; current credentials and hooks form a cooperative boundary.
- Weekly CI runs against the latest Codex and Claude Code CLIs.
- Quests created from issues or failing checks.
- Spend, throughput and stall dashboards per workspace and team.
- Benchmarks: the same quest delegated to different models, with verify results and cost.

## Why the name raidlock

The name borrows two ideas from raid groups in MMO games.

| Half | In a raid group | In raidlock |
|---|---|---|
| raid | One raid leader runs a coordinated group | One authenticated lead per board, with explicit handover; other agents join as workers or reviewers |
| raid | A ready check before the pull | A signed verify receipt before the lead can mark a card done |
| raid | Raid frames show each member's state | The web UI's raid frames view shows each agent's context and burn rate |
| lock | A raid lockout binds you to one instance until it resets | A path lease binds one holder to its paths until it expires or is taken over; the fence number rules out the previous holder |

The web UI uses a raid theme whose names and art are original to this project; the CLI and protocol use plain terms. raidlock is not affiliated with or endorsed by Blizzard Entertainment.
