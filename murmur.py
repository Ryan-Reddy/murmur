"""Murmur — reads your selection aloud, anywhere on Windows, fully offline.

Select text in any app, then:
  Ctrl+Alt+M     read it (press again on a new selection to switch to it)
  Ctrl+Alt+Space pause / resume (or click the pill)
  Ctrl+Alt+S     stop (or click the pill's ✕)
  Ctrl+Alt+Up    faster
  Ctrl+Alt+Down  slower

Runs as a tray icon (green while speaking). Quit from the tray menu.
"""

import ctypes
import queue
import sys
import time
import tkinter as tk
from pathlib import Path

import keyboard
import pyperclip
import pystray
from PIL import Image, ImageDraw

from speaker import Speaker

# ------------------------------------------------------------------ config

HOTKEY_READ = "ctrl+alt+m"  # M for Murmur (avoid alt+r combos: NVIDIA overlay)
HOTKEY_PAUSE = "ctrl+alt+space"
HOTKEY_STOP = "ctrl+alt+s"
HOTKEY_FASTER = "ctrl+alt+up"
HOTKEY_SLOWER = "ctrl+alt+down"

# The voice: 70% bf_emma (British, clear) + 30% af_nicole (breathy rasp).
BLEND = (("bf_emma", 0.7), ("af_nicole", 0.3))

SPEEDS = [0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0]

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).parent  # packaged: models/ sits next to Murmur.exe
else:
    ROOT = Path(__file__).parent


def already_running() -> bool:
    ctypes.windll.kernel32.CreateMutexW(None, False, "MurmurTTS-single-instance")
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS

# ------------------------------------------------------------------ selection

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

# ------------------------------------------------------------------ tray icons

def make_icon_image(color) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, 62, 62], fill=color)
    for x, h in ((20, 10), (30, 20), (40, 14)):
        d.rounded_rectangle([x, 32 - h, x + 6, 32 + h], radius=3, fill="white")
    return img


IMG_IDLE = make_icon_image((124, 92, 255, 255))     # purple
IMG_SPEAKING = make_icon_image((52, 199, 123, 255))  # green
IMG_PAUSED = make_icon_image((255, 170, 60, 255))    # amber

# ------------------------------------------------------------------ app

def main():
    if already_running():
        print("Murmur is already running.")
        return
    ui_events: queue.Queue = queue.Queue()

    print("Loading Kokoro model...")
    speaker = Speaker(
        ROOT / "models" / "kokoro-v1.0.onnx",
        ROOT / "models" / "voices-v1.0.bin",
        blend=BLEND,
        on_state=lambda speaking: ui_events.put(("state", speaking)),
        on_sentence=lambda text: ui_events.put(("sentence", text)),
        on_pause=lambda paused: ui_events.put(("pause", paused)),
    )
    print("Model loaded.")

    speed_index = SPEEDS.index(1.0)

    def change_speed(step: int):
        nonlocal speed_index
        speed_index = max(0, min(len(SPEEDS) - 1, speed_index + step))
        speaker.speed = SPEEDS[speed_index]
        ui_events.put(("speed", SPEEDS[speed_index]))

    def set_speed(index: int):
        def handler(_icon, _item):
            nonlocal speed_index
            speed_index = index
            speaker.speed = SPEEDS[index]
            ui_events.put(("speed", SPEEDS[index]))
        return handler

    def on_read():
        text = grab_selection()
        if text:
            speaker.speak(text)
        else:
            speaker.speak("I couldn't find any selected text.")

    # --- overlay pill: sentence being read; click = pause/resume, ✕ = stop ---
    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.93)
    root.configure(bg="#1e1b2e")
    body = tk.Label(
        root, text="", fg="#e8e4ff", bg="#1e1b2e",
        font=("Segoe UI", 10), padx=14, pady=9, cursor="hand2",
    )
    body.pack(side="left")
    speed_hud = tk.Label(
        root, text="1.0×", fg="#8f87b8", bg="#1e1b2e",
        font=("Segoe UI", 9), padx=0, pady=9,
    )
    speed_hud.pack(side="left")
    close = tk.Label(
        root, text="✕", fg="#8f87b8", bg="#1e1b2e",
        font=("Segoe UI", 10, "bold"), padx=12, pady=9, cursor="hand2",
    )
    close.pack(side="left")
    body.bind("<Button-1>", lambda _e: speaker.toggle_pause())
    close.bind("<Button-1>", lambda _e: speaker.stop())

    pill = {"sentence": "", "paused": False, "speaking": False}

    def render_pill():
        snippet = pill["sentence"]
        if len(snippet) > 90:
            snippet = snippet[:87] + "..."
        prefix = "⏸" if pill["paused"] else "\U0001f50a"
        body.config(text=f"{prefix}  {snippet}")
        place_pill()

    def place_pill():
        root.update_idletasks()
        x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
        y = root.winfo_screenheight() - 110
        root.geometry(f"+{x}+{y}")
        root.deiconify()

    flash_timer = {"id": None}

    def flash(message: str):
        # Transient toast for setting changes while nothing is being read.
        if flash_timer["id"] is not None:
            root.after_cancel(flash_timer["id"])
        body.config(text=message)
        place_pill()

        def done():
            flash_timer["id"] = None
            if pill["speaking"]:
                render_pill()
            else:
                root.withdraw()

        flash_timer["id"] = root.after(1400, done)

    # --- tray ---
    def quit_app(icon, _item):
        speaker.stop()
        keyboard.unhook_all()
        icon.stop()
        root.after(0, root.destroy)

    speed_menu = pystray.Menu(
        *[
            pystray.MenuItem(
                f"{s}x",
                set_speed(i),
                checked=lambda item, i=i: speed_index == i,
                radio=True,
            )
            for i, s in enumerate(SPEEDS)
        ]
    )
    menu = pystray.Menu(
        pystray.MenuItem(f"Read selection: {HOTKEY_READ}", None, enabled=False),
        pystray.MenuItem(f"Pause: {HOTKEY_PAUSE} or click the pill", None, enabled=False),
        pystray.MenuItem(f"Stop: {HOTKEY_STOP} or the pill's ✕", None, enabled=False),
        pystray.MenuItem("Speed", speed_menu),
        pystray.MenuItem("Pause / resume", lambda icon, item: speaker.toggle_pause()),
        pystray.MenuItem("Stop reading", lambda icon, item: speaker.stop()),
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon(
        "murmur", IMG_IDLE, f"Murmur — {HOTKEY_READ} reads your selection", menu
    )

    # --- marshal speaker-thread events onto the tkinter thread ---
    def poll_events():
        try:
            while True:
                kind, value = ui_events.get_nowait()
                if kind == "state":
                    pill["speaking"] = value
                    icon.icon = IMG_SPEAKING if value else IMG_IDLE
                    if not value:
                        pill["paused"] = False
                        root.withdraw()
                elif kind == "sentence":
                    pill["sentence"] = value
                    render_pill()
                elif kind == "pause":
                    pill["paused"] = value
                    if pill["speaking"]:
                        icon.icon = IMG_PAUSED if value else IMG_SPEAKING
                        render_pill()
                elif kind == "speed":
                    speed_hud.config(text=f"{value}×")
                    if pill["speaking"]:
                        render_pill()
                    else:
                        flash(f"⚡  Speed {value}×")
        except queue.Empty:
            pass
        root.after(80, poll_events)

    keyboard.add_hotkey(HOTKEY_READ, on_read)
    keyboard.add_hotkey(HOTKEY_PAUSE, speaker.toggle_pause)
    keyboard.add_hotkey(HOTKEY_STOP, speaker.stop)
    keyboard.add_hotkey(HOTKEY_FASTER, lambda: change_speed(+1))
    keyboard.add_hotkey(HOTKEY_SLOWER, lambda: change_speed(-1))

    icon.run_detached()
    print(f"Ready. {HOTKEY_READ} = read selection, {HOTKEY_STOP} = stop.")
    speaker.speak("Murmur is ready.")
    root.after(80, poll_events)
    root.mainloop()


if __name__ == "__main__":
    main()
