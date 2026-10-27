import re

from .errors import Precondition

LANES = ("todo", "doing", "review", "blocked", "done", "cancelled")
HOLD_LANES = ("doing", "review", "blocked")
ACTIVE_LANES = ("doing", "review", "blocked")
A2A_STATE = {
    "todo": "submitted",
    "doing": "working",
    "blocked": "input-required",
    "done": "completed",
    "cancelled": "canceled",
    "review": "review",
}
VERDICTS = ("approve", "changes", "comment")
ROLES = ("lead", "worker", "reviewer")
CARD_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
AGENT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
TEAM_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_LINE_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_BLOCK_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f  ]")
TEXT_FIELDS = ("title", "scope", "ticket", "verify", "output")


def line_text(value, field, limit=2000):
    """Validate a single-line text field: no control characters, bounded length."""
    text = "" if value is None else str(value)
    if _LINE_CONTROL.search(text):
        raise Precondition(f"{field} must not contain control characters or newlines", field=field)
    if len(text) > limit:
        raise Precondition(f"{field} is longer than {limit} characters", field=field)
    return text.strip()


def block_text(value, field, limit=32768):
    """Validate multi-line text: newlines and tabs allowed, other control characters rejected."""
    text = "" if value is None else str(value)
    if _BLOCK_CONTROL.search(text):
        raise Precondition(f"{field} must not contain control characters", field=field)
    if len(text.encode("utf-8")) > limit:
        raise Precondition(f"{field} is larger than {limit} bytes", field=field, limit=limit)
    return text


def card_id(value):
    if not value or not isinstance(value, str) or not CARD_ID.fullmatch(value):
        raise Precondition(f"invalid card id {value!r}: use [a-z0-9-], max 64", id=value)
    return value


def slug(title):
    value = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if len(value) > 40:
        boundary = value.rfind("-", 0, 41)
        value = value[:boundary] if boundary >= 0 else ""
    return value or "card"


def agent_name(value):
    if not value or not isinstance(value, str) or not AGENT_NAME.fullmatch(value):
        raise Precondition(f"invalid agent name {value!r}: use [a-z0-9-], 3-40 characters", agent=value)
    return value


def team_name(value):
    if not value or not TEAM_NAME.match(value):
        raise Precondition(f"invalid team name {value!r}: use [a-z0-9-], max 40", team=value)
    return value


def lane(value):
    if value not in LANES:
        raise Precondition(f"lane must be one of {', '.join(LANES)}", lane=value)
    return value


def new_card(cid, title, created, **fields):
    card = {
        "id": card_id(cid),
        "title": line_text(title, "title", 300),
        "lane": "todo",
        "owner": "",
        "team": fields.get("team") or "",
        "ticket": line_text(fields.get("ticket"), "ticket", 200),
        "scope": line_text(fields.get("scope"), "scope"),
        "output": line_text(fields.get("output"), "output"),
        "verify": line_text(fields.get("verify"), "verify"),
        "verify_cmd": line_text(fields.get("verify_cmd"), "verify_cmd"),
        "locks": list(fields.get("locks") or []),
        "repo": fields.get("repo") or "",
        "allow_protected": list(fields.get("allow_protected") or []),
        "depends": list(fields.get("depends") or []),
        "reviewers": list(fields.get("reviewers") or []),
        "fence": 0,
        "rev": 0,
        "released": False,
        "created": created,
        "updated": created,
        "notes": [],
        "reviews": [],
        "verifications": [],
        "costs": [],
    }
    if not card["title"]:
        raise Precondition("title is required")
    return card


def a2a_state(card):
    if card["lane"] == "todo" and card.get("released"):
        return "canceled"
    return A2A_STATE[card["lane"]]


def approvals(card):
    latest = {}
    for r in card.get("reviews", []):
        if r["verdict"] != "comment":
            latest[r["by"]] = r["verdict"]
    return sorted(a for a, v in latest.items() if v == "approve" and a != card.get("owner"))


def last_verification(card):
    runs = card.get("verifications") or []
    return runs[-1] if runs else None
