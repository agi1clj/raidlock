import base64
import datetime
import shutil
import subprocess
import sys
import time
from xml.sax.saxutils import escape

from .core import db


def send_notification(title, body):
    if sys.platform == "darwin":
        executable = shutil.which("terminal-notifier")
        if executable:
            command = [executable, "-title", title, "-message", body]
        else:
            executable = shutil.which("osascript")
            if not executable:
                return False
            script = "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run"
            command = [executable, "-e", script, title, body]
    elif sys.platform == "win32":
        executable = shutil.which("powershell") or shutil.which("pwsh")
        if not executable:
            return False
        xml = (
            '<toast><visual><binding template="ToastGeneric"><text>'
            + escape(title)
            + "</text><text>"
            + escape(body)
            + "</text></binding></visual></toast>"
        )
        payload = base64.b64encode(xml.encode("utf-8")).decode("ascii")
        script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType = WindowsRuntime] > $null; "
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
            "ContentType = WindowsRuntime] > $null; "
            "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; "
            f'$xml.LoadXml([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{payload}"))); '
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
            '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Raidlock").Show($toast)'
        )
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        command = [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
    elif sys.platform.startswith("linux"):
        executable = shutil.which("notify-send")
        if not executable:
            return False
        command = [executable, "--", title, body]
    else:
        return False
    try:
        return subprocess.run(command, capture_output=True, timeout=10, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _parse_time(value):
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=datetime.UTC) if parsed.tzinfo is None else parsed
    except (AttributeError, TypeError, ValueError):
        return None


class Watcher:
    def __init__(self, board, notify=None, debounce=60):
        self.board = board
        self.notify = notify or send_notification
        self.debounce = debounce
        self.version = None
        self.cursor = 0
        self.cards = []
        self.leases = {}
        self.active = set()
        self.sent = {}

    def poll(self, now=None, monotonic=None):
        now = now or datetime.datetime.now(datetime.UTC)
        tick = time.monotonic() if monotonic is None else monotonic
        version = self.board.version()
        if version != self.version:
            self.cards = self.board.cards()
            self.leases = self.board.leases()
            self.version = version
        events = []
        active = set()
        for card in self.cards:
            cid = card["id"]
            if card["lane"] == "review":
                key = ("review", cid)
                active.add(key)
                events.append((key, "Card entered review", f"{cid}: {card['title']}"))
        for cid, lease in self.leases.items():
            expires = _parse_time(lease.get("expires"))
            if expires and 0 < (expires - now).total_seconds() < 300:
                key = ("lease", cid, lease.get("fence"), lease.get("expires"))
                active.add(key)
                events.append((key, "Lease expires soon", f"{cid}: {lease.get('agent', '')} (under 5 minutes)"))
        for name, status in self.board.statuses().items():
            if status["status"] == "stalled":
                key = ("stalled", name, status.get("card", ""))
                active.add(key)
                events.append((key, "Agent stalled", f"{name}: {status.get('card', '')}"))
        for record in self.board.transcript.read(since=self.cursor):
            self.cursor = max(self.cursor, record["seq"])
            if record.get("kind") == "msg" and record.get("to") == self.board.lead:
                key = ("message", record.get("messageId", record["seq"]))
                body = "\n".join(p.get("text", "") for p in record.get("parts", []) if p.get("kind") == "text")
                events.append((key, "Message to lead", body))
        delivered = 0
        for key, title, body in events:
            if key in self.active or tick - self.sent.get(key, float("-inf")) < self.debounce:
                continue
            self.sent[key] = tick
            delivered += bool(self.notify(f"Raidlock: {title}", body))
        self.active = active
        self.sent = {key: value for key, value in self.sent.items() if tick - value < self.debounce}
        return delivered


def _setup(parser):
    parser.add_argument("--once", action="store_true", help="Check current events once and exit")


def _handle(ctx, args):
    watcher = Watcher(ctx.board)
    count = 0
    try:
        while True:
            count += watcher.poll()
            if args.once:
                break
            db.close_all()
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    return {"notifications": count}, ""


def register_cli(add_command):
    add_command("notify", "watch the board for desktop notifications", _setup, _handle)
