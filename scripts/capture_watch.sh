#!/usr/bin/env bash
# Starts the policy viewer in a window and saves three cropped frames of the game window.
# Usage: capture_watch.sh <MODEL> <OUT_DIR> [LEVEL]
set -euo pipefail

if [ $# -lt 2 ]; then
    echo "usage: $0 <MODEL> <OUT_DIR> [LEVEL]" >&2
    exit 1
fi
projectDir="$(cd "$(dirname "$0")/.." && pwd)"
model="$(realpath "$1")"
outDir="$(realpath -m "$2")"
level="${3:-0}"
mkdir -p "$outDir"

systemd-run --user --unit=shx-watch-policy --collect --quiet -p CPUQuota=200% \
    --setenv=DISPLAY=:0 --setenv=WAYLAND_DISPLAY=wayland-1 \
    -p WorkingDirectory="$projectDir" -p StandardOutput="file:$outDir/watch.out" \
    "$projectDir/.venv/bin/python" scripts/eval_policy.py "$model" --watch --episodes 1 \
    --max-seconds 40 --levels "$level" --instance "$outDir/instance"

window=""
for _ in $(seq 60); do
    window="$(xdotool search --name "Super Hexagon" 2>/dev/null | head -1 || true)"
    if [ -n "$window" ] && grep -q "lockstep started" "$outDir/instance/hook.log" 2>/dev/null; then
        break
    fi
    sleep 0.5
done
if [ -z "$window" ]; then
    echo "game window never appeared" >&2
    exit 1
fi

geometry="$(xdotool getwindowgeometry "$window")"
position="$(echo "$geometry" | awk '/Position/{print $2}')"
size="$(echo "$geometry" | awk '/Geometry/{print $2}')"
x="${position%,*}"
y="${position#*,}"

for i in 1 2 3; do
    sleep 4
    spectacle -b -n -f -o "$outDir/full$i.png"
    magick "$outDir/full$i.png" -crop "${size}+${x}+${y}" -resize 400x "$outDir/frame$i.png"
    rm -f "$outDir/full$i.png"
done
magick "$outDir/frame1.png" "$outDir/frame2.png" "$outDir/frame3.png" +append "$outDir/frames.png"
echo "saved $outDir/frames.png"
