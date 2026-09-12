"""The bubbler: what is being said, as it is said, in something you can mark.

One window, one job. Each line arrives as a bubble the moment the transcriber
finishes with it; clicking a bubble marks it to come back to; typing in the
box at the bottom hangs a note off whatever was in the air. Nothing else.

It deliberately does *not* wear cufflink's WS_EX_NOACTIVATE. The pill carries
that flag because it must never take focus from the text being read, and it
only ever has to be clicked. This has to be typed into, and a window that
cannot take focus cannot be typed into. The hotkeys in `meeting.py` are the
answer to the other half of that: a mark or a note without touching the
window, so during the meeting itself focus never has to move at all.

Drawing follows the pill's palette, and for the same reasons -- see the "How
the pill is drawn" section of DECISIONS.md.
"""

import queue
import tkinter as tk

from listener import THEM, YOU, _stamp
from minutes import CHECK

BG = "#191622"          # the same warm ground as the pill
EDGE = "#2b2539"        # hairline rim, so it is not a hole cut in the desktop
FG = "#c3bad6"          # spoken words
MUTED = "#7b7391"       # timestamps and furniture
CONTROL = "#aaa1c4"
CONTROL_HOT = "#efe9fb"
VOICE_YOU = "#a98bff"   # you: the accent violet
VOICE_THEM = "#74d3a4"  # everyone else: green, told apart at a glance
MARKED = "#f0b273"      # amber, the same "your attention is required" as pinned
NOTE_FG = "#efe9fb"
SUSPECT = "#6b6483"
LIVE = "#e06c75"        # recording: red, the one colour nothing else here uses
METER = "▁▃▅▆▇"   # five rising blocks
EMPTY_METER = "▁▁▁▁▁"
LIVE_DOT = "●"     # filled while listening
IDLE_DOT = "○"     # hollow while not

FONT = ("Segoe UI", 10)
SMALL = ("Segoe UI", 8)


class Bubbler:
    """The window. `minutes` is the truth; this only ever shows it."""

    def __init__(self, minutes, title="Meeting", on_close=None, on_save=None,
                 on_record=None, on_levels=None, on_devices=None,
                 on_pick=None):
        self.minutes = minutes
        self.on_close = on_close
        self.on_save = on_save
        self.on_record = on_record
        self.on_levels = on_levels
        self.on_devices = on_devices
        self.on_pick = on_pick
        self.picking = False
        self.recording = False
        self.started = None
        self.follow = True          # stick to the bottom until scrolled away
        self._rows = []             # (Line, first_index) per drawn bubble
        self._pending = queue.Queue()

        self.root = tk.Tk()
        self.root.title(title)
        self.root.configure(bg=EDGE)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry("420x520+40+40")

        rim = tk.Frame(self.root, bg=EDGE)
        rim.pack(fill="both", expand=True)
        shell = tk.Frame(rim, bg=BG)
        shell.pack(fill="both", expand=True, padx=1, pady=1)

        self._build_header(shell)
        self._build_body(shell)
        self._build_footer(shell)
        self._build_settings(shell)
        self._tick()
        self._drain()

    # ------------------------------------------------------------- chrome

    def _build_header(self, parent):
        head = tk.Frame(parent, bg=BG)
        head.pack(fill="x", padx=10, pady=(8, 4))

        # The dot is the recording state and the switch for it, in that
        # order: you should be able to tell at a glance across a desk whether
        # this thing is listening, and stop it with one click if it is.
        self.dot = tk.Label(head, text=IDLE_DOT, bg=BG, fg=MUTED, font=SMALL,
                            cursor="hand2")
        self.dot.pack(side="left")
        self.dot.bind("<Button-1>", lambda _e: self._record())
        self.state = tk.Label(head, text="not recording", bg=BG, fg=MUTED,
                              font=SMALL, cursor="hand2")
        self.state.pack(side="left", padx=(6, 0))
        self.state.bind("<Button-1>", lambda _e: self._record())
        self.clock = tk.Label(head, text="00:00", bg=BG, fg=FG, font=SMALL)
        self.clock.pack(side="left", padx=(8, 0))

        # A meter each, because "everything came out as them" and "my
        # microphone was never in the room" produce the same transcript and
        # look identical afterwards. Live, they look nothing alike.
        self.meters = {}
        for source, colour in ((YOU, VOICE_YOU), (THEM, VOICE_THEM)):
            box = tk.Frame(head, bg=BG)
            box.pack(side="left", padx=(10, 0))
            tk.Label(box, text=source, bg=BG, fg=colour, font=SMALL).pack(side="left")
            bar = tk.Label(box, text=EMPTY_METER, bg=BG, fg=MUTED, font=SMALL)
            bar.pack(side="left", padx=(3, 0))
            self.meters[source] = bar
        self.counts = tk.Label(head, text="", bg=BG, fg=MUTED, font=SMALL)
        self.counts.pack(side="left", padx=(10, 0))

        for text, tip, action in (("✕", "close", self._close),
                                  ("⇩", "save", self._save),
                                  ("⚙", "devices", self._toggle_devices)):
            button = tk.Label(head, text=text, bg=BG, fg=CONTROL, font=SMALL,
                              cursor="hand2")
            button.pack(side="right", padx=4)
            button.bind("<Button-1>", lambda _e, a=action: a())
            button.bind("<Enter>", lambda e: e.widget.config(fg=CONTROL_HOT))
            button.bind("<Leave>", lambda e: e.widget.config(fg=CONTROL))

        # Frameless, so the header is the title bar.
        for widget in (head, self.counts):
            widget.bind("<Button-1>", self._grab)
            widget.bind("<B1-Motion>", self._drag)

    def _build_body(self, parent):
        body = tk.Frame(parent, bg=BG)
        body.pack(fill="both", expand=True, padx=(10, 4))

        self.text = tk.Text(body, bg=BG, fg=FG, font=FONT, wrap="word",
                            relief="flat", highlightthickness=0, bd=0,
                            padx=2, pady=2, cursor="arrow",
                            spacing1=2, spacing3=6, state="disabled")
        bar = tk.Scrollbar(body, command=self._scrolled, width=8,
                           bg=BG, troughcolor=BG, activebackground=CONTROL,
                           relief="flat", bd=0, highlightthickness=0)
        self.text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        self.text.tag_configure("when", foreground=MUTED, font=SMALL)
        self.text.tag_configure(YOU, foreground=VOICE_YOU, font=SMALL)
        self.text.tag_configure(THEM, foreground=VOICE_THEM, font=SMALL)
        self.text.tag_configure("said", foreground=FG, lmargin1=4, lmargin2=4)
        self.text.tag_configure("checked", foreground=MARKED)
        self.text.tag_configure("mark", foreground=MARKED, font=SMALL)
        self.text.tag_configure("suspect", foreground=SUSPECT)
        self.text.tag_configure("note", foreground=NOTE_FG, font=SMALL,
                                lmargin1=18, lmargin2=18)
        self.text.tag_configure("waiting", foreground=MUTED, font=SMALL)

    def _build_settings(self, parent):
        """The device pickers, one row each, hidden until asked for.

        In reach during the call, not in a preferences window three clicks
        away. The moment you find out you picked the wrong microphone is the
        moment somebody is already talking, and by the time you have restarted
        anything they have stopped.
        """
        self.settings = tk.Frame(parent, bg=BG)
        self.chosen = {}
        self.menus = {}
        for source, colour in ((YOU, VOICE_YOU), (THEM, VOICE_THEM)):
            row = tk.Frame(self.settings, bg=BG)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=source, bg=BG, fg=colour, font=SMALL,
                     width=5, anchor="w").pack(side="left")
            picked = tk.StringVar(value="(default)")
            self.chosen[source] = picked
            menu = tk.OptionMenu(row, picked, "(default)")
            menu.config(bg=EDGE, fg=FG, font=SMALL, relief="flat", bd=0,
                        highlightthickness=0, activebackground=EDGE,
                        activeforeground=CONTROL_HOT, anchor="w")
            menu["menu"].config(bg=EDGE, fg=FG, font=SMALL,
                                activebackground=colour, activeforeground=BG)
            menu.pack(side="left", fill="x", expand=True)
            self.menus[source] = menu

    def _toggle_devices(self):
        self.picking = not self.picking
        if not self.picking:
            self.settings.pack_forget()
            return
        self.settings.pack(fill="x", padx=10, pady=(0, 4), before=self.footer)
        lists = self.on_devices() if self.on_devices else {}
        for source in (YOU, THEM):
            inner = self.menus[source]["menu"]
            inner.delete(0, "end")
            options = lists.get(source) or []
            if not options:
                inner.add_command(label="(none found)")
                self.chosen[source].set("(none found)")
                continue
            for label, value in options:
                inner.add_command(
                    label=label,
                    command=lambda s=source, l=label, v=value: self._pick(s, l, v))

    def _pick(self, source, label, value):
        self.chosen[source].set(label)
        if self.on_pick:
            self.set_status(self.on_pick(source, value))

    def appear(self):
        """Come to the front. Asked for from the tray, which cannot know
        whether the window is buried, minimised or simply behind a call."""
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
        except Exception:
            pass

    def set_status(self, text):
        if text:
            self.status.config(text=str(text))

    def _build_footer(self, parent):
        self.status = tk.Label(parent, text="", bg=BG, fg=MUTED, font=SMALL,
                               anchor="w")
        self.status.pack(fill="x", padx=10)
        foot = tk.Frame(parent, bg=BG)
        self.footer = foot
        foot.pack(fill="x", padx=10, pady=(4, 8))
        tk.Label(foot, text="note", bg=BG, fg=MUTED, font=SMALL).pack(side="left")
        self.entry = tk.Entry(foot, bg=EDGE, fg=NOTE_FG, font=FONT,
                              relief="flat", insertbackground=NOTE_FG,
                              highlightthickness=1, highlightbackground=EDGE,
                              highlightcolor=VOICE_YOU)
        self.entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.entry.bind("<Return>", self._note)
        self.entry.bind("<Escape>", lambda _e: self.root.focus_set())

        grip = tk.Label(parent, text="◢", bg=BG, fg=MUTED, font=SMALL,
                        cursor="bottom_right_corner")
        grip.place(relx=1.0, rely=1.0, anchor="se")
        grip.bind("<Button-1>", self._grab)
        grip.bind("<B1-Motion>", self._resize)

    # ------------------------------------------------------------ gestures

    def _grab(self, event):
        self._from = (event.x_root, event.y_root,
                      self.root.winfo_x(), self.root.winfo_y(),
                      self.root.winfo_width(), self.root.winfo_height())

    def _drag(self, event):
        x0, y0, wx, wy, _w, _h = self._from
        self.root.geometry(f"+{wx + event.x_root - x0}+{wy + event.y_root - y0}")

    def _resize(self, event):
        x0, y0, _wx, _wy, w, h = self._from
        self.root.geometry(f"{max(280, w + event.x_root - x0)}x"
                           f"{max(200, h + event.y_root - y0)}")

    def _scrolled(self, *args):
        self.text.yview(*args)
        self.follow = self.text.yview()[1] > 0.999

    # ------------------------------------------------------------- actions

    def _note(self, _event=None):
        text = self.entry.get().strip()
        if text:
            self.minutes.note(text, at=self.elapsed())
            self.entry.delete(0, "end")
            self.refresh()

    def _clicked(self, event):
        index = self.text.index(f"@{event.x},{event.y}")
        row = int(str(index).split(".")[0])
        for line, first, last in self._rows:
            if first <= row <= last:
                self.minutes.check(line)
                self.refresh()
                return

    def _record(self):
        if self.on_record:
            self.on_record(not self.recording)

    def show_levels(self, levels: dict):
        """levels: source -> bars lit, or None when that source is not open."""
        for source, bar in self.meters.items():
            lit = levels.get(source)
            if lit is None:
                bar.config(text=EMPTY_METER, fg=SUSPECT)
            else:
                colour = VOICE_YOU if source == YOU else VOICE_THEM
                bar.config(text=METER[:lit] + EMPTY_METER[lit:],
                           fg=colour if lit else MUTED)

    def set_recording(self, on: bool):
        self.recording = bool(on)
        self.dot.config(text=LIVE_DOT if on else IDLE_DOT,
                        fg=LIVE if on else MUTED)
        self.state.config(text="recording" if on else "not recording",
                          fg=LIVE if on else MUTED)

    def _save(self):
        if self.on_save:
            self.on_save()

    def _close(self):
        if self.on_close:
            self.on_close()
        self.root.destroy()

    # ------------------------------------------------------------ drawing

    def elapsed(self) -> float:
        import time

        return 0.0 if self.started is None else time.monotonic() - self.started

    def add(self, _line=None):
        """A line landed. The Minutes already has it; just redraw."""
        self.refresh()

    def refresh(self):
        """Redrawn whole rather than appended to.

        A meeting is a few hundred lines and a redraw is a millisecond, while
        an append has to keep every mark's character range correct as notes
        are inserted above it -- which is the kind of bookkeeping that goes
        wrong quietly, in the one place where being quietly wrong is worst.
        """
        at_bottom = self.follow
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self._rows = []

        by_line = {}
        loose = []
        for note in self.minutes.notes:
            (by_line.setdefault(id(note.about), []) if note.about is not None
             else loose).append(note)

        if not self.minutes.lines and not loose:
            self.text.insert("end", "listening...\n", "waiting")

        for note in loose:
            self.text.insert("end", f"[{_stamp(note.at)}] ", "when")
            self.text.insert("end", f"note: {note.text}\n", "note")

        for line in self.minutes.lines:
            first = int(self.text.index("end-1c").split(".")[0])
            checked = line in self.minutes._checked
            self.text.insert("end", f"{_stamp(line.start)}  ", "when")
            self.text.insert("end", line.source, line.source)
            if checked:
                self.text.insert("end", f"   {CHECK}", "mark")
            self.text.insert("end", "\n")
            style = "checked" if checked else ("suspect" if line.suspect else "said")
            self.text.insert("end", f"{line.text}\n", style)
            for note in by_line.get(id(line), []):
                self.text.insert("end", f"note: {note.text}\n", "note")
            last = int(self.text.index("end-1c").split(".")[0])
            self._rows.append((line, first, last))

        self.text.configure(state="disabled")
        self.text.bind("<Button-1>", self._clicked)
        if at_bottom:
            self.text.see("end")
        self.counts.config(text=self.minutes.summary())

    def _tick(self):
        """The clock counts the meeting; the dot pulses only while something
        is actually being captured. A steady dot on a window that is not
        listening is the lie this has to not tell."""
        if self.started is not None and self.recording:
            self.clock.config(text=_stamp(self.elapsed()))
            self.dot.config(fg=LIVE if int(self.elapsed() * 2) % 2 else BG)
        if self.on_levels:
            try:
                self.show_levels(self.on_levels())
            except Exception:
                pass
        self.root.after(200, self._tick)

    # --------------------------------------------------------------- drive

    def post(self, work):
        """Run something on the UI thread.

        A queue rather than `after(0, ...)`: Tk is not thread-safe, and
        scheduling from the transcribe thread is the documented way to get an
        interpreter crash that only happens on somebody else's machine, in a
        meeting. Everything that touches tkinter from another thread comes
        through here, and only `_drain` ever calls it.
        """
        self._pending.put(work)

    def _drain(self):
        while True:
            try:
                work = self._pending.get_nowait()
            except queue.Empty:
                break
            try:
                work()
            except Exception:
                pass
        self.root.after(50, self._drain)

    def run(self):
        import time

        self.started = time.monotonic()
        self.refresh()
        self.root.mainloop()
