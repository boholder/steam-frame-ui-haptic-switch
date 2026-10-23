#!/usr/bin/env bash
# Shared constants and output helpers for install.sh / uninstall.sh.
# Sourced, not executed.
# The constants below are read by the sourcing scripts.
# shellcheck disable=SC2034

# The caller (install.sh / uninstall.sh) sets HERE to the checkout path.
: "${HERE:?HERE must be set before sourcing installation_lib.sh}"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die()  { bad "$*"; exit 1; }

# The installer runs in place: the checkout is the install destination.
DEST="$HERE"
SERVICE="sf-ui-haptic-switch.service"

# SteamVR is a user service, so the daemon must live in the desktop user's
# systemd instance: a system unit cannot depend on steamvr.service.
SF_USER="${SF_USER:-${SUDO_USER:-steamos}}"
if ! SF_UID="$(id -u "$SF_USER" 2>/dev/null)"; then
  die "unknown user: $SF_USER (set SF_USER to the desktop user)"
fi
SF_HOME="$(getent passwd "$SF_USER" | cut -d: -f6)"
[ -n "$SF_HOME" ] || die "cannot resolve the home directory of $SF_USER"
SF_RUNTIME_DIR="/run/user/$SF_UID"

USER_UNIT_DIR="$SF_HOME/.config/systemd/user"
STEAMVR_WANTS_DIR="$USER_UNIT_DIR/steamvr.service.wants"
UNIT_LINK="$STEAMVR_WANTS_DIR/$SERVICE"
LEGACY_UNIT_DIR="/etc/systemd/system"

STATE_DIR="$SF_HOME/.local/state/sf-ui-haptic-switch"
CFG="$SF_HOME/.config/openvr/config/steamvr.vrsettings"

# Run a command as the desktop user, even when the installer runs via sudo.
as_user() {
  if [ "$(id -u)" -eq "$SF_UID" ]; then
    "$@"
  else
    runuser -u "$SF_USER" -- env XDG_RUNTIME_DIR="$SF_RUNTIME_DIR" "$@"
  fi
}

userctl() { as_user systemctl --user "$@"; }

require_install_user() {
  if [ "$(id -u)" -eq 0 ]; then
    return 0
  fi
  [ "$(id -u)" -eq "$SF_UID" ] || die "run as $SF_USER or root: $0"
}

# The previous release installed a system-wide unit. Two daemons would inject
# into the same pages; remove it when we have the rights, otherwise tell the
# caller to abort.
remove_legacy_unit() {
  if [ ! -e "$LEGACY_UNIT_DIR/$SERVICE" ]; then
    return 0
  fi
  [ "$(id -u)" -eq 0 ] || return 1
  systemctl disable --now "$SERVICE" >/dev/null 2>&1 || true
  rm -f "$LEGACY_UNIT_DIR/$SERVICE"
  systemctl daemon-reload
  ok "removed the legacy system unit $LEGACY_UNIT_DIR/$SERVICE"
}
