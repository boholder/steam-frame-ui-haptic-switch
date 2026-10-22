# QAM 选项分隔线调研

> 目的：确认 Steam Frame 原生 Quick Access Menu 如何在选项之间画灰色分隔线，并给出把同一条线补到插件行上的最小实现。
> 方式：只读 CDP 探测 `:8080` 的 live CEF 页面；未修改源码，未修改持久化设备状态。QAM 通过 bar 上的电源按钮临时打开，探测结束后已再次点击关闭。
> 设备实际 checkout：`/home/steamos/Documents/steam-frame-ui-haptic-switch`（不是 `/home/steamos/Downloads/...`）。
> 探测日期：2026-10-22；客户端 CEF `Chrome/126.0.6478.183`，语言 `schinese`。

## 结论摘要

- 原生分隔线**不是** `border-top` / `border-bottom`，**也不是** `::before` / `::after`。它是行与行之间的一个显式兄弟元素：

  ```html
  <div class="_1vcky98a3hG0ulFCdee4kd" role="separator"></div>
  ```

- 它的样式来自 `https://steamloopback.host/css/sp.css`：

  ```css
  ._1vcky98a3hG0ulFCdee4kd { height: 1px; background: rgba(255, 255, 255, 0.1); }
  ._1vcky98a3hG0ulFCdee4kd { margin-inline: calc(-1*var(--field-negative-horizontal-margin)); }
  ```

  当前上下文中 `--field-negative-horizontal-margin` 为 `0px`，所以实际计算值为 `height: 1px; margin: 0; width: 266.667px`（等于 section 的内容宽度）。

- 我们的行没有线，**不是 class 缺失，也不是伪元素被覆盖**：我们的 row 与原生 row 使用同一个 `_3LM_ZckJp5yJYrL1OI_WqW`，section 也使用同一个 `_3gY0aBuNR8_NPTpXIYfkby`。真正缺少的是那个 `role="separator"` 兄弟元素。`grabTemplate` 只克隆了 row，`mount` 只把 row 放进新建的 section，从未插入 separator。

- 最小实现：在 `grabTemplate` 里采集原生 separator 的 `outerHTML`；在 `mount` 里创建 section 后、`appendChild(row)` 前插入 separator；在 `render` 里做一次幂等补插；把模板缓存键升版，避免旧缓存没有 `separatorHTML`。

## 1. 现场结构

QAM target 为 `89115599A09CE9838227844A43EECEEE`（`SteamVR - valve.steam.gamepadui.barpopup.35660001`）。DOM 中存在 `#QuickAccess-Menu`，`bodyIds` 依次为：

```json
["quickaccess_content_0","quickaccess_content_4","quickaccess_content_5","quickaccess_content_8","quickaccess_content_6"]
```

我们的行在 `quickaccess_content_4`。该 body 的定位与布局：

- `id="quickaccess_content_4"`，`role="tabpanel"`
- classes：`_1QO7bWVxsVONFdHDAJGCtF tab_Settings _3td3cAnGbbbAOXW8x2pD-j _29WypCpglgRKsR_fMPsoFX Panel Focusable`
- `display: flex; flex-direction: column; gap: normal; padding: 16px 0 60px; overflow: hidden auto`
- 尺寸 `298.667 x 342.667`（视口内可见部分）

body 的直接子节点是若干 section。原生 section 与我们的 section 使用同一个 class，并都带 `margin: 0 0 24px; padding: 0 16px`：

```text
section._3gY0aBuNR8_NPTpXIYfkby
  margin: 0px 0px 24px
  padding: 0px 16px
  content width: 266.667px
```

因此 section 之间的 24px 间距来自 `margin-bottom`，不是 body 的 `gap`。分隔线本身没有 margin，宽度直接等于 section 的内容宽度。

### 原生行、我们的行与分隔线

| 元素 | classes / 标记 | rect（x, y, w, h） | border | `::before` / `::after` |
| --- | --- | --- | --- | --- |
| 原生 section（亮度） | `_3gY0aBuNR8_NPTpXIYfkby` | `0.67, 648.67, 298.67, 126` | 0 | `content: none` |
| 原生 row（亮度） | `_3LM_ZckJp5yJYrL1OI_WqW` | `16.67, 678.67, 266.67, 48` | 0 | `content: none` |
| 原生 row（亮度第二行） | `_3LM_ZckJp5yJYrL1OI_WqW` | `16.67, 726.67, 266.67, 48` | 0 | `content: none` |
| 原生 separator | `_1vcky98a3hG0ulFCdee4kd` + `role="separator"` | `16.67, 1052.67, 266.67, 1` | 0 | `content: none` |
| 我们的 section | `_3gY0aBuNR8_NPTpXIYfkby` + `data-sf-ui-haptic-section` | `0.67, 1162.67, 298.67, 42` | 0 | `content: none` |
| 我们的 row | `_3LM_ZckJp5yJYrL1OI_WqW` + `id="sf_ui_haptic_switch_row"` | `16.67, 1162.67, 266.67, 42` | 0 | `content: none` |

原生 row 的高度会随内容不同（观测到 40 / 42 / 44 / 48px），我们的行是 42px，与 Wi-Fi 行一致。原生 row 自身没有任何边框或伪元素；线只可能来自那个 separator 元素。

我们的 section 外层的结构是：

```html
<div class="_3gY0aBuNR8_NPTpXIYfkby" data-sf-ui-haptic-section="">
  <div class="_3LM_ZckJp5yJYrL1OI_WqW" id="sf_ui_haptic_switch_row">...</div>
</div>
```

即 section 下只有一个 row，没有 separator。

## 2. 分隔线的确切机制

### 2.1 DOM

body 中共有 2 个 `[role="separator"]`，整个 QAM 也只有这 2 个。两者的 `outerHTML` 完全一致：

```html
<div class="_1vcky98a3hG0ulFCdee4kd" role="separator"></div>
```

它们都位于“其他”这个 section 内，作为 row 的兄弟节点：

```text
section._3gY0aBuNR8_NPTpXIYfkby
  ├─ div（section header，“其他”）
  ├─ div._3LM_ZckJp5yJYrL1OI_WqW（第 1 个选项）
  ├─ div._3LM_ZckJp5yJYrL1OI_WqW（第 2 个选项）
  ├─ div._1vcky98a3hG0ulFCdee4kd[role=separator]
  ├─ div._3LM_ZckJp5yJYrL1OI_WqW（第 3 个选项）
  ├─ div._1vcky98a3hG0ulFCdee4kd[role=separator]
  └─ div._3LM_ZckJp5yJYrL1OI_WqW（第 4 个选项）
```

section 子节点的 role 序列为：

```json
["div","div","div","separator","div","separator","div"]
```

### 2.2 CSS

规则来自 `https://steamloopback.host/css/sp.css`，实际枚举到的匹配规则为：

```json
{
  "sheet": "https://steamloopback.host/css/sp.css",
  "media": "",
  "selectorText": "._1vcky98a3hG0ulFCdee4kd",
  "cssText": "._1vcky98a3hG0ulFCdee4kd { height: 1px; background: rgba(255, 255, 255, 0.1); }"
}
```

```json
{
  "sheet": "https://steamloopback.host/css/sp.css",
  "media": "",
  "selectorText": "._1vcky98a3hG0ulFCdee4kd",
  "cssText": "._1vcky98a3hG0ulFCdee4kd { margin-inline: calc(-1*var(--field-negative-horizontal-margin)); }"
}
```

高对比度模式下还有一条覆盖规则：

```json
{
  "sheet": "https://steamloopback.host/css/sp.css",
  "media": "(prefers-contrast: more)",
  "selectorText": "._1vcky98a3hG0ulFCdee4kd",
  "cssText": "._1vcky98a3hG0ulFCdee4kd { background-color: rgb(255, 255, 255); }"
}
```

此外还有一条相邻兄弟规则，让 separator 后面的 row 在 focus / hover 时用 `margin-top:-1px; border-top:1px solid transparent` 覆盖掉这条线，避免焦点框被线切断。它不是分隔线的来源，但说明 separator 的预期位置就是“下一个 row 的正上方”。

### 2.3 计算样式

`getComputedStyle(separator)` 的实测值：

```text
outerHTML: <div class="_1vcky98a3hG0ulFCdee4kd" role="separator"></div>
height: 1px
width: 266.667px
background: rgba(255, 255, 255, 0.1)
margin: 0px
margin-inline: 0px
margin-left: 0px
margin-right: 0px
flex: 0 1 auto
box-sizing: content-box
align-self: auto
--field-negative-horizontal-margin: 0px
```

`rgba(255,255,255,0.1)` 叠在 QAM 的深色背景 `rgb(14, 20, 27)` 上，就是肉眼看到的灰色细线。

### 2.4 首行 / 末行 / 分组行为

分隔线**不是自动加在每一对相邻 row 之间**的：

- “亮度”section：header + 2 个 row，没有任何 separator。
- “音频”section：header + 2 个 row，没有任何 separator。
- “其他”section：header + 4 个 row，separator 只出现在第 2/3 行之间和第 3/4 行之间。
- header 与第一个 row 之间没有 separator。
- 最后一个 row 之后没有 separator。
- 不同 section 之间没有 separator；section 之间只靠 `margin-bottom: 24px` 分隔。

结论：原生 separator 是 Valve 在特定选项组里手工放置的兄弟元素，不是 `row + row`、`:first-child` 或 `:last-child` 自动生成的。我们的行被放在一个全新的 section 中，天然不会继承任何 separator。

## 3. 为什么我们的行没有线

逐项排除：

1. **不是 class 缺失**：我们的 row 与原生 row 同为 `_3LM_ZckJp5yJYrL1OI_WqW`；我们的 section 与原生 section 同为 `_3gY0aBuNR8_NPTpXIYfkby`。
2. **不是 border**：row 和 section 的 `border-top-width` / `border-bottom-width` 计算值都是 `0`。
3. **不是伪元素**：row 和 section 的 `::before` / `::after` 计算值都是 `content: none`。
4. **是缺少兄弟元素**：原生线是 section 内部的 `div[role="separator"]`，不是 row 的一部分；`row.outerHTML` 不包含它。

对应到代码：

- `src/sf_ui_haptic_switch_ui.js:271` 的 `grabTemplate` 只从 section 中取 row（`:279-281`），返回对象只有 `sectionCls`、`toggleRowHTML` 和 toggle 状态（`:285-291`）。它没有采集 separator。
- `src/sf_ui_haptic_switch_ui.js:451` 的 `toggleRow` 只负责构造 row，返回 `{ row, checkbox }`。
- `src/sf_ui_haptic_switch_ui.js:501-509` 的 `mount` 新建一个 div，套上 `tpl.sectionCls`，然后直接把 row 塞进去；没有插入 separator。
- `src/sf_ui_haptic_switch_ui.js:517-526` 的 `render` 只同步 toggle 外观和清理 id，也不会补结构。

因此，即使 clone 出来的 row 与原生像素一致，section 里也只有 row 一个子节点，线不会出现。

## 4. 推荐的最小实现

### 4.1 采集原生 separator

在 `grabTemplate` 里增加一个字段。优先取当前 body 内的 separator；Quick Settings 没渲染时，退到 QAM 内任意一个 separator：

```js
function grabTemplate(body) {
  /* ...现有的 section / row 查找... */

  var sep = body.querySelector('[role="separator"]') ||
            document.querySelector('#QuickAccess-Menu [role="separator"]');

  return {
    sectionCls: section.className || '',
    toggleRowHTML: row.outerHTML,
    separatorHTML: sep ? sep.outerHTML : null,
    /* ...其余字段保持不变... */
  };
}
```

这样不会把 `_1vcky98a3hG0ulFCdee4kd` 这种会随构建变化的 hash class 硬编码进源码；和现有的 `sectionCls` / `toggleRowHTML` 采集策略一致。

### 4.2 构造 separator

```js
function makeSeparator() {
  if (tpl && tpl.separatorHTML) {
    var el = nodeFromHTML(tpl.separatorHTML);
    if (el) return el;
  }
  /* 兜底：使用实测的 computed style；role 也让原生 focus 规则能识别它 */
  var d = document.createElement('div');
  d.setAttribute('role', 'separator');
  d.style.height = '1px';
  d.style.background = 'rgba(255, 255, 255, 0.1)';
  return d;
}
```

当前上下文里原生 separator 的 `margin-inline` 计算为 `0`，所以兜底不写 margin 也能得到相同的 266.667px 宽度（block 元素自动填满 section 内容宽度）。`role="separator"` 必须保留；不过兜底节点没有原生 hash class，因此原生“相邻 row focus 时遮线”的规则不会命中，只影响焦点边框与线的重叠观感，不影响线本身。

### 4.3 在 `mount` 里插入

放在创建 section 之后、`appendChild(row)` 之前：

```js
var host = body;
if (tpl.sectionCls) {
  var sec = document.createElement('div');
  sec.className = tpl.sectionCls;
  sec.setAttribute(SECTION_ATTR, '');
  sec.appendChild(makeSeparator());
  sec.appendChild(row);
  host = sec;
}
body.appendChild(host);
```

如果 `tpl.sectionCls` 为空（理论上不会发生），则需要把 separator 直接 append 到 `body` 再 append row；建议保留这个 fallback，但当前 Quick Settings 永远有 section class。

### 4.4 在 `render` / `sync` 里做幂等补插

React 可能只清掉 separator 而保留我们的 row；此时 `sync()` 看到 row 还在，不会重新 `mount`。加一个只检查前一个兄弟节点的守卫：

```js
function ensureSeparator() {
  var row = document.getElementById(ROW_ID);
  if (!row || !row.parentElement) return;
  var prev = row.previousElementSibling;
  if (prev && prev.getAttribute('role') === 'separator') return;
  var sep = makeSeparator();
  if (sep) row.parentElement.insertBefore(sep, row);
}
```

在 `render()` 开头调用即可。因为它只在“前一个兄弟不是 separator”时插入，所以重复调用不会产生多条线。

### 4.5 缓存兼容

现有模板缓存在 `src/sf_ui_haptic_switch_ui.js:29`：

```js
var CACHE_KEY = 'sf_ui_haptic_switch_tpl_v1';
```

旧缓存里的 `tpl` 没有 `separatorHTML`。最省事的做法是升版：

```js
var CACHE_KEY = 'sf_ui_haptic_switch_tpl_v2';
```

这样首次运行会重新采集一次；否则就要在 `mount` 里判断 `tpl && !tpl.separatorHTML` 时重新 `grabTemplate` 并合并。

### 4.6 放置位置总结

- **`grabTemplate`**：采集 `separatorHTML`（必须）。
- **`mount`**：主插入点；创建 section 后、append row 前插入（必须）。
- **`render`**：调用 `ensureSeparator()` 做幂等修复（推荐）。
- **不要放在 `toggleRow`**：它只构造 row，没有 section 上下文；把 separator 混进它的返回值会破坏现有 `{ row, checkbox }` 契约。
- **不要只放在 `render`**：首次 mount 时 section 还不存在，`render` 无法决定 separator 放在哪个父节点。

### 4.7 几何取舍

我们的 section 有 `margin-bottom: 24px`。把 separator 放在 section 顶部，视觉顺序是：

```text
上一个 section 的最后一行
24px section 间距
1px 分隔线
我们的 row
```

原生 separator 是同一 section 内紧贴前后 row 的（上方 0 间距、下方 0 间距）。如果要求“线紧贴上一个原生 row 的下边缘”，就需要把 separator 插到上一个原生 section 的末尾，而不是我们自己的 section 顶部。但那个 section 由 React 管理，向其追加节点更容易在 re-render 时被清掉，也会超出当前“克隆一个独立 section”的设计。**推荐先采用 section 顶部插入**；如果验收时认为 24px 间距不可接受，再单独评估“追加到上一个 section 末尾 + `ensureSeparator` 补插”的方案。

## 5. `ref/steam-frame-fan-panel` 对照

该参考项目没有实现分隔线：

- `ref/steam-frame-fan-panel/sf-fan-qam/device/sf_fan_qam.js:237-302` 的 `grabTemplates` 采集了 wrapper、section、header、row、slider 等模板，但没有 `[role="separator"]`。
- 同文件 `:566-574` 的 `section()` 只创建 section + header。
- 同文件 `:596-605` 的 `buildPanel` 把 row 直接 append 到 section，没有插入 separator。
- 全仓搜索 `divider` / `separator` / `border-top` / `border-bottom` 没有找到分隔线实现。

因此参考项目不能提供 separator 的现成代码；以本次 live DOM / CSS 为准。

## 6. 证据

### 6.1 target 发现

对 `:8080` 的所有 page target 执行：

```js
(function(){
  var ids = [].slice.call(document.querySelectorAll('[id^="quickaccess_content_"]'))
    .map(function(b){ return b.id; });
  return JSON.stringify({
    title: document.title,
    qam: !!document.getElementById('QuickAccess-Menu'),
    bodyIds: ids,
    row: !!document.getElementById('sf_ui_haptic_switch_row'),
    sections: document.querySelectorAll('[data-sf-ui-haptic-section]').length
  });
})()
```

目标页输出：

```json
{
  "title": "SteamVR - valve.steam.gamepadui.barpopup.35660001 (35660001)",
  "qam": true,
  "bodyIds": ["quickaccess_content_0","quickaccess_content_4","quickaccess_content_5","quickaccess_content_8","quickaccess_content_6"],
  "row": true,
  "sections": 1
}
```

### 6.2 separator 与布局

实际执行的探测表达式（节选，完整版本还输出了每个 section 的子节点）：

```js
(function(){
  var body = document.getElementById('quickaccess_content_4');
  var sep = body && body.querySelector('[role="separator"]');
  var cs = sep && getComputedStyle(sep);
  var sec = sep && sep.parentElement;
  return JSON.stringify({
    separatorCountInBody: body ? body.querySelectorAll('[role="separator"]').length : 0,
    separatorCountInQam: document.querySelectorAll('#QuickAccess-Menu [role="separator"]').length,
    sep: sep ? {
      outerHTML: sep.outerHTML,
      height: cs.height,
      width: cs.width,
      background: cs.background,
      margin: cs.margin,
      marginInline: cs.marginInline,
      boxSizing: cs.boxSizing,
      fieldNegative: cs.getPropertyValue('--field-negative-horizontal-margin')
    } : null,
    section: sec ? {
      classes: String(sec.className),
      padding: getComputedStyle(sec).padding,
      margin: getComputedStyle(sec).margin,
      roles: [].slice.call(sec.children).map(function(c){
        return c.getAttribute('role') || c.tagName.toLowerCase();
      })
    } : null,
    bodyFlex: body ? {
      display: getComputedStyle(body).display,
      direction: getComputedStyle(body).flexDirection,
      gap: getComputedStyle(body).gap
    } : null
  });
})()
```

输出：

```json
{
  "separatorCountInBody": 2,
  "separatorCountInQam": 2,
  "sep": {
    "outerHTML": "<div class=\"_1vcky98a3hG0ulFCdee4kd\" role=\"separator\"></div>",
    "height": "1px",
    "width": "266.667px",
    "background": "rgba(255, 255, 255, 0.1) none repeat scroll 0% 0% / auto padding-box border-box",
    "margin": "0px",
    "marginInline": "0px",
    "boxSizing": "content-box",
    "fieldNegative": "0px"
  },
  "section": {
    "classes": "_3gY0aBuNR8_NPTpXIYfkby",
    "padding": "0px 16px",
    "margin": "0px 0px 24px",
    "roles": ["div","div","div","separator","div","separator","div"]
  },
  "bodyFlex": {
    "display": "flex",
    "direction": "column",
    "gap": "normal"
  }
}
```

### 6.3 CSS 规则枚举

探测时递归遍历 `document.styleSheets`（包括 `CSSMediaRule`），筛选 `selectorText` 含 `._1vcky98a3hG0ulFCdee4kd` 或 `[role="separator"]` 的规则，并输出 `selectorText` 与 `cssText`。命中的分隔线规则：

```text
sheet: https://steamloopback.host/css/sp.css
media: (空)
selectorText: ._1vcky98a3hG0ulFCdee4kd
cssText: ._1vcky98a3hG0ulFCdee4kd { height: 1px; background: rgba(255, 255, 255, 0.1); }
```

```text
sheet: https://steamloopback.host/css/sp.css
media: (空)
selectorText: ._1vcky98a3hG0ulFCdee4kd
cssText: ._1vcky98a3hG0ulFCdee4kd { margin-inline: calc(-1*var(--field-negative-horizontal-margin)); }
```

```text
sheet: https://steamloopback.host/css/sp.css
media: (prefers-contrast: more)
selectorText: ._1vcky98a3hG0ulFCdee4kd
cssText: ._1vcky98a3hG0ulFCdee4kd { background-color: rgb(255, 255, 255); }
```

另外命中一条相邻兄弟 focus 规则，其声明的关键部分是：

```css
._1vcky98a3hG0ulFCdee4kd + ...gpfocus,
._1vcky98a3hG0ulFCdee4kd + ...gpfocuswithin,
._1vcky98a3hG0ulFCdee4kd + ...:hover {
  margin-top: -1px;
  border-top: 1px solid rgba(0, 0, 0, 0);
}
```

它不是线的来源，但说明 separator 在 DOM 中必须紧挨着下一个 row，且下一个 row 的焦点/悬停状态会主动遮住这条线。

### 6.4 清理

- 设备端临时脚本均为 `/tmp/qam_*.py`，每次执行后已 `rm -f`。
- 探测期间用 bar 的电源按钮打开 QAM，结束后再次点击已关闭（`before: open` → `after click: absent`）。
- 未修改设备上的源码或服务状态。

## 7. 不确定性

- **class hash 会变**：`_1vcky98a3hG0ulFCdee4kd` 和 row/section 的 class 一样是构建时生成的 hash。不要硬编码；用 `[role="separator"]` 采集 `outerHTML`。兜底才使用内联样式。
- **缓存失效只校验 toggle class**：现有 `loadTemplates` 只用 `selectorKnown(t.toggleOnExtra)` 判断模板是否过期。如果 Valve 只重命名 separator 而保留 toggle class（概率低），缓存里的 separator 会失去原生样式。更稳的做法是升 `CACHE_KEY` 后，额外对 separator 的 class 也调用一次 `selectorKnown`；本文按最小改动只要求升版。
- **separator 是手工放置的**：本次只在“其他”section 看到 2 条；不同客户端版本、不同设置项、不同语言/账号状态下数量可能变化。`grabTemplate` 的兜底查询要允许“当前 body 没有，但 QAM 其他位置有”。
- **几何不是完全等价于原生**：把 separator 放在我们新 section 的顶部，会让它上方保留 24px 的 section 间距；原生同 section 内的 separator 上下都是 0 间距。若验收要求“线紧贴上一行”，需要改成插入到上一个原生 section 的末尾，但那会触碰 React 管理的节点，稳定性更差，本次未验证。
- **只测了当前构建**：CEF 126、`schinese`、当前设置页内容；没有测试 `prefers-contrast: more`、其他语言、冷启动时 Quick Settings 尚未渲染的场景。
- **未实际打补丁**：按只读要求，本文只给出实现方案，没有修改 `src/sf_ui_haptic_switch_ui.js`，也没有在设备上验证插入后的视觉效果。

## 8. 最终放置调整（用户验收）

用户最终要求分隔线位于开关组件**下方**，而不是 section 顶部。实现相应改为：

- `mount`：先 `sec.appendChild(row)`，再 `sec.appendChild(makeSeparator())`。
- `ensureSeparator`：检查 `row.nextElementSibling`，并在 `row.nextSibling` 之前插入。
- 视觉顺序：`我们的 row / 1px 分隔线 / 24px section 间距`；线紧贴 row 下边缘。

本节覆盖 §4.3 与 §4.7 中“插在 row 之前”的描述。
## 9. 上方间距匹配（用户验收）

用户要求开关与上方“飞行模式”的间距等于“飞行模式”与“蓝牙”的间距。原生 row 在同一 section 内相邻（无 margin），section 之间才是 24px `margin-bottom`；我们的开关在独立 section 中，因此多出 24px。

`mount` 现在读取前一个 section 的 computed `margin-bottom`，给我们的 section 设置等值负 `margin-top`，抵消 section 间距：

```js
var gap = parseFloat(getComputedStyle(prevSection).marginBottom) || 0;
if (gap) sec.style.marginTop = '-' + gap + 'px';
```

这样开关 row 与上一行紧贴；分隔线仍在 row 下方。