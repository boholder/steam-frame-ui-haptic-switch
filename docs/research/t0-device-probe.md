# T0 — 设备事实探测

> 说明：本文档的原始章节 1–6（`:8080` 首轮探测）已在 commit `6bf714b`（"prune
> outdated research notes"）中删除，且从未进入 git 历史。结论摘要仍保留在
> `docs/plans/tickets/T0-device-probe.md`。以下为按需重新追加的 **第 7 节**。

## 7. :8087 端点（启用后复测）

`:8087`（SteamVR `vrwebhelper`）此前未监听，此轮复测时已可访问。本节仅做**只读**探测，
未改动设备上任何状态。

### 7.1 端点身份

命令：

```sh
ssh -o BatchMode=yes -o ConnectTimeout=10 frame "curl -s http://127.0.0.1:8087/json/version"
```

原始输出：

```json
{
   "Browser": "Chrome/126.0.6478.183",
   "Protocol-Version": "1.3",
   "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) SteamVR HTML Safari/537.36",
   "V8-Version": "12.6.228.28",
   "WebKit-Version": "537.36 (@2e81dea53f5ecdc1c057e4c70ecd66151987296a)",
   "webSocketDebuggerUrl": "ws://127.0.0.1:8087/devtools/browser/e943baa8-ebde-4419-bb6b-4d2b2cf2fe99"
}
```

→ browser 级 `webSocketDebuggerUrl` **存在**（与 `:8080` 一致）。

### 7.2 `:8087` `/json/list`

命令：

```sh
ssh -o BatchMode=yes -o ConnectTimeout=10 frame "curl -s http://127.0.0.1:8087/json/list"
```

| id | type | title | url |
| --- | --- | --- | --- |
| `2E6761CF6459B65B2EFF0911DC495936` | page | `controllerbindingui` | `http://127.0.0.1:27062/dashboard/controllerbinding.html` |
| `D30ED58EB2577BB2677B3B4C0BCDF36F` | page | `systemui` | `http://127.0.0.1:27062/dashboard/systemui.html#general_settings` |

仅 2 个 page target，且**都指向同一个下游 HTTP 服务** `http://127.0.0.1:27062`（SteamVR
dashboard 本地服务）。

### 7.3 每页事实

探测方式：stdlib-only Python CDP 客户端（复用当时 one-shot 注入器的
`WebSocket` / `CDP`；现内置于 `src/sf_ui_haptic_switch_daemon.py`），逐个 page 的
`webSocketDebuggerUrl` 执行 `Runtime.evaluate`
（`returnByValue:true`）。推送与运行命令：

```powershell
# 本地：CRLF -> LF 归一化后用 cmd 重定向推送到设备
cmd /c "ssh -o BatchMode=yes -o ConnectTimeout=10 frame \"cat > /tmp/probe8087.py\" < <tmp>"
```

```sh
ssh -o BatchMode=yes -o ConnectTimeout=10 frame "python3 /tmp/probe8087.py"
```

| title | `location.origin` | `VRHTML.VROverlay.TriggerOverlayHapticEffect` | `SteamClient.OpenVR.TriggerOverlayHapticEffect` | `localStorage['sf_ui_haptic_switch']` |
| --- | --- | --- | --- | --- |
| `controllerbindingui` | `http://127.0.0.1:27062` | `function` | `undefined` | `null` |
| `systemui` | `http://127.0.0.1:27062` | `function` | `undefined` | `null` |

辅助事实：两页 `typeof window.VRHTML === "object"`、`typeof window.SteamClient === "undefined"`。

**结论**：`:8087` 上存在并可用的桥接是 **`VRHTML.VROverlay.TriggerOverlayHapticEffect`**
（`typeof === "function"`）；`SteamClient.OpenVR.TriggerOverlayHapticEffect` **不存在**
（`typeof === "undefined"`）。即：与 `:8080` 的桥接**不同**。`localStorage` 可读，但键
`sf_ui_haptic_switch` 当前为 `null`（从未设置）。

### 7.4 跨源同步参照（`:8080`）

命令：同上探测脚本，对 `:8080` 的 `barpopup` page target 执行 `Runtime.evaluate`。

| 端点 | title | `location.origin` | `VRHTML...TriggerOverlayHapticEffect` | `SteamClient...TriggerOverlayHapticEffect` | `localStorage['sf_ui_haptic_switch']` |
| --- | --- | --- | --- | --- | --- |
| `:8080` | `SteamVR - valve.steam.gamepadui.barpopup.84670001` | `https://steamloopback.host` | `undefined` | `function` | **`"0"`** |

→ `:8080` 当前该标志为 **`"0"`（关闭）**。

### 7.5 未知项 / 风险

- **源不同**：`:8087` 的源是 `http://127.0.0.1:27062`，`:8080` 是 `https://steamloopback.host`。
  二者是**不同 localStorage 分区**，无法共享同一份键值；跨端点同步必须走 CDP 中介写两处（或
  在各自端点内独立读写）。
- **`27062` 端口非固定**：该端口由 SteamVR dashboard 本地服务分配，**跨重启可能变化**，
  进而改变 `:8087` 页面的 `origin`，也意味着 `:8087` 的 `localStorage` 分区不稳定。
- **桥接语义未验证**：`VRHTML.VROverlay.TriggerOverlayHapticEffect` 是否就是 UI hover 震动的
  实际入口、其参数顺序/数量（`gate.js` 注释称 VRHTML 版把 overlay handle 放在第一个参数，
  与 Steam 版相反）在本轮**未做调用注入验证**。
- **页面集合稳定性未知**：`systemui` / `controllerbindingui` 是否恒常存在、重启后是否保留，
  未复测。
- **持久化未知**：`:8087` 的 `localStorage` 跨客户端/SteamVR 重启是否保留，本轮未测。

### 7.6 对设计的影响（暂记）

- 注入到 `:8087` 的 gate 应包裹 **`VRHTML.VROverlay.TriggerOverlayHapticEffect`**，而非
  `SteamClient.OpenVR.*`（后者在该端点不存在）。
- 因源不同，`:8080` 的 `localStorage['sf_ui_haptic_switch']` 不会自动流向 `:8087`；同步需显式
  设计（写两处 / 由 CDP 桥接）。
