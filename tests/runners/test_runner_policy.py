import pytest
from runhelpers import make_repo

from raidlock.core.errors import Precondition
from raidlock.runners import policy


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        ".claude/settings.json",
        ".board/cards/x.json",
        "keys/server.pem",
        ".env",
        ".env.local",
        "config/secrets/db.yaml",
        "secrets",
        "secrets.yaml",
        ".Claude/x",
        "src/.ENV",
    ],
)
def test_denied(path):
    assert policy.denied(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/main.py",
        ".github/dependabot.yml",
        "docs/secret-sauce.md",
        "environment.py",
        "src/claude/x.py",
        "my.pem.txt",
    ],
)
def test_allowed(path):
    assert not policy.denied(path)


def test_contained_and_check():
    rels = ["src/raidlock/runners", "tests/runners"]
    assert policy.contained("src/raidlock/runners/base.py", rels)
    assert not policy.contained("src/raidlock/runnersx/a.py", rels)
    assert policy.contained("anything", [""])
    found = policy.check(["tests/runners/t.py", "src/raidlock/cli.py", "tests/runners/.env"], rels)
    assert found == {"outside": ["src/raidlock/cli.py"], "denied": ["tests/runners/.env"]}


def test_target_single_repo(tmp_path):
    ws = tmp_path / "ws"
    make_repo(ws / "app", {"a.txt": "a"})
    make_repo(ws / "lib", {"b.txt": "b"})
    (ws / ".board").mkdir()
    t = policy.target(str(ws), str(ws / ".board"), ["app/src", "app/tests"])
    assert t["repo"] == "app" and t["rels"] == ["src", "tests"] and t["path"].endswith("app")
    whole = policy.target(str(ws), str(ws / ".board"), ["lib"])
    assert whole["rels"] == [""]
    with pytest.raises(Precondition, match="span several repos"):
        policy.target(str(ws), str(ws / ".board"), ["app/src", "lib/x"])
    with pytest.raises(Precondition, match="matches no unambiguous repo") as exc:
        policy.target(str(ws), str(ws / ".board"), ["nope/x"])
    assert str(exc.value) == f"lock nope/x matches no unambiguous repo in workspace {ws}"
    assert exc.value.details == {"lock": "nope/x"}
    assert exc.value.hint == "prefix the lock with a workspace repo: app, lib"
    with pytest.raises(Precondition, match="no locks"):
        policy.target(str(ws), str(ws / ".board"), [])


def test_target_workspace_is_the_repo(tmp_path):
    ws = make_repo(tmp_path / "raidlock", {"src/x.py": "x"})
    (ws / ".board").mkdir()
    t = policy.target(str(ws), str(ws / ".board"), ["raidlock/src/raidlock/runners"])
    assert t["repo"] == "raidlock" and t["rels"] == ["src/raidlock/runners"]
