import contextlib
import datetime
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

from raidlock.core import clock, registry, store

CLAUDE_DOC = "https://code.claude.com/docs/en/statusline"
FILES_SCANNED = 5
TAIL_BYTES = 262144
STALE_AFTER = 6 * 3600
FRESH_FOR = 600
SESSIONS_KEPT = 20
DEFAULT_WINDOW = 200000
EXTENDED_WINDOW = 1000000
HOOK_EVERY = 60
STATUSLINE_REFRESH = 300
CHAINED_TIMEOUT = 5
PROMPT_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def codex_home():
    return os.path.abspath(os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex"))


def window_label(minutes):
    if not isinstance(minutes, (int, float)) or minutes <= 0:
        return None
    if minutes % 10080 == 0:
        weeks = int(minutes // 10080)
        return "weekly" if weeks == 1 else f"{weeks}w"
    if minutes % 1440 == 0:
        return f"{int(minutes // 1440)}d"
    if minutes % 60 == 0:
        return f"{int(minutes // 60)}h"
    return f"{int(minutes)}m"


def iso_epoch(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return datetime.datetime.fromtimestamp(value, datetime.UTC).strftime(clock.FORMAT)
    except (OverflowError, OSError, ValueError):
        return None


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def tail_lines(path, size=TAIL_BYTES):
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        end = fh.tell()
        start = max(0, end - size)
        fh.seek(start)
        data = fh.read()
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]
    return lines


def newest_session_files(root, count=FILES_SCANNED):
    found = []
    for base, _dirs, names in os.walk(root):
        for name in names:
            if name.endswith(".jsonl"):
                path = os.path.join(base, name)
                try:
                    found.append((os.stat(path).st_mtime, path))
                except OSError:
                    continue
    found.sort(reverse=True)
    return [path for _mtime, path in found[:count]]


def find_rate_limits(obj):
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("rate_limits"), dict):
        return obj["rate_limits"]
    payload = obj.get("payload")
    if isinstance(payload, dict):
        if isinstance(payload.get("rate_limits"), dict):
            return payload["rate_limits"]
        info = payload.get("info")
        if isinstance(info, dict) and isinstance(info.get("rate_limits"), dict):
            return info["rate_limits"]
    return None


def record_time(obj, path):
    ts = obj.get("timestamp") if isinstance(obj, dict) else None
    if isinstance(ts, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.datetime.strptime(ts, fmt).replace(tzinfo=datetime.UTC).timestamp()
            except ValueError:
                continue
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def codex_window(entry, now):
    if not isinstance(entry, dict):
        return None
    pct = number(entry.get("used_percent"))
    if pct is None:
        return None
    resets = number(entry.get("resets_at"))
    if resets is not None and resets <= now:
        return None
    return {
        "window": window_label(number(entry.get("window_minutes"))),
        "window_minutes": number(entry.get("window_minutes")),
        "used_percent": pct,
        "resets_at": iso_epoch(resets),
        "resets_in_seconds": max(0, int(resets - now)) if resets is not None else None,
    }


def percent(used, size):
    if used is None or not size or size <= 0:
        return None
    return round(min(100.0, max(0.0, 100.0 * used / size)), 1)


def codex_context(obj):
    payload = obj.get("payload") if isinstance(obj, dict) else None
    info = payload.get("info") if isinstance(payload, dict) else None
    if not isinstance(info, dict):
        return None
    last = info.get("last_token_usage")
    size = number(info.get("model_context_window"))
    used = number(last.get("input_tokens")) if isinstance(last, dict) else None
    pct = percent(used, size)
    if pct is None:
        return None
    return {"used_percent": pct, "used_tokens": int(used), "window_tokens": int(size), "source": "codex session log"}


def freshness(base, seen, now):
    age = max(0, int(now - seen)) if seen is not None else None
    return {
        **base,
        "status": "ok" if age is None or age <= STALE_AFTER else "stale",
        "fresh": age is not None and age <= FRESH_FOR,
        "observed_at": iso_epoch(seen),
        "age_seconds": age,
    }


def codex_limits(now=None):
    now = time.time() if now is None else now
    root = os.path.join(codex_home(), "sessions")
    base = {"provider": "codex", "source": "codex session logs"}
    if not os.path.isdir(root):
        return {**base, "status": "unavailable", "detail": "no Codex sessions found"}
    for path in newest_session_files(root):
        try:
            lines = tail_lines(path)
        except OSError:
            continue
        for raw in reversed(lines):
            if b"rate_limits" not in raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            rl = find_rate_limits(obj)
            if not rl:
                continue
            seen = record_time(obj, path)
            windows = [w for w in (codex_window(rl.get("primary"), now), codex_window(rl.get("secondary"), now)) if w]
            credits = rl.get("credits") if isinstance(rl.get("credits"), dict) else {}
            reached = rl.get("rate_limit_reached_type")
            return freshness(
                {
                    **base,
                    "limit_id": rl.get("limit_id") if isinstance(rl.get("limit_id"), str) else None,
                    "plan": rl.get("plan_type") if isinstance(rl.get("plan_type"), str) else None,
                    "windows": windows,
                    "context": codex_context(obj),
                    "limit_reached": reached if isinstance(reached, str) else None,
                    "credits": {
                        "has_credits": bool(credits.get("has_credits")),
                        "unlimited": bool(credits.get("unlimited")),
                    }
                    if credits
                    else None,
                },
                seen,
                now,
            )
    return {**base, "status": "unavailable", "detail": "no rate_limits record in the newest sessions"}


def claude_snapshot_path():
    return os.path.join(registry.home(), "claude-limits.json")


def claude_settings_path():
    root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(os.path.abspath(root), "settings.json")


def statusline_state_path():
    return os.path.join(registry.home(), "claude-statusline.json")


def claude_windows(rl, now):
    windows = []
    for key, label, minutes in (("five_hour", "5h", 300), ("seven_day", "weekly", 10080)):
        entry = rl.get(key)
        if not isinstance(entry, dict):
            continue
        pct = number(entry.get("used_percentage"))
        if pct is None:
            continue
        resets = number(entry.get("resets_at"))
        if resets is not None and resets <= now:
            continue
        windows.append(
            {
                "window": label,
                "window_minutes": minutes,
                "used_percent": pct,
                "resets_at": iso_epoch(resets),
                "resets_in_seconds": max(0, int(resets - now)) if resets is not None else None,
            }
        )
    return windows


def session_key(session_id):
    return hashlib.sha256(str(session_id or "").encode("utf-8")).hexdigest()[:12]


def load_snapshot():
    try:
        with open(claude_snapshot_path(), encoding="utf-8") as fh:
            snap = json.load(fh)
    except (OSError, ValueError):
        return {"schema": 3, "sessions": {}}
    if not isinstance(snap, dict):
        return {"schema": 3, "sessions": {}}
    if snap.get("schema") == 2 and isinstance(snap.get("rate_limits"), dict):
        return {"schema": 3, "rate_limits": snap["rate_limits"], "rate_observed": snap.get("observed"), "sessions": {}}
    if not isinstance(snap.get("sessions"), dict):
        snap["sessions"] = {}
    return snap


def save_snapshot(snap):
    sessions = snap.get("sessions") or {}
    keep = sorted(sessions.items(), key=lambda kv: -(number(kv[1].get("observed")) or 0))[:SESSIONS_KEPT]
    snap = {**snap, "schema": 3, "sessions": dict(keep)}
    path = claude_snapshot_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    store.atomic_write_text(path, json.dumps(snap, sort_keys=True))
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def statusline_rates(rl):
    out = {}
    for key in ("five_hour", "seven_day"):
        entry = rl.get(key) if isinstance(rl, dict) else None
        if isinstance(entry, dict) and number(entry.get("used_percentage")) is not None:
            out[key] = {"used_percentage": entry["used_percentage"], "resets_at": number(entry.get("resets_at"))}
    return out


def statusline_session(data, now):
    ctx = data.get("context_window") if isinstance(data.get("context_window"), dict) else {}
    cost = data.get("cost") if isinstance(data.get("cost"), dict) else {}
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    row = {
        "observed": now,
        "source": "statusline",
        "context_percent": number(ctx.get("used_percentage")),
        "context_window": number(ctx.get("context_window_size")),
        "cost_usd": number(cost.get("total_cost_usd")),
        "duration_ms": number(cost.get("total_duration_ms")),
        "model": model.get("id") if isinstance(model.get("id"), str) else None,
    }
    if isinstance(data.get("transcript_path"), str):
        row["transcript"] = data["transcript_path"]
    return row if any(row[k] is not None for k in ("context_percent", "cost_usd", "duration_ms")) else None


def ingest_claude(text, now=None):
    """Record a Claude Code statusline input: plan windows, and the session's context, cost and duration."""
    now = time.time() if now is None else now
    try:
        data = json.loads(text)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    rates = statusline_rates(data.get("rate_limits"))
    row = statusline_session(data, now)
    if not rates and not row:
        return False
    snap = load_snapshot()
    if rates:
        snap["rate_limits"] = rates
        snap["rate_observed"] = now
    if row:
        snap["sessions"][session_key(data.get("session_id"))] = row
    save_snapshot(snap)
    return True


def context_windows(home=None):
    try:
        data = store.read_json(os.path.join(home or registry.home(), "pricing.json")) or {}
    except Exception:
        data = {}
    out = {}
    for model, rates in (data.get("models") or {}).items() if isinstance(data, dict) else ():
        value = (rates or {}).get("context_window") if isinstance(rates, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            out[model] = value
    return out


def transcript_usage(path):
    """The prompt side of the newest assistant turn in a Claude Code transcript: (tokens, model, mtime)."""
    try:
        mtime = os.stat(path).st_mtime
        lines = tail_lines(path)
    except OSError:
        return None
    for raw in reversed(lines):
        if b'"usage"' not in raw or b'"assistant"' not in raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        msg = rec.get("message") if isinstance(rec, dict) else None
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if not isinstance(usage, dict) or rec.get("isSidechain"):
            continue
        parts = [number(usage.get(k)) for k in PROMPT_FIELDS]
        if all(p is None for p in parts):
            continue
        model = msg.get("model") if isinstance(msg.get("model"), str) and not msg["model"].startswith("<") else None
        return int(sum(p or 0 for p in parts)), model, mtime
    return None


def transcript_row(path, prior, now):
    found = transcript_usage(path)
    if not found:
        return None
    tokens, model, mtime = found
    model = model or (prior or {}).get("model")
    known = number((prior or {}).get("context_window"))
    window = known or context_windows().get(model or "")
    assumed = window or (EXTENDED_WINDOW if tokens > DEFAULT_WINDOW else DEFAULT_WINDOW)
    row = {
        "observed": min(now, mtime),
        "source": "transcript",
        "context_tokens": tokens,
        "context_window": assumed,
        "context_assumed": not window,
        "context_percent": percent(tokens, assumed),
        "model": model,
        "transcript": path,
    }
    for k in ("cost_usd", "duration_ms"):
        if (prior or {}).get(k) is not None:
            row[k] = prior[k]
    return row


def ingest_transcript(data, now=None):
    """Hook path: record context use from the transcript the hook payload names, at most once a minute a session."""
    now = time.time() if now is None else now
    path = data.get("transcript_path") if isinstance(data, dict) else None
    if not isinstance(path, str) or not path.endswith(".jsonl") or not os.path.isfile(path):
        return False
    snap = load_snapshot()
    key = session_key(data.get("session_id"))
    prior = snap["sessions"].get(key)
    if prior and now - (number(prior.get("observed")) or 0) < HOOK_EVERY:
        return False
    row = transcript_row(path, prior, now)
    if not row:
        return False
    snap["sessions"][key] = row
    save_snapshot(snap)
    return True


def refresh_sessions(snap, now):
    """Re-read the newest session's transcript when its record is over FRESH_FOR old and the transcript moved on."""
    sessions = snap.get("sessions") or {}
    if not sessions:
        return snap
    key, row = max(sessions.items(), key=lambda kv: number(kv[1].get("observed")) or 0)
    path = row.get("transcript")
    if not isinstance(path, str) or now - (number(row.get("observed")) or 0) <= FRESH_FOR:
        return snap
    try:
        moved = os.stat(path).st_mtime > (number(row.get("observed")) or 0)
    except OSError:
        return snap
    if not moved:
        return snap
    fresh = transcript_row(path, row, now)
    if fresh:
        snap["sessions"][key] = fresh
        with contextlib.suppress(OSError):
            save_snapshot(snap)
    return snap


def statusline_installed():
    try:
        with open(claude_settings_path(), encoding="utf-8") as fh:
            line = (json.load(fh) or {}).get("statusLine") or {}
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(line, dict) and "limits --statusline" in str(line.get("command", ""))


def claude_limits(now=None):
    now = time.time() if now is None else now
    base = {"provider": "claude", "source": "Claude Code statusline and session transcript", "doc": CLAUDE_DOC}
    snap = refresh_sessions(load_snapshot(), now)
    rates = snap.get("rate_limits") if isinstance(snap.get("rate_limits"), dict) else {}
    windows = claude_windows(rates, now)
    sessions = snap.get("sessions") or {}
    newest = max(sessions.values(), key=lambda r: number(r.get("observed")) or 0) if sessions else None
    if not windows and not newest:
        return {
            **base,
            "status": "not exposed",
            "detail": "No Claude Code session recorded yet. With the raidlock plugin, context use is recorded "
            "on the next tool call; plan usage (5h and weekly) comes only from the statusline: "
            "run `raid limits --install-statusline` once.",
        }
    seen = [number(snap.get("rate_observed")) if windows else None, number((newest or {}).get("observed"))]
    seen = max((s for s in seen if s is not None), default=None)
    out = {**base, "plan": None, "windows": windows}
    if newest:
        ctx = None
        if newest.get("context_percent") is not None:
            ctx = {
                "used_percent": newest["context_percent"],
                "used_tokens": newest.get("context_tokens"),
                "window_tokens": number(newest.get("context_window")),
                "assumed_window": bool(newest.get("context_assumed")),
                "source": newest.get("source", "statusline"),
            }
        out["context"] = ctx
        out["session"] = {
            k: newest[k] for k in ("cost_usd", "duration_ms", "model") if newest.get(k) is not None
        } or None
        out["sessions_recent"] = sum(
            1 for r in sessions.values() if now - (number(r.get("observed")) or 0) <= FRESH_FOR
        )
    return freshness(out, seen, now)


def gemini_limits():
    return {"provider": "gemini", "status": "not supported", "detail": "no local source for Gemini plan limits"}


def collect(now=None):
    return {"providers": [codex_limits(now), claude_limits(now), gemini_limits()]}


def human_age(seconds):
    if seconds is None:
        return "at an unknown time"
    if seconds < 90:
        return f"{seconds}s ago"
    if seconds < 5400:
        return f"{seconds // 60} min ago"
    if seconds < 172800:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


def human_reset(seconds):
    if seconds is None:
        return "reset time not reported"
    if seconds < 5400:
        return f"resets in {max(1, seconds // 60)} min"
    if seconds < 172800:
        return f"resets in {seconds // 3600} h"
    return f"resets in {seconds // 86400} d"


def render(result):
    lines = []
    for p in result["providers"]:
        name = p["provider"]
        if p["status"] in ("ok", "stale"):
            plan = f" ({p['plan']})" if p.get("plan") else ""
            lines.append(f"{name}{plan}: updated {human_age(p.get('age_seconds'))}")
            ctx = p.get("context")
            if ctx:
                note = ", assumed window" if ctx.get("assumed_window") else ""
                lines.append(f"  context: {ctx['used_percent']:g}% used{note}")
            for w in p.get("windows") or []:
                lines.append(
                    f"  {w['window'] or 'window'}: {w['used_percent']:g}% used, {human_reset(w['resets_in_seconds'])}"
                )
            sess = p.get("session") or {}
            if sess.get("cost_usd") is not None:
                lines.append(f"  session cost (Claude Code estimate): ${sess['cost_usd']:.2f}")
            if p.get("limit_reached"):
                lines.append(f"  limit reached: {p['limit_reached']}")
        else:
            lines.append(f"{name}: {p['status']}" + (f" ({p['detail']})" if p.get("detail") else ""))
    return "\n".join(lines)


def raid_command():
    found = shutil.which("raid") or shutil.which("raidlock")
    if found:
        return shlex.quote(found)
    return f"{shlex.quote(sys.executable)} -m raidlock"


def ours(line):
    return isinstance(line, dict) and "limits --statusline" in str(line.get("command", ""))


def read_settings(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def install_statusline():
    """Point Claude Code's statusLine at `raid limits --statusline`, keeping the previous command chained."""
    path = claude_settings_path()
    settings = read_settings(path)
    current = settings.get("statusLine")
    if ours(current):
        return {"installed": True, "changed": False, "settings": path}
    store.atomic_write_text(statusline_state_path(), json.dumps({"previous": current}, sort_keys=True))
    line = {"type": "command", "command": f"{raid_command()} limits --statusline"}
    if isinstance(current, dict):
        for k in ("padding", "refreshInterval"):
            if k in current:
                line[k] = current[k]
    line.setdefault("refreshInterval", STATUSLINE_REFRESH)
    settings["statusLine"] = line
    store.atomic_write_text(path, json.dumps(settings, indent=2) + "\n")
    return {"installed": True, "changed": True, "settings": path, "chained": bool(current)}


def uninstall_statusline():
    path = claude_settings_path()
    settings = read_settings(path)
    if not ours(settings.get("statusLine")):
        return {"installed": False, "changed": False, "settings": path}
    try:
        with open(statusline_state_path(), encoding="utf-8") as fh:
            previous = json.load(fh).get("previous")
    except (OSError, ValueError, AttributeError):
        previous = None
    if previous:
        settings["statusLine"] = previous
    else:
        settings.pop("statusLine", None)
    store.atomic_write_text(path, json.dumps(settings, indent=2) + "\n")
    with contextlib.suppress(OSError):
        os.remove(statusline_state_path())
    return {"installed": False, "changed": True, "settings": path, "restored": bool(previous)}


def previous_command():
    try:
        with open(statusline_state_path(), encoding="utf-8") as fh:
            prev = json.load(fh).get("previous")
    except (OSError, ValueError, AttributeError):
        return None
    if isinstance(prev, dict) and isinstance(prev.get("command"), str) and not ours(prev):
        return prev["command"]
    return None


def own_line(text):
    try:
        data = json.loads(text)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    bits = []
    ctx = data.get("context_window") if isinstance(data.get("context_window"), dict) else {}
    if number(ctx.get("used_percentage")) is not None:
        bits.append(f"ctx {ctx['used_percentage']:.0f}%")
    for key, label in (("five_hour", "5h"), ("seven_day", "wk")):
        entry = (data.get("rate_limits") or {}).get(key) if isinstance(data.get("rate_limits"), dict) else None
        if isinstance(entry, dict) and number(entry.get("used_percentage")) is not None:
            bits.append(f"{label} {entry['used_percentage']:.0f}%")
    return " · ".join(bits)


def statusline(text):
    """Statusline entry point: record the input, then print the chained command's output or a short line."""
    with contextlib.suppress(OSError, ValueError, TypeError):
        ingest_claude(text)
    command = previous_command()
    if command:
        try:
            done = subprocess.run(
                [shutil.which("sh") or "/bin/sh", "-c", command],
                input=text,
                capture_output=True,
                text=True,
                timeout=CHAINED_TIMEOUT,
            )
            return done.stdout
        except (OSError, subprocess.SubprocessError):
            return ""
    return own_line(text)


def register_cli(add_command):
    def setup(p):
        p.add_argument(
            "--ingest-claude",
            action="store_true",
            help="read Claude Code statusline JSON on stdin and record plan windows, context, cost and duration",
        )
        p.add_argument(
            "--statusline",
            action="store_true",
            help="Claude Code statusLine command: record the input, then print the previous statusline's output",
        )
        p.add_argument(
            "--install-statusline",
            action="store_true",
            help="set Claude Code's statusLine to raid limits --statusline, chaining the current one",
        )
        p.add_argument(
            "--uninstall-statusline", action="store_true", help="restore the statusLine that was there before"
        )

    def handler(ctx, a):
        if a.statusline:
            sys.stdout.write(statusline(sys.stdin.read()))
            sys.stdout.flush()
            return {}, ""
        if a.install_statusline:
            r = install_statusline()
            return r, ("statusline installed in " if r["changed"] else "statusline already installed in ") + r[
                "settings"
            ]
        if a.uninstall_statusline:
            r = uninstall_statusline()
            return r, ("statusline restored in " if r["changed"] else "no raidlock statusline in ") + r["settings"]
        if a.ingest_claude:
            ok = ingest_claude(sys.stdin.read())
            return {"recorded": ok}, "recorded" if ok else "nothing to record in input"
        result = collect()
        return result, render(result)

    add_command("limits", "plan limits and context use per provider (Codex, Claude Code, Gemini)", setup, handler)
