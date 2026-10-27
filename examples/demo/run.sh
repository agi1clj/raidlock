#!/usr/bin/env bash
set -euo pipefail

if ! command -v raidlock >/dev/null 2>&1; then
  echo "raidlock is not on PATH. Install it first: uv tool install raidlock (or: pipx install raidlock)" >&2
  exit 3
fi

case "$(uname -s)" in
  Darwin) [ -x /usr/bin/sandbox-exec ] && SANDBOX=yes || SANDBOX=no ;;
  Linux) command -v bwrap >/dev/null 2>&1 && SANDBOX=yes || SANDBOX=no ;;
  *) SANDBOX=no ;;
esac
if [ "$SANDBOX" != yes ]; then
  echo "raid verify needs a sandbox: bubblewrap (bwrap) on Linux or sandbox-exec on macOS" >&2
  exit 3
fi

for var in $(env | sed -n 's/^\(RAIDLOCK_[A-Z_]*\)=.*/\1/p'); do
  unset "$var"
done

DEMO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="${1:-$(mktemp -d "${TMPDIR:-/tmp}/raidlock-demo.XXXXXX")}"
PAUSE="${DEMO_PAUSE:-0}"
mkdir -p "$ROOT"
ROOT="$(cd "$ROOT" && pwd -P)"
if [ -e "$ROOT/acme-shop" ] || [ -e "$ROOT/weather-api" ]; then
  echo "$ROOT already holds a demo; pass an empty folder" >&2
  exit 3
fi
export RAIDLOCK_HOME="$ROOT/home"
export RAIDLOCK_SESSION="demo-lead-session-1"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME="Demo" GIT_AUTHOR_EMAIL="demo@example.com"
export GIT_COMMITTER_NAME="Demo" GIT_COMMITTER_EMAIL="demo@example.com"

STEP=0
step() {
  STEP=$((STEP + 1))
  printf '\n== %d. %s\n' "$STEP" "$*"
  if [ "$PAUSE" != 0 ]; then sleep "$PAUSE"; fi
}
say() { printf '   %s\n' "$*"; }
field() { python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }
show() {
  python3 -c '
import json, sys
r = json.load(sys.stdin)
r["files"] = len(r.get("paths_changed") or [])
r["result"] = "passed" if r.get("passed") else "failed"
print("   " + sys.argv[1].format(**r))
' "$1"
}

ACME="$ROOT/acme-shop"
WEATHER="$ROOT/weather-api"

make_repo() {
  local dir="$1" remote="$2"
  git -C "$dir" init -q -b main
  printf '__pycache__/\n' > "$dir/.gitignore"
  git -C "$dir" add -A
  git -C "$dir" commit -q -m "initial"
  git -C "$dir" remote add origin "$remote"
}

mkdir -p "$ACME/storefront/src/cart" "$ACME/storefront/tests"
: > "$ACME/storefront/src/cart/__init__.py"
cat > "$ACME/storefront/src/cart/cart.py" <<'EOF'
def total(items):
    return sum(item["price"] * item["qty"] for item in items)
EOF
cat > "$ACME/storefront/tests/test_cart.py" <<'EOF'
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cart.cart import total


class TotalTest(unittest.TestCase):
    def test_total(self):
        self.assertEqual(total([{"price": 3, "qty": 2}]), 6)


if __name__ == "__main__":
    unittest.main()
EOF
make_repo "$ACME/storefront" https://example.com/acme/storefront.git

mkdir -p "$WEATHER/forecast/src/forecast" "$WEATHER/forecast/tests"
: > "$WEATHER/forecast/src/forecast/__init__.py"
cat > "$WEATHER/forecast/src/forecast/api.py" <<'EOF'
def forecast(city):
    return {"city": city, "temp_c": 18}
EOF
cat > "$WEATHER/forecast/tests/test_api.py" <<'EOF'
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forecast.api import forecast


class ForecastTest(unittest.TestCase):
    def test_city(self):
        self.assertEqual(forecast("oslo")["city"], "oslo")


if __name__ == "__main__":
    unittest.main()
EOF
make_repo "$WEATHER/forecast" https://example.com/weather/forecast.git
git clone -q "$WEATHER/forecast" "$ROOT/workers/sonnet-builder/forecast"
git -C "$ROOT/workers/sonnet-builder/forecast" remote set-url origin https://example.com/weather/forecast.git

step "Lead opens two boards: acme-shop and weather-api"
ACME_LEAD="$(raidlock init "$ACME" --runtime claude-code --model claude-opus-5-5 --json | field token_file)"
WEATHER_LEAD="$(raidlock init "$WEATHER" --runtime claude-code --model claude-opus-5-5 --json | field token_file)"
acme() { raidlock --board "$ACME" --lead-secret "$ACME_LEAD" "$@"; }
weather() { raidlock --board "$WEATHER" --lead-secret "$WEATHER_LEAD" "$@"; }
acme team add core --max 3 >/dev/null
weather team add core --max 3 >/dev/null
say "lead secret files are private; only their paths are passed around"

step "acme-shop: lead plans a quest with narrow locks and a verify command"
acme new "Percentage discounts at checkout" --id cart-discount --team core \
  --scope "Add src/cart/discount.py with apply_discount(total, percent) and tests/test_discount.py" \
  --locks "storefront/src/cart/discount.py,storefront/tests/test_discount.py" \
  --verify "discount tests pass" --verify-cmd "python3 -m unittest discover -s tests" >/dev/null
say "cart-discount locks storefront/src/cart/discount.py, storefront/tests/test_discount.py"

step "acme-shop: a scripted builder runs the quest through the shell runner"
RUN="$(acme delegate cart-discount --to shell --model claude-sonnet-5-5 --cmd "python3 $DEMO/builder.py" --json --fields all)"
PATCH="$(printf '%s' "$RUN" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("patch") or "")')"
printf '%s' "$RUN" | show "{agent} handed {card} to {lane}: {files} files changed"

step "acme-shop: a second provider reviews the patch"
REVIEWER="$(acme join --agent astra-reviewer --team core --role reviewer --runtime codex-cli --model gpt-6-astra --billing api --json | field token)"
acme review cart-discount --assign astra-reviewer >/dev/null
raidlock --board "$ACME" review cart-discount --agent astra-reviewer --token="$REVIEWER" \
  --verdict approve --note "range check and tests look right" >/dev/null
raidlock --board "$ACME" cost cart-discount --agent astra-reviewer --token="$REVIEWER" \
  --model gpt-6-astra --input 9100 --cached-input 21000 --output 900 --usd 0.0412 --billing api >/dev/null
say "astra-reviewer (gpt-6-astra) approved cart-discount"

step "acme-shop: lead integrates the patch, verifies on main with a signed receipt and closes the quest"
git -C "$ACME/storefront" apply "$PATCH"
git -C "$ACME/storefront" add -A
git -C "$ACME/storefront" commit -q -m "feat(cart): percentage discounts"
acme release cart-discount --note "patch integrated on main" >/dev/null
acme verify cart-discount --cwd "$ACME/storefront" --json --fields all | show "verify {result} at {sha:.12} ({isolation}), receipt signed"
acme "done" cart-discount --note "verified on main" >/dev/null
say "cart-discount is done"

step "weather-api: lead plans a cache quest; a worker claims it in its own clone"
weather new "Cache forecasts for ten minutes" --id forecast-cache --team core \
  --scope "Add src/forecast/cache.py with a TTL cache and tests/test_cache.py" \
  --locks "forecast/src/forecast/cache.py,forecast/tests/test_cache.py" \
  --verify "cache tests pass" --verify-cmd "python3 -m unittest discover -s tests" >/dev/null
BUILDER="$(weather join --agent sonnet-builder --team core --runtime claude-code --model claude-sonnet-5-5 --billing api --json | field token)"
CLAIM="$(raidlock --board "$WEATHER" claim forecast-cache --agent sonnet-builder --token="$BUILDER" \
  --checkout "$ROOT/workers/sonnet-builder/forecast" --json)"
CAP="$(printf '%s' "$CLAIM" | field cap)"
FENCE="$(printf '%s' "$CLAIM" | field fence)"
worker() { raidlock --board "$WEATHER" --agent sonnet-builder --cap="$CAP" --fence="$FENCE" "$@"; }
say "sonnet-builder holds forecast-cache at fence $FENCE"

step "weather-api: the worker tries to write outside its lease and is denied"
set +e
DENIED="$(cd "$ROOT/workers/sonnet-builder/forecast" && worker check --write src/forecast/api.py 2>&1)"
CODE=$?
set -e
if [ "$CODE" -ne 2 ]; then
  echo "expected the out-of-lease write to be denied (exit 2), got $CODE" >&2
  exit 1
fi
say "denied (exit 2): $(printf '%s' "$DENIED" | head -n 1)"

step "weather-api: the lead sends an urgent message; the worker reads it"
weather msg "Keep the TTL at 600 seconds; product asked for ten minutes" --to sonnet-builder --task forecast-cache --urgent >/dev/null
raidlock --board "$WEATHER" inbox --agent sonnet-builder --token="$BUILDER" | sed 's/^/   /'

step "weather-api: the worker builds, records its spend and hands back for review"
cat > "$ROOT/workers/sonnet-builder/forecast/src/forecast/cache.py" <<'EOF'
import time

TTL_SECONDS = 600


class Cache:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.items = {}

    def get(self, key, load):
        now = self.clock()
        hit = self.items.get(key)
        if hit and now - hit[0] < TTL_SECONDS:
            return hit[1]
        value = load(key)
        self.items[key] = (now, value)
        return value
EOF
cat > "$ROOT/workers/sonnet-builder/forecast/tests/test_cache.py" <<'EOF'
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forecast.cache import TTL_SECONDS, Cache


class CacheTest(unittest.TestCase):
    def test_reuses_value_inside_ttl(self):
        now = [0.0]
        calls = []
        cache = Cache(clock=lambda: now[0])
        load = lambda key: calls.append(key) or len(calls)
        self.assertEqual(cache.get("oslo", load), 1)
        now[0] = TTL_SECONDS - 1
        self.assertEqual(cache.get("oslo", load), 1)
        now[0] = TTL_SECONDS + 1
        self.assertEqual(cache.get("oslo", load), 2)


if __name__ == "__main__":
    unittest.main()
EOF
git -C "$ROOT/workers/sonnet-builder/forecast" add -A
git -C "$ROOT/workers/sonnet-builder/forecast" commit -q -m "feat(forecast): ten-minute cache"
worker note forecast-cache "cache.py with 600 s TTL; unittest: 1 test OK" >/dev/null
worker cost forecast-cache --model claude-sonnet-5-5 --input 14200 --cached-input 38000 --output 2100 --billing api >/dev/null
worker move forecast-cache review --note "ready for review" >/dev/null
say "forecast-cache is in review"

step "weather-api: the same second provider reviews"
REVIEWER2="$(weather join --agent astra-reviewer --team core --role reviewer --runtime codex-cli --model gpt-6-astra --billing api --json | field token)"
weather review forecast-cache --assign astra-reviewer >/dev/null
raidlock --board "$WEATHER" review forecast-cache --agent astra-reviewer --token="$REVIEWER2" \
  --verdict approve --note "TTL boundary covered" >/dev/null
raidlock --board "$WEATHER" cost forecast-cache --agent astra-reviewer --token="$REVIEWER2" \
  --model gpt-6-astra --input 7600 --cached-input 15000 --output 700 --usd 0.0336 --billing api >/dev/null
say "astra-reviewer approved forecast-cache"

step "weather-api: the lead hands its session over to a fresh lead session"
weather lead handover --to demo-lead-session-2 >/dev/null
export RAIDLOCK_SESSION="demo-lead-session-2"
weather lead heartbeat >/dev/null
say "lead session is now demo-lead-session-2; the same secret file path, a rotated secret"

step "weather-api: the new lead session verifies with a receipt and closes the quest"
weather verify forecast-cache --json --fields all | show "verify {result} at {sha:.12} ({isolation}), receipt signed"
weather "done" forecast-cache --note "verified in the worker clone" >/dev/null
say "forecast-cache is done"

step "Spend per model across both workspaces"
raidlock --board "$ACME" spend --by model --all | sed 's/^/   /'

cat <<EOF

Demo finished in $ROOT
Watch it: cd "$ACME" && RAIDLOCK_HOME="$RAIDLOCK_HOME" raidlock ui --open
EOF
