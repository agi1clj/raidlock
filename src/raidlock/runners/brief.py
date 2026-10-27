import json
import re

from ..core import redact
from ..core.brief import UNTRUSTED_HEADER, UNTRUSTED_RULE, quote_data
from ..lore.inject import HEADER as LORE_HEADER

HANDBACK_CAP = 4096
ITEM_CAP = 400
LIST_CAP = 20
STATUSES = ("done", "partial", "blocked")
VERDICTS = ("approve", "changes", "comment")

WORKER_CONTRACT = """# Worker contract (raidlock)

You are a worker agent. The raidlock runner delegated one card to you.
The current directory is a private clone of the repository. Nobody else works in it.

- Change only files under the allowed paths listed at the end. Lock paths in the card are workspace paths;
  the allowed paths are the same locks relative to this clone. A change anywhere else fails the run.
- Never touch .github/workflows/, .claude/, .board/, *.pem, .env* or anything named secrets.
- Do not commit, push, merge, apply infrastructure, change settings or permissions, or read secrets.
  The runner collects your uncommitted changes as a patch and does all board work (notes, moves) for you.
- Assume there is no network. Do not install packages. The runner runs the card's verify command after you finish.
- Keep the final answer short: cite paths and commands, never paste file contents.
- End with only the handback JSON object:
  {"status": "done|partial|blocked", "evidence": ["..."], "paths_changed": ["..."], "open_questions": ["..."]}
"""

REVIEWER_CONTRACT = """# Reviewer contract (raidlock)

You are a reviewer agent. The current directory is a private, read-only clone of the repository.
The changes under review are the difference between {base} and HEAD: run `git diff {base} HEAD` and read the files.

- Do not change files. Do not commit, push, merge, or read secrets.
- Check correctness first, then the card's acceptance criteria, then risk.
- Keep the final answer short: cite paths and lines, never paste file contents.
- End with only the review JSON object:
  {{"verdict": "approve|changes|comment", "summary": "...", "findings": ["path:line problem"]}}
"""

HANDBACK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "evidence", "paths_changed", "open_questions"],
    "properties": {
        "status": {"type": "string", "enum": list(STATUSES)},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "paths_changed": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
}

REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "summary", "findings"],
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
    },
}


def schema(role):
    return REVIEW_SCHEMA if role == "reviewer" else HANDBACK_SCHEMA


def render(card_brief, role, rels, base):
    """Prompt text: the stable contract first (cacheable), then the core card brief without its board-command loop
    (the runner does the board work), then the allowed paths as they appear inside the clone."""
    contract = REVIEWER_CONTRACT.format(base=base) if role == "reviewer" else WORKER_CONTRACT
    allowed = ", ".join(r or "(the whole repository)" for r in rels)
    return redact(contract + "\n" + _quoted_brief(card_brief) + "\nAllowed paths in this clone: " + allowed + "\n")


def _quoted_brief(text):
    return (
        UNTRUSTED_HEADER
        + "\n"
        + UNTRUSTED_RULE
        + "\n"
        + "\n".join(quote_data(line) for line in _without_loop(text).splitlines())
        + "\n"
    )


def _without_loop(text):
    out, skip = [], False
    for line in text.splitlines():
        if line.startswith("## ") or line == LORE_HEADER:
            skip = line.strip() == "## Loop"
        if not skip:
            out.append(line)
    return "\n".join(out).rstrip() + "\n"


def _clip(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def find_json(text):
    """The last JSON object in `text` (whole text first, then fenced or brace-delimited candidates)."""
    if isinstance(text, dict):
        return text
    if not text:
        return None
    text = text.strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except ValueError:
        pass
    for match in reversed(list(re.finditer(r"\{", text))):
        try:
            value, _ = json.JSONDecoder().raw_decode(text[match.start() :])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _strings(value):
    if not isinstance(value, list):
        value = [value] if value else []
    return [_clip(redact(str(v)), ITEM_CAP) for v in value if str(v).strip()][:LIST_CAP]


def handback(raw, fallback_text=""):
    """Normalize an agent handback to {status, evidence[], paths_changed[], open_questions[]}."""
    data = find_json(raw) or {}
    status = data.get("status") if data.get("status") in STATUSES else ""
    out = {
        "status": status or "partial",
        "evidence": _strings(data.get("evidence")),
        "paths_changed": _strings(data.get("paths_changed")),
        "open_questions": _strings(data.get("open_questions")),
    }
    if not data and fallback_text:
        out["evidence"] = [_clip(redact(fallback_text), ITEM_CAP)]
        out["open_questions"] = ["the agent returned no handback JSON"]
    return out


def review(raw, fallback_text=""):
    """Normalize a review to {verdict, summary, findings[]}; anything unparseable is a comment."""
    data = find_json(raw) or {}
    verdict = data.get("verdict") if data.get("verdict") in VERDICTS else "comment"
    summary = data.get("summary") or (fallback_text if not data else "") or "no summary"
    return {
        "verdict": verdict,
        "summary": _clip(redact(str(summary)), 1500),
        "findings": _strings(data.get("findings")),
    }


def cap(obj, limit=HANDBACK_CAP):
    """Trim list items (longest lists first) until the JSON encoding fits in `limit` bytes."""
    obj = json.loads(json.dumps(obj))
    while len(json.dumps(obj, ensure_ascii=False).encode("utf-8")) > limit:
        lists = [(len(v), k) for k, v in obj.items() if isinstance(v, list) and v]
        if not lists:
            for k, v in obj.items():
                if isinstance(v, str) and len(v) > 200:
                    obj[k] = v[: len(v) // 2] + "..."
                    break
            else:
                break
            continue
        _, key = max(lists)
        obj[key].pop()
        obj.setdefault("truncated", True)
    return obj
