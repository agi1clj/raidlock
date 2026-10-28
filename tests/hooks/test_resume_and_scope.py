from conftest import LEAD_SESSION, make_repo
from test_native_bridge import (
    TEAMMATE,
    bash,
    claude_tool,
    read,
    reason,
    secret_path,
    session_end,
    session_start,
    write,
)

from raidlock import hooks
from raidlock.core import lead as LD

RESUMED = "9d2e0c4a-1b7f-4e33-a5c8-6f0e2d1c3b4a"


def env_file_exports(path):
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("export "):
            key, _, value = line[len("export ") :].partition("=")
            out[key] = value.strip("'")
    return out


def test_session_start_exports_the_hook_session_id(scene, tmp_path):
    env_file = tmp_path / "env.sh"
    env = {**scene.env, "CLAUDE_ENV_FILE": str(env_file)}
    hooks.run("session-start", session_start(scene, RESUMED, source="resume"), env)
    assert env_file_exports(env_file)["RAIDLOCK_SESSION"] == RESUMED


def test_lead_session_start_also_exports_the_hook_session_id(scene, tmp_path):
    env_file = tmp_path / "env.sh"
    env = {**scene.env, "CLAUDE_ENV_FILE": str(env_file)}
    hooks.run("session-start", session_start(scene, LEAD_SESSION), env)
    assert env_file_exports(env_file)["RAIDLOCK_SESSION"] == LEAD_SESSION


def test_resumed_session_takes_a_handed_over_lease(scene, tmp_path, monkeypatch):
    """After a resume the shell keeps the old CLAUDE_CODE_SESSION_ID while hooks see the new id. The lease handed to
    the new id must accept the new session's first authenticated use."""
    scene.board.lead_handover(scene.lead, RESUMED)
    env_file = tmp_path / "env.sh"
    hooks.run(
        "session-start",
        session_start(scene, RESUMED, source="resume"),
        {**scene.env, "CLAUDE_ENV_FILE": str(env_file)},
    )
    assert reason(hooks.run("pre-read", read(scene, secret_path(scene), session=RESUMED), dict(scene.env))) == ""
    shell = {"CLAUDE_CODE_SESSION_ID": "old-shell-id", **env_file_exports(env_file)}
    secret = scene.board.leadlease().current_secret()
    caller = scene.fresh().caller(agent=scene.board.lead, lead_secret=secret, env=shell)
    assert caller.is_lead
    assert scene.board.leadlease().lease()["session"] == RESUMED


def test_stale_lead_binding_does_not_outrank_the_lease(scene):
    stale = {
        "name": scene.board.lead,
        "board": scene.board.dir,
        "lead_session": LEAD_SESSION,
        "lead_fence": scene.board.lead_status()["fence"] - 1,
    }
    hooks.save_binding(scene.env, LEAD_SESSION, "main", stale)
    assert hooks.session_role({"session_id": LEAD_SESSION}, scene.env, scene.board, stale) == "lead"
    assert reason(hooks.run("pre-read", read(scene, secret_path(scene), session=LEAD_SESSION), dict(scene.env))) == ""


def test_refusal_names_both_sessions_and_the_exact_commands(scene):
    text = reason(hooks.run("pre-read", read(scene, secret_path(scene), session=TEAMMATE), dict(scene.env)))
    assert f"session {LEAD_SESSION}" in text and f"this session is {TEAMMATE}" in text
    assert f"lead handover --to {TEAMMATE}" in text and "lead takeover" in text
    expires = scene.board.lead_status()["expires"]
    assert expires in text
    text = reason(
        hooks.run(
            "pre-bash",
            bash(scene, f"raid --lead-secret {secret_path(scene)} lead heartbeat", session=TEAMMATE),
            dict(scene.env),
        )
    )
    assert f"lead handover --to {TEAMMATE}" in text and text.count("raid ") >= 1 and "raidlock lead" not in text


def test_unbound_session_may_check_lead_status(scene):
    for command in ("raid lead status", "raidlock --board ws lead status", "raid --json lead status"):
        assert hooks.run("pre-bash", bash(scene, command), dict(scene.env)) is None, command


def test_cli_foreign_lead_hint_names_the_commands(scene):
    secret = scene.board.leadlease().current_secret()
    try:
        scene.fresh().caller(agent=scene.board.lead, lead_secret=secret, env={"RAIDLOCK_SESSION": TEAMMATE})
    except LD.ForeignLead as e:
        assert f"lead handover --to {TEAMMATE}" in e.hint and "lead takeover" in e.hint and "<" not in e.hint
    else:
        raise AssertionError("a foreign session must not act as the lead")


def test_unbound_session_writes_outside_the_workspace(scene):
    outside = scene.root / "scratch" / TEAMMATE / "notes.md"
    out = hooks.run(
        "pre-write",
        claude_tool(scene, "Write", {"file_path": str(outside), "content": "x"}),
        dict(scene.env),
    )
    assert out is None
    assert hooks.run("pre-bash", bash(scene, f"echo hi > {outside}"), dict(scene.env)) is None
    assert hooks.run("pre-bash", bash(scene, f"echo hi > /tmp/raidlock-scope-{TEAMMATE}"), dict(scene.env)) is None


def test_unbound_session_still_guarded_inside_the_workspace(scene):
    assert "no raidlock identity" in reason(hooks.run("pre-write", write(scene, "app/README.md"), dict(scene.env)))
    assert "held by" in reason(hooks.run("pre-write", write(scene, "app/src/x.py"), dict(scene.env)))


def test_unbound_session_is_guarded_in_any_clone_of_a_workspace_repo(scene):

    def attempt(path):
        return reason(
            hooks.run(
                "pre-write", claude_tool(scene, "Write", {"file_path": str(path), "content": "x"}), dict(scene.env)
            )
        )

    clone = make_repo(scene.root / "elsewhere" / "app", "git@github.com:acme/app.git")
    (clone / "src").mkdir()
    assert "held by w-one" in attempt(clone / "src" / "x.py")
    assert "no raidlock identity" in attempt(clone / "README.md")
    other = make_repo(scene.root / "elsewhere" / "other", "git@github.com:acme/other.git")
    assert attempt(other / "README.md") == ""


def test_bsd_sed_empty_suffix_is_not_a_write_target(scene):
    outside = scene.root / "scratch" / "f.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("a", encoding="utf-8")
    assert hooks.run("pre-bash", bash(scene, f"sed -i '' 's/a/b/' {outside}"), dict(scene.env)) is None
    assert "no raidlock identity" in reason(
        hooks.run("pre-bash", bash(scene, f"sed -i '' 's/a/b/' {scene.ws / 'app' / 'README.md'}"), dict(scene.env))
    )


def test_hint_rename_covers_messages_and_mid_line_commands(scene, capsys, monkeypatch):
    from raidlock import cli

    assert cli.rename("use raidlock cancel c1 --reason <r>", "raid") == "use raid cancel c1 --reason <r>"
    assert cli.rename("run raidlock --lead-secret f done c1", "raid") == "run raid --lead-secret f done c1"
    assert cli.rename("this session has no raidlock identity", "raid") == "this session has no raidlock identity"
    assert cli.rename("[raidlock] Claimed c1", "raid") == "[raidlock] Claimed c1"
    argv = ["move", "c1", "cancelled", "--board", str(scene.ws), "--agent", "w-one", "--token", scene.tokens["w-one"]]
    assert cli.main(argv, prog="raid") != 0
    err = capsys.readouterr().err
    assert "raid cancel c1" in err and "raidlock cancel" not in err, err


def test_lead_cmd_uses_the_invoked_name(scene, monkeypatch):
    from raidlock.core import hints

    monkeypatch.setattr("sys.argv", ["/usr/local/bin/raid", "show"])
    assert hints.lead_cmd(scene.board, "done c1").startswith("raid --lead-secret ")
    monkeypatch.setattr("sys.argv", ["/usr/local/bin/raidlock", "show"])
    assert hints.lead_cmd(scene.board, "done c1").startswith("raidlock --lead-secret ")
    assert hints.lead_cmd(scene.board, "done c1", prog="raid").startswith("raid --lead-secret ")


def test_lead_session_end_frees_the_lease_for_the_next_session(scene, tmp_path):

    assert hooks.lead_live(scene.board)
    hooks.run("session-end", session_end(scene, TEAMMATE), dict(scene.env))
    assert hooks.lead_live(scene.board), "only the lead's own session releases the lease"
    hooks.run("session-end", session_end(scene, LEAD_SESSION), dict(scene.env))
    assert not hooks.lead_live(scene.board)
    parts = [p for r in scene.fresh().transcript.read() for p in r["parts"]]
    assert any(p.get("data", {}).get("event") == "lead_released" for p in parts)
    out = hooks.run(
        "session-start",
        session_start(scene, RESUMED),
        {**scene.env, "CLAUDE_ENV_FILE": str(tmp_path / "env.sh")},
    )
    text = out["hookSpecificOutput"]["additionalContext"]
    assert "lead lease" in text and "is free" in text and "lead takeover" in text and RESUMED in text


def test_agents_cannot_invoke_hooks_from_a_tool_call(scene):
    for session in (LEAD_SESSION, TEAMMATE):
        for command in (
            "raidlock hook session-end < /dev/null",
            f'RAIDLOCK_AGENT=w-one raid hook session-end <<< \'{{"session_id":"{LEAD_SESSION}"}}\'',
            "python3 -m raidlock hook session-end",
        ):
            text = reason(hooks.run("pre-bash", bash(scene, command, session=session), dict(scene.env)))
            assert "never from a tool call" in text, (session, command, text)


def test_session_end_outside_the_lease_process_tree_keeps_the_lease(scene, monkeypatch):

    lease = scene.board.leadlease().lease()
    monkeypatch.setattr(LD, "ancestors", lambda table, pid=None: [(lease["pid"] + 99999, "x", "other")])
    hooks.run("session-end", session_end(scene, LEAD_SESSION), dict(scene.env))
    assert hooks.lead_live(scene.board)


def test_pending_handover_is_not_released_by_session_end(scene):

    scene.board.lead_handover(scene.lead, RESUMED)
    hooks.run("session-end", session_end(scene, RESUMED), dict(scene.env))
    assert hooks.lead_live(scene.board)


def test_git_init_is_not_an_ambiguous_git_command(scene):
    fresh = scene.root / "scratch" / "new-repo"
    for session in (TEAMMATE, LEAD_SESSION):
        assert hooks.run("pre-bash", bash(scene, f"git init -q {fresh}", session=session), dict(scene.env)) is None


def test_lock_takes_space_separated_paths_and_names_the_fence(scene, capsys):
    from raidlock import cli

    who = ["--board", str(scene.ws), "--agent", "w-one", "--token", scene.tokens["w-one"]]
    assert cli.main(["lock", "c1", "app/lib", "app/bin", *who, "--fence", str(scene.fence)], prog="raid") == 0
    assert {"app/lib", "app/bin"} <= set(scene.fresh().card("c1")["locks"])
    capsys.readouterr()
    assert cli.main(["lock", "c1", "app/etc", *who], prog="raid") != 0
    err = capsys.readouterr().err
    assert f"--fence {scene.fence}" in err and "<fence" not in err


def test_lead_owner_needs_no_fence(scene):
    scene.board.new_card(scene.lead, "c3", card_id="c3", team="core", locks="app/tools")
    scene.board.claim("c3", scene.lead)
    lead = scene.fresh().caller(agent=scene.board.lead, lead_secret=scene.lead_secret, env=scene.lead_env)
    assert "app/more" in scene.fresh().lock_paths("c3", lead, "app/more")["locks"]


def test_the_same_lead_session_coming_back_gets_its_lease_again(scene, tmp_path):

    hooks.run("session-end", session_end(scene, LEAD_SESSION), dict(scene.env))
    assert not hooks.lead_live(scene.board)
    env = {**scene.env, "CLAUDE_ENV_FILE": str(tmp_path / "env.sh")}
    hooks.run("session-start", session_start(scene, TEAMMATE), env)
    assert not hooks.lead_live(scene.board), "another session never re-arms the lead lease"
    hooks.run("session-start", session_start(scene, LEAD_SESSION, source="resume"), env)
    assert hooks.lead_live(scene.board) and scene.board.lead_status()["pending"]
    lead = scene.fresh().caller(agent=scene.board.lead, lead_secret=scene.lead_secret, env=scene.lead_env)
    assert lead.is_lead and scene.board.leadlease().lease()["pid"]


def test_a_takeover_is_never_undone_by_the_old_session(scene, tmp_path):

    hooks.run("session-end", session_end(scene, LEAD_SESSION), dict(scene.env))
    scene.fresh().lead_takeover(lead_secret=scene.lead_secret, session=RESUMED)
    env = {**scene.env, "CLAUDE_ENV_FILE": str(tmp_path / "env.sh")}
    hooks.run("session-start", session_start(scene, LEAD_SESSION, source="resume"), env)
    assert scene.board.lead_status()["session"] == RESUMED


def test_git_config_key_read_is_allowed_and_writes_stay_denied(scene):
    def run(command):
        return reason(hooks.run("pre-bash", bash(scene, command, session=LEAD_SESSION), dict(scene.env)))

    assert run("git -C app config user.email") == ""
    assert "can install commands" in run("git -C app config core.hooksPath /tmp/x")
    assert "can install commands" in run("git -C app config --unset user.email")
