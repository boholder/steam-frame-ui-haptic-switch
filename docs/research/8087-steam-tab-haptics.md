# `:8087` "Steam" 标签页按钮触感未被抑制 — 根因调查（已更正）

> 调查方式：只读 CDP + 只读进程/网络探测；一次运行时可回退的 wrapper 实验。
> 设备：`frame`（Steam Frame，SteamOS `0.5.6`，SteamVR `2.18.2`）。
> 主机：运行 Steam/SteamVR 的 Windows PC。
> 代码基线：本仓库当前 `main`。

## 结论（更正后）

第一个结论（"Steam tab 是本地 SteamUI，native
`SetOverlayInteractionAffordance` 未被包裹"）**不成立**。那个结论是在
`:8080` 的本地 `valve.steam.gamepadui.main` 页面上做的；该页面不是当前
"Steam" tab 的实际渲染源。现场实验证明：在该页面包裹
`SetOverlayInteractionAffordance` 后，用户悬停 Steam tab，wrapper 的
调用计数仍为 **0**。

实际根因：

- 当前激活的 "Steam" tab 是 **SteamVR 主机经 VRLink
  送来的远程 frame**（`is_vrlink_remote: true`），不是 Steam Frame 本地
  `:8080` 的 SteamUI 页面。
- 现场同时存在两个 "Steam" frame：本地 `1302400001` 和远程 `3189883648`；
  `DashboardStore.m_unActiveFrameID = 3189883648`，即远程那个。
- 远程 frame 的 `m_protoForSteam.steamui_page_overlay_keys` 指向
  `valve.steam.gamepadui.main`，但这指向**主机侧** SteamUI 的 overlay key；
  Steam Frame 本地同名页面只是同 key 的本地 frame。
- `frame` 上 `ss -unp` 显示 `vrlinkrunthread` 与 `HOST_IP:10400`
  持续通信；远程 frames 里还有 "OVR Advanced Settings" 和 "桌面"，都是
  主机侧内容。
- 主机 PC 上 `steamwebhelper.exe` 没有开启 CEF 调试端口（监听端口只有
  `27062`），`steam.exe` 命令行也没有 `-cef-enable-debugging`。因此本仓库
  的 headset 侧 daemon/gate 无法触达主机 SteamUI 的 JS。

**含义**：Steam tab 的按钮悬停由主机侧 SteamUI/SteamVR 处理，触感经
VRLink 回到头显。Steam Frame 本地的 JS gate（`:8080`/`:8087` 页面）不在
这条调用链上，无法抑制它。

## 现场证据

### 1. 本地 `9561...` 页面的 wrapper 没有被调用

在 `:8080` target `9561D4695B9B7FAE0EB6C5D024FF32A0`
（`valve.steam.gamepadui.main`，本地 SteamUI）注入：

```js
var orig = SteamClient.OpenVR.SetOverlayInteractionAffordance;
function patched(handle, active) {
  patched.__sfProbeCalls++;
  if (off && active) { patched.__sfProbeDropped++; return undefined; }
  return orig.apply(this, arguments);
}
SteamClient.OpenVR.SetOverlayInteractionAffordance = patched;
```

用户随后在头显里悬停 Steam tab 的按钮。回读：

```json
{
  "affordanceWrapped": true,
  "affordanceCalls": 0,
  "affordanceDropped": 0,
  "triggerWrapped": true
}
```

→ 该页面的 `SetOverlayInteractionAffordance` 根本没有被调用。此前的
"native affordance 通道"结论对当前 tab 不适用。

### 2. 激活的 Steam tab 是远程 frame

在 `systemui`（`:8087`，target `CC1D...`）读取 `FrameStore`：

```json
{
  "frames": [
    {"id": 1302400001, "remote": false,
     "tab": {"display_name": "Steam"}},
    {"id": 3189883648, "remote": true,
     "tab": {"display_name": "Steam"},
     "steam": {
       "is_vrlink_remote": true,
       "steamui_page_overlay_keys": ["valve.steam.gamepadui.main"],
       "is_streaming_client": false
     },
     "vrlink": {"title": "Steam", "is_currently_visible": true}},
    {"id": 3189883657, "remote": true,
     "tab": {"display_name": "桌面"}}
  ],
  "activeID": 3189883648
}
```

→ 本地 "Steam" frame 和远程 "Steam" frame 是两个不同的 frame；当前激活的是
远程那个。

### 3. 网络路径指向主机 PC

`ssh frame "ss -unp"`（节选）：

```text
udp  ESTAB  FRAME_IP%wlan0:10400  HOST_IP:10400
     users:(("vrlinkrunthread",pid=3686,fd=59))
```

`frame` 是本机，`HOST_IP` 是运行 Steam/SteamVR
的 Windows PC；VRLink 使用 UDP 10400。

### 4. 主机侧没有可用的 CEF 调试入口

在 `HOST_IP`（Windows）上：

- 运行中的进程：`steam.exe`、`vrserver.exe`、`vrdashboard.exe`、
  `steamwebhelper.exe`、`vrwebhelper.exe`。
- 监听端口：只有 `127.0.0.1:27062`（SteamVR dashboard HTTP 服务）和
  `0.0.0.0:27036`（Steam 局域网）。
- `steam.exe` 命令行：`"D:\Steam\steam.exe"`，没有 `-cef-enable-debugging`。
- 主机 `steamvr.vrsettings` 中没有按钮 hover 触感的总开关；只有
  `default.vrsettings` 里的 `noEdgeExitHaptics`（边缘退出触感，与本题无关）。

因此当前无法从 headset 的 `:8080`/`:8087` 页面触达主机 SteamUI 的 JS，
也就无法用现有 gate 抑制该 tab 的悬停触感。

## 修复方向（未实现）

1. **主机侧 gate（与现有插件同源）**：给主机 Steam 加
   `-cef-enable-debugging`，在主机 SteamUI 页面注入 gate，包裹
   `TriggerOverlayHapticEffect` 和 `SetOverlayInteractionAffordance`。
   需要解决状态同步：头显 QAM 的开关值目前只存在头显；主机侧需要另一个
   输入源（网络/共享状态），否则两边会不一致。
2. **VRLink/SteamVR 原生开关**：如果 SteamVR 提供了"远程 frame 触感"的
   配置项，可在 Steam Frame 或主机侧关闭。本轮没有找到文档或配置证据；
   需要在主机 SteamVR/Steam Link 设置里继续确认。
3. **不修**：把该 tab 视为"主机 SteamUI 透传"，与游戏/OpenXR 触感一样
   超出本地 gate 的范围（README 的 Limitations 可补充这一条）。

不建议继续在 Steam Frame 的 `:8080` 本地 SteamUI 上找 `SetOverlayInteractionAffordance`
的调用：现场计数为 0，路径不对。

## 复现探针

```sh
ssh frame "curl -s http://127.0.0.1:8087/json/list"
ssh frame "curl -s http://127.0.0.1:8080/json/list"
ssh frame "ss -unp | head -20"
```

CDP 侧使用 stdlib-only `sf_ui_haptic_switch_daemon.WebSocket` 连接
`ws://127.0.0.1:8087/devtools/page/CC1D...`，执行
`FrameStore.GetFrame(DashboardStore.m_unActiveFrameID)` 与
`fs.remote_frames` 的枚举。

## 风险 / 未决

- `SetOverlayInteractionAffordance` 是否为主机侧按钮触感的唯一剩余通道，
  尚未在主机上验证；本轮只证明了它不在头显本地页面的调用链上。
- 主机 SteamUI 的 CEF 调试开关需要重启 Steam；会改变用户环境，未执行。
- VRLink 是否有触感总开关未知；需要主机侧 SteamVR/Steam Link 设置或
  官方文档确认。
