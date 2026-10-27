"""Copy-ready command lines for hints, the approvals inbox and the web board."""

import os
import shlex

from .identity import token_path


def short_path(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home) :] if path and path.startswith(home + os.sep) else path


def quote_path(path):
    text = short_path(str(path))
    if text.startswith("~/"):
        return "~/" + shlex.quote(text[2:])
    return shlex.quote(text)


def lead_secret_src(board):
    """What the lead passes to --lead-secret: `keychain`, or the shell-quoted secret file path."""
    try:
        where = board.leadlease().store.using
    except Exception:
        where = "file"
    return "keychain" if where == "keychain" else quote_path(token_path(board.home, board.uid, board.lead))


def lead_cmd(board, rest, prog="raidlock"):
    """A lead command line that works when pasted into the lead session."""
    return f"{prog} --lead-secret {lead_secret_src(board)} {rest}"
