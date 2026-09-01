"""Generate voice audition samples so you can pick the most pleasing Kokoro voice."""

from pathlib import Path
import time

import soundfile as sf
from kokoro_onnx import Kokoro

TEXT = (
    "Hey! This is what I sound like reading your screen. "
    "Whether it's a long article, a dense email, or documentation you'd rather listen to, "
    "I'll read it aloud right here on your own machine. Nothing ever leaves your computer."
)

VOICES = [
    "af_heart",    # American female - the community favourite
    "af_bella",    # American female - warm, slightly deeper
    "af_sarah",    # American female - clear, neutral
    "bf_emma",     # British female
    "am_michael",  # American male
    "bm_george",   # British male
]

ROOT = Path(__file__).parent
OUT = ROOT / "samples"
OUT.mkdir(exist_ok=True)

kokoro = Kokoro(str(ROOT / "models" / "kokoro-v1.0.onnx"), str(ROOT / "models" / "voices-v1.0.bin"))

for voice in VOICES:
    start = time.perf_counter()
    audio, sample_rate = kokoro.create(TEXT, voice=voice, speed=1.0)
    elapsed = time.perf_counter() - start
    path = OUT / f"{voice}.wav"
    sf.write(path, audio, sample_rate)
    print(f"{voice}: {len(audio) / sample_rate:.1f}s of audio in {elapsed:.2f}s")
