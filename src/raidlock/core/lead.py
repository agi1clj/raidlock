"""The lead lease: one lead capability per board, bound to a session, fenced, renewed by use.

The lead secret has the form `<fence>.<random>`. Presenting it is the only way to get lead rights. Its SHA-256 and
the lease (session, process anchor, fence, expiry, history of retired secrets) live in `.board/lead.json`; the secret
itself lives in the secret store (a 0600 file, or the OS keychain when the board opts in)."""

import contextlib
import hashlib
import hmac
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time

from . import clock, store
from .errors import Denied, IdentityError, Precondition, StaleLead

SESSION_VARS = (
    "RAIDLOCK_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_SESSION_ID",
    "CODEX_THREAD_ID",
    "GEMINI_SESSION_ID",
)
PID_VARS = ("RAIDLOCK_LEAD_PID", "CLAUDE_PID")
WRAPPERS = (
    "python",
    "python3",
    "pytest",
    "py.test",
    "uv",
    "uvx",
    "env",
    "timeout",
    "nohup",
    "raid",
    "raidlock",
    "sudo",
    "xargs",
    "time",
    "pipx",
)
SHELLS = ("sh", "bash", "zsh", "dash", "fish", "ksh", "tcsh", "csh", "pwsh", "powershell")
AGENT_HOSTS = ("claude", "node", "codex", "gemini", "aider", "bun", "deno")
HISTORY = 20
SECRET = re.compile(r"^(\d+)\.([A-Za-z0-9_-]{16,})$")
STORES = ("file", "keychain")
SERVICE = "raidlock-lead"


def digest(secret):
    return hashlib.sha256((secret or "").strip().encode()).hexdigest()


def mint(fence):
    return f"{int(fence)}.{secrets.token_urlsafe(32)}"


def fence_of(secret):
    m = SECRET.match((secret or "").strip())
    return int(m.group(1)) if m else 0


def _base(comm):
    name = os.path.basename((comm or "").strip()).lower().lstrip("-")
    name = re.sub(r"\.exe$", "", name)
    return re.sub(r"[\d.]+$", "", name) or name


KINFO_SIZE = 648
KINFO_PPID = 560
KINFO_COMM = 243
CTL_KERN, KERN_PROC, KERN_PROC_PID = 1, 14, 1
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def lstart(epoch):
    """A start time in the form `ps -o lstart=` prints, so leases written by either source compare equal."""
    t = time.localtime(epoch)
    return (
        f"{DAYS[t.tm_wday]} {MONTHS[t.tm_mon - 1]} {t.tm_mday} "
        f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d} {t.tm_year}"
    )


class LiveTable:
    """{pid: (ppid, start, command)} looked up one pid at a time from the kernel (sysctl kern.proc on macOS, /proc
    on Linux, Toolhelp on Windows), with `ps -p` only when no kernel source works. Rows are memoized per table."""

    def __init__(self, lookup=None):
        self._lookup = lookup or row_lookup()
        self._rows = {}

    def get(self, pid, default=None):
        pid = int(pid)
        if pid not in self._rows:
            try:
                self._rows[pid] = self._lookup(pid)
            except (OSError, ValueError):
                self._rows[pid] = None
        row = self._rows[pid]
        return default if row is None else row

    def __contains__(self, pid):
        return self.get(pid) is not None

    def __getitem__(self, pid):
        row = self.get(pid)
        if row is None:
            raise KeyError(pid)
        return row

    def __bool__(self):
        return self.get(os.getpid()) is not None


def process_table():
    """The caller's view of the process table as a lazy {pid: (ppid, start, command)} mapping; empty when no
    source can describe this process."""
    return LiveTable()


_NATIVE = {}


def row_lookup():
    """The per-pid lookup for this host: a kernel source when it can describe this process, else `ps -p`."""
    if "lookup" not in _NATIVE:
        _NATIVE["lookup"] = None
        for source in (_proc_row, _sysctl_row, _toolhelp_row):
            try:
                ok = source(os.getpid()) is not None
            except (OSError, ValueError, AttributeError):
                ok = False
            if ok:
                _NATIVE["lookup"] = source
                break
    return _NATIVE["lookup"] or _ps_row


def _proc_row(pid, root="/proc"):
    if not os.path.isdir(os.path.join(root, "self")):
        return None
    try:
        with open(os.path.join(root, str(int(pid)), "stat"), encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    head, _, tail = text.rpartition(")")
    comm = head.partition("(")[2]
    fields = tail.split()
    if len(fields) <= 19:
        return None
    return int(fields[1]), fields[19], comm


def _libc():
    import ctypes

    if "libc" not in _NATIVE:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.sysctl.argtypes = (
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
            ctypes.c_size_t,
        )
        libc.sysctl.restype = ctypes.c_int
        _NATIVE["libc"] = libc
    return _NATIVE["libc"]


def _sysctl_row(pid):
    if sys.platform != "darwin":
        return None
    import ctypes

    mib = (ctypes.c_int * 4)(CTL_KERN, KERN_PROC, KERN_PROC_PID, int(pid))
    buf = ctypes.create_string_buffer(KINFO_SIZE)
    size = ctypes.c_size_t(KINFO_SIZE)
    if _libc().sysctl(mib, 4, buf, ctypes.byref(size), None, 0) != 0:
        raise OSError(ctypes.get_errno(), "sysctl kern.proc failed")
    if size.value < KINFO_SIZE:
        return None
    raw = buf.raw
    seconds = int.from_bytes(raw[0:8], sys.byteorder, signed=True)
    ppid = int.from_bytes(raw[KINFO_PPID : KINFO_PPID + 4], sys.byteorder, signed=True)
    comm = raw[KINFO_COMM : KINFO_COMM + 17].split(b"\0", 1)[0].decode("utf-8", "replace")
    microseconds = int.from_bytes(raw[8:16], sys.byteorder, signed=True)
    return ppid, f"darwin:{seconds}.{microseconds:06d}", comm


def _toolhelp_row(pid):
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        )

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    entry_ref = ctypes.POINTER(Entry)
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = (wintypes.HANDLE, entry_ref)
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = (wintypes.HANDLE, entry_ref)
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *(ctypes.POINTER(wintypes.FILETIME),) * 4)
    kernel.GetProcessTimes.restype = wintypes.BOOL
    snap = kernel.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        raise OSError("CreateToolhelp32Snapshot failed")
    found = None
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        more = kernel.Process32FirstW(snap, ctypes.byref(entry))
        while more:
            if entry.th32ProcessID == int(pid):
                found = (int(entry.th32ParentProcessID), entry.szExeFile)
                break
            more = kernel.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snap)
    if found is None:
        return None
    handle = kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)
    return found[0], str(created), found[1]


def _ps_row(pid):
    if sys.platform == "darwin":
        return None
    ps = shutil.which("ps") if os.name == "posix" else None
    if not ps:
        return None
    for attempt in range(2):
        try:
            proc = subprocess.run(
                [ps, "-o", "ppid=,lstart=,comm=", "-p", str(int(pid))],
                capture_output=True,
                text=True,
                timeout=5,
                env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "/bin:/usr/bin")},
            )
            break
        except subprocess.TimeoutExpired:
            if attempt:
                return None
        except (OSError, subprocess.SubprocessError):
            return None
    if proc.returncode != 0:
        return None
    parts = proc.stdout.strip().split(None, 6)
    if len(parts) < 7 or not parts[0].isdigit():
        return None
    return int(parts[0]), " ".join(parts[1:6]), parts[6]


def ancestors(table, pid=None):
    """[(pid, start, command)] from the parent of `pid` (default: this process) upward."""
    pid = os.getpid() if pid is None else pid
    out, seen = [], set()
    cur = table.get(pid, (0,))[0]
    while cur and cur not in seen and cur in table:
        seen.add(cur)
        ppid, start, comm = table[cur]
        out.append((cur, start, comm))
        cur = ppid
    return out


def anchor(env, table=None):
    """The long-lived process this call belongs to: an explicit ancestor (RAIDLOCK_LEAD_PID, CLAUDE_PID), else the
    agent host above a per-call shell, else the first interactive shell or other non-wrapper ancestor."""
    table = process_table() if table is None else table
    chain = ancestors(table)
    if not chain:
        return None
    for var in PID_VARS:
        want = str(env.get(var, "")).strip()
        if want.isdigit():
            hit = next((p for p in chain if p[0] == int(want)), None)
            if hit:
                return hit[0], hit[1]
    host = next((p for p in chain if _base(p[2]) in AGENT_HOSTS), None)
    if host:
        return host[0], host[1]
    shell = None
    for pid, start, comm in chain:
        base = _base(comm)
        if base in WRAPPERS:
            continue
        if base in SHELLS:
            shell = (pid, start)
            continue
        return shell or (pid, start)
    if shell:
        return shell
    return None


def session_of(env, explicit=None, table=None):
    """(session id, pid, pid start) of the caller. Session ids come from the flag or the runtime's environment;
    without one, the session is the process anchor."""
    found = anchor(env, table)
    pid, start = found if found else (None, None)
    sid = (explicit or "").strip() or next(
        (str(env[v]).strip() for v in SESSION_VARS if str(env.get(v, "")).strip()), ""
    )
    if not sid and pid:
        sid = f"pid:{pid}:{start.replace(' ', '_')}"
    return sid, pid, start


def legacy_anchor(start):
    return sys.platform == "darwin" and bool(re.match(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) ", str(start)))


def alive(pid, start, table=None):
    if legacy_anchor(start):
        return False
    if not pid:
        return True
    table = process_table() if table is None else table
    if not table:
        return True
    row = table.get(int(pid))
    return bool(row) and row[1] == start


class FileStore:
    kind = "file"

    def __init__(self, path):
        self.path = path

    def get(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        if os.name == "posix" and (stat.S_IMODE(st.st_mode) & 0o077 or st.st_uid != os.getuid()):
            raise IdentityError(
                f"lead secret file {self.path} must be mode 0600 and owned by you", hint=f"chmod 600 {self.path}"
            )
        try:
            with open(self.path, encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            return None

    def put(self, secret):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        store.atomic_write_text(self.path, secret + "\n")
        os.chmod(self.path, 0o600)
        return True

    def delete(self):
        with contextlib.suppress(OSError):
            os.remove(self.path)


class KeychainStore:
    """macOS `security` or Linux `secret-tool`, called with subprocess only. `available()` is False elsewhere."""

    kind = "keychain"

    def __init__(self, account, run=None, which=None, platform=None):
        self.account = account
        self.run = run or subprocess.run
        self.which = which or shutil.which
        self.platform = platform or sys.platform

    def tool(self):
        if self.platform == "darwin" and self.which("security"):
            return "security"
        if self.platform.startswith("linux") and self.which("secret-tool"):
            return "secret-tool"
        return None

    def available(self):
        return self.tool() is not None

    def _call(self, argv, stdin=None):
        try:
            proc = self.run(argv, input=stdin, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout if proc.returncode == 0 else None

    def get(self):
        tool = self.tool()
        if tool == "security":
            out = self._call(["security", "find-generic-password", "-a", self.account, "-s", SERVICE, "-w"])
        elif tool == "secret-tool":
            out = self._call(["secret-tool", "lookup", "service", SERVICE, "account", self.account])
        else:
            return None
        return (out or "").strip() or None

    def put(self, secret):
        tool = self.tool()
        if tool == "security":
            return (
                self._call(
                    [
                        "security",
                        "add-generic-password",
                        "-U",
                        "-a",
                        self.account,
                        "-s",
                        SERVICE,
                        "-l",
                        "raidlock lead secret",
                        "-w",
                        secret,
                    ]
                )
                is not None
            )
        if tool == "secret-tool":
            return (
                self._call(
                    [
                        "secret-tool",
                        "store",
                        "--label=raidlock lead secret",
                        "service",
                        SERVICE,
                        "account",
                        self.account,
                    ],
                    stdin=secret,
                )
                is not None
            )
        return False

    def delete(self):
        tool = self.tool()
        if tool == "security":
            self._call(["security", "delete-generic-password", "-a", self.account, "-s", SERVICE])
        elif tool == "secret-tool":
            self._call(["secret-tool", "clear", "service", SERVICE, "account", self.account])


class SecretStore:
    """The keychain when chosen and working, with the 0600 file as fallback."""

    def __init__(self, file_path, account, kind="file", keychain=None):
        self.file = FileStore(file_path)
        self.keychain = keychain or KeychainStore(account)
        self.kind = kind if kind in STORES else "file"

    @property
    def using(self):
        return (
            "keychain"
            if self.kind == "keychain" and self.keychain.available() and not os.path.exists(self.file.path)
            else "file"
        )

    def get(self):
        if self.using == "keychain":
            value = self.keychain.get()
            if value:
                return value
        return self.file.get()

    def put(self, secret):
        if self.kind == "keychain" and self.keychain.available() and self.keychain.put(secret):
            self.file.delete()
            return "keychain"
        self.file.put(secret)
        return "file"

    def delete(self):
        if self.kind == "keychain":
            self.keychain.delete()
        self.file.delete()


class ForeignLead(IdentityError):
    """A valid lead secret presented from a session that does not hold the lead lease."""


class Lead:
    """Lead lease operations for one board. Callers hold the board lock around every write."""

    def __init__(self, board_dir, secret_path, account, ttl_s, store_kind="file", keychain=None, table=None):
        self.path = os.path.join(board_dir, "lead.json")
        self.ttl_s = float(ttl_s)
        self.store = SecretStore(secret_path, account, store_kind, keychain)
        self._table = table

    def table(self):
        if self._table is None:
            self._table = process_table()
        return self._table

    def lease(self):
        return store.read_json(self.path) or {}

    def _save(self, lease):
        store.write_json(self.path, lease)

    def expired(self, lease=None, now=None):
        lease = self.lease() if lease is None else lease
        if not lease:
            return True
        exp = clock.parse(lease.get("expires", ""))
        if exp is None or exp <= (now or clock.now()):
            return True
        return not alive(lease.get("pid"), lease.get("pid_start"), self.table())

    def summary(self, lease=None):
        lease = self.lease() if lease is None else lease
        if not lease:
            return {"held": False, "fence": 0, "session": "", "expires": "", "live": False, "store": self.store.using}
        return {
            "held": True,
            "fence": lease.get("fence", 0),
            "session": lease.get("session", ""),
            "pid": lease.get("pid"),
            "expires": lease.get("expires", ""),
            "since": lease.get("since", ""),
            "live": not self.expired(lease),
            "store": self.store.using,
            "pending": not lease.get("pid") and not str(lease.get("session", "")).startswith("pid:"),
        }

    def _bind(self, lease, session, pid, start, fence, secret):
        now = clock.iso()
        lease.update(
            fence=fence,
            session=session,
            pid=pid,
            pid_start=start,
            hash=digest(secret),
            renewed=now,
            expires=clock.later(self.ttl_s),
            since=now,
        )
        return lease

    def _retire(self, lease):
        if lease.get("hash"):
            hist = lease.setdefault("history", [])
            hist.append(
                {
                    "fence": lease.get("fence", 0),
                    "hash": lease["hash"],
                    "session": lease.get("session", ""),
                    "until": clock.iso(),
                }
            )
            del hist[:-HISTORY]

    def establish(self, session, pid, start):
        """Create the lease at fence 1 (board init). Returns the secret; the caller shows its location, never it."""
        if not session or not pid or not start:
            raise IdentityError("cannot establish lead lease without a session process anchor")
        lease = self.lease()
        fence = int(lease.get("fence", 0)) + 1
        secret = mint(fence)
        self._retire(lease)
        self._save(self._bind(lease, session, pid, start, fence, secret))
        self.store.put(secret)
        return secret

    def check(self, secret, session, any_session=False, pid=None, start=None):
        """Verify a presented secret. Returns the lease fence. Raises StaleLead (exit 4) for a retired or older
        secret, ForeignLead when the lease is bound to another session, IdentityError for anything else."""
        secret = (secret or "").strip()
        if not any_session and (not session or not pid or not start):
            raise IdentityError("cannot authenticate the lead without a session process anchor")
        lease = self.lease()
        if not lease:
            stored = self.store.get()
            if stored and hmac.compare_digest(digest(stored), digest(secret)):
                return 0
            raise IdentityError("the presented lead secret is not valid for this board")
        h = digest(secret)
        if hmac.compare_digest(h, lease.get("hash", "")):
            if not any_session and legacy_anchor(lease.get("pid_start")):
                raise StaleLead(
                    "the macOS lead anchor lacks microseconds; re-establish the lease with explicit lead takeover"
                )
            descendant = bool(
                pid
                and self.table().get(pid, (None, None))[1] == start
                and any(
                    parent == lease.get("pid") and born == lease.get("pid_start")
                    for parent, born, _ in ancestors(self.table(), pid)
                )
            )
            process_session = descendant and session.startswith("pid:") and lease.get("session", "").startswith("pid:")
            if lease.get("session") and session != lease["session"] and not any_session and not process_session:
                raise ForeignLead(
                    f"the lead lease is held by session {lease['session']}, not {session or 'this one'}",
                    lead_session=lease["session"],
                    session=session,
                    fence=lease.get("fence", 0),
                    hint="from the lead session: raidlock lead handover --to <this session>; "
                    "or, once its lease expires: raidlock lead takeover",
                )
            if not any_session:
                expires = clock.parse(lease.get("expires", ""))
                if (
                    not expires
                    or clock.age_seconds(lease["expires"]) > 7 * 86400
                    or not alive(lease.get("pid"), lease.get("pid_start"), self.table())
                ):
                    raise StaleLead("the lead lease expired; use explicit lead takeover", fence=lease.get("fence", 0))
                if lease.get("pid") and (pid != lease["pid"] or start != lease.get("pid_start")) and not descendant:
                    raise ForeignLead(
                        "the lead session process does not match its lease",
                        session=session,
                        lead_session=lease.get("session", ""),
                        fence=lease.get("fence", 0),
                        hint="run lead commands from the lead session's own shell; a background job of that shell "
                        "works, but nohup, setsid or a shell that has exited detaches the process from the lead",
                    )
            return int(lease.get("fence", 0))
        old = next((r for r in lease.get("history", []) if hmac.compare_digest(h, r.get("hash", ""))), None)
        presented = old["fence"] if old else fence_of(secret)
        if old or 0 < presented < int(lease.get("fence", 0)):
            raise StaleLead(
                f"lead fence {presented} is stale; the lead is now at fence {lease.get('fence', 0)} "
                f"held by session {lease.get('session', '')}",
                fence=presented,
                current=lease.get("fence", 0),
                lead_session=lease.get("session", ""),
                hint="the lead was handed over or taken over; ask the current lead",
            )
        raise IdentityError("the presented lead secret is not valid for this board")

    def touch(self, secret, session, pid, start):
        """A verified lead write: adopt a legacy secret, bind a pending handover, renew the TTL.
        Returns (fence, replacement secret or None)."""
        lease = self.lease()
        if not lease:
            fence = max(1, fence_of(secret) + 1)
            new = mint(fence)
            self._save(
                self._bind(
                    {
                        "history": [
                            {"fence": fence_of(secret), "hash": digest(secret), "session": "", "until": clock.iso()}
                        ]
                    },
                    session,
                    pid,
                    start,
                    fence,
                    new,
                )
            )
            self.store.put(new)
            return fence, new
        if not lease.get("pid") and pid and session == lease.get("session"):
            lease.update(pid=pid, pid_start=start)
        lease.update(renewed=clock.iso(), expires=clock.later(self.ttl_s))
        self._save(lease)
        return int(lease.get("fence", 0)), None

    def handover(self, to_session):
        """Rotate the secret and bump the fence; bind the lease to `to_session` (its process binds on first use)."""
        to_session = (to_session or "").strip()
        if not to_session:
            raise Precondition("handover needs --to <session>")
        lease = self.lease()
        fence = int(lease.get("fence", 0)) + 1
        secret = mint(fence)
        prev = lease.get("session", "")
        self._retire(lease)
        self._save(self._bind(lease, to_session, None, None, fence, secret))
        where = self.store.put(secret)
        return {"fence": fence, "from": prev, "to": to_session, "store": where}

    def takeover(self, session, pid, start):
        """Only after the lease expired (TTL passed or the holder process is gone)."""
        if not session or not pid or not start:
            raise IdentityError("cannot take over the lead without a session process anchor")
        lease = self.lease()
        if lease and not self.expired(lease):
            raise Denied(
                f"the lead lease of session {lease.get('session', '')} is live until {lease.get('expires')}",
                lead_session=lease.get("session", ""),
                expires=lease.get("expires"),
                hint="ask the lead to run: raidlock lead handover --to <your session>",
            )
        fence = int(lease.get("fence", 0)) + 1
        secret = mint(fence)
        prev = {"session": lease.get("session", ""), "fence": lease.get("fence", 0), "expires": lease.get("expires")}
        self._retire(lease)
        self._save(self._bind(lease, session, pid, start, fence, secret))
        where = self.store.put(secret)
        return {"fence": fence, "previous": prev, "session": session, "store": where}

    def current_secret(self):
        return self.store.get()


def read_presented(src, store_obj, stdin=None):
    """Resolve `--lead-secret SRC`: '-' reads stdin, 'keychain' asks the secret store, anything else is a file path."""
    if not src:
        return ""
    if src == "-":
        stream = stdin or sys.stdin
        return (stream.readline() or "").strip()
    if src == "keychain":
        return (store_obj.get() or "").strip()
    path = os.path.expanduser(src)
    try:
        st = os.stat(path)
    except OSError:
        raise Precondition(f"cannot read lead secret file {src}") from None
    if os.name == "posix" and (stat.S_IMODE(st.st_mode) & 0o077 or st.st_uid != os.getuid()):
        raise IdentityError(f"lead secret file {path} must be mode 0600 and owned by you", hint=f"chmod 600 {path}")
    with open(path, encoding="utf-8") as f:
        return f.read().strip()
