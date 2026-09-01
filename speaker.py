"""Reusable offline text-to-speech speaker built on Kokoro. No UI dependencies.

Usage:
    from speaker import Speaker
    s = Speaker("models/kokoro-v1.0.onnx", "models/voices-v1.0.bin")
    s.speak("Hello there.")   # non-blocking, streams sentence by sentence
    s.stop()

Optional callbacks (called from a worker thread):
    on_state(speaking: bool)   fires when speech starts and ends
    on_sentence(text: str)     fires as each sentence begins playing
"""

import queue
import re
import threading

import sounddevice as sd
from kokoro_onnx import Kokoro

DEFAULT_BLEND = (("bf_emma", 0.7), ("af_nicole", 0.3))


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
        on_state=None,
        on_sentence=None,
    ):
        self.kokoro = Kokoro(str(model_path), str(voices_path))
        self.voice = self.blend_voice(blend)
        self.speed = speed
        self._on_state = on_state or (lambda speaking: None)
        self._on_sentence = on_sentence or (lambda text: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def blend_voice(self, blend):
        return sum(self.kokoro.get_voice_style(name) * weight for name, weight in blend)

    def stop(self):
        self._stop.set()
        sd.stop()

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
                sd.play(audio, sample_rate)
                sd.wait()
        finally:
            self._on_state(False)

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
