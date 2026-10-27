import os
import re
import uuid

from . import clock, store
from .errors import Denied

MAX_BYTES = 32 * 1024
MAX_QUEUED = 50
REPEAT_WINDOW = 60
RATE_WINDOW = 60
RATE_LIMIT = 20


def inbox_path(board_dir, agent):
    return os.path.join(board_dir, "inbox", re.sub(r"[^A-Za-z0-9_.-]", "_", agent) + ".jsonl")


def load(board_dir, agent):
    return store.read_jsonl(inbox_path(board_dir, agent))


def save(board_dir, agent, rows):
    store.write_jsonl(inbox_path(board_dir, agent), rows)


def all_messages(board_dir):
    folder = os.path.join(board_dir, "inbox")
    if not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder)):
        if name.endswith(".jsonl"):
            out.extend(store.read_jsonl(os.path.join(folder, name)))
    return out


def initial_status(task, urgent, busy_card):
    """ready when urgent, when the recipient is idle, or when the message is about the recipient's doing card."""
    if urgent or not busy_card or (task and task == busy_card):
        return "ready"
    return "queued"


def check_limits(board_dir, sender, to, task, text, now):
    """Return a duplicate message when this one repeats it within the window; raise Denied on caps."""
    inbox = load(board_dir, to)
    for m in reversed(inbox):
        if _within(m, now, REPEAT_WINDOW) and (m.get("from"), m.get("task"), m.get("text")) == (sender, task, text):
            return m
    if sum(1 for m in inbox if m.get("status") == "queued") >= MAX_QUEUED:
        raise Denied(f"{to} has {MAX_QUEUED} queued messages; wait until it finishes its task", to=to)
    recent = [m for m in all_messages(board_dir) if m.get("from") == sender and _within(m, now, RATE_WINDOW)]
    if len(recent) >= RATE_LIMIT:
        raise Denied(f"{sender} sent {RATE_LIMIT} messages in the last minute; slow down", sender=sender)
    return None


def _within(message, now, window):
    age = clock.age_seconds(message.get("sent", ""), now)
    return age is not None and age <= window


def make(sender, to, text, task, status, urgent, model):
    return {
        "id": uuid.uuid4().hex[:12],
        "from": sender,
        "to": to,
        "task": task or "",
        "text": text,
        "sent": clock.iso(),
        "status": status,
        "urgent": bool(urgent),
        "model": model,
    }


def promote(rows, busy_card):
    """Queued messages become ready when the recipient is not in doing, or the message is about its doing card."""
    changed = False
    for m in rows:
        if m.get("status") == "queued" and (not busy_card or m.get("task") == busy_card):
            m["status"] = "ready"
            changed = True
    return changed


def counts(board_dir, busy):
    """{agent: {unread, queued}} where `busy` maps agent -> doing card id."""
    out = {}
    for m in all_messages(board_dir):
        c = out.setdefault(m.get("to", ""), {"unread": 0, "queued": 0})
        b = busy.get(m.get("to", ""), "")
        if m.get("status") == "queued" and b and m.get("task") != b:
            c["queued"] += 1
        elif m.get("status") in ("queued", "ready"):
            c["unread"] += 1
    return out
