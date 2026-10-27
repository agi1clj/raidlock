"""Open a pull request from a card's verified commit: receipt gate, lock check, patch scan, branch push, gh."""

import os
import re
import subprocess
import tempfile
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from .core import clock, exe, model, receipts, repos
from .core import transcript as T
from .core.errors import Precondition
from .runners import policy
from .runners import workspace as W
from .runners.base import SECRET_NAME
from .scan import scan_patch

REDACT_KINDS = ("agent", "model", "provider")
REDACTED = "[redacted]"
REF_NAME = re.compile(r"^(?![-./])(?!.*\.\.)(?!.*//)(?!.*@\{)[A-Za-z0-9._/-]{1,120}(?<![./])$")
REMOTE_NAME = re.compile(r"^(?![-.])[A-Za-z0-9._-]{1,60}$")
TERM = re.compile(r"^[^\x00-\x1f\x7f,]{2,80}$")
VENDOR = (
    r"claude[\w.-]*|anthropic|codex[\w.-]*|openai|chatgpt|gpt[\w.-]*|o[1-9](?:-[\w.]+)?|gemini[\w.-]*|"
    r"opus[\w.-]*|sonnet[\w.-]*|haiku[\w.-]*|copilot|llama[\w.-]*|mistral[\w.-]*|deepseek[\w.-]*|qwen[\w.-]*"
)
PR_URL = re.compile(r"https?://[^\s]+/pull/(\d+)")
MAX_FILES = 60
GH_TIMEOUT = 120
GIT_TIMEOUT = 300


_SECRET_VALUES = ContextVar("pr_secret_values", default=())


def _normalize(text):
    return "".join(c for c in unicodedata.normalize("NFKC", str(text)) if unicodedata.category(c) != "Cf")


def sanitize(text, env=None):
    text = _normalize(text or "")
    values = list(_SECRET_VALUES.get())
    for source in (os.environ, env or {}):
        values.extend(v for k, v in source.items() if SECRET_NAME.search(k) and v)
    for value in sorted({_normalize(v) for v in values if v}, key=len, reverse=True):
        if value:
            text = text.replace(value, T.REDACTED)
    return T.redact(text)


def protected(fn):
    @wraps(fn)
    def call(board, *args, **kwargs):
        values = list(_SECRET_VALUES.get())
        values.extend(v for k, v in board.env.items() if SECRET_NAME.search(k) and v)
        for arg in args:
            values.extend(getattr(arg, field, "") for field in ("lead_secret", "token"))
        for path in Path(board.home, "tokens", board.uid).glob("*.token"):
            try:
                values.append(path.read_text(encoding="utf-8").strip())
            except OSError:
                continue
        token = _SECRET_VALUES.set(tuple(values))
        try:
            return fn(board, *args, **kwargs)
        except Precondition as exc:
            exc.message = sanitize(exc.message)
            exc.args = (exc.message,)
            raise
        finally:
            _SECRET_VALUES.reset(token)

    return call


def local_git(cwd, *args, binary=False, **kwargs):
    proc = remote_git(cwd, *args, env=W.git_env(), binary=binary, **kwargs)
    return proc.stdout


def _words(value):
    items = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    return [str(w).strip() for w in items if str(w).strip()]


def setting_value(key, value):
    """Validate a `pr.*` board setting; returns the stored value ("" or [] clears it)."""
    if key == "pr.redact":
        words = [w.lower() for w in _words(value)]
        if words in (["none"], ["off"], []):
            return []
        if "all" in words:
            return list(REDACT_KINDS)
        bad = [w for w in words if w not in REDACT_KINDS]
        if bad:
            raise Precondition(f"pr.redact takes {', '.join(REDACT_KINDS)}, all or none", value=",".join(bad))
        return sorted(set(words))
    if key == "pr.terms":
        words = _words(value)
        if len(words) > 50:
            raise Precondition("pr.terms takes at most 50 terms")
        for w in words:
            if not TERM.match(w):
                raise Precondition("each pr.terms entry is 2-80 printable characters", term=w[:80])
        return sorted(set(words), key=str.lower)
    text = str(value or "").strip()
    if key == "pr.base":
        if text and not REF_NAME.match(text):
            raise Precondition("pr.base must be a plain branch name", value=text[:120])
        return text
    if key == "pr.remote":
        if text and not REMOTE_NAME.match(text):
            raise Precondition("pr.remote must be a plain remote name", value=text[:60])
        return text
    raise Precondition(f"unknown setting {key}", key=key)


def config(board, redact=None, terms=None, base=None, remote=None):
    """Board `pr.*` settings merged with flags. Flags add redaction, they never remove what the board requires."""
    s = board.settings
    kinds = set(s.get("pr.redact") or [])
    if redact:
        kinds |= set(setting_value("pr.redact", redact))
    extra = list(s.get("pr.terms") or [])
    if terms:
        extra += setting_value("pr.terms", terms)
    return {
        "redact": sorted(kinds),
        "terms": sorted(set(extra), key=str.lower),
        "base": setting_value("pr.base", base) if base else s.get("pr.base", ""),
        "remote": setting_value("pr.remote", remote) if remote else (s.get("pr.remote") or "origin"),
    }


def gh_env(env=None):
    env = dict(os.environ if env is None else env)
    return {k: v for k, v in env.items() if not k.startswith("RAIDLOCK_")}


def gh(args, cwd=None, env=None, timeout=GH_TIMEOUT):
    """Run gh with an argv list; None when gh is not installed."""
    try:
        return subprocess.run(
            [exe.find("gh"), *args],
            cwd=cwd,
            env=gh_env(env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (FileNotFoundError, PermissionError, NotADirectoryError):
        return None
    except subprocess.TimeoutExpired:
        raise Precondition("gh command timed out") from None
    except OSError as exc:
        raise Precondition(f"gh command failed: {sanitize(str(exc), env)[:300]}") from None


def gh_ready(env=None):
    proc = gh(["auth", "status"], env=env, timeout=30)
    return bool(proc) and proc.returncode == 0


def remote_git(cwd, *args, env=None, check=True, binary=False):
    """git with the user's credentials (pushes and fetches need them) but no hooks, fsmonitor or prompts."""
    full = gh_env(env)
    full["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = W.git_run(cwd, *args, env=full, binary=binary, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise Precondition("git command timed out") from None
    except OSError as exc:
        raise Precondition(f"git command failed: {sanitize(str(exc), env)[:300]}") from None
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace") if binary else proc.stderr
        raise Precondition(sanitize(f"git {args[0]} failed: {err.strip()}", env)[:300])
    return proc


def verified(board, card):
    """The card's passing, signed verify receipt and the checkout it covers. Same gate as `done`."""
    cid = card["id"]
    if card["lane"] not in ("review", "done"):
        raise Precondition(f"{cid} is in {card['lane']}; a pull request opens from review or done", card=cid)
    if not card.get("verify_cmd"):
        raise Precondition(
            f"{cid} has no verify command; a pull request needs a passing verify receipt",
            card=cid,
            hint="the lead sets one with raidlock new --verify-cmd, then runs raidlock verify",
        )
    last = model.last_verification(card)
    if not last or not receipts.valid(board, card, last):
        raise Precondition(f"{cid} needs a valid verification receipt", card=cid, hint=f"raidlock verify {cid}")
    if not last.get("passed") or last.get("exit") != 0:
        raise Precondition(f"the last verify run of {cid} failed", card=cid, hint=f"raidlock verify {cid}")
    source = board._verification_source(card)
    if last.get("repo") != board._verification_repo(card, source):
        raise Precondition(f"{cid} verification receipt repo does not match the card")
    if last.get("sha") != local_git(source, "rev-parse", "HEAD").strip():
        raise Precondition(
            f"{cid} verification receipt SHA does not match current HEAD", card=cid, hint=f"raidlock verify {cid}"
        )
    return last, source


def _redactor(board, card, cfg, run_info):
    terms = set(cfg["terms"])
    agents = board.agents()
    if "agent" in cfg["redact"]:
        terms |= set(agents)
        terms |= {card.get("owner") or ""}
        terms |= set(card.get("reviewers") or [])
        terms |= {r.get("by", "") for r in card.get("reviews", [])}
        terms |= {v.get("by", "") for v in card.get("verifications", [])}
        terms |= {run_info.get("agent", "")}
    if "model" in cfg["redact"]:
        terms |= {a.get("model", "") for a in agents.values()}
        terms |= {r.get("model", "") for r in card.get("reviews", [])}
        terms |= {run_info.get("model", "")}
    if "provider" in cfg["redact"]:
        terms |= {a.get(k, "") for a in agents.values() for k in ("provider", "runtime")}
        terms |= {r.get("provider", "") for r in card.get("reviews", [])}
        terms |= {run_info.get(k, "") for k in ("provider", "runtime")}
    terms = sorted({_normalize(t) for t in terms if t and _normalize(t)}, key=lambda t: (-len(t), t))
    parts = [re.escape(t) for t in terms]
    if {"model", "provider"} & set(cfg["redact"]):
        parts.append(VENDOR)
    if not parts:
        return None
    return re.compile("(?:" + "|".join(parts) + ")", re.IGNORECASE)


def _scrub(pattern, text):
    return pattern.sub(REDACTED, _normalize(text)) if pattern else _normalize(text)


def _run_info(board, card_id):
    import json

    from .runners.runner import latest_patch

    folder, _ = latest_patch(board, card_id)
    if not folder:
        return {}
    try:
        with open(os.path.join(folder, "run.json"), encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def _md(text, limit=300):
    text = sanitize(str(text or "")).replace("\r", " ").replace("\n", " ").strip()
    text = text.replace("`", "'")
    return text[:limit]


@protected
def render(board, card, receipt, check, cfg, run_info):
    """(title, body) for the pull request. Redaction drops whole fields and scrubs every remaining line."""
    kinds = set(cfg["redact"])
    agents = board.agents()
    owner = card.get("owner") or ""
    rec = agents.get(owner, {})
    model_id = rec.get("model") or run_info.get("model") or ""
    need = board.settings["require_review"]
    approvals = board.approvals(card)
    lines = [f"Card `{card['id']}`: {_md(card['title'])}", ""]
    if card.get("scope"):
        lines += [_md(card["scope"], 600), ""]
    who = []
    if "agent" not in kinds:
        who.append(f"- Agent: {owner or '-'}")
    if "provider" not in kinds:
        who.append(f"- Runtime: {rec.get('runtime') or run_info.get('runtime') or '-'}")
        who.append(f"- Provider: {rec.get('provider') or run_info.get('provider') or '-'}")
    if "model" not in kinds:
        who.append(f"- Model: {model_id or '-'}")
    if who:
        lines += ["### Agent", *who, ""]
    files = check["changed"]
    lines += [
        "### Lock check",
        f"- Locks: {', '.join(f'`{_md(x, 200)}`' for x in card.get('locks', [])) or '-'}",
        f"- Changed files ({len(files)}): "
        + ", ".join(f"`{_md(p, 200)}`" for p in files[:MAX_FILES])
        + (f" and {len(files) - MAX_FILES} more" if len(files) > MAX_FILES else ""),
        f"- Outside locks: {len(check['outside'])}",
        f"- Denied paths: {len(check['denied'])}",
        f"- Patch scan findings: {len(check['findings'])}",
        "",
        "### Verify receipt",
        f"- Command: `{_md(receipt.get('cmd'), 300)}`",
        f"- Commit: `{receipt.get('sha', '')}`",
        f"- Result: exit {receipt.get('exit')} in {receipt.get('duration_ms', 0)} ms, isolation "
        f"{_md(receipt.get('isolation'), 80) or '-'}",
        f"- Recorded: {receipt.get('ts', '')}, signature valid, command hash `{receipt.get('command_hash', '')[:16]}`",
        "",
        "### Review",
        f"- Required approvals: {need['count']}" + (" from another provider" if need["other_provider"] else ""),
    ]
    if "agent" in kinds:
        lines.append(f"- Approvals: {len(approvals)}")
    else:
        lines.append(f"- Approvals: {len(approvals)}" + (f" ({', '.join(approvals)})" if approvals else ""))
    current = [r for r in card.get("reviews", []) if r.get("rev") == card.get("rev", 0)]
    for r in current[-10:]:
        by = "" if "agent" in kinds else f" by {r.get('by', '')}"
        note = _md(r.get("note"), 200)
        lines.append(f"- {r.get('verdict')}{by}" + (f": {note}" if note else ""))
    lines += ["", f"Opened with raidlock from card `{card['id']}` (rev {card.get('rev', 0)})."]
    pattern = _redactor(board, card, cfg, run_info)
    title = _scrub(pattern, _md(card["title"], 200)) or card["id"]
    body = _scrub(pattern, sanitize("\n".join(lines))) + "\n"
    return sanitize(title), sanitize(body), pattern


@contextmanager
def _changes(board, card, source, sha, url, base):
    """Fetch the verified commit and the base into a private store; yield (store, lock check and commit text)."""
    target = policy.target(board.workspace, board.dir, card["locks"])
    root = os.path.join(board.home, "pr")
    os.makedirs(root, mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        store = os.path.join(tmp, "store.git")
        local_git(tmp, "init", "--quiet", "--bare", store)
        local_git(
            store,
            "-c",
            "uploadpack.allowAnySHA1InWant=true",
            "fetch",
            "--quiet",
            "--no-tags",
            os.path.realpath(source),
            sha,
        )
        remote_git(store, "fetch", "--quiet", "--no-tags", url, f"+refs/heads/{base}:refs/raid/base")
        mb = local_git(store, "merge-base", sha, "refs/raid/base", check=False).strip()
        if not mb:
            raise Precondition(f"the verified commit shares no history with {base}", base=base)
        changed = set()
        findings = []
        commits = local_git(store, "rev-list", sha, "^refs/raid/base").splitlines()
        for commit in commits:
            parents = local_git(store, "rev-list", "--parents", "-n", "1", commit).split()[1:]
            for parent in parents or [None]:
                args = ["diff", parent, commit] if parent else ["diff-tree", "--root", "--no-commit-id", "-r", commit]
                paths = local_git(store, *args, "--name-only", "--no-renames", "-z").split("\0")
                changed.update(p for p in paths if p)
                patch = local_git(
                    store, *args, "-p", "--binary", "--no-renames", "--no-ext-diff", "--no-textconv", binary=True
                )
                try:
                    findings.extend(scan_patch(patch, rels=target["rels"], ci=target["ci"]))
                    from .scan import patch_links

                    for path in policy.check_links(patch_links(patch.decode()), target["rels"], target["ci"]):
                        findings.append({"kind": "path", "rule": "unsafe-symlink", "path": path, "line": None})
                except Precondition as exc:
                    findings.append({"kind": "scan", "rule": exc.message, "path": "", "line": None})
                text = _normalize(patch.decode("utf-8", "replace"))
                if sanitize(text) != text:
                    findings.append({"kind": "secret", "rule": "secret", "path": "", "line": None})
        changed = sorted(changed)
        if not changed:
            raise Precondition(f"the verified commit has no changes against {base}", base=base)
        found = policy.check(changed, target["rels"], target["ci"])
        links = {}
        for entry in local_git(store, "ls-tree", "-r", "-z", sha).split("\0"):
            if entry.startswith("120000 "):
                metadata, path = entry.split("\t", 1)
                links[path] = local_git(store, "cat-file", "blob", metadata.split()[2])
        found["denied"] = sorted(
            set(found["denied"]) | set(policy.check_links(links, target["rels"], target["ci"], paths=changed))
        )
        meta = local_git(store, "log", "--format=%an%n%ae%n%cn%n%ce%n%B", sha, "^refs/raid/base")
        yield (
            store,
            {
                "repo": target["repo"],
                "repo_path": target["path"],
                "merge_base": mb,
                "changed": changed,
                "outside": found["outside"],
                "denied": found["denied"],
                "findings": [{k: f.get(k) for k in ("kind", "rule", "path", "line")} for f in findings],
                "commit_text": meta,
            },
        )


def _remote_url(repo_path, remote):
    gdir = repos.git_dir(repo_path)
    url = repos._read_remotes(os.path.join(repos.common_dir(gdir), "config")).get(remote) if gdir else None
    if not url:
        raise Precondition(f"the repo has no remote {remote}", hint="raidlock config set pr.remote <name>")
    return url


def _default_base(repo_path, url):
    proc = remote_git(repo_path, "ls-remote", "--symref", url, "HEAD", check=False)
    for line in (proc.stdout or "").splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            name = line[len("ref: refs/heads/") : -len("\tHEAD")]
            if REF_NAME.match(name):
                return name
    return "main"


def _gh_repo(url):
    from .core.repos import normalize_remote

    norm = normalize_remote(url)
    return "" if norm.startswith("path:") or norm.count("/") != 2 else norm


@protected
def open_pr(board, caller, card_id, dry_run=None, branch=None, draft=False, **flags):
    """Open (or plan, when dry) a pull request from the card's verified commit. Lead-only unless dry."""
    board._auth(caller)
    card = board.card(card_id)
    receipt, source = verified(board, card)
    cfg = config(board, **flags)
    branch = branch or f"raid/{card['id']}"
    if not REF_NAME.match(branch):
        raise Precondition("branch must be a plain branch name", branch=branch[:120])
    ready = gh_ready(board.env)
    dry = (not ready) if dry_run is None else (bool(dry_run) or not ready)
    if not dry:
        board._require_lead(caller, "open a pull request")
    target = policy.target(board.workspace, board.dir, card["locks"])
    url = _remote_url(target["path"], cfg["remote"])
    base = cfg["base"] or _default_base(target["path"], url)
    sha = receipt["sha"]
    with _changes(board, card, source, sha, url, base) as (store, check):
        run_info = _run_info(board, card["id"])
        title, body, pattern = render(board, card, receipt, check, cfg, run_info)
        problems = []
        if check["outside"]:
            problems.append("changes outside the card's locks: " + ", ".join(check["outside"][:20]))
        if check["denied"]:
            problems.append("denied paths: " + ", ".join(check["denied"][:20]))
        if check["findings"]:
            problems.append("patch safety findings: " + ", ".join(sorted({f["rule"] for f in check["findings"]})))
        if pattern and pattern.search(_normalize(branch)):
            problems.append("the branch name contains a redacted term")
        if pattern and pattern.search(_normalize(check["commit_text"])):
            problems.append("commit messages or authors contain a redacted term")
        if sanitize(check["commit_text"]) != _normalize(check["commit_text"]):
            problems.append("commit metadata contains a secret")
        result = {
            "card": card["id"],
            "dry_run": dry,
            "gh_ready": ready,
            "branch": branch,
            "base": base,
            "sha": sha,
            "repo": check["repo"],
            "title": title,
            "body": body,
            "redact": cfg["redact"],
            "lock_check": {k: check[k] for k in ("changed", "outside", "denied", "findings")},
            "problems": problems,
        }
        if problems:
            raise Precondition(f"{card['id']} cannot be proposed: " + "; ".join(problems), card=card["id"])
        if dry:
            return result
        with board.lead_write(caller, "open a pull request"):
            current = board.card(card["id"])
            current_receipt, current_source = verified(board, current)
            if (
                current != card
                or current_receipt != receipt
                or current_source != source
                or config(board, **flags) != cfg
            ):
                raise Precondition("card or verification changed while preparing the pull request")
            proc = remote_git(
                store, "push", "--no-verify", "--porcelain", url, f"{sha}:refs/heads/{branch}", check=False
            )
            if proc.returncode != 0:
                raise Precondition(
                    f"push to {branch} was refused: {sanitize(proc.stderr).strip()[:200]}",
                    branch=branch,
                    hint="pick another branch with --branch",
                )
            pr = _create(check["repo_path"], url, branch, base, title, body, draft, board.env)
            record = {
                "number": pr["number"],
                "url": pr["url"],
                "branch": branch,
                "base": base,
                "sha": sha,
                "repo": check["repo"],
                "opened": clock.iso(),
                "state": "OPEN",
                "checks": {"state": "none", "passed": 0, "failed": 0, "pending": 0, "total": 0},
                "existing": pr["existing"],
            }
            current["pr"] = record
            board._note(current, caller.name, f"pull request #{record['number']} {record['url']} ({branch} -> {base})")
            board._save_card(current)
            board._record(
                "status",
                caller.name,
                f"pull request #{record['number']} for {card['id']}",
                task=card["id"],
                data={"event": "pr_opened", "pr": record},
            )
    return {**result, "pr": record}


def _create(repo_path, url, branch, base, title, body, draft, env):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as f:
        f.write(body)
        body_file = f.name
    try:
        args = ["pr", "create", "--head", branch, "--base", base, "--title", title, "--body-file", body_file]
        repo = _gh_repo(url)
        if repo:
            args += ["--repo", repo]
        if draft:
            args.append("--draft")
        proc = gh(args, cwd=repo_path, env=env)
    finally:
        os.unlink(body_file)
    if proc is None:
        raise Precondition("gh is not installed", hint="install the GitHub CLI and run gh auth login")
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    match = PR_URL.search(proc.stdout or "") or (PR_URL.search(proc.stderr or "") if "already exists" in out else None)
    if not match:
        raise Precondition(f"gh pr create failed: {sanitize(proc.stderr or proc.stdout, env).strip()[:300]}")
    return {"number": int(match.group(1)), "url": match.group(0), "existing": proc.returncode != 0}


def register_cli(add_command):
    def setup(p):
        p.add_argument("id", help="card id")
        p.add_argument("--dry-run", action="store_true", default=None, help="check and print, push nothing")
        p.add_argument("--base", default=None, help="base branch (default: pr.base or the remote's HEAD)")
        p.add_argument("--remote", default=None, help="remote in the card's repo (default: pr.remote or origin)")
        p.add_argument("--branch", default=None, help="head branch (default: raid/<card>)")
        p.add_argument("--redact", default=None, help="drop names from the PR: agent,model,provider or all")
        p.add_argument("--redact-terms", default=None, help="extra comma-separated words to scrub")
        p.add_argument("--draft", action="store_true")

    def handler(ctx, a):
        r = open_pr(
            ctx.board,
            ctx.caller(),
            a.id,
            dry_run=a.dry_run,
            branch=a.branch,
            draft=a.draft,
            redact=a.redact,
            terms=a.redact_terms,
            base=a.base,
            remote=a.remote,
        )
        if r["dry_run"]:
            why = "" if r["gh_ready"] else " (gh is not authenticated)"
            head = f"Dry run{why}: would push {r['sha'][:12]} to {r['branch']} and open a PR into {r['base']}."
            return r, f"{head}\nTitle: {r['title']}\n\n{r['body']}".rstrip()
        pr = r["pr"]
        verb = "Linked existing" if pr["existing"] else "Opened"
        return r, f"{verb} pull request #{pr['number']}: {pr['url']}"

    add_command("pr", "lead: push a card's verified commit to a branch and open a pull request", setup, handler, True)
