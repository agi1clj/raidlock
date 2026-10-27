from ..core.errors import Precondition
from . import distill
from .approval import approve, propose, reject, retire
from .capture import capture
from .inject import candidates, card_locks, lessons_for, overlap
from .vault import Vault

ACTIONS = ("list", "show", "search", "distill", "capture", "for", "pending", "propose", "approve", "reject", "retire")


def _setup(p):
    p.add_argument(
        "action",
        choices=ACTIONS,
        help="list | show ID | search TEXT | distill | capture | for CARD | "
        "pending | propose TEXT | approve ID... | reject ID... | retire ID...",
    )
    p.add_argument("arg", nargs="*", default=None, help="lesson id(s), search text, lesson text or card id")
    p.add_argument("--paths", default=None, help="lock paths a proposed lesson applies to (default: the card's)")
    p.add_argument("--card", default=None, help="only lessons whose paths overlap this card's locks")
    p.add_argument("--local", action="store_true", help="this workspace's vault only, not $RAIDLOCK_HOME/lore")
    p.add_argument("--budget", type=int, default=200, help="token budget for `for`")
    p.add_argument("-n", type=int, default=20, help="maximum lessons to list")


def _row(x):
    return f"{x['id']}  {x.get('created', '')[:10]}  {x.get('source', ''):<6} {x.get('card') or '-':<14} {x['text']}"


def _filter(board, lessons, card_id):
    if not card_id:
        return lessons
    locks = card_locks(board.card(card_id))
    return [x for x in lessons if overlap(x, locks) > 0]


def _sorted(lessons):
    return sorted(lessons, key=lambda x: (x.get("created", ""), x["id"]), reverse=True)


def _handler(ctx, args):
    board = ctx.board
    words = list(args.arg or [])
    args.arg = " ".join(words) or None
    got = capture(board)
    if args.action == "pending":
        rows = _sorted(Vault.for_board(board).pending())[: args.n]
        return (
            {"lessons": rows, "count": len(rows)},
            "\n".join(_row(x) for x in rows) + "\nNext: raidlock lore approve <id>" if rows else "No pending lessons.",
        )
    if args.action == "propose":
        x = propose(board, ctx.caller(), args.arg or "", args.card or "", (args.paths or "").replace(",", " ").split())
        return {
            "lesson": x
        }, f"Proposed lesson {x['id']} (pending). Next: the lead runs raidlock lore approve {x['id']}"
    if args.action in ("approve", "reject", "retire"):
        if not words:
            raise Precondition(f"lore {args.action} needs lesson ids", hint="raidlock lore pending")
        ids = {"approve": approve, "reject": reject, "retire": retire}[args.action](board, ctx.caller(), words)
        verb = {"approve": "approved", "reject": "rejected", "retire": "retired"}[args.action]
        return {verb: ids}, f"{verb.capitalize()} {len(ids)} lesson(s): {', '.join(ids)}"
    lessons = _sorted(candidates(board, include_global=not args.local))
    if args.action == "capture":
        return got, f"Captured {got['captured']} lesson(s) into {Vault.for_board(board).root}"
    if args.action == "list":
        rows = _filter(board, lessons, args.card)[: args.n]
        return ({"lessons": rows, "count": len(rows)}, "\n".join(_row(x) for x in rows) if rows else "No lessons yet.")
    if args.action == "show":
        if not args.arg:
            raise Precondition("lore show needs a lesson id", hint="raidlock lore list")
        match = [x for x in lessons + Vault.for_board(board).pending() if x["id"].startswith(args.arg)]
        if len(match) != 1:
            raise Precondition(f"{len(match)} lessons match {args.arg}", hint="raidlock lore list")
        x = match[0]
        lines = [
            _row(x),
            f"paths: {', '.join(x.get('paths') or []) or '-'}",
            f"agent: {x.get('agent') or '-'} ({x.get('provider') or '-'} {x.get('model') or ''})".rstrip(),
            f"status: {x.get('status') or '-'}  hits: {x.get('hits', 0)}  last used: {x.get('last_used') or '-'}",
        ]
        return {"lesson": x}, "\n".join(lines)
    if args.action == "search":
        q = (args.arg or "").lower()
        if not q:
            raise Precondition("lore search needs text", hint='raidlock lore search "verify"')
        rows = [
            x
            for x in _filter(board, lessons, args.card)
            if q in " ".join([x["text"], x.get("card", ""), " ".join(x.get("paths") or [])]).lower()
        ][: args.n]
        return {"lessons": rows, "count": len(rows)}, "\n".join(_row(x) for x in rows) if rows else "No match."
    if args.action == "distill":
        text = distill.prompt(_filter(board, lessons, args.card)[: args.n])
        return {"prompt": text}, text.rstrip("\n")
    card_id = args.arg or args.card
    if not card_id:
        raise Precondition("lore for needs a card id", hint="raidlock lore for <card>")
    r = lessons_for(board, card_id, args.budget, include_global=not args.local)
    return r, (r["text"].rstrip("\n") + f"\n({r['tokens']} tokens)") if r["lessons"] else "No lessons for this card."


def register_cli(add_command):
    add_command(
        "lore",
        "lessons vault: list, show, search, pending, propose, approve, reject, retire, distill, capture, for CARD",
        _setup,
        _handler,
    )
