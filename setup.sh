#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'Python 3.10以上が必要です。python3を導入してから再実行してください。' >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  printf '%s\n' 'Python 3.10以上が必要です。python3のバージョンを確認してください。' >&2
  exit 1
fi

exec python3 "$PROJECT_DIR/scripts/probe_hardware.py" "$@"
