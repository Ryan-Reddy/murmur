"""Reusable offline speech-to-text listener built on Whisper. No UI deps.

The mirror of `speaker.py`: that one turns text into sound, this one turns
sound into text. Same rules -- everything local, nothing on the network after
the model is fetched once, and nothing running while nobody is talking.

Usage:
    from listener import Meeting, WhisperTranscriber, YOU, THEM

    meeting = Meeting(WhisperTranscriber("small"), on_line=print)
    for frame in capture(source=YOU):        # 30 ms of 16 kHz mono at a time
        meeting.feed(YOU, frame)
    meeting.close()
    print(meeting.transcript.render())

Who said what comes from *which stream it arrived on*, not from diarisation:
your microphone is you, and whatever the speakers are playing is everybody
else. Two capture streams, two labels, no clustering model, no gated
huggingface download, and no chance of the attribution being wrong -- at the
cost of everyone at the far end being one voice called "them".

What this cannot do, honestly: Whisper is a transcriber, not a stenographer.
It punctuates, it tidies, and it drops most disfluencies -- "um" and "you
know" usually do not survive. "As verbatim as possible" here means every
sentence, in order, attributed and timestamped, with nothing summarised away;
it does not mean every sound anybody made.
"""

import bisect
import re
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

# Whisper is trained at 16 kHz mono and resamples anything else itself, so
# there is nothing to gain by capturing higher and plenty of CPU to lose.
SAMPLE_RATE = 16000
FRAME = 480                     # 30 ms, the usual voice-activity frame
FRAME_SECONDS = FRAME / SAMPLE_RATE

# The two sources. They are plain strings so a third ("phone", say) costs
# nothing, but these two are what the wiring assumes.
YOU = "you"
THEM = "them"


# ------------------------------------------------------------------ models

@dataclass(frozen=True)
class Model:
    repo: str
    size: int                   # bytes of the CTranslate2 float16 export
    note: str


# Sizes are the actual `model.bin` in each repo, read from the HuggingFace API
# rather than remembered, since the difference between "about 500 MB" and
# "1.5 GB" is the difference between shipping it and not. int8 quantisation
# roughly halves each of these on disk and in memory, which is why it is the
# default compute type below.
#
# Kokoro, for comparison, is 338 MB. Anything above `small` makes the listener
# the largest thing in the install by a wide margin.
MODELS = {
    "tiny":     Model("Systran/faster-whisper-tiny", 75_538_270,
                      "fast and sloppy; fine for 'is anyone speaking'"),
    "base":     Model("Systran/faster-whisper-base", 145_217_532,
                      "still loses names and technical words"),
    "small":    Model("Systran/faster-whisper-small", 483_546_902,
                      "the usual sweet spot for dictation"),
    "medium":   Model("Systran/faster-whisper-medium", 1_527_906_378,
                      "noticeably better on accents; heavy for a tray app"),
    "large-v3": Model("Systran/faster-whisper-large-v3", 3_087_284_237,
                      "best available, and ten times Kokoro on disk"),
}
DEFAULT_MODEL = "small"


# CTranslate2 picks its own thread count, and on this machine that left
# `small` at a real-time factor of 1.23 -- slower than people talk, so a live
# meeting falls further behind every minute and never recovers. Measured over
# three samples on a 16-core/32-thread Threadripper:
#
#   model   threads  beam    RTF
#   tiny       auto     5   0.17
#   base       auto     5   0.34
#   base          8     1   0.25
#   small      auto     5   1.23   <- cannot keep up
#   small        16     5   0.79
#   small        16     1   0.69
#
# So the same rule `speaker.py` uses for ONNX: half the logical processors,
# which is the physical core count, capped at 16 and floored at 2. Past the
# physical cores SMT contention makes both axes worse, which is the finding
# that section of DECISIONS.md is built on.
def _default_threads() -> int:
    import os

    return max(2, min(16, (os.cpu_count() or 4) // 2))


# The files faster-whisper needs before a directory is loadable. An
# interrupted copy leaves the folder there with the weights missing, and
# loading that fails deep inside CTranslate2 saying nothing useful.
MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")

# `base` ships with the app: it is the pass with a deadline, and without it
# the meeting tracker does not work at all until half a gigabyte has come
# down -- which it would start doing at the moment somebody began talking.
# `small` only improves a transcript that is already readable, so it is
# fetched later, in the background, and costs nobody who never opens it.
SHIPPED = "base"


def app_dir() -> "Path":
    from pathlib import Path

    return Path(__file__).resolve().parent


def model_cache() -> "Path":
    """Where a downloaded model is put.

    Never inside the repository. A Store install is read-only under
    `Program Files\\WindowsApps`, so a download beside the app fails there and
    nowhere else; and a cache in `models/` was silently added to every build
    of the package, taking it from 612 MB to 1823 MB.
    """
    import os
    from pathlib import Path

    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return base / "cufflink" / "models" / "whisper"


def find_model(size: str, shipped=None, cache=None):
    """A directory to load, or a size to fetch.

    faster-whisper takes either, and must never be given both: a download root
    alongside a directory is how a model lands next to one already there.
    """
    from pathlib import Path

    where = Path(shipped) if shipped is not None else app_dir() / "models"
    with_app = where / f"whisper-{size}"
    if all((with_app / name).exists() for name in MODEL_FILES):
        return str(with_app)
    return size


# ------------------------------------------------------------------- audio

def rms_db(frame) -> float:
    """Level of a frame in dBFS. Silence lands at -240 rather than -inf, so
    the arithmetic downstream never has to special-case it."""
    x = np.asarray(frame, dtype=np.float32)
    return float(20.0 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-12))


def mono16k(data, rate: int = SAMPLE_RATE):
    """Downmix and resample whatever the device gave us.

    A last resort: ask the device for 16 kHz mono in the first place if it
    will do it -- the WASAPI microphone on this machine reports 16 kHz as its
    default rate, so usually it will. This is straight linear interpolation
    with no anti-alias filter, which is audibly fine for speech and would not
    be for music.
    """
    x = np.asarray(data, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if rate == SAMPLE_RATE or len(x) == 0:
        return x
    wanted = int(round(len(x) * SAMPLE_RATE / rate))
    at = np.linspace(0.0, len(x) - 1.0, wanted)
    return np.interp(at, np.arange(len(x)), x).astype(np.float32)


# -------------------------------------------------------------------- gate

# How far above the room a frame has to sit before it counts as speech. Nine
# decibels is about twice as loud; below that the gate opens on a fan.
OPEN_OVER_DB = 9.0
# Never treat anything under this as the room, however quiet the room is --
# without it a digitally silent stream sets a floor of -240 dB and the gate
# opens on dither.
FLOOR_DB = -55.0
# The room is learned from the quiet frames only, and asymmetrically: a level
# drop is believed at once (someone stopped talking) and a rise is believed
# slowly (someone started). The other way round, a long sentence walks the
# floor up over itself and the end of it is heard as silence.
NOISE_FALL = 0.30
NOISE_RISE = 0.02
# The first 0.3 s is spent listening to the room rather than judging it, and
# what it takes from that window is the *quietest* frame in it. Taking the
# last one instead cost the first word of samples/bf_emma.wav, which starts
# talking 30 ms in: the calibration landed on "Hey", set the floor at -21 dB,
# and the gate then chattered through the whole sentence at -13 to -27.
#
# Never longer than PRE_ROLL, so nothing said while the room is still being
# learned is lost -- the run-up buffer is still holding all of it.
WARMUP_FRAMES = 10


class EnergyGate:
    """Is this frame speech? Level against a learned noise floor.

    Deliberately the dumbest thing that works, and deliberately behind an
    interface: anything with a `__call__(frame) -> bool` can replace it, and
    the obvious upgrade is the Silero VAD that faster-whisper already ships
    (onnxruntime is in the venv for Kokoro, so it costs no new dependency).
    Energy alone cannot tell a voice from a keyboard, and in a meeting there
    is a lot of keyboard.
    """

    def __init__(self, open_over_db: float = OPEN_OVER_DB,
                 floor_db: float = FLOOR_DB):
        self.open_over_db = open_over_db
        self.floor_db = floor_db
        self.noise_db = floor_db
        self.frames = 0
        self._quietest = float("inf")

    def __call__(self, frame) -> bool:
        db = rms_db(frame)
        self.frames += 1
        if self.frames <= WARMUP_FRAMES:
            self._quietest = min(self._quietest, db)
            self.noise_db = max(self.floor_db, self._quietest)
            return False
        if db > max(self.noise_db + self.open_over_db, self.floor_db):
            return True
        rate = NOISE_RISE if db > self.noise_db else NOISE_FALL
        self.noise_db = max(self.floor_db, self.noise_db + rate * (db - self.noise_db))
        return False


# --------------------------------------------------------------- segmenter

# Audio kept from before the gate opened. The gate needs a frame or two of
# evidence, and by then the first consonant is already past: "sit" and "it"
# differ by less than one frame.
PRE_ROLL = 0.3
# Audio kept after it closes, for the same reason at the other end.
HANGOVER = 0.4
# Silence that ends an utterance. A breath is 0.2-0.3 s and a clause gap
# around 0.5, so anything shorter than this cuts people off mid-sentence --
# and a cut is the edge of Whisper's context, so it loses the words on both
# sides of it, not just the join.
SILENCE_TO_CUT = 0.7
# Whisper's window is 30 seconds and it silently ignores the rest, so someone
# who talks without pausing has to be cut somewhere. This is that somewhere,
# with headroom for the run-up and tail. The cut lands wherever the clock says
# rather than at a pause, which is the one place this splitter knowingly makes
# a bad join; picking the quietest frame in the last second would be better.
MAX_UTTERANCE = 25.0
# Voice, not duration: below this it is a chair or a key press, and handing
# Whisper a quarter second of clatter is exactly how transcripts fill up with
# invented sentences.
MIN_UTTERANCE = 0.25
# How far back to look for a better place to break a monologue than the clock.
# Long enough to contain the breath between two sentences, short enough that
# the piece handed over is not meaningfully shorter than the window allows.
CUT_LOOK_BACK = 2.5
# How much quieter than its neighbours a frame has to be before it counts as a
# gap worth cutting at. On unbroken speech -- or a test tone -- every frame is
# much the same, and then the clock is the honest answer.
CUT_DIP_DB = 6.0


@dataclass(eq=False)
class Utterance:
    """One stretch of one source's audio, bounded by silence."""
    source: str
    start: float                # seconds since this source started capturing
    end: float
    voiced: float               # of which this much had the gate open
    audio: np.ndarray


class Segmenter:
    """Frames in, utterances out. One per source -- a headset and a conference
    call arrive at completely different levels, and a shared noise floor gets
    one of them wrong."""

    def __init__(self, source: str, gate=None):
        self.source = source
        self.gate = gate if gate is not None else EnergyGate()
        self.pre = deque(maxlen=max(1, round(PRE_ROLL / FRAME_SECONDS)))
        self.frames: list = []
        self.start: Optional[float] = None
        self.voiced = 0.0
        self.silence = 0.0
        self.elapsed = 0.0

    def push(self, frame) -> Optional[Utterance]:
        speaking = self.gate(frame)
        at = self.elapsed
        self.elapsed += FRAME_SECONDS

        if self.start is None:
            self.pre.append(frame)
            if not speaking:
                return None
            # Whatever run-up is in hand becomes the head of the utterance.
            self.frames = list(self.pre)
            self.pre.clear()
            self.start = max(0.0, at - (len(self.frames) - 1) * FRAME_SECONDS)
            self.voiced = FRAME_SECONDS
            self.silence = 0.0
            return None

        self.frames.append(frame)
        if speaking:
            self.voiced += FRAME_SECONDS
            self.silence = 0.0
        else:
            self.silence += FRAME_SECONDS
            if self.silence >= SILENCE_TO_CUT:
                return self._cut()
        if len(self.frames) * FRAME_SECONDS >= MAX_UTTERANCE:
            return self._cut(trim=False, forced=True)
        return None

    def flush(self) -> Optional[Utterance]:
        """End of stream. A meeting that stops while somebody is mid-sentence
        keeps the sentence."""
        return None if self.start is None else self._cut()

    def _join(self, frames) -> int:
        """Where to break a monologue: the quietest frame near the end.

        Whisper's window is thirty seconds and it ignores the rest, so
        somebody who talks without pausing has to be cut. A cut is the edge of
        its context, so one placed mid-clause loses words on both sides of the
        join -- which makes where it goes the only interesting part. The
        quietest moment in the last couple of seconds is usually the breath
        between two sentences, which is where a person would have put it.
        """
        back = max(1, int(CUT_LOOK_BACK / FRAME_SECONDS))
        window = frames[-back:]
        if len(window) < 2:
            return len(frames)
        levels = [rms_db(f) for f in window]
        quietest = min(levels)
        if quietest > (sum(levels) / len(levels)) - CUT_DIP_DB:
            return len(frames)          # nowhere better than now
        # A little past the quietest frame, not at it: cutting on the way into
        # a pause leaves the piece ending on the loud frame before it. A
        # natural cut keeps HANGOVER of quiet after the words; a forced one
        # keeps half, because speech starts again straight after.
        keep = int((HANGOVER / 2) / FRAME_SECONDS)
        at = int(np.argmin(levels)) + keep
        return min(len(frames), len(frames) - len(window) + at)

    def _cut(self, trim: bool = True,
             forced: bool = False) -> Optional[Utterance]:
        frames, start, voiced = self.frames, self.start, self.voiced
        carry: list = []
        if forced:
            # Everything after the join belongs to what is still being said,
            # and is carried into the next utterance rather than dropped --
            # otherwise every forced cut silently eats a second of speech.
            split = self._join(frames)
            frames, carry = frames[:split], frames[split:]
        elif trim:
            spare = int(max(0.0, self.silence - HANGOVER) / FRAME_SECONDS)
            if spare:
                frames = frames[:-spare]

        audio = np.concatenate(frames) if frames else np.zeros(0, np.float32)
        end = start + len(audio) / SAMPLE_RATE

        self.pre.clear()
        self.silence = 0.0
        if carry:
            self.frames = list(carry)
            self.start = end
            # `voiced` is accumulated as frames arrive, so the carried part has
            # to be counted again. Measured against the gate's own threshold
            # rather than by running the gate, which would move its floor.
            threshold = max(self.gate.noise_db + self.gate.open_over_db,
                            self.gate.floor_db)
            self.voiced = sum(FRAME_SECONDS for f in carry
                              if rms_db(f) > threshold)
        else:
            self.frames, self.start, self.voiced = [], None, 0.0

        if voiced < MIN_UTTERANCE:
            return None
        return Utterance(self.source, start, end, voiced, audio)


# -------------------------------------------------------------- transcript

@dataclass(frozen=True)
class Line:
    """What one source said, once.

    `id` is what survives being heard again. The fast model puts a line up
    while somebody is still talking and the better one rewrites it seconds
    later; everything you did to it in between -- a mark, a note -- is held
    against this number rather than against the words, which change.
    """
    source: str
    start: float
    end: float
    text: str
    suspect: bool = False
    id: Optional[int] = None


def _stamp(seconds: float) -> str:
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes:02}:{secs:02}"


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).strip(".!?…,")


# Whisper fills near-silence with whatever its training data put after
# silence, which was subtitles. These are the lines that come back from a
# breath or a door; the Dutch one is the single most common wrong line in an
# unattended Dutch transcript. They are marked rather than deleted, because
# "thank you" is also a thing people say.
SILENCE_ARTIFACTS = frozenset({
    "thank you",
    "thanks for watching",
    "thanks for watching!",
    "subtitles by the amara.org community",
    "ondertiteling door de amara.org gemeenschap",
    "ondertiteld door de amara.org gemeenschap",
    "bedankt voor het kijken",
    "you",
    "bye",
})
# Below this much actual voice, one of those phrases is almost certainly the
# model talking to itself rather than a short answer.
ARTIFACT_UNDER = 1.0


class Transcript:
    """Every line, in the order it was said, whoever said it."""

    def __init__(self):
        self.lines: list[Line] = []

    @staticmethod
    def is_empty(text: str) -> bool:
        """Whisper returns "" or a lone full stop when it heard nothing."""
        return not re.sub(r"[\s.…·,\-]", "", text or "")

    @staticmethod
    def is_suspect(text: str, voiced: float) -> bool:
        return voiced < ARTIFACT_UNDER and _normalise(text) in SILENCE_ARTIFACTS

    def add(self, line: Line) -> Optional[Line]:
        """Insert in time order, or replace the line that carries the same id.

        Two sources are transcribed independently and finish out of order -- a
        long utterance lands after a short one that started later -- so the
        position is found rather than appended to. A second, better hearing of
        a line already shown arrives here too, and takes the place of the
        first rather than sitting under it.
        """
        if self.is_empty(line.text):
            return None
        if line.id is not None:
            for at, existing in enumerate(self.lines):
                if existing.id == line.id:
                    self.lines[at] = line
                    return line
        bisect.insort(self.lines, line, key=lambda l: l.start)
        return line

    def render(self) -> str:
        return "\n".join(
            f"[{_stamp(l.start)}] {l.source}: {l.text}" + ("  [?]" if l.suspect else "")
            for l in self.lines
        )

    def save(self, path) -> None:
        from pathlib import Path

        Path(path).write_text(self.render() + "\n", encoding="utf-8")


# ------------------------------------------------------------ transcribers

class WhisperTranscriber:
    """faster-whisper, loaded the first time somebody actually says something.

    Nothing happens in `__init__` on purpose. cufflink already pays about ten
    seconds at startup for the voice, and this would add half a gigabyte of
    read to that for a listener that may never be switched on.
    """

    def __init__(self, size: str = DEFAULT_MODEL, compute_type: str = "int8",
                 language: Optional[str] = None, root: Optional[str] = None,
                 threads: Optional[int] = None, beam: int = 5, shipped=None):
        if size not in MODELS:
            raise ValueError(
                f"unknown model {size!r}; have {', '.join(MODELS)}")
        self.size = size
        self.compute_type = compute_type
        self.language = language        # None lets Whisper detect it per utterance
        # Either a directory that shipped with the app, or the size to fetch.
        self.source = find_model(size, shipped=shipped)
        self.root = root or str(model_cache())
        self.threads = threads or _default_threads()
        self.beam = beam
        self._model = None
        # Loading is minutes, not seconds, the first time: `small` is a 484 MB
        # download before it is a 484 MB read. A window that says nothing
        # while that happens looks broken, and was reported as such.
        self.on_state = None

    def load(self):
        if self._model is None:
            if self.on_state:
                self.on_state(f"fetching {self.size}"
                              if not self.cached() else f"loading {self.size}")
            from faster_whisper import WhisperModel

            from pathlib import Path

            here = Path(self.source).is_dir()
            self._model = WhisperModel(
                self.source, device="cpu", compute_type=self.compute_type,
                cpu_threads=self.threads,
                **({} if here else {"download_root": self.root}),
            )
            if self.on_state:
                self.on_state(f"{self.size} ready")
        return self._model

    def cached(self) -> bool:
        """Is the model already on disk? The difference between a five second
        wait and a five minute one, and the only thing worth saying so."""
        from pathlib import Path

        if Path(self.source).is_dir():
            return True                 # it shipped with the app
        if not self.root:
            return False
        want = "models--" + MODELS[self.size].repo.replace("/", "--")
        return (Path(self.root) / want).exists()

    def transcribe(self, audio, sample_rate: int = SAMPLE_RATE) -> str:
        segments, _ = self.load().transcribe(
            mono16k(audio, sample_rate),
            language=self.language,
            # Everything below is in aid of "verbatim". Whisper's defaults are
            # tuned for readable subtitles, which is a different job.
            #
            # Carrying the previous text as context is what makes it repeat
            # itself forever after one bad segment, and it also lets it
            # "correct" what it heard into what it expected. Off.
            condition_on_previous_text=False,
            # No initial_prompt for the same reason: it biases wording.
            temperature=0.0,
            beam_size=self.beam,
            # Our segmenter already decided where the speech was; a second VAD
            # here would trim the run-up and tail we deliberately kept.
            vad_filter=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()


# ----------------------------------------------------------------- meeting

class Meeting:
    """Frames from any number of sources in, one attributed transcript out.

    Deliberately synchronous and pull-free: `feed` is called with whatever a
    capture callback just handed over, and everything it does is bounded.
    That is what makes the interesting half of this file testable without a
    microphone, a model, or a meeting.
    """

    def __init__(self, transcriber, on_line=None, refine: bool = True):
        self.transcriber = transcriber
        self.on_line = on_line
        self.transcript = Transcript()
        self.segmenters: dict[str, Segmenter] = {}
        # Audio held back for a second, better hearing. Kept in memory only,
        # and only until `refine` has used it: an utterance is at most 25 s of
        # 16 kHz float32, about 1.6 MB, which is fine for a handful and not
        # for a meeting's worth.
        self.awaiting: deque = deque()
        self.refining = refine
        self._next_id = 0

    def feed(self, source: str, frame) -> Optional[Line]:
        if source not in self.segmenters:
            self.segmenters[source] = Segmenter(source)
        return self._transcribe(self.segmenters[source].push(frame))

    def close(self) -> None:
        for segmenter in self.segmenters.values():
            self._transcribe(segmenter.flush())

    def _transcribe(self, utterance: Optional[Utterance]) -> Optional[Line]:
        if utterance is None:
            return None
        self._next_id += 1
        text = self.transcriber.transcribe(utterance.audio, SAMPLE_RATE)
        line = self._emit(utterance, text, self._next_id)
        if self.refining:
            self.awaiting.append((self._next_id, utterance))
        return line

    def _emit(self, utterance: Utterance, text: str, line_id: int):
        """Put words on a line. Takes the text rather than the transcriber:
        an earlier version passed the model in and ran it a second time inside
        here, which doubled the cost of the expensive pass -- the one thing
        the two-pass design exists to spend carefully."""
        text = (text or "").strip()
        line = self.transcript.add(Line(
            source=utterance.source,
            start=utterance.start,
            end=utterance.end,
            text=text,
            suspect=Transcript.is_suspect(text, utterance.voiced),
            id=line_id,
        ))
        if line is not None and self.on_line is not None:
            self.on_line(line)
        return line

    def refine(self, transcriber) -> Optional[Line]:
        """Hear the oldest waiting utterance again, with a better model.

        Synchronous and one at a time on purpose: the caller owns the thread,
        so this stays testable without one, and a refinement pass that has
        fallen behind simply has a longer queue rather than more threads.

        Returns the improved line, or None when there was nothing waiting or
        the better model agreed with the faster one -- in which case nothing
        is redrawn, because a line that flickers and settles on the same words
        is worse than one that never moved.
        """
        if not self.awaiting:
            return None
        line_id, utterance = self.awaiting.popleft()
        before = next((l for l in self.transcript.lines if l.id == line_id), None)
        text = (transcriber.transcribe(utterance.audio, SAMPLE_RATE) or "").strip()
        if before is not None and text.strip() == before.text:
            return None
        return self._emit(utterance, text, line_id)

    def waiting(self) -> int:
        """Seconds of audio still to be heard properly."""
        return sum(u.end - u.start for _, u in self.awaiting)


# ----------------------------------------------------------------- capture

def capture(device=None, source: str = YOU, blocksize: int = FRAME):
    """Frames off a real input device. The one part of this file no unit test
    reaches, which is why it is four lines and holds no decisions.

    `device` is a sounddevice index or name; None is the system default input.
    Capturing what the *other* people say is the same call against a loopback
    device -- on this machine "Stereo Mix (Realtek HD Audio Stereo input)"
    under WDM-KS -- and PortAudio 19.7 here exposes no WASAPI loopback flag,
    so a machine without Stereo Mix will need `pyaudiowpatch` or `soundcard`
    instead. That choice is not made yet.
    """
    import sounddevice as sd

    with sd.InputStream(device=device, channels=1, samplerate=SAMPLE_RATE,
                        blocksize=blocksize, dtype="float32") as stream:
        while True:
            block, _overflowed = stream.read(blocksize)
            yield block[:, 0].copy()
