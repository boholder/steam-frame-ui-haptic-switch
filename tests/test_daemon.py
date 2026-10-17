import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import sf_ui_haptic_switch_daemon as d  # noqa: E402


class FlagTests(unittest.TestCase):
    def test_missing_or_junk_means_on(self):
        for value in (None, "", "junk", "true", "1", "yes"):
            self.assertTrue(d.normalize_enabled(value), value)

    def test_explicit_off_values(self):
        for value in ("0", "false"):
            self.assertFalse(d.normalize_enabled(value), value)

    def test_storage_value(self):
        self.assertEqual(d.storage_value(True), "1")
        self.assertEqual(d.storage_value(False), "0")


class PayloadTests(unittest.TestCase):
    def test_hello(self):
        self.assertEqual(d.parse_binding_payload('{"c":"hello"}'), {"kind": "hello"})

    def test_haptic_boolean(self):
        self.assertEqual(
            d.parse_binding_payload('{"c":"haptic","v":false}'),
            {"kind": "haptic", "enabled": False})
        self.assertEqual(
            d.parse_binding_payload('{"c":"haptic","v":true}'),
            {"kind": "haptic", "enabled": True})

    def test_haptic_without_value_is_a_hint_only(self):
        self.assertEqual(
            d.parse_binding_payload('{"c":"haptic"}'),
            {"kind": "haptic", "enabled": None})

    def test_rejects_bad_or_unknown_payloads(self):
        for raw in ("", "not json", "[]", "null", '{"c":"other"}', '{"x":1}'):
            self.assertIsNone(d.parse_binding_payload(raw), raw)


class TargetTests(unittest.TestCase):
    def test_page_filter(self):
        self.assertTrue(d.is_page_target({"type": "page"}))
        self.assertFalse(d.is_page_target({"type": "other"}))
        self.assertFalse(d.is_page_target({}))

    def test_ui_target(self):
        self.assertTrue(d.is_ui_target(
            "SteamVR - valve.steam.gamepadui.barpopup.1", "about:blank"))
        self.assertTrue(d.is_ui_target(
            "", "about:blank?vrOverlayKey=valve.steam.gamepadui.barpopup.1"))
        self.assertFalse(d.is_ui_target("SharedJSContext", "https://steamloopback.host"))

    def test_scripts_for(self):
        self.assertEqual(
            d.scripts_for("SteamVR - valve.steam.gamepadui.barpopup.1", ""),
            ["gate", "ui"])
        self.assertEqual(d.scripts_for("systemui", ""), ["gate"])


class InjectionExprTests(unittest.TestCase):
    def test_marker_is_stable_and_short(self):
        self.assertEqual(d.build_marker("x"), d.build_marker("x"))
        self.assertNotEqual(d.build_marker("x"), d.build_marker("y"))
        self.assertEqual(len(d.build_marker("x")), 12)

    def test_probe_reads_the_slot(self):
        e = d.probe_expr("gate")
        self.assertIn(d.GLOBAL, e)
        self.assertIn('"gate"', e)

    def test_inject_stamps_the_slot(self):
        e = d.inject_expr("doThing();", "ui", "deadbeef")
        self.assertTrue(e.startswith("doThing();"))
        self.assertIn('"ui"', e)
        self.assertIn('"deadbeef"', e)

    def test_teardown_touches_both_slots(self):
        e = d.teardown_expr()
        self.assertIn(d.GLOBAL, e)
        self.assertIn(".ui.teardown", e)
        self.assertIn(".gate.teardown", e)


class StateFileTests(unittest.TestCase):
    def test_missing_file_defaults_to_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(d.load_state(os.path.join(tmp, "missing.json")))

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "state.json")
            d.save_state(path, False)
            self.assertFalse(d.load_state(path))
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"enabled": False})
            self.assertFalse([n for n in os.listdir(os.path.dirname(path))
                              if n.endswith(".tmp")])

    def test_corrupt_file_defaults_to_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            self.assertTrue(d.load_state(path))

    @unittest.skipIf(os.name == "nt", "POSIX mode bits")
    def test_file_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            d.save_state(path, True)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class CliTests(unittest.TestCase):
    def test_defaults(self):
        a = d.parse_args([])
        self.assertEqual(a.steamwebhelper_port, 8080)
        self.assertEqual(a.vrwebhelper_port, 8087)
        self.assertFalse(a.status)
        self.assertFalse(a.verbose)

    def test_overrides(self):
        a = d.parse_args([
            "--steamwebhelper-port", "9000",
            "--vrwebhelper-port", "9001",
            "--status", "--verbose",
        ])
        self.assertEqual(a.steamwebhelper_port, 9000)
        self.assertEqual(a.vrwebhelper_port, 9001)
        self.assertTrue(a.status)
        self.assertTrue(a.verbose)


if __name__ == "__main__":
    unittest.main()
