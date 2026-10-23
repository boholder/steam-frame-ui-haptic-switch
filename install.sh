#!/usr/bin/env bash
# Steam Frame "Controller UI Haptic" switch — install script (resident daemon)
# 
#   1. Check the desktop user and remove a legacy system-wide unit
#   2. Install the user unit and the steamvr.service.wants symlink
#   3. Open SteamVR's vrwebhelper debugging port
#   4. Start the daemon and confirm it stays up
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/src"

LIB="$HERE/installation_lib.sh"
[ -f "$LIB" ] || { printf 'missing %s\n' "$LIB" >&2; exit 1; }
# shellcheck source=installation_lib.sh
. "$LIB"

# --------------------------------------------------------------------------
step "1/4  Check the desktop user"
# --------------------------------------------------------------------------
require_install_user
[ -d "$SF_RUNTIME_DIR" ] \
  || die "the user systemd instance for $SF_USER is not running ($SF_RUNTIME_DIR missing); log in as $SF_USER first"
ok "installing for $SF_USER ($SF_HOME)"

# The previous release installed a system-wide unit. Running both would make
# two daemons inject into the same pages, so migrate it away first.
remove_legacy_unit \
  || die "legacy system unit found; remove it first: sudo systemctl disable --now $SERVICE && sudo rm $LEGACY_UNIT_DIR/$SERVICE"

# --------------------------------------------------------------------------
step "2/4  Install the user unit and the SteamVR wants link"
# --------------------------------------------------------------------------
# The checkout is the installation directory: nothing is copied; the unit
# points straight at $SRC.
as_user mkdir -p "$USER_UNIT_DIR" "$STEAMVR_WANTS_DIR"
as_user install -m 0644 "$SRC/$SERVICE" "$USER_UNIT_DIR/$SERVICE"
as_user sed -i "s|INSTALLATION_PATH|$DEST|g" "$USER_UNIT_DIR/$SERVICE"
if grep -q 'INSTALLATION_PATH' "$USER_UNIT_DIR/$SERVICE"; then
  die "INSTALLATION_PATH in the unit was not replaced"
fi
as_user ln -sfn "$USER_UNIT_DIR/$SERVICE" "$UNIT_LINK"
ok "wrote $USER_UNIT_DIR/$SERVICE (pointing at $SRC)"
ok "linked $UNIT_LINK -> $USER_UNIT_DIR/$SERVICE"

# --------------------------------------------------------------------------
step "3/4  Enable the SteamVR Dashboard debug port by changing config"
# --------------------------------------------------------------------------
# Only touches VRWebHelper.DebuggerEnabled; other keys such as
# driver_deckard.showDevTools are left alone.
if [ -f "$CFG" ]; then had_cfg=1; else had_cfg=0; fi
if as_user python3 "$SRC/sf_ui_haptic_switch_settings.py" --enable "$CFG"; then
  if [ "$had_cfg" -eq 1 ]; then
    ok "VRWebHelper.DebuggerEnabled = true (backup: $CFG.bak)"
  else
    ok "VRWebHelper.DebuggerEnabled = true (created $CFG)"
  fi
else
  if [ "$had_cfg" -eq 1 ]; then
    warn "could not write $CFG; :8087 will not be enabled. Original backed up at $CFG.bak"
  else
    warn "could not create $CFG; :8087 will not be enabled"
  fi
fi

if pgrep -f '[v]rserver' >/dev/null 2>&1; then
  warn "SteamVR is running: config written, but :8087 will listen only after SteamVR restarts"
else
  ok "SteamVR is not running; :8087 will listen on next start"
fi

# --------------------------------------------------------------------------
step "4/4  Start the daemon"
# --------------------------------------------------------------------------
userctl daemon-reload
userctl restart "$SERVICE"

for _ in $(seq 1 20); do
  userctl is-active --quiet "$SERVICE" && break
  sleep 0.5
done

if ! userctl is-active --quiet "$SERVICE"; then
  bad "$SERVICE did not start; recent logs:"
  journalctl --user-unit "$SERVICE" -n 30 --no-pager -o cat | sed 's/^/     /' || true
  exit 1
fi
ok "daemon is running"

sleep 1
check_endpoint() {
  local port="$1" owner="$2"
  if journalctl --user-unit "$SERVICE" -n 50 --no-pager -o cat 2>/dev/null \
       | grep -q "attached endpoint :$port"; then
    ok "connected to :$port"
  else
    warn "not connected to :$port yet ($owner not running or not restarted? the daemon retries automatically)"
  fi
}
check_endpoint 8080 "Steam"
check_endpoint 8087 "SteamVR"

cat <<EOF

────────────────────────────────────────────────────────────
Installation complete

The daemon is a user service and follows SteamVR: it starts with
steamvr.service and stops when SteamVR stops. Its unit lives in
$USER_UNIT_DIR, with an enable link in $STEAMVR_WANTS_DIR.

Put on the headset, open the Quick Settings menu, and switch to the
Settings tab. The "Controller UI Haptic" switch should appear at the
bottom. Turn it off and the controller cursor stops buzzing when it
touches buttons in the UI.

If :8087 was just configured, restart SteamVR once so the Dashboard
debug port appears.

- view daemon logs
journalctl --user-unit sf-ui-haptic-switch.service -f
- show switch state and endpoint status
python3 $SRC/sf_ui_haptic_switch_daemon.py --status
- uninstall
sudo ./uninstall.sh
────────────────────────────────────────────────────────────
EOF
