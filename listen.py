"""Drive the listener by hand: from a file, from a microphone, or against
speech whose words are already known.

    venv\\Scripts\\python.exe listen.py --devices
    venv\\Scripts\\python.exe listen.py --check --model small
    venv\\Scripts\\python.exe listen.py --file recording.wav
    venv\\Scripts\\python.exe listen.py --mic --language nl

Nothing here is imported by cufflink. It exists so the parts that no unit test
can reach -- a real model, a real microphone, a real voice -- can be tried
without wiring the listener into the tray first.

Audio is never written to disk. Only the transcript is, and only when asked.
"""

import argparse
import queue
import sys
import threading
import time
from pathlib import Path

import numpy as np

import listener
from listener import FRAME, MODELS, SAMPLE_RATE, THEM, YOU, Meeting, WhisperTranscriber

ROOT = Path(__file__).resolve().parent
MODEL_ROOT = listener.model_cache()

# What every file in samples/ says, from audition.py. Known words are the only
# way to answer "is this good enough" with a number instead of a shrug.
SAMPLE_TEXT = (
    "Hey! This is what I sound like reading your screen. "
    "Whether it's a long article, a dense email, or documentation you'd rather "
    "listen to, I'll read it aloud right here on your own machine. "
    "Nothing ever leaves your computer."
)


# --------------------------------------------------------------- accuracy

def words(text: str) -> list[str]:
    """Case and punctuation are Whisper's inventions either way; the words are
    what it either heard or did not."""
    return [w for w in "".join(
        c.lower() if c.isalnum() or c.isspace() or c == "'" else " "
        for c in text
    ).split() if w]


def word_errors(said: str, heard: str) -> tuple[int, int]:
    """Levenshtein over words: (edits, words said)."""
    a, b = words(said), words(heard)
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1,
                                       prev + (x != y))
    return row[-1], len(a)


# ------------------------------------------------------------------ input

def read_wav(path: Path):
    import soundfile as sf

    data, rate = sf.read(str(path), dtype="float32", always_2d=False)
    return listener.mono16k(data, rate)


def frames_of(audio):
    end = len(audio) - len(audio) % FRAME
    for i in range(0, end, FRAME):
        yield audio[i:i + FRAME]


def show(line):
    when = listener._stamp(line.start)
    mark = "   [?]" if line.suspect else ""
    print(f"[{when}] {line.source}: {line.text}{mark}", flush=True)


def transcriber_for(args) -> WhisperTranscriber:
    model = MODELS[args.model]
    kept = MODEL_ROOT / f"models--{model.repo.replace('/', '--')}"
    if not kept.exists():
        print(f"Fetching {args.model} -- {model.size / 1e6:.0f} MB, once, "
              f"into {MODEL_ROOT.relative_to(ROOT)}", flush=True)
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    return WhisperTranscriber(args.model, compute_type=args.compute,
                              language=args.language, root=str(MODEL_ROOT),
                              threads=args.threads, beam=args.beam)


# ------------------------------------------------------------------ modes

def list_devices():
    import sounddevice as sd

    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"]:
            api = sd.query_hostapis(d["hostapi"])["name"]
            print(f"{i:3}  {api:22} {d['max_input_channels']}ch "
                  f"{d['default_samplerate']:>6.0f} Hz  {d['name']}")
    print("\nAnything that captures what you hear rather than what you say "
          "-- 'Stereo Mix', or a device with 'loopback' in the name -- is the "
          "one to give --mic for the far end of a call.")


def from_files(args, meeting):
    for path in args.file:
        audio = read_wav(Path(path))
        print(f"--- {path}  ({len(audio) / SAMPLE_RATE:.1f}s)", flush=True)
        started = time.perf_counter()
        for frame in frames_of(audio):
            meeting.feed(args.source, frame)
        meeting.close()
        took = time.perf_counter() - started
        print(f"    {took:.1f}s to transcribe {len(audio) / SAMPLE_RATE:.1f}s "
              f"of audio (RTF {took / (len(audio) / SAMPLE_RATE):.2f})")


def from_mic(args, meeting):
    """Capture on the audio thread, transcribe on this one.

    The callback must never block, and an inference takes seconds; doing both
    in one place is how you lose the second half of every sentence.
    """
    import sounddevice as sd

    frames: queue.Queue = queue.Queue()

    def on_audio(indata, _n, _time, status):
        if status:
            print(f"  ({status})", file=sys.stderr)
        frames.put(indata[:, 0].copy())

    device = int(args.mic) if str(args.mic).isdigit() else (args.mic or None)
    print(f"Listening on {sd.query_devices(device, 'input')['name']} "
          f"as '{args.source}'. Ctrl+C to stop.", flush=True)

    with sd.InputStream(device=device, channels=1, samplerate=SAMPLE_RATE,
                        blocksize=FRAME, dtype="float32", callback=on_audio):
        behind = 0
        try:
            while True:
                meeting.feed(args.source, frames.get())
                if frames.qsize() > behind + 200:   # six seconds of backlog
                    behind = frames.qsize()
                    print(f"  (running {behind * FRAME / SAMPLE_RATE:.0f}s behind "
                          f"-- try a smaller --model)", file=sys.stderr)
        except KeyboardInterrupt:
            print("\nfinishing the last utterance...", flush=True)
    meeting.close()


def check(args, meeting):
    """Ground truth: the sample WAVs say a sentence we have in writing."""
    paths = sorted(p for p in (ROOT / "samples").glob("*.wav"))
    if args.only:
        paths = [p for p in paths if args.only in p.name]
    if not paths:
        sys.exit("no samples/*.wav to check against -- run audition.py first")

    print(f"model {args.model} ({args.compute}), {len(paths)} samples, "
          f"against {len(words(SAMPLE_TEXT))} known words\n")
    total_edits = total_words = 0
    for path in paths:
        audio = read_wav(path)
        heard = []
        meeting.transcript.lines.clear()
        meeting.segmenters.clear()
        meeting.on_line = heard.append
        started = time.perf_counter()
        for frame in frames_of(audio):
            meeting.feed(YOU, frame)
        meeting.close()
        took = time.perf_counter() - started

        got = " ".join(l.text for l in meeting.transcript.lines)
        edits, said = word_errors(SAMPLE_TEXT, got)
        total_edits, total_words = total_edits + edits, total_words + said
        print(f"{path.name:44} {edits / said:6.1%} WER  "
              f"{len(meeting.transcript.lines)} lines  "
              f"RTF {took / (len(audio) / SAMPLE_RATE):.2f}")
        if args.verbose:
            print(f"    {got}\n")
    print(f"\n{'overall':44} {total_edits / total_words:6.1%} WER")


# ------------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--devices", action="store_true", help="list input devices")
    p.add_argument("--file", nargs="+", help="transcribe wav files")
    p.add_argument("--mic", nargs="?", const="", help="capture from a device")
    p.add_argument("--check", action="store_true",
                   help="score against samples/, whose words are known")
    p.add_argument("--only", help="with --check, only samples matching this")
    p.add_argument("--model", default="small", choices=list(MODELS))
    p.add_argument("--threads", type=int, default=None,
                   help="CPU threads (default: physical cores, capped at 16)")
    p.add_argument("--beam", type=int, default=5,
                   help="beam size; 1 is a little faster and a little worse")
    p.add_argument("--compute", default="int8",
                   help="int8 (default), int8_float32, float32")
    p.add_argument("--language", default=None,
                   help="nl, en, ... (default: detect per utterance)")
    p.add_argument("--source", default=YOU, choices=[YOU, THEM])
    p.add_argument("--save", help="write the transcript here when done")
    p.add_argument("--verbose", action="store_true", help="show what it heard")
    args = p.parse_args()

    if args.devices:
        return list_devices()
    if not (args.file or args.mic is not None or args.check):
        return p.print_help()

    meeting = Meeting(transcriber_for(args), on_line=show)
    if args.check:
        check(args, meeting)
    elif args.file:
        from_files(args, meeting)
    else:
        from_mic(args, meeting)

    if args.save:
        meeting.transcript.save(args.save)
        print(f"\n{len(meeting.transcript.lines)} lines -> {args.save}")


if __name__ == "__main__":
    main()
