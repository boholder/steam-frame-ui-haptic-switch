#!/usr/bin/env bash
# Steam Frame 「Controller UI Haptic」开关 —— 安装脚本（timer + 一次性注入器）
#
#   sudo ./install.sh
#
# 装成「定时器周期触发的注入器」，无常驻进程：
#   1. 校验权限与文件
#   2. 拷贝两个前端脚本 + 注入器 + 两个 systemd 单元
#   3. daemon-reload 并 enable --now 定时器
#   4. 立即跑一次，确认注入成功
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/src"
[ -d "$SRC" ] || SRC="$HERE"

DEST="/home/steamos/sf-ui-haptic-switch"
SERVICE="sf-ui-haptic-switch.service"
TIMER="sf-ui-haptic-switch.timer"
UNIT_DIR="/etc/systemd/system"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die()  { bad "$*"; exit 1; }

# --------------------------------------------------------------------------
step "1/4  检查权限与安装包"
# --------------------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || die "需要 root：sudo $0"
ok "以 root 运行"

command -v python3 >/dev/null || die "找不到 python3"
command -v systemctl >/dev/null || die "找不到 systemctl"
ok "python3 + systemctl 就绪"

for f in sf_ui_haptic_switch_gate.js sf_ui_haptic_switch_ui.js \
         sf_ui_haptic_switch_inject.py \
         sf-ui-haptic-switch.service sf-ui-haptic-switch.timer; do
  [ -f "$SRC/$f" ] || die "安装包不完整，缺少 $SRC/$f"
done
ok "安装包完整"

# --------------------------------------------------------------------------
step "2/4  复制文件到 $DEST"
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

copy sf_ui_haptic_switch_gate.js   0644
copy sf_ui_haptic_switch_ui.js     0644
copy sf_ui_haptic_switch_inject.py 0755

# 单元里写死了 /home/steamos/sf-ui-haptic-switch；装到别处就整体改掉
install -m 0644 "$SRC/$SERVICE" "$UNIT_DIR/$SERVICE"
install -m 0644 "$SRC/$TIMER"   "$UNIT_DIR/$TIMER"
sed -i "s#/home/steamos/sf-ui-haptic-switch#$DEST#g" "$UNIT_DIR/$SERVICE"
ok "单元已写入 $UNIT_DIR/"

for extra in README.md; do
  [ -f "$HERE/$extra" ] || continue
  install -m 0644 "$HERE/$extra" "$DEST/$extra"
  ok "$extra"
done

# --------------------------------------------------------------------------
step "3/4  启用并启动定时器"
# --------------------------------------------------------------------------
systemctl stop "$SERVICE" 2>/dev/null || true
systemctl disable "$TIMER" 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now "$TIMER"
ok "已启用并启动 $TIMER"

# --------------------------------------------------------------------------
step "4/4  立即跑一次，确认注入"
# --------------------------------------------------------------------------
if systemctl start "$SERVICE"; then
  RESULT="$(systemctl show -p Result --value "$SERVICE" 2>/dev/null)"
  if [ "$RESULT" = "success" ]; then
    ok "注入器成功运行（结果：success）"
  else
    warn "注入器运行但结果：${RESULT:-未知}"
  fi
else
  warn "注入器这次运行失败——通常是 :8080 没在监听（Steam 未运行）"
  systemctl status "$SERVICE" --no-pager -l 2>/dev/null | sed 's/^/     /' || true
fi

if ! systemctl is-active --quiet "$TIMER"; then
  bad "$TIMER 未在运行，最近日志："
  journalctl -u "$SERVICE" -n 20 --no-pager -o cat | sed 's/^/     /' || true
  exit 1
fi
ok "定时器运行中（每约 3s 一拍；Steam 起来后会自动注入，无需重装）"

cat <<'EOF'

────────────────────────────────────────────────────────────
 安装完成

 戴上头显，打开快速设置菜单（点底栏的电池图标），切到「设置」页，
 最下方会出现「Controller UI Haptic」开关。关掉它，手柄光标划过
 按钮时就不再震动。

     journalctl -u sf-ui-haptic-switch.service -f   查看每次注入
     systemctl status sf-ui-haptic-switch.timer    查看定时器
     sudo ./uninstall.sh                            卸载
────────────────────────────────────────────────────────────
EOF
