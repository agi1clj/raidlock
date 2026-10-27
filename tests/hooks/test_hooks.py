import json
import os
import shlex
import subprocess
import sys

from conftest import SRC

from raidlock import hooks
from raidlock.core import clock


def reason(out):
    assert out is not None
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    return spec["permissionDecisionReason"]


def test_allow_inside_own_lease_prints_nothing(scene):
    scene.bind_worker()
    proc = scene.run_process("pre-write", "pre_write_allow.json")
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""


def test_deny_path_held_by_other_fence(scene):
    scene.bind_worker()
    text = reason(scene.run("pre-write", "pre_write_other_fence.json"))
    assert "held by w-two on c2" in text
    assert len(text) < 200


def test_deny_subagent_without_task(scene):
    text = reason(scene.run("pre-write", "pre_write_no_task.json"))
    assert "holds no lease" in text or "held by w-one" in text or "no agent identity" in text


def test_deny_unbound_subagent_on_free_path(scene):
    text = reason(scene.run("pre-write", "pre_write_notebook.json"))
    assert "holds no lease" in text or "no agent identity" in text


def test_deny_stale_fence(scene):
    scene.bind_worker()
    scene.board.release("c1", scene.caller("w-one", scene.fence))
    scene.board.claim("c1", scene.caller("w-one"))
    text = reason(scene.run("pre-write", "pre_write_allow.json"))
    assert "stale fence" in text


def test_main_session_free_path_needs_a_card(scene):
    assert "require_card_for_lead_edits" in reason(scene.run("pre-write", "pre_write_main_free.json"))


def test_main_session_denied_on_held_path(scene):
    text = reason(scene.run("pre-write", "pre_write_main_held.json"))
    assert "held by w-one on c1" in text


def test_board_file_write_denied(scene):
    assert ".board/" in reason(scene.run("pre-write", "pre_write_board_file.json"))


def test_subagent_done_attempt_denied(scene):
    text = reason(scene.run("pre-bash", "pre_bash_subagent_done.json"))
    assert "only the lead" in text


def test_subagent_done_variants_denied(scene):
    base = json.loads(scene.fixture("pre_bash_subagent_done.json"))
    for command in (
        "raidlock done c1",
        "cd app && raidlock reopen c1",
        "python3 -m raidlock verify c1",
        "raidlock --board . move c1 done",
        "raidlock msg --to lead --urgent hi",
        "raidlock claim c1 --for w-two",
    ):
        base["tool_input"]["command"] = command
        assert hooks.run("pre-bash", json.dumps(base), dict(scene.env)) is not None, command


def test_main_session_done_not_blocked(scene):
    base = json.loads(scene.fixture("pre_bash_subagent_done.json"))
    del base["agent_id"]
    del base["agent_type"]
    base["tool_input"]["command"] = f"RAIDLOCK_AGENT={scene.board.lead} raidlock move c1 done"
    assert hooks.run("pre-bash", json.dumps(base), dict(scene.env)) is None


def test_bash_board_write_denied(scene):
    assert ".board/" in reason(scene.run("pre-bash", "pre_bash_board_write.json"))
    base = json.loads(scene.fixture("pre_bash_board_write.json"))
    for command in (
        "rm -rf .board/cards",
        "sed -i '' s/a/b/ .board/cards/c1.json",
        "cp x.json ws/.board/cards/",
        "cat a | tee -a .board/LOG.md",
        "mv .board/leases.json /tmp/x",
    ):
        base["tool_input"]["command"] = command
        assert hooks.run("pre-bash", json.dumps(base), dict(scene.env)) is not None, command


def test_bash_reads_and_plain_commands_allowed(scene):
    proc = scene.run_process("pre-bash", "pre_bash_plain.json")
    assert proc.returncode == 0 and proc.stdout == b""
    base = json.loads(scene.fixture("pre_bash_plain.json"))
    for command in (
        "raidlock show --board .board > /tmp/out.txt",
        "cp .board/board.json /tmp/b.json",
        "grep -r fence .board/cards",
        'raidlock note c1 "moved > 3 files"',
    ):
        base["tool_input"]["command"] = command
        assert hooks.run("pre-bash", json.dumps(base), dict(scene.env)) is None, command


def test_bash_redirect_into_held_path_denied(scene):
    base = json.loads(scene.fixture("pre_bash_plain.json"))
    base["tool_input"]["command"] = f"echo x > {shlex.quote(str(scene.ws / 'app' / 'src' / 'main.py'))}"
    assert "held by w-one" in reason(hooks.run("pre-bash", json.dumps(base), dict(scene.env)))


def test_subagent_token_read_denied(scene):
    base = json.loads(scene.fixture("pre_bash_subagent_done.json"))
    base["tool_input"]["command"] = "cat ~/.raidlock/tokens/*/lead.token"
    assert "tokens" in reason(hooks.run("pre-bash", json.dumps(base), dict(scene.env)))


def test_worker_board_and_git_allows_are_silent(scene):
    scene.bind_worker()
    base = json.loads(scene.fixture("pre_bash_subagent_done.json"))
    base["tool_input"]["command"] = 'cd app && git status && RAIDLOCK_AGENT=w-one raidlock note c1 "x"'
    out = hooks.run("pre-bash", json.dumps(base), dict(scene.env))
    assert out is None
    base["tool_input"]["command"] = "git -C app status 2>&1; cat app/src/main.py | head -5"
    assert hooks.run("pre-bash", json.dumps(base), dict(scene.env)) is None
    for command in ("rm -rf build", "cat a > /tmp/b", "find . -delete"):
        base["tool_input"]["command"] = command
        assert hooks.run("pre-bash", json.dumps(base), dict(scene.env)) is None, command
    base["agent_type"] = "general-purpose"
    base["tool_input"]["command"] = "git status"
    assert hooks.run("pre-bash", json.dumps(base), dict(scene.env)) is None


def test_no_board_is_silent(tmp_path, scene):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    data = {
        "session_id": "s9",
        "cwd": str(outside),
        "agent_id": "a-x",
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(outside / "f.txt"), "content": "x"},
    }
    assert hooks.run("pre-write", json.dumps(data), dict(scene.env)) is None


def test_garbage_input_is_silent(scene):
    assert hooks.run("pre-write", "not json", dict(scene.env)) is None
    assert hooks.run("pre-bash", "[]", dict(scene.env)) is None


def test_claim_output_binds_agent_card_and_fence(scene):
    scene.bind_worker()
    binding = hooks.load_binding(scene.env, "s1", "a-one")
    assert binding["name"] == "w-one"
    assert binding["card"] == "c1"
    assert binding["fence"] == scene.fence
    assert binding["board"] == str(scene.ws / ".board")
    assert "token" not in json.dumps(binding).lower()


def test_failed_claim_does_not_bind(scene):
    data = json.loads(scene.fixture("post_claim.json"))
    data["tool_response"]["stdout"] = ""
    data["tool_input"]["command"] = "raidlock show"
    hooks.run("post-tool", json.dumps(data), dict(scene.env))
    assert hooks.load_binding(scene.env, "s1", "a-one") == {}


def test_heartbeat_renews_lease_and_is_throttled(scene):
    before = scene.fresh().leases()["c1"]["renewed"]
    clock.travel(600)
    scene.bind_worker()
    first = scene.fresh().leases()["c1"]["renewed"]
    assert first != before
    clock.travel(120)
    scene.bind_worker()
    assert scene.fresh().leases()["c1"]["renewed"] == first


def test_move_to_review_clears_bound_card(scene):
    scene.bind_worker()
    data = json.loads(scene.fixture("post_claim.json"))
    data["tool_input"]["command"] = "raidlock move c1 review --note done"
    data["tool_response"]["stdout"] = "[ws] Moved c1 to review. Next: the lead runs raidlock done c1"
    hooks.run("post-tool", json.dumps(data), dict(scene.env))
    binding = hooks.load_binding(scene.env, "s1", "a-one")
    assert binding["card"] == "" and binding["last_card"] == "c1"
    scene.run("subagent-stop", "subagent_stop.json")
    assert scene.fresh().transcript.read(kind="handback")[0]["taskId"] == "c1"


def test_model_detected_from_subagent_transcript(scene):
    scene.bind_worker()
    agent = scene.fresh().agents()["w-one"]
    assert agent["model"] == "claude-sonnet-5-5"
    assert agent["model_source"] == "reported"
    assert hooks.load_binding(scene.env, "s1", "a-one")["model"] == "claude-sonnet-5-5"


def test_tail_model_reads_last_assistant_entry(tmp_path):
    path = tmp_path / "t.jsonl"
    filler = json.dumps({"type": "user", "message": {"role": "user", "content": "x" * 1000}})
    lines = [json.dumps({"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-5-5"}})]
    lines += [filler] * 100
    lines += [json.dumps({"type": "assistant", "message": {"role": "assistant", "model": "claude-haiku-5-5"}}), filler]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert hooks.tail_model(str(path)) == "claude-haiku-5-5"
    assert hooks.tail_model(str(tmp_path / "missing.jsonl")) == ""


def test_subagent_transcript_path_derived(scene):
    data = {"transcript_path": str(scene.transcript), "agent_id": "a-one"}
    assert hooks.transcript_for(data) == str(scene.agent_transcript)
    assert hooks.transcript_for({"transcript_path": str(scene.transcript)}) == str(scene.transcript)


def test_subagent_stop_writes_handback(scene):
    scene.bind_worker()
    proc = scene.run_process("subagent-stop", "subagent_stop.json")
    assert proc.returncode == 0 and proc.stdout == b""
    records = scene.fresh().transcript.read(kind="handback")
    assert len(records) == 1
    rec = records[0]
    assert rec["taskId"] == "c1"
    assert rec["from"]["agent"] == "w-one"
    assert rec["from"]["model"] == "claude-sonnet-5-5"
    assert "12 passed" in json.dumps(rec["parts"])


def test_subagent_stop_unbound_is_silent(scene):
    assert scene.run("subagent-stop", "subagent_stop.json") is None
    assert scene.fresh().transcript.read(kind="handback") == []


def test_session_start_registers_silently(scene):
    proc = scene.run_process("session-start", "session_start.json", env=scene.lead_env)
    assert proc.returncode == 0 and proc.stdout == b""
    info = json.loads((scene.data / "sessions" / "s1.json").read_text(encoding="utf-8"))
    assert info["board"] == str(scene.ws / ".board")
    assert hooks.load_binding(scene.env, "s1", "main")["name"] == scene.board.lead


def test_hooks_json_wires_every_event():
    root = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(root, "hooks", "hooks.json"), encoding="utf-8") as f:
        config = json.load(f)["hooks"]
    commands = {h["command"].rsplit(" ", 1)[-1]: h for groups in config.values() for g in groups for h in g["hooks"]}
    assert set(commands) == set(hooks.HANDLERS)
    for event, hook in commands.items():
        assert "${CLAUDE_PLUGIN_ROOT}" in hook["command"]
        assert hook["timeout"] == 5
        assert hook.get("async", False) == (event in ("post-tool", "subagent-stop"))
    pre = {g["matcher"] for g in config["PreToolUse"]}
    assert pre == {
        "Edit|Write|NotebookEdit|apply_patch",
        "Bash|exec_command|shell|shell_command",
        "Read|Glob|Grep",
        "*",
    }


def test_subagent_must_name_itself_on_board_commands(scene):
    base = json.loads(scene.fixture("pre_bash_subagent_done.json"))
    for command in ("raidlock claim c1", "cd app && raidlock note c1 x", "raidlock inbox"):
        base["tool_input"]["command"] = command
        assert "name yourself" in reason(hooks.run("pre-bash", json.dumps(base), dict(scene.env))), command
    for command in ("raidlock who", "raidlock show c1", "raidlock claimable", "raidlock claim --help"):
        base["tool_input"]["command"] = command
        out = hooks.run("pre-bash", json.dumps(base), dict(scene.env))
        assert out is None or out["hookSpecificOutput"]["permissionDecision"] == "allow", command


def test_subagent_cannot_use_lead_name_or_inherited_token(scene):
    base = json.loads(scene.fixture("pre_bash_subagent_done.json"))
    for command in (
        f"RAIDLOCK_AGENT={scene.board.lead} raidlock note c1 x",
        f"raidlock claim c1 --agent {scene.board.lead}",
    ):
        base["tool_input"]["command"] = command
        assert "lead" in reason(hooks.run("pre-bash", json.dumps(base), dict(scene.env))), command
    base["tool_input"]["command"] = "RAIDLOCK_AGENT=w-one RAIDLOCK_TOKEN=$RAIDLOCK_TOKEN raidlock note c1 x"
    assert "token" in reason(hooks.run("pre-bash", json.dumps(base), dict(scene.env)))


def test_exported_identity_carries_to_later_segments(scene):
    base = json.loads(scene.fixture("pre_bash_subagent_done.json"))
    base["tool_input"]["command"] = (
        "export RAIDLOCK_AGENT=w-one RAIDLOCK_TOKEN=abc RAIDLOCK_FENCE=1 && raidlock note c1 x"
    )
    out = hooks.run("pre-bash", json.dumps(base), dict(scene.env))
    assert out is None


def test_binding_from_real_cli_output(scene):
    scene.board.release("c1", scene.caller("w-one", scene.fence))
    proc = subprocess.run(
        [sys.executable, "-m", "raidlock", "claim", "c1", "--board", str(scene.ws)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(scene.ws),
        env={**scene.env, "PYTHONPATH": SRC, "RAIDLOCK_AGENT": "w-one", "RAIDLOCK_TOKEN": scene.tokens["w-one"]},
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(scene.fixture("post_claim.json"))
    data["tool_input"]["command"] = (
        f"cd app && RAIDLOCK_AGENT=w-one RAIDLOCK_TOKEN={scene.tokens['w-one']} raidlock claim c1"
    )
    data["tool_response"]["stdout"] = proc.stdout
    hooks.run("post-tool", json.dumps(data), dict(scene.env))
    binding = hooks.load_binding(scene.env, "s1", "a-one")
    assert binding["card"] == "c1"
    assert binding["fence"] == scene.fresh().leases()["c1"]["fence"]
    assert binding["board"] == str(scene.ws / ".board")
    assert hooks.run("pre-write", scene.fixture("pre_write_allow.json"), dict(scene.env)) is None


def test_skill_flag_form_binds_and_passes_the_guards(scene):
    scene.board.release("c1", scene.caller("w-one", scene.fence))
    token = scene.tokens["w-one"]
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "raidlock",
            "claim",
            "c1",
            "--board",
            str(scene.ws),
            "--agent",
            "w-one",
            "--token",
            token,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(scene.ws),
        env={**scene.env, "PYTHONPATH": SRC},
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(scene.fixture("post_claim.json"))
    data["tool_input"]["command"] = f"raidlock claim c1 --agent w-one --token {token}"
    data["tool_response"]["stdout"] = proc.stdout
    hooks.run("post-tool", json.dumps(data), dict(scene.env))
    binding = hooks.load_binding(scene.env, "s1", "a-one")
    assert binding["card"] == "c1" and binding["cap"].startswith("c1:")
    assert hooks.run("pre-write", scene.fixture("pre_write_allow.json"), dict(scene.env)) is None
    base = json.loads(scene.fixture("pre_bash_subagent_done.json"))
    base["tool_input"]["command"] = (
        f"raidlock note c1 x --agent w-one --cap {binding['cap']} --fence {binding['fence']}"
    )
    assert hooks.run("pre-bash", json.dumps(base), dict(scene.env)) is None


def test_register_tokens_reports_skill_and_hooks():
    seen = {}
    hooks.register_tokens(lambda name, fn: seen.__setitem__(name, fn(None)))
    assert seen["claude-code hooks"]["tokens"] == 0
    assert 0 < seen["claude-code skill listing"]["tokens"] <= 70
    assert 0 < seen["claude-code agent listing"]["tokens"] <= 70


def test_post_tool_records_main_session_context(scene):
    from raidlock import limits

    usage = {"input_tokens": 2000, "cache_read_input_tokens": 48000, "cache_creation_input_tokens": 0}
    line = {"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-5-5", "usage": usage}}
    scene.transcript.write_text(json.dumps(line) + "\n", encoding="utf-8")
    data = json.loads(scene.fixture("post_claim.json"))
    hooks.run("post-tool", json.dumps(data), dict(scene.env))
    assert limits.claude_limits()["status"] == "not exposed"
    data.pop("agent_id")
    data.pop("agent_type")
    data["tool_name"] = "Read"
    hooks.run("post-tool", json.dumps(data), dict(scene.env))
    ctx = limits.claude_limits()["context"]
    assert ctx["used_tokens"] == 50000 and ctx["source"] == "transcript"
