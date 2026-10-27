"""Rebuild board state at any transcript sequence number by folding the recorded events in order."""

import math

from raidlock.core.errors import Precondition
from raidlock.core.transcript import KINDS

LANES = ("todo", "doing", "review", "blocked", "done")
HOLD = ("doing", "review", "blocked")
PAGE = 5000
MAX_RECORDS = 200000
MARKS = 4000
RECENT = 8


def data_of(record):
    out = {}
    parts = list(record.get("parts", []))
    for artifact in record.get("artifacts", []):
        parts.extend(artifact.get("parts", []))
    for part in parts:
        if part.get("kind") == "data" and isinstance(part.get("data"), dict):
            out.update(part["data"])
    return out


def text_of(record):
    return "\n".join(p.get("text", "") for p in record.get("parts", []) if p.get("kind") == "text")


MAX_BYTES = 16 * 1024 * 1024


def history(board):
    version = board.version()
    cached = getattr(board, "_history_cache", None)
    if cached and cached[0] == version:
        return cached[1]
    transcript = board.transcript
    latest = transcript.index.query_transcript(transcript.ws, limit=1)
    last = latest[-1]["seq"] if latest else 0
    out, cursor, size = [], 0, 0
    incomplete = False
    while cursor < last:
        rows = transcript.index.query_transcript(transcript.ws, since=cursor, limit=PAGE)
        selected = []
        for row in rows:
            if len(out) + len(selected) >= MAX_RECORDS or size + row["length"] > MAX_BYTES:
                incomplete = True
                break
            selected.append(row)
            size += row["length"]
        out.extend(transcript._load(selected))
        if incomplete or not rows:
            break
        cursor = rows[-1]["seq"]
    result = (out, last, incomplete)
    board._history_cache = (version, result)
    return result


def records(board, until=None):
    every, _, _ = history(board)
    return [r for r in every if until is None or r["seq"] <= until]


class State:
    def __init__(self, titles):
        self.titles = titles
        self.cards = {}
        self.locks = {}
        self.non_core = False
        self.events = {"lock_violation": 0, "lock_denied": 0, "stale_fence": 0}

    def card(self, cid):
        c = self.cards.get(cid)
        if c is None:
            c = self.cards[cid] = {
                "id": cid,
                "trust": "unverified history",
                "title_source": "current metadata",
                "lease_holder": "",
                "title": self.titles.get(cid, cid),
                "lane": "todo",
                "owner": "",
                "tokens": 0,
                "usd_equiv": 0.0,
                "notes": 0,
                "since": "",
            }
        return c

    def apply(self, r):
        kind, task = r.get("kind"), r.get("taskId") or ""
        if r.get("src") != "board" or kind not in KINDS:
            self.non_core = True
            return
        sender = (r.get("from") or {}).get("agent", "")
        text, data = text_of(r), data_of({"parts": r.get("parts", [])})
        event = data.get("event")
        if event in self.events:
            self.events[event] += 1
        if not task:
            return
        if kind == "status":
            if text.startswith(f"created {task}:"):
                c = self.card(task)
                c["title"] = text.split(":", 1)[1].strip() or task
                c["title_source"] = "unverified history"
                if isinstance(data.get("locks"), list):
                    self.locks[task] = data["locks"]
                c.update(lane="todo", owner="", lease_holder="", since=r.get("ts", ""))
            elif (
                data.get("a2a_state") == "working"
                and "fence" in data
                and (text.startswith("claimed by ") or "taken over from" in text)
            ):
                self.card(task).update(lane="doing", owner=sender, lease_holder=sender, since=r.get("ts", ""))
            elif data.get("lane") in LANES and text.startswith(f"{task} -> {data['lane']}"):
                c = self.card(task)
                c.update(lane=data["lane"], since=r.get("ts", ""))
                if data["lane"] in ("todo", "done"):
                    c["lease_holder"] = ""
                if data["lane"] == "todo":
                    c["owner"] = ""
            elif text == "lease released":
                c = self.card(task)
                c["lease_holder"] = ""
                if c["lane"] == "doing":
                    c.update(lane="todo", owner="", lease_holder="", since=r.get("ts", ""))
            elif data.get("a2a_state") == "submitted" and text.endswith("reopened"):
                self.card(task).update(lane="todo", owner="", lease_holder="", since=r.get("ts", ""))
            elif text == "lease expired; reaped to blocked":
                self.card(task).update(lane="blocked", lease_holder="", since=r.get("ts", ""))
            elif text == "lease resumed after expiry":
                self.card(task)["lease_holder"] = sender
            if (
                (text.startswith(f"created {task}:") or text.startswith("claimed by ") or text == "locks updated")
                and isinstance(data.get("locks"), list)
                and all(isinstance(p, str) for p in data["locks"])
            ):
                self.locks[task] = data["locks"]
        elif kind == "cost":
            c = self.card(task)
            c["tokens"] += int(data.get("tokens") or 0)
            value = float(data.get("usd_equiv") or 0)
            if math.isfinite(value):
                c["usd_equiv"] = round(c["usd_equiv"] + value, 6)
        elif kind == "note":
            self.card(task)["notes"] += 1


def mark(r):
    text = text_of(r) or ", ".join(a.get("name", "") for a in r.get("artifacts", []))
    return {
        "seq": r["seq"],
        "trust": "unverified history",
        "source": r.get("src", "unknown"),
        "ts": r.get("ts", ""),
        "kind": r.get("kind", ""),
        "task": r.get("taskId", "") or "",
        "agent": (r.get("from") or {}).get("agent", ""),
        "text": text[:160],
    }


def fold(board, at=None, marks=True):
    """Board state right after transcript record `at` (default: the newest)."""
    if at is not None and (not isinstance(at, int) or at < 0):
        raise Precondition("at must be a nonnegative transcript sequence number")
    current = {c["id"]: c for c in board.cards()}
    every, latest, incomplete = history(board)
    if not every:
        return {
            "at": 0,
            "ts": "",
            "first": 0,
            "last": latest,
            "incomplete_history": incomplete,
            "trust": "unverified history",
            "non_core_records": False,
            "total": 0,
            "marks": [],
            "truncated": False,
            "cards": [],
            "counts": dict.fromkeys(LANES, 0),
            "holders": [],
            "recent": [],
            "events": {},
            "untracked": len(current),
        }
    last = every[-1]["seq"]
    point = last if at is None else max(every[0]["seq"], min(at, last))
    state = State({k: v.get("title", k) for k, v in current.items()})
    upto = []
    for r in every:
        if r["seq"] > point:
            break
        state.apply(r)
        upto.append(r)
    lock_of = {k: v.get("locks", []) for k, v in current.items()}
    cards = sorted(state.cards.values(), key=lambda c: (LANES.index(c["lane"]), c["id"]))
    for c in cards:
        c["locks"] = state.locks.get(c["id"], lock_of.get(c["id"], []))
        c["locks_source"] = "unverified history" if c["id"] in state.locks else "current metadata"
    holders = [
        {"agent": c["lease_holder"], "card": c["id"], "lane": c["lane"], "paths": c["locks"]}
        for c in cards
        if c["lease_holder"] and c["id"] in state.locks
    ]
    shown = [mark(r) for r in every[-MARKS:]] if marks else []
    return {
        "at": point,
        "ts": upto[-1].get("ts", "") if upto else "",
        "first": every[0]["seq"],
        "last": latest,
        "incomplete_history": incomplete,
        "trust": "unverified history",
        "non_core_records": state.non_core,
        "total": len(every),
        "marks": shown,
        "truncated": len(every) > MARKS,
        "cards": cards,
        "counts": {lane: sum(1 for c in cards if c["lane"] == lane) for lane in LANES},
        "holders": holders,
        "recent": [mark(r) for r in upto[-RECENT:]][::-1],
        "events": dict(state.events),
        "untracked": sum(1 for k in current if k not in state.cards),
    }


def payload(board, query):
    raw = (query.get("at") or [""])[0]
    try:
        at = int(raw) if raw else None
    except ValueError:
        raise Precondition("at must be an integer") from None
    from ..raidmap import territory

    state = fold(board, at, marks=(query.get("marks") or ["1"])[0] != "0")
    return {**state, "territory": territory(board, state["at"], state=state)}


def _setup(parser):
    parser.add_argument("--at", type=int, default=None, help="transcript sequence number (default: newest)")


def _handle(ctx, args):
    result = fold(ctx.board, args.at, marks=False)
    lines = [f"at seq {result['at']} of {result['first']}..{result['last']} ({result['ts'] or 'empty'})"]
    lines.append(", ".join(f"{n} {lane}" for lane, n in result["counts"].items()))
    for c in result["cards"]:
        lines.append(f"  {c['lane']:<8} {c['id']}" + (f" ({c['owner']})" if c["owner"] else ""))
    return result, "\n".join(lines)


def register_cli(add_command):
    add_command("replay", "board state rebuilt from the transcript at a point in time", _setup, _handle)
