# 计划：仅 `:8080` + `localStorage`（timer + oneshot 注入器）

**状态**：草稿（设计共识已达成；待确认后执行）
**范围**：把 `Controller UI Haptic` 开关插件改为「**只针对 `:8080`（Steam 客户端 UI）**、**状态存页面 `localStorage`**、**由一个 systemd timer 周期触发的一次性注入器**把脚本注入所有 target」。

> 本版基于 **T0 实测**（见 `docs/research/t0-device-probe.md`）：
> - `:8087`（SteamVR Dashboard）**未开**（`vrwebhelper` 不带 `--remote-debugging-port`）→ 本次**不覆盖**，延后到 T6。
> - `:8080` 全部页面**同源** `https://steamloopback.host` → **共享 `localStorage`**。
> - `:8080` 的桥接是 **`window.SteamClient.OpenVR.TriggerOverlayHapticEffect`**（不是 `VRHTML.VROverlay`）。

---

## 1. 目标

- 关/开 Steam 客户端 UI 页面里「手柄光标碰到按钮」的 hover 震动；
- 仅覆盖 `:8080`；`:8087` 留作后续；
- **无常驻进程**：一个 timer 周期触发一次性注入器；
- 状态由页面 `localStorage`（同源共享）持有，注入器**不持状态**。

---

## 2. 现状（改动前）

- 常驻 root 服务只连 `barpopup` 一个 target，注入一段 JS（UI + haptic 包装合一），持 `state.json`、0.5s 推送、`addBinding` 收点击。
- 仓库根：`src/`、`docs/`、`scripts/`、`install.sh`、`uninstall.sh`、`README.md`、`sf.ps1`、`AGENTS.md`、`.gitignore`。
  - `src/`：`sf_haptic_switch.js`、`sf_haptic_switch_daemon.py`、`sf-haptic-switchd.service`、`技术说明.md`。
- `diagnose.sh` 与 `VERSION` 当前**不在**仓库。

---

## 3. 已定决策（最终）

| # | 决策 | 结论 |
| --- | --- | --- |
| Q0/Q16 | 常驻守护进程 / `addBinding` | **作废**：状态改 localStorage 后无需回传进程 |
| Q1 | 架构 | **timer + oneshot**；无常驻进程 |
| Q2/Q15 | 状态存储 | **页面 `localStorage['sf_ui_haptic_switch']`**（`"1"`/`"0"`）；无 `state.json` |
| Q3 | 范围 | **只 `:8080`** 全部 target 注 gate；`barpopup` 额外注 ui；`:8087` 延后（T6） |
| Q4 | 权限 | 注入器以 `steamos` 运行；root 仅安装/卸载 |
| Q6/Q23 | 节奏 | `OnBootSec` + `OnUnitInactiveSec`（跑完歇 N 秒，缺省 3s） |
| Q7/Q9/Q24 | 命名 | 见 §6 |
| Q8 | 失败语义 | 端口未起/无 target → 静默退 0 |
| Q10/Q18 | 每拍动作 | 对 `:8080` 每个 target：注入 gate（幂等）；`barpopup` 额外注 ui；退出 |
| Q11 | 模板缓存 | `sessionStorage`（ui 脚本内部） |
| Q12 | 卸载清理 | 尽力对 `:8080` 所有 target `teardown`；失败仅警告 |
| Q13 | 迁移/回滚 | 无 `state.json`；回滚＝恢复旧单元/脚本 |
| Q14 | 前端 | 两脚本：`ui` 写 `localStorage`、`gate` 读 `localStorage`；**无 binding、无 pending/echo** |
| Q17 | 拦截范围 | 只包 `:8080` 的 `SteamClient.OpenVR.TriggerOverlayHapticEffect` |
| Q19 | 脚本组织 | 两个：`gate`（所有 target）/ `ui`（仅 `barpopup`） |
| Q21 | 连接模型 | 每拍对每个 target **短连接**（open→evaluate→close），不保留连接 |

---

## 4. 目标架构

```
sf-ui-haptic-switch.timer  (OnBootSec + OnUnitInactiveSec=3s)
        │ 触发（同名绑定）
        ▼
sf-ui-haptic-switch.service (Type=oneshot, User=steamos)
        │  ExecStart=python3 …/sf_ui_haptic_switch_inject.py
        ▼
  注入器（一次性）：
    curl/HTTP :8080/json/list → 枚举 page target
    for each target: WebSocket → Runtime.evaluate(注入脚本) → 关闭
    （barpopup 注入 ui 脚本；其余注入 gate 脚本）
        │
        ▼
  :8080 页面（同源 https://steamloopback.host）
    gate.js：包 SteamClient.OpenVR.TriggerOverlayHapticEffect；调用时读 localStorage
    ui.js  ：在 barpopup 建开关；点按写 localStorage
```

**关键不变量**：无长驻进程；**状态只在 `localStorage`**。

---

## 5. 运行流程

1. **开机起**：timer 触发 oneshot；此后每「跑完歇 3s」触发。
2. **每拍**：枚举 `:8080` 全部 target；对每个 `evaluate` 注入脚本（幂等，脚本自带 `build` 标记避免重复构建）；`barpopup` 注入 ui，其余注入 gate；退出。
3. **reload**：注入随文档消失；最迟下一拍（≤3s）重新注入。
4. **点按开关**：ui 写 `localStorage`；gate 在**调用时**读 localStorage → 立即生效；**无进程参与**。

---

## 6. 命名映射

| 位置 | 名称 |
| --- | --- |
| gate 脚本（所有 target） | `sf_ui_haptic_switch_gate.js` |
| ui 脚本（仅 barpopup） | `sf_ui_haptic_switch_ui.js` |
| 注入器 | `sf_ui_haptic_switch_inject.py` |
| systemd | `sf-ui-haptic-switch.timer` + `sf-ui-haptic-switch.service` |
| 设备安装目录 | `/home/steamos/sf-ui-haptic-switch` |
| 日志前缀 | `[sf-ui-haptic-switch]` |
| localStorage 键 | `sf_ui_haptic_switch`（`"1"`/`"0"`） |
| JS 全局（build 标记等） | `window.__SF_UI_HAPTIC_SWITCH__` |
| 行 id / CSS id | `sf_ui_haptic_switch_row` / `sf_ui_haptic_switch_look` |
| 模板缓存（sessionStorage） | `sf_ui_haptic_switch_tpl_v1` / `sf_ui_haptic_switch_body_v1` |
| 发行包名 | `sf-ui-haptic-switch-<版本>` |

---

## 7. 变更清单

### 7.1 注入器：`sf_haptic_switch_daemon.py` → `sf_ui_haptic_switch_inject.py`
**保留**：`WebSocket`（RFC6455）、CDP 封装、build 哈希、`evaluate`。
**新增**：枚举 `:8080 /json/list` 全部 page target；逐个注入；`barpopup` 判定。
**删除**：`HapticSwitch`/`state.json`、循环与 `POLL_INTERVAL`、`push`、`Runtime.addBinding`/`BINDING_NAME`、`handle_commands`/`execute`、信号处理。
**改**：`main()` 单次执行；日志前缀 `[sf-ui-haptic-switch]`；只针对 `:8080`。

### 7.2 前端：`sf_haptic_switch.js` → 两个脚本
- **`sf_ui_haptic_switch_gate.js`**：包 `window.SteamClient.OpenVR.TriggerOverlayHapticEffect`（缺失即 no-op）；调用时读 `localStorage['sf_ui_haptic_switch']`；幂等 + restore。
- **`sf_ui_haptic_switch_ui.js`**：只在 `barpopup` 建开关（克隆原生行、`MutationObserver` 自愈、`sessionStorage` 模板缓存）；点按写 `localStorage`；初始化从 `localStorage` 读态。
- 删 `sf_haptic_switch.js`；全局名 → `__SF_UI_HAPTIC_SWITCH__`；去掉 binding/pending。

### 7.3 systemd 单元
- 删 `sf-haptic-switchd.service`；
- 新增 `sf-ui-haptic-switch.service`（`Type=oneshot`、`User=steamos`）+ `sf-ui-haptic-switch.timer`（`OnBootSec` + `OnUnitInactiveSec=3s`）。

### 7.4 脚本
- `install.sh`：装两个 JS + 注入器 + 单元；`enable --now sf-ui-haptic-switch.timer`；验证看日志/一次运行。
- `uninstall.sh`：停/禁 timer + service；尽力 `teardown`（`:8080` 全部 target）；删单元与安装目录。
- `diagnose.sh`：当前不存在，本计划不重建。

### 7.5 文档 / 其它
- `README.md`、`src/技术说明.md`：按新架构重写（`:8080`、timer、localStorage、双脚本）。
- `docs/research/haptic-switch-implementation.md`：标注「已过时」。
- `.gitignore`：移除 `state.json` 条目。

---

## 8. 迁移与回滚

- **迁移**：无 `state.json`；默认 `enabled = true`（无 localStorage 值即开）。
- **回滚**：恢复旧 `sf-haptic-switchd.service` 与旧脚本；前端一并回退。

---

## 9. 待实机验证（T0 已部分覆盖）

1. **`:8080` localStorage 是否跨 target 共享**（T0 佐证同源，但未做写入回环实测）。
2. **`:8080` localStorage 是否跨 Steam 客户端重启保留**（steamwebhelper profile 持久性）。
3. `:8080` 的 `SteamClient.OpenVR.TriggerOverlayHapticEffect` 是否确为 hover 震动的**实际入口**（T0 只证其存在）。
4. `barpopup` 与 `vrOverlayKey` 数字后缀的跨重启稳定性。

---

## 10. 风险

- **时机**：reload 后开关/包装最多缺失一个 tick（≤3s）。
- **行为面**：注入所有 `:8080` target，gate 必须**严格 no-op**。
- **持久性**：若 `:8080` profile 不持久，状态会在客户端重启后回到默认（可接受，非阻断）。

---

## 11. 后续（超范围）

- **T6：启用 `:8087`**（让 `vrwebhelper` 带 `--remote-debugging-port`）并覆盖 SteamVR Dashboard 的 `VRHTML.VROverlay.TriggerOverlayHapticEffect`。
- 拦截 `SteamClient.Input.TriggerSimpleHapticEvent / TriggerHapticPulse / ForceSimpleHapticEvent`。
- `diagnose.sh` 重建；`VERSION` 与发行打包。

---

## 12. 执行顺序（确认后）

1. 注入器（§7.1）。
2. 前端拆分（§7.2）。
3. 单元与脚本（§7.3/§7.4）。
4. 文档与 `.gitignore`（§7.5）。
5. 语法自检：`esprima`（JS）、`py_compile`（py）、`bash -n`（脚本）。
6. 实机端到端（T5）。
