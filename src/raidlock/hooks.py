import hashlib
import hmac
import json
import os
import re
import shlex
import sys
import time

from . import gitguard
from .core.portable import open_nofollow

HEARTBEAT_SECONDS = 300
TAIL_BYTES = 65536
HANDBACK_CHARS = 8000
URGENT_CHARS = 8000
PLUGIN_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
LEAD_ONLY = (
    "migrate",
    "tidy",
    "done",
    "reopen",
    "verify",
    "land",
    "pr",
    "pr-link",
    "pr-sync",
    "delegate",
    "duel",
    "resurrect",
)
READ_ONLY = (
    "",
    "show",
    "who",
    "claimable",
    "help",
    "version",
    "transcript",
    "check",
    "workspaces",
    "wait",
    "next",
    "approvals",
)
BINDING_COMMANDS = ("claim", "join", "up", "heartbeat", "note", "lock")
RELEASING_LANES = ("review", "blocked", "todo")
VALUE_OPTIONS = (
    "--board",
    "--agent",
    "--token",
    "--cap",
    "--fence",
    "--note",
    "--ttl",
    "--for",
    "--checkout",
    "--team",
    "--role",
    "--runtime",
    "--model",
    "--verdict",
    "--assign",
    "--locks",
    "--depends",
    "--scope",
    "--verify",
    "--title",
    "--id",
    "--to",
    "--from",
    "--by",
    "--verify-cmd",
    "--output",
    "--ticket",
    "--reviewers",
    "--provider",
    "--runner-model",
    "--reported-model",
    "--effort",
    "--session",
    "--card",
    "--max",
    "--mission",
    "--repos",
    "--task",
    "--timeout",
    "--interval",
    "--limit",
    "--kind",
    "--out",
    "-o",
    "--dest",
    "--export",
    "--file",
    "--save",
    "--lead-secret",
)
WRITE_VERBS = (
    "rm",
    "rmdir",
    "mv",
    "cp",
    "tee",
    "touch",
    "truncate",
    "ln",
    "chmod",
    "chown",
    "dd",
    "install",
    "rsync",
    "unlink",
    "mkdir",
    "shred",
)
IN_PLACE = ("sed", "perl", "gsed")
SEPARATORS = (";", "&&", "||", "|", "&", "|&")
REDIRECTS = (">", ">>", "&>", "&>>", ">|", ">&")
WRAPPERS = ("env", "command", "exec", "nohup", "time")
OUTPUT_OPTIONS = ("--out", "-o", "--output", "--dest", "--export", "--file", "--save")
READ_COMMANDS = ("cd", "ls", "cat", "head", "tail", "wc", "pwd", "grep", "rg", "diff")
WORKER_TYPES = ("raid-worker", "raidlock:raid-worker")
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
RAID_WORD = re.compile(r"(?<![\w.-])raid(lock)?(?![\w/-])", re.I)
LAUNCH_THROUGH = (*gitguard.LAUNCHERS, *WRAPPERS, "uv", "busybox")
ARG_LAUNCHERS = ("xargs", "parallel", "find", "fd")
PACKAGE_OPTIONS = ("--with", "--from", "--spec", "-p", "--package")
BOARD_WRITE_DENIAL = (
    ".board/ is written only by the raidlock CLI; change cards with raidlock note, move, lock or (lead) edit instead"
)
LEAD_PROOF_DENIAL = "only the lead may present the lead secret; subagents hand over with: raidlock move <id> review"
EXPORT_LINE = re.compile(r"^export RAIDLOCK_(AGENT|FENCE|BOARD)=([^\s#]+)", re.M)
CLAIMED_LINE = re.compile(r"\bClaimed ([A-Za-z0-9_-]+)[:.]")
DENY_BYTES = 200


class NoBoard(Exception):
    pass


def state_dir(env):
    base = env.get("CLAUDE_PLUGIN_DATA") or os.path.join(
        env.get("RAIDLOCK_HOME") or os.path.join(os.path.expanduser("~"), ".raidlock"), "claude-code"
    )
    return base


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name or "main")[:120]


def binding_path(env, session, agent_key):
    return os.path.join(state_dir(env), "bindings", _safe(session), _safe(agent_key) + ".json")


def load_binding(env, session, agent_key):
    try:
        with open(binding_path(env, session, agent_key), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_binding(env, session, agent_key, data):
    path = binding_path(env, session, agent_key)
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    os.chmod(os.path.dirname(folder), 0o700)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = open_nofollow(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def log_error(env, event, err):
    try:
        folder = state_dir(env)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "errors.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {event} {type(err).__name__}: {err}\n")
    except OSError:
        pass


def agent_key(data):
    return data.get("agent_id") or "main"


def is_subagent(data):
    return bool(data.get("agent_id"))


def find_board(*starts):
    for start in starts:
        if not start:
            continue
        cur = os.path.abspath(os.path.expanduser(start))
        if os.path.isfile(cur) or not os.path.exists(cur):
            cur = os.path.dirname(cur)
        while True:
            if os.path.basename(cur) == ".board" and os.path.isfile(os.path.join(cur, "board.json")):
                return cur
            candidate = os.path.join(cur, ".board")
            if os.path.isdir(candidate):
                return candidate
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return None


def open_board(board_dir, env):
    from .core import Board

    return Board.open(board_dir, env=env)


def worker_env(data, env):
    if is_subagent(data):
        try:
            from .core.lead_env import assert_worker_env, scrub_worker_env
        except ImportError:
            pass
        else:
            clean = scrub_worker_env(env)
            assert_worker_env(clean)
        return {k: v for k, v in env.items() if k == "RAIDLOCK_HOME"}
    return dict(env)


def lead_session(board):
    try:
        lease = board.leadlease().lease()
    except Exception:
        return ""
    return str(lease.get("session") or "")


def lead_live(board):
    try:
        summary = board.leadlease().summary()
    except Exception:
        return False
    return bool(summary.get("held") and summary.get("live") and summary.get("session"))


def lead_refusal(board, data, what, prog=None):
    """Why this session is not the lead, with the exact commands that make it the lead, in the name the agent typed."""
    from .core.hints import lead_cmd

    if not prog:
        command = (data.get("tool_input") or {}).get("command") or ""
        prog = "raid" if re.search(r"(?:^|[\s;&|(/])raid\s", command) else "raidlock"
    status = board.lead_status()
    sid = str(data.get("session_id") or "") or "unknown"
    return (
        f"only the lead session may {what}. The lead lease is held by session {status.get('session') or 'none'} "
        f"until {status.get('expires') or 'unknown'}; this session is {sid}. "
        f"From the lead session run: {lead_cmd(board, f'lead handover --to {sid}', prog)}. "
        f"Or, once the lease expires, run here: RAIDLOCK_SESSION={sid} {lead_cmd(board, 'lead takeover', prog)} "
        "(the prefix pins the session id the hooks see, which a resumed session's shell may not report). "
        f"{prog} lead status shows the lease."
    )


def session_role(data, env, board, binding):
    """Classify host sessions against the current lead lease."""
    if is_subagent(data):
        return "subagent"
    name = binding.get("name", "")
    sid = str(data.get("session_id") or "")
    lease = board.leadlease().lease()
    # The lease decides: a binding left from an older fence (handover, takeover) must not outrank it.
    if sid and sid == lease.get("session") and lead_live(board):
        return "lead"
    if name and name != board.lead:
        return "worker"
    if not name and (env.get("RAIDLOCK_AGENT_ID") or env.get("RAIDLOCK_CAP")):
        return "worker"
    agent = env.get("RAIDLOCK_AGENT", "")
    if not name and agent and agent != board.lead and agent in board.agents():
        return "worker"
    from .core.lead import under_lead_host

    # The host renamed the lead's session (resume, clear) but this hook still runs under the lease's host process.
    if lead_live(board) and under_lead_host(lease):
        return "lead"
    return "unbound"


def without_lead_material(env):
    from .core.lead_env import lead_material

    drop = set(lead_material(env))
    return {k: v for k, v in env.items() if k not in drop}


def classify(data, env, *paths):
    binding = load_binding(env, data.get("session_id", ""), agent_key(data))
    starts = [binding.get("board")] + list(paths) + [data.get("_host_cwd"), data.get("cwd")]
    if not is_subagent(data):
        starts.append(env.get("RAIDLOCK_BOARD") or env.get("BOARD_DIR"))
    board_dir = find_board(*starts)
    if not board_dir:
        raise NoBoard()
    wenv = worker_env(data, env)
    board = open_board(board_dir, wenv)
    return board, binding, session_role(data, env, board, binding)


def resolve_role(data, env, *paths):
    board, binding, role = classify(data, env, *paths)
    wenv = worker_env(data, env)
    if role in ("worker", "unbound"):
        wenv = without_lead_material(wenv)
        board.env = wenv
    if binding.get("name") and role != "unbound":
        caller = binding_caller(board, binding, subagent=is_subagent(data), env=wenv)
    else:
        caller = board.caller(env=wenv)
    return board, caller, binding, role


def resolve(data, env, *paths):
    board, caller, binding, _ = resolve_role(data, env, *paths)
    return board, caller, binding


def binding_caller(board, binding, subagent=False, env=None):
    """Authenticate only a host binding established with a valid worker credential."""
    from .core.errors import IdentityError

    name = binding.get("name", "")
    if name not in board.agents() or (subagent and name == board.lead):
        raise IdentityError("the host binding does not identify a joined worker")
    if name == board.lead:
        caller = board.caller(agent=name, env=env or {})
        if not caller.is_lead:
            raise IdentityError("the host must present its lead credential")
        return caller
    pinned = {"RAIDLOCK_AGENT_ID": name} if subagent else {}
    if binding.get("cap"):
        caller = board.caller(agent=name, cap=binding["cap"], env=pinned)
        board._auth(caller, scoped_ok=True)
        return caller
    secret = agent_token(board, name) or ""
    proof = hashlib.sha256(secret.encode()).hexdigest() if secret else ""
    if not proof or not hmac.compare_digest(proof, binding.get("proof", "")):
        raise IdentityError("the host binding has no valid authentication proof")
    return board.caller(agent=name, token=secret, fence=binding.get("fence"), env=pinned)


def deny(reason):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "raidlock: " + reason,
        }
    }


def inside(path, root):
    path, root = os.path.realpath(path).casefold(), os.path.realpath(root).casefold()
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def workspace_repo(board, loc):
    """Whether `loc` (from repos.locate) is a checkout of one of the workspace's repos, wherever it lives."""
    from .core import repos

    found = repos.discover(os.path.dirname(board.dir), repos.declared_repos(board.dir)).values()
    return loc["id"] in {(repos.locate(root) or {}).get("id") for root in found if os.path.isdir(root)}


def write_decision(data, env, path):
    if not path:
        return None
    full = path if os.path.isabs(path) else os.path.join(data.get("cwd") or os.getcwd(), path)
    if has_board_segment(full) or has_board_segment(os.path.realpath(full)):
        return deny(BOARD_WRITE_DENIAL)
    try:
        board, caller, _, role = resolve_role(data, env, full)
    except NoBoard:
        return None
    from .core import repos

    loc = repos.locate(full)
    lead_owned = False
    if loc:
        for conflict in board.index.conflicts([{**loc, "path": full}]):
            here = conflict["workspace"] in (board.name, board.uid)
            if role == "lead" and here and conflict["agent"] == board.lead:
                lead_owned = True
                continue
            if conflict["agent"] != caller.name or not here:
                return deny(
                    f"held by {conflict['agent']} on {conflict['card']} in workspace {conflict['workspace']}. "
                    "Ask the lead or pick another card."
                )
    if lead_owned:
        return None
    if role == "unbound" and not (loc and workspace_repo(board, loc)):
        # Board-first guards the workspace's repos (any clone of them); a scratchpad or /tmp is free.
        if inside(full, board.home) or inside(full, state_dir(env)):
            return deny("raidlock state (tokens, bindings) is written only by the raidlock CLI")
        return None
    if role == "lead" and loc and board.settings.get("require_card_for_lead_edits"):
        from .core.board import LEAD_CARD_RULE

        return deny(LEAD_CARD_RULE)
    if loc and caller.name and not caller.is_lead and (not caller.authenticated or not caller.fence):
        return deny(
            "this session is not bound to your claim yet: run one raidlock command with --agent <name> --cap <cap> "
            "--fence <fence> (from your claim's output), for example raidlock heartbeat <card> ..., then retry"
        )
    d = board.check_write(full, caller)
    if d["allowed"]:
        return None
    if not d.get("card"):
        if role == "lead":
            return None
        if role == "unbound" and board.settings.get("auth") == "off":
            return None
        if role == "unbound":
            return deny(
                "this session has no raidlock identity, so it may write only inside a lease. "
                "Claim a card that locks it: raid claimable"
            )
    if d.get("holder") and d["holder"] != caller.name:
        return deny(f"{d['reason']}. Ask the lead or pick another card.")
    if d.get("card"):
        return deny(f"{d['reason']}. Run: raidlock claim {d['card']}")
    return deny(f"{d['reason']}. Claim a card that locks it first: raid claimable")


def guarded_read_paths(data, env):
    """Resolve each read against every directory in which its segment may run."""
    tool = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    paths = [
        (tool[k], cwd, False, True, env)
        for k in ("file_path", "notebook_path", "path", "glob")
        if isinstance(tool.get(k), str)
    ]
    if data.get("tool_name") == "Glob" and isinstance(tool.get("pattern"), str):
        paths.append((tool["pattern"], cwd, False, True, env))
    command = tool.get("command")
    if isinstance(command, str) and data.get("tool_name") != "apply_patch":
        for op in gitguard.analyze(command, [cwd], all_commands=True):
            argv = op["argv"]
            verb = os.path.basename(argv[0]).casefold() if argv else ""
            navigation = verb in ("cd", "pushd")
            recursive = verb in ("find", "rg", "tar", "rsync", "du", "tree", "zip", "7z") or any(
                a in ("--recursive", "--dereference-recursive", "--archive")
                or (a.startswith("-") and not a.startswith("--") and any(c in a[1:] for c in "rRa"))
                for a in argv[1:]
            )
            for directory in op["cwds"]:
                if directory is None:
                    raise gitguard.Ambiguous("cannot determine read directory")
                words = list(op["raw"][1:])
                if recursive or verb in ("find", "rg", "grep", "tar", "rsync"):
                    words.append(".")
                for word in words:
                    if word in SEPARATORS + REDIRECTS or word.startswith("-") and "=" not in word:
                        continue
                    if word.startswith("-"):
                        word = word.split("=", 1)[1]
                    paths.append((word, directory, navigation, recursive, {**env, **op["env"]}))
    out = []
    for path, directory, navigation, recursive, values in paths:
        path = re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
            lambda m, values=values: values.get(m.group(1) or m.group(2), m.group(0)),
            path,
        )
        if "$" in path:
            continue
        recursive = recursive or bool(gitguard.GLOB.search(path))
        path = os.path.expanduser(literal_prefix(path))
        out.append((path if os.path.isabs(path) else os.path.join(directory, path), navigation, recursive))
    return out


def read_guard(data, env):
    try:
        board, _, role = classify(data, env)
    except NoBoard:
        return
    if role == "lead":
        return
    paths = guarded_read_paths(data, env)
    from .core.errors import IdentityError
    from .core.lead_hook import protected_lead_path

    tokens = os.path.realpath(os.path.join(board.home, "tokens")).casefold()
    secret = os.path.realpath(board.lead_status()["secret_file"]).casefold()
    recovering = role == "unbound" and not lead_live(board)
    for path, navigation, recursive in paths:
        real = os.path.realpath(os.path.expanduser(path)).casefold()
        if recovering and real == secret:
            continue
        in_tokens = real == tokens or real.startswith(tokens + os.sep)
        ancestor = (
            protected_lead_path(board, path) or tokens.startswith(real.rstrip(os.sep) + os.sep)
        ) and not navigation
        reachable = recursive and not navigation and not (ancestor or in_tokens or real == secret)
        if ancestor or in_tokens or real == secret or (reachable and reaches_credentials(path, tokens, secret)):
            if role == "subagent":
                raise IdentityError("subagents may not read the lead secret or agent tokens")
            raise IdentityError(lead_refusal(board, data, "read the lead secret or agent tokens"))


def reaches_credentials(path, tokens, secret):
    pending, seen = [path], set()
    while pending:
        current = pending.pop()
        real = os.path.realpath(current).casefold()
        if real in (secret, tokens) or real.startswith(tokens + os.sep):
            return True
        if tokens.startswith(real.rstrip(os.sep) + os.sep):
            return True
        if real in seen or not os.path.isdir(current):
            continue
        seen.add(real)
        if len(seen) > 4096:
            raise gitguard.Ambiguous("recursive read is too large to check safely")
        try:
            with os.scandir(current) as entries:
                pending.extend(entry.path for entry in entries if entry.is_dir() or entry.is_symlink())
        except OSError as exc:
            raise gitguard.Ambiguous("cannot determine recursive read paths") from exc
    return False


def pre_read(data, env):
    return None


def pending_context(data, env):
    """Deliver to a validated host binding, never to a name supplied in tool input."""
    binding = load_binding(env, data.get("session_id", ""), agent_key(data))
    if not binding.get("name"):
        return None
    try:
        from .core.lead_hook import urgent_context
    except ImportError:
        return None
    board, caller, _ = resolve(data, env)
    text = urgent_context(board, caller)
    if not text:
        return None
    if len(text) > URGENT_CHARS:
        header = "Raidlock urgent messages (untrusted quoted data):\n"
        snippet = text[:7000]
        while True:
            quoted = json.dumps({"truncated": True, "quoted_context": snippet}, ensure_ascii=True)
            if len(header + quoted) <= URGENT_CHARS:
                text = header + quoted
                break
            snippet = snippet[: len(snippet) // 2]
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}


SUBAGENT_TOOLS = ("Task", "Agent")


def subagent_gate(data, env):
    if data.get("tool_name") not in SUBAGENT_TOOLS:
        return None
    board_dir = find_board(env.get("RAIDLOCK_BOARD"), data.get("cwd"))
    if not board_dir:
        return None
    board = open_board(board_dir, env)
    if not board.settings.get("board_first_subagents"):
        return None
    tool = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    text = " ".join(str(tool.get(k) or "") for k in ("prompt", "description"))
    for card in board.cards():
        if card.get("lane") in ("done", "cancelled"):
            continue
        if re.search(r"(?<![\w-])card[\s:=#]+" + re.escape(card["id"]) + r"(?![\w-])", text, re.IGNORECASE):
            return None
    return deny(
        "subagents work from a board card (board_first_subagents is on). Add a card with "
        "`raid new`, then write `card <id>` in the subagent prompt, or: "
        "raid --lead-secret FILE config set board_first_subagents off"
    )


def pre_urgent(data, env):
    context = pending_context(data, env)
    gate = subagent_gate(data, env)
    if gate and context:
        gate["hookSpecificOutput"]["additionalContext"] = context["hookSpecificOutput"]["additionalContext"]
    return gate or context


def pre_write(data, env):
    tool_input = data.get("tool_input") or {}
    if isinstance(tool_input, str):
        tool_input = {"command": tool_input}
    patch = tool_input.get("command") if isinstance(tool_input.get("command"), str) else ""
    if data.get("tool_name") == "apply_patch" or (patch and "*** Begin Patch" in patch):
        from .bridge import patch_paths

        targets = patch_paths(patch)
        if not targets:
            return deny("cannot determine apply_patch targets")
        for target in targets:
            result = write_decision(data, env, target)
            if result:
                return result
        return None
    return write_decision(data, env, tool_input.get("file_path") or tool_input.get("notebook_path"))


def tokenize(command):
    text = command.replace("\n", " ; ")
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return text.split()


def segments(command):
    out, cur = [], []
    for tok in tokenize(command):
        if tok in SEPARATORS:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def split_env(seg):
    env, i = {}, 0
    while i < len(seg):
        if ENV_ASSIGN.match(seg[i]):
            key, _, value = seg[i].partition("=")
            env[key] = value
        elif seg[i] not in WRAPPERS:
            break
        i += 1
    return env, seg[i:]


def walk(command):
    carried = {}
    for seg in segments(command):
        prefix, argv = split_env(seg)
        argv, redirects = strip_redirects(argv)
        if argv and argv[0] == "export":
            for item in argv[1:]:
                if ENV_ASSIGN.match(item):
                    key, _, value = item.partition("=")
                    carried[key] = value
        yield {**carried, **prefix}, argv, redirects


def exports_only(argv):
    return bool(argv) and argv[0] == "export" and all(a.startswith("RAIDLOCK_") for a in argv[1:])


def board_lead(data, binding_board=""):
    from .core import store

    board_dir = find_board(binding_board, data.get("cwd"))
    meta = store.read_json(os.path.join(board_dir, "board.json")) if board_dir else None
    return (meta or {}).get("lead", "")


def session_worker(data, env, ctx, opts):
    """The worker a main session acts as, or "": its binding, a worker credential in its environment, or an agent
    named on the command that is not the board's lead."""
    binding = load_binding(env, data.get("session_id", ""), agent_key(data))
    lead = board_lead(data, binding.get("board", ""))
    if not lead:
        return ""
    for source in (ctx, env):
        if source.get("RAIDLOCK_AGENT_ID") or source.get("RAIDLOCK_CAP"):
            return source.get("RAIDLOCK_AGENT_ID") or "capability holder"
    names = (
        opts.get("--agent"),
        ctx.get("RAIDLOCK_AGENT"),
        env.get("RAIDLOCK_AGENT"),
        binding.get("name"),
    )
    return next((n for n in names if isinstance(n, str) and n and n != lead), "")


def strip_redirects(argv):
    args, targets, i = [], [], 0
    while i < len(argv):
        tok = argv[i]
        if tok in REDIRECTS or (tok.isdigit() and i + 1 < len(argv) and argv[i + 1] in REDIRECTS):
            if tok.isdigit():
                i += 1
                tok = argv[i]
            if i + 1 < len(argv):
                target = argv[i + 1]
                if not target.startswith("&") and not target.isdigit():
                    targets.append(target)
            i += 2
            continue
        if tok in ("<", "<<", "<<<") and i + 1 < len(argv):
            i += 2
            continue
        args.append(tok)
        i += 1
    return args, targets


def raidlock_args(argv):
    if not argv:
        return None
    base = os.path.basename(argv[0]).casefold()
    if base in ("raid", "raidlock"):
        return argv[1:]
    if base.startswith("python") and len(argv) >= 3 and argv[1] == "-m" and argv[2] == "raidlock":
        return argv[3:]
    return None


def is_lead_secret_option(arg):
    return arg == "--lead-secret" or arg.startswith("--lead-secret=")


def lead_only_command(cmd, pos):
    return (
        cmd in LEAD_ONLY
        or (cmd == "move" and len(pos) > 1 and pos[1] == "done")
        or (cmd in ("routing", "resurrect-policy", "budget") and pos[:1] == ["set"])
        or (cmd == "bridge" and pos[:1] == ["pool"])
    )


def launched_raidlock(argv, command):
    """For a launcher segment: why a subagent may not run it, and the raidlock argument lists it reaches."""
    if not argv or os.path.basename(argv[0]).casefold() not in LAUNCH_THROUGH:
        return None, []
    found = []
    for i, tok in enumerate(argv[1:], 1):
        base = os.path.basename(tok).casefold()
        if base in ("raid", "raidlock") and argv[i - 1] not in PACKAGE_OPTIONS:
            rest = argv[i + 1 :]
        elif base.startswith("python") and argv[i + 1 : i + 3] == ["-m", "raidlock"]:
            rest = argv[i + 3 :]
        elif gitguard.INTERPRETER.match(base) or base in gitguard.SHELLS + ("eval",):
            if RAID_WORD.search(command):
                return f"subagents may not run {base} code that calls raidlock", []
            continue
        else:
            continue
        if any(os.path.basename(a).casefold() in ARG_LAUNCHERS for a in argv[:i]):
            return "subagents may not pass raidlock arguments through xargs, parallel, find or fd", []
        found.append(rest)
    return None, found


def computed_verb(cmd, pos):
    words = [cmd] + (pos[1:2] if cmd == "move" else [])
    return any("$" in w or gitguard.SUBST in w for w in words)


def raidlock_decision(data, env, board, role, ctx, rl, prog="raidlock"):
    sub = is_subagent(data)
    unbound = role == "unbound"
    guarded_lead = bool(board) and role in ("worker", "unbound") and lead_live(board)
    cmd, pos, opts = parse_raidlock(rl)
    if cmd == "hook":
        return deny(f"{prog} hook runs only from the host's hook configuration, never from a tool call")
    if sub and ("--lead-secret" in opts or ctx.get("RAIDLOCK_LEAD_SECRET")):
        return deny(LEAD_PROOF_DENIAL)
    if (sub or role in ("worker", "unbound")) and computed_verb(cmd, pos):
        return deny("cannot determine the raidlock command; spell it out literally")
    lead_only = lead_only_command(cmd, pos)
    restricted = lead_only or opts.get("--for") or opts.get("--urgent")
    if guarded_lead and (opts.get("--lead-secret") or ctx.get("RAIDLOCK_LEAD_SECRET")):
        return deny(lead_refusal(board, data, "present the lead secret", prog))
    worker = (
        sub or (restricted and role in ("worker", "unbound")) or (restricted and session_worker(data, env, ctx, opts))
    )
    if worker and lead_only:
        return deny(f"only the lead can run {prog} {cmd}. Hand over with: {prog} move <id> review")
    if worker and (opts.get("--for") or opts.get("--urgent")):
        return deny(f'--for and --urgent are lead-only; drop them, or message the lead: {prog} msg --to <lead> "..."')
    recovering = unbound and cmd == "lead" and not lead_live(board)
    named = sub or (unbound and not recovering)
    read_only = cmd in READ_ONLY or (cmd == "lead" and pos[:1] == ["status"])
    if named and not read_only and "--help" not in opts and "-h" not in opts:
        name = next(
            (
                v
                for v in (
                    opts.get("--agent"),
                    opts.get("--from"),
                    opts.get("--by"),
                    ctx.get("RAIDLOCK_AGENT_ID"),
                    ctx.get("RAIDLOCK_AGENT"),
                )
                if isinstance(v, str) and v
            ),
            "",
        )
        if not name:
            return deny(
                f"name yourself on every board command: RAIDLOCK_AGENT=<name> RAIDLOCK_TOKEN=<token> {prog} {cmd} ..."
            )
        if name == board_lead(data, load_binding(env, data.get("session_id", ""), agent_key(data)).get("board", "")):
            return deny(
                "subagents and unbound sessions cannot act as the lead; name your own agent (--agent <you>), "
                f"or ask the lead session to run it. {prog} lead status shows which session leads"
            )
    return None


def parse_raidlock(args):
    opts, pos, i = {}, [], 0
    while i < len(args):
        a = args[i]
        if a.startswith("--") and "=" in a:
            key, _, value = a.partition("=")
            opts[key] = value
        elif a in VALUE_OPTIONS:
            opts[a] = args[i + 1] if i + 1 < len(args) else ""
            i += 1
        elif a.startswith("-"):
            opts[a] = True
        else:
            pos.append(a)
        i += 1
    return (pos[0] if pos else ""), pos[1:], opts


def has_board_segment(path):
    return ".board" in path.replace("\\", "/").split("/")


def is_tokens_path(text, env):
    home = env.get("RAIDLOCK_HOME") or os.path.join(os.path.expanduser("~"), ".raidlock")
    norm = text.replace("\\", "/")
    return (
        "/.raidlock/tokens" in norm
        or norm.startswith("~/.raidlock/tokens")
        or (os.path.join(home, "tokens") in norm)
        or "$RAIDLOCK_HOME/tokens" in norm
        or "${RAIDLOCK_HOME}/tokens" in norm
    )


def written_paths(argv):
    if not argv:
        return []
    verb = os.path.basename(argv[0]).casefold()
    rest = [a for a in argv[1:] if not a.startswith("-")]
    if verb in ("cp", "mv", "install", "ln"):
        destinations = []
        for i, arg in enumerate(argv[1:], 1):
            if arg in ("-t", "--target-directory"):
                destinations.append(argv[i + 1] if i + 1 < len(argv) else "$unknown")
            elif arg.startswith("--target-directory="):
                destinations.append(arg.split("=", 1)[1])
            elif arg.startswith("-t") and len(arg) > 2:
                destinations.append(arg[2:])
        return (rest if verb == "mv" else rest[-1:]) + destinations
    if verb == "rsync":
        return rest[-1:]
    if verb == "dd":
        return [a[3:] for a in argv[1:] if a.startswith("of=")]
    if verb in WRITE_VERBS:
        return rest
    if verb in IN_PLACE and any(a == "-i" or a.startswith("-i") or a == "--in-place" for a in argv[1:]):
        return in_place_files(argv[1:])
    return []


def in_place_files(args):
    """The files `sed -i` / `perl -i` edit: not the script, not an -e/-f value, not BSD sed's empty `-i ''` suffix."""
    files, script_given, skip = [], False, False
    for a in args:
        if skip:
            skip = False
            continue
        if a in ("-e", "-f", "--expression", "--file"):
            script_given, skip = True, True
        elif a.startswith(("-e", "-f", "--expression=", "--file=")):
            script_given = True
        elif a and not a.startswith("-"):
            files.append(a)
    return files if script_given else files[1:]


def lease_check(data, env, board, caller, full):
    from .core import repos

    loc = repos.locate(full)
    if not loc:
        if os.path.isdir(full):
            for root in repos.discover(full).values():
                result = lease_check(data, env, board, caller, root)
                if result:
                    return result
        return None
    key = {**loc, "path": full}
    for c in board.index.conflicts([key]):
        here = c["workspace"] in (board.name, board.uid)
        if here and c["agent"] == board.lead:
            continue
        if c["agent"] != caller.name or not here:
            return deny(
                f"{full} overlaps {c['held'] or 'the repo'} held by {c['agent']} on {c['card']}. "
                "Ask the lead or pick another card."
            )
    return write_decision(data, env, full)


def repo_lease_held(board, caller, root):
    from .core import repos

    loc = repos.locate(root)
    if not loc:
        return True
    key = {**loc, "path": root}
    if not caller.authenticated or not caller.fence:
        return False
    leases = board.leases()
    return any(
        c["agent"] == caller.name
        and c["workspace"] in (board.name, board.uid)
        and leases.get(c["card"], {}).get("fence") == caller.fence
        and (not getattr(caller, "scope_card", "") or caller.scope_card == c["card"])
        for c in board.index.conflicts([key])
    )


def literal_prefix(path):
    parts = re.split(r"[\\/]", path) if os.sep == "\\" else path.split("/")
    for i, part in enumerate(parts):
        if gitguard.GLOB.search(part):
            return os.sep.join(parts[:i]) or (os.sep if os.path.isabs(path) else ".")
    return path


def git_op_decision(data, env, board, caller, op):
    sub_agent = is_subagent(data)
    if any(k.startswith("GIT_") for k in op["env"]):
        raise gitguard.Forbidden(
            "GIT_* environment overrides are not allowed on git commands (they can redirect git or run commands); "
            "run the same git command without them"
        )
    cdirs, sub, args = gitguard.parse_git(op["argv"])
    kind, paths, written = gitguard.classify(sub, args)
    workspace = os.path.realpath(board.workspace)
    cwds = set()
    for c in op["cwds"]:
        for d in cdirs:
            if c is None or "$" in d or gitguard.SUBST in d:
                c = None
                break
            c = os.path.normpath(os.path.join(c, os.path.expanduser(d)))
        if c is not None and cdirs:
            real = os.path.realpath(c)
            if real != workspace and not real.startswith(workspace + os.sep):
                raise gitguard.Forbidden(
                    f"git -C {c} is outside the workspace: git would load that repo's own config (fsmonitor, hooks, "
                    "aliases can run commands), which raidlock cannot vet. Run git there from your own terminal"
                )
        cwds.add(c)
    if kind == gitguard.READ and not written:
        return None
    if None in cwds:
        raise gitguard.Ambiguous("cannot tell which directory git runs in")
    from .core import repos

    for c in sorted(cwds):
        for target in written:
            result = write_decision(data, env, os.path.normpath(os.path.join(c, os.path.expanduser(target))))
            if result:
                return result
        if kind == gitguard.READ:
            continue
        if kind == gitguard.PATHS:
            for p in paths:
                full = os.path.normpath(os.path.join(c, os.path.expanduser(literal_prefix(p))))
                result = lease_check(data, env, board, caller, full)
                if result:
                    return result
                if sub_agent and not board.check_write(full, caller)["allowed"] and repos.locate(full):
                    return deny(
                        f"git {sub} {p}: you hold no lease covering {full}; claim a card that locks it "
                        "(raidlock claimable) or ask the lead to add it: raidlock lock <card> <repo>/<path>"
                    )
            continue
        root = repos.find_root(c)
        if not root:
            root = c
        if kind == gitguard.REF:
            if sub_agent and not repo_lease_held(board, caller, root):
                return deny(
                    f"git {sub} needs a lease in {root}; claim a card that locks part of it (raidlock claimable)"
                )
            continue
        if kind == gitguard.WHOLE:
            result = lease_check(data, env, board, caller, root)
            if result:
                return deny(
                    f"git {sub} can touch the whole repo; it needs a lease covering all of {root} "
                    f"({result['hookSpecificOutput']['permissionDecisionReason'][10:]})"
                )
            if sub_agent and not board.check_write(root, caller)["allowed"]:
                return deny(
                    f"git {sub} can touch the whole repo; it needs a lease covering all of {root}. Name the files "
                    f"instead (git add <path> ..., git restore <path>), or ask the lead to lock the repo"
                )
            continue
    return None


def git_decision(data, env, command):
    try:
        ops = gitguard.analyze(command, [data.get("cwd") or os.getcwd()])
    except gitguard.Ambiguous as e:
        if not gitguard.mentions_git(re.sub(r"[\"'\\]", "", command)):
            return None, False
        ops, err = None, e
    if ops == []:
        return None, False
    try:
        board, caller, _ = resolve(data, env)
    except NoBoard:
        return None, True
    if ops is None:
        return deny(f"ambiguous git command ({err}); run plain git commands one by one"), True
    try:
        for op in ops:
            result = git_op_decision(data, env, board, caller, op)
            if result:
                return result, True
    except gitguard.Forbidden as e:
        return deny(str(e)), True
    except gitguard.Ambiguous as e:
        return deny(f"ambiguous git command ({e}); run plain git commands one by one"), True
    return None, bool(ops)


def bash_role(data, env):
    try:
        board, _, role = classify(data, env)
    except NoBoard:
        return None, ""
    return board, role


def pre_bash(data, env):
    command = (data.get("tool_input") or {}).get("command") or ""
    sub = is_subagent(data)
    board, role = bash_role(data, env)
    guarded_lead = bool(board) and role in ("worker", "unbound") and lead_live(board)
    unbound = role == "unbound"
    if (sub or guarded_lead) and is_tokens_path(command, env):
        return deny("agent tokens are private; use your own RAIDLOCK_TOKEN")
    if (sub or unbound) and ("$RAIDLOCK_TOKEN" in command or "${RAIDLOCK_TOKEN}" in command):
        return deny("pass your own token literally; the inherited RAIDLOCK_TOKEN is not yours")
    targets = []
    verdict, has_git = git_decision(data, env, command)
    if verdict:
        return verdict
    try:
        commands = gitguard.analyze(command, [data.get("cwd") or os.getcwd()], all_commands=True)
    except gitguard.Ambiguous as exc:
        return deny(f"cannot determine write targets: {exc}")
    for op in commands:
        ctx, argv = op["env"], op["argv"]
        _, redirects = strip_redirects(op["raw"])
        verb = os.path.basename(argv[0]).casefold() if argv else ""
        opaque = raidlock_args(argv) is None and (
            gitguard.INTERPRETER.match(verb)
            or verb in gitguard.SHELLS + ("eval", "source", ".", "rscript")
            or verb.endswith((".py", ".js", ".sh", ".pl", ".rb"))
            or "$" in verb
            or gitguard.SUBST in verb
            or (argv and "/" in argv[0] and verb not in READ_COMMANDS + WRITE_VERBS + ("git", "printf", "echo"))
        )
        if sub and ctx.get("RAIDLOCK_LEAD_SECRET"):
            return deny(LEAD_PROOF_DENIAL)
        if sub and opaque and RAID_WORD.search(command):
            return deny(f"subagents may not run {verb or 'computed'} code that calls raidlock")
        if opaque and role in ("worker", "unbound"):
            if unbound:
                return deny(
                    f"this session has no raidlock identity, so it cannot run {verb or 'this'} scripts (their writes "
                    "cannot be checked). Join and claim a card first: raidlock join --agent <name> --team <team>, "
                    "then raidlock claimable; plain read commands (cat, grep, ls) still work"
                )
            _, caller, _ = resolve(data, env)
            if not caller.authenticated:
                return deny(
                    f"running {verb or 'scripts'} needs a verified worker identity: pass --agent <name> --token "
                    "<token> on a raidlock command (or claim your card) so the hooks can bind this session"
                )
        if verb == "ln" and any(
            a == "--symbolic" or (a.startswith("-") and not a.startswith("--") and "s" in a) for a in argv[1:]
        ):
            return deny(
                "symlinks cannot be checked before later writes go through them; copy the file instead, or create "
                "the link from your own terminal"
            )
        writes = written_paths(argv)
        if verb in WRITE_VERBS and not writes:
            return deny(f"cannot tell which file {verb} writes; name the target path literally in the command")
        local_targets = redirects + writes
        rl_args = raidlock_args(argv)
        if rl_args is not None:
            _, _, output_opts = parse_raidlock(rl_args)
            for key in OUTPUT_OPTIONS:
                if key in output_opts:
                    value = output_opts[key]
                    if not isinstance(value, str) or not value:
                        return deny("cannot determine write targets")
                    local_targets.append(value)
        for target in local_targets:
            if "$" in target or gitguard.SUBST in target or gitguard.GLOB.search(target) or None in op["cwds"]:
                return deny(
                    f"cannot tell where this command writes: {target!r} uses a variable, substitution or glob, or "
                    "the folder depends on one. Spell the path out literally, for example /full/path/file.txt"
                )
            targets.extend(os.path.normpath(os.path.join(c, os.path.expanduser(target))) for c in op["cwds"])
        rl = raidlock_args(argv)
        reached = [rl] if rl is not None else []
        if sub and rl is None:
            why, reached = launched_raidlock(argv, command)
            if why:
                return deny(why)
        prog = "raid" if verb in ("raid", "raid.exe") else "raidlock"
        for found in reached:
            result = raidlock_decision(data, env, board, role, ctx, found, prog)
            if result:
                return result
        if rl is not None:
            continue
        if exports_only(argv):
            continue
        if any(has_board_segment(p) for p in written_paths(argv)):
            return deny(BOARD_WRITE_DENIAL)

    for target in targets:
        if target.startswith("/dev/"):
            continue
        result = write_decision(data, env, os.path.expanduser(target))
        if result:
            return result
    return None


def tail_model(path):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            chunk = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    for line in reversed(chunk.splitlines()):
        line = line.strip()
        if not line.startswith("{") or '"assistant"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        msg = rec.get("message") or {}
        if rec.get("type") == "assistant" or msg.get("role") == "assistant":
            model = msg.get("model") or ""
            if model and not model.startswith("<"):
                return model
    return ""


def transcript_for(data):
    if data.get("agent_transcript_path"):
        return data["agent_transcript_path"]
    main = data.get("transcript_path") or ""
    if not is_subagent(data) or not main:
        return main
    stem = main[:-6] if main.endswith(".jsonl") else main
    return os.path.join(stem, "subagents", f"agent-{data['agent_id']}.jsonl")


def bind_from_bash(data, env, binding):
    from .core import errors

    command = (data.get("tool_input") or {}).get("command") or ""
    response = data.get("tool_response") or {}
    stdout = response.get("stdout") if isinstance(response, dict) else str(response)
    stdout = stdout or ""
    changed = False
    try:
        result = json.loads(stdout)
    except ValueError:
        result = {}
    if not isinstance(result, dict):
        result = {}
    shell_dir = data.get("cwd") or ""
    for prefix, argv, _ in walk(command):
        if argv and argv[0] == "cd" and len(argv) > 1:
            shell_dir = os.path.join(shell_dir, os.path.expanduser(argv[1]))
            continue
        rl = raidlock_args(argv)
        if rl is None:
            continue
        cmd, pos, opts = parse_raidlock(rl)
        if cmd in ("move", "release", "done") and pos and pos[0] == binding.get("card"):
            if cmd != "move" or (len(pos) > 1 and pos[1] in RELEASING_LANES + ("done",)):
                binding["last_card"] = binding["card"]
                binding["card"] = ""
                changed = True
            continue
        if cmd not in BINDING_COMMANDS:
            continue
        exports = dict(EXPORT_LINE.findall(stdout))
        name = (
            exports.get("AGENT")
            or opts.get("--agent")
            or prefix.get("RAIDLOCK_AGENT_ID")
            or prefix.get("RAIDLOCK_AGENT")
            or ("" if is_subagent(data) or binding.get("name") else env.get("RAIDLOCK_AGENT", ""))
        )
        board = (
            exports.get("BOARD")
            or (opts.get("--board") if isinstance(opts.get("--board"), str) else "")
            or prefix.get("RAIDLOCK_BOARD", "")
        )
        found = find_board(os.path.expanduser(board) if board else "", shell_dir, data.get("cwd"))
        if found and binding.get("board") != found:
            binding["board"] = found
            changed = True
        if name and name is not True:
            board_obj = open_board(found, {}) if found else None
            if board_obj is None or name not in board_obj.agents() or (is_subagent(data) and name == board_obj.lead):
                continue
            cap_match = re.search(r"^export RAIDLOCK_CAP=([^\s#]+)", stdout, re.M)
            capability = (
                cap_match.group(1)
                if cap_match
                else (result.get("cap") if cmd == "claim" else None) or opts.get("--cap") or prefix.get("RAIDLOCK_CAP")
            )
            presented = (
                opts.get("--token")
                or prefix.get("RAIDLOCK_TOKEN")
                or (env.get("RAIDLOCK_TOKEN", "") if not is_subagent(data) else "")
            )
            candidate = {"name": name, "fence": binding.get("fence", 0)}
            if capability:
                candidate["cap"] = capability
            elif presented and "$" not in presented:
                candidate["proof"] = hashlib.sha256(presented.encode()).hexdigest()
            elif name == binding.get("name"):
                candidate.update({k: binding[k] for k in ("cap", "proof") if k in binding})
            try:
                binding_caller(board_obj, candidate, subagent=is_subagent(data))
            except (errors.RaidlockError, OSError, ValueError):
                continue
            if capability:
                scoped = binding_caller(board_obj, candidate, subagent=is_subagent(data))
                candidate.update(fence=scoped.fence, card=getattr(scoped, "scope_card", ""))
            binding.update(candidate)
            changed = True
        claimed = CLAIMED_LINE.search(stdout)
        if cmd == "claim" and not binding.get("cap") and (claimed or result.get("card")):
            claimed_card = claimed.group(1) if claimed else result["card"]
            if isinstance(claimed_card, str):
                binding["card"] = claimed_card
                changed = True
        fence = exports.get("FENCE") or result.get("fence") or opts.get("--fence") or prefix.get("RAIDLOCK_FENCE")
        if not binding.get("cap") and fence and str(fence).isdigit():
            binding["fence"] = int(fence)
            changed = True
    return changed


def agent_token(board, name):
    from .core import identity

    try:
        with open(identity.token_path(board.home, board.uid, name), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def record_model(data, env, binding):
    model = tail_model(transcript_for(data))
    if not model or model == binding.get("model") or not binding.get("name") or not binding.get("board"):
        return False
    board = open_board(binding["board"], {})
    name = binding["name"]
    record = board.agents().get(name)
    binding["model"] = model
    if not record or record.get("model") == model:
        return True
    caller = binding_caller(board, binding, subagent=is_subagent(data), env=worker_env(data, env))
    if getattr(caller, "scope_card", ""):
        return True
    board.join(
        caller,
        agent=name,
        role=record.get("role", "worker"),
        runtime=record.get("runtime") or "claude-code",
        provider=record.get("provider") or None,
        reported_model=model,
    )
    return True


def heartbeat_due(env, data, binding):
    path = os.path.join(state_dir(env), "hb", _safe(data.get("session_id", "")) + "-" + _safe(agent_key(data)))
    try:
        if time.time() - os.path.getmtime(path) < HEARTBEAT_SECONDS:
            return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(binding.get("card", ""))
    return True


def post_tool(data, env):
    session, key = data.get("session_id", ""), agent_key(data)
    binding = load_binding(env, session, key)
    changed = False
    if data.get("tool_name") == "Bash":
        changed = bind_from_bash(data, env, binding)
    try:
        changed |= record_model(data, env, binding)
    except Exception as e:
        log_error(env, "model", e)
    if changed:
        save_binding(env, session, key, binding)
    if not is_subagent(data):
        try:
            from .limits import ingest_transcript

            ingest_transcript(data)
        except Exception as e:
            log_error(env, "limits", e)
    if binding.get("card") and binding.get("fence") and binding.get("board") and heartbeat_due(env, data, binding):
        board = open_board(binding["board"], {})
        caller = binding_caller(board, binding, subagent=is_subagent(data), env=worker_env(data, env))
        board.heartbeat(binding["card"], caller)
    return None


def subagent_stop(data, env):
    binding = load_binding(env, data.get("session_id", ""), agent_key(data))
    text = (data.get("last_assistant_message") or "").strip()
    if not binding.get("name") or not binding.get("board") or not text:
        return None
    from .core import transcript

    board = open_board(binding["board"], {})
    binding_caller(board, binding, subagent=is_subagent(data), env=worker_env(data, env))
    record = board.agents().get(binding["name"], {})
    model = tail_model(transcript_for(data)) or record.get("model", "")
    sender = {"agent": binding["name"], "provider": "anthropic", "runtime": "claude-code", "model": model}
    card = binding.get("card") or binding.get("last_card", "")
    if len(text) > HANDBACK_CHARS:
        text = text[:HANDBACK_CHARS] + " [truncated]"
    board.transcript.append(
        transcript.make(
            "handback",
            sender,
            text=text,
            task=card,
            context=f"{board.name}/{card}" if card else board.name,
            src="claude-code",
        )
    )
    return None


def session_start(data, env):
    board_dir = find_board(data.get("cwd"), env.get("RAIDLOCK_BOARD") or env.get("BOARD_DIR"))
    folder = os.path.join(state_dir(env), "sessions")
    os.makedirs(folder, exist_ok=True)
    info = {
        "session_id": data.get("session_id", ""),
        "cwd": data.get("cwd", ""),
        "board": board_dir or "",
        "source": data.get("source", ""),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = os.path.join(folder, _safe(data.get("session_id", "")) + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(info, f, sort_keys=True)
    name = env.get("RAIDLOCK_AGENT_ID") or env.get("RAIDLOCK_AGENT")
    if board_dir and not is_subagent(data):
        out = native_join(data, env, board_dir)
        if out is not None:
            return out
    if board_dir and name:
        binding = load_binding(env, data.get("session_id", ""), "main")
        board_obj = open_board(board_dir, {})
        try:
            caller = board_obj.caller(agent=name, env=env)
        except Exception:
            return None
        if not caller.authenticated:
            return None
        binding.update({"name": caller.name, "board": board_dir})
        if caller.is_lead:
            binding.update(lead_session=caller.session, lead_fence=caller.lead_fence)
        if not caller.is_lead:
            binding["proof"] = hashlib.sha256(env.get("RAIDLOCK_TOKEN", "").encode()).hexdigest()
        if str(env.get("RAIDLOCK_FENCE", "")).isdigit():
            binding["fence"] = int(env["RAIDLOCK_FENCE"])
        save_binding(env, data.get("session_id", ""), "main", binding)
    return None


def env_file_lines(env, role, name="", token="", board_dir=""):
    lines = []
    if role != "lead":
        from .core.lead_env import lead_material

        stale = sorted(lead_material(env))
        if stale:
            lines.append("unset " + " ".join(stale))
    if name and token:
        lines.append(f"export RAIDLOCK_AGENT={shlex.quote(name)}")
        lines.append(f"export RAIDLOCK_TOKEN={shlex.quote(token)}")
        lines.append(f"export RAIDLOCK_BOARD={shlex.quote(os.path.dirname(board_dir))}")
    return lines


def write_env_file(env, lines):
    path = env.get("CLAUDE_ENV_FILE", "")
    if not path or not lines:
        return False
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return True


def native_join(data, env, board_dir):
    """A main session starting in a raidlock workspace: drop inherited lead material from a non-lead session and,
    when the lead configured an auto-join pool and the host offers CLAUDE_ENV_FILE, give an unbound session one
    pooled worker identity of its own."""
    from . import bridge

    board = open_board(board_dir, {})
    with bridge.pool_lock(state_dir(env), board.uid):
        return _native_join(data, env, board_dir, board)


def _native_join(data, env, board_dir, board):
    from . import bridge

    session = data.get("session_id", "")
    binding = load_binding(env, session, "main")
    if binding.get("pooled"):
        reserved = bridge.allocated(state_dir(env), board.uid, session)
        if (
            reserved != binding.get("name")
            or reserved not in bridge.load(board)["pool"]
            or not env.get("CLAUDE_ENV_FILE")
        ):
            bridge.free(state_dir(env), board.uid, session)
            binding = {}
            save_binding(env, session, "main", binding)
    board.lead_session_resumed(session)
    role = session_role(data, env, board, binding)
    if session:
        # A resumed session keeps its old CLAUDE_CODE_SESSION_ID in the shell while hooks see the new id; pin the
        # CLI to the id the hooks (and the lead lease) use.
        write_env_file(env, [f"export RAIDLOCK_SESSION={shlex.quote(session)}"])
    if role == "lead":
        from .core.hints import lead_cmd
        from .tidy import decisions

        waiting = decisions(board)
        if not waiting:
            return None
        text = (
            f"raidlock: {len(waiting)} card(s) in {board.name} wait on the lead "
            f"({', '.join(d['card'] for d in waiting[:5])}{', ...' if len(waiting) > 5 else ''}). "
            f"Run {lead_cmd(board, 'tidy')} to resolve them."
        )
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
    name = token = ""
    if binding.get("pooled") and role == "worker":
        name = binding["name"]
        token = agent_token(board, name) or ""
    elif role == "unbound" and env.get("CLAUDE_ENV_FILE"):
        name = bridge.allocate(board, state_dir(env), session)
        token = agent_token(board, name) if name else ""
        if name and not token:
            bridge.free(state_dir(env), board.uid, session)
            name = ""
    write_env_file(env, env_file_lines(env, role, name, token, board_dir))
    if not name and role == "unbound" and not lead_live(board):
        from .core.hints import lead_cmd

        text = (
            f"raidlock: the lead lease of workspace {board.name} is free (the last lead session ended or its lease "
            f"expired). If this session should lead, run: RAIDLOCK_SESSION={session} {lead_cmd(board, 'lead takeover')}"
        )
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
    if not name:
        return None
    binding.update(
        {"name": name, "board": board_dir, "proof": hashlib.sha256(token.encode()).hexdigest(), "pooled": True}
    )
    save_binding(env, session, "main", binding)
    text = (
        f"raidlock: this session joined workspace {board.name} as worker {name}. "
        "Edits are allowed only inside a lease you hold. Next: raidlock claimable, then raidlock claim <id>."
    )
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def session_end(data, env):
    from . import bridge

    if is_subagent(data):
        return None
    binding = load_binding(env, data.get("session_id", ""), "main")
    board_dir = binding.get("board") or find_board(data.get("cwd"))
    if not board_dir:
        save_binding(env, data.get("session_id", ""), "main", {})
        return None
    board = open_board(board_dir, {})
    with bridge.pool_lock(state_dir(env), board.uid):
        bridge.free(state_dir(env), board.uid, data.get("session_id", ""))
        save_binding(env, data.get("session_id", ""), "main", {})
    board.lead_session_ended(data.get("session_id", ""))
    return None


def listing_line(*parts):
    path = os.path.join(PLUGIN_ROOT, *parts)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    head = text.split("\n---", 1)[0]
    name = re.search(r"^name: (.*)$", head, re.M)
    match = re.search(r"^description: (.*)$", head, re.M)
    if not match or not name:
        return ""
    return name.group(1).strip() + ": " + match.group(1).strip()


def skill_description():
    return listing_line("skills", "raidlock", "SKILL.md")


def _hooks_tokens(board):
    return {
        "tokens": 0,
        "detail": f"silent on allow; a deny costs about {DENY_BYTES // 4} tokens; urgent messages "
        f"only when pending, at most {URGENT_CHARS} chars (~{URGENT_CHARS // 4} tokens)",
    }


def _skill_tokens(board):
    text = skill_description()
    if not text:
        return {
            "tokens": 0,
            "detail": "plugin files not found next to this install; see claude plugin details raidlock",
        }
    return {"tokens": (len(text) + 3) // 4, "detail": f"skill listing line, {len(text)} chars"}


def _agent_tokens(board):
    text = listing_line("agents", "raid-worker.md")
    if not text:
        return {"tokens": 0, "detail": "plugin files not found next to this install"}
    return {"tokens": (len(text) + 3) // 4, "detail": f"raid-worker agent listing line, {len(text)} chars"}


def register_cli(add_command):
    from .bridge import register_cli as bridge_commands
    from .commands_install import register_cli as install_commands

    install_commands(add_command)
    bridge_commands(add_command)


def register_tokens(register):
    register("claude-code hooks", _hooks_tokens)
    register("claude-code skill listing", _skill_tokens)
    register("claude-code agent listing", _agent_tokens)


HANDLERS = {
    "pre-urgent": pre_urgent,
    "pre-read": pre_read,
    "pre-write": pre_write,
    "pre-bash": pre_bash,
    "post-tool": post_tool,
    "subagent-stop": subagent_stop,
    "session-start": session_start,
    "session-end": session_end,
}
GUARDS = ("pre-read", "pre-write", "pre-bash", "pre-urgent")


def normalize_tool(data):
    from .core.errors import IdentityError

    data["_host_cwd"] = data.get("cwd")
    name = data.get("tool_name")
    shell = name in ("Bash", "exec_command", "shell", "shell_command")
    patch = name == "apply_patch"
    tool = data.get("tool_input")
    if shell or patch:
        if patch and isinstance(tool, str):
            tool = {"command": tool}
        if not isinstance(tool, dict):
            raise IdentityError("missing or malformed command")
        fields = ("command", "cmd", "input")
        values = [tool[k] for k in fields if k in tool]
        if not values or any(not isinstance(v, str) or not v.strip() for v in values):
            raise IdentityError("missing or malformed command")
        if any(v != values[0] for v in values):
            raise IdentityError("conflicting command fields")
        tool = {**tool, "command": values[0]}
        if "workdir" in tool:
            if not isinstance(tool["workdir"], str) or not tool["workdir"]:
                raise IdentityError("malformed command workdir")
            data["cwd"] = os.path.abspath(os.path.join(data.get("cwd") or os.getcwd(), tool["workdir"]))
        data["tool_input"] = tool
        if shell:
            data["tool_name"] = "Bash"
    elif name in ("Write", "Edit", "NotebookEdit"):
        if not isinstance(tool, dict) or not any(
            isinstance(tool.get(k), str) and tool[k].strip() for k in ("file_path", "notebook_path")
        ):
            raise IdentityError("missing or malformed write path")
    return data


def run(event, raw, env):
    handler = HANDLERS.get(event)
    if handler is None:
        return None
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        data = normalize_tool(data)
        if event in GUARDS and event != "pre-urgent":
            read_guard(data, env)
        return handler(data, env)
    except NoBoard:
        return None
    except Exception as e:
        log_error(env, event, e)
        if event in GUARDS:
            from .core import RaidlockError

            if isinstance(e, RaidlockError):
                return deny(e.message)
            return deny(f"hook error ({type(e).__name__}); see {os.path.join(state_dir(env), 'errors.log')}")
        return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(
            "usage: raidlock hook pre-write|pre-read|pre-bash|pre-urgent|post-tool|subagent-stop|"
            "session-start|session-end",
            file=sys.stderr,
        )
        return 3
    out = run(argv[0], sys.stdin.read(), dict(os.environ))
    if out:
        sys.stdout.write(json.dumps(out, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
