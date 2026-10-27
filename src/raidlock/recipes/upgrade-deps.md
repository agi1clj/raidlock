# upgrade-deps

Use for dependency upgrades that need compatibility checks before release.

Quest: {"id": "upgrade-runtime", "depends": [], "scope": "Update runtime constraints and lockfiles and record compatibility changes."}

- [ ] Upgrade runtime dependencies in {repo} | locks: {repo}/pyproject.toml, {repo}/requirements.txt

Quest: {"id": "upgrade-tooling", "depends": ["upgrade-runtime"], "scope": "Update development tools and regenerate affected lockfiles."}

- [ ] Upgrade development tools in {repo} | locks: {repo}/pyproject.toml, {repo}/requirements-dev.txt

Quest: {"id": "verify-upgrade", "depends": ["upgrade-tooling"], "scope": "Run the full suite and add regression tests for changed dependency behavior."}

- [ ] Verify upgraded dependencies in {repo} | locks: {repo}/tests
