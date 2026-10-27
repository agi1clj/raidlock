#!/usr/bin/env bash
set -euo pipefail
python3 - "$(cd -- "$(dirname -- "$0")/.." && pwd)" "$@" <<'PY'
import io
import re
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit("Usage: bash scripts/curate-public.sh TARGET")
source = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2]).resolve()
if target == source or source in target.parents or target in source.parents:
    raise SystemExit("Target must be outside the source tree")
if target.exists() and (not target.is_dir() or any(target.iterdir())):
    raise SystemExit("Target must be absent or an empty directory")
ALLOWLIST = (
    "src/", "tests/", "docs/", "examples/", ".github/",
    ".claude-plugin/", "plugin/", "plugins/", "skill/", "skills/",
    "agent/", "agents/", "command/", "commands/", "hooks/", "bin/",
    ".gitignore", ".gitleaks.toml", "pyproject.toml", "uv.lock", "README.md", "LICENSE", "CHANGELOG.md",
    "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SECURITY.md", "PROTOCOL.md",
    "Dockerfile", ".dockerignore", "scripts/docker-smoke.sh",
    "scripts/curate-public.sh", "scripts/bench_overhead.py", "scripts/demo-60s.sh", "scripts/overhead_ab.sh", "scripts/demo.tape", "scripts/demo-tape-setup.sh", "scripts/render-hero.sh", ".release-please-manifest.json", "release-please-config.json",
)
EXCLUDED = {
    "docs/PREMORTEM.md", "docs/LEAD-PLAN.md", "docs/AUDIT.md",
    "docs/RESEARCH.md", "docs/VISION.md", "legacy", ".board", "BOARD.md",
}
scratch = re.compile(r"(?i)(^|[._-])(scratch|tmp|temp|bak|backup|swp|swo|orig|rej)([._-]|$)")
tracked = subprocess.check_output(["git", "-C", str(source), "ls-files", "-z"])
candidates = {p.decode() for p in tracked.split(b"\0") if p}
candidates.update({"scripts/curate-public.sh", ".release-please-manifest.json", "release-please-config.json", "docs/RELEASING.md"})
target.mkdir(parents=True, exist_ok=True)
for name in sorted(candidates):
    parts = Path(name).parts
    if name in EXCLUDED or any(p in EXCLUDED or p in {"__pycache__", ".pytest_cache", ".DS_Store"} or scratch.search(p) or p.endswith("~") for p in parts):
        continue
    if not any(name.startswith(entry) if entry.endswith("/") else name == entry for entry in ALLOWLIST):
        continue
    origin = source / name
    if origin.is_symlink() or any(p.is_symlink() for p in origin.parents if p != source and source in p.parents):
        raise SystemExit(f"Symlink is not publishable: {name}")
    if not origin.is_file():
        raise SystemExit(f"Missing release input: {name}")
    destination = target / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origin, destination)
client = re.compile(r"south[c]o|key[p]anion|key[p]-\d+|rebel[d]ot|do[k]a", re.I)
home = re.compile(r"/(?:User[s]|hom[e])/([^/\s\"'`<>]+)")
username = re.compile(r"gabriel[.]arcas")
placeholders = {"x", "user", "me", "alice", "bob", "runner", "private", "raid"}
account = re.compile(r"(?<![\w])\d{12}(?![\w])")
aws_context = re.compile(r"arn:|account|\.amazonaws\.com|dkr\.ecr", re.I)
email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}")
userinfo = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/]*@[^\s/]+|\bgit@[^\s/:]+(?=[:/\s]|$)")
allowed_email = "38486343+agi1clj" + "@" + "users.noreply.github.com"
conduct_email = "arcas.gabi" + "@" + "gmail.com"

MEDIA = {".gif", ".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm"}


def scan(name, line, number):
    prefix = f"{name}:{number}"
    for match in client.finditer(line):
        failures.append(f"{prefix}: company or internal project: {match.group()}")
    for match in username.finditer(line):
        failures.append(f"{prefix}: real username: {match.group()}")
    for match in home.finditer(line):
        if match[1].split(":")[0] not in placeholders:
            failures.append(f"{prefix}: local home path: {match.group()}")
    if aws_context.search(line):
        for match in account.finditer(line):
            value = match.group()
            if int(value) >= 1000000 and len(set(value)) > 1:
                failures.append(f"{prefix}: account identifier: {value}")
    url_spans = [match.span() for match in userinfo.finditer(line)]
    if Path(name).suffix.lower() in MEDIA and name != line:
        return
    for match in email.finditer(line):
        address = match.group()
        domain = address.rsplit("@", 1)[1].lower()
        if domain in {"example.com", "example.org", "example.test"} or domain.endswith(".invalid"):
            continue
        if any(start <= match.start() and match.end() <= end for start, end in url_spans):
            continue
        if address == allowed_email or (name == "CODE_OF_CONDUCT.md" and address == conduct_email):
            continue
        failures.append(f"{prefix}: disallowed email: {address}")

failures = []
for path in sorted(target.rglob("*")):
    if not path.is_file():
        continue
    name = path.relative_to(target).as_posix()
    data = path.read_bytes()
    content = data.decode("utf-8", errors="replace")
    scan(name, name, 1)
    for number, line in enumerate(content.splitlines(), 1):
        scan(name, line, number)
    if path.suffix == ".py":
        try:
            for token in tokenize.tokenize(io.BytesIO(data).readline):
                if token.type == tokenize.COMMENT:
                    failures.append(f"{name}:{token.start[0]}: Python comment")
        except (SyntaxError, UnicodeError, tokenize.TokenError) as error:
            failures.append(f"{name}:1: cannot tokenize: {error}")
if failures:
    print("Public tree rejected:\n" + "\n".join(failures), file=sys.stderr)
    raise SystemExit(1)
print(f"Public tree validated: {target}")
PY
