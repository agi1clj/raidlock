import pytest

from raidlock.core import IdentityError, Precondition


def test_lockless_card_resolves_workspace_repo_outside_checkout(world, tmp_path, monkeypatch):
    world.card("empty", locks="")
    monkeypatch.chdir(tmp_path)
    assert world.board._verification_source(world.board.card("empty")) == str(world.root / "app")
    worker = world.agent("worker")
    world.claim("empty", worker, checkout=str(world.root / "app"))


def test_card_repo_can_be_named_without_locks(world):
    card = world.card("empty", locks="", repo="app")
    assert card["repo"] == "app"
    world.board.edit_card(world.lead, "empty", repo="app")
    with pytest.raises(Precondition, match="unknown workspace repo"):
        world.board.edit_card(world.lead, "empty", repo="missing")


def test_claim_for_registers_worker(world):
    world.card("task")
    result = world.board.claim("task", world.lead, for_agent="new-worker")
    assert result["agent"] == "new-worker"
    assert world.board.agents()["new-worker"]["team"] == "core"


def test_lead_can_narrow_held_locks_and_edit_scope_dependencies(world):
    worker = world.agent("worker")
    world.card("dependency", locks="")
    world.card("task", locks="app/docs")
    world.claim("task", worker)
    with pytest.raises(IdentityError):
        world.board.edit_card(worker, "task", locks="app/docs/a.md")
    world.board.edit_card(world.lead, "task", locks="app/docs/a.md", depends="dependency", scope="One file")
    card = world.board.card("task")
    assert card["locks"] == ["app/docs/a.md"]
    assert card["depends"] == ["dependency"]
    assert card["scope"] == "One file"
    other = world.agent("other-worker")
    world.card("other", locks="app/docs/b.md")
    world.claim("other", other)
    assert world.board.transcript.read(kind="status")[-2:]


def test_move_review_error_includes_claim_command(world):
    world.card("task")
    with pytest.raises(Precondition, match="raidlock claim task"):
        world.board.move("task", "review", world.lead)


def test_cancel_is_lead_only_releases_locks_and_skips_gates(world):
    worker = world.agent("worker")
    world.card("task")
    world.claim("task", worker)
    with pytest.raises(IdentityError):
        world.board.cancel("task", worker, "superseded")
    with pytest.raises(Precondition):
        world.board.cancel("task", world.lead, " ")
    result = world.board.cancel("task", world.lead, "superseded")
    assert result["lane"] == "cancelled"
    assert world.board.card("task")["cancel_reason"] == "superseded"
    assert not world.board.index.lease(world.board.uid, "task")
    with pytest.raises(Precondition):
        world.claim("task", worker)


def test_protected_exception_is_lead_only_and_never_allows_board(world):
    from raidlock.runners import policy

    worker = world.agent("worker")
    world.card("task")
    with pytest.raises(IdentityError):
        world.board.edit_card(worker, "task", allow_protected=".github/workflows")
    with pytest.raises(Precondition):
        world.board.edit_card(world.lead, "task", allow_protected=".board")
    world.board.edit_card(world.lead, "task", allow_protected=".github/workflows")
    allowed = world.board.card("task")["allow_protected"]
    assert policy.denied(".github/workflows/ci.yml")
    assert not policy.denied(".github/workflows/ci.yml", allowed)
    assert policy.denied(".board/settings.json", [".board"])
    assert policy.denied(".github/workflows/.env", allowed)


def test_lead_lease_accepts_child_but_rejects_unrelated_process(tmp_path):
    from raidlock.core.lead import ForeignLead, Lead

    table = {100: (1, "parent-start", "shell"), 101: (100, "child-start", "shell"), 102: (1, "other-start", "shell")}
    manager = Lead(str(tmp_path), str(tmp_path / "secret"), "test", 3600, table=table)
    secret = manager.establish("session", 100, "parent-start")
    assert manager.check(secret, "session", pid=101, start="child-start") == 1
    with pytest.raises(ForeignLead):
        manager.check(secret, "session", pid=102, start="other-start")


def test_rejoin_points_to_existing_credential(world, capsys):
    from types import SimpleNamespace

    from raidlock import cli

    worker = world.agent("worker")
    args = SimpleNamespace(
        agent="worker",
        team="core",
        role="worker",
        runtime="shell",
        provider=None,
        model=None,
        runner_model=None,
        reported_model=None,
        effort=None,
        session=None,
        card=None,
        billing=None,
    )
    ctx = SimpleNamespace(board=world.board, caller=lambda: worker)
    result, text = cli.cmd_join(ctx, args)
    assert not result.get("token")
    assert result["credential_path"] in text


def test_close_verifies_clean_checkout_and_records_cross_provider_approval(world, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from raidlock import cli
    from raidlock.runners import isolation, workspace

    repo = tmp_path / "clean"
    repo.mkdir()
    workspace.git(str(repo), "init", "--quiet")
    workspace.git(str(repo), *workspace.IDENT, "commit", "--quiet", "--allow-empty", "-m", "fixture")
    workspace.git(str(repo), "remote", "add", "origin", "git@github.com:acme/app.git")
    monkeypatch.setattr(isolation, "mode", lambda: "test-isolation")
    monkeypatch.setattr(isolation, "run", lambda *a, **k: (0, "ok", 1))
    worker = world.agent("worker", provider="openai")
    world.agent("reviewer", provider="anthropic", role="reviewer")
    world.card("task", locks="", repo="app", verify_cmd="true")
    world.claim("task", worker, checkout=str(repo))
    world.board.move("task", "blocked", world.lead)
    args = SimpleNamespace(id="task", reviewer="reviewer", note="Reviewed the change", cwd=str(repo), timeout=10)
    result, _ = cli.cmd_close(SimpleNamespace(board=world.board, caller=lambda: world.lead), args)
    assert result["lane"] == "done"
    assert world.board.approvals(world.board.card("task")) == ["reviewer"]
    (repo / "untracked").write_text("dirty")
    with pytest.raises(Precondition) as caught:
        world.board.verify("task", world.lead, cwd=str(repo))
    assert "--cwd" in caught.value.hint


def test_cancelled_card_is_excluded_from_score_rows(world):
    from raidlock.web.data import score_rows

    world.card("task")
    world.board.cancel("task", world.lead, "not needed")
    assert score_rows(world.board.cards(), world.board.agents()) == []
    world.board.reopen("task", world.lead)
    assert not world.board.card("task").get("cancel_reason")


def test_wrong_checkout_has_repo_selection_hint(world, tmp_path):
    from corehelpers import make_repo

    world.card("task", locks="", repo="app")
    other = make_repo(tmp_path / "other", "git@github.com:acme/other.git")
    with pytest.raises(Precondition) as caught:
        world.board._verification_repo(world.board.card("task"), str(other))
    assert "does not belong" in str(caught.value)
    assert "--checkout" in caught.value.hint and "--cwd" in caught.value.hint


def test_lead_carves_file_out_of_held_directory(world):
    from raidlock.core.errors import Denied

    owner = world.agent("owner")
    other = world.agent("other")
    world.card("directory", locks="app/docs")
    world.card("file", locks="app/docs/one.md")
    world.claim("directory", owner)
    with pytest.raises(Denied):
        world.claim("file", other)
    with pytest.raises(IdentityError):
        world.board.edit_card(owner, "directory", exclude_locks="app/docs/one.md")
    world.board.edit_card(world.lead, "directory", exclude_locks="app/docs/one.md")
    world.claim("file", other)
    assert world.board.card("directory")["locks"] == ["app/docs"]
    assert "excluded lock paths: app/docs/one.md" in world.board.brief("directory")["text"]
    one = str(world.root / "app/docs/one.md")
    two = str(world.root / "app/docs/two.md")
    assert not world.board.check_write(one, owner)["allowed"]
    assert world.board.check_write(one, other)["allowed"]
    assert world.board.check_write(two, owner)["allowed"]
    assert not world.board.check_write(two, other)["allowed"]
    assert not world.board.check_write(str(world.root / "app/docs"), owner)["allowed"]
    with pytest.raises(Denied):
        world.board.edit_card(world.lead, "directory", exclude_locks="")
    assert world.board.card("directory")["lock_exclusions"] == ["app/docs/one.md"]
