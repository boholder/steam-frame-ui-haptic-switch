# T4 — 文档与 `.gitignore`

- **状态**：todo
- **阻塞于**：无
- **阻塞**：无

## 范围

- `README.md`、`src/技术说明.md`：按新架构重写（仅 `:8080`、timer + oneshot、localStorage、双脚本 gate/ui）。
- `docs/research/haptic-switch-implementation.md`：标注「已过时（旧单页守护进程 + `VRHTML` 假设）」。
- `.gitignore`：移除 `state.json`（不再落盘）。
- `sf.ps1`：不变。

## 验收标准

- 文档与 T1/T2/T3 最终实现一致（无遗留 `sf_haptic_switch_daemon.py`、`sf-haptic-switchd.service`、`:8087`/`VRHTML` 描述）。
