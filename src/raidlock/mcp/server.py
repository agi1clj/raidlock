import io
import json
import sys

from .. import __version__
from ..core import db
from ..core.errors import RaidlockError
from ..core.transcript import _clean
from . import resources
from .tools import TOOLS, Session

MODERN = ("2026-07-28",)
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26")
META = "io.modelcontextprotocol/"
INFO = {"name": "raidlock", "version": __version__}
CAPABILITIES = {"tools": {}, "resources": {}}
INSTRUCTIONS = (
    "raidlock board for this workspace. claimable, then claim a card, note progress, move it to review. "
    "Edit only paths your card locks."
)
PARSE_ERROR, INVALID_REQUEST, NO_METHOD, INVALID_PARAMS, INTERNAL = -32700, -32600, -32601, -32602, -32603
UNSUPPORTED_VERSION = -32022


class RpcError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


class Server:
    def __init__(self, board_path=None, env=None):
        self.session = Session(board_path, env)
        self.legacy = None

    def handle(self, msg):
        return _clean(self._handle(msg))

    def _handle(self, msg):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            if isinstance(msg, dict) and "method" not in msg and ("result" in msg or "error" in msg):
                return None
            return _error(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST, "invalid request")
        if "id" not in msg:
            return None
        rid, method = msg["id"], msg["method"]
        params = msg.get("params", {})
        try:
            if not isinstance(params, dict):
                raise RpcError(INVALID_PARAMS, "params must be an object")
            modern = self._era(method, params)
            result = self.dispatch(method, params)
        except RpcError as e:
            return _error(rid, e.code, e.message, e.data)
        except Exception as e:
            return _error(rid, INTERNAL, f"internal error: {e}")
        result.setdefault("resultType", "complete")
        if modern:
            result.setdefault("_meta", {})[META + "serverInfo"] = INFO
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _era(self, method, params):
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        version = meta.get(META + "protocolVersion")
        if method in ("initialize", "ping") and version is None:
            return False
        if version is not None:
            if version not in MODERN:
                raise RpcError(
                    UNSUPPORTED_VERSION,
                    "Unsupported protocol version",
                    {"supported": list(MODERN), "requested": version},
                )
            if not isinstance(meta.get(META + "clientCapabilities"), dict):
                raise RpcError(INVALID_PARAMS, f"missing _meta {META}clientCapabilities")
            return True
        if method == "server/discover" or self.legacy:
            return method == "server/discover"
        raise RpcError(
            INVALID_PARAMS,
            f"missing _meta {META}protocolVersion; send initialize first or use one of {', '.join(MODERN)}",
            {"supported": list(MODERN)},
        )

    def dispatch(self, method, params):
        if method == "initialize":
            requested = params.get("protocolVersion")
            self.legacy = requested if requested in LEGACY else LEGACY[0]
            return {
                "protocolVersion": self.legacy,
                "capabilities": CAPABILITIES,
                "serverInfo": INFO,
                "instructions": INSTRUCTIONS,
            }
        if method == "ping":
            return {}
        if method == "server/discover":
            return {
                "supportedVersions": list(MODERN),
                "capabilities": CAPABILITIES,
                "_meta": {META + "serverInfo": INFO},
                "instructions": INSTRUCTIONS,
                "ttlMs": 3600000,
                "cacheScope": "public",
            }
        if method == "tools/list":
            return {"tools": TOOLS, "ttlMs": 3600000, "cacheScope": "public"}
        if method == "tools/call":
            return self.call_tool(params)
        if method == "resources/list":
            return {"resources": resources.listing(self._board()), "ttlMs": 0, "cacheScope": "private"}
        if method == "resources/templates/list":
            return {"resourceTemplates": resources.templates(self._board()), "ttlMs": 3600000, "cacheScope": "private"}
        if method == "resources/read":
            board = self._board()
            try:
                mime, text = resources.read(board, params.get("uri"))
            except RaidlockError as e:
                raise RpcError(INVALID_PARAMS, e.message, {"uri": params.get("uri")}) from e
            return {
                "contents": [{"uri": params["uri"], "mimeType": mime, "text": text}],
                "ttlMs": 0,
                "cacheScope": "private",
            }
        raise RpcError(NO_METHOD, f"method not found: {method}")

    def _board(self):
        try:
            return self.session.board()
        except RaidlockError as e:
            raise RpcError(INVALID_PARAMS, e.message, e.to_dict()) from e

    def call_tool(self, params):
        name = params.get("name")
        if not isinstance(name, str) or name not in {t["name"] for t in TOOLS}:
            raise RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            raise RpcError(INVALID_PARAMS, "arguments must be an object")
        try:
            out, is_error = self.session.call(name, arguments), False
        except RaidlockError as e:
            out, is_error = e.to_dict(), True
            out.pop("ok", None)
        return {"content": [{"type": "text", "text": compact(out)}], "isError": is_error}


def _error(rid, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


def serve(inp, out, board_path=None, env=None):
    server = Server(board_path, env)
    for raw in inp:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = _error(None, PARSE_ERROR, "parse error")
        else:
            with db.released():
                reply = server.handle(msg)
        if reply is not None:
            data = compact(reply) + "\n"
            out.write(data.encode("utf-8") if _binary(out) else data)
            out.flush()
    return 0


def _binary(stream):
    return not isinstance(stream, io.TextIOBase)


def run(board_path=None, env=None):
    return serve(sys.stdin.buffer, sys.stdout.buffer, board_path, env)
