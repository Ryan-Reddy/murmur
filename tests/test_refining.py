"""Tests for hearing a line twice: quickly, then properly.

    venv\\Scripts\\python.exe -m unittest discover -s tests -v

Real time is the wrong thing to optimise for. A model fast enough to keep up
is not the most accurate one available, and the accurate one cannot keep up --
measured, `base` runs at 0.25x real time and `small` at 0.8-1.0x, which has no
headroom at all. So both run: the fast one puts words on screen while somebody
is still talking, and the better one replaces them a few seconds later.

Which makes identity the whole problem. A line you marked, or hung a note on,
must still be marked and still carry its note after it has been rewritten
underneath you -- otherwise the refinement quietly eats the only work you did
during the meeting.
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from listener import FRAME, SAMPLE_RATE, THEM, YOU, Meeting  # noqa: E402
from minutes import Minutes  # noqa: E402


def voice(seconds, db=-20.0, hz=180.0):
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    amp = 10 ** (db / 20) * np.sqrt(2)
    return (np.sin(2 * np.pi * hz * t) * amp).astype(np.float32)


def quiet(seconds):
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


def framed(*signals):
    joined = np.concatenate(signals)
    end = len(joined) - len(joined) % FRAME
    return [joined[i:i + FRAME] for i in range(0, end, FRAME)]


class Canned:
    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = 0

    def transcribe(self, audio, sample_rate=SAMPLE_RATE):
        self.calls += 1
        return self.texts.pop(0) if self.texts else "..."


def say(meeting, source=YOU, seconds=1.0, lead=0.5):
    for frame in framed(quiet(lead), voice(seconds), quiet(1.5)):
        meeting.feed(source, frame)


class TwoPasses(unittest.TestCase):

    def setUp(self):
        self.heard = []
        self.fast = Canned("rough werds hear")
        self.slow = Canned("rough words here")
        self.meeting = Meeting(self.fast, on_line=self.heard.append)

    def test_the_fast_pass_lands_first_and_alone(self):
        say(self.meeting)
        self.assertEqual([l.text for l in self.heard], ["rough werds hear"])
        self.assertEqual(self.slow.calls, 0, "the slow model ran too early")

    def test_refining_replaces_the_line_rather_than_adding_one(self):
        say(self.meeting)
        self.meeting.refine(self.slow)
        self.assertEqual([l.text for l in self.meeting.transcript.lines],
                         ["rough words here"])

    def test_the_refined_line_keeps_its_place_in_time(self):
        say(self.meeting)
        before = self.meeting.transcript.lines[0]
        self.meeting.refine(self.slow)
        after = self.meeting.transcript.lines[0]
        self.assertEqual((after.start, after.end, after.source),
                         (before.start, before.end, before.source))
        self.assertEqual(after.id, before.id)

    def test_the_listener_says_so_again_so_the_window_can_redraw(self):
        say(self.meeting)
        self.meeting.refine(self.slow)
        self.assertEqual([l.text for l in self.heard],
                         ["rough werds hear", "rough words here"])

    def test_nothing_waiting_is_not_an_error(self):
        self.assertIsNone(self.meeting.refine(self.slow))
        self.assertEqual(self.slow.calls, 0)

    def test_an_identical_second_opinion_changes_nothing(self):
        """No flicker, and no pointless redraw, when the fast model was right
        -- which on clear speech is most of the time."""
        meeting = Meeting(Canned("the same"), on_line=self.heard.append)
        say(meeting)
        self.heard.clear()
        self.assertIsNone(meeting.refine(Canned("the same")))
        self.assertEqual(self.heard, [])

    def test_the_audio_is_let_go_once_it_has_been_used(self):
        """Twenty-five seconds of 16 kHz float32 is 1.6 MB an utterance. Held
        for a second pass, fine; held for the whole meeting, not."""
        say(self.meeting)
        self.assertEqual(len(self.meeting.awaiting), 1)
        self.meeting.refine(self.slow)
        self.assertEqual(len(self.meeting.awaiting), 0)

    def test_refinement_can_be_turned_off_and_then_costs_no_memory(self):
        meeting = Meeting(Canned("once only"), refine=False)
        say(meeting)
        self.assertEqual(len(meeting.awaiting), 0)

    def test_both_sources_are_refined(self):
        meeting = Meeting(Canned("mine", "theirs"), on_line=self.heard.append)
        say(meeting, YOU)
        say(meeting, THEM)
        better = Canned("mine, better", "theirs, better")
        self.assertIsNotNone(meeting.refine(better))
        self.assertIsNotNone(meeting.refine(better))
        self.assertEqual([(l.source, l.text) for l in meeting.transcript.lines],
                         [(YOU, "mine, better"), (THEM, "theirs, better")])

    def test_the_oldest_waiting_line_is_refined_first(self):
        """So the transcript settles from the top down, the way it was said."""
        meeting = Meeting(Canned("first", "second"), on_line=self.heard.append)
        say(meeting)
        say(meeting)
        got = meeting.refine(Canned("first, better"))
        self.assertEqual(got.text, "first, better")


class MarksSurviveRefinement(unittest.TestCase):
    """The part that would be silently destructive if it were wrong."""

    def setUp(self):
        self.minutes = Minutes()
        self.meeting = Meeting(Canned("we shood ship fryday"),
                               on_line=self.minutes.add)
        say(self.meeting)
        self.line = self.minutes.lines[0]

    def refine(self, better="we should ship Friday"):
        self.meeting.refine(Canned(better))

    def test_a_mark_is_still_there_afterwards(self):
        self.minutes.check(self.line)
        self.refine()
        self.assertEqual([l.text for l in self.minutes.checked()],
                         ["we should ship Friday"])

    def test_a_note_is_still_attached_to_it(self):
        self.minutes.note("ask who signs off")
        self.refine()
        self.assertEqual(self.minutes.notes[0].about.text,
                         "we should ship Friday")

    def test_the_note_still_renders_under_the_right_line(self):
        self.minutes.note("ask who signs off")
        self.refine()
        lines = self.minutes.render().splitlines()
        about = next(i for i, l in enumerate(lines) if "should ship" in l)
        self.assertIn("ask who signs off", lines[about + 1])

    def test_an_unmarked_line_does_not_become_marked(self):
        self.refine()
        self.assertEqual(self.minutes.checked(), [])

    def test_the_transcript_does_not_grow(self):
        self.minutes.check(self.line)
        self.refine()
        self.assertEqual(len(self.minutes.lines), 1)

    def test_the_summary_still_counts_one_of_each(self):
        self.minutes.check(self.line)
        self.minutes.note("ask who signs off")
        self.refine()
        self.assertEqual(self.minutes.summary(), "1 lines, 1 to check, 1 notes")


if __name__ == "__main__":
    unittest.main()
