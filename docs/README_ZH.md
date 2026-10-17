[English](../README.md) | **简体中文** | [日本語](README_JP.md)

# Controller UI Haptic — 为 Steam Frame 增加一个开关

> [!NOTE]
> 连接 SteamVR 后出现的 **Steam** 页面仍有手柄震动反馈，该情景超出本插件控制范围（需修改宿主机），恕无法修复。

在 Steam Frame 上，手柄的虚拟光标每次落到 UI 按钮上都会触发一次短促的触觉反馈。在头显菜单里这种反馈持续不断，而系统没有提供关闭它的方式。

本插件补上了这个开关：它出现在 **快速访问菜单 → 设置** 的底部，名称为 **Controller UI Haptic**。

- **开**（默认）：保持原生行为——光标悬停在 UI 按钮上时触发反馈。
- **关**：光标悬停在 UI 按钮上时不再触发反馈——Steam 客户端 UI 和 SteamVR Dashboard 中都一样。

拖动滑杆、吸附以及其他所有反馈都保持原样；只有按钮的进入/离开效果被拦截。

## 使用

```bash
# 在 Steam Frame 上运行
git clone "https://github.com/<owner>/steam-frame-ui-haptic-switch.git"
cd steam-frame-ui-haptic-switch
sudo ./install.sh
sudo ./uninstall.sh
```

运行 `install.sh` 后，重启一次 SteamVR，让 Dashboard 暴露调试端点。daemon 会自动重试，无需重新安装。

## 工作原理

Steam 客户端 UI 与 SteamVR Dashboard 都是 Chromium Embedded Framework (CEF) 页面，各自带一个本地 DevTools 端点：

| 端点 | 所属进程 | 触觉桥接接口 |
| --- | --- | --- |
| `127.0.0.1:8080` | Steam 客户端（`steamwebhelper`） | `SteamClient.OpenVR.TriggerOverlayHapticEffect(effect, …)` |
| `127.0.0.1:8087` | SteamVR Dashboard（`vrwebhelper`） | `VRHTML.VROverlay.TriggerOverlayHapticEffect(handle, effect)` |

常驻 daemon（`sf_ui_haptic_switch_daemon.py`）为每个端点维持一条浏览器级 CDP 连接。它通过 `Target.setDiscoverTargets` 和 `Target.attachToTarget {flatten:true}` 在页面出现时逐个附加——不做轮询。

它在每个页面中注入 `sf_ui_haptic_switch_gate.js`。gate 会包装该页面上的触觉桥接接口，并在开关关闭时只丢弃：

```text
1 ButtonEnter   光标进入按钮 / 可点击行
2 ButtonLeave   光标离开按钮 / 可点击行
```

`Snap(3)`、`Sliding(4)`、`SlidingEdge(5)` 刻意保持原样。

开关本身（`sf_ui_haptic_switch_ui.js`）只注入快速访问菜单页面。切换它会：

1. 写入 `localStorage['sf_ui_haptic_switch']`（`'1'`/`'0'`）——这是唯一事实来源，同页的 gate 会立即响应；
2. 调用 `__sfUiHapticSwitchSend` CDP binding 通知 daemon。

daemon 会从 `:8080` 读回该值，持久化到 `~/.local/state/sf-ui-haptic-switch/state.json`，并推送到每个 `:8087` 页面。它从不回写 `:8080`，因此开关不会来回弹跳。没有周期性轮询：daemon 还会在启动时、重连时，以及页面获得新的执行上下文时重新同步。

## 文件

| 路径 | 用途 |
| --- | --- |
| `install.sh` / `uninstall.sh` | 在 checkout 中运行；安装 unit，不复制仓库 |
| `src/sf_ui_haptic_switch_gate.js` | 包装两个触觉桥接接口；拦截两种按钮效果 |
| `src/sf_ui_haptic_switch_ui.js` | 渲染开关并通知 daemon |
| `src/sf_ui_haptic_switch_daemon.py` | 常驻 daemon 与 CDP 传输（Python 3，仅标准库） |
| `src/sf_ui_haptic_switch_settings.py` | 供 install/uninstall 使用的安全 `steamvr.vrsettings` 编辑器 |
| `installation_lib.sh` | install/uninstall 共享常量与输出辅助函数 |
| `src/sf-ui-haptic-switch.service` | systemd daemon unit |

安装后的布局：

```text
<this checkout>/                                      源码与脚本（就地安装）
/etc/systemd/system/sf-ui-haptic-switch.service       daemon unit，指向 checkout
/home/steamos/.local/state/sf-ui-haptic-switch/       持久化的开关值
~/.config/openvr/config/steamvr.vrsettings            VRWebHelper.DebuggerEnabled
```

service 直接从 checkout 运行 `src/sf_ui_haptic_switch_daemon.py`。移动或删除 checkout 后需要重新安装。

`install.sh` 就地运行：把 `HERE` 设为当前 checkout，source `installation_lib.sh`（其中 `DEST="$HERE"`），并把 unit 中的 `INSTALLATION_PATH` 替换为 checkout 路径。它不复制任何仓库文件。`uninstall.sh` 会删除 unit、状态和 `VRWebHelper.DebuggerEnabled`，但保留 checkout。

## 限制

- `:8087` 必须通过 `steamvr.vrsettings` 中的 `VRWebHelper.DebuggerEnabled = true` 启用（install 会做），并需要重启 SteamVR。
- 开关值保存在 Steam 客户端配置 / 状态文件中。重置该配置会把开关恢复为**开**。
- 只拦截两种按钮悬停效果。滑杆与吸附触觉刻意保持原样。
- 游戏和 OpenXR 应用产生的触觉完全不在范围内。

## 日志与状态

```bash
journalctl -u sf-ui-haptic-switch.service -f
systemctl status sf-ui-haptic-switch.service
python3 src/sf_ui_haptic_switch_daemon.py --status
```

`--status` 会打印持久化的值，以及两个端点的可达性和页面数量。给 daemon 加 `--verbose` 可查看 attach/inject/push 细节。
