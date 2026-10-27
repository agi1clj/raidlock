"""Treasury forecast: burn rate, a forecast to the end of the calendar month (UTC) and budget alerts.

Spend is measured the way budget checks measure it: paid USD, plus the API-equivalent of plan usage unless
`budget.count_plan_usage` is false. The forecast adds the trailing seven-day daily rate for the rest of the month.
Alerts fire at ALERT_PCT of a cap, when a cap is reached, and when the forecast passes the monthly cap.
"""

import datetime
import math

from . import budgets
from .core import RaidlockError, clock

ALERT_PCT = 80
TRAIL_DAYS = 7
HISTORY_DAYS = 14


def _period(now):
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + datetime.timedelta(days=32)).replace(day=1)
    return start, end


def _measure(rows, count_plan):
    for r in rows:
        for key in ("usd", "usd_equiv"):
            value = r.get(key)
            if value is not None and (not math.isfinite(float(value)) or float(value) < 0):
                raise ValueError("invalid-data: nonfinite or negative money")
    paid = sum(float(r.get("usd") or 0) for r in rows)
    plan = sum(float(r.get("usd_equiv") or 0) for r in rows if r.get("key") == "plan")
    equiv = sum(float(r.get("usd_equiv") or 0) for r in rows)
    total = paid + (plan if count_plan else 0)
    if not math.isfinite(total) or not math.isfinite(equiv):
        raise ValueError("invalid-data: money aggregation overflow")
    return round(total, 6), round(equiv, 6)


def _spent(board, since, until, count_plan):
    rows = board.spend(by="billing", since=clock.iso(since), until=clock.iso(until))["rows"]
    return _measure(rows, count_plan)


def _settings(board):
    try:
        return budgets._settings(board)
    except (RaidlockError, ValueError, TypeError):
        return {}


def _alert(workspace, level, kind, text):
    return {"workspace": workspace, "level": level, "kind": kind, "text": text}


def forecast(board, now=None):
    try:
        return _forecast(board, now)
    except (RaidlockError, ValueError, TypeError, OverflowError):
        return {"workspace": board.name, "error": "invalid-data", "alerts": [], "by_day": []}


def _forecast(board, now=None):
    """Forecast for one workspace."""
    now = now or clock.now()
    start, end = _period(now)
    settings = _settings(board)
    count_plan = settings.get(budgets.COUNT_PLAN)
    count_plan = True if count_plan is None else count_plan
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    until = now + datetime.timedelta(seconds=1)
    month, month_equiv = _spent(board, start, until, count_plan)
    today, _ = _spent(board, day_start, until, count_plan)
    trail, _ = _spent(board, now - datetime.timedelta(days=TRAIL_DAYS), until, count_plan)
    elapsed = max((now - start).total_seconds() / 86400.0, 1 / 24)
    remaining = max((end - now).total_seconds() / 86400.0, 0.0)
    rate = trail / TRAIL_DAYS
    projected = round(month + rate * remaining, 6)
    history_from = clock.iso(day_start - datetime.timedelta(days=HISTORY_DAYS - 1))
    by_day = {r["key"]: r for r in board.spend(by="day", since=history_from)["rows"]}
    days = []
    for i in range(HISTORY_DAYS):
        key = (day_start - datetime.timedelta(days=HISTORY_DAYS - 1 - i)).strftime("%Y-%m-%d")
        row = by_day.get(key) or {}
        days.append({"day": key, "usd": round(float(row.get("usd") or 0), 6), "usd_equiv": row.get("usd_equiv") or 0})
    daily_cap = settings.get(budgets.DAILY)
    monthly_cap = settings.get(budgets.MONTHLY)
    name = board.name
    alerts = []
    if daily_cap is not None:
        if today >= daily_cap:
            alerts.append(
                _alert(name, "critical", "daily", f"today ${today:.2f} reached the daily cap ${daily_cap:.2f}")
            )
        elif daily_cap and today >= daily_cap * ALERT_PCT / 100:
            alerts.append(
                _alert(name, "warning", "daily", f"today ${today:.2f} is over {ALERT_PCT}% of ${daily_cap:.2f}")
            )
    if monthly_cap is not None:
        if month >= monthly_cap:
            alerts.append(
                _alert(name, "critical", "monthly", f"month ${month:.2f} reached the monthly cap ${monthly_cap:.2f}")
            )
        elif projected > monthly_cap:
            alerts.append(
                _alert(
                    name,
                    "warning",
                    "forecast",
                    f"on track for ${projected:.2f} by {end.strftime('%Y-%m-%d')}, over the ${monthly_cap:.2f} cap",
                )
            )
        elif monthly_cap and month >= monthly_cap * ALERT_PCT / 100:
            alerts.append(
                _alert(name, "warning", "monthly", f"month ${month:.2f} is over {ALERT_PCT}% of ${monthly_cap:.2f}")
            )
    return {
        "workspace": name,
        "now": clock.iso(now),
        "period": {
            "start": clock.iso(start),
            "end": clock.iso(end),
            "days_elapsed": round(elapsed, 3),
            "days_left": round(remaining, 3),
        },
        "count_plan": count_plan,
        "month": month,
        "month_api_equiv": month_equiv,
        "today": today,
        "burn": {
            "per_day_month": round(month / elapsed, 6),
            "per_day_trailing": round(rate, 6),
            "trail_days": TRAIL_DAYS,
        },
        "forecast": projected,
        "caps": {"daily": daily_cap, "monthly": monthly_cap},
        "by_day": days,
        "alerts": alerts,
    }


def combine(items, now=None):
    """Sum per-workspace forecasts into one view across workspaces."""
    now = now or clock.now()
    if any(f.get("error") for f in items):
        return {"workspace": "*", "error": "invalid-data", "alerts": [], "by_day": []}
    start, end = _period(now)
    days = {}
    for f in items:
        for d in f["by_day"]:
            row = days.setdefault(d["day"], {"day": d["day"], "usd": 0.0, "usd_equiv": 0.0})
            row["usd"] = round(row["usd"] + d["usd"], 6)
            row["usd_equiv"] = round(row["usd_equiv"] + float(d["usd_equiv"] or 0), 6)

    def total(key):
        return round(sum(f[key] for f in items), 6)

    return {
        "workspace": "*",
        "now": clock.iso(now),
        "period": {"start": clock.iso(start), "end": clock.iso(end)},
        "month": total("month"),
        "month_api_equiv": total("month_api_equiv"),
        "today": total("today"),
        "burn": {
            "per_day_month": round(sum(f["burn"]["per_day_month"] for f in items), 6),
            "per_day_trailing": round(sum(f["burn"]["per_day_trailing"] for f in items), 6),
            "trail_days": TRAIL_DAYS,
        },
        "forecast": total("forecast"),
        "by_day": [days[k] for k in sorted(days)],
        "alerts": [a for f in items for a in f["alerts"]],
        "workspaces": [{k: f[k] for k in ("workspace", "month", "today", "forecast", "caps", "alerts")} for f in items],
    }


def everywhere(now=None):
    """Forecasts for every registered workspace, combined."""
    from .core import Board, registry
    from .core.db import Index

    home = registry.home()
    items = []
    for w in registry.workspaces(Index(home)):
        try:
            items.append(forecast(Board.open(w["board"], home=home), now))
        except (RaidlockError, OSError, ValueError):
            continue
    return combine(items, now)


def _setup(parser):
    parser.add_argument("--all", action="store_true", help="every registered workspace, with alerts per workspace")


def _handle(ctx, args):
    f = everywhere() if args.all else forecast(ctx.board)
    if f.get("error"):
        return f, "invalid-data: money could not be aggregated"
    lines = [
        f"{f['workspace']}: ${f['month']:.2f} this month, ${f['today']:.2f} today",
        f"burn ${f['burn']['per_day_trailing']:.2f}/day over {TRAIL_DAYS} days; "
        f"forecast ${f['forecast']:.2f} by {f['period']['end'][:10]}",
    ]
    lines += [f"{a['level'].upper()} {a['workspace']}: {a['text']}" for a in f["alerts"]]
    return f, "\n".join(lines)


def register_cli(add_command):
    add_command("treasury", "burn rate, month-end forecast and budget alerts", _setup, _handle)
