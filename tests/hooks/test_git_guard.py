import json

import pytest

from raidlock import gitguard, hooks

VICTIM = "app/docs/x.md"
MINE = "app/src/main.py"

WORKER_DENIED = [
    ("restore-other", f"git restore {VICTIM}"),
    ("restore-free", "git restore app/free.py"),
    ("restore-quoted", f"git restore '{VICTIM}'"),
    ("restore-split-quote", 'git restore "app"/docs/x.md'),
    ("restore-quoted-git", f"g'i't restore {VICTIM}"),
    ("restore-escaped-git", f"\\git restore {VICTIM}"),
    ("restore-dashdash", f"git restore -- {VICTIM}"),
    ("restore-source", f"git restore --source=HEAD -- {VICTIM}"),
    ("restore-source-short", f"git restore -s HEAD {VICTIM}"),
    ("restore-glob", "git restore 'app/*'"),
    ("restore-magic", f"git restore ':(top){VICTIM}'"),
    ("restore-dir", "git restore app"),
    ("restore-abs-git", f"/usr/bin/git restore {VICTIM}"),
    ("restore-dashed-binary", f"git-restore {VICTIM}"),
    ("restore-var", "git restore $F"),
    ("env-prefix", f"env git restore {VICTIM}"),
    ("env-i", f"env -i PATH=/usr/bin git restore {VICTIM}"),
    ("env-split", f"env -S 'git restore {VICTIM}'"),
    ("command-wrapper", f"command git restore {VICTIM}"),
    ("exec-wrapper", f"exec git restore {VICTIM}"),
    ("nohup-wrapper", f"nohup git restore {VICTIM}"),
    ("time-wrapper", f"time git restore {VICTIM}"),
    ("sudo", f"sudo git restore {VICTIM}"),
    ("timeout", f"timeout 5 git restore {VICTIM}"),
    ("xargs", "echo app/docs/x.md | xargs git restore"),
    ("find-exec", "find app/docs -name '*.md' -exec git restore {} ;"),
    ("sh-c", f'sh -c "git restore {VICTIM}"'),
    ("bash-lc-cd", "bash -lc 'cd app && git restore docs/x.md'"),
    ("sh-nested", f"sh -c \"bash -c 'git restore {VICTIM}'\""),
    ("eval", f'eval "git restore {VICTIM}"'),
    ("subst", f"echo $(git restore {VICTIM})"),
    ("subst-quoted", f'echo "$(git restore {VICTIM})"'),
    ("backticks", f"echo `git restore {VICTIM}`"),
    ("process-subst", f"cat <(git restore {VICTIM})"),
    ("and-chain", f"true && git restore {VICTIM}"),
    ("semicolon", f"ls; git restore {VICTIM}"),
    ("pipe", f"ls | git restore {VICTIM}"),
    ("newline", f"ls\ngit restore {VICTIM}"),
    ("cd-relative", "cd app && git restore docs/x.md"),
    ("subshell-cd-reset", "cd app && (cd src) && git restore docs/x.md"),
    ("brace-group", f"{{ git restore {VICTIM}; }}"),
    ("if-block", f"if true; then git restore {VICTIM}; fi"),
    ("stdin-shell", f"echo 'git restore {VICTIM}' | sh"),
    ("here-string-shell", f"bash <<< 'git restore {VICTIM}'"),
    ("heredoc-shell", f"bash <<EOF\ngit restore {VICTIM}\nEOF"),
    ("python", f"python3 -c \"import os; os.system('git restore {VICTIM}')\""),
    ("unknown-launcher", f"mytool git restore {VICTIM}"),
    ("checkout-dashdash", f"git checkout -- {VICTIM}"),
    ("checkout-rev-path", f"git checkout HEAD~1 {VICTIM}"),
    ("checkout-rev-dashdash", f"git checkout HEAD -- {VICTIM}"),
    ("checkout-dot", "git checkout ."),
    ("checkout-branch", "git checkout main"),
    ("switch", "git switch main"),
    ("reset-hard", "git reset --hard"),
    ("reset-hard-rev", "git reset --hard HEAD~1"),
    ("reset-mixed", "git reset HEAD~1"),
    ("reset-path", f"git reset -- {VICTIM}"),
    ("clean", "git clean -fd"),
    ("clean-path", "git clean -f app/docs"),
    ("stash", "git stash"),
    ("stash-push", "git stash push"),
    ("stash-pop", "git stash pop"),
    ("stash-apply", "git stash apply"),
    ("stash-path", f"git stash push -- {VICTIM}"),
    ("apply", "git apply p.patch"),
    ("apply-stdin", "cat p.patch | git apply"),
    ("am", "git am p.patch"),
    ("rm", f"git rm {VICTIM}"),
    ("rm-cached", f"git rm --cached {VICTIM}"),
    ("mv", f"git mv {MINE} app/docs/y.md"),
    ("merge", "git merge main"),
    ("rebase", "git rebase main"),
    ("pull", "git pull"),
    ("cherry-pick", "git cherry-pick abc1234"),
    ("revert", "git revert abc1234"),
    ("commit-a", "git commit -a -m x"),
    ("commit-am", "git commit -am x"),
    ("commit-all", "git commit --all -m x"),
    ("commit-path", f"git commit -m x {VICTIM}"),
    ("add-other", f"git add {VICTIM}"),
    ("add-all", "git add -A"),
    ("add-dot", "git add ."),
    ("alias", "git co main"),
    ("unknown-sub", "git update-index --assume-unchanged app/docs/x.md"),
    ("diff-output", f"git diff --output={VICTIM}"),
    ("diff-output-space", f"git diff --output {VICTIM}"),
    ("log-output", f"git log --output={VICTIM}"),
    ("show-output", f"git show --output={VICTIM} HEAD"),
    ("sub-from-var", "git $SUB app/docs/x.md"),
    ("snapshot-out", f"RAIDLOCK_AGENT=w-one raidlock snapshot --out {VICTIM}"),
]

FORBIDDEN = [
    ("c-ssh", "git -c core.sshCommand=evil fetch origin"),
    ("c-ssh-quoted", "git -c 'core.sshCommand=touch /tmp/x' fetch origin"),
    ("c-fsmonitor-status", 'git -c core.fsmonitor="touch /tmp/x" status'),
    ("c-glued", "git -ccore.fsmonitor=x status"),
    ("c-alias", "git -c alias.st='!sh -c evil' st"),
    ("c-after-C", "git -C app -c core.pager=evil log"),
    ("c-in-sh", 'sh -c "git -c core.sshCommand=evil fetch"'),
    ("c-in-subst", "echo $(git -c core.fsmonitor=x status)"),
    ("config-env", "git --config-env=core.sshCommand=E fetch"),
    ("exec-path", "git --exec-path=/tmp/evil status"),
    ("exec-path-bare", "git --exec-path status"),
    ("git-dir", "git --git-dir=/tmp/other/.git status"),
    ("work-tree", "git --work-tree=/tmp/other restore x"),
    ("namespace", "git --namespace=x status"),
    ("C-outside", "git -C /tmp status"),
    ("C-parent", "git -C ../.. status"),
    ("env-git-dir", "GIT_DIR=/tmp/x git status"),
    ("env-ssh", "GIT_SSH_COMMAND=evil git fetch"),
    ("env-wrapper-git", "env GIT_CONFIG_PARAMETERS=\"'core.fsmonitor'='x'\" git status"),
    ("env-exported", "export GIT_SSH_COMMAND=evil; git fetch"),
    ("env-exported-and", "export GIT_EXTERNAL_DIFF=evil && git diff"),
    ("env-bare-assign", "GIT_PAGER=evil; export GIT_PAGER; git log"),
    ("ext-transport", "git fetch 'ext::sh -c touch% /tmp/pwned' main"),
    ("ext-url-pull", "git pull ext::evil"),
    ("fd-transport", "git fetch fd::3"),
    ("remote-ext", "git remote add x 'ext::sh -c evil'"),
    ("upload-pack", "git fetch --upload-pack='touch /tmp/x' ."),
    ("upload-pack-short", "git fetch -u 'touch /tmp/x' ."),
    ("receive-pack", "git push --receive-pack=evil origin"),
    ("grep-O", "git grep -O'touch /tmp/x' foo"),
    ("grep-open-files", "git grep --open-files-in-pager=evil foo"),
    ("rebase-x", "git rebase -x 'touch /tmp/x' main"),
    ("diff-ext", "git diff --ext-diff"),
    ("difftool", "git difftool -x evil"),
    ("bisect-run", "git bisect run evil"),
    ("submodule-foreach", "git submodule foreach evil"),
    ("config-write", "git config core.sshCommand evil"),
    ("config-hookspath", "git config core.hooksPath /tmp/hooks"),
]

WORKER_ALLOWED = [
    ("status", "git status"),
    ("status-short", "git status --short"),
    ("diff", "git diff"),
    ("diff-cached", "git diff --cached app/src"),
    ("diff-orderfile", "git diff -Oorder.txt"),
    ("log", "git log --oneline -5"),
    ("log-n", "git log -n 3"),
    ("show", "git show HEAD"),
    ("rev-parse", "git rev-parse HEAD"),
    ("branch", "git branch"),
    ("branch-list", "git branch --list"),
    ("branch-list-pattern", "git branch --list 'feat/*'"),
    ("branch-a", "git branch -a"),
    ("C-inside", "git -C app status"),
    ("no-pager", "git --no-pager log -1"),
    ("ls-files", "git ls-files"),
    ("config-get", "git config --get user.name"),
    ("restore-mine", f"git restore {MINE}"),
    ("restore-mine-cd", "cd app && git restore src/main.py"),
    ("restore-mine-dashdash", f"git restore -- {MINE}"),
    ("restore-own-dir", "git restore app/src"),
    ("checkout-mine", f"git checkout -- {MINE}"),
    ("add-mine", f"git add {MINE}"),
    ("commit", "git commit -m 'fix: thing'"),
    ("commit-heredoc", "git commit -m \"$(cat <<'EOF'\nfix: it's done\n\nbody\nEOF\n)\""),
    ("commit-mine", f"git commit -m x -- {MINE}"),
    ("fetch", "git fetch origin"),
    ("push", "git push origin HEAD"),
    ("branch-create", "git checkout -b feature"),
    ("switch-create", "git switch -c feature"),
    ("stash-list", "git stash list"),
    ("clean-dry", "git clean -n"),
    ("note-mentions-git", 'RAIDLOCK_AGENT=w-one raidlock note c1 "ran git restore on it"'),
    ("grep-word-git", "grep -rn git app/src"),
]


def worker(scene, command):
    scene.bind_worker()
    base = json.loads(scene.fixture("pre_bash_subagent_done.json"))
    base["tool_input"]["command"] = command
    return hooks.run("pre-bash", json.dumps(base), dict(scene.env))


def main_session(scene, command):
    base = json.loads(scene.fixture("pre_bash_plain.json"))
    base["tool_input"]["command"] = command
    return hooks.run("pre-bash", json.dumps(base), dict(scene.env))


def decision(out):
    return None if out is None else out["hookSpecificOutput"]["permissionDecision"]


def files(scene):
    for rel in (VICTIM, MINE, "app/free.py"):
        path = scene.ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")


@pytest.mark.parametrize("command", [c for _, c in WORKER_DENIED], ids=[i for i, _ in WORKER_DENIED])
def test_worker_mutation_outside_lease_denied(scene, command):
    files(scene)
    assert decision(worker(scene, command)) == "deny"


@pytest.mark.parametrize("command", [c for _, c in FORBIDDEN], ids=[i for i, _ in FORBIDDEN])
def test_git_config_and_exec_overrides_denied(scene, command):
    files(scene)
    assert decision(worker(scene, command)) == "deny"
    assert decision(main_session(scene, command)) == "deny"


@pytest.mark.parametrize("command", [c for _, c in WORKER_ALLOWED], ids=[i for i, _ in WORKER_ALLOWED])
def test_worker_reads_and_own_lease_pass(scene, command):
    files(scene)
    out = worker(scene, command)
    if gitguard.analyze(command, [str(scene.ws)]):
        assert out is None, out
    else:
        assert out is None


@pytest.mark.parametrize(
    "command",
    [c for _, c in WORKER_DENIED + FORBIDDEN + WORKER_ALLOWED],
    ids=[i for i, _ in WORKER_DENIED + FORBIDDEN + WORKER_ALLOWED],
)
def test_git_is_never_preapproved(scene, command):
    files(scene)
    assert decision(worker(scene, command)) != "allow"


def test_reported_exploits_denied(scene):
    files(scene)
    assert "held by w-two" in worker(scene, f"git restore {VICTIM}")["hookSpecificOutput"]["permissionDecisionReason"]
    assert (
        "-c"
        in worker(scene, "git -c core.sshCommand=evil fetch origin")["hookSpecificOutput"]["permissionDecisionReason"]
    )


def test_main_session_respects_other_leases(scene):
    files(scene)
    assert decision(main_session(scene, f"git restore {MINE}")) == "deny"
    assert decision(main_session(scene, "git reset --hard")) == "deny"
    assert decision(main_session(scene, "git stash pop")) == "deny"
    assert "require_card_for_lead_edits" in str(main_session(scene, "git restore app/free.py"))
    assert main_session(scene, "git status") is None
    assert main_session(scene, "git commit -m x") is None


def test_whole_repo_lease_unlocks_tree_wide_commands(scene):
    files(scene)
    lead = scene.lead
    scene.board.release(
        "c2",
        scene.board.caller(
            agent="w-two", token=scene.tokens["w-two"], fence=scene.board.leases()["c2"]["fence"], env={}
        ),
    )
    scene.board.release("c1", scene.caller("w-one", scene.fence))
    scene.board.new_card(lead, "c3", card_id="c3", team="core", locks="app")
    fence = scene.board.claim("c3", scene.caller("w-one"))["fence"]
    data = json.loads(scene.fixture("post_claim.json"))
    data["tool_input"]["command"] = f"RAIDLOCK_AGENT=w-one RAIDLOCK_TOKEN={scene.tokens['w-one']} raidlock claim c3"
    data["tool_response"]["stdout"] = f"[ws] Claimed c3: lock app, fence {fence}.\nexport RAIDLOCK_FENCE={fence}"
    hooks.run("post-tool", json.dumps(data), dict(scene.env))
    base = json.loads(scene.fixture("pre_bash_subagent_done.json"))
    for command in (
        "git reset --hard",
        "git checkout .",
        "git clean -fd",
        "git stash pop",
        f"git restore {VICTIM}",
        "git merge main",
        "git commit -am x",
    ):
        base["tool_input"]["command"] = command
        assert hooks.run("pre-bash", json.dumps(base), dict(scene.env)) is None, command
    base["tool_input"]["command"] = "git -c core.sshCommand=evil fetch"
    assert decision(hooks.run("pre-bash", json.dumps(base), dict(scene.env))) == "deny"


def test_no_board_is_silent_for_git(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    data = {
        "session_id": "s9",
        "cwd": str(outside),
        "agent_id": "a",
        "agent_type": "raidlock:raid-worker",
        "tool_name": "Bash",
        "tool_input": {"command": "git -c x=y reset --hard"},
    }
    assert hooks.run("pre-bash", json.dumps(data), {"RAIDLOCK_HOME": str(tmp_path / "home")}) is None


def test_board_allow_has_no_output(scene):
    out = worker(scene, "RAIDLOCK_AGENT=w-one raidlock note c1 x")
    assert out is None


@pytest.mark.parametrize(
    "command",
    [
        "cd app/docs && printf corrupted > x.md",
        "cd app/docs; printf corrupted > x.md",
        "pushd app/docs && tee x.md",
        "(cd app/docs && cp ../src/main.py x.md)",
        "cd app/docs && mv x.md ../src/stolen.md",
        "cd app/docs && rm x.md",
        "cd app/src && (cd ../docs && touch x.md)",
        "cd $TARGET && printf corrupted > main.py",
        "pushd app/docs; popd; rm x.md",
        "sh -c 'cd app/docs && tee x.md'",
    ],
)
def test_compound_write_targets_use_effective_directory(scene, command):
    files(scene)
    assert decision(worker(scene, command)) == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "git --icase-pathspecs restore app/src/main.py",
        "git --literal-pathspecs --glob-pathspecs restore app/src/main.py",
        "git --glob-pathspecs --literal-pathspecs restore app/src/main.py",
        "git restore ':(icase)app/src/main.py'",
        "git restore ':(glob)app/src/*'",
        "git restore ':/app/src/main.py'",
    ],
)
def test_pathspec_modes_cannot_broaden_lease(scene, command):
    files(scene)
    (scene.ws / "app/src/MAIN.py").write_text("victim")
    assert decision(worker(scene, command)) == "deny"
