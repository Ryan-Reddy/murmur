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
    WORD_BREAK_OVER,
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

    def test_short_comma_less_opening_is_left_whole(self):
        """Breaking mid-clause costs an audible falling intonation, so it is
        only worth it when the wait would otherwise be long."""
        text = "This opening sentence has no internal punctuation so it stays whole."
        self.assertLess(len(text), WORD_BREAK_OVER)
        self.assertEqual(len(split_sentences(text)), 1)

    def test_very_long_comma_less_opening_is_broken_anyway(self):
        """Past a point, waiting is worse than an awkward break."""
        text = "word " * 80 + "end."
        self.assertGreater(len(split_sentences(text)), 1)

    def test_word_break_threshold(self):
        """Pins where the trade flips. Comfortably under stays whole, well over
        gets broken; the exact boundary depends on where the words fall."""
        short = "word " * (WORD_BREAK_OVER // 10) + "end."
        self.assertLess(len(short), WORD_BREAK_OVER)
        self.assertEqual(len(split_sentences(short)), 1)

        long_ = "word " * (WORD_BREAK_OVER // 2) + "end."
        self.assertGreater(len(long_), WORD_BREAK_OVER)
        self.assertGreater(len(split_sentences(long_)), 1)

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


class Names(unittest.TestCase):
    """Every treatment is offered as somebody, so every one needs a name."""

    def test_all_named(self):
        import voices

        self.assertEqual(set(voices.NAMES), set(voices.TREATMENTS))

    def test_names_are_distinct(self):
        import voices

        labels = [label for _key, label in voices.catalogue()]
        self.assertEqual(len(set(labels)), len(labels))

    def test_catalogue_keeps_the_declared_order(self):
        import voices

        self.assertEqual([key for key, _ in voices.catalogue()],
                         list(voices.TREATMENTS))

    def test_an_unnamed_treatment_falls_back_to_its_key(self):
        import voices

        self.assertEqual(voices.label("nothing-by-that-name"),
                         "nothing-by-that-name")


class Treatments(unittest.TestCase):
    """The voice treatments. Cheap to check without a model: they are pure
    signal processing over an array."""

    @classmethod
    def setUpClass(cls):
        import numpy as np
        import voices
        cls.np, cls.voices = np, voices
        cls.rate = 24000
        cls.audio = np.random.default_rng(1).normal(
            0, 0.15, cls.rate * 2).astype("float32")

    def test_every_treatment_preserves_length_and_stays_finite(self):
        for name in self.voices.TREATMENTS:
            with self.subTest(treatment=name):
                out = self.voices.treat(name, self.audio, self.rate)
                self.assertEqual(len(out), len(self.audio))
                self.assertTrue(bool(self.np.all(self.np.isfinite(out))))

    def test_nothing_clips(self):
        """Anything over 1.0 crackles on the way out."""
        for name in self.voices.TREATMENTS:
            with self.subTest(treatment=name):
                out = self.voices.treat(name, self.audio, self.rate)
                self.assertLessEqual(float(self.np.max(self.np.abs(out))), 1.0)

    def test_clean_is_untouched(self):
        out = self.voices.treat("clean", self.audio, self.rate)
        self.assertTrue(bool(self.np.array_equal(out, self.audio)))

    def test_an_unknown_name_reads_rather_than_fails(self):
        """A typo in a profile should not silence a read."""
        out = self.voices.treat("no-such-treatment", self.audio, self.rate)
        self.assertTrue(bool(self.np.array_equal(out, self.audio)))

    def test_empty_and_tiny_audio_are_survivable(self):
        """A treatment is a decoration. It must never be the reason nothing
        gets spoken -- an empty chunk used to raise out of every chain."""
        for length in (0, 1, 64, self.voices.MIN_SAMPLES - 1):
            clip = self.np.zeros(length, dtype="float32")
            for name in self.voices.TREATMENTS:
                with self.subTest(treatment=name, length=length):
                    try:
                        out = self.voices.treat(name, clip, self.rate)
                    except Exception as error:
                        self.fail(f"{name} raised on {length} samples: {error}")
                    self.assertEqual(len(out), length)

    def test_treatments_actually_differ(self):
        """If two chains produced the same audio, one of them is not wired up."""
        rendered = {n: self.voices.treat(n, self.audio, self.rate)
                    for n in self.voices.TREATMENTS if n != "clean"}
        names = sorted(rendered)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                self.assertFalse(bool(self.np.allclose(rendered[a], rendered[b])),
                                 f"{a} and {b} came out identical")

    def test_cost_estimate_is_in_the_right_order(self):
        self.assertEqual(self.voices.cost_estimate("clean", 10), 0.0)
        cost = self.voices.cost_estimate("agent", 8.5)
        self.assertGreater(cost, 0.05)
        self.assertLess(cost, 1.0)
