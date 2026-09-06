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

The pill appears with the whole selection in it the moment you press the
hotkey — about a millisecond, rather than waiting for the first audio — and
lights each word as it is reached, scrolling to keep up. Words already read stay
bright, the rest stay dim.

Repeat reads it again until you stop it, with a soft chime and a beat of quiet
between passes so a repeat is obviously a repeat rather than the reader
stumbling back to the top. Under it are the controls:

| Control | Does |
|---|---|
| ⏸ / ▶ | pause / resume |
| − `1.0×` + | speed (the readout also takes the mouse wheel) |
| ♪ bars | volume — click or drag a bar, or use the wheel |
| ↻ | repeat — read it again until stopped, green while on |
| ⇱ select | select mode on/off, green while on |
| ◉ | pin it open — it stops hiding itself, amber while pinned |
| ? | what everything does |
| ✕ | dismiss — stops the reading and puts the pill away |

Hovering any control explains it in the text area, and **?** lists the lot.

Click the text itself to pause or resume, and **drag the pill anywhere**; it
snaps to a screen edge or the centre line if you drop it near one, and stays
where you left it. Pin it if you want the controls to hand rather than only
during a read.

The pill hides a moment after reading ends, but never while the pointer is on
it, so there is time to reach a control. The tray icon is green while speaking,
amber while paused.

Select mode is the only thing that wants mouse events, so Murmur's mouse hook
exists exactly as long as select mode does — with it off, Murmur is not in the
system's mouse input path at all.

## Install

One line in PowerShell, nothing to download first:

```powershell
irm https://raw.githubusercontent.com/Ryan-Reddy/murmur/main/install.ps1 | iex
```

That fetches Murmur into `%USERPROFILE%\Murmur`, finds (or installs) Python,
builds the virtual environment, downloads the voice model and sets up the
shortcuts. It uses `git` when it is there and falls back to the source zip when
it is not, so nothing has to be installed beforehand. To put it somewhere else:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/Ryan-Reddy/murmur/main/install.ps1))) -InstallDir D:\Murmur
```

Or clone it yourself and double-click **`Install.cmd`**:

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

Afterwards, **`Murmur.cmd`** is the thing to double-click — it puts up the
reddy.world banner, starts Murmur, and closes itself while Murmur stays in the
tray. The desktop and startup shortcuts both point at it, so the banner shows on
every sign-in. The banner lives in `assetsanner.txt` — the figlet is lifted
verbatim from the [reddy.world](https://github.com/Ryan-Reddy/reddy.world)
README, so edit that file directly if it should say something else.

The banner is sized to fit a default console on its own — Windows Terminal
ignores `mode con`, so anything taller than about 25 lines scrolls its own top
off the screen.

## Run

```
venv\Scripts\python.exe murmur.py
```

Model files live in `models/` (`kokoro-v1.0.onnx` + `voices-v1.0.bin`).

## CPU use

Idle Murmur costs nothing measurable; all the work happens while it is actually
speaking. ONNX Runtime would by default give the model one thread per core and
let those threads spin-wait between operators, which on a 32-thread desktop
burned 4.5 CPU-seconds per second of speech. Murmur turns the spinning off and
sizes the pool to the machine — `max(2, min(16, logical // 2))`, which is the
physical core count on most desktops — for about a third of the original energy.

Disabling the spinning is what recovered nearly all of that; the thread count
barely moves total CPU, so it is free to spend on latency. Measured on a
16-core/32-thread Threadripper: RTF 0.67 at 6 threads, 0.49 at 16, 0.47 at 32.
The knee is at the physical core count — SMT adds almost nothing but heat —
hence the halving, capped at 16 and floored at 2. `MURMUR_THREADS` overrides it.

Nothing is heard until the first chunk has finished rendering, so a long opening
sentence used to mean seconds of silence — 6.6 of them for a 137-character one.
The opening is now cut at commas into progressively larger pieces, each at most
half again the size of the one before, which is as fast as synthesis can grow
without falling behind playback. First word in about 2 seconds instead of 6.6.

Playback also used to open a fresh audio stream for every chunk, and opening one
costs ~375 ms — 1.1 seconds of silence spread through a three-chunk paragraph,
which sounded like synthesis falling behind but was the sound device being
reopened. One stream now serves a whole utterance: gaps went from 1.29s to
0.23s.

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
| `::pin on` / `off` / `toggle` | keep the pill on screen instead of letting it hide |
| `::repeat on` / `off` / `toggle` | read it again until stopped |
| `::close` | dismiss the pill |
| `::help on` / `off` / `toggle` | show what everything does |

## Standalone build (no Python needed)

```
venv\Scripts\python.exe -m pip install pyinstaller
.uild.ps1
```

That regenerates the icon and splash, runs PyInstaller with them plus the
Windows version resource, copies `models\` next to the exe and zips the lot
into `dist\Murmur-win64.zip` (365 MB). `-NoZip` stops before the slow part.

The recipient unzips anywhere and runs `Murmur.exe` — no install, no Python,
no network. A splash appears immediately while the 310 MB model is read (about
four seconds warm, longer on the very first run when it comes off the disk
cold), so the app never looks like it failed to start. A second launch is
ignored (single-instance guard), and quitting is via the tray icon.

### The SmartScreen warning, and how to not get it

The exe is unsigned, so a *downloaded* zip triggers "Windows protected your PC".
The warning comes from the mark-of-the-web that browsers attach to downloads,
and Windows copies that mark onto every file unzipped out of a marked archive.
Remove it from the zip and nothing inside it is ever marked:

> **right-click the zip → Properties → tick Unblock → Apply, then extract**

Tell whoever you send it to do that *before* extracting — afterwards is too
late, the mark is already on the files. Two other routes never pick up a mark
at all: copying the zip from a USB stick, or cloning the repo and running
`Install.cmd`.

A code signing certificate would **not** help here. Since 2024 an EV
certificate no longer grants instant SmartScreen reputation — OV, EV and Azure
Artifact Signing all have to earn it from download volume, which an app sent to
one person will never accumulate. It would cost money and still warn.

`make_assets.py` draws `assets\murmur.ico` and `assets\splash.png` from the
same shape as the tray icon; run it after changing the look.

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

## Licence

Murmur is [GPL-3.0](LICENSE). It has to be: it speaks through
[espeak-ng](https://github.com/espeak-ng/espeak-ng) and
[phonemizer](https://github.com/bootphon/phonemizer), both GPL-3.0, and the
packaged build ships espeak-ng's DLL inside the exe.

| | |
|---|---|
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) — the voice | Apache-2.0 |
| [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) | MIT |
| [espeak-ng](https://github.com/espeak-ng/espeak-ng) — phonemes | GPL-3.0 |
| [phonemizer](https://github.com/bootphon/phonemizer) | GPL-3.0 |
| [pystray](https://github.com/moses-palmer/pystray) — the tray icon | LGPL-3.0 |
| [onnxruntime](https://onnxruntime.ai) · [keyboard](https://github.com/boppreh/keyboard) | MIT |
| [soundfile](https://github.com/bastibe/python-soundfile) · [pyperclip](https://github.com/asweigart/pyperclip) · [numpy](https://numpy.org) | BSD |
| [Pillow](https://python-pillow.org) | MIT-CMU |

The model files are not redistributed here — the installer fetches them from the
kokoro-onnx releases and checks them against a known SHA-256.

## Why it is built this way

[DECISIONS.md](DECISIONS.md) records the non-obvious choices and the numbers
behind them — including the ones I got wrong first and had to correct.

## Tests and benchmarks

```
venv\Scripts\python.exe -m unittest discover -s tests -v
venv\Scripts\python.exe bench.py
```

The tests cover everything that can be checked without making a sound — the
splitter (including that it never drops a word), the lead-in ramp, the volume
curve, the thread sizing, read-along spans, and that the mouse hook really does
stop. Anything needing the model skips itself when `models\` is absent.

`bench.py` measures synthesis cost across thread counts, time to first word,
gaps and idle draw, and writes [BENCHMARKS.md](BENCHMARKS.md). It speaks aloud
while running, because measuring the gaps means actually playing the audio.
