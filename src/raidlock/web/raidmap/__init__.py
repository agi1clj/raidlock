"""Territory map: every locked path, grouped by repo, with its current holder and contention."""

import os

from raidlock.core import leases as L
from raidlock.core.errors import Precondition
from raidlock.core.paths import overlaps

from ..replay import data_of, fold, records

OPEN = ("todo", "doing", "review", "blocked")
EVENTS = ("lock_violation", "lock_denied")
EVENT_SCAN = 5000
MAX_HISTORY_WORK = 100000


def _relative(board, path):
    if not path:
        return ""
    if not os.path.isabs(path):
        return path.strip("/")
    for root in (board.workspace, os.path.realpath(board.workspace)):
        try:
            rel = os.path.relpath(path, root)
        except ValueError:
            continue
        if not rel.startswith(".."):
            return rel.replace(os.sep, "/")
    return ""


def lock_events(board, until=None):
    """Recorded lock denials and violations: [{seq, ts, event, agent, card, paths}], newest EVENT_SCAN status
    records only."""
    out = []
    source = records(board, until) if until is not None else board.transcript.read(kind="status", limit=EVENT_SCAN)
    for r in source[-EVENT_SCAN:]:
        if until is not None and r["seq"] > until:
            continue
        data = data_of(r)
        if data.get("event") not in EVENTS:
            continue
        raw = data.get("paths") or ([data.get("path")] if data.get("path") else [])
        paths = [p for p in (_relative(board, x) for x in raw) if p]
        out.append(
            {
                "seq": r["seq"],
                "ts": r.get("ts", ""),
                "event": data["event"],
                "agent": data.get("agent") or (r.get("from") or {}).get("agent", ""),
                "card": r.get("taskId", "") or "",
                "paths": paths,
            }
        )
    return out


def _holders_now(board, cards):
    out = []
    for cid, lease in board.leases().items():
        if not L.is_live(lease):
            continue
        card = cards.get(cid, {})
        out.append(
            {
                "agent": lease.get("agent", ""),
                "card": cid,
                "lane": card.get("lane", ""),
                "paths": lease.get("paths") or card.get("locks", []),
                "expires": lease.get("expires"),
            }
        )
    return out


def territory(board, at=None, state=None):
    cards = {c["id"]: c for c in board.cards()}
    if at is None:
        holders = _holders_now(board, cards)
        lanes = {cid: c["lane"] for cid, c in cards.items()}
    else:
        state = state if state is not None else fold(board, at, marks=False)
        cards = {c["id"]: c for c in state["cards"]}
        holders = [dict(h, expires=None) for h in state["holders"]]
        lanes = {c["id"]: c["lane"] for c in state["cards"]}
    events = lock_events(board, until=at)
    if at is not None:
        path_count = sum(len(c.get("locks", [])) for c in cards.values())
        comparisons = sum(len(h["paths"]) for h in holders) + sum(len(e["paths"]) for e in events)
        if path_count * max(1, comparisons) > MAX_HISTORY_WORK:
            return {
                "at": at,
                "trust": "unverified history",
                "approximate": True,
                "incomplete_history": True,
                "error": "history work budget exceeded",
                "repos": [],
                "agents": [],
                "held": 0,
                "contested": 0,
                "events": [],
            }
    held_cards = {h["card"] for h in holders}
    paths = {}

    def entry(path):
        e = paths.get(path)
        if e is None:
            head, _, rest = path.partition("/")
            e = paths[path] = {
                "path": path,
                "metadata_source": "current metadata" if at is not None else "live",
                "repo": head,
                "rest": rest,
                "holder": "",
                "card": "",
                "lane": "",
                "expires": None,
                "cards": [],
                "waiting": [],
                "denials": 0,
                "violations": 0,
            }
        return e

    for h in holders:
        for p in h["paths"]:
            e = entry(p)
            e.update(holder=h["agent"], card=h["card"], lane=h["lane"], expires=h["expires"])
            if at is not None:
                e["metadata_source"] = "unverified history"
    for cid, c in sorted(cards.items()):
        lane = lanes.get(cid)
        if lane not in OPEN:
            continue
        for p in c.get("locks", []):
            e = entry(p)
            if cid not in e["cards"]:
                e["cards"].append(cid)
    for e in paths.values():
        for cid in e["cards"]:
            if cid in held_cards or lanes.get(cid) not in ("todo", "blocked"):
                continue
            for h in holders:
                clash = h["card"] != cid and any(overlaps(e["path"], p) for p in h["paths"])
                if clash and cid not in e["waiting"]:
                    e["waiting"].append(cid)
        for ev in events:
            if any(overlaps(e["path"], p) for p in ev["paths"]):
                e["violations" if ev["event"] == "lock_violation" else "denials"] += 1
        e["contested"] = bool(e["waiting"] or e["denials"] or e["violations"])
    repos = {}
    for e in sorted(paths.values(), key=lambda x: x["path"]):
        repos.setdefault(e["repo"], []).append(e)
    agents = sorted({e["holder"] for e in paths.values() if e["holder"]})
    return {
        "at": at,
        "trust": "unverified history" if at is not None else "live",
        "approximate": at is not None,
        "incomplete_history": bool(state and state["incomplete_history"]),
        "repos": [
            {
                "name": name,
                "paths": items,
                "held": sum(1 for i in items if i["holder"]),
                "contested": sum(1 for i in items if i["contested"]),
            }
            for name, items in sorted(repos.items())
        ],
        "agents": agents,
        "held": sum(1 for e in paths.values() if e["holder"]),
        "contested": sum(1 for e in paths.values() if e["contested"]),
        "events": events[-50:],
    }


def payload(board, query):
    raw = (query.get("at") or [""])[0]
    try:
        at = int(raw) if raw else None
    except ValueError:
        raise Precondition("at must be an integer") from None
    if at is not None and at < 0:
        raise Precondition("at must be a nonnegative transcript sequence number")
    return territory(board, at)


def _setup(parser):
    parser.add_argument("--at", type=int, default=None, help="transcript sequence number (default: live leases)")


def _handle(ctx, args):
    result = territory(ctx.board, args.at)
    lines = []
    for repo in result["repos"]:
        lines.append(f"{repo['name']}: {repo['held']} held, {repo['contested']} contested")
        for e in repo["paths"]:
            who = f"{e['holder']} on {e['card']}" if e["holder"] else "free"
            flag = " CONTESTED" if e["contested"] else ""
            lines.append(f"  {e['path']}: {who}{flag}")
    return result, "\n".join(lines) or "No locked paths."


def register_cli(add_command):
    add_command("map", "territory map: which agent holds which locked paths, and where they clash", _setup, _handle)
