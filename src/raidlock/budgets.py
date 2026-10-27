"""Delegation budget checks, using UTC days and calendar months.

The core config API must accept budget.* keys before set_budget can persist
limits. Register the CLI with register_cli(raidlock.cli.add_command).
Checks inspect recorded usage; they do not reserve funds or stop active runs.
"""

import datetime
import json
from decimal import Decimal, InvalidOperation

from .core import clock
from .core.errors import Precondition
from .core.model import card_id

DAILY = "budget.workspace.daily_usd"
MONTHLY = "budget.workspace.monthly_usd"
TOKENS = "budget.tokens_per_card"
COUNT_PLAN = "budget.count_plan_usage"


def _number(value, key, integer=False):
    try:
        if isinstance(value, bool):
            raise ValueError
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or (integer and number != number.to_integral_value()):
            raise ValueError
        result = int(number) if integer else float(number)
        if not integer and not Decimal(str(result)).is_finite():
            raise ValueError
        return result
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise Precondition(f"{key} must be a finite nonnegative {'integer' if integer else 'number'}") from None


def _boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise Precondition(f"{COUNT_PLAN} must be true or false")


def _value(key, value):
    if value is None:
        return None
    if key in (DAILY, MONTHLY):
        return _number(value, key)
    if key == TOKENS:
        return _number(value, key, integer=True)
    if key == COUNT_PLAN:
        return _boolean(value)
    if key.startswith("budget.card."):
        card_id(key[len("budget.card.") :])
        if not isinstance(value, dict) or not value or set(value) - {"usd", "tokens"}:
            raise Precondition(f"{key} must be an object with usd and/or tokens")
        return {
            unit: None if cap is None else _number(cap, key + "." + unit, integer=unit == "tokens")
            for unit, cap in value.items()
        }
    raise Precondition(f"unknown budget key {key}")


def set_budget(board, caller, key, value):
    """Persist a validated cap through core's lead-only config API; null clears it."""
    if key not in (DAILY, MONTHLY, TOKENS, COUNT_PLAN) and not key.startswith("budget.card."):
        raise Precondition(f"unknown budget key {key}")
    if key.startswith("budget.card."):
        board.card(card_id(key[len("budget.card.") :]))
    value = _value(key, value)
    board.set_setting(caller, key, value)
    return {"key": key, "value": value}


def _settings(board):
    settings = board.get_setting()
    return {key: _value(key, value) for key, value in settings.items() if key.startswith("budget.")}


def _remaining(cap, used):
    if cap is None:
        return None
    return max(0, float(Decimal(str(cap)) - Decimal(str(used))))


def _workspace_usd(board, since, until, count_plan):
    rows = board.spend(by="billing", since=clock.iso(since), until=clock.iso(until))["rows"]
    return float(
        sum(
            (Decimal(str(row.get("usd_equiv" if row["key"] == "plan" and count_plan else "usd") or 0)) for row in rows),
            Decimal(0),
        )
    )


def check_before_delegate(board, card):
    """Return allowed, reasons, limits, used and remaining; None means uncapped.

    Accept a card id or a card dict. All token usage counts, even when plan USD
    equivalents are disabled. A card token cap overrides tokens_per_card.
    Call before runner preparation, joining, claiming or cloning, for either role.
    """
    with board.lock():
        card = board.card(card["id"] if isinstance(card, dict) else card)
        settings = _settings(board)
        count_plan = settings.get(COUNT_PLAN)
        count_plan = True if count_plan is None else count_plan
        caps = settings.get("budget.card." + card["id"]) or {}
        limits = {
            "card_usd": caps.get("usd"),
            "card_tokens": caps.get("tokens", settings.get(TOKENS)),
            "daily_usd": settings.get(DAILY),
            "monthly_usd": settings.get(MONTHLY),
        }
        row = next((r for r in board.spend(by="card")["rows"] if r["key"] == card["id"]), {})
        usd = Decimal(str(row.get("usd") or 0))
        if count_plan:
            usd += sum(
                (Decimal(str(r.get("usd_equiv") or 0)) for r in card.get("costs", []) if r.get("billing") == "plan"),
                Decimal(0),
            )
        now = clock.now()
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month = day.replace(day=1)
        next_month = (month.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        used = {
            "card_usd": float(usd),
            "card_tokens": row.get("tokens") or 0,
            "daily_usd": _workspace_usd(board, day, day + datetime.timedelta(days=1), count_plan),
            "monthly_usd": _workspace_usd(board, month, next_month, count_plan),
        }
        remaining = {key: _remaining(cap, used[key]) for key, cap in limits.items()}
        if remaining["card_tokens"] is not None:
            remaining["card_tokens"] = max(0, limits["card_tokens"] - used["card_tokens"])
        reasons = [f"{key} budget exhausted" for key, cap in limits.items() if cap is not None and used[key] >= cap]
        return {
            "card": card["id"],
            "allowed": not reasons,
            "reasons": reasons,
            "limits": limits,
            "used": used,
            "remaining": remaining,
            "count_plan_usage": count_plan,
        }


def _setup(parser):
    parser.add_argument("budget_action", nargs="?", default="show", choices=("set", "show"))
    parser.add_argument("key", nargs="?", help="budget config key for set, optional card id for show")
    parser.add_argument(
        "value", nargs="?", help='JSON value, e.g. 10, true, or {"usd":10,"tokens":50000}; null clears a cap'
    )


def _handler(ctx, args):
    if args.budget_action == "set":
        if args.key is None or args.value is None:
            raise Precondition("budget set requires a config key and JSON value")
        try:
            value = json.loads(args.value)
        except ValueError:
            raise Precondition("budget value must be valid JSON") from None
        result = set_budget(ctx.board, ctx.caller(), args.key, value)
    elif args.value is not None:
        raise Precondition("budget show accepts at most one card id")
    elif args.key:
        result = check_before_delegate(ctx.board, args.key)
    else:
        settings = _settings(ctx.board)
        if settings.get(COUNT_PLAN) is None:
            settings[COUNT_PLAN] = True
        result = {"settings": settings}
    return result, json.dumps(result, indent=2, sort_keys=True)


def register_cli(add_command):
    add_command("budget", "set or show delegation budgets", _setup, _handler, mutating=True)
