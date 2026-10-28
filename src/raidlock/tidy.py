"""Cards that wait on the lead: in review, blocked, gone quiet in doing, or untouched in todo for weeks."""

import contextlib
import time

from .core import clock
from .core import status as S
from .core.errors import RaidlockError
from .core.hints import lead_cmd

IDLE_DAYS = 14
REAP_SECONDS = 300


def decisions(board):
    leases = board.leases()
    now = clock.now()
    out = []
    for c in board.cards():
        cid, lane = c["id"], c["lane"]
        close = lead_cmd(board, f"close {cid} --evidence <PR URL or SHA> --note <text>")
        release = lead_cmd(board, f"release {cid}")
        cancel = lead_cmd(board, f"cancel {cid} --reason <text>")
        if lane == "review":
            reason, commands = "waiting in review", [lead_cmd(board, f"done {cid}"), close]
        elif lane == "blocked":
            reason, commands = "blocked", [f"raidlock claim {cid}", release, cancel, close]
        elif lane == "doing" and S._doing_status(leases.get(cid), now) == "stalled":
            reason, commands = f"{c['owner'] or 'its owner'} went quiet", [release, close, cancel]
        elif lane == "todo" and (clock.age_seconds(c.get("updated", ""), now) or 0) > IDLE_DAYS * 86400:
            reason, commands = f"untouched in todo for over {IDLE_DAYS} days", [cancel]
        else:
            continue
        out.append({"card": cid, "lane": lane, "title": c["title"], "reason": reason, "commands": commands})
    return out


def reaper(catalog, every=REAP_SECONDS):
    """While `raid ui` runs, move cards whose doing lease expired to blocked, as `raid reap` does."""
    while True:
        for ws in catalog.workspaces():
            with contextlib.suppress(RaidlockError, OSError):
                ws.board().reap()
        time.sleep(every)
