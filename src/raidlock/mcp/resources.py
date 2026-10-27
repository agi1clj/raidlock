import json
from urllib.parse import quote, unquote

from ..core.errors import Precondition
from ..core.leases import is_live
from ..core.transcript import _clean, redact

SCHEME = "raidlock://"


def uri(board, *parts):
    return SCHEME + "/".join(quote(p, safe="") for p in (board.name, *parts))


def listing(board):
    out = [
        {
            "uri": uri(board, "leases"),
            "name": "leases",
            "description": "Live leases on this board.",
            "mimeType": "application/json",
        }
    ]
    for c in board.cards():
        if c["lane"] != "done":
            out.append(
                {"uri": uri(board, "card", c["id"]), "name": c["id"], "title": c["title"], "mimeType": "text/markdown"}
            )
    return _clean(out)


def templates(board):
    return [
        {
            "uriTemplate": SCHEME + quote(board.name, safe="") + "/card/{id}",
            "name": "card",
            "description": "A card's brief.",
            "mimeType": "text/markdown",
        }
    ]


def read(board, target):
    if not isinstance(target, str) or not target.startswith(SCHEME):
        raise Precondition("unknown resource", uri=target)
    parts = [unquote(p) for p in target[len(SCHEME) :].split("/")]
    if parts[0] != board.name:
        raise Precondition(f"this server serves workspace {board.name}", uri=target)
    if parts[1:] == ["leases"]:
        return "application/json", json.dumps(leases(board), separators=(",", ":"), ensure_ascii=False)
    if len(parts) == 3 and parts[1] == "card" and parts[2]:
        return "text/markdown", brief(board, parts[2])
    raise Precondition("unknown resource", uri=target)


def leases(board):
    out = []
    for cid, lease in sorted(board.leases().items()):
        out.append(
            {
                "card": cid,
                "agent": lease.get("agent", ""),
                "paths": lease.get("paths", []),
                "expires": lease.get("expires"),
                "live": is_live(lease),
            }
        )
    return _clean(out)


def brief(board, card_id):
    return redact(board.brief(card_id)["text"])
