import os
import time

from serving import start

from raidlock.core import Board, db


def open_fds():
    folder = "/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"
    return len(os.listdir(folder))


def settle(limit, seconds=5.0):
    deadline = time.monotonic() + seconds
    count = open_fds()
    while count > limit and time.monotonic() < deadline:
        time.sleep(0.05)
        count = open_fds()
    return count


def test_thousand_ui_requests_keep_the_fd_count_flat(tmp_path):
    Board.init(tmp_path / "fds")
    db.close_all()
    running = start()
    try:
        paths = ("/api/overview", "/api/board/fds", "/api/transcript/fds", "/")
        for path in paths * 5:
            assert running.get(path)[0] == 200
        before = settle(0, 0.5)
        for i in range(1000):
            assert running.get(paths[i % len(paths)])[0] == 200
        after = settle(before + 2)
    finally:
        running.srv.shutdown()
        running.srv.server_close()
    assert after <= before + 2, (before, after)


def test_request_threads_close_their_index_connections(tmp_path):
    Board.init(tmp_path / "threads")
    seen = []
    running = start()
    original = running.srv.finish_request

    def finish(request, address):
        original(request, address)
        seen.append(db.open_count())

    running.srv.finish_request = finish
    try:
        for _ in range(5):
            assert running.get("/api/board/threads")[0] == 200
        deadline = time.monotonic() + 5
        while len(seen) < 5 and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        running.srv.shutdown()
        running.srv.server_close()
    assert seen == [0] * 5
