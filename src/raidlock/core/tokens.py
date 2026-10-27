import importlib
import math

CONTRIBUTORS = {}
EXTENSION_MODULES = ("raidlock.hooks", "raidlock.mcp", "raidlock.web", "raidlock.runners", "raidlock.plugin")
SAMPLE_OUTPUTS = (
    "[ws] Claimed fix-login: lock app/auth until 14:05, fence 7.",
    'export RAIDLOCK_FENCE=7  # next: raidlock note fix-login "..."',
    "[ws] Noted on fix-login.",
    "[ws] Renewed fix-login until 14:50.",
    "[ws] Moved fix-login to review. Next: the lead runs raidlock done fix-login",
    "No messages.",
)


def estimate(text):
    """Token estimate: ceil(characters / 4)."""
    return math.ceil(len(text or "") / 4)


def register(name, fn):
    """Register an overhead contributor. `fn(board)` returns {"tokens": int, "detail": str} or a text to estimate.
    Edge components (skill, hooks, MCP schemas, UI) call this from `register_tokens(register)`."""
    CONTRIBUTORS[name] = fn


def _load_extensions():
    for module in EXTENSION_MODULES:
        try:
            mod = importlib.import_module(module)
        except ImportError:
            continue
        if hasattr(mod, "register_tokens"):
            mod.register_tokens(register)
    try:
        from importlib.metadata import entry_points

        eps = entry_points()
        group = eps.select(group="raidlock.tokens") if hasattr(eps, "select") else eps.get("raidlock.tokens", [])
        for ep in group:
            ep.load()(register)
    except Exception:
        return


def _core_outputs(board):
    text = "\n".join(SAMPLE_OUTPUTS)
    return {"tokens": estimate(text), "detail": f"{len(SAMPLE_OUTPUTS)} typical command outputs per session"}


def _core_brief(board):
    if board is None:
        return {"tokens": 0, "detail": "no board"}
    cards = [c for c in board.cards() if c["lane"] in ("todo", "doing", "blocked")]
    if not cards:
        return {"tokens": 0, "detail": "no open cards"}
    sizes = sorted(board.brief(c["id"])["tokens"] for c in cards)
    return {"tokens": sizes[len(sizes) // 2], "detail": f"median card brief over {len(cards)} open card(s)"}


register("cli outputs", _core_outputs)
register("card brief", _core_brief)


def report(board=None):
    """Estimated per-session token overhead of raidlock, by contributor."""
    _load_extensions()
    items = []
    for name, fn in sorted(CONTRIBUTORS.items()):
        try:
            value = fn(board)
        except Exception as e:
            value = {"tokens": 0, "detail": f"error: {e}"}
        if isinstance(value, str):
            value = {"tokens": estimate(value), "detail": f"{len(value)} chars"}
        items.append({"name": name, "tokens": int(value.get("tokens", 0)), "detail": value.get("detail", "")})
    return {"items": items, "total": sum(i["tokens"] for i in items), "method": "ceil(chars / 4)"}
