import json
import os

MODES = ("api", "plan", "unknown")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _codex(env):
    if env.get("OPENAI_API_KEY") or env.get("CODEX_API_KEY"):
        return "api", "env"
    base = env.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    auth = _read_json(os.path.join(base, "auth.json"))
    mode = str(auth.get("auth_mode", "")).lower()
    if mode == "chatgpt" or auth.get("tokens"):
        return "plan", "config"
    if mode in ("apikey", "api_key") or auth.get("OPENAI_API_KEY"):
        return "api", "config"
    return "unknown", "unknown"


def _claude(env):
    if env.get("ANTHROPIC_API_KEY") or env.get("CLAUDE_CODE_USE_BEDROCK") or env.get("CLAUDE_CODE_USE_VERTEX"):
        return "api", "env"
    base = env.get("CLAUDE_CONFIG_DIR")
    state = os.path.join(base, ".claude.json") if base else os.path.join(os.path.expanduser("~"), ".claude.json")
    if _read_json(state).get("oauthAccount"):
        return "plan", "config"
    return "unknown", "unknown"


def _gemini(env):
    if env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or env.get("GOOGLE_GENAI_USE_VERTEXAI"):
        return "api", "env"
    return "unknown", "unknown"


def detect(runtime, declared=None, env=None):
    """Billing mode of an agent: api (pay per token), plan (subscription, $0 marginal) or unknown.
    Order: declared, RAIDLOCK_BILLING, runtime credentials (never read beyond the auth mode)."""
    env = os.environ if env is None else env
    for value, source in ((declared, "declared"), (env.get("RAIDLOCK_BILLING"), "env")):
        if value in MODES:
            return {"billing": value, "billing_source": source}
    detector = {"codex-cli": _codex, "claude-code": _claude, "gemini-cli": _gemini}.get(runtime)
    mode, source = detector(env) if detector else ("unknown", "unknown")
    return {"billing": mode, "billing_source": source}
