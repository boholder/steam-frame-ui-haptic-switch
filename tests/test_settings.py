import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import sf_ui_haptic_switch_settings as s

SAMPLE = {
    "GpuSpeed": {"gpuSpeed0": 1},
    "LastKnown": {"HMDModel": "Steam Frame"},
    "camera": {"roomViewStyle": 4},
    "VRWebHelper": {"KeepMe": 1},
}


def write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class EnableTests(unittest.TestCase):
    def test_sets_key_and_keeps_everything_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(os.path.join(tmp, "steamvr.vrsettings"), SAMPLE)
            s.enable(path)
            data = load(path)
            self.assertTrue(data["VRWebHelper"]["DebuggerEnabled"])
            self.assertEqual(data["VRWebHelper"]["KeepMe"], 1)
            self.assertEqual(data["GpuSpeed"], SAMPLE["GpuSpeed"])
            self.assertEqual(data["camera"], SAMPLE["camera"])

    def test_makes_a_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(os.path.join(tmp, "steamvr.vrsettings"), SAMPLE)
            s.enable(path)
            with open(path + ".bak", encoding="utf-8") as f:
                self.assertEqual(json.load(f), SAMPLE)

    def test_writes_steamvr_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(os.path.join(tmp, "steamvr.vrsettings"), {})
            s.enable(path)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn('"VRWebHelper" : {', text)
            self.assertIn('"DebuggerEnabled" : true', text)
            self.assertIn('\n   "VRWebHelper" : {', text)
            self.assertFalse([n for n in os.listdir(tmp) if n.endswith(".tmp")])

    def test_creates_a_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config", "steamvr.vrsettings")
            s.enable(path)
            self.assertTrue(load(path)["VRWebHelper"]["DebuggerEnabled"])

    def test_rejects_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "steamvr.vrsettings")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            with self.assertRaises(json.JSONDecodeError):
                s.enable(path)
            self.assertTrue(os.path.exists(path + ".bak"))


class DisableTests(unittest.TestCase):
    def test_removes_key_and_empty_section_but_keeps_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(os.path.join(tmp, "steamvr.vrsettings"), SAMPLE)
            s.enable(path)
            s.disable(path)
            data = load(path)
            self.assertNotIn("DebuggerEnabled", data.get("VRWebHelper", {}))
            self.assertEqual(data["VRWebHelper"], {"KeepMe": 1})
            self.assertTrue(os.path.exists(path + ".bak"))

    def test_removes_the_section_when_it_becomes_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(os.path.join(tmp, "steamvr.vrsettings"), {})
            s.enable(path)
            s.disable(path)
            self.assertNotIn("VRWebHelper", load(path))

    def test_missing_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(s.disable(os.path.join(tmp, "nope.vrsettings")))

    def test_leaves_the_file_untouched_when_the_key_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(os.path.join(tmp, "steamvr.vrsettings"), {"GpuSpeed": {"gpuSpeed0": 1}})
            with open(path, encoding="utf-8") as f:
                before = f.read()
            s.disable(path)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), before)


if __name__ == "__main__":
    unittest.main()
