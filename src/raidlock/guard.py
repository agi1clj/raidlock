"""Git lease guards; run with python -m raidlock.guard.

register_cli is available to CLI extension loaders. The built-in loader does
not discover this module without a raidlock.commands entry point.
"""

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .core import Board, canonical, exe
from .core.errors import Denied, IdentityError, StaleLead
from .runners.workspace import git_run

HOOKS = ("pre-commit", "pre-push")
MARKER = ": 'raidlock guard hook v1'"


def git(*args):
    proc = git_run(os.getcwd(), *args, binary=True)
    proc.check_returncode()
    return proc.stdout


def root():
    return Path(os.fsdecode(git("rev-parse", "--show-toplevel")).strip())


def hook_dir():
    from .core.repos import common_dir, git_dir

    repo = root()
    common = common_dir(git_dir(str(repo)))
    configured = git_run(
        repo, "config", "--file", os.path.join(common, "config"), "--no-includes", "--get", "core.hooksPath"
    ).stdout.strip()
    return repo / configured if configured else Path(common, "hooks")


def backup(path):
    return path.with_name(path.name + ".raidlock-original")


def managed(path):
    return path.is_file() and MARKER in path.read_text(errors="replace").splitlines()[:3]


def hook_text(name):
    source = str(Path(__file__).resolve().parent.parent)
    code = f"import sys; sys.path.insert(0, {source!r}); from raidlock.guard import main; sys.exit(main())"
    return f'#!/bin/sh\n{MARKER}\nexec {shlex.quote(sys.executable)} -c {shlex.quote(code)} _hook {name} "$0" "$@"\n'


def install():
    folder = hook_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for name in HOOKS:
        path = folder / name
        if not managed(path) and os.path.lexists(backup(path)):
            raise ValueError(f"backup already exists: {backup(path)}")
    for name in HOOKS:
        path = folder / name
        if managed(path):
            continue
        if os.path.lexists(path):
            path.rename(backup(path))
        try:
            path.write_text(hook_text(name))
            path.chmod(0o755)
        except OSError:
            path.unlink(missing_ok=True)
            if os.path.lexists(backup(path)):
                backup(path).rename(path)
            raise


def uninstall():
    for name in HOOKS:
        path = hook_dir() / name
        if managed(path):
            path.unlink()
            if os.path.lexists(backup(path)):
                backup(path).rename(path)


def staged_paths():
    return split_paths(git("diff", "--cached", "--name-only", "--no-renames", "-z"))


def split_paths(data):
    return {os.fsdecode(p) for p in data.split(b"\0") if p}


def pushed_paths(data, caller=None):
    paths = set()
    for line in data.splitlines():
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("invalid pre-push input")
        _, local, _, remote = fields
        if (
            len(local) not in (40, 64)
            or len(remote) != len(local)
            or any(c not in "0123456789abcdef" for c in local + remote)
        ):
            raise ValueError("invalid pre-push object id")
        if set(local) == {"0"}:
            if caller is None or not caller.is_lead:
                raise IdentityError("ref deletion requires explicit lead authorization")
            continue
        if set(remote) != {"0"} and (caller is None or not caller.is_lead):
            try:
                ancestor = git("merge-base", local, remote).decode().strip()
            except subprocess.CalledProcessError:
                raise IdentityError("cannot establish fast-forward; explicit lead authorization required") from None
            if ancestor != remote:
                raise IdentityError("force push requires explicit lead authorization")
        revisions = [local] if set(remote) == {"0"} else [local, "^" + remote]
        for commit in git("rev-list", *revisions, "--").decode().splitlines():
            paths.update(
                split_paths(
                    git(
                        "diff-tree",
                        "--root",
                        "-m",
                        "--no-commit-id",
                        "--name-only",
                        "--no-renames",
                        "-r",
                        "-z",
                        commit,
                        "--",
                    )
                )
            )
    return paths


def refusal(board, caller, full):
    if not caller.authenticated:
        return {"allowed": False, "reason": "no authenticated agent; present your own token"}
    decision = board.check_write(str(full), caller)
    card_id = decision.get("card")
    if (
        decision["allowed"]
        and card_id
        and not caller.is_lead
        and (not caller.fence or caller.fence != board.card(card_id)["fence"])
    ):
        decision = dict(decision, allowed=False, reason="stale fence")
    return None if decision["allowed"] else decision


def check(hook="pre-commit", data="", board_path=None):
    try:
        board = Board.open(board_path)
        caller = board.caller()
        if not caller.authenticated:
            raise IdentityError("no authenticated agent; present your own token")
        board._auth(caller, scoped_ok=True)
        repo = root()
        paths = staged_paths() if hook == "pre-commit" else pushed_paths(data, caller)
        denied = []
        for path in sorted(paths):
            canonical(repo.name + "/" + path)
            decision = refusal(board, caller, repo / path)
            if decision:
                card = decision.get("card")
                fix = f"raidlock claim {shlex.quote(card)}" if card else "raidlock claim <card>"
                denied.append(
                    f"raidlock guard: {path!r}: holder={decision.get('holder') or 'none'}; "
                    f"{decision['reason']}; fix: {fix}"
                )
    except (Denied, IdentityError, StaleLead) as exc:
        print(f"raidlock guard: refused: {exc.message}", file=sys.stderr)
        return 2
    except Exception:
        warn = hook != "pre-push" and os.environ.get("RAIDLOCK_GUARD") == "warn"
        print(
            f"raidlock guard: {'warning' if warn else 'refused'}: board or git check unavailable; "
            "fix: raidlock show --board <workspace>",
            file=sys.stderr,
        )
        return 0 if warn else 2
    for line in denied:
        print(line, file=sys.stderr)
    return 2 if denied else 0


def run_hook(args):
    name, path, *forward = args
    data = sys.stdin.buffer.read() if name == "pre-push" else None
    code = check(name, os.fsdecode(data or b""))
    original = backup(Path(path).absolute())
    if code or not original.exists() or not os.access(original, os.X_OK):
        return code
    shell = [] if os.name == "posix" else [exe.find("sh")]
    return subprocess.run([*shell, str(original), *forward], input=data).returncode


def setup(parser):
    parser.add_argument("action", choices=("install", "uninstall", "check"))
    parser.add_argument("--hook", choices=HOOKS, default="pre-commit")


def dispatch(args):
    if args.action == "check":
        data = sys.stdin.read() if args.hook == "pre-push" else ""
        return check(args.hook, data, getattr(args, "board", None))
    {"install": install, "uninstall": uninstall}[args.action]()
    return 0


def register_cli(add_command):
    def handler(ctx, args):
        return {"_exit": dispatch(args)}, ""

    add_command("guard", "install, uninstall or check Git lease guards", setup, handler)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] == "_hook":
            return run_hook(argv[1:])
        parser = argparse.ArgumentParser(prog="python -m raidlock.guard")
        setup(parser)
        parser.add_argument("--board")
        return dispatch(parser.parse_args(argv))
    except Exception:
        print("raidlock guard: operation failed; fix: python -m raidlock.guard --help", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
