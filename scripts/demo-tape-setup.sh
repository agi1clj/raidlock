RAIDLOCK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
DEMO_ROOT="$(mktemp -d /tmp/raidlock-tape.XXXXXX)"
DEMO_ROOT="$(cd "$DEMO_ROOT" && pwd -P)"
for var in $(env | sed -n 's/^\(RAIDLOCK_[A-Z_]*\)=.*/\1/p'); do
  unset "$var"
done
unset CLAUDE_CODE_SESSION_ID CLAUDECODE CLAUDE_CODE_ENTRYPOINT CODEX_HOME CODEX_THREAD_ID
export HOME="$DEMO_ROOT"
export RAIDLOCK_SESSION="tape-lead-session"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME="Demo" GIT_AUTHOR_EMAIL="demo@example.com"
export GIT_COMMITTER_NAME="Demo" GIT_COMMITTER_EMAIL="demo@example.com"
export PS1='$ '
REPO="$HOME/shop/storefront"
CLONE="$HOME/workers/codex-1/storefront"
mkdir -p "$REPO/src/cart" "$REPO/tests" "$REPO/docs"
: > "$REPO/src/cart/__init__.py"
printf 'def total(items):\n    return sum(i["price"] * i["qty"] for i in items)\n' > "$REPO/src/cart/cart.py"
printf 'import unittest\n' > "$REPO/tests/test_smoke.py"
printf '# FAQ\n' > "$REPO/docs/faq.md"
printf '__pycache__/\n' > "$REPO/.gitignore"
git -C "$REPO" init -q -b main
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "initial"
git -C "$REPO" remote add origin https://example.com/acme/storefront.git
git clone -q "$REPO" "$CLONE"
git -C "$CLONE" remote set-url origin https://example.com/acme/storefront.git

lead() { raidlock --board "$HOME/shop" --lead-secret "$(ls "$HOME"/.raidlock/tokens/*/*lead.token | head -n 1)" "$@"; }
agent() {
  local name="$1"
  shift
  local fence=()
  [ -f "$HOME/.demo/$name.fence" ] && fence=(--fence "$(cat "$HOME/.demo/$name.fence")")
  local out code
  out="$(raidlock --board "$HOME/shop" --agent "$name" --token "$(cat "$HOME/.demo/$name.token")" "${fence[@]}" "$@" 2>&1)"
  code=$?
  printf '%s\n' "$out" | grep -v '^export RAIDLOCK_\(CAP\|TOKEN\)='
  printf '%s\n' "$out" | sed -n 's/^export RAIDLOCK_FENCE=\([0-9]*\).*/\1/p' | head -n 1 > "$HOME/.demo/$name.fence.new"
  if [ -s "$HOME/.demo/$name.fence.new" ]; then mv "$HOME/.demo/$name.fence.new" "$HOME/.demo/$name.fence"; else rm -f "$HOME/.demo/$name.fence.new"; fi
  return $code
}
join_agents() {
  mkdir -p "$HOME/.demo"
  local spec name runtime role
  for spec in codex-1:codex-cli:worker claude-1:claude-code:worker claude-review:claude-code:reviewer; do
    IFS=: read -r name runtime role <<<"$spec"
    raidlock --board "$HOME/shop" join --agent "$name" --runtime "$runtime" --role "$role" --json \
      | python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])' > "$HOME/.demo/$name.token"
  done
}
codex_builds() {
  cat > "$CLONE/src/cart/discount.py" <<'EOF'
def apply_discount(total, percent):
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return round(total * (100 - percent) / 100, 2)
EOF
  cat > "$CLONE/tests/test_discount.py" <<'EOF'
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cart.discount import apply_discount


class DiscountTest(unittest.TestCase):
    def test_ten_percent(self):
        self.assertEqual(apply_discount(50, 10), 45)

    def test_range(self):
        with self.assertRaises(ValueError):
            apply_discount(50, 120)
EOF
  git -C "$CLONE" add -A
  git -C "$CLONE" commit -q -m "feat(cart): percentage discounts"
}
cd "$HOME/shop"
