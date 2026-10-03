# Shared interpreter discovery for setup; benchmark always uses the prepared venv.
python_is_supported() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}
choose_python() {
  LOCAL_LLM_PYTHON=${SETUP_PYTHON:-${BENCHMARK_PYTHON:-python3}}
  if ! python_is_supported "$LOCAL_LLM_PYTHON" && [ -z "${SETUP_PYTHON:-}${BENCHMARK_PYTHON:-}" ]; then
    for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3; do
      if python_is_supported "$candidate"; then LOCAL_LLM_PYTHON=$candidate; break; fi
    done
  fi
  if ! python_is_supported "$LOCAL_LLM_PYTHON"; then
    printf '%s\n' 'Python 3.10以上が必要です。SETUP_PYTHONでPythonを指定して ./setup.sh を実行してください。' >&2
    exit 1
  fi
}
native_python() {
  if [ "$(uname -s)" = Darwin ] && /usr/bin/arch -arm64 "$LOCAL_LLM_PYTHON" -c 'pass' >/dev/null 2>&1; then
    /usr/bin/arch -arm64 "$LOCAL_LLM_PYTHON" "$@"
  else
    "$LOCAL_LLM_PYTHON" "$@"
  fi
}
