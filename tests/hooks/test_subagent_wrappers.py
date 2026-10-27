import json

import pytest

from raidlock import hooks


def event(scene, command):
    return {
        "session_id": "s1",
        "agent_id": "a-one",
        "agent_type": "raidlock:raid-worker",
        "cwd": str(scene.ws),
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


def pre_bash(scene, command):
    return hooks.run("pre-bash", json.dumps(event(scene, command)), dict(scene.env))


def is_denied(result):
    return (result or {}).get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


BYPASSES = (
    "echo c1 | xargs raid --lead-secret F done",
    "echo c1 | xargs raid done",
    "echo done c1 | xargs raid",
    "xargs -n1 raidlock done < ids",
    "uv run raid --lead-secret F done c1",
    "uv run raid done c1",
    "uv run --with raidlock raidlock verify c1",
    "uvx raidlock done c1",
    "uvx --from raidlock raid done c1",
    "pipx run raidlock done c1",
    "npx raidlock done c1",
    r"find . -maxdepth 0 -exec raid --lead-secret F done c1 \;",
    r"find . -maxdepth 0 -execdir raid done c1 \;",
    "env raid --lead-secret F done c1",
    "env -i raid done c1",
    "nice -n 5 raid done c1",
    "timeout 30 raid done c1",
    "command raid --lead-secret F show",
    "nohup raidlock reopen c1",
    "uv run python -m raidlock done c1",
    "raid --lead-secret F show",
    "raid --lead-secret=F show",
    "RAIDLOCK_AGENT=w-one raid move c1 done",
    "bash -c 'raid --lead-secret F done c1'",
    "python3 -c \"from raidlock.cli import main; main(['done', 'c1'])\"",
    'python -c \'import raidlock.cli as c; c.main(["done", "c1"])\'',
    'node -e \'require("child_process").execSync("raidlock done c1")\'',
    "perl -e 'system(\"raid done c1\")'",
    'ruby -e \'system("raid", "done", "c1")\'',
    "echo 'raid done c1' | sh",
    "echo 'raid done c1' | xargs -0 sh -c",
    "python3 <<'EOF'\nfrom raidlock.cli import main\nmain(['done', 'c1'])\nEOF",
    "uv run python -c 'from raidlock.cli import main'",
    r"RAIDLOCK_AGENT=w-one find done -maxdepth 0 -exec raid {} c1 \;",
    "RAIDLOCK_AGENT=w-one fd -x raid show",
    "RAIDLOCK_AGENT=w-one raid $V c1",
    "RAIDLOCK_AGENT=w-one raid $(cat verb) c1",
    "RAIDLOCK_AGENT=w-one raid move c1 $LANE",
    "RAIDLOCK_LEAD_SECRET=x uv run raid show",
    "export RAIDLOCK_LEAD_SECRET=x; raid show",
    "uv run raid note c1 progress",
    "timeout 30 raidlock move c1 review",
)


@pytest.mark.parametrize("command", BYPASSES)
def test_subagent_cannot_reach_lead_commands_behind_wrappers(scene, command):
    scene.bind_worker()
    assert is_denied(pre_bash(scene, command))


ALLOWED = (
    "RAIDLOCK_AGENT=w-one raid note c1 progress",
    "RAIDLOCK_AGENT=w-one raidlock move c1 review",
    "RAIDLOCK_AGENT=w-one raid show c1",
    "RAIDLOCK_AGENT=w-one python -m raidlock note c1 progress",
    "RAIDLOCK_AGENT=w-one uv run raid note c1 progress",
    "RAIDLOCK_AGENT=w-one timeout 30 raid show c1",
    "echo raid done",
    "grep -rn raidlock README.md",
    "python3 -c 'print(1)'",
    "ls app/src",
    "uv run --with raidlock pytest -q",
    'cd app && RAIDLOCK_AGENT=w-one raid note c1 "$(git log -1 --format=%s)"',
)


@pytest.mark.parametrize("command", ALLOWED)
def test_subagent_worker_commands_still_allowed(scene, command):
    scene.bind_worker()
    assert pre_bash(scene, command) is None
