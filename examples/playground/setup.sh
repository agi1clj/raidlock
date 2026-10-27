#!/usr/bin/env bash
set -euo pipefail

if ! command -v raidlock >/dev/null 2>&1; then
  echo "raidlock is not on PATH. Install it first: uv tool install raidlock (or: pipx install raidlock)" >&2
  exit 3
fi

for var in $(env | sed -n 's/^\(RAIDLOCK_[A-Z_]*\)=.*/\1/p'); do
  [ "$var" = RAIDLOCK_HOME ] || unset "$var"
done

WS="${1:-$(mktemp -d "${TMPDIR:-/tmp}/raidlock-playground.XXXXXX")}"
mkdir -p "$WS"
WS="$(cd "$WS" && pwd -P)"
if [ -e "$WS/.board" ] || [ -e "$WS/acme-shop" ]; then
  echo "$WS already holds a playground; pass an empty folder" >&2
  exit 3
fi
cd "$WS"

json() {
  python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"
}

mkdir -p acme-shop/src/cart acme-shop/src/health acme-shop/tests
cat > acme-shop/README.md <<'EOF'
# acme-shop

A tiny fictional shop used by the raidlock playground.

Run the tests with `python3 -m unittest discover -s tests`.
EOF
: > acme-shop/src/cart/__init__.py
cat > acme-shop/src/cart/cart.py <<'EOF'
def checkout(items):
    total = sum(item["price"] * item["qty"] for item in items)
    return {"items": len(items), "total": total}
EOF
: > acme-shop/src/health/__init__.py
cat > acme-shop/tests/test_cart.py <<'EOF'
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cart.cart import checkout


class CheckoutTest(unittest.TestCase):
    def test_total(self):
        self.assertEqual(checkout([{"price": 3, "qty": 2}])["total"], 6)


if __name__ == "__main__":
    unittest.main()
EOF
printf '__pycache__/\n' > acme-shop/.gitignore

git -C acme-shop init -q -b main
git -C acme-shop add -A
git -C acme-shop -c user.name="Playground" -c user.email="playground@example.com" commit -q -m "acme-shop: initial"
git -C acme-shop remote add origin https://example.com/acme/acme-shop.git

for who in claude codex; do
  git clone -q acme-shop "workers/$who/acme-shop"
  git -C "workers/$who/acme-shop" remote set-url origin https://example.com/acme/acme-shop.git
done

LEAD_SECRET_FILE="$(raidlock init --runtime claude-code --json | json token_file)"
lead() {
  raidlock --lead-secret "$LEAD_SECRET_FILE" "$@" >/dev/null
}
lead team add core --max 3
lead new "Add a health check" --id health --team core \
  --scope "Add src/health/health.py with health() returning {\"status\": \"ok\"}, plus tests/test_health.py" \
  --locks "acme-shop/src/health,acme-shop/tests/test_health.py" \
  --verify "health tests pass" --verify-cmd "python3 -m unittest discover -s tests -p 'test_health.py'"
lead new "Reject empty carts" --id cart --team core \
  --scope "checkout([]) raises ValueError; keep the existing total test green" \
  --locks "acme-shop/src/cart,acme-shop/tests/test_cart.py" \
  --verify "cart tests pass" --verify-cmd "python3 -m unittest discover -s tests -p 'test_cart.py'"
lead new "Document local setup" --id readme --team core \
  --scope "Explain how to run the tests in README.md" \
  --locks "acme-shop/README.md"

SCOUT_TOKEN="$(raidlock join --agent scout --team core --runtime shell --json | json token)"
SCOUT_CLAIM="$(raidlock claim readme --agent scout --token="$SCOUT_TOKEN" --checkout "$WS/acme-shop" --json)"
SCOUT_CAP="$(printf '%s' "$SCOUT_CLAIM" | json cap)"
SCOUT_FENCE="$(printf '%s' "$SCOUT_CLAIM" | json fence)"
raidlock note readme "drafting the setup section" --agent scout --cap="$SCOUT_CAP" --fence="$SCOUT_FENCE" >/dev/null
raidlock heartbeat readme --ttl 240 --agent scout --cap="$SCOUT_CAP" --fence="$SCOUT_FENCE" >/dev/null

CODEX_TOKEN="$(raidlock join --agent codex-worker --team core --runtime codex-cli --json | json token)"

LEAD_SESSION="$(python3 -c 'import uuid; print(uuid.uuid4())')"
lead lead handover --to "$LEAD_SESSION"

cat <<EOF
Playground ready in $WS

Board: 3 cards on team core
  health  todo   locks acme-shop/src/health, acme-shop/tests/test_health.py
  cart    todo   locks acme-shop/src/cart, acme-shop/tests/test_cart.py
  readme  doing  leased by scout (shell worker) for 4 hours

Lead secret file (pass the path, never the contents): $LEAD_SECRET_FILE
The lead lease is handed over to Claude Code session $LEAD_SESSION.

Step 3, lead:          cd "$WS" && claude --session-id $LEAD_SESSION
Step 4, Claude worker: cd "$WS/workers/claude/acme-shop" && claude
Step 4, Codex worker:  codex mcp add raidlock-playground --env RAIDLOCK_AGENT=codex-worker --env RAIDLOCK_TOKEN=$CODEX_TOKEN${RAIDLOCK_HOME:+ --env RAIDLOCK_HOME=$RAIDLOCK_HOME} -- raidlock mcp --workspace $WS
                       cd "$WS/workers/codex/acme-shop" && codex
Step 5, watch:         cd "$WS" && raidlock ui --open
EOF
