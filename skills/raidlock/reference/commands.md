# raidlock commands

Every command takes `--board PATH`, `--json`, `--agent NAME`, `--token T`, `--cap C`, `--fence N`, `--lead-secret FILE` and `--debug`. Identity also comes from `RAIDLOCK_AGENT`, `RAIDLOCK_TOKEN`, `RAIDLOCK_CAP` and `RAIDLOCK_FENCE`. Commands marked Lead need `--lead-secret FILE` from the lead's session. Runtime names take short or long forms: `claude`/`claude-code`, `codex`/`codex-cli`, `gemini`/`gemini-cli`.

Exit codes: 0 ok, 2 denied or conflict, 3 precondition or usage, 4 timeout, 5 identity or role, 1 internal error.

## Everyday

| Command | What it does |
|---|---|
| `raidlock up [--team T] [--role worker\|reviewer]` | Create the board, join it, or show what you are on |
| `raidlock new "<title>" --locks a/x,b/y [--team T] [--depends id] [--verify "..."] [--verify-cmd "..."]` | Create a card (`--verify-cmd` is lead only) |
| `raidlock plan import FILE [--team T]` | Import cards from a JSON or Markdown plan |
| `raidlock claim <id> [--take-over]` | Take a card, lease its locks, print the fence |
| `raidlock note <id> "<text>"` | Progress note; renews the lease |
| `raidlock move <id> review\|blocked\|todo [--note "..."]` | Change lane (doing is reached only by claim) |
| `raidlock done <id>` | Lead: close a card after its verify and review gates pass |
| `raidlock who` | Agents, status, card and spend |
| `raidlock show [<id>] [--all]` | The board, or one card with notes |

## Advanced

| Command | What it does |
|---|---|
| `raidlock join --agent N --team T --runtime claude-code [--model M]` | Join; prints the token once |
| `raidlock claimable [--for-review]` | What you can claim, and why not |
| `raidlock heartbeat <id>` | Renew a lease without a note |
| `raidlock lock <id> <repo>/<path>` | Add a path to a card's locks |
| `raidlock release <id>` | Give the card back |
| `raidlock check <paths>` / `raidlock check --write FILE` | Lock conflicts, or the write decision for one file |
| `raidlock msg --to N "<text>" [--task id]` | Message an agent (queued while it is busy on another card) |
| `raidlock inbox` | Read your messages |
| `raidlock wait <id>` | Wait until a card leaves doing |
| `raidlock review <id> --verdict approve\|changes\|comment [--note]` | Review a card |
| `raidlock review <id> --assign a,b` | Lead: assign reviewers |
| `raidlock land <id> [--cwd CHECKOUT] [--message MSG]` | Lead: apply the newest handed-back delegated patch, commit, verify, note the sha on the card and print the next step |
| `raidlock verify <id> [--cwd CHECKOUT]` | Lead: run the card's verify command in a sandbox on the committed HEAD; records a signed receipt |
| `raidlock edit <id> --verify-cmd "..."` | Lead: set, change or clear a card's verify command (old receipts stop counting) |
| `raidlock reopen <id>` | Lead: move a done card back to todo |
| `raidlock team add\|rm\|list [<name>] [--max N] [--mission "..."]` | Lead: manage teams |
| `raidlock config [get\|set] <key> [value]` | Board settings: auth, ttl_minutes, require_review, require_verify, require_card_for_lead_edits, board_first_subagents |
| `raidlock cost [<id>]` | Record or show usage |
| `raidlock transcript` | The conversation log |
| `raidlock reap` | Move expired doing cards to blocked (`raidlock ui` does this every 5 minutes) |
| `raidlock tidy` | Lead: reap, close cards whose linked PR merged, list what waits on the lead with copy-ready commands |
| `raidlock lead status\|heartbeat\|handover --to <session>\|takeover` | The lead lease; `status` works from any session, and a lease frees itself when the lead's session ends |
| `raidlock upgrade` | Upgrade the CLI and the Claude Code plugin to the latest release |
| `raidlock workspaces` | Registered boards on this machine |
| `raidlock install-commands [--uninstall]` | Add (or remove) a personal bare `/raid` skill in `~/.claude/skills/raid/SKILL.md`; the plugin's own is `/raidlock:raid` |

## Orchestration and lead tools

| Command | What it does |
|---|---|
| `raidlock approvals [--kind K]` | Everything waiting for the lead, with copy-ready commands |
| `raidlock next [--provider P] [--for-review]` | Suggest the next card and the provider to run it on (read-only) |
| `raidlock routing [show\|set\|explain] [key] [json]` | Routing rules per label and path; set is lead only |
| `raidlock delegate <id> --to codex\|claude\|gemini\|shell [--as reviewer]` | Lead: run a card in a throwaway remote-less clone and hand back a patch; the clone is deleted on done, close or cancel |
| `raidlock duel <id> --vs claude,codex` / `--winner a\|b` | Lead: run a card on two providers, pick the winner on signed receipts |
| `raidlock resume <id>` | Lead or owner: token-capped resume packet for a stalled card |
| `raidlock resurrect <id> [--to R] [--dry-run]` | Lead: hand a stalled card to a fresh agent |
| `raidlock resurrect-policy [show\|set]` | Lead: automatic resurrection (off by default) |
| `raidlock pr <id>` / `pr-link <id> <ref>` / `pr-sync` | Lead: open a pull request from the verified commit, link one, track checks and merges |
| `raidlock bench [--plugin-root PATH]` | Offline overhead benchmark |
| `raidlock bridge [show\|pool] [names]` | Agent Teams and Codex hooks: the auto-join pool (lead sets it) |

## Views

`raidlock map` (who holds which paths), `replay --at SEQ` (board at one transcript record), `graph` (dependencies and lock contention), `frames` (context fill per agent), `achievements`, `treasury` (burn rate and month-end forecast), `spend --by model`, `limits`, `reputation`, `ui --open`, `watch`.

Lock paths are `<repo>/<path>`, where `<repo>` is a repo folder name in the workspace. The same remote cloned twice is one lock space. Paths conflict when one contains the other.
