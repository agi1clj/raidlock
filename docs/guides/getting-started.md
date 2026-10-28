# Getting started

Ten minutes, two terminals: install raidlock, create a board, make one card, claim it, change the code, verify it and close it. The demo repo comes from `raid quickstart --demo`, so nothing touches your own projects.

If you are an agent, read [skills/raidlock/SKILL.md](../../skills/raidlock/SKILL.md) first. It has the worker and lead loops in imperative form.

## 1. Install

```bash
uv tool install raidlock          # or: pipx install raidlock
raid doctor
```

If `uv` warns that `~/.local/bin` is not on your `PATH`, run `uv tool update-shell` and open a new terminal. `raid` and `raidlock` are the same CLI. You need Python 3.11 or newer, git, and Linux or macOS (on Windows, WSL2). On Linux, `raid verify` also needs bubblewrap (`sudo apt install bubblewrap`); `raid doctor` warns when it is missing. Claude Code plugin, Codex and MCP setup: [Install](install.md).

## 2. Create the board (lead terminal)

```bash
mkdir -p ~/demo/shop && cd ~/demo/shop
raid quickstart --demo
```

This creates `.board/`, a `core` team, a placeholder `example` card (ignore it or replace it) and `acme-shop/`, a git repo whose checkout test fails until carts reject empty input. In your own projects, run `raid up` in the folder that holds your repos.

A board started with `raid up` has no cards yet, so `raid ui` opens it with a **First quest** panel that walks steps 3 to 6 and ticks them off as you go.

`quickstart` prints the path of the lead secret file, never the secret, as a ready line. Paste it into the lead terminal (set it, do not export it):

```bash
LEAD_SECRET_FILE=<the path it printed>
```

It also prints `export RAIDLOCK_BOARD=...` and `export RAIDLOCK_AGENT=...` lines; run them in this lead terminal only, never in a worker's. Every lead write presents it with `--lead-secret`, from this session only. Never pass it to a worker. Switching sessions: [Lead session handover](install.md#lead-session-handover).

## 3. Create a card

```bash
raid --lead-secret "$LEAD_SECRET_FILE" new "Reject empty carts at checkout" \
  --id checkout --team core \
  --locks "acme-shop/src/checkout,acme-shop/tests/checkout" \
  --verify-cmd "python3 -m unittest"
```

`--locks` lists the `<repo>/<path>` paths the worker may edit. `--verify-cmd` is the check `raid verify` runs in a sandbox. Change it later with `raid --lead-secret "$LEAD_SECRET_FILE" edit checkout --verify-cmd "..."`.

The lead works through cards too. With the Claude Code plugin, a lead edit inside a git repo is denied unless the lead holds a card that covers the path, and a subagent launch is denied unless its prompt names an open card as `card <id>`. See [Hooks](hooks.md#board-first-subagents-pre-urgent). The lead is the session that ran `raid quickstart` or `raid up`; a Claude Code session opened later is a worker until it joins or takes the lead with [lead handover](install.md#lead-session-handover).

## 4. Claim it (worker terminal)

Open a second terminal with no `RAIDLOCK_*` variables from the lead:

```bash
cd ~/demo/shop/acme-shop
raid up --join --agent worker-checkout --card checkout --runtime human
```

`--runtime human` means you type the commands yourself; an agent names its own runtime (`claude`, `codex`, `shell`). `up --join` writes a private credential file and prints a `source` line. Run it, then claim:

```bash
raid claim checkout --checkout "$PWD"
```

`claim` prints `export RAIDLOCK_CAP=...` and `export RAIDLOCK_FENCE=...`. Paste both, then drop the reusable token, since the capability covers this card only:

```bash
unset RAIDLOCK_TOKEN
```

## 5. Change the code and hand it back

```bash
cat > src/checkout/cart.py <<'PY'
def validate(cart):
    """Return the cart if it can be checked out."""
    if not cart:
        raise ValueError("cart is empty")
    return cart
PY
python3 -m unittest
git commit -qam "Reject empty carts at checkout"
raid note checkout "validate() rejects empty carts"
raid move checkout review --note "python3 -m unittest: 2 passed"
```

Commit before you hand back: the lead verifies committed code only. If git asks who you are, set `git config user.name` and `user.email` in this repo.

## 6. Verify and close (lead terminal)

```bash
raid --lead-secret "$LEAD_SECRET_FILE" verify checkout
raid --lead-secret "$LEAD_SECRET_FILE" done checkout
raid ui --open
```

`verify` runs the verify command on the committed `HEAD` of the worker's checkout and records a signed receipt. `done` checks that receipt against the current `HEAD`. For a different checkout, pass `--cwd /path/to/checkout`. In real work, read the notes and integrate the change before you verify.

A human is not a second provider, so this walkthrough has no review gate. Once agents from two providers are on the board, every card needs a receipt and an approval from a reviewer on another provider: [Verification](verification.md#settings-and-defaults).

## Next

- To take another card as the same worker: `unset RAIDLOCK_CAP RAIDLOCK_FENCE`, source the worker file again, claim, and paste the new exports.
- Run Claude Code and Codex together: [Claude Code plus Codex](multi-provider.md).
- Lead habits: [Lead playbook](lead-playbook.md). Worker limits: [Worker rules](worker-rules.md).
- Every command: [Command reference](commands.md).
