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

While reading, a small pill at the bottom of the screen shows the sentence
being spoken — click it to pause/resume, or its ✕ to stop. The tray icon is
green while speaking, amber while paused.

## Run

```
venv\Scripts\python.exe murmur.py
```

Model files live in `models/` (`kokoro-v1.0.onnx` + `voices-v1.0.bin`, from the
[kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0)).

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

Optional `on_state(speaking)` / `on_sentence(text)` callbacks give you
progress feedback, and `blend=` / `speed=` customize the voice.

## Voice auditions

`audition.py` / `audition2.py` regenerate the voice comparison samples in `samples/`.
