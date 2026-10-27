"""Read-only lock suggestions from tracked paths, recent history and CODEOWNERS."""

import fnmatch
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

from .core import canonical
from .core.errors import Precondition
from .core.repos import Resolver
from .runners.policy import denied
from .runners.workspace import git_run


def _git(root, *args):
    try:
        result = git_run(root, *args, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _tokens(text):
    result = set()
    for token in re.findall(r"[\w./\\-]+", text.replace("\\", "/")):
        token = token.rstrip(".")
        while token.startswith("./"):
            token = token[2:]
        if token and ".." not in PurePosixPath(token).parts and not token.startswith("/"):
            result.add(token)
    return result


def _glob(parts, pattern):
    if not pattern:
        return not parts
    if pattern[0] == "**":
        return _glob(parts, pattern[1:]) or bool(parts) and _glob(parts[1:], pattern)
    return bool(parts) and fnmatch.fnmatchcase(parts[0], pattern[0]) and _glob(parts[1:], pattern[1:])


def _matches(pattern, path):
    if "[" in pattern or "]" in pattern:
        return False
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    directory = pattern.endswith("/")
    pattern = pattern.strip("/")
    if not pattern:
        return False
    parts = path.split("/")
    if anchored:
        ends = range(1, len(parts) + (0 if directory else 1))
        return any(_glob(parts[:end], pattern.split("/")) for end in ends)
    candidates = parts[:-1] if directory else parts
    return any(fnmatch.fnmatchcase(part, pattern) for part in candidates)


def _tracked_file(root, file):
    try:
        canonical(file)
        if denied(file):
            return False
        parts = PurePosixPath(file).parts
        if any(
            part.lower() in (".git", ".board", ".claude", "secret", "secrets")
            or part.lower().startswith(".env")
            or part.lower().endswith(".pem")
            for part in parts
        ) or parts[:2] == (".github", "workflows"):
            return False
        path = Path(root, file)
        root = Path(root).resolve()
        if not path.is_file() or path.is_symlink() or root not in path.resolve().parents:
            return False
        return not any(parent.is_symlink() for parent in path.parents if parent != root and root in parent.parents)
    except (OSError, RuntimeError, Precondition):
        return False


def _owners(root, files):
    for location in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        path = Path(root, location)
        if path.is_file() and not path.is_symlink() and Path(root).resolve() in path.resolve().parents:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                return {}
            break
    else:
        return {}
    assignments = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") or line.startswith("^["):
            continue
        fields = line.split("#", 1)[0].split()
        if not fields or fields[0].startswith("!"):
            continue
        for file in files:
            if _matches(fields[0], file):
                owners = tuple(
                    sorted({owner for owner in fields[1:] if re.fullmatch(r"@[A-Za-z0-9_/-]+|[^\s@]+@[^\s@]+", owner)})
                )
                assignments[file] = (owners, ", ".join(owners)) if owners else None
    return assignments


def suggest_locks(board, text, repos=None, limit=5):
    """Return ranked {path, score, reason} dictionaries without changing board state.

    repos optionally selects repository names from the board's resolver. Scores
    prioritize explicit paths, then co-change frequency, then shared CODEOWNERS owners.
    Bulk commits over 50 paths, protected files and symlinks are excluded. Directory
    collapse never covers additional tracked files; suggestions never mutate the board.
    """
    if not isinstance(text, str):
        raise Precondition("suggestion text must be text")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise Precondition("limit must be a non-negative integer")
    if not text.strip() or limit == 0:
        return []
    resolver = Resolver(board.workspace, board.dir)
    names = sorted(resolver.repos) if repos is None else ([repos] if isinstance(repos, str) else list(repos))
    if any(not isinstance(name, str) for name in names):
        raise Precondition("repository names must be text")
    unknown = set(names) - resolver.repos.keys()
    if unknown:
        raise Precondition("unknown repositories: " + ", ".join(sorted(unknown)))
    tokens = _tokens(text)
    suggestions = []
    for name in sorted(set(names)):
        root = resolver.repos[name]
        tracked_files = set(filter(None, _git(root, "ls-files", "-z").split("\0")))
        files = {file for file in tracked_files if _tracked_file(root, file)}
        scores, reasons = {}, {}
        for file in sorted(files):
            path = PurePosixPath(file)
            aliases = {file, name + "/" + file, path.name}
            directories = {str(parent) for parent in path.parents if str(parent) != "."}
            if tokens & aliases:
                scores[file], reasons[file] = 100, "Path mentioned in text"
            elif tokens & (directories | {name + "/" + d for d in directories}):
                scores[file], reasons[file] = 90, "Inside directory mentioned in text"
        seeds = set(scores)
        if not seeds:
            continue
        counts = Counter()
        history = _git(root, "log", "-200", "--format=%x00", "--name-only", "-z", "--no-renames")
        for commit in history.split("\0\0"):
            raw_changed = {item.lstrip("\n") for item in commit.split("\0")} - {""}
            changed = raw_changed & files
            if changed & seeds and len(raw_changed) <= 50:
                counts.update(changed - seeds)
        for file, count in counts.items():
            scores[file] = 40 + min(count, 39)
            reasons[file] = f"Changed with mentioned files in {count} recent commit(s)"
        owners = _owners(root, files)
        groups = {owners[file] for file in seeds if owners.get(file)}
        for file in sorted(files - seeds):
            if owners.get(file) in groups:
                scores[file] = scores.get(file, 0) + 10
                reason = "Same CODEOWNERS ownership group"
                reasons[file] = reasons[file] + "; " + reason if file in reasons else reason
        siblings = defaultdict(list)
        for file in scores:
            siblings[str(PurePosixPath(file).parent)].append(file)
        for directory, children in sorted(siblings.items()):
            named = directory in tokens or name + "/" + directory in tokens
            if len(children) < 3 or directory == "." or ("/" not in directory and not named):
                continue
            if any(file.startswith(directory + "/") and file not in children for file in tracked_files):
                continue
            scores[directory] = max(scores[child] for child in children)
            reasons[directory] = (
                f"{len(children)} sibling files; " + reasons[max(children, key=lambda child: (scores[child], child))]
            )
            for child in children:
                del scores[child]
        for file, score in scores.items():
            try:
                path = canonical(name + "/" + file)
            except Precondition:
                continue
            suggestions.append({"path": path, "score": score, "reason": reasons[file]})
    return sorted(suggestions, key=lambda item: (-item["score"], item["path"]))[:limit]


def register_cli(add_command):
    """Register with the raidlock.commands extension interface."""

    def setup(parser):
        parser.add_argument("text")
        parser.add_argument("--repos", nargs="+")
        parser.add_argument("--limit", type=int, default=5)

    def handler(ctx, args):
        suggestions = suggest_locks(ctx.board, args.text, args.repos, args.limit)
        return {"suggestions": suggestions}, "\n".join(
            item["path"] + ": " + item["reason"] for item in suggestions
        ) or "No lock suggestions."

    add_command("suggest-locks", "suggest narrow lock paths from card text", setup, handler)
