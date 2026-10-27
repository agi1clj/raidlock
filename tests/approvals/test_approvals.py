import json
import shlex
import subprocess
from pathlib import Path

import pytest
from runhelpers import fake, git

from raidlock import approvals, cli
from raidlock.core.hints import lead_cmd
from raidlock.lore.approval import propose
from raidlock.runners import isolation
from raidlock.runners.runner import delegate
from raidlock.web import data


def lead(board, rest):
    return lead_cmd(board, rest, prog="raid")


DONE = '{"status":"done","evidence":["ok"],"paths_changed":[],"open_questions":[]}'
BLOCKED = '{"status":"blocked","evidence":[],"paths_changed":[],"open_questions":["which API?"]}'


@pytest.fixture(autouse=True)
def sandboxed(monkeypatch):
    original = isolation.run
    monkeypatch.setattr(isolation, "mode", lambda: "test-isolation")
    monkeypatch.setattr(isolation, "run", lambda *a, **k: original(*a, **{**k, "kind": None}))


def kinds(board, **kw):
    return {x["id"]: x for x in approvals.collect(board, **kw)["items"]}


def test_empty_board_needs_nothing(ws):
    r = approvals.collect(ws.board)
    assert r["items"] == [] and r["total"] == 0
    assert approvals.render(r) == "Nothing needs you right now."


def test_review_close_apply_lifecycle(ws):
    ws.card("api")
    delegate(ws.board, ws.lead, "api", "shell", heartbeat=0.05, cmd=fake("--write", "src/new.py=1", "--say", DONE))
    ws.board.set_setting(ws.lead, "require_review", 1)
    ws.board.set_setting(ws.lead, "require_verify", "false")
    items = kinds(ws.board)
    assert items["review:api"]["kind"] == "review"
    assert "raid review api --verdict approve   # as a reviewer agent" in items["review:api"]["commands"]
    assert lead(ws.board, "delegate api --to <runtime> --as reviewer") in items["review:api"]["commands"]
    joined = ws.board.join(ws.lead, agent="rev-one", role="reviewer", provider="openai")
    ws.board.review("api", ws.board.caller(agent="rev-one", token=joined["token"]), "approve")
    items = kinds(ws.board)
    assert "review:api" not in items
    assert items["close:api"]["commands"] == [lead(ws.board, "done api")]
    assert "--lead-secret " in items["close:api"]["commands"][0]
    ws.board.move("api", "done", ws.lead)
    items = kinds(ws.board)
    assert "close:api" not in items
    apply = items["apply:api"]
    assert apply["commands"][0].startswith("git -C app apply --index ")
    subprocess.run(shlex.split(apply["commands"][0].replace("~", str(Path.home()), 1)), cwd=ws.root, check=True)
    assert "apply:api" not in kinds(ws.board)
    assert "apply:api" not in kinds(ws.board, check_apply=False)


def test_verify_gate_blocks_close(ws):
    ws.card("api", verify_cmd="true")
    delegate(ws.board, ws.lead, "api", "shell", heartbeat=0.05, cmd=fake("--say", DONE))
    item = kinds(ws.board)["review:api"]
    assert "verification receipt" in item["reason"]
    assert lead(ws.board, "verify api") in item["commands"]


def test_missing_verify_command_suggests_edit(ws):
    ws.card("api")
    ws.board.set_setting(ws.lead, "require_verify", "true")
    delegate(ws.board, ws.lead, "api", "shell", heartbeat=0.05, cmd=fake("--say", DONE))
    item = kinds(ws.board)["review:api"]
    assert lead(ws.board, 'edit api --verify-cmd "<check>"') in item["commands"]


def test_blocked_cards_and_lore_are_lead_decisions(ws):
    ws.card("api")
    delegate(ws.board, ws.lead, "api", "shell", heartbeat=0.05, cmd=fake("--say", BLOCKED))
    lesson = propose(ws.board, ws.lead, "Prefer small fixtures", "api")
    items = kinds(ws.board)
    decision = items["decision:api"]
    assert lead(ws.board, "resurrect api --dry-run") in decision["commands"]
    assert "raid claim api --take-over   # as another worker" in decision["commands"]
    assert "-> blocked" not in decision["reason"]
    lore = items[f"lore:{lesson['id']}"]
    assert lore["commands"][0] == lead(ws.board, f"lore approve {lesson['id']}")


def test_hostile_titles_and_patch_paths_cannot_inject_commands(ws, tmp_path):
    ws.board.new_card(ws.lead, "x; rm -rf ~ `id` $(whoami)", card_id="api", team="core", locks="app/src")
    delegate(ws.board, ws.lead, "api", "shell", heartbeat=0.05, cmd=fake("--say", BLOCKED))
    for item in approvals.collect(ws.board)["items"]:
        for command in item["commands"]:
            assert "rm -rf" not in command and "`" not in command and "$(" not in command
            assert shlex.split(command)


def test_reading_the_inbox_changes_nothing(ws):
    ws.card("api")
    delegate(ws.board, ws.lead, "api", "shell", heartbeat=0.05, cmd=fake("--write", "src/n.py=1", "--say", DONE))
    before = (ws.board.card("api"), ws.board.leases(), ws.board.version())
    approvals.collect(ws.board)
    assert (ws.board.card("api"), ws.board.leases(), ws.board.version()) == before


def test_stale_patch_is_not_offered_for_apply(ws):
    ws.card("api")
    delegate(ws.board, ws.lead, "api", "shell", heartbeat=0.05, cmd=fake("--write", "src/main.py=x", "--say", DONE))
    ws.board.move("api", "done", ws.lead)
    assert "apply:api" in kinds(ws.board)
    (ws.repo / "src" / "main.py").write_text("conflicting\n")
    git(ws.repo, "commit", "-qam", "conflict")
    assert "apply:api" not in kinds(ws.board)


def test_cli_and_web_payload(ws, capsys):
    ws.card("api")
    delegate(ws.board, ws.lead, "api", "shell", heartbeat=0.05, cmd=fake("--say", BLOCKED))
    assert cli.main(["approvals", "--board", str(ws.root), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [x["id"] for x in out["items"]] == ["decision:api"]
    assert cli.main(["approvals", "--board", str(ws.root), "--kind", "close"]) == 0
    assert "Nothing needs you" in capsys.readouterr().out
    ws_view = data.Workspace(ws.board.name, ws.board.dir, ws.board.home)
    _, payload = ws_view.refresh()
    public = data.public(payload)
    assert public["approvals"]["items"][0]["id"] == "decision:api"
    assert str(ws.root) not in json.dumps(public["approvals"])


def test_malicious_lesson_frontmatter_id_never_becomes_a_command(ws, tmp_path):
    from raidlock.lore import Vault

    ws.card("api")
    lesson = propose(ws.board, ws.lead, "Prefer small fixtures", "api")
    vault = Vault.for_board(ws.board)
    path = Path(vault.lesson_path(lesson["id"], ".pending"))
    text = path.read_text()
    payload = f"x; touch {tmp_path / 'approval-pwn'} #"
    path.write_text(text.replace(lesson["id"], payload))
    assert approvals._lore_items(ws.board) == []
    assert not (tmp_path / "approval-pwn").exists()


@pytest.mark.parametrize(
    ("src", "shown"),
    [
        ("~/.raidlock/tokens/abc/lead", "~/.raidlock/tokens/abc/lead"),
        ("keychain", "keychain"),
        ("/srv/raid/tokens/abc/lead", '"$LEAD_SECRET_FILE"'),
    ],
)
def test_web_commands_show_a_runnable_lead_secret(ws, monkeypatch, src, shown):
    monkeypatch.setattr(approvals, "lead_secret_src", lambda board: src)
    assert approvals.web_lead_src(ws.board) == shown


def test_close_items_never_say_merge(ws):
    ws.card("api")
    delegate(ws.board, ws.lead, "api", "shell", heartbeat=0.05, cmd=fake("--say", DONE))
    ws.board.set_setting(ws.lead, "require_verify", "false")
    result = approvals.collect(ws.board)
    assert [x["kind"] for x in result["items"]] == ["close"]
    labels = [line for line in approvals.render(result).splitlines() if not line.lstrip().startswith("$")]
    assert "merge" not in approvals.KINDS and not any("merge" in line for line in labels)
