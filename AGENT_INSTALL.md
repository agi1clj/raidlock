# Install raidlock (instructions for coding agents)

You are a coding agent (Claude Code, Codex, Gemini CLI or similar) asked to install or update raidlock for the user. Follow these steps in order. Report each result in one line. Stop and ask the user when a step needs a decision or a permission you do not have.

## Rules

- Install the latest release. Never pin an old version unless the user asks.
- Never read, print or copy the contents of a lead secret file. Pass only its path with `--lead-secret`.
- Do not change files in the user's repos during installation.

## 1. Check prerequisites

```bash
python3 --version   # needs 3.11 or newer
git --version
uv --version || pipx --version
```

If neither `uv` nor `pipx` exists, ask the user before installing uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`). On Windows, stop and tell the user to run everything inside WSL2.

## 2. Install or upgrade the CLI

```bash
raid --version 2>/dev/null && uv tool upgrade raidlock || uv tool install raidlock
```

With pipx: `pipx upgrade raidlock || pipx install raidlock`. If `raid` is not found afterwards, run `uv tool update-shell` (or `pipx ensurepath`) and tell the user to open a new terminal.

## 3. Verify

```bash
raid --version
raid doctor
```

`raid doctor` must end with `Healthy.` Fix what it reports, or tell the user what you cannot fix.

## 4. Add or update the Claude Code plugin (Claude Code only)

```bash
claude plugin marketplace add agi1clj/raidlock 2>/dev/null || claude plugin marketplace update raidlock
claude plugin install raidlock@raidlock || claude plugin update raidlock@raidlock
```

Tell the user to start a new Claude Code session: the hooks load at session start.

## 4b. Add the hooks to Codex (Codex only)

Codex runs the same raidlock hooks. Read `~/.codex/hooks.json` if it exists, show the user what you will add, and ask before writing. Merge these entries into it (keep the user's existing hooks), then tell the user to review and approve them with `/hooks` inside Codex:

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

A Codex worker starts with its identity in the environment (`RAIDLOCK_AGENT=<name> RAIDLOCK_TOKEN=<token> codex`). Details: https://github.com/agi1clj/raidlock/blob/main/docs/guides/native.md#codex

## 4c. Other agents (Gemini CLI and others)

They coordinate through the CLI (`raid claim`, `raid note`, `raid move`) or the MCP server (`raid mcp`); they get no edit-time hooks. Tell the user that only the git commit guard and `raid delegate` sandboxing protect against them, and suggest delegating their work with `raid --lead-secret FILE delegate CARD --to gemini`.

## 5. Create a board

Ask the user which folder holds their repos, then run `raid up` there. To try raidlock first, run `raid quickstart --demo` in an empty folder instead. Both print the path of the lead secret file; remember the path, never the contents.

## 6. Confirm board-first enforcement and follow it

Board-first rules are on by default. Confirm, in the board folder:

```bash
raid config get require_card_for_lead_edits   # expect true
raid config get board_first_subagents         # expect true
```

Explain them to the user in two sentences: every piece of work is a card on the board first; the hooks deny a lead edit inside a git repo without a held card, and a subagent launch whose prompt does not contain `card <id>` for an open card. Only the user can turn a rule off (`raid --lead-secret FILE config set <key> off`); do not do it yourself.

From now on, work board-first yourself: create a card with `raid --lead-secret FILE new "<title>" --locks <repo>/<path>`, claim it before editing, put `card <id>` in every subagent prompt, and close the card with evidence when the work is done.

## 7. Finish

Run `raid show`, tell the user to open the board with `raid ui --open`, and stop. Point them to the getting-started guide: https://github.com/agi1clj/raidlock/blob/main/docs/guides/getting-started.md
