# Pull requests

`raid pr` turns a card's verified commit into a branch and a pull request. `raid pr-sync` keeps the card in step with that pull request and closes the card when it merges, but only through the same gates as `raid done`.

Both talk to GitHub through the [GitHub CLI](https://cli.github.com) (`gh`). raidlock never calls the GitHub API itself and never sees your GitHub token.

## Open a pull request

```bash
raid --lead-secret "$LEAD_SECRET_FILE" verify parser
raid --lead-secret "$LEAD_SECRET_FILE" pr parser
```

`raid pr CARD` is lead-only. It refuses unless all of these hold:

1. The card is in `review` or `done`.
2. The card has a verify command and its last verify run is a valid, signed, passing receipt (see [Verification](verification.md)).
3. The checkout the receipt covers still has the receipt's commit as `HEAD`. Commit after `raid verify` and you must verify again.
4. Every file the commit changes against the base branch is inside the card's locks, none is on the deny list (`.github/workflows`, `.claude`, `.board`, `.env*`, `*.pem`, `secrets`), and the patch passes `raid scan`.
5. With redaction on, no redacted name appears in the branch name or in the commit messages and authors being proposed.

Then it pushes exactly the verified commit to `raid/<card>` (or `--branch`) on the card's repo remote and runs `gh pr create`. If a pull request for that branch already exists, raidlock links it instead. A branch that already holds other commits is never overwritten; the push is refused and nothing is recorded.

The work happens in a private bare repository under `$RAIDLOCK_HOME/pr/`, so hooks, `fsmonitor` and config planted in the verified checkout never run. `gh` and `git push` get your normal environment minus every `RAIDLOCK_*` variable.

### Dry run

When `gh` is missing or `gh auth status` fails, `raid pr` runs every check and prints the title and body it would use, and pushes nothing. Pass `--dry-run` to get the same without touching the remote when `gh` is ready. Any joined agent may dry-run; only the lead opens pull requests.

### What the body says

| Section | Content |
|---|---|
| Agent | owner agent, runtime, provider, model |
| Lock check | card locks, changed files, counts of files outside the locks, denied paths and scan findings (all zero, or there is no PR) |
| Verify receipt | command, commit, exit code, duration, isolation, time, command hash |
| Review | required approvals, approvals so far, the current verdicts and their notes |

The body never contains local paths, the receipt signature, tokens or secrets.

### Redaction

Some teams do not allow agent or model names in pull requests. Redaction removes them from the title and body, and refuses a branch or commit that still carries them.

```bash
raid --lead-secret "$LEAD_SECRET_FILE" config set pr.redact all
raid --lead-secret "$LEAD_SECRET_FILE" config set pr.terms "Project Falcon,internal-codename"
```

| Value | Effect |
|---|---|
| `agent` | drops the Agent line and reviewer names; scrubs every joined agent name |
| `model` | drops the Model line; scrubs every recorded model id and common model family names |
| `provider` | drops runtime and provider; scrubs provider and runtime names and common vendor names |
| `all` | all three |
| `none` | clears the setting |

`pr.terms` adds words of your own. `--redact` and `--redact-terms` on the command line add to the board setting; they can never remove what the board requires. Scrubbed words become `[redacted]`.

### Flags and settings

| Flag | Setting | Default |
|---|---|---|
| `--base BRANCH` | `pr.base` | the remote's default branch, else `main` |
| `--remote NAME` | `pr.remote` | `origin` |
| `--branch NAME` | | `raid/<card>` |
| `--redact KINDS` | `pr.redact` | none |
| `--redact-terms WORDS` | `pr.terms` | none |
| `--draft` | | off |
| `--dry-run` | | on when `gh` is not authenticated |

## Track pull requests

```bash
raid --lead-secret "$LEAD_SECRET_FILE" pr-sync
raid --lead-secret "$LEAD_SECRET_FILE" pr-sync --watch 120
raid --lead-secret "$LEAD_SECRET_FILE" pr-link parser 42
```

`raid pr` links the card for you; `raid pr-link CARD NUMBER|URL` links a pull request opened some other way.

`raid pr-sync` polls each linked pull request with `gh pr view` and records its state (`open`, `closed`, `merged`), head commit and checks on the card. `raid show` prints them (`PR #7 open checks pending`) and `--json` returns them under `pr`.

### Merge closes the card only through the gates

When a pull request is merged and its card is in `review` or `blocked`, `pr-sync` asks for `done` exactly as `raid done` would. The card stays where it is, with a note saying why, when:

- the merged head commit is not the commit the receipt verified;
- the receipt is missing, failed, forged or stale (checkout `HEAD` moved);
- approvals are short, or come from the owner's own provider when `require_review.other_provider` is on;
- any check failed;
- the card is in `doing` or `todo`.

Fix the cause (verify again, get the review) and the next `pr-sync` closes it. The note is written once per reason, not on every poll.

### Rate limits

Each pull request is polled at most once per `--min-interval` seconds (60 by default); merged-and-done and closed pull requests are not polled again unless you pass `--force`. When GitHub reports a rate limit, the pass stops at once and exits 2. With `--watch`, the wait doubles after each rate limit up to 15 minutes and returns to the interval after a clean poll.

## Not covered

- GitLab and other forges: there is no `gh` equivalent wired in.
- Merge method: `pr-sync` compares the pull request's head commit with the verified commit, whatever merge method was used. It does not inspect the commit that lands on the base branch.
- The web UI shows the pull request in the card JSON; the card badge in the UI is not drawn yet.
