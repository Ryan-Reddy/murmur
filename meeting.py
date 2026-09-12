"""Follow a meeting: both sides of it, live, in a window you can mark up.

    venv\\Scripts\\python.exe meeting.py
    venv\\Scripts\\python.exe meeting.py --language nl
    venv\\Scripts\\python.exe meeting.py --model tiny --refine-with medium
    venv\\Scripts\\python.exe meeting.py --no-loopback        # just you

What you say comes off the microphone and what everyone else says comes off
the output device, so who said what is decided by which wire it arrived on
rather than by a diarisation model that can be wrong. See DECISIONS.md.

Every line is heard twice. `base` is fast enough to put words up while
somebody is still talking; `small` follows a few seconds behind and rewrites
them properly, keeping whatever you marked or noted in the meantime. Real time
was never the goal -- being readable now and right shortly is.

While it runs:

    Ctrl+Alt+L            start or stop recording -- it starts stopped
    click a bubble        mark it to come back to
    Ctrl+Alt+K            mark the line that just went past
    Ctrl+Alt+N            jump to the note box
    type, Enter           hang a note off whatever is being said

Any of those three can be moved: --key-record, --key-mark, --key-note, or ""
to do without. They avoid cufflink's own keys and anything with alt+r in it,
which belongs to the NVIDIA overlay.

and everything above is also a line on port 52720, so the assistant can read
the transcript, mark a line or leave a note without touching your keyboard:

    ::record   ::pause   ::lines 10   ::check   ::note ...   ::marked   ::save

It opens holding nothing and recording nothing, and says so on its face. It
hears the far end by capturing your output device, which means it hears
everything your speakers play -- so it waits to be told to start.

The model is downloaded once, not per meeting -- it lands in
models/whisper/ and is read from disk every launch after. Get it out of the
way ahead of time with `--fetch`, which is worth doing before you need it:

    venv\\Scripts\\python.exe meeting.py --model small --fetch

The transcript is written out when the window closes. Audio never is.
"""

import argparse
import json
import queue
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import listener
from bubbler import Bubbler
from capture import Loopback, Microphone
from listener import FRAME_SECONDS, MODELS, THEM, YOU, WhisperTranscriber
from minutes import Minutes

ROOT = Path(__file__).resolve().parent
MODEL_ROOT = listener.model_cache()
MEETING_PORT = 52720            # cufflink is on 52719; this is the next along

# Kept clear of two things: cufflink's own set (m, space, s, b, up, down, g)
# and anything with alt+r in it, which is the NVIDIA overlay -- a note already
# written at the top of cufflink.py and then walked straight into here.
#
# The collision is not the ordinary kind. `keyboard` installs a low-level hook
# rather than registering with Windows, so a taken combination does not fail
# to bind: both handlers fire. Ctrl+Alt+R started a recording *and* opened the
# NVIDIA overlay, and nothing anywhere reported a conflict.
KEY_RECORD = "ctrl+alt+l"       # L for listen
KEY_MARK = "ctrl+alt+k"         # K for keep this, I will come back to it
KEY_NOTE = "ctrl+alt+n"         # N for note
HELP = ("::where  ::state  ::dump  ::devices  ::use <you|them> [n]"
        "  ::show  ::record  ::pause  ::lines [n]  ::notes  ::marked  ::status"
        "  ::check [n]  ::uncheck [n]  ::note <text>  ::save [path]"
        "  ::stop  ::help")


def state_home(local=None) -> Path:
    """Where cufflink keeps what it has to remember between runs.

    Hardcoded rather than imported from cufflink. That module pulls in
    tkinter, pystray and keyboard on the way past, and this process has no
    business paying for any of them to find out the name of a directory --
    the arm's-length rule that keeps these two apart everywhere else.

    Kept in step with `cufflink.settings_home()`. If that moves, this moves.
    """
    import os

    base = Path(local) if local is not None else Path(
        os.environ.get("LOCALAPPDATA") or Path.home())
    return base / "cufflink"


def migrate_meetings(local=None) -> int:
    """Bring transcripts written under the old name across, once.

    The app was Murmur until the Store took the name. Transcripts kept going
    to `...\\Murmur\\meetings` while the log that describes the same run
    moved to `...\\cufflink\\`, which is not a crash -- it is somebody
    looking for last Tuesday's meeting in a folder nothing writes to any more.

    Nothing is ever written over: a name that already exists on the new side
    keeps a suffix instead. Losing a transcript is not recoverable, and the
    minute-resolution filenames have already collided once.
    """
    import os
    import shutil

    base = Path(local) if local is not None else Path(
        os.environ.get("LOCALAPPDATA") or Path.home())
    was = base / "Murmur" / "meetings"
    now = state_home(local) / "meetings"
    if not was.is_dir():
        return 0
    moved = 0
    for old in sorted(was.glob("*.txt")):
        now.mkdir(parents=True, exist_ok=True)
        target = now / old.name
        if target.exists():
            target = now / f"{old.stem}-from-murmur{old.suffix}"
        if target.exists():
            continue
        try:
            shutil.move(str(old), str(target))
            moved += 1
        except OSError:
            continue
    return moved


class _NoWindow:
    """Stands in for the bubbler when the pill is the window.

    Same shape as the real one so nothing that drives a session has to ask
    which it is talking to -- the same trick cufflink plays with `_Loading`
    while the voice is still coming off disk. Missing a method here is not a
    small oversight: it would raise on the transcribe thread, where nobody is
    looking.
    """

    recording = False
    status = ""

    def __init__(self):
        self._stop = threading.Event()

    def post(self, work):
        try:
            work()
        except Exception:
            pass

    def set_status(self, text):
        self.status = str(text or "")

    def set_recording(self, on):
        self.recording = bool(on)

    def appear(self):
        pass

    def refresh(self):
        pass

    def _close(self):
        self._stop.set()

    def run(self):
        self._stop.wait()


class Session:
    """Capture, transcription, marks and the window, wired together."""

    def __init__(self, args):
        self.args = args
        self.minutes = Minutes()
        self.frames: queue.Queue = queue.Queue()
        self.running = threading.Event()
        self.sources = []
        self._saved = False
        self._late = False
        self.bubbler = _NoWindow() if args.headless else Bubbler(
            self.minutes, on_close=self.stop, on_save=self.save,
            on_record=self.record, on_levels=self.levels,
            on_devices=self.devices, on_pick=self.pick)
        self.listener = listener.Meeting(
            self._transcriber(args.model),
            on_line=self._heard, refine=bool(args.refine_with))
        self.listener.transcriber.on_state = self._model_state
        self.better = (self._transcriber(args.refine_with)
                       if args.refine_with else None)
        # The refining pass must not start loading the better model while the
        # warm-up is already doing it: on a first run that is two threads
        # fetching the same half-gigabyte at once.
        self.better_ready = threading.Event()

    def _transcriber(self, size):
        return WhisperTranscriber(size, compute_type=self.args.compute,
                                  language=self.args.language,
                                  root=str(MODEL_ROOT),
                                  threads=self.args.threads, beam=self.args.beam)

    # ------------------------------------------------------------ plumbing

    def _heard(self, line):
        """Called on the transcribe thread; tkinter is not thread-safe, so
        the window is only ever touched through post()."""
        self.minutes.add(line)
        self.bubbler.post(self.bubbler.refresh)

    def _transcribe(self):
        while self.running.is_set():
            try:
                source, frame = self.frames.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.listener.feed(source, frame)
            except Exception as blew_up:      # a meeting is not the moment
                print(f"transcribe: {blew_up}", file=sys.stderr)

    def start(self):
        """Open the window and the port. Deliberately *not* the microphone.

        A tool that can hear both sides of a call must never be recording
        because it happens to be open. It starts holding nothing, says so on
        its own face, and waits to be told -- clicking the dot, Ctrl+Alt+L, or
        ::record. This was learned the hard way: an earlier build began
        capturing the moment it launched, and quietly transcribed a personal
        voice message that was playing through the speakers at the time.
        """
        MODEL_ROOT.mkdir(parents=True, exist_ok=True)
        brought = migrate_meetings()
        if brought:
            print(f"moved {brought} transcript(s) from the old Murmur folder")
        self.running.set()
        threading.Thread(target=self._warm, daemon=True).start()
        threading.Thread(target=self._improve, daemon=True).start()
        threading.Thread(target=self._transcribe, daemon=True).start()
        threading.Thread(target=self._serve, daemon=True).start()
        self._hotkeys()
        if self.args.record:
            self.record(True)

    def _improve(self):
        """The second pass, on its own thread.

        Real time was the wrong thing to aim at. The model that keeps up is
        not the accurate one and the accurate one cannot keep up -- `base`
        runs at 0.25x real time and `small` at 0.8-1.0x, which leaves no
        headroom at all. So `base` puts words on screen while somebody is
        still talking, and this walks along behind rewriting them properly.
        Falling behind here costs nothing anybody watches: the words are
        already up, and they only get better.
        """
        if self.better is not None:
            self.better_ready.wait()
        while self.running.is_set():
            if self.better is None or not self.listener.awaiting:
                time.sleep(0.3)
                continue
            try:
                self.listener.refine(self.better)
            except Exception as blew_up:
                print(f"refine: {blew_up}", file=sys.stderr)

    def _warm(self):
        """Load the model now, not when somebody first speaks.

        It used to load lazily, inside the first utterance that completed.
        That put a one-off cost -- an import of ctranslate2, a read of up to
        half a gigabyte, and on a machine that has just installed them, a
        virus scan of both -- in the middle of a live meeting, with audio
        queueing up behind it. Measured at four minutes on a first run, during
        which the window sat there saying nothing and then dumped the backlog
        all at once.

        The wait is the same length here. The difference is that it happens
        before anyone is talking, and it says so.
        """
        model = MODELS[self.args.model]
        for name, which in (("", self.listener.transcriber),
                            (self.args.refine_with or "", self.better)):
            if which is not None and not which.cached():
                size = MODELS[which.size].size / 1e6
                self._model_state(f"fetching {which.size}, {size:.0f} MB, once")
                print(f"Fetching {which.size} -- {size:.0f} MB, once")
        cached = self.listener.transcriber.cached()
        if not cached:
            self._model_state(f"fetching {self.args.model}, "
                              f"{model.size / 1e6:.0f} MB, once")
            print(f"Fetching {self.args.model} -- {model.size / 1e6:.0f} MB, once")
        started = time.perf_counter()
        try:
            self.listener.transcriber.load()
            took = time.perf_counter() - started
            self._model_state(f"{self.args.model} ready {took:.0f}s")
            print(f"model: {self.args.model} ready in {took:.0f}s")
            if self.better is not None:
                # Loaded second, and only after the fast one is usable: with
                # `base` shipped alongside the app, the meeting starts at once
                # and this can take as long as a download needs.
                if not self.better.cached():
                    self._model_state(f"{self.args.model} live, fetching "
                                      f"{self.better.size}")
                self.better.load()
                self.better_ready.set()
                self._model_state(f"{self.args.model} → {self.better.size}")
                print(f"model: {self.better.size} refining behind it")
        except Exception as failed:
            self._model_state(f"model failed: {failed}")
            print(f"model: {failed}", file=sys.stderr)
        finally:
            # Released even on failure, or the refining thread waits for ever
            # on a model that is never coming.
            self.better_ready.set()

    def _model_state(self, text):
        self.bubbler.post(lambda: self.bubbler.set_status(text))

    # Host APIs, best first. WASAPI is the low-latency route on anything since
    # Vista and the right default for capture; MME is the 1991 one and is here
    # only so a machine that offers nothing else still gets an entry.
    API_ORDER = ("Windows WASAPI", "Windows WDM-KS", "Windows DirectSound", "MME")

    def devices(self) -> dict:
        """What can be captured, one entry per physical device.

        PortAudio lists every input once per host API, so a single microphone
        appears four times under four identical names. Offering all of them
        asks somebody to choose between things that look the same and are not,
        so only the best route to each device is offered.
        """
        import sounddevice as sd

        import capture as cap

        apis = sd.query_hostapis()
        rank = {name: i for i, name in enumerate(self.API_ORDER)}
        best = {}
        for index, device in enumerate(sd.query_devices()):
            if not device["max_input_channels"]:
                continue
            if "[Loopback]" in device["name"]:
                continue
            api = apis[device["hostapi"]]["name"]
            here = rank.get(api, len(rank))
            if device["name"] not in best or here < best[device["name"]][0]:
                best[device["name"]] = (here, index)
        mics = [(name, index) for name, (_, index)
                in sorted(best.items(), key=lambda pair: pair[1][1])]

        far = []
        try:
            import pyaudiowpatch as pa

            audio = pa.PyAudio()
            # The loopback list duplicates too, by the same logic: one entry
            # per name, the first one offered.
            seen = {}
            for device in cap.loopback_candidates(audio):
                seen.setdefault(device["name"], device["index"])
            far = [(name, index) for name, index in seen.items()]
            audio.terminate()
        except Exception:
            pass
        return {YOU: mics, THEM: far}

    def pick(self, source: str, index) -> str:
        """Swap a device mid-meeting, restarting only that half of it."""
        if source == YOU:
            self.args.mic = index
        else:
            self.args.loopback = index
        if not self.recording:
            return f"{source}: device {index}, used when recording starts"
        for open_source in list(self.sources):
            if open_source.source == source:
                try:
                    open_source.stop()
                except Exception:
                    pass
                self.sources.remove(open_source)
        try:
            return f"{source}: {self._open(source)}"
        except Exception as failed:
            return f"{source}: {failed}"

    def _open(self, source: str) -> str:
        if source == YOU:
            mic = Microphone(self.frames, device=self.args.mic, source=YOU)
            name = mic.start()
            self.sources.append(mic)
            return name
        far = Loopback(self.frames, device=self.args.loopback, source=THEM)
        name = far.start()
        self.sources.append(far)
        return name

    def levels(self) -> dict:
        """What each source is hearing right now, for the meters."""
        import capture as cap

        now = time.monotonic()
        out = {YOU: None, THEM: None}
        for source in self.sources:
            quiet = now - source.heard_at > cap.QUIET_AFTER
            out[source.source] = 0 if quiet else cap.meter(source.level)
        self._watch_backlog()
        return out

    # A model slower than real time does not fail, it drifts: the transcript
    # arrives later and later and then all at once, which reads as the tool
    # having hung. `small` runs at about 0.8-1.0x real time on this machine,
    # so there is almost no headroom and a busy machine can tip it over.
    # Saying so is the difference between "it is broken" and "it is behind".
    BEHIND_SECONDS = 4.0

    def _watch_backlog(self):
        """Only the fast pass falling behind is worth saying.

        The refining queue is allowed to lag -- the words are already on
        screen and it is only improving them -- so reporting it would be
        alarming about the thing that is working as designed.
        """
        behind = self.frames.qsize() * FRAME_SECONDS
        if behind >= self.BEHIND_SECONDS:
            self._late = True
            self.bubbler.set_status(
                f"{behind:.0f}s behind -- try a smaller --model")
        elif getattr(self, "_late", False) and behind < 1.0:
            self._late = False
            self.bubbler.set_status("caught up")

    @property
    def recording(self) -> bool:
        return bool(self.sources)

    def record(self, on: bool = True) -> str:
        """Open or close the capture devices.

        Closing them, rather than dropping frames on the floor, is the same
        reasoning as cufflink's mouse hook: while this is paused it is not in
        the machine's audio path at all, and that is a fact about the process
        rather than a promise about its code.
        """
        if on == self.recording:
            return "recording" if on else "paused"
        if not on:
            for source in self.sources:
                try:
                    source.stop()
                except Exception:
                    pass
            self.sources = []
            self._say_state()
            return "paused"

        opened = []
        for source, skip in ((YOU, self.args.no_mic), (THEM, self.args.no_loopback)):
            if skip:
                continue
            try:
                print(f"{source}: {self._open(source)}")
                opened.append(source)
            except Exception as failed:
                # Never silently: a half-open meeting transcribes as one voice
                # and looks exactly like a quiet one.
                print(f"{source}: {failed}", file=sys.stderr)
                self.bubbler.post(
                    lambda s=source, f=failed: self.bubbler.set_status(f"{s}: {f}"))
        self._say_state()
        return f"recording {' and '.join(opened)}" if opened else "nothing to record"

    def _say_state(self):
        self.bubbler.post(lambda: self.bubbler.set_recording(self.recording))

    def stop(self):
        """Closing the window calls this, and so does the mainloop unwinding
        after it. Saving twice wrote the transcript to two files -- the
        never-overwrite rule turned a double call into a duplicate rather than
        a loss, which is the right failure but still a mess to come back to."""
        self.record(False)
        self.running.clear()
        if not self._saved:
            self._saved = True
            self.save()

    def save(self, path=None):
        if not self.minutes.lines and not self.minutes.notes:
            return "nothing to save"
        where = Path(path or self.args.save or self._default_path())
        where.parent.mkdir(parents=True, exist_ok=True)
        # Never write over a transcript that is already there, whatever the
        # name says. Losing one is not recoverable.
        if where.exists():
            where = where.with_name(f"{where.stem}-{int(time.time())}{where.suffix}")
        self.minutes.save(where)
        print(f"{self.minutes.summary()} -> {where}")
        return f"saved to {where}"

    def _default_path(self):
        """Outside the repo, like the rest of cufflink's state, and in the
        same folder as the log of the run that produced it. A transcript of a
        meeting is not source, and must not end up in a commit by being in the
        working tree when somebody types `git add .`."""
        # Seconds, not minutes: two sessions closing in the same minute wrote
        # the same filename and the second silently replaced the first. That
        # happened, and what it cost was somebody's only copy of a call.
        stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
        return state_home() / "meetings" / f"{stamp}.txt"

    # ------------------------------------------------------------ hotkeys

    def _hotkeys(self):
        """Marking without touching the window, because the window is not
        where your attention is during a meeting.

        Which keys were actually taken is printed, because a clash here is
        silent by construction: the hook fires our handler and the other
        application's, so the only symptom is something else also happening.
        """
        try:
            import keyboard
        except Exception:
            print("hotkeys: unavailable", file=sys.stderr)
            return

        def check():
            self.minutes.check()
            self.bubbler.post(self.bubbler.refresh)

        def toggle():
            self.record(not self.recording)

        def jot():
            def focus():
                self.bubbler.root.lift()
                self.bubbler.entry.focus_force()
            self.bubbler.post(focus)

        wanted = ((self.args.key_record, toggle, "record"),
                  (self.args.key_mark, check, "mark"),
                  (self.args.key_note, jot, "note"))
        live = []
        for combination, action, what in wanted:
            if not combination:
                continue
            try:
                keyboard.add_hotkey(combination, action)
                live.append(f"{combination} {what}")
            except Exception as refused:
                print(f"hotkeys: {combination} refused ({refused})", file=sys.stderr)
        if live:
            print("hotkeys: " + ",  ".join(live))

    # --------------------------------------------------------------- port

    def _serve(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("127.0.0.1", MEETING_PORT))
        except OSError:
            print(f"Port {MEETING_PORT} taken; control disabled.", file=sys.stderr)
            return
        server.listen(2)
        while self.running.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                try:
                    asked = conn.recv(65536).decode("utf-8", "replace").strip()
                    conn.sendall(self.command(asked).encode("utf-8"))
                except Exception:
                    pass

    def command(self, text: str) -> str:
        """One line in, one answer out.

        cufflink's port takes commands and says nothing back; this one answers,
        because the reason the assistant connects at all is to read what was
        said. Same `::` dialect either way.
        """
        if not text.startswith("::"):
            return HELP
        name, _, arg = text[2:].strip().partition(" ")
        arg = arg.strip()
        if name == "help":
            return HELP
        if name == "save":
            return self.save(arg or None)
        if name in ("record", "pause"):
            return self.record(name == "record")
        if name == "where":
            return str(state_home() / "meetings")
        if name == "state":
            # The cheap question, safe to ask twice a second. Everything here
            # is a number already in hand; nothing is rendered or copied.
            levels = self.levels()
            return json.dumps({
                "version": self.minutes.version,
                "recording": self.recording,
                "levels": {source: levels.get(source) for source in (YOU, THEM)},
                "lines": len(self.minutes.lines),
                "checked": len(self.minutes.checked()),
                "notes": len(self.minutes.notes),
                "status": getattr(self.bubbler, "status", ""),
                "behind": round(self.frames.qsize() * FRAME_SECONDS, 1),
                "model": self.args.model,
                "refining": self.args.refine_with or "",
            })
        if name == "devices":
            return json.dumps(self.devices())
        if name == "use":
            source, _, index = arg.partition(" ")
            if source not in (YOU, THEM):
                return f"? which source: {source}"
            index = index.strip()
            return self.pick(source, int(index) if index.isdigit() else None)
        if name == "show":
            self.bubbler.post(self.bubbler.appear)
            return "here"
        if name == "stop":
            self.bubbler.post(self.bubbler._close)
            return "stopping"
        answer = self.minutes.command(name, arg)
        if not answer.startswith("? unknown"):
            self.bubbler.post(self.bubbler.refresh)
        return answer

    def run(self):
        self.start()
        try:
            self.bubbler.run()
        finally:
            self.stop()


def fetch(args):
    """Get the model onto the disk now, deliberately, rather than during a
    call. Once done it is never downloaded again -- it is read from
    models/whisper/ on every launch after."""
    model = MODELS[args.model]
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    grabber = WhisperTranscriber(args.model, compute_type=args.compute,
                                 root=str(MODEL_ROOT))
    if grabber.cached():
        print(f"{args.model} is already here: {MODEL_ROOT}")
        return
    print(f"Fetching {args.model} -- {model.size / 1e6:.0f} MB, once, "
          f"into {MODEL_ROOT}")
    started = time.perf_counter()
    grabber.load()
    print(f"done in {time.perf_counter() - started:.0f}s. "
          f"No meeting will wait for this again.")


def list_devices():
    """Which microphone, and which loopback. The default input is not always
    the one your call is using, and picking the wrong one is invisible in the
    transcript -- it just says `them` for an hour."""
    import sounddevice as sd

    import capture as cap

    print("you  -- microphones (--mic <index>):")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] and "[Loopback]" not in d["name"]:
            api = sd.query_hostapis(d["hostapi"])["name"]
            mark = " <- default" if i == sd.default.device[0] else ""
            print(f"  {i:3}  {api:20} {d['name']}{mark}")
    print()
    print("them -- loopback (--loopback <index>):")
    try:
        import pyaudiowpatch as pa

        audio = pa.PyAudio()
        working = cap.pick_loopback(audio)
        for d in cap.loopback_candidates(audio):
            live = " <- delivers audio" if working and d["index"] == working["index"] else ""
            print(f"  {d['index']:3}  {d['name']}{live}")
        audio.terminate()
    except Exception as no_loopback:
        print(f"  none: {no_loopback}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="base", choices=list(MODELS),
                   help="the fast one, which has to keep up (default: base)")
    p.add_argument("--refine-with", default="small", choices=list(MODELS) + [""],
                   help="the better one, which rewrites each line a few "
                        "seconds later; \"\" to do only one pass")
    p.add_argument("--threads", type=int, default=None,
                   help="CPU threads (default: physical cores, capped at 16)")
    p.add_argument("--beam", type=int, default=5,
                   help="beam size; 1 is a little faster and a little worse")
    p.add_argument("--compute", default="int8")
    p.add_argument("--language", default=None, help="nl, en, ... (default: detect)")
    p.add_argument("--mic", default=None, help="input device index or name")
    p.add_argument("--loopback", type=int, default=None,
                   help="loopback device index (default: whichever works)")
    p.add_argument("--no-mic", action="store_true", help="only the far end")
    p.add_argument("--no-loopback", action="store_true", help="only you")
    p.add_argument("--save", default=None, help="where to write the transcript")
    p.add_argument("--fetch", action="store_true",
                   help="download the model and stop, so a meeting never waits")
    p.add_argument("--devices", action="store_true",
                   help="list what can be captured, and stop")
    p.add_argument("--key-record", default=KEY_RECORD,
                   help=f"start/stop recording (default: {KEY_RECORD}); "
                        f'"" for none')
    p.add_argument("--key-mark", default=KEY_MARK,
                   help=f"mark the line just said (default: {KEY_MARK})")
    p.add_argument("--key-note", default=KEY_NOTE,
                   help=f"jump to the note box (default: {KEY_NOTE})")
    p.add_argument("--headless", action="store_true",
                   help="no window of its own: the pill in cufflink is the "
                        "window, and this is only capture and transcription")
    p.add_argument("--record", action="store_true",
                   help="start recording at once (default: wait to be asked)")
    args = p.parse_args()
    if args.devices:
        return list_devices()
    if args.fetch:
        return fetch(args)
    Session(args).run()


if __name__ == "__main__":
    main()
