#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_PATH="${1:-$PWD/blender-render-0.2.0b1-linux-x64.sif}"
DEF_PATH="$SCRIPT_DIR/blender_bridge.def"

cd "$SCRIPT_DIR/.."
test -f containers/runtime-install.sh || { echo 'Build from the source distribution or repository.' >&2; exit 1; }
apptainer build "$IMAGE_PATH" "$DEF_PATH"
