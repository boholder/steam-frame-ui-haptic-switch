# T6 — 启用 `:8087`（SteamVR Dashboard）

- **状态**：backlog（本计划不做）
- **阻塞于**：无（调研先行）
- **阻塞**：无

## 背景

T0 实测：`:8087` 未监听，`vrwebhelper` 启动参数为 `-lang=… -forceOnPaint=cpu`，**不带 `--remote-debugging-port`**。设备上虽预置了 `socat 8088→8087` 转发（`steamvr-web-debug-portforward.service`），但没有上游可转。

## 目标

调研并实现「让 `vrwebhelper` 带调试端口启动」，从而可注入 SteamVR Dashboard（`systemui` 等）页面的 `window.VRHTML.VROverlay.TriggerOverlayHapticEffect`。

## 待查

- SteamVR 是否有允许开启 DevTools / 调试端口的配置（如 `driver_deckard.showDevTools`、`steamvr.vrsettings`、启动 wrapper）。
- 是否需要改动 `steamvr.service`/`vrwebhelper` 启动链（**属改设备状态，需授权**）。
- 启用后与 `:8080` 的 origin/状态如何协调（`:8087` profile 在 `~/.cache/SteamVR`，可能被清）。

## 验收（若实现）

- `:8087/json/version` 返回 browser 级 `webSocketDebuggerUrl`；能注入 `systemui` 并关闭其 hover 震动。
