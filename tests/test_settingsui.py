"""Tests for the settings window.

    venv\\Scripts\\python.exe -m unittest discover -s tests -v

These build the real window against a stand-in speaker and drive it the way a
click would -- `menu.invoke(index)` runs exactly the callback the mouse would
-- so the wiring is checked without taking over the pointer. Every test skips
itself where there is no display to build a window on.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import settingsui  # noqa: E402

try:
    import tkinter as tk

    _root = tk.Tk()
    _root.withdraw()
    has_display = True
except Exception:  # pragma: no cover - headless CI
    _root = None
    has_display = False


class FakeSpeaker:
    """Enough of a Speaker to build the window against, and a record of what
    it was asked to say."""

    def __init__(self, voices=("af_nicole", "bf_emma", "am_michael")):
        self.kokoro = type("K", (), {"get_voices": lambda _self: list(voices)})()
        self.spoken = []

    def speak(self, text, profile=None):
        self.spoken.append((text, profile))


def settings():
    return {
        "profiles": {
            "default": {
                "blend": [["bf_emma", 0.7], ["af_nicole", 0.3]],
                "speed": 1.0, "treatment": "clean",
                "sentence_pause": 0.25, "clause_pause": 0.10,
            },
            "claude": {
                "blend": [["am_michael", 0.4], ["af_nicole", 0.6]],
                "speed": 0.95, "treatment": "bbc",
                "sentence_pause": 0.10, "clause_pause": 0.03,
            },
        }
    }


@unittest.skipUnless(has_display, "needs a display to build a window on")
class Window(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self.applied = []
        self.speaker = FakeSpeaker()
        self.settings = settings()
        settingsui._open["window"] = None
        self.window = settingsui._Window(
            _root, self.settings, self.save, self.speaker, self.apply)

    def tearDown(self):
        try:
            self.window.top.destroy()
        except Exception:
            pass
        settingsui._open["window"] = None

    def save(self, data):
        self.saved.append({k: dict(v) for k, v in data["profiles"].items()})
        return True

    def apply(self, name, profile):
        self.applied.append((name, dict(profile)))

    # -- the form ------------------------------------------------------------

    def test_opens_on_the_default_profile(self):
        self.assertEqual(self.window.profile_name.get(), "default")
        self.assertEqual(self.window.voice_a.get(), "bf_emma")
        self.assertEqual(self.window.voice_b.get(), "af_nicole")
        self.assertEqual(self.window.mix.get(), 70)
        self.assertEqual(self.window.treatment.get(), "clean")

    def test_switching_profile_loads_it(self):
        """The bug this covers: the picker changed the name and nothing else,
        so you edited one profile's dials and saved them over another."""
        self.window.profile_name.set("claude")
        self.assertEqual(self.window.voice_a.get(), "am_michael")
        self.assertEqual(self.window.mix.get(), 40)
        self.assertEqual(self.window.treatment.get(), "bbc")
        self.assertAlmostEqual(self.window.speed.get(), 0.95)

    def test_the_form_round_trips(self):
        for name in self.settings["profiles"]:
            with self.subTest(profile=name):
                self.window.profile_name.set(name)
                self.assertEqual(self.window.as_profile(),
                                 self.settings["profiles"][name])

    def test_mix_is_shared_between_the_two_voices(self):
        self.window.mix.set(25)
        blend = self.window.as_profile()["blend"]
        self.assertEqual(blend[0][1], 0.25)
        self.assertEqual(blend[1][1], 0.75)

    def test_an_unknown_profile_falls_back_rather_than_raising(self):
        self.window.load_profile("nothing-by-that-name")
        self.assertTrue(self.window.voice_a.get())
        self.assertEqual(self.window.treatment.get(), "clean")

    # -- the controls --------------------------------------------------------

    def test_every_voice_is_offered(self):
        self.assertEqual(self.window._voice_names(),
                         ["af_nicole", "am_michael", "bf_emma"])

    def test_a_voice_that_is_not_loaded_yet_does_not_stop_the_window(self):
        window = settingsui._Window(
            _root, settings(), self.save, object(), self.apply)
        self.addCleanup(window.top.destroy)
        self.assertEqual(window._voice_names(), ["(still loading)"])

    def test_choosing_from_a_menu_sets_the_value(self):
        """What a click actually runs, without a click."""
        menu = self._menu_under("Sounds like")
        labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)]
        menu.invoke(labels.index("BBC — even and measured"))
        self.assertEqual(self.window.treatment.get(), "bbc")

    def test_the_voice_menu_breaks_into_columns(self):
        """54 voices in one column is taller than the screen."""
        many = FakeSpeaker([f"v{n:02d}" for n in range(54)])
        window = settingsui._Window(_root, settings(), self.save, many, self.apply)
        self.addCleanup(window.top.destroy)
        menu = self._menu_under("First", window)
        breaks = sum(int(menu.entrycget(i, "columnbreak"))
                     for i in range(menu.index("end") + 1))
        self.assertEqual(breaks, 2)

    def _menu_under(self, label, window=None):
        """The dropdown belonging to the row with this label."""
        window = window or self.window
        for row in window.top.winfo_children()[0].winfo_children():
            children = row.winfo_children()
            if children and getattr(children[0], "cget", None) \
                    and children[0].winfo_class() == "Label" \
                    and children[0].cget("text") == label:
                return children[1]["menu"]
        raise AssertionError(f"no row labelled {label!r}")

    # -- the buttons ---------------------------------------------------------

    def test_hearing_it_speaks_the_form_not_the_saved_profile(self):
        self.window.treatment.set("agent")
        self.window.preview()
        text, profile = self.speaker.spoken[-1]
        self.assertEqual(text, settingsui.SAMPLE)
        self.assertEqual(profile["treatment"], "agent")
        self.assertEqual(self.settings["profiles"]["default"]["treatment"],
                         "clean", "preview must not write anything")

    def test_saving_writes_and_pushes_the_profile(self):
        self.window.speed.set(1.25)
        self.window.apply()
        self.assertEqual(self.settings["profiles"]["default"]["speed"], 1.25)
        self.assertEqual(self.saved[-1]["default"]["speed"], 1.25)
        self.assertEqual(self.applied[-1][0], "default")
        self.assertEqual(self.applied[-1][1]["speed"], 1.25)

    def test_a_save_that_cannot_be_written_still_goes_live_and_says_so(self):
        window = settingsui._Window(
            _root, settings(), lambda _data: False, self.speaker, self.apply)
        self.addCleanup(window.top.destroy)
        window.apply()
        self.assertEqual(self.applied[-1][0], "default")
        self.assertIn("could not", window.note.cget("text"))

    def test_only_one_window_at_a_time(self):
        first = settingsui.open_window(
            _root, self.settings, self.save, self.speaker, self.apply)
        second = settingsui.open_window(
            _root, self.settings, self.save, self.speaker, self.apply)
        self.addCleanup(lambda: first.winfo_exists() and first.destroy())
        self.assertIs(first, second)

    def test_closing_lets_the_next_one_open(self):
        first = settingsui.open_window(
            _root, self.settings, self.save, self.speaker, self.apply)
        first.destroy()
        settingsui._open["window"] = None
        second = settingsui.open_window(
            _root, self.settings, self.save, self.speaker, self.apply)
        self.addCleanup(second.destroy)
        self.assertIsNot(first, second)


if __name__ == "__main__":
    unittest.main()
