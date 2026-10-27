# docs-refresh

Use when documentation has drifted from current behavior.

Quest: {"id": "refresh-guide", "depends": [], "scope": "Check documented commands against current behavior and update examples."}

- [ ] Refresh user guides for {repo} | locks: {repo}/docs

Quest: {"id": "refresh-readme", "depends": ["refresh-guide"], "scope": "Update setup instructions and links to the refreshed guides."}

- [ ] Refresh the README for {repo} | locks: {repo}/README.md

Quest: {"id": "verify-docs", "depends": ["refresh-readme"], "scope": "Run examples, check links and correct inconsistencies."}

- [ ] Verify documentation examples for {repo} | locks: {repo}/docs
