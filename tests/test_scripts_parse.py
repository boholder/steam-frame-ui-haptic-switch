import os
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
        self.assertIn("arguments", t)

    def test_gate_documents_the_effect_enum(self):
        t = read(GATE)
        for name in ("ButtonEnter", "ButtonLeave", "Snap", "Sliding", "SlidingEdge"):
            self.assertIn(name, t)
        self.assertIn("only", t.lower())

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

    def test_gate_never_uses_the_binding(self):
        self.assertNotIn("__sfHapticSend", read(GATE))
        self.assertNotIn("__sfUiHapticSwitchSend", read(GATE))

    def test_scripts_do_not_replace_the_injector_namespace(self):
        for path in (GATE, UI):
            t = read(path)
            self.assertNotIn("window.__SF_UI_HAPTIC_SWITCH__ = {", t)


if __name__ == "__main__":
    unittest.main()
