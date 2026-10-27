import json
import re

import pytest
from serving import start


def body(resp):
    return json.loads(resp[2])


@pytest.mark.parametrize("host", ["127.0.0.1:{p}", "localhost:{p}", "[::1]:{p}"])
def test_allowed_hosts(server, host):
    status, headers, _ = server.get("/", host=host.format(p=server.port))
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1", "localhost:1", "127.0.0.1:{p}.evil.example", "", False])
def test_other_hosts_are_forbidden(server, host):
    h = host.format(p=server.port) if isinstance(host, str) else host
    for path in ("/", "/api/overview", "/api/board/acme-shop", "/static/app.js"):
        status, headers, raw = server.get(path, host=h)
        assert status == 403, (path, h)
        assert b"acme" not in raw


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE"])
def test_only_get(server, method):
    status, headers, _ = server.get("/api/overview", method=method)
    assert status == 405
    assert headers.get("Allow") == "GET"


@pytest.mark.parametrize(
    "path",
    [
        "/server.py",
        "/static/server.py",
        "/data.py",
        "/static/../server.py",
        "/../pyproject.toml",
        "/%2e%2e/server.py",
        "/static/",
        "/static/index.html",
        "/a/b",
        "/index.js",
        "/.board",
        "/acme-shop/cards",
        "/api/nope",
        "/api/board/../../etc",
        "/api/board/missing-ws",
    ],
)
def test_static_allowlist(server, path):
    status, _, raw = server.get(path)
    assert status == 404, path
    assert b"import " not in raw


def test_static_assets_and_routes(server):
    for path, ctype in (
        ("/static/app.js", "text/javascript"),
        ("/static/app.css", "text/css"),
        ("/static/icon.svg", "image/svg+xml"),
        ("/static/icon-32.png", "image/png"),
        ("/acme-shop", "text/html"),
        ("/acme%20shop", "text/html"),
        ("/my.app", "text/html"),
    ):
        status, headers, _ = server.get(path)
        assert status == 200, path
        assert headers["Content-Type"].startswith(ctype)


def test_brand_favicons_and_header_lockup(server):
    status, _, body = server.get("/static/icon-32.png")
    assert status == 200
    assert body.startswith(b"\x89PNG\r\n\x1a\n")
    status, _, svg = server.get("/static/icon.svg")
    assert status == 200 and b"<text" not in svg
    status, _, html = server.get("/")
    assert b'href="/static/icon-raid.svg"' in html
    assert b'href="/static/icon-32.png"' in html
    assert b'class="lockup"' in html
    assert b'<span class="sr-only">raidlock</span>' in html


def test_security_headers_and_no_cors(server):
    for path in ("/", "/api/overview", "/static/app.js", "/api/board/acme-shop"):
        status, headers, _ = server.get(path, headers={"Origin": "http://evil.example"})
        assert status == 200
        csp = headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "default-src 'none'" in csp
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert "require-trusted-types-for 'script'" in csp and "data:" not in csp
        assert "microphone=()" in headers["Permissions-Policy"] and "autoplay" not in headers["Permissions-Policy"]
        assert not any(k.lower().startswith("access-control-") for k in headers)


def test_static_assets_revalidate_with_etags(server):
    for path in ("/", "/static/app.js", "/static/app.css"):
        status, headers, body = server.get(path)
        assert status == 200 and body and headers["ETag"]
        status, again, body = server.get(path, headers={"If-None-Match": headers["ETag"]})
        assert status == 304 and body == b"" and again["ETag"] == headers["ETag"]


def test_index_has_no_inline_script_or_style(server):
    _, _, raw = server.get("/")
    html = raw.decode()
    assert re.search(r"<script(?![^>]*\bsrc=)", html) is None
    assert "<style" not in html and " style=" not in html
    assert re.search(r"\son[a-z]+=", html) is None


def test_app_never_uses_inner_html():
    from pathlib import Path

    import raidlock.web

    js = (Path(raidlock.web.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "outerHTML" not in js and "insertAdjacentHTML" not in js
    assert "document.write" not in js and "eval(" not in js and "new Function" not in js


def test_overview_contract(server):
    status, headers, raw = server.get("/api/overview")
    assert status == 200 and headers["ETag"]
    d = json.loads(raw)
    assert d["mode"] == "all"
    names = [p["name"] for p in d["projects"]]
    assert names == ["acme-shop", "docs-site", "pocket-notes", "weather-api"]
    acme = d["projects"][0]
    assert set(acme) >= {"name", "counts", "claimable", "open", "cost", "agents", "last", "chat"}
    assert acme["counts"]["done"] == 2
    a = {x["name"]: x for x in acme["agents"]}
    assert a["opus-checkout"]["display"] == "opus-checkout · Claude Opus 5.5 · claude-code"
    assert a["opus-checkout"]["billing"] == "plan" and a["codex-payments"]["billing"] == "api"
    assert d["treasury"]["billing"]["plan"]["usd"] == 0 and d["treasury"]["billing"]["plan"]["usd_equiv"] > 0
    assert {r["key"] for r in d["treasury"]["by_workspace"]} >= {"acme-shop", "pocket-notes"}
    assert d["scorecard"]


def test_board_contract(server):
    status, headers, raw = server.get("/api/board/acme-shop")
    assert status == 200
    d = json.loads(raw)
    for k in (
        "workspace",
        "lead",
        "cards",
        "teams",
        "agents",
        "claimable",
        "review_queue",
        "inbox",
        "messages",
        "costs",
        "scorecard",
        "treasury",
        "cursor",
        "counts",
        "model_names",
    ):
        assert k in d, k
    assert not any(k.startswith("_") for k in d)
    cards = {c["id"]: c for c in d["cards"]}
    assert cards["pay-retry"]["lane"] == "done"
    assert cards["cart-api"]["lease"]["live"] is True
    assert cards["promo-codes"]["id"] in d["claimable"] and not d["claimable"]["promo-codes"]["ok"]
    assert d["claimable"]["promo-codes"]["reasons"]
    score = {(g["provider"], g["model"]): g for g in d["scorecard"]}
    gpt = score[("openai", "gpt-6-astra")]
    assert gpt["done"] == 1 and gpt["approve"] == 1 and gpt["changes"] == 1 and gpt["pass_rate"] == 0.5
    t = d["treasury"]
    for by in ("by_provider", "by_model", "by_agent", "by_workspace", "by_day"):
        assert isinstance(t[by], list)
    assert {r["key"] for r in t["by_agent"]} >= {"opus-checkout", "codex-payments"}
    assert {r["label"] for r in t["by_model"]} >= {"Claude Opus 5.5", "GPT-6 Astra"}


def test_no_absolute_paths_or_tokens_leak(server):
    for path in (
        "/api/overview",
        "/api/board/acme-shop",
        "/api/card/acme-shop/cart-api",
        "/api/transcript/acme-shop",
        "/api/snapshot/acme-shop",
    ):
        status, _, raw = server.get(path)
        assert status == 200, path
        text = raw.decode()
        assert str(server.srv.catalog.home) not in text
        assert "/private/" not in text and "/Users/" not in text and "/home/" not in text and "/tmp/" not in text, path
        assert "RAIDLOCK_TOKEN" not in text and '"fence"' not in text.replace('"fence": null', "")


def test_etag_304(server):
    status, headers, _ = server.get("/api/board/acme-shop")
    tag = headers["ETag"]
    status, headers2, raw = server.get("/api/board/acme-shop", headers={"If-None-Match": tag})
    assert status == 304 and raw == b"" and headers2["ETag"] == tag
    status, headers, _ = server.get("/api/overview")
    assert server.get("/api/overview", headers={"If-None-Match": headers["ETag"]})[0] == 304
    assert server.get("/api/board/acme-shop", headers={"If-None-Match": '"stale"'})[0] == 200


def test_etag_changes_after_a_write(server):
    _, headers, _ = server.get("/api/board/weather-api")
    tag = headers["ETag"]
    assert server.get("/api/board/weather-api", headers={"If-None-Match": tag})[0] == 304
    server.demos["weather-api"].note("cache-ttl", "gemini-cache", "hit rate 92 percent")
    status, headers2, raw = server.get("/api/board/weather-api", headers={"If-None-Match": tag})
    assert status == 200 and headers2["ETag"] != tag
    cards = {c["id"]: c for c in json.loads(raw)["cards"]}
    assert cards["cache-ttl"]["notes"][-1]["text"] == "hit rate 92 percent"


def test_transcript_since_cursor(server):
    d = body(server.get("/api/transcript/acme-shop?limit=5"))
    assert len(d["records"]) == 5
    last = d["cursor"]
    assert last == d["records"][-1]["seq"]
    first = body(server.get("/api/transcript/acme-shop?since=1&limit=3"))
    assert [r["seq"] for r in first["records"]] == [2, 3, 4]
    assert first["cursor"] == 4 and first["more"] is True
    tail = body(server.get(f"/api/transcript/acme-shop?since={last}"))
    assert tail["records"] == [] and tail["cursor"] == last
    task = body(server.get("/api/transcript/acme-shop?task=pay-retry"))
    assert task["records"] and all(r["task"] == "pay-retry" for r in task["records"])
    r = task["records"][0]
    assert set(r["from"]) == {"agent", "provider", "runtime", "model"}
    assert server.get("/api/transcript/acme-shop?since=abc")[0] == 400


def test_card_detail(server):
    d = body(server.get("/api/card/acme-shop/pay-retry"))
    assert [r["verdict"] for r in d["reviews"]] == ["changes", "approve"]
    assert d["log"] and "fence" not in d and "verify_cmd" not in d
    assert server.get("/api/card/acme-shop/nope")[0] == 404


def test_xss_payload_is_stored_as_text(server):
    import demo

    d = body(server.get("/api/card/acme-shop/cart-api"))
    assert any(n["text"] == demo.XSS for n in d["notes"])
    _, headers, raw = server.get("/api/snapshot/acme-shop")
    html = raw.decode()
    assert "<img src=x" not in html and "<script>window" not in html
    assert "&lt;img src=x" in html
    assert headers["Content-Disposition"].startswith("attachment;")


def test_single_board_mode(tmp_path):
    import demo

    d = demo.weather_api(tmp_path / "solo")
    running = start(single=d.board.dir)
    try:
        o = json.loads(running.get("/api/overview")[2])
        assert o["mode"] == "single" and [p["name"] for p in o["projects"]] == ["weather-api"]
    finally:
        running.srv.shutdown()
        running.srv.server_close()


def test_unreadable_board_is_reported_not_fatal(tmp_path):
    import demo

    d = demo.weather_api(tmp_path / "broken")
    running = start()
    try:
        (d.root / ".board" / "board.json").write_text("{not json", encoding="utf-8")
        status, _, raw = running.get("/api/overview")
        assert status == 200
        p = json.loads(raw)["projects"]
        assert p and p[0]["missing"] is True
    finally:
        running.srv.shutdown()
        running.srv.server_close()


def test_cli_registers_ui():
    from raidlock import cli

    cli.load_extensions()
    assert "ui" in cli.COMMANDS
    assert "ui" in cli.everyday_help()


def test_scale_overview_and_conditional_polls(tmp_path):
    import os
    import time

    import demo

    full = os.environ.get("RAIDLOCK_WEB_SCALE") == "1"
    boards, cards = (50, 1000) if full else (3, 300)
    from raidlock.core import Board

    made = [demo.bulk_board(tmp_path / f"w{i}", f"scale-{i:02d}", cards, agents=4) for i in range(boards)]
    t0 = time.monotonic()
    for d in made:
        Board.open(d.board.dir).cards()
    mirror = time.monotonic() - t0
    running = start()
    try:
        t0 = time.monotonic()
        status, headers, raw = running.get("/api/overview")
        cold = time.monotonic() - t0
        assert status == 200
        assert len(json.loads(raw)["projects"]) == boards
        t0 = time.monotonic()
        for _ in range(10):
            assert running.get("/api/overview", headers={"If-None-Match": headers["ETag"]})[0] == 304
        warm = (time.monotonic() - t0) / 10
        status, h2, raw = running.get("/api/board/scale-00")
        assert status == 200 and len(json.loads(raw)["cards"]) == cards
        t0 = time.monotonic()
        assert running.get("/api/board/scale-00", headers={"If-None-Match": h2["ETag"]})[0] == 304
        board_304 = time.monotonic() - t0
        print(
            f"scale {boards}x{cards}: core first open {mirror:.2f}s, cold overview {cold:.2f}s, "
            f"304 overview {warm * 1000:.0f}ms, "
            f"304 board {board_304 * 1000:.0f}ms"
        )
        assert warm < (1.0 if full else 0.5)
        assert board_304 < 0.5
    finally:
        running.srv.shutdown()
        running.srv.server_close()


def test_ui_does_not_register_boards(tmp_path):
    import demo

    from raidlock.core import registry
    from raidlock.core.db import Index

    d = demo.weather_api(tmp_path / "loose")
    home = registry.home()
    registry.remove(Index(home), ["weather-api"])
    assert not registry.workspaces(Index(home))
    running = start(extra=d.board.dir)
    try:
        o = json.loads(running.get("/api/overview")[2])
        assert [p["name"] for p in o["projects"]] == ["weather-api"]
        assert running.get("/api/board/weather-api")[0] == 200
        assert not registry.workspaces(Index(home))
    finally:
        running.srv.shutdown()
        running.srv.server_close()


def test_delegated_run_paths_scrubbed(tmp_path):
    import os
    import shlex
    import sys
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    import demo

    from raidlock.runners import workspace
    from raidlock.runners.runner import delegate
    from raidlock.web import data
    from raidlock.web.server import Handler

    project = demo.acme_shop(tmp_path / "delegated")
    board = project.board
    repo = str(project.root / project.repo)
    workspace.git(repo, "init", "--quiet")
    Path(repo, "README.md").write_text("fixture")
    workspace.git(repo, "add", "README.md")
    workspace.git(repo, *workspace.IDENT, "commit", "--quiet", "-m", "fixture")
    project.card("delegated", "delegated", "checkout", "delegated")
    command = shlex.join(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import json; Path('delegated').write_text('change'); "
            "print(json.dumps({'status': 'done', 'evidence': ['wrote delegated'], "
            "'paths_changed': ['delegated'], 'open_questions': []}))",
        ]
    )
    result = delegate(board, project.lead, "delegated", "shell", cmd=command, heartbeat=0.05)
    assert result["lane"] == "review"
    handler = object.__new__(Handler)
    handler.server = SimpleNamespace(catalog=data.Catalog(home=board.home))
    handler.headers = {}
    responses = []
    handler._send = lambda code, body, *args: responses.append((code, body))
    folder = os.path.join(board.home, "runs", board.name, "delegated", "1")
    clone = os.path.join(folder, "repo")
    patch = os.path.join(folder, "changes.patch")
    with board.lock():
        board._record(
            "artifact",
            "raid-lead",
            f"delegated run clone {clone}; patch {patch}",
            task="delegated",
            data={
                "clone": clone,
                "patch": patch,
                "home": os.path.expanduser("~") + "/secret",
                "temp": tempfile.gettempdir() + "/runner",
            },
        )
    for path in (
        "/api/overview",
        "/api/board/acme-shop",
        "/api/card/acme-shop/delegated",
        "/api/transcript/acme-shop",
        "/api/snapshot/acme-shop",
    ):
        handler._api(path.removeprefix("/api/"), {})
        status, raw = responses.pop()
        assert status == 200
        text = raw.decode()
        assert board.home not in text
        assert os.path.expanduser("~") + "/secret" not in text
        assert tempfile.gettempdir() + "/runner" not in text
        if "transcript" in path or "snapshot" in path:
            assert "~raid/runs/acme-shop/delegated" in text
