import posixpath
import re
import unicodedata

from .errors import Precondition

_DRIVE = re.compile(r"^[A-Za-z]:")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def canonical(raw, casefold=False):
    """Return the canonical form of a lock path or raise Precondition."""
    if not isinstance(raw, str):
        raise Precondition("lock path must be text", path=repr(raw))
    text = unicodedata.normalize("NFC", raw.strip())
    if not text:
        raise Precondition("empty lock path", path=raw)
    if _CONTROL.search(text):
        raise Precondition("control character in lock path", path=raw)
    text = text.replace("\\", "/")
    if text.startswith("/") or text.startswith("~") or _DRIVE.match(text):
        raise Precondition(f"lock path must be relative to the workspace: {raw}", path=raw)
    if ".." in text.split("/"):
        raise Precondition(f"lock path must not contain '..': {raw}", path=raw)
    text = posixpath.normpath(text).strip("/")
    if text in ("", "."):
        raise Precondition(f"lock path resolves to the workspace root: {raw}", path=raw)
    return text.casefold() if casefold else text


def split(text):
    return [p for p in text.split("/") if p]


def parse_list(value):
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else re.split(r"[,\s]+", value)
    out = []
    for item in items:
        if item and item.strip():
            p = canonical(item)
            if p not in out:
                out.append(p)
    return out


def _parts(path, fold):
    parts = split(path)
    return [p.casefold() for p in parts] if fold else parts


def contains(outer, inner, casefold=False):
    """True when `inner` equals `outer` or lies under it on a `/` boundary."""
    a, b = _parts(outer, casefold), _parts(inner, casefold)
    return len(a) <= len(b) and b[: len(a)] == a


def overlaps(a, b, casefold=False):
    """True when one path contains the other."""
    return contains(a, b, casefold) or contains(b, a, casefold)
