#!/bin/zsh
set -eu
APP_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$APP_DIR"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  print "缺少项目运行环境，请在项目目录执行："
  print "python3 -m venv .venv"
  print ".venv/bin/python -m pip install -r requirements.txt"
  read "?按回车退出"
  exit 1
fi
exec "$APP_DIR/.venv/bin/python" "$APP_DIR/app.py" "$@"
