"""Reusable offline text-to-speech speaker built on Kokoro. No UI dependencies.

Usage:
    from speaker import Speaker
    s = Speaker("models/kokoro-v1.0.onnx", "models/voices-v1.0.bin")
    s.speak("Hello there.")   # non-blocking, streams sentence by sentence
    s.stop()

Optional callbacks (called from a worker thread):
    on_state(speaking: bool)   fires when speech starts and ends
    on_sentence(text: str)     fires as each sentence begins playing
    on_pause(paused: bool)     fires on pause / resume
"""

import os
import queue
import re
import threading
import time

import onnxruntime as rt
import sounddevice as sd
from kokoro_onnx import Kokoro

DEFAULT_BLEND = (("bf_emma", 0.7), ("af_nicole", 0.3))

# ONNX Runtime defaults to one thread per core, and lets those threads spin-wait
# between operators rather than sleep. On a many-core desktop that costs ~4.5
# CPU-seconds per second of speech and pins every core, for no useful gain: the
# model is small and synthesis only has to stay ahead of playback. Four
# non-spinning threads still run at ~2x real time for about a fifth of the
# energy. Override with MURMUR_THREADS if you want a different trade.
DEFAULT_THREADS = 4


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
    return out


class Speaker:
    def __init__(
        self,
        model_path,
        voices_path,
        blend=DEFAULT_BLEND,
        speed: float = 1.0,
        threads: int = 0,
        on_state=None,
        on_sentence=None,
        on_pause=None,
    ):
        threads = threads or int(os.environ.get("MURMUR_THREADS", DEFAULT_THREADS))
        threads = max(1, min(threads, os.cpu_count() or 1))
        self.kokoro = Kokoro.from_session(
            _session(model_path, threads), str(voices_path)
        )
        self.voice = self.blend_voice(blend)
        self.speed = speed
        self._on_state = on_state or (lambda speaking: None)
        self._on_sentence = on_sentence or (lambda text: None)
        self._on_pause = on_pause or (lambda paused: None)
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def blend_voice(self, blend):
        return sum(self.kokoro.get_voice_style(name) * weight for name, weight in blend)

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
        # PortAudio snapshots the device list at init; a monitor sleeping or an
        # output re-plugging strands streams on a dead endpoint (silent, no
        # error). Re-initializing here picks up the current default device.
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        self._on_state(True)
        try:
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
                sentence, audio, sample_rate = item
                if stop.is_set():
                    break
                self._on_sentence(sentence)
                self._play(audio, sample_rate, stop)
        finally:
            self._on_state(False)

    def _play(self, audio, sample_rate, stop):
        """Chunked playback so pause and stop can interrupt mid-sentence."""
        data = audio.reshape(-1, 1).astype("float32", copy=False)
        block = 2048
        with sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32") as stream:
            i = 0
            while i < len(data) and not stop.is_set():
                if self._paused.is_set():
                    stream.stop()
                    while self._paused.is_set() and not stop.is_set():
                        time.sleep(0.05)
                    if stop.is_set():
                        break
                    stream.start()
                stream.write(data[i : i + block])
                i += block

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
            while not stop.is_set():
                try:
                    q.put((sentence, audio, sample_rate), timeout=0.2)
                    break
                except queue.Full:
                    pass
        while not stop.is_set():
            try:
                q.put(None, timeout=0.2)
                return
            except queue.Full:
                pass
