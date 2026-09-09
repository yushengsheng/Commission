#!/bin/zsh
set -eu
APP_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
# Share environment checks, logging, and crash reporting with the Finder launcher.
exec "$APP_DIR/返佣结算.app/Contents/MacOS/返佣结算" "$@"
