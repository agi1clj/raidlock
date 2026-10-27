from ..core import Usage
from .base import Adapter, short


class Gemini(Adapter):
    """Experimental: flags and stream-json event shapes are not verified against a real gemini binary."""

    name = "gemini"
    runtime = "gemini-cli"
    provider = "google"
    binary = "gemini"
    env_keys = ("GEMINI_CONFIG_DIR", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GOOGLE_GENAI_USE_VERTEXAI")
    api_keys = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

    def command(self, run):
        mode = "auto_edit" if run.sandbox == "workspace-write" else "default"
        argv = self.executable(run.parent_env) + [
            "--output-format",
            "stream-json",
            "--approval-mode",
            mode,
            "-p",
            "Follow the instructions given on stdin.",
        ]
        if run.model:
            argv += ["-m", run.model]
        return argv

    def parse(self, obj):
        kind = obj.get("type", "")
        if kind == "init":
            return [{"kind": "model", "model": obj.get("model", "")}]
        if kind == "message" and obj.get("role") == "assistant" and obj.get("content"):
            return [{"kind": "message", "text": obj["content"], "delta": bool(obj.get("delta"))}]
        if kind == "tool_use":
            return [
                {
                    "kind": "command",
                    "command": short(f"{obj.get('tool_name', 'tool')} {obj.get('parameters', '')}", 300),
                    "exit": None,
                }
            ]
        if kind == "error":
            return [{"kind": "error", "text": obj.get("message", "error")}]
        if kind == "result":
            stats = obj.get("stats") or {}
            cached = int(stats.get("cached", 0) or 0)
            out = [
                {
                    "kind": "usage",
                    "usage": Usage(
                        input=max(int(stats.get("input_tokens", 0) or 0) - cached, 0),
                        cached_input=cached,
                        output=int(stats.get("output_tokens", 0) or 0),
                    ),
                }
            ]
            if obj.get("status") not in (None, "success"):
                out.append({"kind": "result", "error": short(obj.get("error") or obj.get("status"), 300)})
            return out
        return []

    def final(self, run, collected):
        return "".join(collected.messages) if collected.messages else collected.final
