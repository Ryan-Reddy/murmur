"""Tests for what the pill asks the meeting process, and how often.

    venv\\Scripts\\python.exe -m unittest discover -s tests -v

The pill and the transcription live in different processes on purpose, so the
pill has to ask. Asking well matters: a poll that drags the whole transcript
across every half-second, for an hour, to discover that nothing has changed,
is the kind of thing that makes a tray app feel heavy for no reason.

So there are two questions. `state` is a short line, safe to ask often, and
carries a version. `dump` is everything, and is only worth asking when the
version has moved.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from listener import THEM, YOU, Line  # noqa: E402
from minutes import Minutes  # noqa: E402


def line(start, source=YOU, text="hello", line_id=None):
    return Line(source=source, start=start, end=start + 1.0, text=text,
                id=line_id)


class Versions(unittest.TestCase):
    """One number that moves whenever anything the pill draws has changed."""

    def setUp(self):
        self.m = Minutes()

    def test_a_new_meeting_starts_somewhere(self):
        self.assertIsInstance(self.m.version, int)

    def test_a_line_moves_it(self):
        was = self.m.version
        self.m.add(line(1.0))
        self.assertNotEqual(self.m.version, was)

    def test_a_mark_moves_it(self):
        self.m.add(line(1.0))
        was = self.m.version
        self.m.check()
        self.assertNotEqual(self.m.version, was)

    def test_a_note_moves_it(self):
        self.m.add(line(1.0))
        was = self.m.version
        self.m.note("something")
        self.assertNotEqual(self.m.version, was)

    def test_a_line_being_rewritten_moves_it(self):
        """The whole point of the second pass is that the words change under
        you. If the version did not move, the pill would never redraw them."""
        self.m.add(line(1.0, text="rough werds", line_id=7))
        was = self.m.version
        self.m.add(line(1.0, text="rough words", line_id=7))
        self.assertNotEqual(self.m.version, was)

    def test_nothing_happening_does_not_move_it(self):
        """Which is what makes polling it cheap: silence costs one short
        string and no redraw."""
        self.m.add(line(1.0))
        was = self.m.version
        self.m.render()
        self.m.summary()
        self.m.command("status", "")
        self.assertEqual(self.m.version, was)

    def test_an_empty_line_is_not_a_change(self):
        """Whisper returns "" on silence, and that must not make the pill
        redraw itself every few seconds all meeting."""
        self.m.add(line(1.0))
        was = self.m.version
        self.m.add(line(2.0, text="   "))
        self.assertEqual(self.m.version, was)


class Dump(unittest.TestCase):
    """Everything the pill needs to draw, as JSON, in one answer."""

    def setUp(self):
        self.m = Minutes()
        self.a = self.m.add(line(1.0, YOU, "so the installer fetches it", 1))
        self.b = self.m.add(line(5.0, THEM, "and checks the hash?", 2))
        self.m.check(self.b)
        self.m.note("ask where the SHA comes from", at=7.0)

    def parsed(self):
        return json.loads(self.m.command("dump", ""))

    def test_it_is_json(self):
        self.assertIsInstance(self.parsed(), dict)

    def test_every_line_is_there_in_order(self):
        got = self.parsed()["lines"]
        self.assertEqual([l["text"] for l in got],
                         ["so the installer fetches it", "and checks the hash?"])

    def test_a_line_says_who_when_and_whether_it_is_marked(self):
        first, second = self.parsed()["lines"]
        self.assertEqual(first["source"], YOU)
        self.assertEqual(second["at"], 5.0)
        self.assertTrue(second["checked"])
        self.assertFalse(first["checked"])

    def test_a_line_carries_its_id_so_the_pill_can_mark_it(self):
        self.assertEqual([l["id"] for l in self.parsed()["lines"]], [1, 2])

    def test_notes_come_with_the_line_they_belong_to(self):
        note = self.parsed()["notes"][0]
        self.assertEqual(note["text"], "ask where the SHA comes from")
        self.assertEqual(note["about"], 2)

    def test_the_version_comes_with_it(self):
        """So the pill can stop asking again until it moves."""
        self.assertEqual(self.parsed()["version"], self.m.version)

    def test_an_empty_meeting_dumps_cleanly(self):
        empty = json.loads(Minutes().command("dump", ""))
        self.assertEqual(empty["lines"], [])
        self.assertEqual(empty["notes"], [])


class Marking(unittest.TestCase):
    """The pill marks by id, because that is what it was given."""

    def setUp(self):
        self.m = Minutes()
        self.m.add(line(1.0, YOU, "first", 11))
        self.m.add(line(5.0, THEM, "second", 12))

    def test_a_line_can_be_marked_by_its_id(self):
        self.m.command("mark", "12")
        self.assertEqual([l.text for l in self.m.checked()], ["second"])

    def test_marking_by_id_twice_unmarks(self):
        self.m.command("mark", "12")
        self.m.command("mark", "12")
        self.assertEqual(self.m.checked(), [])

    def test_an_id_that_is_not_there_is_refused_not_crashed(self):
        self.assertIn("no line", self.m.command("mark", "999").lower())

    def test_a_mark_by_id_survives_the_line_being_rewritten(self):
        self.m.command("mark", "12")
        self.m.add(line(5.0, THEM, "and checks the hash?", 12))
        self.assertEqual([l.text for l in self.m.checked()],
                         ["and checks the hash?"])


if __name__ == "__main__":
    unittest.main()
