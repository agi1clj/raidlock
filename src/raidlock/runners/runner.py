import contextlib
import json
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path

from ..budgets import check_before_delegate
from ..core import clock, db
from ..core import identity as I
from ..core import model as model_mod
from ..core import transcript as T
from ..core.brief import UNTRUSTED_RULE, quote_data
from ..core.errors import Denied, IdentityError, Precondition, RaidlockError
from ..core.identity import RUNTIME_SHORT
from ..core.lead_env import assert_worker_env, scrub_worker_env
from ..core.store import atomic_write_text
from ..lore import capture, mark_used
from ..scan import scan_patch
from . import brief, isolation, policy
from . import workspace as W
from .base import Collector, Process, Redactor, short
from .claude_code import ClaudeCode
from .codex import Codex
from .gemini import Gemini
from .shell import Shell

ADAPTERS = {"codex": Codex, "claude": ClaudeCode, "gemini": Gemini, "shell": Shell}
ROLES = ("worker", "reviewer")
SANDBOXES = ("read-only", "workspace-write")
MAX_RELAYS = 40
VERIFY_TIMEOUT = 900
NOTE_CAP = 1800
MODEL_ID = re.compile(r"^[A-Za-z0-9._:/@-]{1,100}$")


class Run:
    def __init__(self, **fields):
        self.__dict__.update(fields)


def agent_name(adapter, role, card_id):
    return f"{RUNTIME_SHORT[adapter.runtime]}-{role}-{card_id}"[:40].rstrip("-")


def runs_dir(board, card_id):
    return os.path.join(board.home, "runs", board.name, card_id)


def _unknown_option(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            return "unknown option" in stream.read()[-4000:].lower()
    except OSError:
        return False


def prune_runs(board, card_id):
    """Delete the clones and agent homes of a closed card's runs; logs, patches, run records and an opted-in
    quarantine stay."""
    root = runs_dir(board, card_id)
    removed = 0
    for run in sorted(os.listdir(root)) if os.path.isdir(root) and not os.path.islink(root) else []:
        path = os.path.join(root, run)
        if os.path.islink(path) or not os.path.isdir(path):
            continue
        if os.path.exists(os.path.join(path, "started.json")) and not os.path.exists(os.path.join(path, "run.json")):
            continue
        for name in sorted(os.listdir(path)):
            child = os.path.join(path, name)
            if name != "quarantine" and (os.path.isdir(child) or os.path.islink(child)):
                W._remove(child)
                removed += 1
    return removed


def run_numbers(board, card_id):
    root = runs_dir(board, card_id)
    names = os.listdir(root) if os.path.isdir(root) else []
    return sorted(int(n) for n in names if n.isdigit())


def latest_patch(board, card_id):
    """(run dir, BASE) of the newest worker run that produced an accepted changes.patch, else (None, None)."""
    for n in reversed(run_numbers(board, card_id)):
        folder = os.path.join(runs_dir(board, card_id), str(n))
        try:
            with open(os.path.join(folder, "run.json"), encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        if info.get("role") == "worker" and os.path.exists(os.path.join(folder, "changes.patch")):
            return folder, info.get("base") or ""
    return None, None


class Relay:
    """Summarized transcript records (agent messages, command lines), appended in batches under the board lock."""

    def __init__(self, board, agent, card_id, limit=MAX_RELAYS):
        self.board, self.agent, self.card, self.limit = board, agent, card_id, limit
        self.pending, self.sent, self.dropped = [], 0, 0
        self._lock = threading.Lock()

    def add(self, event):
        kind = event.get("kind")
        if kind == "message" and event.get("text") and not event.get("delta"):
            text, data = short(event["text"]), {"event": "message"}
        elif kind == "command":
            code = event.get("exit")
            text = "$ " + event.get("command", "") + (f" (exit {code})" if code is not None else "")
            data = {"event": "command", "exit": code}
        else:
            return
        with self._lock:
            if self.sent + len(self.pending) >= self.limit:
                self.dropped += 1
                return
            self.pending.append(
                T.make(
                    "relay",
                    self.board._from(self.agent),
                    text=text,
                    task=self.card,
                    context=f"{self.board.name}/{self.card}",
                    src="runner",
                    data=data,
                )
            )

    def flush(self):
        with self._lock:
            rows, self.pending = self.pending, []
        if not rows:
            return
        with self.board.lock():
            for row in rows:
                self.board.transcript.append(row)
        self.sent += len(rows)


class Heartbeat(threading.Thread):
    """Renews the lease on a timer and flushes relays; stops the agent if the lease is lost."""

    def __init__(self, board, card_id, caller, interval, relay, on_lost):
        super().__init__(daemon=True)
        self.board, self.card, self.caller, self.interval = board, card_id, caller, interval
        self.relay, self.on_lost = relay, on_lost
        self.done = threading.Event()
        self.lost = ""
        self.beats = 0

    def run(self):
        with db.released():
            self._beat()

    def _beat(self):
        while not self.done.wait(self.interval):
            with contextlib.suppress(RaidlockError):
                self.relay.flush()
            if self.caller is None:
                continue
            try:
                self.board.heartbeat(self.card, self.caller)
                self.beats += 1
            except (Denied, IdentityError) as e:
                self.lost = e.message
                self.on_lost("lease lost: " + e.message)
                return
            except RaidlockError:
                continue

    def stop(self):
        self.done.set()
        if self.is_alive():
            self.join(timeout=30)


def preflight(board, card, name):
    """Refuse early, before joining or cloning, when the card cannot be claimed for `name`."""
    cid, lane = card["id"], card["lane"]
    if lane in ("done", "review"):
        raise Precondition(
            f"{cid} is in {lane}; a worker cannot take it",
            card=cid,
            hint=f"raidlock delegate {cid} --to <runtime> --as reviewer" if lane == "review" else "",
        )
    if card["owner"] and card["owner"] != name and lane != "todo":
        raise Denied(
            f"{cid} is owned by {card['owner']}",
            card=cid,
            owner=card["owner"],
            hint=f"raidlock release {cid}   (lead), then delegate again",
        )
    info = board.claimable().get(cid)
    if info is None:
        raise Denied(f"{cid} is not claimable now", card=cid, hint="raidlock claimable")
    if not info["ok"]:
        raise Precondition(
            f"cannot delegate {cid}: " + "; ".join(info["reasons"]),
            card=cid,
            reasons=info["reasons"],
            hint="raidlock claimable",
        )


def _review_preflight(board, card):
    cid = card["id"]
    if card["lane"] != "review":
        raise Precondition(f"{cid} is in {card['lane']}, not review", card=cid)
    checkout = ((board.leases().get(cid) or {}).get("checkout") or {}).get("path")
    if not latest_patch(board, cid)[0] and not (checkout and os.path.isdir(checkout)):
        raise Precondition(f"nothing to review for {cid}: no runner patch and no recorded checkout", card=cid)


def prepare(board, card_id):
    """A fresh numbered run folder: $RAIDLOCK_HOME/runs/<workspace>/<card>/<n>/ (outside the workspace)."""
    numbers = run_numbers(board, card_id)
    path = os.path.join(runs_dir(board, card_id), str((numbers[-1] if numbers else 0) + 1))
    os.makedirs(path, mode=0o700)
    return path


def _note(text):
    text = " ".join(str(text).split())
    return text if len(text) <= NOTE_CAP else text[: NOTE_CAP - 3] + "..."


def _rel(board, path):
    home = os.path.expanduser("~")
    return "~" + path[len(home) :] if path.startswith(home + os.sep) else path


def _stored_token(board, name):
    try:
        with open(I.token_path(board.home, board.uid, name), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _write_json(path, data, redactor=None):
    data = json.loads(json.dumps(data, ensure_ascii=False, default=str))
    data = (redactor or Redactor())(data)
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n")


def _scrub_file(path, redactor):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return
    clean = redactor.text(text)
    if clean != text:
        atomic_write_text(path, clean)


def delegate(
    board,
    lead,
    card_id,
    to,
    role="worker",
    sandbox=None,
    model=None,
    cmd=None,
    timeout=3600.0,
    heartbeat=60.0,
    env=None,
    tools=None,
    unsafe_verify=False,
    raw=False,
    agent=None,
    resume=None,
    ref=None,
):
    """Run one card through an external CLI as `<runtime>-<role>-<card>` (or `agent`) and hand it back. `resume` is a
    resume packet whose validated patch is applied to the fresh clone and whose text joins the prompt as quoted data.
    Returns a result dict."""
    env = dict(os.environ if env is None else env)
    if not lead.is_lead:
        raise IdentityError(
            "delegate runs as the lead: it joins and claims on the agent's behalf",
            agent=lead.name,
            hint="run raidlock delegate from the lead's session",
        )
    if to not in ADAPTERS:
        raise Precondition(f"--to must be one of {', '.join(ADAPTERS)}", to=to)
    if role not in ROLES:
        raise Precondition(f"--as must be one of {', '.join(ROLES)}", role=role)
    sandbox = sandbox or ("read-only" if role == "reviewer" else "workspace-write")
    if sandbox not in SANDBOXES:
        raise Precondition(f"--sandbox must be one of {', '.join(SANDBOXES)}", sandbox=sandbox)
    if role == "reviewer" and sandbox != "read-only":
        raise Precondition("reviewers run read-only", sandbox=sandbox)
    if model and not MODEL_ID.match(model):
        raise Precondition(f"invalid model id {model!r}", model=model)
    if not math.isfinite(float(timeout)) or float(timeout) <= 0:
        raise Precondition("worker timeout must be a positive finite number of seconds")
    adapter = ADAPTERS[to]()
    card = board.card(card_id)
    budget = check_before_delegate(board, card)
    if not budget["allowed"]:
        raise Precondition("delegation budget exhausted: " + "; ".join(budget["reasons"]), card=card_id)
    target = policy.target(board.workspace, board.dir, card["locks"])
    name = agent_name(adapter, role, card["id"]) if agent is None else model_mod.agent_name(agent)
    if adapter.name == "shell":
        adapter.command(Run(cmd=cmd, parent_env=env))
    if tools is not None:
        if adapter.name != "claude":
            raise Precondition("--tools applies to --to claude only", to=to)
        tools = adapter.check_tools(tools, sandbox)
    if role == "worker":
        preflight(board, card, name)
    else:
        _review_preflight(board, card)
    run_dir = prepare(board, card["id"])
    run = Run(
        card=card,
        role=role,
        agent=name,
        timeout=float(timeout),
        heartbeat=float(heartbeat),
        sandbox=sandbox,
        model=model or "",
        cmd=cmd,
        run_dir=run_dir,
        tools=tools,
        clone=os.path.join(run_dir, "repo"),
        schema=brief.schema(role),
        parent_env=env,
        rels=target["rels"],
        ci=target["ci"],
        repo=target["repo"],
        run_id=uuid.uuid4().hex[:12],
        schema_path=os.path.join(run_dir, "schema.json"),
        last_path=os.path.join(run_dir, "last.md"),
        prompt_path=os.path.join(run_dir, "prompt.md"),
        events_path=os.path.join(run_dir, "events.jsonl"),
        stderr_path=os.path.join(run_dir, "stderr.log"),
        unsafe_verify=bool(unsafe_verify),
        raw=bool(raw),
        resume=resume,
        ref=ref,
    )
    joined = board.join(
        lead,
        agent=name,
        team=(card.get("team") or None) if role == "worker" else None,
        role=role,
        runtime=adapter.runtime,
        provider=adapter.provider,
        flag_model=model,
        session=run.run_id,
        card=card["id"],
    )
    run.token = joined["token"]
    from ..resume import _secrets

    run.redactor = Redactor.for_env(env, [run.token, *_secrets(board, lead, card)])
    run.billing = joined.get("billing") or "unknown"
    if role == "worker":
        return _work(board, lead, adapter, run, target)
    if name not in (card.get("reviewers") or []):
        board.assign_reviewers(card["id"], lead, list(card.get("reviewers") or []) + [name])
    return _review(board, adapter, run, target)


def _start(board, adapter, run, rels_base):
    _write_json(run.schema_path, run.schema)
    capture(board)
    run.brief = board.brief(run.card["id"])
    prompt = brief.render(run.brief["text"], run.role, run.rels, rels_base)
    if run.card.get("lock_exclusions"):
        prompt += "\nExcluded lock paths (do not change): " + ", ".join(run.card["lock_exclusions"]) + "\n"
    if getattr(run, "resume", None):
        prompt += _resume_section(run.resume)
    atomic_write_text(run.prompt_path, prompt)
    run.prompt = prompt
    child_env = adapter.child_env(run.parent_env)
    child_env.update(adapter.prepare(run))
    child_env = scrub_worker_env(child_env)
    child_env["RAIDLOCK_AGENT_ID"] = run.agent
    if getattr(run, "cap", None):
        child_env["RAIDLOCK_CAP"] = run.cap
    else:
        child_env["RAIDLOCK_TOKEN"] = run.token
    assert_worker_env(child_env)
    run.argv = adapter.command(run)
    return child_env


def _execute(board, adapter, run, caller, child_env, timeout, interval):
    collected = Collector()
    relay = Relay(board, run.agent, run.card["id"])
    proc = Process(
        run.argv,
        run.clone,
        child_env,
        run.prompt,
        run.events_path,
        run.stderr_path,
        timeout,
        redactor=run.redactor,
        raw=run.raw,
    )

    def on_event(event):
        collected.add(event)
        relay.add(event)

    beat = Heartbeat(board, run.card["id"], caller, interval, relay, proc.stop)
    beat.start()
    start = time.monotonic()
    try:
        code, killed = proc.run(adapter, on_event)
    finally:
        beat.stop()
        with contextlib.suppress(RaidlockError):
            relay.flush()
    run.duration_ms = int((time.monotonic() - start) * 1000)
    if proc.proc is not None and run.brief.get("lore_ids"):
        mark_used(board, run.brief.get("lore_ids", []))
    _scrub_file(run.last_path, run.redactor)
    run.exit, run.killed, run.lost = code, killed, beat.lost
    run.relays = {"sent": relay.sent, "dropped": relay.dropped}
    if collected.billing:
        run.billing = collected.billing
    if killed:
        run.failure = killed
    elif code != 0:
        run.failure = f"agent exited {code}" + (f": {collected.error}" if collected.error else "")
        if _unknown_option(run.stderr_path):
            run.failure += f"; the {adapter.binary} CLI rejected an option, so it is likely too old: update it"
    elif collected.error:
        run.failure = "agent reported an error: " + collected.error
    else:
        run.failure = ""
    return collected


def _cost(board, caller, run, collected):
    if not collected.usage.total and collected.usd is None:
        return None
    return board.record_cost(
        run.card["id"],
        caller,
        model_id=collected.model or run.model or None,
        usage=collected.usage,
        usd=collected.usd,
        duration_ms=run.duration_ms,
        billing=run.billing,
    )["entry"]


def _work(board, lead, adapter, run, target):
    cid = run.card["id"]
    run.source_dirty = W.dirty(target["path"])
    run.base, run.branch = W.clone(target["path"], run.clone, ref=run.ref)
    run.source = target["path"]
    if run.resume:
        with board.lock():
            current = board.card(cid)
            target = policy.target(board.workspace, board.dir, current["locks"])
            run.rels, run.ci = target["rels"], target["ci"]
            _apply_resume(run)
    _write_json(
        os.path.join(run.run_dir, "started.json"),
        {
            "run_id": run.run_id,
            "card": cid,
            "agent": run.agent,
            "role": "worker",
            "base": run.base,
            "branch": run.branch,
        },
    )
    config = W.snapshot_config(run.clone)
    board.trust_checkout(lead, cid, run.clone, target["path"])
    grant = board.claim(cid, lead, for_agent=run.agent, checkout=run.clone, runner_base=run.base)
    run.cap = grant["cap"]
    run.redactor = Redactor([*run.redactor.secrets, run.cap])
    worker = board.caller(agent=run.agent, cap=run.cap, fence=grant["fence"], env={})
    try:
        child_env = _start(board, adapter, run, run.base)
        board.note(
            cid,
            worker,
            _note(
                f"runner: {adapter.runtime} · {adapter.provider} · model {run.model or 'default'} · "
                f"billing {run.billing} · sandbox {run.sandbox} · clone "
                f"{_rel(board, run.clone)} at {run.base[:12]}"
            ),
        )
        collected = _execute(board, adapter, run, worker, child_env, run.timeout, run.heartbeat)
    except BaseException as e:
        _abandon(board, worker, cid, f"runner error before the agent finished: {e}")
        raise
    if run.lost:
        _write_json(
            os.path.join(run.run_dir, "run.json"), _summary(run, adapter, None, None, None, None, []), run.redactor
        )
        return {
            "card": cid,
            "agent": run.agent,
            "lane": board.card(cid)["lane"],
            "status": "lost",
            "reason": run.lost,
            "run_dir": run.run_dir,
            "_exit": 2,
        }
    try:
        return _finish(board, lead, adapter, run, worker, collected, config)
    except BaseException as e:
        _abandon(board, worker, cid, f"runner error after the agent finished: {e}")
        raise


def _resume_section(packet):
    lines = (packet.get("text") or "").splitlines()
    return (
        "\n## Resume packet (an earlier attempt on this card stopped; its validated work is already applied)\n"
        + UNTRUSTED_RULE
        + "\n"
        + "\n".join(quote_data(line) for line in lines)
        + "\n"
    )


def _apply_resume(run):
    """Put the interrupted attempt's validated patch into the fresh clone, uncommitted, at its recorded BASE."""
    patch = run.resume.get("patch") or ""
    if not patch:
        return
    if not os.path.isfile(patch) or os.path.islink(patch):
        raise Precondition("the resume patch is missing", card=run.card["id"])
    with open(patch, "rb") as f:
        data = f.read()
    from ..resume import validate_patch

    _, problems = validate_patch(data, run.rels, run.ci, run.redactor)
    if problems:
        raise Precondition("the resume patch failed validation; it is never applied", card=run.card["id"])
    base = run.resume.get("base") or ""
    if base and base != run.base:
        W.git(run.clone, "checkout", "--quiet", "--detach", base)
        run.base = base
    W.git(run.clone, "apply", "--whitespace=nowarn", "-", binary=True, input=data)


def _abandon(board, worker, cid, text):
    try:
        if board.card(cid)["lane"] == "doing":
            board.move(cid, "blocked", worker, note=_note(text))
    except RaidlockError:
        pass


def _dispose_sensitive(run, patch_path, store):
    artifacts = [patch_path, store, run.clone]
    if run.raw:
        quarantine = os.path.join(run.run_dir, "quarantine")
        os.mkdir(quarantine, 0o700)
        os.chmod(quarantine, 0o700)
        for artifact in artifacts:
            os.replace(artifact, os.path.join(quarantine, os.path.basename(artifact)))
    else:
        for artifact in artifacts:
            W._remove(artifact)


def _finish(board, lead, adapter, run, worker, collected, config):
    cid = run.card["id"]
    W.restore(run.clone, config)
    store = os.path.join(run.run_dir, "lead.git")
    paths, tree = W.audit(run.source, run.base, run.clone, store)
    current = board.card(cid)
    target = policy.target(board.workspace, board.dir, current["locks"])
    found = policy.check(paths, target["rels"], run.ci, current.get("allow_protected", ()))
    excluded = []
    if current.get("lock_exclusions"):
        excluded = policy.target(board.workspace, board.dir, current["lock_exclusions"])["rels"]
        found["outside"] = sorted(set(found["outside"]) | {p for p in paths if policy.contained(p, excluded, run.ci)})
    from ..scan import clone_links

    found["denied"] = sorted(
        set(found["denied"])
        | set(
            policy.check_links(
                clone_links(Path(run.clone)),
                target["rels"],
                run.ci,
                paths=paths,
                allow_protected=current.get("allow_protected", ()),
                excluded=excluded,
            )
        )
    )
    violation = bool(found["outside"] or found["denied"])
    patch_path = os.path.join(run.run_dir, "rejected.patch" if violation else "changes.patch")
    patch_bytes = W.write_patch(store, run.base, tree, patch_path) if paths else 0
    findings, scan_error = [], ""
    if paths:
        try:
            with open(patch_path, "rb") as patch:
                findings = scan_patch(
                    patch.read(), rels=target["rels"], ci=run.ci, allow_protected=current.get("allow_protected", ())
                )
        except RaidlockError as exc:
            scan_error = exc.message
        if findings or scan_error:
            violation = True
            _dispose_sensitive(run, patch_path, store)
            findings = run.redactor(findings)
            patch_path, patch_bytes = "", 0
    found["scan"] = findings
    if not paths and os.path.exists(patch_path):
        os.remove(patch_path)
    verify, skipped = None, ""
    if run.card.get("verify_cmd") and not violation and not run.failure:
        verify, skipped = _verify(board, lead, run)
    agent_hb = brief.handback(adapter.final(run, collected), fallback_text=collected.final)
    lane, reason = "review", ""
    if run.failure:
        lane, reason = "blocked", run.failure
    elif violation:
        parts = []
        if found["outside"]:
            parts.append("outside the card's locks: " + ", ".join(found["outside"][:20]))
        if found["denied"]:
            parts.append("denied paths: " + ", ".join(found["denied"][:20]))
        if findings:
            parts.append("patch safety findings: " + ", ".join(sorted({item["rule"] for item in findings})))
        if scan_error:
            parts.append("patch safety scan failed: " + scan_error)
        lane, reason = "blocked", "changes " + "; ".join(parts)
    elif verify and not verify["passed"]:
        kind = verify["isolation"] if verify["isolation"] in ("sandbox-exec", "bwrap") else None
        detail = isolation.failure_reason(verify["exit"], kind)
        lane, reason = "blocked", detail or f"verify failed (exit {verify['exit']}): {verify['cmd']}"
    elif agent_hb["status"] == "blocked":
        lane, reason = "blocked", "the agent reported blocked"
    evidence = list(agent_hb["evidence"])
    evidence.append(
        f"runner: {len(paths)} path(s) changed against {run.base[:12]}"
        + (f"; patch {_rel(board, patch_path)} ({patch_bytes} bytes)" if patch_path and paths else "")
    )
    if verify:
        evidence.append(
            f"runner verify: `{verify['cmd']}` exit {verify['exit']} in {verify['duration_ms']} ms"
            f" ({verify['isolation']})"
        )
    elif skipped:
        evidence.append("runner verify: not run, " + skipped)
    elif run.card.get("verify"):
        evidence.append("runner verify: no verify command on the card; check by hand: " + run.card["verify"][:200])
    status = agent_hb["status"] if lane == "review" else "blocked"
    questions = list(agent_hb["open_questions"]) + ([reason] if reason else [])
    handback = brief.cap({"status": status, "evidence": evidence, "paths_changed": paths, "open_questions": questions})
    _write_json(os.path.join(run.run_dir, "handback.json"), handback, run.redactor)
    cost = _cost(board, worker, run, collected)
    if verify:
        with board.lock():
            board.transcript.append(
                T.make(
                    "artifact",
                    board._from(run.agent),
                    task=cid,
                    context=f"{board.name}/{cid}",
                    text=f"runner verify {'passed' if verify['passed'] else 'failed'}: {verify['cmd']}",
                    src="runner",
                    artifacts=[
                        T.artifact(
                            "runner-verify",
                            verify["tail"],
                            data={"exit": verify["exit"], "passed": verify["passed"]},
                            kind="verify",
                        )
                    ],
                )
            )
    patch_meta = {
        "path": _rel(board, patch_path) if patch_path and paths else "",
        "bytes": patch_bytes,
        "base": run.base,
        "files": len(paths),
        "rejected": violation,
    }
    with board.lock():
        board.transcript.append(
            T.make(
                "handback",
                board._from(run.agent),
                task=cid,
                context=f"{board.name}/{cid}",
                src="runner",
                text=f"{status}: {len(paths)} path(s) changed" + (f"; {reason}" if reason else ""),
                artifacts=[
                    T.artifact("handback", data=handback, kind="handback"),
                    T.artifact("rejected.patch" if violation else "changes.patch", data=patch_meta, kind="patch"),
                ],
                data={
                    "billing": run.billing,
                    "sandbox": run.sandbox,
                    "runtime": adapter.runtime,
                    "model": collected.model or run.model,
                    "usd": collected.usd,
                },
            )
        )
    summary = f"{len(paths)} path(s) changed" + (
        f", verify {'passed' if verify['passed'] else 'failed'}" if verify else ""
    )
    patch_note = f"; patch {_rel(board, patch_path)}" if patch_path and paths else ""
    board.move(cid, lane, worker, note=_note(reason or summary + patch_note))
    capture(board)
    _write_json(
        os.path.join(run.run_dir, "run.json"),
        _summary(
            run,
            adapter,
            collected,
            found,
            verify,
            cost,
            paths,
            lane=lane,
            reason=reason,
            patch=patch_meta,
            verify_skipped=skipped,
        ),
        run.redactor,
    )
    return {
        "card": cid,
        "agent": run.agent,
        "role": "worker",
        "runtime": adapter.runtime,
        "model": collected.model or run.model,
        "billing": run.billing,
        "lane": lane,
        "status": status,
        "reason": reason,
        "paths_changed": paths,
        "outside": found["outside"],
        "denied": found["denied"],
        "patch": patch_path if paths else "",
        "verify": verify,
        "verify_skipped": skipped,
        "usage": collected.usage.as_dict(),
        "usd": collected.usd,
        "run_dir": run.run_dir,
        "handback": handback,
        "_exit": 0 if lane == "review" else 2,
    }


def _verify(board, lead, run):
    """Run the card's verify command in the worker clone without the lead's env, HOME or board state, inside a
    sandbox when the host has one. Without one it runs only with unsafe_verify. Returns (verify, skipped)."""
    with board.lead_write(lead, "start runner verification"):
        pass
    kind = isolation.mode()
    if kind is None and not run.unsafe_verify:
        return None, (
            f"no sandbox on this host for automatic verify; run `raidlock verify {run.card['id']} "
            f"--cwd {_rel(board, run.clone)}` by hand or delegate with --unsafe-verify"
        )
    cmd = run.card["verify_cmd"]
    deny = isolation.hidden(run.parent_env, [board.home, board.dir])
    code, tail, ms = isolation.run(
        cmd,
        run.clone,
        run.parent_env,
        VERIFY_TIMEOUT,
        kind=kind,
        agent=run.agent,
        deny_read=deny,
        redact=run.redactor.text,
    )
    label = kind or "unsafe: scrubbed env, no sandbox"
    rec = {
        "ts": clock.iso(),
        "by": lead.name,
        "cmd": cmd,
        "cwd": run.clone,
        "exit": code,
        "passed": code == 0,
        "tail": tail,
        "duration_ms": ms,
        "rev": run.card.get("rev", 0),
        "isolation": label,
    }
    with board.lead_write(lead, "record runner verification"):
        card = board.card(run.card["id"])
        card.setdefault("verifications", []).append(rec)
        board._note(card, lead.name, f"verify {'passed' if rec['passed'] else 'failed'} (exit {code}; {label})")
        board._save_card(card)
        board._record(
            "verify",
            lead.name,
            f"verify {'passed' if rec['passed'] else 'failed'}: {cmd}",
            task=card["id"],
            artifacts=[T.artifact("verify", tail, data={"exit": code, "passed": rec["passed"]}, kind="verify")],
        )
    return {k: rec[k] for k in ("cmd", "exit", "passed", "tail", "duration_ms", "isolation")}, ""


def _summary(run, adapter, collected, found, verify, cost, paths, **extra):
    return {
        "run_id": run.run_id,
        "card": run.card["id"],
        "agent": run.agent,
        "role": run.role,
        "runtime": adapter.runtime,
        "provider": adapter.provider,
        "model": (collected.model if collected else "") or run.model,
        "billing": run.billing,
        "sandbox": run.sandbox,
        "repo": run.repo,
        "base": run.base,
        "branch": getattr(run, "branch", ""),
        "source_dirty": getattr(run, "source_dirty", False),
        "argv": run.argv,
        "brief_tokens": run.brief["tokens"],
        "exit": run.exit,
        "failure": run.failure,
        "lost": run.lost,
        "lore_tokens": run.brief.get("lore_tokens", 0),
        "duration_ms": run.duration_ms,
        "relays": run.relays,
        "events": collected.summary() if collected else None,
        "paths": paths,
        "check": found,
        "verify": verify,
        "cost": cost,
        "finished": clock.iso(),
        **extra,
    }


def _review(board, adapter, run, target):
    cid = run.card["id"]
    reviewer = board.caller(agent=run.agent, token=run.token, env={})
    worker_dir, worker_base = latest_patch(board, cid)
    run.base, run.branch = W.clone(target["path"], run.clone, ref=run.ref)
    run.reviewed = "patch" if worker_dir else "checkout"
    if run.reviewed == "patch":
        patch = os.path.join(worker_dir, "changes.patch")
        run.patch_from = worker_dir
        if worker_base and worker_base != run.base:
            W.git(run.clone, "checkout", "--quiet", "--detach", worker_base)
            run.base = worker_base
        W.apply_and_commit(run.clone, patch)
    else:
        checkout = ((board.leases().get(cid) or {}).get("checkout") or {}).get("path")
        if checkout and os.path.isdir(checkout):
            src_base = run.base
            run.base, run.branch = W.clone(checkout, run.clone)
            merge = W.git(run.clone, "merge-base", "HEAD", src_base, check=False).strip()
            run.base = merge or run.base
    child_env = _start(board, adapter, run, run.base)
    collected = _execute(board, adapter, run, None, child_env, run.timeout, run.heartbeat)
    if run.failure:
        _write_json(
            os.path.join(run.run_dir, "run.json"), _summary(run, adapter, collected, None, None, None, []), run.redactor
        )
        _cost(board, reviewer, run, collected)
        return {
            "card": cid,
            "agent": run.agent,
            "role": "reviewer",
            "status": "failed",
            "reason": run.failure,
            "run_dir": run.run_dir,
            "_exit": 2,
        }
    result = brief.review(adapter.final(run, collected), fallback_text=collected.final)
    result = brief.cap(result)
    _write_json(os.path.join(run.run_dir, "review.json"), result, run.redactor)
    cost = _cost(board, reviewer, run, collected)
    done = board.review(cid, reviewer, result["verdict"], note=_note(result["summary"]))
    with board.lock():
        board.transcript.append(
            T.make(
                "artifact",
                board._from(run.agent),
                task=cid,
                context=f"{board.name}/{cid}",
                src="runner",
                text=f"review {result['verdict']}: {short(result['summary'], 300)}",
                artifacts=[T.artifact("review", data=result, kind="review")],
                data={"billing": run.billing, "reviewed": run.reviewed, "base": run.base},
            )
        )
    _write_json(
        os.path.join(run.run_dir, "run.json"),
        _summary(run, adapter, collected, None, None, cost, [], verdict=result["verdict"], reviewed=run.reviewed),
        run.redactor,
    )
    return {
        "card": cid,
        "agent": run.agent,
        "role": "reviewer",
        "runtime": adapter.runtime,
        "model": collected.model or run.model,
        "billing": run.billing,
        "verdict": result["verdict"],
        "summary": result["summary"],
        "findings": result["findings"],
        "lane": done["lane"],
        "approvals": done["approvals"],
        "usage": collected.usage.as_dict(),
        "usd": collected.usd,
        "run_dir": run.run_dir,
        "_exit": 0,
    }
