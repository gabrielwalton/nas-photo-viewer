#!/bin/sh
set -eu
python3 -m venv .venv
.venv/bin/pip install --disable-pip-version-check .

