import json
import os
import shlex
import sys
import time
from pathlib import Path

import pytest
from runhelpers import FAKE, FIXTURES, fake

from raidlock import cli, resurrect
from raidlock.core import clock
from raidlock.core.errors import Denied, IdentityError, Precondition, RaidlockError
from raidlock.runners.runner import delegate, run_numbers

HERE = Path(__file__).resolve().parent
QUOTA_ERROR = '{"kind":"error","text":"usage limit reached: credit exhausted"}'
BUSY_ERROR = '{"kind":"error","text":"429 too many requests"}'
DONE = '{"status":"done","evidence":["ok"],"paths_changed":[],"open_questions":[]}'


def claude_bin(*args):
    return shlex.join([sys.executable, str(HERE / "fake_claude.py"), *args])


def codex_bin(tmp_path, *args):
    return shlex.join(
        [
            sys.executable,
            str(FAKE),
            "--replay",
            str(FIXTURES / "codex_workspace_write.jsonl"),
            "--dump-env",
            str(tmp_path / "codex-env.json"),
            "--dump-prompt",
            str(tmp_path / "codex-prompt.md"),
            *args,
        ]
    )


def codex_headroom(used=10):
    root = Path(os.environ["CODEX_HOME"], "sessions")
    root.mkdir(parents=True, exist_ok=True)
    now = time.time()
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now)),
        "payload": {
            "rate_limits": {
                "primary": {"used_percent": used, "window_minutes": 300, "resets_at": now + 3600},
                "secondary": {"used_percent": used, "window_minutes": 10080, "resets_at": now + 86400},
            }
        },
    }
    (root / "s.jsonl").write_text(json.dumps(record) + "\n")


def claude_quota_failure(ws, monkeypatch):
    ws.card("api")
    monkeypatch.setenv(
        "RAIDLOCK_CLAUDE_BIN", claude_bin("--quota", "--write", "src/main.py=half", "--write", "src/partial.py=wip")
    )
    r = delegate(ws.board, ws.lead, "api", "claude", heartbeat=0.05)
    assert r["lane"] == "blocked"
    assert "usage limit" in r["reason"]
    return r


def policy(ws, **values):
    for key, value in values.items():
        resurrect.set_policy(ws.board, ws.lead, key, value)


def test_quota_failure_continues_on_another_provider_with_partial_work(ws, monkeypatch, tmp_path):
    first = claude_quota_failure(ws, monkeypatch)
    old_owner = ws.board.card("api")["owner"]
    codex_headroom()
    monkeypatch.setenv("RAIDLOCK_CODEX_BIN", codex_bin(tmp_path, "--write", "src/done.py=1"))
    policy(ws, providers=["codex"], cooldown_minutes=0)
    monkeypatch.setenv("RAIDLOCK_LEAD_SECRET", ws.token)
    r = resurrect.attempt(ws.board, ws.lead, "api", heartbeat=0.05)
    assert (r["classification"], r["from_provider"], r["to"]) == ("quota", "claude", "codex")
    assert r["from_agent"] == old_owner and r["agent"] != old_owner
    assert r["status"] == "ran" and r["lane"] == "review"
    patch = Path(r["run_dir"], "changes.patch").read_text()
    for path in ("src/main.py", "src/partial.py", "src/done.py"):
        assert f"b/{path}" in patch
    assert Path(first["run_dir"], "changes.patch").exists()
    env = json.loads((tmp_path / "codex-env.json").read_text())
    assert not [k for k in env if k.startswith("RAIDLOCK_LEAD")]
    assert ws.token not in json.dumps(env)
    prompt = (tmp_path / "codex-prompt.md").read_text()
    assert "Resume packet" in prompt and ws.token not in prompt
    card = ws.board.card("api")
    assert card["owner"] == r["agent"]
    assert [c["by"] for c in card["costs"]] == [old_owner, r["agent"]]
    ledger = resurrect.attempts(ws.board, "api")
    assert ledger[0]["to"] == "codex" and ledger[0]["classification"] == "quota"
    alerts = [x for x in ws.board.transcript.read(task="api") if "ALERT RESURRECT" in json.dumps(x)]
    assert alerts


def test_exhausted_provider_without_fallback_stays_blocked(ws, monkeypatch):
    claude_quota_failure(ws, monkeypatch)
    before = ws.board.card("api")["owner"]
    r = resurrect.attempt(ws.board, ws.lead, "api")
    assert r["status"] == "stuck"
    card = ws.board.card("api")
    assert (card["lane"], card["owner"]) == ("blocked", before)
    assert "resurrect stuck" in card["notes"][-1]["text"]
    assert resurrect.attempts(ws.board, "api") == []


def test_max_attempts_are_never_exceeded(ws, monkeypatch):
    ws.card("api")
    failing = fake("--write", "src/a.py=1", "--say", QUOTA_ERROR, "--exit", "1")
    delegate(ws.board, ws.lead, "api", "shell", heartbeat=0.05, cmd=failing)
    policy(ws, providers=["shell"], cooldown_minutes=0, max_attempts=1)
    r = resurrect.attempt(ws.board, ws.lead, "api", to="shell", cmd=failing, heartbeat=0.05)
    assert r["lane"] == "blocked"
    runs = len(run_numbers(ws.board, "api"))
    with pytest.raises(Denied, match="attempts exhausted"):
        resurrect.attempt(ws.board, ws.lead, "api", to="shell", cmd=failing, heartbeat=0.05)
    assert len(run_numbers(ws.board, "api")) == runs
    assert ws.board.card("api")["lane"] == "blocked"
    assert any("resurrect refused" in json.dumps(x) for x in ws.board.transcript.read(task="api"))


def test_budget_stops_resurrection(ws, monkeypatch):
    claude_quota_failure(ws, monkeypatch)
    policy(ws, providers=["shell"], budget_usd=0.01)
    owner = ws.board.card("api")["owner"]
    with pytest.raises(Denied, match="budget exhausted"):
        resurrect.attempt(ws.board, ws.lead, "api", cmd=fake("--say", DONE))
    assert ws.board.card("api")["owner"] == owner
    resurrect.set_policy(ws.board, ws.lead, "budget_usd", None)
    ws.board.set_setting(ws.lead, "budget.card.api", {"usd": 0.01})
    with pytest.raises(Denied, match="budget exhausted"):
        resurrect.attempt(ws.board, ws.lead, "api", cmd=fake("--say", DONE))


def test_live_worker_is_never_duplicated(ws):
    ws.card("api")
    joined = ws.board.join(ws.lead, agent="worker-a", team="core")
    ws.board.claim("api", ws.board.caller(agent="worker-a", token=joined["token"]))
    policy(ws, providers=["shell"])
    with pytest.raises(Denied, match="live worker"):
        resurrect.attempt(ws.board, ws.lead, "api", cmd=fake("--say", DONE))
    assert ws.board.card("api")["owner"] == "worker-a"
    assert run_numbers(ws.board, "api") == []


def test_expired_worker_is_fenced_out_and_replaced(ws):
    ws.card("api")
    joined = ws.board.join(ws.lead, agent="worker-a", team="core")
    grant = ws.board.claim("api", ws.board.caller(agent="worker-a", token=joined["token"]), ttl_minutes=1)
    old = ws.board.caller(agent="worker-a", token=joined["token"], fence=grant["fence"])
    clock.travel(120)
    policy(ws, providers=["shell"], cooldown_minutes=0)
    r = resurrect.attempt(ws.board, ws.lead, "api", cmd=fake("--write", "src/x.py=1", "--say", DONE), heartbeat=0.05)
    assert r["classification"] == "expired"
    assert ws.board.card("api")["fence"] != grant["fence"]
    with pytest.raises(Denied):
        ws.board.heartbeat("api", old)
    with pytest.raises(Denied):
        ws.board.move("api", "blocked", old)


def test_cooldown_between_attempts(ws):
    ws.card("api")
    failing = fake("--say", BUSY_ERROR, "--exit", "1")
    delegate(ws.board, ws.lead, "api", "shell", heartbeat=0.05, cmd=failing)
    policy(ws, providers=["shell"], cooldown_minutes=30, max_attempts=5)
    resurrect.attempt(ws.board, ws.lead, "api", cmd=failing, heartbeat=0.05)
    with pytest.raises(Precondition, match="cooling down"):
        resurrect.attempt(ws.board, ws.lead, "api", cmd=failing, heartbeat=0.05)
    clock.travel(31 * 60)
    assert resurrect.attempt(ws.board, ws.lead, "api", cmd=failing, heartbeat=0.05)["attempt"] == 2


def test_sweep_is_off_by_default_then_acts_once(ws):
    ws.card("api")
    ws.card("asked", locks="app/README.md")
    failing = fake("--write", "src/a.py=1", "--say", BUSY_ERROR, "--exit", "1")
    delegate(ws.board, ws.lead, "api", "shell", heartbeat=0.05, cmd=failing)
    blocked = '{"status":"blocked","evidence":[],"paths_changed":[],"open_questions":["which API?"]}'
    delegate(ws.board, ws.lead, "asked", "shell", heartbeat=0.05, cmd=fake("--say", blocked))
    assert resurrect.sweep(ws.board, ws.lead) == {"enabled": False, "acted": [], "skipped": []}
    assert ws.board.card("api")["lane"] == "blocked"
    policy(ws, enabled=True, providers=["shell"], cooldown_minutes=0)
    r = resurrect.sweep(ws.board, ws.lead, cmd=fake("--say", DONE), heartbeat=0.05)
    assert [x["card"] for x in r["acted"]] == ["api"]
    assert r["acted"][0]["lane"] == "review"
    assert [x["card"] for x in r["skipped"]] == ["asked"]
    again = resurrect.sweep(ws.board, ws.lead, cmd=fake("--say", DONE), heartbeat=0.05)
    assert again["acted"] == []
    assert len(resurrect.attempts(ws.board, "api")) == 1
    assert ws.board.card("asked")["lane"] == "blocked"


def test_workers_and_replaced_leads_cannot_resurrect(ws):
    ws.card("api")
    joined = ws.board.join(ws.lead, agent="worker-a", team="core")
    worker = ws.board.caller(agent="worker-a", token=joined["token"])
    with pytest.raises(IdentityError):
        resurrect.attempt(ws.board, worker, "api")
    with pytest.raises(IdentityError):
        resurrect.sweep(ws.board, worker)
    with pytest.raises(RaidlockError):
        resurrect.set_policy(ws.board, worker, "enabled", True)
    assert resurrect.policy(ws.board)["enabled"] is False
    ws.board.lead_handover(ws.lead, "supervisor-session")
    with pytest.raises(RaidlockError):
        resurrect.sweep(ws.board, ws.lead)


@pytest.mark.parametrize(
    "key, value",
    [("enabled", "yes"), ("max_attempts", -1), ("providers", ["paid-api"]), ("budget_usd", -2), ("nope", 1)],
)
def test_invalid_policy_is_refused(ws, key, value):
    with pytest.raises(Precondition):
        resurrect.set_policy(ws.board, ws.lead, key, value)


def test_cli_dry_run_changes_nothing(ws, monkeypatch, tmp_path):
    from runhelpers import run_cli

    claude_quota_failure(ws, monkeypatch)
    resurrect.set_policy(ws.board, ws.lead, "providers", ["shell"])
    before = ws.board.card("api")
    secret = tmp_path / "lead.secret"
    secret.write_text(ws.token)
    secret.chmod(0o600)
    p = run_cli("resurrect", "api", "--dry-run", "--json", "--board", str(ws.root), "--lead-secret", str(secret))
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert (out["status"], out["to"]) == ("planned", "shell")
    after = ws.reopen().card("api")
    assert (after["lane"], after["owner"], after["fence"]) == (before["lane"], before["owner"], before["fence"])


def test_parked_duel_is_never_resurrected(ws, monkeypatch):
    from raidlock import duel
    from raidlock.runners import isolation

    original = isolation.run
    monkeypatch.setattr(isolation, "mode", lambda: "test-isolation")
    monkeypatch.setattr(isolation, "run", lambda *a, **k: original(*a, **{**k, "kind": None}))
    ws.card("api", verify_cmd="true")
    slow = fake("--write", "src/a.py=1", "--sleep", "5", "--say", DONE)
    r = duel.run(ws.board, ws.lead, "api", "shell,shell", cmds=[fake("--say", DONE), slow], timeout=0.5, heartbeat=0.05)
    assert r["status"] == "decide"
    assert ws.board.card("api")["lane"] == "blocked"
    policy(ws, enabled=True, providers=["shell"], cooldown_minutes=0)
    out = resurrect.sweep(ws.board, ws.lead, cmd=fake("--say", DONE), heartbeat=0.05)
    assert out["acted"] == []
    assert "duel" in out["skipped"][0]["reason"]
    assert ws.board.card("api")["lane"] == "blocked"


def test_concurrent_resurrection_reserves_one_attempt_and_records_by_id(ws, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    ws.card("api")
    delegate(ws.board, ws.lead, "api", "shell", heartbeat=0.05, cmd=fake("--say", BUSY_ERROR, "--exit", "1"))
    policy(ws, providers=["shell"], cooldown_minutes=0, max_attempts=1)
    entered, release = Event(), Event()
    calls = []

    def launch(*args, **kw):
        calls.append(kw["agent"])
        entered.set()
        assert release.wait(10)
        with ws.board.lead_write(ws.lead, "append unrelated ledger row"):
            pol, rows = resurrect._load(ws.board)
            rows["api"].append({"id": "other", "status": "sentinel"})
            resurrect._write(ws.board, pol, rows)
        return {"lane": "review", "reason": "first result"}

    monkeypatch.setattr(resurrect, "delegate", launch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(resurrect.attempt, ws.board, ws.lead, "api", to="shell", cmd="true")
        assert entered.wait(10)
        try:
            second = pool.submit(resurrect.attempt, ws.reopen(), ws.lead, "api", to="shell", cmd="true")
            with pytest.raises(Denied, match="attempts exhausted"):
                second.result(timeout=10)
        finally:
            release.set()
        result = first.result(timeout=10)
    rows = resurrect.attempts(ws.board, "api")
    assert len(calls) == 1
    assert rows[0]["id"] == result["id"]
    assert rows[0]["result"] == "first result"
    assert rows[1]["status"] == "sentinel"


def test_concurrent_resurrection_cannot_exceed_last_attempt(ws, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    ws.card("api")
    delegate(ws.board, ws.lead, "api", "shell", heartbeat=0.05, cmd=fake("--say", BUSY_ERROR, "--exit", "1"))
    policy(ws, providers=["shell"], cooldown_minutes=0, max_attempts=1)
    building, overlap, release, second_started = Event(), Event(), Event(), Event()
    guard = Lock()
    builds, launches = [], []

    def build(*args, **kw):
        with guard:
            builds.append(True)
            first = len(builds) == 1
        if first:
            building.set()
            assert release.wait(10)
        else:
            overlap.set()
        return {"dir": "unused", "status": "none", "text": "", "patch": "", "base": ""}

    def launch(*args, **kw):
        launches.append(kw["agent"])
        return {"lane": "review"}

    def second():
        second_started.set()
        return resurrect.attempt(ws.reopen(), ws.lead, "api", to="shell", cmd="true")

    monkeypatch.setattr(resurrect.R, "build", build)
    monkeypatch.setattr(resurrect, "delegate", launch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(resurrect.attempt, ws.board, ws.lead, "api", to="shell", cmd="true")
        assert building.wait(10)
        other = pool.submit(second)
        try:
            assert second_started.wait(10)
            overlap.wait(1)
        finally:
            release.set()
        first.result(timeout=10)
        with pytest.raises(Denied, match="attempts exhausted"):
            other.result(timeout=10)
    assert len(builds) == len(launches) == len(resurrect.attempts(ws.board, "api")) == 1


def test_sweep_cli_passes_replacement_options(ws, monkeypatch):
    seen = {}

    def fake_sweep(board, lead, **kw):
        seen.update(kw)
        return {"enabled": True, "acted": [], "skipped": []}

    monkeypatch.setattr(resurrect, "sweep", fake_sweep)
    secret = ws.root.parent / "lead.secret"
    secret.write_text(ws.token)
    secret.chmod(0o600)
    argv = ["--board", str(ws.root), "--lead-secret", str(secret), "resurrect", "--sweep"]
    rc = cli.main([*argv, "--to", "codex", "--model", "gpt-6-mini", "--max-attempts", "2"])
    assert rc == 0
    assert seen["model"] == "gpt-6-mini"
    assert seen["max_attempts"] == 2
    assert seen["to"]
