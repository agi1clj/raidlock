from . import clock

WORKING_SECONDS = 15 * 60
STALLED_SECONDS = 45 * 60
LABELS = {
    "working": "Working",
    "quiet": "Working (quiet)",
    "review": "Waiting for review",
    "blocked": "Blocked",
    "stalled": "Stalled",
    "idle": "Idle",
}
ORDER = ("working", "quiet", "blocked", "stalled", "review", "idle")


def _doing_status(lease, at):
    if not lease or not lease.get("expires") or (clock.parse(lease["expires"]) or at) <= at:
        return "stalled"
    age = clock.age_seconds(lease.get("renewed", ""), at)
    if age is None or age > STALLED_SECONDS:
        return "stalled"
    return "quiet" if age > WORKING_SECONDS else "working"


def agent_status(name, cards, leases, at=None):
    """Derived status of one agent from its cards and its own lease heartbeat (never card.updated)."""
    at = at or clock.now()
    mine = [c for c in cards if c.get("owner") == name]
    doing = next((c for c in mine if c["lane"] == "doing"), None)
    if doing:
        lease = leases.get(doing["id"])
        return {"status": _doing_status(lease, at), "card": doing["id"], "heartbeat": (lease or {}).get("renewed", "")}
    for lane, status in (("blocked", "blocked"), ("review", "review")):
        card = next((c for c in mine if c["lane"] == lane), None)
        if card:
            return {
                "status": status,
                "card": card["id"],
                "heartbeat": (leases.get(card["id"]) or {}).get("renewed", ""),
            }
    return {"status": "idle", "card": "", "heartbeat": ""}


def statuses(agents, cards, leases, at=None):
    """{agent: {status, label, card, heartbeat}} for every joined agent and card owner."""
    names = set(agents) | {c["owner"] for c in cards if c.get("owner")}
    out = {}
    for name in sorted(names):
        s = agent_status(name, cards, leases, at)
        s["label"] = LABELS[s["status"]]
        out[name] = s
    return out
