# security-audit

Use for a security audit covering dependencies, authentication and input handling. Each area has one build quest and one independent review quest. The lead must assign builds to openai and reviews to anthropic; provider assignment is an instruction, not an importer-enforced field. Review quests follow completed builds and do not replace board approval gates.

Quest: {"id": "build-dependencies", "depends": [], "scope": "Assign to provider openai. Audit dependencies and repair vulnerable versions with regression evidence."}

- [ ] Build security fixes for dependencies in {repo} | locks: {repo}/pyproject.toml, {repo}/requirements.txt

Quest: {"id": "review-dependencies", "depends": ["build-dependencies"], "scope": "Assign to provider anthropic, different from the build provider openai. Independently reproduce findings, inspect the patch and tests, and write a review report. Do not modify build files."}

- [ ] Independently review dependencies fixes in {repo} | locks: {repo}/audit-reviews/dependencies.md

Quest: {"id": "build-auth", "depends": [], "scope": "Assign to provider openai. Audit authentication and authorization boundaries and repair demonstrated flaws."}

- [ ] Build security fixes for auth in {repo} | locks: {repo}/src/auth, {repo}/tests/auth

Quest: {"id": "review-auth", "depends": ["build-auth"], "scope": "Assign to provider anthropic, different from the build provider openai. Independently reproduce findings, inspect the patch and tests, and write a review report. Do not modify build files."}

- [ ] Independently review auth fixes in {repo} | locks: {repo}/audit-reviews/auth.md

Quest: {"id": "build-inputs", "depends": [], "scope": "Assign to provider openai. Audit input validation and injection risks and add regression tests for fixes."}

- [ ] Build security fixes for inputs in {repo} | locks: {repo}/src/inputs, {repo}/tests/inputs

Quest: {"id": "review-inputs", "depends": ["build-inputs"], "scope": "Assign to provider anthropic, different from the build provider openai. Independently reproduce findings, inspect the patch and tests, and write a review report. Do not modify build files."}

- [ ] Independently review inputs fixes in {repo} | locks: {repo}/audit-reviews/inputs.md
