"""Run the source-checkout overhead benchmark; JSON on stdout, Markdown on stderr."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from raidlock.bench import main

if __name__ == "__main__":
    raise SystemExit(main())
