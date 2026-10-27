# What the hooks enforce

| When | Check | Outcome |
|---|---|---|
| Edit, Write, NotebookEdit, Codex `apply_patch` | Is the file inside a live lease you hold with your current fence? | Silent allow, or deny with one line |
| Bash | `raidlock done`, `move <id> done`, `reopen`, `verify`, `land`, `delegate`, `pr`, other lead-only commands, `--for`, `--urgent` from a subagent or any session that is not the lead's, also behind wrappers | Deny |
| Bash | A subagent's or unbound session's `raidlock` write command without its own `RAIDLOCK_AGENT=`/`--agent`, or with the lead's name | Deny |
| Bash | Any write into `.board/` (redirect, `tee`, `rm`, `mv`, `cp`, `sed -i`, ...) | Deny |
| Bash | Redirect or `tee` into a repo file | Same rule as Edit |
| Bash | Reading `~/.raidlock/tokens` or presenting the lead secret from a session that is not the lead's | Deny |
| Read, Glob, Grep | The lead secret and agent tokens, including aliases | Deny except in the lead's session |
| Task, Agent | The prompt or description contains `card <id>` for a card that is not done or cancelled (`board_first_subagents`, on by default) | Silent allow, or deny |
| Before any tool | Pending urgent messages for the authenticated caller | Deliver bounded quoted context once; silent when empty |
| After each tool | Lease heartbeat for your card | At most once per 5 minutes, in the background |
| Subagent stops | Its last message | Appended to the board transcript as a handback |

A subagent, worker session or unbound session (for example an Agent Teams teammate in its own pane) with no claimed card is denied every repo edit inside a board workspace. A board with `auth off` lets an unbound session edit unleased files. The lead's session may edit inside a card the lead holds; with `require_card_for_lead_edits` on (the default), any other path in a git repo is denied. Paths outside git repos stay writable for the lead. Turn either board-first rule off with `raidlock --lead-secret FILE config set <key> off`; the git commit guard is unaffected. Details: `docs/guides/native.md` and `docs/guides/hooks.md`. Projects without a `.board/` are never touched.

The hooks learn which board agent a subagent is from its own successful `raidlock join|up|claim|note|heartbeat|lock` commands.
Git mutations also require valid leases. No passing hook emits an explicit permission pre-approval.
