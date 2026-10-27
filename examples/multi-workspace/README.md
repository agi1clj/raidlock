# Multiple workspaces

Two workspaces on one machine, each with its own board, both containing a clone of `github.com/acme-demo/pocket-notes`.

```bash
cd ~/demo/shop && raid quickstart
cd ~/demo/notes && raid quickstart
raid workspaces
```

A quest in `shop` that locks `pocket-notes/src/parser` blocks a quest in `notes` that locks `pocket-notes/src`, because both clones resolve to the same repo identity. The refusal names the workspace, quest and agent holding the path.

`raid ui` serves every registered workspace (`--only` limits it to the current board). Leases, cost and agents are listed per workspace and across all of them.
