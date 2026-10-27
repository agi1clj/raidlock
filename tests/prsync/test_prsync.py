import json

import pytest
from prhelpers import commit

from raidlock import pr as P
from raidlock import prsync as S
from raidlock.core import Denied, IdentityError, Precondition, clock, store

PASS_RUN = {"__typename": "CheckRun", "name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}
FAIL_RUN = {"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"}
PENDING_RUN = {"__typename": "CheckRun", "name": "e2e", "status": "IN_PROGRESS", "conclusion": ""}


def view(sha, state="MERGED", number=7, checks=(PASS_RUN,), **extra):
    data = {
        "number": number,
        "url": f"https://github.com/acme/app/pull/{number}",
        "state": state,
        "isDraft": False,
        "mergedAt": "2026-10-23T10:00:00Z" if state == "MERGED" else None,
        "headRefOid": sha,
        "headRefName": "raid/c1",
        "baseRefName": "main",
        "statusCheckRollup": list(checks),
        "reviewDecision": "APPROVED",
        **extra,
    }
    return {"json": data}


RATE_LIMIT = {"exit": 1, "stderr": "GraphQL: API rate limit exceeded for user ID 1."}


def opened(prw, approve=True, **kw):
    card = prw.build(**kw)
    if approve:
        prw.board.review("c1", prw.reviewer, "approve", "looks right")
    P.open_pr(prw.board, prw.lead, "c1")
    return card["verifications"][-1]["sha"]


def lane(prw, cid="c1"):
    return prw.board.card(cid)["lane"]


def test_open_pr_state_and_checks_recorded(prw):
    sha = opened(prw)
    prw.gh_state(view=view(sha, state="OPEN", checks=(PASS_RUN, PENDING_RUN)))
    r = S.sync(prw.board, prw.lead)
    assert r["synced"] == [{"card": "c1", "state": "OPEN", "checks": "pending"}]
    pr = prw.board.card("c1")["pr"]
    assert pr["checks"]["passed"] == 1 and pr["checks"]["pending"] == 1 and pr["head"] == sha
    assert lane(prw) == "review"


def test_merge_with_receipt_and_review_closes_card(prw):
    sha = opened(prw)
    prw.gh_state(view=view(sha))
    r = S.sync(prw.board, prw.lead)
    assert r["done"] == ["c1"]
    assert lane(prw) == "done"
    assert any("merged as pull request #7" in n["text"] for n in prw.board.card("c1")["notes"])


def test_merge_without_required_review_is_held(prw):
    sha = opened(prw, approve=False)
    prw.gh_state(view=view(sha))
    r = S.sync(prw.board, prw.lead)
    assert r["held"] and "approving review" in r["held"][0]["reason"]
    assert lane(prw) == "review"


def test_same_provider_approval_does_not_count(prw):
    sha = opened(prw, approve=False)
    peer = prw.join("builder-three", provider="anthropic", model="claude-opus-5-5", runtime="claude-code")
    prw.board.review("c1", peer, "approve", "same vendor")
    prw.gh_state(view=view(sha))
    S.sync(prw.board, prw.lead)
    assert lane(prw) == "review"


def _edit_card(prw, fn):
    path = prw.board._p("cards", "c1.json")
    doc = store.read_json(path)
    fn(doc["card"])
    store.write_doc(path, "card", doc["card"])


@pytest.mark.parametrize("attack", ["tamper", "unsigned", "drop"])
def test_merge_with_bad_receipt_is_held(prw, attack):
    sha = opened(prw)

    def forge(card):
        if attack == "tamper":
            card["verifications"][-1]["duration_ms"] += 1
        elif attack == "unsigned":
            card["verifications"][-1].pop("signature")
        else:
            card["verifications"] = []

    _edit_card(prw, forge)
    prw.gh_state(view=view(sha))
    r = S.sync(prw.board, prw.lead)
    assert r["held"] and not r["done"]
    assert lane(prw) == "review"


def test_merged_head_other_than_verified_commit_is_held(prw):
    opened(prw)
    other = "f" * 40
    prw.gh_state(view=view(other))
    r = S.sync(prw.board, prw.lead)
    assert "is not the verified commit" in r["held"][0]["reason"]
    assert lane(prw) == "review"


def test_forged_head_matching_receipt_but_local_head_moved_is_held(prw):
    sha = opened(prw)
    commit(prw.repo, "src/late.py", "L = 1\n", "late")
    prw.gh_state(view=view(sha))
    r = S.sync(prw.board, prw.lead)
    assert "does not match current HEAD" in r["held"][0]["reason"]
    assert lane(prw) == "review"


def test_merge_with_failing_checks_is_held(prw):
    sha = opened(prw)
    prw.gh_state(view=view(sha, checks=(PASS_RUN, FAIL_RUN)))
    r = S.sync(prw.board, prw.lead)
    assert "checks failed: lint" in r["held"][0]["reason"]
    assert lane(prw) == "review"


def test_linked_pr_on_unverified_card_never_closes(prw):
    prw.build(verify=False)
    prw.board.review("c1", prw.reviewer, "approve", "ok")
    S.link(prw.board, prw.lead, "c1", "https://github.com/acme/app/pull/9")
    prw.gh_state(view=view("a" * 40, number=9))
    r = S.sync(prw.board, prw.lead)
    assert r["held"][0]["reason"] == "no verify receipt"
    assert lane(prw) == "review"


def test_card_in_doing_is_not_closed_by_merge(prw):
    prw.build(review=False, verify=False)
    S.link(prw.board, prw.lead, "c1", "7")
    prw.gh_state(view=view("a" * 40))
    r = S.sync(prw.board, prw.lead)
    assert r["held"][0]["reason"] == "card is in doing"
    assert lane(prw) == "doing"


def test_hold_note_written_once_then_advances_after_review(prw):
    sha = opened(prw, approve=False)
    prw.gh_state(view=view(sha))
    S.sync(prw.board, prw.lead)
    S.sync(prw.board, prw.lead, force=True)
    notes = [n["text"] for n in prw.board.card("c1")["notes"] if n["text"].startswith("merged but not done")]
    assert len(notes) == 1
    prw.board.review("c1", prw.reviewer, "approve", "now fine")
    r = S.sync(prw.board, prw.lead, force=True)
    assert r["done"] == ["c1"] and lane(prw) == "done"


@pytest.mark.parametrize(
    "reply",
    [
        view("a" * 40, number=8),
        {"stdout": "not json"},
        {"stdout": "[]"},
        view("a" * 40, state="WEIRD"),
        view("not-a-sha"),
        {"exit": 1, "stderr": "HTTP 404: Not Found"},
    ],
)
def test_bad_gh_output_changes_nothing(prw, reply):
    opened(prw)
    before = prw.board.card("c1")
    prw.gh_state(view=reply)
    r = S.sync(prw.board, prw.lead)
    assert r["errors"] and not r["done"] and not r["synced"]
    after = prw.board.card("c1")
    assert after["lane"] == "review" and after["pr"] == before["pr"]


def test_rate_limit_stops_the_pass(prw):
    sha = opened(prw)
    prw.board.new_card(prw.lead, "Second", card_id="c2", team="core", locks="app/lib")
    S.link(prw.board, prw.lead, "c2", "8")
    prw.gh_state(views=[RATE_LIMIT, view(sha)])
    r = S.sync(prw.board, prw.lead)
    assert r["rate_limited"] and not r["synced"]
    assert json.loads(prw.state_file.read_text())["view_calls"] == 1
    assert lane(prw) == "review"


def test_watch_backs_off_on_rate_limit_and_resets(prw):
    sha = opened(prw)
    prw.gh_state(views=[RATE_LIMIT, RATE_LIMIT, view(sha, state="OPEN"), view(sha, state="OPEN")])
    waits = []
    r = S.watch(prw.board, prw.lead, interval=60, max_polls=4, sleep=waits.append, min_interval=0)
    assert waits == [120, 240, 60]
    assert r["polls"] == 4 and not r["rate_limited"]


def test_backoff_is_capped(prw):
    opened(prw)
    prw.gh_state(views=[RATE_LIMIT])
    waits = []
    S.watch(prw.board, prw.lead, interval=500, max_polls=4, sleep=waits.append, min_interval=0)
    assert waits == [S.MAX_BACKOFF] * 3


def test_min_interval_and_force(prw):
    sha = opened(prw)
    prw.gh_state(view=view(sha, state="OPEN"))
    assert S.sync(prw.board, prw.lead)["synced"]
    assert S.sync(prw.board, prw.lead)["skipped"] == ["c1"]
    assert S.sync(prw.board, prw.lead, force=True)["synced"]
    clock.travel(120)
    assert S.sync(prw.board, prw.lead)["synced"]


def test_closed_pr_recorded_and_not_polled_again(prw):
    sha = opened(prw)
    prw.gh_state(view=view(sha, state="CLOSED"))
    S.sync(prw.board, prw.lead)
    assert prw.board.card("c1")["pr"]["state"] == "CLOSED" and lane(prw) == "review"
    assert S.sync(prw.board, prw.lead, min_interval=0)["skipped"] == ["c1"]


def test_worker_cannot_sync_or_link(prw):
    opened(prw)
    with pytest.raises((Denied, IdentityError, Precondition)):
        S.sync(prw.board, prw.worker)
    with pytest.raises((Denied, IdentityError, Precondition)):
        S.link(prw.board, prw.worker, "c1", "9")


@pytest.mark.parametrize("ref", ["abc", "0", "-1", "https://evil.example/pull/x", "7; rm -rf /", "", "1e9"])
def test_parse_ref_rejects_junk(ref):
    with pytest.raises(Precondition):
        S.parse_ref(ref)


def test_parse_ref_accepts_number_and_url():
    assert S.parse_ref("#12") == {"number": 12, "url": ""}
    assert S.parse_ref("https://github.com/acme/app/pull/3")["number"] == 3


def test_summarize_mixed_rollup():
    rollup = [
        PASS_RUN,
        FAIL_RUN,
        PENDING_RUN,
        {"__typename": "StatusContext", "context": "legacy", "state": "SUCCESS"},
        {"__typename": "StatusContext", "context": "old", "state": "ERROR"},
        {"__typename": "CheckRun", "name": "skip", "status": "COMPLETED", "conclusion": "SKIPPED"},
        "junk",
    ]
    s = S.summarize(rollup)
    assert (s["passed"], s["failed"], s["pending"], s["total"]) == (2, 2, 2, 6)
    assert s["state"] == "failure" and s["failing"] == ["lint", "old"]
    assert S.summarize([])["state"] == "none"
    assert S.summarize(None)["state"] == "none"
    assert S.summarize([PASS_RUN, PENDING_RUN])["state"] == "pending"


def test_sync_unknown_or_unlinked_card(prw):
    prw.card("c3")
    with pytest.raises(Precondition, match="no card"):
        S.sync(prw.board, prw.lead, ids=["nope"])
    with pytest.raises(Precondition, match="has no pull request"):
        S.sync(prw.board, prw.lead, ids=["c3"])


def test_cli_pr_sync_rate_limit_exit(prw, monkeypatch, capsys):
    from raidlock import cli

    opened(prw)
    prw.gh_state(view=RATE_LIMIT)
    monkeypatch.setenv("RAIDLOCK_LEAD_SECRET", prw.lead_token)
    assert cli.main(["pr-sync", "--board", str(prw.ws)]) == 2
    assert "rate limit" in capsys.readouterr().out
    assert cli.main(["pr-link", "c1", "11", "--board", str(prw.ws)]) == 0
    assert prw.board.card("c1")["pr"]["number"] == 11


@pytest.mark.parametrize("race", ["verify", "relink"])
def test_sync_rechecks_receipt_and_identity_under_lock(prw, monkeypatch, race):
    from contextlib import contextmanager

    sha = opened(prw)
    prw.gh_state(view=view(sha))
    original = prw.board.lead_write
    injected = False

    @contextmanager
    def concurrent(caller, action):
        nonlocal injected
        if action == "sync a pull request" and not injected:
            injected = True
            if race == "verify":
                commit(prw.repo, "src/new.py", "NEW = 1\n")
                prw.board.verify("c1", prw.lead, cwd=str(prw.repo))
            else:
                S.link(prw.board, prw.lead, "c1", "8")
        with original(caller, action):
            yield prw.board

    monkeypatch.setattr(prw.board, "lead_write", concurrent)
    result = S.sync(prw.board, prw.lead)
    assert injected and not result["done"] and lane(prw) == "review"
    if race == "verify":
        assert "is not the verified commit" in result["held"][0]["reason"]
    else:
        assert result["errors"] and prw.board.card("c1")["pr"]["number"] == 8


@pytest.mark.parametrize("command", ["view", "create", "git"])
def test_subprocess_diagnostics_redact_secrets_before_truncation(prw, monkeypatch, command):
    import subprocess

    opened(prw)
    opaque = "opaque-service-credential"
    prw.board.env["SERVICE_TOKEN"] = opaque
    secret = "ghp_" + "C" * 340
    diagnostic = "failure " + secret + " " + opaque + " " + prw.worker.token + " " + prw.lead_token
    if command == "view":
        prw.gh_state(view={"exit": 1, "stderr": diagnostic})
        message = S.sync(prw.board, prw.lead)["errors"][0]["error"]
    elif command == "create":
        prw.gh_state(create={"exit": 1, "stderr": diagnostic})
        with pytest.raises(Precondition) as exc:
            P.open_pr(prw.board, prw.lead, "c1")
        message = exc.value.message
    else:
        monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", diagnostic))
        with pytest.raises(Precondition) as exc:
            P.protected(lambda board: P.remote_git(prw.repo, "fetch"))(prw.board)
        message = exc.value.message
    for value in ("ghp_", "C" * 20, opaque, prw.worker.token, prw.lead_token):
        assert value not in message
    assert "[REDACTED]" in message


def test_concurrent_verify_cannot_cross_merged_head_check(prw, monkeypatch):
    import threading

    sha = opened(prw)
    prw.gh_state(view=view(sha))
    checked = threading.Event()
    attempted = threading.Event()
    finished = threading.Event()
    errors = []
    original = S.hold_reason

    def verify():
        if not checked.wait(10):
            return
        attempted.set()
        try:
            with prw.board.lock():
                if lane(prw) != "done":
                    commit(prw.repo, "src/concurrent.py", "NEW = 1\n")
                    prw.board.verify("c1", prw.lead, cwd=str(prw.repo))
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    def check(board, card, info):
        reason = original(board, card, info)
        checked.set()
        assert attempted.wait(5)
        assert not finished.wait(0.2)
        return reason

    monkeypatch.setattr(S, "hold_reason", check)
    thread = threading.Thread(target=verify)
    thread.start()
    try:
        result = S.sync(prw.board, prw.lead)
    finally:
        checked.set()
        thread.join(15)
    assert not thread.is_alive() and not errors
    assert result["done"] == ["c1"]
    assert prw.board.card("c1")["verifications"][-1]["sha"] == sha


@pytest.mark.parametrize("checks", [(), (PENDING_RUN,), ({**PASS_RUN, "conclusion": "NEUTRAL"},)])
def test_merged_pending_missing_neutral_checks_hold(prw, checks):
    sha = opened(prw)
    prw.gh_state(view=view(sha, checks=checks))
    result = S.sync(prw.board, prw.lead)
    assert result["held"] and result["done"] == []
    assert lane(prw) == "review"
