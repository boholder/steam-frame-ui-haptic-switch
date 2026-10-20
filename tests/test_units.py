import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.path.join(ROOT, "src")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class UnitFileTests(unittest.TestCase):
    def test_service_is_a_restarting_daemon(self):
        t = read(os.path.join(SRC, "sf-ui-haptic-switch.service"))
        unit, _, service = t.partition("[Service]")
        self.assertIn("Type=simple", service)
        self.assertIn("User=steamos", service)
        self.assertIn("INSTALLATION_PATH/src/sf_ui_haptic_switch_daemon.py", service)
        self.assertIn("Restart=always", service)
        self.assertIn("RestartSec=5", service)
        self.assertNotIn("Type=oneshot", service)
        self.assertIn("WantedBy=multi-user.target", t)
        # Start rate limiting belongs in [Unit] and must trip before the
        # window slides past the retries: RestartSec * Burst < Interval.
        self.assertIn("StartLimitIntervalSec=60", unit)
        self.assertIn("StartLimitBurst=3", unit)
        self.assertNotIn("StartLimit", service)

    def test_timer_is_gone(self):
        self.assertFalse(os.path.exists(os.path.join(SRC, "sf-ui-haptic-switch.timer")))

    def test_old_injector_is_gone(self):
        self.assertFalse(os.path.exists(os.path.join(SRC, "sf_ui_haptic_switch_inject.py")))
        self.assertFalse(os.path.exists(os.path.join(HERE, "test_inject.py")))

    def test_old_daemon_unit_is_gone(self):
        self.assertFalse(os.path.exists(os.path.join(SRC, "sf-haptic-switchd.service")))


class ScriptContractTests(unittest.TestCase):
    def test_install_installs_the_daemon_and_enables_the_vr_debugger(self):
        t = read(os.path.join(ROOT, "install.sh"))
        self.assertIn("sf_ui_haptic_switch_daemon.py", t)
        self.assertNotIn("sf_ui_haptic_switch_cdp.py", t)
        self.assertIn("installation_lib.sh", t)
        self.assertIn("systemctl enable", t)
        self.assertNotIn("$TIMER", t)
        self.assertIn("sf_ui_haptic_switch_settings.py", t)
        self.assertIn("--enable", t)
        self.assertNotIn("--teardown", t)
        helper = read(os.path.join(SRC, "sf_ui_haptic_switch_settings.py"))
        self.assertIn("VRWebHelper", helper)
        self.assertIn("DebuggerEnabled", helper)
        lib = read(os.path.join(ROOT, "installation_lib.sh"))
        self.assertIn("steamvr.vrsettings", lib)

    def test_install_does_not_copy_the_repo(self):
        t = read(os.path.join(ROOT, "install.sh"))
        self.assertNotIn("copy sf_", t)
        self.assertNotIn('install -d "$DEST"', t)
        self.assertIn('install -m 0644 "$SRC/$SERVICE"', t)

    def test_install_replaces_the_service_placeholder(self):
        t = read(os.path.join(ROOT, "install.sh"))
        self.assertIn("INSTALLATION_PATH", t)
        self.assertNotIn("ESCAPED_DEST", t)
        self.assertIn('sed -i "s|INSTALLATION_PATH|$DEST|g"', t)

    def test_scripts_use_the_src_directory_without_a_fallback(self):
        for name in ("install.sh", "uninstall.sh"):
            t = read(os.path.join(ROOT, name))
            self.assertIn('SRC="$HERE/src"', t)
            self.assertNotIn('[ -d "$SRC" ]', t)
            self.assertNotIn('|| SRC="$HERE"', t)

    def test_uninstall_aborts_while_the_service_is_active(self):
        t = read(os.path.join(ROOT, "uninstall.sh"))
        self.assertIn('systemctl is-active --quiet "$SERVICE"', t)
        self.assertIn('die "$SERVICE is still running', t)
        self.assertLess(t.index('systemctl is-active --quiet "$SERVICE"'), t.index('rm -f "$UNIT_DIR/$SERVICE"'))
        self.assertIn("pgrep -f '[v]rserver'", t)
        self.assertLess(t.index("pgrep -f '[v]rserver'"), t.index("--disable"))

    def test_uninstall_does_not_delete_the_checkout(self):
        t = read(os.path.join(ROOT, "uninstall.sh"))
        self.assertNotIn('rm -rf "$DEST"', t)
        self.assertIn("$SRC/sf_ui_haptic_switch_settings.py", t)

    def test_lib_uses_the_checkout_as_dest(self):
        lib = read(os.path.join(ROOT, "installation_lib.sh"))
        self.assertIn('DEST="$HERE"', lib)

    def test_uninstall_removes_the_daemon_and_the_debugger_key(self):
        t = read(os.path.join(ROOT, "uninstall.sh"))
        self.assertNotIn("$TIMER", t)
        self.assertIn("systemctl", t)
        self.assertIn("installation_lib.sh", t)
        self.assertIn("sf_ui_haptic_switch_settings.py", t)
        self.assertIn("--disable", t)
        self.assertNotIn("--teardown", t)
        lib = read(os.path.join(ROOT, "installation_lib.sh"))
        self.assertIn("steamvr.vrsettings", lib)
        self.assertNotIn("TIMER=", lib)
        self.assertIn("STATE_DIR", lib)

    def test_cdp_transport_lives_in_the_daemon(self):
        t = read(os.path.join(SRC, "sf_ui_haptic_switch_daemon.py"))
        for symbol in ("class WSError", "class WebSocket", "def http_get_json", "class Endpoint"):
            self.assertIn(symbol, t)
        self.assertFalse(os.path.exists(os.path.join(SRC, "sf_ui_haptic_switch_cdp.py")))


if __name__ == "__main__":
    unittest.main()
