#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_PATH="${1:-$SCRIPT_DIR/blender_bridge.sif}"
DEF_PATH="$SCRIPT_DIR/blender_bridge.def"

echo "Building Apptainer image: $IMAGE_PATH"
apptainer build "$IMAGE_PATH" "$DEF_PATH"
