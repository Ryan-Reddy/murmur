"""Reusable offline text-to-speech speaker built on Kokoro. No UI dependencies.

Usage:
    from speaker import Speaker
    s = Speaker("models/kokoro-v1.0.onnx", "models/voices-v1.0.bin")
    s.speak("Hello there.")   # non-blocking, streams sentence by sentence
    s.repeat = True           # read it again until stopped
    s.stop()

Optional callbacks (called from a worker thread):
    on_state(speaking: bool)   fires when speech starts and ends
    on_text(text: str)         fires at once with everything about to be read
    on_sentence(text: str)     fires as each chunk begins playing
    on_word(index: int)        fires as each word begins, counted across on_text
    on_pause(paused: bool)     fires on pause / resume
"""

import os
import queue
import re
import threading
import time

import numpy as np
import onnxruntime as rt
import sounddevice as sd
from kokoro_onnx import Kokoro

DEFAULT_BLEND = (("bf_emma", 0.7), ("af_nicole", 0.3))

# Between repeats: a soft chime, then quiet. Without a marker a repeat sounds
# like the reader stumbling back to the top of the page.
REPEAT_GAP = 1.1        # seconds of quiet after the chime
CHIME_HZ = 660.0        # an E, high enough to cut through speech, low enough not to nag
CHIME_SECONDS = 0.16
CHIME_LEVEL = 0.10      # soft on purpose -- it is punctuation, not an alarm
FALLBACK_RATE = 24000

# ONNX Runtime defaults to one thread per core, and lets those threads spin-wait
# between operators rather than sleep. On a many-core desktop that costs ~4.5
# CPU-seconds per second of speech and pins every core. Disabling the spinning is
# what recovers nearly all of that; the thread count barely moves total CPU, so
# it is free to spend on latency.
#
# Measured on a 16-core/32-thread Threadripper: RTF 0.67 at 6 threads, 0.49 at
# 16, 0.47 at 32 -- so the knee is at the *physical* core count and SMT adds
# almost nothing but heat. os.cpu_count() reports logical processors, hence the
# halving. Capped at 16, because past that it stops paying for itself, and
# floored at 2, below which synthesis stops keeping up with playback.
# Override with MURMUR_THREADS.
def _default_threads() -> int:
    return max(2, min(16, (os.cpu_count() or 4) // 2))


def _session(model_path, threads: int) -> rt.InferenceSession:
    """A CPU-frugal inference session: few threads, and none of them spinning."""
    options = rt.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = rt.ExecutionMode.ORT_SEQUENTIAL
    # Sleep while waiting for the next operator instead of burning a core on it.
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return rt.InferenceSession(
        str(model_path), options, providers=["CPUExecutionProvider"]
    )


# Nothing is heard until the first chunk has finished rendering, so a long
# opening sentence is silence the whole time it takes to synthesize -- six and a
# half seconds for a 137-character one, measured. The opening is therefore cut
# into progressively larger pieces: the first is short enough to start almost
# at once, and each next one stays small enough to land before the one playing
# runs out. Each piece may be at most 1/RTF times the one before it -- about
# 1.5x at six threads -- or synthesis falls behind and the speech gaps instead,
# which sounds worse than the wait it was meant to remove.
LEAD_IN_FIRST = 70      # characters to aim for in the opening piece
LEAD_IN_GROWTH = 1.5    # each next piece may be this much bigger
LEAD_IN_STEPS = 4       # after which the buffer has enough slack
# Above this many characters, an opening with no comma in it may be broken at a
# word instead. The break is audible -- the model gives the fragment a falling,
# sentence-final intonation -- so it is only worth it once the alternative is a
# long silence. A comma-less sentence costs roughly 0.11 + 0.033 per character
# in seconds before its first word, so 110 characters is about 3.7 seconds of
# nothing, which is where waiting stops being the better option. Measured on
# such a sentence: 5.7s to the first word unbroken against 4.3s broken, with the
# same number of gaps either way (~0.4 per five seconds of audio).
WORD_BREAK_OVER = 110


def _split_at(text: str, limit: int, allow_words: bool = False):
    """Cut at the last clause break at or before `limit`, else None.

    Punctuation only by default. A gap at a comma is heard as a pause; a gap
    mid-clause is heard as a fault, so an awkward break is only worth it when
    the alternative is waiting on a very long comma-less sentence.
    """
    if len(text) <= limit:
        return None
    for mark in (",", ";", ":"):
        cut = text.rfind(mark, limit // 3, limit)
        if cut != -1:
            return text[: cut + 1].strip(), text[cut + 1 :].strip()
    if allow_words:
        cut = text.rfind(" ", limit // 3, limit)
        if cut != -1:
            return text[:cut].strip(), text[cut + 1 :].strip()
    return None


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?…])\s+", text)
    out = []
    for part in parts:
        # Kokoro degrades on very long inputs; split monsters at a comma.
        while len(part) > 400:
            cut = part.rfind(",", 100, 400)
            if cut == -1:
                cut = 400
            out.append(part[: cut + 1].strip())
            part = part[cut + 1 :].strip()
        if part:
            out.append(part)

    at, limit = 0, float(LEAD_IN_FIRST)
    for _ in range(LEAD_IN_STEPS):
        if at >= len(out):
            break
        piece = _split_at(out[at], int(limit), allow_words=len(out[at]) > WORD_BREAK_OVER)
        if piece is None:
            break
        out[at : at + 1] = list(piece)
        limit = max(len(out[at]), 20) * LEAD_IN_GROWTH
        at += 1
    return out


class Speaker:
    def __init__(
        self,
        model_path,
        voices_path,
        blend=DEFAULT_BLEND,
        speed: float = 1.0,
        volume: float = 1.0,
        repeat: bool = False,
        threads: int = 0,
        on_state=None,
        on_text=None,
        on_sentence=None,
        on_word=None,
        on_pause=None,
    ):
        threads = threads or int(os.environ.get("MURMUR_THREADS", 0) or _default_threads())
        threads = max(1, min(threads, os.cpu_count() or 1))
        self.kokoro = Kokoro.from_session(
            _session(model_path, threads), str(voices_path)
        )
        self.voice = self.blend_voice(blend)
        self.speed = speed
        self.volume = volume
        self.repeat = repeat
        self._on_state = on_state or (lambda speaking: None)
        self._on_text = on_text or (lambda text: None)
        self._on_sentence = on_sentence or (lambda text: None)
        self._on_word = on_word or (lambda index: None)
        self._on_pause = on_pause or (lambda paused: None)
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def blend_voice(self, blend):
        return sum(self.kokoro.get_voice_style(name) * weight for name, weight in blend)

    def word_spans(self, sentence: str, samples: int) -> list[tuple[int, int]]:
        """Where each word of a sentence falls in the rendered audio.

        The v1.0 export returns audio and nothing else -- no per-token
        durations -- so the sentence's known length is divided between its
        words in proportion to how many phonemes each one takes. Phonemizing a
        word costs about 0.05 ms, and the per-word counts add up exactly to the
        phonemization of the whole sentence (the difference is one space per
        gap), so the split follows what the model will actually say rather than
        how the words happen to be spelled.
        """
        words = sentence.split()
        if not words:
            return []
        weights = []
        for word in words:
            try:
                weight = len(self.kokoro.tokenizer.phonemize(word))
            except Exception:
                weight = len(word)  # spelling is a decent fallback
            weights.append(max(weight, 1) + 1)  # + the space that follows it
        total = sum(weights)
        spans, at = [], 0.0
        for weight in weights:
            end = at + weight / total * samples
            spans.append((int(at), int(end)))
            at = end
        return spans

    def stop(self):
        self._stop.set()
        self._paused.clear()

    def pause(self):
        if not self._paused.is_set():
            self._paused.set()
            self._on_pause(True)

    def resume(self):
        if self._paused.is_set():
            self._paused.clear()
            self._on_pause(False)

    def toggle_pause(self):
        if self._paused.is_set():
            self.resume()
        else:
            self.pause()

    def wait(self):
        """Block until the current speech finishes (for one-shot scripts)."""
        thread = self._thread
        if thread is not None:
            thread.join()

    def speak(self, text: str):
        """Start reading text aloud; interrupts any reading in progress."""
        with self._lock:
            self.stop()
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._stop = threading.Event()
            self._thread = threading.Thread(
                target=self._run, args=(text, self._stop), daemon=True
            )
            self._thread.start()

    def _run(self, text: str, stop: threading.Event):
        sentences = split_sentences(text)
        if not sentences:
            return
        # Say what is about to be read before anything slow happens. Waiting for
        # the first chunk to synthesize meant the words appeared seconds after
        # the hotkey, when they were known all along.
        self._on_state(True)
        self._on_text(" ".join(sentences))
        # PortAudio snapshots the device list at init; a monitor sleeping or an
        # output re-plugging strands streams on a dead endpoint (silent, no
        # error). Re-initializing here picks up the current default device.
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        # Opening an output stream costs ~375 ms, and doing it per chunk put
        # that between every pair of chunks -- 1.1s of the gap in a
        # three-chunk paragraph. One stream serves the whole utterance.
        out = {"stream": None, "rate": None}
        try:
            while not stop.is_set():
                rate = self._read_through(sentences, stop, out)
                # Checked here rather than up front, so the toggle can be
                # flipped mid-read and takes effect at the end of this pass.
                if stop.is_set() or not self.repeat:
                    break
                self._interlude(rate, stop, out)
        finally:
            self._close(out)
            self._on_state(False)

    def _stream(self, out, sample_rate):
        """The utterance's output stream, opened on first use."""
        if out["stream"] is not None and out["rate"] != sample_rate:
            self._close(out)
        if out["stream"] is None:
            out["stream"] = sd.OutputStream(
                samplerate=sample_rate, channels=1, dtype="float32"
            )
            out["stream"].start()
            out["rate"] = sample_rate
        return out["stream"]

    @staticmethod
    def _close(out):
        stream = out["stream"]
        out["stream"], out["rate"] = None, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _read_through(self, sentences, stop, out) -> int:
        """One pass over the text. Returns the sample rate it played at."""
        rate, spoken = FALLBACK_RATE, 0
        q: queue.Queue = queue.Queue(maxsize=3)
        threading.Thread(
            target=self._produce, args=(sentences, q, stop), daemon=True
        ).start()
        while not stop.is_set():
            try:
                item = q.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                break
            sentence, audio, sample_rate, spans = item
            rate = sample_rate
            if stop.is_set():
                break
            self._on_sentence(sentence)
            self._play(audio, sample_rate, spans, stop, spoken, out)
            spoken += len(sentence.split())
        return rate

    def _interlude(self, sample_rate: int, stop: threading.Event, out=None):
        """The chime and the quiet that mark one reading from the next."""
        count = int(CHIME_SECONDS * sample_rate)
        t = np.arange(count, dtype="float32") / sample_rate
        tone = np.sin(2 * np.pi * CHIME_HZ * t).astype("float32")
        # A raised-cosine envelope: a tone that starts and stops at full
        # amplitude clicks, which is exactly what a soft marker must not do.
        tone *= np.hanning(count).astype("float32") * CHIME_LEVEL
        self._play(tone, sample_rate, [], stop, 0, out)
        quiet = time.monotonic() + REPEAT_GAP
        while not stop.is_set() and time.monotonic() < quiet:
            time.sleep(0.05)

    def _play(self, audio, sample_rate, spans, stop, first_word=0, out=None):
        """Chunked playback so pause and stop can interrupt mid-sentence."""
        data = audio.reshape(-1, 1).astype("float32", copy=False)
        block = 2048  # 85 ms at 24 kHz: fine enough to follow words
        word, gain = -1, self._gain()
        if out is None:
            out = {"stream": None, "rate": None}  # a one-off, e.g. from a test
        stream = self._stream(out, sample_rate)
        i = 0
        while i < len(data) and not stop.is_set():
            if self._paused.is_set():
                stream.stop()
                while self._paused.is_set() and not stop.is_set():
                    time.sleep(0.05)
                if stop.is_set():
                    break
                stream.start()
            while word + 1 < len(spans) and i >= spans[word + 1][0]:
                word += 1
                self._on_word(first_word + word)
            chunk = data[i : i + block]
            target = self._gain()
            if target != gain:
                # Step to the new volume across the block instead of at its
                # edge, or the jump in amplitude is audible as a click.
                ramp = np.linspace(gain, target, len(chunk), dtype="float32")
                chunk = chunk * ramp.reshape(-1, 1)
                gain = target
            elif gain != 1.0:
                chunk = chunk * gain
            stream.write(chunk)
            i += block

    def _gain(self) -> float:
        """Loudness is perceived roughly logarithmically, so a linear slider
        sounds like it does nothing until the very bottom; squaring it makes the
        control feel even. Never above 1.0 -- the model already peaks near full
        scale and anything more clips."""
        return float(min(max(self.volume, 0.0), 1.0)) ** 2

    def _produce(self, sentences, q, stop):
        for sentence in sentences:
            if stop.is_set():
                return
            try:
                audio, sample_rate = self.kokoro.create(
                    sentence, voice=self.voice, speed=self.speed
                )
            except Exception as exc:
                print(f"Skipping unspeakable chunk: {exc}")
                continue
            spans = self.word_spans(sentence, len(audio))
            while not stop.is_set():
                try:
                    q.put((sentence, audio, sample_rate, spans), timeout=0.2)
                    break
                except queue.Full:
                    pass
        while not stop.is_set():
            try:
                q.put(None, timeout=0.2)
                return
            except queue.Full:
                pass
