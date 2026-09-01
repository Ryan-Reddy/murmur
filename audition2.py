"""Round two: remaining British voices plus blends aiming for suave/sweet/raspy."""

from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro

TEXT = (
    "Hey! This is what I sound like reading your screen. "
    "Whether it's a long article, a dense email, or documentation you'd rather listen to, "
    "I'll read it aloud right here on your own machine. Nothing ever leaves your computer."
)

ROOT = Path(__file__).parent
OUT = ROOT / "samples"
OUT.mkdir(exist_ok=True)

kokoro = Kokoro(str(ROOT / "models" / "kokoro-v1.0.onnx"), str(ROOT / "models" / "voices-v1.0.bin"))


def blend(a: str, wa: float, b: str, wb: float):
    return kokoro.get_voice_style(a) * wa + kokoro.get_voice_style(b) * wb


candidates = {
    # plain British voices not in round one
    "bf_isabella": "bf_isabella",
    "bf_alice": "bf_alice",
    "bf_lily": "bf_lily",
    "bm_fable": "bm_fable",
    # blends: British base + raspier/breathier American texture
    "blend_emma60_bella40": blend("bf_emma", 0.6, "af_bella", 0.4),
    "blend_isabella60_bella40": blend("bf_isabella", 0.6, "af_bella", 0.4),
    "blend_emma70_nicole30": blend("bf_emma", 0.7, "af_nicole", 0.3),
}

for name, voice in candidates.items():
    audio, sample_rate = kokoro.create(TEXT, voice=voice, speed=1.0)
    sf.write(OUT / f"{name}.wav", audio, sample_rate)
    print(f"{name}: {len(audio) / sample_rate:.1f}s")
