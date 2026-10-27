"""Read-only local board UI and CLI entrypoint."""

from .cli import register_cli

__all__ = ["register_cli"]
