import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "device"))

import sf_ui_haptic_switch_inject as inj  # noqa: E402


def _page(title, url, ws="ws://t"):
    return {"type": "page", "title": title, "url": url, "webSocketDebuggerUrl": ws}


class PlanTests(unittest.TestCase):
    def test_filters_non_page_and_missing_ws(self):
        targets = [
            _page("a", "b", "ws://a"),
            {"type": "other", "title": "z", "url": "w", "webSocketDebuggerUrl": "ws://b"},
            {"type": "page", "title": "n", "url": "m"},  # no webSocketDebuggerUrl
        ]
        self.assertEqual(inj.build_plan(targets), [("ws://a", ["gate"])])

    def test_barpopup_gets_gate_and_ui(self):
        targets = [_page("QuickAccess", "…valve.steam.gamepadui.barpopup.1", "ws://p")]
        self.assertEqual(inj.build_plan(targets), [("ws://p", ["gate", "ui"])])

    def test_plain_page_gets_gate_only(self):
        targets = [_page("Steam", "…sharedjscontext…", "ws://s")]
        self.assertEqual(inj.build_plan(targets), [("ws://s", ["gate"])])


class ExprTests(unittest.TestCase):
    def test_probe_expr_reads_slot(self):
        e = inj.probe_expr("gate")
        self.assertIn("__SF_UI_HAPTIC_SWITCH__", e)
        self.assertIn('"gate"', e)

    def test_inject_expr_keeps_source_and_stamps_slot(self):
        code = inj.inject_expr("doThing();", "ui", "deadbeef")
        self.assertTrue(code.startswith("doThing();"))
        self.assertIn('"ui"', code)
        self.assertIn('"deadbeef"', code)

    def test_build_marker_is_stable_and_short(self):
        self.assertEqual(inj.build_marker("x"), inj.build_marker("x"))
        self.assertNotEqual(inj.build_marker("x"), inj.build_marker("y"))
        self.assertEqual(len(inj.build_marker("x")), 12)


class _FakeTransport:
    def __init__(self, probe_value=""):
        self.probe_value = probe_value
        self.calls = []

    def evaluate(self, ws_url, expr, timeout=10.0):
        self.calls.append((ws_url, expr))
        return self.probe_value


class RunTests(unittest.TestCase):
    def test_injects_when_stale(self):
        t = _FakeTransport("")
        inj.run(t, [("ws://p", ["gate"])], {"gate": "GATE"})
        exprs = [e for _, e in t.calls]
        self.assertEqual(len(exprs), 2)  # probe + inject
        self.assertEqual(exprs[0], inj.probe_expr("gate"))
        self.assertIn(inj.build_marker("GATE"), exprs[1])

    def test_skips_when_fresh(self):
        build = inj.build_marker("GATE")
        t = _FakeTransport(build)
        inj.run(t, [("ws://p", ["gate"])], {"gate": "GATE"})
        self.assertEqual(len(t.calls), 1)  # probe only

    def test_barpopup_injects_both_scripts(self):
        t = _FakeTransport("")
        inj.run(t, [("ws://p", ["gate", "ui"])], {"gate": "G", "ui": "U"})
        exprs = [e for _, e in t.calls]
        self.assertEqual(len(exprs), 4)  # (probe+inject) x 2
        self.assertIn(inj.build_marker("U"), exprs[3])

    def test_target_error_is_isolated(self):
        class Boom(_FakeTransport):
            def evaluate(self, ws_url, expr, timeout=10.0):
                raise RuntimeError("socket died")

        # must not raise, and must return 0
        self.assertEqual(inj.run(Boom(), [("ws://p", ["gate"])], {"gate": "G"}), 0)


if __name__ == "__main__":
    unittest.main()
