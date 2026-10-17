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

  function wrap(owner, prop, makePatched) {
    if (!owner || typeof owner[prop] !== 'function') return false;

    var current = owner[prop];

    /* Already wrapped by us: take ownership so a later teardown restores it. */
    if (current.__sfUiHapticWrapped && current.__sfUiHapticOriginal) {
      wrappers.push({
        owner: owner,
        prop: prop,
        patched: current,
        original: current.__sfUiHapticOriginal
      });
      return true;
    }

    var patched = makePatched(current);
    patched.__sfUiHapticWrapped = true;
    patched.__sfUiHapticOriginal = current;

    try { owner[prop] = patched; } catch (e) { return false; }
    if (owner[prop] !== patched) return false;

    wrappers.push({
      owner: owner,
      prop: prop,
      patched: patched,
      original: current
    });
    return true;
  }

  function install() {
    var installed = 0;

    /* Steam client UI (:8080): effect is the first argument. */
    try {
      var sc = window.SteamClient;
      var sov = sc && sc.OpenVR;
      if (sov && typeof sov.TriggerOverlayHapticEffect === 'function') {
        if (wrap(sov, 'TriggerOverlayHapticEffect', function (original) {
          return function (effect) {
            if (shouldDrop(effect)) return undefined;
            return original.apply(this, arguments);
          };
        })) installed += 1;
      }
    } catch (e) {}

    /* SteamVR Dashboard (:8087): effect is the second argument. */
    try {
      var vh = window.VRHTML;
      var vov = vh && vh.VROverlay;
      if (vov && typeof vov.TriggerOverlayHapticEffect === 'function') {
        if (wrap(vov, 'TriggerOverlayHapticEffect', function (original) {
          return function (handle, effect) {
            if (shouldDrop(effect)) return undefined;
            return original.apply(this, arguments);
          };
        })) installed += 1;
      }
    } catch (e) {}

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
