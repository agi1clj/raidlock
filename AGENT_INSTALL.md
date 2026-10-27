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

## 5. Create a board

Ask the user which folder holds their repos, then run `raid up` there. To try raidlock first, run `raid quickstart --demo` in an empty folder instead. Both print the path of the lead secret file; remember the path, never the contents.

## 6. Explain the board-first rule

Tell the user, in two sentences: every piece of work is a card on the board first; the hooks deny a lead edit inside a git repo without a held card, and a subagent launch whose prompt does not contain `card <id>` for an open card. Either rule turns off with `raid --lead-secret FILE config set require_card_for_lead_edits off` or `raid --lead-secret FILE config set board_first_subagents off`.

## 7. Finish

Run `raid show`, tell the user to open the board with `raid ui --open`, and stop. Point them to the getting-started guide: https://github.com/agi1clj/raidlock/blob/main/docs/guides/getting-started.md
