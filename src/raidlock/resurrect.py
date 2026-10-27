"""Resurrection: hand a stalled card to a fresh agent with its resume packet, within budget and attempt limits.

Only the lead runs it: the explicit `raidlock resurrect <card>` command, or a supervisor that holds the current lead
lease running `raidlock resurrect --sweep`, which does nothing unless the lead enabled automatic resurrection. The
replacement is a new agent with a new claim and fence, launched through the runner, which strips every board
credential from its environment; the lead secret never reaches it. Every attempt is logged as an alert and
recorded with its provider, reason and cost. A card that cannot be resurrected stays blocked with the reason."""

import argparse
import datetime
import json
import os
import re
import time
import uuid

from . import duel, routing
from . import resume as R
from .budgets import check_before_delegate
from .core import clock, store
from .core import leases as L
from .core.errors import Denied, IdentityError, Precondition, RaidlockError
from .core.identity import RUNTIME_SHORT, short_runtime
from .runners.runner import ADAPTERS, MODEL_ID, delegate, run_numbers, runs_dir

FILE = "resurrect.json"
DEFAULTS = {"enabled": False, "max_attempts": 2, "cooldown_minutes": 10, "providers": [], "budget_usd": None}
QUOTA = re.compile(
    r"usage limit|credit|quota|insufficient|limit reached|rate_limit_reached|exhausted|billing|out of tokens", re.I
)
TRANSIENT = re.compile(r"\b429\b|rate.?limit|overloaded|too many requests|temporarily unavailable", re.I)
ELIGIBLE = ("quota", "transient", "timeout", "lost", "expired", "stopped")


def _load(board):
    data = store.read_doc(board._p(FILE), "resurrect")
    if not isinstance(data, dict):
        raise Precondition(f"corrupt {FILE}")
    policy = dict(DEFAULTS)
    for key, value in (data.get("policy") or {}).items():
        policy[key] = validate(key, value)
    attempts = data.get("attempts") or {}
    if not isinstance(attempts, dict):
        raise Precondition(f"corrupt {FILE}: attempts must be an object")
    return policy, attempts


def policy(board):
    return _load(board)[0]


def attempts(board, card_id):
    return list(_load(board)[1].get(card_id) or [])


def _write(board, policy_value, attempts_value):
    store.write_doc(board._p(FILE), "resurrect", {"policy": policy_value, "attempts": attempts_value})
    board.index.bump(board.uid)


def validate(key, value):
    if key == "enabled":
        if not isinstance(value, bool):
            raise Precondition("enabled must be true or false")
        return value
    if key in ("max_attempts", "cooldown_minutes"):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 1000:
            raise Precondition(f"{key} must be an integer from 0 to 1000")
        return value
    if key == "providers":
        if not isinstance(value, list) or any(p not in ADAPTERS for p in value):
            raise Precondition(f"providers must be a list from {', '.join(ADAPTERS)}")
        return list(dict.fromkeys(value))
    if key == "budget_usd":
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise Precondition("budget_usd must be a nonnegative number or null")
        return float(value)
    raise Precondition(f"unknown resurrect setting {key}; known: {', '.join(DEFAULTS)}")


def set_policy(board, caller, key, value):
    """Lead-only: change one resurrect setting. Turning it on is logged as an alert."""
    value = validate(key, value)
    with board.lead_write(caller, f"change resurrect {key}"):
        current, ledger = _load(board)
        current[key] = value
        _write(board, current, ledger)
        board._record(
            "status",
            caller.name,
            f"{'ALERT ' if key == 'enabled' and value else ''}resurrect {key} set to {json.dumps(value)}",
            data={"event": "resurrect_policy", "key": key, "value": value, "alert": key == "enabled" and value},
        )
    return current


def evidence(board, card):
    """Why the last attempt stopped, from runner records only: (classification, detail, runtime, run dir)."""
    numbers = run_numbers(board, card["id"])
    for n in reversed(numbers):
        folder = os.path.join(runs_dir(board, card["id"]), str(n))
        info = store.read_json(os.path.join(folder, "run.json"), default=None)
        started = store.read_json(os.path.join(folder, "started.json"), default=None)
        if not isinstance(info, dict):
            if isinstance(started, dict) and started.get("role") == "worker":
                return "stopped", "the runner stopped before the agent finished", "", folder
            continue
        if info.get("role") != "worker":
            continue
        runtime = RUNTIME_SHORT.get(info.get("runtime", ""), info.get("runtime", ""))
        detail = str(info.get("failure") or info.get("lost") or "")
        if info.get("lost"):
            return "lost", detail, runtime, folder
        if not detail:
            return "finished", str(info.get("reason") or ""), runtime, folder
        if QUOTA.search(detail):
            return "quota", detail, runtime, folder
        if TRANSIENT.search(detail):
            return "transient", detail, runtime, folder
        if "timed out" in detail:
            return "timeout", detail, runtime, folder
        return "failed", detail, runtime, folder
    for note in reversed(card.get("notes", [])):
        if note.get("by") == "raidlock" and "lease expired" in note.get("text", ""):
            return "expired", note["text"], "", ""
    return "none", "", "", ""


def _alert(board, lead, card_id, text, **data):
    with board.lock():
        board._record("status", lead.name, "ALERT " + text, task=card_id, data={"alert": True, **data})


def _check_limits(board, card, ledger, max_attempts, budget_usd):
    done = len(ledger)
    if done >= max_attempts:
        raise Denied(
            f"maximum attempts reached for {card['id']}: {done} of {max_attempts} used, attempts exhausted",
            card=card["id"],
            hint=f"raidlock show {card['id']}",
        )
    used = check_before_delegate(board, card)
    if not used["allowed"]:
        raise Denied("resurrect budget exhausted: " + "; ".join(used["reasons"]), card=card["id"])
    spent = used["used"]["card_usd"]
    if budget_usd is not None and spent >= budget_usd:
        raise Denied(
            f"resurrect budget exhausted for {card['id']}: ${spent:.2f} spent of ${budget_usd:.2f}", card=card["id"]
        )


def _check_state(board, card, ledger, cooldown_minutes):
    cid, lane = card["id"], card["lane"]
    lease = board.leases().get(cid)
    if lane == "doing" and lease and L.is_live(lease):
        raise Denied(f"{cid} has a live worker ({card['owner']}); resurrect never starts a second one", card=cid)
    if lane in ("review", "done", "todo"):
        raise Precondition(f"{cid} is in {lane}; only a stalled card (blocked or expired) is resurrected", card=cid)
    record = duel.load(board, cid)
    if record and record.get("status") in ("running", "decide"):
        raise Precondition(f"{cid} is in a duel waiting for the lead; resurrect leaves it alone", card=cid)
    if ledger and cooldown_minutes:
        last = clock.parse(ledger[-1].get("ts", ""))
        ready = last + datetime.timedelta(minutes=cooldown_minutes) if last else None
        if ready and ready > clock.now():
            raise Precondition(f"{cid} is cooling down until {clock.iso(ready)} after its last attempt", card=cid)


def _choose(board, card, pol, to, kind, failed):
    if to:
        if to not in ADAPTERS:
            raise Precondition(f"--to must be one of {', '.join(ADAPTERS)}")
        candidates = [to]
    else:
        candidates = list(pol["providers"]) or ([failed] if failed else [])
    if kind == "quota" and not to:
        candidates = [c for c in candidates if c != failed]
    if not candidates:
        return "", "no allowed provider: set resurrect providers or pass --to"
    cfg = routing.config(board)
    collected = routing.LM.collect()
    reasons = []
    for c in candidates:
        room = routing.headroom(c, cfg, collected)
        if room["ok"]:
            return c, room["reason"]
        reasons.append(room["reason"])
    return "", "; ".join(reasons)


def _name(n, runtime, card_id):
    return f"res{n}-{RUNTIME_SHORT.get(ADAPTERS[runtime].runtime, runtime)}-{card_id}"[:40].rstrip("-")


def attempt(
    board,
    lead,
    card_id,
    to=None,
    model=None,
    cmd=None,
    max_attempts=None,
    budget_usd=None,
    reason="manual",
    dry_run=False,
    timeout=3600.0,
    heartbeat=60.0,
):
    """Resurrect one card. Limits are checked first, then the card state, then the provider. Lead only."""
    if not lead.is_lead:
        raise IdentityError("only the lead (or a supervisor holding the lead lease) resurrects cards")
    with board.lead_write(lead, f"consider resurrecting {card_id}"):
        pol, ledger_all = _load(board)
        card = board.card(card_id)
        ledger = list(ledger_all.get(card_id) or [])
        if model and not MODEL_ID.match(model):
            raise Precondition(f"invalid model id {model!r}")
        cap = pol["max_attempts"] if max_attempts is None else validate("max_attempts", max_attempts)
        budget = pol["budget_usd"] if budget_usd is None else validate("budget_usd", budget_usd)
        try:
            _check_limits(board, card, ledger, cap, budget)
        except Denied as e:
            _alert(board, lead, card_id, f"resurrect refused for {card_id}: {e.message}", event="resurrect_refused")
            raise
        board.reap()
        card = board.card(card_id)
        _check_state(board, card, ledger, pol["cooldown_minutes"])
        kind, detail, failed, _ = evidence(board, card)
        provider, why = _choose(board, card, pol, to, kind, failed)
        plan = {
            "card": card_id,
            "attempt": len(ledger) + 1,
            "max_attempts": cap,
            "classification": kind,
            "evidence": detail[:300],
            "from_agent": card.get("owner", ""),
            "from_provider": failed,
            "to": provider,
            "why": why,
            "reason": reason,
        }
        if not provider:
            board.note(card_id, lead, f"resurrect stuck: {why}"[:1900])
            _alert(board, lead, card_id, f"resurrect stuck on {card_id}: {why}", event="resurrect_stuck")
            return {**plan, "status": "stuck"}
        if dry_run:
            return {**plan, "status": "planned"}
        if provider == "shell" and not cmd:
            raise Precondition("resurrecting with shell needs --cmd")
        packet = R.build(board, lead, card_id)
        plan["packet"] = packet["dir"]
        plan["packet_status"] = packet["status"]
        name = _name(plan["attempt"], provider, card_id)
        entry = {"id": uuid.uuid4().hex, **plan, "ts": clock.iso(), "agent": name, "status": "launched"}
        ledger_all.setdefault(card_id, []).append(entry)
        _write(board, pol, ledger_all)
        board.move(card_id, "todo", lead, note=f"resurrect: attempt {plan['attempt']} replaces {card['owner'] or '-'}")
    _alert(
        board,
        lead,
        card_id,
        f"RESURRECT {card_id}: attempt {plan['attempt']}/{cap} to {provider} as {name} "
        f"(was {plan['from_agent'] or '-'} on {failed or '-'}, {kind})",
        event="resurrect",
        attempt=plan["attempt"],
        to=provider,
        agent=name,
        classification=kind,
    )
    try:
        result = delegate(
            board,
            lead,
            card_id,
            provider,
            model=model,
            cmd=cmd,
            timeout=timeout,
            heartbeat=heartbeat,
            agent=name,
            resume={k: packet[k] for k in ("text", "patch", "base")},
        )
        outcome = {"status": "ran", "lane": result.get("lane", ""), "usd": result.get("usd")}
        outcome["run_dir"] = result.get("run_dir", "")
        outcome["result"] = result.get("reason") or result.get("status", "")
    except RaidlockError as e:
        outcome = {"status": "error", "lane": board.card(card_id)["lane"], "result": e.message}
        board.note(card_id, lead, f"resurrect attempt {plan['attempt']} failed to launch: {e.message}"[:1900])
        _alert(board, lead, card_id, f"resurrect launch failed on {card_id}: {e.message}", event="resurrect_error")
    with board.lead_write(lead, f"record resurrect result on {card_id}"):
        pol_now, ledger_now = _load(board)
        rows = ledger_now.get(card_id) or []
        row = next(row for row in rows if row.get("id") == entry["id"])
        row.update(outcome)
        _write(board, pol_now, ledger_now)
    return {**entry, **outcome}


def sweep(board, lead, **kw):
    """One supervisor pass: resurrect eligible stalled cards when enabled. Returns what it did and skipped."""
    if not lead.is_lead:
        raise IdentityError("the sweep runs as the lead: hand the lead lease to the supervisor session first")
    with board.lead_write(lead, "resurrect sweep"):
        pol = _load(board)[0]
    if not pol["enabled"]:
        return {"enabled": False, "acted": [], "skipped": []}
    board.reap()
    acted, skipped = [], []
    for card in board.cards():
        if card["lane"] != "blocked":
            continue
        kind = evidence(board, card)[0]
        if kind not in ELIGIBLE:
            skipped.append({"card": card["id"], "reason": f"not resurrectable ({kind}); needs the lead"})
            continue
        try:
            acted.append(attempt(board, lead, card["id"], reason="sweep", **kw))
        except RaidlockError as e:
            skipped.append({"card": card["id"], "reason": e.message})
    return {"enabled": True, "acted": acted, "skipped": skipped}


def _setup(p):
    p.add_argument("card", nargs="?", help="card id (omit with --sweep)")
    p.add_argument(
        "--to", default=None, type=short_runtime, choices=sorted(ADAPTERS), help="runtime for the replacement"
    )
    p.add_argument("--model", default=None)
    p.add_argument("--cmd", default=None, help="command for --to shell")
    p.add_argument("--max-attempts", type=int, default=None, help="override the attempt limit for this call")
    p.add_argument("--budget", type=float, default=None, help="card spend in USD above which resurrect refuses")
    p.add_argument("--dry-run", action="store_true", help="show the plan without changing anything")
    p.add_argument("--sweep", action="store_true", help="supervisor pass over stalled cards (needs enabled policy)")
    p.add_argument("--watch", type=float, default=0, help="with --sweep: repeat every N seconds")
    p.add_argument("--iterations", type=int, default=0, help=argparse.SUPPRESS)
    p.add_argument("--timeout", type=float, default=3600.0, help=argparse.SUPPRESS)
    p.add_argument("--heartbeat", type=float, default=60.0, help=argparse.SUPPRESS)


def _handler(ctx, a):
    kw = {"timeout": a.timeout, "heartbeat": a.heartbeat}
    if a.sweep:
        if a.card:
            raise Precondition("--sweep takes no card")
        kw.update(
            {
                k: v
                for k, v in {
                    "to": a.to,
                    "model": a.model,
                    "cmd": a.cmd,
                    "max_attempts": a.max_attempts,
                    "budget_usd": a.budget,
                    "dry_run": a.dry_run,
                }.items()
                if v not in (None, False)
            }
        )
        n = 0
        while True:
            r = sweep(ctx.board, ctx.caller(), **kw)
            n += 1
            if not a.watch or (a.iterations and n >= a.iterations):
                break
            time.sleep(a.watch)
        if not r["enabled"]:
            return r, "automatic resurrection is off.\nNext: raidlock resurrect-policy set enabled true   (lead)"
        lines = [f"resurrected {x['card']} -> {x.get('to')} ({x.get('status')})" for x in r["acted"]]
        lines += [f"skipped {x['card']}: {x['reason']}" for x in r["skipped"]]
        return r, "\n".join(lines) or "nothing stalled"
    if not a.card:
        raise Precondition("give a card id or --sweep")
    r = attempt(
        ctx.board,
        ctx.caller(),
        a.card,
        to=a.to,
        model=a.model,
        cmd=a.cmd,
        max_attempts=a.max_attempts,
        budget_usd=a.budget,
        dry_run=a.dry_run,
        **kw,
    )
    text = f"{a.card}: {r['status']} (attempt {r['attempt']}/{r['max_attempts']}, {r['classification']})"
    if r.get("to"):
        text += f" -> {r['to']}"
    if r["status"] == "stuck":
        text += f"\nleft blocked: {r['why']}"
    return r, text + f"\nNext: raidlock show {a.card}"


def _policy_setup(p):
    p.add_argument("action", nargs="?", default="show", choices=("show", "set"))
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?", help="JSON value")


def _policy_handler(ctx, a):
    if a.action == "set":
        if a.key is None or a.value is None:
            raise Precondition("resurrect-policy set needs a key and a JSON value")
        try:
            value = json.loads(a.value)
        except ValueError:
            raise Precondition("value must be JSON") from None
        r = set_policy(ctx.board, ctx.caller(), a.key, value)
        return {"policy": r}, f"resurrect {a.key} = {json.dumps(r[a.key])}"
    pol, ledger = _load(ctx.board)
    return {"policy": pol, "attempts": ledger}, json.dumps({"policy": pol}, indent=2, sort_keys=True)


def register_cli(add_command):
    add_command("resurrect", "hand a stalled card to a fresh agent with its resume packet", _setup, _handler, True)
    add_command(
        "resurrect-policy", "show or set automatic resurrection (off by default)", _policy_setup, _policy_handler, True
    )
