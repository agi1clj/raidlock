import json
import os
import subprocess
import sys
from pathlib import Path

from raidlock import limits

NOW = 1793200000.0


def rl_line(ts, pct, secondary=None, plan="prolite"):
    return json.dumps(
        {
            "timestamp": ts,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"input_tokens": 1},
                    "rate_limits": {
                        "limit_id": "codex",
                        "primary": {"used_percent": pct, "window_minutes": 300, "resets_at": NOW + 3600},
                        "secondary": secondary,
                        "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
                        "plan_type": plan,
                        "rate_limit_reached_type": None,
                    },
                },
            },
        }
    )


def session(tmp_path, name, lines, mtime):
    d = tmp_path / "codex" / "sessions" / "2026" / "10" / "22"
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text("\n".join(lines) + "\n")
    os.utime(f, (mtime, mtime))
    return f


def test_codex_latest_record_wins(tmp_path):
    week = {"used_percent": 40.0, "window_minutes": 10080, "resets_at": NOW + 86400 * 3}
    session(
        tmp_path,
        "a.jsonl",
        [
            "garbage",
            rl_line("2026-10-22T10:00:00.000Z", 10.0),
            rl_line("2026-10-22T10:05:00.000Z", 25.0, week),
            '{"x":1}',
        ],
        NOW,
    )
    r = limits.codex_limits(now=1792965900.0)
    assert r["plan"] == "prolite" and r["limit_id"] == "codex"
    assert [w["window"] for w in r["windows"]] == ["5h", "weekly"]
    assert r["windows"][0]["used_percent"] == 25.0
    assert r["windows"][0]["resets_at"].endswith("Z")
    assert "balance" not in json.dumps(r)


def test_codex_newest_file_and_freshness(tmp_path):
    session(tmp_path, "old.jsonl", [rl_line("2026-10-20T10:00:00.000Z", 90.0)], NOW - 1000)
    session(tmp_path, "new.jsonl", [rl_line("2026-10-22T10:00:00.000Z", 5.0)], NOW)
    r = limits.codex_limits(now=1792965600.0 + 30 * 3600)
    assert r["windows"][0]["used_percent"] == 5.0
    assert r["status"] == "stale" and r["age_seconds"] > limits.STALE_AFTER


def test_codex_falls_back_to_older_file_without_record(tmp_path):
    session(tmp_path, "old.jsonl", [rl_line("2026-10-22T10:00:00.000Z", 7.0)], NOW - 1000)
    session(tmp_path, "new.jsonl", ['{"type":"x"}'], NOW)
    assert limits.codex_limits(now=NOW)["windows"][0]["used_percent"] == 7.0


def test_codex_tail_only_and_partial_line(tmp_path):
    big = ["x" * 200] * 2000
    f = session(
        tmp_path,
        "a.jsonl",
        [rl_line("2026-10-22T10:00:00.000Z", 1.0)] + big + [rl_line("2026-10-22T11:00:00.000Z", 2.0)],
        NOW,
    )
    assert len(f.read_bytes()) > limits.TAIL_BYTES
    assert limits.codex_limits(now=NOW)["windows"][0]["used_percent"] == 2.0


def test_codex_unavailable(tmp_path):
    assert limits.codex_limits()["status"] == "unavailable"
    session(tmp_path, "a.jsonl", ["nothing"], NOW)
    assert limits.codex_limits()["status"] == "unavailable"


def test_window_label():
    assert [limits.window_label(m) for m in (300, 10080, 1440, 45, None)] == ["5h", "weekly", "1d", "45m", None]


def test_claude_not_exposed_without_snapshot():
    r = limits.claude_limits()
    assert r["status"] == "not exposed" and r["doc"] == "https://code.claude.com/docs/en/statusline"


def test_claude_ingest_statusline_roundtrip():
    payload = {
        "model": {"display_name": "x"},
        "session_id": "secret-ish",
        "rate_limits": {
            "five_hour": {"used_percentage": 23.5, "resets_at": NOW + 600},
            "seven_day": {"used_percentage": 41.2, "resets_at": NOW + 86400},
            "spend_limit": {"used_usd": 3},
        },
    }
    assert limits.ingest_claude(json.dumps(payload), now=NOW)
    stored = Path(limits.claude_snapshot_path()).read_text()
    assert "secret-ish" not in stored and "spend_limit" not in stored
    r = limits.claude_limits(now=NOW + 60)
    assert r["status"] == "ok" and [w["window"] for w in r["windows"]] == ["5h", "weekly"]
    assert r["windows"][0]["used_percent"] == 23.5 and r["age_seconds"] == 60
    later = limits.claude_limits(now=NOW + 700)
    assert [w["window"] for w in later["windows"]] == ["weekly"]


def test_claude_ingest_rejects_absent_or_bad():
    assert not limits.ingest_claude("not json")
    assert not limits.ingest_claude('{"model":{}}')
    assert not os.path.exists(limits.claude_snapshot_path())


def test_gemini_placeholder():
    assert limits.gemini_limits()["status"] == "not supported"


def test_cli_json_and_text(tmp_path):
    session(tmp_path, "a.jsonl", [rl_line("2026-10-22T10:00:00.000Z", 12.0)], NOW)
    env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(limits.__file__)))
    out = subprocess.run(
        [sys.executable, "-m", "raidlock", "limits", "--json"], capture_output=True, text=True, env=env
    )
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert [p["provider"] for p in data["providers"]] == ["codex", "claude", "gemini"]
    text = subprocess.run([sys.executable, "-m", "raidlock", "limits"], capture_output=True, text=True, env=env).stdout
    assert "codex (prolite)" in text and "12% used" in text and "gemini: not supported" in text
    assert "stale" not in text


def token_line(ts, used, window, pct=20.0, resets=NOW + 3600):
    return json.dumps(
        {
            "timestamp": ts,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {"input_tokens": used, "cached_input_tokens": used - 10},
                    "model_context_window": window,
                },
                "rate_limits": {
                    "limit_id": "codex",
                    "primary": {"used_percent": pct, "window_minutes": 10080, "resets_at": resets},
                    "secondary": None,
                    "plan_type": "prolite",
                },
            },
        }
    )


SEEN = 1792663200.0


def test_codex_context_from_the_same_record(tmp_path):
    session(tmp_path, "a.jsonl", [token_line("2026-10-22T10:00:00.000Z", 95000, 475000)], NOW)
    r = limits.codex_limits(now=SEEN + 120)
    assert r["context"] == {
        "used_percent": 20.0,
        "used_tokens": 95000,
        "window_tokens": 475000,
        "source": "codex session log",
    }
    assert r["fresh"] is True and r["age_seconds"] == 120


def test_codex_drops_a_window_that_already_reset(tmp_path):
    session(tmp_path, "a.jsonl", [token_line("2026-10-22T10:00:00.000Z", 1000, 475000, resets=NOW - 5)], NOW)
    r = limits.codex_limits(now=NOW)
    assert r["windows"] == [] and r["context"]["used_percent"] == 0.2


def test_codex_old_record_is_not_fresh(tmp_path):
    session(tmp_path, "a.jsonl", [token_line("2026-10-22T10:00:00.000Z", 1000, 475000)], NOW)
    r = limits.codex_limits(now=SEEN + limits.FRESH_FOR + 1)
    assert r["fresh"] is False and r["status"] == "ok"


STATUSLINE = {
    "session_id": "session-one",
    "transcript_path": "/nowhere/session-one.jsonl",
    "model": {"id": "claude-opus-5-5", "display_name": "Opus"},
    "cost": {"total_cost_usd": 1.25, "total_duration_ms": 600000},
    "context_window": {"context_window_size": 1000000, "used_percentage": 12.5},
    "rate_limits": {
        "five_hour": {"used_percentage": 30, "resets_at": NOW + 3600},
        "seven_day": {"used_percentage": 55, "resets_at": NOW + 86400},
    },
}


def test_statusline_records_context_cost_and_plan_windows():
    assert limits.ingest_claude(json.dumps(STATUSLINE), now=NOW)
    stored = Path(limits.claude_snapshot_path()).read_text()
    assert 'session-one"' not in stored
    r = limits.claude_limits(now=NOW + 30)
    assert r["status"] == "ok" and r["fresh"] is True
    assert r["context"]["used_percent"] == 12.5 and r["context"]["window_tokens"] == 1000000
    assert r["context"]["assumed_window"] is False and r["context"]["source"] == "statusline"
    assert r["session"] == {"cost_usd": 1.25, "duration_ms": 600000, "model": "claude-opus-5-5"}
    assert [(w["window"], w["used_percent"]) for w in r["windows"]] == [("5h", 30), ("weekly", 55)]
    assert "/nowhere" not in json.dumps(r)


def test_statusline_without_plan_windows_still_records_context():
    payload = {k: v for k, v in STATUSLINE.items() if k != "rate_limits"}
    assert limits.ingest_claude(json.dumps(payload), now=NOW)
    r = limits.claude_limits(now=NOW)
    assert r["windows"] == [] and r["context"]["used_percent"] == 12.5


def test_schema_two_snapshot_still_reads():
    path = Path(limits.claude_snapshot_path())
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema": 2, "observed": NOW, "rate_limits": STATUSLINE["rate_limits"]}))
    r = limits.claude_limits(now=NOW + 60)
    assert [w["window"] for w in r["windows"]] == ["5h", "weekly"] and r["age_seconds"] == 60


def transcript(tmp_path, name, rows, mtime=NOW):
    f = tmp_path / f"{name}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    os.utime(f, (mtime, mtime))
    return f


def assistant(inp, read, made, model="claude-sonnet-5-5"):
    usage = {"input_tokens": inp, "cache_read_input_tokens": read, "cache_creation_input_tokens": made}
    return {"type": "assistant", "message": {"role": "assistant", "model": model, "usage": usage}}


def test_hook_records_context_from_the_transcript(tmp_path):
    f = transcript(tmp_path, "s", [{"type": "user"}, assistant(10, 20, 30), assistant(1000, 40000, 9000)])
    assert limits.ingest_transcript({"session_id": "s", "transcript_path": str(f)}, now=NOW + 5)
    r = limits.claude_limits(now=NOW + 10)
    assert r["context"]["used_tokens"] == 50000 and r["context"]["window_tokens"] == 200000
    assert r["context"]["used_percent"] == 25.0 and r["context"]["assumed_window"] is True
    assert r["context"]["source"] == "transcript" and r["windows"] == []
    assert not limits.ingest_transcript({"session_id": "s", "transcript_path": str(f)}, now=NOW + 20)


def test_hook_uses_a_known_window(tmp_path):
    home = Path(os.environ["RAIDLOCK_HOME"])
    home.mkdir(parents=True)
    (home / "pricing.json").write_text(json.dumps({"models": {"claude-sonnet-5-5": {"context_window": 1000000}}}))
    f = transcript(tmp_path, "s", [assistant(0, 100000, 0)])
    assert limits.ingest_transcript({"session_id": "s", "transcript_path": str(f)}, now=NOW)
    ctx = limits.claude_limits(now=NOW)["context"]
    assert ctx["used_percent"] == 10.0 and ctx["assumed_window"] is False


def test_hook_keeps_the_statusline_window(tmp_path):
    f = transcript(tmp_path, "s", [assistant(0, 300000, 0)], mtime=NOW + 200)
    line = dict(STATUSLINE, session_id="s", transcript_path=str(f))
    assert limits.ingest_claude(json.dumps(line), now=NOW)
    assert limits.ingest_transcript({"session_id": "s", "transcript_path": str(f)}, now=NOW + 200)
    r = limits.claude_limits(now=NOW + 200)
    assert r["context"]["used_percent"] == 30.0 and r["context"]["window_tokens"] == 1000000
    assert r["session"]["cost_usd"] == 1.25


def test_old_record_refreshes_from_a_moved_transcript(tmp_path):
    f = transcript(tmp_path, "s", [assistant(0, 20000, 0)], mtime=NOW)
    assert limits.ingest_transcript({"session_id": "s", "transcript_path": str(f)}, now=NOW)
    later = NOW + limits.FRESH_FOR + 60
    transcript(tmp_path, "s", [assistant(0, 20000, 0), assistant(0, 80000, 0)], mtime=later - 10)
    r = limits.claude_limits(now=later)
    assert r["context"]["used_tokens"] == 80000 and r["age_seconds"] == 10


def test_ingest_ignores_bad_transcripts(tmp_path):
    assert not limits.ingest_transcript({"session_id": "s", "transcript_path": str(tmp_path / "missing.jsonl")})
    f = transcript(tmp_path, "s", [{"type": "user"}, {"type": "assistant", "message": {"content": "hi"}}])
    assert not limits.ingest_transcript({"session_id": "s", "transcript_path": str(f)})
    assert not limits.ingest_transcript({"transcript_path": 3})
    assert limits.claude_limits()["status"] == "not exposed"


def settings_file():
    return Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"


def test_install_statusline_chains_and_uninstall_restores(tmp_path):
    previous = {"type": "command", "command": "printf 'mine:'; cat >/dev/null; printf ok", "padding": 1}
    settings_file().parent.mkdir(parents=True)
    settings_file().write_text(json.dumps({"theme": "dark", "statusLine": previous}))
    r = limits.install_statusline()
    assert r["changed"] and r["chained"]
    data = json.loads(settings_file().read_text())
    assert data["theme"] == "dark" and data["statusLine"]["command"].endswith("limits --statusline")
    assert data["statusLine"]["padding"] == 1 and data["statusLine"]["refreshInterval"] == limits.STATUSLINE_REFRESH
    assert limits.statusline_installed()
    assert not limits.install_statusline()["changed"]
    assert limits.statusline(json.dumps(STATUSLINE)) == "mine:ok"
    assert limits.claude_limits()["context"]["used_percent"] == 12.5
    assert limits.uninstall_statusline()["restored"]
    assert json.loads(settings_file().read_text()) == {"theme": "dark", "statusLine": previous}
    assert not limits.uninstall_statusline()["changed"]


def test_statusline_without_previous_prints_its_own_line():
    assert limits.install_statusline()["chained"] is False
    assert limits.statusline(json.dumps(STATUSLINE)) == "ctx 12% · 5h 30% · wk 55%"
    assert limits.statusline("not json") == ""
    limits.uninstall_statusline()
    assert "statusLine" not in json.loads(settings_file().read_text())


def test_cli_statusline_prints_only_the_line(tmp_path):
    env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(limits.__file__)))
    out = subprocess.run(
        [sys.executable, "-m", "raidlock", "limits", "--statusline"],
        input=json.dumps(STATUSLINE),
        capture_output=True,
        text=True,
        env=env,
    )
    assert out.returncode == 0 and out.stdout == "ctx 12% · 5h 30% · wk 55%"


def test_hook_assumes_the_extended_window_past_the_default(tmp_path):
    f = transcript(tmp_path, "s", [assistant(1000, 499000, 0)])
    assert limits.ingest_transcript({"session_id": "s", "transcript_path": str(f)}, now=NOW)
    ctx = limits.claude_limits(now=NOW)["context"]
    assert ctx["window_tokens"] == 1000000 and ctx["used_percent"] == 50.0 and ctx["assumed_window"] is True
