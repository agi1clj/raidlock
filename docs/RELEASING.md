# Releasing raidlock

Release raidlock under the MIT license in `agi1clj/raidlock`. Run these steps from the private source checkout. Python 3.11 or newer, Git, uv and the GitHub CLI are required.

## Prepare the public tree

Confirm that `pyproject.toml` and `src/raidlock/__init__.py` declare `0.1.0`. The project version in `pyproject.toml` controls distribution metadata. The plugin manifests and `uv.lock` must carry the same version, which the release workflow checks. Confirm the date in the `0.1.0` changelog entry is the actual release date.

Choose an absent directory outside the source checkout:

```sh
public_tree="$(mktemp -d "${TMPDIR:-/tmp}/raidlock-public.XXXXXX")"
bash scripts/curate-public.sh "$public_tree"
```

The curator copies only allowlisted tracked files, plus the release guide, curator, release-please configuration, new workflows and Dependabot configuration. Add intended new release files to the source index before curating. Internal planning documents, legacy material, board state and scratch files are excluded. Symlinks are rejected. A nonzero exit means the tree must not be published: fix the reported inputs and rerun into a new empty directory.

Inspect the copied files and capture their names before tests or builds create any output:

```sh
cd "$public_tree"
python3 - <<'PYTHON'
from pathlib import Path
files = sorted(str(p) for p in Path('.').rglob('*') if p.is_file())
Path('.release-files').write_bytes(b''.join(p.encode() + b'\0' for p in files))
PYTHON
uv run --locked pytest --ignore=tests/web
uv sync --locked --extra ui-test
uv run --locked playwright install --with-deps chromium
uv run --locked pytest tests/web
uv build
```

## Fresh-user check

Before each release, follow only the README on a clean account and time it from install to the first claimed quest. The target is under five minutes.

```bash
uv build --wheel -o /tmp/fresh/dist
env -i HOME=/tmp/fresh/home PATH=/usr/bin:/bin:/opt/homebrew/bin TERM=xterm bash
```

Then, in that shell and a second one with the same clean `HOME`, run the README's Install and Quickstart commands with the wheel, a one-commit `storefront` repo under `~/code/shop`, and the printed lead secret path.

Last run, 2026-10-23, macOS, wheel built from the release candidate, scripted:

| Step | Result |
|---|---|
| `uv tool install ./dist/raidlock-0.1.0-py3-none-any.whl` | installed `raid` and `raidlock`; uv warned that `~/.local/bin` was not on `PATH` (the README now says to run `uv tool update-shell`) |
| `raid up` in `~/code/shop` | board created, lead secret path printed, never the secret |
| `raid --lead-secret <path> new "Fix checkout" --id fix-checkout --locks storefront/src/checkout` | quest created |
| second shell: `raid join --agent worker-1 --runtime human`, paste the two export lines | joined as worker |
| `raid claim fix-checkout` | lease on `storefront/src/checkout`, fence 1 |
| total | 6 and 7 seconds of command time in two runs, install to claimed quest |

On 2026-10-27 the same flow passed from a wheel built from the curated tree on macOS and in a Linux container (Debian, Python 3.11, bubblewrap), including `quickstart --demo`, verify in the sandbox, `done` and `raid ui`.

These times exclude reading and typing; a person following the README also copies one path and two export lines. In the second run uv first downloaded a managed CPython 3.11 (25.5 MiB) because the clean home had none; that download is included in the 7 seconds.


Initialize a fresh repository in the curated directory. This creates one initial commit containing the public tree, with no private history:

```sh
git init -b main
git config user.name 'Gabriel Arcas'
git config user.email '38486343+agi1clj@users.noreply.github.com'
git add --pathspec-from-file=.release-files --pathspec-file-nul
GIT_COMMITTER_NAME='Gabriel Arcas' \
GIT_COMMITTER_EMAIL='38486343+agi1clj@users.noreply.github.com' \
git commit --author='Gabriel Arcas <38486343+agi1clj@users.noreply.github.com>' \
  --signoff -m 'Release raidlock v0.1.0'
git log -1 --format=full
```

Verify that both the author and `Signed-off-by` trailer identify Gabriel Arcas with the noreply address above. Verify that the commit contains no build output, local environments or test artifacts.

## Configure publishing

Create an empty public GitHub repository named `agi1clj/raidlock`, without an initial README, license or ignore file. Create its `pypi` environment and configure the desired release approval rules.

On PyPI, configure a trusted publisher for the `raidlock` project (a pending publisher if the project does not exist):

- Owner: `agi1clj`
- Repository: `raidlock`
- Workflow filename: `release.yml`
- Environment: `pypi`

No PyPI API token is needed. The publishing job requests an OIDC token using its job-scoped `id-token: write` permission and publishes PEP 740 attestations with `attestations: true`.

Resolve the SHA placeholders in `release-please.yml` and `pr-title.yml` before enabling them. Configure `RELEASE_PLEASE_TOKEN` as a fine-grained token limited to this repository, with contents and pull requests write access. Unlike the default `GITHUB_TOKEN`, this token lets release-please tags trigger the publishing workflow and its PRs trigger CI. Enable GitHub Actions to create pull requests in the repository settings.

## Push and release

```sh
git remote add origin https://github.com/agi1clj/raidlock.git
git push -u origin main
```

`v0.1.0` is the first and only manually created release tag. Wait for CI to pass on `main`, then tag and push:

```sh
git tag -a v0.1.0 -m 'raidlock v0.1.0'
git push origin v0.1.0
```

The tag triggers `.github/workflows/release.yml`. It checks the tag and metadata versions, builds the distributions, extracts the `0.1.0` section from `CHANGELOG.md`, publishes to PyPI through the `pypi` environment, and creates a GitHub release with those notes and the wheel and source archive attached. Approve the environment deployment if its rules require it.

Verify the completed workflow, the GitHub release at `https://github.com/agi1clj/raidlock/releases/tag/v0.1.0`, and the package at `https://pypi.org/project/raidlock/0.1.0/`. If publishing fails, inspect the failed job before retrying; PyPI distribution filenames cannot be reused.

## Subsequent releases

After `v0.1.0`, release-please maintains a release PR on pushes to `main`. Its Python release strategy updates `pyproject.toml`, `CHANGELOG.md`, the plugin versions and `.release-please-manifest.json`. The manifest starts at `0.1.0`; tags use `v` without a package prefix.

Use Conventional Commits for commit messages and PR titles: `type(scope): summary`. Allowed types are `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `ci`, `build`, `chore` and `revert`. `feat` requests a minor release; `fix` and `perf` request a patch release. Mark breaking changes with `!` and describe them in a `BREAKING CHANGE:` footer. Other types normally appear in a later release without triggering one themselves. Squash merges must retain the validated PR title as the commit subject.

Before merging the release PR, review its version and notes, update `src/raidlock/__init__.py` to the proposed version, and run `uv lock` to refresh the project version in `uv.lock`. Commit these changes to the release PR branch. Check that both plugin manifests match and run CI. The publishing workflow rejects inconsistent versions.

Merging the release PR lets release-please create the tag and GitHub release. The tag starts `release.yml`, which runs `uv build`, publishes through trusted publishing with attestations, and attaches the distributions to the existing GitHub release. Do not create subsequent release tags manually.

Dependabot groups weekly GitHub Actions and Python dependency updates. Python uses the `pip` ecosystem fallback; review and refresh `uv.lock` with `uv lock` when a dependency PR changes `pyproject.toml`.
