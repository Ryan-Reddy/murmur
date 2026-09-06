"""Murmur — reads your selection aloud, anywhere on Windows, fully offline.

Select text in any app, then:
  Ctrl+Alt+M     read it (press again on a new selection to switch to it)
  Ctrl+Alt+Space pause / resume (or click the pill)
  Ctrl+Alt+S     stop (or click the pill's ✕)
  Ctrl+Alt+B     toggle select mode: every new selection is read at once
  Ctrl+Alt+Up    faster
  Ctrl+Alt+Down  slower

Runs as a tray icon (green while speaking). Quit from the tray menu.
"""

import ctypes
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

import keyboard
import pyperclip
import pystray
from PIL import Image, ImageDraw

import winshutdown
from mousehook import MouseButtons
from speaker import Speaker

# ------------------------------------------------------------------ config

HOTKEY_READ = "ctrl+alt+m"  # M for Murmur (avoid alt+r combos: NVIDIA overlay)
HOTKEY_PAUSE = "ctrl+alt+space"
HOTKEY_STOP = "ctrl+alt+s"
HOTKEY_SELECTMODE = "ctrl+alt+b"  # B for browse: read every new selection
HOTKEY_FASTER = "ctrl+alt+up"
HOTKEY_SLOWER = "ctrl+alt+down"

# The voice: 70% bf_emma (British, clear) + 30% af_nicole (breathy rasp).
BLEND = (("bf_emma", 0.7), ("af_nicole", 0.3))

SPEEDS = [0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0]

# How long the pill hangs about, in milliseconds. A read used to end and the
# pill vanish 0.6s later, which is no use if you were reaching for pause.
LINGER_AFTER_SPEECH = 4000
LINGER_AFTER_TOUCH = 120_000   # once you have used it, assume you may again
LINGER_TOAST = 2000            # a setting changed while nothing is being read

# Pill palette.
BG, FG, MUTED = "#1e1b2e", "#e8e4ff", "#8f87b8"
ACCENT, GREEN, AMBER = "#7c5cff", "#34c77b", "#ffaa3c"

# Localhost text-in port: other local apps (e.g. ryans-assistant) send UTF-8
# text here and it plays through the same pill + hotkey controls.
MURMUR_PORT = 52719

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

# ------------------------------------------------------------------ text-in

def start_text_server(speaker, control):
    def serve():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("127.0.0.1", MURMUR_PORT))
        except OSError:
            print(f"Port {MURMUR_PORT} taken; text-in disabled.")
            return
        srv.listen(2)
        while True:
            conn, _ = srv.accept()
            with conn:
                chunks, total = [], 0
                while total < 200_000:
                    data = conn.recv(65536)
                    if not data:
                        break
                    chunks.append(data)
                    total += len(data)
                text = b"".join(chunks).decode("utf-8", "replace").strip()
                if text.startswith("::"):
                    control(text)
                elif text:
                    speaker.speak(text)

    threading.Thread(target=serve, daemon=True).start()

# ------------------------------------------------------------------ tray icons

def make_icon_image(color) -> Image.Image:
    # Drawn at 4x and downsampled: PIL does not antialias, and a 64 px circle
    # drawn directly has visibly stepped edges in the tray.
    scale, size = 4, 64
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2 * scale, 2 * scale, 62 * scale, 62 * scale], fill=color)
    for x, h in ((20, 10), (30, 20), (40, 14)):
        d.rounded_rectangle(
            [x * scale, (32 - h) * scale, (x + 6) * scale, (32 + h) * scale],
            radius=3 * scale, fill="white",
        )
    return img.resize((size, size), Image.LANCZOS)


def close_splash(text: str | None = None):
    """The packaged build puts a splash up while the model loads; plain Python
    runs have no pyi_splash module and skip this entirely."""
    try:
        import pyi_splash
    except ImportError:
        return
    try:
        if text:
            pyi_splash.update_text(text)
        else:
            pyi_splash.close()
    except Exception:
        pass


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
    close_splash("loading the voice model…")
    speaker = Speaker(
        ROOT / "models" / "kokoro-v1.0.onnx",
        ROOT / "models" / "voices-v1.0.bin",
        blend=BLEND,
        on_state=lambda speaking: ui_events.put(("state", speaking)),
        on_text=lambda text: ui_events.put(("text", text)),
        on_word=lambda index: ui_events.put(("word", index)),
        on_pause=lambda paused: ui_events.put(("pause", paused)),
    )
    print("Model loaded.")
    close_splash()

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

    # --- select mode: any completed selection (drag / double-click) is read ---
    select_mode = {"on": False}
    last_spoken = {"text": None}
    drag_start = {"pos": (0, 0)}

    def toggle_select_mode():
        select_mode["on"] = not select_mode["on"]
        # A WH_MOUSE_LL hook puts Murmur in the path of every mouse event on the
        # desktop, and while it is busy synthesizing that shows up as laggy
        # input. Select mode is the only thing that wants clicks, so the hook
        # exists exactly as long as select mode does.
        # start() waits on the hook thread and stop() joins it; on the tkinter
        # thread that freezes the pill mid-click, so it happens off to the side.
        threading.Thread(
            target=mouse_hook.start if select_mode["on"] else mouse_hook.stop,
            daemon=True,
        ).start()
        ui_events.put(("selectmode", select_mode["on"]))

    def toggle_repeat():
        speaker.repeat = not speaker.repeat
        ui_events.put(("repeat", speaker.repeat))

    def read_selection_quietly():
        def go():
            time.sleep(0.15)  # let the app finalize the selection
            text = grab_selection()
            if text and text != last_spoken["text"]:
                last_spoken["text"] = text
                speaker.speak(text)
        threading.Thread(target=go, daemon=True).start()

    def on_mouse(kind, x, y):
        if not select_mode["on"]:
            return
        if kind == "down":
            drag_start["pos"] = (x, y)
        elif kind == "up":
            sx, sy = drag_start["pos"]
            if abs(x - sx) + abs(y - sy) > 25:  # a real drag, not a click
                read_selection_quietly()
        elif kind == "double":  # double or triple click selects a word or line
            read_selection_quietly()

    mouse_hook = MouseButtons(on_mouse)

    # --- overlay pill: what is being read, and the controls for it --------
    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.95)
    root.configure(bg=BG)

    shell = tk.Frame(root, bg=BG, padx=2, pady=2)
    shell.pack()

    # The sentence being spoken, with the current word lit as it is reached.
    reader = tk.Text(
        shell, height=4, width=58, wrap="word", relief="flat", cursor="arrow",
        bg=BG, fg=MUTED, font=("Segoe UI", 10), padx=14, pady=10,
        highlightthickness=0, borderwidth=0, spacing3=3, takefocus=0,
    )
    reader.tag_configure("said", foreground=FG)
    reader.tag_configure("now", foreground="#ffffff", background=ACCENT)
    reader.config(state="disabled")
    reader.pack(fill="x")

    controls = tk.Frame(shell, bg=BG)
    controls.pack(fill="x", padx=12, pady=(0, 9))

    def touched():
        """Any deliberate interaction keeps the pill up for a while, so a second
        click never has to wait for it to come back."""
        if not pill["pinned"] and not pill["speaking"]:
            hide_later(LINGER_AFTER_TOUCH)

    def chip(parent, text, command, font=("Segoe UI", 10), pad=7):
        """A label that behaves like a flat button."""
        # pady 7 rather than 3: at 3 these were ~12x20 px, which is a poor
        # target even before the pill starts hiding itself.
        widget = tk.Label(
            parent, text=text, fg=MUTED, bg=BG, font=font,
            padx=pad, pady=7, cursor="hand2",
        )
        widget.rest = MUTED
        widget.bind("<Button-1>", lambda _e: (command(), touched()))
        widget.bind("<Enter>", lambda _e: widget.config(fg=FG))
        widget.bind("<Leave>", lambda _e: widget.config(fg=widget.rest))
        return widget

    play_btn = chip(controls, "⏸", lambda: speaker.toggle_pause(),
                    font=("Segoe UI", 12), pad=8)
    play_btn.pack(side="left")

    slower_btn = chip(controls, "−", lambda: change_speed(-1), pad=9)
    slower_btn.pack(side="left", padx=(10, 0))
    speed_hud = tk.Label(controls, text="1.0×", fg=FG, bg=BG,
                         font=("Segoe UI", 9), padx=2, pady=3, width=5)
    speed_hud.pack(side="left")
    faster_btn = chip(controls, "+", lambda: change_speed(+1), pad=9)
    faster_btn.pack(side="left")

    volume_icon = tk.Label(controls, text="🔊", fg=MUTED, bg=BG,
                           font=("Segoe UI", 9), padx=6, pady=3)
    volume_icon.pack(side="left", padx=(10, 0))
    BARS = 7
    volume_bar = tk.Canvas(controls, width=BARS * 8, height=16, bg=BG,
                           highlightthickness=0, cursor="hand2")
    volume_bar.pack(side="left", pady=3)

    repeat_btn = chip(controls, "🔁", lambda: toggle_repeat(), pad=8)
    repeat_btn.pack(side="left", padx=(10, 0))

    select_btn = chip(controls, "⇱ select", lambda: toggle_select_mode(), pad=8)
    select_btn.pack(side="left", padx=(6, 0))

    close_btn = chip(controls, "✕", lambda: speaker.stop(),
                     font=("Segoe UI", 10, "bold"), pad=8)
    close_btn.pack(side="right")
    pin_btn = chip(controls, "📌", lambda: toggle_pin(), pad=8)
    pin_btn.pack(side="right")

    pill = {"sentence": "", "paused": False, "speaking": False,
            "words": [], "volume": 1.0, "pinned": False, "muted": 0.0,
            "pos": None}

    # --- volume -----------------------------------------------------------
    def render_volume():
        volume_bar.delete("all")
        for i in range(BARS):
            lit = (i + 1) / BARS <= pill["volume"] + 1e-9
            height = 5 + i * 1.6
            volume_bar.create_rectangle(
                i * 8 + 1, 15 - height, i * 8 + 6, 15,
                fill=ACCENT if lit else "#3a3550", width=0,
            )

    def set_volume(level: float, announce: bool = True):
        pill["volume"] = max(0.0, min(1.0, level))
        speaker.volume = pill["volume"]
        render_volume()
        if announce and not pill["speaking"]:
            flash(f"🔊  Volume {round(pill['volume'] * 100)}%")

    def volume_from_x(x):
        # Snap to whole bars, so clicking a bar fills exactly that bar.
        set_volume(max(1, min(BARS, round(x / 8 + 0.5))) / BARS)

    def toggle_mute():
        if pill["volume"] > 0:
            pill["muted"] = pill["volume"]
            set_volume(0.0)
        else:
            set_volume(pill["muted"] or 1.0)

    volume_icon.bind("<Button-1>", lambda _e: toggle_mute())
    volume_icon.config(cursor="hand2")
    speed_hud.bind("<Button-1>", lambda _e: change_speed(SPEEDS.index(1.0) - speed_index))
    speed_hud.config(cursor="hand2")
    volume_bar.bind("<Button-1>", lambda e: volume_from_x(e.x))
    volume_bar.bind("<B1-Motion>", lambda e: volume_from_x(e.x))
    step = 1 / BARS
    for widget in (volume_bar, volume_icon):
        widget.bind(
            "<MouseWheel>",
            lambda e: set_volume(pill["volume"] + (step if e.delta > 0 else -step)),
        )
    for widget in (speed_hud, slower_btn, faster_btn):
        widget.bind("<MouseWheel>", lambda e: change_speed(1 if e.delta > 0 else -1))
    render_volume()

    # --- read-along -------------------------------------------------------
    def show_sentence(text: str):
        reader.config(state="normal")
        reader.delete("1.0", "end")
        spans = []
        for word in text.split():
            start = reader.index("end-1c")
            reader.insert("end", word + " ")
            spans.append((start, reader.index("end-2c")))
        pill["words"] = spans
        reader.config(state="disabled")

    def highlight_word(index: int):
        reader.tag_remove("now", "1.0", "end")
        if not (0 <= index < len(pill["words"])):
            return
        start, stop = pill["words"][index]
        reader.tag_add("said", "1.0", start)
        reader.tag_add("now", start, stop)
        reader.see(start)  # long sentences scroll to keep the word in view

    # --- pin, drag and snap ------------------------------------------------
    def toggle_pin():
        pill["pinned"] = not pill["pinned"]
        if pill["pinned"]:
            cancel_hide()
            if not pill["speaking"] and not pill["words"]:
                show_sentence("Murmur is listening. " + HOTKEY_READ + " reads your selection.")
            render_pill()
        else:
            render_pill()
            hide_later()

    SNAP = 40

    def snap_pill():
        """Pull the pill onto an edge or the centre line if it was dropped
        near one, so it lands somewhere deliberate rather than almost-aligned."""
        root.update_idletasks()
        w, h = root.winfo_width(), root.winfo_height()
        left, top, right, bottom = work_area()
        x, y = root.winfo_x(), root.winfo_y()
        if x - left < SNAP:
            x = left
        elif right - (x + w) < SNAP:
            x = right - w
        elif abs((x + w / 2) - (left + right) / 2) < SNAP:
            x = left + (right - left - w) // 2
        if y - top < SNAP:
            y = top
        elif bottom - (y + h) < SNAP:
            y = bottom - h
        x = max(left, min(int(x), right - w))
        y = max(top, min(int(y), bottom - h))
        root.geometry(f"+{x}+{y}")

    drag = {"x": 0, "y": 0, "moved": False}

    def pill_press(event):
        drag["x"], drag["y"] = event.x_root, event.y_root
        drag["moved"] = False
        touched()
        return "break"

    def pill_drag(event):
        dx, dy = event.x_root - drag["x"], event.y_root - drag["y"]
        if not drag["moved"] and abs(dx) + abs(dy) < 4:
            return "break"  # a click with a shaky hand, not a drag
        drag["moved"] = True
        root.geometry(f"+{root.winfo_x() + dx}+{root.winfo_y() + dy}")
        drag["x"], drag["y"] = event.x_root, event.y_root
        return "break"

    def pill_drop(event):
        if not drag["moved"]:
            return None
        snap_pill()
        pill["pos"] = (root.winfo_x(), root.winfo_y())
        return "break"

    def reader_release(event):
        # Dragging moves the pill; a plain click on the body pauses, which is
        # what the pill did before it grew a control row.
        if pill_drop(event) is None:
            speaker.toggle_pause()
        return "break"

    for widget in (shell, controls):
        widget.bind("<Button-1>", pill_press)
        widget.bind("<B1-Motion>", pill_drag)
        widget.bind("<ButtonRelease-1>", pill_drop)
    reader.bind("<Button-1>", pill_press)
    reader.bind("<B1-Motion>", pill_drag)
    reader.bind("<ButtonRelease-1>", reader_release)
    reader.config(cursor="hand2")

    # --- layout -----------------------------------------------------------
    def render_pill():
        idle = not pill["speaking"]
        play_btn.config(text="▶" if (pill["paused"] or idle) else "⏸")
        select_btn.rest = GREEN if select_mode["on"] else MUTED
        select_btn.config(fg=select_btn.rest)
        repeat_btn.rest = GREEN if speaker.repeat else MUTED
        repeat_btn.config(fg=repeat_btn.rest)
        pin_btn.rest = AMBER if pill["pinned"] else MUTED
        pin_btn.config(fg=pin_btn.rest)
        place_pill()

    def work_area():
        """The desktop minus the taskbar. Placing against the full screen
        height put the control row underneath the taskbar, where the clicks
        went to the taskbar instead of to Murmur."""
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
        return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()

    def place_pill():
        root.update_idletasks()
        left, top, right, bottom = work_area()
        width, height = root.winfo_reqwidth(), root.winfo_reqheight()
        if pill["pos"]:
            x, y = pill["pos"]  # wherever it was dragged to, and left
        else:
            x = left + (right - left - width) // 2
            y = bottom - height - 12
        # However it got its position, keep every control reachable.
        x = max(left, min(x, right - width))
        y = max(top, min(y, bottom - height))
        root.geometry(f"+{x}+{y}")
        root.deiconify()

    hide_timer = {"id": None}

    def cancel_hide():
        if hide_timer["id"] is not None:
            root.after_cancel(hide_timer["id"])
            hide_timer["id"] = None

    def hide_later(delay: int = LINGER_TOAST):
        cancel_hide()

        def done():
            hide_timer["id"] = None
            if pill["speaking"] or pill["pinned"]:
                return
            # Never pull the controls out from under a reaching cursor.
            # winfo_containing covers the buttons too, which <Leave> bindings
            # on the frames alone would miss.
            if root.winfo_containing(*root.winfo_pointerxy()) is not None:
                hide_later(LINGER_AFTER_TOUCH)  # the cursor is on it; leave it
                return
            root.withdraw()

        hide_timer["id"] = root.after(delay, done)

    def flash(message: str):
        """Show a setting change while nothing is being read."""
        if not pill["speaking"]:
            reader.config(state="normal")
            reader.delete("1.0", "end")
            reader.insert("end", message)
            reader.config(state="disabled")
            pill["words"] = []
        render_pill()
        hide_later()

    # Windows activates a window when it is clicked, which would pull focus out
    # of whatever the user is reading from. WS_EX_NOACTIVATE stops that, so the
    # pill's buttons can be clicked without the source app losing focus.
    root.update_idletasks()
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        GWL_EXSTYLE, NOACTIVATE, TOOLWINDOW = -20, 0x08000000, 0x00000080
        user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | NOACTIVATE | TOOLWINDOW,
        )
    except Exception:
        pass

    # --- tray ---
    def release_hooks():
        # The global keyboard and mouse hooks are the only things Murmur leaves
        # in other processes; drop them before anything slower.
        speaker.stop()
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        try:
            mouse_hook.stop()
        except Exception:
            pass

    def quit_app(icon, _item):
        release_hooks()
        icon.stop()
        root.after(0, root.destroy)

    winshutdown.install(release_hooks)

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
    def set_volume_level(level: float):
        def handler(_icon, _item):
            ui_events.put(("volume", level))
        return handler

    volume_menu = pystray.Menu(
        *[
            pystray.MenuItem(
                f"{int(level * 100)}%",
                set_volume_level(level),
                checked=lambda item, level=level: abs(pill["volume"] - level) < 0.01,
                radio=True,
            )
            for level in (0.25, 0.5, 0.75, 1.0)
        ]
    )
    menu = pystray.Menu(
        pystray.MenuItem(f"Read selection: {HOTKEY_READ}", None, enabled=False),
        pystray.MenuItem(f"Pause: {HOTKEY_PAUSE} or click the pill", None, enabled=False),
        pystray.MenuItem(f"Stop: {HOTKEY_STOP} or the pill's ✕", None, enabled=False),
        pystray.MenuItem("Speed", speed_menu),
        pystray.MenuItem("Volume", volume_menu),
        pystray.MenuItem(
            f"Select mode ({HOTKEY_SELECTMODE}): read on select",
            lambda icon, item: toggle_select_mode(),
            checked=lambda item: select_mode["on"],
        ),
        pystray.MenuItem(
            "Repeat: read it again until stopped",
            lambda icon, item: toggle_repeat(),
            checked=lambda item: speaker.repeat,
        ),
        pystray.MenuItem("Pause / resume", lambda icon, item: speaker.toggle_pause()),
        pystray.MenuItem("Stop reading", lambda icon, item: speaker.stop()),
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon(
        "murmur", IMG_IDLE, f"Murmur — {HOTKEY_READ} reads your selection", menu
    )

    def control(line: str):
        """Commands arriving on the text-in port.

        Everything the hotkeys and the pill can do is reachable here, so other
        apps -- and tests -- can drive Murmur without taking over the keyboard.

            ::stop  ::pause  ::speed +1 | -1 | 1.25  ::volume 0.6
            ::select on | off | toggle  ::pin on | off | toggle
            ::repeat on | off | toggle  ::read
        """
        name, _, arg = line[2:].strip().partition(" ")
        arg = arg.strip()
        if name == "stop":
            speaker.stop()
        elif name == "pause":
            speaker.toggle_pause()
        elif name == "read":
            threading.Thread(target=on_read, daemon=True).start()
        else:
            # The rest touch UI state, so they run on the tkinter thread.
            ui_events.put(("command", (name, arg)))

    def run_command(name: str, arg: str):
        if name == "speed":
            if arg.startswith(("+", "-")):
                change_speed(int(arg))
            elif arg:
                nearest = min(SPEEDS, key=lambda s: abs(s - float(arg)))
                change_speed(SPEEDS.index(nearest) - speed_index)
        elif name == "volume":
            set_volume(float(arg))
        elif name == "select":
            wanted = {"on": True, "off": False}.get(arg, not select_mode["on"])
            if wanted != select_mode["on"]:
                toggle_select_mode()
        elif name == "pin":
            wanted = {"on": True, "off": False}.get(arg, not pill["pinned"])
            if wanted != pill["pinned"]:
                toggle_pin()
        elif name == "repeat":
            wanted = {"on": True, "off": False}.get(arg, not speaker.repeat)
            if wanted != speaker.repeat:
                toggle_repeat()

    # --- marshal speaker-thread events onto the tkinter thread ---
    def poll_events():
        try:
            while True:
                kind, value = ui_events.get_nowait()
                if kind == "state":
                    pill["speaking"] = value
                    icon.icon = IMG_SPEAKING if value else IMG_IDLE
                    if value:
                        cancel_hide()
                        show_sentence("")  # drop the last read's leftovers
                    else:
                        pill["paused"] = False
                        last_spoken["text"] = None  # allow re-reading later
                        hide_later(LINGER_AFTER_SPEECH)
                    render_pill()
                elif kind == "text":
                    pill["sentence"] = value
                    show_sentence(value)
                    render_pill()
                elif kind == "word":
                    highlight_word(value)
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
                elif kind == "volume":
                    set_volume(value)
                elif kind == "repeat":
                    render_pill()
                    if not pill["speaking"]:
                        flash("🔁  Repeat on" if value else "🔁  Repeat off")
                elif kind == "command":
                    run_command(*value)
                elif kind == "selectmode":
                    flash(
                        "🖱  Select mode on — new selections are read aloud"
                        if value
                        else "🖱  Select mode off"
                    )
        except queue.Empty:
            pass
        root.after(80, poll_events)

    start_text_server(speaker, control)

    keyboard.add_hotkey(HOTKEY_READ, on_read)
    keyboard.add_hotkey(HOTKEY_PAUSE, speaker.toggle_pause)
    keyboard.add_hotkey(HOTKEY_STOP, speaker.stop)
    keyboard.add_hotkey(HOTKEY_SELECTMODE, toggle_select_mode)
    keyboard.add_hotkey(HOTKEY_FASTER, lambda: change_speed(+1))
    keyboard.add_hotkey(HOTKEY_SLOWER, lambda: change_speed(-1))

    icon.run_detached()
    print(f"Ready. {HOTKEY_READ} = read selection, {HOTKEY_STOP} = stop.")
    speaker.speak("Murmur is ready.")
    root.after(80, poll_events)
    root.mainloop()


if __name__ == "__main__":
    main()
