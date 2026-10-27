import os
import re
from urllib.parse import urlsplit

from . import paths as P
from . import store

_SCP = re.compile(r"^(?:[^@/]+@)?([^:/]+):(?!//)(.+)$")
_REMOTE = re.compile(r'^\s*\[remote\s+"([^"]+)"\]\s*$')
_URL = re.compile(r"^\s*url\s*=\s*(.+?)\s*$")
_SECTION = re.compile(r"^\s*\[")


def normalize_remote(url):
    """Normalize a git remote URL to `host/owner/name`, lowercase, without scheme, user, port or `.git`."""
    text = url.strip().replace("\\", "/")
    text = re.sub(r"(\.git)?/*$", "", text)
    if "://" in text:
        parts = urlsplit(text)
        if parts.scheme == "file":
            return "path:" + parts.path.rstrip("/")
        host = (parts.hostname or "").lower()
        return (host + "/" + parts.path.strip("/")).lower()
    m = _SCP.match(text)
    if m and not os.path.isabs(text):
        return (m.group(1) + "/" + m.group(2).strip("/")).lower()
    return "path:" + os.path.realpath(text).replace("\\", "/")


def git_dir(repo):
    dotgit = os.path.join(repo, ".git")
    if os.path.isdir(dotgit):
        return dotgit
    if os.path.isfile(dotgit):
        try:
            with open(dotgit, encoding="utf-8") as f:
                line = f.read().strip()
        except OSError:
            return None
        if line.startswith("gitdir:"):
            target = line[7:].strip()
            return os.path.normpath(os.path.join(repo, target))
    return None


def common_dir(gdir):
    try:
        with open(os.path.join(gdir, "commondir"), encoding="utf-8") as f:
            return os.path.normpath(os.path.join(gdir, f.read().strip()))
    except OSError:
        return gdir


def remote_url(repo):
    gdir = git_dir(repo)
    if not gdir:
        return None
    remotes = _read_remotes(os.path.join(common_dir(gdir), "config"))
    return remotes.get("origin") or (next(iter(remotes.values())) if remotes else None)


def _read_remotes(config):
    remotes, current = {}, None
    try:
        with open(config, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return remotes
    for line in lines:
        m = _REMOTE.match(line)
        if m:
            current = m.group(1)
            continue
        if _SECTION.match(line):
            current = None
            continue
        u = _URL.match(line)
        if current and u and current not in remotes:
            remotes[current] = u.group(1)
    return remotes


def identity(repo):
    """Repo identity: normalized remote URL, else `path:<absolute path>`."""
    url = remote_url(repo)
    if url:
        return normalize_remote(url)
    return "path:" + os.path.realpath(repo).replace("\\", "/")


def find_root(path):
    cur = os.path.realpath(path)
    while True:
        if git_dir(cur):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def checkout_info(path):
    """Describe the checkout containing `path`: {path, kind: clone|worktree, branch}, or None."""
    root = find_root(path)
    if not root:
        return None
    gdir = git_dir(root)
    kind = "worktree" if os.path.isfile(os.path.join(root, ".git")) and common_dir(gdir) != gdir else "clone"
    return {"path": root, "kind": kind, "branch": _branch(gdir)}


def _branch(gdir):
    try:
        with open(os.path.join(gdir, "HEAD"), encoding="utf-8") as f:
            head = f.read().strip()
    except OSError:
        return ""
    if head.startswith("ref:"):
        return head.split("refs/heads/", 1)[-1]
    return head[:12]


def case_insensitive(folder):
    probe = os.path.join(folder, ".board")
    if not os.path.exists(probe):
        probe = folder
    head, tail = os.path.split(probe.rstrip(os.sep))
    swapped = os.path.join(head, tail.swapcase())
    if swapped == probe or not os.path.exists(swapped):
        return False
    try:
        return os.path.samefile(probe, swapped)
    except OSError:
        return False


def discover(workspace, declared=None):
    """Map repo name -> absolute path: the workspace itself, git repos at depth 1-2, plus declared repos."""
    workspace = os.path.realpath(workspace)
    found = {}
    if git_dir(workspace):
        found[os.path.basename(workspace)] = workspace
    for name in _children(workspace):
        full = os.path.join(workspace, name)
        if git_dir(full):
            found[name] = full
            continue
        for sub in _children(full):
            if git_dir(os.path.join(full, sub)):
                found[name + "/" + sub] = os.path.join(full, sub)
    for name, rel in (declared or {}).items():
        found[P.canonical(name)] = os.path.realpath(os.path.join(workspace, os.path.expanduser(rel)))
    return found


def _children(folder):
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [n for n in names if not n.startswith(".") and os.path.isdir(os.path.join(folder, n))]


def declared_repos(board_dir):
    data = store.read_json(os.path.join(board_dir, "workspace.json"), {})
    store.check_schema(data, "workspace.json")
    return data.get("repos", {})


class Resolver:
    """Resolve `<repo>/<path>` lock paths of one workspace to repo identities."""

    def __init__(self, workspace, board_dir):
        self.workspace = os.path.realpath(workspace)
        self.ci = case_insensitive(self.workspace)
        self.repos = discover(self.workspace, declared_repos(board_dir))
        self._ids = {}

    def _identity(self, name):
        if name not in self._ids:
            self._ids[name] = identity(self.repos[name])
        return self._ids[name]

    def _match(self, parts):
        names = sorted(self.repos, key=lambda n: -len(P.split(n)))
        for name in names:
            want = P.split(name)
            have = parts[: len(want)]
            if want == have or (self.ci and [w.casefold() for w in want] == [h.casefold() for h in have]):
                return name, len(want)
        root = next((n for n, p in self.repos.items() if p == self.workspace), None)
        if root:
            return root, 0
        if len(self.repos) == 1:
            return next(iter(self.repos)), 0
        return None, 1

    def resolve(self, raw):
        """Return {path, id, rel, ci} for a lock path."""
        path = P.canonical(raw)
        parts = P.split(path)
        name, used = self._match(parts)
        if name:
            ident = self._identity(name)
        else:
            ident = "path:" + os.path.realpath(os.path.join(self.workspace, parts[0])).replace("\\", "/")
        rel = "/".join(parts[used:])
        if self.ci:
            ident = ident.casefold() if ident.startswith("path:") else ident
            rel = rel.casefold()
        return {"path": path, "id": ident, "rel": rel, "ci": self.ci}


def locate(abs_path):
    """Map an absolute file path to {id, rel, ci, root} through its checkout's git remote, or None."""
    root = find_root(os.path.dirname(abs_path) if not os.path.isdir(abs_path) else abs_path)
    if not root:
        return None
    real = os.path.realpath(abs_path)
    rel = os.path.relpath(real, root).replace("\\", "/")
    rel = "" if rel == "." else rel
    ci = case_insensitive(root)
    ident = identity(root)
    if ci:
        rel = rel.casefold()
        ident = ident.casefold() if ident.startswith("path:") else ident
    return {"id": ident, "rel": rel, "ci": ci, "root": root}


def keys_overlap(a, b):
    if a["id"] != b["id"]:
        return False
    fold = a.get("ci") or b.get("ci")
    if any(P.contains(path, b["rel"], fold) for path in a.get("exclude", [])):
        return False
    if any(P.contains(path, a["rel"], fold) for path in b.get("exclude", [])):
        return False
    if not a["rel"] or not b["rel"]:
        return True
    return P.overlaps(a["rel"], b["rel"], fold)


def key_contains(outer, inner):
    if outer["id"] != inner["id"]:
        return False
    if any(P.overlaps(path, inner["rel"], outer.get("ci") or inner.get("ci")) for path in outer.get("exclude", [])):
        return False
    if not outer["rel"]:
        return True
    return P.contains(outer["rel"], inner["rel"], outer.get("ci") or inner.get("ci"))
