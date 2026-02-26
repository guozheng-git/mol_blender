#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_PATH="${APPTAINER_IMAGE:-${1:-$SCRIPT_DIR/blender_bridge.sif}}"

if [[ ! -f "$IMAGE_PATH" ]]; then
  echo "Apptainer image not found: $IMAGE_PATH" >&2
  exit 1
fi

if [[ $# -gt 0 && "$1" == "$IMAGE_PATH" ]]; then
  shift
fi

python3 "$SCRIPT_DIR/bridge_runner.py" slurm \
  --use-apptainer 1 \
  --apptainer-image "$IMAGE_PATH" \
  "$@"
