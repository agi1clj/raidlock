import json

from mcphelpers import Client


def test_list_and_read_card(worker, world):
    worker.call("claim", id="api")
    worker.call("note", id="api", text="started")
    listed = worker.rpc("resources/list")["result"]["resources"]
    uris = {r["uri"] for r in listed}
    card_uri = f"raidlock://{world.board.name}/card/api"
    assert card_uri in uris and f"raidlock://{world.board.name}/leases" in uris
    body = worker.rpc("resources/read", {"uri": card_uri})["result"]["contents"][0]
    assert body["mimeType"] == "text/markdown"
    assert body["text"] == world.board.brief("api")["text"]
    assert "started" in body["text"]


def test_read_leases_hides_fence(worker, world):
    worker.call("claim", id="api")
    body = worker.rpc("resources/read", {"uri": f"raidlock://{world.board.name}/leases"})["result"]["contents"][0]
    rows = json.loads(body["text"])
    assert rows[0]["card"] == "api" and rows[0]["agent"] == "w-one" and rows[0]["live"]
    assert "fence" not in rows[0]


def test_templates(worker, world):
    t = worker.rpc("resources/templates/list")["result"]["resourceTemplates"]
    assert t[0]["uriTemplate"] == f"raidlock://{world.board.name}/card/{{id}}"


def test_read_unknown(worker, world):
    for uri in ("raidlock://elsewhere/leases", f"raidlock://{world.board.name}/card/missing", "file:///etc/passwd"):
        r = worker.rpc("resources/read", {"uri": uri})
        assert r["error"]["code"] == -32602, uri


def test_resources_readable_without_identity(world):
    world.card("api", locks="app/src")
    r = Client(world).rpc("resources/read", {"uri": f"raidlock://{world.board.name}/card/api"})
    assert "result" in r
