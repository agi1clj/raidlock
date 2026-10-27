import io
import json
import os
import subprocess
import sys

import pytest
from corehelpers import SRC
from mcphelpers import Client

from raidlock.mcp.server import serve
from raidlock.mcp.tools import TOOLS


def lines(*msgs):
    return io.BytesIO(b"".join((m if isinstance(m, bytes) else json.dumps(m).encode()) + b"\n" for m in msgs))


def replies(out):
    return [json.loads(x) for x in out.getvalue().decode().splitlines()]


def test_legacy_handshake(world):
    out = io.BytesIO()
    serve(
        lines(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        ),
        out,
        str(world.root),
    )
    init, ping, listed = replies(out)
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["serverInfo"]["name"] == "raidlock"
    assert set(init["result"]["capabilities"]) == {"tools", "resources"}
    assert ping == {"jsonrpc": "2.0", "id": 2, "result": {"resultType": "complete"}}
    assert [t["name"] for t in listed["result"]["tools"]] == [t["name"] for t in TOOLS]


def test_legacy_unknown_version_gets_newest_legacy(world):
    r = Client(world).rpc("initialize", {"protocolVersion": "2024-01-01", "capabilities": {}}, modern=False)
    assert r["result"]["protocolVersion"] == "2025-11-25"


def test_modern_discover_and_per_request_meta(world):
    c = Client(world)
    d = c.rpc("server/discover")["result"]
    assert d["supportedVersions"] == ["2026-07-28"]
    assert d["resultType"] == "complete"
    assert d["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "raidlock"
    listed = c.rpc("tools/list")["result"]
    assert listed["ttlMs"] > 0 and listed["cacheScope"] == "public"
    assert listed["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "raidlock"


def test_modern_unsupported_version(world):
    r = Client(world).server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "1900-01-01",
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        }
    )
    assert r["error"]["code"] == -32022
    assert r["error"]["data"]["requested"] == "1900-01-01"
    assert r["error"]["data"]["supported"] == ["2026-07-28"]


def test_modern_missing_capabilities_is_invalid_params(world):
    r = Client(world).server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}},
        }
    )
    assert r["error"]["code"] == -32602


def test_no_era_is_refused(world):
    r = Client(world).rpc("tools/list", modern=False)
    assert r["error"]["code"] == -32602


def test_framing_errors(world):
    out = io.BytesIO()
    serve(
        lines(
            b"{not json",
            [1, 2],
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "nope",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            },
        ),
        out,
        str(world.root),
    )
    parse, batch, missing = replies(out)
    assert parse["error"]["code"] == -32700 and parse["id"] is None
    assert batch["error"]["code"] == -32600
    assert missing["error"]["code"] == -32601 and missing["id"] == 9


@pytest.mark.parametrize("params", [None, False, 0, "", [], ["invalid"], "invalid", 1])
def test_nonobject_params_are_refused(world, params):
    server = Client(world).server
    response = server.handle({"jsonrpc": "2.0", "id": 12, "method": "ping", "params": params})
    assert response["id"] == 12
    assert response["error"]["code"] == -32602
    assert server.handle({"jsonrpc": "2.0", "id": 13, "method": "ping"})["result"]


@pytest.mark.parametrize("name", [None, False, 0, "", [], {}, ["claim"], {"name": "claim"}])
def test_invalid_tool_name_is_invalid_params(world, monkeypatch, name):
    client = Client(world)
    calls = []
    monkeypatch.setattr(client.server.session, "call", lambda *args: calls.append(args))
    response = client.rpc("tools/call", {"name": name, "arguments": {}})
    assert response["error"]["code"] == -32602
    assert calls == []


@pytest.mark.parametrize("arguments", [None, False, 0, "", [], ["invalid"], "invalid", 1])
def test_nonobject_tool_arguments_are_refused(world, monkeypatch, arguments):
    client = Client(world)
    calls = []
    monkeypatch.setattr(client.server.session, "call", lambda *args: calls.append(args))
    response = client.rpc("tools/call", {"name": "claimable", "arguments": arguments})
    assert response["error"]["code"] == -32602
    assert calls == []


def test_omitted_tool_arguments_are_an_empty_object(world, monkeypatch):
    client = Client(world)
    calls = []

    def record(name, arguments):
        calls.append((name, arguments))
        return {"ready": [], "blocked": []}

    monkeypatch.setattr(client.server.session, "call", record)
    response = client.rpc("tools/call", {"name": "claimable"})
    assert response["result"]["isError"] is False
    assert calls == [("claimable", {})]


def test_tool_annotations_mark_reads_and_safe_repeats():
    hints = {t["name"]: t["annotations"] for t in TOOLS}
    assert hints["claimable"]["readOnlyHint"] is True
    assert hints["heartbeat"]["idempotentHint"] is True
    assert all(h.get("destructiveHint") is False for n, h in hints.items() if n != "claimable")
    assert all(h["openWorldHint"] is False for h in hints.values())
    assert hints["inbox"]["readOnlyHint"] is False


def test_schema_size_budget():
    size = len(json.dumps(TOOLS, separators=(",", ":")))
    assert len(TOOLS) <= 8
    assert size / 4 <= 1500, size


def test_stdio_subprocess(world):
    env = dict(os.environ, PYTHONPATH=SRC)
    msgs = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    p = subprocess.run(
        [sys.executable, "-m", "raidlock", "mcp", "--board", str(world.root)],
        input="".join(json.dumps(m) + "\n" for m in msgs),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
    )
    assert p.returncode == 0, p.stderr
    out = [json.loads(x) for x in p.stdout.splitlines()]
    assert [m["id"] for m in out] == [1, 2]
    assert len(out[1]["result"]["tools"]) == 8


def test_token_report_includes_schemas(world):
    from raidlock.core.tokens import report

    item = next(i for i in report(world.board)["items"] if i["name"] == "mcp tool schemas")
    assert 0 < item["tokens"] <= 1500


@pytest.mark.parametrize("kind", ["result", "error"])
def test_mcp_redacts_all_response_paths(monkeypatch, kind):
    from raidlock.mcp.server import Server

    secret = "ghp_" + "A" * 36
    server = Server()

    def dispatch(*args):
        if kind == "error":
            raise ValueError(secret)
        return {"contents": [{"text": secret}]}

    monkeypatch.setattr(server, "dispatch", dispatch)
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert secret not in json.dumps(response)
    assert "[REDACTED]" in json.dumps(response)
