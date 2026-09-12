#!/bin/sh
set -eu
export PHOTO_VIEWER_DATA_DIR=${PHOTO_VIEWER_DATA_DIR:-/opt/managed-pi/data/photo-viewer}
exec .venv/bin/waitress-serve --host=0.0.0.0 --port=8080 --call photo_viewer:create_app

