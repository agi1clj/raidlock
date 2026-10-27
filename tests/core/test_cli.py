import json

import pytest
from corehelpers import make_repo, run_cli


@pytest.fixture
def cli(tmp_path):
    ws = tmp_path / "ws"
    make_repo(ws / "app", "git@github.com:acme/app.git")
    env = {"RAIDLOCK_HOME": str(tmp_path / "home"), "RAIDLOCK_BOARD": str(ws)}

    def call(*args, as_env=None, expect=None):
        r = run_cli(*args, "--json", env={**env, **(as_env or {})}, cwd=str(tmp_path))
        data = json.loads(r.stdout) if r.stdout.strip() else {}
        if expect is not None:
            assert r.returncode == expect, (args, r.stdout, r.stderr)
        assert "Traceback" not in r.stderr
        return r.returncode, data

    code, init = call("init", str(ws), "--runtime", "human", expect=0)
    lead = {"RAIDLOCK_AGENT": init["lead"], "RAIDLOCK_LEAD_SECRET_FILE": init["token_file"]}
    call("team", "add", "core", "--max", "2", as_env=lead, expect=0)
    _, w = call(
        "join",
        "--agent",
        "w-one",
        "--team",
        "core",
        "--runtime",
        "codex-cli",
        "--model",
        "gpt-6-astra",
        as_env=lead,
        expect=0,
    )
    worker = {"RAIDLOCK_AGENT": "w-one", "RAIDLOCK_TOKEN": w["token"]}
    call.lead, call.worker, call.ws, call.env = lead, worker, ws, env
    return call


def test_json_contract_happy_path(cli):
    _, new = cli("new", "Fix login", "--locks", "app/auth", as_env=cli.lead, expect=0)
    assert new == {"ok": True, "id": "fix-login"}
    _, full = cli("show", "fix-login", "--fields", "all", as_env=cli.lead, expect=0)
    assert full["board"].endswith(".board") and full["card"]["locks"] == ["app/auth"]
    _, claim = cli("claim", "fix-login", as_env=cli.worker, expect=0)
    worker = {**cli.worker, "RAIDLOCK_FENCE": str(claim["fence"])}
    assert claim["agent"] == "w-one" and claim["paths"] == ["app/auth"]
    for args in (("heartbeat", "fix-login"), ("note", "fix-login", "halfway"), ("lock", "fix-login", "app/auth2")):
        cli(*args, as_env=worker, expect=0)
    cli("cost", "fix-login", "--model", "claude-opus-5-5", "--input", "1000", "--output", "10", as_env=worker, expect=0)
    _, totals = cli("cost", expect=0)
    assert totals["total"]["tokens"] == 1010
    cli("move", "fix-login", "review", "--note", "tests pass", as_env=worker, expect=0)
    _, done = cli("done", "fix-login", as_env=cli.lead, expect=0)
    assert done["lane"] == "done" and done["a2a_state"] == "completed"
    for args in (
        ("show",),
        ("show", "fix-login"),
        ("who",),
        ("claimable",),
        ("transcript", "--task", "fix-login"),
        ("workspaces",),
        ("projects",),
        ("version",),
        ("config", "get"),
        ("team", "list"),
        ("reap",),
        ("check", "app/auth"),
        ("spend", "--by", "model"),
        ("spend", "--all", "--by", "week"),
    ):
        code, data = cli(*args, as_env=cli.lead)
        assert code == 0 and data["ok"] is True, args


def test_exit_code_2_conflict_and_move_doing(cli):
    cli("new", "A", "--id", "card-a", "--locks", "app/x", as_env=cli.lead, expect=0)
    cli("new", "B", "--id", "card-b", "--locks", "app/x/y", as_env=cli.lead, expect=0)
    cli("claim", "card-a", as_env=cli.worker, expect=0)
    _, err = cli("claim", "card-b", as_env=cli.lead, expect=2)
    assert err["ok"] is False and err["error"] == "denied" and err["conflicts"] and err["hint"]
    _, err = cli("move", "card-b", "doing", as_env=cli.lead, expect=2)
    assert err["hint"] == "raidlock claim card-b"
    code, data = cli("check", "app/x/z")
    assert code == 2 and data["free"] is False


def test_exit_code_3_usage_and_missing(cli):
    _, err = cli("claim", expect=3)
    assert err["error"] == "usage"
    cli("show", "nope", expect=3)
    cli("move", "x", "sideways", expect=3)


def test_exit_code_4_wait_timeout(cli):
    cli("new", "A", "--id", "card-a", "--locks", "app/x", as_env=cli.lead, expect=0)
    cli("claim", "card-a", as_env=cli.worker, expect=0)
    _, err = cli("wait", "card-a", "--timeout", "0.2", "--interval", "0.05", expect=4)
    assert err["error"] == "timeout"


def test_exit_code_5_identity(cli):
    cli("new", "A", "--id", "card-a", as_env=cli.lead, expect=0)
    cli("done", "card-a", as_env=cli.worker, expect=5)
    cli("team", "add", "evil", as_env=cli.worker, expect=5)
    cli("claim", "card-a", as_env={"RAIDLOCK_AGENT": "w-one", "RAIDLOCK_TOKEN": "wrong"}, expect=5)
    cli(
        "done",
        "card-a",
        as_env={"RAIDLOCK_AGENT_ID": "w-one", "RAIDLOCK_ROLE": "lead", "RAIDLOCK_TOKEN": cli.worker["RAIDLOCK_TOKEN"]},
        expect=5,
    )
    cli("done", "card-a", "--agent", cli.lead["RAIDLOCK_AGENT"], as_env=cli.worker, expect=5)
    cli("join", "--agent", "w-one", "--role", "lead", as_env={"RAIDLOCK_AGENT_ID": "w-one"}, expect=5)
    cli("msg", "do it", "--to", "w-one", "--from", cli.lead["RAIDLOCK_AGENT"], as_env=cli.worker, expect=5)


def test_human_output_prints_board_and_next_step(cli, tmp_path):
    r = run_cli("new", "Plain", "--locks", "app/p", env={**cli.env, **cli.lead}, cwd=str(tmp_path))
    lines = r.stdout.splitlines()
    assert r.returncode == 0 and len(lines) == 1
    assert lines[0].startswith("[ws] Created plain") and lines[0].endswith("Next: raidlock claim plain")


def test_human_errors_have_next_and_no_traceback(cli, tmp_path):
    r = run_cli("claim", "missing", env={**cli.env, **cli.worker}, cwd=str(tmp_path))
    assert r.returncode == 3 and "Next: raidlock show" in r.stderr and "Traceback" not in r.stderr


def test_grouped_help(tmp_path):
    r = run_cli(cwd=str(tmp_path))
    assert r.returncode == 0
    for group in ("Start:", "Plan:", "Work:", "Agents:", "Board:"):
        assert group in r.stdout
    assert "heartbeat" not in r.stdout
    adv = run_cli("help", "advanced", cwd=str(tmp_path))
    assert adv.returncode == 0 and "heartbeat" in adv.stdout and "Exit codes" in adv.stdout


def test_read_commands_do_not_register(tmp_path):
    ws = tmp_path / "quiet"
    env = {"RAIDLOCK_HOME": str(tmp_path / "home2")}
    run_cli("init", str(ws), "--runtime", "human", env=env, cwd=str(tmp_path))
    from raidlock.core.db import Index

    index = Index(str(tmp_path / "home2"))
    assert [w["name"] for w in index.workspaces()] == ["quiet"]
    index.unregister(["quiet"])
    run_cli("show", "--board", str(ws), env=env, cwd=str(tmp_path))
    run_cli("who", "--board", str(ws), env=env, cwd=str(tmp_path))
    assert index.workspaces() == []


def test_plan_import(cli, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# plan\n- Write parser | locks: app/parser\n- Add docs\n", encoding="utf-8")
    _, r = cli("plan", "import", str(plan), as_env=cli.lead, expect=0)
    assert r["cards"] == ["write-parser", "add-docs"]
    plan_json = tmp_path / "plan.json"
    plan_json.write_text(json.dumps([{"id": "j1", "title": "From JSON", "locks": "app/j"}]), encoding="utf-8")
    cli("plan", "import", str(plan_json), as_env=cli.lead, expect=0)
    _, again = cli("plan", "import", str(plan_json), as_env=cli.lead, expect=3)


def test_plan_import_reads_spec_tool_task_lists(cli, tmp_path):
    change = tmp_path / "openspec" / "changes" / "add-cart"
    change.mkdir(parents=True)
    (change / "tasks.md").write_text(
        "## 1. Cart\n\n- [x] 1.1 Sketch the API\n- [ ] 1.2 Reject empty carts | locks: app/cart\n"
        "- [ ] 1.3 Add cart tests\n\nNotes:\n- keep it small\n",
        encoding="utf-8",
    )
    _, r = cli("plan", "import", str(change), as_env=cli.lead, expect=0)
    assert r["cards"] == ["reject-empty-carts", "add-cart-tests"]
    _, card = cli("show", "reject-empty-carts", "--fields", "all", as_env=cli.lead, expect=0)
    assert card["card"]["ticket"] == "1.2" and card["card"]["locks"] == ["app/cart"]
    kit = tmp_path / "tasks.md"
    kit.write_text(
        "# Tasks\n\n- [ ] T001 [P] [US1] Create the order model in app/models/order.py\n"
        "- [X] T002 Set up the project\n- Path conventions: app/\n",
        encoding="utf-8",
    )
    _, r = cli("plan", "import", str(kit), as_env=cli.lead, expect=0)
    assert r["cards"] == ["create-the-order-model-in-app-models"]


def test_up_creates_then_joins(tmp_path):
    ws = tmp_path / "fresh"
    ws.mkdir()
    env = {"RAIDLOCK_HOME": str(tmp_path / "home3"), "RAIDLOCK_RUNTIME": "shell"}
    first = json.loads(run_cli("up", "--json", env=env, cwd=str(ws)).stdout)
    assert first["action"] == "init" and "token" not in first and first["token_file"]
    second = json.loads(run_cli("up", "--json", "--agent", "shell-worker-9", env=env, cwd=str(ws)).stdout)
    assert second["action"] == "join" and second["token"]
    third = json.loads(
        run_cli(
            "up",
            "--json",
            env={**env, "RAIDLOCK_AGENT": "shell-worker-9", "RAIDLOCK_TOKEN": second["token"]},
            cwd=str(ws),
        ).stdout
    )
    assert third["action"] == "status" and third["status"] == "idle"


def test_quickstart(tmp_path):
    env = {"RAIDLOCK_HOME": str(tmp_path / "home4")}
    r = run_cli("quickstart", str(tmp_path / "qs"), "--runtime", "human", env=env, cwd=str(tmp_path))
    assert r.returncode == 0 and "raidlock ui" in r.stdout and "raidlock join" in r.stdout
    data = json.loads(run_cli("show", "--json", "--board", str(tmp_path / "qs"), env=env, cwd=str(tmp_path)).stdout)
    assert [c["id"] for c in data["cards"]] == ["example"]


def test_verify_exit_code(cli):
    from raidlock.runners import isolation, workspace

    workspace.git(str(cli.ws), "init", "--quiet")
    (cli.ws / ".git" / "info" / "exclude").write_text("app/\n")
    workspace.git(str(cli.ws), *workspace.IDENT, "commit", "--quiet", "--allow-empty", "-m", "fixture")
    cli("new", "V", "--id", "v1", "--verify-cmd", "python3 -c 'import sys; sys.exit(3)'", as_env=cli.lead, expect=0)
    if isolation.mode() is None:
        _, result = cli("verify", "v1", as_env=cli.lead, expect=3)
        assert "isolated" in result["message"]
        return
    _, r = cli("verify", "v1", as_env=cli.lead, expect=2)
    assert r["passed"] is False and r["exit"] == 3


def test_extension_registration():
    from raidlock import cli as C

    C.add_command("zz-test", "test extension", lambda p: p.add_argument("x"), lambda ctx, a: ({"x": a.x}, f"x={a.x}"))
    try:
        assert C.main(["zz-test", "42"]) == 0
    finally:
        C.COMMANDS.pop("zz-test")


def test_minimal_json_and_fields(cli):
    cli("new", "A", "--id", "card-a", "--locks", "app/x", as_env=cli.lead, expect=0)
    _, claim = cli("claim", "card-a", as_env=cli.worker, expect=0)
    assert set(claim) == {"ok", "card", "agent", "fence", "expires", "paths", "cap"}
    _, some = cli("show", "card-a", "--fields", "card", expect=0)
    assert set(some) == {"ok", "card"}
    _, who = cli("who", expect=0)
    assert set(who["agents"]["w-one"]) == {"display", "status", "card"}


def test_brief_command_deterministic_and_capped(cli):
    cli("new", "Brief me", "--id", "b1", "--locks", "app/b", "--scope", "do the thing", as_env=cli.lead, expect=0)
    _, one = cli("brief", "b1", expect=0)
    _, two = cli("brief", "b1", expect=0)
    assert one["text"] == two["text"] and one["tokens"] == -(-len(one["text"]) // 4)
    assert "locks: app/b" in one["text"] and "scope: do the thing" in one["text"]


def test_tokens_command(cli):
    _, r = cli("tokens", expect=0)
    assert r["total"] > 0 and {i["name"] for i in r["items"]} >= {"cli outputs", "card brief"}


def test_inbox_wait_timeout(cli):
    _, err = cli("inbox", "--wait", "0.2", "--interval", "0.05", as_env=cli.worker, expect=4)
    assert err["error"] == "timeout"


@pytest.mark.parametrize("lane", ["todo", "doing", "review", "blocked"])
def test_close_with_evidence(cli, lane):
    from raidlock.core import Board
    from raidlock.reputation import reputation
    from raidlock.web.data import Workspace

    cli("new", "External delivery", "--locks", "app/auth", "--verify-cmd", "exit 1", as_env=cli.lead, expect=0)
    if lane != "todo":
        _, claim = cli("claim", "external-delivery", as_env=cli.worker, expect=0)
        if lane != "doing":
            cli(
                "move",
                "external-delivery",
                lane,
                as_env={**cli.worker, "RAIDLOCK_FENCE": str(claim["fence"])},
                expect=0,
            )
    board = Board.open(str(cli.ws), home=cli.env["RAIDLOCK_HOME"])
    before = reputation(board)
    url = "https://github.com/acme/app/pull/42"
    _, result = cli(
        "close", "external-delivery", "--evidence", url, "--note", "Merged externally", as_env=cli.lead, expect=0
    )
    assert result["lane"] == "done" and result["evidence"] == url
    _, shown = cli("show", "external-delivery", as_env=cli.lead, expect=0)
    card = shown["card"]
    assert card["evidence"] == url
    assert not card.get("verifications") and not card.get("reviews")
    assert f"closed with evidence {url}: Merged externally" in card["notes"][-1]["text"]
    assert "external-delivery" not in board.leases()
    assert reputation(board) == before
    records = board.transcript.read(task="external-delivery")
    assert any(p.get("data", {}).get("evidence") == url for r in records for p in r["parts"])
    payload = Workspace(board.name, board.dir, board.home).refresh()[1]
    assert payload["cards"][0]["evidence"] == url
    assert all(row["done"] == 0 for row in payload["scorecard"])
    cli("close", "external-delivery", "--evidence", url, "--note", "Again", as_env=cli.lead, expect=3)
    cli("reopen", "external-delivery", as_env=cli.lead, expect=0)
    assert not board.card("external-delivery").get("evidence")


@pytest.mark.parametrize("url", ["http://example.com/pr/1", "file:///tmp/pr", "https:///missing", "garbage"])
def test_close_evidence_refuses_invalid_url(cli, url):
    cli("new", "External", as_env=cli.lead, expect=0)
    _, result = cli("close", "external", "--evidence", url, "--note", "Delivered", as_env=cli.lead, expect=3)
    assert "https" in str(result) and "--evidence" in str(result)
    _, shown = cli("show", "external", as_env=cli.lead, expect=0)
    assert shown["card"]["lane"] == "todo"


def test_close_evidence_requires_lead_and_note(cli):
    cli("new", "External", as_env=cli.lead, expect=0)
    args = ("close", "external", "--evidence", "https://example.com/pr/1")
    cli(*args, "--note", "Delivered", as_env=cli.worker, expect=5)
    cli(*args, as_env=cli.lead, expect=3)
    cli(*args, "--note", "   ", as_env=cli.lead, expect=3)
    cli(*args, "--reviewer", "w-one", "--note", "Delivered", as_env=cli.lead, expect=3)


def test_close_with_commit_evidence(cli):
    import subprocess

    app = cli.ws / "app"
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "-C", str(app), "init", "--quiet"], check=True)
    subprocess.run(["git", "-C", str(app), *ident, "commit", "--quiet", "--allow-empty", "-m", "x"], check=True)
    sha = subprocess.run(["git", "-C", str(app), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    cli("new", "Merged locally", "--locks", "app/auth", as_env=cli.lead, expect=0)
    _, missing = cli("close", "merged-locally", "--evidence", "deadbeef", "--note", "x", as_env=cli.lead, expect=3)
    assert "not in" in str(missing)
    _, result = cli("close", "merged-locally", "--evidence", sha[:9], "--note", "Merged", as_env=cli.lead, expect=0)
    assert result["evidence"] == "commit:" + sha
    _, shown = cli("show", "merged-locally", as_env=cli.lead, expect=0)
    assert shown["card"]["lane"] == "done"


def test_plan_import_keeps_leading_numbers_in_titles(cli, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("- [ ] 2024 roadmap review\n- [ ] 3. Ship it\n", encoding="utf-8")
    _, r = cli("plan", "import", str(plan), as_env=cli.lead, expect=0)
    assert r["cards"] == ["2024-roadmap-review", "ship-it"]
