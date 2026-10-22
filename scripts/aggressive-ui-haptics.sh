#!/usr/bin/env bash
# Aggressive UI-haptics off: mask the compositor's lasermouse haptic binding.
#
#   sudo ./scripts/aggressive-ui-haptics.sh apply
#   sudo ./scripts/aggressive-ui-haptics.sh persist
#   sudo ./scripts/aggressive-ui-haptics.sh revert
#        ./scripts/aggressive-ui-haptics.sh status
#
# Scope: this mutes ALL laser-mouse UI haptics (button hover, sliders, snap,
# slider edges). Game / OpenXR / Steam Input haptics use different action sets
# and are not affected. SteamVR reads the binding at startup; restart SteamVR
# after apply/revert.
#
# SteamOS mounts / on a read-only btrfs subvolume, so /opt/steamvr cannot be
# edited in place even as root. Instead, a patched copy is bind-mounted over
# the original file. `persist` installs a systemd unit so the bind mount is
# recreated at boot; `unpersist` removes only the unit. SteamVR updates replace
# the file under the mount; run revert + apply after an update.
set -euo pipefail

TARGET="/opt/steamvr/drivers/frame_controller/resources/input/vrcompositor_bindings_frame_controller.json"
STATE_DIR="/var/lib/sf-ui-haptic-switch"
COPY="$STATE_DIR/vrcompositor_bindings_frame_controller.json"
UNIT="/etc/systemd/system/sf-ui-haptic-binding.service"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
usage: sudo ./scripts/aggressive-ui-haptics.sh <command>

commands:
  apply      bind-mount a copy with /actions/lasermouse haptics removed
  persist    install + enable the systemd unit that recreates the mount at boot
  unpersist  remove the unit (the current mount is left in place)
  revert     unmount and remove the patched copy
  status     show mount/haptics state (no root needed)

After apply/revert, restart SteamVR for the compositor to reload bindings.
EOF
}

is_mounted() {
  [ "$(findmnt -n -o TARGET -T "$TARGET" 2>/dev/null || true)" = "$TARGET" ]
}

# 0 = haptics present, 1 = already removed, 2 = unreadable/invalid
haptics_present() {
  python3 - "$1" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
except Exception as exc:
    print("cannot read %s: %s" % (sys.argv[1], exc), file=sys.stderr)
    raise SystemExit(2)

laser = data.get("bindings", {}).get("/actions/lasermouse")
if not isinstance(laser, dict):
    print("missing /actions/lasermouse in %s" % sys.argv[1], file=sys.stderr)
    raise SystemExit(2)

raise SystemExit(0 if laser.get("haptics") else 1)
PY
}

patch_copy() {
  python3 - "$1" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    laser = data["bindings"]["/actions/lasermouse"]
    if not laser.get("haptics"):
        raise SystemExit("lasermouse haptics already absent")
    del laser["haptics"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
except Exception as exc:
    raise SystemExit("patch failed: %s" % exc)
PY
}

status() {
  if is_mounted; then
    echo "mount:   bind (patched copy)"
  else
    echo "mount:   original"
  fi
  if haptics_present "$TARGET"; then
    echo "haptics: present"
  else
    rc=$?
    if [ "$rc" -eq 1 ]; then
      echo "haptics: absent (patched)"
    else
      die "cannot inspect $TARGET"
    fi
  fi
  [ -f "$COPY" ] && echo "copy:    $COPY" || echo "copy:    none"
  [ -f "$UNIT" ] && echo "unit:    $UNIT" || echo "unit:    none"
}

apply() {
  [ "$(id -u)" -eq 0 ] || die "root required: sudo $0 apply"
  command -v python3 >/dev/null 2>&1 || die "python3 not found"
  command -v mount >/dev/null 2>&1 || die "mount not found"
  [ -f "$TARGET" ] || die "missing $TARGET"

  if is_mounted; then
    echo "already applied (bind mount is active)."
    echo "to refresh after a SteamVR update: sudo $0 revert && sudo $0 apply"
    return 0
  fi

  install -d -m 0755 "$STATE_DIR"
  cp -p "$TARGET" "$COPY"
  patch_copy "$COPY"
  mount --bind "$COPY" "$TARGET"
  is_mounted || die "bind mount did not take effect"
  if haptics_present "$TARGET"; then
    die "patched copy is not visible through the mount"
  fi

  echo "applied: /actions/lasermouse haptics removed (bind mount)"
  echo "copy:    $COPY"
  echo "scope:   ALL laser-mouse UI haptics (hover, slider, snap, edges)"
  echo "next:    restart SteamVR for the compositor to reload bindings"
  echo "persist: sudo $0 persist  # recreate the mount at boot"
}

persist() {
  [ "$(id -u)" -eq 0 ] || die "root required: sudo $0 persist"
  [ -f "$COPY" ] || die "run apply first"
  cat > "$UNIT" <<EOF
[Unit]
Description=Mask SteamVR laser-mouse haptics for sf-ui-haptic-switch
After=local-fs.target
RequiresMountsFor=$COPY
Before=graphical.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/mount --bind $COPY $TARGET
ExecStop=/bin/umount $TARGET

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "$UNIT" >/dev/null
  if ! is_mounted; then
    systemctl start "$UNIT"
  fi
  echo "persisted: $UNIT"
}

unpersist() {
  [ "$(id -u)" -eq 0 ] || die "root required: sudo $0 unpersist"
  if [ -f "$UNIT" ]; then
    systemctl disable "$UNIT" >/dev/null 2>&1 || true
    rm -f "$UNIT"
    systemctl daemon-reload
    echo "removed $UNIT (current mount, if any, was left in place)"
  else
    echo "no unit installed"
  fi
}

revert() {
  [ "$(id -u)" -eq 0 ] || die "root required: sudo $0 revert"
  if [ -f "$UNIT" ]; then
    systemctl disable "$UNIT" >/dev/null 2>&1 || true
    rm -f "$UNIT"
    systemctl daemon-reload
  fi
  if is_mounted; then
    umount "$TARGET"
  fi
  rm -f "$COPY"
  if haptics_present "$TARGET"; then
    echo "reverted: original binding is visible again"
    echo "next:     restart SteamVR for the compositor to reload bindings"
  else
    die "revert failed: original binding is not visible"
  fi
}

case "${1:-}" in
  apply) apply ;;
  persist) persist ;;
  unpersist) unpersist ;;
  revert) revert ;;
  status) status ;;
  -h|--help|help) usage ;;
  *) usage; exit 2 ;;
esac
