#!/usr/bin/env bash
# Read-only probe of the two CEF DevTools endpoints on the Steam Frame.
# Requires the `frame` SSH alias (see scripts/setup-sf-ssh.sh).
set -euo pipefail

HOST="${SF_HOST:-frame}"

ssh "$HOST" 'for p in 8080 8087; do
  printf "\n== :%s /json/version\n" "$p"
  curl -s --max-time 5 "http://127.0.0.1:$p/json/version" || true
  printf "\n== :%s /json/list\n" "$p"
  curl -s --max-time 5 "http://127.0.0.1:$p/json/list" || true
  printf "\n"
done'
