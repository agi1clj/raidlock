import io
import json

import pytest

from raidlock.core import Timeout, db
from raidlock.mcp.server import serve


def test_mcp_closes_index_connections_after_each_message(world):
    seen = []

    class Out(io.BytesIO):
        def flush(self):
            seen.append(db.open_count())

    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "claimable", "arguments": {}}}
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
    raw = b"".join(json.dumps(m).encode() + b"\n" for m in (init, call, call))
    serve(io.BytesIO(raw), Out(), str(world.root))
    assert seen == [0, 0, 0]


@pytest.mark.parametrize("which", ["wait", "inbox"])
def test_polling_loops_release_connections_while_sleeping(world, monkeypatch, which):
    worker = world.agent("w-one")
    world.card("c1")
    world.claim("c1", worker)
    held = []
    monkeypatch.setattr("raidlock.core.board.time.sleep", lambda s: held.append(db.open_count()))
    with pytest.raises(Timeout):
        if which == "wait":
            world.board.wait("c1", timeout=0.05, interval=0.01)
        else:
            world.board.inbox(worker, wait=0.05, interval=0.01)
    assert held and set(held) == {0}
