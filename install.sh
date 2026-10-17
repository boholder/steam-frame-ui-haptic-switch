#!/usr/bin/env bash
# Steam Frame "Controller UI Haptic" switch — install script (resident daemon)
#
#   sudo ./install.sh
#
#   1. Check privileges and the installation payload
#   2. Install the systemd unit (points at this checkout; no file copies)
#   3. Migrate old versions (stop/remove the timer and oneshot injector)
#   4. Open SteamVR's vrwebhelper debugging port
#   5. Start the daemon and confirm it stays up
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/src"

LIB="$HERE/installation_lib.sh"
[ -f "$LIB" ] || { printf 'missing %s\n' "$LIB" >&2; exit 1; }
# shellcheck source=installation_lib.sh
. "$LIB"

# --------------------------------------------------------------------------
step "1/5  Check privileges and the installation payload"
# --------------------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || die "root required: sudo $0"
ok "running as root"

command -v python3 >/dev/null || die "python3 not found"
command -v systemctl >/dev/null || die "systemctl not found"
ok "python3 + systemctl ready"

for f in sf_ui_haptic_switch_gate.js sf_ui_haptic_switch_ui.js \
         sf_ui_haptic_switch_daemon.py \
         sf_ui_haptic_switch_settings.py \
         sf-ui-haptic-switch.service; do
  [ -f "$SRC/$f" ] || die "incomplete installation payload: missing $SRC/$f"
done
ok "installation payload complete"

# --------------------------------------------------------------------------
step "2/5  Install the systemd unit"
# --------------------------------------------------------------------------
# The checkout is the installation directory: nothing is copied; the unit
# points straight at $SRC.
install -m 0644 "$SRC/$SERVICE" "$UNIT_DIR/$SERVICE"
sed -i "s|INSTALLATION_PATH|$DEST|g" "$UNIT_DIR/$SERVICE"
if grep -q 'INSTALLATION_PATH' "$UNIT_DIR/$SERVICE"; then
  die "INSTALLATION_PATH in the unit was not replaced"
fi
ok "wrote $UNIT_DIR/$SERVICE (pointing at $SRC)"

# --------------------------------------------------------------------------
step "3/5  Migrate old versions (timer / oneshot injector)"
# --------------------------------------------------------------------------
systemctl disable --now "$TIMER" 2>/dev/null || true
systemctl stop "$SERVICE" 2>/dev/null || true
systemctl disable "$SERVICE" 2>/dev/null || true
rm -f "$UNIT_DIR/$TIMER"
systemctl daemon-reload
ok "old timer disabled and removed; old service stopped"

# --------------------------------------------------------------------------
step "4/5  Enable the SteamVR Dashboard debug port"
# --------------------------------------------------------------------------
# Only touches VRWebHelper.DebuggerEnabled; other keys such as
# driver_deckard.showDevTools are left alone.
if [ -f "$CFG" ]; then had_cfg=1; else had_cfg=0; fi
if python3 "$SRC/sf_ui_haptic_switch_settings.py" --enable "$CFG"; then
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
step "5/5  Start the daemon"
# --------------------------------------------------------------------------
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"

for _ in $(seq 1 20); do
  systemctl is-active --quiet "$SERVICE" && break
  sleep 0.5
done

if ! systemctl is-active --quiet "$SERVICE"; then
  bad "$SERVICE did not start; recent logs:"
  journalctl -u "$SERVICE" -n 30 --no-pager -o cat | sed 's/^/     /' || true
  exit 1
fi
ok "daemon is running"

sleep 1
check_endpoint() {
  local port="$1" owner="$2"
  if journalctl -u "$SERVICE" -n 50 --no-pager -o cat 2>/dev/null \
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

  Put on the headset, open the Quick Settings menu (tap the battery
  icon in the bottom bar), and switch to the Settings tab. The
  "Controller UI Haptic" switch appears at the bottom. Turn it off
  and the controller cursor stops buzzing when it touches buttons;
  buttons in the SteamVR Dashboard are muted too.

  If :8087 was just configured, restart SteamVR once so the Dashboard
  debug port appears; the daemon connects automatically, no reinstall.

     journalctl -u sf-ui-haptic-switch.service -f   view daemon logs
     python3 $SRC/sf_ui_haptic_switch_daemon.py --status
     sudo ./uninstall.sh                            uninstall
────────────────────────────────────────────────────────────
EOF
