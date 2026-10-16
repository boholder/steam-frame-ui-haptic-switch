# T3 — 单元与脚本

- **状态**：todo
- **阻塞于**：T1
- **阻塞**：T5

## 范围

- 删除 `device/sf-haptic-switchd.service`；
- 新增 `device/sf-ui-haptic-switch.service`：`Type=oneshot`、`User=steamos`、`ExecStart=/usr/bin/python3 /home/steamos/sf-ui-haptic-switch/sf_ui_haptic_switch_inject.py`；
- 新增 `device/sf-ui-haptic-switch.timer`：`OnBootSec=…`、`OnUnitInactiveSec=3s`、`WantedBy=timers.target`（同名绑定 service）；
- `install.sh`：按 §6 命名安装两个 JS + 注入器 + 两个单元到 `/home/steamos/sf-ui-haptic-switch/`；`daemon-reload` + `enable --now sf-ui-haptic-switch.timer`；安装后跑一次并看日志；
- `uninstall.sh`：`disable --now` timer + service；尽力对 `:8080` 所有 target `teardown`；删单元与安装目录；
- `diagnose.sh`：当前不存在，本 ticket 不重建。

## 验收标准

- `bash -n install.sh uninstall.sh` 通过；
- 文件名/路径与 §6 一致；
- 实机安装/卸载由 T5 验收。
