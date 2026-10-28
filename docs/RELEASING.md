# Releasing

Releases are automated with [release-please](https://github.com/googleapis/release-please). There is nothing to tag or upload by hand.

## How a release happens

1. Merge pull requests with Conventional Commit titles (`feat:`, `fix:`, `docs:`...). The squash commit title becomes the changelog entry.
2. On every push to `main`, `.github/workflows/release.yml` runs release-please. It opens or updates a PR titled `chore(main): release X.Y.Z` that bumps `pyproject.toml`, `src/raidlock/__init__.py`, `uv.lock`, the plugin manifests, `.release-please-manifest.json` and `CHANGELOG.md`.
3. GitHub holds the release PR's checks (`action_required`) because the Actions token opened it. The workflow's `release-pr-ci` job approves those held `pull_request` runs for the PR's head commit, polling for up to two minutes, so the PR's own runs fill the required checks. Wait for them to pass. If the job warns that it found nothing to approve, approve the runs from the PR's Checks tab.
4. Merge the release PR. The same workflow then creates the `vX.Y.Z` tag and GitHub release, builds the sdist and wheel, publishes them to PyPI through trusted publishing (environment `pypi`, attestations on), signs them with Sigstore and attaches the `.sigstore.json` bundles, and builds, signs and pushes `ghcr.io/agi1clj/raidlock:X.Y.Z` and `:latest` for `linux/amd64` and `linux/arm64` with SBOM and provenance.

## Check a release

```bash
uv tool install --force raidlock==X.Y.Z && raid --version
docker run --rm ghcr.io/agi1clj/raidlock:X.Y.Z --version
```

## Configuration this depends on

- PyPI trusted publisher: repository `agi1clj/raidlock`, workflow `release.yml`, environment `pypi`. Renaming the workflow file breaks publishing.
- Repository setting "Allow GitHub Actions to create and approve pull requests" must stay on for release-please.
- Workflow tokens are read-only by default; each job asks for the permissions it needs.
