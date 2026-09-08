"""Tests for the Claude Code integration switch.

    venv\\Scripts\\python.exe -m unittest discover -s tests -v

This module edits a file that belongs to another application and already has
other tools' hooks in it. Everything worth testing here is about not damaging
those: merge rather than replace, remove only our own, and never write over a
file we could not parse.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import claudehook  # noqa: E402

# A hook belonging to something else, of the shape really found in the wild.
SOMEONE_ELSE = {
    "type": "command",
    "command": 'node "C:\\Users\\RyRy\\.claude\\claude-notifications\\hook.cjs"',
    "async": True,
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / ".claude"
        self.home.mkdir(parents=True)
        self.path = self.home / "settings.json"
        patch = mock.patch.object(claudehook, "config_path", lambda: self.path)
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))


class Status(Base):
    def test_no_claude_code_at_all(self):
        with mock.patch.object(claudehook, "config_path",
                               lambda: Path(self.tmp.name) / "nope" / "settings.json"):
            self.assertEqual(claudehook.status(), "no-claude-code")

    def test_installed_but_no_settings_file(self):
        self.assertEqual(claudehook.status(), "off")

    def test_off_when_only_other_hooks_are_there(self):
        self.write({"hooks": {"Stop": [{"hooks": [SOMEONE_ELSE]}]}})
        self.assertEqual(claudehook.status(), "off")

    def test_on_when_ours_is_there(self):
        self.write({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "python.exe",
             "args": ["C:\\x\\claude_notify.py"]}]}]}})
        self.assertEqual(claudehook.status(), "on")

    def test_on_for_the_packaged_form_too(self):
        self.write({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "cufflink.exe",
             "args": ["--claude-hook"]}]}]}})
        self.assertEqual(claudehook.status(), "on")

    def test_unparseable_settings_are_reported_not_guessed(self):
        self.path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(claudehook.status(), "unreadable")


class Enable(Base):
    def test_turning_on_from_nothing(self):
        ok, _ = claudehook.enable()
        self.assertTrue(ok)
        self.assertEqual(claudehook.status(), "on")
        self.assertEqual(set(self.read()["hooks"]), set(claudehook.EVENTS))

    def test_other_hooks_survive(self):
        """The thing that would be unforgivable to get wrong."""
        self.write({"hooks": {"Stop": [{"hooks": [SOMEONE_ELSE]}]},
                    "permissions": {"allow": ["Bash"]}})
        claudehook.enable()
        after = self.read()
        stop = [h for g in after["hooks"]["Stop"] for h in g["hooks"]]
        self.assertIn(SOMEONE_ELSE, stop)
        self.assertEqual(after["permissions"], {"allow": ["Bash"]},
                         "unrelated settings must be left alone")

    def test_turning_on_twice_does_not_double_up(self):
        claudehook.enable()
        ok, message = claudehook.enable()
        self.assertTrue(ok)
        self.assertEqual(message, "Already on.")
        ours = [h for g in self.read()["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(len(ours), 1)

    def test_a_backup_is_left_behind(self):
        self.write({"hooks": {}})
        claudehook.enable()
        self.assertTrue(list(self.home.glob("settings.json.*.bak")),
                        "should have backed the file up before writing")

    def test_an_unreadable_file_is_never_overwritten(self):
        self.path.write_text("{ not json", encoding="utf-8")
        ok, message = claudehook.enable()
        self.assertFalse(ok)
        self.assertIn("could not be read", message)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{ not json")

    def test_no_temporary_file_is_left_lying_around(self):
        claudehook.enable()
        self.assertEqual(list(self.home.glob("*cufflink-tmp*")), [])


class Disable(Base):
    def test_turning_off_removes_ours(self):
        claudehook.enable()
        ok, _ = claudehook.disable()
        self.assertTrue(ok)
        self.assertEqual(claudehook.status(), "off")

    def test_turning_off_keeps_everyone_elses(self):
        self.write({"hooks": {"Stop": [{"hooks": [SOMEONE_ELSE]}]}})
        claudehook.enable()
        claudehook.disable()
        stop = [h for g in self.read()["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(stop, [SOMEONE_ELSE])

    def test_a_shared_group_keeps_the_other_hook(self):
        """Ours and someone else's in the same array: take one, leave one."""
        self.write({"hooks": {"Stop": [{"hooks": [
            SOMEONE_ELSE,
            {"type": "command", "command": "py", "args": ["claude_notify.py"]},
        ]}]}})
        claudehook.disable()
        stop = [h for g in self.read()["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(stop, [SOMEONE_ELSE])

    def test_turning_off_when_already_off(self):
        self.write({"hooks": {}})
        ok, message = claudehook.disable()
        self.assertTrue(ok)
        self.assertEqual(message, "Already off.")

    def test_round_trip_leaves_the_rest_as_it_was(self):
        original = {"hooks": {"Stop": [{"hooks": [SOMEONE_ELSE]}]},
                    "effortLevel": "high"}
        self.write(original)
        claudehook.enable()
        claudehook.disable()
        after = self.read()
        self.assertEqual(after["effortLevel"], "high")
        self.assertEqual([h for g in after["hooks"]["Stop"] for h in g["hooks"]],
                         [SOMEONE_ELSE])


if __name__ == "__main__":
    unittest.main()
