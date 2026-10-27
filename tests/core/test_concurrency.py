import os
import subprocess
import sys
import textwrap
import time

from corehelpers import SRC

N = 8
VARIANTS = ("app/shared", "app/./shared/", "app\\shared", "app//shared", "app")

RACER = textwrap.dedent("""
    import os, sys, time
    sys.path.insert(0, os.environ["RL_SRC"])
    from raidlock.core import Board, RaidlockError
    go = os.environ["RL_GO"]
    board = Board.open(os.environ["RL_WS"])
    caller = board.caller(agent=os.environ["RL_AGENT"], token=os.environ["RL_TOKEN"])
    deadline = time.time() + 30
    while not os.path.exists(go) and time.time() < deadline:
        time.sleep(0.001)
    try:
        board.claim(os.environ["RL_CARD"], caller)
    except RaidlockError as e:
        sys.exit(e.code)
""")


def _race(world, tmp_path, cards):
    script = tmp_path / "racer.py"
    script.write_text(RACER, encoding="utf-8")
    go = tmp_path / "go"
    procs = []
    for i, card in enumerate(cards):
        agent = world.agent(f"racer-{i}")
        env = {
            **os.environ,
            "RL_SRC": SRC,
            "RL_GO": str(go),
            "RL_WS": str(world.root),
            "RL_AGENT": agent.name,
            "RL_TOKEN": agent.token,
            "RL_CARD": card,
        }
        procs.append(subprocess.Popen([sys.executable, str(script)], env=env))
    time.sleep(1.0)
    go.write_text("go", encoding="utf-8")
    return sorted(p.wait(60) for p in procs)


def test_racing_one_card_exactly_one_wins(world, tmp_path):
    world.board.add_team(world.lead, "core", N + 1)
    world.card("prize")
    codes = _race(world, tmp_path, ["prize"] * N)
    assert codes == [0] + [2] * (N - 1)
    assert len(world.board.leases()) == 1


def test_racing_overlapping_cards_exactly_one_wins(world, tmp_path):
    world.board.add_team(world.lead, "core", N + 1)
    cards = []
    for i in range(N):
        cid = f"card-{i}"
        world.card(cid, locks=VARIANTS[i % len(VARIANTS)])
        cards.append(cid)
    codes = _race(world, tmp_path, cards)
    assert codes == [0] + [2] * (N - 1)
    leases = world.board.leases()
    assert len(leases) == 1
    fences = [c["fence"] for c in world.board.cards() if c["lane"] == "doing"]
    assert len(fences) == 1


def test_racing_across_workspaces(tmp_path):
    from corehelpers import World

    a, b = World(tmp_path, "ws-a"), World(tmp_path, "ws-b")
    a.card("c-a", locks="app/hot")
    b.card("c-b", locks="app/hot")
    script = tmp_path / "racer.py"
    script.write_text(RACER, encoding="utf-8")
    go = tmp_path / "go"
    procs = []
    for world, card, name in ((a, "c-a", "racer-a"), (b, "c-b", "racer-b")):
        agent = world.agent(name)
        env = {
            **os.environ,
            "RL_SRC": SRC,
            "RL_GO": str(go),
            "RL_WS": str(world.root),
            "RL_AGENT": name,
            "RL_TOKEN": agent.token,
            "RL_CARD": card,
        }
        procs.append(subprocess.Popen([sys.executable, str(script)], env=env))
    time.sleep(1.0)
    go.write_text("go", encoding="utf-8")
    assert sorted(p.wait(60) for p in procs) == [0, 2]
