"""Read-only patch checks; register_cli must be wired by the CLI's owner."""

import ast
import base64
import collections
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import zlib
from pathlib import Path, PurePosixPath

from .core.errors import Precondition
from .runners.policy import check_links, denied
from .runners.workspace import git

MAX_BINARY_BYTES = 1024 * 1024
RULES = (
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("anthropic-token", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-token", re.compile(r"\bsk-(?!ant-)[A-Za-z0-9_-]{20,}")),
)
ASSIGNMENT = re.compile(r"""[\w.-]+["']?\s*[:=]\s*["']?([A-Za-z0-9_+/=-]{20,})""")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _finding(kind, path, line, rule):
    return {"kind": kind, "path": path, "line": line, "rule": rule, "snippet": "[REDACTED]"}


def _path(raw, prefix=False):
    raw = raw.strip()
    if raw.startswith('"'):
        try:
            raw = ast.literal_eval("b" + raw).decode("utf-8", "surrogateescape")
        except (ValueError, SyntaxError, UnicodeError):
            raise Precondition("invalid quoted patch path") from None
    else:
        raw = raw.split("\t", 1)[0]
    if raw == "/dev/null":
        return None
    if prefix:
        if "/" not in raw:
            raise Precondition("patch path has no component to strip with -p1")
        raw = raw.split("/", 1)[1]
    raw = raw.replace("\\", "/")
    if raw.startswith("/") or ".." in PurePosixPath(raw).parts or not raw:
        raise Precondition("patch path escapes the clone")
    return str(PurePosixPath(raw))


def _delta_size(lines):
    try:
        packed = bytearray()
        for line in lines:
            if not line:
                break
            n = ord(line[0]) - (64 if line[0].isupper() else 70)
            if not 1 <= n <= 52:
                raise ValueError
            packed.extend(base64.b85decode(line[1:])[:n])
        data = zlib.decompressobj().decompress(bytes(packed), 32)
        pos = 0
        values = []
        for _ in range(2):
            value = shift = 0
            while True:
                b = data[pos]
                pos += 1
                value |= (b & 127) << shift
                if not b & 128:
                    break
                shift += 7
                if shift > 63:
                    raise ValueError
            values.append(value)
        return values[1]
    except (ValueError, IndexError, zlib.error):
        raise Precondition("invalid binary delta in patch") from None


def _parse(text):
    paths, added, sizes = set(), [], []
    path = old = None
    remaining = old_remaining = 0
    number = 0
    binary = False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if remaining or old_remaining:
            if line.startswith("+") and remaining:
                if path:
                    added.append((path, number, line[1:]))
                number += 1
                remaining -= 1
                continue
            if line.startswith(" ") and remaining and old_remaining:
                number += 1
                remaining -= 1
                old_remaining -= 1
                continue
            if line.startswith("-") and old_remaining:
                old_remaining -= 1
                continue
            if line.startswith("\\"):
                continue
            raise Precondition("malformed unified patch hunk")
        if line.startswith("diff --git "):
            remaining = 0
            binary = False
            raw = line[11:]
            match = re.match(r'("(?:[^"\\]|\\.)*"|a/.*?) ("(?:[^"\\]|\\.)*"|b/.*)$', raw)
            if not match:
                raise Precondition("invalid git patch header")
            old, path = (_path(s, True) for s in match.groups())
            paths.update(p for p in (old, path) if p)
        elif line.startswith("--- "):
            old = _path(line[4:], True)
            if old:
                paths.add(old)
        elif line.startswith("+++ "):
            path = _path(line[4:], True)
            if path:
                paths.add(path)
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            raw = line.split(" ", 2)[2]
            name = _path(raw)
            if name:
                paths.add(name)
        elif HUNK.match(line):
            match = HUNK.match(line)
            old_remaining = int(match[2]) if match[2] is not None else 1
            number = int(match[3])
            remaining = int(match[4]) if match[4] is not None else 1
        elif line.startswith("@@"):
            raise Precondition("unsupported or malformed patch hunk")
        elif line == "GIT binary patch":
            binary = True
        elif binary and re.fullmatch(r"(?:literal|delta) \d+", line):
            size = int(line.split()[1]) if line.startswith("literal") else _delta_size(lines[i + 1 :])
            sizes.append((path or old, size))
            binary = False
        elif line.startswith("Binary files "):
            raise Precondition("binary patch omits blob sizes; supply a clone or a git diff --binary patch")
    if remaining or old_remaining:
        raise Precondition("truncated unified patch hunk")
    if text.strip() and not paths:
        raise Precondition("input is not a unified git patch")
    return paths, added, sizes


def _builtin(added):
    findings = []
    for path, number, text in added:
        for rule, pattern in RULES:
            if pattern.search(text):
                findings.append(_finding("secret", path, number, rule))
        for match in ASSIGNMENT.finditer(text):
            value = match[1].rstrip("=")
            counts = collections.Counter(value)
            entropy = -sum((n / len(value)) * math.log2(n / len(value)) for n in counts.values())
            if entropy >= 3.5:
                findings.append(_finding("secret", path, number, "high-entropy-assignment"))
                break
    return findings


def _secrets(added):
    executable = shutil.which("gitleaks")
    if not executable or not added:
        return _builtin(added)
    with tempfile.TemporaryDirectory(prefix="raidlock-scan-") as temp:
        source = Path(temp) / "source"
        source.mkdir()
        mapping = {}
        grouped = collections.defaultdict(list)
        for path, number, text in added:
            grouped[path].append((number, text))
        for index, (path, rows) in enumerate(grouped.items()):
            name = f"added-{index}.txt"
            (source / name).write_text("\n".join(text for _, text in rows) + "\n", encoding="utf-8", errors="replace")
            mapping[name] = (path, rows)
        report = Path(temp) / "report.json"
        try:
            proc = subprocess.run(
                [
                    executable,
                    "detect",
                    "--no-git",
                    "--source",
                    str(source),
                    "--report-format",
                    "json",
                    "--report-path",
                    str(report),
                ],
                cwd=temp,
                capture_output=True,
                timeout=120,
            )
            if proc.returncode not in (0, 1):
                raise ValueError
            rows = json.loads(report.read_text())
            if not isinstance(rows, list) or (proc.returncode == 1 and not rows):
                raise ValueError
            findings = []
            for row in rows:
                path, lines = mapping[Path(row["File"]).name]
                start, end = int(row["StartLine"]), int(row.get("EndLine", row["StartLine"]))
                if not 1 <= start <= end <= len(lines):
                    raise ValueError
                for number, _ in lines[start - 1 : end]:
                    findings.append(_finding("secret", path, number, str(row["RuleID"])))
            return findings
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            raise Precondition("gitleaks scan failed or returned an invalid report") from None


def _clone(repo, base, selected=None):
    repo = Path(repo).resolve()
    revision = git(repo, "rev-parse", "--verify", "--end-of-options", str(base) + "^{commit}").strip()
    args = ["diff", "--binary", "--no-ext-diff", "--no-textconv", "--no-renames", revision, "--"]
    if selected is not None:
        args.extend(":(literal)" + p for p in selected)
    paths, added, sizes = _parse(git(repo, *args)) if selected != [] else (set(), [], [])
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    for raw in filter(None, untracked):
        path = _path(raw)
        if selected is not None and path not in selected:
            continue
        paths.add(path)
        target = repo / path
        if target.is_symlink():
            data = os.readlink(target).encode()
        else:
            if not target.resolve().is_relative_to(repo):
                raise Precondition("changed file escapes the clone")
            data = target.read_bytes()
        if b"\0" in data:
            sizes.append((path, len(data)))
        else:
            added.extend((path, n, line) for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1))
    return paths, added, sizes


def clone_links(repo):
    links = {}
    for folder, dirs, files in os.walk(repo, followlinks=False):
        dirs[:] = [name for name in dirs if name != ".git"]
        for name in dirs + files:
            path = Path(folder, name)
            if path.is_symlink():
                links[path.relative_to(repo).as_posix()] = os.readlink(path)
    return links


def scan_patch(patch_text, base="HEAD", *, changed_files=None, rels=None, ci=False, allow_protected=()):
    """Return redacted finding dicts for patch text/bytes or a clone directory.

    For a clone, base is a Git revision (default HEAD); changed_files optionally
    selects repo-relative paths. A list as the first argument uses base as the
    clone directory and compares those files against HEAD. All clone reads are
    read-only, including the index. Binary limit is 1 MiB, strictly greater.
    Symlink targets require explicit lease roots (rels); unknown scope fails closed.
    Invalid input or scanner failures raise Precondition rather than pass.
    """
    links = {}
    if isinstance(patch_text, (list, tuple)):
        selected = [_path(os.fspath(p)) for p in patch_text]
        paths, added, sizes = _clone(base, "HEAD", selected)
        links = clone_links(Path(base))
    elif isinstance(patch_text, os.PathLike) or (
        isinstance(patch_text, str) and "\n" not in patch_text and len(patch_text) < 4096 and os.path.isdir(patch_text)
    ):
        selected = None if changed_files is None else [_path(os.fspath(p)) for p in changed_files]
        paths, added, sizes = _clone(patch_text, base, selected)
        links = clone_links(Path(patch_text))
    else:
        if changed_files is not None:
            raise Precondition("changed_files requires a clone directory")
        text = patch_text.decode("utf-8", "replace") if isinstance(patch_text, bytes) else patch_text
        paths, added, sizes = _parse(text)
        links = patch_links(text)
    findings = [
        _finding("path", path, None, "runner-denylist") for path in sorted(paths) if denied(path, allow_protected)
    ]
    findings.extend(
        _finding("path", path, None, "unsafe-symlink")
        for path in check_links(
            links, rels if rels is not None else [], ci, paths=paths, allow_protected=allow_protected
        )
    )
    findings.extend(
        _finding("binary", path, None, "binary-over-1mb") for path, size in sizes if size > MAX_BINARY_BYTES
    )
    findings.extend(_secrets(added))
    unique = {(f["kind"], f["path"], f["line"], f["rule"]): f for f in findings}
    return sorted(unique.values(), key=lambda f: (f["path"], f["line"] or 0, f["kind"], f["rule"]))


def register_cli(add_command):
    """Register with raidlock.cli.add_command; the host handles --json and _exit."""

    def setup(parser):
        parser.add_argument("source", help="patch file or clone directory")
        parser.add_argument("--card", help="card whose locks authorize symlink targets")
        parser.add_argument("--base", default="HEAD", help="clone comparison revision (default HEAD)")

    def handler(ctx, args):
        source = Path(args.source)
        from .runners import policy

        rels, ci, allowed = [], False, []
        if args.card:
            board = ctx.board
            target = policy.target(board.workspace, board.dir, board.card(args.card)["locks"])
            rels, ci = target["rels"], target["ci"]
            allowed = board.card(args.card).get("allow_protected", [])
        try:
            findings = scan_patch(
                source if source.is_dir() else source.read_bytes(), args.base, rels=rels, ci=ci, allow_protected=allowed
            )
        except OSError:
            raise Precondition("cannot read scan input") from None
        result = {"findings": findings, "_exit": 2 if findings else 0}
        text = (
            "\n".join(f"{f['path']}:{f['line'] or '-'}: {f['kind']} {f['rule']} {f['snippet']}" for f in findings)
            or "No patch safety findings."
        )
        return result, text

    add_command("scan", "check a patch or clone for secrets, denied paths and large binaries", setup, handler)


def patch_links(text):
    """Extract complete new symlink blobs; reject partial/binary link patches."""
    links = {}
    for block in re.split(r"(?m)(?=^diff --git )", text):
        if not re.search(r"(?m)^(?:new file mode|new mode) 120000$|^index .* 120000$", block):
            continue
        paths, added, _ = _parse(block)
        header = re.search(r"(?m)^\+\+\+ (.*)$", block)
        if not header:
            raise Precondition("symlink patch must contain its target")
        path = _path(header[1], True)
        if path is None:
            continue
        rows = [value for p, _, value in added if p == path]
        if len(rows) != 1:
            raise Precondition("symlink patch must contain one complete target")
        links[path] = rows[0]
    return links
