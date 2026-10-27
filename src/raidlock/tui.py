"""Live terminal board for supported Linux/macOS hosts; native Windows is unverified."""

import argparse
import math
import os
import selectors
import sys
import time
from contextlib import contextmanager
from datetime import datetime

from raidlock.core import Board, Precondition, db

LANES = ("todo", "doing", "review", "blocked", "done", "cancelled")
STATUSES = {
    "working": "Working",
    "quiet": "Working",
    "review": "Waiting",
    "blocked": "Blocked",
    "stalled": "Stalled",
    "idle": "Idle",
}


def _text(value):
    return "".join(c if c.isprintable() else " " for c in str(value))


def _countdown(lease, now):
    if not lease:
        return "-"
    if lease.get("expires") is None:
        return "held"
    expires = datetime.fromisoformat(lease["expires"].replace("Z", "+00:00"))
    seconds = max(0, math.ceil((expires - now).total_seconds()))
    if not seconds:
        return "expired"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


def render(snapshot, color=False):
    now = datetime.fromisoformat(snapshot["now"].replace("Z", "+00:00"))
    color = color and "NO_COLOR" not in os.environ

    def heading(text):
        return f"\033[1;36m{text}\033[0m" if color else text

    total = snapshot["costs"]["workspace"]
    lines = [heading(f"Raidlock | {_text(snapshot['workspace'])} | Spend ${total['usd']:.4f}")]
    if total.get("unpriced_runs"):
        lines.append(f"Unpriced runs: {total['unpriced_runs']}")
    for lane in LANES:
        cards = sorted((c for c in snapshot["cards"] if c["lane"] == lane), key=lambda c: c["id"])
        if lane == "cancelled" and not cards:
            continue
        lines.extend(["", heading(f"{lane.upper()} ({len(cards)})")])
        for card in cards:
            title = _text(card["title"])
            title = title if len(title) <= 44 else title[:41] + "..."
            lease = snapshot.get("leases", {}).get(card["id"], card.get("lease"))
            lines.append(
                f"  {_text(card['id'])} | {title} | {_text(card.get('owner') or '-')} | lock {_countdown(lease, now)}"
            )
        if not cards:
            lines.append("  (empty)")
    agents = snapshot["agents"]
    lines.extend(["", heading(f"AGENTS ({len(agents)})")])
    for name, agent in sorted(agents.items()):
        status = STATUSES.get(agent.get("status"), "Idle")
        lines.append(
            f"  {_text(name)} | {status} | [{_text(agent.get('model') or 'unknown')}] | "
            f"{_text(agent.get('card') or '-')}"
        )
    if not agents:
        lines.append("  (none)")
    lines.extend(["", "q / Ctrl-C to exit | countdowns sampled on board changes"])
    return "\n".join(lines) + "\n"


@contextmanager
def _terminal(output):
    enabled = output.isatty()
    restore = None
    if enabled and os.name == "nt":
        import ctypes
        import msvcrt

        kernel = ctypes.windll.kernel32
        handle = ctypes.c_void_p(msvcrt.get_osfhandle(output.fileno()))
        mode = ctypes.c_ulong()
        enabled = bool(kernel.GetConsoleMode(handle, ctypes.byref(mode)))
        if enabled:
            enabled = bool(kernel.SetConsoleMode(handle, mode.value | 4))
            if enabled:

                def restore():
                    return kernel.SetConsoleMode(handle, mode.value)

    try:
        if enabled:
            output.write("\033[?25l")
        yield enabled
    finally:
        if enabled:
            output.write("\033[0m\033[?25h")
            output.flush()
        if restore:
            restore()


@contextmanager
def _keyboard(stream):
    saved = None
    if os.name != "nt" and stream.isatty():
        import termios
        import tty

        saved = termios.tcgetattr(stream.fileno())
        tty.setcbreak(stream.fileno())
    try:
        yield
    finally:
        if saved is not None:
            termios.tcsetattr(stream.fileno(), termios.TCSADRAIN, saved)


def _quit(stream, delay):
    deadline = time.monotonic() + delay
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if os.name == "nt":
            import msvcrt

            if stream.isatty() and msvcrt.kbhit() and msvcrt.getwch() in ("q", "Q", "\x03"):
                return True
            time.sleep(min(0.05, remaining))
        else:
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(stream, selectors.EVENT_READ)
                    ready = selector.select(remaining)
            except (ValueError, OSError):
                time.sleep(remaining)
                return False
            if not ready:
                return False
            char = os.read(stream.fileno(), 1)
            if char in (b"q", b"Q", b"\x03"):
                return True
            if not char:
                time.sleep(remaining)
                return False


def watch(board, interval=3, output=None, stdin=None):
    if not math.isfinite(interval) or interval <= 0:
        raise Precondition("interval must be a positive finite number")
    output = sys.stdout if output is None else output
    stdin = sys.stdin if stdin is None else stdin
    version = None
    try:
        with _terminal(output) as ansi, _keyboard(stdin):
            while True:
                current = board.version()["workspace"]
                if current != version:
                    snapshot = board.snapshot(transcript_limit=0, messages_limit=0)
                    output.write(("\033[H\033[2J" if ansi else "") + render(snapshot, color=ansi))
                    output.flush()
                    version = snapshot["version"]["workspace"]
                db.close_all()
                if _quit(stdin, interval):
                    break
    except KeyboardInterrupt:
        pass
    return 0


def _open(workspace=None, path=None):
    return Board.open(workspace if workspace is not None else path)


def _interval(value):
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("interval must be a positive finite number") from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("interval must be a positive finite number")
    return number


def _setup(parser):
    parser.add_argument("--workspace", metavar="NAME", help="workspace folder name or path")
    parser.add_argument("--interval", type=_interval, default=3)


def _command(ctx, args):
    if args.json:
        raise Precondition("watch does not support --json")
    board = _open(args.workspace, args.board) if args.workspace else ctx.board
    return {"_exit": watch(board, args.interval)}, ""


def register_cli(add_command):
    add_command("watch", "live terminal board", _setup, _command)


def main(argv=None):
    from raidlock.cli import add_command
    from raidlock.cli import main as cli_main

    register_cli(add_command)
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "watch":
        args.insert(0, "watch")
    return cli_main(args)


if __name__ == "__main__":
    sys.exit(main())
