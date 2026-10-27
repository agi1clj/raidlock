"""Refuse to run on a Python older than raidlock supports, with one clear line.

This module must stay importable by old interpreters: plain syntax and the standard library only. A hook guard that
runs inside a raidlock workspace denies the tool call instead of failing open."""

import json
import os
import sys

GUARDS = ("pre-write", "pre-read", "pre-bash", "pre-urgent")


def message(minimum):
    have = ".".join(str(n) for n in sys.version_info[:3])
    need = ".".join(str(n) for n in minimum)
    return (
        "raidlock needs Python {need} or newer, but {exe} is Python {have}. Install raidlock with "
        "`uv tool install raidlock` or `pipx install raidlock`, or set RAIDLOCK_PYTHON to a Python {need}+ "
        "interpreter."
    ).format(need=need, exe=sys.executable or "python", have=have)


def board_above(start):
    cur = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(cur, ".board", "board.json")):
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def hook_input():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def refuse(minimum):
    text = message(minimum)
    if any(arg in GUARDS for arg in sys.argv[1:]) and not sys.stdin.isatty():
        data = hook_input()
        if board_above(data.get("cwd")):
            out = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "raidlock: " + text,
                }
            }
            sys.stdout.write(json.dumps(out, separators=(",", ":")))
            sys.stdout.flush()
            raise SystemExit(0)
    sys.stderr.write(text + "\n")
    raise SystemExit(1)
