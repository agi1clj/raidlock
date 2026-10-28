# Claude Code plus Codex

A Claude Code lead coordinates a Claude worker and a Codex worker. Run lead commands inside the session that owns the lead lease. Follow [Install](install.md) for the secret-file path and handover rules.

## Set up

```bash
cd ~/demo/shop
raid quickstart
LEAD_SECRET_FILE="/absolute/path/printed/by/raid"
raid --lead-secret "$LEAD_SECRET_FILE" team add core --mission "Shop features" --repos acme-shop --max 3
raid --lead-secret "$LEAD_SECRET_FILE" config set require_review.count 1
raid --lead-secret "$LEAD_SECRET_FILE" config set require_review.other_provider true
(cd acme-shop && raid guard install)
```

Replace the secret-file path. Keep this variable unexported and scrub lead credentials from worker environments. Install guards separately in each worker checkout; add `--hook pre-push` for push checks.

## Create independent quests

```bash
raid --lead-secret "$LEAD_SECRET_FILE" new "Validate cart at checkout" --id checkout --team core \
  --locks "acme-shop/src/checkout,acme-shop/tests/checkout" \
  --verify "Checkout tests pass" --verify-cmd "uv run pytest tests/checkout"
raid --lead-secret "$LEAD_SECRET_FILE" new "Paginate orders" --id orders --team core \
  --locks "acme-shop/src/orders,acme-shop/tests/orders" \
  --verify "Order tests pass" --verify-cmd "uv run pytest tests/orders"
```

On macOS, `raid verify` runs these `uv run` commands offline; warm its cache once as shown in [Verification](verification.md#the-verify-gate).

## Assign workers

Start a `raid-worker` Claude subagent on `checkout`, in its own checkout. It joins with `--runtime claude-code`, exports its own worker identity/token, claims, then uses the returned card capability and fence. It never receives the lead secret. See the exact CLI sequence in [Getting started](getting-started.md).

Delegate `orders` from the lead session:

```bash
raid --lead-secret "$LEAD_SECRET_FILE" delegate orders --to codex --sandbox workspace-write
```

The runner claims, heartbeats and records the handback. It runs Codex in a separate remote-less clone, checks the diff against allowed paths, and returns a patch for the lead to inspect. It does not merge the patch into your repository.

## Review and close

A delegated reviewer automatically receives the latest accepted worker `changes.patch` for the card: the runner applies and commits it in the private reviewer clone at the recorded worker base. You do not need to integrate that patch into your main checkout before requesting review.

If there is no accepted runner patch, the runner falls back to the card's recorded checkout and reviews its committed changes. Uncommitted edits are not included by that clone; commit the intended changes in the worker's own checkout before using this fallback. Without an accepted patch or a recorded checkout, delegation refuses the review.

```bash
raid --lead-secret "$LEAD_SECRET_FILE" delegate checkout --to codex --as reviewer
```

The reviewer records its own verdict. Manual reviews also require the reviewer's own authenticated session; `--by` names a reviewer and grants no authority. Join it with `--role reviewer` first, export its worker credentials, then run:

```bash
raid review checkout --by codex-reviewer-checkout --verdict approve --note "empty carts rejected; tests pass"
```

With the cross-provider review requirement enabled, the lead cannot close work until the required independent approval exists. A `changes` verdict returns the card to its owner in `doing`.

Gates switched on automatically follow the providers present. A provider counts while one of its agents was seen within the lease TTL (`ttl_minutes`) or holds a live lease. When only one provider is left, the next close relaxes the review and verify gates back to the defaults and records a `gate_defaults_relaxed` event; they switch on again when a second provider returns. Values you set with `raid config set` never change on their own, so set `require_review` yourself to keep a review gate on a mixed team. Cards that touch protected paths always need the cross-provider review.

A blocked close names the ways out: assign a reviewer, close merged work with `raid --lead-secret FILE close ID --evidence <PR URL or SHA> --note <text>`, or never require reviews with `raid --lead-secret FILE config set require_review 0`.

After inspecting and integrating the intended changes, release the worker's lease if it still points at the worker's clone, then verify from the lead session, using the checkout that contains those changes:

```bash
raid --lead-secret "$LEAD_SECRET_FILE" release checkout
raid --lead-secret "$LEAD_SECRET_FILE" verify checkout --cwd /absolute/path/to/reviewed-checkout
raid --lead-secret "$LEAD_SECRET_FILE" done checkout
```

`orders` was delegated, so land its patch instead of releasing and verifying by hand. `land` applies and commits the patch, verifies that commit and prints the next step:

```bash
raid --lead-secret "$LEAD_SECRET_FILE" land orders --cwd ~/demo/shop/acme-shop
raid --lead-secret "$LEAD_SECRET_FILE" done orders
```

Keep the [worker contract](install.md#codex-workers) in `AGENTS.md` for other providers.
