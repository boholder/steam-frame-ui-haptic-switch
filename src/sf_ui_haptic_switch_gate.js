/* sf_ui_haptic_switch_gate.js — injected into every page on both endpoints.
 *
 *  :8080 Steam client UI:
 *    SteamClient.OpenVR.TriggerOverlayHapticEffect(effect, controllerOrHand)
 *    -> the effect id is arguments[0]
 *  :8087 SteamVR Dashboard:
 *    VRHTML.VROverlay.TriggerOverlayHapticEffect(overlayHandle, effect)
 *    -> the effect id is arguments[1]
 *
 * EOverlayHapticEffect (read from the live Dashboard bundle):
 *   0 None        no effect
 *   1 ButtonEnter cursor enters a button
 *   2 ButtonLeave cursor leaves a button
 *   3 Snap        slider / list snaps to a detent
 *   4 Sliding     continuous slider drag
 *   5 SlidingEdge slider reaches its end stop
 *
 * Only 1/2 (button enter/leave) are dropped; 3/4/5 (slider and snap feedback)
 * are deliberately left alone. The flag is read on every call, so toggling it
 * takes effect immediately. Re-injection is idempotent, and a slow timer
 * re-checks a bridge that was absent at inject time or got replaced later.
 *
 * The daemon owns window.__SF_UI_HAPTIC_SWITCH__.build; this script only ever
 * sets .gate. Plain ES5, no build step.
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
  var PROP = 'TriggerOverlayHapticEffect';

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

  /* Steam's bridge takes the effect first; VRHTML's takes the overlay handle
   * first. One parametrised wrapper covers both. */
  function wrap(owner, effectArg) {
    if (!owner || typeof owner[PROP] !== 'function') return false;

    /* Already wrapped by us: take ownership so a later teardown restores it.
     * The install timer runs every second, so do not append a duplicate
     * record on every tick. */
    var current = owner[PROP];
    if (current.__sfUiHapticWrapped && current.__sfUiHapticOriginal) {
      for (var i = 0; i < wrappers.length; i++) {
        if (wrappers[i].patched === current) return true;
      }
      wrappers.push({
        owner: owner,
        prop: PROP,
        patched: current,
        original: current.__sfUiHapticOriginal
      });
      return true;
    }

    var patched = function () {
      if (shouldDrop(arguments[effectArg])) return undefined;
      return Reflect.apply(current, this, arguments);
    };
    patched.__sfUiHapticWrapped = true;
    patched.__sfUiHapticOriginal = current;

    try { owner[PROP] = patched; } catch (e) { return false; }
    if (owner[PROP] !== patched) return false;

    wrappers.push({ owner: owner, prop: PROP, patched: patched, original: current });
    return true;
  }

  function install() {
    if (stopped) return false;
    var specs = [
      { owner: window.SteamClient && window.SteamClient.OpenVR, effectArg: 0 },
      { owner: window.VRHTML && window.VRHTML.VROverlay, effectArg: 1 }
    ];
    var installed = 0;
    for (var i = 0; i < specs.length; i++) {
      if (wrap(specs[i].owner, specs[i].effectArg)) installed += 1;
    }
    return installed > 0;
  }

  function restore() {
    while (wrappers.length) {
      var w = wrappers.pop();
      try {
        if (w.owner[w.prop] === w.patched) w.owner[w.prop] = w.original;
      } catch (e) {}
    }
  }

  function teardown() {
    /* A queued interval tick must not re-wrap after NS.gate is cleared. */
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
