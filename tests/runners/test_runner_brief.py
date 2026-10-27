import json

from raidlock.runners import brief

CORE = """# api: Add API
team core | lane doing | owner x | fence 3
scope: add /health
locks: app/src
## Rules
- Edit only inside your locks; reads need no lock.
## Loop
raidlock note api "<evidence>"
raidlock move api review --note "<evidence>"
## Notes
- 2026-10-22T12:00:00Z lead: use token=abcdefgh1234 for nothing
"""


def test_render_worker_has_contract_then_core_brief_without_loop():
    text = brief.render(CORE, "worker", ["src", ""], "abc123")
    assert text.startswith("# Worker contract (raidlock)")
    assert text.index("Worker contract") < text.index("api: Add API") < text.index("Allowed paths in this clone")
    assert "Allowed paths in this clone: src, (the whole repository)" in text and "scope: add /health" in text
    assert "## Loop" not in text and "raidlock move" not in text and "Notes" in text and "Rules" in text
    assert '"status": "done|partial|blocked"' in text


def test_render_reviewer_names_base():
    text = brief.render(CORE, "reviewer", ["src"], "abc123")
    assert "git diff abc123 HEAD" in text and '"verdict": "approve|changes|comment"' in text


def test_render_redacts():
    assert "abcdefgh1234" not in brief.render(CORE, "worker", ["src"], "b")


def test_render_with_real_board_brief(tmp_path):
    from runhelpers import Ws

    ws = Ws(tmp_path)
    ws.card("api", scope="add a module")
    core = ws.board.brief("api")
    text = brief.render(core["text"], "worker", ["src"], "b")
    assert "scope: add a module" in text and "locks: app/src" in text and "## Loop" not in text


def test_handback_parsing():
    good = '{"status":"done","evidence":["ran tests"],"paths_changed":["a"],"open_questions":[]}'
    assert brief.handback(good)["status"] == "done"
    fenced = "Done.\n```json\n" + good + "\n```\n"
    assert brief.handback(fenced)["evidence"] == ["ran tests"]
    assert brief.handback({"status": "weird", "evidence": "one"}) == {
        "status": "partial",
        "evidence": ["one"],
        "paths_changed": [],
        "open_questions": [],
    }
    loose = brief.handback("no json here", fallback_text="no json here")
    assert loose["status"] == "partial" and loose["open_questions"] == ["the agent returned no handback JSON"]


def test_review_parsing():
    assert brief.review('{"verdict":"approve","summary":"ok","findings":[]}')["verdict"] == "approve"
    assert brief.review("looks fine", fallback_text="looks fine") == {
        "verdict": "comment",
        "summary": "looks fine",
        "findings": [],
    }


def test_cap_keeps_handback_under_4kb():
    big = {
        "status": "done",
        "evidence": ["e" * 390] * 20,
        "paths_changed": [f"p/{i}" * 30 for i in range(500)],
        "open_questions": [],
    }
    out = brief.cap(big)
    assert len(json.dumps(out, ensure_ascii=False).encode()) <= 4096
    assert out["truncated"] is True and out["status"] == "done"


def test_schemas_are_strict():
    for schema in (brief.HANDBACK_SCHEMA, brief.REVIEW_SCHEMA):
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])
