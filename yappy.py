"""Yappy — reads your selection aloud, anywhere on Windows, fully offline.

Select text in any app, then:
  Ctrl+Alt+R     read it (press again on a new selection to switch to it)
  Ctrl+Alt+S     stop
  Ctrl+Alt+Up    faster
  Ctrl+Alt+Down  slower

Runs as a tray icon. Quit from the tray menu.
"""

import queue
import re
import threading
import time
from pathlib import Path

import keyboard
import pyperclip
import sounddevice as sd
from kokoro_onnx import Kokoro
from PIL import Image, ImageDraw
import pystray

# ------------------------------------------------------------------ config

HOTKEY_READ = "ctrl+alt+r"
HOTKEY_STOP = "ctrl+alt+s"
HOTKEY_FASTER = "ctrl+alt+up"
HOTKEY_SLOWER = "ctrl+alt+down"

# The voice: 70% bf_emma (British, clear) + 30% af_nicole (breathy rasp).
BLEND = (("bf_emma", 0.7), ("af_nicole", 0.3))

SPEEDS = [0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0]
DEFAULT_SPEED_INDEX = 2  # 1.0x

ROOT = Path(__file__).parent

# ------------------------------------------------------------------ text

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


def grab_selection() -> str:
    """Copy the current selection via Ctrl+C, restoring the old clipboard."""
    try:
        old = pyperclip.paste()
    except Exception:
        old = ""
    try:
        pyperclip.copy("")
    except Exception:
        pass
    # The user is still physically holding the hotkey; synthetic releases
    # reset the modifier state so the target app sees a clean Ctrl+C.
    for key in ("ctrl", "alt", "shift", "windows"):
        keyboard.release(key)
    time.sleep(0.05)
    keyboard.send("ctrl+c")
    text = ""
    for _ in range(20):
        time.sleep(0.05)
        try:
            text = pyperclip.paste()
        except Exception:
            text = ""
        if text:
            break
    if old:
        try:
            pyperclip.copy(old)
        except Exception:
            pass
    return text.strip()

# ------------------------------------------------------------------ reader

class Reader:
    def __init__(self):
        print("Loading Kokoro model...")
        self.kokoro = Kokoro(
            str(ROOT / "models" / "kokoro-v1.0.onnx"),
            str(ROOT / "models" / "voices-v1.0.bin"),
        )
        self.voice = sum(
            self.kokoro.get_voice_style(name) * weight for name, weight in BLEND
        )
        self.speed_index = DEFAULT_SPEED_INDEX
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        print("Model loaded.")

    @property
    def speed(self) -> float:
        return SPEEDS[self.speed_index]

    def change_speed(self, step: int):
        self.speed_index = max(0, min(len(SPEEDS) - 1, self.speed_index + step))
        print(f"Speed: {self.speed}x")

    def stop(self):
        self._stop.set()
        sd.stop()

    def read(self, text: str):
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
        q: queue.Queue = queue.Queue(maxsize=3)

        def produce():
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
                        q.put((audio, sample_rate), timeout=0.2)
                        break
                    except queue.Full:
                        pass
            while not stop.is_set():
                try:
                    q.put(None, timeout=0.2)
                    return
                except queue.Full:
                    pass

        threading.Thread(target=produce, daemon=True).start()

        while not stop.is_set():
            try:
                item = q.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                break
            audio, sample_rate = item
            if stop.is_set():
                break
            sd.play(audio, sample_rate)
            sd.wait()

# ------------------------------------------------------------------ tray

def make_icon_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, 62, 62], fill=(124, 92, 255, 255))
    for x, h in ((20, 10), (30, 20), (40, 14)):
        d.rounded_rectangle([x, 32 - h, x + 6, 32 + h], radius=3, fill="white")
    return img


def main():
    reader = Reader()

    def on_read():
        text = grab_selection()
        if text:
            print(f"Reading {len(text)} chars...")
            reader.read(text)
        else:
            print("No text selected.")
            reader.read("I couldn't find any selected text.")

    keyboard.add_hotkey(HOTKEY_READ, on_read)
    keyboard.add_hotkey(HOTKEY_STOP, reader.stop)
    keyboard.add_hotkey(HOTKEY_FASTER, lambda: reader.change_speed(+1))
    keyboard.add_hotkey(HOTKEY_SLOWER, lambda: reader.change_speed(-1))

    def quit_app(icon, _item):
        reader.stop()
        keyboard.unhook_all()
        icon.stop()

    def set_speed(index):
        def handler(_icon, _item):
            reader.speed_index = index
        return handler

    speed_menu = pystray.Menu(
        *[
            pystray.MenuItem(
                f"{s}x",
                set_speed(i),
                checked=lambda item, i=i: reader.speed_index == i,
                radio=True,
            )
            for i, s in enumerate(SPEEDS)
        ]
    )
    menu = pystray.Menu(
        pystray.MenuItem(f"Read selection: {HOTKEY_READ}", None, enabled=False),
        pystray.MenuItem(f"Stop: {HOTKEY_STOP}", None, enabled=False),
        pystray.MenuItem("Speed", speed_menu),
        pystray.MenuItem("Stop reading", lambda icon, item: reader.stop()),
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("yappy", make_icon_image(), "Yappy — Ctrl+Alt+R reads your selection", menu)

    reader.read("Yappy is ready.")
    print(f"Ready. {HOTKEY_READ} = read selection, {HOTKEY_STOP} = stop.")
    icon.run()


if __name__ == "__main__":
    main()
