#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
for tool in vhs ffmpeg ffprobe raidlock; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool is not on PATH" >&2; exit 3; }
done
FPS="${HERO_FPS:-12}"
vhs scripts/demo.tape
ffmpeg -v error -y -i docs/assets/hero-raw.mp4 -vf "fps=$FPS" -c:v libx264 -pix_fmt yuv420p -crf 26 \
  -movflags +faststart -an docs/assets/hero.mp4
ffmpeg -v error -y -i docs/assets/hero-raw.mp4 \
  -vf "fps=$FPS,split[a][b];[a]palettegen=max_colors=16:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" \
  -loop 0 docs/assets/hero.gif
rm -f docs/assets/hero-raw.mp4
for file in docs/assets/hero.gif docs/assets/hero.mp4; do
  ffprobe -v error -count_frames -select_streams v:0 \
    -show_entries stream=nb_read_frames,avg_frame_rate,width,height:format=duration,size -of compact=p=0:nk=0 "$file" \
    | sed "s#^#$file: #"
done
HOME_PATHS="/Use""rs/|/ho""me/"
if grep -aE "$HOME_PATHS" docs/assets/hero.gif >/dev/null; then
  echo "hero.gif contains a home path" >&2
  exit 1
fi
