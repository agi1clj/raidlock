"""Raid frames: per-agent context fill (health) and burn rate (mana).

Context fill uses the prompt side (input + cache reads + cache writes) of the agent's newest recorded run, over the
model's context window. A run that spans several turns reports summed usage, so the fill is an upper bound. Agents
with no recorded run get an estimate of ceil(characters / 4) over the transcript records they sent or received
since their current claim, plus the card brief; the payload marks it as an estimate.

The window comes from `context_window` on the model's entry in `$RAIDLOCK_HOME/pricing.json`; without it raidlock
assumes DEFAULT_WINDOW and says so. Burn rate is tokens and API-equivalent USD per hour of recorded run time over
the newest BURN_RUNS runs.
"""

import contextlib
import os

from .core import RaidlockError, clock, store
from .core.status import agent_status
from .core.tokens import estimate
from .party import roles

DEFAULT_WINDOW = 200000
WARN_PCT = 80
CRITICAL_PCT = 95
BURN_RUNS = 5
ESTIMATE_RECORDS = 400


def _windows(home):
    try:
        data = store.read_json(os.path.join(home, "pricing.json")) or {}
    except (OSError, ValueError, RaidlockError):
        data = {}
    out = {}
    for model, rates in (data.get("models") or {}).items():
        value = (rates or {}).get("context_window") if isinstance(rates, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            out[model] = value
    return out


def _prompt(row):
    u = row.get("usage") or {}
    return int(u.get("input") or 0) + int(u.get("cached_input") or 0) + int(u.get("cache_write") or 0)


def _estimate(board, name, card):
    since = ""
    if card:
        claims = [n["ts"] for n in card.get("notes", []) if str(n.get("text", "")).startswith(f"claimed by {name}")]
        since = claims[-1] if claims else ""
    text = []
    for r in board.transcript.read(agent=name, limit=ESTIMATE_RECORDS):
        if since and r.get("ts", "") < since:
            continue
        text.extend(p.get("text", "") for p in r.get("parts", []) if p.get("kind") == "text")
    tokens = estimate("\n".join(text))
    if card and card.get("lane") in ("todo", "doing", "blocked", "review"):
        with contextlib.suppress(RaidlockError, OSError, ValueError, KeyError):
            tokens += int(board.brief(card["id"]).get("tokens") or 0)
    return tokens


def level(pct):
    if pct is None:
        return "unknown"
    return "critical" if pct >= CRITICAL_PCT else "warning" if pct >= WARN_PCT else "ok"


def frames(board):
    cards = board.cards()
    agents = board.agents()
    leases = board.leases()
    windows = _windows(board.home)
    runs = {}
    for c in cards:
        for row in c.get("costs", []):
            runs.setdefault(row.get("by", ""), []).append((row.get("ts", ""), c["id"], row))
    now = clock.now()
    party = roles(board)
    out = []
    for name in sorted(agents):
        a = agents[name]
        status = agent_status(name, cards, leases, at=now)
        mine = sorted(runs.get(name, []), key=lambda x: x[0])
        current = next((c for c in cards if c["id"] == status.get("card")), None)
        model = a.get("model", "") or (mine[-1][2].get("model", "") if mine else "")
        window = windows.get(model)
        window_source = "pricing" if window else "assumed"
        window = window or DEFAULT_WINDOW
        if mine:
            used, source, last = _prompt(mine[-1][2]), "usage", mine[-1][0]
        else:
            used, source, last = _estimate(board, name, current), "estimate", ""
        pct = round(100.0 * used / window, 1) if window else None
        recent = [r for _, _, r in mine[-BURN_RUNS:]]
        ms = sum(int(r.get("duration_ms") or 0) for r in recent)
        tokens = sum(int(r.get("tokens") or 0) for r in recent)
        usd = sum(float(r.get("usd_equiv") or 0) for r in recent)
        hours = ms / 3600000.0
        out.append(
            {
                "agent": name,
                "role": a.get("role", "worker"),
                "party": (party.get(name) or {}).get("role", ""),
                "team": a.get("team", ""),
                "model": model,
                "provider": a.get("provider", ""),
                "runtime": a.get("runtime", ""),
                "status": status.get("status", "idle"),
                "card": status.get("card", "") or "",
                "context": {
                    "tokens": used,
                    "window": window,
                    "window_source": window_source,
                    "pct": pct,
                    "source": source,
                    "as_of": last,
                    "level": level(pct),
                },
                "burn": {
                    "runs": len(recent),
                    "tokens_per_hour": round(tokens / hours) if hours else None,
                    "usd_per_hour": round(usd / hours, 4) if hours else None,
                    "tokens": tokens,
                    "usd_equiv": round(usd, 6),
                },
                "handoff": level(pct) in ("warning", "critical") and status.get("status") != "idle",
            }
        )
    peak = max([f["burn"]["tokens_per_hour"] or 0 for f in out] + [0])
    for f in out:
        rate = f["burn"]["tokens_per_hour"]
        f["burn"]["share"] = round(rate / peak, 4) if rate and peak else 0
    return {
        "agents": out,
        "warn_pct": WARN_PCT,
        "critical_pct": CRITICAL_PCT,
        "default_window": DEFAULT_WINDOW,
        "handoffs": [f["agent"] for f in out if f["handoff"]],
    }


def payload(board, query):
    return frames(board)


def _handle(ctx, args):
    result = frames(ctx.board)
    lines = []
    for f in result["agents"]:
        c, b = f["context"], f["burn"]
        pct = "?" if c["pct"] is None else f"{c['pct']:.0f}%"
        tag = " (estimate)" if c["source"] == "estimate" else ""
        win = "" if c["window_source"] == "pricing" else " assumed"
        rate = f"{b['tokens_per_hour']} tok/h" if b["tokens_per_hour"] else "no runs timed"
        warn = "  HAND OFF SOON" if f["handoff"] else ""
        lines.append(f"{f['agent']}: context {pct}{tag} of {c['window']}{win}; burn {rate}{warn}")
    return result, "\n".join(lines) or "No agents."


def register_cli(add_command):
    add_command("frames", "per-agent context fill and burn rate, with handoff warnings", None, _handle)
