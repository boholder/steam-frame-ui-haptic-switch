#!/usr/bin/env bash
# Shared constants and output helpers for install.sh / uninstall.sh.
# Sourced, not executed.

# The caller (install.sh / uninstall.sh) sets HERE to the checkout path.
: "${HERE:?HERE must be set before sourcing installation_lib.sh}"

# The installer runs in place: the checkout is the install destination.
DEST="$HERE"
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
