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
  var KEY = 'sf_ui_haptic_switch';
  var BINDING = '__sfUiHapticSwitchSend';
  var STATE_ATTR = 'data-sf-ui-haptic-state';
  var SECTION_ATTR = 'data-sf-ui-haptic-section';
  var LOOK_STYLE_ID = 'sf_ui_haptic_switch_look';

  function classList(el) {
    return String((el && el.className) || '').split(/\s+/).filter(Boolean);
  }

  function nodeFromHTML(doc, html) {
    var box = doc.createElement('div');
    box.innerHTML = html;
    return box.firstElementChild;
  }

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

  /* How an open/closed checkbox is drawn. Two adapters sit behind the same
   * interface: the native single-class fast path and the HTML fallback. */
  function createToggleLook(doc) {
    var KNOB_ON = 'translateX(16px)';
    var KNOB_OFF = 'translateX(0px)';
    var RAIL_ON = 'rgb(26, 159, 255)';
    var RAIL_OFF = 'hsla(0, 0%, 100%, 0.15)';

    function ensureLookStyle() {
      if (doc.getElementById(LOOK_STYLE_ID)) return;
      var st = doc.createElement('style');
      st.id = LOOK_STYLE_ID;
      st.textContent = '#' + ROW_ID +
        ' [role="checkbox"] > div:first-child::before{content:none !important}';
      (doc.head || doc.documentElement).appendChild(st);
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

    function classAdapter(extra) {
      return {
        apply: function (cb, on) {
          if (cb.getAttribute(STATE_ATTR) === (on ? 'true' : 'false')) {
            return finishToggle(cb, on);
          }
          var cls = classList(cb).filter(function (c) { return c !== extra; });
          if (on) cls.push(extra);
          cb.className = cls.join(' ');
          return finishToggle(cb, on);
        }
      };
    }

    function htmlAdapter(onHTML, offHTML) {
      return {
        apply: function (cb, on) {
          if (cb.getAttribute(STATE_ATTR) === (on ? 'true' : 'false')) {
            return finishToggle(cb, on);
          }
          var html = on ? onHTML : offHTML;
          if (!html) return finishToggle(cb, on);
          var fresh = nodeFromHTML(doc, html);
          if (!fresh) return cb;
          fresh.removeAttribute('aria-labelledby');
          var label = cb.getAttribute('aria-label');
          if (label) fresh.setAttribute('aria-label', label);
          if (cb.parentNode) cb.parentNode.replaceChild(fresh, cb);
          return finishToggle(fresh, on);
        }
      };
    }

    function fromTemplate(tpl) {
      if (tpl && tpl.toggleOnExtra) return classAdapter(tpl.toggleOnExtra);
      if (tpl && tpl.toggleOnHTML && tpl.toggleOffHTML) {
        return htmlAdapter(tpl.toggleOnHTML, tpl.toggleOffHTML);
      }
      return { apply: finishToggle };
    }

    return { fromTemplate: fromTemplate };
  }

  /* Template harvesting and its sessionStorage cache. The caller only asks for
   * the current template or for one harvested from the visible QAM bodies. */
  function createTemplateStore(doc, win) {
    var CACHE_KEY = 'sf_ui_haptic_switch_tpl_v2';
    var tpl = null;

    function mine(el) {
      return !!(el && el.closest && el.closest('#' + ROW_ID));
    }

    /* A stock toggle shows its state purely through one extra class on the
     * checkbox element: the knob only slides and the track only turns blue
     * while that class is present (the stylesheet has no [aria-checked] rule
     * at all). A clone therefore keeps whatever state it was cloned in
     * forever. Harvest that class by diffing checked against unchecked. */
    function harvestToggleStates(scope) {
      var boxes = [].slice.call(
        (scope || doc).querySelectorAll('[role="checkbox"]'))
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

    function childWith(scope, probe) {
      var cs = [].slice.call(scope.children);
      for (var i = 0; i < cs.length; i++) {
        if (cs[i].querySelector(probe)) return cs[i];
      }
      return null;
    }

    function grab(body) {
      if (!body) return null;
      var probe = '[role="checkbox"]';

      /* body > section > row, mirroring the stock layout: the section is the
       * group wrapper (reused for spacing), the row is what we clone. */
      var section = childWith(body, probe);
      if (!section) return null;
      var row = childWith(section, probe) || section;
      if (mine(row)) return null;

      var ts = harvestToggleStates(body) || harvestToggleStates(doc) || {};
      /* The native divider is a sibling element, not a border or pseudo-element.
       * Capture its outerHTML so the hashed class stays in sync with the build. */
      var sep = body.querySelector('[role="separator"]') ||
                doc.querySelector('#QuickAccess-Menu [role="separator"]');

      return {
        sectionCls: section.className || '',
        toggleRowHTML: row.outerHTML,
        separatorHTML: sep ? sep.outerHTML : null,
        separatorCls: sep ? String(sep.className) : '',
        toggleOnExtra: ts.toggleOnExtra,
        toggleOnHTML: ts.toggleOnHTML,
        toggleOffHTML: ts.toggleOffHTML
      };
    }

    /* Valve renders a tab's rows only while that tab is selected, so if the
     * Quick Settings page was never opened there is nothing to clone from it.
     * Borrow a row from whichever body IS currently rendered. */
    function grabAny(bodies) {
      for (var i = 0; i < bodies.length; i++) {
        var t = grab(bodies[i]);
        if (t) return t;
      }
      return null;
    }

    function selectorKnown(cls) {
      if (!cls) return false;
      var sheets = doc.styleSheets;
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

    function save() {
      if (!tpl) return;
      try { win.sessionStorage.setItem(CACHE_KEY, JSON.stringify({ tpl: tpl })); }
      catch (e) {}
    }

    function invalidate() {
      tpl = null;
      try { win.sessionStorage.removeItem(CACHE_KEY); } catch (e) {}
    }

    function load() {
      var raw;
      try { raw = win.sessionStorage.getItem(CACHE_KEY); } catch (e) { return null; }
      if (!raw) return null;
      var obj;
      try { obj = JSON.parse(raw); } catch (e) { return null; }
      if (!obj || !obj.tpl) return null;
      var t = obj.tpl;
      if (!t.toggleOnExtra && !(t.toggleOnHTML && t.toggleOffHTML)) return null;
      if (t.toggleOnExtra && !selectorKnown(t.toggleOnExtra)) {
        invalidate();
        return null;
      }
      if (t.separatorCls && !selectorKnown(t.separatorCls)) {
        invalidate();
        return null;
      }
      tpl = t;
      return tpl;
    }

    function harvest(body, bodies) {
      if (!tpl) {
        tpl = grab(body) || grabAny(bodies);
        if (tpl) save();
      }
      return tpl;
    }

    return {
      load: load,
      current: function () { return tpl; },
      harvest: harvest,
      save: save,
      invalidate: invalidate,
      reset: function () { tpl = null; }
    };
  }

  /* Which Quick Settings body is the right one, and which bodies are visible?
   * The body id is the only persisted part; the rest is recomputed. */
  function createQamBody(doc, win) {
    var BODY_KEY = 'sf_ui_haptic_switch_body_v1';

    function menuRoot() { return doc.getElementById('QuickAccess-Menu'); }

    function parts() {
      var root = menuRoot();
      if (!root) return null;
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

    function bodyControls(body) {
      return (body.querySelectorAll('[role="slider"]').length * 2) +
             (body.querySelectorAll('[role="checkbox"]').length);
    }

    function loadBodyId() {
      try { return win.sessionStorage.getItem(BODY_KEY) || null; }
      catch (e) { return null; }
    }

    function pick(p) {
      var bodyId = loadBodyId();
      if (bodyId) {
        var saved = doc.getElementById(bodyId);
        if (saved) return saved;
        try { win.sessionStorage.removeItem(BODY_KEY); } catch (e) {}
      }
      var best = null, bestScore = 0;
      p.bodies.forEach(function (b) {
        var s = bodyControls(b);
        /* the Quick Settings body is quickaccess_content_4; nudge it ahead
         * when the scores are otherwise close */
        if (/_4$/.test(b.id)) s += 1;
        if (s > bestScore) { bestScore = s; best = b; }
      });
      if (!best || bestScore < 2) return null;
      try { win.sessionStorage.setItem(BODY_KEY, best.id); } catch (e) {}
      return best;
    }

    return {
      parts: parts,
      pick: pick,
      visibleBodies: function (p) { return p.bodies.filter(rendered); }
    };
  }

  /* Pointer/click/keydown handling and its duplicate-event throttle. */
  function createGesture(win, getCheckbox, onFlip) {
    var sawDown = 0;
    var lastFlip = 0;
    var cleanups = [];
    var attached = false;

    function toggleTarget(ev) {
      var cb = getCheckbox();
      if (!cb || !ev || !ev.target) return null;
      var t = ev.target;
      if (t === cb) return cb;
      if (cb.contains && cb.contains(t)) return cb;
      if (t.closest && t.closest('#' + ROW_ID)) return cb;
      return null;
    }

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

      onFlip(cb);
    }

    function onKey(ev) {
      if (ev.key !== 'Enter' && ev.key !== ' ' && ev.key !== 'Spacebar') return;
      flip(ev);
    }

    return {
      attach: function () {
        if (attached) return;
        attached = true;
        win.addEventListener('pointerdown', flip, true);
        win.addEventListener('click', flip, true);
        win.addEventListener('keydown', onKey, true);
        cleanups.push(function () {
          win.removeEventListener('pointerdown', flip, true);
          win.removeEventListener('click', flip, true);
          win.removeEventListener('keydown', onKey, true);
        });
      },
      detach: function () {
        cleanups.forEach(function (fn) { try { fn(); } catch (e) {} });
        cleanups = [];
        attached = false;
      }
    };
  }

  function createUi(win) {
    var doc = win.document;
    var qam = createQamBody(doc, win);
    var templates = createTemplateStore(doc, win);
    var gesture;
    var look = null;
    var mounted = false;
    var observer = null;
    var scheduled = false;
    var scheduleTimer = null;
    var tornDown = false;

    var LABELS = {
      en: 'Controller UI Haptic',
      'zh-CN': '控制器界面震动',
      'zh-TW': '控制器介面震動',
      ja: 'コントローラー UI 振動'
    };

    function log() {
      try {
        win.console.log.apply(win.console,
          ['[sf-ui-haptic-ui]'].concat(
            Array.prototype.slice.call(arguments)));
      } catch (e) {}
    }

    function languageTag(value) {
      if (!value) return null;
      var tag = String(value).toLowerCase().replace(/_/g, '-');
      if (tag.indexOf('zh') === 0) {
        return /^zh-(hant|tw|hk|mo)/.test(tag) ? 'zh-TW' : 'zh-CN';
      }
      if (tag.indexOf('ja') === 0) return 'ja';
      if (tag.indexOf('en') === 0) return 'en';
      return null;
    }

    function detectLang() {
      var candidates = [];
      try {
        if (win.navigator.languages && win.navigator.languages.length) {
          candidates = candidates.concat(
            Array.prototype.slice.call(win.navigator.languages));
        }
      } catch (e) {}
      try { if (win.navigator.language) candidates.push(win.navigator.language); }
      catch (e) {}
      try {
        var root = doc.documentElement;
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

    function readEnabled() {
      try {
        var v = win.localStorage.getItem(KEY);
        return !(v === '0' || v === 'false');
      } catch (e) { return true; }
    }

    function writeEnabled(on) {
      try { win.localStorage.setItem(KEY, on ? 'true' : 'false'); } catch (e) {}
    }

    function sendEnabled(on) {
      try {
        var fn = win[BINDING];
        if (typeof fn === 'function') {
          fn(JSON.stringify({ c: 'haptic', v: !!on }));
        }
      } catch (e) {}
    }

    function liveCheckbox() {
      return doc.querySelector('#' + ROW_ID + ' [role="checkbox"]') || null;
    }

    function onFlip(cb) {
      var shown = cb.getAttribute(STATE_ATTR);
      var on = shown === 'true' ? true
             : (shown === 'false' ? false : readEnabled());
      setEnabled(!on);
    }

    gesture = createGesture(win, liveCheckbox, onFlip);

    function labelBoxOf(row) {
      return row.querySelector('[id$="_Label"]') || row.querySelector('div > div');
    }

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
      box.appendChild(doc.createTextNode(text));
      return box;
    }

    function setIcon(row, svgText) {
      var box = labelBoxOf(row);
      if (!box) return;
      var holder = box.firstElementChild;
      if (holder && holder.querySelector('svg')) holder.innerHTML = svgText;
    }

    function makeSeparator() {
      var tpl = templates.current();
      if (tpl && tpl.separatorHTML) {
        var el = nodeFromHTML(doc, tpl.separatorHTML);
        if (el) return el;
      }
      var d = doc.createElement('div');
      d.setAttribute('role', 'separator');
      d.style.height = '1px';
      d.style.background = 'rgba(255, 255, 255, 0.1)';
      return d;
    }

    /* React may drop the sibling while keeping our row; restore it after it. */
    function ensureSeparator() {
      var row = doc.getElementById(ROW_ID);
      if (!row || !row.parentElement) return;
      var next = row.nextElementSibling;
      if (next && next.getAttribute('role') === 'separator') return;
      row.parentElement.insertBefore(makeSeparator(), row.nextSibling);
    }

    function toggleRow(label, icon) {
      var tpl = templates.current();
      var row = nodeFromHTML(doc, tpl.toggleRowHTML);
      if (!row) return null;
      row.id = ROW_ID;
      row.removeAttribute('aria-labelledby');
      setLabel(row, label);
      setIcon(row, icon);
      var cb = row.querySelector('[role="checkbox"]');
      if (cb) {
        cb.removeAttribute('aria-labelledby');
        cb.setAttribute('aria-label', label);
        look.apply(cb, readEnabled());
      }
      stripIds(row);
      return row;
    }

    function removeSections() {
      var sections = [].slice.call(
        doc.querySelectorAll('[' + SECTION_ATTR + ']'));
      sections.forEach(function (el) {
        if (el.parentNode) el.parentNode.removeChild(el);
      });
    }

    function mount() {
      if (tornDown) return false;
      var p = qam.parts();
      if (!p) return false;
      var body = qam.pick(p);
      if (!body) return false;

      var tpl = templates.harvest(body, qam.visibleBodies(p));
      if (!tpl) return false;
      look = createToggleLook(doc).fromTemplate(tpl);

      removeSections();
      var existing = doc.getElementById(ROW_ID);
      if (existing && existing.parentNode) existing.parentNode.removeChild(existing);

      var row = toggleRow(labelText(), ICON_HAPTIC);
      if (!row) return false;

      /* Native sections are 24px apart, but rows inside a section touch. Pull
       * our section up by the previous section's bottom margin so the switch
       * sits as close to the row above as two native rows do. */
      var prevSection = body.lastElementChild;
      var host = body;
      if (tpl.sectionCls) {
        var sec = doc.createElement('div');
        sec.className = tpl.sectionCls;
        sec.setAttribute(SECTION_ATTR, '');
        if (prevSection) {
          var gap = win.getComputedStyle(prevSection).marginBottom;
          gap = parseFloat(gap) || 0;
          if (gap) sec.style.marginTop = '-' + gap + 'px';
        }
        sec.appendChild(row);
        sec.appendChild(makeSeparator());
        host = sec;
      }
      body.appendChild(host);

      mounted = true;
      log('switch mounted into ' + body.id);
      return true;
    }

    function render() {
      if (!mounted) return;
      ensureSeparator();
      var cb = liveCheckbox();
      if (cb) look.apply(cb, readEnabled());
      var row = doc.getElementById(ROW_ID);
      if (row) stripIds(row);
    }

    function sync() {
      if (tornDown || !qam.parts()) return;
      if (!doc.getElementById(ROW_ID)) {
        mount();
      } else {
        render();
      }
    }

    function schedule() {
      if (tornDown || scheduled) return;
      scheduled = true;
      scheduleTimer = win.setTimeout(function () {
        scheduleTimer = null;
        scheduled = false;
        if (tornDown) return;
        try { sync(); } catch (e) { log('sync error', e); }
      }, 60);
    }

    function startObserver() {
      if (observer) return;
      observer = new win.MutationObserver(schedule);
      observer.observe(doc.body || doc.documentElement,
                       { childList: true, subtree: true });
    }

    function setEnabled(next) {
      next = !!next;
      writeEnabled(next);
      sendEnabled(next);
      render();
    }

    function teardown() {
      tornDown = true;
      if (scheduleTimer) { win.clearTimeout(scheduleTimer); scheduleTimer = null; }
      scheduled = false;
      if (observer) { observer.disconnect(); observer = null; }
      gesture.detach();
      var row = doc.getElementById(ROW_ID);
      if (row && row.parentNode) row.parentNode.removeChild(row);
      removeSections();
      var lookStyle = doc.getElementById(LOOK_STYLE_ID);
      if (lookStyle && lookStyle.parentNode) lookStyle.parentNode.removeChild(lookStyle);
      mounted = false;
      look = null;
      templates.reset();
      try { if (NS.ui === API) NS.ui = null; } catch (e) {}
    }

    var API = {
      v: 1,
      getState: function () { return { enabled: readEnabled() }; },
      sync: sync,
      teardown: teardown
    };

    function start() {
      NS.ui = API;
      templates.load();
      gesture.attach();
      startObserver();
      sync();
      log('installed v1' + (templates.current() ? ' (cached templates)' : ''));
      return 'ok:ui';
    }

    return { start: start };
  }

  return createUi(window).start();
})();
