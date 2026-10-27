from types import SimpleNamespace

import pytest

from raidlock.core import Board, Precondition


class MembershipBoard(SimpleNamespace):
    _resolver_cache = None
    _resolver = Board._resolver
    _card_repo = Board._card_repo


@pytest.mark.parametrize(
    "remote, accepted",
    [
        ("https://github.com/acme/app.git", True),
        ("git@github.com:acme/app.git", True),
        ("git@github.com:other/app.git", False),
        (None, False),
    ],
)
def test_verification_membership_uses_remote_identity(tmp_path, remote, accepted):
    target = tmp_path / "app"
    checkout = tmp_path / "worker"
    for path, url in [(target, "git@github.com:acme/app.git"), (checkout, remote)]:
        (path / ".git").mkdir(parents=True)
        if url:
            (path / ".git" / "config").write_text(f'[remote "origin"]\nurl = {url}\n')
    board = MembershipBoard(workspace=str(target), dir=str(target / ".board"))
    card = {"locks": ["app/src"]}
    if accepted:
        assert Board._verification_repo(board, card, str(checkout)) == "github.com/acme/app"
    else:
        with pytest.raises(Precondition, match="does not belong"):
            Board._verification_repo(board, card, str(checkout))


@pytest.mark.parametrize("origin", ["../app space.git", "file-url"])
def test_verification_membership_local_origin(tmp_path, origin):
    target = tmp_path / "app space.git"
    checkout = tmp_path / "worker"
    for path in (target, checkout):
        (path / ".git").mkdir(parents=True)
    url = target.as_uri() if origin == "file-url" else origin
    (checkout / ".git" / "config").write_text(f'[remote "origin"]\nurl = {url}\n')
    board = MembershipBoard(workspace=str(target), dir=str(target / ".board"))
    assert Board._verification_repo(board, {"locks": []}, str(checkout)) == "path:" + target.resolve().as_posix()


def test_verification_membership_shared_common_dir(tmp_path):
    target = tmp_path / "app"
    checkout = tmp_path / "worker"
    metadata = target / ".git" / "worktrees" / "worker"
    metadata.mkdir(parents=True)
    checkout.mkdir()
    (checkout / ".git").write_text(f"gitdir: {metadata}\n")
    (metadata / "commondir").write_text("../..\n")
    board = MembershipBoard(workspace=str(target), dir=str(target / ".board"))
    assert Board._verification_repo(board, {"locks": []}, str(checkout)) == "path:" + target.resolve().as_posix()


@pytest.mark.parametrize("lease_kind", ["live", "expired", "different", "absent"])
def test_verification_membership_rejects_self_attested_lease(tmp_path, lease_kind):
    target = tmp_path / "app"
    checkout = tmp_path / "worker"
    for path in (target, checkout):
        (path / ".git").mkdir(parents=True)
    lease = {
        "checkout": {"path": str(target if lease_kind == "different" else checkout)},
        "expires": "2000-01-01T00:00:00Z" if lease_kind == "expired" else None,
    }
    board = MembershipBoard(
        workspace=str(target),
        dir=str(target / ".board"),
        uid="board",
        index=SimpleNamespace(lease=lambda uid, card_id: None if lease_kind == "absent" else lease),
    )
    card = {"id": "task", "locks": [], "verification_checkout": str(checkout)}
    with pytest.raises(Precondition, match="does not belong"):
        Board._verification_repo(board, card, str(checkout))


def test_claim_rejects_unrelated_checkout_before_grant(world, tmp_path):
    from corehelpers import make_repo

    worker = world.agent("worker")
    world.card("task")
    checkout = make_repo(tmp_path / "unrelated")
    with pytest.raises(Precondition, match="does not belong"):
        world.board.claim("task", worker, checkout=str(checkout))
    assert world.board.leases() == {}
    assert world.board.card("task")["lane"] == "todo"


def test_clone_provenance_requires_lead_and_survives_reclaim(world, tmp_path):
    from corehelpers import make_repo

    from raidlock.core.errors import IdentityError

    worker = world.agent("worker")
    world.card("task")
    clone = make_repo(tmp_path / "clone")
    board = world.board
    with pytest.raises(IdentityError):
        board.trust_checkout(worker, "task", str(clone), str(world.root / "app"))
    board.trust_checkout(world.lead, "task", str(clone), str(world.root / "app"))
    grant = board.claim("task", worker, checkout=str(clone))
    assert grant["fence"] > 0
    assert board._verification_repo(board.card("task"), str(clone)) == "github.com/acme/app"
