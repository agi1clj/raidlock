"""Tell the user when a newer raidlock release exists on PyPI.

The check runs at most every ten minutes, never from a hook,
and is skipped in CI or when RAIDLOCK_NO_UPDATE_CHECK is set."""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

from . import __version__
from .core import registry

URL = "https://pypi.org/pypi/raidlock/json"
TTL = 600
TIMEOUT = 2
UPGRADE = "raid upgrade"
PLUGIN = "raidlock@raidlock"
VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}$")


def disabled(env=None):
    env = os.environ if env is None else env
    return bool(env.get("RAIDLOCK_NO_UPDATE_CHECK") or env.get("CI"))


def _path():
    return os.path.join(registry.home(), "update-check.json")


def valid(text):
    return isinstance(text, str) and bool(VERSION_RE.match(text))


def _version(text):
    parts = []
    for piece in str(text).split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def cached():
    try:
        with open(_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def refresh(force=False, persist=True):
    if disabled():
        return None
    data = cached()
    try:
        checked = float(data.get("checked", 0) or 0)
    except (TypeError, ValueError):
        checked = 0
    if not force and time.time() - checked < TTL:
        return data.get("latest") if valid(data.get("latest")) else None
    fetched = None
    try:
        with urllib.request.urlopen(URL, timeout=TIMEOUT) as resp:
            fetched = json.load(resp)["info"]["version"]
    except (OSError, ValueError, KeyError, TypeError):
        fetched = None
    ok = valid(fetched)
    latest = fetched if ok else (data.get("latest") if valid(data.get("latest")) else None)
    if not persist:
        return latest
    try:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"checked": time.time(), "latest": latest}, fh)
        os.replace(tmp, _path())
    except OSError:
        pass
    return latest


def watch():
    while True:
        refresh()
        time.sleep(TTL)


def available(latest=None):
    latest = cached().get("latest") if latest is None else latest
    if disabled() or not valid(latest) or _version(latest) <= _version(__version__):
        return None
    return {"current": __version__, "latest": latest, "upgrade": UPGRADE}


def notice(latest=None):
    info = available(latest)
    if not info:
        return ""
    return f"raidlock {info['latest']} is available (you have {info['current']}): {info['upgrade']}"


def _run(argv, timeout=300):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def upgrade_steps():
    """The commands that upgrade this install: the CLI through the tool that installed it, then the Claude Code
    plugin when it is installed. A CLI installed some other way gets no CLI step."""
    steps = []
    prefix = sys.prefix.replace("\\", "/").lower()
    uv, pipx, claude = shutil.which("uv"), shutil.which("pipx"), shutil.which("claude")
    if uv and "/uv/tools/raidlock" in prefix:
        steps.append([uv, "tool", "upgrade", "--refresh", "raidlock"])
    elif pipx and "/pipx/venvs/raidlock" in prefix:
        steps.append([pipx, "upgrade", "raidlock"])
    if claude:
        try:
            listed = _run([claude, "plugin", "list"], timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            listed = ""
        if PLUGIN in listed:
            steps.append([claude, "plugin", "marketplace", "update", "raidlock"])
            steps.append([claude, "plugin", "update", PLUGIN])
    return steps
