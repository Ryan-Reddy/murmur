"""The settings window: tune a voice and hear it, without editing JSON.

    import settingsui
    settingsui.open_window(root, settings, save, speaker, on_applied)

One window at a time. Unlike the pill this one *does* take focus -- it has
dropdowns and sliders to drive, and a dialog that cannot be typed into is a
worse citizen than one that takes focus honestly.

Nothing here writes to the running speaker except through `on_applied`, so
closing without saving leaves the voice exactly as it was.
"""

import ctypes
import tkinter as tk

# Matches the pill, so the two read as one application.
BG = "#191622"
PANEL = "#221d2f"
EDGE = "#2b2539"
FG = "#efe9f8"
MUTED = "#8f87a8"
CONTROL = "#c3bad6"
CONTROL_HOT = "#efe9fb"
ACCENT = "#a98bff"
TRACK = "#7a63c0"

SAMPLE = ("Here is the outlook. The tests have passed, and the branch is ready "
          "for review.")

# A profile is a job. This is the job description, so the dropdown is a list
# of posts rather than a list of words.
JOBS = {
    "default": "Everything you ask to be read. Hired for the long haul.",
    "claude": "Claude Code, interrupting. Hired to land without alarming you.",
    "clock": "The time, and anything else on a schedule. Hired to be ignorable.",
}
FREELANCE = "Yours. Anything that sends ::as {name} gets read in this."


def _treatments():
    """The treatments as (key, label), from the one place that names them."""
    try:
        import characters

        return characters.catalogue()
    except Exception:
        return [("clean", "Murmur — plain, no colour")]


def _ingredients():
    """The mixer's dials. Empty if the DSP module will not import, in which
    case the window is still a window and the characters still work."""
    try:
        import voices

        return voices.INGREDIENTS
    except Exception:
        return {}


def _recipe_for(name):
    try:
        import voices

        return voices.recipe_for(name)
    except Exception:
        return {}


def _percent(value) -> str:
    amount = float(value)
    if amount < 0.005:
        return "none"
    return f"{amount * 100:.0f}%"


# A menu of 54 voices is taller than the screen, so it breaks into columns.
MENU_COLUMN = 18

_open = {"window": None}


def open_window(root, settings, save, speaker, on_applied):
    """Show the settings window, raising the existing one if it is already up."""
    existing = _open.get("window")
    if existing is not None and existing.winfo_exists():
        existing.deiconify()
        existing.lift()
        existing.focus_force()
        return existing

    window = _Window(root, settings, save, speaker, on_applied)
    _open["window"] = window.top
    return window.top


def _dark_titlebar(window):
    """Ask the compositor for a dark frame. A pale title bar over this palette
    looks like a mistake rather than a window."""
    try:
        window.update_idletasks()
        user32 = ctypes.windll.user32
        # Declared, because an undeclared return is a C int and a window
        # handle above 2^31 comes back truncated -- silently, and only on the
        # launches where the loader happened to place things up there.
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        frame = user32.GetParent(ctypes.c_void_p(window.winfo_id()))
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        dwm.DwmSetWindowAttribute(
            ctypes.c_void_p(frame), 20,            # USE_IMMERSIVE_DARK_MODE
            ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass


class _Window:
    def __init__(self, root, settings, save, speaker, on_applied):
        self.settings = settings
        self.save = save
        self.speaker = speaker
        self.on_applied = on_applied
        self.loading = False

        self.top = tk.Toplevel(root)
        self.top.title("Murmur — voices")
        self.top.configure(bg=BG)
        self.top.resizable(False, False)
        self.top.protocol("WM_DELETE_WINDOW", self.close)

        body = tk.Frame(self.top, bg=BG, padx=24, pady=6)
        body.pack(fill="both", expand=True)

        self.profile_name = tk.StringVar(value="default")
        self.voice_a = tk.StringVar()
        self.voice_b = tk.StringVar()
        self.mix = tk.DoubleVar(value=70)
        self.speed = tk.DoubleVar(value=1.0)
        self.treatment = tk.StringVar(value="clean")
        self.sentence_pause = tk.DoubleVar(value=0.25)
        self.clause_pause = tk.DoubleVar(value=0.10)

        # Built before the rows, because a row that explains itself on hover
        # writes into this one.
        self._resting_note = ""
        self.note = tk.Label(body, text="", bg=BG, fg=MUTED, justify="left",
                             font=("Segoe UI", 9), anchor="w", wraplength=400)

        singers = self._voice_names()
        keys = [key for key, _ in singers]
        said = dict(singers)

        self._heading(body, "Profile")
        self._row(body, "Job", self._profile_picker)
        self.job = tk.Label(body, text="", bg=BG, fg=MUTED, anchor="w",
                            font=("Segoe UI", 9), wraplength=380, justify="left")
        self.job.pack(fill="x", pady=(2, 0))

        self._heading(body, "Voice")
        self._row(body, "First",
                  lambda row: self._dropdown(row, self.voice_a, keys, said))
        self._row(body, "Second",
                  lambda row: self._dropdown(row, self.voice_b, keys, said))
        self._row(body, "Mix",
                  lambda row: self._slider(row, self.mix, 0, 100, 1, self._mix))
        self._row(body, "Speed",
                  lambda row: self._slider(row, self.speed, 0.7, 2.0, 0.05,
                                           lambda v: f"{float(v):.2f}×"))

        treatments = _treatments()
        self._heading(body, "Sound")
        self._row(body, "Read by",
                  lambda row: self._dropdown(row, self.treatment,
                                             [t[0] for t in treatments],
                                             dict(treatments)))

        # The mixer. Picking a character loads its recipe here; moving any of
        # these takes over from the character, so you can start at The
        # Informant and walk it towards Abbey.
        self.amounts = {}
        for key, hint in _ingredients().items():
            self.amounts[key] = tk.DoubleVar(value=0.0)
            self._row(body, hint.split(" — ")[0],
                      lambda row, k=key: self._slider(
                          row, self.amounts[k], 0.0, 1.0, 0.01, _percent),
                      hint=hint)
        self.treatment.trace_add("write", self._character_chosen)
        self._row(body, "Sentence pause",
                  lambda row: self._slider(row, self.sentence_pause, 0.0, 0.40,
                                           0.01, lambda v: f"{float(v):.2f}s"))
        self._row(body, "Clause pause",
                  lambda row: self._slider(row, self.clause_pause, 0.0, 0.25,
                                           0.01, lambda v: f"{float(v):.2f}s"))

        self.note.pack(fill="x", pady=(18, 0))

        buttons = tk.Frame(body, bg=BG)
        buttons.pack(fill="x", pady=(12, 18))
        self._button(buttons, "Hear it", self.preview).pack(side="left")
        self._button(buttons, "Close", self.close).pack(side="right")
        self._button(buttons, "Save", self.apply, primary=True).pack(
            side="right", padx=(0, 8))

        # The mix readout names the two voices, so it is stale the moment
        # either of them changes. Re-setting the value fires its own trace.
        for chooser in (self.voice_a, self.voice_b):
            chooser.trace_add("write", lambda *_: self.mix.set(self.mix.get()))

        self.load_profile("default")
        self.top.update_idletasks()
        self._centre(root)
        _dark_titlebar(self.top)

    # -- construction helpers ------------------------------------------------

    def _voice_names(self):
        """Every voice the model ships, as (key, label) -- the ones we have
        actually listened to first. A single placeholder while it is still
        loading, since the window can be opened before the voice exists."""
        try:
            import characters

            return characters.singers(self.speaker.kokoro.get_voices())
        except Exception:
            return [("(still loading)", "(still loading)")]

    def _heading(self, parent, text):
        tk.Label(parent, text=text.upper(), bg=BG, fg=MUTED, anchor="w",
                 font=("Segoe UI", 8, "bold")).pack(fill="x", pady=(18, 5))
        tk.Frame(parent, bg=EDGE, height=1).pack(fill="x", pady=(0, 8))

    def _row(self, parent, label, build, hint=None):
        """A labelled control.

        `build` is handed the row and must parent its widget to it: a widget
        built on `parent` and merely packed `in_` the row sits *below* the row
        in the stacking order, and is drawn over -- which is exactly how the
        first version came out as a column of labels with nothing beside them.
        """
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=3)
        name = tk.Label(row, text=label, bg=BG, fg=CONTROL,
                        font=("Segoe UI", 10), width=14, anchor="w")
        name.pack(side="left")
        if hint:
            # The hint goes where the note goes, rather than in a tooltip:
            # there is already somewhere on this window that explains things.
            name.bind("<Enter>", lambda _e, h=hint: self._say(h))
            name.bind("<Leave>", lambda _e: self._say(None))
        build(row).pack(side="left", fill="x", expand=True)
        return row

    def _say(self, text):
        """Explain something under the pointer, and put the note back after."""
        if text is None:
            self.note.config(text=self._resting_note)
        else:
            self.note.config(text=text)

    def _dropdown(self, parent, variable, values, labels=None):
        labels = labels or {}
        shown = tk.StringVar(value=labels.get(variable.get(), variable.get()))

        menu = tk.OptionMenu(parent, shown, "")
        menu.configure(bg=PANEL, fg=FG, activebackground=EDGE,
                       activeforeground=CONTROL_HOT, highlightthickness=0,
                       borderwidth=0, anchor="w", padx=10, pady=4,
                       font=("Segoe UI", 10), cursor="hand2",
                       direction="flush", indicatoron=False)
        items = menu["menu"]
        items.configure(bg=PANEL, fg=FG, activebackground=ACCENT,
                        activeforeground=BG, borderwidth=0, activeborderwidth=0,
                        font=("Segoe UI", 10))
        items.delete(0, "end")

        def choose(value):
            variable.set(value)

        for index, value in enumerate(values):
            items.add_command(
                label=labels.get(value, value),
                columnbreak=index and index % MENU_COLUMN == 0,
                command=lambda v=value: choose(v))

        variable.trace_add(
            "write", lambda *_: shown.set(labels.get(variable.get(),
                                                     variable.get())))
        return menu

    def _mix(self, value) -> str:
        """The mix, said as the two people it is mixing.

        "70% first" tells you nothing when the two dropdowns above already say
        first and second. Who is 70% of it is the useful part.
        """
        first = int(float(value))
        who = self.voice_a.get().split("_", 1)[-1].title()
        other = self.voice_b.get().split("_", 1)[-1].title()
        if first >= 99:
            return f"all {who}"
        if first <= 1:
            return f"all {other}"
        return f"{first}% {who}"

    def _slider(self, parent, variable, low, high, step, fmt):
        frame = tk.Frame(parent, bg=BG)
        readout = tk.Label(frame, text=fmt(variable.get()), bg=BG, fg=FG,
                           font=("Segoe UI", 9), width=9, anchor="e")
        # Tk paints the handle in the widget's own background, so `bg` here is
        # the handle colour, not the backdrop -- with no border and no
        # highlight the trough fills everything else.
        scale = tk.Scale(frame, variable=variable, from_=low, to=high,
                         resolution=step, orient="horizontal", showvalue=False,
                         bg=TRACK, fg=FG, troughcolor=EDGE,
                         activebackground=ACCENT, highlightthickness=0,
                         borderwidth=0, sliderrelief="flat", sliderlength=18,
                         width=8, cursor="hand2",
                         command=lambda v: readout.config(text=fmt(v)))
        scale.pack(side="left", fill="x", expand=True, pady=4)
        readout.pack(side="right", padx=(10, 0))
        variable.trace_add("write",
                           lambda *_: readout.config(text=fmt(variable.get())))
        return frame

    def _button(self, parent, text, command, primary=False):
        rest = ACCENT if primary else PANEL
        hot = CONTROL_HOT if primary else EDGE
        widget = tk.Label(parent, text=text, bg=rest, fg=BG if primary else FG,
                          font=("Segoe UI", 10), padx=18, pady=8, cursor="hand2")
        widget.bind("<Button-1>", lambda _e: command())
        widget.bind("<Enter>", lambda _e: widget.config(bg=hot))
        widget.bind("<Leave>", lambda _e: widget.config(bg=rest))
        return widget

    def _profile_picker(self, parent):
        picker = self._dropdown(parent, self.profile_name,
                                sorted(self.settings["profiles"]))

        def switched(*_):
            if not self.loading:
                self.load_profile(self.profile_name.get())

        self.profile_name.trace_add("write", switched)
        return picker

    def _centre(self, root):
        width, height = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        x = root.winfo_screenwidth() // 2 - width // 2
        y = root.winfo_screenheight() // 2 - height // 2
        self.top.geometry(f"+{max(0, x)}+{max(0, y)}")

    # -- state ---------------------------------------------------------------

    def _character_chosen(self, *_):
        """Choosing a character loads its recipe into the mixer.

        The characters run their own hand-built chains, so a name on its own
        sounds exactly as it always did; this is only where the dials start
        from if you decide to move them.
        """
        if self.loading:
            return
        self.loading = True
        try:
            recipe = _recipe_for(self.treatment.get())
            for key, variable in self.amounts.items():
                variable.set(round(float(recipe.get(key, 0.0)), 2))
        finally:
            self.loading = False

    def load_profile(self, name):
        """Fill the form from a stored profile. Re-entrant: setting the name
        fires the picker's trace, which lands back here."""
        if self.loading:
            return
        self.loading = True
        try:
            profile = self.settings["profiles"].get(name, {})
            blend = profile.get("blend") or [["bf_emma", 0.7], ["af_nicole", 0.3]]
            first = blend[0] if blend else ["bf_emma", 1.0]
            second = blend[1] if len(blend) > 1 else ["af_nicole", 0.0]
            self.voice_a.set(first[0])
            self.voice_b.set(second[0])
            self.mix.set(round(float(first[1]) * 100))
            self.speed.set(profile.get("speed", 1.0))
            self.treatment.set(profile.get("treatment", "clean"))
            self.sentence_pause.set(profile.get("sentence_pause", 0.25))
            self.clause_pause.set(profile.get("clause_pause", 0.10))
            recipe = profile.get("recipe") or _recipe_for(
                profile.get("treatment", "clean"))
            for key, variable in self.amounts.items():
                variable.set(round(float(recipe.get(key, 0.0) or 0.0), 2))
            self.profile_name.set(name)
            self.job.config(text=JOBS.get(name) or FREELANCE.format(name=name))
            self._resting_note = f"Editing “{name}”. Nothing changes until you save."
            self.note.config(text=self._resting_note)
        finally:
            self.loading = False

    def as_profile(self) -> dict:
        first = max(0.0, min(1.0, self.mix.get() / 100.0))
        profile = {
            "blend": [[self.voice_a.get(), round(first, 3)],
                      [self.voice_b.get(), round(1 - first, 3)]],
            "speed": round(self.speed.get(), 3),
            "treatment": self.treatment.get(),
            "sentence_pause": round(self.sentence_pause.get(), 3),
            "clause_pause": round(self.clause_pause.get(), 3),
        }
        # Only when the dials have actually been moved off the character's own
        # position. Otherwise the profile stays a plain name, and the name
        # keeps running the hand-built chain it always did.
        recipe = {key: round(var.get(), 3) for key, var in self.amounts.items()}
        if recipe != {key: round(float(value), 3) for key, value
                      in _recipe_for(self.treatment.get()).items()}:
            profile["recipe"] = recipe
        return profile

    # -- actions -------------------------------------------------------------

    def preview(self):
        """Speak the sample in whatever the form currently says, saved or not.
        Tuning a voice you cannot hear is guesswork."""
        try:
            self.speaker.speak(SAMPLE, profile=self.as_profile())
            self.note.config(text="Playing the sample in the settings above.")
        except Exception as error:
            self.note.config(text=f"Could not play that: {error}")

    def apply(self):
        name = self.profile_name.get()
        self.settings["profiles"][name] = self.as_profile()
        saved = self.save(self.settings)
        self.on_applied(name, self.settings["profiles"][name])
        self.note.config(
            text=f"Saved “{name}”." if saved
            else f"“{name}” is live, but the settings file could not "
                 "be written.")

    def close(self):
        _open["window"] = None
        self.top.destroy()
