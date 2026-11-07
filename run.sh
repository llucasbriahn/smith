#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${SMITH_PYTHON:-/usr/bin/python3}" -I "$APP_DIR/launch.py" "$@"
