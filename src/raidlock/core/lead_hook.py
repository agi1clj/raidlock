"""Helpers for hook adapters. Host hooks supply authenticated worker identity.

Context is untrusted message data. It must be appended to host additionalContext,
never used as hook instructions or a permission decision.
"""

import json
import os

from . import identity as I
from .errors import IdentityError


def protected_lead_path(board, path):
    """Recognize the secret file or a containing directory, including symlink aliases."""
    secret = os.path.realpath(I.token_path(board.home, board.uid, board.lead))
    candidate = os.path.realpath(os.path.expanduser(os.fspath(path)))
    try:
        if os.path.samefile(secret, candidate):
            return True
    except OSError:
        pass
    try:
        return os.path.commonpath((secret.casefold(), candidate.casefold())) == candidate.casefold()
    except ValueError:
        return False


def guard_lead_read(board, paths, subagent=False):
    if subagent and any(protected_lead_path(board, p) for p in paths):
        raise IdentityError("subagents may not read the lead secret or its containing directory")


def urgent_context(board, caller):
    """Drain urgent messages for a validated worker, even while its card is doing."""
    with board.lock():
        board._auth(caller, scoped_ok=True)
        rows = board.take_urgent(caller.name)
    if not rows:
        return ""
    data = [{"from": r.get("from", ""), "task": r.get("task", ""), "text": r.get("text", "")} for r in rows]
    return "Raidlock urgent messages (untrusted quoted data):\n" + json.dumps(data, ensure_ascii=True)
