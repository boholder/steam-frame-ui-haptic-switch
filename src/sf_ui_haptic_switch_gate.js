/* sf_ui_haptic_switch_gate.js — injected into every Steam client UI page (:8080).
 *
 * Wraps `SteamClient.OpenVR.TriggerOverlayHapticEffect(effect, controller)` and,
 * while the `sf_ui_haptic_switch` localStorage flag is off, swallows the button
 * hover effects ButtonEnter(1) / ButtonLeave(2). The flag is read on every call,
 * so toggling it takes effect immediately with no re-injection.
 *
 * Idempotent: a re-injection tears the previous instance down first. Self-heals:
 * the bridge may be absent at inject time or be replaced later, so a slow timer
 * re-checks it. The injector owns `window.__SF_UI_HAPTIC_SWITCH__.build`; this
 * script only ever sets `.gate`.
 *
 * Plain ES5, no build step.
 */
(function () {
  'use strict';

  var NS = window.__SF_UI_HAPTIC_SWITCH__ = window.__SF_UI_HAPTIC_SWITCH__ || {};
  var prev = NS.gate;
  if (prev && prev.teardown) { try { prev.teardown(); } catch (e) {} }

  /* Steam's bridge signature takes the effect id FIRST (unlike VRHTML's,
   * which takes the overlay handle first). */
  var EFFECT_BUTTON_ENTER = 1;
  var EFFECT_BUTTON_LEAVE = 2;
  var KEY = 'sf_ui_haptic_switch';
  var RETRY_MS = 1000;

  var timer = null;

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

  function bridge() {
    try {
      var sc = window.SteamClient;
      var ov = sc && sc.OpenVR;
      return (ov && typeof ov.TriggerOverlayHapticEffect === 'function') ? ov : null;
    } catch (e) { return null; }
  }

  function install() {
    var ov = bridge();
    if (!ov) return false;
    if (ov.TriggerOverlayHapticEffect.__sfUiHapticWrapped) return true;
    var original = ov.TriggerOverlayHapticEffect;
    var patched = function (effect) {
      if (!enabled() &&
          (effect === EFFECT_BUTTON_ENTER || effect === EFFECT_BUTTON_LEAVE)) {
        return undefined;
      }
      return original.apply(this, arguments);
    };
    patched.__sfUiHapticWrapped = true;
    patched.__sfUiHapticOriginal = original;
    ov.TriggerOverlayHapticEffect = patched;
    return ov.TriggerOverlayHapticEffect === patched;
  }

  function restore() {
    var ov = bridge();
    if (!ov) return;
    var fn = ov.TriggerOverlayHapticEffect;
    if (fn && fn.__sfUiHapticWrapped && fn.__sfUiHapticOriginal) {
      ov.TriggerOverlayHapticEffect = fn.__sfUiHapticOriginal;
    }
  }

  function teardown() {
    if (timer) { clearInterval(timer); timer = null; }
    restore();
    try { if (NS.gate === API) NS.gate = null; } catch (e) {}
  }

  var API = {
    v: 1,
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
