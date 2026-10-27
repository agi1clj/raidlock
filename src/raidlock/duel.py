"""Duel: the same card run by two providers in isolated runner clones, compared on evidence, decided by the lead.

The card has one lease at a time, so the contestants run one after the other: each gets its own claim and fence,
and the lead parks the card between runs, which makes the earlier fence stale. Each accepted patch is committed in
a lead-owned clone and verified there, giving a signed receipt for that exact commit. The lead picks the winner
only from a contestant with a valid, passing receipt; the loser's patch is discarded. Both runs record their cost
on the card, so both count toward budgets."""

import argparse
import os
import shutil
import time

from .core import clock, receipts, repos, store
from .core import transcript as T
from .core.errors import Denied, Precondition, RaidlockError
from .core.identity import RUNTIME_SHORT, short_runtime
from .runners import policy
from .runners import workspace as W
from .runners.runner import ADAPTERS, MODEL_ID, delegate

LABELS = ("a", "b")


def duel_dir(board, card_id):
    return os.path.join(board.home, "duels", board.name, card_id)


def record_path(board, card_id):
    return os.path.join(duel_dir(board, card_id), "duel.json")


def load(board, card_id):
    data = store.read_json(record_path(board, card_id), default=None)
    return data if isinstance(data, dict) else None


def _save(board, record):
    os.makedirs(duel_dir(board, record["card"]), mode=0o700, exist_ok=True)
    store.write_json(record_path(board, record["card"]), record)


def contestant_name(label, runtime, card_id):
    return f"duel-{label}-{RUNTIME_SHORT.get(ADAPTERS[runtime].runtime, runtime)}-{card_id}"[:40].rstrip("-")


def _sides(vs, models, cmds):
    runtimes = [short_runtime(v) for v in (vs.split(",") if isinstance(vs, str) else vs) if v.strip()]
    if len(runtimes) != 2:
        raise Precondition("a duel needs exactly two contestants: --vs <runtime>,<runtime>")
    for r in runtimes:
        if r not in ADAPTERS:
            raise Precondition(f"--vs: {r} is not one of {', '.join(ADAPTERS)}", runtime=r)
    models = list(models or ["", ""])
    cmds = list(cmds or [])
    if len(models) != 2:
        raise Precondition("--model takes one model per contestant: --model <a>,<b>")
    for m in models:
        if m and not MODEL_ID.match(m):
            raise Precondition(f"invalid model id {m!r}")
    shells = [i for i, r in enumerate(runtimes) if r == "shell"]
    if len(cmds) != len(shells):
        raise Precondition("give one --cmd per shell contestant")
    cmd_for = dict(zip(shells, cmds, strict=True))
    if runtimes[0] == runtimes[1] and models[0] == models[1] and cmd_for.get(0) == cmd_for.get(1):
        raise Precondition("both contestants are identical; vary the runtime, the model or the command")
    return [{"label": LABELS[i], "runtime": runtimes[i], "model": models[i], "cmd": cmd_for.get(i)} for i in range(2)]


def passing(board, card_id, side):
    """True only when the side's receipt is on the card, signed by this board, passed, and names the commit its
    lead-owned clone still has checked out."""
    rec = side.get("receipt") or {}
    clone = side.get("clone") or ""
    if not rec.get("signature") or not clone or not os.path.isdir(clone):
        return False
    card = board.card(card_id)
    found = next((v for v in card.get("verifications", []) if v.get("signature") == rec["signature"]), None)
    if not found or not receipts.valid(board, card, found):
        return False
    if not found.get("passed") or found.get("exit") != 0 or found.get("sha") != side.get("sha"):
        return False
    try:
        return W.head(clone) == found["sha"]
    except RaidlockError:
        return False


def _commit_clone(board, card_id, label, run_dir, base, target):
    """A lead-owned clone at BASE with the contestant's accepted patch committed; returns (path, sha)."""
    dst = os.path.join(duel_dir(board, card_id), label, "repo")
    W.clone(target["path"], dst)
    origin = repos.remote_url(target["path"]) or os.path.realpath(target["path"])
    W.git(dst, "remote", "add", "origin", origin)
    W.git(dst, "checkout", "--quiet", "--detach", base)
    patch = os.path.join(run_dir, "changes.patch")
    if os.path.exists(patch):
        W.apply_and_commit(dst, patch)
    return dst, W.head(dst)


def _patch_meta(run_dir):
    info = store.read_json(os.path.join(run_dir, "run.json"), default={}) or {}
    patch = info.get("patch") or {}
    return info, {"files": patch.get("files", 0), "bytes": patch.get("bytes", 0), "rejected": patch.get("rejected")}


def _run_side(board, lead, card_id, side, target, reviewer, timeout, heartbeat):
    started = time.monotonic()
    out = {**side, "agent": contestant_name(side["label"], side["runtime"], card_id)}
    try:
        r = delegate(
            board,
            lead,
            card_id,
            side["runtime"],
            model=side["model"] or None,
            cmd=side["cmd"],
            timeout=timeout,
            heartbeat=heartbeat,
            agent=out["agent"],
        )
    except RaidlockError as e:
        return {**out, "status": "refused", "reason": e.message, "lane": board.card(card_id)["lane"]}
    info, meta = _patch_meta(r["run_dir"])
    out.update(
        status=r.get("status", ""),
        lane=r.get("lane", ""),
        reason=r.get("reason", ""),
        run_dir=r["run_dir"],
        duration_ms=info.get("duration_ms") or int((time.monotonic() - started) * 1000),
        usd=r.get("usd"),
        tokens=sum((r.get("usage") or {}).values()) if isinstance(r.get("usage"), dict) else 0,
        model=r.get("model") or side["model"],
        diff=meta,
        base=info.get("base", ""),
    )
    card = board.card(card_id)
    if not meta["rejected"] and r.get("status") != "lost" and out["base"] and card.get("verify_cmd"):
        try:
            clone, sha = _commit_clone(board, card_id, side["label"], r["run_dir"], out["base"], target)
            board.trust_checkout(lead, card_id, clone, target["path"])
            receipt = board.verify(card_id, lead, cwd=clone)
            out["clone"], out["sha"] = clone, sha
            out["receipt"] = {k: receipt.get(k) for k in ("ts", "sha", "exit", "passed", "signature", "isolation")}
        except RaidlockError as e:
            out["verify_error"] = e.message
    elif not card.get("verify_cmd"):
        out["verify_error"] = "the card has no verify command; a duel needs one for signed receipts"
    if reviewer and board.card(card_id)["lane"] == "review":
        try:
            rv = delegate(board, lead, card_id, reviewer, role="reviewer", timeout=timeout, heartbeat=heartbeat)
            out["review"] = {"by": rv.get("agent", ""), "verdict": rv.get("verdict") or rv.get("status", "")}
        except RaidlockError as e:
            out["review"] = {"by": "", "verdict": "", "error": e.message}
    return out


def _park(board, lead, card_id, lane, note, owners=()):
    card = board.card(card_id)
    current = card["lane"]
    if owners and card.get("owner") and card["owner"] not in owners:
        raise Denied(f"{card_id} is now held by {card['owner']}, not a duel contestant; the duel stops", card=card_id)
    if current != lane and current != "done":
        board.move(card_id, lane, lead, note=note)


def run(board, lead, card_id, vs, models=None, cmds=None, reviewer=None, timeout=3600.0, heartbeat=60.0):
    """Run both contestants, verify each accepted patch, record the comparison. Lead only."""
    if not lead.is_lead:
        raise Precondition("only the lead runs a duel")
    sides = _sides(vs, models, cmds)
    reviewer = short_runtime(reviewer) if reviewer else reviewer
    if reviewer and reviewer not in ADAPTERS:
        raise Precondition(f"--reviewer must be one of {', '.join(ADAPTERS)}")
    card = board.card(card_id)
    existing = load(board, card_id)
    if existing and existing.get("status") == "decide":
        raise Precondition(f"{card_id} has an undecided duel", hint=f"raidlock duel {card_id} --winner a|b")
    if card["lane"] != "todo":
        raise Precondition(f"{card_id} is in {card['lane']}; a duel starts from todo", card=card_id)
    if not card.get("verify_cmd"):
        raise Precondition(f"{card_id} has no verify command; a duel is decided on signed receipts", card=card_id)
    target = policy.target(board.workspace, board.dir, card["locks"])
    shutil.rmtree(duel_dir(board, card_id), ignore_errors=True)
    record = {"card": card_id, "started": clock.iso(), "status": "running", "sides": [], "winner": ""}
    _save(board, record)
    for i, side in enumerate(sides):
        result = _run_side(board, lead, card_id, side, target, reviewer, timeout, heartbeat)
        record["sides"].append(result)
        _save(board, record)
        last = i == len(sides) - 1
        if result["status"] == "refused":
            break
        _park(
            board,
            lead,
            card_id,
            "blocked" if last else "todo",
            f"duel: contestant {side['label']} parked",
            owners=(result["agent"],),
        )
    if len(record["sides"]) < 2 or record["sides"][-1]["status"] == "refused":
        record["status"] = "incomplete"
        if board.card(card_id)["lane"] != "todo":
            _park(board, lead, card_id, "todo", "duel stopped before both contestants ran")
    else:
        record["status"] = "decide"
    record["finished"] = clock.iso()
    record["verified"] = [s["label"] for s in record["sides"] if passing(board, card_id, s)]
    _save(board, record)
    rows = compare(record)
    with board.lock():
        board._record(
            "artifact",
            lead.name,
            f"duel on {card_id}: {record['status']}",
            task=card_id,
            artifacts=[T.artifact("duel", data={"rows": rows, "status": record["status"]}, kind="duel")],
        )
    return {**record, "rows": rows}


def compare(record):
    rows = []
    for s in record.get("sides", []):
        receipt = s.get("receipt") or {}
        rows.append(
            {
                "side": s["label"],
                "agent": s.get("agent", ""),
                "runtime": s["runtime"],
                "model": s.get("model") or "default",
                "lane": s.get("lane", ""),
                "verify": "passed" if receipt.get("passed") else ("failed" if receipt else "none"),
                "review": (s.get("review") or {}).get("verdict", "") or "-",
                "files": (s.get("diff") or {}).get("files", 0),
                "bytes": (s.get("diff") or {}).get("bytes", 0),
                "seconds": round((s.get("duration_ms") or 0) / 1000, 1),
                "usd": s.get("usd"),
                "reason": s.get("reason", ""),
            }
        )
    return rows


def table(rows):
    head = ("side", "runtime", "model", "verify", "review", "files", "bytes", "seconds", "usd")
    lines = ["  ".join(head)]
    for r in rows:
        usd = "-" if r["usd"] is None else f"{r['usd']:.4f}"
        lines.append(
            "  ".join(
                str(x)
                for x in (
                    r["side"],
                    r["runtime"],
                    r["model"],
                    r["verify"],
                    r["review"],
                    r["files"],
                    r["bytes"],
                    r["seconds"],
                    usd,
                )
            )
        )
    return "\n".join(lines)


def pick(board, lead, card_id, winner):
    """Lead decision: hand the card to the winner in review, discard the loser's patch, verify the winner again."""
    record = load(board, card_id)
    sides = (record or {}).get("sides") or []
    verified = [s for s in sides if passing(board, card_id, s)]
    if not record or not verified:
        raise Precondition(
            f"no verified duel evidence for {card_id}: a winner needs a passing signed verification receipt",
            card=card_id,
            hint=f"raidlock duel {card_id} --vs <runtime>,<runtime>",
        )
    if record.get("status") != "decide":
        raise Precondition(f"the duel on {card_id} is {record.get('status')}, not waiting for a decision")
    side = next((s for s in sides if winner in (s["label"], s.get("agent"))), None)
    if side is None:
        raise Precondition(f"no contestant {winner}; use a, b or the agent name", card=card_id)
    if side not in verified:
        raise Precondition(
            f"contestant {winner} has no passing signed verification receipt for its commit; the evidence decides",
            card=card_id,
        )
    loser = next(s for s in sides if s is not side)
    with board.lead_write(lead, f"pick duel winner on {card_id}"):
        pass
    _park(
        board,
        lead,
        card_id,
        "todo",
        f"duel: verifying selection {side['label']}",
        owners=tuple(s.get("agent") for s in sides),
    )
    grant = board.claim(card_id, lead, for_agent=side["agent"], checkout=side["clone"])
    worker = board.caller(agent=side["agent"], cap=grant["cap"], fence=grant["fence"], env={})
    board.move(card_id, "review", worker, note=f"duel candidate {side['label']} ({side['runtime']})")
    receipt = board.verify(card_id, lead, cwd=side["clone"])
    if not receipt["passed"]:
        _park(board, lead, card_id, "blocked", "duel final verification failed", owners=(side["agent"],))
        raise Precondition("duel final verification receipt failed", card=card_id)
    if loser.get("run_dir"):
        store.write_json(os.path.join(loser["run_dir"], "discarded.json"), {"discarded": True})
    for name in ("changes.patch", "lead.git", "repo"):
        path = os.path.join(loser.get("run_dir") or "", name)
        if loser.get("run_dir") and os.path.lexists(path):
            W._remove(path)
    if loser.get("clone"):
        shutil.rmtree(os.path.dirname(loser["clone"]), ignore_errors=True)
    record.update(status="decided", winner=side["label"], decided=clock.iso(), by=lead.name)
    loser["discarded"] = True
    _save(board, record)
    with board.lock():
        board._record(
            "status",
            lead.name,
            f"duel on {card_id} decided: {side['label']} ({side['runtime']}) wins",
            task=card_id,
            data={"event": "duel_decided", "winner": side["label"], "loser": loser["label"]},
        )
    return {
        "card": card_id,
        "winner": side["label"],
        "agent": side["agent"],
        "lane": board.card(card_id)["lane"],
        "verify": {"passed": receipt["passed"], "sha": receipt["sha"]},
    }


def _setup(p):
    p.add_argument("card", help="card id")
    p.add_argument("--vs", default=None, help="two runtimes, e.g. claude,codex")
    p.add_argument("--model", default=None, help="one model per contestant, e.g. m1,m2 (empty for default)")
    p.add_argument("--cmd", action="append", default=None, help="command for each shell contestant, in order")
    p.add_argument("--reviewer", default=None, help="runtime that reviews each contestant's patch")
    p.add_argument("--winner", default=None, help="pick the winner: a, b or the contestant's agent name")
    p.add_argument("--timeout", type=float, default=3600.0, help=argparse.SUPPRESS)
    p.add_argument("--heartbeat", type=float, default=60.0, help=argparse.SUPPRESS)


def _handler(ctx, a):
    board = ctx.board
    if a.winner:
        if a.vs:
            raise Precondition("--winner and --vs do not combine")
        r = pick(board, ctx.caller(), a.card, a.winner)
        return r, f"{a.card}: {r['winner']} wins; in review as {r['agent']}.\nNext: raidlock done {a.card}"
    if a.vs:
        models = a.model.split(",") if a.model else None
        r = run(board, ctx.caller(), a.card, a.vs, models, a.cmd, a.reviewer, a.timeout, a.heartbeat)
        nxt = f"raidlock duel {a.card} --winner a|b" if r["status"] == "decide" else f"raidlock show {a.card}"
        return r, f"duel on {a.card}: {r['status']}\n{table(r['rows'])}\nNext: {nxt}"
    record = load(board, a.card)
    if not record:
        raise Precondition(f"no duel on {a.card}", hint=f"raidlock duel {a.card} --vs claude,codex")
    rows = compare(record)
    return {**record, "rows": rows}, f"duel on {a.card}: {record['status']}\n{table(rows)}"


def register_cli(add_command):
    add_command(
        "duel", "run a card with two providers and pick the winner on verified evidence", _setup, _handler, True
    )
