import json
import os
import statistics
import subprocess
import sys
import textwrap
import time

import pytest
from corehelpers import SRC, make_repo, run_cli

from raidlock.core import Board, clock, identity
from raidlock.core.db import Index

pytestmark = pytest.mark.slow

WORKSPACES = 50
AGENTS = 20
CARDS = 1000
REMOTES = 5
GROUPS = 20
PER_GROUP = 5
FREE = 100
PROCS = 20
RESULTS = {}

WORKER = textwrap.dedent("""
    import json, os, sys, time
    sys.path.insert(0, os.environ["RL_SRC"])
    from raidlock.core import Board, RaidlockError
    jobs = json.loads(os.environ["RL_JOBS"])
    boards = {}
    while not os.path.exists(os.environ["RL_GO"]):
        time.sleep(0.001)
    out = []
    for ws, card, agent, token in jobs:
        start = time.perf_counter()
        board = boards.get(ws) or boards.setdefault(ws, Board.open(ws))
        try:
            fence = board.claim(card, board.caller(agent=agent, token=token))["fence"]
            code = 0
        except RaidlockError as e:
            fence, code = 0, e.code
        out.append({"ws": ws, "card": card, "agent": agent, "code": code, "fence": fence,
                    "ms": (time.perf_counter() - start) * 1000})
    print(json.dumps(out))
""")


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _seed_workspace(root, i, home):
    ws = root / f"ws{i:02d}"
    make_repo(ws / "app", f"git@github.com:acme/repo{i % REMOTES}.git")
    board, lead_token = Board.init(ws)
    lead = board.caller(agent=board.lead, token=lead_token)
    board.add_team(lead, "core", CARDS)
    now = clock.iso()
    agents, tokens = {}, {}
    for a in range(AGENTS):
        name = f"agent-{a:02d}"
        agents[name] = {
            "team": "core",
            "role": "worker",
            "provider": "anthropic",
            "runtime": "claude-code",
            "model": "claude-opus-5-5",
            "display": f"{name} · Claude Opus 5.5 · claude-code",
            "joined": now,
            "last_seen": now,
            "card": "",
        }
        tokens[name] = identity.issue_token(home, board.uid, name)
    agents[board.lead] = board.agents()[board.lead]
    _write(ws / ".board" / "agents.json", {"schema": 2, "agents": agents})
    cards = []
    for c in range(CARDS):
        card = {
            "id": f"card-{c:04d}",
            "title": f"Task {c}",
            "lane": "todo",
            "owner": "",
            "team": "core",
            "ticket": "",
            "scope": "",
            "output": "",
            "verify": "",
            "verify_cmd": "",
            "locks": [f"app/area{c % 50}/mod{c}"],
            "depends": [],
            "reviewers": [],
            "fence": 0,
            "rev": 0,
            "released": False,
            "created": now,
            "updated": now,
            "notes": [],
            "reviews": [],
            "verifications": [],
            "costs": [],
        }
        cards.append(card)
        _write(ws / ".board" / "cards" / f"{card['id']}.json", {"schema": 2, "card": card})
    board.index.put_cards(board.uid, cards)
    resolver = board._resolver()
    for c in range(0, CARDS, 100):
        cid = f"card-{c:04d}"
        board.index.put_lease(
            board.uid,
            {
                "card": cid,
                "agent": "agent-00",
                "paths": [f"app/area{c % 50}/mod{c}"],
                "keys": [resolver.resolve(f"app/area{c % 50}/mod{c}")],
                "fence": 0,
                "since": now,
                "renewed": now,
                "expires": clock.later(3600),
                "checkout": None,
                "branch": "",
            },
            mint=True,
        )
    return str(ws), board, tokens


@pytest.fixture(scope="module")
def fleet(tmp_path_factory):
    root = tmp_path_factory.mktemp("fleet")
    home = str(root / "home")
    os.environ["RAIDLOCK_HOME"] = home
    start = time.perf_counter()
    spaces = [_seed_workspace(root, i, home) for i in range(WORKSPACES)]
    RESULTS["seed_s"] = time.perf_counter() - start
    return root, home, spaces


def _plan_jobs(spaces):
    jobs, groups, free = [], {}, []
    for g in range(GROUPS):
        remote = g % REMOTES
        members = [s for s in spaces if int(os.path.basename(s[0])[2:]) % REMOTES == remote][:PER_GROUP]
        for k, (ws, board, tokens) in enumerate(members):
            cid = f"hot-{g:02d}"
            variant = (f"app/hot{g}", f"app/hot{g}/", f"app/./hot{g}", f"app\\hot{g}", f"app//hot{g}/x")[k % 5]
            _direct_card(board, cid, [variant])
            agent = f"agent-{(g + k) % AGENTS:02d}"
            jobs.append((ws, cid, agent, tokens[agent]))
            groups.setdefault(g, []).append((ws, cid))
    for f in range(FREE):
        ws, board, tokens = spaces[f % WORKSPACES]
        cid = f"free-{f:03d}"
        _direct_card(board, cid, [f"app/free/{f}"])
        agent = f"agent-{f % AGENTS:02d}"
        jobs.append((ws, cid, agent, tokens[agent]))
        free.append((ws, cid))
    return jobs, groups, free


def _direct_card(board, cid, locks):
    board.new_card(_lead(board), cid, card_id=cid, team="core", locks=locks)


def _lead(board):
    path = identity.token_path(board.home, board.uid, board.lead)
    with open(path, encoding="utf-8") as f:
        return board.caller(agent=board.lead, token=f.read().strip())


def test_scale_claims_and_reads(fleet, tmp_path, monkeypatch):
    root, home, spaces = fleet
    monkeypatch.setenv("RAIDLOCK_HOME", home)
    jobs, groups, free = _plan_jobs(spaces)
    assert len(jobs) == GROUPS * PER_GROUP + FREE == 200
    jobs = jobs[::2] + jobs[1::2]
    script = tmp_path / "worker.py"
    script.write_text(WORKER, encoding="utf-8")
    go = tmp_path / "go"
    procs = []
    for p in range(PROCS):
        mine = jobs[p::PROCS]
        env = {**os.environ, "RL_SRC": SRC, "RL_GO": str(go), "RL_JOBS": json.dumps(mine), "RAIDLOCK_HOME": home}
        procs.append(subprocess.Popen([sys.executable, str(script)], env=env, stdout=subprocess.PIPE, text=True))
    time.sleep(2.0)
    go.write_text("go", encoding="utf-8")
    results = []
    for proc in procs:
        out, _ = proc.communicate(timeout=300)
        assert proc.returncode == 0
        results.extend(json.loads(out))
    assert len(results) == 200

    by_card = {(r["ws"], r["card"]): r for r in results}
    for g, members in groups.items():
        codes = sorted(by_card[m]["code"] for m in members)
        assert codes == [0] + [2] * (len(members) - 1), (g, codes)
    assert all(by_card[f]["code"] == 0 for f in free)

    winners = [r for r in results if r["code"] == 0]
    assert len({r["fence"] for r in winners}) == len(winners)
    for r in winners:
        board = Board.open(r["ws"], home=home)
        card = board.card(r["card"])
        lease = board.index.lease(board.uid, r["card"])
        assert card["lane"] == "doing" and card["owner"] == r["agent"]
        assert lease and lease["fence"] == card["fence"] == r["fence"]

    latencies = sorted(r["ms"] for r in results)
    p95 = latencies[int(0.95 * len(latencies)) - 1]
    RESULTS.update(
        claims=len(results), winners=len(winners), p50_ms=statistics.median(latencies), p95_ms=p95, max_ms=latencies[-1]
    )

    ws0 = spaces[0][0]
    env = {"RAIDLOCK_HOME": home, "RAIDLOCK_BOARD": ws0}
    timings = {}
    for name, args in (("who", ("who", "--json")), ("show", ("show", "--json")), ("version", ("version",))):
        best = []
        for _ in range(3):
            t0 = time.perf_counter()
            r = run_cli(*args, env=env, cwd=str(root))
            best.append((time.perf_counter() - t0) * 1000)
            assert r.returncode == 0, r.stderr
        timings[name] = min(best)
    RESULTS.update(who_ms=timings["who"], show_ms=timings["show"], startup_ms=timings["version"])
    board = Board.open(ws0, home=home)
    t0 = time.perf_counter()
    board.snapshot()
    RESULTS["snapshot_ms"] = (time.perf_counter() - t0) * 1000
    total = sum(len(Index(home).leases(w["uid"])) for w in Index(home).workspaces())
    RESULTS["leases_total"] = total
    print("\nSCALE " + json.dumps({k: round(v, 1) if isinstance(v, float) else v for k, v in RESULTS.items()}))
    assert p95 < float(os.environ.get("RAIDLOCK_SCALE_P95_MS", "100")), RESULTS
    assert timings["who"] < 200 and timings["show"] < 200, RESULTS
