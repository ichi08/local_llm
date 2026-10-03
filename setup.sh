#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"
. "$PROJECT_DIR/scripts/python_entry.sh"
if [ -z "${SETUP_PYTHON:-}${BENCHMARK_PYTHON:-}" ] && python_is_supported "$PROJECT_DIR/.venv/bin/python"; then
  LOCAL_LLM_PYTHON="$PROJECT_DIR/.venv/bin/python"
else
  choose_python
fi
native_python scripts/setup_environment.py "$@"
