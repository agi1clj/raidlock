import json
import os

from raidlock.mcp.server import Server

MODERN_META = {
    "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}}
}


class Client:
    def __init__(self, world, agent=None, token=None, extra=None):
        env = dict(os.environ)
        if agent:
            env["RAIDLOCK_AGENT"] = agent
        if token:
            env["RAIDLOCK_TOKEN"] = token
        env.update(extra or {})
        self.server = Server(str(world.root), env)
        self.next_id = 0

    def rpc(self, method, params=None, modern=True):
        self.next_id += 1
        body = dict(params or {})
        if modern:
            body.update(MODERN_META)
        return self.server.handle({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": body})

    def call(self, name, **arguments):
        r = self.rpc("tools/call", {"name": name, "arguments": arguments})
        result = r["result"]
        return json.loads(result["content"][0]["text"]), result["isError"]
