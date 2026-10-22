# `target_sessions` 的值类型：`session_id` vs `Session` 的风险评估

> 调查方式：只读代码/规范检索；在**临时副本**中应用候选改动并运行单测，未改动本仓库的源码与测试。
> 代码基线：`master` / `e663cd3`。
> 外部规范：Python 3.15 文档；CDP `tot/Target`；`ChromeDevTools/devtools-protocol` `7245ec5`。
>
> 后续（已实施）：`target_sessions` 已删除，target 反查改为遍历 `sessions`（§5 方案 C1 的变体，按 `Endpoint` 实例匹配）。本文其余部分（含 §4、§5 的收益与推荐、§6 的护栏）均为决策前快照。

## 结论

**不建议为了省掉两次 O(1) 字典查找而把 `target_sessions` 的值改成 `Session`。**
改动本身可行，但它没有消除“双索引必须同步”的复杂度，只是把“id 外键”换成了“同一个对象被两个 dict 强引用”的隐式别名不变量；这个不变量目前既没有类型检查，也没有测试保护。现有测试中至少 2 个会直接失败（见 §3.1），另有一个错误类型的写入会静默通过。

如果动机只是消除 `_dispatch_cdp_event` / `_on_target_info_changed` 里重复的两行查找，保留现有 `str` 值并抽一个 `_session_for_target()` 辅助方法即可，语义和内存模型都不变（见 §5）。

## 1. 仓库事实

### 1.1 现状结构

- `Session` 的标识字段在构造时写入：`endpoint`、`session_id`、`target_id` 分别在 `src/sf_ui_haptic_switch_daemon.py:651-653`。
- `Session` 的其余可变字段：`title`/`url`（`:654-655`）、`script_names`（`:657`）、`is_ui`（`:659`）、`handshake`（`:660`）。
- `Session` 没有定义 `__eq__` / `__hash__`（类体 `src/sf_ui_haptic_switch_daemon.py:636-716`），因此默认按对象身份比较/哈希。
- `Daemon.sessions: dict[str, Session]`：`src/sf_ui_haptic_switch_daemon.py:758-761`。
- `Daemon.target_sessions: dict[tuple[int, str], str]`：`src/sf_ui_haptic_switch_daemon.py:762-764`。
- 类 docstring 明确把两者描述为两个注册表：`session_id -> Session` 与 `(port, targetId) -> session_id`（`src/sf_ui_haptic_switch_daemon.py:722-725`）。

### 1.2 所有读写点

| 位置 | 操作 | 说明 |
| --- | --- | --- |
| `src/sf_ui_haptic_switch_daemon.py:764` | 初始化 | `target_sessions` 为空 dict |
| `src/sf_ui_haptic_switch_daemon.py:845-846` | 读 | `targetDestroyed`：先取 `sid`，再经 `sessions` 取对象 |
| `src/sf_ui_haptic_switch_daemon.py:880` | 读（键） | attach 前检查 target 是否已注册 |
| `src/sf_ui_haptic_switch_daemon.py:906-907` | 写 | 先写 `sessions[session_id]`，再写 `target_sessions[key] = session_id` |
| `src/sf_ui_haptic_switch_daemon.py:947-948` | 读 | `targetInfoChanged`：同样的双重查找 |
| `src/sf_ui_haptic_switch_daemon.py:975-976` | 删 | `_drop()` 按 `session.session_id` 和 `(session.endpoint.port, session.target_id)` 各删一个键 |
| `src/sf_ui_haptic_switch_daemon.py:775-776` | 间接删 | 端点重连前只遍历 `sessions`，逐个 `_drop` |
| `src/sf_ui_haptic_switch_daemon.py:926-928` | 间接删 | attach 失败后经 `sessions` 找对象再 `_drop` |

`target_sessions` 的**值**目前在源码中从未被读取或比较；唯一需要对象的地方都是先读值再查 `sessions`。`target_sessions` 实际承担的是“target 是否存在/对应哪个 session”的索引。

`_on_target_info_changed` 只修改 `title`、`url`、`is_ui`、`script_names`（`src/sf_ui_haptic_switch_daemon.py:956-963`），**不修改** `endpoint` 或 `target_id`。全仓检索也未发现对这两个字段的再赋值。

### 1.3 对象图与引用

- `Daemon` 持有两个 dict；`sessions` 和 `target_sessions` 各自持有引用。
- `Session.endpoint` 指回 `Endpoint`（`src/sf_ui_haptic_switch_daemon.py:651`）。
- `Endpoint` 的字段（`src/sf_ui_haptic_switch_daemon.py:222-242`）不持有 `Daemon` 或 `Session`，因此当前对象图里 **没有** `dict -> Session -> ... -> dict` 的环。
- 把同一个 `Session` 同时放进两个 dict，不会复制对象，只会多一条强引用；也不会凭空制造引用环。

### 1.4 测试对内部结构的直接依赖

以下测试直接写 `target_sessions`，绕过 `_attach_to_page_target`：

- `tests/test_daemon.py:446`：`daemon.target_sessions[(endpoint.port, "tid")] = "sid"`，与 `tests/test_daemon.py:445` 的 `sessions["sid"]` 手工配对。
- `tests/test_daemon.py:582`：`daemon.target_sessions[(page.endpoint.port, page.target_id)] = page.session_id`。
- `tests/test_daemon.py:648`：同样写入 `session.session_id`。

测试只断言空表：`tests/test_daemon.py:381-382`、`:450-451`、`:658-659`、`:668-669`。没有测试断言非空 `target_sessions` 的内容或值类型。

大量测试直接写 `daemon.sessions[...]` 而不登记 `target_sessions`（`tests/test_daemon.py:513`、`:523`、`:555`、`:566`、`:581`、`:618`、`:628`、`:704`、`:717`、`:728`），说明两个索引在测试里并没有统一的注册入口。

### 1.5 临时副本实验

在只复制 `src/` + `tests/` 的临时副本中，按候选方案改 4 处（类型注解、两处双重查找、一处 attach 写入）后运行全量单测：

- `HandshakeTriggerTests.test_navigating_into_the_qam_starts_the_handshake` 失败。`tests/test_daemon.py:582` 写入的是 `str`，新代码在 `src/sf_ui_haptic_switch_daemon.py:956-957` 对它执行 `session.title = ...`，抛 `AttributeError`。
- `DropSessionTests.test_target_destroyed_drops_both_indexes` 失败。`tests/test_daemon.py:648` 写入的是 `str`，新代码在 `src/sf_ui_haptic_switch_daemon.py:975` 访问 `session.session_id`，抛 `AttributeError`。
- `EndpointBackoffTests.test_dropping_a_dead_endpoint_also_drops_its_sessions` **仍然通过**：`tests/test_daemon.py:446` 的错误类型值没有阻碍 `_drop()`，因为 `_drop()` 只按 session 对象推导键，不读取 `target_sessions` 的值（`src/sf_ui_haptic_switch_daemon.py:975-976`）。

也就是说，类型迁移后有一个测试会静默保留错误类型，另有测试要改。该项目没有 mypy/pyright 检查（`pyproject.toml:1-37`、`scripts/check.sh:1-40` 只有 unittest + ruff + shellcheck），静态检查不会兜底。

## 2. 外部规范事实

### 2.1 CPython 对象身份、引用与 GC

> “Every object has an identity, a type and a value. An object's identity never changes once it has been created; you may think of it as the object's address in memory.”
>
> “Some objects contain references to other objects; these are called containers. ... The references are part of a container's value.”
>
> “(`e = f = []` assigns the same object to both `e` and `f`.)”
>
> — <https://docs.python.org/3/reference/datamodel.html#objects-values-and-types>

由此：

- 两个 dict 的 value 指向同一个 `Session` 时，只有一个对象、两条引用；`sessions[sid] is target_sessions[key]` 会为真。
- 用 `==` 比较不会按内容合并；默认语义是身份比较：“By default, `object` implements `__eq__()` by using `is`, returning `NotImplemented` in the case of a false comparison: `True if x is y else NotImplemented`.” — <https://docs.python.org/3/reference/datamodel.html#object.__eq__>
- 因此两个 `Session` 实例即使 `session_id`/`target_id` 相同，也是不同且不相等的对象；这使“索引里存的是不是当前那个对象”成为一个必须显式保证的不变量。

CPython 的回收方式：

> “CPython currently uses a reference-counting scheme with (optional) delayed detection of cyclically linked garbage, which collects most objects as soon as they become unreachable, but is not guaranteed to collect garbage containing circular references.”
>
> — <https://docs.python.org/3/reference/datamodel.html#objects-values-and-types>

> “Since the collector supplements the reference counting already used in Python, you can disable the collector if you are sure your program does not create reference cycles.”
>
> — <https://docs.python.org/3/library/gc.html>

由文档可推导：只要 `sessions` 或 `target_sessions` 中任一 dict 仍可达，`Session` 就仍可达，GC 不会回收；从两个 dict 中只删一个，另一个仍会延长对象寿命。两个 dict 互不引用，当前也不构成环，所以这里的问题不是 cycle collector，而是强引用别名。

### 2.2 CDP 的 `TargetID` / `SessionID` / `flatten`

当前规范（commit `7245ec551bc9d450170193456ab3965bb7e716cb`）：

- `SessionID` 的定义只有一句：“Unique identifier of attached debugging session.” — `json/browser_protocol.json:28826-28828`，<https://github.com/ChromeDevTools/devtools-protocol/blob/7245ec551bc9d450170193456ab3965bb7e716cb/json/browser_protocol.json#L28826-L28828>
- `TargetID` **没有任何 description**，只是 `string` — `json/browser_protocol.json:28822-28824`，同上链接 `#L28822-L28824`。
- `Target.attachToTarget` 的 `flatten`：“Enables "flat" access to the session via specifying sessionId attribute in the commands. We plan to make this the default, deprecate non-flattened mode, and eventually retire it.” — `json/browser_protocol.json:28987-28989`（<https://chromedevtools.github.io/devtools-protocol/tot/Target/#method-attachToTarget>）。
- `attachToTarget` 返回的 `sessionId` 仅描述为“Id assigned to the session.” — `json/browser_protocol.json:28995-28997`。
- `detachedFromTarget`：“Can be issued multiple times per target if multiple sessions have been attached to it.” — `json/browser_protocol.json:29464-29465`（<https://chromedevtools.github.io/devtools-protocol/tot/Target/#event-detachedFromTarget>）。因此 `SessionID` 是**一次 attach 关系**的标识，不是 target 的永久别名。
- `targetInfoChanged`：“This only happens between `targetCreated` and `targetDestroyed`.” — `json/browser_protocol.json:29545-29546`（<https://chromedevtools.github.io/devtools-protocol/tot/Target/#event-targetInfoChanged>）。
- `targetDestroyed` 只携带 `targetId` — `json/browser_protocol.json:29515-29520`。

规范**没有**承诺：

- `TargetID` 在 `targetDestroyed` 之后不会被复用；
- `SessionID` 在 `detachedFromTarget` 之后不会被复用；
- 两个 id 的“唯一”是跨连接、跨进程还是仅在某个 browser 连接内。

规范只明确 `SessionID` 是“unique identifier”，且 `targetInfoChanged` 的生命周期被限制在 created/destroyed 之间。仓库用 `(port, target_id)` 作为键、用 `session_id` 作为键，实际上都依赖了“在需要区分的时间窗内 id 唯一且不复用”这一假设；改动不会消除这个假设。

## 3. 风险清单（按 可能性 × 影响 排序）

### R1. 测试/内部契约破坏：高可能性，中影响

仓库测试直接把 `target_sessions` 当 `dict[..., str]` 用（`tests/test_daemon.py:446`、`:582`、`:648`）。只改源码会得到 §1.5 的 2 个失败；`:446` 那条还会以错误类型静默通过，掩盖迁移不完整。

这是最确定的风险，但修复成本低（改测试为写入 `Session` 对象）。它同时说明：`target_sessions` 的值类型是事实上的内部契约，不只是私有实现细节。

### R2. 两个索引持有同一个对象，但“同一对象”不变量未强制：低-中可能性，中-高影响

改成 `target_sessions[key] = session` 后，正确性依赖：

```python
self.sessions[session.session_id] is self.target_sessions[key]
```

当前没有任何地方检查或封装这个不变量。若未来某条路径替换了 `sessions[sid]` 却没有更新 `target_sessions`，就会出现：

- `targetDestroyed` 从 `target_sessions` 取到**旧对象**；
- `_drop(old)` 执行 `sessions.pop(old.session_id)`（`src/sf_ui_haptic_switch_daemon.py:975`）；
- 若新对象复用了同一个 `session_id`，被删掉的是**新对象**；目标键也被删掉，但新的活 session 已从 `sessions` 消失。

现状的 id 间接层不会返回“孤儿对象”：它总是经 `sessions` 解析成当前注册的对象，或者返回 `None`。所以这个失败模式是改成对象值后**新增**的。当前代码没有 `sessions[sid]` 的替换路径（attach 受 `:880` 的键存在检查保护），因此可能性低；但一旦引入重连时复用 session、或测试/手工替换，代价很高。

### R3. 单边删除会保留整个 `Session`，并可能永久阻止重新 attach：低-中可能性，低-中影响

`_drop()` 同时删两个索引（`src/sf_ui_haptic_switch_daemon.py:973-976`），正常情况下不会单边残留。但：

- 重连清理只遍历 `sessions`（`src/sf_ui_haptic_switch_daemon.py:775-776`），不会发现“只在 `target_sessions` 里”的条目；
- `_attach_to_page_target` 只看键是否存在（`src/sf_ui_haptic_switch_daemon.py:880`），残留键会阻止同一 target 重新 attach；
- 源码中没有 `sessions.clear()` / `target_sessions.clear()`；但测试大量手工写 `sessions`，未来维护代码也可能单边操作。

现在若只残留 `target_sessions`，留下的是一个短字符串；改成对象值后，留下的是整个 `Session`（以及它引用的 `Endpoint`、`handshake`、`title`/`url`、`script_names`）。它仍然可达，GC 不会回收。内存量不大，但“残留键阻塞重连 + 多保留一个对象”比现状更难察觉。

反过来，这也是新方案唯一的一个小优势：若 `sessions` 先被误删、`target_sessions` 还保留对象，`targetDestroyed` 现在仍能拿到对象并调用 `_drop`，从而清掉残留的目标键；现状拿到 `sid` 后查 `sessions` 得到 `None`，不会清理目标键。

### R4. `_drop()` 从可变字段推导键：低可能性，中影响（既有隐患，被对象值放大）

`_drop()` 用 `session.endpoint.port` 和 `session.target_id` 推导要删的键（`src/sf_ui_haptic_switch_daemon.py:976`）。当前这两个字段只在构造时赋值（`:651-653`），`_on_target_info_changed` 只改展示字段（`:956-963`），所以现在安全。

如果以后允许原地修改 `target_id`/`endpoint`，`_drop()` 会删新键、留下旧键。这个缺陷在现状下已经存在；改成对象值后，旧键的 value 会继续持有整个对象，使“改了标识字段”从字符串残留升级为对象残留，并且按旧键查找仍会返回该对象。Session 没有 `__slots__`、只读属性或 frozen 保护，无法从类型层面阻止这种修改。

### R5. 调试、日志与序列化：低可能性，低影响

- `target_sessions` 目前不参与日志、状态输出或持久化（全仓仅 `src/` 与 `tests/` 使用；`status()` 走 `/json/list`，见 `src/sf_ui_haptic_switch_daemon.py:1121` 起）。
- 但改成对象值后，`repr(target_sessions)` 会变成 `<...Session object at 0x...>`，不再能直接看出 `(port, target)` 对应哪个 `session_id`；排障时需要额外打印 `session.session_id`。
- 测试不能再把非空 `target_sessions` 与 id 字面量直接比较；当前测试只比较 `{}`，所以暂时不受影响。
- 若未来有人把 `target_sessions` 直接 `json.dumps` 或写日志，会从“可序列化”变成“不可序列化”。

### R6. CDP id 复用假设：极低可能性，中影响（两种方案共有）

规范只承诺 `SessionID` 唯一，没有承诺 detach/destroy 后不复用（§2.2）。两种方案都依赖 id 在相关窗口内不复用：

- 现状：旧目标键 + 复用后的 `session_id` 可能解析到新 session，导致 `_drop` 删除错误的活 session；
- 新方案：旧目标键直接持有旧对象，`_drop` 仍按旧 `session_id` 删除，同样可能命中复用后的新 session，并且 attach 守卫会先挡住新 target。

这不是改动的差异化理由，但值得在注释里明确假设，避免把“唯一”误读为“永不复用”。

## 4. 改动是否真的降低了复杂度？

收益：`targetDestroyed` 和 `targetInfoChanged` 各少一次 dict 查找、少一个 `sid` 局部变量；最坏情况节省两次哈希查找。相对于 CDP 网络 I/O 和事件循环，这是可忽略的微优化（工程判断，非规范事实）。

成本：

- 仍然要维护两个 dict：attach 写两次（`:906-907`），`_drop` 删两次（`:975-976`），重连遍历仍只看 `sessions`（`:775-776`）。
- 新增一个未强制的别名不变量（R2）。
- 单边残留的代价从“字符串”变成“整个对象”（R3）。
- 测试和排障的可读性下降（R1、R5）。

结论：复杂度没有消失，只是从“id 外键 + 双重查找”转移成“两个 dict 持有同一对象 + 身份一致性”。对于只有两个查找点的场景，净收益为负。

## 5. 反向方案

### 方案 A（推荐）：保留 `str` 值，抽一个查询辅助方法

把重复的双重查找收进一个地方，保持单一事实来源（`sessions` 拥有对象，`target_sessions` 只是外键索引）：

```python
def _session_for_target(self, endpoint: Endpoint, target_id: str) -> Session | None:
    sid = self.target_sessions.get((endpoint.port, target_id))
    return self.sessions.get(sid) if sid else None
```

`targetDestroyed`（`:845-846`）与 `targetInfoChanged`（`:947-948`）都调用它。这直接解决“重复两行”的动机，不改变任何生命周期、测试或调试语义。

### 方案 B：`weakref.WeakValueDictionary` 作为二级索引

`WeakValueDictionary` 的值不会单独延长对象寿命：“Entries in the dictionary will be discarded when no strong reference to the value exists any more.” — <https://docs.python.org/3/library/weakref.html#weakref.WeakValueDictionary>

`Session` 是普通类实例，支持弱引用（“Objects which support weak references include class instances...”，<https://docs.python.org/3/library/weakref.html>）。若 `sessions` 是唯一强引用，单边删除 `sessions` 后目标条目会自动消失，可缓解 R3。

但不推荐：它把索引清理交给 GC 时机，查找可能静默返回 `None`；`WeakValueDictionary` 的迭代/回调语义和普通 dict 不同；为了两个查找点引入这种隐式生命周期不值得。而且只要 `sessions` 还在，目标条目就不会消失，R2 依旧存在。

### 方案 C：只保留一个注册表

- 变体 C1：删掉 `target_sessions`，按 `(port, target_id)` 线性扫描 `sessions.values()`。当前页面数量很少（每个端点通常个位数），`targetInfoChanged`/`targetDestroyed` 也不是热路径；这能真正消除双索引同步问题，但把 O(1) 变成 O(n)，且 `_attach_to_page_target` 的“已 attach”检查、`_drop` 都要改。
- 变体 C2：以 `(port, target_id)` 为主键，另建 `session_id -> target_key` 的二级 map。仍然是两个 map，只是换了主次，不解决同步问题。

若目标是根除“双索引不一致”这一类 bug，C1 才是结构性方案；若目标只是少写两行，方案 A 足够。当前没有证据表明双索引不一致已经造成线上问题，因此不建议为此做大改。

## 6. 如果仍然要改成 `Session` 值：最低成本护栏

1. 更新 `tests/test_daemon.py:446`、`:582`、`:648` 为写入对应的 `Session` 对象；补一个非空断言，明确 `target_sessions[key] is sessions[sid]`。
2. 增加 `_register(session)` / 保持 `_drop(session)` 为唯一写/删入口，禁止在别处单独改一个 dict。attach 的两次写入应相邻且在同一函数内（现在已是 `:906-907`）。
3. 让 `_drop()` 做身份校验，避免旧对象删掉同 id 的新对象：

   ```python
   if self.sessions.get(session.session_id) is session:
       self.sessions.pop(session.session_id, None)
   self.target_sessions.pop((session.endpoint.port, session.target_id), None)
   ```

4. 把 `endpoint`、`session_id`、`target_id` 在注册后视为只读（property / `__slots__` / 文档约束），避免 R4。
5. 在注释和测试里写清“值必须是 `sessions` 中的同一对象”，并说明 CDP id 不复用只是假设，不是规范保证（§2.2）。

## 7. 未验证 / 不确定

- **CDP id 是否真的不复用**：规范未承诺；本次没有审查 Chromium 的 id 生成实现，因此“实际不会复用”未经证实。
- **仓库外是否有消费者**：全仓检索只发现 `src/`、`tests/`（以及 `docs/plans/tickets/T8-cleanup-fixes.md` 的文字提及）使用 `target_sessions`；无法排除仓库外的脚本/调试工具直接读写该属性。
- **CPython 引用计数 +2 的精确机制**：文档确认 CPython 使用引用计数、容器持有引用；具体增量属于实现细节，本文只按“两个 dict 各持一条强引用”推导，不依赖精确计数。
- **残留对象的内存影响**：未测量 `Session`/`Endpoint` 的实际大小与残留条目数量；判断为“小但可能长期存在”，不是精确量化结论。
- **`TargetID` 的完整生命周期语义**：规范只通过 `targetInfoChanged` 的 created/destroyed 边界间接约束；没有一句直接声明“target 存活期间 id 稳定”。
