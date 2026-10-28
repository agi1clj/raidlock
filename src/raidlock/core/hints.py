"""Copy-ready command lines for hints, the approvals inbox and the web board."""

import os
import shlex
import sys

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


PROGS = ("raid", "raidlock")


def prog_name(argv0=None):
    """The name the CLI was invoked with (`raid` or `raidlock`, the same command); `raidlock` otherwise."""
    base = os.path.basename(argv0 if argv0 is not None else (sys.argv[0] if sys.argv else ""))
    base = base[:-4] if base.lower().endswith(".exe") else base
    return base if base in PROGS else "raidlock"


def lead_cmd(board, rest, prog=None):
    """A lead command line that works when pasted into the lead session."""
    return f"{prog or prog_name()} --lead-secret {lead_secret_src(board)} {rest}"
