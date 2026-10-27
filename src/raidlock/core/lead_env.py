"""Credential handling for cooperative worker launchers."""

from .errors import IdentityError


def lead_material(env):
    return [
        k
        for k, v in env.items()
        if v and (k.startswith("RAIDLOCK_LEAD_") or k == "RAIDLOCK_ROLE" and str(v).lower() == "lead")
    ]


def assert_worker_env(env):
    """Refuse a worker environment containing lead material; never include values in errors."""
    if lead_material(env):
        raise IdentityError("worker environment contains lead material; scrub RAIDLOCK_LEAD_* before launch")
    return env


def scrub_worker_env(env):
    """Copy the inherited environment without board identity or lead credentials."""
    return {k: v for k, v in env.items() if not k.startswith("RAIDLOCK_")}
