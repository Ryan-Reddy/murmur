# Murmur

Reads any selected text aloud, anywhere on Windows, fully offline. Powered by
[Kokoro-82M](https://github.com/thewh1teagle/kokoro-onnx) — nothing ever leaves
your machine.

Voice: 70% `bf_emma` + 30% `af_nicole` (British, sweet, a little raspy).
Tweak the `BLEND` constant in `murmur.py` to taste.

## Hotkeys

| Keys | Action |
|---|---|
| `Ctrl+Alt+M` | Read the current selection (press again to read a new one) |
| `Ctrl+Alt+Space` | Pause / resume (or click the pill) |
| `Ctrl+Alt+S` | Stop (or click the pill's ✕) |
| `Ctrl+Alt+B` | Select mode: every new selection (drag / double-click) is read at once |
| `Ctrl+Alt+Up` / `Down` | Faster / slower |

Select mode (also in the tray menu) is great for quick browsing; keep it off
in terminals, where the copy it triggers (Ctrl+C) can mean "interrupt".

While reading, a pill at the bottom of the screen shows the sentence being
spoken with the current word lit as it is reached; words already read stay
bright, the rest stay dim. Under it are the controls:

| Control | Does |
|---|---|
| ⏸ / ▶ | pause / resume |
| − `1.0×` + | speed (the readout also takes the mouse wheel) |
| 🔊 bars | volume — click or drag a bar, or use the wheel |
| ⇱ select | select mode on/off, green while on |
| ✕ | stop |

The pill hides a moment after reading ends, but never while the pointer is on
it, so there is time to reach a control. The tray icon is green while speaking,
amber while paused.

Select mode is the only thing that wants mouse events, so Murmur's mouse hook
exists exactly as long as select mode does — with it off, Murmur is not in the
system's mouse input path at all.

## Install

Clone the repo and double-click **`Install.cmd`**. It finds (or installs) Python,
builds the virtual environment, downloads the voice model, and offers to start
Murmur with Windows.

```
git clone https://github.com/Ryan-Reddy/murmur
cd murmur
.\Install.cmd
```

The two model files are 338 MB, far too big for git, so they are not in the
clone — the installer fetches them from the
[kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0)
and checks them against a known SHA-256 before keeping them. It is safe to run
again; anything already in place is left alone. `-Unattended` skips the
questions, `-Autostart` answers the startup one with yes.

That one download is the only time Murmur touches the network. After it, the
machine can be offline forever.

## Run

```
venv\Scripts\python.exe murmur.py
```

Model files live in `models/` (`kokoro-v1.0.onnx` + `voices-v1.0.bin`).

## CPU use

Idle Murmur costs nothing measurable; all the work happens while it is actually
speaking. ONNX Runtime would by default give the model one thread per core and
let those threads spin-wait between operators, which on a 32-thread desktop
burned 4.5 CPU-seconds per second of speech. Murmur caps it at four
non-spinning threads instead: about 1 CPU-second per second of speech, still
roughly twice as fast as playback, and around half the memory.

Set `MURMUR_THREADS` to trade back the other way — higher starts the first
sentence sooner without costing much more total CPU, lower is gentler on a
laptop. Below two threads synthesis stops keeping up with playback.

The global keyboard and mouse hooks, measured separately, cost nothing worth
chasing: 75,000 mouse events delivered to a hooked but idle process did not
move its CPU time at all.

## Text-in port (voice service for other apps)

While running, Murmur listens on `127.0.0.1:52719`. Any local app can send
UTF-8 text (close the connection to finish) and it plays with the full pill +
pause/stop controls — this is how
[ryans-assistant](https://github.com/Ryan-Reddy/ryans-assistant) speaks.
Sending the literal text `::stop` stops playback.

```python
import socket
with socket.create_connection(("127.0.0.1", 52719)) as c:
    c.sendall("Hello from another project.".encode("utf-8"))
```

Anything starting with `::` is a command rather than something to read aloud,
so another app — or a test — can drive Murmur without touching the keyboard:

| Command | Does |
|---|---|
| `::stop` | stop reading |
| `::pause` | pause / resume |
| `::read` | read the current selection |
| `::speed +1` / `::speed -1` / `::speed 1.25` | a step at a time, or the nearest preset |
| `::volume 0.6` | 0.0 to 1.0 |
| `::select on` / `off` / `toggle` | select mode |

## Standalone build (no Python needed)

```
venv\Scripts\python.exe -m PyInstaller --noconfirm --noconsole --name Murmur --collect-all espeakng_loader --collect-all phonemizer --collect-all kokoro_onnx murmur.py
Copy-Item -Recurse models dist\Murmur\models
```

Ship the `dist\Murmur` folder (or zip it). The recipient just unzips anywhere
and runs `Murmur.exe` — no install, no Python, fully offline. A second launch
is ignored (single-instance guard), and quitting is via the tray icon.

## Autostart

Put a shortcut to `Murmur.exe` (or `venv\Scripts\pythonw.exe murmur.py` for
the dev copy) in `shell:startup`.

## Use in your own projects

The engine lives in `speaker.py` with no UI dependencies — just
`kokoro-onnx` + `sounddevice` and the two model files:

```python
from speaker import Speaker

s = Speaker("models/kokoro-v1.0.onnx", "models/voices-v1.0.bin")
s.speak("Hello there.")   # non-blocking, streams sentence by sentence
s.stop()
```

Optional `on_state(speaking)` / `on_sentence(text)` / `on_word(index)` callbacks
give you progress feedback — `on_word` is what drives the read-along highlight —
and `blend=` / `speed=` / `volume=` / `threads=` customize the rest.

## Voice auditions

`audition.py` / `audition2.py` regenerate the voice comparison samples in `samples/`.
