import os
import stat
import sys
import tomllib
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from corehelpers import make_repo

from raidlock import cli
from raidlock.core import Board

GLOBALS = ["--agent", "x", "--token", "y", "--json", "--fence", "3", "--fields", "all", "--debug", "--board", "b"]
PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _value(action):
    if action.choices:
        return str(next(iter(action.choices)))
    if action.type in (int, float):
        return "1"
    return "v"


def _argv(name):
    p = cli.command_parser(name)
    positionals, required = [], []
    for action in p._actions:
        if action.option_strings:
            if action.required:
                required += [action.option_strings[0], _value(action)]
        elif action.dest != "help":
            positionals.append(_value(action))
    for group in p._mutually_exclusive_groups:
        if group.required:
            action = group._group_actions[0]
            required += [action.option_strings[0], _value(action)]
    return positionals, required


@pytest.mark.parametrize("name", sorted(cli.COMMANDS))
def test_every_command_accepts_trailing_global_flags(name):
    positionals, required = _argv(name)
    args = cli.parse([name, *positionals, *required, *GLOBALS])
    assert (args._cmd, args.agent, args.token, args.json, args.fence, args.fields, args.debug, args.board) == (
        name,
        "x",
        "y",
        True,
        3,
        "all",
        True,
        "b",
    )


@pytest.mark.parametrize("name", sorted(cli.COMMANDS))
def test_every_command_accepts_global_flags_before_and_between(name):
    positionals, required = _argv(name)
    argv = ["--json", name, "--agent", "x"]
    for value in positionals:
        argv += [value, "--token", "y"] if "--token" not in argv else [value]
    args = cli.parse(argv + required)
    assert args._cmd == name and args.agent == "x" and args.json


@pytest.mark.parametrize(
    "argv",
    [
        ["team", "add", "foo", "--agent", "x", "--token", "y"],
        ["team", "add", "--agent", "x", "foo"],
        ["team", "--agent", "x", "add", "foo"],
        ["team", "add", "--max", "2", "foo", "--token", "y", "--agent", "x"],
        ["--agent", "x", "team", "add", "foo"],
    ],
)
def test_team_add_with_interleaved_flags(argv):
    args = cli.parse(argv)
    assert (args.action, args.name, args.agent) == ("add", "foo", "x")


def test_command_specific_agent_aliases_win():
    assert cli.parse(["review", "c1", "--by", "w", "--verdict", "approve"]).agent == "w"
    assert cli.parse(["msg", "hi", "--to", "w", "--from", "lead", "--json"]).agent == "lead"
    assert cli.parse(["msg", "hi", "--to", "w"]).token is None


def test_unknown_command_and_flag_are_usage_errors():
    with pytest.raises(cli.UsageError):
        cli.parse(["nope"])
    with pytest.raises(cli.UsageError):
        cli.parse(["show", "--nope"])
    with pytest.raises(cli.UsageError):
        cli.parse(["show", "--ag", "x"])


def test_team_add_end_to_end_with_trailing_flags(tmp_path, capsys):
    ws = tmp_path / "ws"
    make_repo(ws / "app")
    board, _ = Board.init(ws)
    assert (
        cli.main(
            [
                "team",
                "add",
                "core",
                "--lead-secret",
                str(Path(board.home) / "tokens" / board.uid / (board.lead + ".token")),
                "--max",
                "3",
                "--board",
                str(ws),
            ]
        )
        == 0
    )
    assert Board.open(ws).teams()["core"]["max"] == 3


def test_prog_name():
    assert cli.prog_name("/usr/local/bin/raid") == "raid"
    assert cli.prog_name("C:\\bin\\raid.exe".replace("\\", os.sep)) == "raid"
    assert cli.prog_name("/x/raidlock") == "raidlock"
    assert cli.prog_name("/x/raidlock/__main__.py") == "raidlock"


@pytest.mark.parametrize("prog", ["raid", "raidlock"])
def test_help_and_hints_use_invoked_name(prog, tmp_path, capsys):
    assert cli.main(["--help"], prog=prog) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"{prog} ") and f"{prog} help advanced" in out
    other = "raidlock" if prog == "raid" else "raid"
    assert f"{other} help" not in out
    with pytest.raises(SystemExit):
        cli.main(["team", "--help"], prog=prog)
    assert f"usage: {prog} team" in capsys.readouterr().out
    ws = tmp_path / "raidlock"
    make_repo(ws / "app")
    Board.init(ws)
    assert cli.main(["show", "missing", "--board", str(ws)], prog=prog) != 0
    err = capsys.readouterr().err
    assert err.startswith(f"{prog}: ") and f"Next: {prog} " in err
    assert cli.main(["help", "team", "--board", str(ws)], prog=prog) == 0
    assert f"usage: {prog} team" in capsys.readouterr().out
    assert cli.main(["init", str(ws)], prog=prog) == 0
    out = capsys.readouterr().out
    assert "Board for raidlock already exists" in out and f"Next: {prog} show" in out


def test_rename_scopes_to_hints():
    text = (
        "Board for raidlock already exists.\nNext: raidlock show\n  raidlock ui   # x\n"
        "export RAIDLOCK_FENCE=1  # next: raidlock note c1"
    )
    assert (
        cli.rename(text, "raid") == "Board for raidlock already exists.\nNext: raid show\n  raid ui   # x\n"
        "export RAIDLOCK_FENCE=1  # next: raid note c1"
    )
    assert cli.rename(text, "raidlock") == text


def test_pyproject_scripts_and_entry_points():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert data["project"]["scripts"] == {"raidlock": "raidlock.cli:main", "raid": "raidlock.cli:main"}
    eps = data["project"]["entry-points"]["raidlock.commands"]
    assert set(eps) == {
        "notify",
        "snapshot",
        "reputation",
        "tui",
        "recipes",
        "guard",
        "mcp",
        "delegate",
        "ui",
        "suggest-locks",
        "foresight",
        "limits",
        "lore",
        "scan",
        "budget",
        "pr",
        "pr-sync",
        "bench",
        "next",
        "resume",
        "duel",
        "resurrect",
        "approvals",
        "map",
        "replay",
        "graph",
        "frames",
        "achievements",
        "treasury",
    }
    modules = {value.split(":")[0] for value in eps.values()}
    assert modules <= set(cli.EXTENSION_MODULES)
    assert "pytest" in " ".join(data["dependency-groups"]["dev"]) and "hypothesis" in " ".join(
        data["project"]["optional-dependencies"]["test"]
    )


def test_missing_extension_modules_fail_softly(monkeypatch, capsys):
    ghost = EntryPoint(name="ghost", value="raidlock.ghost_missing_module:register_cli", group="raidlock.commands")
    attr = EntryPoint(name="attr", value="raidlock.core.clock:register_cli", group="raidlock.commands")
    monkeypatch.setattr(cli, "_entry_points", lambda: [ghost, attr])
    cli.load_extensions()
    assert capsys.readouterr().err == ""
    cli.load_extensions(debug=True)
    err = capsys.readouterr().err
    assert "ghost" in err and "skipped" in err and "attr" in err


def _script(path, body):
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.mark.skipif(os.name != "posix", reason="POSIX PATH lookup")
def test_doctor_reports_shadowing_raid(tmp_path, monkeypatch, capsys):
    ws = tmp_path / "ws"
    make_repo(ws / "app")
    Board.init(ws)
    other = tmp_path / "other-bin"
    other.mkdir()
    _script(other / "raid", "#!/bin/sh\necho some other raid\n")
    ours = tmp_path / "venv-bin"
    ours.mkdir()
    mine = _script(ours / "raid", '#!/bin/sh\nexec python -m raidlock "$@"\n')
    monkeypatch.setattr(cli, "own_script", lambda name="raid": str(mine))
    monkeypatch.setenv("PATH", os.pathsep.join([str(other), str(ours)]))
    assert cli.main(["doctor", "--board", str(ws)], prog="raid") == 3
    out = capsys.readouterr().out
    assert f"another raid at {other / 'raid'}" in out and "use raidlock instead or fix PATH" in out
    monkeypatch.setenv("PATH", os.pathsep.join([str(ours), str(other)]))
    assert cli.main(["doctor", "--board", str(ws), "--json"]) == 0
    assert '"healthy":true' in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["read-only", "under-a-file"])
def test_doctor_flags_unwritable_home(tmp_path, monkeypatch, capsys, kind):
    if kind == "read-only" and (os.name != "posix" or os.geteuid() == 0):
        pytest.skip("needs POSIX directory permissions enforced for this user")
    if kind == "read-only":
        home = tmp_path / "ro"
        home.mkdir()
        home.chmod(0o500)
    else:
        (tmp_path / "file").write_text("x")
        home = tmp_path / "file" / "home"
    monkeypatch.setenv("RAIDLOCK_HOME", str(home))
    monkeypatch.setenv("PATH", str(tmp_path))
    try:
        assert cli.main(["doctor"]) == 3
    finally:
        if kind == "read-only":
            home.chmod(0o700)
    out = capsys.readouterr().out
    assert "FAIL home" in out and sys.version.split()[0] in out
