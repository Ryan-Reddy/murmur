"""Measure what cufflink costs and how quickly it starts, into BENCHMARKS.md.

    venv\\Scripts\\python.exe bench.py

Takes a few minutes. The end-to-end section speaks aloud, repeatedly, because
measuring the gaps between chunks means actually playing them -- pass --silent
to skip it if somebody is trying to work. Results are
machine-specific; the file records which machine they came from.
"""

import ctypes
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

MODEL = ROOT / "models" / "kokoro-v1.0.onnx"
VOICES = ROOT / "models" / "voices-v1.0.bin"

SHORT = "The quick brown fox jumps over the lazy dog,"
LONG = (
    "The quick brown fox jumps over the lazy dog, and then it does so again "
    "because repetition makes for a fair benchmark of speech synthesis."
)
PARAGRAPH = LONG + " A second sentence follows so we can see how the pipeline behaves."

THREAD_SWEEP = (2, 4, 6, 8, 12, 16, 24, 32)


def machine() -> list[str]:
    name, cores = platform.processor(), None
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$c = Get-CimInstance Win32_Processor | Select-Object -First 1;"
             "\"$($c.Name)|$($c.NumberOfCores)|$($c.NumberOfLogicalProcessors)\""],
            capture_output=True, text=True, timeout=30).stdout.strip()
        name, cores, logical = out.split("|")
    except Exception:
        logical = os.cpu_count()
    return [
        f"- CPU: {name.strip()}",
        f"- Cores: {cores} physical / {logical} logical",
        f"- Python {platform.python_version()} on {platform.system()} {platform.release()}",
        f"- Measured {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
    ]


def synth_cost(threads: int) -> dict:
    """Run a child interpreter per thread count -- the ONNX session's pool is
    fixed at construction, so they cannot share a process."""
    script = f'''
import os, sys, time
sys.path.insert(0, r"{ROOT}")
os.environ["CUFFLINK_THREADS"] = "{threads}"
from speaker import Speaker
s = Speaker(r"{MODEL}", r"{VOICES}")
s.kokoro.create("warm", voice=s.voice, speed=1.0)
def best(text):
    b = None
    for _ in range(3):
        c0, w0 = time.process_time(), time.perf_counter()
        a, sr = s.kokoro.create(text, voice=s.voice, speed=1.0)
        w, c = time.perf_counter() - w0, time.process_time() - c0
        if b is None or w < b[0]: b = (w, c, len(a) / sr)
    return b
sw, sc, sa = best({SHORT!r})
lw, lc, la = best({LONG!r})
print(f"{{sw}} {{sa}} {{lw}} {{lc}} {{la}}")
'''
    out = subprocess.run([sys.executable, "-c", script],
                         capture_output=True, text=True).stdout.strip().splitlines()
    sw, sa, lw, lc, la = (float(x) for x in out[-1].split())
    return {"threads": threads, "short_rtf": sw / sa, "long_rtf": lw / la,
            "cpu_per_audio_second": lc / la}


def end_to_end() -> dict:
    from speaker import Speaker, _default_threads

    best = None
    for _ in range(3):
        marks = []
        speaker = Speaker(MODEL, VOICES,
                          on_text=lambda t, m=marks: m.append(("text", time.perf_counter())),
                          on_word=lambda i, m=marks: m.append(("word", time.perf_counter())))
        speaker.kokoro.create("warm", voice=speaker.voice, speed=1.0)
        start = time.perf_counter()
        speaker.speak(PARAGRAPH)
        while not any(k == "word" for k, _ in marks):
            time.sleep(0.005)
        text_at = next(t for k, t in marks if k == "text") - start
        word_at = next(t for k, t in marks if k == "word") - start
        speaker.wait()
        whole = time.perf_counter() - start
        if best is None or whole < best["finished_s"]:
            best = {"threads": _default_threads(), "text_ms": text_at * 1000,
                    "first_word_s": word_at, "finished_s": whole}
        del speaker
    audio = 0.0
    from speaker import Speaker as S, split_sentences
    s = S(MODEL, VOICES)
    for chunk in split_sentences(PARAGRAPH):
        a, sr = s.kokoro.create(chunk, voice=s.voice, speed=1.0)
        audio += len(a) / sr
    best["audio_s"] = audio
    best["gaps_s"] = best["finished_s"] - best["first_word_s"] - audio
    return best


def idle_cost() -> float:
    """Whatever a running-but-silent cufflink costs, as a share of one core."""
    kernel32 = ctypes.WinDLL("kernel32")

    class FT(ctypes.Structure):
        _fields_ = [("lo", ctypes.c_ulong), ("hi", ctypes.c_ulong)]

    def cpu_seconds():
        c, e, k, u = FT(), FT(), FT(), FT()
        kernel32.GetProcessTimes(kernel32.GetCurrentProcess(),
                                 *[ctypes.byref(x) for x in (c, e, k, u)])
        return sum(((f.hi << 32) | f.lo) / 1e7 for f in (k, u))

    from speaker import Speaker

    speaker = Speaker(MODEL, VOICES)
    speaker.kokoro.create("warm", voice=speaker.voice, speed=1.0)
    before, start = cpu_seconds(), time.perf_counter()
    time.sleep(20)
    return (cpu_seconds() - before) / (time.perf_counter() - start) * 100


def main():
    if not (MODEL.exists() and VOICES.exists()):
        sys.exit("models/ is missing -- run Install.cmd first.")

    print("Sweeping thread counts (a few minutes, silent)...")
    sweep = []
    for threads in THREAD_SWEEP:
        row = synth_cost(threads)
        sweep.append(row)
        print(f"  {threads:>2} threads  short RTF {row['short_rtf']:.2f}  "
              f"long RTF {row['long_rtf']:.2f}  {row['cpu_per_audio_second']:.2f} CPU-s/audio-s")

    if "--silent" in sys.argv:
        print("\nSkipping the end-to-end section (--silent).")
        e2e = None
    else:
        print("\nEnd to end -- THIS SPEAKS ALOUD, repeatedly. Ctrl+C now to skip.")
        time.sleep(4)
        e2e = end_to_end()
    if e2e:
        print(f"  first word {e2e['first_word_s']:.2f}s, gaps {e2e['gaps_s']:+.2f}s")

    print("\nIdle cost (20s)...")
    idle = idle_cost()
    print(f"  {idle:.2f}% of one core")

    lines = [
        "# Benchmarks",
        "",
        "Generated by `bench.py`. Numbers are machine-specific.",
        "",
        *machine(),
        "",
        "## Synthesis cost by thread count",
        "",
        "`short` is a lead-in chunk (44 chars), `long` a full sentence (137).",
        "RTF is wall time over audio produced, so below 1.0 is faster than real time.",
        "",
        "| threads | short RTF | long RTF | CPU-s per audio-s |",
        "|---:|---:|---:|---:|",
    ]
    for row in sweep:
        lines.append(f"| {row['threads']} | {row['short_rtf']:.2f} | "
                     f"{row['long_rtf']:.2f} | {row['cpu_per_audio_second']:.2f} |")
    best = min(sweep, key=lambda r: r["long_rtf"])
    cheap = min(sweep, key=lambda r: r["cpu_per_audio_second"])
    lines += [
        "",
        f"Fastest: {best['threads']} threads. Cheapest per second of audio: "
        f"{cheap['threads']}. The default picks `max(2, min(16, logical // 2))`, "
        "which lands on the physical core count.",
        "",
        *([] if not e2e else [
            "## End to end",
            "",
            f"Reading a {len(PARAGRAPH)}-character paragraph at "
            f"{e2e['threads']} threads, best of three:",
            "",
            "| | |",
            "|---|---:|",
            f"| Text on screen | {e2e['text_ms']:.1f} ms |",
            f"| First spoken word | {e2e['first_word_s']:.2f} s |",
            f"| Audio produced | {e2e['audio_s']:.2f} s |",
            f"| Finished after | {e2e['finished_s']:.2f} s |",
            f"| Gaps between chunks | {e2e['gaps_s']:+.2f} s |",
            "",
        ]),
        "## Idle",
        "",
        f"Loaded and silent: **{idle:.2f}% of one core** over 20 seconds. "
        "Spin-wait is disabled, so the pool sleeps between utterances.",
        "",
    ]
    out = ROOT / "BENCHMARKS.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
