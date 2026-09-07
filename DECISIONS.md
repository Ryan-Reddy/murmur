# Decisions

Why Murmur is built the way it is, and what the numbers were. Measurements come
from a 16-core / 32-thread Threadripper 2950X — see [BENCHMARKS.md](BENCHMARKS.md)
for the current run and `bench.py` to reproduce it.

---

## Spin-wait off, threads sized to physical cores

ONNX Runtime defaults to one thread per logical core and lets them spin-wait
between operators. That cost **4.5 CPU-seconds per second of speech** and pinned
all 32 threads.

**Disabling the spinning is what recovered nearly all of it.** The thread count
barely moves total CPU, so it is free to spend on latency.

I got this wrong first time: I capped the pool at 4 threads *as well*, which
saved almost nothing further and made synthesis slow enough to be noticeable.
That was a latency-for-energy trade nobody asked for. The pool is now
`max(2, min(16, logical // 2))`.

Past the physical core count both axes get worse — SMT contention:

| threads | long RTF | CPU-s per audio-s |
|---:|---:|---:|
| 4 | 0.85 | 1.32 |
| 16 | **0.48** | **1.66** |
| 32 | 0.61 | 2.88 |

`MURMUR_THREADS` overrides it.

## The opening is split into growing pieces

Nothing is heard until the first chunk has finished rendering, so a long opening
sentence was silence for as long as it took — **6.56 s** measured for a
137-character one.

The opening is now cut into progressively larger pieces. Two rules keep it from
being a downgrade:

- **Each piece may be at most ~1.5x the one before.** A piece larger than
  `1/RTF` times its predecessor lands after the previous has finished playing,
  and the resulting gap sounds worse than the wait it replaced.
- **Breaks go at punctuation only.** A gap at a comma is heard as a pause; a gap
  mid-clause is heard as a fault. A comma-less opening is left whole unless it is
  long enough that waiting is clearly worse.

### How small the opening can get

The pipeline already renders chunk 2 while chunk 1 plays, so the only
unavoidable wait is chunk 1 itself. How short it can be is bounded by the fixed
cost of an inference — measured at **0.11 s**, plus ~0.53 s per second of audio
produced. An opening chunk only has to exceed ~0.2 s of audio to render faster
than it plays, so the maths is not the constraint. **Punctuation is.**

Sweeping the lead-in size showed the number of breaks never changes — one per
paragraph regardless, about **0.4 per five seconds of audio**, or one every
twelve seconds. Only the wait moves:

| policy | first word | breaks per 5s |
|---|---:|---:|
| no lead-in | 6.6s | 0.40 |
| break at commas | 3.3s | 0.42 |
| also break at words over 110 chars | 2.6s | 0.42 |

Since breaking more does not cost more breaks, the threshold for accepting an
audible mid-clause break dropped from 250 characters to 110 — about 3.7 seconds
of silence, which is where waiting stops being the better option. A comma-less
sentence that used to wait 6.6 s now starts at 4.2 s, with the same one break.

## One audio stream per utterance

Playback opened a fresh `sd.OutputStream` for every chunk. Opening one costs
**375 ms** measured — 1.13 s spread through a three-chunk paragraph.

I had blamed those gaps on synthesis falling behind and tuned the splitter
against a cause that wasn't there. One stream now serves a whole utterance:
gaps went **1.29 s → 0.23 s**.

## The text appears before the audio exists

`on_sentence` only fired once a chunk had been synthesized, so the words arrived
with the sound — seconds late. The text is known the moment the hotkey is
pressed. A separate `on_text` callback fires immediately: **0.7 ms**, and word
indices count across the whole text rather than restarting per chunk.

## The window comes up before the voice does

Startup was silent for as long as it took: nothing on screen at all, then a
tray icon. Measured, the wait was not mostly the model — `speaker` costs about
ten seconds because it drags in onnxruntime and numpy, and `keyboard` another
three and a half, all of it before `main()` even ran.

Neither is needed until there is a voice to drive, so both load in the
background thread. A stand-in object holds the same attributes as the Speaker
while that happens, which means the pill and tray can be built and shown without
every call site checking whether it is ready yet.

The window is now up in about seven seconds with a percentage on it. The
percentage is elapsed time against how long the last load took on this machine,
recorded in `%LOCALAPPDATA%\Murmur`; it is honest after the first run and a
guess before it, and never reaches 100 until the voice is actually there.

## The pill is placed inside the work area

It was positioned at `screenheight - 150`, accounting for neither the taskbar nor
its own height. On a 1440-tall screen with a 48 px taskbar, the pill ran to
y=1442 — **the entire control row sat underneath the taskbar and clicks went to
the taskbar.** The buttons were not flaky; they were not there.

It now uses `SPI_GETWORKAREA` and is clamped inside it however it got there,
dragging included.

## Murmur installs its own mouse hook

Not for CPU. **I claimed the `mouse` package's hook cost 8% of a core; that was
wrong** — `QueryProcessCycleTime` counts something other than executed work.
75,557 real mouse events delivered to an idle hooked process cost **0.000
CPU-seconds**.

The real reason: `mouse` installs its `WH_MOUSE_LL` hook on first use and can
never take it down — `unhook()` only empties a handler list while the callback
goes on allocating an event per mouse move forever. `mousehook.py` ignores
movement in the callback and stops for real, so with select mode off Murmur is
out of the desktop's input path entirely.

## ctypes signatures are declared, always

`winshutdown.py` crashed intermittently: `GetModuleHandleW` had no `restype`, so
ctypes truncated the module handle to a C int and `CreateWindowExW` overflowed
whenever ASLR placed the module above 2^31. **It worked when tested and broke on
a later launch** — roughly half of them, silently returning Murmur to blocking
Windows shutdown. Every signature is declared now.

## Word timing is estimated from phonemes

The v1.0 export returns audio and nothing else — no per-token durations — so
read-along cannot be exact. Each sentence's known length is divided between its
words in proportion to their phoneme counts. Phonemizing a word costs ~0.05 ms,
and per-word counts sum exactly to the whole-sentence phonemization (the
difference is one space per gap), so the split follows what the model will
actually say rather than how the words are spelled.

## Repeat is marked, not silent

A repeat that just starts again sounds like the reader stumbling back to the top.
Between passes: a 660 Hz chime at 10% under a raised-cosine envelope — a tone
that starts at full amplitude clicks — then 1.1 s of quiet. The flag is read at
the *end* of each pass, so it can be flipped mid-read.

## How the pill is drawn

Three steps of attention rather than two: what has been read is a mid tone,
the word being spoken is warm white on a soft tint, and what is still to come is
dim. The old highlight was solid violet under white, which stamped the current
word rather than lighting it, and read and current were both near-white so the
distinction between them was invisible anyway.

Rounded corners come from clipping the window to a region — Tk cannot do it, and
a sharp-cornered rectangle floating over an OS whose every other surface is
rounded reads as a debug window. It has to be reapplied on every resize, since
the region is measured in pixels.

The ground is warm: a deep indigo with red left in it, and off-white text rather
than blue-white, so something that sits on screen while you read feels lamplit
instead of clinical. The volume bars stay muted until the pointer is on them —
they were the loudest thing on a row where volume is a secondary control.

`⏸` and `▶` are rendered as emoji by Windows, each in a little rounded box of
its own, so the play control is drawn with `❚❚` and `▸` instead.

## Getting back to where the text came from

Reading a selection means looking away from it, and the pill deliberately never
takes focus, so there was no way back except finding the window yourself. It
remembers whichever window was in front when the selection was grabbed, and ↩
returns to it.

Windows only lets the *foreground* process hand focus to another window, and
Murmur is never the foreground -- the pill carries WS_EX_NOACTIVATE precisely so
that clicking it does not steal focus from what you are reading. Attaching to the
input queues of both the current foreground thread and the target's is the way
round that; a bare SetForegroundWindow is ignored.

## The pill waits for you

It used to vanish 0.6 s after a read, which is no use if you were reaching for
pause. Now: **4 s** after speech, **2 minutes** once you have clicked something
on it, **indefinitely** when pinned, and never while the pointer is on it.

## Everything is drivable over the port

The text-in port took a `::` command vocabulary covering everything the hotkeys
and the pill can do. Other apps get scripted control, and it exists because
testing UI by injecting real keystrokes and mouse events on a machine somebody is
using is not acceptable — it moves their cursor and fires hotkeys into whatever
they have focused. Screenshots plus a control port verify the same things without
touching their input.

## GPL-3.0, because it had to be

`phonemizer` and `espeak-ng` are both GPL-3.0, and the packaged build ships
`espeak-ng.dll` inside the exe, so the binary is a combined work either way.
"Freeware" was never available. See the licence table in the README.

Talking to Murmur over a socket is arm's length and carries no obligation;
importing `speaker.py` is linking and does. That distinction is why sibling
projects should use the port rather than `sys.path.insert`.

## One repo per tool, not a monorepo

Murmur is public and GPL-3.0; the sibling projects are private. One repo means
one visibility and one licence, so the split is forced rather than chosen. Tools
integrate over localhost ports, with the assistant as the only orchestrator.

## No code signing certificate

Since 2024 an EV certificate no longer grants instant SmartScreen reputation —
OV, EV and Azure Artifact Signing all have to earn it from download volume that
an app sent to a handful of people never gets. It would cost money and still
warn. Unblocking the zip before extracting removes the mark-of-the-web from
everything inside it, which is free and works today.
