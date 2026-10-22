import os
import re
import unittest

try:
    import esprima  # optional dev-only dependency (no JS engine on this box)
except Exception:  # pragma: no cover
    esprima = None

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
GATE = os.path.join(SRC, "sf_ui_haptic_switch_gate.js")
UI = os.path.join(SRC, "sf_ui_haptic_switch_ui.js")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def function_body(src, name):
    """Return the balanced body of `function name(...) { ... }`."""
    match = re.search(r"function %s\([^)]*\) \{" % re.escape(name), src)
    if match is None:
        return None
    depth = 1
    index = match.end()
    while index < len(src):
        char = src[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return src[match.end() : index]
        index += 1
    return None


class ParseTests(unittest.TestCase):
    @unittest.skipIf(esprima is None, "esprima not installed")
    def test_gate_parses(self):
        esprima.parseScript(read(GATE))

    @unittest.skipIf(esprima is None, "esprima not installed")
    def test_ui_parses(self):
        esprima.parseScript(read(UI))


class ContractTests(unittest.TestCase):
    def test_gate_wraps_the_steam_client_bridge(self):
        t = read(GATE)
        self.assertIn("SteamClient", t)
        self.assertIn("TriggerOverlayHapticEffect", t)
        self.assertIn("EFFECT_BUTTON_ENTER", t)
        self.assertIn("EFFECT_BUTTON_LEAVE", t)
        self.assertIn("sf_ui_haptic_switch", t)  # localStorage key
        self.assertNotIn("setItem", t)  # gate only reads the flag

    def test_gate_wraps_the_vrhtml_bridge(self):
        t = read(GATE)
        self.assertIn("VRHTML", t)
        self.assertIn("VROverlay", t)
        # Steam's bridge takes the effect first; VRHTML's takes it second.
        self.assertIn("effectArg: 0", t)
        self.assertIn("effectArg: 1", t)
        self.assertIn("arguments[effectArg]", t)

    def test_gate_documents_the_effect_enum(self):
        t = read(GATE)
        for line in (
            "1 ButtonEnter",
            "2 ButtonLeave",
            "3 Snap",
            "4 Sliding",
            "5 SlidingEdge",
        ):
            self.assertIn(line, t)
        self.assertIn("Only 1/2 (button enter/leave) are dropped", t)

    def test_ui_has_deep_internal_modules(self):
        t = read(UI)
        for name in ("createToggleLook", "createTemplateStore", "createQamBody", "createGesture", "createUi"):
            self.assertIn("function %s(" % name, t)
        self.assertIn("createUi(window).start()", t)
        self.assertIn("fromTemplate", t)

    def test_ui_owns_the_localstorage_key(self):
        t = read(UI)
        self.assertIn("sf_ui_haptic_switch", t)
        self.assertIn("Controller UI Haptic", t)
        self.assertIn("localStorage.setItem", t)
        self.assertIn("localStorage.setItem(KEY, on ? 'true' : 'false')", t)

    def test_ui_notifies_the_daemon(self):
        t = read(UI)
        self.assertIn("__sfUiHapticSwitchSend", t)
        self.assertIn("JSON.stringify", t)
        self.assertIn("haptic", t)

    def test_ui_removes_the_keydown_listener_on_teardown(self):
        t = read(UI)
        self.assertIn("removeEventListener('keydown'", t)

    def test_ui_does_not_hide_state_on_a_dom_node(self):
        t = read(UI)
        self.assertNotIn("__cb", t)
        self.assertNotIn("ui.row", t)

    def test_ui_has_one_toggle_finish_path(self):
        t = read(UI)
        self.assertIn("function finishToggle", t)
        self.assertIn("return finishToggle(", t)

    def test_ui_has_one_body_id_loader(self):
        t = read(UI)
        self.assertIn("function loadBodyId", t)
        self.assertEqual(t.count("sessionStorage.getItem(BODY_KEY)"), 1)

    def test_ui_tears_down_its_section_wrapper(self):
        t = read(UI)
        self.assertIn("data-sf-ui-haptic-section", t)
        self.assertIn("function removeSections", t)
        self.assertGreaterEqual(t.count("removeSections();"), 2)

    def test_ui_blocks_queued_sync_after_teardown(self):
        t = read(UI)
        self.assertIn("tornDown", t)
        self.assertIn("clearTimeout(scheduleTimer)", t)
        body = function_body(t, "schedule")
        self.assertIsNotNone(body)
        self.assertIn("if (tornDown) return;", body)
        self.assertIn("sync()", body)

    def test_ui_strips_ids_from_cloned_rows(self):
        t = read(UI)
        self.assertIn("function stripIds", t)
        self.assertIn("querySelectorAll('[id]')", t)
        self.assertIn("removeAttribute('id')", t)
        for name in ("toggleRow", "render"):
            body = function_body(t, name)
            self.assertIsNotNone(body, name)
            self.assertIn("stripIds(row)", body, name)

    def test_ui_adds_the_native_separator(self):
        t = read(UI)
        self.assertIn("separatorHTML", t)
        self.assertIn("function makeSeparator", t)
        self.assertIn("function ensureSeparator", t)
        self.assertIn("nextElementSibling", t)
        self.assertNotIn("previousElementSibling", t)
        self.assertIn("setAttribute('role', 'separator')", t)
        self.assertIn("sf_ui_haptic_switch_tpl_v2", t)
        body = function_body(t, "render")
        self.assertIsNotNone(body)
        self.assertIn("ensureSeparator()", body)

    def test_ui_matches_the_native_row_gap(self):
        t = read(UI)
        self.assertIn("prevSection", t)
        self.assertIn("getComputedStyle(prevSection)", t)
        self.assertIn("marginBottom", t)
        self.assertIn("style.marginTop", t)

    def test_ui_harvests_only_a_single_toggle_class(self):
        t = read(UI)
        self.assertIn("diff.length === 1", t)
        self.assertIn("extra = diff[0]", t)
        self.assertNotIn("diff.join(' ')", t)

    def test_ui_skips_redundant_toggle_repaints(self):
        t = read(UI)
        for name in ("classAdapter", "htmlAdapter"):
            body = function_body(t, name)
            self.assertIsNotNone(body, name)
            self.assertIn("getAttribute(STATE_ATTR)", body, name)

    def test_ui_throttles_before_marking_a_pointerdown(self):
        t = read(UI)
        body = function_body(t, "flip")
        self.assertIsNotNone(body)
        pointer = re.search(r"if \(ev\.type === 'pointerdown'\) \{(.*?)\}", body, re.S)
        self.assertIsNotNone(pointer)
        self.assertNotIn("preventDefault", pointer.group(1))
        prevent = body.index("if (ev.type !== 'pointerdown' && ev.preventDefault) ev.preventDefault();")
        self.assertLess(body.index("ev.button !== 0"), prevent)
        self.assertLess(prevent, body.index("lastFlip < 250"))
        self.assertGreater(body.index("sawDown = now"), body.index("lastFlip < 250"))

    def test_gate_stops_after_teardown(self):
        t = read(GATE)
        self.assertIn("var stopped = false;", t)
        self.assertIn("if (stopped) return false;", t)
        m = re.search(r"function teardown\(\) \{(.*?)\n  \}", t, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertLess(body.index("stopped = true;"), body.index("clearInterval(timer)"))

    def test_gate_never_uses_the_binding(self):
        self.assertNotIn("__sfHapticSend", read(GATE))
        self.assertNotIn("__sfUiHapticSwitchSend", read(GATE))

    def test_scripts_do_not_replace_the_injector_namespace(self):
        for path in (GATE, UI):
            t = read(path)
            self.assertNotIn("window.__SF_UI_HAPTIC_SWITCH__ = {", t)


if __name__ == "__main__":
    unittest.main()
