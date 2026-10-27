import json
import re

import pytest
from prhelpers import commit, git

from raidlock import pr as P
from raidlock.core import Denied, IdentityError, Precondition, store

SECRET = "AKIA" + "ABCDEFGHIJKLMNOP"


def test_opens_pr_from_verified_commit(prw):
    card = prw.build()
    sha = card["verifications"][-1]["sha"]
    r = P.open_pr(prw.board, prw.lead, "c1")
    assert not r["dry_run"]
    assert prw.remote_branches()["raid/c1"] == sha
    create = prw.calls(["pr", "create"])
    assert len(create) == 1
    argv = create[0]["argv"]
    assert argv[argv.index("--head") + 1] == "raid/c1"
    assert argv[argv.index("--base") + 1] == "main"
    body = create[0]["body"]
    for text in ("builder-one", "claude-opus-5-5", "anthropic", "claude-code", sha, "src/parser.py", "`check`"):
        assert text in body
    assert "Outside locks: 0" in body and "Patch scan findings: 0" in body
    assert "signature" not in json.dumps(r["lock_check"])
    saved = prw.board.card("c1")["pr"]
    assert saved["number"] == 7 and saved["sha"] == sha and saved["branch"] == "raid/c1"
    assert any("pull request #7" in n["text"] for n in prw.board.card("c1")["notes"])


def test_body_has_no_local_paths_or_receipt_signature(prw):
    card = prw.build()
    P.open_pr(prw.board, prw.lead, "c1")
    body = prw.calls(["pr", "create"])[0]["body"]
    assert str(prw.tmp) not in body and "/Users/" not in body
    assert card["verifications"][-1]["signature"] not in body


def test_gh_gets_no_board_credentials(prw, monkeypatch):
    prw.build()
    monkeypatch.setenv("RAIDLOCK_LEAD_SECRET", "s3cret-value")
    prw.board.env["RAIDLOCK_LEAD_SECRET"] = "s3cret-value"
    prw.board.env["RAIDLOCK_TOKEN"] = "tok"
    P.open_pr(prw.board, prw.lead, "c1")
    assert all(not call["raidlock_env"] for call in prw.calls())


def test_dry_run_when_gh_not_authenticated(prw):
    prw.build()
    prw.gh_state(auth=False)
    r = P.open_pr(prw.board, prw.lead, "c1")
    assert r["dry_run"] and not r["gh_ready"]
    assert "raid/c1" not in prw.remote_branches()
    assert not prw.calls(["pr", "create"])
    assert "pr" not in prw.board.card("c1")
    assert "Lock check" in r["body"]


def test_explicit_dry_run_pushes_nothing(prw):
    prw.build()
    r = P.open_pr(prw.board, prw.lead, "c1", dry_run=True)
    assert r["dry_run"] and r["gh_ready"]
    assert "raid/c1" not in prw.remote_branches()
    assert not prw.calls(["pr", "create"])


def test_gh_missing_means_dry_run(prw, monkeypatch):
    prw.build()
    real = P.exe.find
    monkeypatch.setattr(P.exe, "find", lambda name: str(prw.tmp / "missing-gh") if name == "gh" else real(name))
    r = P.open_pr(prw.board, prw.lead, "c1")
    assert r["dry_run"] and not r["gh_ready"]


def test_worker_may_dry_run_but_not_open(prw):
    prw.build()
    assert P.open_pr(prw.board, prw.worker, "c1", dry_run=True)["dry_run"]
    with pytest.raises((Denied, IdentityError, Precondition)):
        P.open_pr(prw.board, prw.worker, "c1")
    assert "raid/c1" not in prw.remote_branches()
    assert not prw.calls(["pr", "create"])


def _refused(prw, match, **kw):
    with pytest.raises(Precondition, match=match):
        P.open_pr(prw.board, prw.lead, "c1", **kw)
    assert "raid/c1" not in prw.remote_branches()
    assert not prw.calls(["pr", "create"])
    assert "pr" not in prw.board.card("c1")


def test_refuses_without_receipt(prw):
    prw.build(verify=False)
    _refused(prw, "valid verification receipt")


def test_refuses_failed_receipt(prw, monkeypatch):
    from raidlock.runners import isolation

    monkeypatch.setattr(isolation, "run", lambda *a, **k: (1, "boom", 5))
    prw.build()
    _refused(prw, "failed")


def _edit_card(prw, cid, fn):
    path = prw.board._p("cards", cid + ".json")
    doc = store.read_json(path)
    fn(doc["card"])
    store.write_doc(path, "card", doc["card"])


@pytest.mark.parametrize(
    "attack",
    ["tamper-sha", "tamper-exit", "drop-signature", "other-command", "unsigned-runner-receipt"],
)
def test_refuses_forged_receipts(prw, attack):
    prw.build()
    other = commit(prw.repo, "src/extra.py", "X = 2\n", "unverified")

    def forge(card):
        last = card["verifications"][-1]
        if attack == "tamper-sha":
            last["sha"] = other
        elif attack == "tamper-exit":
            last["exit"], last["passed"] = 0, True
            last["cmd"] = "true"
        elif attack == "drop-signature":
            last.pop("signature")
        elif attack == "other-command":
            card["verify_cmd"] = "other-check"
        else:
            card["verifications"].append(
                {"cmd": "check", "exit": 0, "passed": True, "sha": other, "isolation": "unsafe", "rev": card["rev"]}
            )

    _edit_card(prw, "c1", forge)
    _refused(prw, "receipt|does not match")


def test_refuses_when_head_moved_after_verify(prw):
    prw.build()
    commit(prw.repo, "src/later.py", "LATER = 1\n", "after verify")
    _refused(prw, "does not match current HEAD")


def test_refuses_card_in_doing(prw):
    prw.build(review=False, verify=False)
    _refused(prw, "in doing")


def test_refuses_changes_outside_locks(prw):
    prw.build(files={"src/parser.py": "A = 1\n", "docs/notes.md": "outside\n"})
    _refused(prw, "outside the card's locks: docs/notes.md")


def test_refuses_denied_paths_even_inside_locks(prw):
    prw.build(locks="app", files={".github/workflows/ci.yml": "on: push\n"})
    _refused(prw, "denied paths")


def test_refuses_secret_in_patch(prw):
    prw.build(files={"src/config.py": f'KEY = "{SECRET}"\n'})
    _refused(prw, "patch safety findings: .*aws")


def test_refuses_diverged_remote_branch(prw):
    prw.build()
    git(prw.repo, "push", "--quiet", "origin", "HEAD~1:refs/heads/raid/c1")
    other = prw.tmp / "other"
    git(prw.tmp, "clone", "--quiet", str(prw.remote), str(other))
    git(other, "checkout", "--quiet", "raid/c1")
    commit(other, "src/foreign.py", "F = 1\n", "foreign")
    git(other, "push", "--quiet", "origin", "raid/c1")
    with pytest.raises(Precondition, match="push to raid/c1 was refused"):
        P.open_pr(prw.board, prw.lead, "c1")
    assert not prw.calls(["pr", "create"])
    assert "pr" not in prw.board.card("c1")


def test_existing_pr_is_linked(prw):
    prw.build()
    prw.gh_state(
        create={
            "exit": 1,
            "stderr": 'a pull request for branch "raid/c1" into branch "main" already exists:\n'
            "https://github.com/acme/app/pull/12\n",
        }
    )
    r = P.open_pr(prw.board, prw.lead, "c1")
    assert r["pr"]["number"] == 12 and r["pr"]["existing"]


def test_gh_failure_records_nothing(prw):
    prw.build()
    prw.gh_state(create={"exit": 1, "stderr": "GraphQL: something broke"})
    with pytest.raises(Precondition, match="gh pr create failed"):
        P.open_pr(prw.board, prw.lead, "c1")
    assert "pr" not in prw.board.card("c1")


def _approve(prw, note):
    prw.board.review("c1", prw.reviewer, "approve", note)


NAMES = ("builder-one", "checker-two", "claude-opus-5-5", "gpt-5.5", "anthropic", "openai", "claude-code", "codex-cli")


def test_redaction_all_scrubs_title_body_and_reviews(prw):
    prw.build(title="Parser from builder-one on claude-opus-5-5 via Codex")
    _approve(prw, "checker-two ran gpt-5.5 and Claude agrees")
    prw.board.set_setting(prw.lead, "pr.redact", "all")
    P.open_pr(prw.board, prw.lead, "c1")
    call = prw.calls(["pr", "create"])[0]
    title = call["argv"][call["argv"].index("--title") + 1]
    text = (title + "\n" + call["body"]).lower()
    for name in (*NAMES, "claude", "codex", "gpt"):
        assert not re.search(r"(?<![a-z0-9_])" + re.escape(name.lower()) + r"(?![a-z0-9_])", text), name
    assert "### Agent" not in call["body"]
    assert "Approvals: 1" in call["body"]
    assert "[redacted]" in title


def test_redact_agent_only_keeps_model(prw):
    prw.build()
    _approve(prw, "fine")
    r = P.open_pr(prw.board, prw.lead, "c1", dry_run=True, redact="agent")
    assert "builder-one" not in r["body"] and "checker-two" not in r["body"]
    assert "claude-opus-5-5" in r["body"]


def test_flag_cannot_loosen_board_redaction(prw):
    prw.build()
    prw.board.set_setting(prw.lead, "pr.redact", "agent,model")
    r = P.open_pr(prw.board, prw.lead, "c1", dry_run=True, redact="none")
    assert r["redact"] == ["agent", "model"]
    assert "builder-one" not in r["body"] and "claude-opus-5-5" not in r["body"]


def test_custom_terms_are_scrubbed(prw):
    prw.build(title="Parser for Project Falcon")
    prw.board.set_setting(prw.lead, "pr.terms", "Project Falcon")
    r = P.open_pr(prw.board, prw.lead, "c1", dry_run=True)
    assert "Falcon" not in r["title"] and "Falcon" not in r["body"]


def test_redaction_refuses_commit_message_with_redacted_name(prw):
    prw.build(message="parser written by builder-one")
    prw.board.set_setting(prw.lead, "pr.redact", "agent")
    _refused(prw, "commit messages or authors contain a redacted term")


def test_redaction_refuses_branch_with_redacted_name(prw):
    prw.build()
    prw.board.set_setting(prw.lead, "pr.redact", "model")
    _refused(prw, "branch name contains a redacted term", branch="raid/claude-c1")


@pytest.mark.parametrize("branch", ["-x", "../x", "a..b", "raid/c1.", "a b", "x@{1}", "a//b"])
def test_rejects_unsafe_branch_names(prw, branch):
    prw.build()
    _refused(prw, "plain branch name", branch=branch)


@pytest.mark.parametrize(
    "key,value",
    [
        ("pr.redact", "agents"),
        ("pr.redact", "agent,email"),
        ("pr.terms", "x"),
        ("pr.terms", "bad\nterm"),
        ("pr.base", "-main"),
        ("pr.base", "main..dev"),
        ("pr.remote", "--upload-pack=evil"),
    ],
)
def test_settings_validated(prw, key, value):
    with pytest.raises(Precondition):
        prw.board.set_setting(prw.lead, key, value)


def test_settings_round_trip_and_clear(prw):
    prw.board.set_setting(prw.lead, "pr.redact", "model, agent")
    assert prw.board.get_setting("pr.redact") == ["agent", "model"]
    prw.board.set_setting(prw.lead, "pr.redact", "none")
    assert prw.board.get_setting("pr.redact") == ""
    prw.board.set_setting(prw.lead, "pr.base", "develop")
    assert P.config(prw.board)["base"] == "develop"


def test_base_from_setting_and_remote_head(prw):
    git(prw.repo, "push", "--quiet", "origin", "main:refs/heads/develop")
    prw.build()
    assert P.open_pr(prw.board, prw.lead, "c1", dry_run=True)["base"] == "main"
    prw.board.set_setting(prw.lead, "pr.base", "develop")
    assert P.open_pr(prw.board, prw.lead, "c1", dry_run=True)["base"] == "develop"


def test_missing_base_branch_refused(prw):
    prw.build()
    with pytest.raises(Precondition, match="git fetch"):
        P.open_pr(prw.board, prw.lead, "c1", dry_run=True, base="nope")


def test_unknown_remote_refused(prw):
    prw.build()
    with pytest.raises(Precondition, match="no remote upstream"):
        P.open_pr(prw.board, prw.lead, "c1", dry_run=True, remote="upstream")


def test_planted_hooks_in_verified_checkout_do_not_run(prw):
    prw.build()
    hooks = prw.repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    marker = prw.tmp / "hook-ran"
    for name in ("pre-push", "post-checkout", "reference-transaction"):
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook.chmod(0o755)
    P.open_pr(prw.board, prw.lead, "c1")
    assert not marker.exists()


def test_cli_dry_run_and_show(prw, monkeypatch, capsys):
    from raidlock import cli

    prw.build()
    monkeypatch.setenv("RAIDLOCK_LEAD_SECRET", prw.lead_token)
    assert cli.main(["pr", "c1", "--dry-run", "--json", "--board", str(prw.ws)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["dry_run"] and out["branch"] == "raid/c1"
    assert cli.main(["pr", "c1", "--board", str(prw.ws)]) == 0
    assert "Opened pull request #7" in capsys.readouterr().out
    assert cli.main(["show", "c1", "--board", str(prw.ws)]) == 0
    assert "pr: PR #7 open" in capsys.readouterr().out
    assert cli.main(["show", "--board", str(prw.ws)]) == 0
    assert "(PR #7 open)" in capsys.readouterr().out


def test_cli_refusal_exit_code(prw, monkeypatch, capsys):
    from raidlock import cli

    prw.build(verify=False)
    monkeypatch.setenv("RAIDLOCK_LEAD_SECRET", prw.lead_token)
    assert cli.main(["pr", "c1", "--board", str(prw.ws)]) == 3
    assert "valid verification receipt" in capsys.readouterr().err


@pytest.mark.parametrize("attack", ["outside", "denied", "secret", "merge"])
def test_rejects_unsafe_intermediate_history(prw, attack):
    prw.build()
    if attack == "merge":
        git(prw.repo, "checkout", "-b", "side", "HEAD~1")
    path = {"outside": "docs/leak.txt", "denied": "src/.env", "secret": "src/leak.txt", "merge": "src/leak.txt"}[attack]
    commit(prw.repo, path, SECRET if attack in ("secret", "merge") else "unsafe\n")
    git(prw.repo, "rm", path)
    git(prw.repo, "commit", "-m", "remove unsafe file")
    if attack == "merge":
        git(prw.repo, "checkout", "main")
        git(prw.repo, *P.W.IDENT, "merge", "--no-ff", "side", "-m", "merge side")
    commit(prw.repo, "src/parser.py", "VALUE = 2\n")
    prw.board.verify("c1", prw.lead, cwd=str(prw.repo))
    _refused(prw, "outside|denied|patch safety")


@pytest.mark.parametrize("field", ["message", "author-name", "author-email", "committer-name", "committer-email"])
def test_rejects_secret_commit_metadata(prw, field):
    prw.build()
    token = "ghp_" + "A" * 36
    args = [
        "-c",
        "user.name=" + (token if field == "committer-name" else "Tester"),
        "-c",
        "user.email=" + (token + "@example.test" if field == "committer-email" else "test@example.test"),
    ]
    author = (
        (token if field == "author-name" else "Tester")
        + " <"
        + (token if field == "author-email" else "test")
        + "@example.test>"
    )
    git(prw.repo, *args, "commit", "--allow-empty", "--author", author, "-m", token if field == "message" else "safe")
    sha = git(prw.repo, "rev-parse", "HEAD")
    prw.board.verify("c1", prw.lead, cwd=str(prw.repo))
    _refused(prw, "commit metadata contains a secret")
    assert git(prw.repo, "rev-parse", "HEAD") == sha


@pytest.mark.parametrize("surface", ["render", "branch", "commit"])
def test_redaction_blocks_embedded_and_unicode_terms(prw, surface):
    text = "builder_one ＯｐｅｎＡＩ Open\u200bAI"
    prw.build(title=text, message=text if surface == "commit" else "safe")
    prw.board.set_setting(prw.lead, "pr.terms", "builder")
    prw.board.set_setting(prw.lead, "pr.redact", "provider")
    if surface == "render":
        result = P.open_pr(prw.board, prw.lead, "c1", dry_run=True)
        assert "builder" not in result["title"] and "OpenAI" not in result["title"]
        assert "Ｏ" not in result["body"] and "\u200b" not in result["body"]
    else:
        _refused(prw, "redacted term", **({"branch": "raid/builder_one"} if surface == "branch" else {}))


def test_redacts_all_assembled_metadata_secrets(prw, monkeypatch):
    opaque = "opaque-environment-credential"
    prw.board.env["SERVICE_PASSWORD"] = opaque
    card = prw.build(title="Title " + opaque)
    card["owner"] = ""
    check = {"changed": [], "outside": [], "denied": [], "findings": []}
    token = "ghp_" + "B" * 36
    title, body, _ = P.render(
        prw.board,
        card,
        card["verifications"][-1],
        check,
        P.config(prw.board, terms="ghp"),
        {"model": token, "runtime": prw.worker.token, "provider": prw.lead_token},
    )
    for secret in (opaque, token, prw.worker.token, prw.lead_token):
        assert secret not in title + body
    assert "B" * 36 not in body
    assert "[REDACTED]" in title and "[REDACTED]" in body


@pytest.mark.parametrize("change", ["handover", "card", "receipt"])
def test_pr_revalidates_after_preparation_before_outward_actions(prw, monkeypatch, change):
    from contextlib import contextmanager

    from raidlock.core.errors import RaidlockError

    prw.build()
    original = P._changes

    @contextmanager
    def delayed(*args, **kwargs):
        with original(*args, **kwargs) as prepared:
            if change == "handover":
                prw.board.lead_handover(prw.lead, "next-session")
            else:
                with prw.board.lead_write(prw.lead, "change prepared state"):
                    card = prw.board.card("c1")
                    if change == "card":
                        card["title"] = "changed while preparing"
                    else:
                        card["verifications"] = []
                    prw.board._save_card(card)
            yield prepared

    monkeypatch.setattr(P, "_changes", delayed)
    with pytest.raises(RaidlockError):
        P.open_pr(prw.board, prw.lead, "c1", dry_run=False)
    assert "raid/c1" not in prw.remote_branches()
    assert not prw.calls(["pr", "create"])
    assert "pr" not in prw.board.card("c1")


def test_pr_holds_board_lock_through_push_create_and_record(prw, monkeypatch):
    from raidlock.core.lock import _held

    prw.build()
    git_call, create, save = P.remote_git, P._create, prw.board._save_card
    seen = []

    def locked(name):
        assert any(path == prw.board._p(".lock") for path, _ in _held)
        seen.append(name)

    def push(cwd, *args, **kwargs):
        if "push" in args:
            locked("push")
        return git_call(cwd, *args, **kwargs)

    def creating(*args, **kwargs):
        locked("create")
        return create(*args, **kwargs)

    def saving(card):
        locked("record")
        return save(card)

    monkeypatch.setattr(P, "remote_git", push)
    monkeypatch.setattr(P, "_create", creating)
    monkeypatch.setattr(prw.board, "_save_card", saving)
    P.open_pr(prw.board, prw.lead, "c1", dry_run=False)
    assert seen == ["push", "create", "record"]


def test_pr_accepts_remote_less_clone_with_lead_provenance(prw):
    from raidlock.runners import workspace as W

    prw.card()
    clone = str(prw.tmp / "runner-clone")
    W.clone(str(prw.repo), clone)
    prw.board.trust_checkout(prw.lead, "c1", clone, str(prw.repo))
    grant = prw.board.claim("c1", prw.worker, checkout=clone)
    prw.worker.fence = grant["fence"]
    sha = commit(clone, "src/task.py", "VALUE = 1\n")
    prw.board.move("c1", "review", prw.worker)
    receipt = prw.board.verify("c1", prw.lead, cwd=clone)
    assert receipt["sha"] == sha
    result = P.open_pr(prw.board, prw.lead, "c1", dry_run=False)
    assert result["pr"]["sha"] == sha
    assert prw.remote_branches()["raid/c1"] == sha


@pytest.mark.parametrize("target", ["../.github/workflows", "../docs/private", "/etc/passwd", "../../outside"])
def test_pr_rejects_symlink_target(prw, target):
    prw.build(verify=False)
    (prw.repo / "src" / "link").symlink_to(target)
    git(prw.repo, "add", "src/link")
    git(prw.repo, "commit", "--quiet", "-m", "link")
    prw.board.verify("c1", prw.lead, cwd=str(prw.repo))
    with pytest.raises(Precondition):
        P.open_pr(prw.board, prw.lead, "c1")
    assert not prw.calls(["pr", "create"])
