# T1 — 注入器（timer + oneshot）

- **状态**：todo
- **阻塞于**：T0（已完成）
- **阻塞**：T3、T5

## 范围

文件：`device/sf_haptic_switch_daemon.py` → **`device/sf_ui_haptic_switch_inject.py`**（改名，折进本 ticket）。

一次性执行，每次：

1. `GET http://127.0.0.1:8080/json/list` 枚举全部 `type=="page"` 的 target；
2. 对每个 target：建 WebSocket（`webSocketDebuggerUrl`）→ `Runtime.evaluate` 注入脚本 → 关闭；
   - target 是 `barpopup` → 注入 `sf_ui_haptic_switch_ui.js`；
   - 其余 → 注入 `sf_ui_haptic_switch_gate.js`；
3. 端口未起 / 无 target → **静默退 0**；真正异常才非零。

脚本注入需**幂等**（脚本自身带 `build` 标记，重复注入不重复构建）。

## 保留 / 删除

- 保留：`WebSocket`（RFC6455）、CDP 封装、`evaluate`、build 哈希。
- 删除：`HapticSwitch`/`state.json`、主循环/`POLL_INTERVAL`、`push`、`Runtime.addBinding`/`BINDING_NAME`、`handle_commands`/`execute`、信号处理。
- 日志前缀 `[sf-ui-haptic-switch]`。

## 验收标准

- `python -m py_compile` 通过；
- 与前端一致：脚本文件名、localStorage 键名；
- 实机由 T5 验收。
