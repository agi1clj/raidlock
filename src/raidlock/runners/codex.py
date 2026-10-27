import os
import re
import shutil

from ..core import Usage
from .base import Adapter, short

CONFIG_KEYS = ("model", "model_reasoning_effort")
_LINE = re.compile(r'^\s*([A-Za-z_]+)\s*=\s*"([^"\n]*)"\s*$')


def user_home(env):
    return env.get("CODEX_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".codex")


def top_level(config_text):
    out = {}
    for line in config_text.splitlines():
        if line.strip().startswith("["):
            break
        m = _LINE.match(line)
        if m and m.group(1) in CONFIG_KEYS:
            out[m.group(1)] = m.group(2)
    return out


class Codex(Adapter):
    name = "codex"
    runtime = "codex-cli"
    provider = "openai"
    binary = "codex"
    env_keys = ("CODEX_HOME",)
    api_keys = ("OPENAI_API_KEY", "CODEX_API_KEY")

    def prepare(self, run):
        """An ephemeral CODEX_HOME: a link to the user's auth.json and a config.toml with only model and effort,
        so the user's skills, rules, MCP servers and other config never reach the run."""
        source = user_home(run.parent_env)
        home = os.path.join(run.run_dir, "codex-home")
        os.makedirs(home, mode=0o700, exist_ok=True)
        auth = os.path.join(source, "auth.json")
        if os.path.exists(auth):
            try:
                os.symlink(os.path.realpath(auth), os.path.join(home, "auth.json"))
            except OSError:
                shutil.copy2(auth, os.path.join(home, "auth.json"))
        try:
            with open(os.path.join(source, "config.toml"), encoding="utf-8") as f:
                settings = top_level(f.read())
        except OSError:
            settings = {}
        if run.model:
            settings["model"] = run.model
        with open(os.path.join(home, "config.toml"), "w", encoding="utf-8") as f:
            f.write("".join(f'{k} = "{v}"\n' for k, v in sorted(settings.items())))
        run.codex_config = settings
        return {"CODEX_HOME": home}

    def command(self, run):
        argv = self.executable(run.parent_env) + [
            "exec",
            "-C",
            run.clone,
            "-s",
            run.sandbox,
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-rules",
            "--json",
            "--color",
            "never",
            "--output-schema",
            run.schema_path,
            "-o",
            run.last_path,
        ]
        if run.model:
            argv += ["-m", run.model]
        return argv + ["-"]

    def parse(self, obj):
        kind = obj.get("type", "")
        item = obj.get("item") or {}
        if kind == "item.completed":
            itype = item.get("type")
            if itype == "agent_message":
                return [{"kind": "message", "text": item.get("text", "")}]
            if itype == "command_execution":
                return [
                    {"kind": "command", "command": short(item.get("command", ""), 300), "exit": item.get("exit_code")}
                ]
            if itype == "file_change":
                return [{"kind": "files", "paths": [c.get("path", "") for c in item.get("changes", [])]}]
            if itype == "error":
                return [{"kind": "error", "text": item.get("message", "")}]
            return []
        if kind == "turn.completed":
            u = obj.get("usage") or {}
            usage = Usage.from_codex(u)
            usage.cache_write = int(u.get("cache_write_input_tokens", 0) or 0)
            return [{"kind": "usage", "usage": usage}]
        if kind == "turn.failed":
            return [{"kind": "error", "text": (obj.get("error") or {}).get("message", "turn failed")}]
        if kind == "error":
            return [{"kind": "error", "text": obj.get("message", "error")}]
        return []

    def final(self, run, collected):
        try:
            with open(run.last_path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = ""
        return text.strip() or collected.final
