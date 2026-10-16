# T5 — 实机端到端验收

- **状态**：todo（依赖真机）
- **阻塞于**：T1、T2、T3
- **阻塞**：无

## 范围

在真实 Steam Frame 上验证：

1. timer 周期性触发；`:8080` 全部 target 被注入 `gate`，`barpopup` 额外注入 `ui`；
2. `barpopup` 出现「Controller UI Haptic」开关，UI 正常、可点；
3. 关掉开关后，**Steam 客户端 UI 页面的按钮 hover 不再震动**；打开恢复；
4. **localStorage 跨 target 共享**实测（一个页面写、另一个页面读到同一值）；
5. **localStorage 跨 Steam 客户端重启保留**实测；
6. 跨页面 reload 后注入与状态正确恢复；
7. 卸载后界面干净。

## 验收标准

- 各项**明确通过/失败 + 证据**（日志、录屏/截图）。
- 失败项回流成新 ticket 或修订 T1/T2/T3。
