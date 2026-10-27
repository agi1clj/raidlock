import json
import os
import re

from . import store

DEFAULT_DISPLAY = {
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-haiku-5-5": "Claude Haiku 5.5",
    "gpt-6-astra": "GPT-6 Astra",
}
_TOML_MODEL = re.compile(r'^\s*model\s*=\s*"([^"]+)"\s*$')
_TOML_EFFORT = re.compile(r'^\s*model_reasoning_effort\s*=\s*"([^"]+)"\s*$')
_VERSION = re.compile(r"(?<![0-9])(\d{1,2})(?:[-.](\d{1,2}))?(?![0-9])")


def display_map(home):
    path = os.path.join(home, "models.json")
    data = store.read_json(path)
    if data is None:
        store.write_doc(path, "models", DEFAULT_DISPLAY)
        return dict(DEFAULT_DISPLAY)
    store.check_schema(data, path)
    return {**DEFAULT_DISPLAY, **data.get("models", {})}


def display(model, home):
    if not model:
        return "unknown model"
    return display_map(home).get(model, model)


def version(model):
    """Best-effort version from a model id: claude-opus-5-5 -> 5.5, gpt-6-astra -> 6."""
    m = _VERSION.search(model or "")
    if not m:
        return ""
    return m.group(1) + ("." + m.group(2) if m.group(2) else "")


def _codex_config(env):
    base = env.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    model = effort = ""
    try:
        with open(os.path.join(base, "config.toml"), encoding="utf-8") as f:
            for line in f:
                if line.lstrip().startswith("["):
                    break
                m, e = _TOML_MODEL.match(line), _TOML_EFFORT.match(line)
                model = m.group(1) if m else model
                effort = e.group(1) if e else effort
    except OSError:
        pass
    return model, effort


def _json_model(path, *keys):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ""
    for k in keys:
        data = data.get(k) if isinstance(data, dict) else None
    return data if isinstance(data, str) else ""


def _claude_config(env):
    base = env.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return _json_model(os.path.join(base, "settings.json"), "model")


def _gemini_config(env):
    base = env.get("GEMINI_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".gemini")
    path = os.path.join(base, "settings.json")
    return _json_model(path, "model", "name") or _json_model(path, "model")


def _runtime_sources(runtime, env):
    if runtime == "codex-cli":
        model, effort = _codex_config(env)
        return [(model, "config", effort)]
    if runtime == "claude-code":
        return [(env.get("ANTHROPIC_MODEL", ""), "env", ""), (_claude_config(env), "config", "")]
    if runtime == "gemini-cli":
        return [(env.get("GEMINI_MODEL", ""), "env", ""), (_gemini_config(env), "config", "")]
    return []


def detect(runtime, declared=None, flag=None, reported=None, effort=None, env=None):
    """Resolve an agent's model: declared, RAIDLOCK_MODEL, runner flag, runtime-reported, runtime env, runtime
    config."""
    env = os.environ if env is None else env
    sources = [
        (declared, "declared", ""),
        (env.get("RAIDLOCK_MODEL", ""), "env", ""),
        (flag, "flag", ""),
        (reported, "reported", ""),
    ] + _runtime_sources(runtime, env)
    for model, source, found_effort in sources:
        if model:
            return {
                "model": model,
                "model_version": version(model),
                "effort": effort or found_effort or "",
                "model_source": source,
            }
    return {"model": "", "model_version": "", "effort": effort or "", "model_source": "unknown"}
