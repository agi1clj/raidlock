import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time

from ..core import Usage
from ..core.portable import open_nofollow
from ..core.transcript import REDACTED, redact

BASE_ENV = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
    "APPDATA",
    "LOCALAPPDATA",
    "USERPROFILE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "no_proxy",
)
SUMMARY_CAP = 500
SECRET_NAME = re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key|access[_-]?key|credential|session[_-]?key)")
MIN_SECRET = 8


class Redactor:
    """Masks the given literal secrets, then the common key and token patterns, in strings and JSON values."""

    def __init__(self, secrets=()):
        self.secrets = sorted(
            {s for s in secrets if isinstance(s, str) and len(s.strip()) >= MIN_SECRET}, key=len, reverse=True
        )

    @classmethod
    def for_env(cls, env, extra=()):
        values = [v for k, v in (env or {}).items() if SECRET_NAME.search(k)]
        return cls(list(values) + list(extra))

    def text(self, value):
        if not isinstance(value, str) or not value:
            return value
        for secret in self.secrets:
            value = value.replace(secret, REDACTED)
        return redact(value)

    def stream(self, mask=True):
        """A separate PEM-block state for each output stream."""
        private = False

        def clean(line):
            nonlocal private
            begin = re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", line)
            end = re.search(r"-----END [A-Z ]*PRIVATE KEY-----", line)
            if private:
                if end:
                    private = False
                    return REDACTED + self.text(line[end.end() :])
                return REDACTED + ("\n" if line.endswith("\n") else "")
            if begin and not end:
                private = True
            return self.text(line) if mask or begin else line

        return clean

    def __call__(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self(v) for v in value)
        if isinstance(value, dict):
            return {
                self(k) if isinstance(k, str) else k: REDACTED
                if isinstance(k, str) and SECRET_NAME.search(k) and isinstance(v, str)
                else self(v)
                for k, v in value.items()
            }
        return value


class Adapter:
    """One external runtime. Subclasses set the identity fields and implement `command` and `parse`."""

    prompt_stdin = True
    name = ""
    runtime = ""
    provider = ""
    binary = ""
    env_keys = ()
    api_keys = ()
    sandboxes = ("read-only", "workspace-write")

    def executable(self, env=None):
        env = os.environ if env is None else env
        override = env.get(f"RAIDLOCK_{self.name.upper()}_BIN", "")
        return shlex.split(override) if override else [self.binary]

    def child_env(self, env=None):
        env = os.environ if env is None else env
        keep = BASE_ENV + tuple(self.env_keys) + tuple(self.api_keys)
        out = {k: env[k] for k in keep if k in env}
        out.update(NO_COLOR="1", CI="1")
        return out

    def prepare(self, run):
        return {}

    def command(self, run):
        raise NotImplementedError

    def parse(self, obj):
        return []

    def final(self, run, collected):
        return collected.structured if collected.structured is not None else collected.final


def short(text, limit=SUMMARY_CAP):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class Collector:
    """Accumulates normalized events of one run."""

    def __init__(self):
        self.messages = []
        self.commands = []
        self.usage = Usage()
        self.usd = None
        self.final = ""
        self.structured = None
        self.error = ""
        self.model = ""
        self.billing = ""
        self.files = []
        self.events = 0

    def add(self, event):
        kind = event.get("kind")
        self.events += 1
        if kind == "message" and event.get("text"):
            self.messages.append(event["text"])
            self.final = event["text"]
        elif kind == "command":
            self.commands.append({"command": event.get("command", ""), "exit": event.get("exit")})
        elif kind == "usage":
            u = event.get("usage") or Usage()
            self.usage = Usage(**{k: getattr(self.usage, k) + getattr(u, k) for k in self.usage.as_dict()})
            if event.get("usd") is not None:
                self.usd = (self.usd or 0.0) + float(event["usd"])
        elif kind == "result":
            if event.get("text"):
                self.final = event["text"]
            if event.get("structured") is not None:
                self.structured = event["structured"]
            if event.get("error"):
                self.error = event["error"]
        elif kind == "error":
            self.error = event.get("text") or "error"
        elif kind == "model":
            self.model = event.get("model") or self.model
            self.billing = event.get("billing") or self.billing
        elif kind == "files":
            self.files.extend(event.get("paths", []))

    def summary(self):
        return {
            "messages": len(self.messages),
            "commands": len(self.commands),
            "usage": self.usage.as_dict(),
            "usd": self.usd,
            "model": self.model,
            "error": self.error,
            "events": self.events,
        }


def decode(line):
    text = line.rstrip("\r\n")
    if not text.strip():
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return {"type": "raw", "text": text}
    return obj if isinstance(obj, dict) else {"type": "raw", "text": text}


class Process:
    """Runs one adapter command: prompt on stdin, redacted events to disk, parsed events to `on_event`."""

    def __init__(self, argv, cwd, env, prompt, events_path, stderr_path, timeout, redactor=None, raw=False):
        self.argv, self.cwd, self.env, self.prompt = argv, cwd, env, prompt
        self.events_path, self.stderr_path, self.timeout = events_path, stderr_path, timeout
        self.redactor = redactor or Redactor()
        self.raw = raw
        self.proc = None
        self.killed = ""
        self._lock = threading.Lock()

    def stop(self, reason):
        with self._lock:
            if self.killed or self.proc is None or self.proc.poll() is not None:
                return
            self.killed = reason
            _terminate(self.proc)

    def _raw_path(self, path):
        root, ext = os.path.splitext(path)
        return root + ".raw" + ext

    def _open_raw(self, path, mode):
        if not self.raw:
            return None
        fd = open_nofollow(
            self._raw_path(path), os.O_WRONLY | os.O_CREAT | (os.O_APPEND if mode == "a" else os.O_TRUNC), 0o600
        )
        return os.fdopen(fd, mode, encoding="utf-8")

    def _drain_stderr(self, stream, err, raw):
        clean = self.redactor.stream()
        for line in stream:
            err.write(clean(line))
            err.flush()
            if raw:
                raw.write(line)
                raw.flush()

    def run(self, adapter, on_event):
        posix = os.name == "posix"
        raw_events = self._open_raw(self.events_path, "a")
        raw_err = self._open_raw(self.stderr_path, "w")
        try:
            return self._run(adapter, on_event, posix, raw_events, raw_err)
        finally:
            for f in (raw_events, raw_err):
                if f:
                    f.close()

    def _run(self, adapter, on_event, posix, raw_events, raw_err):
        with (
            open(self.events_path, "a", encoding="utf-8") as events,
            open(self.stderr_path, "w", encoding="utf-8") as err,
        ):
            try:
                self.proc = subprocess.Popen(
                    self.argv,
                    cwd=self.cwd,
                    env=self.env,
                    stdin=subprocess.PIPE if adapter.prompt_stdin else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    start_new_session=posix,
                )
            except OSError as e:
                return None, f"could not start {self.argv[0]}: {e}"
            drain = threading.Thread(target=self._drain_stderr, args=(self.proc.stderr, err, raw_err), daemon=True)
            drain.start()
            timer = threading.Timer(self.timeout, self.stop, args=(f"timed out after {self.timeout:g}s",))
            timer.daemon = True
            timer.start()
            try:
                try:
                    if adapter.prompt_stdin:
                        self.proc.stdin.write(self.prompt)
                        self.proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
                clean = self.redactor.stream(mask=False)
                for line in self.proc.stdout:
                    obj = decode(line)
                    if obj is None:
                        continue
                    if raw_events:
                        raw_events.write(json.dumps(obj, ensure_ascii=False) + "\n")
                        raw_events.flush()
                    normalized = decode(clean(line))
                    if normalized is None:
                        continue
                    obj = self.redactor(normalized)
                    events.write(json.dumps(obj, ensure_ascii=False) + "\n")
                    events.flush()
                    for event in adapter.parse(normalized):
                        on_event(self.redactor(event))
                code = self.proc.wait(timeout=self.timeout)
            except BaseException:
                self.stop("interrupted")
                raise
            finally:
                timer.cancel()
                drain.join(timeout=10)
        return code, self.killed


def _terminate(proc):
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        return
    deadline = time.monotonic() + 5
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if proc.poll() is None:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
