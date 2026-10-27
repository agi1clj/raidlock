"""Card dependency graph and a lock-contention heatmap (lock path by day)."""

from raidlock.core.errors import RaidlockError
from raidlock.core.paths import overlaps

from ..raidmap import lock_events, territory
from ..replay import data_of, text_of

DAYS = 14
MAX_ROWS = 40


MAX_NODES = 5000
MAX_EDGES = 20000


class GraphTooLarge(RaidlockError):
    pass


def layers(cards):
    if len(cards) > MAX_NODES or sum(len(c.get("depends", [])) for c in cards) > MAX_EDGES:
        raise GraphTooLarge("graph too large")
    ids = {c["id"] for c in cards}
    deps = {c["id"]: [d for d in c.get("depends", []) if d in ids] for c in cards}
    depth, cycles = {}, []
    active = set()
    for root in sorted(ids):
        if root in depth:
            continue
        stack = [(root, iter(deps[root]))]
        active.add(root)
        while stack:
            cid, children = stack[-1]
            child = next(children, None)
            if child is None:
                depth[cid] = 1 + max((depth.get(d, -1) for d in deps[cid]), default=-1)
                active.remove(cid)
                stack.pop()
            elif child in active:
                trail = [node for node, _ in stack]
                cycles.append(trail[trail.index(child) :])
            elif child not in depth:
                active.add(child)
                stack.append((child, iter(deps[child])))
    return depth, cycles


def dependency_graph(cards):
    depth, cycles = layers(cards)
    ids = {c["id"] for c in cards}
    rows = {}
    nodes = []
    order = ("doing", "review", "blocked", "todo", "done", "cancelled")

    def sort_key(c):
        lane = c.get("lane", "todo")
        return depth[c["id"]], order.index(lane) if lane in order else len(order), c["id"]

    for c in sorted(cards, key=sort_key):
        d = depth[c["id"]]
        rows[d] = rows.get(d, -1) + 1
        nodes.append(
            {
                "id": c["id"],
                "title": c.get("title", ""),
                "lane": c.get("lane", ""),
                "owner": c.get("owner", ""),
                "team": c.get("team", ""),
                "repos": c.get("repos") or sorted({p.split("/")[0] for p in c.get("locks", [])}),
                "depth": d,
                "row": rows[d],
            }
        )
    edges = []
    for c in cards:
        for dep in c.get("depends", []):
            edges.append({"from": dep, "to": c["id"], "missing": dep not in ids})
    return {"nodes": nodes, "edges": edges, "cycles": cycles, "columns": 1 + max(depth.values(), default=-1)}


def heatmap(board, cards):
    by_id = {c["id"]: c for c in cards}
    hits = {}
    takeovers = []
    for r in board.transcript.read(kind="status", limit=5000):
        if "taken over from" in text_of(r) and data_of(r).get("a2a_state") == "working":
            takeovers.append({"ts": r.get("ts", ""), "paths": by_id.get(r.get("taskId", ""), {}).get("locks", [])})
    events = [{"ts": e["ts"], "paths": e["paths"], "event": e["event"]} for e in lock_events(board)]
    events += [{"ts": t["ts"], "paths": t["paths"], "event": "takeover"} for t in takeovers]
    locked = {p for c in cards for p in c.get("locks", [])}
    loose = {p for e in events for p in e["paths"] if not any(overlaps(p, q) for q in locked)}
    paths = sorted(locked | loose)
    days = sorted({e["ts"][:10] for e in events if e["ts"]})[-DAYS:]
    for e in events:
        day = e["ts"][:10]
        if day not in days:
            continue
        for p in paths:
            if any(overlaps(p, q) for q in e["paths"]):
                row = hits.setdefault(p, {})
                cell = row.setdefault(day, {"lock_denied": 0, "lock_violation": 0, "takeover": 0})
                cell[e["event"]] += 1
    waiting = {e["path"]: len(e["waiting"]) for repo in territory(board)["repos"] for e in repo["paths"]}
    rows = []
    for p in paths:
        cells = [hits.get(p, {}).get(d, {"lock_denied": 0, "lock_violation": 0, "takeover": 0}) for d in days]
        total = sum(sum(c.values()) for c in cells)
        if not total and not waiting.get(p):
            continue
        rows.append({"path": p, "cells": cells, "total": total, "waiting": waiting.get(p, 0)})
    rows.sort(key=lambda r: (-(r["total"] + r["waiting"]), r["path"]))
    peak = max([sum(c.values()) for r in rows for c in r["cells"]] + [0])
    return {"days": days, "rows": rows[:MAX_ROWS], "peak": peak, "paths": len(paths)}


def build(board):
    cards = board.cards()
    return {**dependency_graph(cards), "heat": heatmap(board, cards)}


def payload(board, query):
    return build(board)


def _handle(ctx, args):
    result = build(ctx.board)
    lines = [f"{len(result['nodes'])} cards, {len(result['edges'])} dependencies"]
    for e in result["edges"]:
        lines.append(f"  {e['from']} -> {e['to']}" + (" (missing)" if e["missing"] else ""))
    for c in result["cycles"]:
        lines.append("  cycle: " + " -> ".join(c))
    heat = result["heat"]
    if heat["rows"]:
        lines.append("Contention:")
        for r in heat["rows"]:
            lines.append(f"  {r['path']}: {r['total']} events, {r['waiting']} waiting")
    return result, "\n".join(lines)


def register_cli(add_command):
    add_command("graph", "card dependency graph and lock-contention heatmap", None, _handle)
