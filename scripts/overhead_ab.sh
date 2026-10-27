#!/usr/bin/env bash
set -euo pipefail

if [ "${1:-}" != "--yes" ]; then
  cat >&2 <<'EOF'
Usage: bash scripts/overhead_ab.sh --yes [model]

Measures the tokens raidlock adds to a real Claude Code session by comparing the input tokens Claude Code
reports for the same one-word prompt with and without the plugin, and with and without the MCP server.
It makes 6 short paid requests with your own Claude Code login (a few cents on a small model).
EOF
  exit 2
fi
MODEL="${2:-haiku}"
command -v claude >/dev/null 2>&1 || { echo "claude is not on PATH" >&2; exit 3; }
command -v raidlock >/dev/null 2>&1 || { echo "raidlock is not on PATH" >&2; exit 3; }
PLUGIN="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT="$(mktemp -d "${TMPDIR:-/tmp}/raidlock-ab.XXXXXX")"
trap 'rm -rf "$ROOT"' EXIT
export RAIDLOCK_HOME="$ROOT/home"
mkdir -p "$ROOT/ws"
RAIDLOCK_SESSION=ab-lead raidlock init "$ROOT/ws" >/dev/null
printf '{"mcpServers":{"raidlock":{"command":"%s","args":["mcp","--workspace","%s"],"env":{"RAIDLOCK_HOME":"%s"}}}}' \
  "$(command -v raidlock)" "$ROOT/ws" "$RAIDLOCK_HOME" > "$ROOT/mcp.json"

run() {
  (cd "$ROOT/ws" && claude -p "Reply with the single word OK." --model "$MODEL" --output-format json \
    --setting-sources "" --strict-mcp-config "$@" 2>/dev/null) | python3 -c '
import json, sys
u = json.load(sys.stdin)["usage"]
print(sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")))
'
}

BASE=$(run)
PLUG=$(run --plugin-dir "$PLUGIN")
MCP=$(run --mcp-config "$ROOT/mcp.json")
BOTH=$(run --plugin-dir "$PLUGIN" --mcp-config "$ROOT/mcp.json")
BASE_EAGER=$(ENABLE_TOOL_SEARCH=false run)
MCP_EAGER=$(ENABLE_TOOL_SEARCH=false run --mcp-config "$ROOT/mcp.json")
printf 'claude %s, model %s\n' "$(claude --version)" "$MODEL"
printf 'plugin (skills, agent, hooks): +%s input tokens\n' $((PLUG - BASE))
printf 'MCP server, tool search on:     +%s input tokens\n' $((MCP - BASE))
printf 'plugin and MCP server:          +%s input tokens\n' $((BOTH - BASE))
printf 'MCP server, tool search off:    +%s input tokens\n' $((MCP_EAGER - BASE_EAGER))
