import json
import sys
import threading
from types import ModuleType, SimpleNamespace

from raidlock import limits
from raidlock.web import data


def test_workspace_cache_keeps_the_version_sampled_before_rendering():
    version = ["v0"]
    builds = []

    def build(board):
        result = {"rendered": version[0]}
        builds.append(result)
        version[0] = "v1"
        return result

    workspace = SimpleNamespace(
        lock=threading.Lock(), key=None, payload=None, _key=lambda: version[0], board=lambda: None, _build=build
    )
    assert data.Workspace.refresh(workspace) == ("v0", {"rendered": "v0"})
    assert data.Workspace.refresh(workspace) == ("v1", {"rendered": "v1"})
    assert data.Workspace.refresh(workspace) == ("v1", {"rendered": "v1"})
    assert len(builds) == 2


def test_scorecard_reputation_matches_the_public_scorer_per_workspace(server):
    from raidlock.reputation import reputation

    workspace = server.srv.catalog.get("acme-shop")
    workspace.key = None
    status, _, raw = server.get("/api/board/acme-shop")
    assert status == 200
    rows = json.loads(raw)["model_reputation"]
    expected = {row["key"]: row for row in reputation(workspace.board(), by="model")["rows"]}
    assert rows and {row["key"] for row in rows} == set(expected)
    for row in rows:
        assert row["workspace"] == "acme-shop"
        assert row["score"] == expected[row["key"]]["score"]
        assert row["inputs"] == expected[row["key"]]["inputs"]
        assert row["contributions"] == expected[row["key"]]["contributions"]
    overview = json.loads(server.get("/api/overview")[2])
    assert [row for row in overview["model_reputation"] if row["workspace"] == "acme-shop"] == rows
    exported = server.get("/api/snapshot/acme-shop")[2]
    assert b"Model trust scores" in exported and b"verify_passed" in exported


def test_overview_does_not_tag_old_content_with_a_new_version(server, monkeypatch):
    catalog = server.srv.catalog
    version = ['"v0"']

    def build():
        result = {"rendered": version[0]}
        version[0] = '"v1"'
        return result

    monkeypatch.setattr(catalog, "overview_key", lambda: version[0])
    monkeypatch.setattr(catalog, "overview", build)
    status, headers, body = server.get("/api/overview")
    assert status == 200 and headers["ETag"] == '"v0"'
    assert json.loads(body)["rendered"] == '"v0"'
    status, newer, body = server.get("/api/overview", headers={"If-None-Match": headers["ETag"]})
    assert status == 200 and newer["ETag"] == '"v1"'
    assert json.loads(body)["rendered"] == '"v1"'
    assert server.get("/api/overview", headers={"If-None-Match": newer["ETag"]})[0] == 304


def usage(percent=75):
    return {
        "providers": [
            {
                "provider": "codex",
                "plan": "prolite",
                "status": "ok",
                "windows": [{"window": "weekly", "used_percent": percent, "resets_at": "2026-10-23T14:00:00Z"}],
            }
        ]
    }


def test_limits_refresh_independently_of_board_version(server, monkeypatch):
    catalog = server.srv.catalog
    current = usage()
    calls = []
    monkeypatch.setattr(limits, "collect", lambda: calls.append(1) or current)
    catalog._limits_at = float("-inf")
    status, headers, raw = server.get("/api/overview")
    assert status == 200 and json.loads(raw)["limits"]["providers"][0]["windows"][0]["used_percent"] == 75
    assert server.get("/api/overview", headers={"If-None-Match": headers["ETag"]})[0] == 304
    assert len(calls) == 1
    current = usage(95)
    catalog._limits_at = float("-inf")
    status, updated, raw = server.get("/api/overview", headers={"If-None-Match": headers["ETag"]})
    assert status == 200 and updated["ETag"] != headers["ETag"]
    assert json.loads(raw)["limits"]["providers"][0]["windows"][0]["used_percent"] == 95
    catalog._limits_at = float("-inf")


def test_optional_reputation_is_used_without_a_required_dependency(server, monkeypatch):
    module = ModuleType("raidlock.reputation")
    module.reputation = lambda board, by: {
        "rows": [{"key": "opus-checkout", "score": 75, "inputs": {"verify_passed": 1}}]
    }
    monkeypatch.setitem(sys.modules, "raidlock.reputation", module)
    ws = server.srv.catalog.get("acme-shop")
    ws.key = None
    payload = json.loads(server.get("/api/board/acme-shop")[2])
    assert payload["agents"]["opus-checkout"]["reputation"] == 75
    assert payload["agents"]["opus-checkout"]["reputation_inputs"]["verify_passed"] == 1
    monkeypatch.setitem(sys.modules, "raidlock.reputation", None)
    ws.key = None
    payload = json.loads(server.get("/api/board/acme-shop")[2])
    assert payload["agents"]["opus-checkout"]["reputation"] is None
    ws.key = None


def test_nested_credentials_are_removed_from_public_data():
    payload = {
        "agents": {
            "worker": {
                "token": "private",
                "cap": "private-cap",
                "lead_secret": "private-lead",
                "RAIDLOCK_CAP": "scoped-secret",
            }
        },
        "message": "api_key=sk-ant-abcdefghijklmnopqrs RAIDLOCK_CAP=task:1:private-signature",
        "scorecard": [],
    }
    clean = data.public(payload)
    assert clean["agents"]["worker"] == {}
    assert "sk-ant-" not in clean["message"]
    assert "private-signature" not in clean["message"]


def test_snapshot_contains_limits_and_no_active_content(server, monkeypatch):
    catalog = server.srv.catalog
    monkeypatch.setattr(limits, "collect", lambda: usage())
    catalog._limits_at = float("-inf")
    status, headers, raw = server.get("/api/snapshot/acme-shop")
    assert status == 200 and headers["Content-Disposition"].startswith("attachment;")
    assert b"Provider usage" in raw and b"weekly 75%" in raw
    assert b"unavailable" not in raw and b"unknown" not in raw
    assert b"<script" not in raw and b"<img src=x" not in raw
    catalog._limits_at = float("-inf")


def test_web_package_exposes_installed_entrypoint():
    import raidlock.web
    from raidlock.web.cli import register_cli

    assert raidlock.web.register_cli is register_cli


def test_ui_started_inside_a_board_opens_that_board(tmp_path):
    from types import SimpleNamespace

    from raidlock.web.cli import start_path

    here, other = tmp_path / "my shop" / ".board", tmp_path / "other" / ".board"
    catalog = SimpleNamespace(entries=lambda: [("my shop", str(here)), ("other", str(other))])
    assert start_path(catalog, str(here), None) == "my%20shop"
    assert start_path(catalog, None, None) == ""
    assert start_path(catalog, str(here), str(here)) == ""


def test_duplicate_host_headers_are_denied(server):
    assert server.get("/api/overview", headers={"Host": "evil.example"})[0] == 403


def test_invalid_quota_payload_does_not_break_the_overview(server, monkeypatch):
    monkeypatch.setattr(limits, "collect", lambda: {"providers": [{"used_percent": float("nan")}]})
    server.srv.catalog._limits_at = float("-inf")
    status, _, raw = server.get("/api/overview")
    assert status == 200 and json.loads(raw)["limits"] == {"providers": []}
    server.srv.catalog._limits_at = float("-inf")
