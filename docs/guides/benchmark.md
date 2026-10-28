# Overhead benchmark

## Headline: 189 tokens per Claude Code session

Measured in real Claude Code sessions, the plugin (both skill listings, the `raid-worker` agent listing and every hook) adds **189 input tokens** to a session, counted by Anthropic's tokenizer. Idle hooks print nothing. The MCP server adds 221 more while Claude Code's tool search defers its tool schemas, and 1,661 with tool search turned off.

| Configuration | Input tokens added per session |
| --- | ---: |
| Plugin: skills, agent, hooks | 189 |
| MCP server, tool search on (default) | 221 |
| Plugin and MCP server | 410 |
| MCP server, tool search off (`ENABLE_TOOL_SEARCH=false`) | 1,661 |

Method: [`scripts/overhead_ab.sh`](../../scripts/overhead_ab.sh) sends the same one-word prompt through `claude -p --output-format json --setting-sources "" --strict-mcp-config` in a workspace with a raidlock board, with and without `--plugin-dir` and `--mcp-config`, and subtracts the input tokens Claude Code reports (`input_tokens` plus cache creation plus cache reads). Recorded with Claude Code 2.1.308 on the `haiku` model on 2026-10-23; three repeated plugin runs and a second full run gave identical counts. It makes six short paid requests with your own login, so it never runs in CI and asks for `--yes`:

```bash
bash scripts/overhead_ab.sh --yes          # optional second argument: model, default haiku
```

The numbers depend on the Claude Code version and its listing format. CI instead runs the offline harness below on every push and prints its estimate (128 tokens for the plugin, 752 with the MCP `tools/list` response, at four characters per token) in the job summary of the `lint, types, overhead` job. The offline estimate is about a third below the measured plugin count: it is a characters/4 heuristic rather than Claude's tokenizer, and it leaves out whatever framing Claude Code adds around listings. Quote the measured number; use the estimate to catch regressions.

## Offline harness

Run from a source checkout with Python 3.11 or newer. The harness uses only the standard library, makes no network requests and creates its board, registry and hook bindings in a temporary directory. It does not use your board or credentials. Installed wheels need `--plugin-root /path/to/raidlock-checkout` because plugin listing files live outside the Python package.

```bash
python scripts/bench_overhead.py --runs 20 --tokenizer estimate \
  --budget tests/bench/budget.json > overhead.json 2> overhead.md
raid bench --runs 20 --budget tests/bench/budget.json
raid bench --runs 20 --json
```

The script writes JSON to stdout and a Markdown table to stderr. `raid bench` displays both; `--json` returns only JSON using the usual CLI envelope. A failed budget returns exit status 1 and includes every exceeded limit in `failures`. Invalid budgets (including unknown metrics, negative limits and nonfinite numbers) fail as usage errors. Equal-to-budget values pass. Budgets are JSON objects mapping flat metric names to inclusive upper limits; omitted metrics are unbudgeted.

```json
{"idle.estimated_tokens": 154, "hook.pre-write.allow.bytes": 0, "brief.cap_ratio": 1}
```

The `lint, types, overhead` CI job prints the summary line and the full table to the job summary. The CI test job collects `tests/bench/test_overhead.py`, including its checked-in budget test. Run it locally with:

```bash
python -m pytest -q -p no:cacheprovider -o addopts= tests/bench
```

## Measurement definitions

- Bytes count UTF-8 output; characters count Python Unicode characters. Estimated tokens are `ceil(characters / 4)`, the same heuristic as the core brief cap. They are not provider billing counts.
- Default `--tokenizer auto` also reports `tiktoken_tokens` using `cl100k_base` if optional tiktoken and its encoding cache are available. Encoding downloads are blocked. Otherwise it falls back to estimates. `--tokenizer estimate` makes budget results independent of optional packages. The report records the tokenizer used; estimated-token metrics always remain available.
- Listings concatenate sorted `name: description` lines from every `skills/*/SKILL.md` and `agents/*.md`, separated by newlines. Both skills are included, including the command-only `raid` skill. `idle.estimated_tokens` is the sum of the two listing estimates plus the estimated output of every idle hook case (`idle.hooks.estimated_tokens`, zero today because the harness fails if an allow case prints anything). `idle.with_mcp.estimated_tokens` adds the MCP `tools/list` response. Host formatting and host-specific listing exclusions can change the actual injected count. Full skill bodies are loaded on demand and are excluded.
- Each registered hook event runs N times in a fresh Python process through `python -m raidlock hook`. Timing includes process startup, imports, board reads and serialization. One warmup call per case runs first and is discarded, so a cold bytecode compile after install does not count as hook latency; real sessions run hooks with a warm cache. Percentiles use nearest rank: sorted sample at `ceil(p * N)`, counting from one. Output size is the largest response observed across runs. These are warm-filesystem measurements; they are not cold-disk benchmarks.
- All eight hook events have silent idle/allow cases, plus `session-start.unbound`: a session start from a session that is not the lead, in a workspace without an auto-join pool. With an auto-join pool and `CLAUDE_ENV_FILE`, session start prints one join line of about 50 tokens; that case is not idle and is not measured here. Pre-write and pre-bash denial fixtures attempt direct board writes; pre-read attempts a subagent read of the lead secret. The harness verifies denial decisions and rejects internal hook errors. Pre-urgent, post-tool, subagent-stop and session-start have no permission-denial branch, so deny is not applicable. These idle cases have no pending urgent messages, authenticated binding or transcript; active heartbeats, handbacks and urgent delivery require separate workload measurements.
- MCP size includes the complete compact JSON-RPC `tools/list` response plus newline, after legacy protocol `2025-11-25` initialization. Initialization itself and modern protocol metadata are excluded. MCP discovery is reported separately from plugin idle overhead because clients may use either integration.
- CLI sizes include actual compact `--json` stdout and its newline for each command in the fixture: show, show-card, who, claimable, brief, tokens, claim, note and move. The fixture creates one lead and one unscoped card, then joins a worker and runs claim, note and move-to-review. It measures representative board and worker-loop commands, not every administrative command or arbitrary board size. Generated identifiers have fixed width; paths and platform-specific identity details can still change output sizes.
- Brief size is measured after the worker loop and compared with the board's 1,200-estimated-token cap. `brief.cap_ratio > 1` fails the checked-in budget. The CLI brief sample is taken before the loop. This small fixture does not measure worst-case cards; mandatory scope, locks and rules can exceed the cap on larger cards.

Size budgets allow roughly 20% above the initial baseline, with strict zero-byte allow outputs and a strict brief cap ratio of one. Latency budgets are 150 ms at p50 and 300 ms at p95 for every hook, about six times the p95 measured below. A Bash tool call runs three hooks (`pre-bash`, `pre-urgent` and `post-tool`), so these limits keep a call's hook overhead under a second. `raid bench --runs 20 --budget tests/bench/budget.json` applies them as written. The test suite's budget test takes only three runs per case on shared CI machines and allows three times these latency limits; size limits apply unchanged. Compare local baselines on the same hardware before attributing changes to code.

`raid bench` and `raid tokens` need a checkout: from a wheel install, pass `--plugin-root /path/to/raidlock` to `raid bench`, and `raid tokens` reports the agent listing as 0 because the plugin files are not installed next to the package.

## Recorded baseline

Measured on Darwin, Python 3.11.15, N=20 after one discarded warmup per hook case, four characters/token, at commit 23a0505. The checked-in budget passed.

| Surface | Bytes | Estimated tokens |
| --- | ---: | ---: |
| brief | 877 | 220 |
| cli.brief | 731 | 183 |
| cli.claim | 171 | 43 |
| cli.claimable | 113 | 29 |
| cli.move | 69 | 18 |
| cli.note | 32 | 8 |
| cli.show | 139 | 35 |
| cli.show_card | 568 | 142 |
| cli.tokens | 796 | 199 |
| cli.who | 470 | 117 |
| listing.agent | 158 | 40 |
| listing.skill | 326 | 82 |
| mcp.tools_list | 2496 | 624 |

Idle total: 122 estimated tokens (listings 122, idle hook output 0); 746 with MCP. Brief: 220 / 1200 estimated tokens.

Measured on macOS, Apple silicon, 20 runs per case. Session start now also writes the `RAIDLOCK_SESSION` line and checks whether to re-arm the lead lease; session end also checks whether to free it. Both stay silent and well inside the budget.

| Hook case | Bytes | p50 ms | p95 ms |
| --- | ---: | ---: | ---: |
| post-tool.allow | 0 | 44.527 | 54.342 |
| pre-bash.allow | 0 | 47.582 | 51.452 |
| pre-bash.deny | 164 | 49.007 | 51.576 |
| pre-read.allow | 0 | 48.141 | 51.276 |
| pre-read.deny | 175 | 48.945 | 51.297 |
| pre-urgent.allow | 0 | 45.911 | 52.819 |
| pre-write.allow | 0 | 48.919 | 55.82 |
| pre-write.deny | 164 | 47.8 | 51.907 |
| session-end.allow | 0 | 48.679 | 55.988 |
| session-start.allow | 0 | 51.702 | 63.35 |
| session-start.unbound | 0 | 49.342 | 51.596 |
| subagent-stop.allow | 0 | 44.333 | 47.101 |
