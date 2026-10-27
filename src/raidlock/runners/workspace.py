import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
import zlib
from contextlib import ExitStack, contextmanager

from ..core import exe
from ..core.errors import Precondition
from ..core.portable import open_nofollow, remove_tree

METADATA = ("config", "hooks", "info", "objects", "objects/info", "refs")
PLANTED = (
    "hooks",
    "info/attributes",
    "info/grafts",
    "info/exclude",
    "refs/replace",
    "objects/info/alternates",
    "objects/info/http-alternates",
)
IDENT = ("-c", "user.name=raidlock", "-c", "user.email=raidlock@localhost")


def git_env(extra=None):
    env = {
        k: os.environ[k]
        for k in ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP")
        if k in os.environ
    }
    context_keys = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_COMMITTER_DATE",
    }
    env.update({k: v for k, v in (extra or {}).items() if not k.startswith("GIT_") or k in context_keys})
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_OPTIONAL_LOCKS="0",
        GIT_NO_REPLACE_OBJECTS="1",
        GIT_ATTR_NOSYSTEM="1",
        GIT_NO_LAZY_FETCH="1",
        GIT_PAGER="cat",
        GIT_SSH_COMMAND="false",
        GIT_ASKPASS="false",
        SSH_ASKPASS="false",
    )
    return env


SAFE_CONFIG = (
    "core.hooksPath=" + os.devnull,
    "core.fsmonitor=false",
    "core.attributesFile=" + os.devnull,
    "core.pager=cat",
    "diff.external=",
    "core.sshCommand=false",
    "credential.helper=",
    "gpg.program=false",
    "gpg.ssh.program=false",
    "gpg.x509.program=false",
    "commit.gpgsign=false",
    "core.askPass=false",
    "core.untrackedCache=false",
    "core.splitIndex=false",
)
READ_COMMANDS = {
    "status",
    "diff",
    "diff-tree",
    "rev-parse",
    "rev-list",
    "ls-tree",
    "ls-files",
    "cat-file",
    "merge-base",
    "log",
    "show",
    "show-ref",
    "ls-remote",
    "for-each-ref",
    "merge-tree",
}

WRITE_COMMANDS = {
    "add",
    "commit",
    "checkout",
    "reset",
    "apply",
    "read-tree",
    "write-tree",
    "commit-tree",
    "update-index",
}


def repo_location(path):
    from ..core import repos

    path = os.path.realpath(path)
    if os.path.isfile(os.path.join(path, "HEAD")) and os.path.isdir(os.path.join(path, "objects")):
        return path, None
    root = repos.find_root(path)
    return (repos.git_dir(root), root) if root else (None, None)


@contextmanager
def object_view(repo):
    """Expose refs, objects and a copied index without reading local config or attributes."""
    from ..core import repos

    gdir, root = repo_location(repo)
    if not gdir:
        yield None
        return
    common = repos.common_dir(gdir)
    with tempfile.TemporaryDirectory(prefix="raidlock-git-") as temp:
        for name in ("HEAD", "index"):
            source = os.path.join(gdir, name)
            if os.path.isfile(source):
                shutil.copyfile(source, os.path.join(temp, name))
        os.symlink(os.path.abspath(os.path.join(common, "refs")), os.path.join(temp, "refs"))
        objects = os.path.join(temp, "objects")
        os.makedirs(os.path.join(objects, "info"))
        with open(os.path.join(objects, "info", "alternates"), "w", newline="\n") as stream:
            stream.write(os.path.abspath(os.path.join(common, "objects")) + "\n")
        os.mkdir(os.path.join(objects, "4b"))
        with open(os.path.join(objects, "4b", "825dc642cb6eb9a060e54bf8d69288fbee4904"), "wb") as stream:
            stream.write(zlib.compress(b"tree 0\0"))
        exclude = os.path.join(common, "info", "exclude")
        if os.path.isfile(exclude):
            os.mkdir(os.path.join(temp, "info"))
            shutil.copyfile(exclude, os.path.join(temp, "info", "exclude"))
        packed = os.path.join(common, "packed-refs")
        if os.path.isfile(packed):
            shutil.copyfile(packed, os.path.join(temp, "packed-refs"))
        with open(os.path.join(temp, "config"), "w", newline="\n") as stream:
            stream.write("[core]\nrepositoryformatversion = 0\nbare = " + ("false" if root else "true") + "\n")
            for remote in repos._read_remotes(os.path.join(common, "config")):
                if re.fullmatch(r"[A-Za-z0-9_.-]+", remote):
                    stream.write(f'[remote "{remote}"]\nfetch = +refs/heads/*:refs/remotes/{remote}/*\n')
        yield temp


def git_run(cwd, *args, binary=False, env=None, timeout=None, input=None):
    command = next((a for i, a in enumerate(args) if not a.startswith("-") and (i == 0 or args[i - 1] != "-c")), "")
    full = git_env(env)
    options = [item for value in SAFE_CONFIG for item in ("-c", value)]
    if command in {"diff", "diff-tree", "log", "show"}:
        pos = args.index(command) + 1
        args = (*args[:pos], "--no-ext-diff", "--no-textconv", *args[pos:])
    if command == "apply" and not ({"--index", "--cached"} & set(args)):
        pos = args.index(command) + 1
        args = (*args[:pos], "--index", *args[pos:])
    with ExitStack() as stack:
        read_only = command in READ_COMMANDS or (command == "config" and "--file" in args)
        view = (
            stack.enter_context(object_view(cwd))
            if read_only or command in WRITE_COMMANDS or full.get("GIT_INDEX_FILE")
            else None
        )
        persist = bool(
            view and command in WRITE_COMMANDS and not full.get("GIT_DIR") and not full.get("GIT_INDEX_FILE")
        )
        clone_origin = None
        if command in {"clone", "fetch"}:
            replaced = list(args)
            for i, arg in enumerate(args):
                if os.path.isabs(arg) and os.path.isdir(arg) and (command != "clone" or i == len(args) - 2):
                    source = stack.enter_context(object_view(arg))
                    if source:
                        replaced[i] = source
                        if command == "clone":
                            clone_origin = arg
            args = tuple(replaced)
            if command == "clone":
                args = (*args[: args.index(command) + 1], "--no-local", *args[args.index(command) + 1 :])
        if view and not full.get("GIT_DIR"):
            full.update(
                GIT_DIR=view,
                GIT_ATTR_SOURCE="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
            )
            _, worktree = repo_location(cwd)
            if worktree:
                full["GIT_WORK_TREE"] = worktree
        if persist:
            from ..core.repos import common_dir

            full["GIT_OBJECT_DIRECTORY"] = os.path.join(common_dir(repo_location(cwd)[0]), "objects")
        if view and full.get("GIT_OBJECT_DIRECTORY"):
            full["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = os.pathsep.join(
                filter(None, (full.get("GIT_ALTERNATE_OBJECT_DIRECTORIES"), os.path.join(view, "objects")))
            )
        command_at = args.index(command) if command else 0
        proc = subprocess.run(
            [exe.find("git"), *args[:command_at], "--no-pager", *options, *args[command_at:]],
            cwd=cwd,
            env=full,
            capture_output=True,
            input=input,
            timeout=timeout,
            text=not binary,
            **({} if binary else {"encoding": "utf-8", "errors": "replace"}),
        )

        if persist and proc.returncode == 0:
            gdir = repo_location(cwd)[0]
            for name in ("HEAD", "index"):
                path = os.path.join(view, name)
                if os.path.isfile(path):
                    _replace_file(os.path.join(gdir, name), _read_nofollow(path))
        if clone_origin and proc.returncode == 0:
            destination = os.path.abspath(os.path.join(cwd, args[-1]))
            git(destination, "config", "remote.origin.url", clone_origin)
        if view and command == "rev-parse":
            before, after = view, os.path.abspath(repo_location(cwd)[0])
            proc.stdout = (
                proc.stdout.replace(before.encode(), after.encode()) if binary else proc.stdout.replace(before, after)
            )
        return proc


def git(cwd, *args, check=True, binary=False, env=None, timeout=None, input=None):
    proc = git_run(cwd, *args, binary=binary, env=env, timeout=timeout, input=input)
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace") if binary else proc.stderr
        raise Precondition(f"git {' '.join(args[:2])} failed: {err.strip()[:300]}", cwd=cwd)
    return proc.stdout


def head(repo):
    return git(repo, "rev-parse", "HEAD").strip()


def is_ancestor(repo, older, newer):
    if not re.fullmatch(r"[0-9a-f]{7,64}", older or "") or not re.fullmatch(r"[0-9a-f]{7,64}", newer or ""):
        return False
    return git_run(repo, "merge-base", "--is-ancestor", older, newer, timeout=30).returncode == 0


def dirty(repo):
    return bool(git(repo, "status", "--porcelain", "--untracked-files=no", check=False).strip())


def clone(src, dst, ref=None):
    """Clone the committed state of `src` into `dst` through a pack transfer, drop its remotes, return
    (BASE, branch)."""
    if os.path.exists(dst):
        remove_tree(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    git(os.path.dirname(dst), "clone", "--quiet", "--no-local", "--no-hardlinks", os.path.realpath(src), dst)
    if ref:
        commit = git(src, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").strip()
        git(dst, "checkout", "--quiet", "--detach", commit)
    for remote in git(dst, "remote").split():
        git(dst, "remote", "remove", remote)
    base = head(dst)
    branch = git(dst, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
    return base, branch


def check_metadata(repo):
    """Refuse a clone whose git metadata is a symlink or a gitfile: the runner would write through it."""
    if os.path.islink(repo):
        raise Precondition("symlinked git metadata: clone root is a symlink", cwd=repo, path=".")
    gdir = os.path.join(repo, ".git")
    try:
        st = os.lstat(gdir)
    except FileNotFoundError:
        raise Precondition("the clone has no .git directory any more", cwd=repo) from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise Precondition("symlinked git metadata: .git is not a plain directory", cwd=repo, path=".git")
    for rel in METADATA:
        path = os.path.join(gdir, rel)
        if os.path.islink(path):
            raise Precondition(f"symlinked git metadata: .git/{rel} is a symlink", cwd=repo, path=".git/" + rel)
    return gdir


def snapshot_config(repo):
    gdir = check_metadata(repo)
    return _read_nofollow(os.path.join(gdir, "config"))


def _read_nofollow(path):
    fd = open_nofollow(path, os.O_RDONLY)
    with os.fdopen(fd, "rb") as f:
        return f.read()


def _replace_file(path, data):
    folder = os.path.dirname(path)
    tmp = os.path.join(folder, f".raidlock-{uuid.uuid4().hex}.tmp")
    fd = open_nofollow(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise


def _remove(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        remove_tree(path)


def restore(repo, config):
    """Undo what an agent could plant in .git before anyone runs git there again: config, hooks, attributes,
    replace refs, grafts and alternates. Refuses symlinked metadata instead of writing through it."""
    gdir = check_metadata(repo)
    _replace_file(os.path.join(gdir, "config"), config)
    for rel in PLANTED:
        _remove(os.path.join(gdir, rel))


def audit(source, base, worktree, store):
    """Hash the worker's files into a lead-owned object store that holds BASE fetched from `source`, and return
    (changed paths, tree). The worker's .git (refs, index, objects, config) is never read."""
    if os.path.exists(store):
        remove_tree(store)
    git(os.path.dirname(store), "init", "--quiet", "--bare", store)
    git(
        store,
        "-c",
        "uploadpack.allowAnySHA1InWant=true",
        "fetch",
        "--quiet",
        "--no-tags",
        "--depth=1",
        os.path.realpath(source),
        base,
    )
    env = {
        "GIT_DIR": store,
        "GIT_WORK_TREE": os.path.realpath(worktree),
        "GIT_INDEX_FILE": os.path.join(store, "audit-index"),
    }
    git(store, "-c", "core.bare=false", "read-tree", base, env=env)
    git(store, "-c", "core.bare=false", "-c", "core.symlinks=true", "add", "-A", env=env)
    tree = git(store, "-c", "core.bare=false", "write-tree", env=env).strip()
    paths = git(store, "diff", "--name-only", "--no-renames", "-z", base, tree).split("\0")
    return sorted({p for p in paths if p}), tree


def write_patch(store, base, tree, out):
    """Write a binary patch from BASE to the audited tree to `out`; return its size."""
    data = git(store, "diff", "--binary", "--no-renames", base, tree, binary=True)
    with open(out, "wb") as f:
        f.write(data)
    return len(data)


def apply_and_commit(repo, patch_file):
    git(repo, "apply", "--whitespace=nowarn", patch_file)
    git(repo, "add", "-A")
    git(repo, *IDENT, "commit", "--quiet", "--no-verify", "-m", "changes under review")
    return head(repo)
