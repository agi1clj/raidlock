# Contributing to raidlock

Thanks for helping. This file covers setup, tests, conventions and the review checklist. By contributing you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Setup

You need Python 3.11 or newer, git and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/agi1clj/raidlock
cd raidlock
uv sync
uv run raidlock --help
```

raidlock itself uses the standard library only. `uv sync` installs the declared test tools into the local `.venv`; nothing is installed globally.

## Install from a checkout

To try an unreleased change, install from a local checkout.

From the checkout's root, as a tool on `PATH`:

```bash
uv tool install --force "git+file://$PWD"
raid --version
```

Or build a wheel and install that:

```bash
uv build
uv tool install --force dist/raidlock-*.whl
```

`pipx install dist/raidlock-*.whl` works the same way. For the Claude Code plugin, start a session with `claude --plugin-dir /path/to/raidlock`, or add the checkout as a local marketplace: `/plugin marketplace add /path/to/raidlock`, then `/plugin install raidlock@raidlock`. `raid bench` needs `--plugin-root /path/to/raidlock` when raidlock runs from a wheel.

## Tests

```bash
uv run --locked pytest --ignore=tests/web
```

UI changes reuse the tokens and components in [docs/guides/design.md](docs/guides/design.md). Web UI tests use Playwright and run separately:

```bash
uv sync --locked --extra ui-test
uv run --locked playwright install chromium
uv run --locked pytest tests/web
```

Tests must pass on Linux and macOS with Python 3.11 and newer. The recovered lead lease requires process-anchor verification, which reads Toolhelp on Windows but is not verified there, so native Windows is a release limitation; use WSL2.

Every bug fix comes with a test that fails without the fix. Concurrency and path-canonicalization code also gets property tests with Hypothesis.

## Conventions

- No code comments: no `#` or `//` comments in code, workflows or config. Names and tests carry the meaning. Rationale goes in the docs; linter suppressions go in the tool's configuration (`pyproject.toml` for Ruff), never inline. Docstrings on public functions are fine.
- Use the standard library only at runtime. A new runtime dependency needs an issue and a good reason first.
- Keep user-facing text and docs short and concrete. The raid theme is presentation; the protocol, CLI help and errors use plain terms (lead, worker, task, lease) next to the theme word where it helps.
- Names, art and text in the theme must be original. Do not use names, terms or assets from existing games.
- Keep one concern per pull request.

## Developer Certificate of Origin

Every commit must be signed off under the [Developer Certificate of Origin 1.1](https://developercertificate.org/): a `Signed-off-by: Your Name <you@example.com>` trailer that matches the commit author. `git commit -s` adds it. Pull requests with unsigned commits are not merged; fix them with `git rebase --signoff main`.

## Commit style

[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/): `type(scope): summary`, imperative, under 72 characters.

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `ci`, `build`, `chore`, `revert`. Breaking changes add `!` (`feat(cli)!: ...`) and a `BREAKING CHANGE:` footer.

PR titles follow the same format and are checked automatically. Preserve the PR title when squash merging: release-please reads the commit subjects on `main` to choose the next version and write the changelog. `feat` requests a minor release; `fix` and `perf` request a patch release. Other types normally wait for the next release. See [Releases](#releases).

Examples:

```text
feat(runners): add gemini-cli adapter
fix(core): canonicalize backslash lock paths on Windows
docs: explain fencing tokens in concepts guide
```

## Pull request checklist

- [ ] Every commit is signed off (`git commit -s`).
- [ ] Tests added or updated, and the suite passes locally.
- [ ] User-visible changes are described in the PR so maintainers can review the generated release notes.
- [ ] Docs updated (README, `PROTOCOL.md`, `docs/guides/`) if behavior or commands changed.
- [ ] `PROTOCOL.md` changes are called out in the PR description; they change the contract for every runtime.
- [ ] No code comments, no new runtime dependency, no secrets or real board data in fixtures.
- [ ] Workflow changes pass `actionlint` and pin actions to full commit SHAs.

## Adding a runner adapter

A runner adapter lets raidlock delegate a quest to another agent CLI (for example Aider or a new vendor CLI). The contract is in [PROTOCOL.md section 7](PROTOCOL.md#7-providers-runners-and-interfaces).

Implement `Adapter` from `raidlock.runners.base` and register it in `raidlock.runners.runner.ADAPTERS`. An adapter must:

1. Build the command line from the quest (prompt on stdin, model, sandbox mode, working directory set to the run clone).
2. Run in the isolated checkout the runner prepares (remote removed, committed files only, cleared environment). Never push, never run in another agent's checkout, never pass flags that disable the CLI's own sandbox or approvals.
3. Parse the CLI's event stream into transcript entries (A2A-style Message and Part fields, see PROTOCOL.md section 6) and heartbeat the lease while the run is alive. The agent does not write the board; the runner does.
4. Ask the CLI for a structured JSON handback (status, evidence, paths changed, open questions) when it supports an output schema.
5. Report usage: input, cached input and output tokens, and USD when the CLI reports it.
6. Return the exit status. The shared runner does the diff check, patch and move to `review`.

Tests: add a recorded event stream under the runner tests and assert the transcript, usage and exit handling. Do not call the real CLI in unit tests. Open an issue with the "Runner adapter request" template first so the flags and sandbox modes get checked against the CLI's docs.

## GitHub Actions pins

All actions are pinned to full commit SHAs; Dependabot updates them weekly.

actionlint 1.7.12 is downloaded in CI from its GitHub release and checked against its published SHA-256.

## Releases

Releases are automated. release-please keeps a release PR open from the Conventional Commits on `main` and bumps every version file (`pyproject.toml`, `src/raidlock/__init__.py`, `uv.lock`, plugin manifests, changelog). A maintainer merges it; the release workflow then tags the version, publishes to PyPI with attestations, attaches Sigstore-signed distributions to the GitHub release, and pushes a signed multi-arch image to `ghcr.io/agi1clj/raidlock`.

See [docs/RELEASING.md](docs/RELEASING.md) for the release flow and the settings it depends on.

## Python checks

CI tests the oldest supported Python (3.11) on Linux and the newest (3.14) on macOS, and runs ruff, pyright and actionlint. The Playwright UI suite runs when the web UI changes; the container build and Trivy scan run when the image inputs change and weekly.
Run the same static checks locally:

```bash
uv run --locked --with ruff==0.15.14 ruff check
uv run --locked --with ruff==0.15.14 ruff format --check
uv run --locked --with pyright==1.1.408 pyright src/raidlock
```

Ruff uses a 120-character line length and the rule sets `E`, `F`, `W`, `I`, `UP`, `B`, `SIM` and `S`. Both checks are clean on `main` and CI fails on any new finding. The check tools are version-pinned with `--with`; project dependencies use `uv.lock`.

`S603` is ignored for the whole project because it cannot tell a safe list-argument `subprocess` call from an unsafe one, so it flags every call. The rules that catch the real risks stay enforced: `S602` and `S604` (`shell=True`) and `S607` (a partial executable path). Tests also ignore `S607`, since they run `git` and `bash` from `PATH`.

Pyright uses basic checking. Existing diagnostic categories are reported as warnings while other errors still fail CI. The baseline has 0 errors and 89 warnings in `src/raidlock`. These warning-level categories also apply to new code; promote them to errors as the existing findings are fixed.
