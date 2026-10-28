# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.4](https://github.com/agi1clj/raidlock/compare/v0.2.3...v0.2.4) (2026-10-28)


### Bug Fixes

* review gates follow the providers present ([#29](https://github.com/agi1clj/raidlock/issues/29)) ([2ee4f98](https://github.com/agi1clj/raidlock/commit/2ee4f987f7eadb524ca52d33e03f7b27caab41f4))

## [0.2.3](https://github.com/agi1clj/raidlock/compare/v0.2.2...v0.2.3) (2026-10-27)


### Bug Fixes

* check for updates every 10 minutes, and keep checking while raid ui runs ([#27](https://github.com/agi1clj/raidlock/issues/27)) ([e2d9eae](https://github.com/agi1clj/raidlock/commit/e2d9eae3707b6a85dba1cd0b3525c0586acce62e))

## [0.2.2](https://github.com/agi1clj/raidlock/compare/v0.2.1...v0.2.2) (2026-10-27)


### Documentation

* README renders on PyPI; summary matches the headline ([#25](https://github.com/agi1clj/raidlock/issues/25)) ([4786bc6](https://github.com/agi1clj/raidlock/commit/4786bc654e727392672ccd1bb6cc50aec5e27ddb))

## [0.2.1](https://github.com/agi1clj/raidlock/compare/v0.2.0...v0.2.1) (2026-10-27)


### Bug Fixes

* a failed update check retries within the hour instead of silencing notices for a day ([#22](https://github.com/agi1clj/raidlock/issues/22)) ([b27a07d](https://github.com/agi1clj/raidlock/commit/b27a07d33ac903762b8134e8d12303b3c8dac613))

## [0.2.0](https://github.com/agi1clj/raidlock/compare/v0.1.1...v0.2.0) (2026-10-27)


### Features

* raid land applies a delegated patch, verifies and hands it to review ([#11](https://github.com/agi1clj/raidlock/issues/11)) ([b98a108](https://github.com/agi1clj/raidlock/commit/b98a1080bb2e81efeba1ad7096a16fa54bb66fc9))
* tell users when a newer release exists ([#4](https://github.com/agi1clj/raidlock/issues/4)) ([f1ef8dc](https://github.com/agi1clj/raidlock/commit/f1ef8dcfe8d1b4012aff7bcb0dace0d5579b3cd6))
* **ui:** first-run tour on an empty board ([#10](https://github.com/agi1clj/raidlock/issues/10)) ([cc034dd](https://github.com/agi1clj/raidlock/commit/cc034ddf849b97ffe1634f6e77e37623bf83b57a))
* **ui:** notification bell scoped to realm or guild ([#5](https://github.com/agi1clj/raidlock/issues/5)) ([72cbc20](https://github.com/agi1clj/raidlock/commit/72cbc200da6e418d1d4b4816c91ad0245e88a8b1))


### Bug Fixes

* hooks deny lead commands from subagents behind wrappers; Windows hooks fail closed ([#17](https://github.com/agi1clj/raidlock/issues/17)) ([b97eb4b](https://github.com/agi1clj/raidlock/commit/b97eb4b667a1bafdab633fc33eb82c322e441be2))
* redaction keeps harmless RAIDLOCK_ settings visible ([#15](https://github.com/agi1clj/raidlock/issues/15)) ([99d5925](https://github.com/agi1clj/raidlock/commit/99d592535a4637496f7481172be22954b0ee4eef))
* review hints name a reviewer runtime from another provider ([#6](https://github.com/agi1clj/raidlock/issues/6)) ([9e6ecdb](https://github.com/agi1clj/raidlock/commit/9e6ecdb1327cd7e5022e085a9bf489c2d684152e))
* SHA-256 lesson IDs; Python badge shows 3.11+ ([#12](https://github.com/agi1clj/raidlock/issues/12)) ([57dbd8c](https://github.com/agi1clj/raidlock/commit/57dbd8c6ec7e01405444a540f0a97ab97ccd55bc))
* **ui:** boss board explains bosses named by the raid leader ([#9](https://github.com/agi1clj/raidlock/issues/9)) ([f2341c3](https://github.com/agi1clj/raidlock/commit/f2341c347cc19e10b6c6c6c872a6f3c4cde1df6d))
* update check accepts only plain release versions and survives a corrupt cache ([#13](https://github.com/agi1clj/raidlock/issues/13)) ([7092a6b](https://github.com/agi1clj/raidlock/commit/7092a6b176f5eb7fbdb9cf944194328acafafce0))
* verify gets a private uv cache; raid land runs without git hooks ([#16](https://github.com/agi1clj/raidlock/issues/16)) ([4b353af](https://github.com/agi1clj/raidlock/commit/4b353afb699afb1f45a05bf44dbf1b6ec4acc6d5))


### Documentation

* agent install guide that installs or upgrades to the latest release ([660951e](https://github.com/agi1clj/raidlock/commit/660951e2fb92470161962b3ee285ec3573e44218))
* align guides, examples and agent docs with the code ([#20](https://github.com/agi1clj/raidlock/issues/20)) ([1702d51](https://github.com/agi1clj/raidlock/commit/1702d51751d5101b6529806f8a8712f2cd6a8f7a))
* design tokens and shared UI components ([#7](https://github.com/agi1clj/raidlock/issues/7)) ([fe0ddbf](https://github.com/agi1clj/raidlock/commit/fe0ddbf721f4f6ae35bfacbde72d595ee8bf243b))
* headline Many agents. One repo. No collisions. ([#19](https://github.com/agi1clj/raidlock/issues/19)) ([fb98827](https://github.com/agi1clj/raidlock/commit/fb98827332452fa6941b96ef57cba89d2a15a85f))
* latest image tag by default; automated release flow ([689618b](https://github.com/agi1clj/raidlock/commit/689618b7be672e9e9c771101330147dd64f4b0cf))
* On Windows, with the WSL2 and Docker Desktop paths ([#8](https://github.com/agi1clj/raidlock/issues/8)) ([b7a3623](https://github.com/agi1clj/raidlock/commit/b7a362384500cbb338da95ff2075d663d4827371))
* OpenSSF Best Practices badge ([5b29132](https://github.com/agi1clj/raidlock/commit/5b2913218ba6fb20286926e9140abb8c464325ce))
* overview covers Codex hooks and current Gemini status ([#18](https://github.com/agi1clj/raidlock/issues/18)) ([43ad506](https://github.com/agi1clj/raidlock/commit/43ad506dafdf64ef7170ba7e589f8964713cc3ed))
* README leads with outcomes, a comparison table and the new features ([#14](https://github.com/agi1clj/raidlock/issues/14)) ([49af2eb](https://github.com/agi1clj/raidlock/commit/49af2ebc32c6841e0b884109e8b4ed481ab63a1f))
* social preview with the current message ([7f7316d](https://github.com/agi1clj/raidlock/commit/7f7316d95ac07e169e9fdbc283df4d6a3817b5f6))

## [0.1.1](https://github.com/agi1clj/raidlock/compare/v0.1.0...v0.1.1) (2026-10-27)


### Bug Fixes

* **types:** treat a missing lease age as zero ([af86a4d](https://github.com/agi1clj/raidlock/commit/af86a4dc34f2833a4d9c21a57398f2a0c0141c45))


### Documentation

* centered header with badges under the pitch ([501cdc2](https://github.com/agi1clj/raidlock/commit/501cdc2063fc16ebe59ec5c6de230da973d2d75d))
* one title, the logo ([4ce8f35](https://github.com/agi1clj/raidlock/commit/4ce8f3572375ff3f98d4768b7aeda9e8cb85ee9a))
* raid theme note; gold logo, favicon and social preview ([7bfbb13](https://github.com/agi1clj/raidlock/commit/7bfbb1338668b1b3b2b5d361e94324fa493095ec))
* sharper UI tour as animated WebP ([6aee151](https://github.com/agi1clj/raidlock/commit/6aee151dd2b95f785e510b2b65d8d5990c079e8a))
* short PR template ([14402b9](https://github.com/agi1clj/raidlock/commit/14402b92dee168bf5bdbcffa7d2d69917e9d988f))

## [Unreleased]

## [0.1.0] - 2026-10-27

### Added

- Board-first enforcement, on by default: the lead edits only inside a card it holds (`require_card_for_lead_edits`), and a `Task`/`Agent` subagent starts only when its prompt names an open card as `card <id>` (`board_first_subagents`). Either can be turned off with `raid config set`.
- README, install prompt, getting-started and agent instructions rewritten for first-time users and agents; a UI tour GIF in the README.
- The realm page has the same header and tabs as a guild: Guilds, Tavern chatter, Treasury.
- Optional hardened container image (`ghcr.io/agi1clj/raidlock`) for `raid ui`, `raid mcp` and sandboxed `raid verify` (bubblewrap inside the container, receipt isolation `bwrap-container`), with `raid ui --host 0.0.0.0` allowed only inside the image, a compose example, and a multi-arch signed release with an SBOM. See [Docker image](docs/guides/docker.md).
- Raid theme: party roles (Tank, Healer, DPS) on raid frames, derived from recorded card activity.
- Raid theme: ambient motion (drifting embers, torch-lit logo, breathing quests in progress, bobbing avatars); achievements are on by default.
- Raid theme: gold-on-purple favicon; the guild or realm level sits in the header.
- The raid theme is the only theme in Settings; the theme switch is gone.
- Raid theme: a victory screen with a fanfare when a boss is slain, four new feats (Clean Run, Peacekeeper, Swift Blade, Night Watch), titles earned from feats, a guild daily streak, and more motion (button shimmer, glowing Needs you, swaying icons, XP shine, lifting cards, view transitions). Text sizes start at 12 px. The Raid calendar is removed; it repeated lease details shown on cards. Tabs are ordered by how often a lead needs them.
- `?` shows the keyboard shortcuts. The usage strip says Mana (context used) and dims a provider whose data is over an hour old.
- Raid theme typography: Cinzel (bundled, SIL OFL 1.1) for titles and tabs, the system sans-serif for body text.
- Tavern chatter shows the newest message first, marks messages read when the tab is open, and uses the full width.
- In-page search: press `/` to filter cards and chat by title, id, agent, ticket or repo, with a match count. Treasury is its own tab in each guild and includes the results by model.
- Tavern chatter is a view tab; the status filter row is gone because each filter repeated a War room section. Zones list only real repos. Needs you drops questions on closed cards or already answered, and patches of cards closed with evidence. The footer credits the author and links the version.
- Raid theme: Boss board view with boss HP bars for cards many others depend on, and a deterministic loot bag per agent.
- Lead-only `edit CARD --boss on|off` marks a large quest as a boss on the boss board.
- `plan import` reads OpenSpec and Spec Kit `tasks.md` files, or a change folder, as they are: only unchecked tasks, task numbers kept as tickets.
- Workspace board with tasks, teams, review gates and JSON CLI output.
- Virtual path leases with fencing, a single lead lease and lead handover.
- Agent-to-agent (A2A) transcript with provider and model attribution.
- Multi-provider runners for Claude, Codex, Gemini and shell commands.
- Claude Code plugin with hooks, a `/raid` skill and a worker agent.
- Git guard and MCP server.
- Web UI with raid and plain themes and sounds.
- TUI watch view.
- Spend tracking and limits per model, plus delegation budgets.
- Lore lessons vault with approved lessons included in worker briefs.
- Patch secret scan and lock inference with a read-only suggestion preview.
- Built-in recipes and plan imports.
- Experimental reputation tracking.
- A five-step playground (`examples/playground`) with a setup script, ready prompts and check-it-works steps for Claude Code and Codex.
- MCP tool annotations (`readOnlyHint`, `idempotentHint`, `destructiveHint`, `openWorldHint`).
- Web board layout: attention-sorted workspace cards with repo chips and one status line, idle workspaces on one line, a sticky workspace bar with a repo filter, a "Needs you" strip for review and blocked cards above the lanes, and agents in a right-hand rail (collapsible on mobile).
- Web board sound cues for message sent, queued, delivered and read, notes, claims, reviews, blocks, done, stalls and lease expiry, with a per-event toggle and a louder default volume.
- `raid next` and `raid routing`: provider routing per label and path with plan headroom checks.
- `raid duel`: run a card with two providers and pick the winner on signed verify receipts.
- `raid resume`: token-capped resume packets with validated partial work for cards a dead or expired worker left.
- `raid resurrect` and `raid resurrect-policy`: opt-in reassignment of stalled cards with attempt and budget limits.
- `raid approvals`: one human inbox with copy-ready commands, also shown as a strip in the web board.
- `raid pr`: push a card's verified commit and open a pull request with a lock check, a history scan and redaction.
- `raid pr-link` and `raid pr-sync`: link cards to pull requests, track checks, and close merged cards only through the verify and review gates; the web board shows a pull request pill per card.
- `raid bench`: a reproducible offline overhead benchmark with a CI budget.
- `raid map`: a territory map of locked paths per repo, who holds each and where claims clash; also a web board view.
- `raid replay`: board state rebuilt from the transcript at any record, with a web board view and timeline.
- `raid graph`: the card dependency graph and a lock-contention heatmap by path and day.
- `raid frames`: context fill and burn rate per agent, with handoff warnings near the context limit.
- `raid achievements`: opt-in achievements earned from recorded board events.
- `raid treasury`: burn rate, a month-end spend forecast and budget alerts, also in the web board's spend panel.
- `raid bridge` with Claude Code Agent Teams and Codex hooks: unbound host sessions count as workers, and teammates can auto-join from a lead-set pool (`docs/guides/native.md`).
- `raid edit`: the lead sets, changes or clears a card's verify command after creation.
- `raid quickstart --demo`: also creates a small git repo, so the getting-started guide runs as written.
- A copy-paste prompt in the README and Install guide that has a coding agent install raidlock, add the Claude Code plugin and create a board without reading the lead secret.
- Raid theme on by default with game wording across titles, views, buttons, empty states and status; Guild level per workspace and Realm level across workspaces from closed against open cards, with ranks, a quest progress bar and a level-up chime and animation.
- Needs you shows the first 5 items with a per-kind count and **Show all N**.
- Lead-only `close --evidence HTTPS_URL|SHA --note TEXT` records external delivery as Done without verification or review credit.

### Changed

- `raid done` warns when it closes a card that no worker claimed, or one with no verify command; the README states the actual gate rule: a receipt is needed when the card has a verify command or `require_verify` is on. Gate errors name the fix (`raid edit`, `raid verify`, `raid review --assign` or `config set require_verify false`).
- Hints, `raid approvals` and the web approvals strip print lead commands with `--lead-secret` and the secret file's path, and never point the lead at `join`. `raid up` in the lead's own session no longer joins it as a worker.
- Runtime names accept the short forms `claude`, `codex` and `gemini` and the long forms `claude-code`, `codex-cli` and `gemini-cli` in every command.
- Every command flag has help text, the main commands show examples, and each command's own options come before the common ones.
- `raid config`, `budget`, `routing`, `resurrect-policy` and `bridge` show their current state when run without an action.
- Hook latency budgets are p50 150 ms and p95 300 ms per hook, and cover `session-end`.
- Web board: agents get distinct colours on the lock map and the frames view, reputation evidence is a readable list, and the scorecard and reputation tables stack on narrow screens.
- A shell runner that reports a priced model bills that model's provider in spend and the scorecard, instead of `local`.
- A verify run that cannot start its command (exit 126 or 127, or a missing tool) is no longer proposed as a lore lesson.
- `raid claim` prints warnings as `#` comments after its `export` lines, so the block can be pasted as is.
- The `/raid` slash command is a skill (`skills/raid/SKILL.md`); `install-commands` writes `~/.claude/skills/raid/SKILL.md` and removes its older command file.
- Worker instructions pass identity with `--agent`, `--cap` and `--fence` flags, which match the skill's `Bash(raidlock *)` permission.
- Delegated `claude -p` runs no longer persist sessions (`--no-session-persistence`).

### Fixed

- Removed the `MultiEdit` and `LS` tool names, which current Claude Code no longer has, from hook matchers, the worker agent and the runner's allowed tools.
- `raid migrate` imports v1 boards losslessly: log, lead, card bodies, settings and leases.
- Web board accessibility (landmarks, heading order, card buttons, contrast in both themes, focus return from menus), stricter security headers and static-file revalidation.
- Status filter chips give the chosen lane the full board width and keep the sidebar beside it.
- Dependencies view draws only linked cards in compact columns, shows missing cards, and supports keyboard and the repo filter.
- UX audit: opaque card drawer and menus, tables keep their columns outside the narrow sidebar, stronger control borders and map tile contrast, raid wording leads in every view, capped Done lane and hidden empty Cancelled lane in the quest log, underline view tabs, scrollable header rows on phones, 24px targets, wrapped drawer commands, unread divider only with unread messages, quieter stall timer.
- UX follow-up: toasts stay above the open card drawer, Score has its own panel, the overview counts lead items the way the workspace does, the workspace status line no longer repeats the chip counts, a shorter phone header, activity rows lead with who and when, the activity filter lists recent cards, empty blocks open with one empty line, the plain close button reads **Close card**, and the cost forecast has an axis with bars scaled to spend.
- Detached lead commands explain why they are refused.
- Card ids cut at a word boundary.
- Sandboxed verify allows the uv cache offline and explains signal kills with a next step.
- Sandboxed verify on macOS runs the system `/usr/bin/python3`; the sandbox lets a command inspect its own processes and still hides the rest.
- A verify command missing from the sandbox's `PATH` (exit 127) says so and names the fix.
- Done, close, cancel and pr-sync delete the card's delegated clones and agent homes; logs, patches and run records stay. `raid doctor` shows the disk raidlock uses.
- A delegated agent whose CLI rejects an option is reported as likely too old, with the fix.
- `done` accepts the lead's receipt for a delegated card when its commit descends from the run's base; the runner marks its lease explicitly.
- Pruning skips runs still in progress; the container sandbox hides `/proc` and turns on only inside a real container.
- Review and blocked leases become advisory holds with a configurable maximum duration; hooks require a hard lease to write.
- Added fenced, audited lead lock replacement and removal, including release during review.
- Explicit workers can join inside the lead session, inherit a card team, and use separate private credential files.
- Claims resolve repo paths from the board workspace and validate only explicitly selected checkouts.
- Authenticated lead commands renew leases within a seven-day expiry grace; authenticated UI sessions send heartbeats.

### Security

- Explicit lead-session authentication and fenced worker capabilities.
- Lease checks in hooks, isolated runner clones and post-run diff checks.
- Patch scanning and budget enforcement in the worker path.
- Secret-path checks and snapshot output protection.
- `raid migrate` treats v1 boards as untrusted input.

### Known limitations

- The same-UID cooperative boundary assumes cooperating processes; it does not isolate hostile processes running as the same operating-system user.
- Windows process anchors are unverified.
- Reputation is experimental; provider usage and quota data depend on what each runner reports.

[Unreleased]: https://github.com/agi1clj/raidlock/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/agi1clj/raidlock/releases/tag/v0.1.0
