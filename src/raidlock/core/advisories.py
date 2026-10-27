"""Best-effort pre-claim advice that cannot veto a claim."""

from importlib import import_module

PRE_CLAIM = (("raidlock.foresight", "pre_claim"),)


def pre_claim_warnings(board, card_id, checkout=None):
    warnings = []
    for module, name in PRE_CLAIM:
        try:
            hook = getattr(import_module(module), name)
            warnings.extend(hook(board, card_id, checkout=checkout))
        except Exception as exc:
            warnings.append(f"Warning: {module} advice skipped: {exc}")
    return warnings
