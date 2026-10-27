import fnmatch
import os

from ..core import paths as P
from ..core import repos
from ..core.errors import Precondition

DENY_PREFIXES = (".github/workflows", ".claude", ".board")
DENY_NAMES = ("*.pem", ".env*", "secrets", "secrets.*")
DENY_SEGMENTS = ("secrets",)


def denied(path, allow_protected=()):
    """True when a repo-relative path matches a deny rule, whatever the card's locks say."""
    text = path.replace("\\", "/").strip("/").lower()
    parts = P.split(text)
    if not parts:
        return False
    for prefix in DENY_PREFIXES:
        if (text == prefix or text.startswith(prefix + "/")) and (prefix == ".board" or prefix not in allow_protected):
            return True
    if any(seg in DENY_SEGMENTS for seg in parts[:-1]):
        return True
    return any(fnmatch.fnmatchcase(parts[-1], pattern) for pattern in DENY_NAMES)


def target(workspace, board_dir, locks):
    """The single repo a card's locks live in: {repo, path, rels, ci}. Raises when locks span repos or none."""
    if not locks:
        raise Precondition("the card has no locks; add some with `raidlock lock <card> <repo>/<path>`")
    resolver = repos.Resolver(workspace, board_dir)
    found = resolver.repos
    ci = repos.case_insensitive(os.path.realpath(workspace))
    hits = {}
    for raw in locks:
        parts = P.split(P.canonical(raw))
        name, used = resolver._match(parts)
        if name is None:
            raise Precondition(
                f"lock {raw} matches no unambiguous repo in workspace {workspace}",
                lock=raw,
                hint="prefix the lock with a workspace repo: "
                + (", ".join(sorted(found)) or "initialize or declare a git repo in this workspace"),
            )
        hits.setdefault(name, []).append("/".join(parts[used:]))
    if len(hits) > 1:
        raise Precondition(
            "the card's locks span several repos (" + ", ".join(sorted(hits)) + "); split the quest per repo",
            repos=sorted(hits),
        )
    name, rels = next(iter(hits.items()))
    return {"repo": name, "path": found[name], "rels": sorted(set(rels)), "ci": ci}


def _prefix(want, have, ci):
    if len(want) > len(have):
        return False
    a, b = want, have[: len(want)]
    if ci:
        a, b = [x.casefold() for x in a], [x.casefold() for x in b]
    return a == b


def contained(path, rels, ci=False):
    return any(not rel or P.contains(rel, path, ci) for rel in rels)


def check(paths, rels, ci=False, allow_protected=()):
    """{outside: [...], denied: [...]} for repo-relative changed paths."""
    return {
        "outside": sorted(p for p in paths if not contained(p, rels, ci)),
        "denied": sorted(p for p in paths if denied(p, allow_protected)),
    }


def link_target(path, target, links=None):
    """Resolve components in filesystem order: expand links before processing '..'."""
    import posixpath
    from collections import deque

    def absolute(value):
        return "\\" in value or value.startswith(("/", "~")) or (len(value) > 1 and value[1] == ":")

    if absolute(target):
        return None
    links = links or {}
    pending = deque((posixpath.dirname(path) + "/" + target).split("/"))
    resolved, expansions = [], 0
    while pending:
        part = pending.popleft()
        if part in ("", "."):
            continue
        if part == "..":
            if not resolved:
                return None
            resolved.pop()
            continue
        resolved.append(part)
        prefix = "/".join(resolved)
        if prefix in links:
            expansions += 1
            value = links[prefix]
            if expansions > 64 or absolute(value):
                return None
            resolved.pop()
            pending.extendleft(reversed(value.split("/")))
    return "/".join(resolved) or "."


def check_links(links, rels=None, ci=False, paths=None, allow_protected=(), excluded=()):
    bad = []
    for path, target in links.items():
        if paths is not None and path not in paths:
            continue
        resolved = link_target(path, target, links)
        if (
            resolved is None
            or contained(resolved, excluded, ci)
            or denied(resolved, allow_protected)
            or (rels is not None and not contained(resolved, rels, ci))
        ):
            bad.append(path)
    return sorted(bad)
