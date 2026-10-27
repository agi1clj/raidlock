import argparse
import contextlib
import json
import os
import re
import shlex
import shutil
import sys
import sysconfig
import traceback

from . import __version__
from .core import Board, Usage, clock, model, registry
from .core.db import Index
from .core.errors import IdentityError, Precondition, RaidlockError
from .core.hints import lead_cmd, lead_secret_src, quote_path
from .core.identity import detect_runtime, long_runtime, token_path
from .core.leases import describe, is_live
from .core.model import LANES

COMMANDS = {}
GROUPS = (
    ("Start", ("up", "quickstart", "doctor")),
    ("Plan", ("new", "plan")),
    ("Work", ("brief", "claim", "note", "move", "done")),
    ("Agents", ("delegate", "who", "lead")),
    ("Board", ("show", "ui")),
)
EXTENSION_MODULES = (
    "raidlock.bench",
    "raidlock.web.cli",
    "raidlock.runners.cli",
    "raidlock.routing",
    "raidlock.resume",
    "raidlock.duel",
    "raidlock.resurrect",
    "raidlock.approvals",
    "raidlock.hooks",
    "raidlock.mcp",
    "raidlock.lore.cli",
    "raidlock.scan",
    "raidlock.pr",
    "raidlock.prsync",
    "raidlock.budgets",
    "raidlock.limits",
    "raidlock.notify",
    "raidlock.snapshot",
    "raidlock.guard",
    "raidlock.infer",
    "raidlock.foresight",
    "raidlock.reputation",
    "raidlock.recipes",
    "raidlock.tui",
    "raidlock.web.raidmap",
    "raidlock.web.replay",
    "raidlock.web.graph",
    "raidlock.frames",
    "raidlock.achievements",
    "raidlock.treasury",
)


RUNTIME_HELP = "claude-code (or claude), codex-cli (or codex), gemini-cli (or gemini), aider, shell, human"
FLAG_HELP = {
    ("*", "id"): "card id",
    ("*", "note"): "short note recorded on the card",
    ("*", "team"): "team name",
    ("*", "runtime"): "agent runtime: " + RUNTIME_HELP + " (default: detected)",
    ("*", "model"): "model id (default: detected)",
    ("*", "path"): "workspace folder (default: current folder)",
    ("*", "action"): "what to do",
    ("*", "interval"): "seconds between polls",
    ("*", "timeout"): "seconds before giving up",
    ("bench", "runs"): "samples per hook (more runs give steadier percentiles)",
    ("bench", "budget"): "JSON file of metric limits; exit 3 when one is exceeded",
    ("bench", "plugin_root"): "plugin checkout to measure (needed when installed from a wheel)",
    ("bench", "tokenizer"): "estimate (4 characters per token) or auto (a real tokenizer when installed)",
    ("brief", "id"): "card to brief a worker on",
    ("budget", "budget_action"): "show the budgets, or set one",
    ("check", "paths"): "comma-separated <repo>/<path> locks to test for conflicts",
    ("claim", "id"): "card to claim",
    ("claimable", "for_review"): "list cards waiting for review instead",
    ("config", "action"): "get a setting, or set one (lead)",
    ("config", "key"): "setting name, e.g. require_verify or require_review.count",
    ("config", "value"): "new value (set only)",
    ("cost", "id"): "card the usage belongs to (omit to show totals)",
    ("cost", "model"): "model id the usage was billed to",
    ("cost", "cache_write"): "cache-write tokens",
    ("cost", "billing"): "api, plan or unknown (default: the agent's billing)",
    ("cost", "duration_ms"): "run time in milliseconds",
    ("done", "id"): "card to close",
    ("edit", "id"): "card to change",
    ("foresight", "card"): "card whose locks to test against other open work",
    ("guard", "action"): "install, uninstall or check the git guard in this checkout",
    ("guard", "hook"): "which git hook to manage",
    ("heartbeat", "id"): "card whose lease to renew",
    ("heartbeat", "ttl"): "lease minutes (default: board ttl_minutes)",
    ("help", "topic"): "advanced, or a command name",
    ("inbox", "all"): "show read messages too",
    ("init", "path"): "workspace folder that holds your repos (default: current folder)",
    ("init", "model"): "the lead's model id (default: detected)",
    ("join", "team"): "team to join (cards on other teams are not claimable)",
    ("join", "role"): "worker claims cards, reviewer reviews them",
    ("join", "provider"): "provider: anthropic, openai, google, local or human (default: from the runtime)",
    ("join", "effort"): "reasoning effort the agent runs with, if any",
    ("join", "session"): "session id of the agent (default: from the runtime's environment)",
    ("lead", "action"): "status, heartbeat (renew), handover to another session, or takeover an expired lease",
    ("lock", "id"): "your card",
    ("lock", "paths"): "comma-separated <repo>/<path> locks to add",
    ("move", "id"): "card to move",
    ("move", "lane"): "target lane (doing is reached by claim, done by raid done)",
    ("msg", "text"): "message text",
    ("msg", "to"): "receiving agent",
    ("msg", "task"): "card the message is about",
    ("new", "title"): "card title",
    ("new", "id"): "card id (default: from the title)",
    ("new", "team"): "team whose members may claim it",
    ("new", "locks"): "comma-separated <repo>/<path> paths the claimer may edit",
    ("new", "depends"): "card ids that must be done first",
    ("new", "scope"): "what is in and out of scope",
    ("new", "verify"): "how the check is described to the worker",
    ("new", "verify_cmd"): "lead: command raid verify runs in a sandbox (needed for done when gates are on)",
    ("new", "output"): "what the worker hands back",
    ("new", "ticket"): "issue tracker key or URL",
    ("new", "reviewers"): "comma-separated agents who must review it",
    ("note", "id"): "your card",
    ("note", "text"): "what changed, or evidence",
    ("plan", "action"): "import",
    ("plan", "file"): "plan file (.json or .md), or recipe:<name>",
    ("plan", "team"): "team for items that name none",
    ("pr", "draft"): "open the pull request as a draft",
    ("pr-sync", "max_polls"): "stop after this many polls",
    ("quickstart", "path"): "workspace folder that holds your repos (default: current folder)",
    ("quickstart", "lead"): "lead name (default: <runtime>-lead)",
    ("quickstart", "runtime"): "the lead's runtime: " + RUNTIME_HELP + " (default: detected)",
    ("quickstart", "team"): "name of the default team",
    ("release", "id"): "card to give back",
    ("reopen", "id"): "done card to reopen",
    ("reputation", "by"): "group scores by agent, model or provider",
    ("resurrect", "model"): "model for the replacement agent",
    ("resurrect-policy", "action"): "show the policy, or set a key",
    ("resurrect-policy", "key"): "policy key to set",
    ("review", "id"): "card in review",
    ("review", "verdict"): "approve, changes (back to doing) or comment",
    ("routing", "routing_action"): "show the rules, set a key, or explain the route for a card",
    ("show", "id"): "card to show (omit for the board)",
    ("spend", "by"): "group spend by this field",
    ("spend", "until"): "UTC date or timestamp (exclusive)",
    ("suggest-locks", "text"): "card text to suggest locks for",
    ("suggest-locks", "repos"): "comma-separated repos to search (default: all)",
    ("suggest-locks", "limit"): "most suggestions to return",
    ("team", "action"): "add, rm or list teams (add and rm: lead)",
    ("team", "name"): "team name",
    ("team", "max"): "most cards the team may have in doing at once",
    ("team", "mission"): "one line on what the team works on",
    ("team", "repos"): "comma-separated repos the team works in",
    ("transcript", "task"): "only records for this card",
    ("transcript", "kind"): "only this record kind (msg, note, status, review, verify, handback, run)",
    ("up", "team"): "team to join as a worker",
    ("up", "role"): "role when joining as a worker",
    ("land", "id"): "delegated card whose latest handed-back patch to land",
    ("land", "cwd"): "checkout of the card's repo to commit in (default: current directory)",
    ("land", "message"): "commit message (default: feat(<card>): <title>)",
    ("land", "timeout"): "seconds before the verify run is stopped",
    ("verify", "id"): "card to verify",
    ("verify", "timeout"): "seconds before the verify run is stopped",
    ("verify", "cwd"): "checkout to verify (default: the worker's recorded checkout, else the workspace)",
    ("wait", "id"): "card to wait on",
    ("workspaces", "rm"): "forget these registered workspaces",
}
LEAD = "raidlock --lead-secret <lead secret file>"
EXAMPLES = {
    "up": ["raidlock up", "raidlock up --team core   # in a worker session: join as a worker"],
    "quickstart": ["raidlock quickstart", "raidlock quickstart --demo   # also create a small git repo to try it on"],
    "new": [
        f'{LEAD} new "Validate cart" --id checkout --team core --locks acme-shop/src/checkout '
        '--verify-cmd "python3 -m unittest"',
        'raidlock new "Fix login" --suggest-locks',
    ],
    "edit": [f'{LEAD} edit checkout --verify-cmd "python3 -m unittest"'],
    "join": [
        "raidlock join --agent worker-checkout --team core --runtime shell",
        "raidlock join --agent codex-api --team core --runtime codex",
    ],
    "claim": ["raidlock claim checkout", "raidlock claim checkout --take-over"],
    "note": ['raidlock note checkout "validator added; 14 tests pass"'],
    "move": ['raidlock move checkout review --note "tests: 14 passed"', "raidlock move checkout blocked"],
    "verify": [f"{LEAD} verify checkout", f"{LEAD} verify checkout --cwd ~/demo/shop/acme-shop"],
    "done": [f"{LEAD} verify checkout", f"{LEAD} done checkout"],
    "land": [f"{LEAD} land checkout", f"{LEAD} land checkout --cwd ~/demo/shop/acme-shop"],
    "review": [
        'raidlock review checkout --verdict approve --note "read the diff; tests cover empty carts"',
        f"{LEAD} review checkout --assign codex-reviewer",
    ],
    "delegate": ["raidlock delegate checkout --to codex", "raidlock delegate checkout --to claude --as reviewer"],
    "config": ["raidlock config get", f"{LEAD} config set require_verify false"],
    "team": [f'{LEAD} team add core --max 3 --mission "checkout flow"', "raidlock team list"],
    "show": ["raidlock show", "raidlock show checkout"],
    "who": ["raidlock who", "raidlock who --brief"],
    "brief": ["raidlock brief checkout --quiet"],
    "msg": ['raidlock msg --to worker-checkout "please rebase on main" --task checkout'],
    "plan": [f"{LEAD} plan import plan.md --team core"],
    "release": ['raidlock release checkout --note "blocked on API key"'],
    "reopen": [f"{LEAD} reopen checkout"],
    "lead": [f"{LEAD} lead heartbeat", "raidlock lead status"],
    "cost": ["raidlock cost checkout", "raidlock cost checkout --model gpt-6-astra --input 1200 --output 300"],
    "spend": ["raidlock spend --by model --since 2026-10-01"],
    "approvals": ["raidlock approvals", "raidlock approvals --kind review"],
    "next": ["raidlock next", "raidlock next --provider codex"],
    "duel": [f"{LEAD} duel checkout --vs claude,codex"],
    "doctor": ["raidlock doctor"],
    "ui": ["raidlock ui --open"],
}


class UsageError(Precondition):
    kind = "usage"


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message):
        raise UsageError(message, hint=f"{self.prog} --help")


class Context:
    def __init__(self, args):
        self.args = args
        self._board = None

    @property
    def board(self):
        if self._board is None:
            self._board = Board.open(self.args.board)
        return self._board

    def lead_secret(self):
        from .core import lead as LD

        if not hasattr(self, "_secret"):
            src = getattr(self.args, "lead_secret", None)
            self._secret = LD.read_presented(src, self.board.leadlease().store) if src else None
        return self._secret

    def caller(self):
        a = self.args
        return self.board.caller(
            agent=a.agent, token=a.token, fence=a.fence, lead_secret=self.lead_secret(), cap=getattr(a, "cap", None)
        )


def add_command(name, help_text, setup, handler, mutating=False, aliases=()):
    """Extension point: register a subcommand. `setup(parser)` adds arguments; `handler(ctx, args)` returns
    (result_dict, human_text). `ctx.board` opens the board lazily; `ctx.caller()` resolves the agent."""
    COMMANDS[name] = {"fn": handler, "help": help_text, "mutating": mutating, "aliases": tuple(aliases), "setup": setup}


def command(name, help_text, mutating=False, aliases=()):
    def wrap(fn):
        add_command(name, help_text, None, fn, mutating, aliases)
        return fn

    return wrap


def options(name):
    def wrap(fn):
        COMMANDS[name]["setup"] = fn
        return fn

    return wrap


def _entry_points():
    from importlib.metadata import entry_points

    eps = entry_points()
    return eps.select(group="raidlock.commands") if hasattr(eps, "select") else eps.get("raidlock.commands", [])


def load_extensions(debug=False):
    import importlib

    for module in EXTENSION_MODULES:
        try:
            mod = importlib.import_module(module)
        except ImportError:
            continue
        if hasattr(mod, "register_cli"):
            mod.register_cli(add_command)
    try:
        group = list(_entry_points())
    except Exception:
        return
    for ep in group:
        try:
            ep.load()(add_command)
        except (ImportError, AttributeError) as e:
            if debug:
                print(f"raidlock: debug: extension {ep.name} ({ep.value}) not installed, skipped: {e}", file=sys.stderr)
        except Exception as e:
            print(f"raidlock: extension {ep.name} failed to load: {e}", file=sys.stderr)


def glyph(emoji, fallback):
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        emoji.encode(enc)
        return emoji
    except (UnicodeEncodeError, LookupError):
        return fallback


def short(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home) :] if path and path.startswith(home + os.sep) else path


def hhmm(ts):
    dt = clock.parse(ts) if ts else None
    return dt.astimezone().strftime("%H:%M") if dt else "it leaves review/blocked"


GLOBAL_DESTS = ("board", "json", "agent", "token", "fence", "fields", "debug", "lead_secret", "cap")
PROGS = ("raid", "raidlock")
_COMMAND_WORD = re.compile(r"(?<![\w./-])raidlock (?=[a-z-])")


def prog_name(argv0=None):
    base = os.path.basename(argv0 if argv0 is not None else (sys.argv[0] if sys.argv else ""))
    base = base[:-4] if base.lower().endswith(".exe") else base
    return base if base in PROGS else "raidlock"


def rename(text, prog):
    """Rewrite command hints in `text` to the name the user invoked: the part of a line after Next:/next:, and lines
    that start with a raidlock command."""
    if prog == "raidlock" or not text:
        return text
    out = []
    for line in text.split("\n"):
        body = line.lstrip()
        if body.startswith("raidlock "):
            line = line[: len(line) - len(body)] + _COMMAND_WORD.sub(prog + " ", body)
        else:
            for marker in ("Next:", "next:", "Warning:"):
                i = line.find(marker)
                if i >= 0:
                    line = line[:i] + _COMMAND_WORD.sub(prog + " ", line[i:])
                    break
        out.append(line)
    return "\n".join(out)


def _common_args(p):
    p.add_argument("--board", default=None, help="workspace or .board folder (default: nearest .board)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--agent", default=None, help="agent name (default: RAIDLOCK_AGENT_ID or RAIDLOCK_AGENT)")
    p.add_argument("--token", default=None, help="agent token (default: RAIDLOCK_TOKEN)")
    p.add_argument("--fence", type=int, default=None, help="card fence (default: RAIDLOCK_FENCE)")
    p.add_argument(
        "--lead-secret",
        default=None,
        metavar="SRC",
        help="present the lead secret: a 0600 file, - for stdin, or keychain",
    )
    p.add_argument("--cap", default=None, help="card-scoped capability (default: RAIDLOCK_CAP)")
    p.add_argument("--fields", default=None, help="--json fields: all, or a comma list (default: minimal)")
    p.add_argument("--debug", action="store_true", help="show tracebacks")


def common():
    p = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    _common_args(p)
    return p


def command_name(word):
    if word in COMMANDS:
        return word
    return next((n for n, s in COMMANDS.items() if word in s["aliases"]), None)


def _examples(name, prog):
    lines = EXAMPLES.get(name)
    if not lines:
        return None
    return "examples:\n" + "\n".join("  " + rename(line, prog) for line in lines)


def command_parser(name, prog="raidlock"):
    spec = COMMANDS[name]
    p = Parser(
        prog=f"{prog} {name}",
        description=spec["help"],
        epilog=_examples(name, prog),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.set_defaults(_cmd=name)
    if spec["setup"]:
        spec["setup"](p)
    _common_args(p.add_argument_group("common options"))
    for action in p._actions:
        if not action.help and action.dest != "help":
            action.help = FLAG_HELP.get((name, action.dest)) or FLAG_HELP.get(("*", action.dest))
    return p


def parse(argv, prog="raidlock"):
    """Parse a command line. Global flags may appear anywhere; command flags and positionals may interleave."""
    pre, rest = common().parse_known_args(argv)
    if not rest or rest[0].startswith("-"):
        raise UsageError(
            "no command given" if not rest else f"unrecognized arguments: {' '.join(rest)}", hint=f"{prog} --help"
        )
    name = command_name(rest[0])
    if name is None:
        raise UsageError(f"unknown command {rest[0]}", hint=f"{prog} --help")
    p = command_parser(name, prog)
    try:
        args = p.parse_intermixed_args(rest[1:])
    except TypeError:
        args = p.parse_args(rest[1:])
    for dest in GLOBAL_DESTS:
        if getattr(args, dest, None) in (None, False):
            setattr(args, dest, getattr(pre, dest))
    args.prog = prog
    return args


def everyday_help(prog="raidlock"):
    lines = [f"{prog} {__version__}: a lease-and-review board for coding agents.", ""]
    width = max(len(n) for n in COMMANDS)
    for title, names in GROUPS:
        present = [n for n in names if n in COMMANDS]
        if present:
            lines.append(f"{title}:")
            lines += [f"  {n:<{width}}  {COMMANDS[n]['help']}" for n in present]
    lines += [
        "",
        f"More commands: {prog} help advanced   Command help: {prog} <command> --help",
        "Every command takes --json for machines.",
    ]
    return "\n".join(lines)


def advanced_help():
    everyday = {n for _, names in GROUPS for n in names}
    width = max(len(n) for n in COMMANDS)
    rows = [f"  {n:<{width}}  {s['help']}" for n, s in sorted(COMMANDS.items()) if n not in everyday]
    return (
        "Advanced commands:\n" + "\n".join(rows) + "\n\nExit codes: 0 ok, 2 denied or conflict, 3 precondition, "
        "4 timeout or stale lead fence, 5 identity or role. Usage errors also exit 3."
    )


MINIMAL = {
    "init": ("workspace", "lead", "token_file", "created"),
    "quickstart": ("workspace", "created", "lead", "token_file", "card", "demo_repo"),
    "up": ("action", "agent", "token", "token_file", "env_file", "status", "card", "lead", "workspace"),
    "doctor": ("healthy", "problems", "warnings"),
    "join": ("agent", "token", "role", "team", "display", "billing"),
    "new": ("id", "created", "suggestions", "warnings"),
    "edit": ("card", "verify_cmd", "verify"),
    "claim": ("card", "agent", "fence", "expires", "paths", "cap"),
    "heartbeat": ("card", "expires", "resumed"),
    "note": ("card",),
    "lock": ("card", "locks"),
    "release": ("card", "lane", "released"),
    "move": ("card", "lane", "a2a_state"),
    "done": ("card", "lane", "a2a_state", "warnings"),
    "reopen": ("card", "lane"),
    "review": ("card", "verdict", "lane", "approvals", "reviewers", "warnings"),
    "verify": ("card", "passed", "exit", "tail"),
    "land": ("card", "commit", "verify", "lane", "next"),
    "msg": ("id", "status", "dropped"),
    "inbox": ("messages", "queued"),
    "wait": ("card", "lane"),
    "check": ("free", "conflicts", "allowed", "reason"),
    "cost": ("card", "entry", "total", "rows"),
    "brief": ("card", "text", "tokens", "over_cap"),
}
SLIM = {
    "show": lambda r: (
        {**r, "cards": [{k: c.get(k) for k in ("id", "title", "lane", "owner", "team")} for c in r["cards"]]}
        if isinstance(r.get("cards"), list)
        else r
    ),
    "who": lambda r: {
        "agents": {n: {k: a.get(k) for k in ("display", "status", "card")} for n, a in r["agents"].items()},
        "lead_lease": r.get("lead_lease"),
    },
}


def select(name, result, fields):
    if fields == "all":
        return result
    if fields:
        keys = [f.strip() for f in fields.split(",") if f.strip()]
        return {k: result[k] for k in keys if k in result}
    if name in SLIM:
        return SLIM[name](result)
    if name in MINIMAL:
        return {k: result[k] for k in MINIMAL[name] if k in result}
    return result


def emit(args, name, result, text, board_dir, mutating, prog="raidlock"):
    if args.json:
        out = select(name, result, args.fields)
        if args.fields == "all" and board_dir:
            out = {"board": board_dir, **out}
        print(json.dumps({"ok": True, **out}, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str))
        return
    if text and mutating and board_dir:
        first, _, rest = text.partition("\n")
        text = f"[{os.path.basename(os.path.dirname(board_dir))}] {first}" + (f"\n{rest}" if rest else "")
    if text:
        print(rename(text, prog))


WINDOWS_MESSAGE = (
    "raidlock does not support native Windows yet; run it inside WSL2: "
    "https://github.com/agi1clj/raidlock/blob/main/docs/guides/install.md#on-windows"
)


def main(argv=None, prog=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if sys.platform == "win32" and argv[:1] == ["hook"]:
        from ._python import GUARDS

        if argv[1:2] and argv[1] in GUARDS:
            decision = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "raidlock: " + WINDOWS_MESSAGE,
                }
            }
            sys.stdout.write(json.dumps(decision, separators=(",", ":")))
        return 0
    if sys.platform == "win32" and argv[:1] not in (["--version"], ["-V"]):
        print(WINDOWS_MESSAGE, file=sys.stderr)
        return 3
    if argv[:1] == ["hook"]:
        from .hooks import main as hook_main

        return hook_main(argv[1:])
    prog = prog or prog_name()
    want_json, debug = "--json" in argv, "--debug" in argv
    load_extensions(debug)
    if not argv or argv[0] in ("-h", "--help"):
        print(everyday_help(prog))
        return 0
    if argv[0] in ("--version", "-V"):
        print(f"{prog} {__version__}")
        return 0
    try:
        args = parse(argv, prog)
        found = None
        if args._cmd not in ("doctor", "migrate", "help", "version"):
            from .core.board import find_board_dir
            from .core.migrate import reject_legacy

            try:
                found = find_board_dir(args.board)
            except Precondition:
                found = None
            if found:
                reject_legacy(found)
        spec = COMMANDS[args._cmd]
        ctx = Context(args)
        if (
            found
            and (
                args.lead_secret
                or os.environ.get("RAIDLOCK_LEAD_SECRET")
                or os.environ.get("RAIDLOCK_LEAD_SECRET_FILE")
            )
            and not (args._cmd == "lead" and args.action == "takeover")
        ):
            ctx.caller()
        result, text = spec["fn"](ctx, args)
        code = result.pop("_exit", 0)
        board_dir = ctx._board.dir if ctx._board else None
        emit(args, args._cmd, result, text, board_dir, spec["mutating"], prog)
        return code
    except RaidlockError as e:
        _lead_identity_hint(e, locals().get("ctx"), argv)
        if e.hint:
            e.hint = _COMMAND_WORD.sub(prog + " ", e.hint) if prog != "raidlock" else e.hint
        if want_json:
            print(json.dumps(e.to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str))
        else:
            print(f"{prog}: {e.message}" + (f"\nNext: {e.hint}" if e.hint else ""), file=sys.stderr)
        if debug:
            traceback.print_exc()
        return e.code
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        if debug:
            traceback.print_exc()
        if want_json:
            print(json.dumps({"ok": False, "error": "internal", "code": 1, "message": str(e)}))
        else:
            print(f"{prog}: internal error: {e}\nNext: rerun with --debug and report it", file=sys.stderr)
        return 1


LEAD_COMMANDS = {
    "new",
    "edit",
    "plan",
    "verify",
    "land",
    "done",
    "reopen",
    "reap",
    "team",
    "config",
    "lead",
    "approvals",
    "delegate",
    "duel",
    "resurrect",
    "resurrect-policy",
    "pr",
    "pr-sync",
    "routing",
    "budget",
    "lore",
}


def _lead_session_state(board):
    """(lead lease held, this call comes from the lead's session)."""
    try:
        from .core import lead as LD

        status = board.lead_status()
        sid = LD.session_of(board.env)[0]
    except (RaidlockError, OSError):
        return False, False
    return bool(status.get("held")), bool(status.get("held")) and bool(sid) and sid == status.get("session")


STRICT_LEAD_COMMANDS = {"edit", "verify", "land", "done", "reopen", "reap", "delegate", "duel", "resurrect"}


def _lead_identity_hint(e, ctx, argv):
    """Point a caller without the lead secret at the exact lead command when the command needs the lead, or when the
    call comes from the lead's own session; never point the lead at join."""
    if not isinstance(e, IdentityError) or ctx is None or ctx._board is None:
        return
    if getattr(ctx.args, "lead_secret", None) or any(k.startswith("RAIDLOCK_LEAD_SECRET") for k in os.environ):
        return
    from .core.identity import LEAD_HINT

    no_identity = e.message == "no agent identity"
    lead_only = ctx.args._cmd in STRICT_LEAD_COMMANDS or e.hint == LEAD_HINT or e.message.startswith("only the lead")
    if not (no_identity or lead_only):
        return
    board = ctx._board
    held, in_lead_session = _lead_session_state(board)
    if not held:
        return
    if not (in_lead_session or lead_only or ctx.args._cmd in LEAD_COMMANDS):
        return
    rest = " ".join(shlex.quote(x) for x in _without_identity(argv))
    suffix = "" if in_lead_session or lead_only else "   (a worker runs raidlock join instead)"
    command = lead_cmd(board, rest) if in_lead_session else f"raidlock --lead-secret <lead secret file> {rest}"
    e.hint = command + suffix


IDENTITY_FLAGS = ("--agent", "--token", "--cap", "--fence", "--lead-secret")


def _without_identity(argv):
    """The command line without worker identity flags, which the lead must not pass."""
    out, skip = [], False
    for word in argv:
        if skip:
            skip = False
            continue
        if word in IDENTITY_FLAGS:
            skip = True
            continue
        if word.startswith(tuple(f + "=" for f in IDENTITY_FLAGS)):
            continue
        out.append(word)
    return out


def _exports(**values):
    return "\n".join(
        f"export RAIDLOCK_{k.upper()}={shlex.quote(str(v))}" for k, v in values.items() if v not in (None, "")
    )


def _interactive():
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


@command("help", "show help: raid help [advanced|<command>]")
def cmd_help(ctx, a):
    if a.topic == "advanced":
        return {"commands": sorted(COMMANDS)}, advanced_help()
    name = command_name(a.topic) if a.topic else None
    if name:
        return {"command": name}, command_parser(name, a.prog).format_help()
    return {"commands": sorted(COMMANDS)}, everyday_help(a.prog)


@options("help")
def _help_opts(p):
    p.add_argument("topic", nargs="?")


def _lead_file(board):
    return token_path(board.home, board.uid, board.lead)


def _lead_lines(board):
    where = board.leadlease().store.using
    src = lead_secret_src(board)
    return [
        f"The lead secret is in {src}" + (" (mode 0600)" if where == "file" else "") + "; it is never printed. "
        "Lead commands present it, from this session only; never give it to an agent:",
        "  " + lead_cmd(board, "<command>"),
        _exports(board=board.workspace, agent=board.lead),
    ]


def _init_text(board, token):
    if not token:
        return f"Board for {board.name} already exists at {short(board.dir)}.\nNext: raidlock show"
    lines = (
        [f"Created a board for {board.name}. You are the lead ({board.lead})."]
        + _lead_lines(board)
        + ["Next: " + lead_cmd(board, 'new "<task>" --locks <repo>/<path> --verify-cmd "<check>"')]
    )
    return "\n".join(lines + [f"Warning: {w}" for w in board.warnings()])


@command("init", "create a board in a workspace folder", mutating=True)
def cmd_init(ctx, a):
    board, token = Board.init(
        a.path,
        lead=a.lead,
        runtime=long_runtime(a.runtime) or detect_runtime(interactive=_interactive()),
        model_id=a.model,
        auth=not a.no_auth,
    )
    ctx._board = board
    return {
        "workspace": board.workspace,
        "lead": board.lead,
        "token_file": _lead_file(board),
        "created": token is not None,
        "warnings": board.warnings(),
    }, _init_text(board, token)


@options("init")
def _init_opts(p):
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--lead", default=None, help="lead name (default <runtime>-lead)")
    p.add_argument("--runtime", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--no-auth", action="store_true", help="do not require agent tokens")


def _update_line():
    from . import updates

    newer = updates.notice(updates.refresh())
    return f"\n{newer}" if newer else ""


@command("up", "get going: create the board, join it, or show what you are on", mutating=True)
def cmd_up(ctx, a):
    try:
        board = ctx.board
    except Precondition:
        if a.board:
            raise
        board, token = Board.init(
            ".", runtime=long_runtime(a.runtime) or detect_runtime(interactive=_interactive()), model_id=a.model
        )
        ctx._board = board
        return {
            "action": "init",
            "workspace": board.workspace,
            "lead": board.lead,
            "token_file": _lead_file(board),
        }, _init_text(board, token) + _update_line()
    caller = ctx.caller()
    if a.join and caller.is_lead:
        raise IdentityError(
            "worker joins must not present a lead secret",
            hint="remove --lead-secret and scrub RAIDLOCK_LEAD_* from the worker environment",
        )
    if not caller.name and not a.join and _lead_session_state(board)[1]:
        return {"action": "lead", "lead": True, "agent": board.lead, "workspace": board.workspace}, (
            f"This session holds the lead of {board.name}; up does not join it as a worker.\n"
            "Next: " + lead_cmd(board, "up")
        )
    if a.join or not caller.name or caller.name not in board.agents():
        if board.meta.get("migrated_from") and not a.join:
            raise Precondition("migrated board has no identity; use lead takeover or raid up --join")
        r = _no_lead_token(
            board,
            board.join(
                caller,
                agent=caller.name or None,
                team=a.team or (board.card(a.card).get("team") if a.card else None),
                role=a.role,
                runtime=long_runtime(a.runtime) or detect_runtime(interactive=_interactive()),
                model_id=a.model,
            ),
        )
        text = f"Joined {board.name} as {r['display']}."
        if r["agent"] != board.lead:
            from .core import identity, safeio

            token = r.get("token") or identity.read_token(board.home, board.uid, r["agent"])
            path = os.path.join(board.home, "agents", board.uid, r["agent"] + ".env")
            safeio.write_text(
                path,
                "unset RAIDLOCK_CAP RAIDLOCK_FENCE\n"
                + _exports(board=board.dir, agent=r["agent"], agent_id=r["agent"], token=token)
                + "\n",
            )
            r["env_file"] = path
            text += "\nsource " + shlex.quote(path)
        return {"action": "join", **r}, text + "\nNext: raidlock claimable"
    return {"action": "status", **_status(board, caller)}, _status_text(board, caller)


def _no_lead_token(board, r):
    if r.get("agent") == board.lead:
        r = {k: v for k, v in r.items() if k != "token"}
        r["token_file"] = _lead_file(board)
    return r


def _status(board, caller):
    me = board.who().get(caller.name, {})
    return {
        "agent": caller.name,
        "status": me.get("status", "idle"),
        "card": me.get("card", ""),
        "lead": caller.is_lead,
    }


def _status_text(board, caller):
    s = _status(board, caller)
    if s["card"]:
        lease = board.leases().get(s["card"]) or {}
        return (
            f"You are {caller.name} on {board.name}: {s['status']} on {s['card']} "
            f"(fence {lease.get('fence', '-')}, lock until {hhmm(lease.get('expires'))}).\n"
            f'Next: raidlock note {s["card"]} "..."'
        )
    if caller.is_lead:
        review = [c["id"] for c in board.cards() if c["lane"] == "review"]
        nxt = _close_step(board, board.card(review[0])) if review else "raidlock show"
        return f"You are the lead of {board.name}; {len(review)} card(s) in review.\nNext: {nxt}"
    return f"You are {caller.name} on {board.name} with no card.\nNext: raidlock claimable"


@options("up")
def _up_opts(p):
    p.add_argument("--join", action="store_true", help="explicitly join as a worker")
    p.add_argument("--card", default=None, help="default the worker team from this card")
    p.add_argument("--team", default=None)
    p.add_argument("--role", default="worker", choices=["worker", "reviewer"])
    p.add_argument("--runtime", default=None)
    p.add_argument("--model", default=None)


def _close_step(board, card):
    if card.get("verify_cmd") or board.settings.get("require_verify"):
        return lead_cmd(board, f"verify {card['id']}") + ", then " + lead_cmd(board, f"done {card['id']}")
    return lead_cmd(board, f"done {card['id']}")


DEMO_REPO = "acme-shop"
DEMO_FILES = {
    "src/__init__.py": "",
    "src/checkout/__init__.py": "",
    "src/checkout/cart.py": (
        'def validate(cart):\n    """Return the cart if it can be checked out."""\n    return cart\n'
    ),
    "tests/__init__.py": "",
    "tests/checkout/__init__.py": "",
    "tests/checkout/test_cart.py": (
        "import unittest\n"
        "\n"
        "from src.checkout.cart import validate\n"
        "\n"
        "\n"
        "class CartTest(unittest.TestCase):\n"
        "    def test_keeps_items(self):\n"
        '        self.assertEqual(validate(["book"]), ["book"])\n'
        "\n"
        "    def test_rejects_empty_cart(self):\n"
        "        with self.assertRaises(ValueError):\n"
        "            validate([])\n"
    ),
    ".gitignore": "__pycache__/\n",
    "README.md": "# acme-shop\n\nA tiny repo for trying raidlock. Run the tests with `python3 -m unittest`.\n",
}


def _demo_repo(workspace):
    """Create a small git repo with one failing test, so the getting-started guide has real work to verify."""
    from .runners import workspace as W

    root = os.path.join(workspace, DEMO_REPO)
    if os.path.exists(root):
        raise Precondition(f"{short(root)} already exists", hint="raidlock quickstart   (without --demo)")
    for rel, text in DEMO_FILES.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    ident = ["-c", "user.name=raidlock demo", "-c", "user.email=demo@raidlock.invalid", "-c", "commit.gpgsign=false"]
    W.git(root, "init", "-q", "-b", "main")
    W.git(root, "add", ".")
    W.git(root, *ident, "commit", "-q", "--no-verify", "-m", "demo: cart without validation")
    return root


@command("quickstart", "new board with a default team and an example card", mutating=True)
def cmd_quickstart(ctx, a):
    if a.demo and os.path.isdir(os.path.join(a.path, ".board")):
        raise Precondition(f"a board already exists in {short(os.path.abspath(a.path))}", hint="raidlock show")
    board, token = Board.init(
        a.path, lead=a.lead, runtime=long_runtime(a.runtime) or detect_runtime(interactive=_interactive())
    )
    ctx._board = board
    if token is None:
        return {
            "workspace": board.workspace,
            "created": False,
        }, f"A board already exists at {short(board.dir)}.\nNext: raidlock show"
    demo = _demo_repo(board.workspace) if a.demo else None
    lead = board.caller(lead_secret=token)
    board.add_team(lead, a.team, 2, "default team")
    card = board.new_card(
        lead,
        "Example: replace with a real task",
        card_id="example",
        team=a.team,
        locks=a.locks,
        verify="describe how the lead checks this",
    )
    steps = [
        f"Created {board.name} with team {a.team} and card {card['id']}. You are the lead ({board.lead}).",
        *_lead_lines(board),
    ]
    if demo:
        steps.append(
            f"Demo repo: {short(demo)} (a git repo whose empty-cart test fails until a worker fixes it). "
            "Follow https://github.com/agi1clj/raidlock/blob/main/docs/guides/getting-started.md from here."
        )
    steps += [
        "Then:",
        "  " + lead_cmd(board, f'new "<task>" --team {a.team} --locks <repo>/<path> --verify-cmd "<check>"'),
        f"  raidlock join --agent claude-worker-1 --team {a.team} --runtime claude   # worker session; prints a token",
        "  raidlock claim <card>             # as the worker, inside its checkout",
        "  raidlock move <card> review       # worker hands back",
        "  " + lead_cmd(board, "verify <card>") + "   # lead: signed receipt",
        "  " + lead_cmd(board, "done <card>"),
        "  raidlock ui                       # board in the browser (127.0.0.1)",
    ]
    return {
        "workspace": board.workspace,
        "created": True,
        "lead": board.lead,
        "token_file": _lead_file(board),
        "team": a.team,
        "card": card["id"],
        "demo_repo": demo,
    }, "\n".join(steps)


@options("quickstart")
def _qs_opts(p):
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--lead", default=None)
    p.add_argument("--runtime", default=None)
    p.add_argument("--team", default="core")
    p.add_argument("--locks", default=None, help="locks for the example card")
    p.add_argument("--demo", action="store_true", help=f"also create {DEMO_REPO}/, a small git repo to try raidlock on")


@command("team", "add, remove or list teams", mutating=True)
def cmd_team(ctx, a):
    b = ctx.board
    if a.action == "list":
        teams = b.team_list()
        lines = [
            f"{n}: max {t['max']}, members {', '.join(t['members']) or '-'}"
            + (f" ({t['mission']})" if t.get("mission") else "")
            for n, t in teams.items()
        ]
        return {"teams": teams}, "\n".join(lines) or "No teams yet.\nNext: raidlock team add <name>"
    if not a.name:
        raise UsageError(f"team {a.action} needs a name", hint=f"raidlock team {a.action} <name>")
    if a.action == "add":
        t = b.add_team(ctx.caller(), a.name, a.max, a.mission or "", a.repos)
        return t, f"Team {a.name} ready (max {t['max']} in doing). Next: raidlock join --team {a.name}"
    return b.remove_team(ctx.caller(), a.name), f"Removed team {a.name}."


@options("team")
def _team_opts(p):
    p.add_argument("action", choices=["add", "rm", "list"])
    p.add_argument("name", nargs="?")
    p.add_argument("--max", type=int, default=None)
    p.add_argument("--mission", default=None)
    p.add_argument("--repos", default=None)


@command(
    "config",
    "get or set board settings: auth, ttl_minutes, hold_minutes, require_review, require_verify, "
    "require_card_for_lead_edits, board_first_subagents",
    mutating=True,
)
def cmd_config(ctx, a):
    b = ctx.board
    if a.action == "get":
        value = b.get_setting(a.key)
        if a.key:
            return {"key": a.key, "value": value}, json.dumps(value, sort_keys=True)
        return {"settings": value}, "\n".join(f"{k}: {json.dumps(v, sort_keys=True)}" for k, v in value.items())
    if a.key is None or a.value is None:
        raise UsageError("config set needs a key and a value", hint="raidlock config set require_review 1")
    settings = b.set_setting(ctx.caller(), a.key, a.value)
    return {"settings": settings}, f"Set {a.key} to {a.value}."


@options("config")
def _config_opts(p):
    p.add_argument("action", nargs="?", default="get", choices=["get", "set"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")


@command("join", "join the board as an agent; prints its token once", mutating=True)
def cmd_join(ctx, a):
    board = ctx.board
    r = _no_lead_token(
        board,
        board.join(
            ctx.caller(),
            agent=a.agent,
            team=a.team,
            role=a.role,
            runtime=long_runtime(a.runtime) or detect_runtime(interactive=_interactive()),
            provider=a.provider,
            model_id=a.model,
            flag_model=a.runner_model,
            reported_model=a.reported_model,
            effort=a.effort,
            session=a.session or _env_session(),
            card=a.card,
            billing=a.billing,
        ),
    )
    text = f"Joined as {r['display']}" + (f" on team {r['team']}" if r["team"] else "") + f" ({r['role']})."
    if r.get("token"):
        text += "\n" + _exports(agent=r["agent"], token=r["token"])
    elif r["agent"] != board.lead:
        from .core.identity import token_path

        path = token_path(board.home, board.uid, r["agent"])
        r["credential_path"] = path
        text += f"\nExisting credential: {path}"
    nxt = "raidlock claimable" + (" --for-review" if r["role"] == "reviewer" else "")
    return r, text + (f"  # next: {nxt}" if r.get("token") else f" Next: {nxt}")


def _env_session():
    from .core.lead import SESSION_VARS

    return next((os.environ[v].strip() for v in SESSION_VARS if os.environ.get(v, "").strip()), "")


@options("join")
def _join_opts(p):
    p.add_argument("--team", default=None)
    p.add_argument("--role", default="worker", choices=["lead", "worker", "reviewer"])
    p.add_argument("--runtime", default=None, help=RUNTIME_HELP + " (default: detected)")
    p.add_argument("--provider", default=None)
    p.add_argument("--model", default=None, help="model id (default: detected)")
    p.add_argument("--runner-model", default=None, help="model a runner passed with -m")
    p.add_argument("--reported-model", default=None, help="model reported by the runtime")
    p.add_argument("--effort", default=None)
    p.add_argument("--session", default=None)
    p.add_argument("--card", default=None, help="card id used in a generated name")
    p.add_argument("--billing", choices=["api", "plan", "unknown"], default=None, help="default: detected")


@command("new", "create a card", mutating=True)
def cmd_new(ctx, a):
    if a.suggest_locks:
        if a.locks is not None:
            raise Precondition("lock suggestions do not create a card; rerun with --locks without --suggest-locks")
        from .infer import suggest_locks

        suggestions = suggest_locks(ctx.board, " ".join(filter(None, (a.title, a.scope))))
        text = "\n".join(f"{item['path']}: {item['reason']}" for item in suggestions) or "No lock suggestions."
        if suggestions:
            paths = ",".join(item["path"] for item in suggestions)
            text += (
                "\nNo card created. Review these paths, then rerun without --suggest-locks and add "
                f"--locks {shlex.quote(paths)}."
            )
        else:
            text += "\nNo card created."
        return {"suggestions": suggestions, "created": False}, text
    c = ctx.board.new_card(
        ctx.caller(),
        a.title,
        card_id=a.id,
        team=a.team or "",
        locks=a.locks,
        depends=a.depends,
        scope=a.scope or "",
        verify=a.verify or "",
        verify_cmd=a.verify_cmd or "",
        output=a.output or "",
        ticket=a.ticket or "",
        reviewers=a.reviewers,
        repo=a.repo,
        allow_protected=a.allow_protected,
    )
    locks = f"locks {', '.join(c['locks'])}" if c["locks"] else "no locks, so it reserves nothing"
    warnings = []
    if not c.get("verify_cmd") and ctx.board.settings.get("require_verify"):
        warnings.append(
            f"require_verify is on and {c['id']} has no verify command, so done will refuse it until one is set: "
            + lead_cmd(ctx.board, f'edit {c["id"]} --verify-cmd "<check>"')
        )
    text = f"Created {c['id']} ({locks}). Next: raidlock claim {c['id']}"
    extra = {"warnings": warnings} if warnings else {}
    return {"id": c["id"], "card": c, **extra}, text + "".join(f"\nWarning: {w}" for w in warnings)


@command("edit", "lead: edit verification, repository, locks, dependencies or scope", mutating=True)
def cmd_edit(ctx, a):
    r = ctx.board.edit_card(
        ctx.caller(),
        a.id,
        verify_cmd=a.verify_cmd,
        verify=a.verify,
        repo=a.repo,
        locks=a.locks,
        depends=a.depends,
        scope=a.scope,
        allow_protected=a.allow_protected,
        exclude_locks=a.exclude_locks,
        boss=None if a.boss is None else a.boss == "on",
    )
    if set(r) - {"card"} == {"boss"}:
        state = "is now a boss on the boss board" if r["boss"] else "is no longer a boss"
        return r, f"{a.id} {state}. Next: raidlock show {a.id}"
    if "verify_cmd" in r:
        what = f"verify command is now {r['verify_cmd']!r}" if r["verify_cmd"] else "verify command is cleared"
    else:
        what = "updated " + ", ".join(k for k in r if k != "card")
    nxt = lead_cmd(ctx.board, f"verify {a.id}") if r.get("verify_cmd") else "raidlock show " + a.id
    return r, f"Edited {a.id}: {what}; earlier receipts no longer count. Next: {nxt}"


@options("edit")
def _edit_opts(p):
    p.add_argument("id")
    p.add_argument("--verify-cmd", default=None, help='command raid verify runs in a sandbox ("" clears it)')
    p.add_argument("--verify", default=None, help="how the check is described to the worker")
    p.add_argument("--allow-protected", default=None, help="lead-approved protected paths, comma-separated")
    p.add_argument("--repo", default=None, help="workspace repository for this card")
    p.add_argument("--locks", default=None, help="replace reserved paths; empty string clears them")
    p.add_argument("--exclude-locks", default=None, help="lead: reserve the directory except these file paths")
    p.add_argument("--depends", default=None, help="replace dependency card IDs")
    p.add_argument("--scope", default=None, help="replace the work description")
    p.add_argument("--boss", choices=("on", "off"), default=None, help="mark a large quest as a boss on the boss board")


@options("new")
def _new_opts(p):
    p.add_argument("title")
    p.add_argument("--allow-protected", default=None, help="lead-approved protected paths, comma-separated")
    p.add_argument("--suggest-locks", action="store_true", help="preview lock suggestions without creating a card")
    for k in (
        "id",
        "team",
        "locks",
        "depends",
        "scope",
        "verify",
        "verify-cmd",
        "output",
        "ticket",
        "reviewers",
        "repo",
    ):
        p.add_argument(f"--{k}", default=None, help="workspace repository" if k == "repo" else None)


@command("plan", "import cards from a JSON or Markdown plan: raid plan import FILE", mutating=True)
def cmd_plan(ctx, a):
    if a.file.startswith("recipe:"):
        from .recipes import parse_variables, read_plan

        items = read_plan(a.file, a.team, parse_variables(a.var))
    else:
        if a.var:
            raise Precondition("--var applies only to recipe imports")
        items = _read_plan(a.file, a.team)
    cards = ctx.board.import_plan(ctx.caller(), items)
    ids = [c["id"] for c in cards]
    return {"cards": ids}, f"Imported {len(ids)} card(s): {', '.join(ids)}.\nNext: raidlock show"


def _read_plan(path, team):
    if os.path.isdir(path):
        path = os.path.join(path, "tasks.md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise Precondition(
            f"cannot read {path}: {e.strerror}", hint="raidlock plan import <file.json|file.md>"
        ) from None
    if path.endswith(".json"):
        try:
            data = json.loads(text)
        except ValueError as e:
            raise Precondition(f"{path} is not valid JSON: {e}") from None
        items = data.get("cards", []) if isinstance(data, dict) else data
    else:
        bullets = [line for line in text.splitlines() if line.lstrip().startswith(("- ", "* "))]
        tasks = [line for line in bullets if _MD_BOX.match(line)]
        items = [_md_item(line) for line in (tasks or bullets) if not _MD_DONE.match(line)]
    return [{"team": team, **i} if team and isinstance(i, dict) and not i.get("team") else i for i in items]


_MD_BOX = re.compile(r"^\s*[-*] \[[ xX]\]")
_MD_DONE = re.compile(r"^\s*[-*] \[[xX]\]")
_MD_TASK_NO = re.compile(r"^(T\d+|\d+(?:\.\d+)+\.?|\d+\.)\s+")
_MD_MARKER = re.compile(r"^\[[A-Za-z0-9]{1,8}\]\s*")


def _md_item(line):
    text = line.lstrip()[2:].strip()
    if text.startswith("[ ]") or text.lower().startswith("[x]"):
        text = text[3:].strip()
    title, _, locks = text.partition(" | locks:")
    title = title.strip()
    ticket = ""
    number = _MD_TASK_NO.match(title)
    if number:
        ticket = number[1].rstrip(".")
        title = title[number.end() :]
    while _MD_MARKER.match(title):
        title = _MD_MARKER.sub("", title, count=1)
    item = {"title": title.strip(), "locks": locks.strip()}
    if ticket:
        item["ticket"] = ticket
    return item


@options("plan")
def _plan_opts(p):
    p.add_argument("action", choices=["import"])
    p.add_argument("file")
    p.add_argument("--team", default=None)
    p.add_argument("--var", action="append", default=[], metavar="NAME=VALUE", help="variable for a recipe import")


@command("claimable", "cards you can claim, and why others are blocked")
def cmd_claimable(ctx, a):
    m = ctx.board.claimable(agent=a.agent or ctx.caller().name or None, for_review=a.for_review)
    if not m:
        return {"cards": {}}, "Nothing waiting for review." if a.for_review else "Nothing open to claim."
    lines = []
    for cid, v in sorted(m.items(), key=lambda kv: (not kv[1]["ok"], kv[0])):
        if a.for_review:
            lines.append(f"review   {cid}: {v['title']} (by {v['owner'] or '-'}, {len(v['approvals'])} approval(s))")
        elif v["ok"]:
            lines.append(
                f"ready    {cid}: {v['title']}" + (f" (take over from {v['takeover']})" if v["takeover"] else "")
            )
        else:
            lines.append(f"not now  {cid}: {v['title']} | {'; '.join(v['reasons'])}")
    return {"cards": m}, "\n".join(lines)


@options("claimable")
def _claimable_opts(p):
    p.add_argument("--for-review", action="store_true")


@command("claim", "take a card: lock its paths and start work", mutating=True)
def cmd_claim(ctx, a):
    r = ctx.board.claim(
        a.id,
        ctx.caller(),
        take_over=a.take_over,
        ttl_minutes=a.ttl,
        for_agent=a.for_agent,
        checkout=a.checkout,
    )
    paths = ", ".join(r["paths"])
    held = f"lock {paths} until {hhmm(r['expires'])}" if paths else f"no locks, lease until {hhmm(r['expires'])}"
    advice = "".join(f"\n# {warning}" for warning in r.get("warnings", []))
    cap = f"\nexport RAIDLOCK_CAP={r['cap']}" if r.get("cap") else ""
    return r, (
        f"Claimed {a.id}: {held}, fence {r['fence']}.{cap}\n"
        f'export RAIDLOCK_FENCE={r["fence"]}  # next: raidlock note {a.id} "..."{advice}'
    )


@options("claim")
def _claim_opts(p):
    p.add_argument("id")
    p.add_argument("--take-over", action="store_true", help="take a card whose owner's lease expired")
    p.add_argument("--ttl", type=float, default=None, help="lease minutes (default: board ttl_minutes)")
    p.add_argument("--for", dest="for_agent", default=None, help="lead claims on behalf of an agent (runners)")
    p.add_argument(
        "--checkout", default=None, help="checkout recorded on the lease; omitted uses board repository resolution"
    )


@command("heartbeat", "renew your lease on a card", mutating=True)
def cmd_heartbeat(ctx, a):
    r = ctx.board.heartbeat(a.id, ctx.caller(), ttl_minutes=a.ttl)
    extra = " (resumed after it had expired)" if r["resumed"] else ""
    return r, f"Renewed {a.id} until {hhmm(r['expires'])}{extra}."


@options("heartbeat")
def _hb_opts(p):
    p.add_argument("id")
    p.add_argument("--ttl", type=float, default=None)


@command("note", "add a progress note (renews your lease)", mutating=True)
def cmd_note(ctx, a):
    return ctx.board.note(a.id, ctx.caller(), a.text), f"Noted on {a.id}."


@options("note")
def _note_opts(p):
    p.add_argument("id")
    p.add_argument("text")


@command("lock", "add paths to a card's locks", mutating=True)
def cmd_lock(ctx, a):
    r = ctx.board.lock_paths(a.id, ctx.caller(), a.replace or a.paths, replace=a.replace is not None)
    return r, f"{a.id} now locks {', '.join(r['locks'])}."


@options("lock")
def _lock_opts(p):
    p.add_argument("id")
    p.add_argument("paths", nargs="?", default="")
    p.add_argument("--replace", default=None, help="lead: replace all locks with these paths")


@command("unlock", "lead: remove exact lock paths from a card", mutating=True)
def cmd_unlock(ctx, a):
    r = ctx.board.lock_paths(a.id, ctx.caller(), a.paths, remove=True)
    return r, f"{a.id} now locks {', '.join(r['locks'])}."


@options("unlock")
def _unlock_opts(p):
    p.add_argument("id")
    p.add_argument("paths", help="exact lock paths to remove (lead only)")


@command("release", "give a card back: drop its lease (doing goes back to todo)", mutating=True)
def cmd_release(ctx, a):
    r = ctx.board.release(a.id, ctx.caller(), note=a.note)
    return r, f"Released {a.id}; it is in {r['lane']}."


@options("release")
def _release_opts(p):
    p.add_argument("id")
    p.add_argument("--note", default=None)


def _moved(card_id, lane):
    nxt = f" Next: the lead runs raidlock verify {card_id}, then done" if lane == "review" else ""
    return f"Moved {card_id} to {lane}.{nxt}"


@command("move", "move a card to review, blocked or todo (done: the lead uses raid done)", mutating=True)
def cmd_move(ctx, a):
    r = ctx.board.move(a.id, a.lane, ctx.caller(), note=a.note)
    return r, _moved(a.id, a.lane)


@options("move")
def _move_opts(p):
    p.add_argument("id")
    p.add_argument("lane", choices=list(LANES))
    p.add_argument("--note", default=None)


def _prune_runs(board, card_id):
    from .runners.runner import prune_runs

    prune_runs(board, card_id)


@command("close", "lead: verify, record a reviewer approval, and close a card", mutating=True)
def cmd_close(ctx, a):
    from .core.identity import read_token

    board, caller = ctx.board, ctx.caller()
    if getattr(a, "evidence", None) is not None:
        result = board.close_with_evidence(a.id, caller, a.evidence, a.note)
        _prune_runs(board, a.id)
        return result, f"Closed {a.id} with evidence {a.evidence}."
    with board.lead_write(caller, "close a card"):
        card = board.card(a.id)
        agents = board.agents()
        reviewer = agents.get(a.reviewer, {})
        owner = agents.get(card.get("owner"), {})
        if not reviewer or not reviewer.get("provider") or not owner.get("provider"):
            raise Precondition("close requires registered owner and reviewer providers")
        if a.reviewer == card.get("owner") or reviewer["provider"] == owner["provider"]:
            raise Precondition("close requires a reviewer from another provider")
        token = read_token(board.home, board.uid, a.reviewer)
        if not token:
            raise Precondition("reviewer credential is missing; re-join the reviewer first")
        review_caller = board.caller(agent=a.reviewer, token=token, env={})
        board._auth(review_caller)
        if card["lane"] not in ("doing", "blocked", "review"):
            raise Precondition("close requires a claimed card")
    _verify_checkout(board, card, a.cwd)
    verified = board.verify(a.id, caller, timeout=a.timeout, cwd=a.cwd)
    if not verified["passed"]:
        return {**verified, "_exit": 2}, f"Verification failed for {a.id}: {verified['tail']}"
    if board.card(a.id)["lane"] != "review":
        board.move(a.id, "review", caller)
    board.assign_reviewers(a.id, caller, list(dict.fromkeys(card.get("reviewers", []) + [a.reviewer])))
    board.review(a.id, review_caller, "approve", note=a.note)
    result = board.move(a.id, "done", caller)
    _prune_runs(board, a.id)
    return result, f"Closed {a.id}; verified and approved by {a.reviewer}."


@options("close")
def _close_opts(p):
    p.add_argument("id", help="card to verify and close")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--reviewer", help="reviewer whose existing credential records approval")
    mode.add_argument("--evidence", help="https URL or commit SHA proving work delivered outside the board")
    p.add_argument("--cwd", "--checkout", default=None, help="clean checkout of the card branch")
    p.add_argument("--timeout", type=float, default=600, help="verification timeout in seconds")
    p.add_argument("--note", required=True, help="review evidence supporting the approval")


@command("cancel", "lead: cancel a card without verification or review gates", mutating=True)
def cmd_cancel(ctx, a):
    result = ctx.board.cancel(a.id, ctx.caller(), a.reason)
    _prune_runs(ctx.board, a.id)
    return result, f"Cancelled {a.id}: {result['reason']}"


@options("cancel")
def _cancel_opts(p):
    p.add_argument("id", help="card to cancel")
    p.add_argument("--reason", required=True, help="why the work will not be done")


@command("done", "lead: close a card after its verify and review gates pass", mutating=True)
def cmd_done(ctx, a):
    before = ctx.board.card(a.id)
    r = ctx.board.move(a.id, "done", ctx.caller(), note=a.note)
    _prune_runs(ctx.board, a.id)
    warnings = []
    if before["lane"] == "todo":
        warnings.append(f"{a.id} was closed from todo: no worker claimed it or handed work back")
    if not model.last_verification(before) and not before.get("verify_cmd"):
        warnings.append(f"{a.id} has no verify command, so no receipt was checked")
    text = f"Done: {a.id}. Next: raidlock show" + "".join(f"\nWarning: {w}" for w in warnings)
    return {**r, **({"warnings": warnings} if warnings else {})}, text


@options("done")
def _done_opts(p):
    p.add_argument("id")
    p.add_argument("--note", default=None)


@command("reopen", "lead: move a done card back to todo", mutating=True)
def cmd_reopen(ctx, a):
    return ctx.board.reopen(a.id, ctx.caller(), note=a.note), f"Reopened {a.id}; it is in todo."


@options("reopen")
def _reopen_opts(p):
    p.add_argument("id")
    p.add_argument("--note", default=None)


@command("review", "review a card (--verdict) or assign reviewers (--assign)", mutating=True)
def cmd_review(ctx, a):
    if a.assign is not None:
        r = ctx.board.assign_reviewers(a.id, ctx.caller(), a.assign)
        return r, f"Reviewers of {a.id}: {', '.join(r['reviewers']) or 'none'}." + "".join(
            f"\nWarning: {w}" for w in r["warnings"]
        )
    if not a.verdict:
        raise UsageError("review needs --verdict or --assign", hint=f"raidlock review {a.id} --verdict approve")
    r = ctx.board.review(a.id, ctx.caller(), a.verdict, a.note or "")
    if r["lane"] == "doing":
        return r, f"Asked for changes on {a.id}; it is back in doing for its owner."
    return r, f"Recorded {a.verdict} on {a.id} ({len(r['approvals'])} approval(s))."


@options("review")
def _review_opts(p):
    p.add_argument("id")
    p.add_argument("--by", dest="agent", default=None, help="reviewing agent (same as --agent)")
    p.add_argument("--verdict", choices=["approve", "changes", "comment"], default=None)
    p.add_argument("--note", default=None)
    p.add_argument("--assign", default=None, help="comma-separated reviewer agents")


@command("verify", "lead: run the card's verify command and record the result", mutating=True)
def cmd_verify(ctx, a):
    board = ctx.board
    caller = ctx.caller()
    if caller.is_lead:
        _verify_checkout(board, board.card(a.id), a.cwd)
    r = board.verify(a.id, caller, timeout=a.timeout, cwd=a.cwd)
    secs = r["duration_ms"] / 1000
    if r["passed"]:
        return r, f"Verify passed for {a.id} ({secs:.1f} s). Next: " + lead_cmd(board, f"done {a.id}")
    return {**r, "_exit": 2}, (
        f"Verify failed for {a.id} (exit {r['exit']}, {secs:.1f} s).\n{r['tail']}".rstrip()
        + "\nNext: fix the work, commit, then "
        + lead_cmd(board, f"verify {a.id}")
    )


def _verify_checkout(board, card, cwd):
    from .core import repos

    if not card.get("verify_cmd"):
        return
    source = getattr(board, "_verification_source", None)
    if cwd:
        where = os.path.realpath(cwd)
    elif source is not None:
        where = os.path.realpath(source(card))
    else:
        return
    if repos.find_root(where):
        from .runners import workspace

        try:
            workspace.head(where)
            return
        except RaidlockError:
            pass
    guess = "<repo checkout>"
    if card.get("locks"):
        try:
            from .runners import policy

            guess = quote_path(policy.target(board.workspace, board.dir, card["locks"])["path"])
        except RaidlockError:
            pass
    raise Precondition(
        f"{short(where)} is not a git repository; verify runs a committed checkout of the card's repo",
        card=card["id"],
        hint=lead_cmd(board, f"verify {card['id']} --cwd {guess}"),
    )


@options("verify")
def _verify_opts(p):
    p.add_argument("id")
    p.add_argument("--timeout", type=float, default=600)
    p.add_argument("--cwd", "--checkout", default=None, help="clean checkout of the card branch to verify")


@command("land", "lead: apply a delegated patch, commit, verify and hand the card to review", mutating=True)
def cmd_land(ctx, a):
    from .runners.land import land

    r = land(ctx.board, ctx.caller(), a.id, cwd=a.cwd, message=a.message, timeout=a.timeout)
    verify = r["verify"]
    outcome = (
        f"verify {'passed' if verify['passed'] else 'failed'} (exit {verify['exit']})"
        if verify
        else "no verify command on the card"
    )
    tail = f"\n{verify['tail']}".rstrip() if verify and not verify["passed"] else ""
    return r, f"Landed {a.id} as {r['commit'][:12]} ({len(r['paths'])} file(s)); {outcome}.{tail}\nNext: {r['next']}"


@options("land")
def _land_opts(p):
    p.add_argument("id")
    p.add_argument("--cwd", "--checkout", default=None)
    p.add_argument("--message", "-m", default=None)
    p.add_argument("--timeout", type=float, default=600)


@command("msg", "send a message to an agent", mutating=True)
def cmd_msg(ctx, a):
    m = ctx.board.send(ctx.caller(), a.to, a.text, task=a.task, urgent=a.urgent)
    if m["dropped"]:
        return m, f"Not sent: the same message went to {a.to} less than a minute ago."
    if m["status"] == "queued":
        return m, f"Queued for {a.to}; delivered when it finishes its current card."
    return m, f"Sent to {a.to}."


@options("msg")
def _msg_opts(p):
    p.add_argument("text")
    p.add_argument("--to", required=True)
    p.add_argument("--from", dest="agent", default=None, help="sender (same as --agent)")
    p.add_argument("--task", "--card", dest="task", default=None)
    p.add_argument("--urgent", action="store_true", help="lead only: deliver now")


@command("inbox", "read your messages", mutating=True)
def cmd_inbox(ctx, a):
    r = ctx.board.inbox(ctx.caller(), show_all=a.all, wait=a.wait or 0.0, interval=a.interval)
    lines = [
        f"{m['sent']} {m['from']}{(' [' + m['task'] + ']') if m.get('task') else ''}"
        f"{(' (' + m['status'] + ')') if a.all else ''}: {m['text']}"
        for m in r["messages"]
    ]
    if r["queued"] and not a.all:
        lines.append(f"{r['queued']} more queued until you finish {r['busy']}.")
    return r, "\n".join(lines) or "No messages."


@options("inbox")
def _inbox_opts(p):
    p.add_argument("--all", action="store_true")
    p.add_argument(
        "--wait",
        type=float,
        nargs="?",
        const=1800.0,
        default=None,
        help="block until a message arrives (seconds, default 1800); exit 4 on timeout",
    )
    p.add_argument("--interval", type=float, default=2.0)


@command("wait", "wait until a card leaves doing (exit 4 on timeout)")
def cmd_wait(ctx, a):
    r = ctx.board.wait(a.id, timeout=a.timeout, interval=a.interval)
    return r, f"{a.id} is now in {r['lane']}."


@options("wait")
def _wait_opts(p):
    p.add_argument("id")
    p.add_argument("--timeout", type=float, default=1800)
    p.add_argument("--interval", type=float, default=5.0)


@command("cost", "record a run's usage on a card, or show totals without an id", mutating=True)
def cmd_cost(ctx, a):
    b = ctx.board
    if not a.id:
        t = b.spend(by="card")
        return t, _spend_text(t)
    usage = Usage(
        input=a.input,
        cached_input=a.cached_input,
        cache_write=a.cache_write,
        output=a.output,
        reasoning_output=a.reasoning_output,
    )
    r = b.record_cost(
        a.id,
        ctx.caller(),
        model_id=a.model,
        usage=usage,
        usd=a.usd,
        duration_ms=a.duration_ms,
        billing=a.billing,
        agent=a.for_agent,
    )
    e = r["entry"]
    return r, f"Recorded {e['tokens']:,} tokens ({_usd(e)}) on {a.id}."


def _usd(row):
    if row.get("billing") == "plan":
        return "plan" + (f", API-equivalent ${row['usd_equiv']:.4f}" if row.get("usd_equiv") else "")
    return f"${row['usd']:.4f}" if row.get("usd") is not None else "USD —"


def _spend_text(t):
    tot = t["total"]
    head = (
        f"Spend: ${tot['usd']:.4f} (API-equivalent ${tot['usd_equiv']:.4f}), {tot['tokens'] or 0:,} tokens, "
        f"{tot['runs']} run(s)."
    )
    rows = [f"  {r['key'] or '-'}: ${r['usd']:.4f}, {r['tokens']:,} tokens" for r in t["rows"]]
    return "\n".join([head] + rows)


@options("cost")
def _cost_opts(p):
    p.add_argument("id", nargs="?")
    p.add_argument("--model", default=None)
    p.add_argument("--input", type=int, default=0, help="uncached input tokens")
    p.add_argument("--cached-input", "--cached", dest="cached_input", type=int, default=0, help="cache-read tokens")
    p.add_argument("--cache-write", type=int, default=0)
    p.add_argument("--output", type=int, default=0, help="output tokens, reasoning included")
    p.add_argument("--reasoning-output", type=int, default=0, help="reasoning part of output (informational)")
    p.add_argument("--usd", type=float, default=None, help="runner-reported cost; wins over pricing")
    p.add_argument("--billing", choices=["api", "plan", "unknown"], default=None)
    p.add_argument("--for", dest="for_agent", default=None, help="lead records a run for another agent")
    p.add_argument("--duration-ms", type=int, default=0)


@command("spend", "spend by provider, model, agent, team, card, workspace, day or week")
def cmd_spend(ctx, a):
    t = ctx.board.spend(by=a.by, since=a.since, until=a.until, everywhere=a.all)
    return t, _spend_text(t)


@options("spend")
def _spend_opts(p):
    p.add_argument(
        "--by",
        default="provider",
        choices=["provider", "model", "agent", "team", "card", "billing", "workspace", "day", "week"],
    )
    p.add_argument("--since", default=None, help="UTC date or timestamp, e.g. 2026-10-01")
    p.add_argument("--until", default=None)
    p.add_argument("--all", action="store_true", help="every registered workspace")


@command("who", "who is on the board and what they are doing")
def cmd_who(ctx, a):
    b = ctx.board
    who = b.who()
    icons = {
        "working": glyph("\U0001f525", "*"),
        "quiet": glyph("\U0001f525", "*"),
        "review": glyph("\U0001f989", "?"),
        "blocked": glyph("\U0001f578", "!"),
        "stalled": glyph("⌛", "~"),
        "idle": glyph("\U0001f37a", "-"),
    }
    lines = []
    for name, w in sorted(who.items()):
        card = f" on {w['card']}" if w["card"] else ""
        spent = f", spent ${w['spend_usd']:.2f}" if w["spend_usd"] else ""
        session = f", session {w['session']}" if w.get("session") else ""
        lines.append(f"{icons[w['status']]} {w.get('display') or name}: {w['label']}{card}{spent}{session}")
    leases = b.leases()
    lease = b.lead_status()
    if lease["held"]:
        state = "live" if lease["live"] else "expired"
        lines.append(
            f"lead session: {lease['session'] or '-'} (fence {lease['fence']}, {state} until "
            f"{hhmm(lease['expires'])}, secret in {lease['store']})"
        )
    if a.brief:
        lines = [
            "; ".join(f"{n} {w['status']}" + (f" {w['card']}" if w["card"] else "") for n, w in sorted(who.items()))
        ]
    return {"agents": who, "leases": leases, "lead_lease": lease}, "\n".join(
        lines
    ) or "No agents yet. Next: raidlock join"


@options("who")
def _who_opts(p):
    p.add_argument("--brief", action="store_true", help="one line")


@command("lead", "inspect or transfer the lead session lease", mutating=True)
def cmd_lead(ctx, a):
    b = ctx.board
    if a.action == "status":
        r = b.lead_status()
        if not r["held"]:
            return r, f"No lead lease on {b.name}. Next: raidlock lead takeover --lead-secret <file>"
        state = "live" if r["live"] else "EXPIRED"
        return r, (
            f"Lead {r['lead']}: session {r['session'] or '-'}, fence {r['fence']}, {state} until "
            f"{hhmm(r['expires'])}, secret in {r['store']}" + (" (handover pending)" if r.get("pending") else "")
        )
    if a.action == "heartbeat":
        r = b.lead_heartbeat(ctx.caller())
        return r, f"Lead lease renewed until {hhmm(r['expires'])} (fence {r['fence']})."
    if a.action == "handover":
        if not a.to:
            raise UsageError("lead handover needs --to <session>", hint="raidlock lead handover --to <session id>")
        r = b.lead_handover(ctx.caller(), a.to)
        return r, (
            f"Lead handed over to session {r['to']} at fence {r['fence']}; the old secret is retired.\n"
            "Next (in that session): raidlock --lead-secret "
            f"{short(r['secret_file']) if r['store'] == 'file' else 'keychain'} lead status"
        )
    r = b.lead_takeover(lead_secret=ctx.lead_secret())
    prev = r["previous"]
    print(
        f"WARNING: lead takeover: session {r['session']} replaces session {prev['session'] or '(none)'} "
        f"(lease expired {prev['expires'] or '-'}); logged in the transcript.",
        file=sys.stderr,
    )
    return r, f"You hold the lead lease now (fence {r['fence']}). The previous lead secret is retired."


@options("lead")
def _lead_opts(p):
    p.add_argument("action", choices=["status", "heartbeat", "handover", "takeover"])
    p.add_argument("--to", default=None, help="session id that receives the lead (handover)")


@command("reap", "move cards whose doing lease expired to blocked", mutating=True)
def cmd_reap(ctx, a):
    r = ctx.board.reap()
    return r, f"Reaped {len(r['reaped'])} card(s): {', '.join(r['reaped'])}." if r["reaped"] else "Nothing to reap."


@command("show", "the board, or one card with its notes")
def cmd_show(ctx, a):
    b = ctx.board
    if a.id:
        c = b.card_view(b.card(a.id))
        lines = [
            f"{c['id']}: {c['title']}",
            f"{c['lane']} ({c['a2a_state']}), owner {c['owner'] or '-'}, team {c['team'] or '-'}, fence {c['fence']}",
            f"locks: {', '.join(c['locks']) or '-'}",
        ]
        lines += [
            f"{k}: {c[k]}"
            for k in ("repo", "scope", "output", "verify", "verify_cmd", "ticket", "evidence")
            if c.get(k)
        ]
        if c.get("lock_exclusions"):
            lines.append("excluded lock paths: " + ", ".join(c["lock_exclusions"]))
        if c.get("allow_protected"):
            lines.append("lead-approved protected paths: " + ", ".join(c["allow_protected"]))
        if c.get("pr"):
            lines.append("pr: " + _pr_badge(c) + (f" {c['pr']['url']}" if c["pr"].get("url") else ""))
        lines += [f"- {n['ts']} {n['by'] or '?'}: {n['text']}" for n in c["notes"]]
        return {"card": c}, "\n".join(lines)
    cards, leases = b.cards(), b.leases()
    if a.brief:
        lanes = [(lane, [c["id"] for c in cards if c["lane"] == lane]) for lane in LANES]
        text = " | ".join(
            f"{lane} {len(ids)}"
            + (f": {', '.join(ids[:8])}" + (" ..." if len(ids) > 8 else "") if ids and lane != "done" else "")
            for lane, ids in lanes
        )
        return {"cards": cards, "version": b.version()}, text
    lock = glyph("\U0001f512", "lock")
    out = []
    for lane in LANES:
        rows = [c for c in cards if c["lane"] == lane]
        out.append(f"{lane} ({len(rows)})")
        if lane == "done" and not a.all:
            continue
        for c in rows:
            held = f"{lock} {hhmm(leases[c['id']]['expires'])}" if c["id"] in leases else ""
            extra = " ".join(
                x
                for x in (
                    f"[{c['team']}]" if c["team"] else "",
                    f"@{c['owner']}" if c["owner"] else "",
                    held,
                    f"({_pr_badge(c)})" if c.get("pr") else "",
                )
                if x
            )
            out.append(f"  {c['id']}: {c['title']} {extra}".rstrip())
    agents = b.agents()
    return {"cards": [b.card_view(c, leases, agents) for c in cards], "version": b.version()}, "\n".join(out)


def _pr_badge(card):
    pr = card["pr"]
    state = str(pr.get("state") or "linked").lower()
    checks = (pr.get("checks") or {}).get("state", "none")
    return f"PR #{pr.get('number')} {state}" + (f" checks {checks}" if checks != "none" else "")


@options("show")
def _show_opts(p):
    p.add_argument("id", nargs="?")
    p.add_argument("--all", action="store_true", help="list done cards too")
    p.add_argument("--brief", action="store_true", help="one line per lane")


@command("workspaces", "registered workspaces on this machine", aliases=("projects",))
def cmd_workspaces(ctx, a):
    index = Index(registry.home())
    if a.rm:
        gone = registry.remove(index, a.rm)
        return {"removed": gone}, f"Removed {', '.join(gone) or 'nothing'}."
    rows = [_workspace_row(index, w) for w in registry.workspaces(index)]
    lines = [
        f"{r['name']}: {short(os.path.dirname(r['board']))}"
        + (
            " (missing)"
            if r["missing"]
            else ", "
            + ", ".join(f"{n} {k}" for k, n in r["lanes"].items() if n)
            + f", {r['live_leases']} live lease(s)"
        )
        for r in rows
    ]
    return {"workspaces": rows}, "\n".join(lines) or "No workspaces registered.\nNext: raidlock up"


def _workspace_row(index, w):
    lanes = {lane: 0 for lane in LANES}
    for c in index.card_rows(w["uid"]):
        lanes[c["lane"]] = lanes.get(c["lane"], 0) + 1
    live = sum(1 for lease in index.leases(w["uid"]).values() if is_live(lease))
    return {
        **w,
        "missing": not os.path.exists(os.path.join(w["board"], "board.json")),
        "lanes": lanes,
        "live_leases": live,
    }


@options("workspaces")
def _ws_opts(p):
    p.add_argument("--rm", nargs="+", default=None, metavar="NAME")


@command("check", "check paths for lock conflicts, or a file write (--write FILE)")
def cmd_check(ctx, a):
    b = ctx.board
    if a.write:
        d = b.check_write(a.write, ctx.caller())
        return {**d, "_exit": 0 if d["allowed"] else 2}, ("Allowed: " if d["allowed"] else "Denied: ") + d[
            "reason"
        ] + "."
    if not a.paths:
        raise UsageError("check needs paths or --write FILE", hint="raidlock check app/src")
    conflicts = b.check_paths(a.paths, card_id=a.id)
    if not conflicts:
        return {"free": True, "conflicts": []}, "Free."
    return {"free": False, "conflicts": conflicts, "_exit": 2}, "\n".join(describe(c) for c in conflicts)


@options("check")
def _check_opts(p):
    p.add_argument("paths", nargs="?")
    p.add_argument("--id", default=None, help="ignore this card's own lease")
    p.add_argument("--write", default=None, help="file to check for write permission")


@command("transcript", "the conversation log: newest records, filterable")
def cmd_transcript(ctx, a):
    rows = ctx.board.transcript.read(task=a.task, kind=a.kind, agent=a.who, limit=a.tail)
    lines = []
    for r in rows:
        who = (r.get("from") or {}).get("agent", "?")
        text = " ".join(p.get("text", "") for p in r.get("parts", []) if p.get("kind") == "text")
        if r.get("src") == "v1-log" and not text.startswith("[untrusted v1 import]"):
            text = "[untrusted v1 import] " + text
        lines.append(
            f"{r['ts']} {r['kind']} {who}{(' -> ' + r['to']) if r.get('to') else ''}"
            f"{(' [' + r['taskId'] + ']') if r.get('taskId') else ''}: {text}"
        )
    return {"records": rows}, "\n".join(lines) or "No transcript yet."


@options("transcript")
def _transcript_opts(p):
    p.add_argument("--task", default=None)
    p.add_argument("--kind", default=None)
    p.add_argument("--who", default=None, help="records from or to this agent")
    p.add_argument("-n", "--tail", type=int, default=50, help="newest N records (0 = all)")


@command("brief", "the card brief for an agent prompt, with its token estimate")
def cmd_brief(ctx, a):
    r = ctx.board.brief(a.id, max_tokens=a.max_tokens)
    note = (
        f"~{r['tokens']} tokens"
        + (f", {r['dropped']} line(s) cut" if r["dropped"] else "")
        + (", over the cap" if r["over_cap"] else "")
    )
    return r, r["text"].rstrip("\n") + ("" if a.quiet else f"\n({note})")


@options("brief")
def _brief_opts(p):
    p.add_argument("id")
    p.add_argument("--max-tokens", type=int, default=None, help="default: board setting brief_max_tokens")
    p.add_argument("--quiet", action="store_true", help="omit the token note (pipe straight into a prompt)")


@command("tokens", "estimated per-session token overhead of raidlock")
def cmd_tokens(ctx, a):
    from .core import tokens as TK

    try:
        board = ctx.board
    except Precondition:
        board = None
    r = TK.report(board)
    lines = [f"{i['name']}: ~{i['tokens']} ({i['detail']})" for i in r["items"]]
    return r, "\n".join(lines + [f"total: ~{r['total']} tokens per session ({r['method']})"])


@command("version", "print the version")
def cmd_version(ctx, a):
    return {"version": __version__}, f"raidlock {__version__}"


def own_script(name="raid"):
    """Path of this install's console script `name`, or None when it is not installed."""
    exe = name + (".exe" if os.name == "nt" else "")
    candidates = [os.path.join(sysconfig.get_path("scripts"), exe), os.path.join(os.path.dirname(sys.executable), exe)]
    if prog_name() == name and sys.argv and os.path.isabs(sys.argv[0]):
        candidates.insert(0, sys.argv[0])
    return next((c for c in candidates if os.path.isfile(c)), None)


def _shadow(which, ours):
    if not which:
        return ""
    if ours:
        return "" if os.path.realpath(which) == os.path.realpath(ours) else which
    try:
        with open(which, "rb") as f:
            head = f.read(4096)
    except OSError:
        return which
    return "" if b"raidlock" in head else which


def _home_writable(home):
    parent = home
    while not os.path.exists(parent):
        parent = os.path.dirname(parent)
    if not os.path.isdir(parent):
        return "not a directory"
    return "" if os.access(parent, os.W_OK) else "permission denied"


@command("migrate", "explicitly upgrade a legacy board or restore its backup", mutating=True)
def cmd_migrate(ctx, a):
    from .core import migrate
    from .core.board import find_board_dir

    board_dir = find_board_dir(a.board)
    if a.rollback:
        choices = migrate.backups(board_dir)
        if not choices:
            raise Precondition("no recorded migration backup found")
        plan = f"Replace {board_dir} with backup {choices[0]}; changes since migration will be lost."
    else:
        migrate.preflight(board_dir, a.allow_auth_downgrade)
        if not migrate.needs_migration(board_dir):
            raise Precondition("no legacy v1 board found")
        plan = (
            f"Upgrade {board_dir} to schema 2: convert cards and inbox, rewrite agents and teams, "
            "import leases as inactive history (holders must claim again), create board.json and v1/. "
            "Save the complete original tree in a timestamped "
            "sibling .board.backup-* directory first (ignored in git workspaces)."
        )
    print(plan, file=sys.stderr)
    if not a.yes:
        if not _interactive():
            raise Precondition("migration requires --yes or interactive confirmation", hint="raid migrate --yes")
        if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
            raise Precondition("migration cancelled; nothing changed")
    result = (
        migrate.rollback(board_dir, registry.home(), caller=ctx.caller(), board=ctx.board)
        if a.rollback
        else migrate.upgrade(board_dir, registry.home(), allow_auth_downgrade=a.allow_auth_downgrade, lead=a.lead)
    )
    message = f"{'Restored' if a.rollback else 'Migrated'} {board_dir}. Backup: {result['backup']}"
    if not a.rollback:
        message += f"\nLead: {result['lead']}\nSecret: {result['secret_file']}\nTakeover: {result['takeover']}"
        if result.get("dropped_untrusted_lore"):
            message += "\nDropped pre-existing untrusted lore; the original remains in the migration backup."
        for key, value in result["unmapped_settings"].items():
            message += f"\nNo v2 equivalent: {key} = {value!r}"
    return result, message


@options("migrate")
def _migrate_opts(p):
    p.add_argument("--lead", help="confirmed v1 lead when several lead-role agents exist")
    p.add_argument("--allow-auth-downgrade", action="store_true", help="explicitly accept imported auth=off")
    p.add_argument("--yes", action="store_true", help="confirm the printed changes")
    p.add_argument("--rollback", action="store_true", help="restore the recorded migration backup")


def _tree_bytes(root):
    total = 0
    for base, _, files in os.walk(root):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(base, name)).st_size
    return total


@command("doctor", "check this install: Python, board, RAIDLOCK_HOME, database, PATH")
def cmd_doctor(ctx, a):
    checks, problems, warnings = [], [], []

    def check(name, ok, detail):
        checks.append({"check": name, "ok": ok, "detail": detail})
        if not ok:
            problems.append(f"{name}: {detail}")

    py = ".".join(map(str, sys.version_info[:3]))
    check("python", sys.version_info >= (3, 11), py if sys.version_info >= (3, 11) else f"{py}; raidlock needs 3.11+")
    try:
        from .core import migrate, store
        from .core.board import find_board_dir

        board_dir = find_board_dir(a.board)
        if migrate.needs_migration(board_dir):
            warnings.append("legacy v1 board found (not migrated); run raid migrate")
            check("board", True, "WARNING: " + warnings[-1])
        else:
            meta = store.read_json(os.path.join(board_dir, "board.json"))
            if meta is None:
                raise Precondition("not a raidlock board")
            store.check_schema(meta, os.path.join(board_dir, "board.json"))
            check("board", True, short(board_dir))
    except RaidlockError as e:
        explicit = a.board or os.environ.get("RAIDLOCK_BOARD")
        check("board", not explicit, f"none here ({e.message})" if not explicit else e.message)
    home = registry.home()
    err = _home_writable(home)
    check("home", not err, short(home) + (f" not writable: {err}" if err else " writable"))
    try:
        import sqlite3
        from pathlib import Path

        path = Path(home) / "raidlock.db"
        if path.exists():
            import tempfile

            with tempfile.TemporaryDirectory(prefix="raid-doctor-") as scratch:
                copy = Path(scratch) / path.name
                shutil.copyfile(path, copy)
                wal = Path(str(path) + "-wal")
                if wal.exists():
                    shutil.copyfile(wal, str(copy) + "-wal")
                conn = sqlite3.connect(str(copy))
                try:
                    n = conn.execute("SELECT count(*) FROM workspaces").fetchone()[0]
                    state = conn.execute("PRAGMA quick_check").fetchone()[0]
                finally:
                    conn.close()
            check("database", state == "ok", f"{state}, {n} workspace(s) registered")
        else:
            check("database", True, "not created yet")
    except Exception as e:
        check("database", False, f"cannot open the index in {short(home)}: {e}")
    used = sum(_tree_bytes(os.path.join(home, name)) for name in ("runs", "verify"))
    check("storage", True, f"{used / 1e6:.0f} MB in run clones and the verify cache; closed cards free theirs")
    from . import updates

    latest = updates.refresh(persist=False)
    newer = updates.notice(latest)
    check(
        "update",
        True,
        newer
        or (
            f"raidlock {__version__} is the latest release" if latest else "update check skipped (offline or disabled)"
        ),
    )
    which = shutil.which("raid")
    shadow = _shadow(which, own_script())
    check(
        "path",
        not shadow,
        f"another raid at {shadow} shadows this install; use raidlock instead or fix PATH"
        if shadow
        else (f"raid -> {short(which)}" if which else "raid not on PATH; raidlock works"),
    )
    lines = [f"{'ok  ' if c['ok'] else 'FAIL'} {c['check']}: {c['detail']}" for c in checks]
    lines.append("Healthy." if not problems else f"{len(problems)} problem(s).")
    return {
        "healthy": not problems,
        "problems": problems,
        "warnings": warnings,
        "checks": checks,
        "_exit": 3 if problems else 0,
    }, "\n".join(lines)
