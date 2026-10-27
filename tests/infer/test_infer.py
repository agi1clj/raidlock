import json
import subprocess
from pathlib import Path

import pytest

from raidlock.core import Board, canonical
from raidlock.core.errors import Precondition
from raidlock.infer import register_cli, suggest_locks


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RAIDLOCK_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    root = workspace / "app"
    root.mkdir(parents=True)
    git(root, "init")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")

    def commit(*paths):
        for path in paths:
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a") as stream:
                stream.write("change\n")
        git(root, "add", ".")
        git(root, "commit", "-m", "fixture")

    commit("src/auth/login.py")
    commit("src/auth/login.py", "tests/login_test.py")
    commit("src/auth/login.py", "tests/login_test.py")
    commit("src/auth/session.py")
    commit("src/other/unrelated.py")
    (root / "CODEOWNERS").write_text("[Auth]\n/src/auth/ @auth\n[Other]\n/src/other/ @other\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "owners")
    board, _ = Board.init(workspace, auth=False)
    return board, root, commit


def test_ranking(fixture):
    board, root, commit = fixture
    results = suggest_locks(board, "Fix src/auth/login.py")
    assert [item["path"] for item in results] == [
        "app/src/auth/login.py",
        "app/tests/login_test.py",
        "app/src/auth/session.py",
    ]
    assert "2 recent commit" in results[1]["reason"]
    assert "CODEOWNERS" in results[2]["reason"]
    assert all(item["path"] == canonical(item["path"]) for item in results)


def test_collapse_only_three_siblings(fixture):
    board, root, commit = fixture
    assert suggest_locks(board, "src/auth")[0]["path"] == "app/src/auth/login.py"
    commit("src/auth/token.py")
    result = suggest_locks(board, "src/auth")
    assert result[0]["path"] == "app/src/auth"
    assert "3 sibling files" in result[0]["reason"]


def test_top_level_narrowness(fixture):
    board, root, commit = fixture
    commit("tests/a.py", "tests/b.py", "tests/c.py")
    result = suggest_locks(board, "a.py b.py c.py", limit=20)
    assert len(result) == 3
    assert "app/tests" not in {item["path"] for item in result}
    assert suggest_locks(board, "tests")[0]["path"] == "app/tests"


def test_limits_filters_empty(fixture):
    board, root, commit = fixture
    assert suggest_locks(board, "nothing relevant") == []
    assert suggest_locks(board, "login.py", repos=[]) == []
    assert len(suggest_locks(board, "login.py", repos=["app"], limit=1)) == 1
    assert suggest_locks(board, "login.py", limit=0) == []
    with pytest.raises(Precondition):
        suggest_locks(board, "login.py", repos=["missing"])


def test_cli(fixture, monkeypatch, capsys):
    from raidlock import cli

    board, root, commit = fixture
    monkeypatch.setattr(cli, "COMMANDS", dict(cli.COMMANDS))
    register_cli(cli.add_command)
    assert cli.main(["suggest-locks", "login.py", "--board", str(board.workspace), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["suggestions"][0]["path"] == "app/src/auth/login.py"


def test_root_files_never_collapse_to_repository(fixture):
    board, root, commit = fixture
    commit("one.txt", "two.txt", "three.txt")
    results = suggest_locks(board, "one.txt two.txt three.txt")
    assert {item["path"] for item in results} == {"app/one.txt", "app/two.txt", "app/three.txt"}


def test_codeowners_last_rule_and_precedence(fixture):
    board, root, commit = fixture
    (root / ".github").mkdir()
    (root / ".github/CODEOWNERS").write_text("/src/auth/ @auth\n/src/auth/session.py @other\n")
    results = suggest_locks(board, "login.py")
    assert "app/src/auth/session.py" not in {item["path"] for item in results}


def test_untracked_and_deleted_files_excluded(fixture):
    board, root, commit = fixture
    (root / "untracked.py").write_text("untracked")
    assert suggest_locks(board, "untracked.py") == []
    git(root, "rm", "tests/login_test.py")
    git(root, "commit", "-m", "delete")
    results = suggest_locks(board, "login.py")
    assert "app/tests/login_test.py" not in {item["path"] for item in results}


def test_history_window(fixture):
    board, root, commit = fixture
    tree = git(root, "rev-parse", "HEAD^{tree}").strip()
    parent = git(root, "rev-parse", "HEAD").strip()
    for _ in range(200):
        parent = git(root, "commit-tree", tree, "-p", parent, "-m", "padding").strip()
    git(root, "update-ref", "HEAD", parent)
    results = suggest_locks(board, "login.py")
    assert "app/tests/login_test.py" not in {item["path"] for item in results}


def test_wildcards_do_not_cross_path_segments():
    from raidlock.infer import _matches

    assert _matches("/src/*.py", "src/login.py")
    assert not _matches("/src/*.py", "src/auth/login.py")
    assert _matches("/src/**/*.py", "src/auth/login.py")
    assert _matches("/src/**/*.py", "src/login.py")
    assert not _matches("/src/[ab].py", "src/a.py")


def test_common_codeowners_across_rules(fixture):
    board, root, commit = fixture
    (root / "CODEOWNERS").write_text("/src/auth/login.py @auth\n/src/auth/session.py @auth\n")
    results = suggest_locks(board, "login.py")
    assert any(item["path"] == "app/src/auth/session.py" and "CODEOWNERS" in item["reason"] for item in results)


def test_sibling_collapse_does_not_expand_to_unselected_descendants(fixture):
    board, root, commit = fixture
    commit("src/auth/one.py", "src/auth/two.py", "src/auth/three.py")
    commit("src/auth/deep/unrelated.py")
    (root / "CODEOWNERS").write_text("")
    results = suggest_locks(board, "one.py two.py three.py", limit=20)
    assert "app/src/auth" not in {item["path"] for item in results}
    assert "app/src/auth/deep/unrelated.py" not in {item["path"] for item in results}


def test_hidden_paths_and_traversal_precision(fixture):
    board, root, commit = fixture
    commit(".config/settings.json")
    assert suggest_locks(board, "./.config/settings.json")[0]["path"] == "app/.config/settings.json"
    assert suggest_locks(board, "../src/auth/login.py") == []
    assert suggest_locks(board, "/src/auth/login.py") == []


def test_protected_and_symlink_paths_never_suggested(fixture, tmp_path):
    board, root, commit = fixture
    commit(".board/state.json", ".env.production", "keys/key.pem", ".github/workflows/ci.yml")
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (root / "link.txt").symlink_to(outside)
    git(root, "add", ".")
    git(root, "commit", "-m", "link")
    for text in (".board/state.json", ".env.production", "keys/key.pem", ".github/workflows/ci.yml", "link.txt"):
        assert suggest_locks(board, text) == []
    (root / "CODEOWNERS").unlink()
    (root / "CODEOWNERS").symlink_to(outside)
    assert isinstance(suggest_locks(board, "login.py"), list)


def test_deleted_unstaged_file_is_not_a_seed(fixture):
    board, root, commit = fixture
    (root / "src/auth/login.py").unlink()
    assert suggest_locks(board, "src/auth/login.py") == []


def test_bulk_migration_commit_not_used_for_cochange(fixture):
    board, root, commit = fixture
    (root / "CODEOWNERS").write_text("")
    commit("src/auth/login.py", *[f"migration/f{i}.py" for i in range(51)])
    results = suggest_locks(board, "login.py", limit=100)
    assert not any(item["path"].startswith("app/migration/") for item in results)


def test_suggestions_leave_board_unchanged(fixture):
    board, root, commit = fixture
    before = {path: path.read_bytes() for path in Path(board.dir).rglob("*") if path.is_file()}
    suggest_locks(board, "login.py")
    after = {path: path.read_bytes() for path in Path(board.dir).rglob("*") if path.is_file()}
    assert after == before


def test_directory_collapse_does_not_cover_protected_files(fixture):
    board, root, commit = fixture
    commit("src/auth/token.py", "src/auth/private.pem")
    results = suggest_locks(board, "src/auth", limit=20)
    assert "app/src/auth" not in {item["path"] for item in results}
    assert not any("private.pem" in item["path"] for item in results)


@pytest.mark.parametrize("path", ["secrets.json", ".GitHub/Workflows/ci.yml"])
def test_suggestions_respect_runner_deny_policy(fixture, path):
    board, root, commit = fixture
    commit(path)
    assert suggest_locks(board, path) == []


def test_bulk_commit_counts_protected_paths_before_filtering(fixture):
    board, root, commit = fixture
    (root / "CODEOWNERS").write_text("")
    commit("src/auth/login.py", "src/other/companion.py", *[f"private/key{i}.pem" for i in range(51)])
    results = suggest_locks(board, "login.py", limit=100)
    assert "app/src/other/companion.py" not in {item["path"] for item in results}


def test_bulk_commit_counts_deleted_paths_before_filtering(fixture):
    board, root, commit = fixture
    (root / "CODEOWNERS").write_text("")
    paths = [f"obsolete/file{i}.py" for i in range(51)]
    commit(*paths)
    for path in paths:
        (root / path).unlink()
    commit("src/auth/login.py", "src/other/companion.py")
    results = suggest_locks(board, "login.py", limit=100)
    assert "app/src/other/companion.py" not in {item["path"] for item in results}
