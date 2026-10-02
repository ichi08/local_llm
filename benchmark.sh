#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"
BENCHMARK_PYTHON=${BENCHMARK_PYTHON:-python3}
if ! "$BENCHMARK_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  printf '%s\n' 'Python 3.10以上が必要です。' >&2
  exit 1
fi

case "${1:-}" in
  --help|-h) exec "$BENCHMARK_PYTHON" scripts/benchmark_models.py all --help ;;
  --plan) shift; exec "$BENCHMARK_PYTHON" scripts/benchmark_models.py plan "$@" ;;
esac

if [ "$(uname -s)" = Darwin ] && command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i "$BENCHMARK_PYTHON" -u scripts/benchmark_models.py all "$@"
fi
exec "$BENCHMARK_PYTHON" -u scripts/benchmark_models.py all "$@"
