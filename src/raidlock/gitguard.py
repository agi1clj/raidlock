import os
import re
import shlex


class Ambiguous(Exception):
    pass


class Forbidden(Exception):
    pass


READ = "read"
REF = "ref"
PATHS = "paths"
WHOLE = "whole"
SUBST = "__raidlock_subst__"
MAX_DEPTH = 4

SEPARATORS = (";", "&&", "||", "|", "&", "|&", ";;", ";&", ";;&")
REDIRECTS = (">", ">>", "&>", "&>>", ">|", ">&", "<", "<<", "<<<", "<&", "<>")
GROUP_OPEN = ("(", "{")
GROUP_CLOSE = (")", "}")
KEYWORDS = ("if", "then", "else", "elif", "fi", "do", "done", "while", "until", "!", "time", "{", "}")
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Not after "-": "--skip-git-repo-check" is an option, not git; "git" and "git-lfs" still match.
GIT_WORD = re.compile(r"(?<![\w.-])git(?![\w.])")
HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
EXEC_CONSUMER = re.compile(
    r"(?<![\w.-])(sh|bash|zsh|dash|ksh|mksh|fish|eval|source|xargs|python[\d.]*|perl|ruby|node|"
    r"php|osascript|awk|gawk|lua|deno|bun|pwsh)(?![\w.-])"
)
INTERPRETER = re.compile(
    r"^(python[\d.]*|perl[\d.]*|ruby|node|nodejs|php|osascript|awk|gawk|mawk|nawk|lua[\d.]*|"
    r"tclsh|deno|bun|pwsh|powershell|Rscript|expect)$"
)
SHELLS = ("sh", "bash", "zsh", "dash", "ksh", "mksh", "fish", "busybox")
LAUNCHERS = (
    "xargs",
    "sudo",
    "doas",
    "timeout",
    "gtimeout",
    "nice",
    "ionice",
    "stdbuf",
    "setsid",
    "caffeinate",
    "flock",
    "chronic",
    "unbuffer",
    "watch",
    "parallel",
    "find",
    "fd",
    "script",
    "entr",
    "nohup",
    "exec",
    "ssh",
    "su",
    "runuser",
    "chroot",
    "nsenter",
    "unshare",
    "firejail",
    "strace",
    "ltrace",
    "gdb",
    "lldb",
    "make",
    "just",
    "npx",
    "uvx",
    "pipx",
    "bunx",
    "direnv",
    "with",
    "tmux",
    "screen",
)
NON_EXEC = (
    "echo",
    "printf",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "ag",
    "which",
    "type",
    "whereis",
    "man",
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "test",
    "[",
    "raidlock",
    "brew",
    "apt",
    "true",
    "false",
    ":",
    "diff",
    "less",
    "more",
)
SIMPLE_WRAPPERS = ("command", "builtin", "noglob", "nocorrect")

GLOBAL_FLAGS = (
    "-p",
    "--paginate",
    "-P",
    "--no-pager",
    "--literal-pathspecs",
    "--no-optional-locks",
    "--no-replace-objects",
    "--glob-pathspecs",
    "--noglob-pathspecs",
    "--icase-pathspecs",
    "--no-advice",
)
READ_ONLY = (
    "status",
    "diff",
    "log",
    "show",
    "rev-parse",
    "ls-files",
    "blame",
    "cat-file",
    "ls-tree",
    "rev-list",
    "describe",
    "show-ref",
    "merge-base",
    "shortlog",
    "grep",
    "whatchanged",
    "count-objects",
    "var",
    "check-ignore",
    "check-attr",
    "diff-files",
    "diff-index",
    "diff-tree",
    "help",
    "version",
)
REF_ONLY = ("fetch", "push")
WHOLE_TREE = ("merge", "rebase", "pull", "cherry-pick", "revert", "am")
FORBIDDEN_SUBS = (
    "difftool",
    "mergetool",
    "bisect",
    "filter-branch",
    "filter-repo",
    "hook",
    "submodule",
    "daemon",
    "instaweb",
    "credential",
    "send-email",
    "web--browse",
    "upload-pack",
    "receive-pack",
    "remote-ext",
    "remote-fd",
    "shell",
    "archimport",
    "cvsimport",
    "svn",
    "p4",
)
EXEC_OPTIONS = (
    "--upload-pack",
    "--receive-pack",
    "--exec",
    "--extcmd",
    "--open-files-in-pager",
    "--tool",
    "--ext-diff",
    "--textconv",
)
OUTPUT_OPTIONS = ("--output",)
REV = re.compile(r"^(HEAD|@|ORIG_HEAD|FETCH_HEAD|MERGE_HEAD|[0-9a-f]{7,40})([~^][0-9~^]*)?$|[~^]")
GLOB = re.compile(r"[*?\[{]")


def mentions_git(text):
    return bool(GIT_WORD.search(text or ""))


def strip_heredocs(text):
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for m in HEREDOC.finditer(line):
            body = []
            while i < len(lines):
                cand = lines[i].lstrip("\t") if m.group(1) else lines[i]
                i += 1
                if cand == m.group(3):
                    break
                body.append(lines[i - 1])
            if EXEC_CONSUMER.search(line[: m.start()]) and mentions_git("\n".join(body)):
                raise Ambiguous("a heredoc feeds git commands to a shell or interpreter")
    return "\n".join(out)


def _close_paren(text, start):
    depth, quote, i = 1, "", start
    while i < len(text):
        c = text[i]
        if quote == "'":
            quote = "" if c == "'" else quote
        elif c == "\\":
            i += 1
        elif c == "'" and not quote:
            quote = "'"
        elif c == '"':
            quote = "" if quote == '"' else '"'
        elif not quote and c == "(":
            depth += 1
        elif not quote and c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise Ambiguous("unbalanced command substitution")


def extract_substitutions(text):
    out, inner, quote, i = [], [], "", 0
    while i < len(text):
        c = text[i]
        if quote == "'":
            quote = "" if c == "'" else quote
            out.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < len(text):
            out.append(text[i : i + 2])
            i += 2
            continue
        if c == "'" and not quote:
            quote = "'"
        elif c == '"':
            quote = "" if quote == '"' else '"'
        elif c == "`":
            j = i + 1
            while j < len(text) and text[j] != "`":
                j += 2 if text[j] == "\\" else 1
            if j >= len(text):
                raise Ambiguous("unclosed backtick")
            inner.append(text[i + 1 : j])
            out.append(SUBST)
            i = j + 1
            continue
        elif text.startswith("$(", i) or (not quote and text[i : i + 2] in ("<(", ">(")):
            j = _close_paren(text, i + 2)
            inner.append(text[i + 2 : j])
            out.append(SUBST)
            i = j + 1
            continue
        out.append(c)
        i += 1
    if quote:
        raise Ambiguous("unclosed quote")
    return "".join(out), inner


def tokenize(text):
    try:
        lexer = shlex.shlex(text.replace("\n", " ; "), posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError as e:
        raise Ambiguous(f"cannot parse the command ({e})") from None


def split_segments(tokens):
    segs, cur, prev = [], [], None
    for tok in tokens:
        if tok in SEPARATORS or tok in ("(", ")"):
            if cur:
                segs.append([prev, cur, tok])
            elif segs and segs[-1][2] is None:
                segs[-1][2] = tok
            if tok in ("(", ")"):
                segs.append([tok, None, None])
            prev, cur = tok, []
        else:
            cur.append(tok)
    if cur:
        segs.append([prev, cur, None])
    for i, seg in enumerate(segs):
        if seg[1] is not None and seg[2] is None and i + 1 < len(segs):
            seg[2] = segs[i + 1][0]
    return segs


def strip_redirects(argv):
    args, i = [], 0
    while i < len(argv):
        tok = argv[i]
        if tok.isdigit() and i + 1 < len(argv) and argv[i + 1] in REDIRECTS:
            i += 1
            tok = argv[i]
        if tok in REDIRECTS:
            i += 2
            continue
        args.append(tok)
        i += 1
    return args


def strip_keywords(argv):
    i = 0
    while i < len(argv) and argv[i] in KEYWORDS:
        i += 1
    return argv[i:]


def unwrap(argv):
    env, i = {}, 0
    while i < len(argv):
        tok = argv[i]
        base = os.path.basename(tok)
        if ENV_ASSIGN.match(tok):
            key, _, value = tok.partition("=")
            env[key] = value
            i += 1
        elif base == "env":
            i += 1
            while i < len(argv) and argv[i].startswith("-"):
                if argv[i] in ("-i", "--ignore-environment", "-"):
                    i += 1
                elif argv[i] in ("-u", "--unset"):
                    i += 2
                elif argv[i].startswith("--unset="):
                    i += 1
                else:
                    raise Ambiguous(f"env {argv[i]} hides what runs")
        elif base in SIMPLE_WRAPPERS:
            i += 1
            while i < len(argv) and argv[i].startswith("-"):
                if argv[i] in ("-v", "-V"):
                    return env, []
                if argv[i] not in ("-p", "--"):
                    raise Ambiguous(f"{base} {argv[i]} hides what runs")
                i += 1
        else:
            break
    return env, argv[i:]


def is_git(word):
    base = os.path.basename(word)
    return base == "git" or base.startswith("git-")


def _raidlock_python(argv):
    return len(argv) >= 3 and argv[1] == "-m" and argv[2] == "raidlock"


def _cd_target(argv, cwds):
    args = argv[1:]
    if args and args[0] == "--":
        args = args[1:]
    if len(args) != 1 or args[0].startswith(("-", "+")):
        return {None}
    target = args[0]
    if target == "-" or "$" in target or SUBST in target or GLOB.search(target):
        return {None}
    out = set()
    for c in cwds:
        out.add(None if c is None else os.path.normpath(os.path.join(c, os.path.expanduser(target))))
    return out


def analyze(command, cwds, depth=0, exports=None, all_commands=False):
    if depth > MAX_DEPTH:
        raise Ambiguous("commands nested too deep")
    text, inners = extract_substitutions(strip_heredocs(command))
    cur = set(cwds)
    seen = set(cur)
    stack = []
    carried = dict(exports or {})
    ops = []
    for prev, argv, nxt in split_segments(tokenize(text)):
        if argv is None:
            if prev == "(":
                stack.append(set(cur))
            elif stack:
                cur = stack.pop()
            else:
                raise Ambiguous("unbalanced parentheses")
            continue
        raw_argv = argv
        argv = strip_keywords(strip_redirects(argv))
        prefix, argv = unwrap(argv)
        if all_commands:
            ops.append({"env": {**carried, **prefix}, "argv": argv, "raw": raw_argv, "cwds": frozenset(cur)})
        if not argv:
            carried.update(prefix)
            continue
        base = os.path.basename(argv[0])
        if ("$" in argv[0] or SUBST in argv[0]) and mentions_git(command):
            raise Ambiguous("the executable is computed at run time next to git")
        env = {**carried, **prefix}
        if base in ("cd", "pushd", "popd"):
            new = {None} if base == "popd" else _cd_target(argv, cur)
            if env.get("CDPATH") or os.environ.get("CDPATH"):
                new = {None}
            cur = new if prev in (None, ";", "(", ")") and nxt == "&&" else cur | new
            seen |= cur
            continue
        if base == "export":
            for item in argv[1:]:
                if ENV_ASSIGN.match(item):
                    key, _, value = item.partition("=")
                    carried[key] = value
            continue
        if is_git(argv[0]):
            if not all_commands:
                ops.append({"env": env, "argv": argv, "cwds": frozenset(cur)})
            continue
        if base in SHELLS:
            script = None
            for i, a in enumerate(argv[1:], 1):
                if a == "--":
                    break
                if a.startswith("-") and not a.startswith("--") and "c" in a[1:]:
                    if i + 1 >= len(argv):
                        raise Ambiguous(f"{base} -c without a script")
                    script = argv[i + 1]
                    break
            if script is not None:
                ops += analyze(script, cur, depth + 1, env, all_commands)
            elif len([a for a in argv[1:] if not a.startswith("-")]) == 0 and mentions_git(command):
                raise Ambiguous(f"{base} reads commands from stdin next to git")
            continue
        if base == "eval":
            ops += analyze(" ".join(argv[1:]), cur, depth + 1, env, all_commands)
            cur = {None}
            seen |= cur
            continue
        if base in ("source", "."):
            cur = {None}
            seen |= cur
            continue
        if INTERPRETER.match(base):
            if not _raidlock_python(argv) and any(mentions_git(a) for a in argv[1:]):
                raise Ambiguous(f"{base} runs code that mentions git")
            continue
        if base in LAUNCHERS:
            if any(mentions_git(a) for a in argv[1:]):
                raise Ambiguous(f"{base} launches git indirectly")
            continue
        if base not in NON_EXEC and any(is_git(a) and os.path.basename(a) == "git" for a in argv[1:]):
            raise Ambiguous(f"{base} is passed git as an argument")
    for inner in inners:
        ops += analyze(inner, seen, depth + 1, carried, all_commands)
    return ops


def parse_git(argv):
    base = os.path.basename(argv[0])
    if base != "git":
        return [], base[4:], argv[1:]
    cdirs, i = [], 1
    while i < len(argv) and argv[i].startswith("-"):
        a = argv[i]
        if a == "-C":
            if i + 1 >= len(argv):
                raise Ambiguous("git -C without a directory")
            cdirs.append(argv[i + 1])
            i += 2
            continue
        if a in ("--icase-pathspecs", "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs"):
            raise Forbidden("pathspec mode overrides are not safely auditable")
        if a in GLOBAL_FLAGS:
            i += 1
            continue
        if a in ("--version", "--help"):  # print and exit; git runs nothing after them
            return cdirs, "", []
        raise Forbidden(f"git option {a.split('=', 1)[0]} is not allowed")
    if i >= len(argv):
        return cdirs, "", []
    return cdirs, argv[i], argv[i + 1 :]


def split_args(args, value_opts=(), short_values=""):
    flags, values, pos, after = set(), {}, [], []
    i, dashdash = 0, False
    while i < len(args):
        a = args[i]
        if dashdash:
            after.append(a)
        elif a == "--":
            dashdash = True
        elif a.startswith("--"):
            name, eq, value = a.partition("=")
            flags.add(name)
            if eq:
                values.setdefault(name, []).append(value)
            elif name in value_opts and i + 1 < len(args):
                values.setdefault(name, []).append(args[i + 1])
                i += 1
        elif a.startswith("-") and len(a) > 1:
            for j, ch in enumerate(a[1:], 1):
                flags.add("-" + ch)
                if ch in short_values:
                    rest = a[j + 1 :]
                    if rest:
                        values.setdefault("-" + ch, []).append(rest)
                    elif i + 1 < len(args):
                        values.setdefault("-" + ch, []).append(args[i + 1])
                        i += 1
                    break
        else:
            pos.append(a)
        i += 1
    return flags, values, pos, after, dashdash


def _paths(items):
    if not items:
        return WHOLE, []
    for p in items:
        if p.startswith(":") or "$" in p or SUBST in p:
            return WHOLE, []
    return PATHS, list(items)


def _exec_check(sub, flags, args):
    for f in flags:
        if f.startswith("--") and any(option.startswith(f) and option != f for option in EXEC_OPTIONS + OUTPUT_OPTIONS):
            raise Forbidden(f"git {sub} abbreviated option {f} is not safely auditable")
        if f in EXEC_OPTIONS:
            raise Forbidden(f"git {sub} {f} runs an external command")
    if sub in ("rebase",) and ("-x" in flags or "--exec" in flags):
        raise Forbidden("git rebase --exec runs an external command")
    if sub in ("fetch", "pull", "clone", "ls-remote", "archive") and "-u" in flags:
        raise Forbidden(f"git {sub} -u runs an external command")
    if sub == "grep" and "-O" in flags:
        raise Forbidden("git grep -O runs an external command")
    if any("ext::" in a or "fd::" in a for a in args):
        raise Forbidden("git ext:: and fd:: transports run external commands")


def classify(sub, args):
    """Return (kind, paths, written) for one git subcommand: kind is read, ref, paths or whole."""
    flags, values, pos, after, dashdash = split_args(
        args,
        (
            "--output",
            "--source",
            "--pathspec-from-file",
            "--message",
            "--file",
            "--author",
            "--date",
            "--template",
            "--reuse-message",
            "--reedit-message",
            "--fixup",
            "--squash",
            "--trailer",
            "--cleanup",
            "--exclude",
            "--orphan",
            "--conflict",
            "--chmod",
            "--upload-pack",
            "--receive-pack",
            "--exec",
            "--extcmd",
            "--tool",
            "--open-files-in-pager",
            "--format",
            "--pretty",
            "--sort",
            "--contains",
            "--points-at",
        ),
        _short_values(sub),
    )
    if "$" in sub or SUBST in sub:
        raise Ambiguous("the git subcommand is computed at run time")
    _exec_check(sub, flags, args)
    if sub in FORBIDDEN_SUBS:
        raise Forbidden(f"git {sub} runs external commands or rewrites the repo")
    if "--pathspec-from-file" in flags:
        return WHOLE, [], []
    written = [v for opt in OUTPUT_OPTIONS for v in values.get(opt, [])]
    if sub in READ_ONLY or sub == "":
        return READ, [], written
    if any(a.startswith(":") for a in pos + after):
        raise Forbidden("magic pathspecs are not safely auditable")
    if sub == "branch":
        listing = {
            "--list",
            "-l",
            "-a",
            "--all",
            "-r",
            "--remotes",
            "-v",
            "-vv",
            "--verbose",
            "--show-current",
            "--contains",
            "--no-contains",
            "--merged",
            "--no-merged",
            "--sort",
            "--format",
            "--points-at",
            "--column",
            "--no-column",
            "--color",
            "--no-color",
            "-i",
            "--ignore-case",
            "--abbrev",
            "--no-abbrev",
            "-q",
            "--quiet",
        }
        if "--list" in flags or (not pos and flags <= listing):
            return READ, [], written
        return REF, [], written
    if sub == "tag":
        if (
            "-l" in flags
            or "--list" in flags
            or (not pos and flags <= {"-n", "--sort", "--format", "--contains", "--points-at"})
        ):
            return READ, [], written
        return REF, [], written
    if sub == "remote":
        if not pos or pos[0] in ("show", "get-url") or flags <= {"-v", "--verbose"} and not pos:
            return READ, [], written
        return REF, [], written
    if sub == "config":
        if flags & {
            "--add",
            "--replace-all",
            "--unset",
            "--unset-all",
            "--remove-section",
            "--rename-section",
            "--edit",
            "-e",
        }:
            raise Forbidden("git config writes can install commands")
        if flags & {"--get", "--get-all", "--get-regexp", "--list", "-l"} or (pos and pos[0] in ("get", "list")):
            return READ, [], written
        if len(pos) == 1 and "." in pos[0]:
            # `git config user.email` with no value reads the key.
            return READ, [], written
        raise Forbidden("git config writes can install commands; edit config outside agents")
    if sub == "stash":
        action = pos[0] if pos else "push"
        if action in ("list", "show"):
            return READ, [], written
        if action in ("drop", "clear", "create", "store"):
            return REF, [], written
        if action in ("push", "save") or not pos:
            items = (pos[1:] if pos and action == "push" else []) + after
            return _paths(items) + (written,)
        return WHOLE, [], written
    if sub in REF_ONLY:
        return REF, [], written
    if sub in WHOLE_TREE:
        return WHOLE, [], written
    if sub == "restore":
        return _paths(pos + after) + (written,)
    if sub == "checkout":
        if dashdash:
            return _paths(after) + (written,)
        if not pos and not flags & {"--orphan", "-f", "--force", "-p", "--patch", "-m", "--merge"}:
            return REF, [], written
        return WHOLE, [], written
    if sub == "switch":
        if flags & {"-c", "-C", "--create", "--force-create"} and not pos:
            return REF, [], written
        return WHOLE, [], written
    if sub == "reset":
        if flags & {"--hard", "--merge", "--keep", "-p", "--patch"}:
            return WHOLE, [], written
        if "--soft" in flags:
            return REF, [], written
        if dashdash:
            return _paths(after) + (written,)
        if pos and REV.match(pos[0]) and pos[1:]:
            return _paths(pos[1:]) + (written,)
        return WHOLE, [], written
    if sub == "clean":
        if flags & {"-n", "--dry-run"}:
            return READ, [], written
        return _paths(pos + after) + (written,)
    if sub == "apply":
        if flags & {"--check", "--stat", "--numstat", "--summary"} and "--apply" not in flags:
            return READ, [], written
        return WHOLE, [], written
    if sub in ("rm", "mv"):
        if flags & {"-n", "--dry-run"}:
            return READ, [], written
        return _paths(pos + after) + (written,)
    if sub == "add":
        if flags & {"-n", "--dry-run"}:
            return READ, [], written
        if flags & {"-p", "--patch", "-i", "--interactive", "-e", "--edit"}:
            return WHOLE, [], written
        return _paths(pos + after) + (written,)
    if sub == "commit":
        if flags & {"-a", "--all", "-p", "--patch", "--interactive"}:
            return WHOLE, [], written
        items = pos + after
        if items:
            return _paths(items) + (written,)
        return REF, [], written
    if sub == "init":
        # Creates (or harmlessly re-initializes) a repository; it changes no tracked file and no ref of an existing one.
        return READ, [], written
    raise Ambiguous(f"unsupported git subcommand {sub!r}; use a directly auditable command")


def _short_values(sub):
    return {
        "commit": "mFCct",
        "restore": "s",
        "checkout": "bB",
        "switch": "cC",
        "clean": "e",
        "stash": "m",
        "diff": "",
        "log": "n",
        "show": "",
        "tag": "mFu",
        "branch": "",
        "add": "",
        "rm": "",
        "mv": "",
        "fetch": "uo",
        "pull": "u",
        "push": "o",
        "rebase": "xs",
        "merge": "msX",
        "grep": "efO",
    }.get(sub, "")
