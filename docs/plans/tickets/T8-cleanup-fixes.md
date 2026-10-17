# T8 — 修复审计确认的运行时 / UI 问题

- **状态**：todo（先不实现）
- **载体**：本地 ticket
- **阻塞于**：T7（已完成）
- **阻塞**：无
- **范围**：只改现有文件，不新增源文件

## 背景

两轮并行 bug 审计（Python 运行时 + JS/集成）确认了一批真实问题。以下只列**已确认要改**的项；已决定保留/忽略的项见文末。

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

**改法（Q2 B：回滚 + 推旧值）**：

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
- `_apply_enabled` 仍按现状 `steam_synced = True` 并 `_push_vr()`；`_set_enabled` 返回 False 时推的是回滚后的旧值。
- **已知取舍**：`:8080` localStorage 已是新值，`:8087` 会被推回旧值；下一次 `_sync_from_steam()` 会再次尝试写盘。如果磁盘持续失败，UI 与 8087 会短暂不一致。这是 Q2 B 选择的后果，ticket 接受。

**测试**：用 fake `save_state` 抛异常；断言 `self.enabled` 保留旧值，且 `_push_vr` 推的是旧值。

---

### 3. Python 5 — uninstall stop 失败与 vrserver 回写

**文件**：`uninstall.sh`

**现状**：`systemctl stop ... || true` 后继续删 unit/状态；daemon 若仍在跑会重建 state 并继续注入。另外删除 `DebuggerEnabled` 时如果 SteamVR 正在运行，vrserver 退出时可能回写内存配置。

**改法（Q4 A）**：

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

**改法**：

- 在 `setLabel` / `setIcon` **之后**（它们还会用 `[id$="_Label"]`），把 row 内所有后代 id 去掉：
  ```js
  var ids = row.querySelectorAll('[id]');
  for (var i = 0; i < ids.length; i++) ids[i].removeAttribute('id');
  row.id = ROW_ID;
  ```
- 保留 `aria-labelledby` 的移除逻辑；`labelBoxOf` 在去 id 之前已执行，不受影响。
- 不要给后代重新生成 id。

**测试**：`tests/test_scripts_parse.py` 断言 UI 源含 `querySelectorAll('[id]')` + `removeAttribute('id')`。

---

### 6. JS 4 — 节流与焦点

**文件**：`src/sf_ui_haptic_switch_ui.js`（`hookToggle.flip`）

**现状**：`sawDown` 在 `lastFlip` 节流检查之前写入，被节流的 pointerdown 会压掉随后 800ms 内的真实 click；window 捕获阶段对 pointerdown `preventDefault` 会阻止该行获得指针焦点。

**改法（Q7 A）**：

- 先算 `now`，先过 `lastFlip` 节流，再写 `sawDown`。
- 只在 `pointerdown` 上不 `preventDefault`；`click` / `keydown` 保留 `preventDefault`。
- `stopPropagation` 保持。
- 建议结构：
  ```js
  function flip(ev) {
    var cb = toggleTarget(ev);
    if (!cb) return;

    var now = Date.now();
    if (ev.type === 'click') {
      if (now - sawDown < 800) return;
    } else if (ev.type === 'pointerdown') {
      if (ev.button !== undefined && ev.button !== 0) return;
      if (now - lastFlip < 250) return;
      sawDown = now;
    } else {
      if (now - lastFlip < 250) return;
    }
    if (now - lastFlip < 250) return;
    lastFlip = now;

    if (ev.type !== 'pointerdown' && ev.preventDefault) ev.preventDefault();
    if (ev.stopPropagation) ev.stopPropagation();
    ...
  }
  ```
- 注意 keydown 不再写 `sawDown`；它本来也不产生 pointerdown/click 配对。

**测试**：`tests/test_scripts_parse.py` 断言 `pointerdown` 分支不含 `preventDefault`，且 `sawDown` 出现在节流之后（静态契约；无 DOM harness）。

---

### 7. JS 7 — gate 的 teardown 停止标志

**文件**：`src/sf_ui_haptic_switch_gate.js`

**现状**：`teardown()` 只 `clearInterval`；已排队的 `install` 回调仍可能在 teardown 后重新包装 bridge，且 `NS.gate` 已为 null，包装无法再被清理。

**改法（Q8 A + 注释）**：

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

**改法（Q1 A）**：

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
- 超时处理：`call` 仍然等待响应；如果响应超时，调用方（`_attach` / `teardown_all`）按现状处理。为避免 sender 永久阻塞，`Endpoint.close()` 必须关闭 socket，让 `sendall` 退出；`_ensure_endpoint` 在 `alive` 为 false 时重连。若需要更强保证，`call` 超时可以直接 `self.close()`，让 endpoint 重连；ticket 要求实现者明确选择并在测试中覆盖。
- `_read_loop` / `_fail_pending` / `call` 的 pending 逻辑不变。

**测试**：

- fake `ws` 的 `send_text` 阻塞（或用 event 控制）；断言 `call` 不阻塞调用线程，且 `close()` 能唤醒 sender。
- 现有的 `WebSocketCloseTests`、`CommandLogTests` 保持通过。
- 需要避免测试挂死：fake sender/close 用短超时。

---

### 9. JS 2 / Q6 — 单 class harvest

**文件**：`src/sf_ui_haptic_switch_ui.js`（`harvestToggleStates`）

**现状**：`diff` 可能包含多个 class，`diff.join(' ')` 得到 `"a b"`；`applyToggleLook` 按单个 token 过滤，关闭时删不掉。

**改法（Q6 A + Q6a A：保留 HTML fallback）**：

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

**改法（Q3a + Q3b A）**：

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

**测试**：`tests/test_units.py` 断言两个脚本含 `SRC="$HERE/src"`，不含 `SRC="$HERE"` 回退和 `ESCAPED_DEST`；`bash -n` 通过。

---

## 明确不改

- **Python 6**：单 UI 握手失败 `fatal` 是 Q1e 的设计选择，保留。
- **Python 7**：错误 origin 的 localStorage，T0 表明所有客户端页面共享 `steamloopback.host`，忽略。
- **JS 1**：非 UI 页面权威，同上。
- **JS 6**：第三方再包一层导致 restore 跳过，理论场景，忽略。
- **Q6 的 HTML fallback**：保留（既有实现，真实触发条件是只渲染一种状态/class rehash）。
- **`Page.addScriptToEvaluateOnNewDocument` 注册**：研究笔记实测为 session-scoped，不需要显式 remove。

## 测试与验收

- 每个改动按 TDD：先写失败测试，再实现。
- 全部完成后：
  - `python -m unittest discover -s tests`
  - `bash scripts/check.sh`
- 真机手工验证（如设备可用）：
  - `sudo ./uninstall.sh` 在 daemon 仍 active 时中止；
  - attach 失败后 `Target.detachFromTarget` 被调用；
  - `:8080`/`:8087` 的 teardown、QAM 开关、视觉状态。
- 不新增源文件；ticket 本身是唯一新增的 Markdown。

## 风险

- **Python 1**：sender 线程增加生命周期复杂度；必须保证 `close()` 能唤醒阻塞的 `sendall`，并避免线程泄漏。
- **Python 3**：Q2 B 会短暂出现 8080 新值 / 8087 旧值的不一致；下一次 sync 会重试。
- **JS 4**：去掉 pointerdown 的 `preventDefault` 后，Steam 原生指针处理可能参与；`stopPropagation` 仍保留。
- **Q6**：diff 多于一个时依赖 HTML 替换；如果 HTML 也缺失，仍走现有的 aria/pin-only 行为。

## 参考

- 审计结论：本轮两路 bug 审计（Python 运行时 / JS 集成）。
- 研究笔记：`docs/research/teardown-behavior.md`。
- 前序 ticket：`T7-daemon-8087.md`。
