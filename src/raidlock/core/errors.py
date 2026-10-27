class RaidlockError(Exception):
    """Base error. `code` is the CLI exit code; `details` is JSON-safe context."""

    code = 1
    kind = "error"

    def __init__(self, message, hint="", **details):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details

    def to_dict(self):
        out = {"ok": False, "error": self.kind, "code": self.code, "message": self.message, **self.details}
        if self.hint:
            out["hint"] = self.hint
        return out


class Denied(RaidlockError):
    """A conflict or refusal: lock held, stale fence, not the owner, cap reached."""

    code = 2
    kind = "denied"


class Precondition(RaidlockError):
    """The request is invalid in the current state: unknown card, bad lane, bad input."""

    code = 3
    kind = "precondition"


class Timeout(RaidlockError):
    """A wait or lock acquisition ran out of time."""

    code = 4
    kind = "timeout"


class IdentityError(RaidlockError):
    """The caller is unknown, unauthenticated, or lacks the role."""

    code = 5
    kind = "identity"


class StaleLead(RaidlockError):
    """A lead write carried a lead fence older than the current lead lease (after a handover or takeover)."""

    code = 4
    kind = "stale_lead"
