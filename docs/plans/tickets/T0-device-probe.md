# T0 — 设备事实探测 ✅

- **状态**：**完成**
- **阻塞于**：无
- **阻塞**：T1、T2、T5（已满足）

## 结果

完整报告见 `docs/research/t0-device-probe.md`。要点：

| # | 事实 | 结论 |
| --- | --- | --- |
| 1 | 两端点都有 browser 级 `webSocketDebuggerUrl` | **否** — `:8080` 有；`:8087` 未监听 |
| 2 | `/json/list` | 仅 `:8080`（11 个 page） |
| 3 | `setAutoAttach(flatten)` + `sessionId` | **是**（`:8080`） |
| 4a | `:8087` 有 `VRHTML.VROverlay.TriggerOverlayHapticEffect` | **未知**（端点没开） |
| 4b | `:8080` 有 `SteamClient.OpenVR.TriggerOverlayHapticEffect` | **是**；origin 恒为 `https://steamloopback.host` |
| 5 | `:8080` 有 `barpopup` | **是** |
| 6 | reload 收到导航事件 | **是**（`:8080`） |

## 影响（已并入计划）

- **本次只做 `:8080`**；`:8087` 延后（见 T6）。
- `:8080` 全部页面**同源** → 可用 `localStorage`（计划改用它）。
- 桥接名字是 **`SteamClient.OpenVR.TriggerOverlayHapticEffect`**（不是 `VRHTML.VROverlay`）。

## 仍未测（留给 T5）

- `:8080` localStorage 跨 target 共享 / 跨客户端重启保留；
- `SteamClient.OpenVR.TriggerOverlayHapticEffect` 是否为 hover 震动的实际入口；
- `barpopup` 跨重启稳定性。
