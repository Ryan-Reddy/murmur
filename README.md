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
| `Ctrl+Alt+G` | Go back to the window you read from |

Select mode (also in the tray menu) is great for quick browsing; keep it off
in terminals, where the copy it triggers (Ctrl+C) can mean "interrupt".

The pill appears with the whole selection in it the moment you press the
hotkey — about a millisecond, rather than waiting for the first audio — and
lights each word as it is reached, scrolling to keep up. Words already read stay
bright, the rest stay dim.

Repeat reads it again until you stop it, with a soft chime and a beat of quiet
between passes so a repeat is obviously a repeat rather than the reader
stumbling back to the top. Under it are the controls:

```
┌────────────────────────────────────────────────┐
│                                         ◉   ✕  │
│  the text, with the current word lit           │
│  ─────────────                                 │
│   −  1.0×  +       ↻  ⏸  ↩       ♪▁▂▃  ⇱  ?   │
│      speed         transport      volume/modes │
└────────────────────────────────────────────────┘
```

Laid out the way a player is laid out. Pin and close are window chrome, so
they sit top right. Pause is the thing you reach for, so it is the largest
control and it is dead centre — a three-column grid with the outer two
weighted equally, so the transport is centred on the pill rather than on
whatever the two sides happen to add up to.

| Control | Does |
|---|---|
| ⏸ / ▶ | pause / resume — centre, and the biggest thing on the row |
| ↻ | repeat — read it again until stopped, green while on |
| ↩ | go back to the window the text came from |
| − `1.0×` + | speed (the readout also takes the mouse wheel) |
| ♪ bars | volume — click or drag a bar, or use the wheel |
| ⇱ select | select mode on/off, green while on |
| ? | what everything does |
| ◉ | pin it open — it stops hiding itself, amber while pinned |
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
every sign-in. The banner lives in `assets\banner.txt` — the figlet is lifted
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
| `::source` | go back to the window the text came from |
| `::voice clean` / `bbc` / `veronica` / `submarine` / `agent` | who reads the everyday voice |
| `::as <profile>` + newline + text | speak that text in a named profile |
| `::settings` | open the settings window |
| `::help on` / `off` / `toggle` | show what everything does |

## Speak Claude Code notifications

`integrations/claude-code/claude_notify.py` is a Claude Code hook that posts
to the text-in port as `::as claude`, so Murmur says what your Claude Code
sessions are up to — in every project, in Auntie's voice rather than its own,
so an interruption is recognisable before you have parsed a word of it:

- *"my-project: Claude needs your permission to use Bash"* — the
  `Notification` event, whenever a session wants attention;
- *"my-project: Claude is done. Tests pass. Want me to open the PR?"* — the
  `Stop` event, reading the opening of Claude's last message (markdown
  stripped, about 300 characters) plus its closing question, from the
  session transcript the hook is handed;
- *"my-project: Still working: editing main.py."* — `PostToolUse`, throttled
  to nothing until a turn has run a minute and then at most every three
  minutes per session (`TURN_WARMUP` and `NARRATE_EVERY` in the script).

Stdlib only, so any Python 3 runs it. Add the same entry under all three
events in `~/.claude/settings.json` (exec form: no shell, no quoting):

```json
"hooks": {
  "Notification": [ { "hooks": [ { "type": "command",
    "command": "C:\\Python312\\python.exe",
    "args": ["C:\\path\\to\\murmur\\integrations\\claude-code\\claude_notify.py"],
    "async": true, "timeout": 10 } ] } ],
  "Stop": [ /* the same entry */ ],
  "PostToolUse": [ /* the same entry */ ]
}
```

Don't add it under `PermissionRequest` or `PreToolUse`/`AskUserQuestion`:
both raise a `Notification` as well, so you'd hear everything twice. If
Murmur isn't running the hook connects to nothing and stays silent; every
spoken line is also logged to `claude_notify.log` next to the script.

## Microsoft Store

`.\package.ps1` builds `dist\Murmur.msix` for submission. The reason to
bother is that **Microsoft signs it**, which removes the SmartScreen warning
without a certificate of your own. [STORE.md](STORE.md) is the whole process:
the account, the two identity strings, the `runFullTrust` justification, and
the custom-licence step that GPL-3.0 needs (the same route VLC takes).

## Standalone build (no Python needed)

```
venv\Scripts\python.exe -m pip install pyinstaller
.\build.ps1
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

`voices.py` is usable on its own, and needs nothing but numpy:

```python
import voices

audio = voices.treat("bbc", audio, 24000)                    # a character
audio = voices.cook(audio, 24000, {"tape": 0.6, "hiss": .2}) # or a mix
```

The modules split along what they cost to import, because the tray icon has to
appear before the model has loaded:

| | |
|---|---|
| `murmur.py` | the tray, the pill, the hotkeys, the port |
| `speaker.py` | synthesis and playback — no UI |
| `voices.py` | the treatment chains and the mixer — numpy, ~700 ms |
| `characters.py` | who the voices are: names only, no imports, ~3 ms |
| `settingsui.py` | the settings window |
| `mousehook.py` · `winshutdown.py` | the two bits of Win32 |

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

## Starting up

The window appears in about seven seconds and shows a percentage while the voice
loads behind it, rather than nothing at all until everything is ready. The
percentage is measured against how long the last load took on this machine, so
it is honest after the first run and a guess before it.

Getting there meant not importing the heavy things up front: `speaker` pulls in
onnxruntime and numpy (about ten seconds off disk) and `keyboard` costs another
three and a half. None is needed until there is a voice to drive, so they load
in the background thread while the window is already up.

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

## Voices

Kokoro gives a clean studio voice. A *treatment* puts it through something, so
a notification and a paragraph you asked for are told apart by ear without
either having to be louder. They are offered as people, because nobody picks a
voice by its compressor settings. Pick one from the tray under **Voice**, in
the settings window under **Read by**, or per message over the port.

| | | key |
|---|---|---|
| **Murmur** | plain, no colour — untouched | `clean` |
| **Auntie** | the shipping forecast: even and measured, compression doing the work | `bbc` |
| **Veronica** | offshore, after dark — narrower, limited far harder because pirates competed on loudness, with a skywave fade and a noise floor | `veronica` |
| **Abbey** | tape, wound a little slack: wow and flutter, double tracking, asymmetric saturation, a plate behind it | `submarine` |
| **The Informant** | a recorder in a coat pocket — narrow at both ends, honking where a tiny earpiece resonates, an AGC brutal enough to catch a whisper across a room | `agent` |

### Settings

**Tray → Voices and settings…** (or `::settings`) opens a window with every
dial a profile holds: the two voices and their mix, the speed, who reads it,
the mixer below, and both pauses. **Hear it** speaks a sample in whatever the
form currently says, saved or not — nothing touches the running voice until
you press **Save**, so a bad guess costs nothing.

Voices are offered by what they are rather than by their key, since `bf_emma`
is something you have to learn before it tells you anything:

> **Emma** — British, higher, clear and close
> **Nicole** — American, higher, breathy, with a rasp
> **Yunxi** — Mandarin, lower

Accent and range are decoded from the key. The character notes exist only for
the six voices that have actually been listened to — the other 48 get their
accent and range, which is true, rather than a description someone made up.
`samples/` holds a dozen rendered auditions, and `audition.py` renders more.

### The mixer

The five characters are not five things. They are five points in one space,
and the space between them is reachable — a lot of transistor, a twist of spy,
less tape. Eight ingredients, each 0 to 100%:

| | |
|---|---|
| **narrow** | how much of the band survives — radio, then telephone |
| **transistor** | driven until it warms, then until it grits |
| **squash** | the limiter; high is flat, loud, and runs on |
| **tape** | wow, flutter, and a second pass wandering behind the first |
| **spy** | the honk of a tiny earpiece, and an AGC chasing a whisper |
| **fade** | a signal breathing, the way skywave does |
| **room** | a short tail, so the end of a word laps the next |
| **hiss** | the floor under everything |

Choosing a character loads its recipe into the sliders. Where each character
sits was **fitted, not guessed**: a coordinate search against that character's
own output, scored on the long-term spectrum across fourteen bands plus the
loudness envelope over time.

The first fit was wrong in a useful way. Left free, it put tape at 0.29 and
fade at 0.23 on the BBC chain, because both moved the spectrum the right way —
and wow and flutter on a newsreader is exactly what that metric cannot see and
the ear cannot miss. Refitted over only the ingredients each character
actually contains. How close each lands, RMS over those bands:

| Abbey | Auntie | Veronica | The Informant |
|---|---|---|---|
| 0.95 dB | 1.50 dB | 1.89 dB | **3.72 dB** |

The Informant is not close enough to pass off as the same thing. So **the
named characters still run their own hand-built chains, exactly as built**, and
a recipe is only where the sliders start from. Choose a name and it sounds as
it always did; move a dial and the mixer takes over. A profile that has been
mixed stores a `recipe` alongside its `treatment`:

```json
"default": {
  "treatment": "agent",
  "recipe": {"narrow": 0.45, "transistor": 1.0, "squash": 0.55,
             "tape": 0.55, "spy": 0.25, "fade": 0.0, "room": 0.35, "hiss": 0.3}
}
```

### Profiles

A *profile* is a job — what a voice is **for** — and which character reads it
is one of the dials inside. Three ship, and you can add your own:

| | reads | as |
|---|---|---|
| `default` | everything you ask for | Murmur |
| `claude` | Claude Code's notifications | Auntie |
| `clock` | the time, and anything else on a schedule | The Informant |

They live in `%LOCALAPPDATA%\Murmur\settings.json` — each sets a voice blend,
speed, treatment and how short the pauses run — and the settings window
(**tray → Voices and settings…**, or `::settings`) edits them without going
near the file. Any app can ask for one:

```python
import socket
with socket.create_connection(("127.0.0.1", 52719)) as c:
    c.sendall("::as claude
The tests have passed.".encode("utf-8"))
```

The chain runs once, on the audio the model has just rendered — it is not a
second pass over anything. A style vector decides *who* is speaking; no style
vector can make a voice arrive through a 3.5 kHz receiver with the limiter
pinned, and there is nothing to pre-compute because the words are different
every time.

It is cheap where it counts. Measured on the shipped model:

| | lead-in (1.34 s of audio) | full chunk (7.81 s) |
|---|---|---|
| synthesis | 909 ms | 3252 ms |
| `bbc` | +14 ms (+2%) | +148 ms (+5%) |
| `veronica` | +16 ms | +150 ms |
| `submarine` | +18 ms | +188 ms |
| `agent` | +21 ms (+2%) | +179 ms (+6%) |

Only the lead-in touches how soon you hear the first word, and there it is
about a fiftieth of the synthesis it follows. Every chunk after that is
treated while the previous one is still playing.
