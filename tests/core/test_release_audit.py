"""Adversarial regressions for the release audit (B1, F1–F8, M1–M3)."""

import hashlib
import hmac
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from raidlock import cli, hooks, prsync
from raidlock.core import Board, identity, lead, migrate, receipts, registry
from raidlock.core.errors import IdentityError, Precondition
from raidlock.core.transcript import redact
from raidlock.mcp import resources
from raidlock.runners import workspace as W
from raidlock.scan import scan_patch
from raidlock.snapshot import render_snapshot


@pytest.fixture(autouse=True)
def anchored(monkeypatch):
    monkeypatch.setattr(
        lead,
        "process_table",
        lambda: {
            os.getpid(): (os.getppid(), "test-process", "python"),
            os.getppid(): (0, "test-parent", "zsh"),
        },
    )


@pytest.mark.parametrize(
    "key,command",
    [
        ("filter.pwn.clean", ("status", "--porcelain")),
        ("diff.external", ("diff", "HEAD")),
        ("core.pager", ("log", "-1")),
    ],
)
def test_worker_git_config_cannot_execute(tmp_path, key, command):
    repo = tmp_path / "repo"
    repo.mkdir()
    W.git(repo, "init", "--quiet")
    (repo / "file").write_text("original\n")
    W.git(repo, "add", ".")
    W.git(repo, *W.IDENT, "commit", "--quiet", "-m", "base")
    marker = tmp_path / "executed"
    script = tmp_path / "attack.sh"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n")
    script.chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "config", key, str(script)], check=True)
    (repo / ".gitattributes").write_text("* filter=pwn\n")
    (repo / "file").write_text("changed\n")
    W.git(repo, *command)
    assert not marker.exists()


def test_old_workspace_key_forgery_rejected_by_done(world):
    world.card("forged", verify_cmd="true")
    old = b"x" * 32
    Path(world.board.dir, "verify.key").write_bytes(old)
    card = world.board.card("forged")
    receipt = {
        "card": "forged",
        "board": world.board.uid,
        "sha": "a" * 40,
        "command_hash": receipts.command_hash("true"),
        "exit": 0,
        "passed": True,
    }
    receipt["signature"] = hmac.new(old, receipts._payload(receipt), hashlib.sha256).hexdigest()
    card["verifications"] = [receipt]
    world.board._save_card(card)
    with pytest.raises(Precondition):
        world.board.move("forged", "done", world.lead)
    signed = receipts.sign(world.board, receipt)
    assert signed["signature"] != receipt["signature"]
    key = Path(identity.token_path(world.board.home, world.board.uid, world.board.lead)).with_name("verify.key")
    assert key.stat().st_mode & 0o777 == 0o600
    monkey_data = {"tool_name": "Read", "tool_input": {"file_path": str(key)}, "cwd": str(world.root)}
    worker = world.agent("reader")
    with pytest.raises(IdentityError):
        hooks.read_guard(
            monkey_data,
            {"RAIDLOCK_AGENT": worker.name, "RAIDLOCK_TOKEN": worker.token, "RAIDLOCK_BOARD": world.board.dir},
        )


@pytest.mark.parametrize(
    "rollup",
    [
        [],
        [{"status": "IN_PROGRESS"}],
        [{"status": "COMPLETED", "conclusion": "NEUTRAL"}],
        [{"status": "COMPLETED", "conclusion": "SKIPPED"}],
    ],
)
def test_merged_pr_requires_successful_checks(rollup):
    card = {"lane": "review", "verifications": [{"sha": "abc", "passed": True}]}
    assert prsync.hold_reason(None, card, {"head": "abc", "checks": prsync.summarize(rollup)})


SECRETS = [
    "t" + "a" * 32,
    "1." + "b" * 43,
    "quest:1:" + "c" * 40,
    'RAIDLOCK_CAP="secret-value"',
    '{"password": "hunter2hunter2"}',
    '{"api_key": "hunter2hunter2"}',
    "sk_live_1234567890",
    "rk_live_1234567890",
    "postgres://user:pass@host/db",
    "mysql://user:pass@host/db",
    "https://hooks.slack.com/services/T123/B456/secret",
    "npm_" + "a" * 30,
    "hf_" + "a" * 30,
    "SG.abc.def",
    "AccountKey=secret-value",
    "AKIAIOSFODNN7EXAMPLE " + "aB0/" * 10,
]


@pytest.mark.parametrize("secret", SECRETS)
def test_secret_shapes_redacted_everywhere(world, secret):
    assert "[REDACTED]" in redact(secret)
    world.card("secret", scope=secret, verify_cmd=secret, ticket=secret)
    card = world.board.card("secret")
    card["title"] = secret
    world.board._save_card(card)
    assert secret not in json.dumps(resources.listing(world.board))
    assert secret not in resources.brief(world.board, "secret")
    assert secret not in render_snapshot({"cards": [card]})
    from raidlock.web.data import redacted

    assert secret not in json.dumps(redacted(card))
    world.board.note("secret", world.lead, secret)
    assert secret not in json.dumps(world.board.transcript.read())


@pytest.mark.parametrize("path", ["src/.github/workflows/evil.yml", '"src/.github/workflows/evil.yml"'])
def test_scan_strips_arbitrary_first_component(path):
    patch = f"--- /dev/null\n+++ {path}\n@@ -0,0 +1 @@\n+on: push\n"
    assert any(f["path"] == ".github/workflows/evil.yml" for f in scan_patch(patch))


@pytest.mark.parametrize(
    "target", ["../.github/workflows", "../../outside", "/etc/passwd", "~/.bashrc", r"..\..\outside"]
)
def test_symlink_patch_rejected(target):
    patch = (
        "diff --git a/src/link b/src/link\nnew file mode 120000\n"
        f"--- /dev/null\n+++ b/src/link\n@@ -0,0 +1 @@\n+{target}\n"
    )
    assert any(f["rule"] == "unsafe-symlink" for f in scan_patch(patch))


def test_migrate_two_leads_takeover_claim_done(tmp_path, monkeypatch):
    fixture = Path(__file__).parents[1] / "fixtures" / "v1-board"
    root = tmp_path / "migrated"
    shutil.copytree(fixture, root / ".board")
    (root / ".board" / "agents.json").write_text(
        json.dumps({"lead": {"role": "lead"}, "codex-pr-closeout": {"role": "lead"}})
    )
    result = migrate.upgrade(root / ".board", registry.home())
    board = Board.open(root)
    assert result["lead"] == "lead"
    secret_path = Path(result["secret_file"])
    assert list(secret_path.parent.glob("*.token")) == [secret_path]
    board.lead_takeover(lead_secret=secret_path.read_text().strip())
    caller = board.caller(lead_secret=secret_path.read_text().strip())
    board.set_setting(caller, "require_review", 0)
    board.set_setting(caller, "require_verify", False)
    board.new_card(caller, "migration probe", card_id="probe", locks="docs")
    claim = board.claim("probe", caller)
    caller.fence = claim["fence"]
    board.move("probe", "done", caller)
    assert board.card("probe")["lane"] == "done"
    before = board.agents()
    assert cli.main(["up", "--board", str(root)]) == 0
    assert board.agents() == before
    monkeypatch.setenv("RAIDLOCK_SESSION", "another-unidentified-session")
    assert cli.main(["up", "--board", str(root)]) != 0
    assert board.agents() == before
    assert cli.main(["up", "--board", str(root), "--join", "--runtime", "shell"]) == 0


def test_ambiguous_v1_lead_requires_selection():
    agents = {"one": {"role": "lead"}, "two": {"role": "lead"}}
    with pytest.raises(Precondition, match="--lead"):
        migrate._lead(agents)
    assert migrate._lead(agents, "two") == "two"


@pytest.mark.parametrize("target", ["../docs/private", "../.github/workflows", "../../outside", "/etc/passwd"])
def test_runner_patch_rejects_unleased_symlink(target):
    from raidlock.resume import validate_patch
    from raidlock.runners.base import Redactor

    patch = (
        "diff --git a/src/link b/src/link\nnew file mode 120000\n"
        "--- /dev/null\n+++ b/src/link\n@@ -0,0 +1 @@\n+" + target + "\n"
    ).encode()
    _, problems = validate_patch(patch, ["src"], False, Redactor([]))
    assert any("symlink" in problem for problem in problems)


def test_clone_scan_rejects_symlink_target(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    W.git(repo, "init", "--quiet")
    W.git(repo, *W.IDENT, "commit", "--allow-empty", "--quiet", "-m", "base")
    (repo / "src").mkdir()
    (repo / "src" / "link").symlink_to("../.github/workflows")
    assert any(f["rule"] == "unsafe-symlink" for f in scan_patch(repo))


def test_symlink_chain_cannot_hide_denied_target():
    from raidlock.runners.policy import check_links

    assert check_links({"src/a": "b", "src/b": "../.github/workflows"}, ["src"]) == ["src/a", "src/b"]


def test_verification_key_refuses_workspace_home(world):
    world.card("key")
    world.board.home = str(world.root / "private")
    with pytest.raises(Precondition, match="outside the workspace"):
        receipts.sign(world.board, {"card": "key"})


def test_migration_docs_include_takeover_and_private_key():
    root = Path(__file__).parents[2]
    operations = (root / "docs/guides/operations.md").read_text()
    verification = (root / "docs/guides/verification.md").read_text()
    security = (root / "SECURITY.md").read_text()
    assert "lead takeover --lead-secret" in operations and "raid up --join" in operations
    assert "tokens/<board uid>/verify.key" in verification and "tokens/<board uid>/verify.key" in security


def test_scan_symlink_needs_known_lease():
    patch = (
        "diff --git a/src/link b/src/link\nnew file mode 120000\n"
        "--- /dev/null\n+++ b/src/link\n@@ -0,0 +1 @@\n+../docs/file\n"
    )
    assert scan_patch(patch)
    assert scan_patch(patch, rels=["src"])
    assert scan_patch(patch, rels=["src", "docs"]) == []


def test_symlink_scope_checks_changed_links_and_resolves_unchanged_links():
    from raidlock.runners.policy import check_links

    links = {"vendor/link": "../vendor/target", "src/new": "existing", "src/existing": "../docs/file"}
    assert check_links(links, ["src"], paths=["src/new"]) == ["src/new"]
    assert check_links(links, ["src"], paths=["src/ordinary"]) == []


def test_symlink_parent_traversal_expands_intermediate_link_first():
    from raidlock.runners.policy import check_links

    links = {"src/new": "alias/../file", "src/alias": "../../outside"}
    assert check_links(links, ["src"], paths=["src/new"]) == ["src/new"]


def test_lead_git_add_ignores_worker_filter_and_preserves_index(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    W.git(repo, "init", "--quiet")
    marker = tmp_path / "filter-ran"
    script = tmp_path / "filter.sh"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n")
    script.chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "config", "filter.pwn.clean", str(script)], check=True)
    (repo / ".gitattributes").write_text("* filter=pwn\n")
    (repo / "file").write_text("data\n")
    W.git(repo, "add", "-A")
    W.git(repo, *W.IDENT, "commit", "--quiet", "-m", "safe add")
    assert not marker.exists()
    assert W.git(repo, "show", "HEAD:file") == "data\n"


def test_scan_cli_enforces_card_scope(world, tmp_path):
    world.card("scan", locks="app/src")
    patch = tmp_path / "link.patch"
    prefix = "diff --git a/src/link b/src/link\nnew file mode 120000\n--- /dev/null\n+++ b/src/link\n@@ -0,0 +1 @@\n+"
    patch.write_text(prefix + "../docs/file\n")
    args = ["scan", str(patch), "--board", str(world.root), "--card", "scan"]
    assert cli.main(args) != 0
    patch.write_text(prefix + "file\n")
    assert cli.main(args) == 0


def test_migration_selection_must_name_confirmed_lead():
    agents = {"one": {"role": "lead"}, "two": {"role": "lead"}}
    with pytest.raises(Precondition, match="v1 lead"):
        migrate._lead(agents, "lead")


def test_migrated_up_requires_explicit_join(tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures" / "v1-board"
    root = tmp_path / "migrated"
    shutil.copytree(fixture, root / ".board")
    migrate.upgrade(root / ".board", registry.home())
    board = Board.open(root)
    before = board.agents()
    assert cli.main(["up", "--board", str(root)]) != 0
    assert board.agents() == before
    assert cli.main(["up", "--board", str(root), "--join", "--runtime", "shell"]) == 0
