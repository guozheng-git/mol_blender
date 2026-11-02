#!/bin/sh
# Run once per user. Uses the administrator's read-only Linux controller on NFS.
# No Blender installation, token, or change to the system Python is needed.
set -eu
shared_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
client="$shared_dir/controller/bin/blender-render"
"$client" cluster configure --profile "${1:-lab}" --url '@SERVICE_URL@' --ca-file "$shared_dir/ca.pem"
# setup records this controller; it preserves configured Blender/PyMOL paths.
"$client" setup
