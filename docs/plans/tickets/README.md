# Tickets — `sf-ui-haptic-switch`（仅 `:8080` + localStorage）

按「可独立验收的能力」切分。每张一个文件。阻塞边如下。

```
T0 设备事实探测 (done) ─┬─> T1 注入器(oneshot) ─┬─> T3 单元与脚本 ─┐
                        │                        │                  ├─> T5 实机端到端验收
                        └─> T2 前端拆分 ─────────┴──────────────────┘
T4 文档与 .gitignore（无阻塞）
T6 启用 :8087（延后，本计划不做）
```

| Ticket | 标题 | 阻塞于 | 阻塞 |
| --- | --- | --- | --- |
| T0 | 设备事实探测 | — | **完成** |
| T1 | 注入器（timer + oneshot） | T0(done) | T3, T5 |
| T2 | 前端拆分（gate + ui） | T0(done) | T5 |
| T3 | 单元与脚本 | T1 | T5 |
| T4 | 文档与 `.gitignore` | 无 | 无 |
| T5 | 实机端到端验收 | T1, T2, T3 | 无 |
| T6 | 启用 `:8087`（SteamVR Dashboard） | 无（调研先行） | 无（超范围） |

- **重命名/前缀统一**折进各组件 ticket。
- 设计见 `../sf-ui-haptic-switch-redesign.md`；T0 结果见 `../../research/t0-device-probe.md`。
- Issue tracker（GitHub）尚未配置 remote；本清单为过渡载体。
