/* sf_ui_haptic_switch_ui.js — injected into the Quick Access Menu popup only.
 *
 * Appends a single "Controller UI Haptic" switch to the end of the Quick
 * Settings tab. Flipping it writes `localStorage['sf_ui_haptic_switch']`
 * ('0'/'1'); the gate script in every page reads that flag at call time. There
 * is no daemon and no binding, so the switch just updates the flag and its own
 * look.
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
  var LABEL = 'Controller UI Haptic';
  var KEY = 'sf_ui_haptic_switch';
  var CACHE_KEY = 'sf_ui_haptic_switch_tpl_v1';
  var BODY_KEY = 'sf_ui_haptic_switch_body_v1';
  var STATE_ATTR = 'data-sf-ui-haptic-state';

  var ui = null;          /* { row, checkbox } */
  var tpl = null;
  var bodyId = null;
  var observer = null;
  var scheduled = false;
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
    try { localStorage.setItem(KEY, on ? '1' : '0'); } catch (e) {}
  }

  /* ------------------------------------------------------------------ */
  /* icons                                                               */
  /* ------------------------------------------------------------------ */

  function svg(inner) {
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36"' +
      ' fill="none" width="100%" height="100%">' + inner + '</svg>';
  }

  var ICON_HAPTIC = svg(
    '<g fill="none" stroke="currentColor" stroke-width="2.4"' +
    ' stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M12.5 17.5v-6a2 2 0 0 1 4 0v5"/>' +
    '<path d="M16.5 15v-1.5a2 2 0 0 1 4 0V16"/>' +
    '<path d="M20.5 16v-1a2 2 0 0 1 4 0v8.5a7 7 0 0 1-7 7h-1.2a6 6 0 0' +
    ' 1-4.6-2.2l-4-4.9a1.9 1.9 0 0 1 2.8-2.5l2 1.8"/>' +
    '</g>' +
    '<g fill="none" stroke="currentColor" stroke-width="2.2"' +
    ' stroke-linecap="round">' +
    '<path d="M28.5 12.5a6 6 0 0 1 0 6"/>' +
    '<path d="M31.5 10a10 10 0 0 1 0 11"/>' +
    '</g>');

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

  function targetBody(p) {
    if (bodyId) {
      var cached = document.getElementById(bodyId);
      if (cached) return cached;
      bodyId = null;
    }
    try { bodyId = sessionStorage.getItem(BODY_KEY) || null; } catch (e) { bodyId = null; }
    if (bodyId) {
      var saved = document.getElementById(bodyId);
      if (saved) return saved;
      bodyId = null;
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
    bodyId = best.id;
    try { sessionStorage.setItem(BODY_KEY, bodyId); } catch (e) {}
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
      if (diff.length) extra = diff.join(' ');
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
    var toggle = body.querySelector(probe);
    if (!toggle || mine(toggle)) return null;

    /* body > section > row, mirroring the stock layout: the section is the
     * group wrapper (reused for spacing), the row is what we clone. */
    var section = childWith(body, probe);
    if (!section) return null;
    var row = childWith(section, probe) || section;

    var ts = harvestToggleStates(body) || harvestToggleStates(document) || {};

    return {
      sectionCls: section.className || '',
      toggleRowHTML: row.outerHTML,
      toggleOnExtra: ts.toggleOnExtra || null,
      toggleOnHTML: ts.toggleOnHTML || null,
      toggleOffHTML: ts.toggleOffHTML || null
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

  function applyToggleLook(cb, on) {
    if (!cb || !tpl) return cb;
    var want = on ? '1' : '0';
    if (cb.getAttribute(STATE_ATTR) === want) {
      pinToggleLook(cb, on);
      return cb;
    }
    if (tpl.toggleOnExtra) {
      var cls = classList(cb).filter(function (c) { return c !== tpl.toggleOnExtra; });
      if (on) cls.push(tpl.toggleOnExtra);
      cb.className = cls.join(' ');
      cb.setAttribute(STATE_ATTR, want);
      cb.setAttribute('aria-checked', on ? 'true' : 'false');
      pinToggleLook(cb, on);
      return cb;
    }
    var html = on ? tpl.toggleOnHTML : tpl.toggleOffHTML;
    if (!html) {
      cb.setAttribute('aria-checked', on ? 'true' : 'false');
      pinToggleLook(cb, on);
      return cb;
    }
    var fresh = nodeFromHTML(html);
    if (!fresh) return cb;
    fresh.setAttribute(STATE_ATTR, want);
    fresh.setAttribute('aria-checked', on ? 'true' : 'false');
    fresh.removeAttribute('aria-labelledby');
    var label = cb.getAttribute('aria-label');
    if (label) fresh.setAttribute('aria-label', label);
    if (cb.parentNode) cb.parentNode.replaceChild(fresh, cb);
    pinToggleLook(fresh, on);
    if (ui) ui.checkbox = fresh;
    return fresh;
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
      cb = applyToggleLook(cb, readEnabled()) || cb;
    }
    row.__cb = cb;
    return row;
  }

  /* ------------------------------------------------------------------ */
  /* mount / render                                                      */
  /* ------------------------------------------------------------------ */

  function mount() {
    var p = parts();
    if (!p) return false;
    var body = targetBody(p);
    if (!body) return false;

    if (!tpl) {
      tpl = grabTemplate(body) || grabTemplateAny(p);
      if (tpl) saveTemplates();
    }
    if (!tpl) return false;

    var existing = document.getElementById(ROW_ID);
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);

    var row = toggleRow(LABEL, ICON_HAPTIC);
    if (!row) return false;

    var host = body;
    if (tpl.sectionCls) {
      var sec = document.createElement('div');
      sec.className = tpl.sectionCls;
      sec.appendChild(row);
      host = sec;
    }
    body.appendChild(host);

    ui = { row: row, checkbox: row.__cb };
    render();
    log('switch mounted into ' + body.id);
    return true;
  }

  function render() {
    if (!ui) return;
    var cb = liveCheckbox();
    if (cb) {
      cb = applyToggleLook(cb, readEnabled()) || cb;
      ui.checkbox = cb;
    }
  }

  function sync() {
    if (!parts()) return;
    /* (Re)mount when our row vanished (tab switch / React re-render). */
    if (!document.getElementById(ROW_ID)) {
      mount();
    } else {
      render();
    }
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    setTimeout(function () {
      scheduled = false;
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

  /* No daemon and no echo: flipping writes the shared flag and repaints. */
  function setEnabled(next) {
    next = !!next;
    writeEnabled(next);
    render();
    return true;
  }

  function hookToggle() {
    var sawDown = 0;

    function flip(ev) {
      var cb = toggleTarget(ev);
      if (!cb) return;
      if (ev.type === 'click') {
        if (Date.now() - sawDown < 800) return;   /* already handled pointerdown */
      } else {
        if (ev.button !== undefined && ev.button !== 0) return;
        sawDown = Date.now();
      }
      if (ev.preventDefault) ev.preventDefault();
      if (ev.stopPropagation) ev.stopPropagation();
      var now = Date.now();
      if (now - lastFlip < 250) return;
      lastFlip = now;

      var shown = cb.getAttribute(STATE_ATTR);
      var on = shown === '1' ? true
             : (shown === '0' ? false : readEnabled());
      setEnabled(!on);
    }

    window.addEventListener('pointerdown', flip, true);
    window.addEventListener('click', flip, true);
    window.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Enter' && ev.key !== ' ' && ev.key !== 'Spacebar') return;
      flip(ev);
    }, true);
    cleanups.push(function () {
      window.removeEventListener('pointerdown', flip, true);
      window.removeEventListener('click', flip, true);
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
      if (observer) { observer.disconnect(); observer = null; }
      cleanups.forEach(function (fn) { try { fn(); } catch (e) {} });
      cleanups = [];
      var row = document.getElementById(ROW_ID);
      if (row && row.parentNode) row.parentNode.removeChild(row);
      var look = document.getElementById(LOOK_STYLE_ID);
      if (look && look.parentNode) look.parentNode.removeChild(look);
      ui = null; tpl = null;
      try { if (NS.ui === API) NS.ui = null; } catch (e) {}
    }
  };
  NS.ui = API;

  tpl = loadTemplates();
  if (tpl) {
    try { bodyId = sessionStorage.getItem(BODY_KEY) || null; } catch (e) {}
  }

  hookToggle();
  startObserver();
  sync();
  log('installed v1' + (tpl ? ' (cached templates)' : ''));
  return 'ok:ui';
})();
