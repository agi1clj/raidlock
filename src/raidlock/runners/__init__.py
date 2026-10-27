"""Runners: delegate a card to an external agent CLI in an isolated clone, enforce its locks, hand it back."""

SAMPLE = (
    "codex-worker-api handed api to review (3 file(s) changed, verify passed). "
    "Patch: ~/ws/.board/runs/api/changes.patch\nNext: raidlock show api"
)


def register_tokens(register):
    register(
        "delegate output",
        lambda board: {
            "tokens": -(-len(SAMPLE) // 4),
            "detail": "one line per delegate run; prompts and events stay on disk",
        },
    )
