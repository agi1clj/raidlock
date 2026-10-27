import contextlib
import http.server
import json
import os
import select
import shlex
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from portable import SH, assert_mode
from runhelpers import fake, git

from raidlock.core.errors import Precondition
from raidlock.runners import isolation
from raidlock.runners import workspace as W
from raidlock.runners.base import Redactor
from raidlock.runners.runner import delegate

DONE = '{"status": "done", "evidence": ["ok"], "paths_changed": [], "open_questions": []}'
SANDBOX = isolation.mode()


def go(ws, cid, **kw):
    kw.setdefault("heartbeat", 0.05)
    return delegate(ws.board, ws.lead, cid, "shell", **kw)


def test_rotated_lead_cannot_record_a_runner_verification(ws, monkeypatch):
    from raidlock.core.errors import StaleLead

    ws.card("api", verify_cmd="echo ok")
    monkeypatch.setattr(isolation, "mode", lambda: "sandbox-exec")

    def finish_after_handover(*args, **kwargs):
        ws.board.lead_handover(ws.lead, "next-session")
        return 0, "ok", 1

    monkeypatch.setattr(isolation, "run", finish_after_handover)
    with pytest.raises(StaleLead):
        go(ws, "api", cmd=fake("--say", DONE))
    assert ws.board.card("api")["verifications"] == []


def test_rotated_lead_cannot_start_a_runner_verification(ws, monkeypatch):
    from types import SimpleNamespace

    from raidlock.core.errors import StaleLead
    from raidlock.runners.runner import _verify

    ws.card("api", verify_cmd="echo ok")
    ws.board.lead_handover(ws.lead, "next-session")
    invoked = []
    monkeypatch.setattr(isolation, "run", lambda *args, **kwargs: invoked.append(args))
    with pytest.raises(StaleLead):
        _verify(ws.board, ws.lead, SimpleNamespace(card=ws.board.card("api")))
    assert invoked == []


def sh(script):
    return shlex.join([SH, "-c", script])


def py(code):
    return shlex.join([sys.executable, "-c", code])


def only_run(ws, cid):
    root = os.path.join(os.environ["RAIDLOCK_HOME"], "runs", "ws", cid)
    return os.path.join(root, max(os.listdir(root), key=int))


def lead_env(ws, **extra):
    return {
        **os.environ,
        "RAIDLOCK_TOKEN": ws.token,
        "RAIDLOCK_AGENT": ws.board.lead,
        "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
        "SENTINEL_CANARY": "canary-value-1234",
        **extra,
    }


def evil_verify_script(tmp_path):
    home = os.environ["RAIDLOCK_HOME"]
    return (
        "\n".join(
            [
                "env > src/env.out",
                f"cat {shlex.quote(home)}/tokens/*/*.token > src/stolen.out 2>/dev/null",
                f"echo pwned > {shlex.quote(str(tmp_path / 'escaped'))} 2>/dev/null",
                f"echo pwned > {shlex.quote(os.path.expanduser('~'))}/.raidlock-verify-escape-probe 2>/dev/null",
                "echo $HOME > src/home.out",
                "exit 0",
            ]
        )
        + "\n"
    )


def _write_agent(script_path, body):
    return fake("--write", f"{script_path}={body}", "--say", DONE)


@pytest.mark.skipif(SANDBOX is None, reason="no verify sandbox on this host")
def test_malicious_verify_cannot_read_env_tokens_or_write_outside_clone(ws, tmp_path):
    ws.card("evil", verify_cmd="sh src/evil.sh")
    r = go(ws, "evil", cmd=_write_agent("src/evil.sh", evil_verify_script(tmp_path)), env=lead_env(ws))
    assert r["verify"]["passed"] is True and r["verify"]["isolation"] == SANDBOX
    clone = os.path.join(r["run_dir"], "repo")
    env = dict(
        line.split("=", 1)
        for line in Path(os.path.join(clone, "src", "env.out")).read_text().splitlines()
        if "=" in line
    )
    assert not [k for k in env if k.startswith("RAIDLOCK_") and k != "RAIDLOCK_AGENT_ID"]
    assert env.get("RAIDLOCK_AGENT_ID") == "shell-worker-evil"
    assert "AWS_SECRET_ACCESS_KEY" not in env and "SENTINEL_CANARY" not in env
    assert env["HOME"] != os.path.expanduser("~") and env["HOME"] != os.environ.get("HOME")
    stolen = Path(os.path.join(clone, "src", "stolen.out")).read_text()
    assert ws.token not in stolen and not stolen.strip()
    assert not (tmp_path / "escaped").exists()
    assert not os.path.exists(os.path.expanduser("~/.raidlock-verify-escape-probe"))
    assert not os.path.exists(Path(os.path.join(clone, "src", "home.out")).read_text().strip())
    card = ws.board.card("evil")
    assert card["verifications"][-1]["isolation"] == SANDBOX


def test_verify_is_off_without_a_sandbox_unless_unsafe(ws, tmp_path, monkeypatch):
    monkeypatch.setattr(isolation, "mode", lambda *a, **k: None)
    ws.card("evil", verify_cmd="sh src/evil.sh")
    r = go(ws, "evil", cmd=_write_agent("src/evil.sh", evil_verify_script(tmp_path)), env=lead_env(ws))
    clone = os.path.join(r["run_dir"], "repo")
    assert r["verify"] is None and "--unsafe-verify" in r["verify_skipped"] and r["lane"] == "review"
    assert not os.path.exists(os.path.join(clone, "src", "env.out"))
    assert any("not run" in e for e in r["handback"]["evidence"])
    assert not ws.board.card("evil")["verifications"]


def test_unsafe_verify_still_scrubs_env_and_home(ws, tmp_path, monkeypatch):
    monkeypatch.setattr(isolation, "mode", lambda *a, **k: None)
    ws.card("evil", verify_cmd="sh src/evil.sh")
    r = go(ws, "evil", cmd=_write_agent("src/evil.sh", "env > src/env.out\n"), env=lead_env(ws), unsafe_verify=True)
    assert r["verify"]["passed"] is True and r["verify"]["isolation"].startswith("unsafe")
    clone = os.path.join(r["run_dir"], "repo")
    env = dict(
        line.split("=", 1)
        for line in Path(os.path.join(clone, "src", "env.out")).read_text().splitlines()
        if "=" in line
    )
    shell_set = {"PWD", "SHLVL", "_", "OLDPWD", "__CF_USER_TEXT_ENCODING"}
    if os.name == "nt":
        shell_set |= {"COMSPEC", "PROMPT", "PATHEXT", "WINDIR", "TERM", "!C:"}
    assert set(env) - shell_set <= set(isolation.ENV_KEEP) | {
        "HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "NO_COLOR",
        "CI",
        "RAIDLOCK_AGENT_ID",
    }
    assert env["RAIDLOCK_AGENT_ID"] == "shell-worker-evil" and "RAIDLOCK_TOKEN" not in env
    assert not os.path.exists(env["HOME"])


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_verify_timeout_kills_the_process_group(tmp_path, monkeypatch):
    marker = tmp_path / "late"
    popen = subprocess.Popen
    processes = []

    def started(*args, **kwargs):
        proc = popen(*args, **kwargs)
        processes.append(proc)
        ready, _, _ = select.select([proc.stdout], [], [], 10)
        assert ready, "verify child did not start"
        return proc

    monkeypatch.setattr(isolation.subprocess, "Popen", started)
    try:
        code, tail, _ = isolation.run(
            f"(echo ready; sleep 30; echo late > {shlex.quote(str(marker))}) & wait",
            str(tmp_path),
            dict(os.environ),
            0.5,
        )
        assert code is None and "timed out" in tail and "ready" in tail
        assert processes[0].poll() is not None
        assert not marker.exists()
    finally:
        for proc in processes:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=10)


def test_bwrap_argv_hides_state_and_binds_only_clone_and_home(tmp_path):
    clone, home, secret = tmp_path / "clone", tmp_path / "home", tmp_path / "state"
    for p in (clone, home, secret):
        p.mkdir()
    argv = isolation.wrap("bwrap", ["/bin/sh", "-c", "true"], str(clone), str(home), [str(secret)])
    joined = " ".join(argv)
    assert argv[:5] == ["bwrap", "--ro-bind", "/", "/", "--dev"]
    assert f"--tmpfs {secret}" in joined
    assert f"--bind {os.path.realpath(clone)} {os.path.realpath(clone)}" in joined
    assert argv.index("--tmpfs", 6) < argv.index("--bind")
    assert argv[-3:] == ["/bin/sh", "-c", "true"]
    assert isolation.mode("linux", which=lambda name: None) is None
    assert isolation.mode("linux", which=lambda name: "/usr/bin/bwrap") == "bwrap"
    assert isolation.mode("win32") is None


BWRAP = "/usr/bin/bwrap"


@pytest.mark.parametrize(
    ("platform", "env", "found", "expected"),
    [
        ("linux", {"RAIDLOCK_SANDBOX": "container"}, BWRAP, "bwrap-container"),
        ("linux", {"RAIDLOCK_SANDBOX": "container"}, None, None),
        ("linux", {"RAIDLOCK_SANDBOX": "none"}, BWRAP, None),
        ("linux", {"RAIDLOCK_SANDBOX": "Container"}, BWRAP, None),
        ("darwin", {"RAIDLOCK_SANDBOX": "container"}, BWRAP, None),
        ("linux", {}, BWRAP, "bwrap"),
        ("linux", {"RAIDLOCK_SANDBOX": ""}, BWRAP, "bwrap"),
    ],
)
def test_container_sandbox_is_detected_and_fails_closed(platform, env, found, expected):
    assert isolation.mode(platform, which=lambda name: found, env=env, exists=lambda path: True) == expected


def test_container_mode_needs_a_real_container(monkeypatch):
    env = {"RAIDLOCK_SANDBOX": "container"}
    assert isolation.mode("linux", which=lambda name: BWRAP, env=env, exists=lambda path: False) is None
    marker = "/.dockerenv"
    assert isolation.mode("linux", which=lambda name: BWRAP, env=env, exists=lambda path: path == marker) == (
        "bwrap-container"
    )
    monkeypatch.setenv("RAIDLOCK_SANDBOX", "off")
    assert isolation.mode("linux", which=lambda name: BWRAP, exists=lambda path: True) is None


def test_container_bwrap_argv_keeps_isolation_and_hides_host_proc(tmp_path):
    clone, home, secret = tmp_path / "clone", tmp_path / "home", tmp_path / "state"
    for p in (clone, home, secret):
        p.mkdir()
    host = isolation.wrap("bwrap", ["/bin/sh", "-c", "true"], str(clone), str(home), [str(secret)])
    argv = isolation.wrap("bwrap-container", ["/bin/sh", "-c", "true"], str(clone), str(home), [str(secret)])
    joined = " ".join(argv)
    assert argv[:5] == ["bwrap", "--ro-bind", "/", "/", "--dev"]
    assert "--proc" not in argv
    assert "--unshare-user --tmpfs /proc" in joined
    assert "--ro-bind /proc /proc" not in joined
    assert f"--tmpfs {secret}" in joined
    for flag in ("--unshare-net", "--unshare-pid", "--unshare-ipc", "--die-with-parent", "--new-session"):
        assert flag in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    at = host.index("--proc")
    assert argv == [*host[:at], "--unshare-user", "--tmpfs", "/proc", *host[at + 2 :]]


REPLACE_SPOOF = f"""
set -e
base=$(git rev-parse HEAD)
echo pwned >> README.md
git -c user.name=x -c user.email=x@x add -A
git -c user.name=x -c user.email=x@x -c commit.gpgsign=false commit -q -m spoof
git replace "$base" HEAD
git reset -q --soft "$base" 2>/dev/null || true
echo '{DONE}'
"""


def test_replace_ref_cannot_hide_a_change_outside_the_locks(ws):
    ws.card("spoof")
    r = go(ws, "spoof", cmd=sh(REPLACE_SPOOF))
    assert "README.md" in r["outside"] and r["lane"] == "blocked"
    assert "outside the card's locks" in r["reason"]
    assert os.path.exists(os.path.join(r["run_dir"], "rejected.patch"))
    assert not os.path.exists(os.path.join(r["run_dir"], "repo", ".git", "refs", "replace"))


def test_audit_ignores_replace_refs_index_flags_and_worker_objects(ws, tmp_path):
    clone = tmp_path / "c"
    base, _ = W.clone(str(ws.repo), str(clone))
    with open(clone / "README.md", "a") as f:
        f.write("pwned\n")
    git(clone, "update-index", "--assume-unchanged", "README.md")
    git(clone, "add", "src")
    git(clone, "commit", "-q", "--allow-empty", "-m", "c")
    git(clone, "update-index", "--assume-unchanged", "README.md")
    spoof = git(clone, "rev-parse", "HEAD").strip()
    git(clone, "replace", base, spoof)
    plain = subprocess.run(["git", "diff", "--name-only", base], cwd=clone, capture_output=True, text=True).stdout
    assert "README.md" not in plain
    paths, tree = W.audit(str(ws.repo), base, str(clone), str(tmp_path / "lead.git"))
    assert paths == ["README.md"]
    out = tmp_path / "p.patch"
    assert W.write_patch(str(tmp_path / "lead.git"), base, tree, str(out)) > 0 and b"+pwned" in out.read_bytes()


def test_git_env_disables_replace_objects_and_user_config():
    env = W.git_env()
    assert env["GIT_NO_REPLACE_OBJECTS"] == "1" and env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull


def test_symlinked_git_config_is_refused_not_written_through(ws, tmp_path):
    sentinel = tmp_path / "victim.txt"
    sentinel.write_text("original\n")
    ws.card("link")
    script = f"import os; os.remove('.git/config'); os.symlink({str(sentinel)!r}, '.git/config'); print({DONE!r})"
    with pytest.raises(Precondition, match="symlinked git metadata"):
        go(ws, "link", cmd=py(script))
    assert sentinel.read_text() == "original\n"
    card = ws.board.card("link")
    assert card["lane"] == "blocked" and "symlinked git metadata" in card["notes"][-1]["text"]


def test_symlinked_git_dir_and_hooks_are_refused(ws, tmp_path):
    outside = tmp_path / "outside-hooks"
    outside.mkdir()
    (outside / "keep").write_text("keep\n")
    ws.card("hooks")
    with pytest.raises(Precondition, match="symlinked git metadata"):
        go(
            ws,
            "hooks",
            cmd=py(
                "import os, shutil; shutil.rmtree('.git/hooks', ignore_errors=True); "
                f"os.symlink({str(outside)!r}, '.git/hooks', target_is_directory=True); print({DONE!r})"
            ),
        )
    assert (outside / "keep").read_text() == "keep\n"
    ws.card("gitdir", locks="app/lib")
    with pytest.raises(Precondition, match="symlinked git metadata"):
        go(
            ws,
            "gitdir",
            cmd=py(
                "import os; os.rename('.git', '../moved.git'); "
                f"os.symlink('../moved.git', '.git', target_is_directory=True); print({DONE!r})"
            ),
        )
    assert ws.board.card("gitdir")["lane"] == "blocked"


def test_restore_replaces_config_atomically_without_following(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q")
    config = W.snapshot_config(str(repo))
    gdir = repo / ".git"
    (gdir / "config").write_text("[core]\n\tfsmonitor = touch /tmp/x\n")
    (gdir / "info").mkdir(exist_ok=True)
    (gdir / "info" / "attributes").write_text("* filter=evil\n")
    W.restore(str(repo), config)
    assert (gdir / "config").read_bytes() == config
    assert not (gdir / "info" / "attributes").exists() and not (gdir / "hooks").exists()
    assert_mode(gdir / "config", 0o600, follow=False)
    victim = tmp_path / "victim"
    victim.write_text("v\n")
    os.unlink(gdir / "config")
    os.symlink(victim, gdir / "config")
    with pytest.raises(Precondition, match="symlinked git metadata"):
        W.restore(str(repo), config)
    assert victim.read_text() == "v\n"


SECRETS = ("sk-ant-api03-" + "A" * 40, "AKIA" + "ABCDEFGHIJKLMNOP", "ghp_" + "b" * 36)


def test_secrets_in_agent_streams_are_redacted_before_they_hit_disk(ws):
    ws.card("leak")
    script = "\n".join(
        [
            f"echo 'key {SECRETS[0]} and {SECRETS[1]}'",
            f'echo \'{{"kind": "message", "text": "{SECRETS[2]}"}}\'',
            'echo "worker capability $RAIDLOCK_CAP"',
            f"echo 'lead token {ws.token}'",
            'echo "canary $SENTINEL_CANARY"',
            f"echo 'stderr {SECRETS[0]} '\"$RAIDLOCK_CAP\" >&2",
            f"echo 'lead {ws.token}' >&2",
            'echo \'{"status": "done", "evidence": ["'
            + SECRETS[1]
            + '"], "paths_changed": [], "open_questions": []}\'',
        ]
    )
    r = go(ws, "leak", cmd=sh(script), env=lead_env(ws, OPENAI_API_KEY="sess-" + "q" * 30))
    d = r["run_dir"]
    worker_token = (
        Path(os.path.join(os.environ["RAIDLOCK_HOME"], "tokens", ws.board.uid, "shell-worker-leak.token"))
        .read_text()
        .strip()
    )
    from raidlock.core.identity import make_cap

    worker_cap = make_cap(worker_token, "shell-worker-leak", "leak", ws.board.card("leak")["fence"])
    for name in ("events.jsonl", "stderr.log", "handback.json", "run.json"):
        text = Path(os.path.join(d, name)).read_text(encoding="utf-8")
        for secret in SECRETS + (ws.token, worker_token, worker_cap):
            assert secret not in text, (name, secret)
    assert "[REDACTED]" in Path(os.path.join(d, "events.jsonl")).read_text()
    assert "[REDACTED]" in Path(os.path.join(d, "stderr.log")).read_text()
    assert not os.path.exists(os.path.join(d, "events.raw.jsonl")) and not os.path.exists(
        os.path.join(d, "stderr.raw.log")
    )
    for row in ws.board.transcript.read() if hasattr(ws.board.transcript, "read") else []:
        blob = json.dumps(row)
        assert ws.token not in blob and worker_token not in blob


def test_raw_capture_is_opt_in_and_private(ws):
    ws.card("raw")
    r = go(ws, "raw", cmd=sh(f"echo 'x {SECRETS[0]}'; echo 'y {SECRETS[0]}' >&2; echo '{DONE}'"), raw=True)
    d = r["run_dir"]
    for name in ("events.raw.jsonl", "stderr.raw.log"):
        path = os.path.join(d, name)
        assert SECRETS[0] in Path(path).read_text()
        assert_mode(path, 0o600)
    assert SECRETS[0] not in Path(os.path.join(d, "events.jsonl")).read_text()


def test_redactor_masks_literals_and_json_values():
    red = Redactor.for_env({"MY_API_KEY": "literal-secret-value", "PATH": "/usr/bin"}, ["board-token-xyz123"])
    out = red({"a": "x literal-secret-value y", "b": ["board-token-xyz123"], "c": {"password": "hunter22"}, "n": 3})
    assert out["a"] == "x [REDACTED] y" and out["b"] == ["[REDACTED]"] and out["n"] == 3
    assert out["c"]["password"] == "[REDACTED]"
    assert "/usr/bin" not in red.secrets


@pytest.mark.skipif(SANDBOX is None, reason="no verify sandbox on this host")
def test_verify_cannot_read_arbitrary_parent_home_or_use_network(tmp_path):
    parent_home = tmp_path / "lead-home"
    parent_home.mkdir()
    secret = parent_home / "unusual-secret-store"
    secret.write_text("private-lead-state")
    cwd = tmp_path / "clone"
    cwd.mkdir()
    env = dict(os.environ, HOME=str(parent_home))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    command = (
        f"cat {shlex.quote(str(secret))} > stolen 2>/dev/null; "
        f"curl --max-time 2 -s http://127.0.0.1:{server.server_port} > network 2>/dev/null; "
        "echo $? > network-status; exit 0"
    )
    try:
        code, _, _ = isolation.run(command, str(cwd), env, 10, kind=SANDBOX, deny_read=isolation.hidden(env))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert code == 0
    assert not (cwd / "stolen").read_text()
    assert (cwd / "network-status").read_text().strip() != "0"


def test_multiline_private_keys_are_redacted_in_both_streams(ws):
    ws.card("pem")
    body = "unique-private-key-material-0123456789"
    pem = f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----"
    script = f"printf '%s\n' {shlex.quote(pem)}; printf '%s\n' {shlex.quote(pem)} >&2; echo '{DONE}'"
    result = go(ws, "pem", cmd=sh(script))
    assert result["lane"] == "review"
    for name in ("events.jsonl", "stderr.log", "handback.json", "run.json"):
        assert body not in Path(os.path.join(result["run_dir"], name)).read_text()
    assert result["handback"]["status"] == "done"


def test_restore_refuses_symlinked_clone_root(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    git(real, "init", "-q")
    config = W.snapshot_config(str(real))
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(Precondition, match="clone root is a symlink"):
        W.restore(str(alias), b"replacement")
    assert (real / ".git" / "config").read_bytes() == config


def test_verify_redacts_before_truncating_output(tmp_path):
    secret = "prefix-" + "z" * 5000 + "-suffix"
    redactor = Redactor([secret])
    cmd = f"printf '%s' {shlex.quote(secret)}"
    code, tail, _ = isolation.run(cmd, str(tmp_path), dict(os.environ), 10, redact=redactor.text)
    assert code == 0 and tail == "[REDACTED]"


@pytest.mark.parametrize("raw", [False, True])
def test_secret_patch_objects_and_clone_are_removed_or_private(ws, raw):
    ws.card("secret", locks="app/src")
    secret = "ghp_" + "A1b2C3d4E5" * 4
    cmd = [SH, "-c", f"printf '%s' '{secret}' > src/leak.txt; echo '{DONE}'"]
    result = go(ws, "secret", cmd=shlex.join(cmd), raw=raw)
    folder = result["run_dir"]
    assert result["status"] == "blocked"
    assert result["patch"] == ""
    for name in ("rejected.patch", "changes.patch", "lead.git", "repo"):
        assert not os.path.lexists(os.path.join(folder, name))
    quarantine = os.path.join(folder, "quarantine")
    assert os.path.exists(quarantine) == raw
    if raw:
        assert_mode(quarantine, 0o700)
        assert secret in Path(os.path.join(quarantine, "changes.patch")).read_text()
    for name in ("run.json", "handback.json", "events.jsonl", "stderr.log"):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            assert secret not in Path(path).read_text()


@pytest.mark.parametrize("target", ["../.github/workflows", "../docs/private", "/etc/passwd", "../../outside"])
def test_runner_diff_rejects_symlink_targets(ws, target):
    ws.card("link")
    result = go(ws, "link", cmd=sh(f"ln -s {shlex.quote(target)} src/link\nprintf '%s\\n' '{DONE}'"))
    assert result["lane"] == "blocked" and "src/link" in result["denied"]


def test_failure_reason_names_the_signal_and_next_step():
    from raidlock.runners import isolation

    sandboxed = isolation.failure_reason(133, "sandbox-exec")
    assert "SIGTRAP" in sandboxed and "--unsafe-verify" in sandboxed
    assert "SIGTRAP" in isolation.failure_reason(-5, "sandbox-exec")
    assert "--unsafe-verify" not in isolation.failure_reason(-9, None)
    assert isolation.failure_reason(1, "sandbox-exec") == ""
    assert "not found" in isolation.failure_reason(127, "bwrap")
    assert isolation.failure_reason(0, "sandbox-exec") == ""


def test_sandbox_profile_allows_the_uv_cache(tmp_path):
    from raidlock.runners import isolation

    env = {"HOME": str(tmp_path), "UV_CACHE_DIR": str(tmp_path / "uvc"), "PATH": "/usr/bin"}
    cache, readable = isolation.uv_paths(env)
    assert cache.endswith("uvc")
    argv = isolation.wrap("sandbox-exec", ["true"], str(tmp_path), str(tmp_path), [], readable, [cache])
    assert cache in argv[2]


@pytest.mark.skipif(not os.path.exists("/usr/bin/python3") or SANDBOX != "sandbox-exec", reason="macOS sandbox only")
def test_sandboxed_verify_runs_the_system_python(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path / "home"))
    code, out, _ = isolation.run(
        "/usr/bin/python3 -c 'print(42)'", str(tmp_path), env, 60, kind=SANDBOX, deny_read=isolation.hidden(env)
    )
    assert code == 0
    assert "42" in out


@pytest.mark.skipif(SANDBOX != "sandbox-exec", reason="macOS sandbox only")
def test_sandboxed_verify_cannot_write_the_real_uv_cache(tmp_path):
    real = tmp_path / "real-uv"
    real.mkdir()
    home = tmp_path / "raidlock-home"
    env = dict(os.environ, HOME=str(tmp_path / "home"), UV_CACHE_DIR=str(real), RAIDLOCK_HOME=str(home))
    work = tmp_path / "work"
    work.mkdir()
    cmd = f'touch "$UV_CACHE_DIR/pwned"; touch {shlex.quote(str(real))}/direct; true'
    code, _, _ = isolation.run(cmd, str(work), env, 30, kind=SANDBOX, deny_read=isolation.hidden(env))
    assert code == 0
    assert not (real / "pwned").exists()
    assert not (real / "direct").exists()
    assert (home / "verify" / "uv-cache" / "pwned").exists()


def test_closing_a_card_prunes_its_run_clones_and_keeps_the_logs(ws):
    from raidlock.runners.runner import prune_runs

    ws.card("tidy", locks="app/src")
    cmd = [SH, "-c", f"printf 'x' > src/tidy.txt; echo '{DONE}'"]
    folder = go(ws, "tidy", cmd=shlex.join(cmd))["run_dir"]
    assert os.path.isdir(os.path.join(folder, "repo"))
    assert prune_runs(ws.board, "tidy") >= 1
    assert not os.path.lexists(os.path.join(folder, "repo"))
    assert os.path.isfile(os.path.join(folder, "run.json"))
    assert prune_runs(ws.board, "missing") == 0


def test_an_agent_cli_rejecting_an_option_is_called_out(ws):
    ws.card("old", locks="app/src")
    cmd = [SH, "-c", "echo \"error: unknown option '--safe-mode'\" >&2; exit 1"]
    result = go(ws, "old", cmd=shlex.join(cmd))
    assert "likely too old" in json.dumps(result)


@pytest.mark.skipif(SANDBOX is None, reason="no verify sandbox on this host")
def test_lead_integrates_a_delegated_patch_then_verifies_and_closes(ws):
    ws.card("land", locks="app/src", verify_cmd="test -f src/land.txt")
    cmd = [SH, "-c", f"printf 'x' > src/land.txt; echo '{DONE}'"]
    result = go(ws, "land", cmd=shlex.join(cmd))
    assert ws.board.card("land")["lane"] == "review"
    git(ws.repo, "apply", "--index", result["patch"])
    git(ws.repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "land")
    assert ws.board.verify("land", ws.lead, cwd=str(ws.repo))["passed"]
    ws.board.move("land", "done", ws.lead)
    assert ws.board.card("land")["lane"] == "done"


def test_prune_skips_a_run_still_in_progress(ws, tmp_path):
    from raidlock.runners.runner import prune_runs, runs_dir

    live = os.path.join(runs_dir(ws.board, "busy"), "1")
    os.makedirs(os.path.join(live, "repo"))
    Path(live, "started.json").write_text("{}")
    assert prune_runs(ws.board, "busy") == 0
    assert os.path.isdir(os.path.join(live, "repo"))
