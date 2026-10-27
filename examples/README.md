# Examples

Everyday commands use `raid`, the alias of `raidlock`. Scripts, MCP configs and CI use `raidlock`.

- [demo](demo/): a scripted raid across two workspaces with no model calls: a delegated build, a cross-provider review, a denied write, an urgent message, a lead handover and spend per model.
- [playground](playground/): five steps on a throwaway repo with a Claude Code lead, a Claude Code worker and a Codex worker.
- [claude-only](claude-only/): one Claude Code lead and two subagent workers.
- [claude-plus-codex](claude-plus-codex/): Claude and Codex workers that review each other.
- [multi-workspace](multi-workspace/): two workspaces sharing one repo's lock space.
