import json
import subprocess
import sys

import pytest
from conftest import SRC

from raidlock import hooks


def main_session(scene, command, session="s-worker"):
    data = json.loads(scene.fixture("pre_bash_subagent_done.json"))
    data.pop("agent_id")
    data.pop("agent_type")
    data["session_id"] = session
    data["tool_input"]["command"] = command
    return json.dumps(data)


def reason(out):
    return (out or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


LEAD_ONLY = (
    "raidlock done c1",
    "raid done c1",
    "raidlock move c1 done",
    "python3 -m raidlock done c1",
    "raidlock reopen c1",
    "raidlock verify c1",
    "raid land c1",
    "cd app && raidlock --board .. done c1",
    "raidlock msg w-two hi --urgent",
)


@pytest.mark.parametrize("command", LEAD_ONLY)
def test_worker_session_from_its_environment_cannot_run_lead_commands(scene, command):
    env = {**scene.env, "RAIDLOCK_AGENT": "w-one", "RAIDLOCK_TOKEN": scene.tokens["w-one"]}
    text = reason(hooks.run("pre-bash", main_session(scene, command), env))
    assert "only the lead" in text or "lead-only" in text


@pytest.mark.parametrize("command", ["RAIDLOCK_AGENT=w-one raidlock done c1", "raidlock done c1 --agent w-one"])
def test_worker_named_on_the_command_cannot_run_done(scene, command):
    assert "only the lead" in reason(hooks.run("pre-bash", main_session(scene, command), dict(scene.env)))


def test_worker_session_bound_by_its_own_claim_cannot_run_done(scene):
    claim = json.loads(scene.fixture("post_claim.json"))
    claim.pop("agent_id")
    claim.pop("agent_type")
    claim["session_id"] = "s-worker"
    hooks.run("post-tool", json.dumps(claim), dict(scene.env))
    assert hooks.load_binding(scene.env, "s-worker", "main")["name"] == "w-one"
    assert "only the lead" in reason(hooks.run("pre-bash", main_session(scene, "raidlock done c1"), dict(scene.env)))


@pytest.mark.parametrize("pinned", ["RAIDLOCK_AGENT_ID", "RAIDLOCK_CAP"])
def test_pinned_or_capability_sessions_cannot_run_done(scene, pinned):
    env = {**scene.env, pinned: "w-one" if pinned == "RAIDLOCK_AGENT_ID" else "c1:1:abc"}
    assert "only the lead" in reason(hooks.run("pre-bash", main_session(scene, "raidlock done c1"), env))


def test_the_lead_session_keeps_done(scene):
    env = {**scene.env, "RAIDLOCK_AGENT": scene.board.lead}
    assert hooks.run("pre-bash", main_session(scene, "raidlock done c1", "s1"), env) is None
    assert hooks.run("pre-bash", main_session(scene, "raidlock done c1", "s1"), dict(scene.env)) is None


def test_other_main_sessions_lose_done_even_when_named_as_lead(scene):
    env = {**scene.env, "RAIDLOCK_AGENT": scene.board.lead}
    assert "only the lead" in reason(hooks.run("pre-bash", main_session(scene, "raidlock done c1", "s-lead"), env))
    anon = hooks.run("pre-bash", main_session(scene, "raidlock done c1", "s-anon"), dict(scene.env))
    assert "only the lead" in reason(anon)


def test_worker_session_keeps_its_own_commands(scene):
    env = {**scene.env, "RAIDLOCK_AGENT": "w-one", "RAIDLOCK_TOKEN": scene.tokens["w-one"]}
    for command in ("raidlock move c1 review", "raidlock note c1 progress", "raidlock show c1"):
        assert hooks.run("pre-bash", main_session(scene, command), env) is None


def test_worker_session_done_is_denied_through_the_process(scene):
    env = {**scene.env, "PYTHONPATH": SRC, "RAIDLOCK_AGENT": "w-one", "RAIDLOCK_TOKEN": scene.tokens["w-one"]}
    proc = subprocess.run(
        [sys.executable, "-m", "raidlock", "hook", "pre-bash"],
        input=main_session(scene, "raidlock done c1").encode(),
        capture_output=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "only the lead" in json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("role", ["worker", "unbound"])
@pytest.mark.parametrize("args", ["", "--rollback --yes", "--yes --allow-auth-downgrade", "--help"])
def test_migrate_all_subcommands_denied_to_nonlead(scene, role, args):
    env = dict(scene.env)
    if role == "worker":
        env.update(RAIDLOCK_AGENT="w-one", RAIDLOCK_TOKEN=scene.tokens["w-one"])
    command = f"raid --agent w-one migrate {args}"
    assert "only the lead" in reason(hooks.run("pre-bash", main_session(scene, command), env))
