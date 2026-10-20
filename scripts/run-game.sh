#!/usr/bin/env bash
# Launches the native Super Hexagon build with the hook loaded, outside Steam.
# Usage: run-game.sh <INSTANCE_DIR> [SPEED] [SEED]
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "usage: $0 <INSTANCE_DIR> [SPEED] [SEED]" >&2
    exit 1
fi

gameDir="$HOME/.local/share/Steam/steamapps/common/Super Hexagon"
projectDir="$(cd "$(dirname "$0")/.." && pwd)"
hook="$projectDir/hook/shxhook.so"
expectedSha="6f13c58d136b84df212cc23a560f7383e2f689005af18072e51d8a7439d2ba1d"

instanceDir="$(realpath -m "$1")"
speed="${2:-1}"
seed="${3:-1}"

if [ ! -f "$hook" ]; then
    echo "hook not built, run make in $projectDir/hook" >&2
    exit 1
fi

# the hook relies on fixed addresses, so a game update has to be caught here
actualSha="$(sha256sum "$gameDir/SuperHexagon" | cut -d' ' -f1)"
if [ "$actualSha" != "$expectedSha" ]; then
    echo "SuperHexagon binary changed (sha256 $actualSha), the hook needs checking first" >&2
    exit 1
fi

mkdir -p "$instanceDir"
cd "$gameDir"
exec env \
    LD_PRELOAD="$hook" \
    SHX_DATA_DIR="$instanceDir/data" \
    SHX_LOG="$instanceDir/hook.log" \
    SHX_SPEED="$speed" \
    SHX_SEED="$seed" \
    SDL_AUDIODRIVER="${SDL_AUDIODRIVER:-dummy}" \
    ./SuperHexagon
