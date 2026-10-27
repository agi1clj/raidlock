"""Tell the user when a newer raidlock release exists on PyPI.

The check runs at most once a day, never from a hook, and is skipped in CI or when RAIDLOCK_NO_UPDATE_CHECK is set."""

import json
import os
import re
import time
import urllib.request

from . import __version__
from .core import registry

URL = "https://pypi.org/pypi/raidlock/json"
TTL = 86400
RETRY = 3600
TIMEOUT = 2
UPGRADE = "uv tool upgrade raidlock"
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
    stamp = time.time() if ok else time.time() - TTL + RETRY
    if not persist:
        return latest
    try:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"checked": stamp, "latest": latest}, fh)
        os.replace(tmp, _path())
    except OSError:
        pass
    return latest


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
