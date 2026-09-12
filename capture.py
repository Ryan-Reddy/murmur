"""Two ways into a meeting: what you say, and what you hear.

The microphone is the easy half -- PortAudio, one stream, the same call cufflink
already makes to play audio, backwards.

The far end is the hard half, and on Windows it is WASAPI loopback: a capture
of the mix that is being sent to an output device. What is *not* available:

  - `sounddevice` 0.5.6 / PortAudio 19.7 as shipped exposes no loopback flag.
    Its `WasapiSettings` has exclusive, auto_convert and explicit_sample_format
    and nothing else.
  - `Stereo Mix` enumerates under WDM-KS on this machine, opens without error,
    and then delivers **no frames at all** -- it is disabled at the driver, and
    nothing in the API says so.
  - `soundcard` finds the right endpoint and captures the right content, but
    delivers it in bursts separated by seconds of digital silence. Measured
    against a known sentence: 90% word error against 0% for the same audio
    from a file. It also needs CoInitializeEx on every thread that touches it.

`PyAudioWPatch` is a PyAudio fork whose whole purpose is this, and it delivers
a continuous stream: 0% word error on the same test. Hence the dependency.

The trap it does not solve: **several loopback devices can carry the same
name, and only one of them works.** This machine has two "27M1N3500LS (NVIDIA
High Definition Audio) [Loopback]" entries; one delivers audio and the other
delivers nothing, forever, without an error. Picking by name is a coin flip.

The obvious test -- open each and keep the one that hands over frames -- turns
out not to work either, because a loopback endpoint delivers no callbacks at
all while nothing is playing. Measured on an idle machine, both endpoints look
dead, including the one that had worked minutes earlier. So `pick_loopback`
asks the library first, falls back to the default output's name, and uses the
delivery probe only as a tiebreak, where it is decisive because something
happens to be playing.
"""

import queue
import threading
import time

import numpy as np

from listener import FRAME, SAMPLE_RATE, THEM, YOU, mono16k, rms_db

# How long to hold a candidate device open before deciding it is the dead one.
# Measured: an MME input takes about 200 ms to produce its first callback, and
# a quarter-second probe reported a perfectly good microphone as delivering
# nothing. A second is slow enough to be sure and quick enough not to be felt.
PROBE_SECONDS = 1.0

# Nothing has been heard on this source for this long -> show it as silent.
QUIET_AFTER = 1.5


class Framer:
    """Whatever size the device hands over, in 30 ms frames out.

    A 48 kHz block of 1024 samples resamples to 341, which divides into no
    whole number of frames. Without this the leftovers are either dropped --
    a click every block, and a gap in the middle of a word -- or padded, which
    walks the timeline out of true over an hour-long meeting.
    """

    def __init__(self, size: int = FRAME):
        self.size = size
        self._held = np.zeros(0, dtype=np.float32)

    def feed(self, samples) -> list:
        self._held = np.concatenate([self._held, np.asarray(samples, np.float32)])
        whole = len(self._held) // self.size
        out = [self._held[i * self.size:(i + 1) * self.size] for i in range(whole)]
        self._held = self._held[whole * self.size:]
        return out

    def held(self) -> int:
        return len(self._held)


class Microphone:
    """What you say. PortAudio, via sounddevice, which is already here."""

    source = YOU

    def __init__(self, sink: queue.Queue, device=None, source: str = YOU):
        self.sink, self.device, self.source = sink, device, source
        self.framer = Framer()
        self._stream = None
        self.rate = SAMPLE_RATE
        # The level is the only honest answer to "why is nobody being
        # attributed to me". A transcript that says `them` for an hour looks
        # exactly the same whether the far end did all the talking or your
        # microphone was never in the room.
        self.level = -120.0
        self.heard_at = 0.0

    def start(self):
        import sounddevice as sd

        info = sd.query_devices(self.device, "input")
        # Ask for 16 kHz and let the host API convert; fall back to the
        # device's own rate if it refuses, and resample on the way through.
        for rate in (SAMPLE_RATE, int(info["default_samplerate"])):
            try:
                self._stream = sd.InputStream(
                    device=self.device, channels=1, samplerate=rate,
                    blocksize=FRAME, dtype="float32", callback=self._on_audio)
                self._stream.start()
                self.rate = rate
                return info["name"]
            except Exception:
                self._stream = None
        raise RuntimeError(f"could not open {info['name']}")

    def _on_audio(self, indata, _frames, _time, _status):
        block = indata[:, 0]
        if self.rate != SAMPLE_RATE:
            block = mono16k(block, self.rate)
        self.level = rms_db(block)
        self.heard_at = time.monotonic()
        for frame in self.framer.feed(block):
            self.sink.put((self.source, frame))

    def stop(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class Loopback:
    """What you hear: everyone else on the call, off the output device."""

    def __init__(self, sink: queue.Queue, device=None, source: str = THEM):
        self.sink, self.source = sink, source
        self.device = device
        self.framer = Framer()
        self._pa = None
        self._stream = None
        self.rate = SAMPLE_RATE
        self.channels = 2
        self.level = -120.0
        self.heard_at = 0.0

    def start(self):
        import pyaudiowpatch as pa

        self._pa = pa.PyAudio()
        info = (self._pa.get_device_info_by_index(self.device)
                if self.device is not None else pick_loopback(self._pa))
        if info is None:
            self._pa.terminate()
            self._pa = None
            raise RuntimeError(
                "no working loopback device -- the far end cannot be heard")
        self.rate = int(info["defaultSampleRate"])
        self.channels = int(info["maxInputChannels"])
        self._stream = self._pa.open(
            format=pa.paFloat32, channels=self.channels, rate=self.rate,
            input=True, input_device_index=info["index"],
            frames_per_buffer=1024, stream_callback=self._on_audio)
        self._stream.start_stream()
        return info["name"]

    def _on_audio(self, raw, _frames, _time, _status):
        import pyaudiowpatch as pa

        block = np.frombuffer(raw, np.float32).reshape(-1, self.channels).mean(axis=1)
        self.level = rms_db(block)
        self.heard_at = time.monotonic()
        for frame in self.framer.feed(mono16k(block, self.rate)):
            self.sink.put((self.source, frame))
        # PyAudio insists on a (data, flag) tuple; returning the bare flag
        # raises SystemError from inside the callback, where it is invisible.
        return (None, pa.paContinue)

    def stop(self):
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None


def loopback_candidates(audio) -> list:
    return list(audio.get_loopback_device_info_generator())


def delivers(audio, info, seconds: float = PROBE_SECONDS) -> bool:
    """Does this device actually hand over frames?

    The only reliable difference between the live loopback endpoint and its
    dead twin. Amplitude is no use as a test -- at the start of a meeting
    nobody is talking yet, so the working device is legitimately silent.
    """
    import pyaudiowpatch as pa

    seen = threading.Event()

    def heard(_raw, _frames, _time, _status):
        seen.set()
        return (None, pa.paContinue)

    try:
        stream = audio.open(
            format=pa.paFloat32, channels=int(info["maxInputChannels"]),
            rate=int(info["defaultSampleRate"]), input=True,
            input_device_index=info["index"], frames_per_buffer=1024,
            stream_callback=heard)
    except Exception:
        return False
    try:
        stream.start_stream()
        return seen.wait(seconds)
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass


def pick_loopback(audio=None):
    """The loopback device most likely to be carrying the meeting.

    Three rules, in order, because no single one of them is trustworthy:

    1. Whatever `PyAudioWPatch` itself says the default output's loopback is.
       It matches endpoints properly rather than by name.
    2. Failing that, a name match against the WASAPI default output.
    3. Only then, whether the device actually delivers frames.

    Rule 3 was originally rule 1, and it was wrong. **A loopback endpoint
    delivers no callbacks at all while nothing is playing** -- measured, on an
    idle machine, on both endpoints including the one that had just worked.
    So a signal probe cannot tell the live device from the dead twin at the
    start of a meeting, which is exactly when it is asked. It is kept as a
    tiebreak because when something *is* playing it is the only rule that
    knows for certain.

    When nothing distinguishes them, the first candidate is returned rather
    than None: a guess that the level meters will immediately expose as wrong
    beats refusing to listen at all.
    """
    import pyaudiowpatch as pa

    mine = audio or pa.PyAudio()
    try:
        try:
            best = mine.get_default_wasapi_loopback()
            if best:
                return best
        except Exception:
            pass

        candidates = loopback_candidates(mine)
        if not candidates:
            return None
        try:
            wasapi = mine.get_host_api_info_by_type(pa.paWASAPI)
            default = mine.get_device_info_by_index(
                wasapi["defaultOutputDevice"])["name"]
            candidates.sort(key=lambda d: default not in d["name"])
        except Exception:
            pass

        for info in candidates:
            if delivers(mine, info):
                return info
        return candidates[0]
    finally:
        if audio is None:
            mine.terminate()
