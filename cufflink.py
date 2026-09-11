"""cufflink — reads your selection aloud, anywhere on Windows, fully offline.

Select text in any app, then:
  Ctrl+Alt+M     read it (press again on a new selection to switch to it)
  Ctrl+Alt+Space pause / resume (or click the pill)
  Ctrl+Alt+S     stop (or click the pill's ✕)
  Ctrl+Alt+B     toggle select mode: every new selection is read at once
  Ctrl+Alt+Up    faster
  Ctrl+Alt+Down  slower

Runs as a tray icon (green while speaking). Quit from the tray menu.
"""

import copy
import ctypes
import json
import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

import settingsui
import winshutdown
from mousehook import MouseButtons

# speaker, keyboard and pyperclip are NOT imported here on purpose. speaker
# pulls in onnxruntime and numpy (about ten seconds off disk) and keyboard costs
# another three and a half; none of them is needed until there is a voice to
# drive. Loading them in the background thread lets the window appear in a
# couple of seconds with a percentage on it, rather than the screen staying
# empty until everything is ready.
keyboard = None
pyperclip = None

# ------------------------------------------------------------------ config

HOTKEY_READ = "ctrl+alt+m"  # M for cufflink (avoid alt+r combos: NVIDIA overlay)
HOTKEY_PAUSE = "ctrl+alt+space"
HOTKEY_STOP = "ctrl+alt+s"
HOTKEY_SELECTMODE = "ctrl+alt+b"  # B for browse: read every new selection
HOTKEY_FASTER = "ctrl+alt+up"
HOTKEY_SLOWER = "ctrl+alt+down"
HOTKEY_SOURCE = "ctrl+alt+g"  # G for go back, to wherever the text came from
HOTKEY_MEETING = "ctrl+alt+t"  # T for tracker: open the meeting window

# The meeting tracker is a separate program on a port of its own, started on
# demand and never at startup. Two reasons, both load-bearing: it drags in
# ctranslate2 and half a gigabyte of Whisper, which has no business on the
# path that has to put a window up in seven seconds; and a thing that can hear
# both sides of a call should not be running because the tray happens to be.
MEETING_PORT = 52720
MEETING_SCRIPT = "meeting.py"

# The voice: 70% bf_emma (British, clear) + 30% af_nicole (breathy rasp).
BLEND = (("bf_emma", 0.7), ("af_nicole", 0.3))

SPEEDS = [0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0]

# How long the pill hangs about, in milliseconds. A read used to end and the
# pill vanish 0.6s later, which is no use if you were reaching for pause.
LINGER_AFTER_SPEECH = 4000
LINGER_AFTER_TOUCH = 120_000   # once you have used it, assume you may again
LINGER_TOAST = 2000            # a setting changed while nothing is being read

# Pill palette. Warm rather than cold: the ground is a deep indigo with some
# red left in it, and the text is an off-white rather than a blue-white, so a
# thing that sits on screen while you read feels lamplit instead of clinical.
BG = "#191622"          # deep, slightly warm charcoal-violet
EDGE = "#2b2539"        # a hairline lighter than the ground, for the rim
FG = "#c3bad6"          # what has already been read: present, not shouting
MUTED = "#7b7391"       # what has not been read yet -- dim here means something
# Controls are not text: dimming them says nothing, it just makes them hard to
# find. MUTED measured 3.99:1 against the ground, under the 4.5:1 small text
# needs, and the disabled state was 1.21:1 -- invisible rather than subdued.
CONTROL = "#aaa1c4"     # 7.3:1 -- legible at a glance
CONTROL_HOT = "#efe9fb" # under the pointer
CONTROL_OFF = "#6b6483" # nothing to click: dimmed, but still there
ACCENT = "#a98bff"      # a softer violet than the old one
TRACK = "#7a63c0"       # the progress hairline, dimmer than the accent
TINT = "#463a68"        # the current word sits on this, not on solid accent
NOW = "#fff4e6"         # and is warm white, which reads as lit rather than
GREEN = "#74d3a4"       # inverted
AMBER = "#f0b273"
LIVE = "#e06c75"        # recording: the one colour nothing else here uses

# The brand, sampled from assets/cufflink-hero.png so the drawn pieces and the
# illustration are the same colours rather than nearly the same.
BRAND_NAVY = (9, 28, 54, 255)      # #091c36
BRAND_CREAM = (252, 244, 232, 255) # #fcf4e8
BRAND_MINT = (127, 228, 207, 255)  # #7fe4cf

OPAQUE = 0.97           # the pill's settled opacity
FADE_MS = 16            # a frame, near enough
FADE_STEP = 0.34        # eased: each frame closes a third of what is left

# Localhost text-in port: other local apps (e.g. ryans-assistant) send UTF-8
# text here and it plays through the same pill + hotkey controls.
CUFFLINK_PORT = 52719


def _profile(treatment, speed=0.95, sentence=0.10, clause=0.03) -> dict:
    """One profile, with a blend of its own.

    Built rather than written out, because a shared literal would be shared
    for real: edit one profile's voice in place and every profile that named
    the same list changes with it. deepcopy does not save you -- it keeps
    aliases that were already there.
    """
    return {
        "blend": [list(pair) for pair in BLEND],
        "speed": speed, "treatment": treatment,
        "sentence_pause": sentence, "clause_pause": clause,
    }


# A profile is a job, not a character: what the voice is *for*. Which of the
# characters in characters.NAMES reads it is one of the dials inside, and yours to
# change. Anything sending text asks for a profile by name, so a notification
# from Claude and a paragraph you asked for are told apart by ear rather than
# by one of them being louder.
#
# Edited in settings.json and in the settings window; these are the fallbacks.
DEFAULT_PROFILES = {
    # Everything you ask to be read. Plain, because you chose to listen to it.
    "default": _profile("clean", speed=1.0, sentence=0.25, clause=0.10),
    # Claude, interrupting. Auntie: even enough to land without alarming.
    "claude": _profile("bbc"),
    # The time, and anything else on a schedule. The Informant, because a
    # voice from a coat pocket is the least like being spoken to.
    "clock": _profile("agent"),
}

# Profiles that used to ship named after their treatment -- veronica,
# submarine, agent -- back when picking a character meant making a profile for
# it. The settings window does that job now, so an untouched one is only
# clutter in the dropdown. One you actually edited is yours and is left alone.
LEGACY_PROFILES = {name: _profile(name)
                   for name in ("veronica", "submarine", "agent")}


def settings_home() -> Path:
    """Where settings are written. Always the current name."""
    base = os.environ.get("LOCALAPPDATA") or str(ROOT)
    return Path(base) / "cufflink" / "settings.json"


def settings_path() -> Path:
    """Where settings are read from.

    The app was called Murmur until it reached the Store, where that name was
    already taken. Anyone who used it before has a folder under the old name,
    and silently starting from defaults would look like their settings had
    been thrown away -- so the old file is still read when the new one is not
    there yet. save_settings always writes the new one, which completes the
    move the first time anything is saved.
    """
    current = settings_home()
    if not current.exists():
        previous = current.parent.parent / "Murmur" / "settings.json"
        if previous.exists():
            return previous
    return current


def load_settings() -> dict:
    """Whatever is on disk, over the defaults. A broken file is ignored rather
    than fatal -- losing your settings should not cost you the voice."""
    settings = {"profiles": copy.deepcopy(DEFAULT_PROFILES)}
    try:
        stored = json.loads(settings_path().read_text(encoding="utf-8"))
    except Exception:
        return settings
    for name, profile in (stored.get("profiles") or {}).items():
        if not isinstance(profile, dict):
            continue
        if profile == LEGACY_PROFILES.get(name):
            continue  # shipped, never touched, no longer shipped
        settings["profiles"].setdefault(name, {}).update(copy.deepcopy(profile))
    return settings


def save_settings(settings: dict) -> bool:
    try:
        path = settings_home()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).parent  # packaged: models/ sits next to cufflink.exe
else:
    ROOT = Path(__file__).parent


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.BringWindowToTop.argtypes = [wintypes.HWND]
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
_user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
# Handles are pointer-sized. An undeclared restype is a C int, so on 64-bit
# every one of these comes back with its top half cut off -- and only where the
# loader happened to place things high, which is what made the shutdown
# listener work on about half its launches.
_user32.GetParent.restype = wintypes.HWND
_user32.GetParent.argtypes = [wintypes.HWND]
_user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
_user32.SetWindowRgn.restype = ctypes.c_int

_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
_gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]


_rounded_at = {}


def round_corners(window, radius: int = 16):
    """Give a borderless window rounded corners.

    Tk has no way to do this; Windows does, by clipping the window to a region.
    Without it a sharp-cornered rectangle floats over an OS whose every other
    surface is rounded, and reads as a debug window.

    Only when the size actually changes: SetWindowRgn redraws the whole window,
    and doing that on every placement tears the control row while the pill is
    fading or a word is being lit.
    """
    try:
        window.update_idletasks()
        width, height = window.winfo_width(), window.winfo_height()
        if width <= 1 or height <= 1:
            return
        key = id(window)
        if _rounded_at.get(key) == (width, height):
            return
        _rounded_at[key] = (width, height)
        hwnd = _user32.GetParent(window.winfo_id()) or window.winfo_id()
        region = _gdi32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, radius * 2, radius * 2
        )
        # Windows takes ownership of the region only if this succeeds. On
        # failure it is ours to free, and this runs on every resize -- a leaked
        # region per call runs a process out of GDI handles eventually.
        if not _user32.SetWindowRgn(hwnd, region, True):
            _gdi32.DeleteObject(region)
    except Exception:
        pass


SW_RESTORE = 9


def foreground_window():
    """The window in front, and its title, or (0, "") if there isn't one."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return 0, ""
    title = ctypes.create_unicode_buffer(160)
    _user32.GetWindowTextW(hwnd, title, 160)
    return hwnd, title.value


def raise_window(hwnd) -> bool:
    """Bring a window back to the front.

    Windows only lets the foreground process hand focus away, and cufflink is
    deliberately never the foreground -- the pill carries WS_EX_NOACTIVATE so
    clicking it does not steal focus. Attaching to the input queues of both the
    current foreground thread and the target's is the way round that.
    """
    if not hwnd or not _user32.IsWindow(hwnd):
        return False
    current = _user32.GetForegroundWindow()
    if current == hwnd:
        return True
    ours = ctypes.windll.kernel32.GetCurrentThreadId()
    threads = {
        _user32.GetWindowThreadProcessId(current, None),
        _user32.GetWindowThreadProcessId(hwnd, None),
    } - {ours, 0}
    attached = [t for t in threads if _user32.AttachThreadInput(ours, t, True)]
    try:
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, SW_RESTORE)
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
    finally:
        for thread in attached:
            _user32.AttachThreadInput(ours, thread, False)
    return _user32.GetForegroundWindow() == hwnd


def already_running() -> bool:
    ctypes.windll.kernel32.CreateMutexW(None, False, "cufflink-single-instance")
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

def start_text_server(speaker, control, profile_for=None):
    def serve():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("127.0.0.1", CUFFLINK_PORT))
        except OSError:
            print(f"Port {CUFFLINK_PORT} taken; text-in disabled.")
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
                # A first line of "::as <name>" picks a voice for what follows.
                # Anything else beginning with :: is a command, and plain text
                # is read in the default voice, as it always was.
                profile = None
                if text.startswith("::as ") and profile_for is not None:
                    first, _, rest = text.partition("\n")
                    profile = profile_for(first[len("::as "):].strip())
                    text = rest.strip()
                if text.startswith("::"):
                    control(text)
                elif text:
                    speaker.speak(text, profile=profile)

    threading.Thread(target=serve, daemon=True).start()

# ------------------------------------------------------------------ tray icons

TRAY_ART = "cufflink-tray.png"


def _tray_art():
    """The illustrated mark, if it is beside us.

    Cropped from assets/cufflink-hero-mic.png with the wordmark removed --
    lettering at 16 px is noise, not a name. Windows asks for 20, 24 or 32 px
    on a high-DPI display, and it is those sizes the drawing has to survive;
    at 16 it is a smudge and the flat mark below reads better. Shipped beside
    the exe the same way models/ is, so the frozen build finds it too.
    """
    try:
        art = Image.open(ROOT / "assets" / TRAY_ART).convert("RGBA")
        return art if art.width >= 64 else None
    except Exception:
        return None


def make_icon_image(color) -> Image.Image:
    """The tray mark: a cufflink seen face-on, two discs and a post.

    The same shape make_assets.py generates for every small icon, so the tray,
    the taskbar and the Store tile are one mark rather than three. The state
    colours the discs; the ground stays navy, because a tray icon that changes
    its silhouette is harder to find than one that changes its colour.

    Drawn at 4x and downsampled: PIL does not antialias, and a 64 px shape
    drawn directly has visibly stepped edges in the tray.
    """
    art = _tray_art()
    if art is not None:
        icon = art.resize((64, 64), Image.LANCZOS)
        # State is a pip rather than a tint: tinting a drawing this detailed
        # muddies it, and a coloured dot in the corner is legible at the size
        # this is actually rendered at.
        if tuple(color)[:3] != tuple(BRAND_CREAM)[:3]:
            pip = ImageDraw.Draw(icon)
            pip.ellipse([43, 43, 62, 62], fill=(13, 27, 47, 255))
            pip.ellipse([46, 46, 59, 59], fill=tuple(color))
        return icon

    scale, size = 4, 64
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * 0.22),
                           fill=BRAND_NAVY)
    radius, middle = big * 0.20, big * 0.5
    for centre in (big * 0.30, big * 0.70):
        draw.ellipse([centre - radius, middle - radius,
                      centre + radius, middle + radius], fill=color)
    draw.rectangle([big * 0.30, middle - big * 0.075,
                    big * 0.70, middle + big * 0.075], fill=color)
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


class _Loading:
    """Stands in for the Speaker while the model reads off disk.

    The pill and tray are built in milliseconds but the voice takes tens of
    seconds, and showing nothing for that long reads as a failure to start.
    Holding the same attributes means every call site works untouched instead
    of testing for readiness, and the real Speaker replaces it in place.
    """

    # Every attribute the real Speaker carries. Missing one is not a small
    # oversight: the tray menu reads them while the model is still loading, and
    # an AttributeError there takes the whole icon down.
    speed = 1.0
    volume = 1.0
    repeat = False
    treatment = "clean"
    sentence_pause = 0.25
    clause_pause = 0.10

    def speak(self, text, profile=None): pass

    def stop(self): pass

    def toggle_pause(self): pass

    def wait(self): pass


def load_estimate() -> float:
    """How long the last load took, so the percentage means something. The
    first run on a machine has nothing to go on and guesses."""
    try:
        return max(2.0, float(_estimate_file().read_text()))
    except Exception:
        return 20.0


def remember_load(seconds: float):
    try:
        path = _estimate_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{seconds:.1f}")
    except Exception:
        pass


def _estimate_file() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(ROOT)
    return Path(base) / "cufflink" / "load-seconds"


IMG_IDLE = make_icon_image(BRAND_CREAM)              # resting
IMG_SPEAKING = make_icon_image((127, 228, 207, 255))  # mint, and it is talking
IMG_PAUSED = make_icon_image((255, 190, 110, 255))    # amber, and it is not

# ------------------------------------------------------------------ app

def main():
    if already_running():
        print("cufflink is already running.")
        return
    ui_events: queue.Queue = queue.Queue()
    settings = load_settings()

    def profile_for(name):
        """A named profile, or None to leave the voice alone."""
        return settings["profiles"].get(name)

    print("Loading Kokoro model...")
    close_splash("loading the voice model…")
    speaker = _Loading()
    _default_profile = settings["profiles"].get("default", {})
    speaker.treatment = _default_profile.get("treatment", "clean")
    speaker.sentence_pause = _default_profile.get("sentence_pause", 0.25)
    speaker.clause_pause = _default_profile.get("clause_pause", 0.10)

    def load_voice():
        """Build the real Speaker off the tkinter thread."""
        nonlocal speaker
        global keyboard, pyperclip
        started = time.perf_counter()
        try:
            import keyboard
            import pyperclip

            from speaker import Speaker

            voice = Speaker(
                ROOT / "models" / "kokoro-v1.0.onnx",
                ROOT / "models" / "voices-v1.0.bin",
                blend=BLEND,
                on_state=lambda speaking: ui_events.put(("state", speaking)),
                on_text=lambda text: ui_events.put(("text", text)),
                on_word=lambda index: ui_events.put(("word", index)),
                on_pause=lambda paused: ui_events.put(("pause", paused)),
            )
            voice.speed, voice.volume = speaker.speed, speaker.volume
            voice.repeat = speaker.repeat
            voice.treatment = speaker.treatment
            voice.sentence_pause = speaker.sentence_pause
            voice.clause_pause = speaker.clause_pause
            # Warm the graph here rather than making the first real read pay it.
            voice.kokoro.create("ready", voice=voice.voice, speed=1.0)
        except Exception as exc:  # a missing or corrupt model, most likely
            ui_events.put(("loadfailed", str(exc)))
            return
        speaker = voice
        ui_events.put(("ready", time.perf_counter() - started))

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
        remember_source()
        text = grab_selection()
        if text:
            speaker.speak(text)
        else:
            speaker.speak("I couldn't find any selected text.")

    # Where the words came from, so there is a way back to it. Text arriving on
    # the port has no source window, and the control greys out.
    source = {"hwnd": 0, "title": ""}

    def remember_source():
        hwnd, title = foreground_window()
        if hwnd:
            source["hwnd"], source["title"] = hwnd, title

    def go_to_source():
        if raise_window(source["hwnd"]):
            return
        source["hwnd"] = 0  # it has gone; stop offering it
        render_pill()

    # --- select mode: any completed selection (drag / double-click) is read ---
    select_mode = {"on": False}
    last_spoken = {"text": None}
    drag_start = {"pos": (0, 0)}

    def toggle_select_mode():
        select_mode["on"] = not select_mode["on"]
        # A WH_MOUSE_LL hook puts cufflink in the path of every mouse event on the
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
            remember_source()
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
    root.attributes("-alpha", 0.0)
    root.configure(bg=BG)

    # A hairline rim: one pixel of a lighter colour around the whole thing,
    # which is what stops it reading as a hole cut in the desktop.
    rim = tk.Frame(root, bg=EDGE)
    rim.pack(fill="both", expand=True)
    shell = tk.Frame(rim, bg=BG)
    shell.pack(fill="both", expand=True, padx=1, pady=1)

    # Window chrome goes where window chrome goes: pin and close, top right.
    # They are not transport controls, and sitting them in the same strip as
    # play is most of why the strip read as an undifferentiated row of glyphs.
    chrome = tk.Frame(shell, bg=BG)
    chrome.pack(fill="x", padx=12, pady=(4, 0))

    # The sentence being spoken, with the current word lit as it is reached.
    reader = tk.Text(
        shell, height=3, width=52, wrap="word", relief="flat", cursor="arrow",
        bg=BG, fg=MUTED, font=("Segoe UI", 11), padx=20, pady=(4),
        highlightthickness=0, borderwidth=0, spacing1=1, spacing3=7,
        takefocus=0,
    )
    # Three weights, so attention runs read -> reading -> to come without
    # anything shouting: soft white behind, warm white on a tint at the word
    # being spoken, dim ahead of it.
    reader.tag_configure("said", foreground=FG)
    reader.tag_configure("now", foreground=NOW, background=TINT)
    reader.config(state="disabled")
    reader.pack(fill="x")

    # A two-pixel line under the text: where you are in the whole selection.
    # It is the only moving thing when the pill is otherwise still.
    progress = tk.Canvas(shell, height=2, bg=BG, highlightthickness=0)
    progress.pack(fill="x", padx=20, pady=(0, 0))

    # Three columns, the outer two weighted equally and made uniform, so the
    # transport in the middle is centred on the pill rather than on whatever
    # the two sides happen to add up to.
    controls = tk.Frame(shell, bg=BG)
    controls.pack(fill="x", padx=14, pady=(2, 8))
    controls.grid_columnconfigure(0, weight=1, uniform="flank")
    controls.grid_columnconfigure(1, weight=0)
    controls.grid_columnconfigure(2, weight=1, uniform="flank")
    speed_group = tk.Frame(controls, bg=BG)
    speed_group.grid(row=0, column=0, sticky="w")
    transport = tk.Frame(controls, bg=BG)
    transport.grid(row=0, column=1)
    modes = tk.Frame(controls, bg=BG)
    modes.grid(row=0, column=2, sticky="e")

    def touched():
        """Any deliberate interaction keeps the pill up for a while, so a second
        click never has to wait for it to come back."""
        if pill["pinned"] or pill["speaking"] or not root.winfo_ismapped():
            return
        hide_later(LINGER_AFTER_TOUCH)

    def close_pill():
        """The X dismisses the pill, not merely the audio.

        It used to just call stop(), which does nothing when nothing is
        playing -- so on an idle or pinned pill the button appeared dead. It
        went unnoticed while the pill still hid itself after 600ms.
        """
        # Set before stopping: stop() lands a state event on the queue, and the
        # handler for it re-renders -- which used to pull the pill straight back
        # onto the screen a fraction of a second after it was dismissed.
        pill["dismissed"] = True
        pill["pinned"] = False  # an explicit dismissal outranks the pin
        speaker.stop()
        cancel_hide()
        hide_pill()

    def set_reader(text):
        """Put plain text in the reading area, with no word spans behind it."""
        reader.config(state="normal")
        reader.delete("1.0", "end")
        reader.insert("end", text)
        reader.config(state="disabled")
        pill["words"] = []

    hint = {"restore": None}

    def show_hint(text):
        """A control explains itself where the reading normally goes, so there
        is no second window to place, fade or get in the way. Never during a
        read: what is being spoken matters more than what a button does."""
        if not text or pill["speaking"]:
            return
        if hint["restore"] is None:
            hint["restore"] = reader.get("1.0", "end-1c")
        set_reader(text)

    def clear_hint():
        if hint["restore"] is None:
            return
        if pill["mode"] == "listen":
            hint["restore"] = None
            render_listen()
            return
        set_reader(hint["restore"])
        hint["restore"] = None

    def explain(widget, text):
        """Hover help for anything, chip or not."""
        widget.bind("<Enter>", lambda _e: show_hint(text), add="+")
        widget.bind("<Leave>", lambda _e: clear_hint(), add="+")
        return widget

    def icon_font(size):
        """Windows ships a pushpin in its icon fonts. Segoe UI does not, which
        is the entire reason the pin has been a circle: there was no pin to
        draw. Fluent first (Windows 11), MDL2 behind it (Windows 10), and the
        old placeholder if neither is installed."""
        from tkinter import font as tkfont

        available = set(tkfont.families())
        for family in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
            if family in available:
                return (family, size)
        return None

    def chip(parent, text, command, tip=None, font=("Segoe UI", 11), pad=7):
        """A label that behaves like a flat button."""
        # pady 7 rather than 3: at 3 these were ~12x20 px, which is a poor
        # target even before the pill starts hiding itself.
        widget = tk.Label(
            parent, text=text, fg=CONTROL, bg=BG, font=font,
            padx=pad, pady=7, cursor="hand2",
        )
        widget.rest = CONTROL
        widget.bind("<Button-1>", lambda _e: (command(), touched()))
        widget.bind("<Enter>", lambda _e: widget.config(fg=CONTROL_HOT))
        widget.bind("<Leave>", lambda _e: widget.config(fg=widget.rest))
        return explain(widget, tip)

    # -- centre: the transport. Play is what people reach for, so it is the
    # biggest thing on the row and sits in the middle, which is where every
    # player anyone has ever used puts it.
    repeat_btn = chip(transport, "↻", lambda: toggle_repeat(),
                      tip="Repeat: read it again until you stop it, with a "
                          "chime between", pad=8)
    repeat_btn.pack(side="left")

    play_btn = chip(transport, "❚❚", lambda: speaker.toggle_pause(),
                    tip=f"Pause or resume  ({HOTKEY_PAUSE}).  "
                        "Clicking the text does the same.",
                    font=("Segoe UI", 15), pad=10)
    play_btn.pack(side="left", padx=2)

    source_btn = chip(transport, "↩", lambda: go_to_source(),
                      tip=f"Go back to where the text came from  ({HOTKEY_SOURCE})",
                      pad=8)
    source_btn.pack(side="left")

    # -- left: speed
    slower_btn = chip(speed_group, "−", lambda: change_speed(-1),
                      tip=f"Slower  ({HOTKEY_SLOWER})", pad=9)
    slower_btn.pack(side="left")
    speed_hud = tk.Label(speed_group, text="1.0×", fg=CONTROL_HOT, bg=BG,
                         font=("Segoe UI", 10), padx=2, pady=3, width=5)
    speed_hud.pack(side="left")
    explain(speed_hud, "Speed. Click to go back to 1.0x, or roll the wheel.")
    faster_btn = chip(speed_group, "+", lambda: change_speed(+1),
                      tip=f"Faster  ({HOTKEY_FASTER})", pad=9)
    faster_btn.pack(side="left")

    # -- right: volume, then the two modes
    # Plain BMP symbols rather than emoji: Segoe UI has no glyph for the
    # speaker, repeat or pin emoji, so they drew as empty boxes.
    volume_icon = tk.Label(modes, text="♪", fg=CONTROL, bg=BG,
                           font=("Segoe UI", 11), padx=6, pady=3)
    volume_icon.pack(side="left")
    explain(volume_icon, "Click to mute or unmute")
    BARS = 7
    volume_bar = tk.Canvas(modes, width=BARS * 8, height=16, bg=BG,
                           highlightthickness=0, cursor="hand2")
    volume_bar.hot = False
    volume_bar.pack(side="left", pady=3)
    explain(volume_bar, "Volume: click or drag a bar, or roll the wheel over it")
    for widget in (volume_bar, volume_icon):
        widget.bind("<Enter>", lambda _e: (setattr(volume_bar, "hot", True),
                                           volume_icon.config(fg=CONTROL_HOT), render_volume()), add="+")
        widget.bind("<Leave>", lambda _e: (setattr(volume_bar, "hot", False),
                                           volume_icon.config(fg=CONTROL), render_volume()), add="+")

    select_btn = chip(modes, "⇱ select", lambda: toggle_select_mode(),
                      tip=f"Select mode  ({HOTKEY_SELECTMODE}): read every new "
                          "selection as you make it. Leave it off in terminals.",
                      pad=8)
    select_btn.pack(side="left", padx=(12, 0))

    help_btn = chip(modes, "?", lambda: toggle_help(),
                    tip="What everything does", pad=8)
    help_btn.pack(side="left")

    # -- and the chrome, top right, where a window's chrome belongs
    close_btn = chip(chrome, "✕", close_pill,
                     tip="Stop and put this away",
                     font=("Segoe UI", 10, "bold"), pad=7)
    close_btn.pack(side="right")
    # The other control row. Built now, packed only in listen mode -- one
    # window, two jobs, rather than a second window nobody can find.
    listen_controls = tk.Frame(shell, bg=BG)
    listen_left = tk.Frame(listen_controls, bg=BG)
    listen_left.pack(side="left")
    record_btn = chip(listen_left, "●", lambda: toggle_record(),
                      tip="Start or stop recording this meeting  "
                          "(ctrl+alt+l).  It starts stopped: this hears your "
                          "microphone and everything your speakers play.",
                      font=("Segoe UI", 17), pad=10)
    record_btn.pack(side="left")
    # Drawn on a canvas rather than typed as block characters. The same
    # lesson the play control taught: U+2581 and friends render as a flat
    # underscore in Segoe UI at this size, so five of them at rest read as a
    # blank line and the meter looked broken rather than quiet.
    level_meters = {}
    for _source in ("you", "them"):
        holder = tk.Frame(listen_left, bg=BG)
        holder.pack(side="left", padx=(6, 0))
        tk.Label(holder, text=_source, bg=BG,
                 fg=ACCENT if _source == "you" else GREEN,
                 font=("Segoe UI", 8)).pack(side="left")
        canvas = tk.Canvas(holder, width=34, height=14, bg=BG,
                           highlightthickness=0, takefocus=0)
        canvas.pack(side="left", padx=(3, 0))
        level_meters[_source] = canvas
        explain(canvas,
                "What each side is being heard at. A flat 'you' while you are "
                "talking means the wrong microphone -- the ⚙ picks another.")
        explain(holder, "")

    def draw_meter(canvas, lit, colour):
        """Five bars, the same idiom as the volume control: round-capped
        lines, muted until there is something to show."""
        canvas.delete("all")
        for i in range(5):
            height = 3 + i * 2
            on = lit is not None and i < lit
            canvas.create_line(
                3 + i * 6, 12, 3 + i * 6, 12 - height,
                fill=colour if on else CONTROL_OFF,
                width=3, capstyle="round")
    mark_btn = chip(listen_left, "[!]", lambda: mark_latest(),
                    tip="Mark what was just said, to come back to later  "
                        "(ctrl+alt+k)", font=("Segoe UI", 10))
    mark_btn.pack(side="left")

    # Packed to the right *before* the note box claims the middle, and given
    # a width, so a long model message cannot push it off the pill.
    listen_status = tk.Label(listen_controls, text="", bg=BG, fg=MUTED,
                             font=("Segoe UI", 8), padx=6, width=16,
                             anchor="e")
    listen_status.pack(side="right")
    tk.Label(listen_controls, text="note", bg=BG, fg=MUTED,
             font=("Segoe UI", 8)).pack(side="left", padx=(10, 0))
    note_entry = tk.Entry(listen_controls, bg=EDGE, fg=NOW,
                          font=("Segoe UI", 10), relief="flat",
                          insertbackground=NOW, highlightthickness=1,
                          highlightbackground=EDGE, highlightcolor=ACCENT)
    note_entry.pack(side="left", fill="x", expand=True, padx=(6, 8))
    note_entry.bind("<Return>", lambda e: send_note())

    pins = icon_font(11)
    pin_btn = chip(chrome, "" if pins else "◉", lambda: toggle_pin(),
                   tip="Keep this on screen instead of letting it hide",
                   font=pins or ("Segoe UI", 11), pad=7)
    pin_btn.pack(side="right")

    # Window chrome goes where window chrome goes. These two belong to the
    # pill rather than to either of its jobs, and living in the read-mode
    # control row meant they vanished at exactly the moment they were wanted:
    # the ⚙ that picks a different microphone was unreachable from listen
    # mode, which is the only mode that has microphones.
    devices_btn = chip(chrome, "≡", lambda: device_menu(),
                       tip="Which microphone, which output, and the voice "
                           "settings. In both modes, because the moment you "
                           "need it is the moment somebody is already talking.")
    devices_btn.pack(side="right")
    mode_btn = chip(chrome, "listen", lambda: toggle_mode(),
                    font=("Segoe UI", 9),
                    tip="Switch the pill between reading text aloud and "
                        "following a meeting  (ctrl+alt+t)")
    mode_btn.config(width=6)
    mode_btn.pack(side="right")

    pill = {"sentence": "", "paused": False, "speaking": False,
            "words": [], "volume": 1.0, "pinned": False, "muted": 0.0,
            "pos": None, "dismissed": False, "helping": False, "at": 0,
            "mode": "read"}

    # What the pill knows about the meeting process. `version` is the last
    # thing it drew; the poll is a short line that says whether that number
    # has moved, so nothing but the number crosses the wire while nobody is
    # talking -- and none of it crosses at all outside listen mode.
    listen = {"version": -1, "recording": False, "status": "",
              "levels": {}, "lines": [], "notes": [], "job": None,
              # True while a background ask is out. Without it a slow answer
              # would let the next tick start a second one, and a stalled
              # meeting process would collect a thread every 500 ms.
              "asking": False}

    # --- volume -----------------------------------------------------------
    def render_volume():
        volume_bar.delete("all")
        for i in range(BARS):
            lit = (i + 1) / BARS <= pill["volume"] + 1e-9
            height = 4 + i * 1.5
            # Muted unless it is being used: volume is a secondary control and
            # was previously the loudest thing on the row.
            # Lines with round caps rather than rectangles: the bars were the
            # one blocky thing left beside all the soft edges.
            colour = (ACCENT if volume_bar.hot else MUTED) if lit else EDGE
            volume_bar.create_line(
                i * 8 + 3, 14, i * 8 + 3, 14 - height,
                fill=colour, width=3, capstyle="round",
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
    reader.tag_configure("dim", foreground=MUTED)
    reader.tag_configure("said", foreground=FG)
    reader.tag_configure("marked", foreground=AMBER)
    reader.tag_configure("note", foreground=NOW)
    reader.tag_configure("you", foreground=ACCENT)
    reader.tag_configure("them", foreground=GREEN)

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

    def render_progress():
        progress.delete("all")
        total = len(pill["words"])
        if not total or not pill["speaking"]:
            return
        width = max(progress.winfo_width(), 1)
        done = (pill["at"] + 1) / total
        progress.create_rectangle(0, 0, int(width * done), 2, fill=TRACK, width=0)

    def highlight_word(index: int):
        reader.tag_remove("now", "1.0", "end")
        if not (0 <= index < len(pill["words"])):
            return
        pill["at"] = index
        start, stop = pill["words"][index]
        reader.tag_add("said", "1.0", start)
        reader.tag_add("now", start, stop)
        reader.see(start)  # long sentences scroll to keep the word in view
        render_progress()

    # Kept short enough to fit the four wrapped lines the reading area has.
    HELP = (
        "❚❚ pause   − + speed   ♪ volume   ↻ repeat   ⇱ read on select"
        "\n"
        "◉ keep open   ✕ close   "
        f"·   {HOTKEY_READ} reads the selection   ·   {HOTKEY_STOP} stops"
    )

    def toggle_help():
        pill["helping"] = not pill["helping"]
        hint["restore"] = None
        set_reader(HELP if pill["helping"] else "")
        render_pill()
        if not pill["speaking"]:
            hide_later(LINGER_AFTER_TOUCH)

    # --- pin, drag and snap ------------------------------------------------
    def toggle_pin():
        pill["pinned"] = not pill["pinned"]
        if pill["pinned"]:
            cancel_hide()
            if not pill["speaking"] and not pill["words"]:
                show_sentence("cufflink is listening. " + HOTKEY_READ + " reads your selection.")
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

    # Every bit of empty pill is draggable, which now means the group frames
    # too -- the gaps between the three columns are the biggest empty areas
    # on the thing.
    for widget in (shell, chrome, controls, speed_group, transport, modes):
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
        # The variation selector asks for the plain glyph; without it Windows
        # renders these as emoji, in a little rounded box of their own.
        play_btn.config(text="\u25b8" if (pill["paused"] or idle) else "\u275a\u275a")
        select_btn.rest = GREEN if select_mode["on"] else CONTROL
        select_btn.config(fg=select_btn.rest)
        repeat_btn.rest = GREEN if speaker.repeat else CONTROL
        repeat_btn.config(fg=repeat_btn.rest)
        has_source = bool(source["hwnd"])
        source_btn.rest = CONTROL if has_source else CONTROL_OFF
        source_btn.config(fg=source_btn.rest, cursor="hand2" if has_source else "arrow")
        pin_btn.rest = AMBER if pill["pinned"] else CONTROL
        pin_btn.config(fg=pin_btn.rest)
        # The one control most people want is brighter than the rest.
        play_btn.rest = CONTROL_HOT     # the one most people reach for
        play_btn.config(fg=CONTROL_HOT)
        help_btn.rest = ACCENT if pill["helping"] else CONTROL
        help_btn.config(fg=help_btn.rest)
        place_pill()

    def work_area():
        """The desktop minus the taskbar. Placing against the full screen
        height put the control row underneath the taskbar, where the clicks
        went to the taskbar instead of to cufflink."""
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
        return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()

    # Nothing well made appears instantly. Easing rather than a linear ramp:
    # each frame closes a third of the remaining distance, which starts quickly
    # and settles, the way a physical thing does.
    fade = {"job": None, "now": 0.0}

    def fade_to(target: float, then=None):
        if fade["job"] is not None:
            root.after_cancel(fade["job"])
            fade["job"] = None

        def step():
            remaining = target - fade["now"]
            if abs(remaining) < 0.02:
                fade["now"] = target
                root.attributes("-alpha", target)
                fade["job"] = None
                if then is not None:
                    then()
                return
            fade["now"] += remaining * FADE_STEP
            root.attributes("-alpha", fade["now"])
            fade["job"] = root.after(FADE_MS, step)

        step()

    def hide_pill():
        fade_to(0.0, root.withdraw)

    def place_pill():
        root.update_idletasks()
        left, top, right, bottom = work_area()
        width, height = root.winfo_reqwidth(), root.winfo_reqheight()
        if root.winfo_ismapped():
            # Already on screen: keep the position it has. Recomputing it on
            # every render nudged the window as the text changed width, and a
            # layered window that moves leaves the old paint behind -- which is
            # why the control row appeared doubled.
            x, y = root.winfo_x(), root.winfo_y()
        elif pill["pos"]:
            x, y = pill["pos"]  # wherever it was dragged to, and left
        else:
            x = left + (right - left - width) // 2
            y = bottom - height - 12
        # However it got its position, keep every control reachable.
        x = max(left, min(x, right - width))
        y = max(top, min(y, bottom - height))
        root.geometry(f"+{x}+{y}")
        if not pill["dismissed"]:
            if not root.winfo_ismapped():
                fade["now"] = 0.0
                root.attributes("-alpha", 0.0)
                root.deiconify()
            fade_to(OPAQUE)
        round_corners(root)

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
            hide_pill()

        hide_timer["id"] = root.after(delay, done)

    def flash(message: str):
        """Show a setting change while nothing is being read.

        In listen mode the transcript owns the text area, and a message that
        replaces it has to put it back rather than leaving the pill showing a
        volume change where the meeting was.
        """
        if pill["mode"] == "listen":
            render_pill()
            hide_later()
            return
        if not pill["speaking"]:
            reader.config(state="normal")
            reader.delete("1.0", "end")
            reader.insert("end", message)
            reader.config(state="disabled")
            pill["words"] = []
        render_pill()
        hide_later()

    # --- listen mode: the same pill, doing the other job -------------------

    def meeting_listening(timeout: float = 0.35) -> bool:
        """Is anything holding the meeting port?

        A connect and an immediate close. It does not wait for the process to
        answer, which matters because the thing that answers is the thread
        doing the transcribing -- ask it while it is busy and a running
        process looks like a dead one.
        """
        try:
            with socket.create_connection(("127.0.0.1", MEETING_PORT),
                                          timeout=timeout):
                return True
        except OSError:
            return False

    def meeting_says(command: str, timeout: float = 0.6):
        """Ask the meeting process something. None when it is not running."""
        try:
            with socket.create_connection(("127.0.0.1", MEETING_PORT),
                                          timeout=timeout) as conn:
                conn.sendall(command.encode("utf-8"))
                conn.shutdown(socket.SHUT_WR)
                parts = []
                while True:
                    block = conn.recv(65536)
                    if not block:
                        return b"".join(parts).decode("utf-8", "replace")
                    parts.append(block)
        except OSError:
            return None

    def render_listen():
        """Draw the transcript into the reader, in place of the reading."""
        reader.config(state="normal")
        reader.delete("1.0", "end")
        notes_for = {}
        for note in listen["notes"]:
            notes_for.setdefault(note.get("about"), []).append(note)
        loose = [n for n in listen["notes"] if n.get("about") is None]
        if not listen["lines"] and not loose:
            reader.insert("end",
                          "listening…\n" if listen["recording"]
                          else "not recording — press ● to start\n", "dim")
        # A note written before anybody has spoken is attached to nothing, and
        # would otherwise never be drawn at all -- typed, accepted, invisible.
        for note in loose:
            at = int(note.get("at") or 0)
            reader.insert("end", f"{at // 60:02d}:{at % 60:02d} ", "dim")
            reader.insert("end", f"note: {note['text']}\n", "note")
        for line in listen["lines"]:
            stamp = int(line.get("at") or 0)
            reader.insert("end", f"{stamp // 60:02d}:{stamp % 60:02d} ", "dim")
            reader.insert("end", line.get("source", "?"),
                          "you" if line.get("source") == "you" else "them")
            if line.get("checked"):
                reader.insert("end", "  [!]", "marked")
            reader.insert("end", "\n")
            reader.insert("end", (line.get("text") or "") + "\n",
                          "marked" if line.get("checked") else "said")
            for note in notes_for.get(line.get("id"), []):
                reader.insert("end", f"    note: {note['text']}\n", "note")
        reader.config(state="disabled")
        reader.see("end")
        pill["words"] = []

    def poll_meeting():
        """Only ever scheduled while listen mode is on.

        Two things stop it opening a socket for nothing. Leaving listen mode
        returns here without rescheduling, and `set_mode` cancels whatever was
        pending. And while the pill is hidden -- which it does on its own
        after a while -- there is nobody to draw for, so it keeps its timer
        but asks nothing, at a fifth of the rate. On a laptop that is the
        difference between a background app and a background app you notice.
        """
        listen["job"] = None
        if pill["mode"] != "listen":
            return
        # Slower when nobody can see it: the timer stays, the asking mostly
        # stops. On a laptop that is the difference between a background app
        # and a background app you notice.
        every = 500 if root.winfo_ismapped() else 2500
        if not listen["asking"]:
            listen["asking"] = True
            threading.Thread(target=ask_meeting, args=(listen["version"],),
                             daemon=True).start()
        listen["job"] = root.after(every, poll_meeting)

    def ask_meeting(known_version: int):
        """Talk to the meeting process from a thread, never from the UI.

        Both of these are blocking socket reads. On the Tk thread they froze
        the pill for as long as they took, and the timeout that kept the
        freeze short -- 0.4 s -- was also short enough to miss the answer
        whenever transcription was busy, which is exactly when there is
        something to report.
        """
        try:
            answer = meeting_says("::state", timeout=2.0)
            if answer is None:
                ui_events.put(("meeting", None))
                return
            try:
                state = json.loads(answer)
            except ValueError:
                state = {}
            payload = {"state": state}
            if state.get("version", -1) != known_version:
                whole = meeting_says("::dump", timeout=4.0)
                try:
                    payload["dump"] = json.loads(whole) if whole else {}
                except ValueError:
                    payload["dump"] = {}
            ui_events.put(("meeting", payload))
        finally:
            listen["asking"] = False

    def meeting_answered(payload):
        """What ask_meeting found, applied on the UI thread."""
        if payload is None:
            listen["recording"] = False
            listen["status"] = "not running"
            listen["lines"] = []
            listen["version"] = -1
            render_listen()
        else:
            state = payload["state"]
            was_recording = listen["recording"]
            listen["recording"] = bool(state.get("recording"))
            listen["status"] = state.get("status") or ""
            listen["levels"] = state.get("levels") or {}
            if "dump" in payload:
                listen["lines"] = payload["dump"].get("lines", [])
                listen["notes"] = payload["dump"].get("notes", [])
                listen["version"] = state.get("version", -1)
                render_listen()
            elif listen["recording"] != was_recording:
                # The placeholder says whether it is recording, so it goes
                # stale the moment that changes -- and it changes before there
                # is any transcript to change with it, which is precisely when
                # somebody is looking at it to see whether the button worked.
                render_listen()
        render_listen_controls()

    def render_listen_controls():
        # The one control most people want is the brightest, the same rule
        # the play button follows on the other row.
        record_btn.rest = LIVE if listen["recording"] else CONTROL_HOT
        record_btn.config(text="●", fg=record_btn.rest)
        for source, canvas in level_meters.items():
            draw_meter(canvas, (listen["levels"] or {}).get(source),
                       ACCENT if source == "you" else GREEN)
        # Clipped rather than allowed to squeeze the note box off the pill;
        # the whole message is in the tooltip and the log either way.
        whole = listen["status"] or ""
        listen_status.config(text=whole[:22] + ("…" if len(whole) > 22 else ""))

    def start_meeting():
        """Bring the transcription process up, headless -- this pill is its
        window. It is never started at login and never by opening the pill:
        something that can hear both sides of a call waits to be asked."""
        if meeting_listening():
            return True
        import subprocess

        here = Path(__file__).resolve().parent
        script = here / MEETING_SCRIPT
        if not script.exists():
            listen["status"] = "meeting.py is missing"
            return False
        runner = here / "venv" / "Scripts" / "pythonw.exe"
        if not runner.exists():
            runner = Path(sys.executable).with_name("pythonw.exe")
        log = settings_home().with_name("meeting.log")
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as out:
                subprocess.Popen(
                    [str(runner), str(script), "--headless"], cwd=str(here),
                    stdout=out, stderr=out,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            listen["status"] = "starting…"
            return True
        except Exception as failed:
            listen["status"] = f"{failed}"
            return False

    def set_mode(mode: str):
        """Swap what the pill is for. The reader and the window stay; only the
        control row and what fills the text area change, because two windows
        for two halves of one job is what this replaced.

        Whatever happens in here, the pill ends with a control row on it. A
        half-applied switch leaves a window with no way to do anything and no
        way to say why, which is worse than either mode.
        """
        if mode == pill["mode"]:
            return
        pill["mode"] = mode
        if mode == "listen":
            controls.pack_forget()
            # The hairline is "where you are in what is being read", which
            # means nothing here; it was drawing a violet rule across the
            # middle of a transcript.
            progress.pack_forget()
            # Enough to read a exchange without the pill becoming a wall of
            # empty ground while nobody has said anything yet.
            reader.config(height=7)
            listen_controls.pack(fill="x", padx=14, pady=(2, 8))
            mode_btn.config(text="read")
            speaker.stop()
            start_meeting()
            # Draw the transcript now rather than whenever it next changes.
            # Without this the pill kept showing whatever the reader last
            # held -- a spoken message, a setting flash -- for as long as
            # nobody said anything.
            listen["version"] = -1
            render_listen()
            if listen["job"] is None:
                poll_meeting()
        else:
            if listen["job"] is not None:
                root.after_cancel(listen["job"])
                listen["job"] = None
            listen_controls.pack_forget()
            reader.config(height=3)
            # Packed in order rather than with `before=controls`. `controls`
            # is not managed at this point -- it was forgotten on the way into
            # listen mode -- and pack refuses a reference widget it is not
            # managing. That raised here, half way through the switch, leaving
            # neither control row packed: a pill with nothing on it but the
            # pin and the close.
            progress.pack(fill="x", padx=20, pady=(0, 0))
            controls.pack(fill="x", padx=14, pady=(2, 8))
            mode_btn.config(text="listen")
            show_sentence(pill["sentence"])
        if not controls.winfo_ismapped() and not listen_controls.winfo_ismapped():
            (listen_controls if mode == "listen" else controls).pack(
                fill="x", padx=14, pady=(2, 8))
        render_pill()
        place_pill()
        touched()

    def toggle_mode():
        set_mode("read" if pill["mode"] == "listen" else "listen")

    def open_listening(*_args):
        """The hotkey: put the pill up, in listen mode, wherever you are."""
        pill["dismissed"] = False
        set_mode("listen")
        place_pill()          # what actually deiconifies and fades it in
        cancel_hide()

    def toggle_record():
        if not listen["recording"] and not start_meeting():
            return
        meeting_says("::record" if not listen["recording"] else "::pause")
        listen["recording"] = not listen["recording"]
        render_listen_controls()
        touched()

    def mark_latest():
        if meeting_says("::check") is not None:
            listen["version"] = -1        # force a redraw on the next poll
        touched()

    def send_note(_event=None):
        text = note_entry.get().strip()
        if text and meeting_says(f"::note {text}") is not None:
            note_entry.delete(0, "end")
            listen["version"] = -1
        touched()

    def device_menu():
        """Which microphone, and which output is being listened to. In reach
        during the call: the moment you find out you picked the wrong one is
        the moment somebody is already talking."""
        answer = meeting_says("::devices", timeout=1.5)
        menu = tk.Menu(root, tearoff=0, bg=BG, fg=FG,
                       activebackground=TINT, activeforeground=NOW,
                       bd=0, font=("Segoe UI", 9))
        try:
            lists = json.loads(answer) if answer else {}
        except ValueError:
            lists = {}
        if not lists:
            menu.add_command(label="start listening first", state="disabled")
        for source, label in (("you", "microphone — you"),
                              ("them", "output — everyone else")):
            menu.add_command(label=label, state="disabled")
            for name, index in lists.get(source, []):
                menu.add_command(
                    label=f"   {name}",
                    command=lambda s=source, i=index: meeting_says(f"::use {s} {i}"))
            menu.add_separator()
        menu.add_command(label="Voices and settings…", command=open_settings)
        try:
            menu.tk_popup(root.winfo_pointerx(), root.winfo_pointery())
        finally:
            menu.grab_release()

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
        # The global keyboard and mouse hooks are the only things cufflink leaves
        # in other processes; drop them before anything slower.
        speaker.stop()
        try:
            if keyboard is not None:
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

    def open_settings():
        """The voices window. It takes focus, unlike the pill -- it has
        dropdowns and sliders to drive."""
        def applied(name, profile):
            if name == "default":
                speaker.treatment = profile.get("treatment", "clean")
                speaker.speed = profile.get("speed", speaker.speed)
                speaker.sentence_pause = profile.get("sentence_pause", 0.25)
                speaker.clause_pause = profile.get("clause_pause", 0.10)
                blend = profile.get("blend")
                if blend:
                    try:
                        speaker.voice = speaker.voice_for(
                            [(n, float(w)) for n, w in blend])
                    except Exception:
                        pass  # a half-typed blend should not lose the voice
            ui_events.put(("treatment", profile.get("treatment", "clean")))

        settingsui.open_window(root, settings, save_settings, speaker, applied)

    def set_treatment(name):
        def handler(_icon, _item):
            speaker.treatment = name
            profile = settings["profiles"].setdefault("default", {})
            profile["treatment"] = name
            # Shorter pauses go with a treated voice: it is meant to run on.
            flowing = name != "clean"
            speaker.sentence_pause = profile["sentence_pause"] = 0.10 if flowing else 0.25
            speaker.clause_pause = profile["clause_pause"] = 0.03 if flowing else 0.10
            save_settings(settings)
            ui_events.put(("treatment", name))
        return handler

    import characters

    voice_menu = pystray.Menu(
        *[
            pystray.MenuItem(
                label, set_treatment(name),
                checked=lambda item, name=name: speaker.treatment == name,
                radio=True,
            )
            for name, label in characters.catalogue()
        ]
    )
    def open_meeting(_icon=None, _item=None):
        """Open the meeting tracker, or raise it if it is already up.

        Asking the port first is what makes this idempotent: clicking the tray
        item twice should not leave two windows fighting over one microphone.
        The child gets its own console-free process and a log file, because a
        program started from a tray menu fails invisibly otherwise -- the same
        reason claude_notify.py keeps one.
        """
        import subprocess

        try:
            with socket.create_connection(("127.0.0.1", MEETING_PORT),
                                          timeout=0.6) as conn:
                conn.sendall(b"::show")
                return
        except OSError:
            pass

        here = Path(__file__).resolve().parent
        script = here / MEETING_SCRIPT
        if not script.exists():
            return
        runner = here / "venv" / "Scripts" / "pythonw.exe"
        if not runner.exists():
            runner = Path(sys.executable).with_name("pythonw.exe")
        log = settings_home().with_name("meeting.log")
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as out:
                subprocess.Popen([str(runner), str(script)], cwd=str(here),
                                 stdout=out, stderr=out,
                                 creationflags=getattr(subprocess,
                                                       "CREATE_NO_WINDOW", 0))
        except Exception as failed:
            print(f"meeting: {failed}")

    menu = pystray.Menu(
        pystray.MenuItem(f"Read selection: {HOTKEY_READ}", None, enabled=False),
        pystray.MenuItem(f"Pause: {HOTKEY_PAUSE} or click the pill", None, enabled=False),
        pystray.MenuItem(f"Stop: {HOTKEY_STOP} or the pill's ✕", None, enabled=False),
        pystray.MenuItem(f"Follow a meeting: {HOTKEY_MEETING}",
                         lambda icon, item: ui_events.put(("command",
                                                           ("listen", "")))),
        pystray.MenuItem("Speed", speed_menu),
        pystray.MenuItem("Volume", volume_menu),
        pystray.MenuItem("Voice", voice_menu),
        # Through the queue, not root.after: the menu runs on pystray's own
        # thread, and tkinter called from a thread that is not the one running
        # the mainloop is a crash waiting for the wrong moment.
        pystray.MenuItem(
            "Voices and settings…",
            lambda icon, item: ui_events.put(("command", ("settings", "")))),
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
        "cufflink", IMG_IDLE, f"cufflink — {HOTKEY_READ} reads your selection", menu
    )

    def control(line: str):
        """Commands arriving on the text-in port.

        Everything the hotkeys and the pill can do is reachable here, so other
        apps -- and tests -- can drive cufflink without taking over the keyboard.

            ::stop  ::pause  ::speed +1 | -1 | 1.25  ::volume 0.6
            ::select on | off | toggle  ::pin on | off | toggle
            ::repeat on | off | toggle  ::read  ::close  ::help  ::source
            ::voice clean | bbc | veronica | submarine | agent  ::settings
            ::mix tape=0.6 hiss=0.2 | off      -- the ingredient sliders
            ::as <profile> followed by a newline and the text to speak
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
        if name == "close":
            close_pill()
        elif name == "source":
            go_to_source()
        elif name == "settings":
            open_settings()
        elif name == "listen":
            open_listening()
        elif name == "mode":
            # Not `::read`: that already means "read the selection aloud" and
            # control() takes it before this is ever reached.
            set_mode("listen" if arg == "listen" else "read")
        elif name == "voice":
            import voices

            if arg in voices.TREATMENTS:
                set_treatment(arg)(None, None)
        elif name == "mix":
            # "::mix tape=0.6 hiss=0.2", or "::mix off" to hand the everyday
            # voice back to whichever character it was set to.
            import voices

            profile = settings["profiles"].setdefault("default", {})
            if arg in ("", "off", "none"):
                speaker.recipe = {}
                profile.pop("recipe", None)
            else:
                recipe = dict(speaker.recipe
                              or voices.recipe_for(speaker.treatment))
                for pair in arg.split():
                    key, _, value = pair.partition("=")
                    if key in voices.INGREDIENTS:
                        try:
                            recipe[key] = min(1.0, max(0.0, float(value)))
                        except ValueError:
                            pass
                speaker.recipe = profile["recipe"] = recipe
            save_settings(settings)
            ui_events.put(("treatment", speaker.treatment))
        elif name == "help":
            wanted = {"on": True, "off": False}.get(arg, not pill["helping"])
            if wanted != pill["helping"]:
                toggle_help()
        elif name == "speed":
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
                        pill["dismissed"] = False  # a new read un-dismisses it
                        show_sentence("")  # drop the last read's leftovers
                    else:
                        pill["paused"] = False
                        last_spoken["text"] = None  # allow re-reading later
                        hide_later(LINGER_AFTER_SPEECH)
                    render_pill()
                elif kind == "text":
                    # Reading something aloud is an explicit act, so it wins:
                    # the pill comes back from listening rather than having
                    # the transcript quietly overwritten by a sentence.
                    if pill["mode"] == "listen":
                        set_mode("read")
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
                elif kind == "treatment":
                    if not pill["speaking"]:
                        import characters

                        flash(f"♪  {characters.label(value)}")
                elif kind == "repeat":
                    render_pill()
                    if not pill["speaking"]:
                        flash("🔁  Repeat on" if value else "🔁  Repeat off")
                elif kind == "ready":
                    on_ready(value)
                elif kind == "loadfailed":
                    on_load_failed(value)
                elif kind == "command":
                    run_command(*value)
                elif kind == "meeting":
                    meeting_answered(value)
                elif kind == "selectmode":
                    flash(
                        "🖱  Select mode on — new selections are read aloud"
                        if value
                        else "🖱  Select mode off"
                    )
        except queue.Empty:
            pass
        root.after(80, poll_events)

    # The text server is started in on_ready, not here: started now it would
    # capture the stand-in speaker, whose speak() does nothing, and hold the
    # port so the real one could never bind.
    loading = {"since": time.perf_counter(), "estimate": load_estimate(), "on": True}

    def tick_loading():
        """A percentage against how long the last load took. It is an estimate
        and says so by never reaching 100 until the voice is actually there."""
        if not loading["on"]:
            return
        elapsed = time.perf_counter() - loading["since"]
        percent = min(99, int(elapsed / loading["estimate"] * 100))
        # Padded to a fixed width, and only re-placed when the number actually
        # changes. Unpadded, the text grew from "9%" to "10%" to "99%", which
        # resized the pill; a resize reapplies the corner region, and
        # SetWindowRgn with bRedraw erases the whole window to the class brush
        # -- white -- before Tk repaints it. At one tick every 200 ms that is
        # about fifty white flashes across a normal startup, and far more when
        # the model is slow to load.
        set_reader(f"Warming up the voice\u2026   {percent:3d}%")
        if percent != loading.get("shown"):
            loading["shown"] = percent
            place_pill()
        root.after(200, tick_loading)

    def on_ready(seconds: float):
        loading["on"] = False
        remember_load(seconds)
        close_splash()
        icon.title = f"cufflink — {HOTKEY_READ} reads your selection"
        start_text_server(speaker, control, profile_for)
        # Bound late and through lambdas: at startup `speaker` is still the
        # stand-in, and a bound method would keep pointing at it forever.
        keyboard.add_hotkey(HOTKEY_READ, on_read)
        keyboard.add_hotkey(HOTKEY_PAUSE, lambda: speaker.toggle_pause())
        keyboard.add_hotkey(HOTKEY_STOP, lambda: speaker.stop())
        keyboard.add_hotkey(HOTKEY_SELECTMODE, toggle_select_mode)
        keyboard.add_hotkey(HOTKEY_FASTER, lambda: change_speed(+1))
        keyboard.add_hotkey(HOTKEY_SLOWER, lambda: change_speed(-1))
        keyboard.add_hotkey(HOTKEY_SOURCE, go_to_source)
        keyboard.add_hotkey(HOTKEY_MEETING, open_listening)
        print(f"Ready in {seconds:.1f}s. {HOTKEY_READ} = read selection.")
        speaker.speak("cufflink is ready.")

    def on_load_failed(message: str):
        loading["on"] = False
        close_splash()
        set_reader(f"Could not load the voice: {message}")
        place_pill()
        print(f"Model failed to load: {message}")

    icon.title = "cufflink — warming up…"
    icon.run_detached()
    threading.Thread(target=load_voice, daemon=True).start()
    root.after(80, poll_events)
    tick_loading()
    root.mainloop()


if __name__ == "__main__":
    # Packaged, there is no interpreter for Claude Code to invoke, so the exe
    # stands in for one. Checked before anything heavy is imported: a hook
    # runs on every tool call and must not pay for a tray icon.
    if "--claude-hook" in sys.argv:
        import claudehook

        raise SystemExit(claudehook.run_as_hook())
    main()
