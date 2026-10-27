import contextlib
import os

from .core.errors import Precondition

MARKER = "raidlock-managed: install-commands"
CONTENT = f"""---
name: raid
description: Run a raidlock board command (up, who, claimable, show <id>, claim <id>)
argument-hint: up | who | claimable | show <id> | claim <id>
allowed-tools: Bash(raidlock *)
disable-model-invocation: true
{MARKER}
---

!`raidlock $ARGUMENTS`

Relay the output above in one or two lines, keeping its `Next:` step. Run nothing else.
"""


def config_dir(env=None):
    env = os.environ if env is None else env
    return env.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def commands_dir(env=None):
    return os.path.join(config_dir(env), "skills", "raid")


def target(env=None):
    return os.path.join(commands_dir(env), "SKILL.md")


def legacy_target(env=None):
    return os.path.join(config_dir(env), "commands", "raid.md")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _ours(text):
    head = text.split("\n---", 1)[0] if text.startswith("---") else ""
    return MARKER in head.splitlines()


def _drop_legacy(env):
    path = legacy_target(env)
    current = _read(path)
    if current is not None and _ours(current):
        os.remove(path)


def install(env=None):
    _drop_legacy(env)
    path = target(env)
    current = _read(path)
    if current is not None and not _ours(current):
        raise Precondition(
            f"{path} exists and was not written by raidlock; left untouched",
            path=path,
            hint="move it away, then run raidlock install-commands",
        )
    if current == CONTENT:
        return {"action": "unchanged", "path": path}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(CONTENT)
    os.replace(tmp, path)
    return {"action": "updated" if current is not None else "installed", "path": path}


def uninstall(env=None):
    _drop_legacy(env)
    path = target(env)
    current = _read(path)
    if current is None:
        return {"action": "absent", "path": path}
    if not _ours(current):
        raise Precondition(f"{path} was not written by raidlock; left untouched", path=path)
    os.remove(path)
    with contextlib.suppress(OSError):
        os.rmdir(os.path.dirname(path))
    return {"action": "removed", "path": path}


TEXT = {
    "installed": "Installed /raid at {path}. Next: type /raid who in Claude Code",
    "updated": "Updated /raid at {path}. Next: type /raid who in Claude Code",
    "unchanged": "/raid is already installed at {path}.",
    "removed": "Removed /raid ({path}).",
    "absent": "/raid is not installed ({path}).",
}


def _setup(parser):
    parser.add_argument("--uninstall", action="store_true", help="remove the /raid command this installed")


def _handler(ctx, args):
    result = uninstall() if args.uninstall else install()
    return result, TEXT[result["action"]].format(path=result["path"])


def register_cli(add_command):
    add_command(
        "install-commands", "add a personal /raid skill to Claude Code (--uninstall removes it)", _setup, _handler
    )
