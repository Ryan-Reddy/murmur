"""Tests for where a monologue gets cut.

    venv\\Scripts\\python.exe -m unittest discover -s tests -v

Whisper's window is thirty seconds and it ignores the rest, so somebody who
talks without pausing has to be cut somewhere. A cut is the edge of its
context, so a cut mid-clause loses words on both sides of the join -- which
makes *where* the only interesting question. The clock is the worst answer
available; the quietest moment in the last few seconds is close to where a
person would put it.
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from listener import (  # noqa: E402
    FRAME, MAX_UTTERANCE, SAMPLE_RATE, YOU, Segmenter,
)


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


def run(segmenter, frames):
    out = []
    for f in frames:
        got = segmenter.push(f)
        if got is not None:
            out.append(got)
    last = segmenter.flush()
    if last is not None:
        out.append(last)
    return out


def loudness(audio, at, window=0.2):
    """How loud the audio is around a point, in dBFS."""
    i = int(at * SAMPLE_RATE)
    half = int(window * SAMPLE_RATE / 2)
    piece = audio[max(0, i - half):i + half]
    if not len(piece):
        return -240.0
    return float(20 * np.log10(np.sqrt(np.mean(piece.astype(np.float64) ** 2)) + 1e-12))


class CuttingALongOne(unittest.TestCase):

    def speech_with_a_dip_near_the_limit(self):
        """Unbroken speech, with one quiet moment shortly before the cut has
        to happen -- a breath between sentences, too short to end anything."""
        before = MAX_UTTERANCE - 1.2
        return framed(quiet(0.5), voice(before), quiet(0.25),
                      voice(6.0), quiet(1.5))

    def test_it_still_cuts(self):
        got = run(Segmenter(YOU), self.speech_with_a_dip_near_the_limit())
        self.assertGreaterEqual(len(got), 2)

    def test_no_piece_is_longer_than_the_window_allows(self):
        for u in run(Segmenter(YOU), self.speech_with_a_dip_near_the_limit()):
            self.assertLessEqual(u.end - u.start, MAX_UTTERANCE + 0.2)

    def test_the_cut_lands_on_the_quiet_moment(self):
        """Not at MAX_UTTERANCE, which is a second later and mid-word."""
        got = run(Segmenter(YOU), self.speech_with_a_dip_near_the_limit())
        first = got[0]
        self.assertAlmostEqual(first.end, 0.5 + (MAX_UTTERANCE - 1.2) + 0.25,
                               delta=0.5)

    def test_it_ends_somewhere_quiet(self):
        """The real property, independent of where the dip was put: the last
        moment of a forced cut is not in the middle of a loud word.

        Measured over the final 80 ms rather than a wider window. The gap
        being aimed at is only 250 ms, and a cut placed correctly in the
        middle of it leaves ~120 ms of quiet either side -- so anything
        wider than that necessarily catches the speech it was cutting
        between, and would fail a cut that is exactly right.
        """
        got = run(Segmenter(YOU), self.speech_with_a_dip_near_the_limit())
        ended = got[0].end - got[0].start
        tail = loudness(got[0].audio, ended - 0.045, window=0.08)
        self.assertLess(tail, -35.0, "the cut landed mid-word")

    def test_what_came_after_the_cut_is_not_thrown_away(self):
        """Everything after the join belongs to the next utterance. Dropping
        it would lose a second of speech at every cut, silently."""
        got = run(Segmenter(YOU), self.speech_with_a_dip_near_the_limit())
        loud = sum(int(np.count_nonzero(np.abs(u.audio) > 0.01)) for u in got)
        spoken = np.count_nonzero(
            np.abs(np.concatenate([voice(MAX_UTTERANCE - 1.2), voice(6.0)])) > 0.01)
        self.assertGreater(loud, spoken * 0.95)

    def test_unbroken_speech_with_nowhere_good_still_gets_cut(self):
        """A tone has no quiet moment anywhere. The clock has to win, or a
        monologue never lands in the transcript at all."""
        got = run(Segmenter(YOU), framed(quiet(0.5), voice(60.0), quiet(1.5)))
        self.assertGreaterEqual(len(got), 2)
        for u in got:
            self.assertLessEqual(u.end - u.start, MAX_UTTERANCE + 0.2)

    def test_the_pieces_run_in_order_and_do_not_overlap(self):
        got = run(Segmenter(YOU), framed(quiet(0.5), voice(60.0), quiet(1.5)))
        for earlier, later in zip(got, got[1:]):
            self.assertLessEqual(earlier.end, later.start + 0.05)


if __name__ == "__main__":
    unittest.main()
