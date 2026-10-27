import json
import os
import re
import tempfile
from pathlib import Path

from raidlock.web.data import Catalog, public

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(os.environ.get("RAIDLOCK_LAYOUT_OUT", Path(tempfile.gettempdir()) / "raidlock-ux-layout"))


def build():
    import demo
    import pytest
    from serving import isolate_env

    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="raidlock-layout-") as tmp, pytest.MonkeyPatch.context() as mp:
        root = Path(tmp)
        isolate_env(mp, root)
        demo.fleet_board(root / "ws")
        demo.empty_board(root / "ws")
        catalog = Catalog()
        overview = catalog.overview()
        for name, label in [("orbit-ops", "busy"), ("fresh-start", "empty")]:
            workspace = catalog.get(name)
            _, payload = workspace.refresh()
            board = public(payload)
            html = (ROOT / "src/raidlock/web/static/index.html").read_text()
            html = re.sub(r'<script src="[^"]+" defer></script>', "", html)
            html = re.sub(r"<link[^>]+>", "", html)
            css = (ROOT / "src/raidlock/web/static/app.css").read_text()
            js = (ROOT / "src/raidlock/web/static/app.js").read_text()
            js = js.replace("state.view = route();", f"state.view = {json.dumps(name)};")
            js = js.replace("  tick();\n  setInterval(tick, POLL_MS);", "")
            setup = (
                f"Date.now = () => Date.parse({json.dumps(board['now'])});"
                f"state.overview = {json.dumps(overview)};"
                "state.projects = state.overview.projects;"
                f"state.board = {json.dumps(board)};"
                f"state.log = {json.dumps(payload['_chat'])};"
                "init(); applyView(); renderBoard(state.board);"
                'document.getElementById("soundBanner").hidden = true;'
                'document.getElementById("footer").textContent = '
                '"Review fixture: frozen data; commands are copied only.";'
            )
            js = js.replace("init();\n})();", setup + "\n})();")
            html = html.replace("</head>", "<style>" + css + "</style></head>")
            html = html.replace("</body>", "<script>" + js.replace("</script", "<\\/script") + "</script></body>")
            (OUTPUT / f"{label}.html").write_text(html)


if __name__ == "__main__":
    build()
