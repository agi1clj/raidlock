import json
import os
import shlex
import subprocess

import pytest
from portable import SH
from runhelpers import git

from raidlock import cli
from raidlock.core.errors import IdentityError, Precondition, StaleLead
from raidlock.runners import isolation
from raidlock.runners import land as land_module
from raidlock.runners.land import land
from raidlock.runners.runner import delegate

DONE = '{"status": "done", "evidence": ["ok"], "paths_changed": [], "open_questions": []}'


@pytest.fixture(autouse=True)
def sandbox(monkeypatch):
    def run(cmd, cwd, *args, **kwargs):
        code = subprocess.run([SH, "-c", cmd], cwd=cwd, check=False).returncode
        return code, "ok" if code == 0 else "failed", 1

    monkeypatch.setattr(isolation, "mode", lambda: "sandbox-exec")
    monkeypatch.setattr(isolation, "run", run)


def handed_back(ws, cid="land"):
    ws.card(cid, locks="app/src", verify_cmd="test -f src/land.txt")
    cmd = [SH, "-c", f"printf 'x' > src/land.txt; echo '{DONE}'"]
    result = delegate(ws.board, ws.lead, cid, "shell", cmd=shlex.join(cmd), heartbeat=0.05)
    assert ws.board.card(cid)["lane"] == "review"
    return result


def head(repo):
    return git(repo, "rev-parse", "HEAD").strip()


def status(repo):
    return git(repo, "status", "--porcelain", "--untracked-files=all")


def test_land_applies_commits_verifies_and_notes_the_card(ws):
    handed_back(ws)
    before = head(ws.repo)
    r = land(ws.board, ws.lead, "land", cwd=str(ws.repo))
    assert r["commit"] == head(ws.repo) != before
    assert git(ws.repo, "rev-parse", "HEAD~1").strip() == before
    assert git(ws.repo, "log", "-1", "--format=%s").strip() == "feat(land): land"
    assert (ws.repo / "src" / "land.txt").read_text() == "x"
    assert status(ws.repo) == ""
    assert r["verify"]["passed"] and r["_exit"] == 0
    card = ws.board.card("land")
    assert card["lane"] == "review"
    assert any(r["commit"][:12] in n["text"] and "verify passed" in n["text"] for n in card["notes"])
    assert r["next"].endswith("done land")
    ws.board.move("land", "done", ws.lead)
    assert ws.board.card("land")["lane"] == "done"


def test_land_message_override_and_failed_verify(ws, monkeypatch):
    handed_back(ws)
    monkeypatch.setattr(isolation, "run", lambda *args, **kwargs: (1, "failed", 1))
    r = land(ws.board, ws.lead, "land", cwd=str(ws.repo), message="fix(land): custom")
    assert git(ws.repo, "log", "-1", "--format=%s").strip() == "fix(land): custom"
    assert not r["verify"]["passed"] and r["_exit"] == 2
    assert "verify failed" in r["next"]


def test_dirty_tree_is_refused(ws):
    handed_back(ws)
    before = head(ws.repo)
    (ws.repo / "stray.txt").write_text("wip")
    with pytest.raises(Precondition, match="clean checkout"):
        land(ws.board, ws.lead, "land", cwd=str(ws.repo))
    assert head(ws.repo) == before
    assert not (ws.repo / "src" / "land.txt").exists()


def test_patch_outside_the_locks_is_refused(ws, tmp_path):
    result = handed_back(ws)
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(ws.repo), str(other))
    (other / "README.md").write_text("changed\n")
    (other / "src" / "land.txt").write_text("x")
    git(other, "add", "-A")
    with open(result["patch"], "w", encoding="utf-8") as f:
        f.write(git(other, "diff", "--cached", "--binary"))
    before = head(ws.repo)
    with pytest.raises(Precondition, match="outside the card's locks: README.md"):
        land(ws.board, ws.lead, "land", cwd=str(ws.repo))
    assert head(ws.repo) == before
    assert status(ws.repo) == ""


def test_crafted_header_that_escapes_the_locks_is_refused(ws):
    result = handed_back(ws)
    with open(result["patch"], "w", encoding="utf-8") as f:
        f.write("diff --git a/src b/src/z b/src b/src/z\nnew file mode 100644\nindex 0000000..e69de29\n")
    before = head(ws.repo)
    with pytest.raises(Precondition, match="disagree with git apply: src b/src/z"):
        land(ws.board, ws.lead, "land", cwd=str(ws.repo))
    assert head(ws.repo) == before
    assert status(ws.repo) == ""


def test_patch_swapped_after_the_check_is_not_applied(ws, tmp_path, monkeypatch):
    result = handed_back(ws)
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(ws.repo), str(other))
    (other / "README.md").write_text("swapped\n")
    git(other, "add", "-A")
    swapped = git(other, "diff", "--cached", "--binary")
    real = land_module._check_paths

    def swap(*args, **kwargs):
        checked = real(*args, **kwargs)
        with open(result["patch"], "w", encoding="utf-8") as f:
            f.write(swapped)
        return checked

    monkeypatch.setattr(land_module, "_check_paths", swap)
    land(ws.board, ws.lead, "land", cwd=str(ws.repo))
    assert git(ws.repo, "show", "--name-only", "--format=", "HEAD").split() == ["src/land.txt"]
    assert (ws.repo / "README.md").read_text() != "swapped\n"


def test_apply_failure_leaves_no_commit(ws):
    handed_back(ws)
    (ws.repo / "src" / "land.txt").write_text("conflict")
    git(ws.repo, "add", "-A")
    git(ws.repo, "commit", "-qm", "conflict")
    before = head(ws.repo)
    notes = len(ws.board.card("land")["notes"])
    with pytest.raises(Precondition, match="does not apply"):
        land(ws.board, ws.lead, "land", cwd=str(ws.repo))
    assert head(ws.repo) == before
    assert status(ws.repo) == ""
    assert (ws.repo / "src" / "land.txt").read_text() == "conflict"
    assert len(ws.board.card("land")["notes"]) == notes


def test_commit_failure_rolls_back(ws, monkeypatch):
    handed_back(ws)
    real = land_module._git

    def failing(where, *args, **kwargs):
        if "commit" in args:
            return subprocess.CompletedProcess(args, 1, "", "refused")
        return real(where, *args, **kwargs)

    monkeypatch.setattr(land_module, "_git", failing)
    before = head(ws.repo)
    with pytest.raises(Precondition, match="nothing was committed"):
        land(ws.board, ws.lead, "land", cwd=str(ws.repo))
    assert head(ws.repo) == before
    assert status(ws.repo) == ""


def test_land_runs_no_repository_hooks(ws, tmp_path):
    handed_back(ws)
    marker = tmp_path / "hook-ran"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for name in ("pre-commit", "commit-msg", "post-commit", "post-index-change"):
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\necho {name} >> {shlex.quote(str(marker))}\n")
        os.chmod(hook, 0o700)
    git(ws.repo, "config", "core.hooksPath", str(hooks))
    before = head(ws.repo)
    r = land(ws.board, ws.lead, "land", cwd=str(ws.repo))
    assert r["commit"] == head(ws.repo) != before
    assert not marker.exists()
    assert git(ws.repo, "log", "-1", "--format=%an <%ae>").strip() == "t <t@t>"


def test_lead_handover_before_apply_refuses_without_committing(ws, monkeypatch):
    handed_back(ws)
    real = land_module._check_paths

    def handover(*args, **kwargs):
        ws.board.lead_handover(ws.lead, "next-session")
        return real(*args, **kwargs)

    monkeypatch.setattr(land_module, "_check_paths", handover)
    before = head(ws.repo)
    with pytest.raises(StaleLead):
        land(ws.board, ws.lead, "land", cwd=str(ws.repo))
    assert head(ws.repo) == before
    assert status(ws.repo) == ""


def test_non_lead_is_refused(ws):
    handed_back(ws)
    joined = ws.board.join(ws.lead, agent="w-one", team="core")
    worker = ws.board.caller(agent="w-one", token=joined["token"])
    before = head(ws.repo)
    with pytest.raises(IdentityError, match="lead"):
        land(ws.board, worker, "land", cwd=str(ws.repo))
    assert head(ws.repo) == before
    assert status(ws.repo) == ""


def test_unclaimed_card_is_refused(ws):
    ws.card("idle", locks="app/src")
    with pytest.raises(Precondition, match="only claimed work"):
        land(ws.board, ws.lead, "idle", cwd=str(ws.repo))


def test_land_json_through_the_cli(ws, tmp_path, capsys):
    handed_back(ws)
    secret = tmp_path / "lead.secret"
    secret.write_text(ws.token)
    os.chmod(secret, 0o600)
    code = cli.main(
        ["--board", str(ws.root), "--lead-secret", str(secret), "--json", "land", "land", "--cwd", str(ws.repo)]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["ok"] and out["commit"] == head(ws.repo) and out["verify"]["passed"]
    assert out["lane"] == "review" and out["next"].endswith("done land")
