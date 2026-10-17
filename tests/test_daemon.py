import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import sf_ui_haptic_switch_cdp as cdp  # noqa: E402
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
            d.scripts_for("SteamVR - valve.steam.gamepadui.barpopup.1", "",
                          d.KIND_STEAM),
            ["gate", "ui"])
        self.assertEqual(
            d.scripts_for("systemui", "", d.KIND_STEAM), ["gate"])

    def test_vr_never_gets_the_qam_ui(self):
        self.assertEqual(
            d.scripts_for("valve.steam.gamepadui.barpopup", "", d.KIND_VR),
            ["gate"])
        self.assertEqual(
            d.scripts_for("valve.steam.gamepadui.barpopup", "", d.KIND_STEAM),
            ["gate", "ui"])


class FakeSocket:
    def __init__(self):
        self.sent = []
        self.shutdown_called = False
        self.closed = False

    def sendall(self, data):
        self.sent.append(data)

    def shutdown(self, how):
        self.shutdown_called = True

    def close(self):
        self.closed = True


class WebSocketCloseTests(unittest.TestCase):
    def test_close_sends_the_close_frame_before_marking_closed(self):
        ws = object.__new__(cdp.WebSocket)
        ws._closed = False
        ws._send_lock = threading.Lock()
        ws.sock = FakeSocket()

        ws.close()

        self.assertTrue(ws.sock.sent)
        self.assertEqual(ws.sock.sent[0][0], 0x88)  # FIN + close opcode
        self.assertTrue(ws._closed)
        self.assertTrue(ws.sock.shutdown_called)
        self.assertTrue(ws.sock.closed)


class CommandLogTests(unittest.TestCase):
    def test_short_command(self):
        self.assertEqual(
            cdp.describe_command("Runtime.enable", {}),
            "command Runtime.enable {}")

    def test_long_command_is_truncated(self):
        text = cdp.describe_command(
            "Runtime.evaluate", {"expression": "x" * 500})
        self.assertIn("Runtime.evaluate", text)
        self.assertLessEqual(len(text), 230)
        self.assertTrue(text.endswith("..."))


class HandshakeTests(unittest.TestCase):
    def test_three_retries_after_the_first_send(self):
        h = d.Handshake()
        h.start()
        for expected in (False, False, False, True):
            h.sent(0)
            self.assertEqual(h.exhausted(), expected)

    def test_due_and_verified(self):
        h = d.Handshake()
        h.start()
        self.assertFalse(h.due(0))
        h.sent(0)
        self.assertFalse(h.due(0))
        self.assertTrue(h.due(d.HANDSHAKE_TIMEOUT))
        h.ok = True
        self.assertFalse(h.due(d.HANDSHAKE_TIMEOUT * 2))

    def test_start_uses_an_explicit_unscheduled_state(self):
        h = d.Handshake()
        self.assertIsNone(h.deadline)
        h.sent(100)
        h.ok = True
        h.start()
        self.assertFalse(h.ok)
        self.assertEqual(h.attempts, 0)
        self.assertIsNone(h.deadline)
        self.assertFalse(h.due(1000))


class SessionTests(unittest.TestCase):
    def test_session_has_no_unused_script_identifier(self):
        class FakeEndpoint:
            kind = d.KIND_STEAM

        session = d.Session(FakeEndpoint(), "sid", "tid", "title", "url")
        self.assertFalse(hasattr(session, "addscript_id"))


class AttachFailureTests(unittest.TestCase):
    class FakeEndpoint:
        kind = d.KIND_STEAM
        port = 8080

        def __init__(self):
            self.calls = []
            self.fail_on = None

        def call(self, method, params=None, session=None, timeout=10.0):
            self.calls.append((method, dict(params or {}), session))
            if method == self.fail_on:
                raise cdp.WSError("boom")
            if method == "Target.attachToTarget":
                return {"sessionId": "sid-1"}
            return {}

    def test_attach_failure_detaches_the_browser_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = d.Daemon(
                8080, 8087, {"gate": "g", "ui": "u"},
                os.path.join(tmp, "state.json"))
            endpoint = self.FakeEndpoint()
            endpoint.fail_on = "Runtime.enable"
            info = {
                "targetId": "tid-1", "type": "page",
                "title": "systemui", "url": "",
            }

            daemon._attach(endpoint, info)

            methods = [c[0] for c in endpoint.calls]
            self.assertIn("Target.detachFromTarget", methods)
            detach = [c for c in endpoint.calls
                      if c[0] == "Target.detachFromTarget"][0]
            self.assertEqual(detach[1], {"sessionId": "sid-1"})
            self.assertEqual(daemon.sessions, {})
            self.assertEqual(daemon.target_sessions, {})


class StateWriteTests(unittest.TestCase):
    def _daemon(self, tmp, enabled=True):
        daemon = d.Daemon(
            8080, 8087, {"gate": "g", "ui": "u"},
            os.path.join(tmp, "state.json"))
        daemon.enabled = enabled
        daemon.pushed = []
        daemon._push_vr = lambda: daemon.pushed.append(daemon.enabled)
        return daemon

    def test_write_failure_reverts_and_marks_not_synced(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp, enabled=True)
            with mock.patch.object(
                    d, "save_state", side_effect=OSError("disk full")):
                daemon._apply_enabled(False, "switch")

            self.assertTrue(daemon.enabled)
            self.assertFalse(daemon.steam_synced)
            self.assertEqual(daemon.pushed, [True])

    def test_write_success_keeps_the_new_value_and_marks_synced(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp, enabled=True)

            daemon._apply_enabled(False, "switch")

            self.assertFalse(daemon.enabled)
            self.assertTrue(daemon.steam_synced)
            self.assertEqual(daemon.pushed, [False])
            self.assertFalse(d.load_state(daemon.state_file))

    def test_unchanged_value_stays_synced(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp, enabled=False)
            with mock.patch.object(
                    d, "save_state", side_effect=OSError("should not write")):
                daemon._apply_enabled(False, "sync")

            self.assertFalse(daemon.enabled)
            self.assertTrue(daemon.steam_synced)


class StatusTests(unittest.TestCase):
    def test_ui_is_only_reported_for_the_steam_endpoint(self):
        targets = [
            {"type": "page", "title": "valve.steam.gamepadui.barpopup", "url": ""},
            {"type": "page", "title": "systemui", "url": ""},
            {"type": "other", "title": "ignored", "url": ""},
        ]
        self.assertEqual(
            d.summarize_targets(targets, d.KIND_STEAM),
            {"pages": 2, "ui": True})
        self.assertEqual(
            d.summarize_targets(targets, d.KIND_VR),
            {"pages": 2, "ui": False})


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
