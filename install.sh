#!/usr/bin/env bash
# Steam Frame 「Controller UI Haptic」开关 —— 安装脚本（常驻 daemon）
#
#   sudo ./install.sh
#
#   1. 校验权限与安装包
#   2. 拷贝两个前端脚本 + daemon + 一个 systemd 单元
#   3. 迁移旧版本（停用/删除 timer 与 oneshot 注入器）
#   4. 打开 SteamVR 的 vrwebhelper 调试端口
#   5. 启动 daemon 并确认它活着
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/src"
[ -d "$SRC" ] || SRC="$HERE"

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

# --------------------------------------------------------------------------
step "1/5  检查权限与安装包"
# --------------------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || die "需要 root：sudo $0"
ok "以 root 运行"

command -v python3 >/dev/null || die "找不到 python3"
command -v systemctl >/dev/null || die "找不到 systemctl"
ok "python3 + systemctl 就绪"

for f in sf_ui_haptic_switch_gate.js sf_ui_haptic_switch_ui.js \
         sf_ui_haptic_switch_daemon.py sf_ui_haptic_switch_settings.py \
         sf-ui-haptic-switch.service; do
  [ -f "$SRC/$f" ] || die "安装包不完整，缺少 $SRC/$f"
done
ok "安装包完整"

# --------------------------------------------------------------------------
step "2/5  复制文件到 $DEST"
# --------------------------------------------------------------------------
# 文件由 root 拥有、可被所有人读；服务以 User=steamos 运行，只读这些文件。
install -d "$DEST"

copy() {  # copy <文件名> <权限>
  local src="$SRC/$1" mode="$2" dst="$DEST/$1"
  if [ "$(readlink -f "$src")" = "$(readlink -f "$dst" 2>/dev/null || echo "$dst")" ]; then
    chmod "$mode" "$dst"
  else
    install -m "$mode" "$src" "$dst"
  fi
  ok "$1  ($mode)"
}

copy sf_ui_haptic_switch_gate.js     0644
copy sf_ui_haptic_switch_ui.js       0644
copy sf_ui_haptic_switch_daemon.py   0755
copy sf_ui_haptic_switch_settings.py 0644

# 旧版本的 oneshot 注入器与 timer 不再使用
rm -f "$DEST/sf_ui_haptic_switch_inject.py" "$DEST/sf-ui-haptic-switch.timer"

install -m 0644 "$SRC/$SERVICE" "$UNIT_DIR/$SERVICE"
sed -i "s#/home/steamos/sf-ui-haptic-switch#$DEST#g" "$UNIT_DIR/$SERVICE"
ok "单元已写入 $UNIT_DIR/$SERVICE"

for extra in README.md; do
  [ -f "$HERE/$extra" ] || continue
  install -m 0644 "$HERE/$extra" "$DEST/$extra"
  ok "$extra"
done

# --------------------------------------------------------------------------
step "3/5  迁移旧版本（timer / oneshot 注入器）"
# --------------------------------------------------------------------------
systemctl disable --now "$TIMER" 2>/dev/null || true
systemctl stop "$SERVICE" 2>/dev/null || true
systemctl disable "$SERVICE" 2>/dev/null || true
rm -f "$UNIT_DIR/$TIMER"
systemctl daemon-reload
ok "旧 timer 已停用并删除；旧 service 已停止"

# --------------------------------------------------------------------------
step "4/5  打开 SteamVR Dashboard 调试端口"
# --------------------------------------------------------------------------
# 只改 VRWebHelper.DebuggerEnabled；driver_deckard.showDevTools 等其它键不动。
if python3 "$SRC/sf_ui_haptic_switch_settings.py" --enable "$CFG"; then
  ok "VRWebHelper.DebuggerEnabled = true（备份：$CFG.bak）"
else
  warn "无法写入 $CFG；:8087 不会启用。原文件备份在 $CFG.bak"
fi

if pgrep -f '[v]rserver' >/dev/null 2>&1; then
  warn "SteamVR 正在运行：配置已写入，但需要重启 SteamVR 后 :8087 才会监听"
else
  ok "SteamVR 未运行；下次启动时 :8087 会监听"
fi

# --------------------------------------------------------------------------
step "5/5  启动 daemon"
# --------------------------------------------------------------------------
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"

for _ in $(seq 1 20); do
  systemctl is-active --quiet "$SERVICE" && break
  sleep 0.5
done

if ! systemctl is-active --quiet "$SERVICE"; then
  bad "$SERVICE 没有起来，最近日志："
  journalctl -u "$SERVICE" -n 30 --no-pager -o cat | sed 's/^/     /' || true
  exit 1
fi
ok "daemon 运行中"

sleep 1
if journalctl -u "$SERVICE" -n 50 --no-pager -o cat 2>/dev/null | grep -q 'attached endpoint :8080'; then
  ok "已连上 :8080"
else
  warn "还没连上 :8080（Steam 未运行？daemon 会自动重试，无需重装）"
fi
if journalctl -u "$SERVICE" -n 50 --no-pager -o cat 2>/dev/null | grep -q 'attached endpoint :8087'; then
  ok "已连上 :8087"
else
  warn "还没连上 :8087（SteamVR 未运行或还没重启？daemon 会自动重试）"
fi

cat <<'EOF'

────────────────────────────────────────────────────────────
 安装完成

 戴上头显，打开快速设置菜单（点底栏的电池图标），切到「设置」页，
 最下方会出现「Controller UI Haptic」开关。关掉它，手柄光标划过
 按钮时就不再震动；SteamVR Dashboard 里的按钮也会一起静音。

 如果刚写入 :8087 的配置，请重启一次 SteamVR，Dashboard 的调试端口
 才会出现；daemon 会自动接上，不需要重装。

     journalctl -u sf-ui-haptic-switch.service -f   查看 daemon 日志
     python3 /home/steamos/sf-ui-haptic-switch/sf_ui_haptic_switch_daemon.py --status
     sudo ./uninstall.sh                            卸载
────────────────────────────────────────────────────────────
EOF
