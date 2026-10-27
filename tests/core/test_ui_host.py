import http.client
import threading

import pytest
from corehelpers import run_cli

from raidlock.core.errors import Precondition
from raidlock.web.cli import check_host
from raidlock.web.server import make_server


def test_loopback_is_always_allowed():
    assert check_host("127.0.0.1", env={}) == "127.0.0.1"


@pytest.mark.parametrize("env", [{}, {"RAIDLOCK_SANDBOX": "off"}, {"RAIDLOCK_SANDBOX": "Container"}])
def test_all_interfaces_needs_the_container_image(env):
    with pytest.raises(Precondition, match="only inside the raidlock container"):
        check_host("0.0.0.0", env=env)


def test_all_interfaces_inside_the_container_image():
    assert check_host("0.0.0.0", env={"RAIDLOCK_SANDBOX": "container"}) == "0.0.0.0"


def test_cli_refuses_all_interfaces_outside_the_container(tmp_path):
    env = {"RAIDLOCK_HOME": str(tmp_path / "home"), "RAIDLOCK_SANDBOX": ""}
    r = run_cli("ui", "--host", "0.0.0.0", "--port", "0", env=env, cwd=str(tmp_path), timeout=30)
    assert r.returncode == 3
    assert "only inside the raidlock container" in r.stdout + r.stderr


def test_cli_rejects_other_bind_addresses(tmp_path):
    env = {"RAIDLOCK_HOME": str(tmp_path / "home"), "RAIDLOCK_SANDBOX": "container"}
    r = run_cli("ui", "--host", "192.168.1.5", "--port", "0", env=env, cwd=str(tmp_path), timeout=30)
    assert r.returncode != 0
    assert "invalid choice" in r.stderr


def _get(port, host_header):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest("GET", "/", skip_host=True)
        conn.putheader("Host", host_header)
        conn.endheaders()
        return conn.getresponse().status
    finally:
        conn.close()


def test_all_interfaces_server_keeps_the_host_allowlist(tmp_path):
    srv = make_server(port=0, home=str(tmp_path / "home"), host="0.0.0.0")
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        assert srv.server_address[0] == "0.0.0.0"
        assert _get(port, f"127.0.0.1:{port}") == 200
        assert _get(port, f"localhost:{port}") == 200
        assert _get(port, f"evil.example:{port}") == 403
        assert _get(port, f"0.0.0.0:{port}") == 403
        assert _get(port, f"172.17.0.2:{port}") == 403
        assert _get(port, f"127.0.0.1:{port + 1}") == 403
    finally:
        srv.shutdown()
        srv.server_close()
