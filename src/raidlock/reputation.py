from raidlock.core import Precondition, receipts
from raidlock.core.status import agent_status

REAPED = ("lease expired; reaped to blocked", "lease expired; reaped in blocked")
WEIGHTS = {"verify": 30, "review": 20, "rework": 5, "lock": 10, "stall": 5}
_COUNTS = (
    "verify_passed",
    "verify_failed",
    "review_approve",
    "review_changes",
    "rework",
    "lock_violations",
    "lock_denials",
    "stalls",
)


def reputation(board, by="agent"):
    """Return deterministic trust rows, with raw inputs, for UI, routing and CLI.

    Score = clamp(50 + verify_weight * (2 * verify_pass_rate - 1)
    + review_weight * (2 * approval_rate - 1) - rework_weight * rework
    - lock_weight * (lock_violations + lock_denials) - stall_weight * stalls,
    0, 100), rounded to two decimals. Missing rates contribute zero; an
    unobserved agent starts at 50. WEIGHTS contains all weights. Grouped rates
    use pooled counts, never averages of agent scores. All recorded verdicts
    count, including superseded reviews; comment verdicts do not count.

    Transcript claim events establish historical ownership and model identity.
    Verification belongs to the task owner, not the lead running the command;
    reviews belong to their recipient, not their author. A changes verdict with
    a held lease counts as rework. Card histories fill in only tasks without
    corresponding transcript events, using the current owner when history is
    unavailable. Unknown models/providers are grouped as "unknown".

    Lock evidence must be a structured transcript data part with event equal
    to "lock_violation", "lock_denied" or "stale_fence"; its agent defaults
    to the sender. Core check_write denials are recorded as lock violations;
    stale owner-command fences count as denials. Absent events do not prove
    absence of violations. Stalls count recorded lease reaps plus current
    stalled tasks, once per episode. Core reap records currently identify
    expiry by their exact text. No board state is changed by this function.
    """
    if by not in ("agent", "model", "provider"):
        raise Precondition("reputation grouping must be agent, model or provider")
    agents = board.agents()
    cards = {card["id"]: card for card in board.cards() if card["lane"] != "cancelled"}
    groups = {}
    identities = {name: {"agent": name, **record} for name, record in agents.items()}
    owners, held, stalled = {}, {}, set()
    seen = set()
    verified = set()

    def identity(name):
        return identities.get(name, {"agent": name})

    def bucket(person):
        key = person.get(by) or "unknown"
        return groups.setdefault(key, dict.fromkeys(_COUNTS, 0))

    def add(person, field):
        if person.get("agent"):
            bucket(person)[field] += 1

    for person in identities.values():
        bucket(person)
    for record in board.transcript.read():
        sender = record.get("from") or {}
        name = sender.get("agent", "")
        if name:
            identities[name] = {**identity(name), **sender}
        task = record.get("taskId", "")
        if task and task not in cards:
            continue
        kind = record.get("kind")
        data = _data(record)
        text = "\n".join(p.get("text", "") for p in record.get("parts", []) if p.get("kind") == "text")
        if kind == "status" and data.get("a2a_state") == "working" and "fence" in data:
            owners[task] = dict(identity(name))
            held[task] = True
            stalled.discard(task)
        owner = owners.get(task) or identity(cards.get(task, {}).get("owner", ""))
        if (
            kind == "verify"
            and receipts.valid(board, cards.get(task, {"id": task}), data)
            and data["signature"] not in verified
        ):
            seen.add((task, kind))
            verified.add(data["signature"])
            add(owner, "verify_passed" if data["passed"] else "verify_failed")
        if kind == "review" and data.get("verdict") in ("approve", "changes"):
            seen.add((task, kind))
            recipient = record.get("to") or owner.get("agent", "")
            person = owner if recipient == owner.get("agent") else identity(recipient)
            add(person, "review_" + data["verdict"])
            if data["verdict"] == "changes" and held.get(task, True):
                add(person, "rework")
        if kind == "status" and text == "lease resumed after expiry":
            stalled.discard(task)
            held[task] = True
        if kind == "status" and text in REAPED:
            if task not in stalled:
                add(owner, "stalls")
            stalled.add(task)
            held[task] = False
        if kind == "status" and (
            data.get("lane") in ("todo", "done") or text == "lease released" or data.get("a2a_state") == "submitted"
        ):
            held[task] = False
        event = data.get("event")
        if event in ("lock_violation", "lock_denied", "stale_fence"):
            add(identity(data.get("agent") or name), "lock_violations" if event == "lock_violation" else "lock_denials")
    for task, card in cards.items():
        owner = identity(card.get("owner", ""))
        if (task, "verify") not in seen:
            for result in card.get("verifications", []):
                if receipts.valid(board, card, result) and result["signature"] not in verified:
                    verified.add(result["signature"])
                    add(owner, "verify_passed" if result["passed"] else "verify_failed")
        if (task, "review") not in seen:
            for result in card.get("reviews", []):
                if result.get("verdict") in ("approve", "changes"):
                    add(owner, "review_" + result["verdict"])
                    if result["verdict"] == "changes":
                        add(owner, "rework")
    leases = board.leases()
    for task, card in cards.items():
        name = card.get("owner", "")
        status = agent_status(name, [card], leases)
        if status["status"] == "stalled" and task not in stalled:
            historical = owners.get(task)
            add(historical if historical and historical.get("agent") == name else identity(name), "stalls")
    rows = []
    for key, inputs in sorted(groups.items()):
        verifies = inputs["verify_passed"] + inputs["verify_failed"]
        reviews = inputs["review_approve"] + inputs["review_changes"]
        inputs["verify_pass_rate"] = inputs["verify_passed"] / verifies if verifies else None
        inputs["approval_rate"] = inputs["review_approve"] / reviews if reviews else None
        contributions = {
            "verify": WEIGHTS["verify"] * (2 * inputs["verify_pass_rate"] - 1) if verifies else 0,
            "review": WEIGHTS["review"] * (2 * inputs["approval_rate"] - 1) if reviews else 0,
            "rework": -WEIGHTS["rework"] * inputs["rework"],
            "lock": -WEIGHTS["lock"] * (inputs["lock_violations"] + inputs["lock_denials"]),
            "stall": -WEIGHTS["stall"] * inputs["stalls"],
        }
        rows.append(
            {
                "key": key,
                "score": round(max(0, min(100, 50 + sum(contributions.values()))), 2),
                "inputs": inputs,
                "contributions": contributions,
            }
        )
    return {
        "by": by,
        "rows": rows,
        "weights": dict(WEIGHTS),
        "base_score": 50,
        "formula": "round(clamp(base_score + sum(contributions), 0, 100), 2)",
        "limitations": [
            "Lock counts cover recorded structured events only.",
            "Card-only histories use current ownership; rework uses changes verdicts.",
            "Transcript evidence takes precedence per card and event type; missing logs can undercount.",
            "Scores summarize recorded evidence; routing integration is not implemented here.",
        ],
    }


def _data(record):
    data = {}
    parts = list(record.get("parts", []))
    for artifact in record.get("artifacts", []):
        parts.extend(artifact.get("parts", []))
    for part in parts:
        if part.get("kind") == "data" and isinstance(part.get("data"), dict):
            data.update(part["data"])
    return data


def _setup(parser):
    parser.add_argument("--by", choices=("agent", "model", "provider"), default="agent")


def _handle(ctx, args):
    result = reputation(ctx.board, args.by)
    lines = []
    for row in result["rows"]:
        inputs = ", ".join(f"{key}={value}" for key, value in row["inputs"].items())
        lines.append(f"{row['key']}: {row['score']:.2f}/100 ({inputs})")
    return result, "\n".join(lines) or "No reputation data."


def register_cli(add_command):
    add_command("reputation", "trust scores and their recorded inputs", _setup, _handle, mutating=False)
