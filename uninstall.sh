#!/usr/bin/env bash
# Steam Frame "Controller UI Haptic" switch — uninstall script
# 
# Stop the daemon (SIGTERM removes the injected UI/wrappers best-effort),
# remove the steamvr.service.wants link and the user unit, remove the :8087
# debug port setting, and clean up the state.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LIB="$HERE/installation_lib.sh"
[ -f "$LIB" ] || { printf 'missing %s\n' "$LIB" >&2; exit 1; }
# shellcheck source=installation_lib.sh
. "$LIB"

SRC="$HERE/src"

# --------------------------------------------------------------------------
step "1/4  Stop the daemon"
# --------------------------------------------------------------------------
require_install_user

# A unit from the previous system-wide install may still be running.
remove_legacy_unit \
  || die "legacy system unit found; remove it first: sudo systemctl disable --now $SERVICE && sudo rm $LEGACY_UNIT_DIR/$SERVICE"

if [ -d "$SF_RUNTIME_DIR" ]; then
  userctl disable --now "$SERVICE" >/dev/null 2>&1 || true
  # SIGTERM lets the daemon tear down both endpoints best-effort.
  sleep 1
  state="$(userctl show "$SERVICE" -p ActiveState --value 2>/dev/null)" || state="unknown"
  case "$state" in
    active | activating | deactivating | reloading | unknown)
      die "$SERVICE is not confirmed stopped (state: $state); aborting uninstall so the daemon cannot keep injecting"
      ;;
  esac
  ok "stopped $SERVICE"
else
  warn "the user systemd instance for $SF_USER is not running; skipping the stop"
fi

# --------------------------------------------------------------------------
step "2/4  Disable the SteamVR Dashboard debug port by changing config"
# --------------------------------------------------------------------------
# Remove only VRWebHelper.DebuggerEnabled; if VRWebHelper becomes empty,
# remove the empty section too. The .bak file is not restored.
SETTINGS="$SRC/sf_ui_haptic_switch_settings.py"

if pgrep -f '[v]rserver' >/dev/null 2>&1; then
  warn "SteamVR is running; after DebuggerEnabled is removed, SteamVR may write its in-memory config back on exit"
fi

if [ ! -f "$SETTINGS" ]; then
  warn "settings helper not found; skipping config cleanup"
elif [ ! -f "$CFG" ]; then
  warn "$CFG not found; skipping"
elif as_user python3 "$SETTINGS" --disable "$CFG"; then
  ok "removed VRWebHelper.DebuggerEnabled from $CFG (.bak kept)"
else
  warn "could not modify $CFG; remove VRWebHelper.DebuggerEnabled manually"
fi

# --------------------------------------------------------------------------
step "3/4  Remove the SteamVR wants link, the unit and the state"
# --------------------------------------------------------------------------
as_user rm -f "$UNIT_LINK"
as_user rm -f "$USER_UNIT_DIR/$SERVICE"
if [ -d "$SF_RUNTIME_DIR" ]; then
  userctl daemon-reload
  userctl reset-failed "$SERVICE" 2>/dev/null || true
fi
ok "removed $UNIT_LINK"
ok "removed $USER_UNIT_DIR/$SERVICE"

if [ -d "$STATE_DIR" ]; then
  rm -rf "$STATE_DIR"
  ok "removed state directory $STATE_DIR"
fi

# --------------------------------------------------------------------------
step "4/4  Done"
# --------------------------------------------------------------------------
running() {
  command -v curl >/dev/null \
    && curl -s --max-time 2 "http://127.0.0.1:$1/json/list" >/dev/null 2>&1
}
if running 8080; then
  warn "Steam is still running: if the switch is still visible in the headset, restart the Steam or whole Frame to clean up"
else
  ok "Steam is not running; pages will be clean on next start"
fi
if running 8087; then
  warn "SteamVR is still running: Dashboard wrappers disappear after the next reload"
fi

cat <<'EOF'

────────────────────────────────────────────────────────────
  Uninstall complete

  Controller haptics are back to stock; the :8087 debug port
  setting has been removed. The config backup (.bak) is kept;
  restore it manually if needed.
────────────────────────────────────────────────────────────
EOF
