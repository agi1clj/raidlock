import json
import os
import shlex
import sys
from importlib.metadata import EntryPoint, EntryPoints
from pathlib import Path

import pytest

from raidlock.core import Board, Denied, Precondition, receipts
from raidlock.core.transcript import make
from raidlock.reputation import reputation
from raidlock.runners import isolation
from raidlock.runners import workspace as git_workspace


@pytest.fixture
def board(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("RAIDLOCK_") or key in ("BOARD_DIR", "BOARD_PROJECT"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("RAIDLOCK_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    board, secret = Board.init(workspace, lead="lead", env={"RAIDLOCK_HOME": str(tmp_path / "home")})
    lead = board.caller(lead_secret=secret)
    git_workspace.git(str(workspace), "init", "--quiet")
    git_workspace.git(str(workspace), *git_workspace.IDENT, "commit", "--quiet", "--allow-empty", "-m", "fixture")
    run = isolation.run
    monkeypatch.setattr(isolation, "mode", lambda: "test-isolation")

    def isolated_command(*args, **kwargs):
        kwargs["kind"] = None
        return run(*args, **kwargs)

    monkeypatch.setattr(isolation, "run", isolated_command)
    board.test_lead = lead
    board.test_tokens = {}
    for name, model in (("alice", "m1"), ("bob", "m1"), ("reviewer", "m2")):
        joined = board.join(
            lead, agent=name, provider="openai", model_id=model, role="reviewer" if name == "reviewer" else "worker"
        )
        board.test_tokens[name] = joined["token"]
    return board


def row(board, name="alice", by="agent"):
    return next(r for r in reputation(board, by)["rows"] if r["key"] == name)


def claim(board, name="alice", card="task", verify=""):
    lead = board.test_lead
    board.new_card(lead, "Synthetic task", card_id=card, verify_cmd=verify)
    grant = board.claim(card, lead, for_agent=name)
    return board.caller(agent=name, token=board.test_tokens[name], fence=grant["fence"])


def event(board, kind, agent="alice", task="task", data=None, to=""):
    if kind == "verify":
        data = receipts.sign(
            board,
            {
                **data,
                "board": board.uid,
                "card": task,
                "command_hash": receipts.command_hash(board.card(task)["verify_cmd"]),
                "ts": str(len(board.transcript.read())),
            },
        )
    board.transcript.append(
        make(kind, {"agent": agent, "model": "m1", "provider": "openai"}, task=task, data=data, to=to)
    )


def test_no_evidence_and_invalid_group(board):
    result = row(board)
    assert result["score"] == 50
    assert result["inputs"]["verify_pass_rate"] is None
    with pytest.raises(Precondition):
        reputation(board, "team")


def test_real_verify_review_and_rework(board):
    worker = claim(board, verify=f'"{sys.executable}" -c "print(123)" ')
    lead = board.test_lead
    reviewer = board.caller(agent="reviewer", token=board.test_tokens["reviewer"])
    board.verify("task", lead)
    board.move("task", "review", worker)
    board.review("task", reviewer, "changes")
    board.move("task", "review", worker)
    board.review("task", reviewer, "comment")
    board.review("task", reviewer, "approve")
    result = row(board)
    assert result["score"] == 75
    assert result["inputs"]["verify_passed"] == 1
    assert result["inputs"]["review_approve"] == 1
    assert result["inputs"]["review_changes"] == 1
    assert result["inputs"]["rework"] == 1
    assert row(board, "reviewer")["score"] == 50
    assert row(board, "lead")["score"] == 50


def test_failure_and_score_bounds(board):
    claim(board)
    event(board, "verify", agent="lead", data={"passed": False})
    for _ in range(12):
        event(board, "status", data={"event": "lock_denied"})
    assert row(board)["score"] == 0
    assert row(board)["inputs"]["lock_denials"] == 12
    claim(board, "bob", "other")
    event(board, "verify", task="other", data={"passed": True})
    event(board, "review", task="other", data={"verdict": "approve"}, to="bob")
    assert row(board, "bob")["score"] == 100


def test_grouped_counts_are_pooled(board):
    claim(board)
    claim(board, "bob", "other")
    event(board, "verify", data={"passed": False})
    for _ in range(3):
        event(board, "verify", task="other", data={"passed": True})
    result = row(board, "m1", "model")
    assert result["inputs"]["verify_pass_rate"] == 0.75
    assert result["score"] == 65
    assert row(board, "openai", "provider")["score"] == 65


def test_historical_owner_and_model(board):
    alice = claim(board)
    event(board, "verify", agent="lead", data={"passed": True})
    board.release("task", alice)
    lead = board.test_lead
    board.join(lead, agent="alice", model_id="new-model", provider="openai")
    board.claim("task", lead, for_agent="bob")
    event(board, "verify", agent="lead", data={"passed": False})
    assert row(board)["score"] == 80
    assert row(board, "bob")["score"] == 20
    assert row(board, "m1", "model")["inputs"]["verify_passed"] == 1
    assert row(board, "new-model", "model")["inputs"]["verify_passed"] == 0


def test_lock_events_ignore_prose_and_charge_actor(board):
    claim(board)
    event(board, "note", data={"text": "lock_denied"})
    event(board, "status", agent="lead", data={"event": "lock_denied", "agent": "alice"})
    event(board, "artifact", data={"event": "lock_violation"})
    assert row(board)["score"] == 30
    assert row(board, "lead")["score"] == 50


def test_stall_is_read_only_and_not_double_counted(board):
    lead = board.test_lead
    board.new_card(lead, "Expired task", card_id="task")
    board.claim("task", lead, for_agent="alice", ttl_minutes=-1)
    before = board.version()
    assert row(board)["inputs"]["stalls"] == 1
    assert row(board)["inputs"]["stalls"] == 1
    assert board.version() == before
    board.reap()
    assert row(board)["inputs"]["stalls"] == 1
    assert row(board)["score"] == 45


def test_cli_entry_point(board, monkeypatch, capsys):
    from raidlock import cli

    ep = EntryPoint(name="reputation", value="raidlock.reputation:register_cli", group="raidlock.commands")
    monkeypatch.setattr("importlib.metadata.entry_points", lambda: EntryPoints([ep]))
    monkeypatch.setattr(cli, "COMMANDS", dict(cli.COMMANDS))
    monkeypatch.setattr(cli, "EXTENSION_MODULES", ())
    assert cli.main(["reputation", "--board", str(board.dir), "--by", "model", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["by"] == "model"
    assert result["rows"]
    assert result["weights"]
    assert cli.main(["reputation", "--board", str(board.dir)]) == 0
    assert "verify_passed=0" in capsys.readouterr().out
    assert cli.main(["reputation", "--board", str(board.dir), "--by", "team"]) == 3


def test_changes_without_lease_is_not_rework(board):
    worker = claim(board)
    board.move("task", "review", worker)
    board.release("task", worker)
    board.review("task", board.caller(agent="reviewer", token=board.test_tokens["reviewer"]), "changes")
    assert board.card("task")["lane"] == "blocked"
    assert row(board)["inputs"]["review_changes"] == 1
    assert row(board)["inputs"]["rework"] == 0


def test_card_history_fallback(board, monkeypatch):
    worker = claim(board, verify=f"{shlex.quote(sys.executable)} -c 'raise SystemExit(1)'")
    board.verify("task", board.test_lead)
    board.move("task", "review", worker)
    board.review("task", board.caller(agent="reviewer", token=board.test_tokens["reviewer"]), "approve")
    monkeypatch.setattr(board.transcript, "read", lambda: [])
    assert row(board)["inputs"]["verify_failed"] == 1
    assert row(board)["inputs"]["review_approve"] == 1
    assert row(board)["score"] == 40


def test_unknown_model_and_empty_board(board, monkeypatch):
    monkeypatch.setattr(board, "agents", lambda: {"anonymous": {}})
    monkeypatch.setattr(board, "statuses", lambda: {})
    assert row(board, "unknown", "model")["score"] == 50
    monkeypatch.setattr(board, "agents", lambda: {})
    assert reputation(board)["rows"] == []


def test_real_core_denial_events_and_readonly_formula(board):
    worker = claim(board)
    worker.fence += 1
    with pytest.raises(Denied):
        board.note("task", worker, "stale update")
    bob = board.caller(agent="bob", token=board.test_tokens["bob"])
    with pytest.raises(Denied):
        board.claim("task", bob)
    assert not board.check_write(str(Path(board.dir) / "cards" / "task.json"), worker)["allowed"]
    before = board.version()
    result = reputation(board)
    alice = next(r for r in result["rows"] if r["key"] == "alice")
    assert alice["inputs"]["lock_denials"] == 1
    assert alice["inputs"]["lock_violations"] == 1
    assert row(board, "bob")["inputs"]["lock_denials"] == 1
    assert alice["score"] == max(0, min(100, result["base_score"] + sum(alice["contributions"].values())))
    assert board.version() == before


def test_all_current_stalled_cards_count_per_episode(board):
    claim(board, card="first")
    claim(board, card="second")
    for task in ("first", "second"):
        board._update_lease(task, expires="2000-01-01T00:00:00Z")
    assert row(board)["inputs"]["stalls"] == 2
    board.reap()
    assert row(board)["inputs"]["stalls"] == 2
    assert row(board)["score"] == 40


def test_stall_model_is_claim_time_model(board):
    claim(board)
    board.join(board.test_lead, agent="alice", model_id="new-model", provider="openai")
    board._update_lease("task", expires="2000-01-01T00:00:00Z")
    assert row(board, "m1", "model")["inputs"]["stalls"] == 1
    assert row(board, "new-model", "model")["inputs"]["stalls"] == 0


def test_review_follows_historical_owner_model(board):
    alice = claim(board)
    board.move("task", "review", alice)
    reviewer = board.caller(agent="reviewer", token=board.test_tokens["reviewer"])
    board.review("task", reviewer, "approve")
    board.release("task", alice)
    board.join(board.test_lead, agent="alice", model_id="new-model", provider="openai")
    board.move("task", "todo", board.test_lead)
    board.claim("task", board.test_lead, for_agent="bob")
    assert row(board, "m1", "model")["inputs"]["review_approve"] == 1
    assert row(board, "new-model", "model")["inputs"]["review_approve"] == 0
    assert row(board, "reviewer")["score"] == 50


def test_resumed_lease_starts_another_recorded_stall_episode(board):
    claim(board)
    sender = {"agent": "alice", "model": "m1", "provider": "openai"}
    for text in ("lease expired; reaped to blocked", "lease resumed after expiry", "lease expired; reaped to blocked"):
        board.transcript.append(make("status", sender, task="task", text=text))
    assert row(board)["inputs"]["stalls"] == 2
