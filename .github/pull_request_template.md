## What

## How to verify

## Checklist

- [ ] PR title follows Conventional Commits (`feat:`, `fix:`, `docs:`...); it becomes the changelog entry
- [ ] Commits signed off (`git commit -s`)
- [ ] Tests added or updated; `uv run pytest -n auto` passes
- [ ] Docs updated if behavior changed (README, docs/guides, PROTOCOL.md, skills)
- [ ] No new runtime dependency, no secrets or real board data, actions pinned to commit SHAs
