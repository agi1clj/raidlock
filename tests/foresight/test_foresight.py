import json
import subprocess
from types import SimpleNamespace

import pytest

from raidlock import cli, foresight
from raidlock.core import Board


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIDLOCK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "shared.txt").write_text("base\n")
    (repo / "separate.txt").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    git(repo, "checkout", "-b", "other")
    (repo / "shared.txt").write_text("other\n")
    git(repo, "commit", "-am", "other")
    board, token = Board.init(repo, lead="lead")
    lead = board.caller(lead_secret=token)
    board.join(lead, agent="other-agent")
    board.new_card(lead, "Other", card_id="other-card", locks=[])
    board.claim("other-card", lead, for_agent="other-agent", checkout=str(repo))
    git(repo, "checkout", "-b", "ours", "main")
    board.new_card(lead, "Ours", card_id="ours", locks=[])
    monkeypatch.setenv("RAIDLOCK_LEAD_SECRET", token)
    monkeypatch.chdir(repo)
    return SimpleNamespace(repo=repo, board=board, lead=lead, root=tmp_path)


def test_conflict(world):
    (world.repo / "shared.txt").write_text("ours\n")
    git(world.repo, "commit", "-am", "ours")
    result = foresight.predict(world.board, "ours")
    assert result["checked"] == 1
    assert not result["skipped"]
    (conflict,) = result["conflicts"]
    assert conflict["files"] == ["shared.txt"]
    assert conflict["agent"] == "other-agent"
    assert conflict["card"] == "other-card"
    assert "Wait for card other-card" in conflict["suggestion"]


def test_different_files_clean(world):
    (world.repo / "separate.txt").write_text("ours\n")
    git(world.repo, "commit", "-am", "ours")
    result = foresight.predict(world.board, "ours")
    assert result["checked"] == 1
    assert result["conflicts"] == result["skipped"] == result["messages"] == []


def test_missing_branch(world):
    git(world.repo, "branch", "-D", "other")
    result = foresight.predict(world.board, "ours")
    assert result["conflicts"] == []
    assert "missing locally" in result["skipped"][0]["message"]


@pytest.mark.parametrize("staged", [False, True])
def test_working_tree_preserved(world, staged):
    (world.repo / "shared.txt").write_text("uncommitted\n")
    if staged:
        git(world.repo, "add", "shared.txt")
    before = git(world.repo, "status", "--porcelain"), git(world.repo, "rev-parse", "HEAD")
    index = (world.repo / ".git" / "index").read_bytes()
    objects = sorted(str(p) for p in (world.repo / ".git" / "objects").rglob("*"))
    result = foresight.predict(world.board, "ours")
    assert result["conflicts"][0]["files"] == ["shared.txt"]
    assert before == (git(world.repo, "status", "--porcelain"), git(world.repo, "rev-parse", "HEAD"))
    assert index == (world.repo / ".git" / "index").read_bytes()
    assert objects == sorted(str(p) for p in (world.repo / ".git" / "objects").rglob("*"))


def test_expired_and_unrelated_leases(world):
    world.board.index.update_lease(world.board.uid, "other-card", expires="2000-01-01T00:00:00Z")
    assert foresight.predict(world.board, "ours")["checked"] == 0


def test_old_git_skips(world, monkeypatch):
    original = foresight._git

    def run(path, *args, **kwargs):
        if args == ("--version",):
            return SimpleNamespace(stdout="git version 2.37.0")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(foresight, "_git", run)
    result = foresight.predict(world.board, "ours")
    assert "git >= 2.38" in result["messages"][0]
    assert result["checked"] == 0


def test_pair_timeout(world, monkeypatch):
    original = foresight._git

    def run(path, *args, **kwargs):
        if args[0] == "merge-tree":
            assert 0 < kwargs["deadline"] - foresight.time.monotonic() <= 5
            raise subprocess.TimeoutExpired(args, 5)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(foresight, "_git", run)
    result = foresight.predict(world.board, "ours")
    assert "timeout" in result["skipped"][0]["message"]


@pytest.mark.parametrize("conflict", [False, True])
def test_registered_cli(world, monkeypatch, capsys, conflict):
    if conflict:
        (world.repo / "shared.txt").write_text("ours\n")
    monkeypatch.setattr(cli, "COMMANDS", dict(cli.COMMANDS))
    monkeypatch.setattr(cli, "_entry_points", lambda: [])
    cli.load_extensions()
    code = cli.main(["foresight", "ours", "--board", str(world.repo), "--json"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert bool(output["warnings"]) == conflict
    assert bool(output["conflicts"]) == conflict
    assert output["checked"] == 1


def test_cap(world, monkeypatch):
    leases = world.board.leases()
    others = {f"card-{i}": {**leases["other-card"], "card": f"card-{i}"} for i in range(25)}
    monkeypatch.setattr(world.board.index, "leases", lambda ws: others)
    result = foresight.predict(world.board, "ours")
    assert result["checked"] == 20
    assert "5 leases skipped" in result["messages"][0]


def test_cross_workspace_same_identity(world):
    git(world.repo, "remote", "add", "origin", "https://example.com/team/repo.git")
    other = world.root / "clone"
    git(world.root, "clone", str(world.repo), str(other))
    git(other, "remote", "set-url", "origin", "https://example.com/team/repo.git")
    git(other, "checkout", "other")
    board, token = Board.init(other, lead="lead")
    lead = board.caller(lead_secret=token)
    board.new_card(lead, "Across", card_id="across", locks=[])
    board.claim("across", lead, checkout=str(other))
    (world.repo / "shared.txt").write_text("ours\n")
    result = foresight.predict(world.board, "ours")
    assert {item["card"] for item in result["conflicts"]} == {"other-card", "across"}


def test_recorded_branch_when_checkout_has_moved(world):
    (world.repo / "shared.txt").write_text("ours\n")
    git(world.repo, "commit", "-am", "ours")
    world.board.claim("ours", world.lead, checkout=str(world.repo))
    git(world.repo, "checkout", "main")
    objects = sorted(str(p) for p in (world.repo / ".git" / "objects").rglob("*"))
    result = foresight.predict(world.board, "ours")
    assert result["conflicts"][0]["files"] == ["shared.txt"]
    assert git(world.repo, "branch", "--show-current") == "main"
    assert objects == sorted(str(p) for p in (world.repo / ".git" / "objects").rglob("*"))


def test_unrelated_repository_ignored(world, monkeypatch):
    lease = world.board.leases()["other-card"]
    unrelated = world.root / "unrelated"
    unrelated.mkdir()
    git(unrelated, "init", "-b", "other")
    lease["checkout"]["path"] = str(unrelated)
    monkeypatch.setattr(world.board.index, "leases", lambda ws: {"other-card": lease})
    result = foresight.predict(world.board, "ours")
    assert result["checked"] == 0
    assert result["skipped"] == []


@pytest.mark.parametrize("fields", [None, "minimal", "card", "all"])
def test_conflict_warns_and_claim_succeeds(world, capsys, fields):
    (world.repo / "shared.txt").write_text("ours\n")
    args = ["claim", "ours", "--board", str(world.repo), "--checkout", str(world.repo)]
    if fields is not None:
        args.append("--json")
        if fields != "minimal":
            args.extend(["--fields", fields])
    assert cli.main(args) == 0
    output = capsys.readouterr().out
    if fields is not None:
        result = json.loads(output)
        if fields in {"minimal", "card"}:
            assert "warnings" not in result
        else:
            assert result["fence"] > 0
        output = "\n".join(result.get("warnings", []))
    if fields in {None, "all"}:
        assert "Warning: Foresight:" in output
        assert "shared.txt: conflict with other-agent" in output
    assert world.board.card("ours")["lane"] == "doing"
    assert world.board.leases()["ours"]["fence"] > 0


def test_text_conflict_exits_zero(world, capsys):
    (world.repo / "shared.txt").write_text("ours\n")
    assert cli.main(["foresight", "ours", "--board", str(world.repo)]) == 0
    assert "Warning: Foresight:" in capsys.readouterr().out
