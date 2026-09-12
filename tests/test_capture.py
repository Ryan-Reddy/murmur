"""Tests for getting audio in: reframing, and picking the device that works.

    venv\\Scripts\\python.exe -m unittest discover -s tests -v

No device is opened here. What is tested is the arithmetic between a device's
block size and the listener's frame size, and the rule for choosing between
loopback devices that look identical and are not.
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import capture  # noqa: E402
from capture import Framer  # noqa: E402
from listener import FRAME  # noqa: E402


class Reframing(unittest.TestCase):
    """A 48 kHz block of 1024 samples becomes 341 at 16 kHz, which is not a
    whole number of 30 ms frames. The remainder has to be carried, not
    dropped and not padded."""

    def test_a_block_that_divides_evenly_comes_straight_out(self):
        framer = Framer()
        out = framer.feed(np.zeros(FRAME * 3, np.float32))
        self.assertEqual(len(out), 3)
        self.assertEqual(framer.held(), 0)

    def test_a_short_block_is_held_rather_than_emitted(self):
        framer = Framer()
        self.assertEqual(framer.feed(np.zeros(100, np.float32)), [])
        self.assertEqual(framer.held(), 100)

    def test_every_frame_is_exactly_one_frame_long(self):
        framer = Framer()
        for _ in range(50):
            for frame in framer.feed(np.zeros(341, np.float32)):
                self.assertEqual(len(frame), FRAME)

    def test_not_one_sample_is_lost_or_invented(self):
        """The timeline is counted in frames, so a sample dropped per block
        walks the whole transcript out of true over an hour."""
        framer = Framer()
        fed = 0
        got = 0
        for size in (341, 341, 342, 341, 1024, 7, 341):
            fed += size
            got += sum(len(f) for f in framer.feed(np.zeros(size, np.float32)))
        self.assertEqual(got + framer.held(), fed)

    def test_the_samples_come_out_in_the_order_they_went_in(self):
        framer = Framer()
        source = np.arange(FRAME * 4 + 137, dtype=np.float32)
        out = []
        for start in range(0, len(source), 341):
            out.extend(framer.feed(source[start:start + 341]))
        joined = np.concatenate(out)
        self.assertTrue(np.array_equal(joined, source[:len(joined)]))


class PickingALoopback(unittest.TestCase):
    """Two loopback devices carry the same name and only one works, and
    nothing in the API says which. Worse, measured on an idle machine:
    *neither* delivers a callback while nothing is playing, so a signal probe
    is no help at the moment a meeting starts. Hence a priority order, and a
    guess rather than a refusal at the end of it."""

    def setUp(self):
        self.candidates = [
            {"index": 13, "name": "Monitor [Loopback]", "maxInputChannels": 2,
             "defaultSampleRate": 48000},
            {"index": 14, "name": "Monitor [Loopback]", "maxInputChannels": 2,
             "defaultSampleRate": 48000},
            {"index": 15, "name": "Headset [Loopback]", "maxInputChannels": 2,
             "defaultSampleRate": 48000},
        ]
        self.probed = []
        self.live = {13}
        capture.loopback_candidates = lambda _audio: list(self.candidates)
        capture.delivers = self._delivers

    def tearDown(self):
        import importlib

        importlib.reload(capture)

    def _delivers(self, _audio, info, seconds=0.0):
        self.probed.append(info["index"])
        return info["index"] in self.live

    class FakeAudio:
        """Enough PyAudio to be asked what the default output is."""

        def __init__(self, default="Monitor"):
            self.default = default

        def get_default_wasapi_loopback(self):
            return None          # the library often cannot say

        def get_host_api_info_by_type(self, _kind):
            return {"defaultOutputDevice": 3}

        def get_device_info_by_index(self, _index):
            return {"name": self.default}

    def test_the_one_that_delivers_frames_is_chosen(self):
        got = capture.pick_loopback(self.FakeAudio())
        self.assertEqual(got["index"], 13)

    def test_the_library_is_believed_before_any_probing(self):
        """It matches endpoints properly; everything below is guesswork."""
        audio = self.FakeAudio()
        audio.get_default_wasapi_loopback = lambda: self.candidates[2]
        self.assertEqual(capture.pick_loopback(audio)["index"], 15)
        self.assertEqual(self.probed, [], "it probed anyway")

    def test_a_dead_twin_is_skipped_rather_than_trusted(self):
        self.live = {14}
        got = capture.pick_loopback(self.FakeAudio())
        self.assertEqual(got["index"], 14)
        self.assertIn(13, self.probed, "it never tried the first one")

    def test_the_default_output_is_tried_first(self):
        """Two working loopbacks is a machine with two outputs. The meeting is
        coming out of the one Windows is actually using."""
        self.live = {13, 15}
        got = capture.pick_loopback(self.FakeAudio(default="Headset"))
        self.assertEqual(got["index"], 15)

    def test_a_silent_machine_still_gets_a_device(self):
        """Nothing delivers while nothing is playing -- which is every time a
        meeting is about to start. Refusing to listen would be the one
        outcome worse than picking the wrong endpoint, because the meters
        make a wrong endpoint obvious within seconds."""
        self.live = set()
        got = capture.pick_loopback(self.FakeAudio(default="Monitor"))
        self.assertIsNotNone(got)
        self.assertIn("Monitor", got["name"])

    def test_a_machine_with_no_loopback_at_all_is_not_an_error(self):
        self.candidates = []
        self.assertIsNone(capture.pick_loopback(self.FakeAudio()))


if __name__ == "__main__":
    unittest.main()
