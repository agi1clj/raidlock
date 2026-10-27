"""Raid Lore: lessons captured from the transcript into an Obsidian-compatible vault and injected into briefs."""

from .approval import approve, propose, reject, retire
from .capture import capture
from .inject import lessons_for, mark_used
from .vault import Vault

__all__ = ["approve", "propose", "reject", "retire", "capture", "lessons_for", "mark_used", "Vault", "register_tokens"]


def _injected(board):
    if board is None:
        return {"tokens": 0, "detail": "no board"}
    cards = [c for c in board.cards() if c["lane"] in ("todo", "doing", "blocked")]
    sizes = sorted(lessons_for(board, c, 200)["tokens"] for c in cards)
    if not sizes:
        return {"tokens": 0, "detail": "no open cards"}
    return {"tokens": sizes[len(sizes) // 2], "detail": f"median lore injection over {len(cards)} open card(s)"}


def register_tokens(register):
    register("lore lessons", _injected)
