"""Tests for the settings file: what is shipped, what is kept, what is dropped.

    venv\\Scripts\\python.exe -m unittest discover -s tests -v

cufflink imports pystray and tkinter at module level, so these skip themselves
where those are missing rather than failing the whole run.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import cufflink

    has_cufflink = True
except Exception:  # pragma: no cover - headless CI
    cufflink = None
    has_cufflink = False


class Stored:
    """load_settings against a temporary file rather than yours."""

    def _load(self, stored):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps(stored), encoding="utf-8")
            with mock.patch.object(cufflink, "settings_path", lambda: path):
                return cufflink.load_settings()


@unittest.skipUnless(has_cufflink, "cufflink needs a display and a tray")
class Profiles(Stored, unittest.TestCase):
    """A profile is a job -- what the voice is for -- not a character."""

    def test_the_shipped_ones_are_jobs(self):
        self.assertEqual(set(cufflink.DEFAULT_PROFILES),
                         {"default", "claude", "clock"})

    def test_every_profile_names_a_real_character(self):
        import voices

        for name, profile in cufflink.DEFAULT_PROFILES.items():
            with self.subTest(profile=name):
                self.assertIn(profile["treatment"], voices.TREATMENTS)

    def test_they_sound_different_from_each_other(self):
        """Telling them apart by ear is the entire point."""
        treatments = [p["treatment"] for p in cufflink.DEFAULT_PROFILES.values()]
        self.assertEqual(len(set(treatments)), len(treatments))

    def test_the_defaults_are_not_shared_between_profiles(self):
        """They share one DEFAULT_BLEND literal, so a shallow copy would let
        an edit to one profile's voice show up in the others."""
        loaded = self._load({})
        loaded["profiles"]["default"]["blend"][0][0] = "changed"
        self.assertEqual(loaded["profiles"]["claude"]["blend"][0][0], "bf_emma")
        self.assertEqual(cufflink.DEFAULT_PROFILES["default"]["blend"][0][0],
                         "bf_emma")


@unittest.skipUnless(has_cufflink, "cufflink needs a display and a tray")
class Legacy(Stored, unittest.TestCase):
    """veronica, submarine and agent used to ship as profiles of their own."""

    def test_an_untouched_one_is_dropped(self):
        loaded = self._load({"profiles": dict(cufflink.LEGACY_PROFILES)})
        self.assertEqual(set(loaded["profiles"]), set(cufflink.DEFAULT_PROFILES))

    def test_an_edited_one_is_kept(self):
        """It stopped being ours the moment it was changed."""
        mine = dict(cufflink.LEGACY_PROFILES["veronica"], speed=1.4)
        loaded = self._load({"profiles": {"veronica": mine}})
        self.assertEqual(loaded["profiles"]["veronica"]["speed"], 1.4)

    def test_a_profile_you_named_yourself_is_kept(self):
        loaded = self._load({"profiles": {"bedtime": {"speed": 0.8}}})
        self.assertEqual(loaded["profiles"]["bedtime"]["speed"], 0.8)

    def test_stored_values_win_over_the_defaults(self):
        loaded = self._load({"profiles": {"claude": {"treatment": "submarine"}}})
        self.assertEqual(loaded["profiles"]["claude"]["treatment"], "submarine")
        self.assertEqual(loaded["profiles"]["claude"]["speed"], 0.95,
                         "unmentioned keys keep their default")


@unittest.skipUnless(has_cufflink, "cufflink needs a display and a tray")
class BrokenFile(unittest.TestCase):
    """Losing your settings must not cost you the voice."""

    def test_no_file_gives_the_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nothing" / "settings.json"
            with mock.patch.object(cufflink, "settings_path", lambda: path):
                self.assertEqual(set(cufflink.load_settings()["profiles"]),
                                 set(cufflink.DEFAULT_PROFILES))

    def test_unreadable_json_gives_the_defaults(self):
        loaded = self._write_and_load("{ this is not json")
        self.assertEqual(set(loaded["profiles"]), set(cufflink.DEFAULT_PROFILES))

    def test_a_profile_that_is_not_a_dict_is_ignored(self):
        loaded = self._write_and_load('{"profiles": {"odd": "a string"}}')
        self.assertNotIn("odd", loaded["profiles"])

    def test_a_save_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cufflink" / "settings.json"
            with mock.patch.object(cufflink, "settings_home", lambda: path),                     mock.patch.object(cufflink, "settings_path", lambda: path):
                settings = cufflink.load_settings()
                settings["profiles"]["default"]["speed"] = 1.3
                self.assertTrue(cufflink.save_settings(settings))
                self.assertEqual(
                    cufflink.load_settings()["profiles"]["default"]["speed"], 1.3)

    def test_settings_from_the_old_name_are_still_found(self):
        """It was called Murmur before the Store; nobody should lose their
        voices to a rename."""
        with tempfile.TemporaryDirectory() as tmp:
            new = Path(tmp) / "cufflink" / "settings.json"
            old = Path(tmp) / "Murmur" / "settings.json"
            old.parent.mkdir(parents=True)
            old.write_text('{"profiles": {"default": {"speed": 1.45}}}',
                           encoding="utf-8")
            with mock.patch.object(cufflink, "settings_home", lambda: new):
                self.assertEqual(cufflink.settings_path(), old)
                self.assertEqual(
                    cufflink.load_settings()["profiles"]["default"]["speed"], 1.45)

    def test_the_new_name_wins_once_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = Path(tmp) / "cufflink" / "settings.json"
            old = Path(tmp) / "Murmur" / "settings.json"
            for path, speed in ((old, 1.45), (new, 0.8)):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"profiles": {"default": {"speed": %s}}}' % speed,
                                encoding="utf-8")
            with mock.patch.object(cufflink, "settings_home", lambda: new):
                self.assertEqual(cufflink.settings_path(), new)

    def _write_and_load(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(text, encoding="utf-8")
            with mock.patch.object(cufflink, "settings_path", lambda: path):
                return cufflink.load_settings()


if __name__ == "__main__":
    unittest.main()
