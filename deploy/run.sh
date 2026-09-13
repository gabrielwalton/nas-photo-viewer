#!/bin/sh
set -eu
export PHOTO_VIEWER_DATA_DIR=${PHOTO_VIEWER_DATA_DIR:-/opt/managed-pi/data/photo-viewer}
exec .venv/bin/python -m photo_viewer.server
