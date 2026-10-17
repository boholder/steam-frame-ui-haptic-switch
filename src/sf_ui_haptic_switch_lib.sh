#!/usr/bin/env bash
# Shared constants and output helpers for install.sh / uninstall.sh.
# Sourced, not executed.

DEST="/home/steamos/sf-ui-haptic-switch"
SERVICE="sf-ui-haptic-switch.service"
TIMER="sf-ui-haptic-switch.timer"
UNIT_DIR="/etc/systemd/system"
CFG="/home/steamos/.config/openvr/config/steamvr.vrsettings"
STATE_DIR="/home/steamos/.local/state/sf-ui-haptic-switch"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die()  { bad "$*"; exit 1; }
