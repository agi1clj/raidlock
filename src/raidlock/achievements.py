"""Achievements earned from recorded board events. The web UI shows them unless the viewer turns them off.

Every rule reads the transcript and cards; nothing is stored and nothing is granted. Names and art are original to
raidlock.
"""

import datetime

from .core import clock, receipts
from .web.replay import data_of, history, records, text_of

CLEAN_CARDS = 50
SWIFT_MINUTES = 30
PEACE_DAYS = 7
TITLES = {
    "clean-hands": "the Unbroken",
    "first-seal": "the Sealbearer",
    "second-opinion": "the Wise",
    "clean-run": "the Flawless",
    "swift-blade": "the Swift",
    "night-watch": "of the Night Watch",
    "peacekeeper": "the Peacekeeper",
}
CATALOG = (
    ("first-light", "First Light", "A card reaches done for the first time.", "sun"),
    ("first-seal", "Wax Seal", "The first verify receipt that passes and checks out.", "seal"),
    ("second-opinion", "Second Opinion", "A review from a different provider than the card's owner.", "eyes"),
    ("second-draft", "Second Draft", "A card that got changes requested and was then approved.", "quill"),
    ("many-voices", "Many Voices", "Cards finished by agents from two or more providers.", "chord"),
    (
        "clean-hands",
        "Clean Hands",
        f"One agent finishes {CLEAN_CARDS} cards with zero recorded lock violations.",
        "hands",
    ),
    ("clean-run", "Clean Run", "A card closes on its first verify run.", "rune"),
    ("peacekeeper", "Peacekeeper", f"{PEACE_DAYS} days of finished cards with no lock violation.", "shield"),
    ("swift-blade", "Swift Blade", f"A card goes from claim to done within {SWIFT_MINUTES} minutes.", "blade"),
    ("night-watch", "Night Watch", "A card is finished between midnight and 4 a.m. local time.", "moon"),
)


def evaluate(board):
    agents = board.agents()
    cards = {c["id"]: c for c in board.cards()}

    def provider(name):
        return (agents.get(name) or {}).get("provider", "") or ""

    owners = {}
    earned = {}
    violations = {}
    done_by = {}
    changed = set()
    providers_done = {}
    first_verify = {}
    claimed_at = {}
    since_peace = None

    def win(key, r, by, detail):
        if key not in earned:
            earned[key] = {"at": r.get("ts", ""), "seq": r.get("seq", 0), "by": by, "detail": detail}

    for r in records(board):
        kind, task = r.get("kind"), r.get("taskId", "") or ""
        sender = (r.get("from") or {}).get("agent", "")
        data = data_of(r)
        when = clock.parse(r.get("ts", ""))
        if since_peace is None and when:
            since_peace = when
        if kind == "status" and data.get("a2a_state") == "working" and "fence" in data:
            owners[task] = sender
            if when:
                claimed_at[task] = when
        if data.get("event") == "lock_violation":
            who = data.get("agent") or sender
            violations[who] = violations.get(who, 0) + 1
            since_peace = when or since_peace
        owner = owners.get(task) or cards.get(task, {}).get("owner", "")
        if kind == "status" and data.get("lane") == "done" and task:
            win("first-light", r, owner, task)
            if first_verify.get(task) is True:
                win("clean-run", r, owner, task)
            start = claimed_at.get(task)
            if when and start and when - start <= datetime.timedelta(minutes=SWIFT_MINUTES):
                win("swift-blade", r, owner, task)
            if when and when.astimezone().hour < 4:
                win("night-watch", r, owner, task)
            if when and since_peace and when - since_peace >= datetime.timedelta(days=PEACE_DAYS):
                win("peacekeeper", r, owner, f"{PEACE_DAYS} days without a lock violation")
            if owner:
                done_by[owner] = done_by.get(owner, 0) + 1
                if done_by[owner] >= CLEAN_CARDS and not violations.get(owner):
                    win("clean-hands", r, owner, f"{done_by[owner]} cards")
                if provider(owner):
                    providers_done.setdefault(provider(owner), owner)
                    if len(providers_done) >= 2:
                        win("many-voices", r, owner, ", ".join(sorted(providers_done)))
        if kind == "verify" and task and task not in first_verify:
            first_verify[task] = bool(data.get("passed"))
        if kind == "verify" and data.get("passed") and task in cards and receipts.valid(board, cards[task], data):
            win("first-seal", r, owner, task)
        if kind == "review" and data.get("verdict") in ("approve", "changes") and task:
            if owner and provider(sender) and provider(owner) and provider(sender) != provider(owner):
                win("second-opinion", r, sender, f"{provider(sender)} reviewed {provider(owner)} on {task}")
            if data["verdict"] == "changes":
                changed.add(task)
            elif task in changed:
                win("second-draft", r, owner, task)
        if kind == "status" and text_of(r).endswith("reopened"):
            changed.discard(task)
    clean = [n for n, d in done_by.items() if not violations.get(n)]
    best = max([done_by[n] for n in clean] + [0])
    out = []
    for key, name, desc, art in CATALOG:
        row = {"id": key, "name": name, "description": desc, "art": art, "earned": key in earned}
        row.update(earned.get(key, {"at": "", "seq": 0, "by": "", "detail": ""}))
        if key == "clean-hands":
            row["progress"] = {"value": min(best, CLEAN_CARDS), "goal": CLEAN_CARDS}
        elif key == "many-voices":
            row["progress"] = {"value": min(len(providers_done), 2), "goal": 2}
        out.append(row)
    titles = {}
    for key, title in TITLES.items():
        who = earned.get(key, {}).get("by")
        if who and who not in titles:
            titles[who] = title
    return {
        "titles": titles,
        "incomplete_history": history(board)[2],
        "last": history(board)[1],
        "achievements": out,
        "earned": sum(1 for a in out if a["earned"]),
        "total": len(out),
    }


def payload(board, query):
    return evaluate(board)


def _handle(ctx, args):
    result = evaluate(ctx.board)
    lines = [f"{result['earned']}/{result['total']} earned"]
    for a in result["achievements"]:
        mark = "x" if a["earned"] else " "
        extra = f" by {a['by']} ({a['detail']})" if a["earned"] and a["by"] else ""
        prog = a.get("progress")
        if not a["earned"] and prog:
            extra = f" ({prog['value']}/{prog['goal']})"
        lines.append(f"[{mark}] {a['name']}: {a['description']}{extra}")
    return result, "\n".join(lines)


def register_cli(add_command):
    add_command("achievements", "achievements earned from recorded board events", None, _handle)
