# Playground

A five-step tour on a tiny fictional repo, `acme-shop`. One Claude Code lead, one Claude Code worker and one Codex worker share a board with three cards. Plan on ten minutes. Linux or macOS, Python 3.11 or newer, git, and the `claude` and `codex` CLIs.

## 1. Install

```bash
uv tool install raidlock        # or: pipx install raidlock
raid --version
```

To try an unreleased change, install from a raidlock checkout and add the plugin from it: see [Install from a checkout](../../CONTRIBUTING.md#install-from-a-checkout).

Install the Claude Code plugin (skill, `raid-worker` agent and hooks) inside any Claude Code session:

```text
/plugin marketplace add agi1clj/raidlock
/plugin install raidlock@raidlock
```

From a checkout, `claude --plugin-dir /path/to/raidlock` loads the plugin for one session instead. Codex needs no plugin; it reaches the board through the MCP server in step 4.

## 2. Set up the playground

```bash
bash examples/playground/setup.sh
```

The script makes a temporary workspace (pass a folder to choose one) with:

- `acme-shop/`, a git repo with a cart module, a test and a remote URL on `example.com`;
- `workers/claude/acme-shop` and `workers/codex/acme-shop`, one checkout per worker;
- a board with team `core` and three cards:

| Card | Locks | State |
|---|---|---|
| `health` | `acme-shop/src/health`, `acme-shop/tests/test_health.py` | todo, for the Claude worker |
| `cart` | `acme-shop/src/cart`, `acme-shop/tests/test_cart.py` | todo, for the Codex worker |
| `readme` | `acme-shop/README.md` | doing, leased for four hours by `scout`, a shell worker |

It also joins `codex-worker`, hands the lead lease to a fresh Claude Code session ID, and prints the exact commands for the next steps:

```text
Playground ready in /tmp/raidlock-playground.Ab12Cd
...
Lead secret file (pass the path, never the contents): ~/.raidlock/tokens/<id>/claude-lead.token
The lead lease is handed over to Claude Code session 7f0c...
Step 3, lead:          cd "/tmp/raidlock-playground.Ab12Cd" && claude --session-id 7f0c...
Step 4, Claude worker: cd "/tmp/raidlock-playground.Ab12Cd/workers/claude/acme-shop" && claude
Step 4, Codex worker:  codex mcp add raidlock-playground --env RAIDLOCK_AGENT=codex-worker --env RAIDLOCK_TOKEN=... -- raidlock mcp --workspace /tmp/raidlock-playground.Ab12Cd
                       cd "/tmp/raidlock-playground.Ab12Cd/workers/codex/acme-shop" && codex
Step 5, watch:         cd "/tmp/raidlock-playground.Ab12Cd" && raidlock ui --open
```

The lead lease is bound to a session, so the lead must be the Claude Code session started with that `--session-id`. Keep the terminal output; the Codex token is printed only once.

## 3. Start the lead in Claude Code

Run the printed `Step 3` command, then paste this prompt with the printed secret-file path filled in:

```text
You lead a raidlock board in this folder. The lead secret file is <LEAD SECRET FILE>. Pass it as
--lead-secret <LEAD SECRET FILE> on lead commands; never print its contents or give it to a worker.
Run `raid who` and `raid show`. Cards health and cart wait for workers; scout holds readme, leave it.
Do not edit acme-shop yourself. Each time a card reaches review, inspect the change
(`git -C workers/claude/acme-shop status` and `git -C workers/claude/acme-shop diff` for health,
`git -C workers/codex/acme-shop diff` for cart), then run
`raid --lead-secret <LEAD SECRET FILE> verify <id> --cwd workers/claude/acme-shop` for health or
`--cwd workers/codex/acme-shop` for cart, and, if it passes, `raid --lead-secret <LEAD SECRET FILE> done <id>`.
Report each verdict in one line.
```

Check it works: `raid who` lists `claude-lead` with the session ID from step 2, and the first lead command succeeds. `the lead lease is held by session <id>, not <other>` means Claude Code was started without the printed `--session-id`; restart it with the flag. If the lease expired before you started (45 minutes), run `raid --lead-secret <LEAD SECRET FILE> lead takeover` from this session.

## 4. Start the workers

Claude Code worker. Run the printed command in a second terminal, then paste:

```text
You are the raidlock worker claude-worker on team core. Follow the raidlock skill's worker loop.
Join with `raidlock join --agent claude-worker --team core --runtime claude-code`, then claim card health.
In this checkout only, add src/health/health.py with health() returning {"status": "ok"} and a test in
tests/test_health.py. Run `python3 -m unittest discover -s tests` and leave the change uncommitted.
Note the result, then move health to review with the test output as evidence.
Before you finish, try once to edit README.md and tell me the reason the hook gives.
```

Check it works: `/raidlock:raid who` in that session shows the board. The README edit is denied with `held by scout on readme`.

Codex worker. Run the printed `codex mcp add` line in a third terminal, check it with `codex mcp list` (the `raidlock-playground` server is listed and enabled), start `codex` in the printed checkout, and paste. Codex starts the server in that checkout with a reduced environment, so the line names the workspace with `--workspace` and passes `RAIDLOCK_HOME` when you set one:

```text
You are the raidlock worker codex-worker. Use the raidlock-playground MCP tools for every board action.
Call claimable, then claim cart. In this checkout only, make checkout() in src/cart/cart.py raise
ValueError for an empty cart and add a test for it in tests/test_cart.py. Run
`python3 -m unittest discover -s tests`, call note with the result, then move cart to review with the
test summary as the note. Leave the change uncommitted.
```

Check it works: `/mcp` in Codex lists eight raidlock tools. Codex asks before each MCP call unless you approve the server's tools.

## 5. Watch

```bash
cd /tmp/raidlock-playground.Ab12Cd && raid ui --open
```

The board opens at `http://127.0.0.1:8765`. You should see `readme` held by `scout`, `health` and `cart` move from todo to doing to review as the workers claim and hand back, and then to done after the lead verifies them. `raid who` and `raid watch` show the same from a terminal.

## Clean up

```bash
codex mcp remove raidlock-playground
rm -rf /tmp/raidlock-playground.Ab12Cd
```
