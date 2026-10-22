#!/usr/bin/env bash
# Repository guardrail: unit tests, ruff lint/format, shellcheck, syntax, and LF line endings.

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

# CI runs ruff in a dedicated job (ruff-action); set SKIP_RUFF=1 there.
if [ "${SKIP_RUFF:-}" = "1" ]; then
  echo "== ruff lint/format (skipped: SKIP_RUFF=1)"
else
  echo "== ruff lint/format"
  UVX=
  if command -v uvx >/dev/null 2>&1; then
    UVX=uvx
  elif command -v uvx.exe >/dev/null 2>&1; then
    # WSL/Git-Bash interop: only the Windows uvx is on PATH.
    UVX=uvx.exe
  fi
  if [ -z "$UVX" ]; then
    echo "uvx not found; ruff checks require uv (https://astral.sh/uv)" >&2
    exit 1
  fi
  "$UVX" ruff@0.17.1 check --fix .
  "$UVX" ruff@0.17.1 format --check .
fi

SHELL_FILES=(
  install.sh
  uninstall.sh
  installation_lib.sh
  scripts/check.sh
  scripts/probe-device.sh
  scripts/aggressive-ui-haptics.sh
)

echo "== shellcheck"
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck "${SHELL_FILES[@]}"
elif command -v shellcheck.exe >/dev/null 2>&1; then
  shellcheck.exe "${SHELL_FILES[@]}"
elif command -v uvx >/dev/null 2>&1; then
  uvx --from shellcheck-py shellcheck "${SHELL_FILES[@]}"
elif command -v uvx.exe >/dev/null 2>&1; then
  uvx.exe --from shellcheck-py shellcheck "${SHELL_FILES[@]}"
else
  echo "shellcheck not found; install shellcheck or uv" >&2
  exit 1
fi

echo "== shell line endings"
if grep -n $'\r' "${SHELL_FILES[@]}"; then
  echo "CRLF found in shell scripts; expected LF" >&2
  exit 1
fi

echo "all checks passed"
