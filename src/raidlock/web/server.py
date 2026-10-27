import hashlib
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from raidlock.core import RaidlockError, db

from .. import __version__
from . import data, export

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC = {
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
    "/static/icon.svg": ("icon.svg", "image/svg+xml"),
    "/static/icon-raid.svg": ("icon-raid.svg", "image/svg+xml"),
    "/static/fonts/cinzel-600.woff2": ("fonts/cinzel-600.woff2", "font/woff2"),
    "/static/fonts/cinzel-700.woff2": ("fonts/cinzel-700.woff2", "font/woff2"),
    "/static/icon-32.png": ("icon-32.png", "image/png"),
}
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
VIEW_FILES = ("raidmap", "replay", "graph", "frames", "achievements", "bosses")
for _view in VIEW_FILES:
    STATIC[f"/static/views/{_view}.js"] = (os.path.join(WEB_DIR, _view, "view.js"), "text/javascript; charset=utf-8")
    STATIC[f"/static/views/{_view}.css"] = (os.path.join(WEB_DIR, _view, "view.css"), "text/css; charset=utf-8")
INDEX = ("index.html", "text/html; charset=utf-8")
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
    "font-src 'self'; manifest-src 'self'; media-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'; require-trusted-types-for 'script'; trusted-types 'none'"
)
PERMISSIONS = "camera=(), display-capture=(), geolocation=(), microphone=(), payment=(), usb=()"
_STATIC_CACHE = {}
SEGMENT = re.compile(r"^[^/\\\x00-\x1f]{1,200}$")
TRANSCRIPT_PAGE = 500
FILE_EXTS = (
    ".py",
    ".pyc",
    ".js",
    ".mjs",
    ".css",
    ".json",
    ".html",
    ".htm",
    ".map",
    ".txt",
    ".md",
    ".svg",
    ".png",
    ".ico",
    ".toml",
    ".cfg",
    ".ini",
    ".env",
    ".lock",
    ".db",
    ".jsonl",
    ".yaml",
    ".yml",
    ".sh",
)


def allowed_hosts(port):
    return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "raidlock"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def handle_one_request(self):
        with db.released():
            super().handle_one_request()

    def _headers(self, code, ctype, length, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", PERMISSIONS)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _send(self, code, body, ctype, extra=None):
        self._headers(code, ctype, len(body), extra)
        self.wfile.write(body)

    def _json(self, obj, code=200, etag=None):
        extra = {"Cache-Control": "no-cache"}
        if etag:
            extra["ETag"] = etag
        obj = data.redacted(data.scrub_private(obj, self.server.catalog.home))
        self._send(
            code,
            json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            "application/json; charset=utf-8",
            extra,
        )

    def _error(self, code, message):
        self._json({"error": message}, code)

    def _not_modified(self, etag):
        if etag and self.headers.get("If-None-Match") == etag:
            self._headers(304, "text/plain; charset=utf-8", 0, {"ETag": etag, "Cache-Control": "no-cache"})
            return True
        return False

    def _host_ok(self):
        hosts = self.headers.get_all("Host", [])
        return len(hosts) == 1 and hosts[0] in allowed_hosts(self.server.server_address[1])

    def _deny_method(self):
        if not self._host_ok():
            self._error(403, "forbidden host")
            return
        self._send(405, b'{"error":"method not allowed"}', "application/json", {"Allow": "GET"})

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = do_TRACE = do_CONNECT = _deny_method

    def do_GET(self):
        if not self._host_ok():
            self._error(403, "forbidden host")
            return
        url = urlsplit(self.path)
        path = url.path
        query = parse_qs(url.query)
        try:
            if path.startswith("/api/"):
                self._api(path[len("/api/") :], query)
            else:
                self._static(path)
        except RaidlockError as e:
            if str(e) == "graph too large":
                self._error(413, "graph too large")
                return
            self._error(404 if e.code == 3 else 500, e.message.split(" in /")[0])
        except Exception:
            self._error(500, "board could not be read")

    def _static(self, path):
        if path in STATIC:
            name, ctype = STATIC[path]
        elif path == "/" or path == "/index.html" or self._route(path):
            name, ctype = INDEX
        else:
            self._error(404, "not found")
            return
        body, etag = _static_file(name)
        if self._not_modified(etag):
            return
        self._send(200, body, ctype, {"Cache-Control": "no-cache", "ETag": etag})

    def _route(self, path):
        parts = path.strip("/").split("/")
        if len(parts) != 1:
            return False
        seg = unquote(parts[0])
        return (
            bool(SEGMENT.match(seg))
            and not seg.startswith(".")
            and seg not in ("api", "static")
            and not seg.lower().endswith(FILE_EXTS)
        )

    def _api(self, rest, query):
        catalog = self.server.catalog
        parts = [unquote(p) for p in rest.split("/") if p]
        if parts == ["overview"]:
            key = catalog.overview_key()
            if self._not_modified(key):
                return
            payload = catalog.overview()
            self._json(payload, etag=key)
            return
        if len(parts) == 2 and parts[0] in data.VIEWS:
            self._view(parts[0], parts[1], query)
            return
        if not parts or parts[0] not in ("board", "transcript", "card", "snapshot") or len(parts) < 2:
            self._error(404, "unknown endpoint")
            return
        ws = catalog.get(parts[1])
        if ws is None or not os.path.isdir(ws.dir):
            self._error(404, f"no workspace {parts[1][:80]}")
            return
        kind = parts[0]
        if kind == "board" and len(parts) == 2:
            key = ws.key_now()
            if self._not_modified(key):
                return
            key, payload = ws.refresh()
            self._json(data.public(payload), etag=key)
        elif kind == "transcript" and len(parts) == 2:
            self._transcript(ws, query)
        elif kind == "card" and len(parts) == 3:
            tag = data.etag_of(ws.key_now(), parts[2])
            if self._not_modified(tag):
                return
            self._json(data.card_detail(ws, parts[2]), etag=tag)
        elif kind == "snapshot" and len(parts) == 2:
            _, payload = ws.refresh()
            log = data.transcript_page(ws, 0, 200, "")["records"]
            public = data.public(payload)
            public["limits"] = catalog.provider_limits()
            public = data.scrub_private(public, catalog.home)
            log = data.scrub_private(log, catalog.home)
            body = export.render(data.redacted(public), data.redacted(log)).encode("utf-8")
            name = re.sub(r"[^A-Za-z0-9_.-]", "-", ws.name) or "board"
            self._send(
                200,
                body,
                "text/html; charset=utf-8",
                {"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="raidlock-{name}.html"'},
            )
        else:
            self._error(404, "unknown endpoint")

    def _view(self, kind, name, query):
        ws = self.server.catalog.get(name)
        if ws is None or not os.path.isdir(ws.dir):
            self._error(404, f"no workspace {name[:80]}")
            return
        args = {k: v[:1] for k, v in sorted(query.items()) if k in data.VIEW_QUERY}
        tag = data.etag_of(ws.key_now(), kind, args, data.view_clock(kind))
        if self._not_modified(tag):
            return
        self._json(data.view(ws, kind, args), etag=tag)

    def _transcript(self, ws, query):
        try:
            since = max(0, int((query.get("since") or ["0"])[0]))
            limit = min(TRANSCRIPT_PAGE, max(1, int((query.get("limit") or [str(TRANSCRIPT_PAGE)])[0])))
        except ValueError:
            self._error(400, "since and limit must be integers")
            return
        task = (query.get("task") or [""])[0]
        tag = data.etag_of(ws.key_now(), since, limit, task)
        if self._not_modified(tag):
            return
        self._json(data.transcript_page(ws, since, limit, task), etag=tag)


def _static_file(name):
    path = os.path.join(STATIC_DIR, name)
    stat = os.stat(path)
    key = (name, stat.st_mtime_ns, stat.st_size)
    hit = _STATIC_CACHE.get(name)
    if hit and hit[0] == key:
        return hit[1], hit[2]
    with open(path, "rb") as f:
        body = f.read()
    if name == INDEX[0]:
        body = body.replace(b"{{version}}", __version__.encode())
    etag = '"' + hashlib.sha256(body).hexdigest()[:20] + '"'
    _STATIC_CACHE[name] = (key, body, etag)
    return body, etag


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, catalog):
        super().__init__(address, Handler)
        self.catalog = catalog

    def process_request_thread(self, request, client_address):
        with db.released():
            super().process_request_thread(request, client_address)

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def make_server(port=8765, single=None, home=None, extra=None, host="127.0.0.1"):
    from .. import updates

    threading.Thread(target=updates.watch, daemon=True).start()
    return Server((host, port), data.Catalog(single=single, home=home, extra=extra))
