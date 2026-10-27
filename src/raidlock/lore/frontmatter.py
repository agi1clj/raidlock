import json
import re

FENCE = "---"
_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*): (.*)$")
_LINK = re.compile(r"\[\[([^\[\]|#]+)(?:[|#][^\[\]]*)?\]\]")


class FrontmatterError(ValueError):
    pass


def _value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(json.dumps(str(x), ensure_ascii=False) for x in v) + "]"
    return json.dumps("" if v is None else str(v), ensure_ascii=False)


def dump(meta, body):
    """Render a note: YAML frontmatter (a JSON-compatible subset) and a Markdown body."""
    lines = [FENCE] + [f"{k}: {_value(v)}" for k, v in meta.items()] + [FENCE]
    return "\n".join(lines) + "\n" + body.rstrip("\n") + "\n"


def parse(text):
    """Parse a note written by `dump`. Raises FrontmatterError on anything outside that subset."""
    lines = text.split("\n")
    if not lines or lines[0] != FENCE:
        raise FrontmatterError("note does not start with ---")
    meta = {}
    for i, line in enumerate(lines[1:], start=1):
        if line == FENCE:
            return meta, "\n".join(lines[i + 1 :]).rstrip("\n")
        m = _KEY.match(line)
        if not m:
            raise FrontmatterError(f"bad frontmatter line {i}: {line!r}")
        raw = m.group(2)
        try:
            value = json.loads(raw)
        except ValueError as e:
            raise FrontmatterError(f"bad value for {m.group(1)}: {raw!r}") from e
        if isinstance(value, list) and not all(isinstance(x, str) for x in value):
            raise FrontmatterError(f"list {m.group(1)} must hold strings")
        if isinstance(value, (dict, float)) or value is None:
            raise FrontmatterError(f"unsupported value for {m.group(1)}: {raw!r}")
        meta[m.group(1)] = value
    raise FrontmatterError("frontmatter is not closed")


def links(text):
    """Targets of [[wikilinks]] in a text."""
    return [m.group(1).strip() for m in _LINK.finditer(text)]
