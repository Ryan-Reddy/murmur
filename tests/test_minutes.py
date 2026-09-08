"""Tests for what you do to a transcript while it is still happening.

    venv\\Scripts\\python.exe -m unittest discover -s tests -v

The point of the bubbler is that marking a line takes one click and a note
takes one keystroke, in the middle of a meeting, without looking away from
it. So the thing being tested is: does a mark land on the line you meant, and
does everything you did survive to the saved document.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from listener import THEM, YOU, Line  # noqa: E402
from minutes import CHECK, Minutes, Note  # noqa: E402


def line(start, source=YOU, text="hello"):
    return Line(source=source, start=start, end=start + 1.0, text=text)


class Marking(unittest.TestCase):

    def setUp(self):
        self.m = Minutes()
        self.said = [line(1.0, YOU, "first"), line(5.0, THEM, "second"),
                     line(9.0, YOU, "third")]
        for l in self.said:
            self.m.add(l)

    def test_the_latest_line_is_what_check_means_by_default(self):
        """You mark a thing just after hearing it, so 'this one' is the one
        that just went past."""
        self.m.check()
        self.assertEqual(self.m.checked(), [self.said[-1]])

    def test_an_older_line_can_be_marked_by_clicking_it(self):
        self.m.check(self.said[0])
        self.assertEqual(self.m.checked(), [self.said[0]])

    def test_marking_twice_unmarks(self):
        """The same click both ways -- a mark put on the wrong line during a
        meeting has to come off without a menu."""
        self.m.check(self.said[1])
        self.m.check(self.said[1])
        self.assertEqual(self.m.checked(), [])

    def test_a_mark_stays_on_its_line_when_earlier_lines_arrive_late(self):
        """The far end is transcribed independently and lands out of order.
        A mark held by position would slide onto somebody else's sentence."""
        self.m.check(self.said[2])
        self.m.add(line(3.0, THEM, "arrived late, belongs earlier"))
        self.assertEqual(self.m.checked(), [self.said[2]])

    def test_checking_nothing_at_all_is_harmless(self):
        empty = Minutes()
        empty.check()
        self.assertEqual(empty.checked(), [])

    def test_marks_come_back_in_the_order_they_were_said(self):
        self.m.check(self.said[2])
        self.m.check(self.said[0])
        self.assertEqual([l.text for l in self.m.checked()], ["first", "third"])


class Notes(unittest.TestCase):

    def setUp(self):
        self.m = Minutes()
        self.spoken = line(10.0, THEM, "we should ship on Friday")
        self.m.add(self.spoken)

    def test_a_note_hangs_off_the_line_that_was_just_said(self):
        self.m.note("ask who signs off")
        self.assertEqual(self.m.notes[0].about, self.spoken)

    def test_a_note_remembers_when_you_wrote_it(self):
        """Not when the line was said -- you write it a few seconds later, and
        which line you were reacting to is the useful part."""
        self.m.note("ask who signs off", at=13.5)
        self.assertEqual(self.m.notes[0].at, 13.5)
        self.assertEqual(self.m.notes[0].about, self.spoken)

    def test_a_note_before_anyone_has_spoken_still_lands(self):
        empty = Minutes()
        empty.note("waiting for them to join", at=2.0)
        self.assertEqual(len(empty.notes), 1)
        self.assertIsNone(empty.notes[0].about)

    def test_an_empty_note_is_not_a_note(self):
        self.m.note("   ")
        self.m.note("")
        self.assertEqual(self.m.notes, [])

    def test_several_notes_can_hang_off_one_line(self):
        self.m.note("who signs off")
        self.m.note("and what about the installer")
        self.assertEqual(len(self.m.notes), 2)


class Rendering(unittest.TestCase):
    """What you are left with afterwards. If the marks and notes are not in
    it, the marking was theatre."""

    def setUp(self):
        self.m = Minutes()
        self.a = line(1.0, YOU, "so the installer fetches the model")
        self.b = line(5.0, THEM, "and checks the hash?")
        for l in (self.a, self.b):
            self.m.add(l)

    def test_the_spoken_words_are_all_there(self):
        out = self.m.render()
        self.assertIn("so the installer fetches the model", out)
        self.assertIn("and checks the hash?", out)

    def test_a_checked_line_is_visibly_checked(self):
        self.m.check(self.b)
        marked = [l for l in self.m.render().splitlines() if CHECK in l]
        self.assertEqual(len(marked), 1)
        self.assertIn("checks the hash", marked[0])

    def test_a_note_appears_under_the_line_it_is_about(self):
        self.m.note("ask where the SHA comes from", at=7.0)
        lines = self.m.render().splitlines()
        about = next(i for i, l in enumerate(lines) if "checks the hash" in l)
        self.assertIn("ask where the SHA comes from", lines[about + 1])

    def test_a_note_about_nothing_appears_at_its_own_time(self):
        early = Minutes()
        early.note("they are late", at=30.0)
        early.add(line(60.0, THEM, "sorry, had another call"))
        lines = early.render().splitlines()
        self.assertIn("they are late", lines[0])
        self.assertIn("had another call", lines[1])

    def test_the_summary_counts_what_needs_attention(self):
        self.m.check(self.b)
        self.m.note("ask where the SHA comes from")
        head = self.m.summary()
        self.assertIn("1", head)          # one flagged line
        self.assertIn("2", head)          # two spoken lines

    def test_saving_writes_what_render_shows(self):
        self.m.check(self.b)
        self.m.note("ask where the SHA comes from")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meeting.txt"
            self.m.save(path)
            written = path.read_text(encoding="utf-8")
        self.assertIn("ask where the SHA comes from", written)
        self.assertIn(CHECK, written)

    def test_only_the_marked_parts_can_be_pulled_out(self):
        """What the assistant is actually asked for afterwards: not the whole
        meeting, the bits you flagged."""
        self.m.check(self.b)
        self.m.note("ask where the SHA comes from")
        digest = self.m.render(marked_only=True)
        self.assertIn("checks the hash", digest)
        self.assertIn("ask where the SHA comes from", digest)
        self.assertNotIn("installer fetches", digest)


class Commands(unittest.TestCase):
    """The same actions over the wire, so the assistant can do them and so
    testing the window never means faking a keystroke into somebody's desktop.
    """

    def setUp(self):
        self.m = Minutes()
        self.m.add(line(1.0, YOU, "first"))
        self.m.add(line(5.0, THEM, "second"))

    def test_note_takes_the_rest_of_the_line(self):
        self.m.command("note", "ask about the hash, and the installer")
        self.assertEqual(self.m.notes[0].text,
                         "ask about the hash, and the installer")

    def test_check_with_no_argument_marks_the_latest(self):
        self.m.command("check", "")
        self.assertEqual([l.text for l in self.m.checked()], ["second"])

    def test_check_takes_a_line_number(self):
        self.m.command("check", "1")
        self.assertEqual([l.text for l in self.m.checked()], ["first"])

    def test_an_out_of_range_number_is_refused_not_crashed(self):
        """A meeting is not the moment for a traceback in the tray."""
        self.assertIn("no line", self.m.command("check", "99").lower())
        self.assertEqual(self.m.checked(), [])

    def test_lines_can_be_read_back(self):
        self.assertIn("second", self.m.command("lines", ""))

    def test_an_unknown_command_says_so(self):
        self.assertIn("?", self.m.command("recalibrate", "everything"))


if __name__ == "__main__":
    unittest.main()
