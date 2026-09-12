"""Tests for the listener: everything about hearing that needs no microphone.

    venv\\Scripts\\python.exe -m unittest discover -s tests -v

Nothing here loads Whisper. The model is 483 MB and none of it is needed to
check the parts that decide *what gets transcribed* -- where an utterance
starts, where it stops, and which source it came from. Those decisions shape
accuracy more than the model size does: a cut through the middle of a clause
costs more words than dropping from small to base ever would.
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from listener import (  # noqa: E402
    FRAME,
    FRAME_SECONDS,
    MAX_UTTERANCE,
    MIN_UTTERANCE,
    MODELS,
    PRE_ROLL,
    SAMPLE_RATE,
    SILENCE_TO_CUT,
    THEM,
    WARMUP_FRAMES,
    YOU,
    EnergyGate,
    Line,
    Meeting,
    Segmenter,
    Transcript,
    WhisperTranscriber,
    mono16k,
    rms_db,
)


# --------------------------------------------------------------- signals

def quiet(seconds):
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


def hiss(seconds, db=-40.0, seed=0):
    """Room tone: a fan, a laptop, the far end's open microphone."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(seconds * SAMPLE_RATE)).astype(np.float32)
    return (x / np.sqrt(np.mean(x ** 2)) * 10 ** (db / 20)).astype(np.float32)


def voice(seconds, db=-20.0, hz=180.0):
    """Stands in for a voice: a low tone at a level a speaker would hit."""
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    amp = 10 ** (db / 20) * np.sqrt(2)  # a sine's RMS is its amplitude / root two
    return (np.sin(2 * np.pi * hz * t) * amp).astype(np.float32)


def framed(*signals):
    """Chop a signal into the frames a capture callback delivers."""
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


class Canned:
    """A transcriber that says what it is told to, and counts being asked.

    Standing in for Whisper is the whole point of the seam: the segmenter can
    be wrong about where a sentence ends without a 483 MB download to find out.
    """

    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = []

    def transcribe(self, audio, sample_rate=SAMPLE_RATE):
        self.calls.append(len(audio) / sample_rate)
        return self.texts.pop(0) if self.texts else "..."


# ------------------------------------------------------------------ gate

class Gate(unittest.TestCase):
    """Whether a frame is speech. Everything downstream rests on it."""

    def opened(self, gate, frames):
        return [gate(f) for f in frames]

    def test_digital_silence_is_never_speech(self):
        gate = EnergyGate()
        self.assertFalse(any(self.opened(gate, framed(quiet(2.0)))))

    def test_a_voice_over_silence_is_speech(self):
        gate = EnergyGate()
        opened = self.opened(gate, framed(quiet(0.5), voice(0.5)))
        self.assertTrue(all(opened[-10:]))

    def test_steady_room_tone_is_not_speech(self):
        """A fan is loud in absolute terms and says nothing. The gate has to
        learn the room, or a meeting transcribes as one endless utterance."""
        gate = EnergyGate()
        opened = self.opened(gate, framed(hiss(3.0)))
        self.assertFalse(any(opened[len(opened) // 2:]),
                         "the gate never settled on the noise floor")

    def test_a_voice_over_room_tone_is_still_speech(self):
        gate = EnergyGate()
        frames = framed(hiss(1.0), hiss(1.0) + voice(1.0))
        opened = self.opened(gate, frames)
        self.assertTrue(all(opened[-20:]), "speech was lost in the room tone")

    def test_the_floor_does_not_drift_up_during_a_long_utterance(self):
        """If the noise estimate followed speech it would swallow the second
        half of anything said for more than a few seconds."""
        gate = EnergyGate()
        self.opened(gate, framed(hiss(1.0)))
        settled = gate.noise_db
        self.opened(gate, framed(voice(8.0)))
        self.assertAlmostEqual(gate.noise_db, settled, delta=0.5)

    def test_the_room_is_learned_down_fast_and_up_slowly(self):
        """The dangerous sound is the one louder than the room but too quiet
        to be speech -- a far-off voice, the tail of a word. Believing it as
        fast as silence walks the threshold up over the rest of the sentence."""
        gate = EnergyGate()
        self.opened(gate, framed(hiss(1.0, db=-50)))
        settled = gate.noise_db
        self.opened(gate, framed(hiss(1.0, db=-43, seed=1)))
        self.assertLess(gate.noise_db - settled, 5.0, "the floor chased it up")
        self.opened(gate, framed(hiss(1.0, db=-50, seed=2)))
        self.assertAlmostEqual(gate.noise_db, -50.0, delta=1.0,
                               msg="the floor did not come back down")

    def test_the_warm_up_learns_the_quietest_frame_and_not_the_last(self):
        """Measured on samples/bf_emma.wav, which starts talking 30 ms in: the
        calibration landed on the word "Hey" and set the floor at -21 dB, so
        the gate then chattered through the whole sentence and the first word
        was never heard at all. The room is the quietest thing in the window,
        not the most recent."""
        gate = EnergyGate()
        opened = self.opened(gate, framed(quiet(0.05), voice(1.0)))
        self.assertTrue(all(opened[WARMUP_FRAMES:]),
                        "the gate calibrated on the voice")

    def test_the_warm_up_is_never_longer_than_the_run_up(self):
        """Nothing said while the gate is still learning the room is lost,
        because the pre-roll is still holding all of it."""
        self.assertLessEqual(WARMUP_FRAMES * FRAME_SECONDS, PRE_ROLL)

    def test_rms_db_of_silence_is_far_below_anything_audible(self):
        self.assertLess(rms_db(quiet(0.03)), -100)


# ------------------------------------------------------------- segmenter

class Segmenting(unittest.TestCase):
    """Where the cuts go. Whisper sees one utterance at a time, so a cut is
    the edge of its context: mid-clause, it loses the words either side."""

    def test_a_silent_room_produces_nothing(self):
        self.assertEqual(run(Segmenter(YOU), framed(hiss(5.0))), [])

    def test_one_burst_bounded_by_silence_is_one_utterance(self):
        got = run(Segmenter(YOU), framed(quiet(0.5), voice(1.0), quiet(1.5)))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].source, YOU)

    def test_a_long_gap_ends_the_utterance(self):
        got = run(Segmenter(YOU),
                  framed(quiet(0.5), voice(1.0), quiet(1.5), voice(1.0), quiet(1.5)))
        self.assertEqual(len(got), 2)

    def test_a_breath_does_not(self):
        """People pause. Cutting at every one of them hands Whisper half a
        clause at a time, which is where transcripts stop reading as English."""
        gap = SILENCE_TO_CUT / 2
        got = run(Segmenter(YOU),
                  framed(quiet(0.5), voice(1.0), quiet(gap), voice(1.0), quiet(1.5)))
        self.assertEqual(len(got), 1)

    def test_nothing_said_is_lost(self):
        """The audio handed to Whisper contains every sample of the speech.
        A gate that opens late clips the first consonant, and 'sit' and 'it'
        are one frame apart."""
        said = voice(1.0)
        got = run(Segmenter(YOU), framed(quiet(0.5), said, quiet(1.5)))
        loud = np.count_nonzero(np.abs(got[0].audio) > 0.01)
        self.assertEqual(loud, np.count_nonzero(np.abs(said) > 0.01))

    def test_it_keeps_a_run_up_and_a_tail(self):
        got = run(Segmenter(YOU), framed(quiet(1.0), voice(1.0), quiet(1.5)))
        self.assertAlmostEqual(got[0].start, 0.7, delta=0.1)
        self.assertAlmostEqual(got[0].end, 2.4, delta=0.1)

    def test_the_run_up_is_audio_and_not_arithmetic(self):
        """A start time that is early while the samples are not is a clipped
        first word wearing an honest timestamp."""
        got = run(Segmenter(YOU), framed(quiet(1.0), voice(1.0), quiet(1.5)))
        loud = np.flatnonzero(np.abs(got[0].audio) > 0.01)
        self.assertGreater(loud[0] / SAMPLE_RATE, 0.2, "no run-up in the audio")
        self.assertGreater((len(got[0].audio) - loud[-1]) / SAMPLE_RATE, 0.2,
                           "no tail in the audio")

    def test_a_monologue_is_cut_anyway(self):
        """Whisper's window is thirty seconds. Someone who talks for two
        minutes without a break still has to appear in the transcript."""
        got = run(Segmenter(YOU), framed(quiet(0.5), voice(70.0), quiet(1.5)))
        self.assertGreaterEqual(len(got), 3)
        for u in got:
            self.assertLessEqual(u.end - u.start, MAX_UTTERANCE + 0.2)

    def test_a_click_is_not_an_utterance(self):
        """A key press or a chair opens the gate for a frame or two. Sending
        that to Whisper is how transcripts fill up with invented sentences."""
        got = run(Segmenter(YOU), framed(quiet(0.5), voice(0.06), quiet(1.5)))
        self.assertEqual(got, [])
        self.assertLess(0.06, MIN_UTTERANCE, "the click is no longer a click")

    def test_it_reports_how_much_of_it_was_voice(self):
        got = run(Segmenter(YOU), framed(quiet(0.5), voice(2.0), quiet(1.5)))
        self.assertAlmostEqual(got[0].voiced, 2.0, delta=0.2)
        self.assertLess(got[0].voiced, got[0].end - got[0].start)

    def test_timestamps_are_measured_from_the_first_frame(self):
        got = run(Segmenter(YOU), framed(quiet(10.0), voice(1.0), quiet(1.5)))
        self.assertAlmostEqual(got[0].start, 9.7, delta=0.1)

    def test_a_recording_that_opens_on_a_word_keeps_the_word(self):
        """A file with no lead-in, or a meeting joined mid-sentence. There is
        no quiet room to learn from, so the guess has to fall on the side of
        hearing too much."""
        said = voice(2.0)
        got = run(Segmenter(YOU), framed(quiet(0.05), said, quiet(1.5)))
        self.assertEqual(len(got), 1)
        # Every sample survives, because the run-up buffer was filling all
        # through the calibration. `voiced` still undercounts by the length of
        # the warm-up, which only makes a short opening line look more
        # doubtful than it is.
        self.assertEqual(np.count_nonzero(np.abs(got[0].audio) > 0.01),
                         np.count_nonzero(np.abs(said) > 0.01))
        self.assertGreater(got[0].voiced, 1.6)

    def test_flush_does_not_drop_what_was_still_being_said(self):
        """A meeting that ends while someone is mid-sentence keeps it."""
        seg = Segmenter(YOU)
        for f in framed(quiet(0.5), voice(2.0)):
            self.assertIsNone(seg.push(f))
        self.assertIsNotNone(seg.flush())


# ------------------------------------------------------------ transcript

class Transcripts(unittest.TestCase):

    def line(self, start, source=YOU, text="hello", suspect=False):
        return Line(source=source, start=start, end=start + 1.0,
                    text=text, suspect=suspect)

    def test_lines_are_kept_in_time_order(self):
        """Two sources are transcribed independently and land out of order --
        a long utterance finishes after a short one that started later."""
        t = Transcript()
        for start in (5.0, 1.0, 3.0):
            t.add(self.line(start))
        self.assertEqual([l.start for l in t.lines], [1.0, 3.0, 5.0])

    def test_it_says_who_and_when(self):
        t = Transcript()
        t.add(self.line(63.0, source=THEM, text="over to you"))
        self.assertIn("01:03", t.render())
        self.assertIn(THEM, t.render())
        self.assertIn("over to you", t.render())

    def test_long_meetings_get_an_hour_field(self):
        t = Transcript()
        t.add(self.line(3723.0))
        self.assertIn("1:02:03", t.render())

    def test_nothing_heard_is_not_a_line(self):
        """Whisper returns an empty string, or a lone full stop, on silence."""
        t = Transcript()
        for text in ("", "   ", ".", " ... "):
            t.add(self.line(1.0, text=text))
        self.assertEqual(t.lines, [])

    def test_a_suspect_line_is_marked_rather_than_dropped(self):
        """'As verbatim as possible' means a doubtful line is still a line.
        Deleting it loses a real sentence every time the guess is wrong;
        marking it loses nothing."""
        t = Transcript()
        t.add(self.line(1.0, text="Thank you.", suspect=True))
        self.assertEqual(len(t.lines), 1)
        self.assertIn("Thank you.", t.render())
        self.assertIn("?", t.render())


class Hallucinations(unittest.TestCase):
    """Whisper fills near-silence with whatever its training data put after
    silence -- subtitle credits, mostly. They are the most common wrong line
    in an unattended transcript."""

    def suspect(self, text, voiced=0.2):
        return Transcript.is_suspect(text, voiced)

    def test_the_usual_subtitle_credits_are_suspect(self):
        for text in ("Thank you.", "Thanks for watching!",
                     "Ondertiteling door de Amara.org gemeenschap",
                     "Ondertiteld door de Amara.org gemeenschap"):
            with self.subTest(text=text):
                self.assertTrue(self.suspect(text))

    def test_only_when_there_was_barely_any_voice(self):
        """Said out loud, into a microphone, for a second and a half, 'thank
        you' is a sentence somebody said and belongs in the minutes plain."""
        self.assertFalse(self.suspect("Thank you.", voiced=1.5))

    def test_real_speech_is_never_suspect(self):
        self.assertFalse(self.suspect("Thanks, let us move to the next item."))


# --------------------------------------------------------------- meeting

class Meetings(unittest.TestCase):
    """The wiring: two sources in, one attributed transcript out."""

    def test_who_said_it_comes_from_which_stream_it_arrived_on(self):
        """No diarisation. Your microphone is you; what the speakers play is
        everyone else. It is the one attribution that cannot be wrong."""
        m = Meeting(Canned("mine", "theirs"))
        for f in framed(quiet(0.5), voice(1.0), quiet(1.5)):
            m.feed(YOU, f)
        for f in framed(quiet(0.5), voice(1.0), quiet(1.5)):
            m.feed(THEM, f)
        self.assertEqual([(l.source, l.text) for l in m.transcript.lines],
                         [(YOU, "mine"), (THEM, "theirs")])

    def test_each_source_learns_its_own_noise_floor(self):
        """A headset microphone and a conference call arrive at completely
        different levels; one shared threshold gets one of them wrong."""
        m = Meeting(Canned())
        for f in framed(hiss(1.0, db=-60)):
            m.feed(YOU, f)
        for f in framed(hiss(1.0, db=-30)):
            m.feed(THEM, f)
        self.assertGreater(m.segmenters[THEM].gate.noise_db,
                           m.segmenters[YOU].gate.noise_db + 20)

    def test_a_quiet_room_never_reaches_the_model(self):
        """Idle cost is the thing this project keeps getting right. Silence
        must not run inference."""
        canned = Canned()
        m = Meeting(canned)
        for f in framed(hiss(10.0)):
            m.feed(YOU, f)
        self.assertEqual(canned.calls, [])

    def test_lines_are_announced_as_they_land(self):
        heard = []
        m = Meeting(Canned("first"), on_line=heard.append)
        for f in framed(quiet(0.5), voice(1.0), quiet(1.5)):
            m.feed(YOU, f)
        self.assertEqual([l.text for l in heard], ["first"])

    def test_closing_transcribes_what_was_still_in_hand(self):
        m = Meeting(Canned("cut off mid-"))
        for f in framed(quiet(0.5), voice(2.0)):
            m.feed(YOU, f)
        self.assertEqual(m.transcript.lines, [])
        m.close()
        self.assertEqual(len(m.transcript.lines), 1)


# ----------------------------------------------------------------- model

class Models(unittest.TestCase):

    def test_the_table_is_ordered_by_size(self):
        sizes = [m.size for m in MODELS.values()]
        self.assertEqual(sizes, sorted(sizes))

    def test_every_model_names_a_repo_to_fetch_it_from(self):
        for name, model in MODELS.items():
            with self.subTest(model=name):
                self.assertRegex(model.repo, r"^[\w-]+/[\w.-]+$")

    def test_nothing_is_loaded_until_something_is_said(self):
        """Constructing the transcriber must not touch disk or the network --
        Murmur already pays ten seconds for the voice at startup, and the
        listener is not going to add half a gigabyte to that."""
        t = WhisperTranscriber("tiny")
        self.assertIsNone(t._model)

    def test_an_unknown_size_is_refused_up_front(self):
        with self.assertRaises(ValueError):
            WhisperTranscriber("enormous")


# ------------------------------------------------------------ resampling

class Resampling(unittest.TestCase):
    """Windows hands over 48 kHz stereo; Whisper wants 16 kHz mono."""

    def test_it_lands_on_the_right_length(self):
        stereo = np.zeros((48000, 2), dtype=np.float32)
        self.assertAlmostEqual(len(mono16k(stereo, 48000)), 16000, delta=2)

    def test_a_rate_that_already_matches_is_left_alone(self):
        mono = voice(0.5)
        self.assertEqual(len(mono16k(mono, SAMPLE_RATE)), len(mono))

    def test_the_two_channels_are_averaged(self):
        stereo = np.stack([np.ones(1600), np.zeros(1600)], axis=1).astype(np.float32)
        self.assertAlmostEqual(float(mono16k(stereo, SAMPLE_RATE).mean()), 0.5, places=3)

    def test_a_voice_keeps_its_level(self):
        """Resampling that halves the amplitude moves every gate threshold."""
        wide = np.repeat(voice(1.0, db=-20.0), 3)  # the same tone at 48 kHz
        self.assertAlmostEqual(rms_db(mono16k(wide, 48000)), -20.0, delta=1.5)


if __name__ == "__main__":
    unittest.main()
