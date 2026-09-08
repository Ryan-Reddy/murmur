"""Tests for where a transcript is written, and what happens to the old ones.

    venv\\Scripts\\python.exe -m unittest discover -s tests -v

The app was called Murmur until the Store, and its state moved with the name.
A transcript written to the old folder while the log goes to the new one is
not a crash; it is somebody looking for last Tuesday's meeting in a directory
that stopped being written to, and finding nothing.
"""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import meeting  # noqa: E402


class WhereTranscriptsGo(unittest.TestCase):

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.local = Path(self.tmp.name)
        self.old = self.local / "Murmur" / "meetings"
        self.new = self.local / "cufflink" / "meetings"

    def tearDown(self):
        self.tmp.cleanup()

    def home(self):
        return meeting.state_home(self.local)

    def test_it_writes_under_the_current_name(self):
        self.assertEqual(self.home().name, "cufflink")

    def test_the_log_and_the_transcripts_share_a_folder(self):
        """cufflink writes meeting.log beside its settings. A transcript two
        directories away from the log of the run that made it is the split
        this test exists to prevent."""
        self.assertEqual(self.home(), self.local / "cufflink")

    def test_old_transcripts_are_brought_across(self):
        self.old.mkdir(parents=True)
        (self.old / "2026-09-07 2125.txt").write_text("a meeting", encoding="utf-8")
        moved = meeting.migrate_meetings(self.local)
        self.assertEqual(moved, 1)
        self.assertTrue((self.new / "2026-09-07 2125.txt").exists())
        self.assertEqual((self.new / "2026-09-07 2125.txt").read_text(encoding="utf-8"),
                         "a meeting")

    def test_nothing_to_bring_across_is_not_an_error(self):
        self.assertEqual(meeting.migrate_meetings(self.local), 0)

    def test_it_never_writes_over_a_transcript_already_there(self):
        """Both folders can hold a file of the same name -- the minute-
        resolution names collided once already. Losing one to a migration
        would be the same loss by a different route."""
        self.old.mkdir(parents=True)
        self.new.mkdir(parents=True)
        (self.old / "same.txt").write_text("the old one", encoding="utf-8")
        (self.new / "same.txt").write_text("the new one", encoding="utf-8")
        meeting.migrate_meetings(self.local)
        self.assertEqual((self.new / "same.txt").read_text(encoding="utf-8"),
                         "the new one")
        kept = list(self.new.glob("same*.txt"))
        self.assertEqual(len(kept), 2, "the old one was dropped")

    def test_running_it_twice_moves_nothing_the_second_time(self):
        self.old.mkdir(parents=True)
        (self.old / "one.txt").write_text("x", encoding="utf-8")
        self.assertEqual(meeting.migrate_meetings(self.local), 1)
        self.assertEqual(meeting.migrate_meetings(self.local), 0)


if __name__ == "__main__":
    unittest.main()
