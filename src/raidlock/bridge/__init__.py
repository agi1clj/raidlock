"""Native host bridges: Agent Teams teammates and Codex sessions under raidlock leases."""

import contextlib
import json
import os
import re
import time

from ..core import identity, leases, store
from ..core.errors import Precondition
from ..core.lock import FileLock

CONFIG = "bridge.json"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
STALE_SECONDS = 12 * 3600
PATCH_LINE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.M)


def config_path(board):
    return os.path.join(board.dir, CONFIG)


def load(board):
    data = store.read_json(config_path(board)) or {}
    pool = [n for n in data.get("pool", []) if isinstance(n, str) and NAME.match(n)]
    return {"pool": pool}


def validate_pool(board, names):
    agents = board.agents()
    out = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        if not NAME.match(name):
            raise Precondition(f"invalid agent name {name!r}")
        if name == board.lead:
            raise Precondition("the lead cannot be in the auto-join pool")
        if name not in agents:
            raise Precondition(f"{name} has not joined this board", hint=f"raidlock join --agent {name} --team <team>")
        if name not in out:
            out.append(name)
    return out


def set_pool(board, caller, names):
    """Lead-only. Record which joined workers new unbound host sessions may take on start."""
    pool = validate_pool(board, names)
    with board.lead_write(caller, "configure the auto-join pool"):
        store.write_json(config_path(board), {"pool": pool})
    return load(board)


def patch_paths(text):
    """Every file an apply_patch body adds, updates, deletes or moves to."""
    if not isinstance(text, str):
        return []
    return [m.strip() for m in PATCH_LINE.findall(text) if m.strip()]


def _pool_dir(state, uid):
    return os.path.join(state, "pool", re.sub(r"[^A-Za-z0-9_.-]", "_", uid))


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def pool_lock(state, uid):
    return FileLock(os.path.join(_pool_dir(state, uid), ".lock"))


def allocated(state, uid, session):
    with pool_lock(state, uid):
        return _allocated(state, uid, session)


def _allocated(state, uid, session):
    folder = _pool_dir(state, uid)
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return ""
    for entry in names:
        if entry.endswith(".json") and _read(os.path.join(folder, entry)).get("session") == session:
            return entry[:-5]
    return ""


def _stale(board, path, name, now):
    try:
        age = now - os.path.getmtime(path)
    except OSError:
        return True
    if age < STALE_SECONDS:
        return False
    return not live_worker(board, name)


def live_worker(board, name):
    return any(v.get("agent") == name and leases.is_live(v) for v in board.leases().values())


def allocate(board, state, session, now=None):
    with pool_lock(state, board.uid), board.lock():
        return _allocate(board, state, session, now)


def _allocate(board, state, session, now=None):
    """Give `session` a pool identity it alone holds. Returns the name or ""."""
    if not session:
        return ""
    now = time.time() if now is None else now
    pool = load(board)["pool"]
    held = allocated(state, board.uid, session)
    if held in pool:
        return held
    if held:
        free(state, board.uid, session)
    folder = _pool_dir(state, board.uid)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    for name in pool:
        if live_worker(board, name):
            continue
        path = os.path.join(folder, name + ".json")
        if os.path.exists(path):
            if not _stale(board, path, name, now):
                continue
            try:
                os.unlink(path)
            except OSError:
                continue
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            identity.issue_token(board.home, board.uid, name)
            json.dump({"session": session, "at": now}, f, sort_keys=True)
        return name
    return ""


def free(state, uid, session):
    with pool_lock(state, uid):
        return _free(state, uid, session)


def _free(state, uid, session):
    name = allocated(state, uid, session)
    if name:
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(_pool_dir(state, uid), name + ".json"))
    return name


def _setup(parser):
    parser.add_argument(
        "action",
        nargs="?",
        default="show",
        choices=("show", "pool"),
        help="show the bridge config, or set the auto-join pool",
    )
    parser.add_argument("names", nargs="?", default=None, help="comma-separated joined worker names; empty clears")


def _handler(ctx, args):
    board = ctx.board
    if args.action == "show":
        cfg = load(board)
        return cfg, "auto-join pool: " + (", ".join(cfg["pool"]) or "(empty)")
    names = (args.names or "").split(",")
    cfg = set_pool(board, ctx.caller(), names)
    return cfg, "auto-join pool: " + (", ".join(cfg["pool"]) or "(empty)")


def register_cli(add_command):
    add_command("bridge", "native host bridge: show, or set the auto-join pool (lead)", _setup, _handler, mutating=True)
