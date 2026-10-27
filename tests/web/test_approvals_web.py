import json
from pathlib import Path


def test_board_payload_carries_the_approval_inbox(server):
    status, _, raw = server.get("/api/board/acme-shop")
    assert status == 200
    inbox = json.loads(raw)["approvals"]
    ids = {x["id"] for x in inbox["items"]}
    assert "decision:search-typo" in ids
    assert inbox["total"] == len(inbox["items"])
    for item in inbox["items"]:
        assert item["commands"] and all(c.startswith(("raid ", "git ")) for c in item["commands"])
        assert all("~raid" not in c for c in item["commands"])
        lead_only = [c for c in item["commands"] if c.startswith("raid --lead-secret ")]
        assert all(c.startswith('raid --lead-secret "$LEAD_SECRET_FILE" ') for c in lead_only)
    assert str(Path.home()) not in raw.decode()


def test_page_drives_needs_you_from_the_inbox_without_inline_script(server):
    status, _, raw = server.get("/")
    page = raw.decode()
    assert status == 200
    assert 'id="needs"' in page and 'id="needsList"' in page
    assert "<script>" not in page and "onclick=" not in page
    status, _, js = server.get("/static/app.js")
    assert b"function renderFocus" in js and b"innerHTML" not in js
    assert b'"merge"' not in js and b'close: "Close card"' in js
