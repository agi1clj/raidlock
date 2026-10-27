import json

import pytest

from raidlock import cli, limits, routing
from raidlock.core.errors import IdentityError, Precondition
from raidlock.core.hints import lead_secret_src


def window(minutes, used):
    return {"window_minutes": minutes, "used_percent": used, "resets_in_seconds": 600}


def quota(reached=None, **used):
    providers = []
    for name, (five, weekly) in used.items():
        providers.append(
            {
                "provider": name,
                "status": "ok",
                "windows": [window(300, five), window(10080, weekly)],
                "limit_reached": reached if name == "codex" else None,
            }
        )
    return {"providers": providers}


def rules(ws, value):
    routing.set_config(ws.board, ws.lead, "rules", value)


def test_next_is_read_only_and_never_claims(ws):
    ws.card("api")
    before_card, before_leases, before_version = ws.board.card("api"), ws.board.leases(), ws.board.version()
    r = routing.suggest(ws.board, ws.lead, collected=quota(claude=(10, 10), codex=(50, 20)))
    assert r["card"] == "api"
    assert r["provider"] == "claude"
    assert r["command"] == f"raidlock --lead-secret {lead_secret_src(ws.board)} delegate api --to claude"
    assert ws.board.card("api") == before_card
    assert ws.board.leases() == before_leases
    assert ws.board.version() == before_version


def test_most_five_hour_headroom_wins_without_rules(ws):
    ws.card("api")
    r = routing.suggest(ws.board, ws.lead, collected=quota(claude=(80, 10), codex=(5, 20)))
    assert r["provider"] == "codex"


@pytest.mark.parametrize("weekly", [90, 99.5, 100])
def test_weekly_at_or_over_ninety_percent_is_refused(ws, weekly):
    ws.card("api")
    with pytest.raises(Precondition, match="weekly"):
        routing.suggest(ws.board, ws.lead, provider="claude", collected=quota(claude=(0, weekly)))


def test_unknown_or_stale_headroom_is_refused(ws):
    ws.card("api")
    with pytest.raises(Precondition, match="unknown"):
        routing.suggest(ws.board, ws.lead, provider="codex", collected={"providers": []})
    stale = quota(codex=(1, 1))
    stale["providers"][0]["status"] = "stale"
    with pytest.raises(Precondition, match="unknown"):
        routing.suggest(ws.board, ws.lead, provider="codex", collected=stale)
    with pytest.raises(Precondition, match="no card to route"):
        routing.suggest(ws.board, ws.lead, collected={"providers": []})


def test_exhausted_quota_falls_back_to_allowed_provider_only(ws):
    ws.card("docs-page", locks="app/README.md")
    rules(
        ws, [{"name": "docs", "path": "app/README.md", "provider": "codex", "model": "small-1", "fallback": ["claude"]}]
    )
    r = routing.suggest(ws.board, ws.lead, collected=quota(reached="primary", codex=(100, 50), claude=(10, 10)))
    assert r["provider"] == "claude"
    assert r["fallback"] is True
    assert r["model"] == ""
    assert r["checked"][0]["exhausted"] is True
    rules(ws, [{"name": "docs", "path": "app/README.md", "provider": "codex"}])
    with pytest.raises(Precondition, match="exhausted"):
        routing.suggest(ws.board, ws.lead, collected=quota(reached="primary", codex=(100, 50), claude=(10, 10)))


def test_labels_pick_cheaper_and_stronger_models(ws):
    ws.card("docs")
    ws.card("auth", locks="app/README.md")
    rules(
        ws,
        [
            {"label": "docs", "provider": "codex", "model": "cheap-1"},
            {"label": "security", "provider": "claude", "model": "strong-1"},
        ],
    )
    routing.set_config(ws.board, ws.lead, "label.docs", ["docs"])
    routing.set_config(ws.board, ws.lead, "label.auth", ["security"])
    room = quota(claude=(10, 10), codex=(10, 10))
    assert routing.route(ws.board, ws.board.card("docs"), collected=room)["model"] == "cheap-1"
    strong = routing.route(ws.board, ws.board.card("auth"), collected=room)
    assert (strong["provider"], strong["model"]) == ("claude", "strong-1")


def test_routing_never_suggests_a_gated_card(ws):
    ws.card("base")
    ws.card("after", locks="app/README.md", depends="base")
    ws.board.claim("base", ws.lead)
    with pytest.raises(Precondition, match="no card to route"):
        routing.suggest(ws.board, ws.lead, collected=quota(claude=(1, 1)))
    ws.card("held", locks="app/src/main.py")
    with pytest.raises(Precondition, match="no card to route"):
        routing.suggest(ws.board, ws.lead, collected=quota(claude=(1, 1)))


def test_worker_routes_on_its_own_provider_and_team(ws):
    ws.card("api")
    ws.board.add_team(ws.lead, "other", 2)
    ws.board.new_card(ws.lead, "elsewhere", card_id="elsewhere", team="other", locks="app/README.md")
    joined = ws.board.join(ws.lead, agent="codex-one", team="core", runtime="codex-cli")
    worker = ws.board.caller(agent="codex-one", token=joined["token"])
    with pytest.raises(Precondition, match="codex"):
        routing.suggest(ws.board, worker, collected=quota(codex=(1, 95)))
    r = routing.suggest(ws.board, worker, collected=quota(codex=(1, 5)))
    assert (r["card"], r["provider"], r["command"]) == ("api", "codex", "raidlock claim api")
    rules(ws, [{"team": "core", "provider": "claude"}])
    with pytest.raises(Precondition, match="routes it elsewhere"):
        routing.suggest(ws.board, worker, collected=quota(codex=(1, 5)))


def test_review_is_routed_to_another_provider(ws):
    ws.card("api")
    joined = ws.board.join(ws.lead, agent="claude-one", team="core", runtime="claude-code")
    grant = ws.board.claim("api", ws.board.caller(agent="claude-one", token=joined["token"]))
    owner = ws.board.caller(agent="claude-one", token=joined["token"], fence=grant["fence"])
    ws.board.move("api", "review", owner)
    r = routing.suggest(ws.board, ws.lead, for_review=True, collected=quota(claude=(0, 0), codex=(40, 40)))
    assert r["provider"] == "codex"
    assert r["command"] == f"raidlock --lead-secret {lead_secret_src(ws.board)} delegate api --to codex --as reviewer"
    with pytest.raises(Precondition, match="other than claude"):
        routing.suggest(ws.board, ws.lead, provider="claude", for_review=True, collected=quota(claude=(0, 0)))


def test_only_the_lead_writes_routing(ws):
    ws.card("api")
    joined = ws.board.join(ws.lead, agent="codex-one", team="core", runtime="codex-cli")
    worker = ws.board.caller(agent="codex-one", token=joined["token"])
    with pytest.raises(IdentityError):
        routing.set_config(ws.board, worker, "rules", [{"label": "docs", "provider": "shell"}])
    assert routing.config(ws.board)["rules"] == []


@pytest.mark.parametrize(
    "bad",
    [
        [{"provider": "codex"}],
        [{"label": "docs", "provider": "paid-api"}],
        [{"label": "docs", "provider": "codex", "model": "x; rm -rf /"}],
        [{"path": "../outside", "provider": "codex"}],
        [{"path": "/etc", "provider": "codex"}],
        [{"label": "docs", "provider": "codex", "fallback": ["nope"]}],
        [{"label": "docs", "provider": "codex", "unknown": 1}],
        {"label": "docs"},
    ],
)
def test_invalid_rules_are_rejected(ws, bad):
    with pytest.raises(Precondition):
        routing.set_config(ws.board, ws.lead, "rules", bad)


def test_tampered_routing_file_is_refused(ws):
    ws.card("api")
    with open(ws.board._p(routing.FILE), "w", encoding="utf-8") as f:
        json.dump({"schema": 2, "routing": {"rules": [{"label": "x", "provider": "paid-api"}]}}, f)
    with pytest.raises(Precondition):
        routing.suggest(ws.board, ws.lead, collected=quota(claude=(1, 1)))


def test_shell_has_no_plan_quota_but_plan_quota_can_be_forced(ws):
    ws.card("api")
    assert routing.suggest(ws.board, ws.lead, provider="shell", collected={"providers": []})["provider"] == "shell"
    routing.set_config(ws.board, ws.lead, "quota.shell", "plan")
    with pytest.raises(Precondition, match="unknown"):
        routing.suggest(ws.board, ws.lead, provider="shell", collected={"providers": []})


def test_cli_next_uses_recorded_limits(ws, capsys):
    ws.card("api")
    secret = ws.root.parent / "lead.secret"
    secret.write_text(ws.token)
    secret.chmod(0o600)
    args = ["--board", str(ws.root), "--lead-secret", str(secret), "--json"]
    assert cli.main(["next", "--provider", "claude", *args]) == 3
    assert "headroom" in json.loads(capsys.readouterr().out)["message"]
    assert limits.ingest_claude(json.dumps({"rate_limits": {"five_hour": {"used_percentage": 20}}}))
    assert cli.main(["next", "--provider", "claude", *args]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["card"], out["provider"]) == ("api", "claude")
