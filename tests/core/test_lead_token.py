import json
import os
import subprocess
import sys

import pytest
from corehelpers import SRC, make_repo, run_cli

from raidlock.core import Board, identity
from raidlock.core.errors import IdentityError


def _lead_secret(board):
    with open(identity.token_path(board.home, board.uid, board.lead), encoding="utf-8") as f:
        return f.read().strip()


def test_lead_rights_need_explicit_secret(world):
    b = world.board
    assert not b.caller(agent=b.lead, env={}).is_lead
    assert not b.caller(agent=b.lead, token="wrong", env={}).is_lead
    assert b.caller(lead_secret=world.lead_token).is_lead
    os.remove(identity.token_path(b.home, b.uid, b.lead))
    lead = b.caller(agent=b.lead, token=world.lead_token, env={"RAIDLOCK_TOKEN": world.lead_token})
    assert not lead.is_lead
    with pytest.raises(IdentityError):
        b.add_team(lead, "x", 1)


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
def test_lead_token_file_must_be_private(world):
    b = world.board
    os.chmod(identity.token_path(b.home, b.uid, b.lead), 0o644)
    with pytest.raises(IdentityError, match="0600"):
        b.caller(env={"RAIDLOCK_LEAD_SECRET_FILE": identity.token_path(b.home, b.uid, b.lead)})


def test_worker_token_env_cannot_act_as_lead(world):
    b = world.board
    w = world.agent("w-one")
    for env in ({"RAIDLOCK_TOKEN": w.token}, {"RAIDLOCK_AGENT": b.lead, "RAIDLOCK_TOKEN": w.token}):
        caller = b.caller(agent=b.lead, env=env)
        assert not caller.is_lead
        with pytest.raises(IdentityError):
            b.add_team(caller, "evil", 9)
    with pytest.raises(IdentityError):
        b.caller(agent=b.lead, env={"RAIDLOCK_AGENT_ID": "w-one", "RAIDLOCK_TOKEN": w.token})
    assert not b.caller(env={"RAIDLOCK_AGENT_ID": b.lead}).is_lead


def test_role_lead_env_is_refused(world):
    with pytest.raises(IdentityError, match="RAIDLOCK_ROLE=lead"):
        world.board.caller(agent=world.board.lead, env={"RAIDLOCK_ROLE": "lead"})


def test_lead_token_in_env_is_ignored(world):
    b = world.board
    caller = b.caller(env={"RAIDLOCK_TOKEN": world.lead_token})
    assert caller.name == "" and not caller.is_lead


def _env(tmp_path):
    keep = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "TMPDIR") if k in os.environ}
    return {
        **keep,
        "RAIDLOCK_HOME": str(tmp_path / "home"),
        "RAIDLOCK_RUNTIME": "shell",
        "PYTHONPATH": SRC,
        "PYTHONIOENCODING": "utf-8",
    }


def test_worker_cli_env_cannot_run_lead_commands(tmp_path):
    ws = tmp_path / "ws"
    make_repo(ws / "app")
    board, _ = Board.init(ws)
    base = {"RAIDLOCK_HOME": board.home, "RAIDLOCK_BOARD": str(ws)}
    w = json.loads(run_cli("join", "--agent", "w-one", "--json", env=base).stdout)
    worker = {**base, "RAIDLOCK_TOKEN": w["token"]}
    r = run_cli("team", "add", "evil", "--agent", board.lead, env=worker)
    assert r.returncode == 5
    r = run_cli("team", "add", "evil", "--agent", board.lead, env={**worker, "RAIDLOCK_ROLE": "lead"})
    assert r.returncode == 5
    r = run_cli(
        "team", "add", "good", "--lead-secret", identity.token_path(board.home, board.uid, board.lead), env=base
    )
    assert r.returncode == 0


@pytest.mark.skipif(os.name != "posix", reason="uses sh and env")
def test_up_never_prints_or_exports_the_lead_token(tmp_path):
    ws = tmp_path / "fresh"
    ws.mkdir()
    env = _env(tmp_path)
    script = (
        'out=$("$PY" -m raidlock up) || exit 1; printf "%s\\n" "$out"; '
        'eval "$(printf "%s\\n" "$out" | grep "^export ")"; echo ---ENV---; env; '
        '"$PY" -m raidlock up --json --fields all; '
        '"$PY" -m raidlock join --role lead --agent "$RAIDLOCK_AGENT" '
        '--lead-secret "$(find "$RAIDLOCK_HOME/tokens" -name "*.token")" --json --fields all'
    )
    r = subprocess.run(
        ["sh", "-c", script],
        cwd=str(ws),
        env={**env, "PY": sys.executable},
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr
    board = Board.open(ws, env={})
    secret = _lead_secret(board)
    assert secret not in r.stdout and secret not in r.stderr
    printed_env = r.stdout.split("---ENV---", 1)[1]
    assert f"RAIDLOCK_AGENT={board.lead}" in printed_env
    assert "RAIDLOCK_TOKEN" not in printed_env
    assert "token_file" in r.stdout and _lead_secret(board) == secret
