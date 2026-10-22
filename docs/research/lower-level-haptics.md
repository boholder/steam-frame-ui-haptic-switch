# 更低层的触感抑制与四个 Dashboard 控件仍然震动 — 现场调查

> 调查方式：只读 SSH + 只读 CDP + 读取设备上的 SteamVR bundle/二进制/绑定文件；
> 未修改设备状态，未改本仓库源码。设备：`frame`（Steam Frame，SteamOS；
> 现场 SteamVR bundle 日期 2026-10-07）。主机：运行 Steam/SteamVR 的 Windows
> PC，经 VRLink 连接。
> 所有临时脚本均放在设备 `/tmp`，调查结束后已删除。

## 结论

1. **“显示键盘”“在世界中悬浮”“在影院中查看”“更多选项”不是 DOM 按钮，而是
   Dashboard frame 的 native control-bar action。** CEF 页面（`systemui`）只把
   `display_name` / `icon` / `invocation` 注册成 action descriptor，React 组件
   本身返回 `null`；按钮由 native dashboard/compositor 绘制，hover 由 native
   `CLaserMouse` 直接走 `lasermouse/out/Haptic`。因此
   `VRHTML.VROverlay.TriggerOverlayHapticEffect` 从未被调用，当前 JS gate 即使
   注入成功也拦不到。
2. **远程 “Steam” tab 的 UI 触感与这四个控件在终端 action 上同路。** 主机侧 UI
   hover 的参数经 VRLink 的 `VRLink_LaserMouseEvent.lm_haptic`（含
   `overlay_handle` / `duration` / `frequency` / `amplitude` / `cursor_index`）
   送到头显，头显 `CLaserMouse` 再走 `/actions/lasermouse/out/Haptic` →
   `/user/hand/{left,right}/output/haptic`。四个本地控件也走同一个
   `lasermouse` action，只是上游是本地 native，而不是 VRLink。
3. **最佳的低层方案是关闭 `lasermouse` 的 haptic 输出绑定**（或等价的
   native hook）。这是目前唯一有证据能同时覆盖“四个控件”和“远程 Steam tab”
   的层，而且不会碰游戏/OpenXR 的 haptic action set。代价是它会关闭**所有**
   laser-mouse UI 触感（包括滑杆、吸附、边缘），不是只关 `ButtonEnter/Leave`；
   用户绑定覆盖已实测不生效（见 §3.1a）；已验证用 bind mount 遮蔽绑定
   （见 §3.1b），并且 SteamVR 更新会带来脆弱性。
4. **若必须保持“只关 1/2、保留 3/4/5”的语义并覆盖四个 native 控件：
   当前不可行。** 需要 ARM64 native hook 到 `systemlayer`/`vrcompositor` 的
   `CLaserMouse` 内部，没有稳定公开的 hook 点，也没有 effect ID 可供过滤。

## 1. 四个控件到底在哪里

### 1.1 标签只存在于 Dashboard localization

设备上的精确键值：

```text
/opt/steamvr/resources/webinterface/dashboard/localization/dashboard_schinese.json:75  "FloatInWorld": "在世界中悬浮"
/opt/steamvr/resources/webinterface/dashboard/localization/dashboard_schinese.json:78  "ViewInTheater": "在影院中查看"
/opt/steamvr/resources/webinterface/dashboard/localization/dashboard_schinese.json:81  "ShowKeyboardTooltip": "显示键盘"
/opt/steamvr/resources/webinterface/dashboard/localization/dashboard_schinese.json:87  "AdditionalOptionsTooltip": "更多选项"
```

英文对应：

```text
.../dashboard_english.json:86  "FloatInWorld": "Float in World"
.../dashboard_english.json:89  "ViewInTheater": "View in Theater"
.../dashboard_english.json:92  "ShowKeyboardTooltip": "Show Keyboard"
.../dashboard_english.json:98  "AdditionalOptionsTooltip": "More Options"
```

`grep -RIl` 在 `/home/steamos/.local/share/Steam/clientui` 中找不到这些键，说明
它们不是 Steam 客户端 UI（`:8080`）的本地化，而是 SteamVR Dashboard（`:8087`
的 `systemui`）的本地化。

### 1.2 `:8080` 的全部 page target 都没有这四个标签

命令：

```sh
ssh frame "curl -s http://127.0.0.1:8080/json/list"
```

现场有 13 个 page target，包括：

```text
SteamVR - valve.steam.gamepadui.frame.menu.35660007
SteamVR - valve.steam.gamepadui.frame.menu.35660005
SteamVR - valve.steam.gamepadui.floatingfooter
SteamVR - valve.steam.gamepadui.bar
SteamVR
SteamVR - valve.steam.gamepadui.tooltip.35660002
SteamVR - valve.steam.gamepadui.barpopup.35660001
SteamVR - Notifications
SteamVR - valve.steam.gamepadui.barpopup.35660006
错误
SteamVR - valve.steam.gamepadui.volumelevel
SteamVR - Keyboard
SharedJSContext
```

对每个 target 执行了 `document.body.innerText`、全 DOM 搜索、shadow root 递归
搜索、`iframe` 枚举，并读取 `window.__SF_UI_HAPTIC_SWITCH__`：

```text
SteamVR - ...frame.menu.35660007  => hits=0 body=0   marker={'gate':'5637903c2998'} gate=True sov=function wrapped=True
SteamVR - ...frame.menu.35660005  => hits=0 body=0   marker={'gate':'5637903c2998'} gate=True sov=function wrapped=True
SteamVR - ...floatingfooter       => hits=0 body=6   marker={'gate':'5637903c2998'} gate=True sov=function wrapped=True
SteamVR - ...bar                  => hits=0 body=5   marker={'gate':'5637903c2998'} gate=True sov=function wrapped=True
SteamVR                          => hits=0 body=82  marker={'gate':'5637903c2998'} gate=True sov=function wrapped=True
SteamVR - ...tooltip.35660002      => hits=0 body=0   marker={'gate':'5637903c2998'} gate=True sov=function wrapped=True
SteamVR - ...barpopup.35660001     => hits=0 body=0   marker={'gate':'5637903c2998','ui':'e44d3283cf3c'} gate=True sov=function wrapped=True
SteamVR - Notifications            => hits=0 body=0   marker={'gate':'5637903c2998'} gate=True sov=function wrapped=True
SteamVR - ...barpopup.35660006     => hits=0 body=0   marker={'gate':'5637903c2998','ui':'e44d3283cf3c'} gate=True sov=function wrapped=True
错误                               => hits=0 body=41  marker={'gate':'5637903c2998'} gate=True sov=no-owner
SteamVR - ...volumelevel          => hits=0 body=0   marker={'gate':'5637903c2998'} gate=True sov=function wrapped=True
SteamVR - Keyboard                 => hits=0 body=174 marker={'gate':'5637903c2998'} gate=True sov=function wrapped=True
SharedJSContext                    => hits=0 body=0   marker={'gate':'5637903c2998'} gate=True sov=function wrapped=True
```

shadow-root 递归探针（不只看 `document.body`）：

```text
SteamVR - ...floatingfooter => {"seen":15,"shadowRoots":0,"hits":[]}
SteamVR                    => {"seen":655,"shadowRoots":0,"hits":[]}
SteamVR - ...bar           => {"seen":132,"shadowRoots":0,"hits":[]}
SteamVR - Keyboard         => {"seen":290,"shadowRoots":0,"hits":[]}
SharedJSContext            => {"seen":14,"shadowRoots":0,"hits":[]}
...
```

没有任何 page 命中这四个标签，也没有 iframe。所有页面都有当前 gate marker，
`SteamClient.OpenVR.TriggerOverlayHapticEffect` 已是被包裹的 `function`。

`:8087` 在调查时没有 page target：

```sh
ssh frame "curl -s --max-time 3 http://127.0.0.1:8087/json/version"
# 空
ssh frame "curl -s --max-time 3 http://127.0.0.1:8087/json/list"
# 空
ssh frame "ss -ltnp | grep 8087"
# 无监听
```

`vrdashboard` 进程存在（pid 2680），但 CEF 的 `:8087` 调试端点没有起来；因此
本轮无法对 `systemui` 做 live DOM 探测。下面的 `systemui` 结论来自设备上的
已发布 bundle 和 native 二进制，不是 live DOM。

### 1.3 `systemui.js`：四个控件只注册 action descriptor，返回 `null`

`systemui.js` 是单行 bundle（0 个换行，329704 字节），所以用字节偏移引用。

关键片段（偏移 `115300` 附近）：

```js
function v(e){
  const {actionParams:t}=e,{frame:r}=(0,i.N)();
  if(!r) throw new Error("<FrameControlsItem_Action> must be inside a <Frame>.");
  const o=n.useRef(void 0); o.current=e.onInvoked;
  const a=(0,c.F$)(r,t,((...e)=>{...}));
  return n.createElement(g,{params:{type:2,action_id:a.actionID}})
}
```

`g` 是 `FrameControlsItem`（偏移 `114901` 附近）：

```js
function g(e){
  const {params:t}=e,r=n.useContext(m);
  ...
  const s=(0,o.q3)((()=>"function"==typeof t?t():t));
  return r.OnControlsItemRender(a,s,i),null
}
```

也就是说：它把 action 描述注册到 `Frame`，**React 渲染结果是 `null`**。四个控件
分别在这些位置使用它：

```text
systemui.js offset 116301  #AdditionalOptionsTooltip
systemui.js offset 116798  #FloatInWorld
systemui.js offset 117108  #ViewInTheater
systemui.js offset 119015  #ShowKeyboardTooltip
```

`Frame.SetControlsItems`（偏移 `102844`）把描述放进发给 native 的 proto：

```js
controls:{
  items_for_bottom_frame_controls:this.m_rgControlsItems_BottomFrameControls ?? [],
  items_for_tab_hover_menu:this.m_rgControlsItems_TabHoverControls ?? []
}
```

偏移 `103863` / `104922` 处明确出现 `items_for_bottom_frame_controls`。这说明
四个控件是 **frame control bar 的原生 action**，不是页面里的 `<button>`，也没有
`onMouseEnter` / `onMouseLeave`。

对照普通 DOM 按钮（`chunk~8012d0c89.js`，偏移 `308084`）：

```js
onMouseEnter(e){
  ...
  this.disabled || (0,o.R$f)()==o.OH$.Overlay &&
    l.W.Instance.triggerHaptic(o.enJ.ButtonEnter)
}
onMouseLeave(e){
  ...
  this.disabled || (0,o.R$f)()==o.OH$.Overlay &&
    l.W.Instance.triggerHaptic(o.enJ.ButtonLeave)
}
```

而 `HapticManager.triggerHaptic`（同一文件偏移 `322232`）最终调用：

```js
VRHTML.VROverlay.TriggerOverlayHapticEffect(
  VRHTML.VROverlay.ThisOverlayHandle(), e
)
```

当前 gate 包裹的正是这个 bridge（`src/sf_ui_haptic_switch_gate.js:113-128`,
`:130-137`）。普通 DOM 按钮会经过它；四个 native control-bar 控件不会。

### 1.4 native 侧的控制条与 `lasermouse` 输出

设备上的 native 二进制包含：

```text
systemlayer / vrcompositor:
  VROverlayFlags_EnableControlBar
  VROverlayFlags_EnableControlBarClose
  VROverlayFlags_EnableControlBarKeyboard
  CLaserMouse
  /actions/lasermouse/out/Haptic
  CVRMsg_TriggerHapticPulse
```

`lm_haptic` 在 `vrcompositor`/`systemlayer` 中与
`overlay_handle` / `duration` / `frequency` / `amplitude` / `cursor_index`
出现在同一组协议字段中。native control bar 的 hover 就是 `CLaserMouse` 的
laser-mouse haptic 路径。

`vrcompositor_actions.json` 明确把该输出声明为 vibration，而且这是该文件里
**唯一的 vibration action**：

```text
/opt/steamvr/resources/config/vrcompositor_actions.json:95
  "name": "/actions/lasermouse/out/Haptic", "type": "vibration"
```

因此 native Dashboard 的 UI 触感没有第二条可用的 vibration action；控制条 hover
只能落到 `lasermouse`。这也是“关闭该绑定可以覆盖四个控件”的关键前提。

`vrcompositor_bindings_frame_controller.json` 把它接到左右手：

```text
/opt/steamvr/drivers/frame_controller/resources/input/vrcompositor_bindings_frame_controller.json:70-79
  "/actions/lasermouse": {
    "haptics": [
      {"output": "/actions/lasermouse/out/haptic", "path": "/user/hand/left/output/haptic"},
      {"output": "/actions/lasermouse/out/haptic", "path": "/user/hand/right/output/haptic"}
    ]
  }
```

所以四个控件的 native hover 路径是：

```text
native frame control bar
  -> CLaserMouse
  -> /actions/lasermouse/out/Haptic
  -> /user/hand/{left,right}/output/haptic
  -> frame_controller
```

它完全不经过 `VRHTML.VROverlay.TriggerOverlayHapticEffect`，因此当前 gate 的
`type=="page"` 注入范围和两个 bridge 包装都覆盖不到。

归类：不是 (a) 页面里的 `VRHTML.VROverlay.TriggerOverlayHapticEffect`，不是
(b) `SteamClient.OpenVR...`，不是 (d) iframe/OOPIF；是 **(c) native
Dashboard/frame-control 路径，未暴露给 JS**。`DOMDebugger.getEventListeners`
只能对 DOM node 使用，而这四个控件没有 DOM node，因此没有 listener 可查；
`systemui.js` 里也没有对应的 `onMouseEnter` / `onMouseLeave`。

## 2. 远程 Steam tab / Steam Link 触感路径

### 2.1 VRLink 现场

```text
ps: /bin/bash /opt/steamvr/tools/vrlink/bin/linuxarm64/run_vrlink.sh -I ... -e 10.35.78.23~10400~10400~...
ps: ./vrlink -I ... -e ...
ss -unp: udp ESTAB 10.35.78.1%wlanap:10400 10.35.78.23:10400
         users:(("vrlinkrunthread",pid=187157,fd=68))
```

`run_vrlink.sh` 只是 `./vrlink "$@"` 的启动/信号转发包装，没有 haptic 相关参数。

### 2.2 远程 UI haptic 走 `VRLink_LaserMouseEvent.lm_haptic`

`vrlink` 二进制中的字符串：

```text
VRLink_LaserMouseEvent
VRLink_LaserMouseEvents
VRLink_LaserMouse_State
lm_haptic
overlay_handle
duration
frequency
amplitude
cursor_index
```

`vrlink` 没有 `CVRMsg_TriggerHapticPulse`；而 `vrcompositor`/`systemlayer` 同时有：

```text
VRLink_LaserMouseEvent(s)
lm_haptic
CLaserMouse
CVRMsg_TriggerHapticPulse
/actions/lasermouse/out/Haptic
```

这说明远程 UI hover 的 haptic **参数**是作为 laser-mouse event 的 `lm_haptic`
字段过 VRLink 的；到了头显后由 `CLaserMouse` 消费，最终仍走
`lasermouse/out/Haptic`。因此：

```text
host UI hover
  -> VRLink_LaserMouseEvent.lm_haptic
  -> headset CLaserMouse
  -> /actions/lasermouse/out/Haptic
  -> /user/hand/{left,right}/output/haptic
```

四个本地控件与远程 Steam tab 在终端 action 上**同路**（都走 `lasermouse`），
只是上游不同：四个控件是本地 native control bar，远程 tab 是 VRLink 的
`LaserMouseEvent.lm_haptic`。串流游戏 haptic 则走另一组 action（见 2.3）。

### 2.3 游戏/串流游戏 haptic 是另外的 action set

设备上的默认绑定明确区分了：

```text
steam_vrgamepad_bindings_frame_controller.json:
  /actions/haptics/out/haptic -> /user/hand/{left,right}/output/haptic

legacy_bindings_frame_controller.json:
  /actions/legacy/out/left_haptic  -> /user/hand/left/output/haptic
  /actions/legacy/out/right_haptic -> /user/hand/right/output/haptic

vrlink_legacy_bindings_frame_controller.json:
  /actions/legacy/out/left_haptic / right_haptic -> /user/hand/*/output/haptic
```

Valve 的 Steam Frame 文档也把 OpenXR 输出列为
`/user/hand/left/output/haptic` / `/user/hand/right/output/haptic`
（`XR_ACTION_TYPE_VIBRATION_OUTPUT`）。因此：

- `lasermouse/out/Haptic` 是 Dashboard/overlay 的 laser-mouse UI 通道；
- 游戏/OpenXR/Steam Input 使用 `/actions/legacy/*_haptic`、`/actions/haptics/*`
  或应用自己的 haptic action，不是 `lasermouse`；
- 串流游戏 haptic 也走 VRLink 的 legacy/haptics 路径，而不是 `lm_haptic`。

这给出了一个关键结论：**关闭 `lasermouse` 的 haptic 输出，可以只影响 UI
laser-mouse 触感，而不影响游戏触感**；但它会关闭所有 UI laser-mouse 触感，
不只是 hover。换句话说，它回答“能否只关 Dashboard UI 触感”时是 **能**
（相对游戏而言），但回答“能否只关 hover、保留滑杆/吸附”时是 **不能**。

## 3. 低层方案逐项评估

### 3.1 编辑/覆盖 `vrcompositor_bindings_frame_controller.json`

做法：删除或改写 `vrcompositor_bindings_frame_controller.json:70-79` 的
`haptics` 数组，使 `lasermouse/out/Haptic` 不再绑定到
`/user/hand/*/output/haptic`。

覆盖范围：

- 四个 native 控件：**覆盖**（同一 `lasermouse` action）。
- 远程 Steam tab UI：**覆盖**（`lm_haptic` 最终仍进 `CLaserMouse`）。
- 本地 DOM UI 的 `TriggerOverlayHapticEffect`：**覆盖**（同一
  `TriggerLaserMouseHapticVibration` / lasermouse 输出）。
- 游戏/OpenXR/Steam Input：**不覆盖**（不同 action set）。
- 但它不能只关 1/2；3/4/5（滑杆、吸附、边缘）也一起消失。

安装/权限：

```text
id: uid=1000(steamos)
sudo -n true: sudo: a password is required
stat: /opt root:root 755
/proc/mounts: /dev/sda4 / btrfs rw,...
```

`/opt/steamvr` 是 root-owned；SSH 用户没有免密 sudo。修改默认文件需要 root，
并且 SteamVR 更新会覆盖它。`~/steamvr/input/` 是用户可写目录（现场有
`steam.app.438100_frame_controller.json`），但**实测 compositor 不读取该目录的
覆盖**，见 §3.1a。

### 3.1a 用户绑定覆盖实测（2026-10-22）

- 在 `~/steamvr/input/openvr.component.vrcompositor_frame_controller.json`
  写入去掉 `/actions/lasermouse.haptics` 的默认绑定；`app_key` 为
  `openvr.component.vrcompositor`，pointer/click 保留。
- 重启 SteamVR（`vrserver` / `vrcompositor` 21:53 启动）。
- `~/.local/share/Steam/logs/vrclient_vrcompositor.txt` 仍报
  `Failed to load binding file ... [app: openvr.component.vrcompositor]`；
  四个 native 控件和远程 Steam tab 的触感均未变化。
- 结论：`openvr.component.vrcompositor` 的内部 action set 不读取用户绑定目录；
  用户绑定只对普通 app 的 action manifest 生效。此路不通，测试文件已删除。

### 3.1b SteamOS 只读根：bind mount（已验证）

现场 `sudo cp ... .bak` 报 `Read-only file system`，但 `findmnt` 显示 `/` 是
btrfs `rw`。原因是 **btrfs subvolume 本身只读**：

```text
btrfs property get / ro   -> ro=true
btrfs subvolume show /    -> Flags: readonly
```

`/opt/steamvr` 位于这个只读根 subvolume，root 也不能原地写。已验证的落地方案：

1. `sudo ./scripts/aggressive-ui-haptics.sh apply`
   - 把原文件复制到 `/var/lib/sf-ui-haptic-switch/`（`/var` 是 ext4 rw）
   - 删除副本的 `/actions/lasermouse.haptics`
   - `mount --bind` 副本覆盖 `/opt/.../vrcompositor_bindings_frame_controller.json`
2. `sudo ./scripts/aggressive-ui-haptics.sh persist`
   - 写 `/etc/systemd/system/sf-ui-haptic-binding.service`（enabled + active）
   - 启动时自动重建 bind mount
3. 重启 SteamVR。

验证输出（2026-10-22 22:09）：

```text
mount:   bind (patched copy)
haptics: absent (patched)
unit:    /etc/systemd/system/sf-ui-haptic-binding.service

findmnt -T TARGET:
TARGET ... /dev/sda6[/lib/sf-ui-haptic-switch/...json] ext4 rw

vrserver.txt 22:09:55:
Successfully loaded binding file
'/opt/.../vrcompositor_bindings_frame_controller.json'
for app 'openvr.component.vrcompositor'
```

- 只 `apply` 不 `persist` 时，重启后 bind mount 消失（现场曾看到
  `mount: original / haptics: present / copy exists / unit none`）；`persist`
  是开机保留所必需。
- `revert`：disable unit → `umount` → 删副本；原文件从未被修改。
- 影响：所有 laser-mouse UI 触感（hover / 滑杆 / 吸附 / 边缘）；游戏 /
  OpenXR / Steam Input 不受影响；不受 QAM 开关控制。
- SteamVR 更新后：`revert` → `apply`，用新默认绑定刷新副本。

### 3.2 主机侧 NullHaptics 式 `PollNextEvent` hook

NullHaptics 的实现（`src/driver.cpp`）是在 Windows 的 `vrserver.exe` 里 inline
hook `IVRServerDriverHost::PollNextEvent`（vtable index 5），把每个
`VREvent_Input_HapticVibration` 的 `fAmplitude` / `fDurationSeconds` /
`fFrequency` 清零。它明确是“所有 haptic 全部归零”，没有 UI/游戏区分。

对本题：

- 四个 native 控件：**不覆盖**（发生在头显本地，不在主机 `vrserver` 的控制器
  驱动事件队列上）。
- 远程 Steam tab UI：**没有证据覆盖**。现场协议显示 UI haptic 以
  `VRLink_LaserMouseEvent.lm_haptic` 过 VRLink；主机侧 NullHaptics 拦的是控制器
  驱动的 `VREvent_Input_HapticVibration`，不是 compositor 的 laser-mouse 消息。
  即使某些主机路径最终也生成 haptic event，它也会同时杀掉主机侧游戏触感。
- 游戏/串流游戏：**覆盖**（主机侧所有 `VREvent_Input_HapticVibration`）。

风险：Windows 管理员安装、MinHook/未签名驱动、杀软误报、SteamVR 重启、
vtable/接口版本变化；不能区分 UI 与游戏。它不是本题的答案。

### 3.3 头显侧 Linux/ARM64 driver 或 `vrserver`/`frame_controller` hook

现场事实：

```text
/opt/steamvr/drivers/frame_controller/driver.vrdrivermanifest
  "resourceOnly": true
```

`frame_controller` 没有独立 `.so`；控制器逻辑在 `vrserver` / `systemlayer` 内。
`vrserver` 含 `IVRServerDriverHost_004/005/006`，说明 NullHaptics 的
`PollNextEvent` vtable 思路在 ABI 上存在，但：

- 在 `vrserver` 上 hook `PollNextEvent` 会清零**所有** haptic（UI + 游戏）；
- 想做到 UI-only，必须 hook `CLaserMouse` / `lm_haptic` 输出，或按
  `overlay_handle` 过滤；没有公开稳定的符号/接口，版本升级极易失效；
- 没有 effect ID（1/2/3/4/5）这一层，无法只关 hover；
- 需要 root（`/opt` root-owned，SSH 用户无免密 sudo）和 SteamVR 重启。

因此它可以作为“全关”或“UI 全关”的高风险方案，但不是当前产品的精确方案。

补充：OpenXR API layer 也只能拦截 OpenXR 应用的 `xrApplyHapticFeedback`，
碰不到 Dashboard native control bar；对本节的四个控件无效。

### 3.4 Steam Link/VRLink 设置或协议级过滤

现场 `~/.config/openvr/config/steamvr.vrsettings` 没有 haptic 开关；只有
`vrlink.client.preferredRefreshRate`。`default.vrsettings` 里只有：

```text
:101  "noEdgeExitHaptics": true
:150  "laserMouseDebugging": false
```

`noEdgeExitHaptics` 是 Dashboard 边缘退出的触感，不是按钮 hover；`laserMouseDebugging`
只是调试日志。没有找到第一方 VRLink haptic 总开关。

协议级过滤（在 `vrlink`/`vrcompositor` 收到 `VRLink_LaserMouseEvent` 时把
`lm_haptic` 清零）可以只覆盖远程 UI，不覆盖四个本地控件；实现仍需 native
hook/替换，版本脆弱。它适合作为“只关远程 Steam tab”的补充，不是完整方案。

### 3.5 Kernel/HID 级抑制

- 只能“全关”，无法区分 UI 与游戏；
- Frame 控制器不是标准 USB HID 设备（`lsusb` 没有枚举到控制器；`hidraw0`
  存在但未证明是控制器），链路是头显内部无线/专有协议；
- 需要 kernel module 或固件级逆向，root 风险最高，版本/硬件绑定最强；
- 不推荐，也不满足“只关 UI hover”的产品语义。

### 3.6 方案对比

| 方案 | 四个控件 | 远程 Steam tab | 游戏/OpenXR | UI/游戏区分 | 主要风险 |
| --- | --- | --- | --- | --- | --- |
| 关闭 `lasermouse` haptic 绑定 | 覆盖 | 覆盖 | 不覆盖 | 能（UI vs 游戏） | root、重启、更新覆盖、UI 全静音 |
| 主机 NullHaptics `PollNextEvent` | 不覆盖 | 未证明 | 覆盖 | 不能（全关） | Windows 驱动、全关、版本脆弱 |
| 头显 `vrserver` `PollNextEvent` hook | 覆盖 | 覆盖 | 覆盖 | 不能（全关） | root、全关、版本脆弱 |
| 头显 `CLaserMouse`/`lm_haptic` hook | 覆盖 | 覆盖 | 不覆盖 | 能（UI vs 游戏） | 无稳定符号、逆向、版本脆弱 |
| VRLink `lm_haptic` 过滤 | 不覆盖 | 覆盖 | 不覆盖 | 能（仅远程 UI） | native hook、版本脆弱 |
| Kernel/HID | 覆盖 | 覆盖 | 覆盖 | 不能（全关） | 专有链路、root、最高风险 |

## 4. 建议路径

**推荐：把“四个 native 控件 + 远程 Steam tab”作为一个单独的、明确的
“aggressive UI haptics off”选项，通过关闭 `lasermouse` haptic 输出实现；
不要把它混进当前的 1/2-only gate。**

具体：

1. 保持现有 JS gate（`:8080` / `:8087` 全部 page target）不变；它负责 DOM
   按钮的 `ButtonEnter/ButtonLeave`，并且已经验证可逆、立即生效。
2. 已提供 `scripts/aggressive-ui-haptics.sh`（`apply` / `persist` / `revert` /
   `status`）：把去掉 `lasermouse.haptics` 的副本 bind-mount 到只读根上的
   `vrcompositor_bindings_frame_controller.json`。`persist` 安装 systemd unit；
   需要 root。明确提示“会关闭所有 laser-mouse UI 触感（含滑杆/吸附/边缘），
   但不影响游戏/OpenXR 触感”。
3. 如果产品不能接受“滑杆也静音”，则**不要做低层方案**；把四个控件和远程
   Steam tab 明确写进 Limitations。想要精确到 1/2 的 native 方案，需要
   Valve 暴露 effect 级 API 或稳定的 `CLaserMouse` hook 点；当前没有证据支持
   可维护的实现。

## 5. 范围限制

- 本轮 `:8087` 没有监听，未能对 `systemui` 做 live DOM/`DOMDebugger` 探测；
  四个控件的 native 结论来自设备上的 `systemui.js`、localization、
  `vrcompositor`/`systemlayer` 二进制和绑定文件。
- 已完成一次用户绑定写入/重启实验（未生效，见 §3.1a），以及一次
  bind-mount + systemd 持久化实验（生效，见 §3.1b）；原文件未被修改，也未注入
  运行时 wrapper。
- `lasermouse` 绑定关闭只覆盖 UI laser-mouse 触感；不覆盖
  `SteamClient.Input.TriggerSimpleHapticEvent` / `TriggerHapticPulse` /
  `ForceSimpleHapticEvent`、`/input/pulsehaptics.action`、应用直接
  `CVRMsg_TriggerHapticPulse`、游戏/OpenXR、Steam Input。
- `TriggerOverlayHapticEffect` 只接受 effect ID，不接受 amplitude；任何“缩放
  幅度”的需求都必须下沉到 native，且现有 native 层无法只针对 hover。
- 主机侧配置本轮不可达；主机侧 NullHaptics 只依据其公开源码和 Windows
  `PollNextEvent` 机制评估，未在主机上实测。

## 6. 引用

- OpenVR `openvr.h`：`IVROverlay::TriggerLaserMouseHapticVibration`
  （line 4479）、`VREvent_Input_HapticVibration`（line 1085）、
  `VREvent_HapticVibration_t`（line 1343）、`IVRSystem::PollNextEvent`
  （line 2525）。
  https://raw.githubusercontent.com/ValveSoftware/openvr/master/headers/openvr.h
- OpenVR Driver API：haptic event 经 `IVRServerDriverHost::PollNextEvent`
  送达驱动，字段为 `fDurationSeconds` / `fFrequency` / `fAmplitude`。
  https://raw.githubusercontent.com/ValveSoftware/openvr/master/docs/Driver_API_Documentation.md
- Valve Steam Frame 输入文档：OpenXR 左右手
  `/user/hand/{left,right}/output/haptic`，`XR_ACTION_TYPE_VIBRATION_OUTPUT`。
  https://partner.steamgames.com/doc/steamhardware/steamframe/input
- NullHaptics：`PollNextEvent` inline hook，清零所有
  `VREvent_Input_HapticVibration`。
  https://github.com/KanaiCasual/NullHaptics
  https://raw.githubusercontent.com/KanaiCasual/NullHaptics/main/src/driver.cpp

