import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from ..core import lead
from ..core.portable import remove_tree

ENV_KEEP = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT")
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
TAIL_LINES = 40
TAIL_BYTES = 4000
HOME_SECRETS = (
    ".ssh",
    ".aws",
    ".azure",
    ".kube",
    ".docker",
    ".gnupg",
    ".netrc",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
    ".codex",
    ".claude",
    ".claude.json",
    ".gemini",
    ".raidlock",
    ".config/gcloud",
    ".config/gh",
    ".config/raidlock",
    ".config/git",
    ".gitconfig",
    "Library/Keychains",
)
ENV_DIRS = (
    "RAIDLOCK_HOME",
    "TEAM_BOARD_HOME",
    "CODEX_HOME",
    "CLAUDE_CONFIG_DIR",
    "GEMINI_CONFIG_DIR",
    "AWS_CONFIG_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "KUBECONFIG",
)


CONTAINER_MARKERS = ("/.dockerenv", "/run/.containerenv")


def mode(platform=None, which=shutil.which, env=None, exists=os.path.exists):
    """The sandbox this host offers for verify: "bwrap", "bwrap-container", "sandbox-exec" or None."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    sandbox = env.get("RAIDLOCK_SANDBOX", "")
    if sandbox:
        in_container = any(exists(m) for m in CONTAINER_MARKERS)
        if sandbox == "container" and platform.startswith("linux") and which("bwrap") and in_container:
            return "bwrap-container"
        return None
    if platform.startswith("linux"):
        return "bwrap" if which("bwrap") else None
    if platform == "darwin":
        return "sandbox-exec" if os.path.exists(SANDBOX_EXEC) else None
    return None


def missing_hint(platform=None, env=None):
    """What to install or change so mode() finds a sandbox."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    if env.get("RAIDLOCK_SANDBOX"):
        return "RAIDLOCK_SANDBOX=container needs bwrap inside a container; unset it on a normal host"
    if platform.startswith("linux"):
        return "install bubblewrap: sudo apt install bubblewrap (Debian, Ubuntu) or sudo dnf install bubblewrap (Fedora)"
    if platform == "darwin":
        return f"{SANDBOX_EXEC} is missing; it ships with macOS"
    return "run raidlock in WSL2 or the container image"


def scrub_env(parent_env, home, agent=""):
    env = {k: parent_env[k] for k in ENV_KEEP if parent_env.get(k)}
    env.setdefault("PATH", os.defpath)
    env.update(HOME=home, TMPDIR=home, TMP=home, TEMP=home, NO_COLOR="1", CI="1")
    if agent:
        env["RAIDLOCK_AGENT_ID"] = agent
    return env


def hidden(parent_env, extra=()):
    """Paths the verify command must not read: the board's state, agent CLI homes and credential stores."""
    home = parent_env.get("HOME") or os.path.expanduser("~")
    out = [home] + [os.path.join(home, rel) for rel in HOME_SECRETS]
    out += [parent_env[k] for k in ENV_DIRS if parent_env.get(k)]
    out += [p for p in extra if p]
    seen, result = set(), []
    for p in out:
        real = os.path.realpath(os.path.abspath(os.path.expanduser(p)))
        if real not in seen:
            seen.add(real)
            result.append(real)
    return result


def _sb(path):
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ancestors(path):
    out = []
    cur = os.path.dirname(path)
    while cur and cur != os.path.dirname(cur):
        out.append(cur)
        cur = os.path.dirname(cur)
    return out


def profile(writable, deny_read, readable=()):
    writable = [os.path.realpath(p) for p in writable]
    readable = [os.path.realpath(p) for p in readable]
    rules = [
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        "(deny process-info*)",
        "(allow process-info* (target same-sandbox))",
        "(deny signal)",
        "(allow signal (target same-sandbox))",
        "(deny mach-lookup)",
        "(deny file-write*)",
        "(allow file-write* "
        + " ".join(f"(subpath {_sb(p)})" for p in writable)
        + ' (literal "/dev/null") (literal "/dev/zero") (literal "/dev/dtracehelper")'
        + ' (regex #"^/dev/tty") (regex #"^/dev/fd/"))',
    ]
    if deny_read:
        rules.append("(deny file-read* " + " ".join(f"(subpath {_sb(p)})" for p in deny_read) + ")")
        rules.append("(allow file-read* " + " ".join(f"(subpath {_sb(p)})" for p in writable + readable) + ")")
        parents = sorted({a for p in writable + readable for a in _ancestors(p)})
        if parents:
            rules.append("(allow file-read-metadata " + " ".join(f"(literal {_sb(a)})" for a in parents) + ")")
    return "".join(rules)


def wrap(kind, argv, cwd, home, deny_read, readable=(), writable=()):
    cwd, home = os.path.realpath(cwd), os.path.realpath(home)
    readable = [os.path.realpath(p) for p in readable]
    if kind == "sandbox-exec":
        return [SANDBOX_EXEC, "-p", profile([cwd, home, *writable], deny_read, readable), *argv]
    if kind in ("bwrap", "bwrap-container"):
        proc = ["--unshare-user", "--tmpfs", "/proc"] if kind == "bwrap-container" else ["--proc", "/proc"]
        out = [
            "bwrap",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            *proc,
            "--tmpfs",
            os.path.join(os.sep, "tmp"),
            "--tmpfs",
            os.path.join(os.sep, "run"),
            "--cap-drop",
            "ALL",
        ]
        for p in deny_read:
            if os.path.isdir(p):
                out += ["--tmpfs", p]
            elif os.path.lexists(p):
                out += ["--ro-bind", os.devnull, p]
        for p in readable:
            out += ["--ro-bind", p, p]
        out += [
            "--bind",
            cwd,
            cwd,
            "--bind",
            home,
            home,
            "--chdir",
            cwd,
            "--unshare-net",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--die-with-parent",
            "--new-session",
            "--",
            *argv,
        ]
        return out
    return list(argv)


def uv_paths(parent_env):
    home = parent_env.get("HOME") or os.path.expanduser("~")
    cache_home = parent_env.get("XDG_CACHE_HOME") or os.path.join(home, ".cache")
    data_home = parent_env.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    cache = parent_env.get("UV_CACHE_DIR") or os.path.join(cache_home, "uv")
    python = parent_env.get("UV_PYTHON_INSTALL_DIR") or os.path.join(data_home, "uv", "python")
    executable = shutil.which("uv", path=parent_env.get("PATH", os.defpath))
    readable = [python, os.path.join(home, ".local", "bin")]
    if executable:
        readable.append(os.path.dirname(os.path.realpath(executable)))
    return os.path.realpath(cache), [os.path.realpath(p) for p in readable]


def verify_cache(parent_env):
    """The uv cache verify may write: private to raidlock, never the user's real cache, which the lead runs from."""
    home = parent_env.get("RAIDLOCK_HOME") or os.path.join(
        parent_env.get("HOME") or os.path.expanduser("~"), ".raidlock"
    )
    return os.path.realpath(os.path.join(os.path.abspath(os.path.expanduser(home)), "verify", "uv-cache"))


def failure_reason(code, kind):
    if not isinstance(code, int) or code == 0:
        return ""
    if code == 127:
        return (
            "verify command not found (exit 127): install it or give its full path with "
            'raid edit CARD --verify-cmd "..."'
        )
    number = -code if code < 0 else code - 128
    try:
        name = signal.Signals(number).name
    except ValueError:
        return ""
    if kind:
        return (
            f"verify terminated by {name} (exit {code}); likely sandbox denial or process crash; "
            "for uv, warm UV_CACHE_DIR=<RAIDLOCK_HOME>/verify/uv-cache with uv sync, "
            "or rerun with --unsafe-verify"
        )
    return f"verify terminated by {name} (exit {code}); inspect the command output and retry"


def _kill(proc):
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


CLEANUP_TIMEOUT = 1.0


def _process_rows(home):
    rows = {}
    if sys.platform == "darwin":
        import ctypes

        lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        lib.proc_listpids.argtypes = (ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int)
        lib.proc_listpids.restype = ctypes.c_int
        pids = (ctypes.c_int * 65536)()
        count = lib.proc_listpids(1, 0, pids, ctypes.sizeof(pids))
        buf = ctypes.create_string_buffer(1024 * 1024)
        marker = f"HOME={home}".encode()
        for pid in pids[: max(0, count) // ctypes.sizeof(ctypes.c_int)]:
            try:
                row = lead._sysctl_row(pid)
                if not row:
                    continue
                group = os.getpgid(pid)
                mib = (ctypes.c_int * 3)(lead.CTL_KERN, 49, pid)
                size = ctypes.c_size_t(len(buf))
                tagged = lead._libc().sysctl(mib, 3, buf, ctypes.byref(size), None, 0) == 0 and marker in buf.raw[
                    : size.value
                ].split(b"\0")
                rows[pid] = (row[0], group, tagged)
            except (OSError, ValueError):
                continue
    elif os.path.isdir("/proc/self"):
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            pid = int(name)
            try:
                row = lead._proc_row(pid)
                if not row:
                    continue
                with open(f"/proc/{pid}/environ", "rb") as stream:
                    tagged = f"HOME={home}".encode() in stream.read().split(b"\0")
                rows[pid] = (row[0], os.getpgid(pid), tagged)
            except (OSError, ValueError):
                continue
    return rows


def _descendants(proc, home, known):
    if os.name != "posix":
        return {}
    rows = _process_rows(home)
    found = {proc.pid}
    while True:
        more = {pid for pid, (parent, group, tagged) in rows.items() if parent in found or group == proc.pid or tagged}
        if more <= found:
            break
        found.update(more)
    found.update(known)
    lookup = lead.row_lookup()
    live = {}
    for pid in found - {proc.pid, os.getpid()}:
        try:
            row = lookup(pid)
        except (OSError, ValueError):
            continue
        if row and (pid not in known or row[1] == known[pid]):
            live[pid] = row[1]
    return live


def _cleanup(proc, home, known):
    stray = _descendants(proc, home, known)
    _kill(proc)
    lookup = lead.row_lookup()
    for pid, start in stray.items():
        try:
            row = lookup(pid)
            if row and row[1] == start:
                os.kill(pid, signal.SIGKILL)
        except (OSError, ValueError):
            pass
    return bool(stray)


def _tail(output):
    return "\n".join(output.splitlines()[-TAIL_LINES:])[-TAIL_BYTES:]


def run(cmd, cwd, parent_env, timeout, kind=None, agent="", deny_read=(), redact=None, readable=()):
    """Run `cmd` with /bin/sh in `cwd` with a scrubbed env, a throwaway HOME and, when `kind` is set, a sandbox
    that only writes to `cwd` and that HOME and cannot read `deny_read`, except the read-only `readable` paths.
    Returns (exit or None, tail, ms)."""
    home = tempfile.mkdtemp(prefix="raidlock-verify-")
    start = time.monotonic()
    try:
        env = scrub_env(parent_env, home, agent)
        writable = []
        if kind == "sandbox-exec":
            _, uv_readable = uv_paths(parent_env)
            cache = verify_cache(parent_env)
            os.makedirs(cache, mode=0o700, exist_ok=True)
            env.update(UV_CACHE_DIR=cache, UV_PYTHON_INSTALL_DIR=uv_readable[0], UV_OFFLINE="1")
            writable.append(cache)
            readable = [*readable, *uv_readable]
        if os.name == "posix":
            shell = ["/bin/sh", "-c", cmd]
            argv = wrap(kind, shell, cwd, home, list(deny_read), list(readable), writable) if kind else shell
        else:
            argv = f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /s /c "{cmd}"'
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=os.name == "posix",
            )
        except OSError as e:
            return None, f"could not start verify: {e}", int((time.monotonic() - start) * 1000)
        known = {}
        deadline = time.monotonic() + timeout
        expired = False
        out = b""
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    expired = True
                    break
                try:
                    out, _ = proc.communicate(timeout=min(0.1, remaining))
                    break
                except subprocess.TimeoutExpired as exc:
                    out = exc.output or b""
                    known.update(_descendants(proc, home, known))
        finally:
            stray = _cleanup(proc, home, known)
            try:
                out, _ = proc.communicate(timeout=CLEANUP_TIMEOUT)
            except subprocess.TimeoutExpired as exc:
                stray = True
                out = exc.output or out
                _cleanup(proc, home, known)
                if proc.stdout:
                    proc.stdout.close()
                try:
                    proc.wait(timeout=CLEANUP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    _kill(proc)
        code = None if expired or stray else proc.returncode
        text = out.decode("utf-8", "replace")
        if expired:
            text += f"\n[timed out after {timeout:g}s]"
        if stray:
            text += "\n[stray verification processes detected; receipt failed]"
        reason = failure_reason(code, kind)
        if reason:
            text += "\n" + reason
        tail = _tail(redact(text) if redact else text)
        return code, tail, int((time.monotonic() - start) * 1000)
    finally:
        remove_tree(home, ignore_errors=True)
