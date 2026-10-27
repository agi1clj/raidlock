import http.client
import os
import threading

import pytest

from raidlock.core import clock
from raidlock.web.server import make_server

CLEAR = (
    "RAIDLOCK_BOARD",
    "BOARD_DIR",
    "RAIDLOCK_AGENT",
    "RAIDLOCK_AGENT_ID",
    "RAIDLOCK_TOKEN",
    "RAIDLOCK_FENCE",
    "RAIDLOCK_ROLE",
    "RAIDLOCK_REQUIRE_TOKEN",
    "RAIDLOCK_MODEL",
    "RAIDLOCK_SESSION",
    "RAIDLOCK_RUNTIME",
    "RAIDLOCK_BILLING",
    "ANTHROPIC_MODEL",
    "GEMINI_MODEL",
    "BOARD_PROJECT",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "RAIDLOCK_CAP",
    "RAIDLOCK_LEAD_SECRET",
    "RAIDLOCK_LEAD_SECRET_FILE",
    "RAIDLOCK_LEAD_PID",
)


def isolate_env(mp, root):
    for key in CLEAR:
        mp.delenv(key, raising=False)
    mp.setenv("RAIDLOCK_HOME", str(root / "home"))
    mp.setenv("TEAM_BOARD_HOME", str(root / "legacy-home"))
    mp.setenv("CODEX_HOME", str(root / "codex"))
    mp.setenv("CLAUDE_CONFIG_DIR", str(root / "claude"))
    mp.setenv("GEMINI_CONFIG_DIR", str(root / "gemini"))
    mp.chdir(root)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    isolate_env(monkeypatch, tmp_path)
    clock.reset()
    from raidlock.core import lead

    table = lead.process_table()
    monkeypatch.setattr(lead, "process_table", lambda: table)
    yield
    clock.reset()


class Running:
    def __init__(self, srv):
        self.srv = srv
        self.port = srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def get(self, path, host=None, method="GET", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=600)
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host is not False:
            conn.putheader("Host", f"127.0.0.1:{self.port}" if host is None else host)
        for k, v in (headers or {}).items():
            conn.putheader(k, v)
        conn.endheaders()
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, dict(r.getheaders()), body


def start(**kw):
    srv = make_server(port=0, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return Running(srv)


@pytest.fixture(scope="module")
def demo_server(tmp_path_factory):
    import demo

    root = tmp_path_factory.mktemp("web")
    mp = pytest.MonkeyPatch()
    isolate_env(mp, root)
    demos = demo.build(root / "ws", showcase=os.environ.get("RAIDLOCK_SHOTS") == "1")
    running = start()
    running.demos = demos
    yield running
    running.srv.shutdown()
    running.srv.server_close()
    mp.undo()


@pytest.fixture
def server(demo_server):
    return demo_server
