"""Pull request awareness: link cards to PRs, poll state and checks through gh, close merged cards through the gates."""

import json
import re
import time

from . import pr as P
from .core import clock, model
from .core.errors import Precondition
from .runners import policy

FIELDS = "number,url,state,isDraft,mergedAt,headRefOid,headRefName,baseRefName,statusCheckRollup,reviewDecision"
RATE = re.compile(r"rate limit|abuse detection|HTTP 429", re.IGNORECASE)
PASS = {"SUCCESS"}
FAIL = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}
STATES = ("OPEN", "CLOSED", "MERGED")
SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
MAX_BACKOFF = 900


class RateLimited(Precondition):
    kind = "rate_limited"


def parse_ref(ref):
    text = str(ref or "").strip().lstrip("#")
    if text.isdigit() and 0 < int(text) < 10**9:
        return {"number": int(text), "url": ""}
    match = P.PR_URL.fullmatch(text)
    if match and int(match.group(1)) > 0:
        return {"number": int(match.group(1)), "url": text}
    raise Precondition("a pull request is a number or a .../pull/<n> URL", ref=text[:200])


def summarize(rollup):
    """Collapse gh's statusCheckRollup into counts and one state: success, failure, pending or none."""
    passed = failed = pending = 0
    failing = []
    for item in rollup if isinstance(rollup, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("context") or "check")[:100]
        if item.get("__typename") == "StatusContext" or ("state" in item and "conclusion" not in item):
            state = str(item.get("state", "")).upper()
            if state == "SUCCESS":
                passed += 1
            elif state in ("FAILURE", "ERROR"):
                failed += 1
                failing.append(name)
            else:
                pending += 1
            continue
        if str(item.get("status", "")).upper() != "COMPLETED":
            pending += 1
            continue
        conclusion = str(item.get("conclusion", "")).upper()
        if conclusion in PASS:
            passed += 1
        elif conclusion in FAIL:
            failed += 1
            failing.append(name)
        else:
            pending += 1
    total = passed + failed + pending
    state = "failure" if failed else "pending" if pending else "success" if total else "none"
    return {"state": state, "passed": passed, "failed": failed, "pending": pending, "total": total, "failing": failing}


def _repo_path(board, card):
    try:
        return policy.target(board.workspace, board.dir, card["locks"])["path"]
    except Precondition:
        return None


@P.protected
def fetch(board, card):
    """Current PR state from gh, validated. Raises RateLimited when GitHub throttles."""
    pr = card["pr"]
    proc = P.gh(
        ["pr", "view", pr.get("url") or str(pr["number"]), "--json", FIELDS], cwd=_repo_path(board, card), env=board.env
    )
    if proc is None:
        raise Precondition("gh is not installed", hint="install the GitHub CLI and run gh auth login")
    if proc.returncode != 0:
        err = P.sanitize(proc.stderr or proc.stdout, board.env).strip()
        if RATE.search(err):
            raise RateLimited("GitHub rate limit reached; try again later", card=card["id"])
        raise Precondition(f"gh pr view failed for {card['id']}: {err[:300]}", card=card["id"])
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        raise Precondition(f"gh returned invalid JSON for {card['id']}", card=card["id"]) from None
    if not isinstance(data, dict) or data.get("number") != pr["number"]:
        raise Precondition(f"gh returned a different pull request for {card['id']}", card=card["id"])
    state = str(data.get("state", "")).upper()
    head = str(data.get("headRefOid") or "")
    if state not in STATES or (head and not SHA.match(head)):
        raise Precondition(f"gh returned an unexpected pull request state for {card['id']}", card=card["id"])
    url = str(data.get("url") or pr.get("url") or "")
    if pr.get("url") and url != pr["url"]:
        raise Precondition(f"gh returned a different pull request for {card['id']}", card=card["id"])
    return {
        "state": state,
        "draft": bool(data.get("isDraft")),
        "merged_at": str(data.get("mergedAt") or ""),
        "head": head,
        "head_branch": str(data.get("headRefName") or "")[:200],
        "base": str(data.get("baseRefName") or "")[:200],
        "review_decision": str(data.get("reviewDecision") or ""),
        "checks": summarize(data.get("statusCheckRollup")),
        "url": url if P.PR_URL.fullmatch(url) else pr.get("url", ""),
    }


def link(board, caller, card_id, ref):
    """Lead: link a card to an existing pull request."""
    found = parse_ref(ref)
    with board.lead_write(caller, "link a pull request"):
        card = board.card(card_id)
        previous = card.get("pr") or {}
        card["pr"] = {
            "number": found["number"],
            "url": found["url"] or (previous.get("url") if previous.get("number") == found["number"] else ""),
            "linked": clock.iso(),
            "state": "",
            "checks": summarize([]),
        }
        board._note(card, caller.name, f"linked pull request #{found['number']}")
        board._save_card(card)
        board._record(
            "status",
            caller.name,
            f"{card_id} linked to pull request #{found['number']}",
            task=card_id,
            data={"event": "pr_linked", "pr": card["pr"]},
        )
    return {"card": card_id, "pr": card["pr"]}


def _due(card, min_interval, force):
    pr = card["pr"]
    if force:
        return True
    if card["lane"] == "done" and pr.get("state") in ("MERGED", "CLOSED"):
        return False
    if pr.get("state") == "CLOSED":
        return False
    return not pr.get("synced") or clock.age_seconds(pr["synced"]) >= min_interval


def hold_reason(board, card, info):
    """Why a merged PR does not close its card yet, or "" when the gates may run."""
    if card["lane"] not in ("review", "blocked"):
        return f"card is in {card['lane']}"
    last = model.last_verification(card) or {}
    if not last.get("sha"):
        return "no verify receipt"
    if info["head"] != last["sha"]:
        return f"merged head {info['head'][:12]} is not the verified commit {last['sha'][:12]}"
    if info["checks"]["state"] == "failure":
        return "checks failed: " + ", ".join(info["checks"]["failing"][:5])
    if info["checks"]["state"] != "success":
        return "checks not successful: " + info["checks"]["state"]
    return ""


def _update(board, caller, card_id, info):
    with board.lead_write(caller, "sync a pull request"):
        card = board.card(card_id)
        pr = card.get("pr") or {}
        before = (pr.get("state"), (pr.get("checks") or {}).get("state"))
        pr.update(info, synced=clock.iso())
        card["pr"] = pr
        after = (info["state"], info["checks"]["state"])
        if before != after:
            board._note(card, caller.name, f"pull request #{pr['number']}: {after[0].lower()}, checks {after[1]}")
            board._record(
                "status",
                caller.name,
                f"pull request #{pr['number']} {after[0].lower()}, checks {after[1]}",
                task=card_id,
                data={"event": "pr_state", "state": after[0], "checks": info["checks"]},
            )
        board._save_card(card)
        return card


def _hold(board, caller, card_id, reason):
    with board.lead_write(caller, "hold a merged pull request"):
        card = board.card(card_id)
        if card["pr"].get("held") != reason:
            card["pr"]["held"] = reason
            board._note(card, caller.name, f"merged but not done: {reason}")
            board._record(
                "status",
                caller.name,
                f"{card_id} merged but not done: {reason}",
                task=card_id,
                data={"event": "pr_merge_held", "reason": reason},
            )
            board._save_card(card)


def _advance(board, caller, card, info):
    reason = hold_reason(board, card, info)
    if not reason:
        try:
            board.move(card["id"], "done", caller, note=f"merged as pull request #{card['pr']['number']}")
            from .runners.runner import prune_runs

            prune_runs(board, card["id"])
            return "done", ""
        except Precondition as exc:
            reason = exc.message
    _hold(board, caller, card["id"], reason)
    return "held", reason


def sync(board, caller, ids=None, min_interval=60, force=False):
    """Lead: poll each linked PR once (respecting min_interval), record state and checks, and move merged cards to
    done only through the done gate. Stops at the first rate limit."""
    board._auth(caller)
    board._require_lead(caller, "sync pull requests")
    cards = board.cards()
    if ids:
        known = {c["id"]: c for c in cards}
        for cid in ids:
            if cid not in known:
                raise Precondition(f"no card {cid}", card=cid)
            if not known[cid].get("pr"):
                raise Precondition(f"{cid} has no pull request", card=cid, hint=f"raidlock pr-link {cid} <number>")
        cards = [known[cid] for cid in ids]
    else:
        cards = [c for c in cards if c.get("pr")]
    out = {"synced": [], "skipped": [], "done": [], "held": [], "errors": [], "rate_limited": False}
    for card in cards:
        if not _due(card, min_interval, force):
            out["skipped"].append(card["id"])
            continue
        try:
            info = fetch(board, card)
        except RateLimited:
            out["rate_limited"] = True
            break
        except Precondition as exc:
            out["errors"].append({"card": card["id"], "error": exc.message})
            continue
        with board.lead_write(caller, "sync a pull request"):
            current = board.card(card["id"])
            identity = ("number", "url", "linked", "opened", "repo")
            if any((current.get("pr") or {}).get(k) != card["pr"].get(k) for k in identity):
                out["errors"].append({"card": card["id"], "error": "pull request changed during sync"})
                continue
            card = _update(board, caller, card["id"], info)
            out["synced"].append({"card": card["id"], "state": info["state"], "checks": info["checks"]["state"]})
            if info["state"] == "MERGED" and card["lane"] != "done":
                verdict, reason = _advance(board, caller, card, info)
                if verdict == "done":
                    out["done"].append(card["id"])
                else:
                    out["held"].append({"card": card["id"], "reason": reason})
    return out


def watch(board, caller, interval=60, max_polls=None, sleep=time.sleep, **kw):
    """Repeat sync; double the wait after a rate limit, up to MAX_BACKOFF, and reset after a clean poll."""
    polls, wait, last = 0, interval, None
    while max_polls is None or polls < max_polls:
        last = sync(board, caller, **kw)
        polls += 1
        wait = min(MAX_BACKOFF, wait * 2) if last["rate_limited"] else interval
        if max_polls is not None and polls >= max_polls:
            break
        sleep(wait)
    return {**(last or {}), "polls": polls, "next_wait": wait}


def register_cli(add_command):
    def link_setup(p):
        p.add_argument("id", help="card id")
        p.add_argument("ref", help="pull request number or URL")

    def link_handler(ctx, a):
        r = link(ctx.board, ctx.caller(), a.id, a.ref)
        return r, f"Linked {a.id} to pull request #{r['pr']['number']}."

    def sync_setup(p):
        p.add_argument("ids", nargs="*", help="cards to sync (default: every card with a pull request)")
        p.add_argument("--min-interval", type=float, default=60, help="seconds between polls of one PR")
        p.add_argument("--force", action="store_true", help="poll even when recently synced or closed")
        p.add_argument("--watch", type=float, default=None, metavar="SECONDS", help="keep polling at this interval")
        p.add_argument("--max-polls", type=int, default=None)

    def sync_handler(ctx, a):
        kw = {"ids": a.ids, "min_interval": a.min_interval, "force": a.force}
        if a.watch:
            r = watch(ctx.board, ctx.caller(), interval=a.watch, max_polls=a.max_polls, **kw)
        else:
            r = sync(ctx.board, ctx.caller(), **kw)
        lines = [f"{s['card']}: {s['state'].lower()}, checks {s['checks']}" for s in r["synced"]]
        lines += [f"{cid}: merged, moved to done" for cid in r["done"]]
        lines += [f"{h['card']}: merged, held: {h['reason']}" for h in r["held"]]
        lines += [f"{e['card']}: {e['error']}" for e in r["errors"]]
        if r["rate_limited"]:
            lines.append("Stopped: GitHub rate limit reached; run again later.")
        if not lines:
            lines.append("Nothing to sync." + (f" Skipped: {', '.join(r['skipped'])}." if r["skipped"] else ""))
        return {**r, "_exit": 2 if r["rate_limited"] or r["errors"] else 0}, "\n".join(lines)

    add_command("pr-link", "lead: link a card to an existing pull request", link_setup, link_handler, True)
    add_command(
        "pr-sync",
        "lead: poll linked pull requests; merged cards close only through the gates",
        sync_setup,
        sync_handler,
        True,
    )
