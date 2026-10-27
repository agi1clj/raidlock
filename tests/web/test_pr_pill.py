from raidlock.web.data import slim_card, slim_pr


def test_slim_pr_keeps_display_fields_only():
    pr = {
        "number": 7,
        "url": "https://github.com/o/r/pull/7",
        "state": "OPEN",
        "head": "abc",
        "checks": {"passed": 2, "failed": 1, "pending": 0, "total": 3, "state": "failure", "failing": ["lint"]},
        "held": "checks failed: lint",
    }
    assert slim_pr(pr) == {
        "number": 7,
        "url": "https://github.com/o/r/pull/7",
        "state": "OPEN",
        "checks": {"passed": 2, "failed": 1, "pending": 0, "total": 3, "state": "failure"},
        "held": "checks failed: lint",
    }


def test_slim_pr_drops_missing_or_malformed_links():
    assert slim_pr(None) is None
    assert slim_pr("7") is None
    assert slim_pr({"url": "https://github.com/o/r/pull/7"}) is None
    assert slim_pr({"number": 3})["checks"] == {"passed": 0, "failed": 0, "pending": 0, "total": 0, "state": "none"}


def test_slim_card_carries_the_pr():
    card = {"id": "c", "title": "t", "lane": "review", "pr": {"number": 9, "state": "MERGED"}}
    assert slim_card(card)["pr"]["number"] == 9
    assert slim_card({"id": "c", "title": "t", "lane": "todo"})["pr"] is None


def test_app_renders_a_pr_pill_with_a_checked_link(server):
    status, _, js = server.get("/static/app.js")
    assert status == 200
    assert b"function prPill" in js and b'rel: "noopener noreferrer"' in js
    assert b"/pull\\/\\d+$/" in js
