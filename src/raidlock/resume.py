"""Resume packets: everything a fresh agent needs to continue a card a dead or expired worker left behind.

A packet holds the card brief, the latest notes, the work so far as a validated patch, the open questions of the
last handback and the transcript tail, all redacted and capped by a token budget. Work is read from the newest
runner clone (tracked and untracked files) or from the checkout recorded on the lease, hashed into a lead-owned
object store; the worker's .git is never trusted. A patch that leaves the card's locks, touches a denied path or
trips the secret scan is reported as rejected and never offered for reuse."""

import os
import shutil

from .core import clock, store
from .core import identity as I
from .core.brief import UNTRUSTED_RULE, quote_data
from .core.errors import Denied, Precondition, RaidlockError
from .core.tokens import estimate
from .core.transcript import redact
from .runners import policy
from .runners import workspace as W
from .runners.base import Redactor
from .runners.runner import run_numbers, runs_dir
from .scan import _parse, scan_patch

HANDOFF_PERCENT = 80
NOTES = 8
TRANSCRIPT = 20
PATHS_SHOWN = 50
DEFAULT_FACTOR = 3


def resume_dir(board, card_id):
    return os.path.join(board.home, "resume", board.name, card_id)


def _authorize(board, caller, card):
    if caller.is_lead:
        board._auth(caller)
        return "lead"
    if card.get("owner") and caller.name == card["owner"]:
        return board._owner_or_lead(card, caller)
    board._auth(caller, card["id"])
    raise Denied(f"only the lead or the owner of {card['id']} reads its resume packet", card=card["id"])


def check_fill(card_id, fill, cont):
    """Refuse to continue at or past the handoff mark. Returns None when no fill was given."""
    if fill is None:
        return None
    if isinstance(fill, bool) or not isinstance(fill, (int, float)) or not 0 <= fill <= 100:
        raise Precondition("context fill must be a percentage from 0 to 100")
    handoff = fill >= HANDOFF_PERCENT
    if handoff and cont:
        raise Precondition(
            f"context fill {fill:g}% reached the {HANDOFF_PERCENT}% handoff mark: stop and hand {card_id} to a "
            "fresh agent with its resume packet",
            card=card_id,
            hint=f"raidlock resume {card_id}",
        )
    return handoff


def _latest_run(board, card_id):
    for n in reversed(run_numbers(board, card_id)):
        folder = os.path.join(runs_dir(board, card_id), str(n))
        if os.path.exists(os.path.join(folder, "discarded.json")):
            continue
        info = store.read_json(os.path.join(folder, "run.json"), default=None)
        if not isinstance(info, dict):
            info = store.read_json(os.path.join(folder, "started.json"), default=None)
        if not isinstance(info, dict) or info.get("role") != "worker":
            continue
        return folder, info
    return None, {}


def _source(board, card):
    """Where the unfinished work lives: (kind, repo, base, worktree, run_dir, info)."""
    target = policy.target(board.workspace, board.dir, card["locks"])
    folder, info = _latest_run(board, card["id"])
    if folder:
        clone = os.path.join(folder, "repo")
        if os.path.exists(os.path.join(folder, "changes.patch")):
            return "accepted", target, info.get("base") or "", None, folder, info
        if os.path.exists(os.path.join(folder, "rejected.patch")) or (info.get("check") or {}).get("scan"):
            return "rejected", target, info.get("base") or "", None, folder, info
        if os.path.isdir(clone):
            return "clone", target, info.get("base") or "", clone, folder, info
    lease = board.leases().get(card["id"]) or {}
    checkout = (lease.get("checkout") or {}).get("path") or card.get("verification_checkout") or ""
    if checkout and os.path.isdir(checkout):
        try:
            base = W.head(checkout)
        except RaidlockError:
            base = ""
        return "checkout", target, base, checkout, None, {}
    return "none", target, "", None, folder, info


def _audit(target, base, worktree, out_dir, redactor):
    """Hash the worktree against BASE into a lead-owned store; return (status, paths, patch path, problems)."""
    if not base:
        return "unknown", [], "", ["the base commit of the interrupted work is unknown"]
    store_dir = os.path.join(out_dir, "lead.git")
    staging = os.path.join(out_dir, "pending.patch")
    try:
        try:
            paths, tree = W.audit(target["path"], base, worktree, store_dir)
        except RaidlockError as e:
            return "unknown", [], "", [redactor.text("could not read the interrupted work: " + e.message)]
        if not paths:
            return "empty", [], "", []
        W.write_patch(store_dir, base, tree, staging)
        with open(staging, "rb") as f:
            data = f.read()
        return _publish(data, target, out_dir, redactor)
    finally:
        shutil.rmtree(store_dir, ignore_errors=True)
        if os.path.exists(staging):
            os.remove(staging)


def validate_patch(data, rels, ci, redactor):
    paths = sorted(_parse(data.decode("utf-8", "surrogateescape"))[0])
    found = policy.check(paths, rels, ci)
    from .scan import patch_links

    problems = []
    bad_links = policy.check_links(patch_links(data.decode("utf-8", "surrogateescape")), rels, ci)
    if bad_links:
        problems.append("unsafe symlink targets: " + ", ".join(bad_links))
    if found["outside"]:
        problems.append("outside the card's locks: " + ", ".join(found["outside"][:20]))
    if found["denied"]:
        problems.append("denied paths: " + ", ".join(found["denied"][:20]))
    if any(secret.encode() in data for secret in redactor.secrets):
        problems.append("secret scan findings: known credential")
    findings = scan_patch(data, rels=rels, ci=ci)
    if findings:
        problems.append("secret scan findings: " + ", ".join(sorted({item["rule"] for item in findings})))
    return paths, problems


def _publish(data, target, out_dir, redactor):
    paths, problems = validate_patch(data, target["rels"], target["ci"], redactor)
    if problems:
        return "rejected", paths, "", problems
    patch = os.path.join(out_dir, "changes.patch")
    with open(patch, "xb") as f:
        os.chmod(patch, 0o600)
        f.write(data)
    return "accepted", paths, patch, []


def _secrets(board, caller, card):
    found = [caller.lead_secret] if caller.lead_secret else []
    folder = os.path.join(board.home, "tokens", board.uid)
    names = [p[:-6] for p in os.listdir(folder) if p.endswith(".token")] if os.path.isdir(folder) else []
    for name in names:
        token = I.read_token(board.home, board.uid, name)
        if token:
            found.append(token)
            for current in board.cards():
                for fence in range(1, int(current.get("fence") or 0) + 1):
                    found.append(I.make_cap(token, name, current["id"], fence))
    return found


def _fit(sections, cap):
    """Keep sections in priority order, trimming the first one that overflows and dropping the rest."""
    out, used, kept, dropped = [], 0, [], []
    for name, text in sections:
        if not text.strip():
            continue
        cost = estimate(text + "\n")
        if used + cost <= cap:
            out.append(text)
            used += cost
            kept.append(name)
            continue
        room = (cap - used) * 4 - 40
        if room > 200:
            lines, size = [], 0
            for line in text.splitlines():
                if size + len(line) + 1 > room:
                    break
                lines.append(line)
                size += len(line) + 1
            lines.append(f"[{name} cut to fit the {cap}-token budget]")
            clipped = "\n".join(lines)
            out.append(clipped)
            used += estimate(clipped + "\n")
            kept.append(name)
        else:
            dropped.append(name)
    return "\n".join(out) + "\n", kept, dropped


def build(board, caller, card_id, max_tokens=None, context_fill=None, cont=False):
    with board.lock():
        return _build(board, caller, card_id, max_tokens, context_fill, cont)


def _build(board, caller, card_id, max_tokens=None, context_fill=None, cont=False):
    """Build, save and return a resume packet for `card_id`. Lead or owner (with its fence) only."""
    card = board.card(card_id)
    _authorize(board, caller, card)
    handoff = check_fill(card_id, context_fill, cont)
    if cont:
        return {"card": card_id, "continue": True, "context_fill": context_fill, "handoff_at": HANDOFF_PERCENT}
    cap = int(max_tokens or board.settings["brief_max_tokens"] * DEFAULT_FACTOR)
    if cap < 200:
        raise Precondition("a resume packet needs a budget of at least 200 tokens")
    redactor = Redactor.for_env(os.environ, _secrets(board, caller, card))
    numbers = (
        [int(n) for n in os.listdir(resume_dir(board, card_id)) if n.isdigit()]
        if os.path.isdir(resume_dir(board, card_id))
        else []
    )
    out_dir = os.path.join(resume_dir(board, card_id), str(max(numbers, default=0) + 1))
    os.makedirs(out_dir, mode=0o700)
    kind, target, base, worktree, folder, info = _source(board, card)
    problems, paths, patch = [], [], ""
    if kind == "accepted":
        with open(os.path.join(folder, "changes.patch"), "rb") as f:
            data = f.read()
        status, paths, patch, problems = _publish(data, target, out_dir, redactor)
    elif kind == "rejected":
        status, paths = "rejected", list(info.get("paths") or [])
        problems = ["the last runner patch was rejected: " + str(info.get("reason") or "unsafe changes")[:300]]
    elif kind in ("clone", "checkout"):
        status, paths, patch, problems = _audit(target, base, worktree, out_dir, redactor)
    else:
        status = "none"
    handback = store.read_json(os.path.join(folder, "handback.json"), default={}) if folder else {}
    brief = board.brief(card_id, max_tokens=max(150, cap // 3))["text"]
    header = [
        f"# Resume packet: {card_id}",
        f"lane {card['lane']} | previous owner {card.get('owner') or '-'} | base {base[:12] or 'unknown'}",
        f"work so far: {status}" + (f", {len(paths)} path(s)" if paths else ""),
    ]
    header += [f"- {p}" for p in paths[:PATHS_SHOWN]]
    header += [f"problem: {p}" for p in problems]
    notes = [f"- {n.get('ts', '')} {n.get('by', '')}: {n.get('text', '')}" for n in card.get("notes", [])[-NOTES:]]
    remaining = [f"- {q}" for q in (handback or {}).get("open_questions") or []]
    if card.get("verify_cmd") or card.get("verify"):
        remaining.append(f"- checks: {card.get('verify_cmd') or card.get('verify')}")
    diff = ""
    if patch:
        with open(patch, encoding="utf-8", errors="replace") as f:
            diff = f.read()
    tail = []
    for rec in board.transcript.read(task=card_id, limit=TRANSCRIPT):
        text = " ".join(p.get("text", "") for p in rec.get("parts", []) if p.get("kind") == "text")
        who = (rec.get("from") or {}).get("agent", "")
        tail.append(f"- {rec.get('ts', '')} {rec.get('kind', '')} {who}: {' '.join(text.split())[:300]}")
    sections = [
        ("header", "\n".join(header)),
        ("brief", "## Card brief\n" + brief),
        ("notes", "## Latest notes\n" + "\n".join(notes) if notes else ""),
        ("remaining", "## Remaining work and checks\n" + "\n".join(remaining) if remaining else ""),
        ("diff", "## Work so far (validated patch)\n" + diff if diff else ""),
        ("transcript", "## Transcript tail\n" + "\n".join(tail) if tail else ""),
    ]
    sections = [
        (name, "\n".join(line if i == 0 else quote_data(line) for i, line in enumerate(text.splitlines())))
        for name, text in sections
    ]
    sections = [(name, redactor.text(redact(text))) for name, text in sections]
    sections[0] = ("header", sections[0][1] + "\n" + UNTRUSTED_RULE)
    text, kept, dropped = _fit(sections, cap)
    packet = {
        "card": card_id,
        "created": clock.iso(),
        "by": caller.name,
        "status": status,
        "source": kind,
        "base": base,
        "paths": paths,
        "problems": problems,
        "patch": patch,
        "run_dir": folder or "",
        "tokens": estimate(text),
        "max_tokens": cap,
        "sections": kept,
        "dropped": dropped,
        "handoff": bool(handoff),
        "dir": out_dir,
    }
    store.atomic_write_text(os.path.join(out_dir, "packet.md"), text)
    store.write_json(os.path.join(out_dir, "packet.json"), packet)
    with board.lock():
        board._record(
            "status",
            caller.name,
            f"resume packet for {card_id}: {status}, ~{packet['tokens']} tokens",
            task=card_id,
            data={"event": "resume_packet", "status": status, "tokens": packet["tokens"], "paths": len(paths)},
        )
    return {**packet, "text": text}


def _setup(p):
    p.add_argument("card", help="card id")
    p.add_argument("--max-tokens", type=int, default=None, help="packet budget (default: 3x brief_max_tokens)")
    p.add_argument("--context-fill", type=float, default=None, help="the asking agent's context fill in percent")
    p.add_argument(
        "--continue",
        dest="cont",
        action="store_true",
        help="ask whether to keep going; refused at or past the 80%% handoff mark",
    )


def _short(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home) :] if path and path.startswith(home + os.sep) else path


def _handler(ctx, a):
    r = build(ctx.board, ctx.caller(), a.card, max_tokens=a.max_tokens, context_fill=a.context_fill, cont=a.cont)
    if a.cont:
        return r, f"Keep going on {a.card}; hand off at {HANDOFF_PERCENT}% context fill."
    lines = [r["text"].rstrip("\n"), ""]
    lines.append(f"(~{r['tokens']}/{r['max_tokens']} tokens; work {r['status']}; packet {_short(r['dir'])}/packet.md)")
    return r, "\n".join(lines)


def register_cli(add_command):
    add_command("resume", "build a resume packet for a card a dead or expired worker left", _setup, _handler)
