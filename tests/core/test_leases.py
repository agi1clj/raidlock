import pytest
from corehelpers import World, make_repo

from raidlock.core import clock
from raidlock.core.errors import Denied, Precondition


def test_claim_returns_fence_and_moves_to_doing(world):
    w = world.agent("w-one")
    world.card("c1")
    r = world.claim("c1", w)
    card = world.board.card("c1")
    assert card["lane"] == "doing" and card["owner"] == "w-one" and card["fence"] == r["fence"] > 0
    assert world.board.leases()["c1"]["fence"] == r["fence"]


def test_fence_is_global_and_monotonic_across_workspaces(tmp_path):
    a = World(tmp_path, "ws-a", remote="git@github.com:acme/one.git")
    b = World(tmp_path, "ws-b", remote="git@github.com:acme/two.git")
    fences = []
    for world, name in ((a, "w-aaa"), (b, "w-bbb"), (a, "w-ccc")):
        w = world.agent(name)
        world.card(name, locks=f"app/{name}")
        fences.append(world.claim(name, w)["fence"])
    assert fences == sorted(fences) and len(set(fences)) == 3


def test_fence_kept_by_heartbeat_note_lock(world):
    w = world.agent("w-one")
    world.card("c1")
    fence = world.claim("c1", w)["fence"]
    world.board.heartbeat("c1", w)
    world.board.note("c1", w, "progress")
    world.board.lock_paths("c1", w, "app/docs")
    assert world.board.card("c1")["fence"] == fence == world.board.leases()["c1"]["fence"]


def test_owner_command_without_fence_is_precondition(world):
    w = world.agent("w-one")
    world.card("c1")
    world.claim("c1", w)
    w.fence = 0
    with pytest.raises(Precondition):
        world.board.heartbeat("c1", w)


def test_stale_fence_rejected_after_take_over(world):
    old = world.agent("w-old")
    new = world.agent("w-new")
    world.card("c1")
    stale = world.claim("c1", old)["fence"]
    clock.travel(46 * 60)
    world.claim("c1", new, take_over=True)
    old.fence = stale
    for action in (
        lambda: world.board.heartbeat("c1", old),
        lambda: world.board.move("c1", "review", old),
        lambda: world.board.lock_paths("c1", old, "app/x"),
        lambda: world.board.release("c1", old),
    ):
        with pytest.raises(Denied):
            action()


def test_stale_fence_rejected_after_same_agent_reclaims(world):
    w = world.agent("w-one")
    world.card("c1")
    first = world.claim("c1", w)["fence"]
    second = world.claim("c1", w)["fence"]
    assert second > first
    w.fence = first
    with pytest.raises(Denied):
        world.board.note("c1", w, "from a stale session")
    w.fence = second
    world.board.note("c1", w, "current session")


def test_take_over_needs_flag_and_expired_lease(world):
    old = world.agent("w-old")
    new = world.agent("w-new")
    world.card("c1")
    world.claim("c1", old)
    with pytest.raises(Denied):
        world.claim("c1", new, take_over=True)
    clock.travel(46 * 60)
    with pytest.raises(Denied):
        world.claim("c1", new)
    r = world.claim("c1", new, take_over=True)
    assert r["taken_over_from"] == "w-old" and world.board.card("c1")["owner"] == "w-new"


def test_resume_after_expiry_is_logged(world):
    w = world.agent("w-one")
    world.card("c1")
    world.claim("c1", w)
    clock.travel(50 * 60)
    r = world.board.heartbeat("c1", w)
    assert r["resumed"] is True
    assert any("resumed after expiry" in n["text"] for n in world.board.card("c1")["notes"])


def test_resume_denied_when_paths_taken_meanwhile(world):
    w = world.agent("w-one")
    other = world.agent("w-two")
    world.card("c1", locks="app/src")
    world.card("c2", locks="app/src/x")
    world.claim("c1", w)
    clock.travel(50 * 60)
    world.claim("c2", other)
    with pytest.raises(Denied):
        world.board.heartbeat("c1", w)


def test_reap_expired_holds_preserves_review_and_blocked_lanes(world):
    a, b, c = world.agent("w-aaa"), world.agent("w-bbb"), world.agent("w-ccc")
    world.board.add_team(world.lead, "core", 5)
    for cid, agent in (("doing-card", a), ("review-card", b), ("blocked-card", c)):
        world.card(cid, locks=f"app/{cid}")
        world.claim(cid, agent)
    world.board.move("review-card", "review", b)
    world.board.move("blocked-card", "blocked", c)
    clock.travel(2 * 3600)
    assert world.board.reap()["reaped"] == ["blocked-card", "doing-card", "review-card"]
    leases = world.board.leases()
    assert not leases
    assert world.board.card("doing-card")["lane"] == "blocked"
    assert not world.board.check_paths("app/review-card/x") and not world.board.check_paths("app/blocked-card")


def test_review_lease_expires(world):
    w = world.agent("w-one")
    other = world.agent("w-two")
    world.card("c1", locks="app/src")
    world.card("c2", locks="app/src")
    world.claim("c1", w)
    world.board.move("c1", "review", w)
    clock.travel(10 * 3600)
    assert world.claim("c2", other)["fence"] > w.fence


def test_release_on_done_and_todo(world):
    w = world.agent("w-one")
    world.card("c1")
    world.claim("c1", w)
    world.board.move("c1", "todo", w)
    assert "c1" not in world.board.leases()
    card = world.board.card("c1")
    assert card["owner"] == "" and card["released"]


def test_all_or_nothing_grant(world):
    a, b = world.agent("w-aaa"), world.agent("w-bbb")
    world.card("c1", locks="app/one")
    world.card("c2", locks="app/two app/one/deep")
    world.claim("c1", a)
    with pytest.raises(Denied) as e:
        world.claim("c2", b)
    assert "app/one/deep" in e.value.message and "w-aaa" in e.value.message
    assert "c2" not in world.board.leases()
    assert world.board.check_paths("app/two") == []


def test_cross_workspace_same_remote_shares_lock_space(tmp_path):
    a = World(tmp_path, "ws-a")
    b = World(tmp_path, "ws-b")
    wa, wb = a.agent("w-aaa"), b.agent("w-bbb")
    a.card("c1", locks="app/src")
    b.card("c2", locks="app/src/lib")
    a.claim("c1", wa)
    with pytest.raises(Denied) as e:
        b.claim("c2", wb)
    assert e.value.details["conflicts"][0]["workspace"] == "ws-a"


def test_cross_workspace_different_remote_independent(tmp_path):
    a = World(tmp_path, "ws-a", remote="git@github.com:acme/one.git")
    b = World(tmp_path, "ws-b", remote="git@github.com:acme/two.git")
    wa, wb = a.agent("w-aaa"), b.agent("w-bbb")
    a.card("c1", locks="app/src")
    b.card("c2", locks="app/src")
    a.claim("c1", wa)
    b.claim("c2", wb)


def test_repo_without_remote_uses_path(tmp_path):
    w = World(tmp_path, "ws", remote=None)
    key = w.board._resolver().resolve("app/src")
    assert key["id"].startswith("path:") and key["rel"] in ("src",)


def test_checkout_and_branch_recorded(world, tmp_path):
    clone = make_repo(tmp_path / "clone", "git@github.com:acme/app.git", branch="feat-x")
    w = world.agent("w-one")
    world.card("c1")
    r = world.claim("c1", w, checkout=str(clone))
    assert r["checkout"]["kind"] == "clone" and r["branch"] == "feat-x"


def test_worktree_detected(world, tmp_path):
    main = make_repo(tmp_path / "main", "git@github.com:acme/app.git")
    gitdir = main / ".git" / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    (gitdir / "HEAD").write_text("ref: refs/heads/wt-branch\n", encoding="utf-8")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    w = world.agent("w-one")
    world.card("c1")
    r = world.claim("c1", w, checkout=str(wt))
    assert r["checkout"]["kind"] == "worktree" and r["branch"] == "wt-branch"
    assert world.board.check_write(str(wt / "src" / "a.py"), w)["allowed"]


def test_check_write_containment(world, tmp_path):
    w, other = world.agent("w-one"), world.agent("w-two")
    world.card("c1", locks="app/src")
    world.claim("c1", w)
    app = world.root / "app"
    assert world.board.check_write(str(app / "src" / "x.py"), w)["allowed"]
    assert not world.board.check_write(str(app / "README.md"), w)["allowed"]
    d = world.board.check_write(str(app / "src" / "x.py"), other)
    assert not d["allowed"] and d["holder"] == "w-one"
    assert not world.board.check_write(str(app / "other.py"), other)["allowed"]
    assert not world.board.check_write(str(world.root / ".board" / "cards" / "c1.json"), w)["allowed"]
    assert world.board.check_write(str(tmp_path / "scratch.txt"), other)["allowed"]
    w.fence = 999
    assert not world.board.check_write(str(app / "src" / "x.py"), w)["allowed"]


def test_migrated_lease_gets_fence_and_reclaim_rotates(world):
    w = world.agent("w-one")
    world.card("c1")
    first = world.claim("c1", w)["fence"]
    assert world.claim("c1", w)["fence"] == first + 1
