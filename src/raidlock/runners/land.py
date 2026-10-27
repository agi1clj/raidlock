import json
import os
import re
import shlex
import subprocess

from ..core import exe, repos
from ..core.errors import Precondition, RaidlockError
from ..core.hints import lead_cmd
from ..scan import _parse
from . import policy
from . import workspace as W
from .runner import run_numbers, runs_dir


def landable_run(board, card_id):
    """(run number, run dir, BASE) of the newest worker run that handed back to review with a changes.patch."""
    for n in reversed(run_numbers(board, card_id)):
        folder = os.path.join(runs_dir(board, card_id), str(n))
        try:
            with open(os.path.join(folder, "run.json"), encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        if (
            info.get("role") == "worker"
            and info.get("lane") == "review"
            and os.path.isfile(os.path.join(folder, "changes.patch"))
        ):
            return n, folder, info.get("base") or ""
    return None, None, None


def _git(where, *args, env=None):
    return W.git_run(where, *args, env=env)


def _identity(where):
    """The lead's author and committer, read from its own git setup; the commit itself runs without its config."""
    env = {}
    for role in ("AUTHOR", "COMMITTER"):
        proc = subprocess.run(
            [exe.find("git"), "-C", where, "var", f"GIT_{role}_IDENT"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        match = re.match(r"(.*?) <([^<>]*)> ", proc.stdout)
        if proc.returncode == 0 and match:
            env[f"GIT_{role}_NAME"], env[f"GIT_{role}_EMAIL"] = match.groups()
    return env


def _subject(card):
    title = " ".join(str(card.get("title") or card["id"]).split())
    return f"feat({card['id']}): {title}"[:100]


def _check_paths(card, target, data):
    paths = sorted(_parse(data.decode("utf-8", "surrogateescape"))[0])
    found = policy.check(paths, target["rels"], target["ci"], card.get("allow_protected", ()))
    outside = set(found["outside"])
    if card.get("lock_exclusions"):
        excluded = policy.target(target["workspace"], target["board_dir"], card["lock_exclusions"])["rels"]
        outside |= {p for p in paths if policy.contained(p, excluded, target["ci"])}
    problems = []
    if outside:
        problems.append("outside the card's locks: " + ", ".join(sorted(outside)[:20]))
    if found["denied"]:
        problems.append("denied paths: " + ", ".join(found["denied"][:20]))
    return paths, problems


def _blocker(board, card_id):
    try:
        board._done_gate(board.card(card_id))
    except RaidlockError as exc:
        return exc.message + (f"; {exc.hint}" if exc.hint else "")
    return ""


def land(board, caller, card_id, cwd=None, message=None, timeout=600):
    with board.lead_write(caller, f"land {card_id}"):
        card = board.card(card_id)
    if card["lane"] in ("done", "cancelled", "todo"):
        raise Precondition(f"{card_id} is in {card['lane']}; only claimed work can be landed", card=card_id)
    run, run_dir, base = landable_run(board, card_id)
    if not run_dir:
        raise Precondition(
            f"no delegated run of {card_id} handed back a patch to land",
            card=card_id,
            hint=f"raidlock show {card_id}",
        )
    patch = os.path.join(run_dir, "changes.patch")
    where = repos.find_root(os.path.realpath(cwd or os.getcwd()))
    if not where:
        raise Precondition(f"{cwd or os.getcwd()} is not a git checkout", card=card_id)
    board._verification_repo(card, where)
    if W.git(where, "status", "--porcelain", "--untracked-files=all").strip():
        raise Precondition(
            f"{where} has uncommitted or untracked changes; land needs a clean checkout",
            card=card_id,
            hint=f"git -C {shlex.quote(where)} status",
        )
    before = W.head(where)
    if base and not W.is_ancestor(where, base, before):
        raise Precondition(
            f"HEAD of {where} does not descend from the run's base {base[:12]}; check out a branch that contains it",
            card=card_id,
        )
    target = policy.target(board.workspace, board.dir, card["locks"])
    target.update(workspace=board.workspace, board_dir=board.dir)
    with open(patch, "rb") as f:
        data = f.read()
    paths, problems = _check_paths(card, target, data)
    if problems:
        raise Precondition(f"refusing to land {card_id}: changes " + "; ".join(problems), card=card_id)
    checked = _git(where, "apply", "--check", "--index", "--whitespace=nowarn", patch)
    if checked.returncode != 0:
        raise Precondition(
            f"the patch from run {run} does not apply to {where}: {checked.stderr.strip()[:300]}",
            card=card_id,
        )
    applied = _git(where, "apply", "--index", "--whitespace=nowarn", patch)
    if applied.returncode != 0:
        _git(where, "reset", "-q", "--hard", before)
        raise Precondition(f"git apply failed: {applied.stderr.strip()[:300]}", card=card_id)
    committed = _git(
        where, *W.IDENT, "commit", "-q", "--no-verify", "-m", message or _subject(card), env=_identity(where)
    )
    if committed.returncode != 0:
        _git(where, "reset", "-q", "--hard", before)
        raise Precondition(
            f"git commit failed, nothing was committed: {(committed.stderr or committed.stdout).strip()[:300]}",
            card=card_id,
        )
    sha = W.head(where)
    verify = None
    if card.get("verify_cmd"):
        try:
            verify = board.verify(card_id, caller, timeout=timeout, cwd=where)
        except RaidlockError as exc:
            raise Precondition(
                f"committed {sha[:12]} in {where}, but verify could not run: {exc.message}",
                card=card_id,
                commit=sha,
                hint=exc.hint or lead_cmd(board, f"verify {card_id} --cwd {shlex.quote(where)}"),
            ) from exc
        outcome = f"verify {'passed' if verify['passed'] else 'failed'} (exit {verify['exit']})"
    else:
        outcome = "no verify command on the card"
    note = f"landed run {run} as {sha[:12]}; {outcome}"
    if board.card(card_id)["lane"] == "review":
        board.note(card_id, caller, note)
    else:
        board.move(card_id, "review", caller, note=note)
    if verify and not verify["passed"]:
        blocker = "verify failed; fix the work, commit, then " + lead_cmd(
            board, f"verify {card_id} --cwd {shlex.quote(where)}"
        )
    else:
        blocker = _blocker(board, card_id)
    nxt = blocker or lead_cmd(board, f"done {card_id}")
    return {
        "card": card_id,
        "run": run,
        "patch": patch,
        "repo": where,
        "commit": sha,
        "paths": paths,
        "verify": {k: verify[k] for k in ("passed", "exit", "tail")} if verify else None,
        "lane": "review",
        "blocked_by": blocker,
        "next": nxt,
        "_exit": 2 if verify and not verify["passed"] else 0,
    }
