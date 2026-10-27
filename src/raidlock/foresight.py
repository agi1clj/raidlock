"""Pre-claim and pre-commit checks; CLI extension: raidlock.foresight:register_cli."""

import os
import re
import subprocess
import tempfile
import time

from .core import repos
from .core.leases import is_live
from .runners.workspace import git_run

MAX_BRANCHES = 20
PAIR_TIMEOUT = 5.0


def _git(path, *args, env=None, deadline=None, check=True):
    timeout = PAIR_TIMEOUT if deadline is None else deadline - time.monotonic()
    if timeout <= 0:
        raise subprocess.TimeoutExpired(args, PAIR_TIMEOUT)
    result = git_run(path, *args, env=env, timeout=timeout)
    if check and result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result


def _identity(path):
    if repos.remote_url(path):
        return repos.identity(path)
    gdir = repos.git_dir(path)
    return "path:" + os.path.realpath(os.path.dirname(repos.common_dir(gdir))) if gdir else None


def _resolve(path, branch, **kwargs):
    if not branch:
        raise ValueError("lease has no recorded branch")
    ref = branch if branch.startswith("refs/heads/") else "refs/heads/" + branch
    result = _git(path, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}", check=False, **kwargs)
    if result.returncode:
        raise ValueError(f"branch {branch!r} missing locally")
    return result.stdout.strip()


def _snapshot(path, head, folder, include_worktree=True):
    env = os.environ.copy()
    objects = _git(path, "rev-parse", "--git-path", "objects").stdout.strip()
    objects = os.path.abspath(os.path.join(path, objects))
    target = os.path.join(folder, "objects")
    os.mkdir(target)
    env.update(
        GIT_INDEX_FILE=os.path.join(folder, "index"),
        GIT_OBJECT_DIRECTORY=target,
        GIT_ALTERNATE_OBJECT_DIRECTORIES=objects,
        GIT_AUTHOR_NAME="Raidlock",
        GIT_AUTHOR_EMAIL="raidlock@localhost",
        GIT_COMMITTER_NAME="Raidlock",
        GIT_COMMITTER_EMAIL="raidlock@localhost",
    )
    if not include_worktree:
        return head, env
    _git(path, "read-tree", head, env=env)
    _git(path, "add", "-A", "--", ".", env=env)
    tree = _git(path, "write-tree", env=env).stdout.strip()
    commit = _git(path, "commit-tree", tree, "-p", head, "-m", "Foresight snapshot", env=env).stdout.strip()
    return commit, env


def predict(board, card_id, checkout=None):
    """Predict against at most 20 live leases, without changing refs, index or board state.

    Uses the card's leased checkout, or the current checkout before claiming.
    Results contain conflicts, skipped comparisons, checked count and messages.
    Git older than 2.38 is skipped explicitly. Each pair has a five-second budget.
    """
    board.card(card_id)
    own = {} if checkout else board.leases().get(card_id, {})
    recorded = own.get("checkout") or {}
    path = checkout or recorded.get("path")
    if not path:
        current = repos.checkout_info(os.getcwd())
        path = (current or {}).get("path") or board.workspace
    result = {"card": card_id, "conflicts": [], "skipped": [], "checked": 0, "messages": []}
    try:
        version = _git(path, "--version").stdout
        match = re.search(r"(\d+)\.(\d+)", version)
        if not match or tuple(map(int, match.groups())) < (2, 38):
            result["messages"].append("Foresight skipped: git >= 2.38 is required for merge-tree --write-tree.")
            return result
        ident = _identity(path)
        if ident is None:
            raise ValueError(f"{path} is not a git repository")
        head = _git(path, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
        branch = own.get("branch") or recorded.get("branch")
        source = _resolve(path, branch) if branch else head
        candidates = []
        for workspace in board.index.workspaces():
            for lease in board.index.leases(workspace["uid"]).values():
                if workspace["uid"] == board.uid and lease["card"] == card_id:
                    continue
                if not is_live(lease):
                    continue
                other_path = (lease.get("checkout") or {}).get("path")
                ids = {key["id"] for key in lease.get("keys", [])}
                if ident not in ids and (not other_path or _identity(other_path) != ident):
                    continue
                candidates.append({**lease, "workspace": workspace["name"]})
        if len(candidates) > MAX_BRANCHES:
            result["messages"].append(
                f"Limited to {MAX_BRANCHES} other branches; {len(candidates) - MAX_BRANCHES} leases skipped."
            )
        with tempfile.TemporaryDirectory(prefix="raidlock-foresight-") as folder:
            env = None
            if candidates:
                source, env = _snapshot(path, source, folder, include_worktree=source == head)
            for lease in candidates[:MAX_BRANCHES]:
                detail = {
                    "agent": lease["agent"],
                    "card": lease["card"],
                    "branch": lease.get("branch", ""),
                    "workspace": lease["workspace"],
                }
                deadline = time.monotonic() + PAIR_TIMEOUT
                try:
                    other = _resolve(path, detail["branch"], env=env, deadline=deadline)
                    merged = _git(
                        path,
                        "merge-tree",
                        "--write-tree",
                        "--name-only",
                        "-z",
                        source,
                        other,
                        env=env,
                        deadline=deadline,
                        check=False,
                    )
                    if merged.returncode not in (0, 1):
                        raise ValueError(merged.stderr.strip() or "merge-tree failed")
                    result["checked"] += 1
                    if merged.returncode == 1:
                        fields = merged.stdout.split("\0")[1:]
                        files = fields[: fields.index("")] if "" in fields else fields
                        if not files:
                            raise ValueError("merge-tree reported a conflict without file names")
                        result["conflicts"].append(
                            {
                                **detail,
                                "files": sorted(set(files)),
                                "suggestion": f"Wait for card {lease['card']}, or coordinate via "
                                f"raidlock msg --to {lease['agent']}.",
                            }
                        )
                except subprocess.TimeoutExpired:
                    result["skipped"].append({**detail, "message": "Comparison skipped: five-second timeout."})
                except (OSError, ValueError) as exc:
                    result["skipped"].append({**detail, "message": f"Comparison skipped: {exc}"})
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result["messages"].append(f"Foresight skipped: {exc}")
    return result


def _setup(parser):
    parser.add_argument("card")


def warnings(result):
    lines = [
        f"Warning: Foresight: {', '.join(item['files'])}: conflict with {item['agent']} on card {item['card']}. "
        f"{item['suggestion']}"
        for item in result["conflicts"]
    ]
    lines.extend(
        f"Warning: Foresight: {item['agent']} / {item['card']}: {item['message']}" for item in result["skipped"]
    )
    lines.extend(f"Warning: {message}" for message in result["messages"])
    return lines


def pre_claim(board, card_id, checkout=None):
    return warnings(predict(board, card_id, checkout=checkout))


def _handler(ctx, args):
    result = predict(ctx.board, args.card)
    lines = warnings(result)
    text = "\n".join(lines) or f"No conflicts predicted ({result['checked']} comparisons)."
    return {**result, "warnings": lines, "_exit": 0}, text


def register_cli(add_command):
    add_command("foresight", "predict merge conflicts before claiming or committing", _setup, _handler)
