import hashlib
import re

from ..core import canonical, clock
from ..core.errors import Precondition
from .vault import PENDING, Vault, one_line

PAGE = 500
LESSON_PREFIX = re.compile(r"^\s*lesson\s*:\s*", re.I)
SOURCES = ("review", "note", "patch")


def _texts(parts):
    return [p.get("text", "") for p in parts or [] if p.get("kind") == "text" and p.get("text")]


def _data(parts):
    for p in parts or []:
        if p.get("kind") == "data" and isinstance(p.get("data"), dict):
            return p["data"]
    return {}


def _artifact(rec, name=None, kind=None):
    for a in rec.get("artifacts") or []:
        if (name and a.get("name") == name) or (kind and (a.get("metadata") or {}).get("kind") == kind):
            return a
    return None


def classify(rec):
    """(source, text, extra_paths) for a transcript record that carries a lesson, else None."""
    kind, text = rec.get("kind"), " ".join(_texts(rec.get("parts")))
    if kind == "review":
        a = _artifact(rec, kind="review")
        if a and _data(a.get("parts")).get("verdict") == "changes":
            return "review", "Review asked for changes: " + (text or "no note"), []
        return None
    if kind == "note" and LESSON_PREFIX.match(text):
        body = LESSON_PREFIX.sub("", text, count=1)
        return ("note", body, []) if body.strip() else None
    if kind == "handback":
        a = _artifact(rec, name="rejected.patch") or _artifact(rec, kind="patch")
        if a and (a.get("name") == "rejected.patch" or _data(a.get("parts")).get("rejected")):
            hb = _artifact(rec, name="handback")
            changed = _data(hb.get("parts")).get("paths_changed", []) if hb else []
            return "patch", "Runner rejected the patch: " + (text.split("; ", 1)[-1] if "; " in text else text), changed
        return None
    return None


def lesson_id(board, rec):
    return hashlib.sha256(f"{board.uid}:{rec.get('messageId', '')}".encode()).hexdigest()[:12]


def _canon(paths):
    out = []
    for p in paths:
        try:
            c = canonical(p)
        except Precondition:
            continue
        if c not in out:
            out.append(c)
    return out


def build(board, rec, card=None):
    """The lesson for a transcript record, or None. `card` is the card dict when it is known."""
    found = classify(rec)
    if not found:
        return None
    source, text, extra = found
    task = rec.get("taskId") or ""
    if card is None and task:
        try:
            card = board.card(task)
        except Exception:
            card = None
    locks = _canon((card or {}).get("locks") or [])
    repo = locks[0].split("/", 1)[0] if locks else ""
    paths = list(locks)
    if repo:
        for p in _canon([f"{repo}/{x}" for x in extra]):
            if p not in paths and not any(p.startswith(q + "/") for q in locks):
                paths.append(p)
    sender = rec.get("from") or {}
    return {
        "id": lesson_id(board, rec),
        "created": rec.get("ts") or clock.iso(),
        "workspace": board.name,
        "workspace_uid": board.uid,
        "repo": repo,
        "paths": paths,
        "card": task,
        "agent": sender.get("agent", ""),
        "provider": sender.get("provider", ""),
        "model": sender.get("model", ""),
        "source": source,
        "source_id": rec.get("messageId", ""),
        "tags": ["lore", source],
        "hits": 0,
        "last_used": "",
        "status": "pending",
        "proposed_by": "capture",
        "approved_by": "",
        "approved_at": "",
        "text": one_line(text),
    }


def known(vault, lesson_id, state):
    return vault.has(lesson_id) or vault.has(lesson_id, PENDING) or lesson_id in (state.get("rejected") or [])


def capture(board):
    """Scan the transcript since the last cursor and write each lesson-bearing record as a pending lesson.
    Nothing is injectable until the lead approves it. Idempotent: ids derive from the record's messageId.
    Returns {captured, ids, since}."""
    vault = Vault.for_board(board)
    new, cards = [], {}
    with vault.locked():
        state = vault.state()
        since = int(state.get("since") or 0)
        while True:
            rows = board.transcript.read(since=since, limit=PAGE)
            if not rows:
                break
            for rec in rows:
                since = max(since, rec.get("seq", since))
                if not classify(rec):
                    continue
                task = rec.get("taskId") or ""
                if task and task not in cards:
                    try:
                        cards[task] = board.card(task)
                    except Exception:
                        cards[task] = None
                lesson = build(board, rec, cards.get(task))
                if lesson and not known(vault, lesson["id"], state):
                    vault.put(lesson, PENDING)
                    new.append(lesson)
            if len(rows) < PAGE:
                break
        vault.save_state({**state, "since": since})
    return {"captured": len(new), "ids": [x["id"] for x in new], "since": since}
