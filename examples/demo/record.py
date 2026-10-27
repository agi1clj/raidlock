import argparse
import getpass
import hashlib
import io
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
STEP = re.compile(r"^== (\d+)\. ")
MASK = """roots => {
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    for (const r of roots) if (n.nodeValue.includes(r)) n.nodeValue = n.nodeValue.split(r).join("~/demo");
  }
}"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tool(name):
    path = shutil.which(name)
    if not path:
        sys.exit(f"{name} is not on PATH")
    return path


def route_for(step):
    if step <= 5:
        return "/acme-shop"
    if step <= 12:
        return "/weather-api"
    return "/"


def main():
    p = argparse.ArgumentParser(description="Record the demo raid as an animated image for the README")
    p.add_argument("--out", default=str(REPO / "docs" / "assets" / "hero.gif"))
    p.add_argument("--width", type=int, default=1200)
    p.add_argument("--height", type=int, default=900)
    p.add_argument("--scale", type=float, default=0.66)
    p.add_argument("--pause", default="3.5")
    a = p.parse_args()

    scratch = Path(tempfile.mkdtemp(prefix="raidlock-demo-"))
    root = scratch / "raid"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RAIDLOCK_", "CODEX_", "CLAUDE_", "GEMINI_"))}
    for name in ("home", "codex", "claude", "gemini"):
        (scratch / name).mkdir()
    env.update(
        HOME=str(scratch / "home"),
        CODEX_HOME=str(scratch / "codex"),
        CLAUDE_CONFIG_DIR=str(scratch / "claude"),
        GEMINI_CONFIG_DIR=str(scratch / "gemini"),
        DEMO_PAUSE=a.pause,
    )
    demo = subprocess.Popen(
        [tool("bash"), str(HERE / "run.sh"), str(root)], env=env, stdout=subprocess.PIPE, text=True, bufsize=1
    )
    lines = []
    step = 0
    while step < 2:
        line = demo.stdout.readline()
        if not line:
            sys.exit("demo exited before the boards existed:\n" + "".join(lines))
        lines.append(line)
        m = STEP.match(line)
        if m:
            step = int(m.group(1))
    port = free_port()
    ui_env = {**env, "RAIDLOCK_HOME": str(root / "home")}
    server = subprocess.Popen(
        [tool("raidlock"), "ui", "--port", str(port)],
        cwd=root / "acme-shop",
        env=ui_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.set_blocking(demo.stdout.fileno(), False)
    frames, seen = [], None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": a.width, "height": a.height})
            base = f"http://127.0.0.1:{port}"
            for _ in range(100):
                try:
                    page.goto(base + "/")
                    break
                except Exception:
                    time.sleep(0.1)
            page.evaluate("['logPanel', 'treasuryPanel'].forEach(p => localStorage.setItem('rl.open.' + p, 'true'))")

            def shot(hold):
                page.evaluate("const b = document.getElementById('soundBanner'); if (b) b.hidden = true")
                page.evaluate(MASK, [os.path.realpath(root), str(root)])
                text = page.evaluate("document.body.innerText")
                for leak in (os.path.expanduser("~"), getpass.getuser()):
                    if leak in text:
                        sys.exit(f"refusing to record: the page shows local user data ({leak})")
                png = page.screenshot()
                digest = hashlib.sha256(png).hexdigest()
                if frames and frames[-1][2] == digest:
                    frames[-1][1] += hold
                else:
                    frames.append([png, hold, digest])

            while True:
                for line in iter(demo.stdout.readline, ""):
                    lines.append(line)
                    m = STEP.match(line)
                    if m:
                        step = int(m.group(1))
                done = demo.poll() is not None
                route = route_for(step)
                if route != seen:
                    page.goto(base + route)
                    page.wait_for_load_state("networkidle")
                    seen = route
                page.wait_for_timeout(1000)
                shot(1000)
                if done:
                    break
            for route, hold in (("/acme-shop", 2500), ("/", 3500)):
                page.goto(base + route)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(1500)
                shot(hold)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    if demo.wait() != 0:
        sys.exit("demo failed:\n" + "".join(lines))
    images = []
    for png, _, _ in frames:
        im = Image.open(io.BytesIO(png)).convert("RGB")
        im = im.resize((int(im.width * a.scale), int(im.height * a.scale)), Image.LANCZOS)
        images.append(im.quantize(colors=128, method=Image.MEDIANCUT, dither=Image.NONE))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        out,
        save_all=True,
        append_images=images[1:],
        duration=[hold for _, hold, _ in frames],
        loop=0,
        optimize=True,
    )
    shutil.rmtree(scratch, ignore_errors=True)
    print(f"{out}: {len(images)} frames, {out.stat().st_size / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
