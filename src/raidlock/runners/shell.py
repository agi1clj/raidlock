import json
import shlex

from ..core import Usage
from ..core.errors import Precondition
from .base import Adapter

KINDS = ("message", "command", "usage", "result", "error", "model", "files")


class Shell(Adapter):
    """Any local command. Prompt on stdin, cwd is the clone. Stdout lines that are JSON objects with a
    `kind` from KINDS are events; any other line, JSON or not, is an agent message."""

    name = "shell"
    runtime = "shell"
    provider = "local"
    binary = ""

    def command(self, run):
        if not run.cmd:
            raise Precondition(
                '--to shell needs --cmd "<command>"', hint='raidlock delegate <card> --to shell --cmd "./agent.sh"'
            )
        return shlex.split(run.cmd)

    def parse(self, obj):
        if obj.get("kind") == "usage":
            fields = {k: int(obj.get(k, 0) or 0) for k in ("input", "cached_input", "cache_write", "output")}
            return [{"kind": "usage", "usage": Usage(**fields), "usd": obj.get("usd")}]
        if obj.get("kind") in KINDS:
            return [dict(obj)]
        if obj.get("type") == "raw":
            return [{"kind": "message", "text": obj.get("text", "")}]
        return [{"kind": "message", "text": json.dumps(obj, ensure_ascii=False)}]
