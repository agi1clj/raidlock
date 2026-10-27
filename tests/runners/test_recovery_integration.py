from pathlib import Path

import pytest
from runhelpers import fake

from raidlock import budgets, scan
from raidlock.core.errors import Precondition, StaleLead
from raidlock.lore import Vault, approve, propose
from raidlock.runners.runner import delegate

DONE = '{"status":"done","evidence":["checked"],"paths_changed":[],"open_questions":[]}'


def test_budget_stops_delegation_before_join_or_clone(ws):
    ws.card("api")
    budgets.set_budget(ws.board, ws.lead, budgets.TOKENS, 0)
    before = ws.board.agents()
    with pytest.raises(Precondition, match="budget exhausted"):
        delegate(ws.board, ws.lead, "api", "shell", cmd=fake("--say", DONE))
    assert ws.board.agents() == before
    assert ws.board.card("api")["lane"] == "todo"
    assert not Path(ws.board.home, "runs", ws.board.name, "api").exists()


def test_patch_scan_blocks_sensitive_patch_before_verify(ws, monkeypatch):
    original_which = scan.shutil.which
    monkeypatch.setattr(scan.shutil, "which", lambda name: None if name == "gitleaks" else original_which(name))
    ws.card("api", verify_cmd="touch src/verified")
    result = delegate(
        ws.board,
        ws.lead,
        "api",
        "shell",
        heartbeat=0.05,
        cmd=fake("--write", "src/config.txt=AKIAABCDEFGHIJKLMNOP", "--say", DONE),
    )
    assert result["lane"] == "blocked"
    assert "patch safety findings" in result["reason"]
    assert result["patch"] == ""
    for name in ("rejected.patch", "changes.patch", "repo", "lead.git"):
        assert not Path(result["run_dir"], name).exists()
    assert result["verify"] is None
    assert not Path(result["run_dir"], "repo", "src", "verified").exists()


def test_approved_lore_reaches_bounded_prompt_and_is_counted(ws):
    ws.card("api")
    lesson = propose(ws.board, ws.lead, "Use deterministic fixtures", "api")
    assert ws.board.brief("api")["lore_tokens"] == 0
    approve(ws.board, ws.lead, [lesson["id"]])
    ws.board.set_setting(ws.lead, "brief_max_tokens", 250)
    brief = ws.board.brief("api")
    assert brief["tokens"] <= 250
    assert brief["lore_ids"] == [lesson["id"]]
    result = delegate(ws.board, ws.lead, "api", "shell", heartbeat=0.05, cmd=fake("--say", DONE))
    prompt = Path(result["run_dir"], "prompt.md").read_text()
    assert '> "lessons (lessons):"' in prompt
    lesson_lines = [line for line in prompt.splitlines() if "Use deterministic fixtures" in line]
    assert len(lesson_lines) == 1
    assert lesson_lines[0].startswith("> \"- 'Use deterministic fixtures'")
    assert lesson_lines[0].endswith('"')
    assert Vault.for_board(ws.board).get(lesson["id"])["hits"] == 1


def test_real_handover_fences_lore_approval(ws):
    ws.card("api")
    lesson = propose(ws.board, ws.lead, "Use deterministic fixtures", "api")
    ws.board.lead_handover(ws.lead, "next-session")
    with pytest.raises(StaleLead):
        approve(ws.board, ws.lead, [lesson["id"]])
    assert [item["id"] for item in Vault.for_board(ws.board).pending()] == [lesson["id"]]
    assert Vault.for_board(ws.board).lessons() == []
