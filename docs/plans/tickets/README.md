# Tickets — `sf-ui-haptic-switch`（`:8080` + `:8087`）

按「可独立验收的能力」切分。每张一个文件。阻塞边如下。

```
T0 设备事实探测 (done) ─┬─> T1 注入器(oneshot) ─┬─> T3 单元与脚本 ─┐
                        │                        │                  ├─> T5 实机端到端验收
                        └─> T2 前端拆分 ─────────┴──────────────────┘
T4 文档与 .gitignore（无阻塞）
T6 启用 :8087（调研结论，已由 T7 落地）
T7 常驻 daemon + :8087 gate（基线：T1/T2/T3；替代 T6）
```

| Ticket | 标题 | 阻塞于 | 阻塞 |
| --- | --- | --- | --- |
| T0 | 设备事实探测 | — | **完成** |
| T1 | 注入器（timer + oneshot） | T0(done) | T3, T5 |
| T2 | 前端拆分（gate + ui） | T0(done) | T5 |
| T3 | 单元与脚本 | T1 | T5 |
| T4 | 文档与 `.gitignore` | 无 | 无 |
| T5 | 实机端到端验收 | T1, T2, T3 | 无 |
| T6 | 启用 `:8087`（SteamVR Dashboard） | 无（调研先行） | 已由 T7 落地 |
| T7 | 常驻 daemon + `:8087` gate | T1, T2, T3（现状基线） | 无 |

- **重命名/前缀统一**折进各组件 ticket。
- 当前设计见 `T7-daemon-8087.md`；T0 结果见 `../../research/t0-device-probe.md`。
- T7 已在分支 `t7-daemon-8087` 实现（单测通过；真机验收待执行）。
- GitHub remote 已配置，但本计划的 ticket 仍以本地文件为准（T7 Q7 A）。
