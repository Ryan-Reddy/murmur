"""Who the voices are. Names only -- no numpy, nothing to load.

Kept apart from voices.py on purpose. voices.py is the signal processing and
pulls in numpy, which costs half a second; the tray menu needs these names
before the model has started loading, and the whole point of the startup path
is that the icon and the pill appear straight away.

    import characters
    characters.label("bbc")            -> "Auntie — the shipping forecast"
    characters.voice_label("bf_emma")  -> "Emma — British, higher, clear and close"
"""

# The treatments, as people. Nobody picks a voice by its compressor settings,
# and "BBC — even and measured" describes the signal path rather than who is
# talking. A name and where they are talking from is enough: you know what
# Auntie sounds like before you have heard her.
#
# The keys never change -- they are what settings.json and ::voice use, and
# they are the keys of voices.TREATMENTS.
NAMES = {
    "clean": ("Murmur", "plain, no colour"),
    "bbc": ("Auntie", "the shipping forecast"),
    "veronica": ("Veronica", "offshore, after dark"),
    "submarine": ("Abbey", "tape, wound a little slack"),
    "agent": ("The Informant", "a recorder in a coat pocket"),
}

# The order they are offered in, quietest colouring first.
ORDER = ["clean", "bbc", "veronica", "submarine", "agent"]


def label(name: str) -> str:
    """How a treatment is offered to whoever is choosing one."""
    who, where = NAMES.get(name, (name, ""))
    return f"{who} — {where}" if where else who


def catalogue() -> list[tuple[str, str]]:
    """(key, label) for every treatment, in the order they should be shown."""
    return [(key, label(key)) for key in ORDER]


# --------------------------------------------------------------- the singers

# Kokoro names its voices af_nicole, bf_emma, zm_yunjian. The first letter is
# the accent and the second is the voice's range, which is all the name is
# actually telling you -- so say that instead of making you learn it.
ACCENTS = {
    "a": "American", "b": "British", "e": "Spanish", "f": "French",
    "h": "Hindi", "i": "Italian", "j": "Japanese", "p": "Portuguese",
    "z": "Mandarin",
}
RANGES = {"f": "higher", "m": "lower"}

# What a voice is actually like, for the ones that have been sat and listened
# to. Nothing is invented here: an entry exists only where there is a note
# from having heard it. The rest get their accent and range, which is true,
# and the audition samples are how you find out the rest.
NOTES = {
    "bf_emma": "clear and close",
    "af_nicole": "breathy, with a rasp",
    "af_sky": "very soft, near ASMR",
    "af_heart": "warm, rounded",
    "bm_george": "dry, older",
    "am_michael": "plain, unhurried",
}


def voice_label(key: str) -> str:
    """A Kokoro voice, said in a way that tells you something.

        bf_emma  ->  Emma — British, higher, clear and close
        zm_yunxi ->  Yunxi — Mandarin, lower
    """
    who = key.split("_", 1)[-1].replace("_", " ").title()
    parts = [ACCENTS.get(key[:1], ""), RANGES.get(key[1:2], ""), NOTES.get(key, "")]
    said = ", ".join(part for part in parts if part)
    return f"{who} — {said}" if said else who


def singers(keys) -> list[tuple[str, str]]:
    """(key, label) for a list of voices, the described ones first.

    Six of the fifty-four have been listened to properly. Those are the ones
    worth offering first; the rest are in alphabetical order behind them.
    """
    keys = list(keys)
    known = [key for key in keys if key in NOTES]
    rest = sorted(key for key in keys if key not in NOTES)
    return [(key, voice_label(key)) for key in known + rest]
