# Claude Code plus Codex

Claude builds one quest, Codex builds another, and each reviews the other's work before the lead closes either.

```bash
raid quickstart
LEAD_SECRET_FILE="/absolute/path/printed/by/raid"
raid --lead-secret "$LEAD_SECRET_FILE" config set require_review.count 1
raid --lead-secret "$LEAD_SECRET_FILE" config set require_review.other_provider true
(cd acme-shop && raid guard install)
raid --lead-secret "$LEAD_SECRET_FILE" new "Validate cart at checkout" --id checkout --team core --locks "acme-shop/src/checkout" --verify "uv run pytest tests/checkout"
raid --lead-secret "$LEAD_SECRET_FILE" new "Paginate orders" --id orders --team core --locks "acme-shop/src/orders" --verify "uv run pytest tests/orders"
raid --lead-secret "$LEAD_SECRET_FILE" delegate orders --to codex --sandbox workspace-write
```

Run the setup inside the actual lead session and replace the printed secret-file path; keep it unexported. Workers need separate credentials, card capabilities and checkouts. Delegated reviewers receive the latest accepted worker patch automatically. If no accepted runner patch exists, they review committed changes from the recorded checkout; see the full walk-through for that fallback.

Start a Claude Code worker on `checkout` from the lead session. When both are in review, run `raid --lead-secret "$LEAD_SECRET_FILE" delegate checkout --to codex --as reviewer` and start a Claude reviewer subagent on `orders`. `raid --lead-secret "$LEAD_SECRET_FILE" done ID` is refused until each has an approval from the other provider.

In scripts and CI, write `raidlock` instead of `raid`. The full walk-through is in [docs/guides/multi-provider.md](../../docs/guides/multi-provider.md).
