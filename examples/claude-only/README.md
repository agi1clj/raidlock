# Claude Code only

Run these commands from the Claude Code session that will hold the lead lease. See [Install](../../docs/guides/install.md) for explicit session handover if a terminal already created the board.

```bash
cd ~/demo/shop
raid quickstart
LEAD_SECRET_FILE="/absolute/path/printed/by/raid"
raid --lead-secret "$LEAD_SECRET_FILE" new "Add /health endpoint" --id health --team core \
  --locks "acme-shop/src/health" --verify "uv run pytest tests/health"
raid --lead-secret "$LEAD_SECRET_FILE" new "Fix README typos" --id readme --team core \
  --locks "acme-shop/README.md"
```

In Claude Code, with the plugin installed ([Install](../../docs/guides/install.md)), paste:

```text
Use raidlock. Start one raid-worker subagent per claimable quest (`raid claimable`) in the background.
Keep lead credentials out of worker environments. Watch `raid who`; inspect and integrate changes,
then present --lead-secret with the printed path to verify and close each quest.
```

Replace the example secret-file path; do not export it. Each subagent uses its own worker token to claim, then its card capability and fence while working. Each subagent claims its quest, edits only its locked paths (the hook blocks anything else), and moves the quest to review.

Try it: ask the `readme` worker to edit `acme-shop/src/health/app.py`. The hook denies the edit and names the quest that holds the path.
