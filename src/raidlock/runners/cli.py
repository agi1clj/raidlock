import argparse
import os

from ..core.identity import short_runtime

RUNTIMES = ("codex", "claude", "gemini", "shell")


def _setup(p):
    p.add_argument("card", help="card id")
    p.add_argument(
        "--to",
        required=True,
        type=short_runtime,
        choices=RUNTIMES,
        help="which agent CLI runs the card: codex, claude, gemini (experimental) or shell; codex-cli, claude-code "
        "and gemini-cli also work",
    )
    p.add_argument(
        "--as",
        dest="role",
        default="worker",
        choices=("worker", "reviewer"),
        help="worker changes files and hands back; reviewer reads and records a verdict",
    )
    p.add_argument(
        "--sandbox",
        default=None,
        choices=("read-only", "workspace-write"),
        help="default: workspace-write for workers, read-only for reviewers",
    )
    p.add_argument("--ref", "--base", default=None, help="branch or commit to start the clone from")
    p.add_argument("--model", default=None, help="model id passed to the CLI")
    p.add_argument("--cmd", default=None, help="command to run for --to shell (prompt on stdin, cwd is the clone)")
    p.add_argument(
        "--tools", default=None, help='claude only: comma-separated allowed tools, e.g. "Read,Grep,Edit,Bash(pytest *)"'
    )
    p.add_argument(
        "--unsafe-verify",
        action="store_true",
        help="run the card's verify command even when no sandbox is available (scrubbed env only)",
    )
    p.add_argument(
        "--raw-capture",
        action="store_true",
        help="keep raw logs (0600) and secret-bearing rejected artifacts in a private quarantine (0700)",
    )
    p.add_argument(
        "--timeout", type=float, default=3600.0, help="stop a hung worker after this many seconds (default: 3600)"
    )
    p.add_argument("--heartbeat", type=float, default=60.0, help=argparse.SUPPRESS)


def _short(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home) :] if path and path.startswith(home + os.sep) else path


def _text(r):
    cid = r["card"]
    if r.get("role") == "reviewer":
        if r.get("status") == "failed":
            return f"{r['agent']} could not review {cid}: {r['reason']}\nNext: less {_short(r['run_dir'])}/stderr.log"
        nxt = f"raidlock done {cid}" if r["verdict"] == "approve" else f"raidlock show {cid}"
        return f"{r['agent']} reviewed {cid}: {r['verdict']}. {r['summary'][:200]}\nNext: {nxt}"
    if r.get("status") == "lost":
        return f"{r['agent']} stopped on {cid}: {r['reason']}\nNext: raidlock show {cid}"
    if r["lane"] == "blocked":
        return f"{r['agent']} blocked {cid}: {r['reason']}\nNext: raidlock show {cid}"
    files = len(r["paths_changed"])
    verify = (
        f", verify {'passed' if r['verify']['passed'] else 'failed'}"
        if r.get("verify")
        else (", verify not run: no sandbox" if r.get("verify_skipped") else "")
    )
    patch = f" Patch: {_short(r['patch'])}" if r.get("patch") else ""
    return f"{r['agent']} handed {cid} to review ({files} file(s) changed{verify}).{patch}\nNext: raidlock show {cid}"


def _handler(ctx, args):
    from .runner import delegate

    board = ctx.board
    result = delegate(
        board,
        ctx.caller(),
        args.card,
        args.to,
        role=args.role,
        sandbox=args.sandbox,
        model=args.model,
        cmd=args.cmd,
        timeout=args.timeout,
        heartbeat=args.heartbeat,
        tools=args.tools,
        unsafe_verify=args.unsafe_verify,
        raw=args.raw_capture,
        ref=args.ref,
    )
    return result, _text(result)


def register_cli(add_command):
    add_command(
        "delegate",
        "run a card with codex, claude, gemini or a shell command in its own clone",
        _setup,
        _handler,
        mutating=True,
    )
