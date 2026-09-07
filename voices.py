"""Voice treatments: what the words sound like they are coming out of.

Kokoro gives a clean studio voice. These put it through something, so that a
notification from Claude and a paragraph you asked to be read are told apart by
ear alone, without either having to be louder.

    from voices import treat, TREATMENTS
    audio = treat("bbc", audio, 24000)

The four are real production chains rather than presets:

  bbc        the station. Even and measured; compression does all the work.
  veronica   an offshore AM pirate. Narrower, limited far harder because
             pirates competed on loudness, driven, with the slow fade of a
             skywave signal and a floor of noise under it.
  submarine  not broadcast at all -- tape. Saturation, wow and flutter from
             the transport, double tracking, and a plate behind it.
  agent      a concealed recorder. Narrow at both ends, honking where a tiny
             earpiece resonates, an AGC brutal enough to catch a whisper
             across a room, and hiss under everything.

Everything is FFT-based on purpose: the same chains written with np.convolve
cost 280 ms on an eight-second chunk against 50 ms this way, and that comes
straight off the time before the first word is heard.
"""

import numpy as np

# Every treatment starts from the same place: the levelled, thinned signal a
# broadcast chain would hand to a transmitter. The receiver differs after that.
STATION_LEVEL = 0.55

__all__ = ["TREATMENTS", "treat", "cost_estimate", "label", "catalogue",
           "voice_label", "singers", "NAMES", "NOTES"]


# --------------------------------------------------------------- primitives

def _fft_convolve(signal, kernel):
    """Convolution through the frequency domain.

    Direct convolution is O(n*m), and for a compressor envelope over an
    eight-second chunk that measured 218 ms. This is 19.
    """
    total = len(signal) + len(kernel) - 1
    size = 1 << (total - 1).bit_length()
    spectrum = np.fft.rfft(signal, size) * np.fft.rfft(kernel, size)
    return np.fft.irfft(spectrum, size)[: len(signal)].astype("float32")


def _shape(audio, rate, curve):
    """Apply a gain curve over frequency."""
    spectrum = np.fft.rfft(audio)
    freqs = np.fft.rfftfreq(len(audio), 1.0 / rate)
    return np.fft.irfft(spectrum * curve(freqs), len(audio)).astype("float32")


def _shelf(freqs, corner, gain_db, kind):
    gain = 10 ** (gain_db / 20.0)
    ratio = freqs / max(corner, 1e-6)
    blend = ratio**2 / (1 + ratio**2)
    if kind == "low":
        blend = 1 - blend
    return 1 + (gain - 1) * blend


def _bell(freqs, centre, gain_db, width):
    """A lift or dip centred on `centre`, `width` octaves wide."""
    gain = 10 ** (gain_db / 20.0)
    octaves = np.log2(np.maximum(freqs, 1e-6) / centre)
    return 1 + (gain - 1) * np.exp(-0.5 * (octaves / width) ** 2)


def _band(freqs, low, high, edge):
    """A pass band with raised-cosine edges. A rectangular one rings."""
    gain = np.ones_like(freqs)
    gain[freqs < low - edge] = 0.0
    gain[freqs > high + edge] = 0.0
    rising = (freqs >= low - edge) & (freqs < low)
    gain[rising] = 0.5 - 0.5 * np.cos(np.pi * (freqs[rising] - (low - edge)) / edge)
    falling = (freqs > high) & (freqs <= high + edge)
    gain[falling] = 0.5 + 0.5 * np.cos(np.pi * (freqs[falling] - high) / edge)
    return gain


def _envelope(audio, rate, ms):
    """Smoothed level, the shape a one-pole follower gives."""
    taps = max(int(rate * ms / 1000.0), 1)
    kernel = np.exp(-np.arange(taps * 4) / taps).astype("float32")
    kernel /= kernel.sum()
    return _fft_convolve(np.abs(audio).astype("float32"), kernel)


def _compress(audio, rate, threshold, ratio, ms, makeup):
    """`ms` is the release. Long, and the gain stays up between words, which
    is what makes speech mesh rather than arrive one word at a time."""
    level = np.maximum(_envelope(audio, rate, ms), 1e-9)
    over = level / threshold
    gain = np.where(over > 1.0, over ** (1.0 / ratio - 1.0), 1.0)
    return (audio * gain * makeup).astype("float32")


def _limit(audio, ceiling=0.94):
    peak = float(np.max(np.abs(audio))) or 1.0
    return audio if peak <= ceiling else (audio * (ceiling / peak)).astype("float32")


def _room(audio, rate, decay_ms, mix):
    """A short dark tail. Not meant to be heard as reverb -- it exists so the
    end of one word overlaps the start of the next."""
    taps = max(int(rate * decay_ms / 1000.0), 1)
    rng = np.random.default_rng(11)
    tail = rng.normal(0, 1, taps) * np.exp(-np.arange(taps) / (taps / 3.5))
    tail = np.convolve(tail, np.ones(24) / 24, mode="same").astype("float32")
    tail /= np.max(np.abs(tail)) or 1.0
    wet = _fft_convolve(audio, tail)
    wet /= np.max(np.abs(wet)) or 1.0
    return ((1 - mix) * audio + mix * wet * float(np.max(np.abs(audio)))).astype("float32")


def _warble(audio, rate, flutter, flutter_hz, wow, wow_hz):
    """Wow and flutter. Depths are in samples of displacement, small enough to
    be heard as movement rather than as a fault."""
    count = len(audio)
    t = np.arange(count) / rate
    shift = flutter * np.sin(2 * np.pi * flutter_hz * t) + wow * np.sin(2 * np.pi * wow_hz * t)
    return np.interp(np.clip(np.arange(count) + shift, 0, count - 1),
                     np.arange(count), audio).astype("float32")


def _saturate(audio, drive, bias=0.0):
    peak = float(np.max(np.abs(audio))) or 1.0
    driven = np.tanh(audio / peak * drive + bias) - np.tanh(bias)
    return (driven / (np.max(np.abs(driven)) or 1.0)).astype("float32")


def _hiss(audio, amount, seed):
    if not amount:
        return audio
    noise = np.random.default_rng(seed).normal(0, amount, len(audio))
    return (audio + noise).astype("float32")


# ------------------------------------------------------------------ station

def _station(audio, rate):
    """Levelled and thinned, the way a broadcast chain hands it to a
    transmitter. Presence is kept -- an announcer is clear, never urgent --
    and the sibilance is left alone, because on a real chain the s and z carry.
    """
    out = _shape(audio, rate, lambda f: (
        _shelf(f, 130, -13.0, "low")
        * _bell(f, 250, -1.5, 1.0)
        * _bell(f, 2300, +2.5, 0.9)
        * _bell(f, 5200, -0.5, 0.7)
        * _shelf(f, 8500, -5.0, "high")
    ))
    out = _compress(out, rate, threshold=0.09, ratio=7.5, ms=240, makeup=3.0)
    out = _room(out, rate, decay_ms=110, mix=0.13)
    out = np.tanh(out * 1.1) / np.tanh(1.1)
    return _limit((out * STATION_LEVEL).astype("float32"))


# ---------------------------------------------------------------- receivers

def _bbc(audio, rate, level=0.46):
    out = _shape(_station(audio, rate), rate,
                 lambda f: _band(f, 260, 4600, 240) * _bell(f, 1600, 2.4, 0.6))
    out = _compress(out, rate, threshold=0.12, ratio=4.0, ms=320, makeup=2.2)
    out = _saturate(out, 1.15)
    return _limit((_hiss(out, 0.0003, 3) * level).astype("float32"))


def _veronica(audio, rate, level=0.46):
    out = _shape(_station(audio, rate), rate,
                 lambda f: _band(f, 320, 3500, 180)
                 * _bell(f, 1900, 4.5, 0.5) * _bell(f, 800, -2.5, 0.7))
    # Pirates ran the limiter hard: loud, and flat as a board.
    out = _compress(out, rate, threshold=0.06, ratio=9.0, ms=200, makeup=3.2)
    out = _saturate(out, 2.4)
    # Skywave fading: the signal breathes in and out over several seconds.
    t = np.arange(len(out)) / rate
    out = (out * (1 - 0.16 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.13 * t)))).astype("float32")
    return _limit((_hiss(out, 0.0026, 5) * level).astype("float32"))


def _submarine(audio, rate, level=0.48):
    out = _shape(_station(audio, rate), rate,
                 lambda f: _shelf(f, 90, -10.0, "low")
                 * _bell(f, 900, +2.0, 1.1)
                 * _shelf(f, 7200, -8.0, "high"))
    out = _warble(out, rate, flutter=4.0, flutter_hz=5.5, wow=26.0, wow_hz=0.6)
    # Artificial double tracking: a second pass a hair behind, wandering
    # against the first. Abbey Road built a machine to do this.
    delay = int(rate * 26.0 / 1000.0)
    shifted = np.concatenate([np.zeros(delay, dtype="float32"), out])[: len(out)]
    shifted = _warble(shifted, rate, flutter=7.0, flutter_hz=3.1, wow=14.0, wow_hz=0.9)
    out = (0.66 * out + 0.34 * shifted).astype("float32")
    # Asymmetric, because tape distorts unevenly -- which is why it sounds warm
    # rather than merely clipped.
    out = _saturate(out, 1.7, bias=0.08)
    out = _room(out, rate, decay_ms=260, mix=0.20)
    return _limit((_hiss(out, 0.0016, 9) * level).astype("float32"))


def _agent(audio, rate, level=0.40):
    out = _shape(_station(audio, rate), rate,
                 lambda f: _band(f, 560, 3000, 150)
                 * _bell(f, 2600, +5.5, 0.45)   # the little driver, honking
                 * _bell(f, 1050, -3.0, 0.6))   # and hollow below it
    out = _warble(out, rate, flutter=7.0, flutter_hz=7.2, wow=34.0, wow_hz=0.9)
    # An AGC that has to catch a whisper across a room.
    out = _compress(out, rate, threshold=0.045, ratio=11.0, ms=260, makeup=3.6)
    out = _saturate(out, 2.2)
    out = _room(out, rate, decay_ms=70, mix=0.10)   # a pocket, not a hall
    return _limit((_hiss(out, 0.0042, 13) * level).astype("float32"))


TREATMENTS = {
    "clean": None,
    "bbc": _bbc,
    "veronica": _veronica,
    "submarine": _submarine,
    "agent": _agent,
}

# Who these chains are -- names, notes and ordering -- lives in characters.py,
# which imports nothing. The tray menu needs those names before the model has
# begun loading, and importing this module for them would drag numpy onto the
# startup path. Re-exported so `voices.label(...)` keeps working.
from characters import (  # noqa: E402,F401
    ACCENTS,
    NAMES,
    NOTES,
    RANGES,
    catalogue,
    label,
    singers,
    voice_label,
)


# Below this many samples there is nothing a chain can usefully do, and the
# FFTs start failing outright -- an empty chunk raised "Invalid number of FFT
# data points", which would have taken a whole read down with it.
MIN_SAMPLES = 256


def treat(name, audio, rate):
    """Run `audio` through a named treatment.

    Anything unusable -- an unknown name, "clean", a chunk too short to filter
    -- hands the audio back untouched. A treatment is a decoration; it must
    never be the reason nothing is spoken.
    """
    handler = TREATMENTS.get(name)
    if handler is None or len(audio) < MIN_SAMPLES:
        return audio
    try:
        return handler(audio, rate)
    except Exception:
        return audio


# Measured on a 16-core desktop: 182 ms for bbc through 271 ms for agent on an
# 8.5 second chunk, so about three hundredths of a second per second of audio.
# Not one hot spot -- padding the FFTs to a friendly size saved 1 ms of 12 --
# just the sum of a lot of array work.
SECONDS_PER_SECOND = 0.032


def cost_estimate(name, seconds):
    """Roughly what a treatment will cost, for anything that wants to warn.

    It comes off the time before the first word is heard, so the opening chunk
    is the one that matters: a short lead-in pays about a tenth of this.
    """
    if TREATMENTS.get(name) is None:
        return 0.0
    return SECONDS_PER_SECOND * seconds
