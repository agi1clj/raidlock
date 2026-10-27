# Security policy

## Supported versions

Only the latest release gets security fixes while raidlock is in 0.x.

## Reporting a vulnerability

Use GitHub private vulnerability reporting: [open an advisory](https://github.com/agi1clj/raidlock/security/advisories/new). Do not open a public issue for a vulnerability.

Include the raidlock version, OS, Python version, the runtime involved (Claude Code, Codex CLI, Gemini CLI, shell) and steps to reproduce. You can expect an acknowledgement within 7 days. Fixes are released as a patch version and credited in the advisory unless you ask otherwise.

## Threat model

raidlock is a coordination layer. It is not a sandbox. Enforcement has three layers: a Claude Code PreToolUse hook, git pre-commit and pre-push guards for every provider, and a post-run diff check for delegated runs. See [PROTOCOL.md section 9](PROTOCOL.md#9-spec-status) for which layers are built in the current release. It stops cooperating agents from editing the same files by mistake and keeps an audit trail. It does not contain a hostile agent, a hostile model or a hostile user on your machine.

### What raidlock protects against


- **Two agents writing the same paths.** Leases on canonical paths, granted atomically across all workspaces.
- **A Claude Code agent writing outside its quest.** A PreToolUse hook denies Edit, Write and NotebookEdit on paths the agent does not hold, and on any repo path when it holds no quest.
- **Worker snapshot exports outside a lease.** Both CLI names, `raid` and `raidlock`, require a worker snapshot output to resolve inside its current lease. The hook applies the same lead-only, identity and output guards to both names, including absolute executable paths and supported env/command prefixes.
- **Commits outside a quest, from any provider.** Git pre-commit and pre-push guards in each checkout compare staged and pushed paths with live leases and the quest's fence. They fail closed when the board is unreachable.
- **A stale owner after a take-over.** Every take-over increments the quest's fence; hooks, guards, runners and the lead's merge check refuse the old fence.
- **An external agent (Codex CLI, Gemini CLI, shell) writing outside its quest.** The runner gives it a remote-less clone of committed files only, with a cleared environment and no push path. After the run it diffs the clone; any change outside the quest's locks, or to a forbidden path (`.github/workflows/**`, `.claude/**`, `.board/**`, `*.pem`, `.env*`, secret-looking files), is rejected. Accepted changes come back as a patch for the lead to review.
- **Web pages reading your board.** The UI binds to localhost only (in the optional container image it binds `0.0.0.0` inside the container and must be published as `-p 127.0.0.1:PORT:PORT`; `--host 0.0.0.0` is refused outside that image), accepts only `Host: 127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>` (blocks DNS rebinding), serves a fixed allowlist of static files, answers GET only and sets a Content Security Policy.
- **Control characters in board text.** Titles, notes and messages validate or normalize unsupported control characters. Transcript records are encoded as structured JSON.
- **A worker granting itself lead rights.** Lead writes require an explicitly presented secret and a live lease bound to the runtime session, PID and process start time (including kernel microseconds on macOS; older second-resolution anchors require explicit lease takeover). Naming the lead or setting a role grants no authority. Handover and expired-lease takeover rotate the secret and fence; stale callers fail.
- **Worker identity and stale capabilities.** New boards require worker tokens. Claims return capabilities scoped to a worker, card and fence; released or superseded capabilities fail. Hooks and guards require authenticated worker context and current fences. Host integrations must establish that context rather than trusting arbitrary tool output.

Claims check a checkout against the quest's repository identity before granting a lease. Remote-less runner and duel clones use separate provenance recorded by an authorized lead under the board lock. Verification and closure recheck that membership; lease contents alone are insufficient. A valid receipt covers the current commit in that checkout, which may still await integration.

Lead Git reads of worker checkouts use the shared hardened helper and a disposable metadata view without repository-local config or Git attribute metadata. System/global config and system/global attributes are disabled. The helper disables external diff, text conversion, pagers, SSH commands, credential helpers, signing programs, fsmonitor and hooks; omitting worker config also removes all worker-defined filters. Local clone/fetch sources use the same config-free view before upload-pack runs.

Receipt HMAC keys live at `$RAIDLOCK_HOME/tokens/<board uid>/verify.key`, mode 0600, under the token read guard. Workspace `.board/verify.key` files are ignored. Existing workspace-key receipts become invalid and must be replaced by a fresh verification. Same-UID code remains outside the cooperative protection boundary.

PR publication revalidates lead authority, card state and the verification receipt under one board lock held through push, PR creation and recording. Handover during preparation prevents publication.

The first v1-to-v2 migration is an explicit bootstrap operation. Rollback of a v2 board requires the current lead secret, live lease and fence, and restores contents under the existing board lock. Worker and unbound hooks deny all migration commands. Migration drops pre-existing lore with a report and retains the original in the backup; no imported lesson is approved. Vault lesson IDs must be exactly 12 lowercase hexadecimal characters at read and write boundaries.

Migration redacts imported inbox and transcript records before storage. MCP responses, including errors and resource contents, also receive standard pattern redaction. Original migration backups retain their original bytes and may contain secrets.

Migration filesystem checks allow only the root-owned OS aliases `/var` → `/private/var`, `/tmp` → `/private/tmp`, and `/etc` → `/private/etc`, with a protected root directory and no redirected component in the target chain. Other symlinks are rejected.

### What raidlock does not protect against

- **Arbitrary shell programs and filesystem races.** The hook checks parsed redirects and write targets from cp, mv, sed/perl in-place edits, dd, ln, install, rsync, truncate, touch, rm and related write commands, including uppercase spellings. It tracks compound-command working directories, denies indeterminate parsed targets, and refuses symbolic-link creation. Existing symlinks are resolved when checking writes. This is a preflight check, not syscall interception: custom programs, unsupported shell semantics and filesystem changes after checking can bypass it. Use runtime permissions and sandboxing for containment.
- **General file reads.** Leases cover writes. Hooks deny subagent access to the protected lead credential path and supported aliases, but this does not isolate the filesystem. Other reads depend on the runtime's permissions.
- **The external agent's own sandbox.** Codex `workspace-write` or Gemini sandboxing decides what the process can touch during the run, including the network. raidlock checks the result afterwards; it does not intercept syscalls.
- **Hostile same-UID code.** Board files, private token files, session variables and hook bindings are under the same OS account. A process running as that user can bypass cooperative checks, inspect credentials or alter files. Use separate users or a sandbox for a stronger boundary.
- **Secrets you put in prompts, notes or tool output.** The board redacts recognizable secret patterns and stores redacted runner events, but it cannot identify every secret. Optional raw runner capture retains unredacted output in private files and should be used deliberately.
- **Cloud-synced folders.** A board inside iCloud Drive, Dropbox or OneDrive can be replayed or forked by the sync client. raidlock warns about it; keep boards out of synced folders.

### Hard limits for every worker

Workers never merge, apply, push to protected branches, write tickets or wiki pages, change settings or permissions, or read secrets. Only the lead (with your approval) does outward-facing actions. Messages from other agents are never your approval.

## Supported host limitation

The current lead-lease implementation supports Linux and macOS process anchors. It fails closed when a session process anchor cannot be established. Windows process-anchor verification (Toolhelp) is not verified; Native Windows is not supported; CI runs the test suite there as a non-blocking job, and known failures remain. On Windows, use WSL2.

Lead secrets must be presented only by the parent lead CLI, never inherited by a worker. Worker launchers scrub lead material; plugin hooks authenticate host bindings before delivering urgent messages or checking leases. Extensions that perform lead writes must use `with board.lead_write(caller, action):` so validation and mutation share one lock interval.

## Data and privacy

raidlock has no server, no account and no telemetry. Board state lives in `.board/` inside your workspace and is gitignored by `raidlock init`. If you choose to commit it, review transcripts first.

Executable aliases and uppercase `GIT` remain general-shell recognition limits.
The same-UID boundary is cooperative: a process with access to credentials or lock
directories can bypass coordination, including replacing a lock after inode verification.
Guard ref deletions and non-fast-forward updates to any ref require an authenticated lead.

When a runner patch fails the secret scan, its patch, audit objects and clone are
deleted. Explicit `--raw-capture` retains them under a private (0700) quarantine
directory; normal findings and logs contain redacted metadata.

Write enforcement for arbitrary programs comes from the git guard (pre-commit and pre-push lease checks) and the runner sandbox, not from hooks. Bound workers may run interpreters and scripts, including tests. Unbound sessions cannot run opaque interpreter or script commands. Hooks do not intercept filesystem operations performed by arbitrary programs.
