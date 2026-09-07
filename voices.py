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


# ------------------------------------------------------------------- recipes
#
# The four chains above are each a fixed set of amounts. Written out as
# amounts instead, they stop being four things and become four points in one
# space -- and the space between them is reachable. A lot of transistor, a
# twist of spy, less tape.
#
# Every ingredient runs 0 to 1. The stages are in the same order the fixed
# chains use them, so a recipe at a character's point comes out sounding like
# that character; RECIPES holds the points, fitted rather than guessed (see
# the note above them).

INGREDIENTS = {
    "narrow": "Narrow — how much of the band survives. Radio, then telephone.",
    "transistor": "Transistor — driven until it warms and then until it grits.",
    "squash": "Squash — the limiter. High is flat, loud, and runs on.",
    "tape": "Tape — wow, flutter and a second pass wandering behind the first.",
    "spy": "Spy — the honk of a tiny earpiece, and an AGC chasing a whisper.",
    "fade": "Fade — a signal breathing in and out, the way skywave does.",
    "room": "Room — a short tail, so the end of a word laps the next.",
    "hiss": "Hiss — the floor under everything.",
}

# The band, in Hz, as `narrow` runs 0 -> 1. Log-interpolated: pitch is
# logarithmic, so a linear sweep spends most of its travel doing nothing.
BAND_LOW = (150.0, 560.0)
BAND_HIGH = (9000.0, 3000.0)


def _amount(recipe, name):
    return min(1.0, max(0.0, float(recipe.get(name, 0.0) or 0.0)))


def cook(audio, rate, recipe):
    """One chain, run at whatever amounts the recipe asks for.

    Reproduces the four fixed chains when handed their own recipe, and
    anything between them when handed something else.
    """
    narrow = _amount(recipe, "narrow")
    transistor = _amount(recipe, "transistor")
    squash = _amount(recipe, "squash")
    tape = _amount(recipe, "tape")
    spy = _amount(recipe, "spy")
    fade = _amount(recipe, "fade")
    room = _amount(recipe, "room")
    hiss = _amount(recipe, "hiss")

    out = _station(audio, rate)

    # -- what survives, and what is lifted on the way through ---------------
    low = BAND_LOW[0] * (BAND_LOW[1] / BAND_LOW[0]) ** (narrow ** 1.6)
    high = BAND_HIGH[0] * (BAND_HIGH[1] / BAND_HIGH[0]) ** narrow
    edge = 120.0 + 130.0 * narrow

    def curve(freqs):
        gain = _band(freqs, low, high, edge)
        # Anything band-limited peaks in the middle; that is most of why a
        # radio sounds like a radio rather than like a quiet studio. Stood
        # down as `spy` comes up, because a pocket recorder brings its own
        # peak at 2600 and two of them stacked is a howl.
        gain = gain * _bell(freqs, 1900, 5.2 * narrow * (1.0 - spy), 0.55)
        gain = gain * _bell(freqs, 2600, 5.5 * spy, 0.45)   # the little driver
        gain = gain * _bell(freqs, 1050, -3.0 * spy, 0.60)  # hollow below it
        gain = gain * _shelf(freqs, 90, -10.0 * tape, "low")
        gain = gain * _bell(freqs, 900, 2.0 * tape, 1.10)
        gain = gain * _shelf(freqs, 7200, -8.0 * tape, "high")
        return gain

    out = _shape(out, rate, curve)

    # -- what moves ---------------------------------------------------------
    if tape > 0.02 or spy > 0.02:
        out = _warble(out, rate,
                      flutter=4.0 * tape + 7.0 * spy, flutter_hz=5.5 + 1.7 * spy,
                      wow=26.0 * tape + 34.0 * spy, wow_hz=0.6 + 0.3 * spy)
    if tape > 0.02:
        # Artificial double tracking: a second pass a hair behind, wandering
        # against the first. Abbey Road built a machine to do this.
        delay = int(rate * 26.0 / 1000.0)
        shifted = np.concatenate([np.zeros(delay, dtype="float32"), out])[: len(out)]
        shifted = _warble(shifted, rate, flutter=7.0 * tape, flutter_hz=3.1,
                          wow=14.0 * tape, wow_hz=0.9)
        out = ((1 - 0.34 * tape) * out + 0.34 * tape * shifted).astype("float32")

    # -- how hard it is held down -------------------------------------------
    if squash > 0.02:
        out = _compress(out, rate,
                        threshold=0.16 - 0.115 * squash,
                        ratio=3.0 + 8.0 * squash,
                        ms=340.0 - 140.0 * squash,
                        makeup=1.8 + 1.8 * squash)

    # Tape distorts unevenly, which is why it sounds warm rather than clipped.
    out = _saturate(out, 1.0 + 1.5 * transistor, bias=0.08 * tape)

    if fade > 0.02:
        t = np.arange(len(out)) / rate
        breathing = 0.5 + 0.5 * np.sin(2 * np.pi * 0.13 * t)
        out = (out * (1 - 0.16 * fade * breathing)).astype("float32")

    if room > 0.02:
        out = _room(out, rate, decay_ms=70.0 + 190.0 * room, mix=0.22 * room)

    out = _hiss(out, 0.005 * hiss, 7)
    # A narrower, more squashed signal is a smaller one; the fixed chains
    # ended between 0.40 and 0.48 for the same reason.
    level = 0.48 - 0.06 * spy - 0.02 * narrow
    return _limit((out * level).astype("float32"))

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


# Where each character sits in the ingredient space. Fitted, not guessed:
# a coordinate search against the hand-built chain's own output, scored on the
# long-term spectrum in dB across fourteen bands plus the loudness envelope
# over time, which is what catches compression and fade.
#
# Each was fitted over only the ingredients it actually contains. Left free,
# the search put tape and fade on the BBC chain because they moved the
# spectrum the right way -- and wow and flutter on a newsreader is exactly the
# thing this metric cannot see and the ear cannot miss.
#
# How close each lands, RMS over those bands:
#
#     Abbey          0.95 dB
#     Auntie         1.50 dB
#     Veronica       1.89 dB
#     The Informant  3.72 dB   <- the loose one
#
# Which is why the named characters below still run their own chain, exactly
# as built. A recipe is where the sliders *start* from, not what you get for
# choosing a name -- so nothing you already liked changes underneath you.
RECIPES = {
    "clean": {},
    "bbc": {"narrow": 0.58, "transistor": 0.07, "squash": 0.25, "hiss": 0.06},
    "veronica": {"narrow": 0.76, "transistor": 1.0, "squash": 0.85,
                 "fade": 0.99, "hiss": 0.55},
    "submarine": {"transistor": 0.62, "tape": 0.88, "room": 1.0, "hiss": 0.34},
    "agent": {"narrow": 1.0, "transistor": 0.98, "squash": 0.9, "spy": 0.83,
              "hiss": 1.0},
}


def recipe_for(name) -> dict:
    """A character's position, as somewhere to start moving from."""
    return {key: 0.0 for key in INGREDIENTS} | dict(RECIPES.get(name, {}))


def treat(name, audio, rate, recipe=None):
    """Run `audio` through a treatment.

    `recipe` wins if given: that is the mixer, and the point of it is that it
    is not one of the five. Otherwise `name` picks one of the fixed chains.

    Anything unusable -- an unknown name, "clean", a chunk too short to filter
    -- hands the audio back untouched. A treatment is a decoration; it must
    never be the reason nothing is spoken.
    """
    if len(audio) < MIN_SAMPLES:
        return audio
    try:
        if recipe and any(float(v or 0.0) > 0.02 for v in recipe.values()):
            return cook(audio, rate, recipe)
        handler = TREATMENTS.get(name)
        return audio if handler is None else handler(audio, rate)
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
