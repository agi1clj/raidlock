from . import clock

DEFAULT_TTL_MINUTES = 45


def is_live(lease, at=None):
    """Leases live until expiry; legacy holds without an expiry remain readable."""
    if lease.get("expires") is None:
        return True
    expires = clock.parse(lease["expires"])
    return bool(expires) and expires > (at or clock.now())


def describe(conflict):
    until = conflict["expires"] or "it leaves review/blocked"
    return (
        f"{conflict['path']} overlaps {conflict['held']} held by {conflict['agent']} on card {conflict['card']} "
        f"in workspace {conflict['workspace']} until {until}"
    )
