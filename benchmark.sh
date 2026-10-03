#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"
. "$PROJECT_DIR/scripts/python_entry.sh"
LOCAL_LLM_PYTHON="$PROJECT_DIR/.venv/bin/python"
if [ "${1:-}" = --help ] || [ "${1:-}" = -h ]; then
  if ! python_is_supported "$LOCAL_LLM_PYTHON"; then choose_python; fi
  native_python scripts/benchmark_models.py all --help
  exit 0
fi
if ! python_is_supported "$LOCAL_LLM_PYTHON" || [ ! -f .local/environment.json ]; then
  printf '%s\n' '準備ができていません。先に ./setup.sh を実行してください。' >&2
  exit 1
fi
case "${1:-}" in
  --plan) shift; set -- scripts/benchmark_models.py plan "$@" ;;
  *) set -- -u scripts/benchmark_models.py all "$@" ;;
esac
if [ "$(uname -s)" = Darwin ] && /usr/bin/arch -arm64 "$LOCAL_LLM_PYTHON" -c 'pass' >/dev/null 2>&1; then
  set -- /usr/bin/arch -arm64 "$LOCAL_LLM_PYTHON" "$@"
else
  set -- "$LOCAL_LLM_PYTHON" "$@"
fi
if [ "$(uname -s)" = Darwin ] && command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i "$@"
fi
exec "$@"
