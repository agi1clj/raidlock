import copy
import hashlib
import hmac
import json
import os
from pathlib import Path

import pytest

from raidlock.core import Precondition, identity, lead, receipts
from raidlock.runners import isolation, workspace


@pytest.fixture(autouse=True)
def process_anchor(monkeypatch):
    monkeypatch.setattr(
        lead,
        "process_table",
        lambda: {os.getpid(): (os.getppid(), "test-process", "python"), os.getppid(): (0, "test-parent", "zsh")},
    )


@pytest.fixture
def verification_repo(world, tmp_path, monkeypatch):
    repo = tmp_path / "committed"
    repo.mkdir()
    workspace.git(str(repo), "init", "--quiet")
    (repo / "check.txt").write_text("committed")
    workspace.git(str(repo), "add", "check.txt")
    workspace.git(str(repo), *workspace.IDENT, "commit", "--quiet", "-m", "fixture")
    workspace.git(str(repo), "remote", "add", "origin", "git@github.com:acme/app.git")
    monkeypatch.setattr(isolation, "mode", lambda: "test-isolation")

    def run(cmd, cwd, env, timeout, **kwargs):
        assert cwd != str(repo)
        assert workspace.head(cwd) == workspace.head(str(repo))
        assert Path(cwd, "check.txt").read_text() == "committed"
        assert not Path(cwd, "untracked").exists()
        assert world.board.dir in kwargs["deny_read"]
        return 0, "ok", 7

    monkeypatch.setattr(isolation, "run", run)
    return repo


def verified(world, repo):
    world.card("c1", verify_cmd="check")
    return world.board.verify("c1", world.lead, cwd=str(repo))


def test_clean_clone_receipt_and_done(world, verification_repo):
    result = verified(world, verification_repo)
    assert receipts.valid(world.board, world.board.card("c1"), result)
    assert result["command_hash"] == receipts.command_hash("check")
    assert result["duration_ms"] == 7
    assert result["runner"] and result["ts"]
    world.board.move("c1", "done", world.lead)


@pytest.mark.parametrize("attack", ["stale", "tamper", "other-card", "missing", "key-path", "symlink-key"])
def test_receipt_attacks_refused(world, verification_repo, tmp_path, attack):
    result = verified(world, verification_repo)
    card = world.board.card("c1")
    if attack == "stale":
        workspace.git(str(verification_repo), *workspace.IDENT, "commit", "--quiet", "--allow-empty", "-m", "new head")
    elif attack == "tamper":
        card["verifications"][-1]["duration_ms"] += 1
    elif attack == "other-card":
        world.card("c2", verify_cmd="check")
        card = world.board.card("c2")
        card["verifications"] = [result]
    elif attack == "missing":
        card["verifications"] = []
    elif attack == "key-path":
        key = tmp_path / "forged-key"
        key.write_bytes(b"x" * 32)
        forged = copy.deepcopy(result)
        forged["key_path"] = str(key)
        forged["signature"] = hmac.new(key.read_bytes(), receipts._payload(forged), hashlib.sha256).hexdigest()
        card["verifications"] = [forged]
    else:
        key = Path(identity.token_path(world.board.home, world.board.uid, world.board.lead)).with_name("verify.key")
        forged = tmp_path / "forged-key"
        forged.write_bytes(key.read_bytes())
        key.unlink()
        key.symlink_to(forged)
    with world.board.lock():
        world.board._save_card(card)
    with pytest.raises(Precondition):
        world.board.move(card["id"], "done", world.lead)


def test_no_isolation_refused(world, monkeypatch):
    world.card("c1", verify_cmd="true")
    monkeypatch.setattr(isolation, "mode", lambda: None)
    with pytest.raises(Precondition, match="isolated"):
        world.board.verify("c1", world.lead)


def test_provider_defaults_and_logged_override(world):
    world.agent("first")
    assert world.board.settings["require_review"]["count"] == 0
    assert not world.board.settings["require_verify"]
    world.agent("second", runtime="codex-cli")
    assert world.board.settings["require_review"] == {"count": 1, "other_provider": True}
    assert world.board.settings["require_verify"]
    world.card("c1")
    with pytest.raises(Precondition, match="receipt"):
        world.board.move("c1", "done", world.lead)
    world.board.set_setting(world.lead, "require_review", 0)
    world.board.set_setting(world.lead, "require_verify", False)
    world.agent("third", runtime="gemini-cli")
    world.board.move("c1", "done", world.lead)
    assert "setting_changed" in json.dumps(world.board.transcript.read())


def test_override_before_second_provider_persists(world):
    world.board.set_setting(world.lead, "require_review", 0)
    world.board.set_setting(world.lead, "require_verify", False)
    world.agent("first")
    world.agent("second", runtime="codex-cli")
    assert world.board.settings["require_review"]["count"] == 0
    assert not world.board.settings["require_verify"]


def test_reputation_ignores_forged_results(world, verification_repo):
    from raidlock.reputation import reputation

    worker = world.agent("worker")
    world.card("c1", verify_cmd="check")
    world.claim("c1", worker)
    world.board.verify("c1", world.lead, cwd=str(verification_repo))
    card = world.board.card("c1")
    card["verifications"].append({"passed": False})
    with world.board.lock():
        world.board._save_card(card)
        world.board._record("verify", worker.name, "claimed failure", task="c1", data={"passed": False})
    row = next(r for r in reputation(world.board)["rows"] if r["key"] == worker.name)
    assert row["inputs"]["verify_passed"] == 1
    assert row["inputs"]["verify_failed"] == 0


def test_valid_receipt_still_requires_cross_provider_review(world, verification_repo):
    owner = world.agent("owner")
    reviewer = world.agent("reviewer", role="reviewer", runtime="codex-cli")
    world.card("c1", verify_cmd="check")
    world.claim("c1", owner)
    world.board.move("c1", "review", owner)
    world.board.verify("c1", world.lead, cwd=str(verification_repo))
    with pytest.raises(Precondition, match="approving review"):
        world.board.move("c1", "done", world.lead)
    world.board.review("c1", reviewer, "approve")
    world.board.move("c1", "done", world.lead)


def test_worker_cannot_disable_gates(world):
    from raidlock.core.errors import IdentityError

    worker = world.agent("worker")
    with pytest.raises(IdentityError):
        world.board.set_setting(worker, "require_verify", False)


def test_preplanted_key_symlink_refused(world, verification_repo, tmp_path):
    forged = tmp_path / "key"
    forged.write_bytes(b"x" * 32)
    Path(identity.token_path(world.board.home, world.board.uid, world.board.lead)).with_name("verify.key").symlink_to(
        forged
    )
    with pytest.raises(Precondition, match="key"):
        verified(world, verification_repo)
    assert not world.board.card("c1")["verifications"]


@pytest.mark.parametrize("change", ["tracked", "staged", "untracked"])
def test_dirty_verification_refused(world, verification_repo, change):
    world.card("c1", verify_cmd="check")
    path = verification_repo / ("untracked" if change == "untracked" else "check.txt")
    path.write_text("dirty")
    if change == "staged":
        workspace.git(str(verification_repo), "add", ".")
    with pytest.raises(Precondition, match="verification checkout is dirty; receipts are bound to a commit"):
        world.board.verify("c1", world.lead, cwd=str(verification_repo))
    assert not world.board.card("c1")["verifications"]


def test_receipt_identity_must_match_existing_lease(world, verification_repo, tmp_path):
    clone = tmp_path / "worker"
    workspace.clone(str(verification_repo), str(clone))
    worker = world.agent("worker")
    world.card("c1", verify_cmd="check")
    world.board.trust_checkout(world.lead, "c1", str(clone), str(verification_repo))
    world.claim("c1", worker, checkout=str(clone))
    workspace.git(str(verification_repo), *workspace.IDENT, "commit", "--quiet", "--allow-empty", "-m", "integrated")
    result = world.board.verify("c1", world.lead, cwd=str(verification_repo))
    with pytest.raises(Precondition, match="repo"):
        world.board.move("c1", "done", world.lead)
    assert result["repo"] == "github.com/acme/app"


def test_unrelated_verification_repo_refused(world, verification_repo):
    workspace.git(str(verification_repo), "remote", "set-url", "origin", "git@github.com:other/repo.git")
    world.card("c1", verify_cmd="check")
    with pytest.raises(Precondition, match="repo"):
        world.board.verify("c1", world.lead, cwd=str(verification_repo))


def test_receipt_repo_rechecked_at_done(world, verification_repo):
    verified(world, verification_repo)
    workspace.git(str(verification_repo), "remote", "set-url", "origin", "git@github.com:other/repo.git")
    with pytest.raises(Precondition, match="repo"):
        world.board.move("c1", "done", world.lead)


@pytest.mark.parametrize("kind", ["clone", "worktree"])
def test_receipt_accepts_same_repo_checkout(world, committed_workspace, tmp_path, kind):
    checkout = tmp_path / kind
    if kind == "clone":
        workspace.git(str(tmp_path), "clone", "--quiet", str(world.root), str(checkout))
    else:
        workspace.git(str(world.root), "worktree", "add", "--quiet", "--detach", str(checkout))
    world.card("c1", locks="ws", verify_cmd="true")
    receipt = world.board.verify("c1", world.lead, cwd=str(checkout))
    assert receipt["cwd"] == str(checkout.resolve())
    world.board.move("c1", "done", world.lead)


@pytest.mark.parametrize("change", ["none", "expired", "remote", "drop-lease", "replace-lease"])
def test_leased_checkout_receipt_identity(world, verification_repo, tmp_path, change):
    checkout = tmp_path / "worker"
    workspace.clone(str(verification_repo), str(checkout))
    worker = world.agent("worker")
    world.card("c1", verify_cmd="check")
    world.board.trust_checkout(world.lead, "c1", str(checkout), str(verification_repo))
    world.claim("c1", worker, checkout=str(checkout))
    result = world.board.verify("c1", world.lead)
    assert result["repo"] == "github.com/acme/app"
    if change == "expired":
        world.board._update_lease("c1", expires="2000-01-01T00:00:00Z")
    if change == "remote":
        workspace.git(str(checkout), "remote", "add", "origin", "git@github.com:other/repo.git")
    elif change == "drop-lease":
        world.board._drop_lease("c1")
    elif change == "replace-lease":
        card = world.board.card("c1")
        world.board._grant(
            card, worker.name, card["locks"], card["fence"], 600, checkout={"path": str(verification_repo)}
        )
    if change != "remote":
        world.board.move("c1", "done", world.lead)
        return
    with pytest.raises(Precondition, match="repo"):
        world.board.move("c1", "done", world.lead)


def test_old_workspace_key_cannot_sign_current_head(world, verification_repo):
    result = verified(world, verification_repo)
    old = b"worker-known-key".ljust(32, b"!")
    Path(world.board.dir, "verify.key").write_bytes(old)
    result["signature"] = hmac.new(old, receipts._payload(result), hashlib.sha256).hexdigest()
    card = world.board.card("c1")
    card["verifications"] = [result]
    world.board._save_card(card)
    with pytest.raises(Precondition, match="receipt"):
        world.board.move("c1", "done", world.lead)


def test_f2_worker_cannot_claim_unrelated_checkout(world, verification_repo):
    workspace.git(str(verification_repo), "remote", "set-url", "origin", "git@github.com:other/repo.git")
    worker = world.agent("attacker")
    world.card("c1", verify_cmd="check")
    with pytest.raises(Precondition):
        world.claim("c1", worker, checkout=str(verification_repo))
    assert "c1" not in world.board.leases()


def test_verify_never_executes_worker_clean_filter(world, verification_repo, tmp_path):
    import subprocess

    marker = tmp_path / "lead-executed-filter"
    script = tmp_path / "filter.sh"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n")
    script.chmod(0o755)
    (verification_repo / ".gitattributes").write_text("* filter=pwn\n")
    workspace.git(verification_repo, "add", ".gitattributes")
    workspace.git(verification_repo, *workspace.IDENT, "commit", "--quiet", "-m", "attributes")
    subprocess.run(["git", "-C", str(verification_repo), "config", "filter.pwn.clean", str(script)], check=True)
    os.utime(verification_repo / "check.txt", None)
    verified(world, verification_repo)
    assert not marker.exists()
