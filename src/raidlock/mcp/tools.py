import os

from ..core import Board, repos
from ..core.errors import IdentityError, Precondition
from ..core.transcript import _clean

ID = {"type": "string"}
FENCE = {"type": "integer"}


def _schema(required, **props):
    return {"type": "object", "properties": props, "required": list(required)}


TOOLS = [
    {
        "name": "claimable",
        "description": "Cards you can claim now, and why others are blocked.",
        "inputSchema": _schema(()),
    },
    {
        "name": "claim",
        "description": "Take a card: lock its paths. Returns the fence.",
        "inputSchema": _schema(("id",), id=ID),
    },
    {
        "name": "heartbeat",
        "description": "Renew your lease on a card.",
        "inputSchema": _schema(("id",), id=ID, fence=FENCE),
    },
    {
        "name": "note",
        "description": "Add a one-line progress note; renews the lease.",
        "inputSchema": _schema(("id", "text"), id=ID, text={"type": "string"}, fence=FENCE),
    },
    {
        "name": "lock",
        "description": "Add lock paths (<repo>/<path>, comma-separated) to your card.",
        "inputSchema": _schema(("id", "paths"), id=ID, paths={"type": "string"}, fence=FENCE),
    },
    {
        "name": "move",
        "description": "Move your card to review or blocked. Only the lead closes cards.",
        "inputSchema": _schema(
            ("id", "lane"), id=ID, lane={"enum": ["review", "blocked"]}, note={"type": "string"}, fence=FENCE
        ),
    },
    {
        "name": "msg",
        "description": "Message an agent; card ties it to a task.",
        "inputSchema": _schema(("to", "text"), to={"type": "string"}, text={"type": "string"}, card=ID),
    },
    {"name": "inbox", "description": "Read your new messages.", "inputSchema": _schema(())},
]


def _annotations(name):
    if name == "claimable":
        return {"readOnlyHint": True, "openWorldHint": False}
    return {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": name == "heartbeat",
        "openWorldHint": False,
    }


for tool in TOOLS:
    tool["annotations"] = _annotations(tool["name"])
SPECS = {t["name"]: t["inputSchema"] for t in TOOLS}
LEAD_REFUSAL = "the MCP server never acts as the lead; the lead uses the raidlock CLI"


class Session:
    def __init__(self, board_path=None, env=None):
        base = dict(os.environ if env is None else env)
        base.pop("RAIDLOCK_ROLE", None)
        self.env = base
        self.board_path = board_path
        self.fences = {}

    def board(self):
        return Board.open(self.board_path, env=self.env)

    def caller(self, board, card=None, fence=None):
        if fence is None and card:
            fence = self.fences.get(card)
        c = board.caller(fence=fence, env=self.env)
        if c.is_lead or (c.name and c.name == board.lead):
            raise IdentityError(LEAD_REFUSAL, agent=c.name, hint="set RAIDLOCK_AGENT and RAIDLOCK_TOKEN to a worker")
        return c

    def call(self, name, args):
        check_args(name, args)
        board = self.board()
        return _clean(getattr(self, "t_" + name)(board, args))

    def t_claimable(self, b, a):
        c = self.caller(b)
        ready, waiting = [], []
        for cid, v in sorted(b.claimable(agent=c.name or None).items()):
            if v["ok"]:
                ready.append({"id": cid, "title": v["title"], **({"takeover": v["takeover"]} if v["takeover"] else {})})
            else:
                waiting.append({"id": cid, "why": "; ".join(v["reasons"])})
        return {"ready": ready, "blocked": waiting}

    def t_claim(self, b, a):
        r = b.claim(a["id"], self.caller(b), checkout=os.getcwd() if repos.find_root(os.getcwd()) else None)
        self.fences[a["id"]] = r["fence"]
        return {"card": r["card"], "fence": r["fence"], "expires": r["expires"], "paths": r["paths"]}

    def t_heartbeat(self, b, a):
        r = b.heartbeat(a["id"], self.caller(b, a["id"], a.get("fence")))
        return {"card": r["card"], "expires": r["expires"], **({"resumed": True} if r.get("resumed") else {})}

    def t_note(self, b, a):
        b.note(a["id"], self.caller(b, a["id"], a.get("fence")), a["text"])
        return {"card": a["id"], "noted": True}

    def t_lock(self, b, a):
        r = b.lock_paths(a["id"], self.caller(b, a["id"], a.get("fence")), a["paths"])
        return {"card": r["card"], "locks": r["locks"]}

    def t_move(self, b, a):
        if a["lane"] not in ("review", "blocked"):
            raise Precondition("move takes review or blocked", lane=a["lane"])
        r = b.move(a["id"], a["lane"], self.caller(b, a["id"], a.get("fence")), note=a.get("note"))
        return {"card": r["card"], "lane": r["lane"]}

    def t_msg(self, b, a):
        m = b.send(self.caller(b), a["to"], a["text"], task=a.get("card") or None)
        out = {"to": a["to"], "status": m.get("status", "")}
        if m.get("dropped"):
            out["dropped"] = True
        return out

    def t_inbox(self, b, a):
        r = b.inbox(self.caller(b))
        msgs = [
            {
                k: v
                for k, v in (
                    ("from", m.get("from")),
                    ("card", m.get("task")),
                    ("sent", m.get("sent")),
                    ("text", m.get("text")),
                )
                if v
            }
            for m in r["messages"]
        ]
        out = {"messages": msgs}
        if r.get("queued"):
            out["queued"] = r["queued"]
        return out


def check_args(name, args):
    spec = SPECS[name]
    if not isinstance(args, dict):
        raise Precondition("arguments must be an object")
    extra = sorted(set(args) - set(spec["properties"]))
    if extra:
        raise Precondition(f"unknown argument(s): {', '.join(extra)}", tool=name)
    for key in spec["required"]:
        if key not in args:
            raise Precondition(f"missing argument: {key}", tool=name)
    for key, value in args.items():
        prop = spec["properties"][key]
        if "enum" in prop:
            if value not in prop["enum"]:
                raise Precondition(f"{key} must be one of {', '.join(prop['enum'])}", tool=name)
        elif prop["type"] == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise Precondition(f"{key} must be an integer", tool=name)
        elif not isinstance(value, str) or not value.strip():
            raise Precondition(f"{key} must be a non-empty string", tool=name)
