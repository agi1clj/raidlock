#!/bin/sh
set -eu

pick() { python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }

export RAIDLOCK_SESSION=docker-smoke
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=Smoke GIT_AUTHOR_EMAIL=smoke@raidlock.invalid
export GIT_COMMITTER_NAME=Smoke GIT_COMMITTER_EMAIL=smoke@raidlock.invalid

WS=/workspace
raid --version
raid doctor
cd "$WS"
LEAD="$(raid quickstart --demo --runtime human --json | pick token_file)"
lead() { raid --lead-secret "$LEAD" "$@"; }
lead new "Reject empty carts at checkout" --id checkout \
  --locks "acme-shop/src/checkout,acme-shop/tests/checkout" \
  --verify-cmd "python3 -m unittest && test ! -e $RAIDLOCK_HOME/tokens && test -z \"\$(ls -A $WS/.board)\" && ! python3 -c 'import socket; socket.create_connection((\"1.1.1.1\", 53), 2)'" >/dev/null

(
  unset RAIDLOCK_SESSION
  cd "$WS/acme-shop"
  CRED="$(raid up --join --agent worker-1 --card checkout --runtime human --json | pick env_file)"
  . "$CRED"
  CLAIM="$(raid claim checkout --checkout "$PWD" --json)"
  RAIDLOCK_CAP="$(printf '%s' "$CLAIM" | pick cap)"
  RAIDLOCK_FENCE="$(printf '%s' "$CLAIM" | pick fence)"
  export RAIDLOCK_CAP RAIDLOCK_FENCE
  unset RAIDLOCK_TOKEN
  cat > src/checkout/cart.py <<'EOF'
def validate(cart):
    """Return the cart if it can be checked out."""
    if not cart:
        raise ValueError("cart is empty")
    return cart
EOF
  python3 -m unittest
  git commit -qam "Reject empty carts at checkout"
  raid move checkout review --note "python3 -m unittest passed" >/dev/null
)

RECEIPT="$(lead verify checkout --json --fields all)"
printf '%s\n' "$RECEIPT" | python3 -c '
import json, sys
r = json.load(sys.stdin)
print("verify passed=%s isolation=%s exit=%s" % (r["passed"], r["isolation"], r["exit"]))
sys.exit(0 if r["passed"] and r["isolation"] == "bwrap-container" else 1)
'
lead done checkout --note "verified in the container sandbox" >/dev/null
raid show --json | python3 -c '
import json, sys
lane = {c["id"]: c["lane"] for c in json.load(sys.stdin)["cards"]}["checkout"]
print("checkout lane:", lane)
sys.exit(0 if lane == "done" else 1)
'
echo "docker smoke: ok"
