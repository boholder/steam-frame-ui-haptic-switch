import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.path.join(ROOT, "src")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class UnitFileTests(unittest.TestCase):
    def test_service_is_oneshot_run_as_steamos(self):
        t = read(os.path.join(SRC, "sf-ui-haptic-switch.service"))
        self.assertIn("Type=oneshot", t)
        self.assertIn("User=steamos", t)
        self.assertIn("sf_ui_haptic_switch_inject.py", t)
        self.assertNotIn("Restart=", t)  # oneshot units must not set Restart

    def test_timer_rearms_and_binds_the_service(self):
        t = read(os.path.join(SRC, "sf-ui-haptic-switch.timer"))
        self.assertIn("OnBootSec=", t)
        self.assertIn("OnUnitInactiveSec=3s", t)
        self.assertIn("Unit=sf-ui-haptic-switch.service", t)
        self.assertIn("WantedBy=timers.target", t)

    def test_old_daemon_unit_is_gone(self):
        self.assertFalse(os.path.exists(os.path.join(SRC, "sf-haptic-switchd.service")))


class ScriptContractTests(unittest.TestCase):
    def test_install_enables_the_timer(self):
        t = read(os.path.join(ROOT, "install.sh"))
        self.assertIn("sf-ui-haptic-switch.timer", t)
        self.assertIn("enable --now", t)
        self.assertIn("sf_ui_haptic_switch_inject.py", t)

    def test_uninstall_disables_and_tears_down(self):
        t = read(os.path.join(ROOT, "uninstall.sh"))
        self.assertIn("sf-ui-haptic-switch.timer", t)
        self.assertIn("disable --now", t)
        self.assertIn("--teardown", t)


if __name__ == "__main__":
    unittest.main()
