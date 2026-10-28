import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from corehelpers import make_repo, run_cli

from raidlock import cli
from raidlock.core import IdentityError, Precondition, lead, receipts
from raidlock.core.hints import lead_cmd
from raidlock.core.identity import long_runtime, short_runtime
from raidlock.runners import isolation, workspace

MAIN = ("new", "join", "claim", "verify", "done", "review", "delegate", "edit", "quickstart", "up")


@pytest.fixture(autouse=True)
def process_anchor(monkeypatch):
    monkeypatch.setattr(
        lead,
        "process_table",
        lambda: {os.getpid(): (os.getppid(), "test-process", "python"), os.getppid(): (0, "test-parent", "zsh")},
    )


@pytest.fixture(scope="module")
def parsers():
    cli.load_extensions()
    return {name: cli.command_parser(name, "raid") for name in sorted(cli.COMMANDS)}


def test_every_flag_and_argument_has_help(parsers):
    missing = [
        (name, action.dest)
        for name, p in parsers.items()
        for action in p._actions
        if action.dest != "help" and not action.help
    ]
    assert missing == []


def test_own_options_come_before_common_options(parsers):
    for name, p in parsers.items():
        text = p.format_help().split("\n\n", 1)[1]
        assert "common options:" in text, name
        own = [
            a
            for a in p._actions
            if a.option_strings and a.dest not in cli.GLOBAL_DESTS and a.dest != "help" and a.help != argparse.SUPPRESS
        ]
        for action in own:
            assert text.index(action.option_strings[-1]) < text.index("common options:"), (name, action.dest)


def test_main_commands_show_examples(parsers):
    for name in MAIN:
        text = parsers[name].format_help()
        assert "examples:" in text, name
        assert "raid " in text.split("examples:", 1)[1]


@pytest.mark.parametrize(
    ("given", "long", "short"),
    [
        ("codex", "codex-cli", "codex"),
        ("codex-cli", "codex-cli", "codex"),
        ("claude", "claude-code", "claude"),
        ("Claude-Code", "claude-code", "claude"),
        ("gemini", "gemini-cli", "gemini"),
        ("shell", "shell", "shell"),
    ],
)
def test_runtime_aliases(given, long, short):
    assert long_runtime(given) == long
    assert short_runtime(given) == short


@pytest.mark.parametrize("value", ["codex", "codex-cli"])
def test_runner_flags_accept_both_runtime_forms(value):
    cli.load_extensions()
    assert cli.parse(["delegate", "c1", "--to", value]).to == "codex"
    assert cli.parse(["next", "--provider", value]).provider == "codex"
    assert cli.parse(["resurrect", "c1", "--to", value]).to == "codex"


def test_join_accepts_short_runtime(world):
    worker = world.agent("codex-short", runtime=long_runtime("codex"))
    assert world.board.agents()[worker.name]["runtime"] == "codex-cli"


def test_edit_sets_verify_command_and_is_lead_only(world):
    world.card("c1")
    worker = world.agent("w-edit")
    with pytest.raises(IdentityError):
        world.board.edit_card(worker, "c1", verify_cmd="true")
    r = world.board.edit_card(world.lead, "c1", verify_cmd="python3 -m unittest")
    assert r["verify_cmd"] == world.board.card("c1")["verify_cmd"] == "python3 -m unittest"
    with pytest.raises(Precondition, match="nothing to change"):
        world.board.edit_card(world.lead, "c1")


def test_edit_invalidates_earlier_receipts(world, tmp_path, monkeypatch):
    repo = tmp_path / "committed"
    repo.mkdir()
    workspace.git(str(repo), "init", "--quiet")
    (repo / "f.txt").write_text("x")
    workspace.git(str(repo), "add", "f.txt")
    workspace.git(str(repo), *workspace.IDENT, "commit", "--quiet", "-m", "fixture")
    workspace.git(str(repo), "remote", "add", "origin", "git@github.com:acme/app.git")
    monkeypatch.setattr(isolation, "mode", lambda: "test-isolation")
    monkeypatch.setattr(isolation, "run", lambda *a, **k: (0, "ok", 1))
    world.card("c1", verify_cmd="true")
    result = world.board.verify("c1", world.lead, cwd=str(repo))
    assert receipts.valid(world.board, world.board.card("c1"), result)
    world.board.edit_card(world.lead, "c1", verify_cmd="python3 -m unittest")
    assert not receipts.valid(world.board, world.board.card("c1"), result)
    with pytest.raises(Precondition, match="valid verification receipt") as caught:
        world.board.move("c1", "done", world.lead)
    assert caught.value.hint == lead_cmd(world.board, "verify c1")


def test_gate_errors_name_the_fix(world):
    world.card("c1")
    with pytest.raises(Precondition, match="no verify command") as caught:
        world.board.verify("c1", world.lead)
    assert "edit c1 --verify-cmd" in caught.value.hint and "--lead-secret" in caught.value.hint
    world.board.set_setting(world.lead, "require_verify", "true")
    with pytest.raises(Precondition, match="valid verification receipt") as caught:
        world.board.move("c1", "done", world.lead)
    assert "edit c1 --verify-cmd" in caught.value.hint
    assert "config set require_verify false" in caught.value.hint


@pytest.fixture
def shell(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    env = {"RAIDLOCK_HOME": str(tmp_path / "home"), "HOME": str(tmp_path / "userhome")}
    for key in ("RAIDLOCK_AGENT", "RAIDLOCK_TOKEN", "RAIDLOCK_CAP", "RAIDLOCK_FENCE", "RAIDLOCK_BOARD"):
        env[key] = ""

    def call(*args, cwd=ws, extra=None):
        return run_cli(*args, env={**env, **(extra or {})}, cwd=str(cwd))

    call.ws = ws
    return call


def test_quickstart_demo_builds_a_repo_with_a_failing_test(shell):
    r = shell("quickstart", "--demo", "--runtime", "human", "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    repo = Path(out["demo_repo"])
    assert (repo / ".git").is_dir() and (repo / "src/checkout/cart.py").is_file()
    assert workspace.git(str(repo), "status", "--porcelain") == ""
    tests = subprocess.run([sys.executable, "-m", "unittest"], cwd=repo, capture_output=True, text=True, check=False)
    assert tests.returncode != 0 and "test_rejects_empty_cart" in tests.stderr
    again = shell("quickstart", "--demo")
    assert again.returncode == 3 and "already exists" in again.stderr


def test_quickstart_and_lead_hints_carry_the_lead_secret(shell):
    r = shell("quickstart", "--runtime", "human")
    assert r.returncode == 0, r.stderr
    lines = [x.strip() for x in r.stdout.splitlines()]
    assert any(x.startswith("raidlock --lead-secret ") and " done <card>" in x for x in lines)
    assert any(x.startswith("raidlock --lead-secret ") and " verify <card>" in x for x in lines)
    assert not any(x.startswith("raidlock done") for x in lines)


def test_lead_command_without_identity_points_at_the_lead_secret(shell):
    assert shell("quickstart", "--runtime", "human").returncode == 0
    r = shell("new", "Fix checkout")
    assert r.returncode == 5
    hint = r.stderr.split("Next:", 1)[1]
    assert "--lead-secret " in hint and "new 'Fix checkout'" in hint
    r = shell("verify", "example")
    assert r.returncode == 5
    hint = r.stderr.split("Next:", 1)[1]
    assert "--lead-secret " in hint and "join" not in hint
    r = shell("--agent", "someone", "--token=abc", "verify", "example")
    assert r.returncode == 5
    hint = r.stderr.split("Next:", 1)[1]
    assert "--lead-secret " in hint and "verify example" in hint
    assert "--token" not in hint and "--agent" not in hint and "abc" not in hint


def test_identity_flags_are_dropped_from_lead_hints():
    argv = ["--agent", "w", "--token=t", "--cap", "c", "--fence=2", "verify", "x", "--cwd", "repo"]
    assert cli._without_identity(argv) == ["verify", "x", "--cwd", "repo"]


def test_verify_outside_a_repo_names_the_cwd_fix(shell):
    shell("quickstart", "--runtime", "human")
    secret = next((Path(shell.ws).parent / "home" / "tokens").rglob("*.token"))
    lead_args = ["--lead-secret", str(secret)]
    make_repo(shell.ws / "app")
    assert shell(*lead_args, "edit", "example", "--verify-cmd", "true").returncode == 0
    r = shell(*lead_args, "verify", "example")
    assert r.returncode == 3
    assert "has no committed HEAD" in r.stderr
    assert "verify example --cwd" in r.stderr.split("Next:", 1)[1]


def test_claim_warnings_come_after_the_export_lines(shell):
    shell("quickstart", "--runtime", "human")
    secret = next((Path(shell.ws).parent / "home" / "tokens").rglob("*.token"))
    joined = json.loads(
        shell(
            "--lead-secret",
            str(secret),
            "join",
            "--agent",
            "worker-one",
            "--team",
            "core",
            "--runtime",
            "human",
            "--json",
        ).stdout
    )
    r = shell("claim", "example", extra={"RAIDLOCK_AGENT": "worker-one", "RAIDLOCK_TOKEN": joined["token"]})
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    exports = [i for i, x in enumerate(lines) if x.startswith("export ")]
    assert exports and all(x.startswith(("export ", "# ")) for x in lines[exports[0] :])
    assert "no locks" in lines[0]


def test_done_warns_about_unclaimed_cards(world):
    world.card("c1")
    args = cli.parse(["done", "c1"])
    ctx = cli.Context(args)
    ctx.caller = lambda: world.lead
    ctx._board = world.board
    r, text = cli.cmd_done(ctx, args)
    assert any("closed from todo" in w for w in r["warnings"])
    assert "Warning: c1 was closed from todo" in text
