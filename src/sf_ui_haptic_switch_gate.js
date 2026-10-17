/* sf_ui_haptic_switch_gate.js — injected into every page on both endpoints.
 *
 *  :8080 Steam client UI:
 *    SteamClient.OpenVR.TriggerOverlayHapticEffect(effect, controllerOrHand)
 *    -> the effect id is arguments[0]
 *
 *  :8087 SteamVR Dashboard:
 *    VRHTML.VROverlay.TriggerOverlayHapticEffect(overlayHandle, effect)
 *    -> the effect id is arguments[1]
 *
 * EOverlayHapticEffect (read from the live Dashboard bundle):
 *   0 None         no effect
 *   1 ButtonEnter  cursor enters a button / clickable row
 *   2 ButtonLeave  cursor leaves a button / clickable row
 *   3 Snap         slider / list snaps to a detent
 *   4 Sliding      continuous slider drag
 *   5 SlidingEdge  slider reaches its end stop
 *
 * 中文对照：0 None 无效果；1 ButtonEnter 光标进入按钮；2 ButtonLeave 光标离开按钮；
 * 3 Snap 滑杆/列表吸附；4 Sliding 拖动滑杆；5 SlidingEdge 拖到端点。
 * 本插件只丢弃 1/2（按钮 hover），保留 3/4/5（滑杆/吸附）。
 *
 * This gate only drops 1/2 (button hover). 3/4/5 (slider and snap feedback)
 * are deliberately left alone: the product promise is to remove the
 * cursor-lands-on-a-button tick, not every UI haptic.
 *
 * The flag is read on every call, so toggling it takes effect immediately with
 * no re-injection. Idempotent: a re-injection tears the previous instance down
 * first. Self-heals: either bridge may be absent at inject time or be replaced
 * later, so a slow timer re-checks it.
 *
 * The daemon owns window.__SF_UI_HAPTIC_SWITCH__.build; this script only ever
 * sets .gate.
 *
 * Plain ES5, no build step.
 */
(function () {
  'use strict';

  var NS = window.__SF_UI_HAPTIC_SWITCH__ = window.__SF_UI_HAPTIC_SWITCH__ || {};
  var prev = NS.gate;
  if (prev && prev.teardown) { try { prev.teardown(); } catch (e) {} }

  var EFFECT_BUTTON_ENTER = 1;
  var EFFECT_BUTTON_LEAVE = 2;
  var KEY = 'sf_ui_haptic_switch';
  var RETRY_MS = 1000;

  var timer = null;
  var stopped = false;
  var wrappers = [];   /* { owner, prop, patched, original } */

  function log() {
    try {
      console.log.apply(console, ['[sf-ui-haptic-gate]'].concat(
        Array.prototype.slice.call(arguments)));
    } catch (e) {}
  }

  /* Off only when explicitly "0"/"false"; missing or anything else = on, so a
   * storage hiccup never silences the stock behaviour by accident. */
  function enabled() {
    try {
      var v = localStorage.getItem(KEY);
      return !(v === '0' || v === 'false');
    } catch (e) { return true; }
  }

  function shouldDrop(effect) {
    return !enabled() &&
      typeof effect === 'number' &&
      (effect === EFFECT_BUTTON_ENTER || effect === EFFECT_BUTTON_LEAVE);
  }

  function remember(owner, prop, patched, original) {
    wrappers.push({
      owner: owner,
      prop: prop,
      patched: patched,
      original: original
    });
  }

  function wrap(owner, prop, makePatched) {
    if (!owner || typeof owner[prop] !== 'function') return false;

    var current = owner[prop];

    /* Already wrapped by us: take ownership so a later teardown restores it.
     * The install timer runs every second, so do not append a duplicate
     * record on every tick. */
    if (current.__sfUiHapticWrapped && current.__sfUiHapticOriginal) {
      for (var i = 0; i < wrappers.length; i++) {
        if (wrappers[i].patched === current) return true;
      }
      remember(owner, prop, current, current.__sfUiHapticOriginal);
      return true;
    }

    var patched = makePatched(current);
    patched.__sfUiHapticWrapped = true;
    patched.__sfUiHapticOriginal = current;

    try { owner[prop] = patched; } catch (e) { return false; }
    if (owner[prop] !== patched) return false;

    remember(owner, prop, patched, current);
    return true;
  }

  /* Steam's bridge takes the effect first; VRHTML's takes the overlay handle
   * first. One parametrised wrapper covers both. */
  function bridgeSpecs() {
    var specs = [];
    try {
      var sov = window.SteamClient && window.SteamClient.OpenVR;
      if (sov && typeof sov.TriggerOverlayHapticEffect === 'function') {
        specs.push({ owner: sov, effectArg: 0 });
      }
    } catch (e) {}
    try {
      var vov = window.VRHTML && window.VRHTML.VROverlay;
      if (vov && typeof vov.TriggerOverlayHapticEffect === 'function') {
        specs.push({ owner: vov, effectArg: 1 });
      }
    } catch (e) {}
    return specs;
  }

  function patcher(effectArg) {
    return function (original) {
      return function () {
        if (shouldDrop(arguments[effectArg])) return undefined;
        return Reflect.apply(original, this, arguments);
      };
    };
  }

  function install() {
    if (stopped) return false;
    var specs = bridgeSpecs();
    var installed = 0;
    for (var i = 0; i < specs.length; i++) {
      if (wrap(specs[i].owner, 'TriggerOverlayHapticEffect',
               patcher(specs[i].effectArg))) {
        installed += 1;
      }
    }
    return installed > 0;
  }

  function restore() {
    for (var i = wrappers.length - 1; i >= 0; i--) {
      var w = wrappers[i];
      try {
        if (w.owner[w.prop] === w.patched) {
          w.owner[w.prop] = w.original;
        }
      } catch (e) {}
    }
    wrappers = [];
  }

  function teardown() {
    /* Same idea as the UI's tornDown: 已排队的 interval 回调不能在 teardown 后
     * 重新包装 bridge，此时 NS.gate 已为 null，包装也无法再被清理。 */
    stopped = true;
    if (timer) { clearInterval(timer); timer = null; }
    restore();
    try { if (NS.gate === API) NS.gate = null; } catch (e) {}
  }

  var API = {
    v: 2,
    enabled: enabled,
    install: install,
    teardown: teardown
  };
  NS.gate = API;

  install();
  timer = setInterval(install, RETRY_MS);
  log('installed');
  return 'ok:gate';
})();
