import json
import shlex
import sys
from pathlib import Path

import pytest
from runhelpers import fake, git

from raidlock import budgets, duel
from raidlock.core.errors import Denied, Precondition, RaidlockError
from raidlock.runners import isolation
from raidlock.runners.runner import latest_patch

DONE = '{"status":"done","evidence":["ok"],"paths_changed":[],"open_questions":[]}'
USAGE = '{"kind":"usage","input":1000,"output":100,"usd":0.1}'
CHECK = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
    "import pathlib, sys; sys.exit(0 if pathlib.Path('src/winner.py').exists() else 1)"
)


@pytest.fixture(autouse=True)
def sandboxed(monkeypatch):
    original = isolation.run
    monkeypatch.setattr(isolation, "mode", lambda: "test-isolation")
    monkeypatch.setattr(isolation, "run", lambda *a, **k: original(*a, **{**k, "kind": None}))


def contestants(winner_side="a"):
    good = fake("--write", "src/winner.py=1", "--say", USAGE, "--say", DONE)
    bad = fake("--write", "src/loser.py=2", "--write", "src/extra.py=3", "--say", USAGE, "--say", DONE)
    return [good, bad] if winner_side == "a" else [bad, good]


def start(ws, winner_side="a", **kw):
    ws.card("api", verify_cmd=CHECK)
    return duel.run(ws.board, ws.lead, "api", "shell,shell", cmds=contestants(winner_side), heartbeat=0.05, **kw)


def test_duel_runs_both_in_isolated_clones_and_compares_evidence(ws):
    r = start(ws)
    assert r["status"] == "decide"
    a, b = r["sides"]
    assert a["run_dir"] != b["run_dir"]
    assert a["agent"] != b["agent"]
    assert a["receipt"]["passed"] is True and b["receipt"]["passed"] is False
    assert a["receipt"]["sha"] == a["sha"] != b["sha"]
    assert r["verified"] == ["a"]
    rows = {row["side"]: row for row in r["rows"]}
    assert (rows["a"]["verify"], rows["b"]["verify"]) == ("passed", "failed")
    assert (rows["a"]["files"], rows["b"]["files"]) == (1, 2)
    assert rows["b"]["bytes"] > rows["a"]["bytes"]
    assert rows["a"]["usd"] == pytest.approx(0.1)
    card = ws.board.card("api")
    assert len(card["costs"]) == 2
    assert card["lane"] == "blocked" and card["owner"] == b["agent"]
    joined = ws.board.join(ws.lead, agent="bystander", team="core")
    with pytest.raises(Denied, match="take-over"):
        ws.board.claim("api", ws.board.caller(agent="bystander", token=joined["token"]))


def test_contestant_fences_go_stale_between_runs(ws):
    r = start(ws)
    a = r["sides"][0]
    notes = [n["text"] for n in ws.board.card("api")["notes"] if "claimed by " + a["agent"] in n["text"]]
    fence = int(notes[0].split("fence ")[1].split(")")[0])
    token = Path(ws.board.home, "tokens", ws.board.uid, a["agent"] + ".token").read_text().strip()
    stale = ws.board.caller(agent=a["agent"], token=token, fence=fence)
    with pytest.raises(Denied):
        ws.board.heartbeat("api", stale)


def test_winner_needs_a_passing_signed_receipt(ws):
    r = start(ws)
    with pytest.raises(Precondition, match="receipt"):
        duel.pick(ws.board, ws.lead, "api", "b")
    with pytest.raises(Precondition, match="receipt"):
        duel.pick(ws.board, ws.lead, "api", r["sides"][1]["agent"])
    assert ws.board.card("api")["lane"] == "blocked"


def test_pick_hands_card_to_winner_and_discards_loser(ws):
    r = start(ws)
    a, b = r["sides"]
    out = duel.pick(ws.board, ws.lead, "api", "a")
    assert out["winner"] == "a" and out["verify"]["passed"] is True
    card = ws.board.card("api")
    assert (card["lane"], card["owner"]) == ("review", a["agent"])
    assert not Path(b["run_dir"], "changes.patch").exists()
    assert not Path(b["clone"]).exists()
    assert latest_patch(ws.board, "api")[0] == a["run_dir"]
    assert ws.board.move("api", "done", ws.lead)["lane"] == "done"
    assert duel.load(ws.board, "api")["winner"] == "a"
    with pytest.raises(Precondition):
        duel.pick(ws.board, ws.lead, "api", "a")


def test_winner_order_does_not_matter(ws):
    r = start(ws, winner_side="b")
    assert r["verified"] == ["b"]
    assert duel.pick(ws.board, ws.lead, "api", "b")["winner"] == "b"


def test_forged_duel_record_cannot_crown_the_loser(ws):
    start(ws)
    path = Path(duel.record_path(ws.board, "api"))
    record = json.loads(path.read_text())
    a, b = record["sides"]
    b["receipt"] = dict(a["receipt"])
    b["sha"] = a["sha"]
    path.write_text(json.dumps(record))
    with pytest.raises(Precondition, match="receipt"):
        duel.pick(ws.board, ws.lead, "api", "b")
    b["receipt"]["signature"] = "0" * 64
    b["receipt"]["passed"] = True
    path.write_text(json.dumps(record))
    with pytest.raises(Precondition, match="receipt"):
        duel.pick(ws.board, ws.lead, "api", "b")


def test_winner_clone_changed_after_verify_is_refused(ws):
    r = start(ws)
    clone = r["sides"][0]["clone"]
    Path(clone, "src", "winner.py").write_text("tampered\n")
    git(clone, "commit", "-qam", "after the receipt")
    with pytest.raises(Precondition, match="receipt"):
        duel.pick(ws.board, ws.lead, "api", "a")


def test_changed_verify_command_invalidates_receipts(ws):
    start(ws)
    card = ws.board.card("api")
    card["verify_cmd"] = "true"
    ws.board._save_card(card)
    with pytest.raises(Precondition, match="evidence"):
        duel.pick(ws.board, ws.lead, "api", "a")


def test_both_runs_count_toward_the_budget(ws):
    ws.card("api", verify_cmd=CHECK)
    budgets.set_budget(ws.board, ws.lead, "budget.card.api", {"usd": 0.05})
    r = duel.run(ws.board, ws.lead, "api", "shell,shell", cmds=contestants(), heartbeat=0.05)
    assert r["status"] == "incomplete"
    assert r["sides"][1]["status"] == "refused"
    assert "budget" in r["sides"][1]["reason"]
    assert ws.board.card("api")["lane"] == "todo"
    assert len(ws.board.card("api")["costs"]) == 1
    with pytest.raises(Precondition, match="incomplete"):
        duel.pick(ws.board, ws.lead, "api", "a")


def test_workers_cannot_run_or_decide_a_duel(ws):
    start(ws)
    joined = ws.board.join(ws.lead, agent="worker-x", team="core")
    worker = ws.board.caller(agent="worker-x", token=joined["token"])
    with pytest.raises(RaidlockError):
        duel.pick(ws.board, worker, "api", "a")
    ws.card("other", locks="app/README.md", verify_cmd=CHECK)
    with pytest.raises(Precondition):
        duel.run(ws.board, worker, "other", "shell,shell", cmds=contestants())


@pytest.mark.parametrize(
    "vs, cmds, match",
    [
        ("shell", ["x"], "exactly two"),
        ("shell,shell,shell", ["x", "y", "z"], "exactly two"),
        ("shell,shell", ["same", "same"], "identical"),
        ("shell,paid", ["x"], "not one of"),
        ("shell,shell", ["x"], "one --cmd"),
    ],
)
def test_bad_duel_specs_are_refused_before_anything_runs(ws, vs, cmds, match):
    ws.card("api", verify_cmd=CHECK)
    with pytest.raises(Precondition, match=match):
        duel.run(ws.board, ws.lead, "api", vs, cmds=cmds)
    assert ws.board.card("api")["lane"] == "todo"
    assert duel.load(ws.board, "api") is None


def test_duel_without_verify_command_is_refused(ws):
    ws.card("api")
    with pytest.raises(Precondition, match="verify command"):
        duel.run(ws.board, ws.lead, "api", "shell,shell", cmds=contestants())
    assert ws.board.card("api")["costs"] == []


def test_undecided_duel_blocks_a_second_one(ws):
    start(ws)
    with pytest.raises(Precondition, match="undecided"):
        duel.run(ws.board, ws.lead, "api", "shell,shell", cmds=contestants())


def test_cli_shows_comparison(ws, tmp_path):
    from runhelpers import run_cli

    start(ws)
    secret = tmp_path / "lead.secret"
    secret.write_text(ws.token)
    secret.chmod(0o600)
    p = run_cli("duel", "api", "--board", str(ws.root), "--lead-secret", str(secret))
    assert p.returncode == 0, p.stderr
    assert "side  runtime" in p.stdout and "passed" in p.stdout


def test_pick_never_drops_a_bystanders_lease(ws):
    start(ws)
    joined = ws.board.join(ws.lead, agent="bystander", team="core")
    grant = ws.board.claim("api", ws.board.caller(agent="bystander", token=joined["token"]), take_over=True)
    with pytest.raises(Denied, match="bystander"):
        duel.pick(ws.board, ws.lead, "api", "a")
    card = ws.board.card("api")
    assert (card["lane"], card["owner"], card["fence"]) == ("doing", "bystander", grant["fence"])


def test_discarded_duel_clone_cannot_resurface_in_resume(ws):
    from raidlock import resume

    result = start(ws)
    loser = result["sides"][1]
    duel.pick(ws.board, ws.lead, "api", "a")
    assert Path(loser["run_dir"], "discarded.json").exists()
    assert not Path(loser["run_dir"], "repo").exists()
    packet = resume.build(ws.board, ws.lead, "api")
    assert "src/loser.py" not in packet["paths"]
    assert packet["run_dir"] == result["sides"][0]["run_dir"]


def test_failed_final_receipt_preserves_duel_and_loser(ws, tmp_path):
    marker = tmp_path / "verification-enabled"
    marker.touch()
    ws.card("api", verify_cmd=f"test -f {shlex.quote(str(marker))}")
    result = duel.run(ws.board, ws.lead, "api", "shell,shell", cmds=contestants(), heartbeat=0.05)
    loser = result["sides"][1]
    marker.unlink()
    with pytest.raises(Precondition, match="final verification"):
        duel.pick(ws.board, ws.lead, "api", "a")
    assert duel.load(ws.board, "api")["status"] == "decide"
    assert not duel.load(ws.board, "api").get("winner")
    assert Path(loser["clone"]).exists()
    assert Path(loser["run_dir"], "repo").exists()
    assert Path(loser["run_dir"], "changes.patch").exists()
    assert not Path(loser["run_dir"], "discarded.json").exists()
