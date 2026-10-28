import json
import os
import subprocess
import sys

import pytest
from conftest import LEAD_SESSION, SRC

from raidlock import bridge, hooks
from raidlock.core import clock
from raidlock.core.errors import RaidlockError

TEAMMATE = "7f3c2a10-5b8e-4d21-9a6f-0c1d2e3f4a5b"
CODEX = "019a3c5e-7d10-7b42-8c3e-2f1a0b9c8d7e"


def reason(out):
    return (out or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


def claude_tool(scene, tool, tool_input, session=TEAMMATE, agent_id=None, agent_type=None):
    data = {
        "session_id": session,
        "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
        "transcript_path": str(scene.transcript),
        "cwd": str(scene.ws),
        "scratchpad_dir": str(scene.root / "scratch" / session),
        "permission_mode": "default",
        "effort": {"level": "high"},
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": tool_input,
        "tool_use_id": "toolu_01ABC123",
    }
    if agent_id:
        data["agent_id"] = agent_id
        data["agent_type"] = agent_type or "general-purpose"
    return json.dumps(data)


def codex_tool(scene, tool, command, session=CODEX, agent_id=None):
    data = {
        "session_id": session,
        "transcript_path": None,
        "cwd": str(scene.ws),
        "hook_event_name": "PreToolUse",
        "model": "gpt-5.5-codex",
        "permission_mode": "default",
        "turn_id": "019a3c5e-7d11-7000-8000-000000000001",
        "tool_name": tool,
        "tool_input": {"command": command},
        "tool_use_id": "call_9xK2",
    }
    if agent_id:
        data["agent_id"] = agent_id
        data["agent_type"] = "worker"
    return json.dumps(data)


def session_start(scene, session=TEAMMATE, source="startup"):
    return json.dumps(
        {
            "session_id": session,
            "transcript_path": str(scene.transcript),
            "cwd": str(scene.ws),
            "hook_event_name": "SessionStart",
            "source": source,
            "model": "claude-opus-5-5",
        }
    )


def session_end(scene, session=TEAMMATE):
    return json.dumps(
        {
            "session_id": session,
            "transcript_path": str(scene.transcript),
            "cwd": str(scene.ws),
            "hook_event_name": "SessionEnd",
            "reason": "other",
        }
    )


def write(scene, rel, **kw):
    return claude_tool(scene, "Write", {"file_path": str(scene.ws / rel), "content": "x"}, **kw)


def bash(scene, command, **kw):
    return claude_tool(scene, "Bash", {"command": command, "description": "run"}, **kw)


def read(scene, path, **kw):
    return claude_tool(scene, "Read", {"file_path": str(path)}, **kw)


def lead_env(scene):
    return {**scene.env, **{k: v for k, v in scene.lead_env.items() if k != "RAIDLOCK_SESSION"}}


def secret_path(scene):
    return scene.board.lead_status()["secret_file"]


def test_lead_lease_is_bound_to_the_host_session(scene):
    assert hooks.lead_session(scene.board) == LEAD_SESSION
    assert hooks.lead_live(scene.board)


def test_teammate_session_cannot_write_an_unleased_path(scene):
    text = reason(hooks.run("pre-write", write(scene, "app/README.md"), dict(scene.env)))
    assert "no raidlock identity" in text


def test_teammate_session_cannot_write_another_agents_lease(scene):
    text = reason(hooks.run("pre-write", write(scene, "app/src/main.py"), dict(scene.env)))
    assert "held by w-one on c1" in text


def setting(scene, key, value):
    meta_path = scene.ws / ".board" / "board.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["settings"][key] = value
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


def test_lead_free_path_write_needs_a_card_by_default(scene):
    text = reason(hooks.run("pre-write", write(scene, "app/README.md", session=LEAD_SESSION), dict(scene.env)))
    assert "require_card_for_lead_edits" in text


def test_lead_free_path_write_allowed_when_the_rule_is_off(scene):
    setting(scene, "require_card_for_lead_edits", False)
    assert hooks.run("pre-write", write(scene, "app/README.md", session=LEAD_SESSION), dict(scene.env)) is None


def test_lead_writes_inside_its_own_card(scene):
    scene.board.new_card(scene.lead, "c3", card_id="c3", team="core", locks="app/README.md")
    scene.board.claim("c3", scene.lead)
    assert hooks.run("pre-write", write(scene, "app/README.md", session=LEAD_SESSION), dict(scene.env)) is None


def subagent(scene, prompt):
    payload = claude_tool(scene, "Agent", {"description": "help", "prompt": prompt}, session=LEAD_SESSION)
    return reason(hooks.run("pre-urgent", payload, dict(scene.env)))


def test_subagent_launch_needs_an_open_card_by_default(scene):
    assert "board_first_subagents" in subagent(scene, "refactor the parser")
    assert "board_first_subagents" in subagent(scene, "work on c10 and xc1")
    assert "board_first_subagents" in subagent(scene, "fix c1, for example the parser")
    assert subagent(scene, "work card c1 in app/src") == ""


def test_subagent_launch_free_when_the_rule_is_off(scene):
    setting(scene, "board_first_subagents", False)
    assert subagent(scene, "refactor the parser") == ""


def test_in_process_teammate_is_a_subagent(scene):
    payload = write(scene, "app/README.md", session=LEAD_SESSION, agent_id="a7c1f0e2", agent_type="implementer")
    assert reason(hooks.run("pre-write", payload, dict(scene.env)))


def test_cooperative_board_lets_unbound_sessions_write_free_paths_only(scene):
    meta_path = scene.ws / ".board" / "board.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["settings"]["auth"] = "off"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert hooks.run("pre-write", write(scene, "app/README.md"), dict(scene.env)) is None
    assert "held by w-one" in reason(hooks.run("pre-write", write(scene, "app/src/main.py"), dict(scene.env)))


def test_teammate_with_inherited_lead_environment_is_still_unbound(scene):
    env = lead_env(scene)
    assert reason(hooks.run("pre-write", write(scene, "app/README.md"), env))
    for command in (
        "raidlock done c1",
        "raidlock move c1 done",
        "raidlock verify c1",
        "raidlock msg w-two hi --urgent",
        "raidlock claim c2 --for w-one",
    ):
        assert reason(hooks.run("pre-bash", bash(scene, command), env)), command


@pytest.mark.parametrize(
    "command",
    [
        "raidlock --lead-secret {secret} done c1",
        "raid --lead-secret={secret} move c1 done",
        "RAIDLOCK_LEAD_SECRET=abc raidlock note c1 hi --agent w-one",
        "cat {secret}",
        "cp {secret} /tmp/x",
        "raidlock --lead-secret - lead handover --to " + TEAMMATE,
    ],
)
def test_teammate_cannot_present_or_read_the_lead_secret(scene, command):
    command = command.format(secret=secret_path(scene))
    assert reason(hooks.run("pre-bash", bash(scene, command), dict(scene.env))), command


def test_lead_session_may_present_its_secret(scene):
    command = f"raidlock --lead-secret {secret_path(scene)} done c1"
    assert hooks.run("pre-bash", bash(scene, command, session=LEAD_SESSION), dict(scene.env)) is None


def test_lead_secret_flag_no_longer_hides_the_command_from_subagents(scene):
    command = "RAIDLOCK_AGENT=w-one raidlock --lead-secret - done c1"
    payload = bash(scene, command, session=LEAD_SESSION, agent_id="a-one", agent_type="raidlock:raid-worker")
    assert "only the lead" in reason(hooks.run("pre-bash", payload, dict(scene.env)))


def test_teammate_must_name_itself_and_not_the_lead(scene):
    env = lead_env(scene)
    assert "name yourself" in reason(hooks.run("pre-bash", bash(scene, "raidlock claim c2"), env))
    named = bash(scene, f"RAIDLOCK_AGENT={scene.board.lead} raidlock claim c2")
    assert "cannot act as the lead" in reason(hooks.run("pre-bash", named, env))
    assert hooks.run("pre-bash", bash(scene, "raidlock claimable"), env) is None


def test_teammate_cannot_use_an_inherited_token(scene):
    command = "RAIDLOCK_AGENT=w-one RAIDLOCK_TOKEN=$RAIDLOCK_TOKEN raidlock note c1 hi"
    assert "inherited" in reason(hooks.run("pre-bash", bash(scene, command), dict(scene.env)))


@pytest.mark.parametrize("tool", ["Read", "Grep", "Glob"])
def test_teammate_cannot_read_tokens(scene, tool):
    token_file = os.path.join(os.path.dirname(secret_path(scene)), "w-one.token")
    for target in (secret_path(scene), token_file, os.path.dirname(token_file)):
        if tool == "Read":
            payload = read(scene, target)
        elif tool == "Grep":
            payload = claude_tool(scene, "Grep", {"pattern": ".", "path": target})
        else:
            payload = claude_tool(scene, "Glob", {"pattern": target + "/*"})
        text = reason(hooks.run("pre-read", payload, dict(scene.env)))
        assert "only the lead session" in text, (tool, target)


def test_lead_session_reads_are_not_guarded(scene):
    assert hooks.run("pre-read", read(scene, secret_path(scene), session=LEAD_SESSION), dict(scene.env)) is None


def test_bound_worker_session_cannot_read_other_tokens(scene):
    env = {**scene.env, "RAIDLOCK_AGENT": "w-two", "RAIDLOCK_TOKEN": scene.tokens["w-two"]}
    token_file = os.path.join(os.path.dirname(secret_path(scene)), "w-one.token")
    assert reason(hooks.run("pre-read", read(scene, token_file), env))


def test_teammate_binds_by_claiming_and_then_writes_inside_its_lease(scene):
    scene.board.release("c1", scene.caller("w-one", scene.fence))
    fence = scene.board.claim("c1", scene.caller("w-one"))["fence"]
    post = {
        "session_id": TEAMMATE,
        "transcript_path": str(scene.transcript),
        "cwd": str(scene.ws),
        "permission_mode": "default",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": f"RAIDLOCK_AGENT=w-one RAIDLOCK_TOKEN={scene.tokens['w-one']} raidlock heartbeat c1"},
        "tool_response": {"stdout": f"export RAIDLOCK_FENCE={fence}\n", "stderr": "", "interrupted": False},
        "tool_use_id": "toolu_02",
    }
    hooks.run("post-tool", json.dumps(post), dict(scene.env))
    assert hooks.load_binding(scene.env, TEAMMATE, "main")["name"] == "w-one"
    assert hooks.run("pre-write", write(scene, "app/src/main.py"), dict(scene.env)) is None
    assert "holds no lease" in reason(hooks.run("pre-write", write(scene, "app/README.md"), dict(scene.env)))
    assert "held by w-two" in reason(hooks.run("pre-write", write(scene, "app/docs/a.md"), dict(scene.env)))
    assert "only the lead" in reason(hooks.run("pre-bash", bash(scene, "raidlock done c1"), dict(scene.env)))


def test_expired_lead_lease_allows_recovery_from_a_new_session(scene):
    clock.travel(scene.board.ttl_seconds() + 60)
    assert not hooks.lead_live(scene.board)
    command = f"raidlock --lead-secret {secret_path(scene)} lead takeover"
    assert hooks.run("pre-bash", bash(scene, command), dict(scene.env)) is None
    assert reason(hooks.run("pre-bash", bash(scene, f"raidlock --lead-secret {secret_path(scene)} done c1"), {}))


def test_teammate_denial_through_the_hook_process(scene):
    env = {**scene.env, "PYTHONPATH": SRC}
    proc = subprocess.run(
        [sys.executable, "-m", "raidlock.hooks", "pre-write"],
        input=write(scene, "app/README.md").encode(),
        capture_output=True,
        env=env,
        cwd=SRC,
        timeout=60,
    )
    assert proc.returncode == 0
    assert "no raidlock identity" in reason(json.loads(proc.stdout))


def patch(*lines):
    return "\n".join(["*** Begin Patch", *lines, "*** End Patch", ""])


def test_patch_paths_cover_every_header():
    text = patch(
        "*** Add File: a/new.py",
        "+x",
        "*** Update File: b/old.py",
        "*** Move to: c/moved.py",
        "@@",
        "-a",
        "+b",
        "*** Delete File: d/gone.py",
    )
    assert bridge.patch_paths(text) == ["a/new.py", "b/old.py", "c/moved.py", "d/gone.py"]


def test_codex_apply_patch_into_another_lease_is_denied(scene):
    text = patch("*** Update File: app/src/main.py", "@@", "-a", "+b")
    out = hooks.run("pre-write", codex_tool(scene, "apply_patch", text), dict(scene.env))
    assert "held by w-one on c1" in reason(out)


def test_codex_apply_patch_move_into_a_lease_is_denied(scene):
    setting(scene, "require_card_for_lead_edits", False)
    text = patch("*** Update File: app/README.md", "*** Move to: app/docs/readme.md", "@@", "-a", "+b")
    out = hooks.run("pre-write", codex_tool(scene, "apply_patch", text, session=LEAD_SESSION), dict(scene.env))
    assert "held by w-two on c2" in reason(out)


def test_codex_apply_patch_from_unbound_session_on_a_free_path_is_denied(scene):
    text = patch("*** Add File: app/notes.md", "+x")
    assert "no raidlock identity" in reason(hooks.run("pre-write", codex_tool(scene, "apply_patch", text), {}))


def test_codex_apply_patch_without_targets_is_denied(scene):
    out = hooks.run("pre-write", codex_tool(scene, "apply_patch", "*** Begin Patch\n*** End Patch\n"), dict(scene.env))
    assert "apply_patch targets" in reason(out)


def test_codex_apply_patch_board_file_is_denied(scene):
    text = patch("*** Update File: .board/cards/c1.json", "@@", "-a", "+b")
    out = hooks.run("pre-write", codex_tool(scene, "apply_patch", text, session=LEAD_SESSION), dict(scene.env))
    assert ".board/" in reason(out)


def test_codex_subagent_apply_patch_inside_its_lease_is_allowed(scene):
    scene.bind_worker()
    text = patch("*** Update File: app/src/main.py", "@@", "-a", "+b")
    payload = codex_tool(scene, "apply_patch", text, session="s1", agent_id="a-one")
    assert hooks.run("pre-write", payload, dict(scene.env)) is None


def test_codex_bash_payload_uses_the_same_rules(scene):
    out = hooks.run("pre-bash", codex_tool(scene, "Bash", "echo x > app/src/main.py"), dict(scene.env))
    assert "held by w-one" in reason(out)
    out = hooks.run("pre-bash", codex_tool(scene, "Bash", "raidlock done c1"), dict(scene.env))
    assert "only the lead" in reason(out)


def join_pool(scene, *names):
    for name in names:
        scene.tokens[name] = scene.board.join(scene.lead, agent=name, team="core", runtime="claude-code")["token"]
    return bridge.set_pool(scene.board, scene.lead, list(names))


def start(scene, tmp_path, session=TEAMMATE, extra=None):
    env_file = tmp_path / f"env-{session}.sh"
    if env_file.exists():
        env_file.unlink()
    env = {**scene.env, "CLAUDE_ENV_FILE": str(env_file), **(extra or {})}
    out = hooks.run("session-start", session_start(scene, session), env)
    for name in bridge.load(scene.board)["pool"]:
        scene.tokens[name] = hooks.agent_token(scene.board, name)
    return out, env_file.read_text(encoding="utf-8") if env_file.exists() else ""


def test_pool_is_lead_only_and_validated(scene):
    with pytest.raises(RaidlockError):
        bridge.set_pool(scene.board, scene.caller("w-one"), ["w-two"])
    with pytest.raises(RaidlockError):
        bridge.set_pool(scene.board, scene.lead, [scene.board.lead])
    with pytest.raises(RaidlockError):
        bridge.set_pool(scene.board, scene.lead, ["nobody"])
    with pytest.raises(RaidlockError):
        bridge.set_pool(scene.board, scene.lead, ["bad name"])
    assert bridge.set_pool(scene.board, scene.lead, ["w-two", "w-two", ""]) == {"pool": ["w-two"]}


def test_teammate_auto_joins_from_the_pool(scene, tmp_path):
    join_pool(scene, "mate-1", "mate-2")
    out, text = start(scene, tmp_path)
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "as worker mate-1" in context and scene.tokens["mate-1"] not in context
    assert "export RAIDLOCK_AGENT=mate-1" in text
    assert f"export RAIDLOCK_TOKEN={scene.tokens['mate-1']}" in text
    binding = hooks.load_binding(scene.env, TEAMMATE, "main")
    assert binding["name"] == "mate-1" and "proof" in binding and scene.tokens["mate-1"] not in json.dumps(binding)
    assert "not bound to your claim yet" in reason(
        hooks.run("pre-write", write(scene, "app/README.md"), dict(scene.env))
    )
    assert "only the lead" in reason(hooks.run("pre-bash", bash(scene, "raidlock done c1"), dict(scene.env)))


def test_pooled_teammate_claims_and_writes_inside_its_lease(scene, tmp_path):
    join_pool(scene, "mate-1")
    start(scene, tmp_path)
    scene.board.new_card(scene.lead, "c3", card_id="c3", team="core", locks="app/lib")
    claimed = scene.board.claim("c3", scene.board.caller(agent="mate-1", token=scene.tokens["mate-1"], env={}))
    fence = claimed["fence"]
    post = {
        "session_id": TEAMMATE,
        "transcript_path": str(scene.transcript),
        "cwd": str(scene.ws),
        "permission_mode": "default",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "raidlock claim c3"},
        "tool_response": {
            "stdout": f"[ws] Claimed c3: lock app/lib, fence {fence}.\nexport RAIDLOCK_FENCE={fence}",
            "stderr": "",
            "interrupted": False,
        },
        "tool_use_id": "toolu_03",
    }
    hooks.run("post-tool", json.dumps(post), lead_env(scene))
    binding = hooks.load_binding(scene.env, TEAMMATE, "main")
    assert binding["card"] == "c3" and binding["fence"] == claimed["fence"]
    assert hooks.run("pre-write", write(scene, "app/lib/x.py"), dict(scene.env)) is None
    assert "held by w-one" in reason(hooks.run("pre-write", write(scene, "app/src/main.py"), dict(scene.env)))


def test_each_session_gets_its_own_pool_identity_until_exhausted(scene, tmp_path):
    join_pool(scene, "mate-1", "mate-2")
    start(scene, tmp_path, "sess-a")
    start(scene, tmp_path, "sess-b")
    out, text = start(scene, tmp_path, "sess-c")
    names = {hooks.load_binding(scene.env, s, "main").get("name") for s in ("sess-a", "sess-b")}
    assert names == {"mate-1", "mate-2"}
    assert out is None and "RAIDLOCK_TOKEN" not in text
    assert not hooks.load_binding(scene.env, "sess-c", "main")
    _, text = start(scene, tmp_path, "sess-a")
    assert "export RAIDLOCK_AGENT=mate-" in text
    hooks.run("session-end", session_end(scene, "sess-a"), dict(scene.env))
    out, _ = start(scene, tmp_path, "sess-c")
    assert hooks.load_binding(scene.env, "sess-c", "main")["name"] in names


def test_removed_pool_member_is_not_reused(scene, tmp_path):
    join_pool(scene, "mate-1", "mate-2")
    start(scene, tmp_path)
    bridge.set_pool(scene.board, scene.lead, ["mate-2"])
    out, text = start(scene, tmp_path, "sess-new")
    assert "as worker mate-2" in out["hookSpecificOutput"]["additionalContext"]
    out, text = start(scene, tmp_path, TEAMMATE)
    assert out is None and "RAIDLOCK_TOKEN" not in text
    assert not hooks.load_binding(scene.env, TEAMMATE, "main")


def test_resumed_pooled_session_gets_its_identity_again(scene, tmp_path):
    join_pool(scene, "mate-1")
    start(scene, tmp_path)
    env_file = tmp_path / "resume.sh"
    env = {**scene.env, "CLAUDE_ENV_FILE": str(env_file)}
    hooks.run("session-start", session_start(scene, source="resume"), env)
    assert f"export RAIDLOCK_TOKEN={scene.tokens['mate-1']}" in env_file.read_text(encoding="utf-8")


def test_lead_session_never_takes_a_pool_identity(scene, tmp_path):
    join_pool(scene, "mate-1")
    out, text = start(scene, tmp_path, LEAD_SESSION)
    assert out is None and text == f"export RAIDLOCK_SESSION={LEAD_SESSION}\n"
    assert not hooks.load_binding(scene.env, LEAD_SESSION, "main").get("pooled")


def test_no_env_file_means_no_auto_join(scene):
    join_pool(scene, "mate-1")
    assert hooks.run("session-start", session_start(scene), dict(scene.env)) is None
    assert not hooks.load_binding(scene.env, TEAMMATE, "main")


def test_non_lead_session_start_scrubs_inherited_lead_material(scene, tmp_path):
    out, text = start(scene, tmp_path, extra={k: v for k, v in scene.lead_env.items() if k != "RAIDLOCK_SESSION"})
    assert out is None
    assert "unset" in text and "RAIDLOCK_LEAD_SECRET" in text and scene.lead_secret not in text


def test_stale_allocation_is_reclaimed(scene, tmp_path):
    join_pool(scene, "mate-1")
    start(scene, tmp_path, "sess-a")
    state = hooks.state_dir(scene.env)
    assert bridge.allocate(scene.board, state, "sess-b") == ""
    import time

    later = time.time() + bridge.STALE_SECONDS + 10
    assert bridge.allocate(scene.board, state, "sess-b", now=later) == "mate-1"


def test_bridge_cli_shows_and_sets_the_pool(scene):
    scene.board.join(scene.lead, agent="mate-1", team="core", runtime="claude-code")
    env = {
        **os.environ,
        "PYTHONPATH": SRC,
        "RAIDLOCK_SESSION": LEAD_SESSION,
        "RAIDLOCK_LEAD_PID": scene.lead_env["RAIDLOCK_LEAD_PID"],
    }
    run = [sys.executable, "-m", "raidlock", "--board", str(scene.ws)]
    proc = subprocess.run(run + ["bridge", "show"], capture_output=True, env=env, timeout=60, text=True)
    assert proc.returncode == 0 and "(empty)" in proc.stdout
    proc = subprocess.run(run + ["bridge", "pool", "mate-1"], capture_output=True, env=env, timeout=60, text=True)
    assert proc.returncode != 0
    assert bridge.load(scene.fresh()) == {"pool": []}
    lead = run + ["--lead-secret", secret_path(scene), "bridge", "pool", "mate-1"]
    proc = subprocess.run(lead, capture_output=True, env=env, timeout=60, text=True)
    assert proc.returncode == 0, proc.stderr
    assert bridge.load(scene.fresh()) == {"pool": ["mate-1"]}


def test_codex_hooks_snippet_maps_to_real_handlers():
    import re
    from pathlib import Path

    doc = (Path(SRC).parent / "docs" / "guides" / "native.md").read_text(encoding="utf-8")
    block = re.search(r"```json\n(.*?)```", doc, re.S).group(1)
    config = json.loads(block)["hooks"]
    commands = [h["command"] for groups in config.values() for g in groups for h in g["hooks"]]
    assert commands and all(c.startswith("raidlock hook ") for c in commands)
    assert {c.rsplit(" ", 1)[-1] for c in commands} <= set(hooks.HANDLERS)
    assert {"SessionStart", "PreToolUse", "PostToolUse", "SessionEnd"} <= set(config)


@pytest.mark.parametrize("command", ["cd ~ && pwd", "cd {home} && pwd"])
def test_main_session_shell_may_name_ancestors_of_the_secret(scene, command):
    command = command.format(home=scene.env["RAIDLOCK_HOME"])
    env = {**scene.env, "RAIDLOCK_AGENT": "w-one", "RAIDLOCK_TOKEN": scene.tokens["w-one"]}
    assert hooks.run("pre-bash", bash(scene, command), env) is None
    assert hooks.run("pre-bash", bash(scene, command), dict(scene.env)) is None


def test_lead_lease_bound_elsewhere_leaves_the_claude_session_unbound(scene):
    lease_path = scene.ws / ".board" / "lead.json"
    lease = json.loads(lease_path.read_text(encoding="utf-8"))
    lease["session"] = "term-1"
    lease_path.write_text(json.dumps(lease), encoding="utf-8")
    command = f"raidlock --lead-secret {secret_path(scene)} lead heartbeat"
    text = reason(hooks.run("pre-bash", bash(scene, command, session=LEAD_SESSION), dict(scene.env)))
    assert "raidlock lead status" in text


@pytest.mark.parametrize("binding_kind", ["name-only", "handover"])
def test_lead_binding_requires_the_lease_session(scene, binding_kind):
    session = LEAD_SESSION if binding_kind != "name-only" else TEAMMATE
    binding = {"name": scene.board.lead, "board": scene.board.dir}
    if binding_kind != "name-only":
        binding.update(lead_session=session, lead_fence=scene.board.lead_status()["fence"])
    hooks.save_binding(scene.env, session, "main", binding)
    if binding_kind == "handover":
        scene.board.lead_handover(scene.lead, CODEX)
    assert hooks.session_role({"session_id": session}, scene.env, scene.board, binding) == "unbound"
    assert reason(hooks.run("pre-read", read(scene, secret_path(scene), session=session), dict(scene.env)))
    assert reason(hooks.run("pre-bash", bash(scene, "raidlock done c1", session=session), dict(scene.env)))


@pytest.mark.parametrize(
    "command",
    [
        "grep -R . {home}",
        "find {home} -type f",
        "tar -cf /tmp/leak.tar {home}",
        "rsync -a {home}/ /tmp/leak/",
        "cp -r {home} /tmp/leak",
        "cat {home}/to*/*/*",
        "cd {home}; cat to*/*/*",
        "pushd {home} && cat to*/*/*",
        "(cd {home} && grep -R . .)",
        "cd {home} && tar cf /tmp/leak.tar .",
        "cd {home} && find",
        "cd {home} && rg secret",
        "ls {home}",
    ],
)
def test_recursive_and_relative_reads_cannot_reach_credentials(scene, command):
    assert reason(hooks.run("pre-bash", bash(scene, command.format(home=scene.board.home)), dict(scene.env)))


def test_ended_pool_session_cannot_export_reassigned_identity(scene, tmp_path):
    join_pool(scene, "mate-1")
    start(scene, tmp_path, "old")
    old_token = hooks.agent_token(scene.board, "mate-1")
    old_binding = hooks.load_binding(scene.env, "old", "main")
    hooks.run("session-end", session_end(scene, "old"), dict(scene.env))
    assert hooks.load_binding(scene.env, "old", "main") == {}
    start(scene, tmp_path, "new")
    new_token = hooks.agent_token(scene.board, "mate-1")
    assert old_token != new_token
    hooks.save_binding(scene.env, "old", "main", old_binding)
    out, text = start(scene, tmp_path, "old")
    assert out is None and new_token not in text
    assert bridge.allocated(hooks.state_dir(scene.env), scene.board.uid, "new") == "mate-1"
    assert not scene.board.caller(agent="mate-1", token=old_token, env={}).authenticated


@pytest.mark.parametrize("lane", ["doing", "review", "blocked"])
def test_pool_never_reassigns_a_live_worker(scene, tmp_path, lane):
    join_pool(scene, "mate-1")
    start(scene, tmp_path, "old")
    token = hooks.agent_token(scene.board, "mate-1")
    scene.board.new_card(scene.lead, "pool work", card_id="pool-work", team="core", locks="app/lib")
    caller = scene.board.caller(agent="mate-1", token=token, env={})
    grant = scene.board.claim("pool-work", caller)
    caller.fence = grant["fence"]
    if lane != "doing":
        scene.board.move("pool-work", lane, caller)
    hooks.run("session-end", session_end(scene, "old"), dict(scene.env))
    assert bridge.allocate(scene.board, hooks.state_dir(scene.env), "new") == ""
    assert hooks.agent_token(scene.board, "mate-1") == token


@pytest.mark.parametrize(
    "command",
    [
        "python3 -c 'open(chr(120), chr(119)).write(chr(120))'",
        "node -e 'process.exit()'",
        "perl -e 'print 1'",
        "ruby -e 'puts 1'",
        "bash -c '$PAYLOAD'",
        "eval '$PAYLOAD'",
        "python3 payload.py",
        "./payload.sh",
        "./payload",
        "/tmp/payload",
        "sh payload",
        "awk 'BEGIN {system(ENVIRON[\"CMD\"])}'",
        "./payload.sh --version",
        "/tmp/python3 --version",
        "python3 --version payload.py",
    ],
)
def test_unbound_interpreters_are_denied(scene, command):
    assert reason(hooks.run("pre-bash", bash(scene, command), dict(scene.env)))


@pytest.mark.parametrize("command", ["python3 --version", "node --version", "bash --version"])
def test_unbound_interpreter_version_is_allowed(scene, command):
    assert hooks.run("pre-bash", bash(scene, command), dict(scene.env)) is None


def test_worker_interpreter_write_is_caught_at_commit(scene, monkeypatch):
    from raidlock import guard

    repo = scene.ws / "app"
    env = {
        **os.environ,
        **scene.env,
        "PYTHONPATH": SRC,
        "RAIDLOCK_AGENT": "w-one",
        "RAIDLOCK_TOKEN": scene.tokens["w-one"],
        "RAIDLOCK_FENCE": str(scene.fence),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    for args in (("init", "-q"), ("config", "user.name", "Test"), ("config", "user.email", "test@example.test")):
        assert subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True).returncode == 0
    monkeypatch.chdir(repo)
    guard.install()
    command = 'python3 -c \'from pathlib import Path; Path("app/docs/stolen.md").write_text("owned")\''
    hooks.run("session-start", session_start(scene), env)
    assert hooks.run("pre-bash", bash(scene, command), env) is None
    subprocess.run(
        [sys.executable, "-c", "from pathlib import Path; Path('docs/stolen.md').write_text('owned')"],
        cwd=repo,
        env=env,
        check=True,
    )
    subprocess.run(["git", "add", "docs/stolen.md"], cwd=repo, env=env, check=True)
    result = subprocess.run(
        ["git", "commit", "-qm", "interpreter write"], cwd=repo, env=env, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "w-two" in result.stderr


def test_worker_started_with_only_its_cap_writes_inside_its_lease(scene):
    claimed = scene.board.claim("c1", scene.caller("w-one"))
    env = {
        **scene.env,
        "RAIDLOCK_AGENT": "w-one",
        "RAIDLOCK_CAP": claimed["cap"],
        "RAIDLOCK_FENCE": str(claimed["fence"]),
    }
    hooks.run("session-start", session_start(scene), env)
    assert hooks.run("pre-write", write(scene, "app/src/main.py"), env) is None
    denied = hooks.run("pre-write", write(scene, "app/docs/x.md"), env)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "authentication proof" not in denied["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("same_session", [False, True])
def test_pool_concurrent_stale_reclamation_is_atomic(scene, same_session, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from raidlock.core import Board

    join_pool(scene, "mate-1", "mate-2")
    state = hooks.state_dir(scene.env)
    bridge.allocate(scene.board, state, "stale-a", now=0)
    bridge.allocate(scene.board, state, "stale-b", now=0)
    folder = bridge._pool_dir(state, scene.board.uid)
    for name in ("mate-1", "mate-2"):
        os.utime(os.path.join(folder, name + ".json"), (0, 0))
    import time

    stale = bridge._stale

    def slow_stale(*args):
        result = stale(*args)
        if result:
            time.sleep(0.02)
        return result

    monkeypatch.setattr(bridge, "_stale", slow_stale)
    barrier = Barrier(8)

    def reserve(i):
        board = Board.open(scene.ws)
        barrier.wait(timeout=10)
        session = "same" if same_session else f"racer-{i}"
        return session, bridge.allocate(board, state, session)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(reserve, range(8)))
    winners = [(session, name) for session, name in results if name]
    if same_session:
        assert len(winners) == 8 and len({name for _, name in winners}) == 1
        assert (
            sum(
                bridge._read(os.path.join(folder, name + ".json")).get("session") == "same"
                for name in ("mate-1", "mate-2")
            )
            == 1
        )
    else:
        assert len(winners) == 2 and len({name for _, name in winners}) == 2
    for session, name in winners:
        assert bridge.allocated(state, scene.board.uid, session) == name
    bridge.free(state, scene.board.uid, "stale-a")
    for session, name in winners:
        assert bridge.allocated(state, scene.board.uid, session) == name


@pytest.mark.parametrize(
    "tool,tool_input,event",
    [
        ("Bash", {"command": "rm app/src/main.py"}, "pre-bash"),
        ("exec_command", {"cmd": "rm app/src/main.py"}, "pre-bash"),
        ("exec_command", {"input": "rm app/src/main.py"}, "pre-bash"),
        ("exec_command", {"cmd": "rm main.py", "workdir": "app/src"}, "pre-bash"),
        ("apply_patch", {"input": patch("*** Delete File: app/src/main.py")}, "pre-write"),
        ("apply_patch", patch("*** Delete File: app/src/main.py"), "pre-write"),
        ("apply_patch", {"command": patch("*** Delete File: app/src/main.py")}, "pre-write"),
    ],
)
def test_host_command_schemas_enforce_targets(scene, tool, tool_input, event):
    assert "held by w-one" in reason(hooks.run(event, claude_tool(scene, tool, tool_input), dict(scene.env)))


@pytest.mark.parametrize(
    "tool,event",
    [("exec_command", "pre-bash"), ("Bash", "pre-bash"), ("apply_patch", "pre-write"), ("Write", "pre-write")],
)
@pytest.mark.parametrize("tool_input", [None, {}, {"command": 42}, {"cmd": []}, {"input": ""}])
def test_malformed_mutating_tools_fail_closed(scene, tool, event, tool_input):
    assert reason(hooks.run(event, claude_tool(scene, tool, tool_input), dict(scene.env)))


def test_hooks_json_routes_native_tools():
    import re
    from pathlib import Path

    config = json.loads((Path(SRC).parent / "hooks/hooks.json").read_text())["hooks"]["PreToolUse"]
    for tool, handler in (("exec_command", "pre-bash"), ("apply_patch", "pre-write")):
        assert any(
            re.fullmatch(group["matcher"], tool) and any(hook["command"].endswith(handler) for hook in group["hooks"])
            for group in config
            if group["matcher"] != "*"
        )


@pytest.mark.parametrize("command", ["grep -R . app/links", "cat app/links/*/*/*", "find -L app/links"])
def test_recursive_reads_follow_credential_symlinks(scene, command):
    folder = scene.ws / "app/links"
    folder.mkdir()
    (folder / "credentials").symlink_to(scene.board.home, target_is_directory=True)
    assert reason(hooks.run("pre-bash", bash(scene, command), dict(scene.env)))


def test_absolute_root_glob_cannot_hide_credential_ancestors(scene):
    assert reason(hooks.run("pre-bash", bash(scene, "cat /*/*/*/*.token"), dict(scene.env)))


def test_worker_name_without_credential_cannot_unlock_interpreters(scene):
    env = {**scene.env, "RAIDLOCK_AGENT": "w-one"}
    assert reason(hooks.run("pre-bash", bash(scene, "python3 -c 'print(1)'"), env))


def test_exec_workdir_outside_workspace_keeps_read_guard(scene):
    payload = claude_tool(scene, "exec_command", {"cmd": "cat to*/*/*", "workdir": scene.board.home})
    assert "only the lead session" in reason(hooks.run("pre-bash", payload, dict(scene.env)))
