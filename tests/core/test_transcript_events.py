import pytest

from raidlock.core.errors import Denied


def _events(board, event):
    out = []
    for r in board.transcript.read(limit=0):
        for p in r.get("parts", []):
            if p.get("kind") == "data" and p["data"].get("event") == event:
                out.append((r, p["data"]))
    return out


def test_claim_denial_is_recorded_as_lock_denied(world):
    w1, w2 = world.agent("w-one"), world.agent("w-two")
    world.card("c1", locks="app/src")
    world.card("c2", locks="app/src/x")
    world.claim("c1", w1)
    with pytest.raises(Denied):
        world.board.claim("c2", w2)
    [(rec, data)] = _events(world.board, "lock_denied")
    assert rec["kind"] == "status" and rec["taskId"] == "c2" and data["agent"] == "w-two"
    assert data["reasons"] and data["paths"]


def test_write_denial_is_recorded_as_lock_violation(world):
    w1, w2 = world.agent("w-one"), world.agent("w-two")
    world.card("c1", locks="app/src")
    world.claim("c1", w1)
    target = world.root / "app" / "src" / "f.py"
    assert world.board.check_write(str(target), w1)["allowed"]
    assert not _events(world.board, "lock_violation")
    assert not world.board.check_write(str(target), w2)["allowed"]
    [(rec, data)] = _events(world.board, "lock_violation")
    assert rec["kind"] == "status" and data["agent"] == "w-two" and data["holder"] == "w-one"
    assert data["path"] == str(target)
