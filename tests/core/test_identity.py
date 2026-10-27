import json
import os

import pytest
from portable import assert_mode

from raidlock.core import Board, identity, models
from raidlock.core.errors import IdentityError, Precondition


def test_lead_token_stored_outside_board_with_0600(world):
    path = identity.token_path(world.board.home, world.board.uid, world.board.lead)
    assert os.path.exists(path) and ".board" not in path
    assert_mode(path, 0o600)
    assert not any("token" in f for f in os.listdir(world.root / ".board"))


def test_agent_named_lead_with_worker_token_is_not_lead(world):
    w = world.agent("w-one")
    fake = world.board.caller(agent=world.board.lead, env={"RAIDLOCK_TOKEN": w.token})
    assert not fake.is_lead
    with pytest.raises(IdentityError):
        world.board.add_team(fake, "evil", 9)


def test_subagent_cannot_self_promote(world):
    w = world.agent("w-sub")
    world.card("c1")
    with pytest.raises(IdentityError):
        world.board.caller(env={"RAIDLOCK_AGENT_ID": "w-sub", "RAIDLOCK_ROLE": "lead", "RAIDLOCK_TOKEN": w.token})
    env = {"RAIDLOCK_AGENT_ID": "w-sub", "RAIDLOCK_TOKEN": w.token}
    sub = world.board.caller(env=env)
    assert sub.name == "w-sub" and not sub.is_lead
    with pytest.raises(IdentityError):
        world.board.move("c1", "done", sub)
    with pytest.raises(IdentityError):
        world.board.join(sub, agent=world.board.lead, role="lead")
    with pytest.raises(IdentityError):
        world.board.join(sub, agent="w-sub", role="lead")
    with pytest.raises(IdentityError):
        world.board.caller(agent="other-name", env=env)


def test_subagent_with_stolen_lead_token_still_pinned(world):
    env = {"RAIDLOCK_AGENT_ID": "w-sub", "RAIDLOCK_TOKEN": world.lead_token}
    assert not world.board.caller(env=env).is_lead


def test_role_env_lead_is_refused_with_or_without_auth(tmp_path):
    for name, auth in (("noauth", False), ("auth", True)):
        b, _ = Board.init(tmp_path / name, auth=auth)
        with pytest.raises(IdentityError):
            b.caller(env={"RAIDLOCK_ROLE": "lead"})
        assert not b.caller(agent=b.lead, env={}).is_lead


def test_require_token_env_cannot_disable_auth(world):
    world.agent("w-one")
    world.card("c1")
    no_token = world.board.caller(agent="w-one", env={"RAIDLOCK_REQUIRE_TOKEN": "0"})
    with pytest.raises(IdentityError):
        world.board.claim("c1", no_token)


def test_msg_from_lead_without_credential(world):
    world.agent("w-one")
    with pytest.raises(IdentityError):
        world.board.send(
            world.board.caller(agent=world.board.lead, env={"RAIDLOCK_AGENT_ID": "w-one"}), "w-one", "fake order"
        )


def test_worker_impersonation_needs_token(world):
    world.agent("w-one")
    world.agent("w-two")
    imposter = world.board.caller(agent="w-two", token="wrong")
    with pytest.raises(IdentityError):
        world.board.send(imposter, "w-one", "hi")
    with pytest.raises(IdentityError):
        world.board.join(world.board.caller(agent="w-two"), agent="w-two", team="core")


def test_rejoin_keeps_token(world):
    w = world.agent("w-one")
    r = world.board.join(w, agent="w-one", team="core", runtime="claude-code", model_id="claude-sonnet-5-5")
    assert r["token"] is None and r["model"] == "claude-sonnet-5-5"
    assert world.board.caller(agent="w-one", token=w.token).authenticated


def test_lead_rotates_token(world):
    w = world.agent("w-one")
    r = world.board.join(world.lead, agent="w-one", team="core")
    assert r["token"] and r["token"] != w.token
    assert not world.board.caller(agent="w-one", token=w.token).authenticated


def test_default_names(world):
    r = world.board.join(world.lead, team="core", runtime="codex-cli", role="reviewer", card="kek")
    assert r["agent"] == "codex-reviewer-kek"
    r = world.board.join(world.lead, team="core", runtime="claude-code")
    assert r["agent"] == "claude-worker-1"
    r = world.board.join(world.lead, team="core", runtime="claude-code")
    assert r["agent"] == "claude-worker-2"
    assert world.board.lead == "human-lead"


@pytest.mark.parametrize("bad", ["ab", "Has-Caps", "x" * 41, "sp ace", "semi;colon"])
def test_agent_name_rules(world, bad):
    with pytest.raises(Precondition):
        world.board.join(world.lead, agent=bad)


def test_model_detection_order(tmp_path, monkeypatch):
    (tmp_path / "codex").mkdir()
    (tmp_path / "codex" / "config.toml").write_text(
        'model = "gpt-6-astra"\nmodel_reasoning_effort = "high"\n[profiles.x]\nmodel = "other"\n', encoding="utf-8"
    )
    env = {"CODEX_HOME": str(tmp_path / "codex")}
    d = models.detect("codex-cli", env=env)
    assert (d["model"], d["effort"], d["model_source"], d["model_version"]) == ("gpt-6-astra", "high", "config", "6")
    assert models.detect("codex-cli", flag="gpt-x", env=env)["model_source"] == "flag"
    assert models.detect("codex-cli", declared="m1", env={**env, "RAIDLOCK_MODEL": "m2"})["model"] == "m1"
    assert models.detect("codex-cli", env={**env, "RAIDLOCK_MODEL": "m2"})["model_source"] == "env"
    (tmp_path / "claude").mkdir()
    (tmp_path / "claude" / "settings.json").write_text(json.dumps({"model": "claude-opus-5-5"}), encoding="utf-8")
    cenv = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")}
    assert models.detect("claude-code", env=cenv)["model"] == "claude-opus-5-5"
    assert models.detect("claude-code", reported="claude-haiku-5-5", env=cenv)["model_source"] == "reported"
    assert (
        models.detect("claude-code", env={**cenv, "ANTHROPIC_MODEL": "claude-sonnet-5-5"})["model"]
        == "claude-sonnet-5-5"
    )
    assert models.detect("shell", env={})["model_source"] == "unknown"


def test_display_and_notes_carry_model(world):
    w = world.agent("w-one", model_id="claude-opus-5-5")
    assert world.board.agents()["w-one"]["display"] == "w-one · Claude Opus 5.5 · claude-code"
    assert models.version("claude-opus-5-5") == "5.5"
    world.card("c1")
    world.claim("c1", w)
    world.board.note("c1", w, "hello")
    assert world.board.card("c1")["notes"][-1]["model"] == "claude-opus-5-5"
    assert world.board.transcript.read(kind="note")[-1]["from"]["model"] == "claude-opus-5-5"
    assert models.display("unknown-x", world.board.home) == "unknown-x"
