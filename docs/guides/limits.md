# Limits and context use

`raid limits` shows, per provider, how full the current session's context is and how much of the plan's 5-hour and weekly windows is used. The web UI shows the same data in the strip at the top of the page. raidlock only reads what each tool already writes; it never calls a provider API and never guesses a value it was not given.

```bash
raid limits
raid limits --json
```

## What each provider exposes

| Field | Codex CLI | Claude Code | Gemini CLI |
|---|---|---|---|
| Context used | yes, from the session log | yes, from the transcript (plugin hook) or the statusline | no |
| 5-hour window, with reset time | yes, when the plan reports one | only through the statusline, Pro and Max plans | no |
| Weekly window, with reset time | yes | only through the statusline, Pro and Max plans | no |
| Session cost | no | only through the statusline (Claude Code's own estimate at list price) | no |
| Session duration | no | only through the statusline | no |

A provider with no data is not drawn in the web UI. Its status (`not exposed`, `not supported`, `unavailable`) and what to do about it are in the tooltip of the strip and in the **Settings** menu.

### Codex

raidlock reads the newest `token_count` record in the session logs under `CODEX_HOME` (default `~/.codex/sessions`). That one record carries both the plan windows (`rate_limits.primary`, `rate_limits.secondary`, `plan_type`) and the context: `info.last_token_usage.input_tokens` over `info.model_context_window`. A window whose `resets_at` has passed is dropped, because its percentage no longer applies.

### Claude Code

Claude Code hands usage data to two places, and raidlock reads both.

1. **Plugin hook (automatic).** With the raidlock plugin enabled, the `PostToolUse` hook reads the tail of the main session's transcript (`transcript_path` in the hook payload) at most once a minute and records the prompt side of the newest assistant turn: input, cache read and cache write tokens. Hook payloads carry no context window size, so raidlock uses `context_window` for that model from `$RAIDLOCK_HOME/pricing.json`, or the window the statusline last reported for the same session. Without either, it assumes 200,000 tokens, or 1,000,000 once the prompt is larger than 200,000 (the two window sizes Claude Code reports), and marks the reading as an assumed window. Subagent sessions are skipped.
2. **Statusline (one-time setup).** Plan usage, cost and duration exist only in the JSON Claude Code passes to a statusline command: `context_window.used_percentage`, `context_window.context_window_size`, `cost.total_cost_usd`, `cost.total_duration_ms`, and `rate_limits.five_hour` and `rate_limits.seven_day` (`used_percentage`, `resets_at`). `rate_limits` appears only for Claude.ai Pro and Max subscribers, and only after the session's first API response. A plugin cannot set the main statusline, so install it once:

```bash
raid limits --install-statusline
```

This sets `statusLine` in `~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR/settings.json`) to `raid limits --statusline` with `refreshInterval: 300`. A statusline you already had keeps working: its command is saved in `$RAIDLOCK_HOME/claude-statusline.json`, receives the same input, and its output is what Claude Code shows. Without one, raidlock prints a short line such as `ctx 12% · 5h 30% · wk 55%`. Undo it with:

```bash
raid limits --uninstall-statusline
```

`raid limits --ingest-claude` still reads one statusline JSON document on stdin, for custom statusline scripts.

raidlock stores percentages, reset times, context size, cost, duration, the model id and the transcript path in `$RAIDLOCK_HOME/claude-limits.json` (mode 0600), keyed by a hash of the session id. The transcript path never leaves that file. The newest session is the one shown; the 5-hour and weekly windows are account-wide and come from the newest statusline reading.

Without the statusline install, Claude Code shows context only. Plan usage stays hidden rather than estimated.

### Gemini CLI

Gemini CLI writes no local record of plan usage or context, so Gemini is not shown.

## Freshness

Every reading shows its age: `updated 4 min ago`. When a Claude Code reading is more than 10 minutes old and its transcript has changed since, the next read refreshes the context from the transcript on its own. The statusline's `refreshInterval: 300` keeps plan usage under 10 minutes old while a session is open. The web server re-reads every source every 30 seconds. Codex readings refresh whenever Codex writes a new record; an idle Codex shows the age of its last one.

Routing (`raid next`, `raid delegate` with routing rules) treats a reading older than six hours as unknown headroom.
