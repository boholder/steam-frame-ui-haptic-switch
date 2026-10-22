import io
import json
import os
import queue
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import sf_ui_haptic_switch_daemon as d


class FlagTests(unittest.TestCase):
    def test_missing_or_junk_means_on(self):
        for value in (None, "", "junk", "true", "1", "yes"):
            self.assertTrue(d.storage_value_to_enabled(value), value)

    def test_explicit_off_values(self):
        for value in ("0", "false"):
            self.assertFalse(d.storage_value_to_enabled(value), value)


class PayloadTests(unittest.TestCase):
    def test_hello(self):
        self.assertEqual(d.parse_binding_message('{"c":"hello"}'), {"kind": "hello"})

    def test_haptic_boolean(self):
        self.assertEqual(d.parse_binding_message('{"c":"haptic","v":false}'), {"kind": "haptic", "enabled": False})
        self.assertEqual(d.parse_binding_message('{"c":"haptic","v":true}'), {"kind": "haptic", "enabled": True})

    def test_haptic_without_value_is_a_hint_only(self):
        self.assertEqual(d.parse_binding_message('{"c":"haptic"}'), {"kind": "haptic", "enabled": None})

    def test_rejects_bad_or_unknown_payloads(self):
        for raw in ("", "not json", "[]", "null", '{"c":"other"}', '{"x":1}'):
            self.assertIsNone(d.parse_binding_message(raw), raw)


class TargetTests(unittest.TestCase):
    def test_page_filter(self):
        self.assertTrue(d.is_page_type_target({"type": "page"}))
        self.assertFalse(d.is_page_type_target({"type": "other"}))
        self.assertFalse(d.is_page_type_target({}))

    def test_ui_target(self):
        self.assertTrue(d.is_quick_access_menu("SteamVR - valve.steam.gamepadui.barpopup.1", "about:blank"))
        self.assertTrue(d.is_quick_access_menu("", "about:blank?vrOverlayKey=valve.steam.gamepadui.barpopup.1"))
        self.assertFalse(d.is_quick_access_menu("SharedJSContext", "https://steamloopback.host"))

    def test_scripts_for(self):
        self.assertEqual(d.script_names_for_target("SteamVR - valve.steam.gamepadui.barpopup.1", "", d.KIND_STEAM), ["gate", "ui"])
        self.assertEqual(d.script_names_for_target("systemui", "", d.KIND_STEAM), ["gate"])

    def test_vr_never_gets_the_qam_ui(self):
        self.assertEqual(d.script_names_for_target("valve.steam.gamepadui.barpopup", "", d.KIND_VR), ["gate"])
        self.assertEqual(d.script_names_for_target("valve.steam.gamepadui.barpopup", "", d.KIND_STEAM), ["gate", "ui"])


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
        ws = object.__new__(d.WebSocket)
        ws._closed = False
        ws._send_lock = threading.Lock()
        ws.sock = FakeSocket()

        ws.close()

        self.assertTrue(ws.sock.sent)
        self.assertEqual(ws.sock.sent[0][0], 0x88)  # FIN + close opcode
        self.assertTrue(ws._closed)
        self.assertTrue(ws.sock.shutdown_called)
        self.assertTrue(ws.sock.closed)

    def test_close_skips_the_frame_when_the_send_lock_is_busy(self):
        ws = object.__new__(d.WebSocket)
        ws._closed = False
        ws._send_lock = threading.Lock()
        ws.sock = FakeSocket()
        ws._send_lock.acquire()
        try:
            started = time.monotonic()
            ws.close()
            elapsed = time.monotonic() - started
        finally:
            ws._send_lock.release()

        self.assertLess(elapsed, 1.0)
        self.assertEqual(ws.sock.sent, [])
        self.assertTrue(ws._closed)
        self.assertTrue(ws.sock.shutdown_called)
        self.assertTrue(ws.sock.closed)


class FakeWS:
    def __init__(self):
        self.inbox = queue.Queue()
        self.sent = []
        self.block = False
        self.block_event = threading.Event()
        self.closed = False
        self.auto_respond = True

    def send_text(self, text):
        self.sent.append(text)
        if self.block:
            self.block_event.wait(5)
        if self.closed:
            raise d.WSError("closed")
        msg = json.loads(text)
        if self.auto_respond and "id" in msg:
            self.inbox.put(json.dumps({"id": msg["id"], "result": {}}))

    def recv_text(self):
        item = self.inbox.get()
        if item is None:
            raise d.WSError("closed")
        return item

    def close(self):
        self.closed = True
        self.block_event.set()
        self.inbox.put(None)


class EndpointSenderTests(unittest.TestCase):
    def test_a_stuck_send_times_out_and_closes_the_endpoint(self):
        ws = FakeWS()
        with (
            mock.patch.object(d, "http_get_json", return_value={"webSocketDebuggerUrl": "ws://127.0.0.1:1/devtools/browser/x"}),
            mock.patch.object(d, "WebSocket", return_value=ws),
        ):
            endpoint = d.Endpoint(9999, queue.Queue(), d.KIND_STEAM)
            endpoint.connect()
            sender = endpoint.sender
            ws.block = True

            with self.assertRaises(d.WSError):
                endpoint.send_command("Runtime.evaluate", timeout=0.1)

            self.assertFalse(endpoint.alive)
            self.assertIsNone(endpoint.ws)
            self.assertFalse(sender.is_alive())

    def test_reconnect_after_a_timeout_uses_a_fresh_sender_queue(self):
        first = FakeWS()
        second = FakeWS()
        with (
            mock.patch.object(d, "http_get_json", return_value={"webSocketDebuggerUrl": "ws://127.0.0.1:1/devtools/browser/x"}),
            mock.patch.object(d, "WebSocket", side_effect=[first, second]),
        ):
            endpoint = d.Endpoint(9999, queue.Queue(), d.KIND_STEAM)
            endpoint.connect()
            first.block = True

            with self.assertRaises(d.WSError):
                endpoint.send_command("Runtime.evaluate", timeout=0.1)
            self.assertFalse(endpoint.alive)

            endpoint.connect()
            self.assertEqual(endpoint.send_command("Runtime.evaluate", timeout=1.0), {})
            self.assertIsNotNone(endpoint.sender)
            self.assertTrue(endpoint.sender.is_alive())


class CommandLogTests(unittest.TestCase):
    def test_short_command(self):
        self.assertEqual(d.describe_cdp_command("Runtime.enable", {}), "command Runtime.enable {}")

    def test_long_command_is_truncated(self):
        text = d.describe_cdp_command("Runtime.evaluate", {"expression": "x" * 500})
        self.assertIn("Runtime.evaluate", text)
        self.assertLessEqual(len(text), 230)
        self.assertTrue(text.endswith("..."))


class HandshakeTests(unittest.TestCase):
    def test_three_retries_after_the_first_send(self):
        h = d.Handshake()
        for expected in (False, False, False, True):
            h.sent(0)
            self.assertEqual(h.exhausted(), expected)

    def test_due_and_verified(self):
        h = d.Handshake()
        self.assertFalse(h.due(0))
        h.sent(0)
        self.assertFalse(h.due(0))
        self.assertTrue(h.due(d.HANDSHAKE_TIMEOUT))
        h.ok = True
        self.assertFalse(h.due(d.HANDSHAKE_TIMEOUT * 2))

    def test_a_fresh_instance_is_unscheduled(self):
        h = d.Handshake()
        self.assertIsNone(h.deadline)
        h.sent(100)
        h.ok = True
        h = d.Handshake()
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


class SessionHandshakeTests(unittest.TestCase):
    class RecordingEndpoint:
        kind = d.KIND_STEAM

        def __init__(self, fail=False):
            self.calls = []
            self.fail = fail

        def send_command(self, method, params=None, session=None, timeout=10.0):
            self.calls.append((method, dict(params or {}), session, timeout))
            if self.fail:
                raise d.WSError("boom")
            return {}

        def evaluate(self, expr, session=None, timeout=10.0):
            self.calls.append(("evaluate", expr, session, timeout))
            if self.fail:
                raise d.WSError("boom")
            return None

    def _session(self, fail=False):
        endpoint = self.RecordingEndpoint(fail)
        return d.Session(endpoint, "sid", "tid", "systemui", ""), endpoint

    def test_start_handshake_resets_the_state_and_sends(self):
        session, endpoint = self._session()
        session.handshake.attempts = 9

        session.start_handshake()

        self.assertEqual(session.handshake.attempts, 1)
        self.assertIsNotNone(session.handshake.deadline)
        self.assertEqual(endpoint.calls[0][0], "Runtime.addBinding")
        self.assertEqual(endpoint.calls[0][1], {"name": d.BINDING})
        self.assertEqual(endpoint.calls[0][2], "sid")

    def test_send_handshake_evaluates_the_hello_binding(self):
        session, endpoint = self._session()

        session.send_handshake()

        self.assertEqual(session.handshake.attempts, 1)
        self.assertEqual(endpoint.calls[1][0], "evaluate")
        self.assertIn(d.BINDING, endpoint.calls[1][1])

    def test_send_handshake_swallows_errors_but_counts_the_attempt(self):
        session, _ = self._session(fail=True)

        session.send_handshake()

        self.assertEqual(session.handshake.attempts, 1)


class SessionHandshakeRetryTests(unittest.TestCase):
    UI_TITLE = "SteamVR - valve.steam.gamepadui.barpopup.1"

    def _session(self, title="systemui", attempts=0, deadline=None):
        endpoint = SessionHandshakeTests.RecordingEndpoint()
        session = d.Session(endpoint, "sid", "tid", title, "")
        session.handshake.attempts = attempts
        session.handshake.deadline = deadline
        return session

    def test_non_ui_sessions_never_retry(self):
        session = self._session(deadline=100.0)

        self.assertFalse(session.retry_handshake_if_due(100.0))
        self.assertEqual(session.handshake.attempts, 0)

    def test_nothing_happens_before_the_deadline(self):
        session = self._session(title=self.UI_TITLE, deadline=200.0)

        self.assertFalse(session.retry_handshake_if_due(100.0))
        self.assertEqual(session.handshake.attempts, 0)

    def test_a_due_handshake_sends_another_attempt(self):
        session = self._session(title=self.UI_TITLE, attempts=1, deadline=100.0)

        self.assertFalse(session.retry_handshake_if_due(100.0))
        self.assertEqual(session.handshake.attempts, 2)

    def test_an_exhausted_handshake_asks_for_a_restart(self):
        session = self._session(title=self.UI_TITLE, attempts=d.HANDSHAKE_MAX_SENDS, deadline=100.0)

        self.assertTrue(session.retry_handshake_if_due(100.0))
        self.assertEqual(session.handshake.attempts, d.HANDSHAKE_MAX_SENDS)


class SessionStorageTests(unittest.TestCase):
    def _session(self, kind=d.KIND_STEAM, url="https://steamloopback.host/page"):
        port = d.DEFAULT_VR_PORT if kind == d.KIND_VR else d.DEFAULT_STEAM_PORT
        return d.Session(d.Endpoint(port, queue.Queue(), kind), "sid", "tid", "title", url)

    def test_a_data_page_has_an_opaque_origin(self):
        session = self._session(url="data:text/html,x")

        self.assertFalse(session.shares_local_storage)
        self.assertFalse(session.can_read_local_storage_flag)

    def test_a_steam_page_shares_the_storage_and_can_be_read(self):
        session = self._session()

        self.assertTrue(session.shares_local_storage)
        self.assertTrue(session.can_read_local_storage_flag)

    def test_a_vr_page_shares_the_storage_but_is_not_read(self):
        session = self._session(kind=d.KIND_VR)

        self.assertTrue(session.shares_local_storage)
        self.assertFalse(session.can_read_local_storage_flag)


class AttachFailureTests(unittest.TestCase):
    class FakeEndpoint:
        kind = d.KIND_STEAM
        port = 8080

        def __init__(self):
            self.calls = []
            self.fail_on = None

        def send_command(self, method, params=None, session=None, timeout=10.0):
            self.calls.append((method, dict(params or {}), session))
            if method == self.fail_on:
                raise d.WSError("boom")
            if method == "Target.attachToTarget":
                return {"sessionId": "sid-1"}
            return {}

    def test_attach_failure_detaches_the_browser_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = d.Daemon(8080, 8087, {"gate": "g", "ui": "u"}, os.path.join(tmp, "state.json"))
            endpoint = self.FakeEndpoint()
            endpoint.fail_on = "Runtime.enable"
            info = {
                "targetId": "tid-1",
                "type": "page",
                "title": "systemui",
                "url": "",
            }

            daemon._attach_to_page_target(endpoint, info)

            methods = [c[0] for c in endpoint.calls]
            self.assertIn("Target.detachFromTarget", methods)
            detach = next(c for c in endpoint.calls if c[0] == "Target.detachFromTarget")
            self.assertEqual(detach[1], {"sessionId": "sid-1"})
            self.assertEqual(daemon.sessions, {})
            self.assertEqual(daemon.target_sessions, {})


class BackoffEndpoint(d.Endpoint):
    def __init__(self):
        super().__init__(8080, queue.Queue(), d.KIND_STEAM)
        self.connect_calls = 0
        self.close_calls = 0

    def connect(self):
        self.connect_calls += 1

    def close(self):
        self.close_calls += 1


class EndpointBackoffTests(unittest.TestCase):
    def _daemon(self, tmp):
        return d.Daemon(8080, 8087, {"gate": "g", "ui": "u"}, os.path.join(tmp, "state.json"))

    def test_a_never_connected_endpoint_retries_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp)
            endpoint = BackoffEndpoint()

            daemon._ensure_endpoint_connected(endpoint, 100.0)

            self.assertEqual(endpoint.connect_calls, 1)
            self.assertEqual(endpoint.next_connect_at, 0.0)

    def test_a_dropped_connection_backs_off_before_reconnecting(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp)
            endpoint = BackoffEndpoint()
            endpoint.mark_connected()

            daemon._ensure_endpoint_connected(endpoint, 100.0)
            self.assertEqual(endpoint.next_connect_at, 100.0 + d.RETRY_SECONDS)
            self.assertEqual(endpoint.connect_calls, 0)

            daemon._ensure_endpoint_connected(endpoint, 100.0 + d.RETRY_SECONDS - 0.1)
            self.assertEqual(endpoint.connect_calls, 0)

            daemon._ensure_endpoint_connected(endpoint, 100.0 + d.RETRY_SECONDS)
            self.assertEqual(endpoint.connect_calls, 1)

    def test_a_failed_attempt_backs_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp)
            endpoint = BackoffEndpoint()
            endpoint.connect = mock.Mock(side_effect=OSError("not ready"))

            daemon._ensure_endpoint_connected(endpoint, 100.0)

            endpoint.connect.assert_called_once()
            self.assertEqual(endpoint.close_calls, 1)
            self.assertEqual(endpoint.next_connect_at, 100.0 + d.RETRY_SECONDS)

    def test_dropping_a_dead_endpoint_also_drops_its_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp)
            endpoint = BackoffEndpoint()
            endpoint.mark_connected()
            daemon.sessions["sid"] = d.Session(endpoint, "sid", "tid", "systemui", "")

            daemon._ensure_endpoint_connected(endpoint, 100.0)

            self.assertEqual(daemon.sessions, {})


class StateWriteTests(unittest.TestCase):
    def _daemon(self, tmp, enabled=True):
        daemon = d.Daemon(8080, 8087, {"gate": "g", "ui": "u"}, os.path.join(tmp, "state.json"))
        daemon.enabled = enabled
        daemon.pushed = []
        daemon._push_state_to_all_vr_sessions = lambda: daemon.pushed.append(daemon.enabled)
        return daemon

    def test_write_failure_reverts(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp, enabled=True)
            with mock.patch.object(d, "write_persisted_enabled", side_effect=OSError("disk full")):
                daemon._apply_state_and_push_to_vr(False, "switch")

            self.assertTrue(daemon.enabled)
            self.assertEqual(daemon.pushed, [True])

    def test_write_success_keeps_the_new_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp, enabled=True)

            daemon._apply_state_and_push_to_vr(False, "switch")

            self.assertFalse(daemon.enabled)
            self.assertEqual(daemon.pushed, [False])
            self.assertFalse(d.read_persisted_enabled(daemon.state_file))

    def test_an_unchanged_value_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp, enabled=False)
            with mock.patch.object(d, "write_persisted_enabled", side_effect=OSError("should not write")):
                daemon._apply_state_and_push_to_vr(False, "sync")

            self.assertFalse(daemon.enabled)


class StateSyncTriggerTests(unittest.TestCase):
    class UiPage(d.Session):
        def __init__(self):
            super().__init__(d.Endpoint(8080, queue.Queue(), d.KIND_STEAM), "sid", "tid", "valve.steam.gamepadui.barpopup", "")
            self.evaluations = []

        def send_command(self, method, params=None, timeout=10.0):
            return {}

        def evaluate(self, expr, timeout=10.0):
            self.evaluations.append(expr)
            return "0"

    def _daemon(self, tmp):
        return d.Daemon(8080, 8087, {"gate": "g", "ui": "u"}, os.path.join(tmp, "state.json"))

    def _flag_reads(self, page):
        return [expr for expr in page.evaluations if "localStorage.getItem" in expr]

    def test_initializing_a_page_does_not_read_the_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp)
            page = self.UiPage()
            daemon.sessions[page.session_id] = page

            daemon._initialize_session(page)

            self.assertEqual(self._flag_reads(page), [])

    def test_an_execution_context_event_reads_the_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp)
            page = self.UiPage()
            daemon.sessions[page.session_id] = page

            daemon._dispatch_cdp_event(
                page.endpoint,
                "Runtime.executionContextCreated",
                {"context": {"auxData": {"isDefault": True}}},
                page.session_id,
            )

            self.assertEqual(len(self._flag_reads(page)), 1)


class PushStateTests(unittest.TestCase):
    def test_push_writes_a_boolean_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = d.Daemon(8080, 8087, {"gate": "g", "ui": "u"}, os.path.join(tmp, "state.json"))
            for enabled, literal in ((True, "true"), (False, "false")):
                daemon.enabled = enabled
                session = RecordingSession([], {})

                daemon._push_state_to(session)

                expressions = [expr for expr, _ in session.evaluations]
                self.assertEqual(expressions, [f'localStorage.setItem("{d.KEY}", {literal})'])


class PushPerEndpointTests(unittest.TestCase):
    class Page(d.Session):
        def __init__(self, endpoint, url="https://steamloopback.host/dashboard"):
            super().__init__(endpoint, "sid", "tid", "page", url)
            self.evaluations = []

        def evaluate(self, expr, timeout=10.0):
            self.evaluations.append(expr)

    def _daemon(self, tmp):
        return d.Daemon(8080, 8087, {"gate": "g", "ui": "u"}, os.path.join(tmp, "state.json"))

    def test_the_value_is_written_once_per_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp)
            vr = daemon.endpoints[1]
            first = self.Page(vr)
            second = self.Page(vr)
            daemon.sessions.update({"first": first, "second": second})

            daemon._push_state_to_all_vr_sessions()

            self.assertEqual(len(first.evaluations), 1)
            self.assertEqual(second.evaluations, [])

    def test_pages_with_an_opaque_origin_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp)
            vr = daemon.endpoints[1]
            opaque = self.Page(vr, url="data:text/html,x")
            shared = self.Page(vr)
            daemon.sessions.update({"opaque": opaque, "shared": shared})

            daemon._push_state_to_all_vr_sessions()

            self.assertEqual(opaque.evaluations, [])
            self.assertEqual(len(shared.evaluations), 1)

    def test_steam_sessions_are_never_pushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp)
            steam_page = self.Page(daemon.endpoints[0])
            daemon.sessions["steam"] = steam_page

            daemon._push_state_to_all_vr_sessions()

            self.assertEqual(steam_page.evaluations, [])


class StatusTests(unittest.TestCase):
    def test_ui_is_only_reported_for_the_steam_endpoint(self):
        targets = [
            {"type": "page", "title": "valve.steam.gamepadui.barpopup", "url": ""},
            {"type": "page", "title": "systemui", "url": ""},
            {"type": "other", "title": "ignored", "url": ""},
        ]

        output = io.StringIO()
        with (
            mock.patch.object(d, "http_get_json", return_value=targets),
            mock.patch.object(d, "read_persisted_enabled", return_value=True),
            mock.patch("sys.stdout", output),
        ):
            result = d.status("unused-state.json", 8080, 8087)

        self.assertEqual(result, 0)
        data = json.loads(output.getvalue())

        self.assertEqual(
            data["steam"],
            {"port": 8080, "reachable": True, "pages": 2, "ui": True},
        )
        self.assertEqual(
            data["vr"],
            {"port": 8087, "reachable": True, "pages": 2, "ui": False},
        )


class InjectionExprTests(unittest.TestCase):
    def test_marker_is_stable_and_short(self):
        self.assertEqual(d.build_script_marker("x"), d.build_script_marker("x"))
        self.assertNotEqual(d.build_script_marker("x"), d.build_script_marker("y"))
        self.assertEqual(len(d.build_script_marker("x")), 12)

    def test_probe_reads_the_script(self):
        e = d.build_script_marker_probe("gate")
        self.assertIn(d.GLOBAL, e)
        self.assertIn('"gate"', e)

    def test_inject_stamps_the_script(self):
        e = d.build_script_injection("doThing();", "ui", "deadbeef")
        self.assertTrue(e.startswith("doThing();"))
        self.assertIn('"ui"', e)
        self.assertIn('"deadbeef"', e)

    def test_teardown_touches_both_scripts(self):
        e = d.JS_TEAR_DOWN_EXPR
        self.assertIn(d.GLOBAL, e)
        self.assertIn(".ui.teardown", e)
        self.assertIn(".gate.teardown", e)

    def test_teardown_isolates_each_cleanup(self):
        e = d.JS_TEAR_DOWN_EXPR
        self.assertGreaterEqual(e.count("try{"), 3)
        self.assertGreaterEqual(e.count("catch(e)"), 3)


class RecordingSession:
    def __init__(self, script_names, probe_values):
        self.script_names = script_names
        self.title = "page"
        self.probe_values = probe_values
        self.evaluations = []

    def evaluate(self, expr, timeout=10.0):
        self.evaluations.append((expr, timeout))
        return self.probe_values.get(expr)


class PrecomputedScriptTests(unittest.TestCase):
    def _daemon(self, tmp):
        sources = {d.SCRIPT_GATE: "gate();", d.SCRIPT_UI: "ui();"}
        return d.Daemon(8080, 8087, sources, os.path.join(tmp, "state.json"))

    def test_precomputed_strings_match_the_pure_builders(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp)
            for name in (d.SCRIPT_GATE, d.SCRIPT_UI):
                self.assertEqual(daemon.marker_probe_exprs[name], d.build_script_marker_probe(name))
                self.assertEqual(
                    daemon.injections[name],
                    d.build_script_injection(daemon.js_sources[name], name, daemon.expected_markers[name]),
                )

    def test_new_document_script_joins_precomputed_injections_in_order(self):
        injections = {d.SCRIPT_GATE: "gate();", d.SCRIPT_UI: "ui();"}
        joined = d.build_new_document_script(injections, [d.SCRIPT_UI, "unknown", d.SCRIPT_GATE])
        self.assertEqual(joined, "ui();\n;\ngate();")

    def test_inject_skips_a_page_that_already_runs_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp)
            probe = daemon.marker_probe_exprs[d.SCRIPT_GATE]
            session = RecordingSession([d.SCRIPT_GATE], {probe: daemon.expected_markers[d.SCRIPT_GATE]})

            daemon._inject_into_current_document(session)

            self.assertEqual(session.evaluations, [(probe, 10.0)])

    def test_inject_sends_the_precomputed_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = self._daemon(tmp)
            probe = daemon.marker_probe_exprs[d.SCRIPT_GATE]
            session = RecordingSession([d.SCRIPT_GATE], {probe: ""})

            daemon._inject_into_current_document(session)

            self.assertEqual(
                session.evaluations,
                [(probe, 10.0), (daemon.injections[d.SCRIPT_GATE], 20.0)],
            )


class StateFileTests(unittest.TestCase):
    def test_missing_file_defaults_to_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(d.read_persisted_enabled(os.path.join(tmp, "missing.json")))

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "state.json")
            d.write_persisted_enabled(path, False)
            self.assertFalse(d.read_persisted_enabled(path))
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"enabled": False})
            self.assertFalse([n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")])

    def test_corrupt_file_defaults_to_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            self.assertTrue(d.read_persisted_enabled(path))

    @unittest.skipIf(os.name == "nt", "POSIX mode bits")
    def test_file_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            d.write_persisted_enabled(path, True)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class CliTests(unittest.TestCase):
    def test_defaults(self):
        a = d.parse_args([])
        self.assertEqual(a.steamwebhelper_port, 8080)
        self.assertEqual(a.vrwebhelper_port, 8087)
        self.assertFalse(a.status)
        self.assertFalse(a.verbose)

    def test_overrides(self):
        a = d.parse_args(
            [
                "--steamwebhelper-port",
                "9000",
                "--vrwebhelper-port",
                "9001",
                "--status",
                "--verbose",
            ]
        )
        self.assertEqual(a.steamwebhelper_port, 9000)
        self.assertEqual(a.vrwebhelper_port, 9001)
        self.assertTrue(a.status)
        self.assertTrue(a.verbose)


if __name__ == "__main__":
    unittest.main()
