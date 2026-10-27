import contextlib
import os
import re

from ..core import redact, store
from ..core.errors import Precondition
from ..core.lock import FileLock
from . import frontmatter as F

LESSONS = "lessons"
PENDING = ".pending"
HUBS = "hubs"
STATE = ".state.json"
TEXT_MAX = 200
LINKED_PATHS = 3
FIELDS = (
    "type",
    "id",
    "created",
    "workspace",
    "workspace_uid",
    "repo",
    "paths",
    "card",
    "agent",
    "provider",
    "model",
    "source",
    "source_id",
    "tags",
    "hits",
    "last_used",
    "status",
    "proposed_by",
    "approved_by",
    "approved_at",
)
HUB_KINDS = ("card", "agent", "repo", "path")


def validate_id(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{12}", value) is None:
        raise Precondition("invalid lesson id: expected 12 lowercase hexadecimal characters")
    return value


def validate_folder(folder):
    if folder not in (LESSONS, PENDING):
        raise Precondition("invalid lesson folder")
    return folder


def slug(text):
    """Lowercase [a-z0-9-] form for hub note names."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (text or "").lower())).strip("-") or "x"


def one_line(text, limit=TEXT_MAX):
    text = " ".join(redact(text or "").replace("[[", "[").replace("]]", "]").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def hubs_of(lesson):
    """Hub note names a lesson links to, by kind."""
    out = {"card": [], "agent": [], "repo": [], "path": []}
    if lesson.get("card"):
        out["card"].append("card-" + slug(lesson["card"]))
    if lesson.get("agent"):
        out["agent"].append("agent-" + slug(lesson["agent"]))
    if lesson.get("repo"):
        out["repo"].append("repo-" + slug(lesson["repo"]))
    for p in lesson.get("paths", [])[:LINKED_PATHS]:
        out["path"].append("path-" + slug(p))
    return out


def body(lesson):
    h = hubs_of(lesson)
    head = [lesson["source"]] + [f"[[{n}]]" for k in ("card", "agent", "repo") for n in h[k]]
    lines = [lesson["text"], " · ".join(head)]
    if h["path"]:
        lines.append("paths: " + " ".join(f"[[{n}]]" for n in h["path"]))
    return "\n".join(lines)


def render(lesson):
    meta = {k: lesson.get(k, "") for k in FIELDS}
    meta["type"] = "lesson"
    meta["paths"] = list(lesson.get("paths", []))
    meta["tags"] = list(lesson.get("tags", []))
    meta["hits"] = int(lesson.get("hits", 0))
    return F.dump(meta, body(lesson))


def from_note(text):
    meta, rest = F.parse(text)
    if meta.get("type") != "lesson":
        return None
    validate_id(meta.get("id"))
    lesson = dict(meta)
    lesson["text"] = rest.split("\n", 1)[0]
    return lesson


class Vault:
    """An Obsidian-compatible folder: lessons/lesson-<id>.md plus generated hub notes under hubs/."""

    def __init__(self, root, lock=None):
        self.root = root
        self._lock = lock

    @classmethod
    def for_board(cls, board):
        return cls(os.path.join(board.dir, "lore"), lock=board.lock)

    @classmethod
    def global_(cls, home):
        root = os.path.join(home, "lore")
        return cls(root, lock=lambda: FileLock(os.path.join(root, ".lock")))

    @contextlib.contextmanager
    def locked(self):
        os.makedirs(self.root, exist_ok=True)
        if self._lock is None:
            yield
            return
        with self._lock():
            yield

    def _path(self, *parts):
        path = os.path.abspath(os.path.join(self.root, *parts))
        if os.path.commonpath([os.path.abspath(self.root), path]) != os.path.abspath(self.root):
            raise Precondition("invalid vault path")
        return path

    def lesson_path(self, lesson_id, folder=LESSONS):
        return self._path(validate_folder(folder), f"lesson-{validate_id(lesson_id)}.md")

    def _note_path(self, rel):
        parts = rel.split(os.sep)
        if len(parts) != 2 or parts[0] not in (LESSONS, PENDING, HUBS) or parts[1] in (".", ".."):
            raise Precondition("invalid vault path")
        if parts[0] in (LESSONS, PENDING) and not re.fullmatch(r"lesson-[0-9a-f]{12}\.md", parts[1]):
            raise Precondition("invalid lesson id")
        return self._path(rel)

    def _validate_note(self, rel, text):
        folder, name = rel.split(os.sep)
        if folder in (LESSONS, PENDING):
            lesson = from_note(text)
            if not lesson or name != f"lesson-{lesson['id']}.md":
                raise Precondition("lesson id does not match filename")

    def write(self, rel, text):
        path = self._note_path(rel)
        self._validate_note(rel, text)
        store.atomic_write_text(path, text)

    def has(self, lesson_id, folder=LESSONS):
        return os.path.exists(self.lesson_path(lesson_id, folder))

    def get(self, lesson_id, folder=LESSONS):
        try:
            with open(self.lesson_path(lesson_id, folder), encoding="utf-8") as f:
                lesson = from_note(f.read())
                if lesson and lesson["id"] != lesson_id:
                    raise Precondition("lesson id does not match filename")
                return lesson
        except (FileNotFoundError, F.FrontmatterError):
            return None

    def put(self, lesson, folder=LESSONS):
        self.lesson_path(lesson["id"], folder)
        self.write(os.path.join(folder, f"lesson-{lesson['id']}.md"), render(lesson))

    def drop(self, lesson_id, folder=LESSONS):
        try:
            os.remove(self.lesson_path(lesson_id, folder))
            return True
        except FileNotFoundError:
            return False

    def pending(self):
        """Lessons waiting for lead approval. Kept in a dot folder, so Obsidian does not show them."""
        return self.lessons(PENDING)

    def lessons(self, folder=LESSONS):
        validate_folder(folder)
        try:
            names = sorted(os.listdir(self._path(folder)))
        except FileNotFoundError:
            return []
        out = []
        for name in names:
            if not re.fullmatch(r"lesson-[0-9a-f]{12}\.md", name):
                continue
            try:
                lesson = self.get(name[7:-3], folder)
            except (OSError, Precondition):
                continue
            if lesson:
                out.append(lesson)
        return out

    def state(self):
        return store.read_json(self._path(STATE), {}) or {}

    def save_state(self, data):
        store.write_json(self._path(STATE), {"schema": store.SCHEMA, **data})

    def rebuild_hubs(self, lessons=None):
        """Regenerate every hub note from the lessons and remove hubs nothing links to any more."""
        lessons = [x for x in self.lessons() if x.get("status") != "retired"] if lessons is None else lessons
        groups = {}
        for lesson in lessons:
            validate_id(lesson.get("id"))
            for kind, names in hubs_of(lesson).items():
                for name in names:
                    groups.setdefault((kind, name), []).append(lesson)
        wanted = set()
        for (kind, name), items in sorted(groups.items()):
            rel = os.path.join(HUBS, f"{name}.md")
            wanted.add(f"{name}.md")
            lines = [f"# {name}", ""] + [
                f"- [[lesson-{x['id']}]] {x['text']}"
                for x in sorted(items, key=lambda x: (x.get("created", ""), x["id"]))
            ]
            text = F.dump(
                {"type": "hub", "hub": kind, "name": name, "lessons": len(items), "generated": True}, "\n".join(lines)
            )
            if self._read(rel) != text:
                self.write(rel, text)
        folder = self._path(HUBS)
        if os.path.isdir(folder):
            for n in os.listdir(folder):
                if n.endswith(".md") and n not in wanted:
                    os.remove(os.path.join(folder, n))
        return len(wanted)

    def _read(self, rel):
        path = self._note_path(rel)
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            return None
        self._validate_note(rel, text)
        return text

    def notes(self):
        """Every Markdown note in the vault as (relative path, text)."""
        out = []
        for base, dirs, files in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for n in sorted(files):
                if n.endswith(".md"):
                    p = os.path.join(base, n)
                    rel = os.path.relpath(p, self.root)
                    try:
                        text = self._read(rel)
                    except (Precondition, F.FrontmatterError):
                        continue
                    out.append((rel, text))
        return out
