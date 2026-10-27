import html
import json
import os
import re
import tempfile
from pathlib import Path

from .core import Precondition
from .core.identity import require_lead
from .core.transcript import redact as redact_secrets

_EMAIL = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+", re.UNICODE)
_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|~[/\\]|\.{1,2}[/\\]|/)[^\s<>\"']+|\b[\w.-]+[/\\][^\s<>\"']+")


def render_snapshot(snapshot, redact=False):
    def text(value):
        value = redact_secrets(str(value if value is not None else ""))
        if redact:
            value = _EMAIL.sub("[redacted]", value)
            value = _PATH.sub("[redacted]", value)
        return html.escape(value, quote=True)

    sections = []
    cards = snapshot.get("cards", [])
    lanes = list(dict.fromkeys(["todo", "doing", "review", "blocked", "done"] + [c["lane"] for c in cards]))
    for lane in lanes:
        items = []
        for card in cards:
            if card["lane"] != lane:
                continue
            notes = ""
            if not redact:
                notes = "".join('<p class="note">' + text(n.get("text", "")) + "</p>" for n in card.get("notes", []))
            items.append(
                "<article><h3>"
                + text(card.get("id"))
                + ": "
                + text(card.get("title"))
                + "</h3><p>Owner: "
                + text(card.get("owner") or "Unassigned")
                + "</p><p>Team: "
                + text(card.get("team"))
                + "</p>"
                + notes
                + "</article>"
            )
        sections.append(
            '<section class="lane"><h2>'
            + text(lane.title())
            + "</h2>"
            + ("".join(items) or "<p>No cards</p>")
            + "</section>"
        )
    agents = "".join(
        "<tr><td>"
        + text(name)
        + "</td><td>"
        + text(agent.get("label", agent.get("status", "")))
        + "</td><td>"
        + text(agent.get("model", ""))
        + "</td><td>"
        + text(agent.get("card", ""))
        + "</td></tr>"
        for name, agent in snapshot.get("agents", {}).items()
    )
    spend = snapshot.get("costs", {}).get("workspace", {})
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Raidlock board snapshot</title><style>"
        "body{font:16px system-ui;margin:2rem;background:#f5f6fa;color:#172033}"
        ".lanes{display:flex;gap:1rem;flex-wrap:wrap}.lane{flex:1;min-width:220px}"
        "article{background:white;border:1px solid #ccd2df;border-radius:8px;padding:1rem;margin:1rem 0}"
        "h3,.note,td,pre{overflow-wrap:anywhere;white-space:pre-wrap}table{border-collapse:collapse}"
        "th,td{text-align:left;padding:.6rem;border-bottom:1px solid #ccd2df}"
        "</style></head><body><header><h1>"
        + text(snapshot.get("workspace", "Board"))
        + "</h1><p>Board snapshot · "
        + text(snapshot.get("now", ""))
        + (" · Redacted" if redact else "")
        + '</p></header><h2>Cards</h2><div class="lanes">'
        + "".join(sections)
        + "</div><section><h2>Agents</h2><table><thead><tr>"
        "<th>Agent</th><th>Status</th><th>Model</th><th>Card</th></tr></thead><tbody>"
        + agents
        + "</tbody></table></section><section><h2>Spend</h2><pre>"
        + text(json.dumps(spend, ensure_ascii=False, indent=2))
        + "</pre></section></body></html>"
    )


def _setup(parser):
    parser.add_argument("--out", required=True, help="Destination HTML file; inside RAIDLOCK_HOME use exports/")
    parser.add_argument("--redact", action="store_true", help="Remove notes, paths and email addresses")


def _inside(root, target, ignore_case=False):
    try:
        if ignore_case:
            root, target = root.casefold(), target.casefold()
        return os.path.commonpath([root, target]) == root
    except ValueError:
        return False


def _resolve_out(ctx, raw):
    target = os.path.realpath(os.path.expanduser(raw))
    board = ctx.board
    parts = {part.casefold() for part in Path(target).parts}
    if ".board" in parts or ".git" in parts or _inside(os.path.realpath(board.dir), target, ignore_case=True):
        raise Precondition("snapshot output cannot overwrite board or Git metadata")
    if _inside(os.path.realpath(os.path.join(board.home, "tokens")), target, ignore_case=True):
        raise Precondition("snapshot output cannot overwrite agent credentials")
    home = os.path.realpath(board.home)
    if _inside(home, target, ignore_case=True) and not _inside(os.path.join(home, "exports"), target):
        raise Precondition("snapshot output inside RAIDLOCK_HOME must be under exports/")
    roots = [os.path.realpath(r) for r in (board.home, board.workspace) if r]
    if not any(_inside(root, target) for root in roots):
        require_lead(ctx.caller(), "write a snapshot outside RAIDLOCK_HOME and the workspace")
    caller = ctx.caller()
    if not caller.is_lead:
        decision = board.check_write(target, caller)
        if not decision["allowed"] or not decision.get("card"):
            raise Precondition("snapshot output requires a current lease covering the resolved path")
    return Path(target)


def _handle(ctx, args):
    output = _resolve_out(ctx, args.out)
    content = render_snapshot(ctx.board.snapshot(), redact=args.redact)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, prefix="." + output.name + ".", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    except OSError as exc:
        raise Precondition(f"cannot write snapshot: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"out": str(output), "redacted": args.redact}, f"Snapshot written to {output}"


def register_cli(add_command):
    add_command("snapshot", "export a self-contained HTML board", _setup, _handle)
