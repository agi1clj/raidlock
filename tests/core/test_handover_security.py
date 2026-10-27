import os

import pytest

from raidlock.core import identity, lead, store
from raidlock.core.errors import Denied, IdentityError, StaleLead
from raidlock.core.lead_env import assert_worker_env, scrub_worker_env
from raidlock.core.lead_hook import guard_lead_read, urgent_context


def test_session_and_process_anchor_cannot_be_replayed(world):
    b = world.board
    with pytest.raises(lead.ForeignLead):
        b.caller(lead_secret=world.lead_token, session="other-session")
    lease = b.leadlease().lease()
    lease["pid_start"] = "different-process-start"
    store.write_json(b.leadlease().path, lease)
    with pytest.raises((lead.ForeignLead, StaleLead)):
        b.caller(lead_secret=world.lead_token)
    assert any(p.get("data", {}).get("alert") for r in b.transcript.read() for p in r["parts"])


def test_handover_rotates_secret_fence_and_rejects_cached_caller(world):
    b = world.board
    old = world.lead
    result = b.lead_handover(old, "next-session")
    assert result["fence"] == 2
    with pytest.raises(StaleLead):
        b.add_team(old, "stale", 3)
    with pytest.raises(StaleLead):
        b.caller(lead_secret=world.lead_token)
    current = b.leadlease().current_secret()
    new = b.caller(lead_secret=current, session="next-session")
    b.add_team(new, "fresh", 3)
    assert b.who()[b.lead]["session"] == "next-session"
    assert current not in str(b.transcript.read())
    assert b.leadlease().lease()["pid"]


def test_expiry_requires_explicit_takeover_and_rotates(world):
    b = world.board
    with pytest.raises(Denied):
        b.lead_takeover(lead_secret=world.lead_token, session="replacement")
    lease = b.leadlease().lease()
    lease["expires"] = "2000-01-01T00:00:00Z"
    store.write_json(b.leadlease().path, lease)
    with pytest.raises(StaleLead):
        b.add_team(world.lead, "expired", 3)
    r = b.lead_takeover(lead_secret=world.lead_token, session="replacement")
    assert r["fence"] == 2 and r["session"] == "replacement"
    assert any(p.get("data", {}).get("event") == "lead_takeover" for r in b.transcript.read() for p in r["parts"])
    with pytest.raises(StaleLead):
        b.caller(lead_secret=world.lead_token)


def test_capability_cannot_override_fence_or_escape_card(world):
    b = world.board
    worker = world.agent("worker-one")
    world.card("first")
    world.card("second", locks="app/other")
    grant = world.claim("first", worker)
    cap = b.caller(agent=worker.name, cap=grant["cap"])
    b.note("first", cap, "scoped note")
    with pytest.raises(IdentityError):
        b.caller(agent=worker.name, cap=grant["cap"], fence=grant["fence"] + 1)
    with pytest.raises(Denied):
        b.note("second", cap, "escape")
    with pytest.raises(Denied):
        b.join(cap, agent="new-worker")
    with pytest.raises(IdentityError):
        b.caller(agent="other-worker", cap=grant["cap"])
    b.release("first", worker)
    with pytest.raises(Denied):
        b.send(cap, b.lead, "released capability")


def test_urgent_and_owner_notes_reach_busy_worker_once(world):
    b = world.board
    worker = world.agent("worker-one", session="session-worker")
    world.card("first")
    world.claim("first", worker)
    b.send(world.lead, worker.name, "ordinary queued")
    b.send(world.lead, worker.name, "urgent stop", urgent=True)
    b.note("first", world.lead, "review this requirement")
    context = urgent_context(b, worker)
    assert "urgent stop" in context and "review this requirement" in context
    assert "ordinary queued" not in context
    assert urgent_context(b, worker) == ""
    assert b.inbox(worker)["queued"] == 1
    assert b.agent_for_session("session-worker") == worker.name
    assert b.who()[worker.name]["session"] == "session-worker"
    with pytest.raises(IdentityError):
        b.send(worker, b.lead, "worker urgent", urgent=True)


def test_lead_environment_scrub_and_worker_refusal(world):
    inherited = {
        "PATH": "/bin",
        "RAIDLOCK_LEAD_SECRET": "sensitive",
        "RAIDLOCK_LEAD_SECRET_FILE": "/secret",
        "RAIDLOCK_LEAD_FUTURE": "sensitive",
        "RAIDLOCK_ROLE": "lead",
        "RAIDLOCK_TOKEN": "old",
    }
    assert scrub_worker_env(inherited) == {"PATH": "/bin"}
    with pytest.raises(IdentityError) as exc:
        assert_worker_env(inherited)
    assert "sensitive" not in str(exc.value)
    assert_worker_env({"RAIDLOCK_AGENT_ID": "worker", "RAIDLOCK_CAP": "cap"})
    with pytest.raises(IdentityError):
        world.board.caller(lead_secret=world.lead_token, env={"RAIDLOCK_AGENT_ID": "worker"})


def test_hook_blocks_lead_secret_directory_and_symlink(world, tmp_path):
    b = world.board
    secret = identity.token_path(b.home, b.uid, b.lead)
    alias = tmp_path / "alias"
    alias.symlink_to(secret)
    for path in (secret, os.path.dirname(secret), str(alias)):
        with pytest.raises(IdentityError):
            guard_lead_read(b, [path], subagent=True)
    guard_lead_read(b, [str(tmp_path / "ordinary")], subagent=True)
    guard_lead_read(b, [secret], subagent=False)


def test_keychain_opt_in_and_delete_cleans_both_stores(tmp_path):
    class MemoryKeychain:
        value = None

        def available(self):
            return True

        def get(self):
            return self.value

        def put(self, value):
            self.value = value
            return True

        def delete(self):
            self.value = None

    keychain = MemoryKeychain()
    path = str(tmp_path / "secret")
    secrets = lead.SecretStore(path, "test", "keychain", keychain)
    secrets.file.put("old")
    assert secrets.put("new") == "keychain" and not os.path.exists(path)
    assert secrets.get() == "new"
    secrets.delete()
    assert secrets.get() is None
    keychain.value = "stale-keychain-secret"
    keychain.put = lambda value: False
    assert secrets.put("fallback-new-secret") == "file"
    assert secrets.using == "file" and secrets.get() == "fallback-new-secret"


def test_core_write_decision_requires_auth_fence_and_scope(world):
    b = world.board
    worker = world.agent("worker-one")
    world.card("first")
    grant = world.claim("first", worker)
    target = str(world.root / "app" / "src" / "file.py")
    assert b.check_write(target, worker)["allowed"]
    anonymous = b.caller(agent=worker.name)
    anonymous.fence = worker.fence
    assert not b.check_write(target, anonymous)["allowed"]
    worker.fence = 0
    assert not b.check_write(target, worker)["allowed"]
    worker.fence = grant["fence"]
    cap = b.caller(agent=worker.name, cap=grant["cap"])
    assert b.check_write(target, cap)["allowed"]
    b.release("first", worker)
    assert not b.check_write(target, cap)["allowed"]


def test_stale_worker_fence_is_recorded_for_lore(world):
    b = world.board
    worker = world.agent("worker-one")
    world.card("first")
    world.claim("first", worker)
    stale = worker.fence
    world.claim("first", worker)
    worker.fence = stale
    with pytest.raises(Denied):
        b.note("first", worker, "late update")
    assert any(p.get("data", {}).get("event") == "stale_fence" for r in b.transcript.read() for p in r["parts"])


def test_extension_lead_write_checks_current_lease(world):
    b = world.board
    with b.lead_write(world.lead, "approve extension data"):
        pass
    b.lead_handover(world.lead, "next-session")
    with pytest.raises(StaleLead), b.lead_write(world.lead, "approve extension data"):
        pytest.fail("rotated caller entered an extension mutation")


def test_lead_read_guard_protects_case_aliases_and_hardlinks(world, tmp_path):
    board = world.board
    secret = identity.token_path(board.home, board.uid, board.lead)
    alias = secret.replace(os.sep + "tokens" + os.sep, os.sep + "TOKENS" + os.sep)
    for path in (alias, os.path.dirname(alias)):
        with pytest.raises(IdentityError):
            guard_lead_read(board, [path], subagent=True)
    linked = tmp_path / "linked.txt"
    os.link(secret, linked)
    with pytest.raises(IdentityError):
        guard_lead_read(board, [linked], subagent=True)
