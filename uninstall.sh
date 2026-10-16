#!/usr/bin/env bash
# Steam Frame 「Controller UI Haptic」开关 —— 卸载脚本
#
#   sudo ./uninstall.sh
#
# 会停掉定时器与注入器、尽力摘掉界面上的开关与震动包装，再删掉所有装上去的文件。
set -euo pipefail

DEST="/home/steamos/sf-ui-haptic-switch"
SERVICE="sf-ui-haptic-switch.service"
TIMER="sf-ui-haptic-switch.timer"
UNIT_DIR="/etc/systemd/system"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die()  { bad "$*"; exit 1; }

[ "$(id -u)" -eq 0 ] || die "需要 root：sudo $0"

# --------------------------------------------------------------------------
step "1/4  停止并取消定时器"
# --------------------------------------------------------------------------
if systemctl list-unit-files "$TIMER" >/dev/null 2>&1 || [ -f "$UNIT_DIR/$TIMER" ]; then
  systemctl disable --now "$TIMER" 2>/dev/null || true
  ok "已停用 $TIMER"
else
  warn "没有找到 $TIMER（可能本来就没装）"
fi
systemctl disable "$SERVICE" 2>/dev/null || true   # no [Install]: no-op, kept for symmetry
systemctl stop "$SERVICE" 2>/dev/null || true
ok "已停止 $SERVICE"

# --------------------------------------------------------------------------
step "2/4  尽力摘掉界面上的开关与包装"
# --------------------------------------------------------------------------
INJ="$DEST/sf_ui_haptic_switch_inject.py"
if [ -f "$INJ" ]; then
  if python3 "$INJ" --teardown; then
    ok "已向 :8080 页面发出 teardown（若端点没开则无操作）"
  else
    warn "teardown 未完成（Steam 可能没在运行）；页面下次 reload 会自然干净"
  fi
else
  warn "$INJ 不存在，跳过 teardown"
fi

# --------------------------------------------------------------------------
step "3/4  删除文件"
# --------------------------------------------------------------------------
rm -f "$UNIT_DIR/$TIMER" "$UNIT_DIR/$SERVICE"
ok "删除 $UNIT_DIR/$TIMER 与 $UNIT_DIR/$SERVICE"
systemctl daemon-reload
systemctl reset-failed "$SERVICE" 2>/dev/null || true
ok "systemd 已重新加载"

if [ -d "$DEST" ]; then
  case "$DEST" in
    /|/home|/home/*/|"") die "拒绝删除 $DEST（路径不合理）" ;;
  esac
  if [ -f "$DEST/sf_ui_haptic_switch_inject.py" ] || [ -f "$DEST/sf_ui_haptic_switch_gate.js" ]; then
    rm -rf "$DEST"
    ok "删除 $DEST"
  else
    warn "$DEST 存在但不像我们的安装目录，保留不动"
  fi
else
  warn "$DEST 不存在"
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

cat <<'EOF'

────────────────────────────────────────────────────────────
 卸载完成

 手柄震动恢复出厂状态，和没装过一样。
────────────────────────────────────────────────────────────
EOF
