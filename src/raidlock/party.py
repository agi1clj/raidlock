"""Raid-theme party roles, boss board and loot, derived from recorded card activity on every read. Display only:
nothing is stored and nothing is granted. Item names are original to raidlock.

Tank counts leases held now on blocked cards plus claimed cards that were blocked or that other cards depend on. Healer
counts reviews and passing verify runs on other agents' cards plus moving someone else's card out of blocked. DPS counts
finished cards the agent owns and leases held now on cards that are not blocked. The highest score wins; ties go to DPS,
then Tank, then Healer. Agents with no activity get no role. Cancelled cards never count.

A boss is a card with BOSS_DEPENDENTS or more transitive dependents, or one the lead marked with edit --boss on. Its HP
is the share of the boss and its dependents still open; it is slain when all of them are done. Every done card drops one
item for its owner; the rarity comes from dependents, changes-requested reviews, a first-try verify pass and recorded
run time, and the name from a hash of the card id, so the same card always drops the same item.
"""

import datetime
import hashlib

from .core import clock
from .web.replay import data_of, history

ROLES = ("dps", "tank", "healer")
BOSS_DEPENDENTS = 3
RARITIES = ("Common", "Uncommon", "Rare", "Epic", "Legendary")
ADJECTIVES = (
    ("Worn", "Patched", "Sturdy", "Plain"),
    ("Polished", "Tempered", "Keen", "Lucky"),
    ("Runed", "Moonlit", "Gilded", "Whispering"),
    ("Stormbound", "Emberforged", "Starlit", "Thornwoven"),
    ("Sunforged", "Everflame", "Worldroot", "Firstlight"),
)
NOUNS = (
    "Lantern",
    "Whetstone",
    "Cloak",
    "Compass",
    "Quill",
    "Buckler",
    "Satchel",
    "Ring",
    "Spyglass",
    "Hammer",
    "Amulet",
    "Boots",
)


def live(cards):
    return [c for c in cards if c.get("lane") != "cancelled"]


def dependents(cards):
    ids = {c["id"] for c in cards}
    back = {cid: set() for cid in ids}
    for c in cards:
        for dep in c.get("depends", []):
            if dep in back and dep != c["id"]:
                back[dep].add(c["id"])
    out = {}
    for cid in sorted(ids):
        seen, stack = set(), sorted(back[cid])
        while stack:
            nxt = stack.pop()
            if nxt == cid or nxt in seen:
                continue
            seen.add(nxt)
            stack.extend(sorted(back[nxt]))
        out[cid] = seen
    return out


def _claimers(card):
    out = set()
    for n in card.get("notes", []):
        words = str(n.get("text", "")).split()
        if len(words) >= 3 and words[:2] == ["claimed", "by"]:
            out.add(words[2])
    return out


def _unblockers(card):
    out, lane = [], ""
    for n in card.get("notes", []):
        text = str(n.get("text", ""))
        if not text.startswith("-> "):
            continue
        nxt = text[3:].split(" ", 1)[0]
        if lane == "blocked" and nxt != "blocked":
            out.append(n.get("by", ""))
        lane = nxt
    return out


def roles(board):
    cards = live(board.cards())
    names = sorted(board.agents())
    score = {n: dict.fromkeys(ROLES, 0) for n in names}
    deps = dependents(cards)

    def add(name, role):
        if name in score:
            score[name][role] += 1

    lanes = {c["id"]: c.get("lane") for c in cards}
    for lease in sorted(board.leases().values(), key=lambda x: x.get("card", "")):
        add(lease.get("agent", ""), "tank" if lanes.get(lease.get("card")) == "blocked" else "dps")
    for c in cards:
        owner = c.get("owner", "")
        hard = deps.get(c["id"]) or any(str(n.get("text", "")).startswith("-> blocked") for n in c.get("notes", []))
        if hard:
            for name in _claimers(c):
                add(name, "tank")
        if c.get("lane") == "done" and owner:
            add(owner, "dps")
        for r in c.get("reviews", []):
            if r.get("verdict") in ("approve", "changes") and r.get("by") != owner:
                add(r.get("by", ""), "healer")
        for v in c.get("verifications", []):
            if v.get("passed") and v.get("by") != owner:
                add(v.get("by", ""), "healer")
        for name in _unblockers(c):
            if name != owner:
                add(name, "healer")
    out = {}
    for name in names:
        s = score[name]
        best = max(ROLES, key=lambda r: (s[r], -ROLES.index(r)))
        out[name] = {"role": best if s[best] else "", **s}
    return out


def _done_at(board):
    out = {}
    for r in history(board)[0]:
        if r.get("kind") == "status" and data_of(r).get("lane") == "done" and r.get("taskId"):
            out[r["taskId"]] = r.get("ts", "")
    return out


def bosses(cards, deps, done_at):
    by_id = {c["id"]: c for c in cards}
    out = []
    for cid in sorted(by_id):
        under = deps.get(cid, set())
        if len(under) < BOSS_DEPENDENTS and not by_id[cid].get("boss"):
            continue
        group = [cid, *sorted(under)]
        still = [x for x in group if by_id[x].get("lane") != "done"]
        slain = not still
        out.append(
            {
                "id": cid,
                "title": by_id[cid].get("title", cid),
                "dependents": len(under),
                "total": len(group),
                "open": len(still),
                "hp": round(100 * len(still) / len(group)),
                "slain": slain,
                "slain_at": max(done_at.get(x) or by_id[x].get("updated", "") for x in group) if slain else "",
                "final": by_id[max(group, key=lambda x: done_at.get(x) or by_id[x].get("updated", ""))].get("owner", "")
                if slain
                else "",
            }
        )
    alive = sorted((b for b in out if not b["slain"]), key=lambda b: (-b["hp"], b["id"]))
    dead = sorted((b for b in out if b["slain"]), key=lambda b: (b["slain_at"], b["id"]), reverse=True)
    return alive + dead


def tier(card, dependents):
    changes = sum(1 for r in card.get("reviews", []) if r.get("verdict") == "changes")
    runs = card.get("verifications") or []
    first_pass = bool(runs) and bool(runs[0].get("passed"))
    hours = sum(int(r.get("duration_ms") or 0) for r in card.get("costs", [])) / 3600000.0
    score = min(dependents, 4) + min(changes, 2) + int(first_pass) + (2 if hours >= 4 else 1 if hours >= 1 else 0)
    return min(4, (score + 1) // 2)


def item_name(card_id, rank):
    digest = hashlib.sha256(f"{card_id}\n{rank}".encode()).digest()
    words = ADJECTIVES[rank]
    return f"{words[digest[0] % len(words)]} {NOUNS[digest[1] % len(NOUNS)]}"


def loot(cards, deps, done_at):
    bags = {}
    for c in cards:
        if c.get("lane") != "done" or not c.get("owner"):
            continue
        rank = tier(c, len(deps.get(c["id"], ())))
        bags.setdefault(c["owner"], []).append(
            {
                "card": c["id"],
                "title": c.get("title", c["id"]),
                "item": item_name(c["id"], rank),
                "rarity": RARITIES[rank],
                "tier": rank,
                "at": done_at.get(c["id"]) or c.get("updated", ""),
            }
        )
    out = []
    for name, items in sorted(bags.items()):
        out.append({"agent": name, "items": sorted(items, key=lambda i: (i["at"], i["card"]))})
    return out


def payload(board, query):
    cards = live(board.cards())
    deps = dependents(cards)
    done_at = _done_at(board)
    return {
        "bosses": bosses(cards, deps, done_at),
        "bags": loot(cards, deps, done_at),
        "boss_dependents": BOSS_DEPENDENTS,
    }


def slain_bosses(cards):
    cards = live(cards)
    return [
        {k: b[k] for k in ("id", "title", "total", "final", "slain_at")}
        for b in bosses(cards, dependents(cards), {})
        if b["slain"]
    ]


def streak(cards, today=None):
    days = set()
    for c in cards:
        when = clock.parse(c.get("updated", "")) if c.get("lane") == "done" else None
        if when:
            days.add(when.astimezone().date())
    day = today or datetime.datetime.now().astimezone().date()
    if day not in days:
        day -= datetime.timedelta(days=1)
    count = 0
    while day in days:
        count += 1
        day -= datetime.timedelta(days=1)
    return count
