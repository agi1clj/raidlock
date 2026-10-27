import threading

import pytest

from raidlock.core import clock, messages
from raidlock.core.errors import Denied, IdentityError, Precondition, Timeout


@pytest.fixture
def pair(world):
    a, b = world.agent("w-aaa"), world.agent("w-bbb")
    world.board.add_team(world.lead, "core", 5)
    world.card("c-a", locks="app/a")
    world.card("c-b", locks="app/b")
    return world, a, b


def test_ready_when_recipient_idle(pair):
    world, a, b = pair
    m = world.board.send(a, "w-bbb", "hello")
    assert m["status"] == "ready"
    got = world.board.inbox(b)["messages"]
    assert [x["text"] for x in got] == ["hello"]
    assert world.board.inbox(b)["messages"] == []


def test_queued_while_recipient_doing_other_task(pair):
    world, a, b = pair
    world.claim("c-b", b)
    m = world.board.send(a, "w-bbb", "about something else", task="c-a")
    assert m["status"] == "queued"
    r = world.board.inbox(b)
    assert r["messages"] == [] and r["queued"] == 1
    world.board.move("c-b", "review", b)
    assert [x["text"] for x in world.board.inbox(b)["messages"]] == ["about something else"]


def test_message_about_current_doing_card_is_immediate(pair):
    world, a, b = pair
    world.claim("c-b", b)
    m = world.board.send(a, "w-bbb", "answer to your question", task="c-b")
    assert m["status"] == "ready"
    assert [x["text"] for x in world.board.inbox(b)["messages"]] == ["answer to your question"]


def test_untasked_message_queues_while_busy(pair):
    world, a, b = pair
    world.claim("c-b", b)
    assert world.board.send(a, "w-bbb", "fyi")["status"] == "queued"


def test_urgent_is_lead_only_and_immediate(pair):
    world, a, b = pair
    world.claim("c-b", b)
    with pytest.raises(IdentityError):
        world.board.send(a, "w-bbb", "stop", urgent=True)
    assert world.board.send(world.lead, "w-bbb", "stop", urgent=True)["status"] == "ready"


def test_size_cap(pair):
    world, a, _ = pair
    with pytest.raises(Precondition):
        world.board.send(a, "w-bbb", "x" * (32 * 1024 + 1))


def test_repeat_dropped_within_window(pair):
    world, a, b = pair
    first = world.board.send(a, "w-bbb", "same")
    again = world.board.send(a, "w-bbb", "same")
    assert again["dropped"] and again["id"] == first["id"]
    clock.travel(61)
    assert not world.board.send(a, "w-bbb", "same")["dropped"]


def test_rate_limit_per_sender(pair):
    world, a, _ = pair
    for i in range(messages.RATE_LIMIT):
        world.board.send(a, "w-bbb", f"m{i}")
    with pytest.raises(Denied):
        world.board.send(a, "w-bbb", "one too many")
    clock.travel(61)
    world.board.send(a, "w-bbb", "later")


def test_queue_cap(pair, monkeypatch):
    world, a, b = pair
    monkeypatch.setattr(messages, "RATE_LIMIT", 10_000)
    world.claim("c-b", b)
    for i in range(messages.MAX_QUEUED):
        world.board.send(a, "w-bbb", f"q{i}")
    with pytest.raises(Denied):
        world.board.send(a, "w-bbb", "overflow")


def test_unknown_recipient_rejected(pair):
    world, a, _ = pair
    with pytest.raises(Precondition):
        world.board.send(a, "nobody-here", "hi")


def test_multiline_message_allowed_but_controls_rejected(pair):
    world, a, _ = pair
    world.board.send(a, "w-bbb", "line one\nline two")
    with pytest.raises(Precondition):
        world.board.send(a, "w-bbb", "bell\x07")


def test_message_transcribed(pair):
    world, a, _ = pair
    m = world.board.send(a, "w-bbb", "hello", task="c-a")
    rec = world.board.transcript.read(kind="msg")[-1]
    assert rec["messageId"] == m["id"] and rec["to"] == "w-bbb" and rec["contextId"].endswith("/c-a")


def test_wait_timeout(pair):
    world, a, _ = pair
    world.claim("c-a", a)
    with pytest.raises(Timeout) as e:
        world.board.wait("c-a", timeout=0.2, interval=0.05)
    assert e.value.code == 4


def test_wait_returns_when_card_leaves_doing(pair):
    world, a, _ = pair
    world.claim("c-a", a)
    threading.Timer(0.2, lambda: world.board.move("c-a", "review", a)).start()
    assert world.board.wait("c-a", timeout=5, interval=0.05)["lane"] == "review"
