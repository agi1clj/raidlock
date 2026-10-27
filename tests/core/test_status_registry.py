import os

from raidlock.core import clock, registry


def test_status_from_owner_heartbeat_not_card_updates(world):
    w = world.agent("w-one")
    world.card("c1")
    world.claim("c1", w)
    assert world.board.statuses()["w-one"]["status"] == "working"
    clock.travel(20 * 60)
    world.board.note("c1", world.lead, "lead comment bumps card.updated")
    assert world.board.statuses()["w-one"]["status"] == "quiet"
    assert world.board.statuses()["w-one"]["label"] == "Working (quiet)"
    world.board.heartbeat("c1", w)
    assert world.board.statuses()["w-one"]["status"] == "working"
    clock.travel(50 * 60)
    assert world.board.statuses()["w-one"]["status"] == "stalled"


def test_review_blocked_idle(world):
    a, b = world.agent("w-aaa"), world.agent("w-bbb")
    world.agent("w-ccc")
    world.card("c1", locks="app/1")
    world.card("c2", locks="app/2")
    world.claim("c1", a)
    world.claim("c2", b)
    world.board.move("c1", "review", a)
    world.board.move("c2", "blocked", b)
    st = world.board.statuses()
    assert (st["w-aaa"]["label"], st["w-bbb"]["label"], st["w-ccc"]["label"]) == (
        "Waiting for review",
        "Blocked",
        "Idle",
    )


def test_registry_registers_on_init_and_lists(world):
    from raidlock.core.db import Index

    names = {w["name"]: w["board"] for w in registry.workspaces(Index(world.board.home))}
    assert os.path.realpath(names["ws"]) == os.path.realpath(world.board.dir)


def test_registry_dedupes_names(tmp_path):
    from corehelpers import World

    from raidlock.core.db import Index

    World(tmp_path / "a", "same")
    World(tmp_path / "b", "same")
    assert sorted(w["name"] for w in registry.workspaces(Index(registry.home()))) == ["same", "same-2"]


def test_synced_folder_detection(tmp_path):
    assert registry.synced_folder("/Users/x/Library/Mobile Documents/com~apple~CloudDocs/Desktop/ws") == "iCloud"
    assert registry.synced_folder("/home/x/Dropbox/ws") == "Dropbox"
    assert registry.synced_folder("/Users/x/Library/CloudStorage/OneDrive-Acme/ws") == "OneDrive"
    assert registry.synced_folder("/Users/x/Library/CloudStorage/GoogleDrive-a@b.c/My Drive/ws") == "Google Drive"
    assert registry.synced_folder(str(tmp_path)) == ""


def test_snapshot_shape(world):
    w = world.agent("w-one")
    world.card("c1")
    world.claim("c1", w)
    snap = world.board.snapshot()
    for key in (
        "cards",
        "teams",
        "agents",
        "leases",
        "claimable",
        "review_queue",
        "inbox",
        "messages",
        "costs",
        "transcript",
        "settings",
        "warnings",
    ):
        assert key in snap
    assert snap["cards"][0]["a2a_state"] == "working" and snap["leases"]["c1"]["live"]


def test_board_gitignored_and_files_have_schema(world):
    assert (world.root / ".board" / ".gitignore").read_text(encoding="utf-8").strip() == "*"
    import json

    for name in ("board.json", "agents.json", "teams.json"):
        assert json.loads((world.root / ".board" / name).read_text(encoding="utf-8"))["schema"] == 2


def test_version_changes_on_writes_only(world):
    v0 = world.board.version()
    world.board.cards()
    world.board.snapshot()
    assert world.board.version() == v0
    w = world.agent("w-one")
    v1 = world.board.version()
    assert v1["workspace"] > v0["workspace"] and v1["global"] > v0["global"]
    world.card("c1")
    world.claim("c1", w)
    v2 = world.board.version()
    assert v2["workspace"] > v1["workspace"]
    assert world.board.snapshot()["etag"] == f'"{v2["workspace"]}-{v2["global"]}"'


def test_hand_edited_card_file_wins_over_mirror(world):
    import json
    import os

    world.card("c1")
    world.board.cards()
    path = world.root / ".board" / "cards" / "c1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["card"]["title"] = "Edited by hand, longer title"
    path.write_text(json.dumps(data), encoding="utf-8")
    os.utime(path, ns=(1, 1))
    assert world.board.cards()[0]["title"] == "Edited by hand, longer title"
