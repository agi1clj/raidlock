"""Approval inbox: one list of everything waiting for a human, each item with copy-ready commands.

Items are derived from board state on every read, so an item resolves itself when the state that raised it changes:
a card that passes its done gate is ready to close, a done card whose runner patch still applies is a pending
apply, a review card short of approvals or a receipt is a pending review, and blocked cards, undecided duels,
stuck resurrections and pending lessons are lead decisions. Reading the inbox never changes the board."""

import contextvars
import os
import re

from . import duel
from .core.errors import Precondition, RaidlockError
from .core.hints import lead_cmd, lead_secret_src, quote_path, short_path
from .runners import policy
from .runners import workspace as W
from .runners.runner import latest_patch

KINDS = ("close", "apply", "review", "duel", "decision", "lore")
APPLY_CHECKS = 10
TEXT_CAP = 300


_short = short_path
_q = quote_path


WEB_LEAD_SRC = '"$LEAD_SECRET_FILE"'
_LEAD_SRC = contextvars.ContextVar("lead_secret_src", default=None)


def web_lead_src(board):
    """What the web board shows after --lead-secret: `keychain` or the real secret file path when it is under the
    home folder (shown as ~/...), otherwise the LEAD_SECRET_FILE shell variable the getting-started guide sets up."""
    src = lead_secret_src(board)
    return src if src == "keychain" or src.startswith("~/") else WEB_LEAD_SRC


def _lead(board, rest):
    secret = _LEAD_SRC.get()
    return f"raid --lead-secret {secret} {rest}" if secret else lead_cmd(board, rest, prog="raid")


def _clip(text):
    text = " ".join(str(text or "").split())
    return text if len(text) <= TEXT_CAP else text[: TEXT_CAP - 3] + "..."


def _item(kind, card, reason, commands, **extra):
    cid = card["id"] if card else extra.pop("ref", "")
    return {
        "id": f"{kind}:{cid}",
        "kind": kind,
        "card": cid if card else "",
        "title": _clip(card.get("title", "")) if card else _clip(extra.pop("title", "")),
        "reason": _clip(reason),
        "since": (card or {}).get("updated", "") or extra.pop("since", ""),
        "commands": commands,
        **extra,
    }


def reviewer_runtime(board, card):
    from .core import identity as I

    agents = board.agents()
    owner = agents.get(card.get("owner") or "", {})
    taken = owner.get("provider") or I.RUNTIME_PROVIDER.get(owner.get("runtime", ""), "")
    for runtime in ("codex-cli", "claude-code", "gemini-cli"):
        if I.RUNTIME_PROVIDER[runtime] != taken:
            return I.RUNTIME_SHORT[runtime]
    return "codex"


def _review_items(board, card):
    cid = card["id"]
    try:
        board._done_gate(card)
    except Precondition as e:
        commands = []
        if "verification" in e.message or "verify" in e.message:
            if card.get("verify_cmd"):
                commands.append(_lead(board, f"verify {_q(cid)}"))
            else:
                commands.append(_lead(board, f'edit {_q(cid)} --verify-cmd "<check>"'))
        if "review" in e.message:
            commands += [
                _lead(board, f"delegate {_q(cid)} --to {reviewer_runtime(board, card)} --as reviewer"),
                f"raid review {_q(cid)} --verdict approve   # as a reviewer agent",
            ]
        commands.append(f"raid show {_q(cid)}")
        return [_item("review", card, e.message, commands)]
    except RaidlockError as e:
        return [_item("review", card, e.message, [f"raid show {_q(cid)}"])]
    reason = "passes the done gate"
    approved = board.approvals(card)
    if approved:
        reason += "; approved by " + ", ".join(approved)
    return [_item("close", card, reason, [_lead(board, f"done {_q(cid)}")])]


def _apply_item(board, card):
    folder, _ = latest_patch(board, card["id"])
    if not folder:
        return None
    patch = os.path.join(folder, "changes.patch")
    try:
        target = policy.target(board.workspace, board.dir, card["locks"])
        W.git(target["path"], "apply", "--check", patch)
    except (RaidlockError, OSError):
        return None
    repo = os.path.relpath(target["path"], board.workspace)
    return _item(
        "apply",
        card,
        f"done; its runner patch is not in {repo} yet",
        [f"git -C {_q(repo)} apply --index {_q(patch)}", f"git -C {_q(repo)} diff --cached --stat"],
        patch=_short(patch),
    )


def _blocked_item(board, card):
    cid = card["id"]
    last = next((n["text"] for n in reversed(card.get("notes", []))), "")
    commands = [
        f"raid show {_q(cid)}",
        _lead(board, f"resume {_q(cid)}"),
        _lead(board, f"resurrect {_q(cid)} --dry-run"),
    ]
    if card.get("owner"):
        commands.append(f"raid claim {_q(cid)} --take-over   # as another worker")
    last = last[len("-> blocked") :].strip(" ()") if last.startswith("-> blocked") else last
    return _item("decision", card, f"blocked: {last}" if last else "blocked", commands)


def _duel_item(board, card):
    record = duel.load(board, card["id"])
    if not record or record.get("status") != "decide":
        return None
    verified = [s for s in record.get("sides", []) if duel.passing(board, card["id"], s)]
    commands = [_lead(board, f"duel {_q(card['id'])}")]
    commands += [_lead(board, f"duel {_q(card['id'])} --winner {_q(s['label'])}") for s in verified]
    reason = (
        "duel finished; verified: " + ", ".join(f"{s['label']} ({s['runtime']})" for s in verified)
        if verified
        else "duel finished; no contestant has a passing signed receipt"
    )
    return _item("duel", card, reason, commands)


def _lore_items(board):
    try:
        from .lore import Vault

        rows = Vault.for_board(board).pending()
    except (ImportError, RaidlockError, OSError):
        return []
    return [
        _item(
            "lore",
            None,
            "lesson waiting for approval",
            [_lead(board, f"lore approve {_q(x['id'])}"), _lead(board, f"lore reject {_q(x['id'])}")],
            ref=x["id"],
            title=x.get("text", ""),
            since=x.get("created", ""),
        )
        for x in rows
        if isinstance(x.get("id"), str) and re.fullmatch(r"[a-f0-9]{12}", x["id"])
    ]


def collect(board, check_apply=True, secret=None):
    """All pending items, most actionable first. Read-only. Lead commands carry the lead secret file's path, or
    `secret` (the web board passes web_lead_src)."""
    token = _LEAD_SRC.set(secret)
    try:
        return _collect(board, check_apply)
    finally:
        _LEAD_SRC.reset(token)


def _collect(board, check_apply):
    cards = board.cards()
    items = []
    duels = {}
    for card in cards:
        try:
            found = _duel_item(board, card)
        except RaidlockError:
            found = None
        if found:
            duels[card["id"]] = found
    for card in cards:
        if card["lane"] == "review":
            items += _review_items(board, card)
        elif card["lane"] == "blocked" and card["id"] not in duels:
            items.append(_blocked_item(board, card))
    if check_apply:
        done = sorted(
            (c for c in cards if c["lane"] == "done" and not c.get("evidence")),
            key=lambda c: c.get("updated", ""),
            reverse=True,
        )
        for card in done[:APPLY_CHECKS]:
            found = _apply_item(board, card)
            if found:
                items.append(found)
    items += list(duels.values())
    items += _lore_items(board)
    order = {k: i for i, k in enumerate(KINDS)}
    items.sort(key=lambda x: (order[x["kind"]], x["since"], x["id"]))
    counts = {k: sum(1 for x in items if x["kind"] == k) for k in KINDS}
    return {"workspace": board.name, "items": items, "counts": counts, "total": len(items)}


def render(result):
    if not result["items"]:
        return "Nothing needs you right now."
    urgent = sum(x["kind"] != "lore" for x in result["items"])
    lines = [f"{urgent} item(s) need you in {result['workspace']}:"]
    lore_heading = False
    for x in result["items"]:
        if x["kind"] == "lore" and not lore_heading:
            lines.append("Lore inbox (low priority):")
            lore_heading = True
        label = f"{x['card']}: {x['title']}" if x["card"] else x["title"]
        lines.append(f"[{x['kind']}] {label}")
        lines.append(f"  {x['reason']}")
        lines += [f"  $ {c}" for c in x["commands"]]
    lines.append("Commands with --lead-secret run only in the lead session; never give the secret to an agent.")
    return "\n".join(lines)


def _setup(p):
    p.add_argument("--kind", default=None, choices=KINDS, help="only this kind of item")
    p.add_argument("--no-apply-check", action="store_true", help="skip the git apply checks for done cards")


def _handler(ctx, a):
    r = collect(ctx.board, check_apply=not a.no_apply_check)
    if a.kind:
        r = {**r, "items": [x for x in r["items"] if x["kind"] == a.kind]}
        r["total"] = len(r["items"])
    return r, render(r)


def register_cli(add_command):
    add_command("approvals", "everything waiting for a human, with copy-ready commands", _setup, _handler)
