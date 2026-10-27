from mcphelpers import Client


def test_claimable(worker):
    out, err = worker.call("claimable")
    assert not err and out["ready"] == [{"id": "api", "title": "api"}]


def test_claimable_rejects_unknown_argument(worker):
    out, err = worker.call("claimable", agent="lead")
    assert err and "unknown argument" in out["message"]


def test_claim_returns_fence(worker, world):
    out, err = worker.call("claim", id="api")
    assert not err and out["fence"] == world.board.card("api")["fence"] and out["paths"]


def test_claim_conflict(worker, world):
    other = world.agent("w-two")
    world.card("api2", locks="app/src/x")
    world.claim("api", other)
    out, err = worker.call("claim", id="api2")
    assert err and out["code"] == 2


def test_heartbeat(worker):
    worker.call("claim", id="api")
    out, err = worker.call("heartbeat", id="api")
    assert not err and out["card"] == "api" and out["expires"]


def test_heartbeat_stale_fence(worker):
    grant, _ = worker.call("claim", id="api")
    out, err = worker.call("heartbeat", id="api", fence=grant["fence"] + 1000)
    assert err and out["code"] == 2


def test_note(worker, world):
    worker.call("claim", id="api")
    out, err = worker.call("note", id="api", text="halfway")
    assert not err and out == {"card": "api", "noted": True}
    assert world.board.card("api")["notes"][-1]["text"] == "halfway"


def test_note_needs_text(worker):
    out, err = worker.call("note", id="api")
    assert err and "missing argument: text" in out["message"]


def test_lock(worker):
    worker.call("claim", id="api")
    out, err = worker.call("lock", id="api", paths="app/docs")
    assert not err and "app/docs" in out["locks"]


def test_lock_rejects_escape(worker):
    worker.call("claim", id="api")
    out, err = worker.call("lock", id="api", paths="app/../etc")
    assert err and out["code"] == 3


def test_move_review(worker, world):
    worker.call("claim", id="api")
    out, err = worker.call("move", id="api", lane="review", note="ready")
    assert not err and out == {"card": "api", "lane": "review"}
    assert world.board.card("api")["lane"] == "review"


def test_move_done_refused(worker, world):
    worker.call("claim", id="api")
    out, err = worker.call("move", id="api", lane="done")
    assert err and "review, blocked" in out["message"]
    assert world.board.card("api")["lane"] == "doing"


def test_msg_and_inbox(worker, world):
    other = world.agent("w-two")
    out, err = worker.call("msg", to="w-two", text="hello", card="api")
    assert not err and out["to"] == "w-two" and out["status"] == "ready"
    reader = Client(world, "w-two", other.token)
    got, err = reader.call("inbox")
    assert not err and got["messages"][0]["from"] == "w-one" and got["messages"][0]["text"] == "hello"


def test_msg_unknown_recipient(worker):
    out, err = worker.call("msg", to="nobody", text="hi")
    assert err


def test_inbox_needs_token(world):
    world.agent("w-one")
    out, err = Client(world, "w-one", "wrong").call("inbox")
    assert err and out["code"] == 5


def test_unknown_tool_is_protocol_error(worker):
    r = worker.rpc("tools/call", {"name": "done", "arguments": {"id": "api"}})
    assert r["error"]["code"] == -32602


def test_lead_is_refused_everywhere(world):
    world.card("api", locks="app/src")
    lead = Client(world, world.board.lead, world.lead_token)
    for name, args in (
        ("claimable", {}),
        ("claim", {"id": "api"}),
        ("inbox", {}),
        ("move", {"id": "api", "lane": "review"}),
    ):
        out, err = lead.call(name, **args)
        assert err and out["code"] == 5 and "never acts as the lead" in out["message"], name
    assert world.board.card("api")["lane"] == "todo"


def test_role_lead_env_is_ignored(tmp_path):
    from corehelpers import World

    w = World(tmp_path, name="open", auth=False)
    w.card("api", locks="app/src")
    out, err = Client(w, extra={"RAIDLOCK_ROLE": "lead"}).call("claim", id="api")
    assert err and out["code"] == 5
    assert w.board.card("api")["lane"] == "todo"


def test_all_card_text_redacted_in_mcp(worker, world):
    import json

    secret = "1." + "a" * 43
    card = world.board.card("api")
    for field in ("title", "scope", "verify_cmd", "ticket"):
        card[field] = secret
    world.board._save_card(card)
    listing = worker.rpc("resources/list")
    assert secret not in json.dumps(listing)
    out, err = worker.call("claimable")
    assert not err and secret not in json.dumps(out)
    from raidlock.mcp.resources import uri

    content = worker.rpc("resources/read", {"uri": uri(world.board, "card", "api")})
    assert secret not in json.dumps(content)
