import os
import threading
import webbrowser
from urllib.parse import quote

from raidlock.core import find_board_dir
from raidlock.core.errors import Precondition

HOSTS = ("127.0.0.1", "0.0.0.0")


def check_host(host, env=None):
    env = os.environ if env is None else env
    if host != HOSTS[0] and env.get("RAIDLOCK_SANDBOX") != "container":
        raise Precondition(
            f"--host {host} is allowed only inside the raidlock container image",
            hint="raidlock ui   (binds 127.0.0.1)",
        )
    return host


def setup(parser):
    parser.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (default 8765)")
    parser.add_argument(
        "--host",
        choices=HOSTS,
        default=HOSTS[0],
        help="bind address; 0.0.0.0 only inside the raidlock container image",
    )
    parser.add_argument("--only", action="store_true", help="show only this board, not every registered workspace")
    parser.add_argument("--open", action="store_true", help="open the page in your browser")


def start_path(catalog, here, single):
    """Run inside a board, the page opens on that board; the realm stays one click away."""
    if not here or single:
        return ""
    real = os.path.realpath(here)
    name = next((n for n, d in catalog.entries() if os.path.realpath(d) == real), "")
    return quote(name)


def handler(ctx, args):
    from .server import make_server

    host = check_host(getattr(args, "host", "127.0.0.1"))
    here = None
    try:
        here = find_board_dir(args.board)
    except Precondition:
        if args.board or args.only:
            raise
    if here:
        from raidlock.core.migrate import reject_legacy

        reject_legacy(here)
    single = here if (args.only or args.board) else None
    try:
        srv = make_server(port=args.port, single=single, extra=None if single else here, host=host)
    except OSError as e:
        raise Precondition(
            f"port {args.port} is busy ({e.strerror})", hint=f"raidlock ui --port {args.port + 1}"
        ) from e
    from .. import tidy

    threading.Thread(target=tidy.reaper, args=(srv.catalog,), daemon=True).start()
    if here:
        caller = ctx.caller()
        if caller.is_lead:
            import time

            previous = srv.service_actions
            renewed = [time.monotonic()]

            def heartbeat():
                previous()
                if time.monotonic() - renewed[0] >= 30:
                    from raidlock.core.errors import RaidlockError

                    try:
                        ctx.board.lead_heartbeat(caller)
                    except RaidlockError as exc:
                        print(f"raidlock ui: lead heartbeat stopped: {exc}", flush=True)
                        srv.service_actions = previous
                    renewed[0] = time.monotonic()

            srv.service_actions = heartbeat
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}/" + start_path(srv.catalog, here, single)
    if single:
        where = os.path.basename(os.path.dirname(single))
    elif url.endswith("/"):
        where = "every registered workspace"
    else:
        where = "this board (the realm view lists every registered workspace)"
    print(f"raidlock ui: {where} at {url}\nNext: open {url} (Ctrl+C stops it)", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return {"url": url}, ""


def register_cli(add_command):
    add_command("ui", "open the live board in your browser", setup, handler)
