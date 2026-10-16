# Steam Frame「Controller UI Haptic」开关是怎么工作的

调研笔记，描述快速访问菜单里 `Controller UI Haptic` 开关插件当前端到端的实现，以及用户
按下开关时发生的确切事件序列。

所有路径都相对于仓库根目录。每一条结论
都以 `path:line` 标注来源。前端、守护进程和 systemd 单元都位于 `device/` 下。

---

## 概览

插件在 Steam Frame **快速访问菜单**的 **「设置」页（Quick Settings）**最下方
追加一个布尔开关。关闭它会抑制手柄的界面 hover 震动。它不是 Decky/Steam 插件：

- 一个 **root systemd 守护进程**（`sf_haptic_switch_daemon`）通过 Chrome DevTools 协议
  （CDP）连上已在运行的 GamepadUI **CEF 页面**，地址 `127.0.0.1:8080`
  （`device/sf_haptic_switch_daemon.py:45`）。
-   它通过 `Runtime.evaluate` 把一个 **纯 ES5 JS 文件**（`sf_haptic_switch.js`）注入
  快速访问菜单的 popup target `valve.steam.gamepadui.barpopup`
  （`device/sf_haptic_switch_daemon.py:46`, `:337`）。
- 注入的 JS 从实时 DOM 里 **克隆一行原生开关**，改名为 `Controller UI Haptic`，
  追加到设置页 body（`device/sf_haptic_switch.js:382-397`, `:446-478`）。
- JS 还会 **包装 `VRHTML.VROverlay.TriggerOverlayHapticEffect`**，在开关关闭时
  吞掉 `ButtonEnter`/`ButtonLeave` 两类效果（`device/sf_haptic_switch.js:411-430`）。
- UI → 守护进程的消息通过一个名为 `__sfHapticSend` 的 CDP **binding** 回传
  （`device/sf_haptic_switch_daemon.py:47`, `:314`）；守护进程 → UI 的状态则每 0.5 秒通过
  评估 `window.__SF_HAPTIC__.setState({...})` 推送一次
  （`device/sf_haptic_switch_daemon.py:49`, `:346-356`）。

持久化状态文件是 `state.json`，写在守护进程旁边
（`device/sf_haptic_switch_daemon.py:51-52`）。

**关键架构点：** 守护进程**并不**抑制震动。抑制完全在页面内、在注入的 JS 里。
守护进程只持有*持久化*的开/关值，并把它重复推送给页面。

---

## 架构

| 部件 | 文件 | 作用 |
| --- | --- | --- |
| 前端（注入） | `device/sf_haptic_switch.js` | 建开关、渲染状态、把点击回传、包装 haptic API |
| 守护进程（root） | `device/sf_haptic_switch_daemon.py` | 通过 CDP 注入 JS、持久化开/关值、推送快照 |
| systemd 单元 | `device/sf-haptic-switchd.service` | 让守护进程常驻（`Restart=always`） |
| 持久化状态 | `state.json`（运行期，已被 git 忽略） | `{"enabled": true|false}` |

### 连接细节

- **CEF / CDP 端点：** `CDP_HTTP = "http://127.0.0.1:8080"`
  （`device/sf_haptic_switch_daemon.py:45`）。
- **Target 匹配：** `TARGET_MATCH = "valve.steam.gamepadui.barpopup"`
  （`device/sf_haptic_switch_daemon.py:46`）。`find_target()` 拉取 `…/json/list`，匹配
  `title + url` 含该字符串**且** `type == "page"`，返回该 target 的
  `webSocketDebuggerUrl`（`device/sf_haptic_switch_daemon.py:222-232`）。
- **Binding 名：** `BINDING_NAME = "__sfHapticSend"`（`device/sf_haptic_switch_daemon.py:47`），
  通过 `Runtime.addBinding` 注册（`device/sf_haptic_switch_daemon.py:314`）。
- **推送节奏：** `POLL_INTERVAL = 0.5` 秒（`device/sf_haptic_switch_daemon.py:49`）。
- **前端全局对象：** `window.__SF_HAPTIC__`，暴露 `setState`、`getState`、
  `setEnabled`、`sync`、`ping`、`teardown`（`device/sf_haptic_switch.js:592-628`）。
- **行 id / 标签：** `ROW_ID = 'sf_haptic_row'`、`LABEL = 'Controller UI Haptic'`
  （`device/sf_haptic_switch.js:24-25`）。

守护进程需要 root 只是为了 systemd 能可靠地启动它；它不在自己目录之外写任何东西
（`device/sf_haptic_switch_daemon.py:12-14`）。中间层是一个**手写的 RFC6455 WebSocket 客户端**，
因为设备上没有 Python websocket 库（`device/sf_haptic_switch_daemon.py:16-17`, `:67-169`）；
发送的帧始终带掩码（`device/sf_haptic_switch_daemon.py:122-125`）。

单元运行 `/usr/bin/python3 …/sf_haptic_switch_daemon.py --js …/sf_haptic_switch.js`，带
`Restart=always`、`RestartSec=3`（`device/sf-haptic-switchd.service:9-11`）。

---

## 注入与挂载流程

### 1. 连接

每轮循环守护进程调用 `connect()`（`device/sf_haptic_switch_daemon.py:299-321`）：

1. `find_target()` —— 定位 `barpopup` 页面的 webSocket url（`:300`）。
2. 若 url 未变则复用现有 socket（`:303-304`）；否则关闭并重建（`:305-310`）。
3. 打开 `CDP(url)`，然后 `Runtime.enable`（`:313`）和
   `Runtime.addBinding("__sfHapticSend")`（`:314`）。正是这个 binding 让页面里
   存在 `window.__sfHapticSend(...)`。

循环会一直重试：若 `connect()` 失败，sleep 1.0 秒后继续
（`device/sf_haptic_switch_daemon.py:388-390`）。

### 2. 版本标记 + 注入

`Daemon.__init__` 读取 JS，计算 **SHA-1 内容哈希**（前 12 个十六进制字符）作为
`self.build`，并追加一行结尾代码把 `window.__SF_HAPTIC__.build` 打上标记
（`device/sf_haptic_switch_daemon.py:287-292`）：

```python
self.build = hashlib.sha1(self.js.encode("utf-8")).hexdigest()[:12]
self.code = self.js + (
    '\n;try{window.__SF_HAPTIC__.build="%s";}catch(e){}\n' % self.build)
```

`inject()`（`device/sf_haptic_switch_daemon.py:323-340`）探测
`(window.__SF_HAPTIC__ && window.__SF_HAPTIC__.build) || ''`；若已等于 `self.build`
就跳过（`:327`, `:334`），否则评估整个脚本（`:337`）。因为页面 reload 会清掉
`window.__SF_HAPTIC__`，这个标记本身就是「需要（重新）注入」的信号。注入是幂等的：
脚本的第一件事就是在存在旧实例时执行 `prev.teardown()`（`device/sf_haptic_switch.js:20-22`）。

### 3. 找到设置页 body

IIFE 的启动顺序：接管 `window.__SF_HAPTIC__`、定义辅助函数、加载缓存模板、
`hookToggle()`、`startObserver()`、`sync()`（`device/sf_haptic_switch.js:628-639`）。

DOM 发现：

- `menuRoot()` = `document.getElementById('QuickAccess-Menu')`（`:94`）。
- `parts()` 要求存在 `[role="tablist"]`，并收集所有
  `[id^="quickaccess_content_"]` body（`:96-106`）。
- `targetBody()` 选择 `bodyControls()` 得分最高的 body，得分 =
  `滑块数*2 + 复选框数`（`:126-129`, `:144-152`）。「设置」页 body
  （`quickaccess_content_4`）在并列时获得 `+1` 加权（`:148-149`），并要求最低
  得分为 2（`:152`）。选中的 body id 缓存进 `sessionStorage` 的 `BODY_KEY`
  （`:137`, `:154`），这样在切换标签页时保持稳定（未渲染的 body 没有控件，会输掉
  投票）。

### 4. 克隆一行原生开关（靠克隆，而非 CSS）

`mount()`（`:446-478`）先从设置页 body 抓模板，抓不到就回退到**当前已渲染**的任何
body——Valve 只在某个标签页被选中时才渲染它的行（`grabTemplateAny`, `:230-238`；
注释 `:226-229`）。

`grabTemplate(body)`（`:203-224`）：

- 找到第一个非本插件的 `[role="checkbox"]`（`:206-207`）；
- 用 `childWith(scope, probe)` 沿 `body > section > row` 走，后者返回第一个包含
  probe 的直接子元素（`:195-201`, `:211-213`）；
- 捕获 `sectionCls`、行的 `outerHTML` 作为 `toggleRowHTML`，以及来自
  `harvestToggleStates` 的开/关状态数据（`:217-223`）。

`harvestToggleStates`（`:167-192`）对比一个**开着**和一个**关着**的原生开关。
原生状态由**一个额外的类**表达，而非 `aria-checked`（注释 `:162-166`）；它计算
`toggleOnExtra` = 开着的复选框上的类减去关着的复选框上的类（排除 `gpfocus`），并
保留 `toggleOnHTML` / `toggleOffHTML` 作为回退（`:178-191`）。

模板以 HTML 字符串缓存进 `sessionStorage` 的 `CACHE_KEY`
（`saveTemplates`, `:257-265`）。复用前，`loadTemplates` 通过 `selectorKnown`
验证探测类仍存在于某个样式表规则中（`:242-255`, `:267-281`）；如果 Valve 换了哈希
类名，缓存被丢弃并重抓。

### 5. 构建并追加行

`toggleRow(label, icon)`（`:382-397`）把 `tpl.toggleRowHTML` 克隆成节点，设
`id = ROW_ID`，通过 `setLabel` 改名（`:297-308`），通过 `setIcon` 换 SVG 图标
（`:310-315`），去掉 `aria-labelledby`、设 `aria-label`，并应用开/关外观。
`mount()` 在 `tpl.sectionCls` 存在时把行包进一个克隆的 `section` div
（`:465-470`），追加到设置页 body（`:471`），然后设置 `ui`、调用 `applyHapticWrap()`
和 `render()`（`:473-475`）。

外观被内联写死：因为 popup 未被合成时 CEF 会节流 CSS 过渡，`pinToggleLook` 禁用
`transition` 并直接把旋钮的 `transform` 和轨道的 `background` 写进去（`:322-345`），
`ensureLookStyle` 则抑制轨道 `::before` 的蓝色填充（`:328-335`）。

### 6. 自愈

`startObserver` 在 `document.body` 上装一个 `MutationObserver`
（`childList: true, subtree: true`）调用 `schedule`（`:581-586`）。`schedule` 去抖
60 毫秒后运行 `sync`（`:572-579`）。`sync` 在行消失时重新挂载，否则重新渲染
（`:562-570`）。这就是为什么开关在 React 重渲染或切标签后会自动回来，也是 README
说启动 SteamVR 几秒后开关出现、无需重装的原因（`README.md:61-62`）。

---

## 按下开关时的确切时间线

用户按下 `#sf_haptic_row` 内被克隆的 `[role="checkbox"]`。震动改变之前没有任何
到守护进程的往返——抑制是本地、立即施加的；守护进程只负责持久化。

1. **启动接线。** 注入时 `hookToggle()` 注册 window 级、**捕获阶段**的监听器：
   `pointerdown` 和 `click`（`device/sf_haptic_switch.js:536-537`），以及一个针对
   `Enter`/`Space`/`Spacebar` 的匿名 `keydown` 监听器（`:538-541`）。捕获阶段正是
   让该处理器先于 React 自己的委托运行的原因。

2. **事件到达 → `flip(ev)`**（`:514`）。`toggleTarget(ev)`（`:488-496`）：
   `liveCheckbox()` 返回 `#sf_haptic_row [role="checkbox"]`（`:484-486`）；事件
   target 若*就是*该复选框、被它包含、或在 `#sf_haptic_row` 内，则被接受。若不是
   我们的，`flip` 立即返回（`:516`）。

3. **双触发保护。**
   - 对 `click`：若距一次已处理的 `pointerdown` 在 **800 毫秒**内，忽略
     （`:517-518`）。这是防止浏览器为一次按下同时发出两个事件的保护。
   - 对非 click（`pointerdown`/`keydown`）：要求 `ev.button === 0` 并记录
     `sawDown = Date.now()`（`:519-522`）。

4. **抑制原生处理器：** `ev.preventDefault()` 和 `ev.stopPropagation()`
   （`:523-524`），这样 React 永远不会去切换原生控件。

5. **限速保护：** 若距上次翻转不足 **250 毫秒**则忽略（`lastFlip`, `:525-527`）。

6. **读取当前显示状态**：从复选框自身的 `data-sf-state` 属性（`'1'`/`'0'`）读，
   回退到 `state.enabled`（`:529-533`）。然后调用 `setEnabled(!on)`（`:533`）。

7. **`setEnabled(next)` —— 乐观更新**（`:499-509`）：设置 `state.enabled = next`
   （`:503`），记住 `pendingEnabled = next`、`pendingEnabledAt = now`（`:504-505`），
   然后**先发送** `send({ cmd: 'set_haptic', value: next })`（`:506`），再
   `render()`（`:507`）。`send()` 在 binding 存在时调用
   `window.__sfHapticSend(JSON.stringify(obj))`（`:56-64`）。

8. **`render()`**（`:552-560`）重新施加视觉（`applyToggleLook`, `:556`）并调用
   `applyHapticWrap()`（`:559`）。

9. **震动在此刻改变。** `applyHapticWrap()`（`:411-430`）：包装器**只装一次**
   （`fn.__sfHapticWrapped` 守卫，`:415`）。被补丁的函数读取活的闭包
   `state.enabled`：

   ```js
   function patched(handle, effect) {
     if (!state.enabled &&
         (effect === EFFECT_BUTTON_ENTER || effect === EFFECT_BUTTON_LEAVE)) {
       return 0;
     }
     return original(handle, effect);
   }
   ```
   （`device/sf_haptic_switch.js:417-423`）

   因为第 7 步已经把 `state.enabled = false`，紧接着的
   `TriggerOverlayHapticEffect(handle, ButtonEnter=1 | ButtonLeave=2)` 调用返回
   `0`，不再发出震动。**这就是 hover 震动真正停止的时刻** —— 与守护进程无关。

10. **守护进程收到命令。** `window.__sfHapticSend(...)` 触发一个
    `Runtime.bindingCalled` 事件。在守护进程循环里，每 0.5 秒 `push()` 会运行
    `handle_commands()`（`device/sf_haptic_switch_daemon.py:356`）。`handle_commands`
    （`:358-371`）排空排队的 CDP 事件，只保留
    `params.name === "__sfHapticSend"` 的 `Runtime.bindingCalled`（`:360`, `:363`），
    JSON 解析 `payload`（`:365-367`），并调用 `execute(msg)`（`:371`）。

11. **`execute` → 持久化。** `execute`（`:373-381`）读取 `cmd` 和 `value`，记录
    `command: set_haptic <value>`，对 `set_haptic` 调用
    `self.haptic.set_enabled(val)`（`:378-379`）。

12. **`HapticSwitch.set_enabled`**（`:270-277`）：若未变化则 no-op（所以与持久化
    状态一致的翻转不写任何东西）；否则设置 `self.enabled = enabled` 并
    `self._save()`。

13. **写 `state.json`。** `_save()` 把 `{"enabled": …}` 写进一个 `.tmp` 再
    `os.replace` 原子替换（`:259-268`）。失败被吞掉、只记日志，所以只读安装也能
    继续工作（`:265-268`）。

14. **周期性推回页面。** 下一轮循环守护进程调用 `push()`（`:346-356`），评估
    `window.__SF_HAPTIC__ && window.__SF_HAPTIC__.setState({"enabled": <value>})`
    （`:349-351`）。注意推送的值取自守护进程的 `HapticSwitch.enabled`，即第 12 步
    刚持久化的值。

15. **前端 `setState` 带回显抑制**（`device/sf_haptic_switch.js:594-608`）：对
    `enabled` 键，若推送值与最近的 pending 翻转**不一致**且仍在 `PENDING_MS`
    （2500 毫秒，`:45`）内，则跳过该键（`:600-602`），以免开关肉眼可见地弹回。
    一旦守护进程的值与 pending 值一致，`pendingEnabled` 被清空、值被赋值
    （`:603-604`）。随后运行 `render()`（`:607`）。

16. **`render()` / `applyToggleLook`** 依据 `state.enabled` 重申视觉
    （`:552-560`, `:347-380`），让开关与守护进程保持一致。

所以可观察的结果——「震动关闭」——发生在**第 9 步**（页面内，与按下同步），而持久化
和权威状态在第 10–13 步。

### CLI 路径（绕过 UI）

`main()` 还支持 `--status`、`--on`、`--off`（`device/sf_haptic_switch_daemon.py:428-443`）：
它们构造一个 `HapticSwitch`、调用 `set_enabled` 并打印 JSON。它们会写
`state.json`，但不直接碰页面；运行中的守护进程会在下次推送时取到新值。

---

## 震动到底是怎么被抑制的

唯一关闭震动的，就是页面内对 `VRHTML.VROverlay.TriggerOverlayHapticEffect` 的包装。

- **包装什么：** `overlayApi()` 返回 `window.VRHTML.VROverlay`
  （`device/sf_haptic_switch.js:403-407`）；`applyHapticWrap()` 在
  `ov.TriggerOverlayHapticEffect` 是函数时包装它（`:413-414`），把原函数绑定为
  `original = fn.bind(ov)`（`:416`）。
- **效果编号：** `EFFECT_BUTTON_ENTER = 1`、`EFFECT_BUTTON_LEAVE = 2`
  （`:29-31`）。与头注释里的 `EOverlayHapticEffect` 枚举一致：
  `None=0, ButtonEnter=1, ButtonLeave=2, Snap=3, Sliding=4, SlidingEdge=5`
  （`:9-10`, `device/技术说明.md:63-66`）。
- **关闭时的行为：** 当 `state.enabled` 为 false 时，对
  `ButtonEnter`/`ButtonLeave` 返回 `0`（吞掉效果）（`:418-420`）。
- **打开时的行为：** 调用绑定的原函数并返回其结果（`:422`）。滑块
  （`Sliding`/`SlidingEdge`）、吸附（`Snap`）及其它所有效果都原样放行——符合
  「只移除按钮 hover 反馈」的承诺（`README.md:14-15`, `:139`）。
- **幂等：** 包装器至多安装一次，通过 `__sfHapticWrapped` 标志；
  `__sfHapticOriginal` 存原函数用于还原（`:424-426`）。`render()` 每次渲染都调用
  `applyHapticWrap()`，靠该标志避免重复包装（`:559`）。
- **还原：** `restoreHaptic()`（`:432-440`）把 `fn.__sfHapticOriginal` 放回
  `VRHTML.VROverlay.TriggerOverlayHapticEffect`，由 `teardown()` 调用（`:622`）。
  守护进程在关闭时触发 teardown（`device/sf_haptic_switch_daemon.py:401-408`），
  `uninstall.sh` 停服务从而让 teardown 运行（`uninstall.sh:26-30`）。

**重要局限：** `TriggerOverlayHapticEffect(handle, effect)` 接收的是**预设效果编号，
不是幅度**。所以这一层只能抑制/替换/限频效果；无法缩放幅度
（`device/技术说明.md:71-73`）。

---

## 状态语义与持久化

- **默认值：** 页面（`device/sf_haptic_switch.js:33`）和守护进程
  （`device/sf_haptic_switch_daemon.py:244`）中都是 `enabled = true`。默认 = 原生行为。
- **存哪里：** 守护进程旁的 `state.json`（`STATE_FILE`,
  `device/sf_haptic_switch_daemon.py:51-52`）；格式 `{"enabled": bool}`（`:263`）。
  它已被 git 忽略（`.gitignore:15`）。
- **重启时：** `HapticSwitch._load()` 读取 `state.json` 还原值
  （`device/sf_haptic_switch_daemon.py:247-257`）；`FileNotFoundError` 保留默认值
  （`:254-255`）。因为页面是全新重建的，包装器会依据守护进程在首次 `setState`
  时推送的值开始门控。
- **只读安装：** `_save()` 捕获写错误、只记日志（`:265-268`），所以插件仍能工作；
  只是重启后状态重置回 `true`（`device/技术说明.md:77-79`）。
- **只有守护进程持有权威值**，且每 0.5 秒推回页面一次；前端用
  `pendingEnabled`/`PENDING_MS`（2500 毫秒）在改动在途期间扛过回显
  （`device/sf_haptic_switch.js:43-46`, `:594-608`）。

---

## 已验证 vs 假设 / 未决问题

### 已验证（在本机对工作树运行过）

- **Python 语法：** `python -m py_compile device/sf_haptic_switch_daemon.py` → OK。
- **JS 语法：** `esprima.parseScript(device/sf_haptic_switch.js)` → OK（esprima 4.0.1）。
- **Shell 语法：** `bash -n install.sh`、`bash -n uninstall.sh`、
  `bash -n diagnose.sh` → 全部 OK。
- **跨文件一致性：** binding 名、target 字符串、效果编号、`ROW_ID`、
  `CACHE_KEY`/`BODY_KEY`、`state.json` 形状、`window.__SF_HAPTIC__` 的 API 名，在
  `device/sf_haptic_switch.js` 与 `device/sf_haptic_switch_daemon.py` 之间全部一致。

### 假设 / 未在设备上验证

以下全部来自代码和注释，而非真机 Steam Frame。

1. **hover 震动究竟在哪里发出 —— 最大的未决问题。**
   插件只注入 GamepadUI 的快速访问菜单 popup `barpopup`（`device/sf_haptic_switch_daemon.py:46`），
   并**在该页面内**包装 `VRHTML.VROverlay`。此前的调研笔记
   （`ref/我想修改 …可以.md:625-712`, 尤见 `:627`, `:637`, `:677-683`）结论是：
   Dashboard 的 hover 震动由**原生 Dashboard 页面 `systemui`** 发出
   （`/opt/steamvr/resources/webinterface/dashboard/systemui.html`，可在
   `127.0.0.1:8087` 访问），*而非*快速访问菜单 popup。若属实，`barpopup` 里的包装永远
   拦不到真正的 `TriggerOverlayHapticEffect` 调用，开关将毫无效果。README 自己也
   标出了这个确切风险（`README.md:135-138`：「如果 hover 震动实际由另一个页面
   （例如 Dashboard 的 `systemui`）产生，则需要在那个页面同样注入，本插件才会
   生效。`TARGET_MATCH` 可改」）。本笔记假设快速访问菜单 popup 是对的目标页，但所引调研
   指向相反方向。**未解决；必须在设备上实测。**

2. **效果编号假设。** `ButtonEnter=1`/`ButtonLeave=2` 在代码注释
   （`device/sf_haptic_switch.js:9-10`）和技术文档（`device/技术说明.md:63-66`）里被
   断言，调研笔记（`ref/…可以.md:663-672`）也佐证，但本仓库未对照发布版 SteamVR
   bundle 确认。

3. **设置页 body 探测是启发式。** 「控件最多」投票加 `quickaccess_content_4` 加权
   （`device/sf_haptic_switch.js:126-152`）以及「控件数 ≥ 2」阈值，都是在未公开的
   React DOM 上的启发式。若 Valve 改名 `#QuickAccess-Menu`、`[role="tablist"]`、
   `[id^="quickaccess_content_"]` 或不再用 `[role="checkbox"]`，探测即失效。
   没有设备端证据表明 `_4` 后缀稳定。

4. **靠 diff 出的类来做开关样式。** `toggleOnExtra` 靠对比开着/关着的原生开关反推
   （`:167-192`），只由 `selectorKnown` 扫描 `document.styleSheets` 做校验
   （`:242-255`）。具体类字符串是构建期产物，属假设，上游无文档。

5. **页面内包装是无条件安装的，但只有当该页面确实调用该 API 才有意义。**
   `applyHapticWrap()` 不验证页面真的发出它所门控的效果；若不发出，hook 静默装上
   却什么都不做（与第 1 点相关）。

6. **keydown 监听器泄漏（已观察到，未在设备上测）。** `hookToggle()` 在 `cleanups`
   里只移除了 `pointerdown` 和 `click`（`device/sf_haptic_switch.js:542-545`）；匿名
   `keydown` 监听器（`:538-541`）从未被移除。由于每次（重新）注入在
   `prev.teardown()` 后都会重跑 `hookToggle()`（`:20-22`, `:635`），陈旧的 keydown
   监听器会累积。`lastFlip` 的 250 毫秒守卫是每个闭包各自的、非共享，所以一次按键
   可能被陈旧闭包和新闭包同时处理。

7. **守护进程→页面推送值 vs. fire-and-forget 发送。** 发送是异步的
   （`window.__sfHapticSend` 只是触发一个事件）；在守护进程下次 `push()` 之前，
   页面对视觉而言是权威（通过 `pendingEnabled`）。2500 毫秒窗口（`:45`）被假定
   足以覆盖 0.5 秒轮询；未在高负载下测量。

8. **安装期验证只靠 journal。** `install.sh` 最多等 15 秒让 `inject ->` 出现
   （`install.sh:108-133`），`diagnose.sh` 只列 CEF targets 并 grep journal
   （`diagnose.sh:90-110`, `:41-48`）。两者都不确认开关真的渲染、或翻转真的静音。

9. **本仓库没有设备端工具。** 参考项目随附 `tools/`（包括用 CEF 真实引擎解析 JS 的
   `sf_syntax_check.py`、`dump_toggles.py`、无头测试）。本仓库已被平铺，**没有**
   `tools/` 目录，所以这里只能做上面「已验证」里的离线检查。

---

## 可复用总结

管线是：**systemd → CDP 连接（`Runtime.enable` + `addBinding`）→ hash 门控的
`Runtime.evaluate` 注入 → 把一行原生 `[role=checkbox]` 克隆进设置页 body → 包装
`TriggerOverlayHapticEffect` → 按下时翻转本地 `state` 并经 binding 发回
`{cmd:'set_haptic',value}` → 守护进程持久化到 `state.json` 并每 0.5 秒重推
`setState`。**

唯一让手柄停止嗡嗡响的，是补丁后的 `TriggerOverlayHapticEffect` 里
`state.enabled` 为 false 时的那句 `return 0` —— 而这个补丁到底会不会被触达，取决于
那个未决问题：hover 震动究竟是哪个页面发出的。
