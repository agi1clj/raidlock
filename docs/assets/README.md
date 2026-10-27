# Assets

Images used by the README and guides. All art must be original to raidlock.

| File | Content | Status |
|---|---|---|
| `demo.gif` | Web UI tour of the fictional demo realm (realm tabs, a guild, quest log, quest web, raid frames, treasury). 960 px, 24 fps, about 20 s, recorded at 1440 x 900; shown in the README | generated |
| `hero.gif`, `hero.mp4` | Terminal recording of [scripts/demo.tape](../../scripts/demo.tape): three quests, two agents claim, an overlapping claim refused, done refused until review and a signed verify receipt, then the measured overhead table. 1200 x 680, 12 fps | generated |
| `demo-60s.cast` | Terminal recording (asciicast v2) of [scripts/demo-60s.sh](../../scripts/demo-60s.sh): quota routing, auto-handoff to another provider, cross-vendor review, receipt-gated done, cost and reputation per model | generated |
| `ui-raid-desktop.png`, `ui-plain-desktop.png` | Web board for the demo workspace, full page at 1280 px | generated |
| `ui-raid-mobile.png`, `ui-plain-mobile.png` | Same board at 375 px | generated |
| `ui-raid-overview-desktop.png`, `ui-plain-overview-desktop.png` | All-workspaces overview at 1280 px | generated |
| `ui-raid-overview-mobile.png`, `ui-plain-overview-mobile.png` | Overview at 375 px | generated |
| `ui-raid-{map,replay,graph,frames,achievements}-desktop.png` | The five board views of the demo workspace at 1280 px | generated |
| `brand/` | Mark, wordmark, lockup, palette and social preview; see [brand/README.md](brand/README.md) | hand-written SVG, PNG rendered |

Keep each PNG under 2 MB and `hero.gif` under 3 MB. Only fictional data may appear.

Regenerate the `ui-*.png` files from the demo boards with:

```bash
RAIDLOCK_SHOTS=1 python -m pytest tests/web/test_web_browser.py -k screenshots
```

Regenerate `hero.gif` and `hero.mp4` with [VHS](https://github.com/charmbracelet/vhs) 0.12 or newer (`brew install vhs` brings ttyd and ffmpeg) and `raidlock` on `PATH`:

```bash
uv run bash scripts/render-hero.sh        # HERO_FPS=15 for 15 fps; the GIF then exceeds 3 MB
```

The script renders the tape to a raw MP4, then writes a 12 fps H.264 `hero.mp4` and a 16-color `hero.gif` with ffmpeg, and prints each file's frame count, frame rate, duration and size. The tape runs in a temporary `HOME` under `/tmp` with fictional repos and agents. Last render: 678 frames over 56.5 s in both files (12.0 fps; GIF frame delays are 80 and 90 ms because GIF stores centiseconds), GIF 2.8 MB, MP4 0.8 MB.

The older web-UI animation of the [demo raid](../../examples/demo/README.md) can still be recorded with `uv run --with playwright --with pillow python examples/demo/record.py --out /tmp/ui.gif`; its default output is `hero.gif`.

## The 60-second terminal demo

`demo-60s.cast` was captured by timestamping each output line of `DEMO_PAUSE=1 bash scripts/demo-60s.sh` (13 seconds with the pauses, about 5 without) and replacing the temporary demo folder with `~/demo`. Play it with `asciinema play docs/assets/demo-60s.cast`, or paste it into any asciicast v2 player.

To record it with [asciinema](https://asciinema.org) 2.4 or newer instead, and render a GIF with [agg](https://github.com/asciinema/agg):

```bash
mkdir -p /tmp/raidlock-demo && cd /path/to/raidlock
DEMO_PAUSE=1 asciinema rec --overwrite --cols 110 --rows 32 \
  --command "bash scripts/demo-60s.sh /tmp/raidlock-demo/run" docs/assets/demo-60s.cast
agg --font-size 16 --speed 1.5 docs/assets/demo-60s.cast docs/assets/demo-60s.gif
```

Use a fresh folder under `/tmp` for each take so the recording shows no home path, check the cast with `grep -E '/Users|/home' docs/assets/demo-60s.cast` before committing, and keep the GIF under 3 MB.
