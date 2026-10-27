# multi-repo-refactor

Use for a shared contract change across two repositories. Keep the producer backward compatible until the consumer migrates.

Quest: {"id": "refactor-producer", "depends": [], "scope": "Introduce the new contract while preserving the old interface and test both."}

- [ ] Refactor the producer in {repo} | locks: {repo}/src, {repo}/tests

Quest: {"id": "refactor-consumer", "depends": ["refactor-producer"], "scope": "Adopt the new contract and test compatibility with the producer."}

- [ ] Migrate the consumer in {other_repo} | locks: {other_repo}/src, {other_repo}/tests

Quest: {"id": "verify-contract", "depends": ["refactor-consumer"], "scope": "Run integration tests across both repositories and document rollout and rollback steps."}

- [ ] Verify the contract across {repo} and {other_repo} | locks: {repo}/tests, {other_repo}/tests
