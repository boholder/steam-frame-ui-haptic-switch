/* sf_ui_haptic_switch_ui.js — injected into the Quick Access Menu popup only.
 *
 * Appends a single "Controller UI Haptic" switch to the end of the Quick
 * Settings tab. Flipping it writes `localStorage['sf_ui_haptic_switch']`
 * (true/false) and then notifies the daemon through the __sfUiHapticSwitchSend
 * binding. The daemon persists the value and pushes it to the SteamVR
 * dashboard pages; the gate script in every page reads the flag at call time.
 * The daemon never writes back to :8080, so there is no echo and no pending
 * state to suppress.
 *
 * The row is cloned from a real menu toggle row, so it is pixel-identical to
 * the stock menus and survives Valve rehashing class names. Idempotent: a
 * re-injection tears the previous instance down first. The injector owns
 * `window.__SF_UI_HAPTIC_SWITCH__.build`; this script only ever sets `.ui`.
 *
 * Plain ES5, no build step.
 */
(function () {
  'use strict';

  var NS = window.__SF_UI_HAPTIC_SWITCH__ = window.__SF_UI_HAPTIC_SWITCH__ || {};
  var prev = NS.ui;
  if (prev && prev.teardown) { try { prev.teardown(); } catch (e) {} }

  var ROW_ID = 'sf_ui_haptic_switch_row';
  var LOOK_STYLE_ID = 'sf_ui_haptic_switch_look';
  var KEY = 'sf_ui_haptic_switch';
  var BINDING = '__sfUiHapticSwitchSend';
  var CACHE_KEY = 'sf_ui_haptic_switch_tpl_v1';
  var BODY_KEY = 'sf_ui_haptic_switch_body_v1';
  var STATE_ATTR = 'data-sf-ui-haptic-state';
  var SECTION_ATTR = 'data-sf-ui-haptic-section';

  var mounted = false;    /* our row exists in the DOM */
  var tpl = null;
  var observer = null;
  var scheduled = false;
  var scheduleTimer = null;
  var tornDown = false;
  var cleanups = [];
  var lastFlip = 0;

  function log() {
    try {
      console.log.apply(console, ['[sf-ui-haptic-ui]'].concat(
        Array.prototype.slice.call(arguments)));
    } catch (e) {}
  }

  /* Shared contract with the gate: off only when explicitly '0'/'false'. */
  function readEnabled() {
    try {
      var v = localStorage.getItem(KEY);
      return !(v === '0' || v === 'false');
    } catch (e) { return true; }
  }

  function writeEnabled(on) {
    try { localStorage.setItem(KEY, on ? 'true' : 'false'); } catch (e) {}
  }

  /* Notify the daemon. The value it reads from localStorage is authoritative;
   * this payload is only a "something changed" hint. A missing binding (daemon
   * restarting) is fine: the write above already happened, and the daemon
   * re-syncs from localStorage on reconnect. */
  function sendEnabled(on) {
    try {
      var fn = window[BINDING];
      if (typeof fn === 'function') {
        fn(JSON.stringify({ c: 'haptic', v: !!on }));
      }
    } catch (e) {}
  }

  /* ------------------------------------------------------------------ */
  /* label localization                                                  */
  /* ------------------------------------------------------------------ */

  /* The QAM renders in the client's language, which follows the browser
   * locale. Unsupported locales fall back to English. */
  var LABELS = {
    en: 'Controller UI Haptic',
    'zh-CN': '控制器界面震动',
    'zh-TW': '控制器介面震動',
    ja: 'コントローラー UI 振動'
  };

  function languageTag(value) {
    if (!value) return null;
    var tag = String(value).toLowerCase().replace(/_/g, '-');
    if (tag.indexOf('zh') === 0) {
      /* zh-Hant, zh-TW, zh-HK and zh-MO all read Traditional. */
      return /^zh-(hant|tw|hk|mo)/.test(tag) ? 'zh-TW' : 'zh-CN';
    }
    if (tag.indexOf('ja') === 0) return 'ja';
    if (tag.indexOf('en') === 0) return 'en';
    return null;
  }

  function detectLang() {
    var candidates = [];
    try {
      if (navigator.languages && navigator.languages.length) {
        candidates = candidates.concat(
          Array.prototype.slice.call(navigator.languages));
      }
    } catch (e) {}
    try { if (navigator.language) candidates.push(navigator.language); } catch (e) {}
    /* The client may localize the DOM even when the browser locale differs. */
    try {
      var root = document.documentElement;
      if (root && root.lang) candidates.push(root.lang);
    } catch (e) {}
    for (var i = 0; i < candidates.length; i++) {
      var lang = languageTag(candidates[i]);
      if (lang) return lang;
    }
    return 'en';
  }

  function labelText() {
    return LABELS[detectLang()] || LABELS.en;
  }

  /* ------------------------------------------------------------------ */
  /* icons                                                               */
  /* ------------------------------------------------------------------ */

  function svg(inner, vb) {
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="' +
      (vb || '0 0 36 36') + '" fill="none" width="100%" height="100%">' +
      inner + '</svg>';
  }

  /* vibration icon; the paths are 24x24, so use a matching viewBox instead of
   * the 36x36 default (the stock 36 box made it look undersized). */
  var ICON_HAPTIC = svg(
      '<g fill="currentColor">' +
      '<path fill="none" d="M0 0h24v24H0z"/>' +
      '<path d="M19.39 3.161l1.413 1.414-2.475 2.475 2.475 2.475' +
      'L18.328 12l2.475 2.476-2.475 2.475 2.475 2.475' +
      '-1.414 1.414-3.889-3.89 2.475-2.474L15.5 12' +
      'l2.475-2.475L15.5 7.05l3.89-3.889z' +
      'm-6.503.578a.5.5 0 0 1 .113.316v15.89' +
      'a.5.5 0 0 1-.817.387L6.89 15.999 3 16' +
      'a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1h3.889' +
      'l5.294-4.332a.5.5 0 0 1 .704.07z' +
      'M11 7.22L7.603 9.999H4V14l3.603-.001L11 16.779V7.22z"/>' +
      '</g>',
      '0 0 24 24'
  );

  /* ------------------------------------------------------------------ */
  /* DOM discovery                                                       */
  /* ------------------------------------------------------------------ */

  function menuRoot() { return document.getElementById('QuickAccess-Menu'); }

  function parts() {
    var root = menuRoot();
    if (!root) return null;
    /* the tablist only proves the menu has rendered; we inject into its tab
     * bodies, not into the tab bar */
    if (!root.querySelector('[role="tablist"]')) return null;
    var bodies = [].slice.call(
      root.querySelectorAll('[id^="quickaccess_content_"]'));
    if (!bodies.length) return null;
    return { bodies: bodies };
  }

  function rendered(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function classList(el) {
    return String((el && el.className) || '').split(/\s+/).filter(Boolean);
  }

  function mine(el) {
    return !!(el && el.closest && el.closest('#' + ROW_ID));
  }

  /* Which quickaccess_content_* body carries the Quick Settings controls?  The
   * body with the most sliders/checkboxes is the settings page.  The pick is
   * cached so it stays stable while tabs are switched (an unrendered body has
   * no controls and would otherwise lose the vote). */
  function bodyControls(body) {
    return (body.querySelectorAll('[role="slider"]').length * 2) +
           (body.querySelectorAll('[role="checkbox"]').length);
  }

  function loadBodyId() {
    try { return sessionStorage.getItem(BODY_KEY) || null; } catch (e) { return null; }
  }

  function targetBody(p) {
    var saved = loadBodyId();
    if (saved) {
      var el = document.getElementById(saved);
      if (el) return el;
      try { sessionStorage.removeItem(BODY_KEY); } catch (e) {}
    }
    var best = null, bestScore = 0;
    p.bodies.forEach(function (b) {
      var s = bodyControls(b);
      /* the Quick Settings body is quickaccess_content_4; nudge it ahead when
       * the scores are otherwise close */
      if (/_4$/.test(b.id)) s += 1;
      if (s > bestScore) { bestScore = s; best = b; }
    });
    if (!best || bestScore < 2) return null;
    try { sessionStorage.setItem(BODY_KEY, best.id); } catch (e) {}
    return best;
  }

  /* ------------------------------------------------------------------ */
  /* template harvesting (styling by cloning, not by CSS)                */
  /* ------------------------------------------------------------------ */

  /* A stock toggle shows its state purely through one extra class on the
   * checkbox element: the knob only slides and the track only turns blue while
   * that class is present (the stylesheet has no [aria-checked] rule at all).
   * A clone therefore keeps whatever state it was cloned in forever.  Harvest
   * that class by diffing a checked against an unchecked toggle. */
  function harvestToggleStates(scope) {
    var boxes = [].slice.call(
      (scope || document).querySelectorAll('[role="checkbox"]'))
      .filter(function (b) { return !mine(b); });
    var on = null, off = null;
    for (var i = 0; i < boxes.length; i++) {
      var checked = boxes[i].getAttribute('aria-checked') === 'true';
      if (checked && !on) on = boxes[i];
      if (!checked && !off) off = boxes[i];
      if (on && off) break;
    }
    if (!on && !off) return null;
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
  }

  /* the first direct child of <scope> that contains <probe> */
  function childWith(scope, probe) {
    var cs = [].slice.call(scope.children);
    for (var i = 0; i < cs.length; i++) {
      if (cs[i].querySelector(probe)) return cs[i];
    }
    return null;
  }

  function grabTemplate(body) {
    if (!body) return null;
    var probe = '[role="checkbox"]';

    /* body > section > row, mirroring the stock layout: the section is the
     * group wrapper (reused for spacing), the row is what we clone. */
    var section = childWith(body, probe);
    if (!section) return null;
    var row = childWith(section, probe) || section;
    if (mine(row)) return null;

    var ts = harvestToggleStates(body) || harvestToggleStates(document) || {};

    return {
      sectionCls: section.className || '',
      toggleRowHTML: row.outerHTML,
      toggleOnExtra: ts.toggleOnExtra,
      toggleOnHTML: ts.toggleOnHTML,
      toggleOffHTML: ts.toggleOffHTML
    };
  }

  /* Valve renders a tab's rows only while that tab is selected, so if the Quick
   * Settings page was never opened there is nothing to clone from it.  Borrow a
   * row from whichever body IS currently rendered so the switch can be built at
   * all; the styles are identical across tabs. */
  function grabTemplateAny(p) {
    for (var i = 0; i < p.bodies.length; i++) {
      if (rendered(p.bodies[i])) {
        var t = grabTemplate(p.bodies[i]);
        if (t) return t;
      }
    }
    return null;
  }

  /* Valve rehashes class names between client builds, so a cached template is
   * only reusable while its classes still exist in the stylesheets. */
  function selectorKnown(cls) {
    if (!cls) return false;
    var sheets = document.styleSheets;
    for (var i = 0; i < sheets.length; i++) {
      var rules;
      try { rules = sheets[i].cssRules; } catch (e) { continue; }
      if (!rules) continue;
      for (var j = 0; j < rules.length; j++) {
        var sel = rules[j].selectorText;
        if (sel && sel.indexOf(cls) >= 0) return true;
      }
    }
    return false;
  }

  function saveTemplates() {
    if (!tpl) return;
    try {
      sessionStorage.setItem(CACHE_KEY, JSON.stringify({ tpl: tpl }));
    } catch (e) {}
  }

  function loadTemplates() {
    var raw;
    try { raw = sessionStorage.getItem(CACHE_KEY); } catch (e) { return null; }
    if (!raw) return null;
    var obj;
    try { obj = JSON.parse(raw); } catch (e) { return null; }
    if (!obj || !obj.tpl) return null;
    var t = obj.tpl;
    if (!t.toggleOnExtra && !(t.toggleOnHTML && t.toggleOffHTML)) return null;
    if (t.toggleOnExtra && !selectorKnown(t.toggleOnExtra)) {
      try { sessionStorage.removeItem(CACHE_KEY); } catch (e) {}
      return null;
    }
    return t;
  }

  function nodeFromHTML(html) {
    var box = document.createElement('div');
    box.innerHTML = html;
    return box.firstElementChild;
  }

  /* ------------------------------------------------------------------ */
  /* row construction                                                    */
  /* ------------------------------------------------------------------ */

  function labelBoxOf(row) {
    return row.querySelector('[id$="_Label"]') || row.querySelector('div > div');
  }

  /* A clone carries the stock row's descendant ids (e.g. ..._Label), which
   * would then collide with the original row. Strip them; no code needs them. */
  function stripIds(row) {
    var ids = row.querySelectorAll('[id]');
    for (var i = 0; i < ids.length; i++) ids[i].removeAttribute('id');
  }

  function setLabel(row, text) {
    var box = labelBoxOf(row);
    if (!box) return null;
    var nodes = [].slice.call(box.childNodes);
    nodes.forEach(function (n) {
      if (n.nodeType === 3 || (n.nodeType === 1 && n.tagName !== 'DIV')) {
        box.removeChild(n);
      }
    });
    box.appendChild(document.createTextNode(text));
    return box;
  }

  function setIcon(row, svgText) {
    var box = labelBoxOf(row);
    if (!box) return;
    var holder = box.firstElementChild;
    if (holder && holder.querySelector('svg')) holder.innerHTML = svgText;
  }

  /* The stock switch is drawn entirely by CSS: the knob slides via a transform
   * and the track turns blue via a ::before. Both animate on `transition`, and a
   * transition only advances while the page is actually composited -- the popup
   * is throttled whenever nobody is looking at it, and then the switch looks
   * like it never reacts. So the look is also pinned inline. */
  var KNOB_ON = 'translateX(16px)';
  var KNOB_OFF = 'translateX(0px)';
  var RAIL_ON = 'rgb(26, 159, 255)';
  var RAIL_OFF = 'hsla(0, 0%, 100%, 0.15)';

  function ensureLookStyle() {
    if (document.getElementById(LOOK_STYLE_ID)) return;
    var st = document.createElement('style');
    st.id = LOOK_STYLE_ID;
    st.textContent = '#' + ROW_ID +
      ' [role="checkbox"] > div:first-child::before{content:none !important}';
    (document.head || document.documentElement).appendChild(st);
  }

  function pinToggleLook(cb, on) {
    if (!cb || !cb.children || cb.children.length !== 2) return;
    ensureLookStyle();
    var rail = cb.children[0], knob = cb.children[1];
    rail.style.transition = 'none';
    rail.style.background = on ? RAIL_ON : RAIL_OFF;
    knob.style.transition = 'none';
    knob.style.transform = on ? KNOB_ON : KNOB_OFF;
  }

  function finishToggle(cb, on) {
    cb.setAttribute(STATE_ATTR, on ? 'true' : 'false');
    cb.setAttribute('aria-checked', on ? 'true' : 'false');
    pinToggleLook(cb, on);
    return cb;
  }

  function applyToggleLook(cb, on) {
    if (!cb || !tpl) return cb;
    var want = on ? 'true' : 'false';
    if (cb.getAttribute(STATE_ATTR) === want) {
      return finishToggle(cb, on);
    }
    if (tpl.toggleOnExtra) {
      var cls = classList(cb).filter(function (c) { return c !== tpl.toggleOnExtra; });
      if (on) cls.push(tpl.toggleOnExtra);
      cb.className = cls.join(' ');
      return finishToggle(cb, on);
    }
    var html = on ? tpl.toggleOnHTML : tpl.toggleOffHTML;
    if (!html) {
      return finishToggle(cb, on);
    }
    var fresh = nodeFromHTML(html);
    if (!fresh) return cb;
    fresh.removeAttribute('aria-labelledby');
    var label = cb.getAttribute('aria-label');
    if (label) fresh.setAttribute('aria-label', label);
    if (cb.parentNode) cb.parentNode.replaceChild(fresh, cb);
    return finishToggle(fresh, on);
  }

  function toggleRow(label, icon) {
    var row = nodeFromHTML(tpl.toggleRowHTML);
    if (!row) return null;
    row.id = ROW_ID;
    row.removeAttribute('aria-labelledby');
    setLabel(row, label);
    setIcon(row, icon);
    var cb = row.querySelector('[role="checkbox"]');
    if (cb) {
      cb.removeAttribute('aria-labelledby');
      cb.setAttribute('aria-label', label);
      applyToggleLook(cb, readEnabled());
    }
    stripIds(row);
    return row;
  }

  /* ------------------------------------------------------------------ */
  /* mount / render                                                      */
  /* ------------------------------------------------------------------ */

  function removeSections() {
    var sections = [].slice.call(
      document.querySelectorAll('[' + SECTION_ATTR + ']'));
    sections.forEach(function (el) {
      if (el.parentNode) el.parentNode.removeChild(el);
    });
  }

  function mount() {
    if (tornDown) return false;
    var p = parts();
    if (!p) return false;
    var body = targetBody(p);
    if (!body) return false;

    if (!tpl) {
      tpl = grabTemplate(body) || grabTemplateAny(p);
      if (tpl) saveTemplates();
    }
    if (!tpl) return false;

    removeSections();
    var existing = document.getElementById(ROW_ID);
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);

    var row = toggleRow(labelText(), ICON_HAPTIC);
    if (!row) return false;

    var host = body;
    if (tpl.sectionCls) {
      var sec = document.createElement('div');
      sec.className = tpl.sectionCls;
      sec.setAttribute(SECTION_ATTR, '');
      sec.appendChild(row);
      host = sec;
    }
    body.appendChild(host);

    mounted = true;
    log('switch mounted into ' + body.id);
    return true;
  }

  function render() {
    if (!mounted) return;
    var cb = liveCheckbox();
    if (cb) applyToggleLook(cb, readEnabled());
    var row = document.getElementById(ROW_ID);
    if (row) stripIds(row);
  }

  function sync() {
    if (tornDown || !parts()) return;
    /* (Re)mount when our row vanished (tab switch / React re-render). */
    if (!document.getElementById(ROW_ID)) {
      mount();
    } else {
      render();
    }
  }

  function schedule() {
    if (tornDown || scheduled) return;
    scheduled = true;
    scheduleTimer = setTimeout(function () {
      scheduleTimer = null;
      scheduled = false;
      if (tornDown) return;
      try { sync(); } catch (e) { log('sync error', e); }
    }, 60);
  }

  function startObserver() {
    if (observer) return;
    observer = new MutationObserver(schedule);
    observer.observe(document.body || document.documentElement,
                     { childList: true, subtree: true });
  }

  /* ------------------------------------------------------------------ */
  /* toggle interaction                                                  */
  /* ------------------------------------------------------------------ */

  function liveCheckbox() {
    return document.querySelector('#' + ROW_ID + ' [role="checkbox"]') || null;
  }

  function toggleTarget(ev) {
    var cb = liveCheckbox();
    if (!cb || !ev || !ev.target) return null;
    var t = ev.target;
    if (t === cb) return cb;
    if (cb.contains && cb.contains(t)) return cb;
    if (t.closest && t.closest('#' + ROW_ID)) return cb;
    return null;
  }

  function setEnabled(next) {
    next = !!next;
    writeEnabled(next);
    sendEnabled(next);
    render();
  }

  function hookToggle() {
    var sawDown = 0;

    function flip(ev) {
      var cb = toggleTarget(ev);
      if (!cb) return;

      var now = Date.now();
      if (ev.type === 'pointerdown') {
        if (ev.button !== undefined && ev.button !== 0) return;
      }
      /* Throttled click/keydown still belong to our row: keep the stock
       * default suppressed before either early return below. */
      if (ev.type !== 'pointerdown' && ev.preventDefault) ev.preventDefault();
      if (ev.stopPropagation) ev.stopPropagation();
      if (ev.type === 'click') {
        if (now - sawDown < 800) return;   /* already handled pointerdown */
      }
      if (now - lastFlip < 250) return;
      if (ev.type === 'pointerdown') sawDown = now;
      lastFlip = now;

      var shown = cb.getAttribute(STATE_ATTR);
      var on = shown === 'true' ? true
             : (shown === 'false' ? false : readEnabled());
      setEnabled(!on);
    }

    function onKey(ev) {
      if (ev.key !== 'Enter' && ev.key !== ' ' && ev.key !== 'Spacebar') return;
      flip(ev);
    }

    window.addEventListener('pointerdown', flip, true);
    window.addEventListener('click', flip, true);
    window.addEventListener('keydown', onKey, true);
    cleanups.push(function () {
      window.removeEventListener('pointerdown', flip, true);
      window.removeEventListener('click', flip, true);
      window.removeEventListener('keydown', onKey, true);
    });
  }

  /* ------------------------------------------------------------------ */
  /* api                                                                 */
  /* ------------------------------------------------------------------ */

  var API = {
    v: 1,
    getState: function () { return { enabled: readEnabled() }; },
    sync: sync,
    teardown: function () {
      tornDown = true;
      if (scheduleTimer) { clearTimeout(scheduleTimer); scheduleTimer = null; }
      scheduled = false;
      if (observer) { observer.disconnect(); observer = null; }
      cleanups.forEach(function (fn) { try { fn(); } catch (e) {} });
      cleanups = [];
      var row = document.getElementById(ROW_ID);
      if (row && row.parentNode) row.parentNode.removeChild(row);
      removeSections();
      var look = document.getElementById(LOOK_STYLE_ID);
      if (look && look.parentNode) look.parentNode.removeChild(look);
      mounted = false; tpl = null;
      try { if (NS.ui === API) NS.ui = null; } catch (e) {}
    }
  };
  NS.ui = API;

  tpl = loadTemplates();

  hookToggle();
  startObserver();
  sync();
  log('installed v1' + (tpl ? ' (cached templates)' : ''));
  return 'ok:ui';
})();
