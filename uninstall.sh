#!/usr/bin/env bash
# Steam Frame 「Controller UI Haptic」开关 —— 卸载脚本
#
#   sudo ./uninstall.sh
#
# 停 daemon（SIGTERM 会尽力摘掉注入的 UI/包装）、删除 :8087 调试端口开关、
# 清理 old timer、单元、安装目录与状态文件。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LIB="$HERE/src/sf_ui_haptic_switch_lib.sh"
[ -f "$LIB" ] || LIB="$HERE/sf_ui_haptic_switch_lib.sh"
# shellcheck source=src/sf_ui_haptic_switch_lib.sh
. "$LIB"

[ "$(id -u)" -eq 0 ] || die "需要 root：sudo $0"

# --------------------------------------------------------------------------
step "1/4  停止 daemon 与旧 timer"
# --------------------------------------------------------------------------
systemctl disable --now "$TIMER" 2>/dev/null || true
systemctl disable "$SERVICE" 2>/dev/null || true
systemctl stop "$SERVICE" 2>/dev/null || true
# SIGTERM 让 daemon 对两个端点尽力 teardown。
sleep 1
ok "已停止 $SERVICE（并清理旧 $TIMER）"

# --------------------------------------------------------------------------
step "2/4  关闭 SteamVR Dashboard 调试端口"
# --------------------------------------------------------------------------
# 只删除 VRWebHelper.DebuggerEnabled；若 VRWebHelper 变空，连空节一起删。
# 不恢复 .bak。
SETTINGS="$DEST/sf_ui_haptic_switch_settings.py"
[ -f "$SETTINGS" ] || SETTINGS="$HERE/src/sf_ui_haptic_switch_settings.py"

if [ ! -f "$SETTINGS" ]; then
  warn "找不到 settings helper，跳过配置清理"
elif [ ! -f "$CFG" ]; then
  warn "$CFG 不存在，跳过"
elif python3 "$SETTINGS" --disable "$CFG"; then
  ok "已从 $CFG 删除 VRWebHelper.DebuggerEnabled（.bak 保留）"
else
  warn "无法修改 $CFG；请手动删除 VRWebHelper.DebuggerEnabled"
fi

# --------------------------------------------------------------------------
step "3/4  删除单元、文件与状态"
# --------------------------------------------------------------------------
rm -f "$UNIT_DIR/$TIMER" "$UNIT_DIR/$SERVICE"
systemctl daemon-reload
systemctl reset-failed "$SERVICE" 2>/dev/null || true
ok "删除 $UNIT_DIR/$TIMER 与 $UNIT_DIR/$SERVICE"

if [ -d "$DEST" ]; then
  case "$DEST" in
    /|/home|/home/*/|"") die "拒绝删除 $DEST（路径不合理）" ;;
  esac
  if [ -f "$DEST/sf_ui_haptic_switch_daemon.py" ] || [ -f "$DEST/sf_ui_haptic_switch_gate.js" ]; then
    rm -rf "$DEST"
    ok "删除 $DEST"
  else
    warn "$DEST 存在但不像我们的安装目录，保留不动"
  fi
else
  warn "$DEST 不存在"
fi

if [ -d "$STATE_DIR" ]; then
  rm -rf "$STATE_DIR"
  ok "删除状态目录 $STATE_DIR"
fi

# --------------------------------------------------------------------------
step "4/4  完成"
# --------------------------------------------------------------------------
if command -v curl >/dev/null && curl -s --max-time 2 \
     http://127.0.0.1:8080/json/list >/dev/null 2>&1; then
  warn "Steam 仍在运行：若头显里仍能看到开关，重启一次 Steam 客户端即可清掉"
else
  ok "Steam 未运行，下次启动时页面是干净的"
fi
if command -v curl >/dev/null && curl -s --max-time 2 \
     http://127.0.0.1:8087/json/list >/dev/null 2>&1; then
  warn "SteamVR 仍在运行：Dashboard 页面上的包装会在下一次 reload 后消失"
fi

cat <<'EOF'

────────────────────────────────────────────────────────────
 卸载完成

 手柄震动恢复出厂状态；:8087 的调试端口开关已删除。
 配置文件备份（.bak）保留，如需恢复可手动改回。
────────────────────────────────────────────────────────────
EOF
