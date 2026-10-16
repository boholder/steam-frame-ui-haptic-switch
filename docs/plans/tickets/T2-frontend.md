# T2 — 前端拆分（gate + ui）

- **状态**：todo
- **阻塞于**：T0（已完成）
- **阻塞**：T5

## 背景

现为单个 `sf_haptic_switch.js`（UI + wrapper 合一、走 binding + `state.json`）。改为两个脚本，状态走 `localStorage`。

## 范围

- 新增 **`device/sf_ui_haptic_switch_gate.js`**：
  - 包 `window.SteamClient.OpenVR.TriggerOverlayHapticEffect`（存在才包，缺失即 no-op；`:8080` 无 `VRHTML`）；
  - **调用时**读 `localStorage.getItem('sf_ui_haptic_switch')`（`"0"` = 关；无值 = 开）；
  - 幂等守卫 + `restoreHaptic`。
- 新增 **`device/sf_ui_haptic_switch_ui.js`**：
  - 只在 `barpopup` 建开关（克隆原生行、`MutationObserver` 自愈、`sessionStorage` 模板缓存）；
  - 点按时写 `localStorage.setItem('sf_ui_haptic_switch', '0'|'1')`；
  - 初始化从 `localStorage` 读态渲染；**无 binding、无 pending/echo**。
- 删除 `device/sf_haptic_switch.js`；全局名 → `window.__SF_UI_HAPTIC_SWITCH__`；行 id/CSS id/缓存键 → 新前缀。

## 验收标准

- `esprima.parseScript` 解析通过（两个文件）；
- 与 T1 文件名一致；localStorage 键 `sf_ui_haptic_switch`；
- 实机由 T5 验收。
