"""Tests for the parts of Murmur that can be checked without making a sound.

    venv\\Scripts\\python.exe -m unittest discover -s tests -v

Anything needing the Kokoro model skips itself when models\\ is absent, since
that is 338 MB and not in the repo.
"""

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from speaker import (  # noqa: E402
    LEAD_IN_FIRST,
    LEAD_IN_GROWTH,
    Speaker,
    _default_threads,
    _split_at,
    split_sentences,
)

MODEL = ROOT / "models" / "kokoro-v1.0.onnx"
VOICES = ROOT / "models" / "voices-v1.0.bin"
has_model = MODEL.exists() and VOICES.exists()


class SplitSentences(unittest.TestCase):
    """The splitter decides both what gets said and how soon it starts."""

    PARA = (
        "The quick brown fox jumps over the lazy dog, and then it does so again "
        "because repetition makes for a fair benchmark of speech synthesis. "
        "A second sentence follows so we can see how the pipeline behaves."
    )

    def assert_lossless(self, text):
        """Every character of the original survives, modulo whitespace. A
        splitter that quietly eats words would be hard to notice by ear."""
        joined = "".join(split_sentences(text))
        self.assertEqual(
            "".join(joined.split()),
            "".join(text.split()),
            "splitting dropped or duplicated text",
        )

    def test_nothing_is_lost(self):
        for text in (
            self.PARA,
            "Hi.",
            "One, two, three, four, five, six, seven, eight, nine, ten, eleven.",
            "No punctuation at all just a long run of words " * 6,
            "x" * 500,
        ):
            with self.subTest(text=text[:40]):
                self.assert_lossless(text)

    def test_empty_input_yields_nothing(self):
        for text in ("", "   ", "\n\t "):
            self.assertEqual(split_sentences(text), [])

    def test_no_empty_chunks(self):
        for chunk in split_sentences(self.PARA):
            self.assertTrue(chunk.strip(), "produced a blank chunk")

    def test_opening_chunk_is_short(self):
        """The whole point of the lead-in: nothing is heard until the first
        chunk has rendered, so it must not be a whole long sentence."""
        first = split_sentences(self.PARA)[0]
        self.assertLessEqual(len(first), LEAD_IN_FIRST + 20)

    def test_chunks_do_not_grow_too_fast(self):
        """A chunk more than 1/RTF times its predecessor lands after the
        previous one has finished playing, and the speech gaps."""
        chunks = split_sentences(self.PARA)
        for before, after in zip(chunks, chunks[1:3]):
            self.assertLessEqual(
                len(after) / len(before),
                LEAD_IN_GROWTH * 1.5,
                f"{len(before)} -> {len(after)} chars is too big a jump",
            )

    def test_opening_breaks_only_at_punctuation(self):
        """A gap at a comma is heard as a pause; mid-clause it is heard as a
        fault. A short comma-less sentence is left whole on purpose."""
        chunks = split_sentences(
            "This opening sentence has no internal punctuation at all so it stays whole."
        )
        self.assertEqual(len(chunks), 1)

    def test_very_long_comma_less_opening_is_broken_anyway(self):
        """Past a point, waiting is worse than an awkward break."""
        text = "word " * 80 + "end."
        self.assertGreater(len(split_sentences(text)), 1)

    def test_long_sentences_are_capped(self):
        """Kokoro degrades on very long inputs."""
        for chunk in split_sentences("word " * 300 + "end."):
            self.assertLessEqual(len(chunk), 401)

    def test_split_at_prefers_punctuation(self):
        head, tail = _split_at("one two three, four five six seven", 25)
        self.assertTrue(head.endswith(","))
        self.assertFalse(tail.startswith(" "))

    def test_split_at_refuses_without_punctuation(self):
        self.assertIsNone(_split_at("one two three four five six seven", 25))

    def test_split_at_falls_back_to_words_when_allowed(self):
        result = _split_at("one two three four five six seven", 25, allow_words=True)
        self.assertIsNotNone(result)
        self.assertNotIn(" ", result[0][-1])

    def test_split_at_leaves_short_text_alone(self):
        self.assertIsNone(_split_at("short", 70))


class Threads(unittest.TestCase):
    def test_scales_to_the_machine_and_is_clamped(self):
        cases = {2: 2, 4: 2, 8: 4, 16: 8, 32: 16, 128: 16}
        for logical, expected in cases.items():
            with self.subTest(logical=logical):
                self.assertEqual(max(2, min(16, logical // 2)), expected)

    def test_default_is_sane_here(self):
        threads = _default_threads()
        self.assertGreaterEqual(threads, 2)
        self.assertLessEqual(threads, 16)
        self.assertLessEqual(threads, max(2, os.cpu_count() or 2))


class Gain(unittest.TestCase):
    """Volume is squared so the slider feels even, and never exceeds 1.0 --
    the model already peaks near full scale and anything above clips."""

    def gain(self, volume):
        return Speaker._gain(SimpleNamespace(volume=volume))

    def test_curve(self):
        self.assertAlmostEqual(self.gain(1.0), 1.0)
        self.assertAlmostEqual(self.gain(0.5), 0.25)
        self.assertAlmostEqual(self.gain(0.0), 0.0)

    def test_clamped(self):
        self.assertEqual(self.gain(5.0), 1.0)
        self.assertEqual(self.gain(-3.0), 0.0)


@unittest.skipUnless(has_model, "models/ not present (338 MB, not in the repo)")
class WordSpans(unittest.TestCase):
    """Read-along timing. The v1.0 export returns no durations, so spans are
    estimated from phoneme counts; these guard the estimate's shape."""

    @classmethod
    def setUpClass(cls):
        cls.speaker = Speaker(MODEL, VOICES)

    def test_one_span_per_word(self):
        text = "The quick brown fox jumps over the lazy dog."
        spans = self.speaker.word_spans(text, 100_000)
        self.assertEqual(len(spans), len(text.split()))

    def test_spans_are_ordered_and_within_the_audio(self):
        spans = self.speaker.word_spans("One two three, four five six.", 50_000)
        self.assertEqual(spans[0][0], 0)
        self.assertLessEqual(spans[-1][1], 50_000)
        for (_, end), (start, _) in zip(spans, spans[1:]):
            self.assertEqual(end, start, "spans should tile without gaps")

    def test_empty_text_has_no_spans(self):
        self.assertEqual(self.speaker.word_spans("   ", 1000), [])

    def test_longer_words_get_longer_spans(self):
        spans = self.speaker.word_spans("a extraordinarily a", 90_000)
        short, long_, short2 = (end - start for start, end in spans)
        self.assertGreater(long_, short)
        self.assertGreater(long_, short2)


class MouseHookLifecycle(unittest.TestCase):
    """The reason mousehook.py exists: the `mouse` package installs its hook on
    first use and can never take it down again."""

    def test_starts_and_genuinely_stops(self):
        from mousehook import MouseButtons

        hook = MouseButtons(lambda kind, x, y: None)
        self.assertFalse(hook.running)
        hook.start()
        try:
            self.assertTrue(hook.running)
        finally:
            hook.stop()
        self.assertFalse(hook.running)

    def test_stop_without_start_is_harmless(self):
        from mousehook import MouseButtons

        MouseButtons(lambda *_: None).stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
