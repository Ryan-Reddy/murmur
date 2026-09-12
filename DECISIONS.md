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

## Controls are not text

The design pass gave everything one dim colour, and the buttons went with it.
Measured against the ground: the idle controls sat at **3.99:1**, under the
4.5:1 small text needs, and a disabled control at **1.21:1** was not subdued but
invisible.

Dimming *text* says something -- it is what marks the words not yet read.
Dimming a **control** says nothing; it only makes it hard to find. So they have
their own three tones now, none of which is the text ramp: 7.3:1 at rest,
near-white under the pointer, and a genuinely visible 3.2:1 when there is
nothing to click.

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

## Who said it comes from which stream it arrived on

Speaker attribution normally means diarisation: cluster the voices in one
mixed recording and hope the clusters are people. It needs another model, the
good ones are gated behind a HuggingFace licence click, and it is wrong often
enough that a transcript has to be read with suspicion.

A meeting on this machine already arrives on two separate streams. **The
microphone is you; whatever the speakers are playing is everyone else.** Two
captures, two labels, no model, and an attribution that cannot be wrong. The
price is that everybody at the far end is one voice called "them", and that
is a price worth paying — the split people actually want from their own
minutes is what I said against what I was told.

Each source gets its own noise floor for the same reason a shared one fails:
a headset and a conference call arrive twenty decibels apart.

## The listener is MIT, and it is bigger than the voice

Whisper's weights are MIT, `faster-whisper` and CTranslate2 are MIT, and none
of it needs an account or a key. Nothing in the licence table moves.

Sizes are the real cost, read from the HuggingFace API rather than
remembered. Kokoro, for comparison, is 338 MB:

| model | `model.bin` | |
|---|---:|---|
| tiny | 75 MB | sloppy on names and technical words |
| base | 145 MB | still loses them |
| **small** | **484 MB** | the default; the usual sweet spot for dictation |
| medium | 1.53 GB | better on accents, heavy for a tray app |
| large-v3 | 3.09 GB | ten times the voice, on disk and in RAM |

int8 roughly halves each of those, which is why it is the default compute
type. Whether Dutch survives `small` is untested and worth testing before the
default is trusted.

## The segmenter decides the accuracy, not the model size

Whisper sees one utterance at a time, so where the cuts fall *is* the edge of
its context — a cut mid-clause loses the words on both sides of the join,
which costs more than dropping from small to base ever would. Hence: 0.3 s of
run-up kept from before the gate opened (the first consonant is already past
by the time energy proves it), 0.4 s of tail, and a cut only after 0.7 s of
silence, since a breath is 0.2–0.3 s and a clause gap around 0.5.

The first thing real audio caught: the gate calibrated its noise floor on
the *last* frame of its warm-up rather than the quietest one.
`samples/bf_emma.wav` starts talking 30 ms in, so calibration landed on the
word "Hey", set the floor at -21 dB, and the gate then chattered through a
sentence sitting at -13 to -27 — open for 2 frames in the first second. It
still produced a transcript, because the 0.7 s silence tolerance bridged the
chatter into one utterance; it just quietly lost the first word. Taking the
quietest frame instead took **2.6% WER to 0.0%** on that file. The warm-up is
also held to no longer than the run-up buffer, so nothing said while the room
is still being learned is lost.

Scored against the thirteen `samples/*.wav`, whose words are known from
`audition.py`, `tiny` gets **0.4% WER at RTF 0.18** — which says the pipeline
is right, and nothing at all about the model choice. That is clean synthetic
speech with no room, no crosstalk and no accent; it is the easiest audio that
will ever go through this.

Two known-bad seams, both marked in the source. Cutting a monologue at 25
seconds lands wherever the clock says rather than at a pause; Whisper's window
is 30 s and ignores the rest, so it has to be cut somewhere, but the quietest
frame in the last second would be a better somewhere. And the gate is energy
against a learned floor, which cannot tell a voice from a keyboard — the
upgrade is the Silero VAD `faster-whisper` already ships, and onnxruntime is
in the venv for Kokoro already, so it costs no new dependency.

## Verbatim means every sentence, not every sound

Whisper is a transcriber, not a stenographer: it punctuates, it tidies, and
"um" and "you know" mostly do not survive it. What is achievable is every
sentence, in order, attributed and timestamped, with nothing summarised away —
so `condition_on_previous_text` is off (carrying context is what makes it
repeat itself forever after one bad segment, and lets it correct what it heard
into what it expected), no `initial_prompt`, and temperature 0.

The one thing it invents is on near-silence, where it emits whatever its
training data put after silence: subtitle credits. `Ondertiteling door de
Amara.org gemeenschap` is the most common wrong line in an unattended Dutch
transcript. Those lines are **marked, not deleted** — "thank you" is also a
thing people say, and a filter that is sometimes wrong must not be the thing
that silently removes a real sentence.

## Capturing the far end is still open

PortAudio 19.7 as shipped with `sounddevice` 0.5.6 exposes no WASAPI loopback
flag, so system audio has to come from somewhere else: `Stereo Mix` under
WDM-KS where the driver offers it (this machine does), or `pyaudiowpatch` /
`soundcard` where it does not. Not chosen yet, and `listener.capture` is
deliberately four lines holding no decisions so that choosing later changes
nothing else.

## The meeting tracker starts stopped

It hears the far end by capturing the output device, which means it hears
everything the speakers play -- a call, a video, a voice message from your
mother. An early build began capturing the moment it launched, and while I was
debugging something else it quietly transcribed several minutes of a personal
voice note that happened to be playing. Nothing about that was subtle or
recoverable-by-design; it was simply wrong.

So: it opens holding nothing, recording nothing, and says **not recording** on
its own face. Starting is a deliberate act -- the dot, Ctrl+Alt+R, or
`::record` -- and pausing *closes the devices* rather than dropping frames,
the same reasoning as the mouse hook: while paused it is not in the machine's
audio path at all, which is a fact about the process rather than a promise
about its code.

Transcripts are written to `%LOCALAPPDATA%\Murmur\meetings`, never into the
working tree, and never over a file that already exists. The filename was
minute-resolution to begin with; two sessions closing in the same minute wrote
the same name and the second replaced the first. Losing a transcript is not
recoverable, so the name now carries seconds and collides into a new file
rather than over the old one.

## Level meters, because the failure is invisible in the output

A transcript that says `them` for an hour looks identical whether the far end
did all the talking or your microphone was never in the room. Tested on a real
call, everything came out as `them` and there was no way to tell which of
those it was -- the device we open is the *default* input, which is not always
the one the call is using.

So each source carries a live level, and the window shows two meters. A silent
`you` meter while you are talking is the whole diagnosis, visible in a second,
and `--mic <index>` (from `meeting.py --devices`) is the fix.

## A loopback endpoint is silent until something plays

The first rule for choosing between identically-named loopback devices was
"keep the one that delivers frames", since the dead twin delivers none, ever.
That rule is right only while audio is playing. Measured on an idle machine,
**neither** endpoint delivers a callback -- including the one that had worked
minutes earlier. Which is to say the probe fails exactly when it is asked, at
the start of a meeting.

The order is now: what `PyAudioWPatch` itself says the default output's
loopback is; then a name match against the WASAPI default output; then the
delivery probe as a tiebreak, which is decisive when something happens to be
playing. When nothing separates them it picks the first rather than refusing,
because a wrong endpoint shows up on the meters in seconds and no endpoint at
all shows up as an hour of silence.

## Every line is heard twice, because real time was the wrong target

Measured on this machine, three samples, threads set as above:

| model | RTF | headroom against live speech |
|---|---:|---|
| tiny | 0.13 | plenty |
| base | 0.25 | plenty |
| small | 0.69 – 1.0 | none worth having |
| small, threads left to CTranslate2 | 1.23 | **loses ground every minute** |

The model that keeps up is not the accurate one, and the accurate one cannot
keep up. Picking either is a bad trade: `base` live means reading a worse
transcript all meeting, and `small` live means the words arrive later and
later until they land in a heap, which is what a real call actually did.

So both. `base` puts words on screen while somebody is still talking, and
`small` walks along a few seconds behind rewriting each line properly. The
fast pass is the one with a deadline; the second has none, because the words
are already up and are only improving. A refinement that agrees with the fast
pass redraws nothing -- a line that flickers and settles on what it already
said is worse than one that never moved.

That makes **identity** the whole problem, not speed. A line you marked, or
hung a note on, is rewritten underneath you seconds later, and everything you
did to it has to survive. Marks and notes are therefore held against a line's
`id` rather than its words, and `Minutes.add` moves them across when a better
hearing replaces a line. Getting that wrong would not raise: it would silently
drop the only work anybody does during a meeting.

The first version of the refining pass transcribed each utterance **twice** --
once to compare against what was already shown, once inside the code that
emitted the new line. It doubled the cost of the expensive model, which is the
one thing this design exists to spend carefully. Caught by a test rather than
by a meeting, because the canned transcriber in the tests runs out of answers
on the second call and the transcript went blank.

Turned off with `--refine-with ""`, which also stops the audio being held for
a second pass -- an utterance is up to 25 s of 16 kHz float32, about 1.6 MB,
which is fine for the handful in the queue and not for a meeting's worth.
