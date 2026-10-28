"""raidlock: a local-first lease-and-review board for coding agents from any provider."""

import sys

__version__ = "0.2.13"
MINIMUM_PYTHON = (3, 11)

if sys.version_info < MINIMUM_PYTHON:
    from ._python import refuse

    refuse(MINIMUM_PYTHON)
