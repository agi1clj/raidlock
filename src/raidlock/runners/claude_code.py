import json
import re

from ..core import Usage
from ..core.errors import Precondition
from .base import Adapter, short

READ_TOOLS = (
    "Read",
    "Grep",
    "Glob",
    "Bash(git diff *)",
    "Bash(git log *)",
    "Bash(git show *)",
    "Bash(git status *)",
    "Bash(ls *)",
)
WRITE_TOOLS = READ_TOOLS + ("Edit(./**)", "Write(./**)", "NotebookEdit(./**)")
READ_NAMES = ("Read", "Grep", "Glob", "WebSearch", "WebFetch", "TodoWrite", "Bash")
WRITE_NAMES = ("Edit", "Write", "NotebookEdit")
_RULE = re.compile(r"^([A-Za-z]+)(?:\(([A-Za-z0-9 ._/*:@=+-]+)\))?$")


class ClaudeCode(Adapter):
    name = "claude"
    runtime = "claude-code"
    provider = "anthropic"
    binary = "claude"
    env_keys = ("CLAUDE_CONFIG_DIR",)
    api_keys = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

    def tools(self, sandbox, override=None):
        if override:
            return tuple(override)
        return WRITE_TOOLS if sandbox == "workspace-write" else READ_TOOLS

    def check_tools(self, value, sandbox):
        """Validate a --tools override: known tool names, optional plain rule specs, Bash only with a command
        prefix, write tools only in a workspace-write sandbox."""
        items = [t.strip() for t in (value.split(",") if isinstance(value, str) else value) if t.strip()]
        if not items:
            raise Precondition("--tools is empty")
        for item in items:
            m = _RULE.match(item)
            name = m.group(1) if m else ""
            if name not in READ_NAMES + WRITE_NAMES:
                raise Precondition(
                    f"--tools: {item!r} is not an allowed tool rule; allowed tools: "
                    + ", ".join(READ_NAMES + WRITE_NAMES),
                    tool=item,
                )
            if name == "Bash" and not m.group(2):
                raise Precondition("--tools: Bash needs a command prefix, e.g. Bash(pytest *)", tool=item)
            if name in WRITE_NAMES and sandbox != "workspace-write":
                raise Precondition(f"--tools: {name} needs --sandbox workspace-write", tool=item)
        return items

    def command(self, run):
        argv = self.executable(run.parent_env) + [
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "dontAsk",
            "--safe-mode",
            "--no-session-persistence",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--allowedTools",
            ",".join(self.tools(run.sandbox, getattr(run, "tools", None))),
            "--json-schema",
            json.dumps(run.schema, separators=(",", ":")),
        ]
        if run.model:
            argv += ["--model", run.model]
        return argv

    def parse(self, obj):
        kind = obj.get("type", "")
        if kind == "system" and obj.get("subtype") == "init":
            source = obj.get("apiKeySource")
            billing = "" if source is None else ("plan" if source == "none" else "api")
            return [{"kind": "model", "model": obj.get("model", ""), "billing": billing}]
        if kind == "assistant":
            out = []
            for block in (obj.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text"):
                    out.append({"kind": "message", "text": block["text"]})
                elif block.get("type") == "tool_use" and block.get("name") != "StructuredOutput":
                    out.append({"kind": "command", "command": _tool(block), "exit": None})
            return out
        if kind == "result":
            usage = {
                "kind": "usage",
                "usage": Usage.from_anthropic(obj.get("usage") or {}),
                "usd": obj.get("total_cost_usd"),
            }
            error = ""
            if obj.get("is_error") or (obj.get("subtype") and obj.get("subtype") != "success"):
                error = short(obj.get("result") or obj.get("subtype") or "error", 300)
            return [
                usage,
                {
                    "kind": "result",
                    "text": obj.get("result") or "",
                    "structured": obj.get("structured_output"),
                    "error": error,
                },
            ]
        return []

    def final(self, run, collected):
        return collected.structured if collected.structured is not None else collected.final


def _tool(block):
    data = block.get("input") or {}
    detail = data.get("command") or data.get("file_path") or data.get("pattern") or data.get("path") or ""
    return short(f"{block.get('name', 'tool')} {detail}".strip(), 300)
