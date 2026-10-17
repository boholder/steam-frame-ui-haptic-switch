# T7 — 常驻 daemon + SteamVR Dashboard（`:8087`）震动 gate

- **状态**：done（分支 `t7-daemon-8087`；真机验收待执行）
- **载体**：本地 ticket（Q7 A）
- **阻塞于**：无（T0 已完成；T1/T2/T3 的现状即基线）
- **阻塞**：无
- **替代**：T6（启用 `:8087` 的调研由本票落地）

## 实现状态

- 分支：`t7-daemon-8087`
- 单测：`python -m unittest discover -s tests` → 51 passed，1 skipped
  （Windows 上跳过 POSIX mode 断言）
- 文件：`src/sf_ui_haptic_switch_daemon.py`、`sf_ui_haptic_switch_cdp.py`、
  `sf_ui_haptic_switch_settings.py`、`sf_ui_haptic_switch_lib.sh`、
  `sf_ui_haptic_switch_gate.js`、`sf_ui_haptic_switch_ui.js`、
  `sf-ui-haptic-switch.service`、`install.sh`、`uninstall.sh`、`README.md`、`tests/`
- 真机验收：待执行（见文末清单）

## 背景

现状（T1/T2/T3）是 **systemd timer + oneshot 注入器**，只覆盖 `:8080`：

- timer 每约 3 秒触发一次短连接注入；
- gate 包 `SteamClient.OpenVR.TriggerOverlayHapticEffect`；
- UI 开关只存在于 QAM 的 `barpopup`，写 `localStorage['sf_ui_haptic_switch']`；
- `:8087` 完全没有覆盖。

T0 之后确认 `:8087` 可由 `~/.config/openvr/config/steamvr.vrsettings` 的
`VRWebHelper.DebuggerEnabled=true` 打开；`vrwebhelper` 会暴露 browser 级 CDP。
但 timer 模式下，用户拨动开关后 `:8087` 最多要等下一次 tick（
`OnUnitInactiveSec=3s` + 本次运行耗时 + `AccuracySec`，实际 ≥3 秒）才生效。

本票把注入器改为**常驻 daemon**，用 `Runtime.addBinding` 即时接收 `:8080` 的开关变化，
并立刻推给 `:8087`；同时保持 `:8080` 的现有目标集合与 UI 不变。

## 目标

1. 常驻 daemon 覆盖 `:8080` 与 `:8087`，用户拨动 QAM 开关后 `:8087` 侧尽快生效
   （一次 CDP 往返，而不是等下一次 timer tick）。
2. `:8087` 上所有 Dashboard page target 的 `ButtonEnter(1)` / `ButtonLeave(2)` 被 gate；
   滑杆/吸附（`Snap(3)` / `Sliding(4)` / `SlidingEdge(5)`）保留。
3. 开关状态跨 SteamVR 重启、`:8087` 端口/origin 变化、daemon 重启仍然正确；
   `:8080` 的 `localStorage` 仍是唯一权威。
4. `install.sh` / `uninstall.sh` 负责 `VRWebHelper.DebuggerEnabled` 的增删；
   不再安装 timer。
5. 文档、单测与安装/卸载文案同步更新。

## 非目标

- 不新增 `:8087` 的 UI 开关。
- 不改 `:8080` 的注入目标集合：仍是所有 page target 注 gate、`barpopup` 额外注 UI。
- 不拦 `SteamClient.Input.TriggerSimpleHapticEvent / TriggerHapticPulse /
  ForceSimpleHapticEvent`，不处理 `/input/pulsehaptics.action`。
- 不改 `noEdgeExitHaptics` 等原生 SteamVR 设置。
- 不做 `--on` / `--off` CLI；唯一写入口仍是 QAM 开关。
- 不实现周期对账或 watchdog（Q1g）。

## 已定决策

| # | 决策 | 结论 |
| --- | --- | --- |
| Q1 | 进程形态 | 常驻 daemon + `Runtime.addBinding`；不再使用 timer/oneshot |
| Q1 | 权威状态 | `:8080` 的 `localStorage['sf_ui_haptic_switch']` 是唯一权威 |
| Q1 | 状态流 | UI 写 localStorage → binding 通知 daemon → daemon 回读校验 → 原子写状态文件 → 推 `:8087`；daemon 不回写 8080 |
| Q1 | 对账 | 仅在启动、重连、UI 页新执行上下文时读一次；无周期对账、无 watchdog |
| Q1 | binding 载荷 | `{"c":"haptic","v":true|false}`；`v` 只是提示，实际以 localStorage 为准 |
| Q1 | binding 名 | `__sfUiHapticSwitchSend`（注意不是 `Swtich`） |
| Q1 | 握手 | `addBinding` 后 evaluate 一次 `{"c":"hello"}`，1 秒内等 `Runtime.bindingCalled`；失败快速重试 3 次，仍失败则尽力 teardown + `exit 1` |
| Q1 | 端点缺席 | 1 秒退避重连，不退出；8087 缺席时跳过，8080 缺席时用状态文件 |
| Q2 | 目标发现 | 每个端点一条 browser 级 WS；`Target.setDiscoverTargets` + `Target.attachToTarget {flatten:true}`；零轮询 |
| Q3 | 8080 目标 | 保持不变：所有 page target 注 gate，`barpopup` 额外注 UI |
| Q3 | 8087 目标 | attach 所有 page target，只注 gate；不设 title 白名单 |
| Q4 | 注入方式 | `Page.addScriptToEvaluateOnNewDocument` + 对当前文档 `Runtime.evaluate` 兜底 |
| Q5 | CLI | `--status`、`--verbose`；可选 `--script-dir`、`--state-file`（测试/运维）；无 `--on/--off` |
| Q6 | 服务 | `Type=simple`、`User=steamos`、`Restart=always`、`RestartSec=1`；删除 timer |
| Q6 | 状态文件 | `~/.local/state/sf-ui-haptic-switch/state.json`，`{"enabled": true|false}`，0600，原子写 |
| Q6 | 文件命名 | `sf_ui_haptic_switch_inject.py` → `sf_ui_haptic_switch_daemon.py` |
| Q7 | 失败语义 | 握手失败 teardown + exit；端点未起只重试 |
| Q8 | 效果范围 | 只丢 `1/2`，保留 `3/4/5`；双桥分别取 `arguments[0]`（Steam）/ `arguments[1]`（VRHTML） |
| Q11 | binding 名 | `__sfUiHapticSwitchSend` |
| Q12 | 端口参数 | `--steamwebhelper-port`（默认 8080）、`--vrwebhelper-port`（默认 8087） |
| Q13 | 代码注释 | 在 gate 里解释 `0–5` 的含义与“为什么只丢 1/2” |
| Q3 | 配置增删 | install 写 `VRWebHelper.DebuggerEnabled=true`，同目录 `.bak` 备份；uninstall 删除该键（空节一起删），不恢复 `.bak` |
| Q5 | SteamVR 运行中 | 照写配置并提示“重启 SteamVR 后 `:8087` 才出现”；不拒绝安装 |
| Q6 | 验收 | 真机交互由用户手工验证；不写设备自动化 e2e |

## 目标架构

```text
sf-ui-haptic-switch.service (Type=simple, User=steamos, Restart=always)
        │
        ▼
sf_ui_haptic_switch_daemon.py
        ├─ browser WS :8080  ── Target.setDiscoverTargets
        │     ├─ all page targets: gate
        │     └─ barpopup: gate + UI + Runtime.addBinding
        └─ browser WS :8087  ── Target.setDiscoverTargets
              └─ all page targets: gate

状态：
  8080 localStorage ──(权威)──> daemon ──(原子写)──> state.json
                                   │
                                   └──(evaluate 写 localStorage)──> 8087 页面
```

### 状态流（精确时序）

1. 用户拨动 QAM 开关。
2. UI 立即写 `localStorage['sf_ui_haptic_switch'] = '1' | '0'`；同页 gate 的下一次调用立刻生效。
3. UI 调用 `window.__sfUiHapticSwitchSend(JSON.stringify({c:'haptic', v:true|false}))`。
4. daemon 收到 `Runtime.bindingCalled`：
   - 回读 `:8080` 的 `localStorage`，规范化（`'0'`/`'false'` → off，其余含缺失 → on）；
   - 变化时原子写 `state.json`；
   - 对每个 `:8087` session 执行 `localStorage.setItem('sf_ui_haptic_switch', value)`。
5. `:8087` gate 在下一次震动调用时读到新值。
6. 启动 / 重连 / UI 页新执行上下文：重加 binding + 握手 + 读一次 8080；页面值胜出。
7. `:8080` 不可用：用 `state.json` 的值推 `:8087`。
8. `:8087` 不可用：跳过；出现后 attach、注入 gate、写入当前值。

### gate（`sf_ui_haptic_switch_gate.js`）

同时支持两个 bridge，分别按自己的签名取 effect：

```js
// :8080 Steam 客户端
SteamClient.OpenVR.TriggerOverlayHapticEffect(effect, controllerOrHand)
//   effect = arguments[0]

// :8087 SteamVR Dashboard
VRHTML.VROverlay.TriggerOverlayHapticEffect(overlayHandle, effect)
//   effect = arguments[1]
```

- 只对 `typeof effect === 'number' && (effect === 1 || effect === 2)` 拦截。
- 拦截时返回 `undefined`；否则 `Reflect.apply(original, this, args)`。
- 桥接缺失时 1 秒重试；页面同时存在两个桥接就都包。
- 内容哈希/build 标记与 `teardown()` 沿用现状；teardown 恢复各自原函数。
- **代码注释必须写出**：

```text
EOverlayHapticEffect:
  0 None        无效果
  1 ButtonEnter 光标进入按钮/可点行
  2 ButtonLeave 光标离开按钮/可点行
  3 Snap        滑杆/列表吸附到一格
  4 Sliding     拖动滑杆过程中
  5 SlidingEdge 滑杆拖到端点

本插件只丢弃 1/2（按钮 hover），保留 3/4/5（滑杆/吸附反馈）。
```

### UI（`sf_ui_haptic_switch_ui.js`）

- 现有 UI/模板克隆逻辑保持不变。
- 用户翻转时：
  1. 写 `localStorage`；
  2. 若 `typeof window.__sfUiHapticSwitchSend === 'function'`，发送
     `{"c":"haptic","v":<next>}`；
  3. 渲染新状态。
- 不引入 pending/echo，因为 daemon 不回写 8080。
- binding 不存在时静默跳过发送；daemon 会在重连/新上下文时读到页面值。

### daemon（`sf_ui_haptic_switch_daemon.py`）

#### 端点与 session

- 每个端点从 `/json/version` 取 browser 级 `webSocketDebuggerUrl`。
- `Target.setDiscoverTargets {discover:true}`。
- `Target.targetCreated` → 对 `type=="page"` 调 `Target.attachToTarget {targetId, flatten:true}`。
- 之后所有命令带 `sessionId`；事件按 `sessionId` 路由。
- `Target.targetDestroyed` / `Target.detachedFromTarget` → 关闭 session。
- browser WS 断开 → 1 秒退避重连，重新 discover/attach。
- 端点不可达（Steam/SteamVR 未启动）不得退出。

#### 每个 session

1. `Runtime.enable` + `Page.enable`。
2. `Page.addScriptToEvaluateOnNewDocument`：
   - 所有 page target：gate；
   - `barpopup`（title/url 含 `valve.steam.gamepadui.barpopup`）：再追加 UI。
3. `Runtime.evaluate` 把 gate/UI 注入**当前文档**（build 标记幂等；addScript 只覆盖新文档）。
4. UI target：
   - `Runtime.addBinding {name:"__sfUiHapticSwitchSend"}`；
   - 握手：evaluate `window.__sfUiHapticSwitchSend('{"c":"hello"}')`，
     1 秒内等 `Runtime.bindingCalled`；
   - 失败重试 3 次（每次等 1 秒）；仍失败 → 尽力 teardown → `exit 1`。
   - `Runtime.executionContextCreated`（默认 world）：重新 addBinding + 握手 + 读一次 8080。
5. 每次 binding 事件：回读 8080、持久化、推 8087（见“状态流”）。

#### CLI

```text
--steamwebhelper-port  default 8080
--vrwebhelper-port     default 8087
--status               打印 state.json、两个端点连通性、每个端点的 page target 数、UI target 是否存在
--verbose              打印 attach/inject/push/command 细节
```

日志前缀保持 `[sf-ui-haptic-switch]`。

#### 退出

- SIGTERM/SIGINT：对两个端点所有 session 尽力执行
  `window.__SF_UI_HAPTIC_SWITCH__.ui.teardown()` /
  `window.__SF_UI_HAPTIC_SWITCH__.gate.teardown()`，然后关闭连接。
- 握手失败路径同样先尽力 teardown，再 `exit 1`。

### 服务与安装

`src/sf-ui-haptic-switch.service`：

```ini
[Unit]
Description=Steam Frame "Controller UI Haptic" switch (daemon)
After=multi-user.target graphical.target

[Service]
Type=simple
User=steamos
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /home/steamos/sf-ui-haptic-switch/sf_ui_haptic_switch_daemon.py
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
```

- 删除 `src/sf-ui-haptic-switch.timer`。
- `install.sh` 升级路径：停用并删除旧 timer，停旧 service，重写 unit，`daemon-reload`，
  `enable --now` 新 service；等待数秒确认 active，并提示 `:8087` 需 SteamVR 重启后才出现。
- `uninstall.sh`：停/禁 service，清理旧 timer，删除 unit、安装目录与状态目录
  （`~/.local/state/sf-ui-haptic-switch`）。

#### `steamvr.vrsettings` 编辑

文件：`/home/steamos/.config/openvr/config/steamvr.vrsettings`

- install：
  - 若文件存在，复制为同目录 `steamvr.vrsettings.bak`（覆盖旧 `.bak`）；
  - 若文件不存在，创建它（目录一并创建）；
  - 解析 JSON，设置 `VRWebHelper.DebuggerEnabled = true`；
  - 保留其他所有键；按原风格（3 空格缩进、`"key" : value`）原子写回；
  - 保留原文件所有者与权限。
- uninstall：
  - 解析生效文件，删除 `VRWebHelper.DebuggerEnabled`；
  - 若 `VRWebHelper` 节为空，一并删除该节；
  - **不恢复 `.bak`**，`.bak` 保留；
  - 原子写回并保留所有者/权限。
- SteamVR 正在运行：照写，并提示“重启 SteamVR 后 `:8087` 才会监听”。
- 只动这一个键；不改 `driver_deckard.showDevTools`。

## 文件清单

| 文件 | 动作 |
| --- | --- |
| `src/sf_ui_haptic_switch_inject.py` | 删除；内容重写为 daemon |
| `src/sf_ui_haptic_switch_daemon.py` | 新增（daemon/状态/CLI） |
| `src/sf_ui_haptic_switch_cdp.py` | 新增（WebSocket/CDP 传输） |
| `src/sf_ui_haptic_switch_settings.py` | 新增（vrsettings 编辑） |
| `src/sf_ui_haptic_switch_lib.sh` | 新增（install/uninstall 共享常量） |
| `src/sf_ui_haptic_switch_gate.js` | 双桥 + 效果注释 + teardown |
| `src/sf_ui_haptic_switch_ui.js` | 写 localStorage 后发送 binding |
| `src/sf-ui-haptic-switch.service` | 改为常驻 daemon |
| `src/sf-ui-haptic-switch.timer` | 删除 |
| `install.sh` | 常驻服务、旧 timer 迁移、vrsettings 编辑 |
| `uninstall.sh` | 停服务、清理 timer、删除配置键、状态目录与文件 |
| `tests/test_inject.py` | 删除/替换 |
| `tests/test_daemon.py` | 新增纯逻辑单测 |
| `tests/test_settings.py` | 新增 vrsettings 编辑单测 |
| `tests/test_scripts_parse.py` | 更新契约（binding 名、双桥、注释） |
| `tests/test_units.py` | 更新 service 断言（`Type=simple`、`RestartSec=1`、无 timer） |
| `README.md` | 重写为常驻 daemon + 双端点 |
| `docs/plans/tickets/README.md` | 加入 T7；更新 T6 状态 |
| `docs/plans/tickets/T6-enable-8087.md` | 标记“已由 T7 落地” |

## 测试（TDD）

实现者先写失败测试，再实现：

- **状态规范化**：`'1'`/`'0'`/`'true'`/`'false'`/缺失/垃圾值 → on/off。
- **payload 解析**：`{"c":"haptic","v":true|false}`；忽略 `hello`/坏 JSON/其他 `c`。
- **状态文件**：缺失时默认 on；损坏时回退 on 且不崩溃；原子写路径；0600。
- **target 分类**：`barpopup` 识别、page 过滤、端点归属。
- **CLI 解析**：两个端口默认值、`--status`、`--verbose`。
- **JS 契约**：
  - gate 同时包含 `SteamClient` 与 `VRHTML`；
  - Steam 取 `arguments[0]`、VRHTML 取 `arguments[1]`；
  - 常量 1/2 与注释中的 0–5 表；
  - UI 调用 `__sfUiHapticSwitchSend` 且写 localStorage；
  - `esprima.parseScript` 通过。
- **unit 契约**：`Type=simple`、`User=steamos`、`Restart=always`、`RestartSec=1`；
  timer 文件不存在。
- `python -m unittest discover -s tests` 全绿。

## 真机验收（用户手工）

1. `sudo ./install.sh` 成功；`systemctl status sf-ui-haptic-switch` 为 active；
   `/etc/systemd/system/sf-ui-haptic-switch.timer` 不存在。
2. `~/.config/openvr/config/steamvr.vrsettings` 出现
   `"VRWebHelper": { "DebuggerEnabled": true }`；`.bak` 已生成。
3. 重启 SteamVR 后 `curl http://127.0.0.1:8087/json/version` 返回 browser WS。
4. QAM 开关可见、可拨；`:8080` 的按钮 hover 立刻静音/恢复。
5. `:8087` 的 Dashboard（`systemui`、`controllerbindingui`）按钮 hover 在拨动后
   一次 CDP 往返内静音/恢复；拖滑杆/吸附仍有震动。
6. 重启 SteamVR：daemon 自动重连，开关值保持（8080 localStorage / state.json）。
7. `--status` 显示两个端点、target 数与 UI target。
8. `sudo ./uninstall.sh` 后配置键消失、进程/单元/文件清理干净，震动恢复。
9. README 与注释中的 0–5 说明与实现一致。

## 风险与回滚

- `:8087` 的 origin（当前 `127.0.0.1:27062`）由 `vrserver` 动态分配，localStorage
  可能随重启丢失；daemon 在 attach/推送时重写，属于设计内。
- `steamvr.vrsettings` 可能被运行中的 SteamVR 在退出时整体回写；已在 Q5 接受
  “照写 + 提示重启”。若实测被覆盖，卸载/重装或重启后再写。
- Dashboard 页面按需创建；Target 事件保证新页面即时覆盖。
- 回滚：`sudo ./uninstall.sh`，或恢复旧 tag/提交并重装 timer 版本。

## 参考

- T0：`docs/research/t0-device-probe.md`
- 调研：`ref/我想修改 steam frame 手柄震动的幅度。听说有个叫 haptic tweak 的东西，可以.md`
  §5–§8（两个 bridge、Dashboard 目标列表、持久化限制）
- 旧 daemon 行为：提交 `6bf714b` 删除的
  `docs/research/haptic-switch-implementation.md` /
  `docs/plans/sf-ui-haptic-switch-redesign.md`
- 参考实现：`ref/steam-frame-fan-panel/sf-fan-qam/device/sf_fan_qamd.py`（常驻 daemon、
  binding、推送、teardown）
- 本票实测（2026-10-17）：
  - `Target.setDiscoverTargets` / `Target.attachToTarget {flatten:true}` 在 8080/8087 均可用；
  - `Page.addScriptToEvaluateOnNewDocument` 可用；
  - `Runtime.addBinding` + `Runtime.bindingCalled` 握手可用；
  - Dashboard bundle `chunk~1a88854fa.js` 中
    `ButtonEnter=1, ButtonLeave=2, Snap=3, Sliding=4, SlidingEdge=5`。
