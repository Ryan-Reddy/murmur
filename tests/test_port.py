"""Tests for the text-in port's protocol and the way back to the source.

    venv\\Scripts\\python.exe -m unittest discover -s tests -v

The parsing lives in read_headers rather than inside the socket loop precisely
so these can be about the protocol instead of about sockets.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import cufflink

    has_cufflink = True
except Exception:  # pragma: no cover - headless CI
    cufflink = None
    has_cufflink = False

NL = "\n"


@unittest.skipUnless(has_cufflink, "cufflink needs a display and a tray")
class Headers(unittest.TestCase):
    """What arrives on the port, and what it means."""

    def test_plain_text_is_just_text(self):
        self.assertEqual(cufflink.read_headers("hello there"),
                         ("", "", "hello there"))

    def test_a_voice_can_be_named(self):
        self.assertEqual(cufflink.read_headers("::as claude" + NL + "hello"),
                         ("claude", "", "hello"))

    def test_a_source_can_be_named(self):
        self.assertEqual(cufflink.read_headers("::from murmur" + NL + "hello"),
                         ("", "murmur", "hello"))

    def test_both_in_either_order(self):
        both = ("claude", "murmur", "hello")
        self.assertEqual(
            cufflink.read_headers("::as claude" + NL + "::from murmur" + NL + "hello"),
            both)
        self.assertEqual(
            cufflink.read_headers("::from murmur" + NL + "::as claude" + NL + "hello"),
            both)

    def test_a_command_is_left_alone(self):
        """::stop must reach the command handler, not be read aloud."""
        self.assertEqual(cufflink.read_headers("::stop"), ("", "", "::stop"))

    def test_headers_may_precede_a_command(self):
        self.assertEqual(cufflink.read_headers("::as claude" + NL + "::stop"),
                         ("claude", "", "::stop"))

    def test_an_unknown_header_ends_the_headers(self):
        """Otherwise a paragraph beginning with a colon would be eaten a line
        at a time looking for headers that are not there."""
        text = "::nonsense here" + NL + "the actual words"
        self.assertEqual(cufflink.read_headers(text),
                         ("", "", "::nonsense here" + NL + "the actual words"))

    def test_a_header_with_nothing_after_it_is_not_a_header(self):
        """A lone "::as claude" is somebody's text, not an instruction with a
        missing body."""
        self.assertEqual(cufflink.read_headers("::as claude"),
                         ("", "", "::as claude"))

    def test_an_empty_value_is_ignored_rather_than_believed(self):
        self.assertEqual(cufflink.read_headers("::from   " + NL + "hello"),
                         ("", "", "hello"))

    def test_the_body_keeps_its_own_newlines(self):
        text = "::as claude" + NL + "first line" + NL + "second line"
        self.assertEqual(cufflink.read_headers(text),
                         ("claude", "", "first line" + NL + "second line"))

    def test_nothing_at_all(self):
        self.assertEqual(cufflink.read_headers(""), ("", "", ""))


@unittest.skipUnless(has_cufflink, "cufflink needs a display and a tray")
class FindingTheWindow(unittest.TestCase):
    """::from names a window by a fragment of its title."""

    def test_no_fragment_finds_nothing(self):
        self.assertEqual(cufflink.window_titled(""), (0, ""))

    def test_a_fragment_nobody_has(self):
        found, title = cufflink.window_titled(
            "no window is called this particular thing 8fa3")
        self.assertEqual((found, title), (0, ""))

    def test_it_finds_a_real_window_by_part_of_its_title(self):
        """Against whatever is actually on screen: take a visible window's
        title, hand back a slice of it, and expect the same window."""
        import ctypes

        titles = []

        def look(hwnd, _lparam):
            if cufflink._user32.IsWindowVisible(hwnd):
                buffer = ctypes.create_unicode_buffer(512)
                cufflink._user32.GetWindowTextW(hwnd, buffer, 512)
                if len(buffer.value) > 12:
                    titles.append(buffer.value)
            return True

        cufflink._user32.EnumWindows(cufflink._WNDENUMPROC(look), 0)
        if not titles:
            self.skipTest("no titled windows on screen to search for")
        longest = max(titles, key=len)
        found, title = cufflink.window_titled(longest[:10])
        self.assertTrue(found, f"did not find a window containing {longest[:10]!r}")
        self.assertIn(longest[:10].casefold(), title.casefold())

    def test_matching_ignores_case(self):
        import ctypes

        titles = []

        def look(hwnd, _lparam):
            if cufflink._user32.IsWindowVisible(hwnd):
                buffer = ctypes.create_unicode_buffer(512)
                cufflink._user32.GetWindowTextW(hwnd, buffer, 512)
                if any(c.isalpha() for c in buffer.value) and len(buffer.value) > 12:
                    titles.append(buffer.value)
            return True

        cufflink._user32.EnumWindows(cufflink._WNDENUMPROC(look), 0)
        if not titles:
            self.skipTest("no titled windows on screen to search for")
        fragment = max(titles, key=len)[:10]
        self.assertEqual(cufflink.window_titled(fragment.upper())[0],
                         cufflink.window_titled(fragment.lower())[0])


@unittest.skipUnless(has_cufflink, "cufflink needs a display and a tray")
class StartupShape(unittest.TestCase):
    """Invariants about how startup is allowed to be arranged.

    These are structural rather than behavioural: a second instance putting a
    window on screen before it notices the first one is a bug you only see by
    running two copies, which no unit test does.
    """

    def test_the_instance_guard_comes_before_anything_else(self):
        """main() must refuse to be a second instance before it builds any UI.
        A check further down would flash a window and then exit."""
        import inspect

        body = inspect.getsource(cufflink.main).splitlines()
        code = [line.strip() for line in body[1:]
                if line.strip() and not line.strip().startswith("#")]
        # trace() is allowed ahead of it: it writes one line to stderr.
        code = [line for line in code if not line.startswith("trace(")]
        self.assertTrue(code[0].startswith("if already_running()"),
                        f"first statement in main() is {code[0]!r}")

    def test_the_startup_trace_is_silent_unless_asked_for(self):
        """It runs on every launch, so it must cost nothing when off."""
        import io
        from unittest import mock

        stream = io.StringIO()
        with mock.patch.object(cufflink, "_TIMING", False), \
                mock.patch("sys.stderr", stream):
            cufflink.trace("should not appear")
        self.assertEqual(stream.getvalue(), "")

    def test_the_startup_trace_says_something_when_asked(self):
        import io
        from unittest import mock

        stream = io.StringIO()
        with mock.patch.object(cufflink, "_TIMING", True), \
                mock.patch("sys.stderr", stream):
            cufflink.trace("a landmark")
        self.assertIn("a landmark", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
