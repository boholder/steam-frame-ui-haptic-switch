# T8 — 修复审计确认的运行时 / UI 问题

- **状态**：ready（设计定稿，待实现）
- **载体**：本地 ticket
- **阻塞于**：T7（已完成）
- **阻塞**：无
- **范围**：只改现有文件，不新增源文件

## 背景

两轮并行 bug 审计（Python 运行时 + JS/集成）确认了一批真实问题。以下只列**已确认要改**的项；已决定保留/忽略的项见文末。

## 设计定稿（grilling 结论）

以下决定已与用户确认，实现时不再重新讨论：

> 编号说明：本轮 grilling 的新决定记为 **D1–D8**；各改动项里原有的 `Q1 A`、`Q7 A` 等是上一轮审计的编号，保留不动，互不冲突。

- **D1（原 Q1，第 8 项）**：`Endpoint.call` 超时后调用 `self.close()`，再抛 `WSError`。超时意味着对端不可信；让 `_ensure_endpoint` 重连优于保留一个“活着但永不响应”的 endpoint。
- **D2（原 Q2，第 8 项）**：修 `WebSocket.close()` 的锁竞争。它当前先抢 `_send_lock` 发 close 帧；sender 若卡在 `sendall` 里持锁，`close()` 会一起卡死。改为非阻塞取锁：拿到就发 close 帧；拿不到就跳过，直接 `_closed = True` + `shutdown` + `close`。锁空闲时行为不变。
- **D3（原 Q3 / Q9，第 5 项）**：克隆行的后代 id 一律**删除**，不生成新 id。抽 `stripIds(row)`，在 `toggleRow` 的 `applyToggleLook` 之后、以及 `render()` 替换 checkbox 之后调用。理由：仓库内没有代码依赖这些 id；生成新 id 还要重写 `aria-labelledby` 等引用，且 HTML fallback 每次替换都要重做，收益为零。
- **D4（原 Q4，测试）**：JS / shell 用**限定作用域的静态契约**（正则在 `function flip` / `function teardown` 等函数体内匹配）；Python 用 fake 对象做行为测试。本机没有 node，只有 esprima（可解析、不能执行）。
- **D5（原 Q5，测试）**：不改 `Daemon.__init__` 的构造签名。测试直接驱动 `_attach` / `_on_user_change`，注入 fake `Endpoint` / `Session`，必要时 monkeypatch 模块级 `save_state`。
- **D6（原 Q6，第 8 项）**：reader 线程里的 pong 保持直发，不绕 sender 队列。sender 长期阻塞时 endpoint 已不可用，由 D1 的超时关闭和 D2 的 shutdown 兜底。
- **D7（原 Q7，第 2 项）**：写盘失败时 `steam_synced = False`，让下一次 `_prepare_session` 重试；成功或值未变化时保持 True。
- **D8（原 Q8）**：实现由 `implement-spec` 流程接管（单 ticket，本地 tracker）。

## 改动清单

### 1. Python 2 — attach 失败时 detach

**文件**：`src/sf_ui_haptic_switch_daemon.py`（`Daemon._attach`）

**现状**：`Target.attachToTarget` 成功后，`Runtime.enable` / `Page.enable` / `_prepare_session` 任一失败，只走 `_drop_target` 删除本地 `sessions` / `target_sessions`；浏览器侧的 flattened session 没有 `Target.detachFromTarget`，会泄漏。`targetInfoChanged` 反复 attach 时可能叠加旧 session。

**改法**：

- 在 `_attach` 的异常分支里，如果已经拿到 `session_id`，先尽力：
  ```python
  try:
      endpoint.call("Target.detachFromTarget",
                    {"sessionId": session_id}, timeout=2.0)
  except Exception:
      pass
  ```
- 再执行现有的 `_drop_target(...)`。
- `Target.attachToTarget` 本身失败（没有 `session_id`）时不需要 detach。
- 不要在 `Target.targetDestroyed` / `Target.detachedFromTarget` 上重复 detach；这两种情况浏览器侧已经处理。

**测试**：用 fake endpoint 记录 `detachFromTarget`；模拟 `Runtime.enable` 抛错，断言 detach 被调用且本地映射被清掉。

---

### 2. Python 3 — 状态写盘失败时回滚

**文件**：`src/sf_ui_haptic_switch_daemon.py`（`Daemon._set_enabled` / `_apply_enabled`）

**现状**：先改 `self.enabled`，`save_state` 失败只打日志；内存与磁盘分叉，重启后可能静默回退。

**改法（原 Q2 B：回滚 + 推旧值）**：

- `_set_enabled` 保存旧值：
  ```python
  previous = self.enabled
  self.enabled = enabled
  try:
      save_state(self.state_file, enabled)
  except Exception as e:
      self.enabled = previous
      log("state write failed: %s; reverting" % e)
      return False
  return True
  ```
- `_apply_enabled` 由回滚后的状态判断落盘是否成功：`ok = self.enabled == enabled`。写盘成功或值未变化时为 True；写盘失败被回滚后为 False。
- `self.steam_synced = ok`（D7）：写盘失败时置 False，下一次 `_prepare_session` 会再次 `_sync_from_steam()`；成功或未变化时置 True。
- 无论成败都 `_push_vr()`；`_set_enabled` 返回 False 时推的是回滚后的旧值。
- **已知取舍**：`:8080` localStorage 已是新值，`:8087` 会被推回旧值；下一次 `_sync_from_steam()` 会再次尝试写盘。如果磁盘持续失败，UI 与 8087 会短暂不一致。这是原 Q2 B + D7 选择的后果，ticket 接受。

**测试**：用 fake `save_state` 抛异常；断言 `self.enabled` 保留旧值、`daemon.steam_synced` 为 False，且 `_push_vr` 推的是旧值。再补一个写盘成功的用例，断言 `steam_synced` 为 True。

---

### 3. Python 5 — uninstall stop 失败与 vrserver 回写

**文件**：`uninstall.sh`

**现状**：`systemctl stop ... || true` 后继续删 unit/状态；daemon 若仍在跑会重建 state 并继续注入。另外删除 `DebuggerEnabled` 时如果 SteamVR 正在运行，vrserver 退出时可能回写内存配置。

**改法（原 Q4 A）**：

- `systemctl stop "$SERVICE"` 之后保留 `sleep 1`，然后：
  ```bash
  if systemctl is-active --quiet "$SERVICE"; then
    die "$SERVICE 仍在运行；中止卸载，避免 daemon 继续注入"
  fi
  ```
  - 放在删除 unit/状态/配置**之前**；仍 active 时什么都不删。
- 删除配置前检测：
  ```bash
  if pgrep -f '[v]rserver' >/dev/null 2>&1; then
    warn "SteamVR 正在运行；删除 DebuggerEnabled 后，SteamVR 退出时可能回写配置"
  fi
  ```
- 保持现有 `systemctl stop ... || true` 的形式，但用 `is-active` 做最终判断。

**测试**：`tests/test_units.py` 增加/更新断言：uninstall 含 `is-active` 与 `die`；shellcheck/`bash -n` 通过。真机 stop 失败场景手工验证。

---

### 4. Python 8 — teardown 逐步 catch

**文件**：`src/sf_ui_haptic_switch_daemon.py`（`teardown_expr`）

**现状**：一个 `try` 包住 `ui.teardown()`、`gate.teardown()` 和 `delete window.NS`；UI 抛错会跳过 gate 和 NS 删除，gate 的 1s 定时器继续跑。

**改法**：

```js
(function(){
  var n = window.__SF_UI_HAPTIC_SWITCH__;
  if (!n) return;
  try { if (n.ui && n.ui.teardown) n.ui.teardown(); } catch (e) {}
  try { if (n.gate && n.gate.teardown) n.gate.teardown(); } catch (e) {}
  try { delete window.__SF_UI_HAPTIC_SWITCH__; } catch (e) {}
})()
```

**测试**：`tests/test_daemon.py` 断言 `teardown_expr()` 含三个独立 `try`/`catch`（或通过 fake JS 对象验证 gate 仍被调用）。

---

### 5. JS 3 — 克隆行去掉后代 id

**文件**：`src/sf_ui_haptic_switch_ui.js`（`toggleRow`）

**现状**：`row.id = ROW_ID` 只改最外层；`row.outerHTML` 里的 `..._Label` 等后代 id 原样复制，与原行并存，`getElementById` 可能取错节点。

**改法（Q9 / D3：删除，不改名）**：

- 增加一个 helper：
  ```js
  function stripIds(row) {
    var ids = row.querySelectorAll('[id]');
    for (var i = 0; i < ids.length; i++) ids[i].removeAttribute('id');
  }
  ```
  `querySelectorAll` 只扫后代，不含 row 自身，所以 `row.id = ROW_ID` 与调用顺序无关；仍按现有顺序保留。
- 在 `setLabel` / `setIcon` **之后**（它们还会用 `[id$="_Label"]`）、`applyToggleLook` **之后**调用 `stripIds(row)`。
- `render()` 里 `applyToggleLook` 可能走 HTML fallback 整块替换 checkbox，替换后再调用一次 `stripIds(row)`（用 `cb.closest('#' + ROW_ID)` 或 `document.getElementById(ROW_ID)` 取 row）。
- 保留 `aria-labelledby` 的移除逻辑；`labelBoxOf` 在去 id 之前已执行，不受影响。
- 不要给后代重新生成 id。

**测试**：`tests/test_scripts_parse.py` 断言 UI 源含 `function stripIds`、`querySelectorAll('[id]')`、`removeAttribute('id')`，且 `stripIds(` 至少出现 3 次（定义 + `toggleRow` + `render`）。

---

### 6. JS 4 — 节流与焦点

**文件**：`src/sf_ui_haptic_switch_ui.js`（`hookToggle.flip`）

**现状**：`sawDown` 在 `lastFlip` 节流检查之前写入，被节流的 pointerdown 会压掉随后 800ms 内的真实 click；window 捕获阶段对 pointerdown `preventDefault` 会阻止该行获得指针焦点。

**改法（原 Q7 A）**：

- 先算 `now`，先过 `lastFlip` 节流，再写 `sawDown`。
- 只在 `pointerdown` 上不 `preventDefault`；`click` / `keydown` 保留 `preventDefault`。
- `stopPropagation` 保持。
- 结构（250ms 节流只写一次）：
  ```js
  function flip(ev) {
    var cb = toggleTarget(ev);
    if (!cb) return;

    var now = Date.now();
    if (ev.type === 'click') {
      if (now - sawDown < 800) return;   /* already handled pointerdown */
    } else if (ev.type === 'pointerdown') {
      if (ev.button !== undefined && ev.button !== 0) return;
    }
    if (now - lastFlip < 250) return;
    if (ev.type === 'pointerdown') sawDown = now;
    lastFlip = now;

    if (ev.type !== 'pointerdown' && ev.preventDefault) ev.preventDefault();
    if (ev.stopPropagation) ev.stopPropagation();
    ...
  }
  ```
- 注意 keydown 不再写 `sawDown`；它本来也不产生 pointerdown/click 配对。
- 被节流的 pointerdown 不写 `sawDown`，所以随后 800ms 内的真实 click 仍会翻转。

**测试**：`tests/test_scripts_parse.py` 断言 `pointerdown` 分支不含 `preventDefault`，且 `sawDown` 出现在节流之后（静态契约；无 DOM harness）。

---

### 7. JS 7 — gate 的 teardown 停止标志

**文件**：`src/sf_ui_haptic_switch_gate.js`

**现状**：`teardown()` 只 `clearInterval`；已排队的 `install` 回调仍可能在 teardown 后重新包装 bridge，且 `NS.gate` 已为 null，包装无法再被清理。

**改法（原 Q8 A + 注释）**：

- 增加 `var stopped = false;`。
- `install()` 第一行：
  ```js
  if (stopped) return false;
  ```
- `teardown()` 第一行：
  ```js
  stopped = true;
  ```
  再执行 `clearInterval` / `restore` / `NS.gate = null`。
- 加注释说明：与 UI 的 `tornDown` 同理，防止已排队的 interval 回调在 teardown 后重新包装 bridge。

**测试**：`tests/test_scripts_parse.py` 增加 `test_gate_stops_after_teardown`，断言 `stopped`、`if (stopped) return false;` 和对应注释关键词。

---

### 8. Python 1 — Endpoint sender 线程

**文件**：`src/sf_ui_haptic_switch_cdp.py`（`Endpoint`）

**现状**：每个 endpoint 有独立 reader 线程，但 `Endpoint.call` / `evaluate` 的 `sendall` 在 daemon 主线程执行；对端不读时 `sendall` 可永久阻塞主循环。

**改法（原 Q1 A）**：

- `Endpoint.__init__` 增加 `self._send_queue = queue.Queue()` 和 `self.sender = None`。
- `connect()` 在启动 reader 后启动 sender 线程：
  ```python
  self.sender = threading.Thread(
      target=self._send_loop, args=(ws,), name="cdp-send-%d" % self.port,
      daemon=True)
  self.sender.start()
  ```
- `call()` 不再直接 `self.ws.send_text(...)`，而是把消息放入 `_send_queue`；如果 `alive` 为 false 或 `ws` 为 None，抛 `WSError`。
- `_send_loop(ws)`：
  ```python
  while self.alive and self.ws is ws:
      msg = self._send_queue.get()
      if msg is None:
          return
      try:
          ws.send_text(msg)
      except Exception as e:
          if self.ws is ws:
              self.alive = False
              self._fail_pending(WSError("send failed: %s" % e))
          return
  ```
- `close()`：
  - 先 `alive=False`；
  - `_send_queue.put(None)`；
  - 关闭 ws（shutdown 会唤醒阻塞的 `sendall`）；
  - join sender（和 reader 一样有超时）；
  - 现有 `_fail_pending` 保持。
- 超时处理（D1）：`call` 等待响应超时后，先 `self.close()`，再抛 `WSError`。关闭会唤醒阻塞的 sender、失败所有 pending，并让 `_ensure_endpoint` 在下一轮重连。`_attach` / `teardown_all` 现有的异常处理不用改。
- `WebSocket.close()`（D2）：sender 可能正卡在 `sendall` 里持有 `_send_lock`。当前 `close()` 会先抢锁发 close 帧，结果自己也卡死。改为非阻塞取锁：
  ```python
  locked = self._send_lock.acquire(blocking=False)
  if locked:
      try:
          self._send_frame_unlocked(b"", 0x8)
      finally:
          self._send_lock.release()
  self._closed = True
  shutdown / close
  ```
  把现有 `_send_frame` 拆出 `_send_frame_unlocked`（调用方仍持锁）。锁空闲时仍先发 close 帧，现有测试语义不变；拿不到锁就跳过 close 帧，直接 shutdown，让阻塞的 `sendall` 退出。
- `close()` 的 join 若发现当前线程就是 sender，跳过 sender 的 join（防御性；目前没有该调用路径）。
- `_read_loop` / `_fail_pending` / `call` 的 pending 逻辑不变。

**测试**：

- fake `ws` 的 `send_text` 阻塞（用 event 控制）；断言 `call` 在超时后抛 `WSError`、endpoint 被关闭（`alive` 为 false）、阻塞的 sender 被 `close()` 唤醒并退出。为避免挂死，超时与 join 都用短值。
- `WebSocketCloseTests` 增加：预先持有 `_send_lock`（模拟阻塞中的 sender），调用 `close()` 必须在短时间内返回、不发送 close 帧，但 `shutdown`/`close` 仍被调用。
- 现有 `WebSocketCloseTests`、`CommandLogTests` 保持通过。

---

### 9. JS 2 / Q6 — 单 class harvest

**文件**：`src/sf_ui_haptic_switch_ui.js`（`harvestToggleStates`）

**现状**：`diff` 可能包含多个 class，`diff.join(' ')` 得到 `"a b"`；`applyToggleLook` 按单个 token 过滤，关闭时删不掉。

**改法（原 Q6 A + Q6a A：保留 HTML fallback）**：

- 只在 `diff.length === 1` 时使用 class 快路径：
  ```js
  var extra = null;
  if (on && off) {
    var offSet = classList(off);
    var diff = classList(on).filter(function (c) {
      return c !== 'gpfocus' && offSet.indexOf(c) < 0;
    });
    if (diff.length === 1) extra = diff[0];
  }
  return {
    toggleOnExtra: extra,
    toggleOnHTML: on ? on.outerHTML : null,
    toggleOffHTML: off ? off.outerHTML : null
  };
  ```
- `toggleOnExtra` 为 `null` 时，`applyToggleLook` 继续走已有的 `toggleOnHTML` / `toggleOffHTML` 整块替换。
- **不要**在 `applyToggleLook` 里 split/解析空格；不要改 `toggleOnHTML` / `toggleOffHTML` 的用途。
- 这是对既有 fallback 的复用，不是新增防御：当只渲染一种状态、class diff 为空或 diff 多于一个时，HTML 替换仍然能正确显示。

**测试**：`tests/test_scripts_parse.py` 断言 harvest 里有 `diff.length === 1` 和 `extra = diff[0]`；不出现 `diff.join(' ')`。

---

### 10. Q3 — install/uninstall 精简

**文件**：`install.sh`、`uninstall.sh`

**改法（原 Q3a + Q3b A）**：

- 两个脚本都改为：
  ```bash
  SRC="$HERE/src"
  ```
  删除 `[ -d "$SRC" ] || SRC="$HERE"`。`src/` 缺失时由现有文件检查明确报错。
- `install.sh` 删除 `ESCAPED_DEST`，直接：
  ```bash
  sed -i "s|INSTALLATION_PATH|$DEST|g" "$UNIT_DIR/$SERVICE"
  ```
- 保留：
  - `LIB` 存在检查（报错清晰）；
  - `grep -q 'INSTALLATION_PATH'` 替换校验。
- `uninstall.sh` 的 `SRC` 同样去回退；`SETTINGS="$SRC/sf_ui_haptic_switch_settings.py"` 不变。

**测试**：`tests/test_units.py` 断言两个脚本含 `SRC="$HERE/src"`，不含 `SRC="$HERE"` 回退和 `ESCAPED_DEST`；同步更新现有的 `test_install_replaces_the_service_placeholder`（它当前断言 `ESCAPED_DEST`）；`bash -n` 通过。

---

## 明确不改

- **Python 6**：单 UI 握手失败 `fatal` 是 Q1e 的设计选择，保留。
- **Python 7**：错误 origin 的 localStorage，T0 表明所有客户端页面共享 `steamloopback.host`，忽略。
- **JS 1**：非 UI 页面权威，同上。
- **JS 6**：第三方再包一层导致 restore 跳过，理论场景，忽略。
- **Q6 的 HTML fallback**：保留（既有实现，真实触发条件是只渲染一种状态/class rehash）。
- **`Page.addScriptToEvaluateOnNewDocument` 注册**：研究笔记实测为 session-scoped，不需要显式 remove。

## 测试与验收

**预同意的 seam（D4 / D5）**：

- `Endpoint`：monkeypatch `cdp.WebSocket` / `cdp.http_json` 后走公开的 `connect()` / `call()` / `close()`；不直接调 `_send_loop`。
- `WebSocket`：`object.__new__` + `FakeSocket`，直接调公开 `close()`。
- `Daemon`：直接驱动 `_attach` / `_on_user_change`，注入 fake `Endpoint` / `Session`；`save_state` 通过 monkeypatch 模块级符号替换。不改 `Daemon.__init__`。
- `teardown_expr` / `harvestToggleStates` / `hookToggle.flip` / gate `teardown`：JS 静态契约，正则限定在对应函数体内。
- shell：源码静态契约 + `bash -n`；真机场景只做手工验收。

**环境事实**：本机没有 node，只有 esprima（可解析、不能执行）；没有 `tests/test_cdp.py`，CDP 测试放在 `tests/test_daemon.py`。

- 每个改动按 TDD：先写失败测试，再实现（垂直切片，一项一个循环）。
- 全部完成后：
  - `python -m unittest discover -s tests`
  - `bash scripts/check.sh`
- 真机手工验证（如设备可用）：
  - `sudo ./uninstall.sh` 在 daemon 仍 active 时中止，且不删 unit/状态/配置；
  - attach 失败后 `Target.detachFromTarget` 被调用（可开 `--verbose`）；
  - `:8080`/`:8087` 的 teardown、QAM 开关、视觉状态。
- 不新增源文件；ticket 本身是唯一新增的 Markdown。

## 风险

- **Python 1**：sender 线程增加生命周期复杂度；必须保证 `close()` 能唤醒阻塞的 `sendall`（D2 的非阻塞 close 是关键），并避免线程泄漏。
- **Python 3**：原 Q2 B 会短暂出现 8080 新值 / 8087 旧值的不一致；D7 会在下一次 `_prepare_session` 重试写盘，持久失败时会反复重试（频率受 attach/context 限制）。
- **JS 4**：去掉 pointerdown 的 `preventDefault` 后，Steam 原生指针处理可能参与；`stopPropagation` 仍保留。
- **Q6**：diff 多于一个时依赖 HTML 替换；如果 HTML 也缺失，仍走现有的 aria/pin-only 行为。
- **Q3b**：`sed` 替换去掉转义后，若 checkout 路径含 `&`、`|` 或 `\`，单元文件里的 `INSTALLATION_PATH` 替换会出错。当前安装路径由用户 checkout 决定，接受该风险；`grep` 校验仍能发现未替换。

## 参考

- 审计结论：本轮两路 bug 审计（Python 运行时 / JS 集成）。
- 研究笔记：`docs/research/teardown-behavior.md`。
- 前序 ticket：`T7-daemon-8087.md`。
