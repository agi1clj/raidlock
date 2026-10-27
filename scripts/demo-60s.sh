#!/usr/bin/env bash
set -euo pipefail

if ! command -v raidlock >/dev/null 2>&1; then
  echo "raidlock is not on PATH. Install it first, or run: uv run bash scripts/demo-60s.sh" >&2
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

ROOT="${1:-$(mktemp -d "${TMPDIR:-/tmp}/raidlock-60s.XXXXXX")}"
PAUSE="${DEMO_PAUSE:-0}"
mkdir -p "$ROOT"
ROOT="$(cd "$ROOT" && pwd -P)"
if [ -e "$ROOT/acme-shop" ]; then
  echo "$ROOT already holds a demo; pass an empty folder" >&2
  exit 3
fi
WS="$ROOT/acme-shop"
REPO="$WS/storefront"
export RAIDLOCK_HOME="$ROOT/home"
export RAIDLOCK_SESSION="demo-lead-session"
export CODEX_HOME="$ROOT/codex-home"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME="Demo" GIT_AUTHOR_EMAIL="demo@example.com"
export GIT_COMMITTER_NAME="Demo" GIT_COMMITTER_EMAIL="demo@example.com"
START=$(date +%s)

STEP=0
step() {
  STEP=$((STEP + 1))
  printf '\n\033[1m== %d. %s\033[0m\n' "$STEP" "$*"
  if [ "$PAUSE" != 0 ]; then sleep "$PAUSE"; fi
}
say() { printf '   %s\n' "$*"; }
pick() { python3 -c 'import json, sys; d = json.load(sys.stdin); print(sys.argv[1].format(**d))' "$1"; }

mkdir -p "$ROOT/bin" "$CODEX_HOME/sessions/demo"
cat > "$ROOT/bin/claude" <<'EOF'
#!/usr/bin/env python3
import json, sys
sys.stdin.read()
print(json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5-5", "apiKeySource": "ANTHROPIC_API_KEY"}))
print(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                  "result": "Claude usage limit reached; resets in 3h",
                  "usage": {"input_tokens": 5200, "output_tokens": 310}, "total_cost_usd": 0.0135}))
sys.exit(1)
EOF
cat > "$ROOT/bin/codex" <<'EOF'
#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
clone = argv[argv.index("-C") + 1]
last = argv[argv.index("-o") + 1]
sys.stdin.read()
os.makedirs(os.path.join(clone, "src/cart"), exist_ok=True)
with open(os.path.join(clone, "src/cart/discount.py"), "w") as f:
    f.write("def apply_discount(total, percent):\n"
            "    if not 0 <= percent <= 100:\n"
            "        raise ValueError(\"percent must be between 0 and 100\")\n"
            "    return round(total * (100 - percent) / 100, 2)\n")
with open(os.path.join(clone, "tests/test_discount.py"), "w") as f:
    f.write("import sys\nimport unittest\nfrom pathlib import Path\n\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / \"src\"))\n\n"
            "from cart.discount import apply_discount\n\n\n"
            "class DiscountTest(unittest.TestCase):\n"
            "    def test_ten_percent(self):\n"
            "        self.assertEqual(apply_discount(50, 10), 45)\n\n"
            "    def test_range(self):\n"
            "        with self.assertRaises(ValueError):\n"
            "            apply_discount(50, 120)\n")
answer = {"status": "done", "evidence": ["discount.py with a 0-100 range check; 2 unittest cases pass"],
          "paths_changed": ["src/cart/discount.py", "tests/test_discount.py"], "open_questions": []}
with open(last, "w") as f:
    f.write(json.dumps(answer))
print(json.dumps({"type": "thread.started", "thread_id": "demo"}))
print(json.dumps({"type": "item.completed", "item": {"id": "1", "type": "agent_message", "text": json.dumps(answer)}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 18400, "cached_input_tokens": 9000,
                                                      "output_tokens": 640}}))
EOF
chmod +x "$ROOT/bin/claude" "$ROOT/bin/codex"
printf 'model = "gpt-6-astra"\n' > "$CODEX_HOME/config.toml"
mkdir -p "$RAIDLOCK_HOME"
cat > "$RAIDLOCK_HOME/pricing.json" <<'EOF'
{"schema": 2, "unit": "USD per MTok", "models": {
  "claude-sonnet-5-5": {"provider": "anthropic", "input": 2.0, "output": 10.0, "cache_read": 0.1, "cache_write": 2.5, "source": "demo"},
  "claude-opus-5-5": {"provider": "anthropic", "input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write": 5.0, "source": "demo"},
  "gpt-6-astra": {"provider": "openai", "input": 1.5, "output": 8.0, "cache_read": 0.15, "cache_write": 0, "source": "demo"}}}
EOF
export RAIDLOCK_CLAUDE_BIN="$ROOT/bin/claude" RAIDLOCK_CODEX_BIN="$ROOT/bin/codex"

limits() {
  local claude="$1" codex="$2" now
  now=$(date +%s)
  printf '{"rate_limits":{"five_hour":{"used_percentage":%s,"resets_at":%s},"seven_day":{"used_percentage":30,"resets_at":%s}}}' \
    "$claude" $((now + 10800)) $((now + 400000)) | raidlock limits --ingest-claude >/dev/null
  printf '{"timestamp":"%s","type":"event_msg","payload":{"type":"token_count","rate_limits":{"primary":{"used_percent":%s,"window_minutes":300,"resets_at":%s},"secondary":{"used_percent":12,"window_minutes":10080,"resets_at":%s}}}}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$codex" $((now + 9000)) $((now + 500000)) > "$CODEX_HOME/sessions/demo/rollout.jsonl"
}

mkdir -p "$REPO/src/cart" "$REPO/tests"
: > "$REPO/src/cart/__init__.py"
printf 'def total(items):\n    return sum(i["price"] * i["qty"] for i in items)\n' > "$REPO/src/cart/cart.py"
printf 'import unittest\n' > "$REPO/tests/test_smoke.py"
printf '__pycache__/\n' > "$REPO/.gitignore"
git -C "$REPO" init -q -b main
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "initial"
git -C "$REPO" remote add origin https://example.com/acme/storefront.git

printf 'raidlock in 60 seconds: one fictional repo, scripted stand-ins for the claude and codex CLIs, no model calls.\n'
step "Lead opens a board; plan usage: Claude 41% of its 5h window, Codex 22%"
LEAD="$(raidlock init "$WS" --runtime claude-code --model claude-opus-5-5 --json | pick '{token_file}')"
lead() { raidlock --board "$WS" --lead-secret "$LEAD" "$@"; }
limits 41 22
lead routing set default '{"provider": "claude", "fallback": ["codex"]}' >/dev/null
lead resurrect-policy set providers '["claude", "codex"]' >/dev/null
lead resurrect-policy set cooldown_minutes 0 >/dev/null
lead resurrect-policy set enabled true >/dev/null
lead new "Percentage discounts at checkout" --id cart-discount \
  --locks "storefront/src/cart/discount.py,storefront/tests/test_discount.py" \
  --scope "Add src/cart/discount.py with apply_discount(total, percent) and tests/test_discount.py" \
  --verify "discount tests pass" --verify-cmd "python3 -m unittest discover -s tests" >/dev/null
lead next --json --fields all | pick '   raid next: {card} -> {provider} ({reason})'

step "Quota: the Claude run hits its plan limit mid-quest"
{ lead delegate cart-discount --to claude --model claude-sonnet-5-5 --json --fields all || true; } \
  | pick '   {agent} -> {lane}: {reason}'
limits 100 22
say "statusline now reports Claude at 100% of its 5h window"

step "Auto-handoff: the resurrect sweep moves the quest to Codex with a resume packet"
SWEEP="$(lead resurrect --sweep --json --fields all)"
printf '%s' "$SWEEP" | python3 -c '
import json, sys
for r in json.load(sys.stdin).get("acted", []):
    print("   {card}: {classification} on {from_provider} -> {to} as {agent}, now in {lane}".format(**r))
'
RUN_DIR="$(printf '%s' "$SWEEP" | python3 -c 'import json, sys; print(json.load(sys.stdin)["acted"][0]["run_dir"])')"

step "Quota routing: new work skips Claude until its window resets"
lead new "Tax line on receipts" --id receipt-tax --locks "storefront/src/cart/tax.py" >/dev/null
lead next --json --fields all | pick '   raid next: {card} -> {provider}, fallback={fallback} ({reason})'

step "Cross-vendor review: a Claude reviewer approves the Codex patch"
REVIEWER="$(lead join --agent claude-reviewer --role reviewer --runtime claude-code --model claude-opus-5-5 --json | pick '{token}')"
lead review cart-discount --assign claude-reviewer >/dev/null
raidlock --board "$WS" review cart-discount --agent claude-reviewer --token="$REVIEWER" \
  --verdict approve --note "range check and tests look right" >/dev/null
say "claude-reviewer approved cart-discount"

step "Receipt-gated done: refused until a signed receipt covers the integrated commit"
set +e
REFUSED="$(lead "done" cart-discount 2>&1)"
CODE=$?
set -e
say "raid done -> exit $CODE: $(printf '%s' "$REFUSED" | head -n 1)"
git -C "$REPO" apply "$RUN_DIR/changes.patch"
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "feat(cart): percentage discounts"
lead release cart-discount --note "patch integrated on main" >/dev/null
lead verify cart-discount --cwd "$REPO" --json --fields all | pick '   verify passed={passed} at {sha:.12}, receipt signed'
lead "done" cart-discount --note "verified on main" >/dev/null
say "cart-discount is done"

step "Cost and reputation per model"
SPEND="$(raidlock --board "$WS" spend --by model --json)"
REP="$(raidlock --board "$WS" reputation --by model --json)"
python3 - "$SPEND" "$REP" <<'EOF'
import json, sys
spend = {r["key"]: r for r in json.loads(sys.argv[1])["rows"]}
rep = {r["key"]: r for r in json.loads(sys.argv[2])["rows"]}
print("   {:<18} {:>5} {:>8} {:>10} {:>11} {:>9}".format("model", "runs", "tokens", "usd(api)", "reputation", "verified"))
for key in sorted(set(spend) | set(rep)):
    s, r = spend.get(key, {}), rep.get(key, {})
    print("   {:<18} {:>5} {:>8} {:>10.4f} {:>11} {:>9}".format(
        key, s.get("runs", 0), s.get("tokens", 0), s.get("usd_equiv") or 0, r.get("score", "-"),
        (r.get("inputs") or {}).get("verify_passed", 0)))
EOF

printf '\nDemo finished in %ss. Board: %s\n' $(($(date +%s) - START)) "$WS"
printf 'Watch it: RAIDLOCK_HOME="%s" raidlock ui --board "%s" --open\n' "$RAIDLOCK_HOME" "$WS"
