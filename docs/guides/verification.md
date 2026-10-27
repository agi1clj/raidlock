# Verification: receipts and gates

A quest reaches `done` only through the lead, and only after two gates: a verify gate (a signed receipt from a passing run of the card's verify command) and a review gate (enough approvals). Both are checked by `raid done`; there is no flag to skip them for one card. On a single-provider board both gates start off, so only cards with a verify command need a receipt, and `done` closes any other card with a warning (also when nobody claimed it); the table below shows when the gates switch on.

A card created without a verify command can get one later: `raid --lead-secret "$LEAD_SECRET_FILE" edit CARD --verify-cmd "..."`. Changing the command invalidates earlier receipts, because each receipt stores a hash of the command it ran.

## Settings and defaults

| Setting | New board | After a second provider joins |
|---|---|---|
| `require_verify` | `false` | `true` |
| `require_review.count` | `0` | at least `1` |
| `require_review.other_provider` | `false` | `true` |
| `require_card_for_lead_edits` | `true` | `true` |
| `board_first_subagents` | `true` | `true` |

The last two keep work on the board: the lead edits only inside a card it holds, and a subagent starts only with `card <id>` in its prompt (see [Hooks](hooks.md)).

The moment agents from two different providers are on the board (for example a Claude Code lead and a Codex reviewer, or a local shell runner), raidlock turns the gates on and records the change in the transcript. A value you set yourself with `raid config set` is never changed afterwards:

```bash
raid --lead-secret "$LEAD_SECRET_FILE" config set require_review 2
raid --lead-secret "$LEAD_SECRET_FILE" config set require_review.other_provider false
raid --lead-secret "$LEAD_SECRET_FILE" config set require_verify false
```

A card with a `--verify-cmd` always needs a receipt, even when `require_verify` is off, unless you turned `require_verify` off explicitly.

## The verify gate

```bash
raid --lead-secret "$LEAD_SECRET_FILE" verify checkout
```

`raid verify` is lead-only. It:

1. Picks the checkout: `--cwd PATH` if given, else the checkout recorded on the card's live lease, else the last verified checkout, else the workspace.
2. Reads that checkout's `HEAD` commit and runs the verify command in a clean copy of that commit. A dirty checkout is refused: commit or stash first; receipts are bound to a commit. The object store is cached; each run gets a fresh disposable worktree. Git reads use a temporary metadata directory without worker config, hooks or attribute files, with external diff and text conversion disabled.
3. Runs the command inside an OS sandbox with a scrubbed environment. The board, `RAIDLOCK_HOME`, agent CLI homes and credential stores are hidden from it. On Linux this needs bubblewrap (`bwrap`); on macOS it uses `sandbox-exec`. Without either, `raid verify` refuses to run. In the [container image](docker.md#verify-in-a-container) bubblewrap reuses the container's `/proc` read-only and the receipt records `bwrap-container`.
4. Records a receipt on the card: command, exit code, output tail, duration, commit SHA, a hash of the command, the isolation used and the card owner. The receipt is signed with HMAC-SHA256 using a per-board key (`$RAIDLOCK_HOME/tokens/<board uid>/verify.key`, mode 0600).

Worker read hooks deny access to the signing key. Legacy workspace keys are ignored; a fresh private key invalidates their receipts. Run `raid verify` again before closing those cards.

`raid done` then requires that the latest receipt:

- is signed by this board's key and names this card and board,
- matches the card's current verify command (a changed command needs a new run),
- passed with exit code 0,
- was taken at the commit that is `HEAD` of the verification checkout now. A commit after verifying means verifying again.

The receipt binds the repository, checkout and commit SHA. `done` refuses a checkout from an unrelated repository or a changed `HEAD`. While a worker holds a live lease with a recorded checkout, the receipt must come from that checkout. After integrating a worker's patch into another checkout, run `raid release CARD` (a card in review keeps its lane and approvals), verify that checkout with `--cwd` and run `done`.

### Runner verification

`raid delegate` runs the verify command inside the run's clone after the agent finishes, in the same sandbox, so a failing change goes to `blocked` instead of `review`. That run covers the uncommitted result and is recorded as a note and transcript entry, not as a signed receipt. The lead still runs `raid verify` on the current commit in a checkout belonging to the quest's repository before `done`. Claims validate repository membership; remote-less runner and duel clones use provenance recorded by the lead under the board lock. A lease alone does not establish membership. `--unsafe-verify` lets a delegated run verify without a sandbox (scrubbed environment only); `raid verify` itself has no such option.

## The review gate

```bash
raid --lead-secret "$LEAD_SECRET_FILE" review checkout --assign codex-reviewer
raid review checkout --verdict approve --note "edge cases covered"
```

Verdicts are `approve`, `changes` and `comment`. An approval counts when:

- it is the reviewer's latest verdict on the card,
- it was given after the owner's latest change (each owner move to `review` or `blocked` starts a new revision),
- the reviewer is not the owner,
- with `require_review.other_provider`, the reviewer's provider differs from the owner's.

`done` needs at least `require_review.count` counting approvals. The web UI shows the count on each card in review, for example `Approvals 0/1`, and `Approved: ready for done` once the gate passes.

## What the gates do not prove

A receipt shows that a command passed on a commit inside a sandbox. It does not show that the command tests the right thing; review the verify command when you write the card. Approvals come from agents you joined to the board. The signing key and lead secret live on the same machine and user account as the agents, so the gates are a cooperative boundary, not a defense against a hostile local process. See [SECURITY.md](../../SECURITY.md).
