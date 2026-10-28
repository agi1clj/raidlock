import shlex
from pathlib import Path

import pytest
from corehelpers import make_repo, run_cli

from raidlock import cli
from raidlock.core import clock
from raidlock.core.errors import Denied, IdentityError, Precondition, StaleLead
from raidlock.runners import policy


@pytest.mark.parametrize("lane", ["review", "blocked"])
def test_advisory_hold_warns_but_never_authorizes_writes(world, lane):
    a, b = world.agent("worker-ash"), world.agent("worker-birch")
    world.card("first", locks="app/docs")
    world.card("second", locks="app/docs/page.md")
    world.claim("first", a)
    world.board.move("first", lane, a)
    assert not world.board.check_write(str(world.root / "app/docs/page.md"), a)["allowed"]
    result = world.claim("second", b)
    assert any("Advisory hold" in warning for warning in result["warnings"])
    assert world.board.check_write(str(world.root / "app/docs/page.md"), b)["allowed"]
    assert not world.board.check_write(str(world.root / "app/docs/page.md"), a)["allowed"]
    world.board._back_to_doing(world.board.card("first"))
    assert not world.board.check_write(str(world.root / "app/docs/page.md"), a)["allowed"]


def test_hold_deadline_cannot_be_extended_by_heartbeat_lock_or_lane_changes(world):
    world.board.set_setting(world.lead, "hold_minutes", 2)
    a = world.agent("worker-ash")
    world.card("first")
    world.claim("first", a)
    world.board.move("first", "review", a)
    expiry = world.board.leases()["first"]["expires"]
    clock.travel(60)
    world.board.heartbeat("first", a)
    world.board.lock_paths("first", world.lead, "app/src/page", replace=True)
    world.board.move("first", "blocked", world.lead)
    assert world.board.leases()["first"]["expires"] == expiry
    clock.travel(61)
    assert world.board.reap()["reaped"] == ["first"]


def test_lead_replaces_unlocks_releases_and_worker_cannot(world):
    a = world.agent("worker-ash")
    world.card("first", locks="app/docs")
    world.claim("first", a)
    with pytest.raises(IdentityError):
        world.board.lock_paths("first", a, "app/docs/a", replace=True)
    world.board.lock_paths("first", world.lead, "app/docs/a app/docs/b", replace=True)
    assert not world.board.check_write(str(world.root / "app/docs/c"), a)["allowed"]
    world.board.lock_paths("first", world.lead, "app/docs/a", remove=True)
    assert world.board.card("first")["locks"] == ["app/docs/b"]
    world.board.move("first", "review", a)
    world.board.release("first", world.lead)
    assert "first" not in world.board.leases()
    assert any(p.get("data", {}).get("replace") for r in world.board.transcript.read() for p in r["parts"])


def test_replace_conflict_is_atomic_and_stale_lead_is_refused(world):
    a, b = world.agent("worker-ash"), world.agent("worker-birch")
    world.card("first", locks="app/one")
    world.card("second", locks="app/two")
    world.claim("first", a)
    world.claim("second", b)
    with pytest.raises(Denied):
        world.board.lock_paths("first", world.lead, "app/two", replace=True)
    assert world.board.card("first")["locks"] == ["app/one"]
    world.lead.lead_fence += 1
    with pytest.raises(StaleLead):
        world.board.lock_paths("first", world.lead, "app/one", remove=True)


def test_join_inside_lead_session_uses_separate_private_files(world, monkeypatch):
    world.card("first")
    monkeypatch.setattr(cli, "_lead_session_state", lambda board: (None, True))
    files = []
    for name in ("worker-ash", "worker-birch"):
        args = cli.parse(["up", "--join", "--agent", name, "--card", "first"])
        ctx = cli.Context(args)
        ctx._board = world.board
        result, text = cli.cmd_up(ctx, args)
        path = Path(result["env_file"])
        assert path == Path(world.board.home) / "agents" / world.board.uid / f"{name}.env"
        assert path.stat().st_mode & 0o777 == 0o600
        assert f"source {shlex.quote(str(path))}" in text
        assert world.lead_token not in path.read_text()
        assert result["team"] == "core"
        files.append((path, path.read_text()))
    assert files[0][0].read_text() == files[0][1]
    with pytest.raises(IdentityError):
        world.board.caller(agent="worker-ash", token=result["token"], lead_secret=world.lead_token)


def test_teamless_worker_can_claim_unrestricted_card_and_gets_team_hint(world):
    worker = world.agent("worker-ash", team=None)
    world.card("open", team=None)
    world.claim("open", worker)
    world.card("restricted", locks="app/elsewhere")
    with pytest.raises(IdentityError) as exc:
        world.claim("restricted", worker)
    assert "--card restricted" in exc.value.hint


def test_claim_does_not_infer_checkout_from_cwd(world, tmp_path):
    worker = world.agent("worker-ash")
    world.card("first", locks="docs")
    foreign = make_repo(tmp_path / "foreign", "https://example.invalid/other.git")
    result = run_cli(
        "claim", "first", "--board", str(world.root), "--agent", worker.name, "--token", worker.token, cwd=foreign
    )
    assert result.returncode == 0, result.stderr
    assert world.board.check_write(
        str(world.root / "app/docs/page.md"),
        world.board.caller(agent=worker.name, token=worker.token, fence=world.board.card("first")["fence"]),
    )["allowed"]
    with pytest.raises(Precondition):
        world.board.claim("first", worker, checkout=str(foreign))


def test_repo_prefixes_resolve_to_same_keys_and_unknown_repo_has_hint(world):
    resolver = world.board._resolver()
    assert resolver.resolve("docs")["id"] == resolver.resolve("app/docs")["id"]
    assert resolver.resolve("docs")["rel"] == "docs"
    assert policy.target(world.board.workspace, world.board.dir, ["docs"])["rels"] == ["docs"]
    make_repo(world.root / "other")
    world.board._resolver_cache = None
    world.card("first", locks="missing/docs")
    with pytest.raises(Precondition) as exc:
        world.claim("first", world.agent("worker-ash"))
    assert "app" in exc.value.hint and "other" in exc.value.hint


def test_authenticated_lead_read_renews_within_grace_but_not_after(world):
    fence = world.lead.lead_fence
    clock.travel(4 * 3600)
    caller = world.board.caller(lead_secret=world.lead_token)
    assert caller.lead_fence == fence
    assert clock.parse(world.board.leadlease().lease()["expires"]) > clock.now()
    clock.travel(8 * 86400)
    with pytest.raises(StaleLead):
        world.board.caller(lead_secret=world.lead_token)


def test_wrong_session_and_secret_cannot_renew_expired_lead(world):
    clock.travel(4 * 3600)
    before = world.board.leadlease().lease()
    with pytest.raises(IdentityError):
        world.board.caller(lead_secret="wrong")
    with pytest.raises(IdentityError):
        world.board.caller(lead_secret=world.lead_token, session="foreign-session")
    assert world.board.leadlease().lease() == before


def test_join_with_lead_secret_is_refused(world):
    args = cli.parse(["up", "--join", "--agent", "worker-ash"])
    ctx = cli.Context(args)
    ctx._board = world.board
    ctx._secret = world.lead_token
    with pytest.raises(IdentityError, match="must not present"):
        cli.cmd_up(ctx, args)
    assert "worker-ash" not in world.board.agents()


def test_join_rejects_credential_file_symlink(world, tmp_path):
    path = Path(world.board.home) / "agents" / world.board.uid / "worker-ash.env"
    path.parent.mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_text("unchanged")
    path.symlink_to(victim)
    args = cli.parse(["up", "--join", "--agent", "worker-ash"])
    ctx = cli.Context(args)
    ctx._board = world.board
    with pytest.raises(Precondition):
        cli.cmd_up(ctx, args)
    assert victim.read_text() == "unchanged"


def test_legacy_indefinite_hold_becomes_advisory_and_expires(world):
    from raidlock.core import Board

    worker = world.agent("worker-ash")
    world.card("first")
    world.claim("first", worker)
    world.board.index.update_lease(world.board.uid, "first", expires=None)
    clock.travel(2 * 3600)
    reopened = Board.open(world.root)
    lease = reopened.leases()["first"]
    assert all(key["advisory"] for key in lease["keys"])
    assert clock.parse(lease["expires"]) < clock.now()
    assert not reopened.check_write(str(world.root / "app/src/a"), worker)["allowed"]


def test_advisory_conflicts_warn_across_workspaces(world, tmp_path):
    from corehelpers import World

    other = World(tmp_path, "other-workspace")
    a = world.agent("worker-ash")
    b = other.agent("worker-birch")
    world.card("first")
    other.card("second")
    world.claim("first", a)
    world.board.move("first", "review", a)
    result = other.claim("second", b)
    assert any("Advisory hold" in warning and "first" in warning for warning in result["warnings"])


def test_ui_heartbeat_renews_and_stops_after_handover(world, monkeypatch, capsys):
    import time
    from types import SimpleNamespace

    from raidlock.web import cli as web_cli
    from raidlock.web import server

    args = cli.parse(["up"])
    ctx = cli.Context(args)
    ctx._board = world.board
    ctx._secret = world.lead_token
    ticks = [0]
    monkeypatch.setattr(time, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(web_cli, "find_board_dir", lambda path: world.board.dir)

    class FakeCatalog:
        def workspaces(self):
            return []

    class FakeServer:
        server_address = ("127.0.0.1", 8765)
        catalog = FakeCatalog()

        def service_actions(self):
            pass

        def serve_forever(self):
            before = world.board.leadlease().lease()["expires"]
            clock.travel(60)
            ticks[0] = 60
            self.service_actions()
            assert world.board.leadlease().lease()["expires"] > before
            world.board.lead_handover(world.lead, "other-session")
            handed = world.board.leadlease().lease()
            clock.travel(60)
            ticks[0] = 120
            self.service_actions()
            assert world.board.leadlease().lease() == handed

        def server_close(self):
            pass

    monkeypatch.setattr(server, "make_server", lambda **kwargs: FakeServer())
    web_cli.handler(ctx, SimpleNamespace(board=None, only=True, port=8765, open=False))
    assert "heartbeat stopped" in capsys.readouterr().out


def test_cli_read_command_renews_lead(world, capsys):
    from raidlock.core.identity import token_path

    clock.travel(4 * 3600)
    assert (
        cli.main(
            [
                "show",
                "--board",
                str(world.root),
                "--lead-secret",
                token_path(world.board.home, world.board.uid, world.board.lead),
            ]
        )
        == 0
    )
    assert clock.parse(world.board.leadlease().lease()["expires"]) > clock.now()
    assert world.lead_token not in capsys.readouterr().out


def test_root_repo_paths_and_no_repo_verification_hint(tmp_path):
    from raidlock.core import repos

    root = make_repo(tmp_path / "repo")
    resolver = repos.Resolver(str(root), str(root / ".board"))
    assert resolver.resolve("docs")["rel"] == resolver.resolve("repo/docs")["rel"] == "docs"
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(Precondition) as exc:
        policy.target(str(empty), str(empty / ".board"), ["docs"])
    assert "initialize or declare" in exc.value.hint


@pytest.mark.parametrize("value", [0, -1, "nan", "inf"])
def test_hold_limit_rejects_unbounded_values(world, value):
    with pytest.raises(Precondition):
        world.board.set_setting(world.lead, "hold_minutes", value)


def test_lock_and_unlock_cli_dispatch(world):
    worker = world.agent("worker-ash")
    world.card("first", locks="app/docs")
    world.claim("first", worker)
    args = cli.parse(["lock", "first", "--replace", "app/docs/a app/docs/b"])
    ctx = cli.Context(args)
    ctx._board = world.board
    ctx._secret = world.lead_token
    result, _ = cli.cmd_lock(ctx, args)
    assert result["locks"] == ["app/docs/a", "app/docs/b"]
    args = cli.parse(["unlock", "first", "app/docs/a"])
    result, _ = cli.cmd_unlock(ctx, args)
    assert result["locks"] == ["app/docs/b"]


def test_twenty_workers_share_session_without_overwriting_credentials(world, tmp_path):
    import json
    from concurrent.futures import ThreadPoolExecutor

    shared = tmp_path / "shared.env"
    shared.write_text("unchanged")
    env = {
        "RAIDLOCK_SESSION": world.board.lead_status()["session"],
        "CLAUDE_ENV_FILE": str(shared),
    }

    def join(number):
        name = f"worker-{number:02}"
        result = run_cli("up", "--join", "--agent", name, "--board", str(world.root), "--json", env=env)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        path = Path(data["env_file"])
        assert path.stat().st_mode & 0o777 == 0o600
        return name, path, data["token"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        joined = list(pool.map(join, range(20)))
    assert len({path for _, path, _ in joined}) == 20
    assert len({token for _, _, token in joined}) == 20
    for name, path, token in joined:
        assert f"export RAIDLOCK_AGENT_ID={name}\n" in path.read_text()
        assert f"export RAIDLOCK_TOKEN={token}\n" in path.read_text()
    assert shared.read_text() == "unchanged"


def test_lead_can_release_own_review_using_lead_fence(world):
    world.card("first")
    world.claim("first", world.lead)
    world.board.move("first", "review", world.lead)
    world.lead.fence = 0
    assert world.board.release("first", world.lead)["released"]
