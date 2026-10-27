import os
import sys

from raidlock.core import clock
from raidlock.lore import Vault, capture


def _by_source(vault):
    out = {}
    for x in vault.pending():
        out.setdefault(x["source"], []).append(x)
    return out


def test_captures_each_source(world):
    w, b = world, world.board
    worker, reviewer = w.agent("w-one", provider="openai"), w.agent("r-one", provider="anthropic")
    w.card("auth", locks="app/src/auth")
    w.changes("auth", worker, reviewer, "token refresh races; guard the cache with the session lock")
    b.note("auth", worker, "lesson: run migrations before the auth tests")
    b.note("auth", worker, "ordinary note, not a lesson")
    w.card("api", locks="app/src/api", verify_cmd=f'"{sys.executable}" -c "print(\'boom\'); raise SystemExit(3)"')
    b.verify("api", w.lead)
    w.append("status", "w-one", "claim denied: app/src/api is held by w-two", task="api")
    w.append("status", "w-one", "lease resumed after expiry", task="api")
    w.rejected_handback("api", "w-one", ["docs/README.md"])
    r = capture(b)
    vault = Vault.for_board(b)
    got = _by_source(vault)
    assert r["captured"] == 3
    assert sorted(got) == ["note", "patch", "review"]
    review = got["review"][0]
    assert "guard the cache" in review["text"]
    assert review["card"] == "auth" and review["agent"] == "r-one" and review["provider"] == "anthropic"
    assert review["paths"] == ["app/src/auth"] and review["repo"] == "app"
    assert got["note"][0]["text"] == "run migrations before the auth tests"
    assert "app/docs/README.md" in got["patch"][0]["paths"]


def test_capture_is_idempotent_and_uses_cursor(world):
    w, b = world, world.board
    worker, reviewer = w.agent("w-one"), w.agent("r-one")
    w.card("auth", locks="app/src/auth")
    b.note("auth", w.lead, "lesson: one")
    first = capture(b)
    assert first["captured"] == 1
    assert capture(b)["captured"] == 0
    os.remove(os.path.join(b.dir, "lore", ".state.json"))
    assert capture(b)["captured"] == 0
    w.changes("auth", worker, reviewer, "second")
    assert capture(b)["captured"] == 1
    assert len(Vault.for_board(b).pending()) == 2
    assert Vault.for_board(b).lessons() == []


def test_approve_and_passing_verify_are_not_lessons(world):
    w, b = world, world.board
    worker, reviewer = w.agent("w-one"), w.agent("r-one")
    w.card("ok", verify_cmd="true")
    w.claim("ok", worker)
    b.move("ok", "review", worker, note="ready")
    b.review("ok", reviewer, "approve", note="fine")
    b.verify("ok", w.lead)
    assert capture(b)["captured"] == 0


def test_verify_that_cannot_start_its_command_is_not_a_lesson(world):
    w, b = world, world.board
    w.card("missing", verify_cmd="raidlock-no-such-command-xyz")
    b.verify("missing", w.lead)
    w.card("spawn", verify_cmd=f'"{sys.executable}" -c "print(\'Failed to spawn: pytest\'); raise SystemExit(2)"')
    b.verify("spawn", w.lead)
    assert capture(b)["captured"] == 0


def test_global_vault_gets_a_copy(world):
    w, b = world, world.board
    w.card("auth", locks="app/src/auth")
    b.note("auth", w.lead, "lesson: global too")
    capture(b)
    g = Vault.global_(b.home)
    assert g.lessons() == []
    w.approve_all()
    assert [x["text"] for x in g.lessons()] == ["global too"]
    assert os.path.isdir(os.path.join(os.environ["RAIDLOCK_HOME"], "lore", "hubs"))


def test_secrets_are_redacted(world):
    w, b = world, world.board
    w.card("auth", locks="app/src/auth")
    w.append("note", w.board.lead, "lesson: never commit AKIAABCDEFGHIJKLMNOP", task="auth")
    capture(b)
    text = Vault.for_board(b).pending()[0]["text"]
    assert "AKIA" not in text and "[REDACTED]" in text


def test_created_uses_record_time(world):
    w, b = world, world.board
    w.card("auth")
    clock.travel(60)
    b.note("auth", w.lead, "lesson: later")
    ts = b.transcript.read(kind="note")[-1]["ts"]
    clock.travel(3600)
    capture(b)
    assert Vault.for_board(b).pending()[0]["created"] == ts
