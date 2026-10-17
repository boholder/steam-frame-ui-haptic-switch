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
        self.assertNotIn("setItem", t)           # gate only reads the flag

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
        self.assertIn("only drops 1/2", t)
        self.assertIn("只丢弃 1/2", t)

    def test_ui_owns_the_localstorage_key(self):
        t = read(UI)
        self.assertIn("sf_ui_haptic_switch", t)
        self.assertIn("Controller UI Haptic", t)
        self.assertIn("localStorage.setItem", t)

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
        self.assertRegex(
            t,
            re.compile(
                r"function schedule\(\).*?if \(tornDown\) return;.*?sync\(\)",
                re.S,
            ),
        )

    def test_ui_strips_ids_from_cloned_rows(self):
        t = read(UI)
        self.assertIn("function stripIds", t)
        self.assertIn("querySelectorAll('[id]')", t)
        self.assertIn("removeAttribute('id')", t)
        self.assertGreaterEqual(t.count("stripIds("), 3)

    def test_ui_throttles_before_marking_a_pointerdown(self):
        t = read(UI)
        m = re.search(r"function flip\(ev\) \{(.*?)\n    \}", t, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("if (ev.type === 'pointerdown')", body)
        self.assertIn(
            "if (ev.type !== 'pointerdown' && ev.preventDefault) "
            "ev.preventDefault();", body)
        self.assertGreater(
            body.index("sawDown = now"), body.index("lastFlip < 250"))

    def test_gate_stops_after_teardown(self):
        t = read(GATE)
        self.assertIn("var stopped = false;", t)
        self.assertIn("if (stopped) return false;", t)
        self.assertIn("stopped = true;", t)
        self.assertIn("已排队", t)

    def test_gate_never_uses_the_binding(self):
        self.assertNotIn("__sfHapticSend", read(GATE))
        self.assertNotIn("__sfUiHapticSwitchSend", read(GATE))

    def test_scripts_do_not_replace_the_injector_namespace(self):
        for path in (GATE, UI):
            t = read(path)
            self.assertNotIn("window.__SF_UI_HAPTIC_SWITCH__ = {", t)


if __name__ == "__main__":
    unittest.main()
