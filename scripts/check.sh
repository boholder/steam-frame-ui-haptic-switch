#!/usr/bin/env bash
# Repository guardrail: unit tests, shell syntax, and LF line endings.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -z "${PYTHON:-}" ]; then
  if command -v python >/dev/null 2>&1 \
     && python -c 'import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)' 2>/dev/null; then
    PYTHON=python
  else
    PYTHON=python3
  fi
fi

echo "== unit tests"
"$PYTHON" -m unittest discover -s tests

echo "== shell syntax"
SHELL_FILES=(
  install.sh
  uninstall.sh
  installation_lib.sh
  scripts/check.sh
  scripts/probe-device.sh
)
for f in "${SHELL_FILES[@]}"; do
  bash -n "$f"
done

echo "== shell line endings"
if grep -n $'\r' "${SHELL_FILES[@]}"; then
  echo "CRLF found in shell scripts; expected LF" >&2
  exit 1
fi

echo "all checks passed"
