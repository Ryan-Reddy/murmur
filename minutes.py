"""A transcript plus what you did to it while it was happening.

`listener.Transcript` records what was said. This records what you thought
about it at the time: which lines to come back to, and the note you typed
while somebody was still talking. Both are worth more during the meeting than
after it, which is why they have to cost one click and one keystroke.

Nothing here draws anything or opens a socket -- `bubbler.py` shows it and
`meeting.py` wires it up. Keeping it apart is what lets the interesting half
be tested without a window, and lets the assistant drive the same actions over
the port that the window drives with the mouse.
"""

import json
from dataclasses import dataclass
from typing import Optional

from listener import Line, Transcript, _stamp

# A mark means "come back to this", not "this is important" -- you are marking
# it because you cannot deal with it now.
CHECK = "[!]"
NOTE = "note:"


@dataclass
class Note:
    """Something you wrote down. `about` is the line that was in the air when
    you started typing, which is nearly always what the note is about."""
    at: float
    text: str
    about: Optional[Line] = None


class Minutes:

    def __init__(self):
        self.transcript = Transcript()
        self.notes: list[Note] = []
        # Marks are held against the Line object itself rather than its
        # position: the far end is transcribed independently and lands out of
        # order, so an index would quietly slide onto somebody else's
        # sentence. Line is frozen, so it hashes by value and stays findable.
        self._checked: set[Line] = set()
        # One number that moves whenever anything worth drawing has changed.
        # The pill is in another process and has to poll; without this it
        # would drag the whole transcript across every half-second for an hour
        # to find out that nobody had said anything.
        self.version = 0

    # ------------------------------------------------------------- writing

    @property
    def lines(self) -> list[Line]:
        return self.transcript.lines

    def add(self, line: Line) -> Optional[Line]:
        """Take a line, or a better hearing of one already here.

        When a line is rewritten by the slower model, everything pointing at
        the old wording is moved across: the mark you put on it, and any note
        you hung off it. Getting this wrong would not raise -- it would
        silently drop the only work you did during the meeting, which is the
        one failure this whole window exists to prevent.
        """
        was = None
        if line.id is not None:
            was = next((l for l in self.transcript.lines if l.id == line.id), None)
        got = self.transcript.add(line)
        if got is not None:
            self.version += 1
        if got is not None and was is not None and was is not got:
            if was in self._checked:
                self._checked.discard(was)
                self._checked.add(got)
            for note in self.notes:
                if note.about is was:
                    note.about = got
        return got

    def latest(self) -> Optional[Line]:
        """What was said most recently -- not the last in the transcript,
        which may be a late arrival from the other source."""
        return max(self.lines, key=lambda l: l.start, default=None)

    def check(self, line: Optional[Line] = None) -> Optional[Line]:
        """Mark a line, or unmark it if it was already marked. With no line,
        the one that just went past."""
        line = line or self.latest()
        if line is None:
            return None
        self._checked.symmetric_difference_update({line})
        self.version += 1
        return line

    def checked(self) -> list[Line]:
        return sorted(self._checked, key=lambda l: l.start)

    def note(self, text: str, at: Optional[float] = None) -> Optional[Note]:
        """Attach a note to whatever was being said. `at` is when you wrote
        it; the line it hangs off is the one you were reacting to."""
        text = (text or "").strip()
        if not text:
            return None
        about = self.latest()
        when = at if at is not None else (about.start if about else 0.0)
        made = Note(at=when, text=text, about=about)
        self.notes.append(made)
        self.version += 1
        return made

    # ------------------------------------------------------------ reading

    def summary(self) -> str:
        return (f"{len(self.lines)} lines, {len(self._checked)} to check, "
                f"{len(self.notes)} notes")

    def render(self, marked_only: bool = False) -> str:
        """The document you are left with: every line in the order it was
        said, your marks on it, and your notes under the line that prompted
        them."""
        under: dict[Optional[Line], list[Note]] = {}
        for note in self.notes:
            under.setdefault(note.about, []).append(note)

        entries: list[tuple[float, int, str]] = []
        for line in self.lines:
            notes = under.get(line, [])
            if marked_only and line not in self._checked and not notes:
                continue
            mark = f"  {CHECK}" if line in self._checked else ""
            suspect = "  [?]" if line.suspect else ""
            entries.append((line.start, 0,
                            f"[{_stamp(line.start)}] {line.source}: "
                            f"{line.text}{suspect}{mark}"))
            for note in notes:
                entries.append((line.start, 1, f"          {NOTE} {note.text}"))
        for note in under.get(None, []):
            entries.append((note.at, 0,
                            f"[{_stamp(note.at)}] {NOTE} {note.text}"))

        entries.sort(key=lambda e: (e[0], e[1]))
        return "\n".join(text for _, _, text in entries)

    def save(self, path) -> None:
        from pathlib import Path

        body = self.render()
        Path(path).write_text(f"{self.summary()}\n\n{body}\n", encoding="utf-8")

    def by_id(self, line_id) -> Optional[Line]:
        return next((l for l in self.lines if l.id == line_id), None)

    def dump(self) -> dict:
        """Everything the pill draws, in one answer.

        Lines carry their id rather than their position, because the second
        pass rewrites them in place and a position would be stale by the time
        the pill acted on it.
        """
        return {
            "version": self.version,
            "lines": [{"id": l.id, "at": l.start, "source": l.source,
                       "text": l.text, "checked": l in self._checked,
                       "suspect": l.suspect}
                      for l in self.lines],
            "notes": [{"at": n.at, "text": n.text,
                       "about": n.about.id if n.about is not None else None}
                      for n in self.notes],
        }

    # ----------------------------------------------------------- commands

    def command(self, name: str, arg: str) -> str:
        """The window and the assistant reach the same actions this way.

        It exists because testing a window by firing real keystrokes and
        clicks at somebody's desktop is not acceptable -- it moves their
        pointer and types into whatever they had focused. Same reason cufflink
        grew its `::` vocabulary.
        """
        arg = (arg or "").strip()
        if name == "note":
            made = self.note(arg)
            return f"noted: {made.text}" if made else "nothing to note"
        if name in ("check", "uncheck"):
            line = self.latest()
            if arg:
                if not arg.isdigit() or not 1 <= int(arg) <= len(self.lines):
                    return f"no line {arg}"
                line = self.lines[int(arg) - 1]
            if line is None:
                return "nothing said yet"
            self.check(line)
            state = "checked" if line in self._checked else "unchecked"
            return f"{state}: {line.text[:60]}"
        if name == "dump":
            return json.dumps(self.dump())
        if name == "mark":
            line = self.by_id(int(arg)) if arg.lstrip("-").isdigit() else None
            if line is None:
                return f"no line {arg}"
            self.check(line)
            state = "checked" if line in self._checked else "unchecked"
            return f"{state}: {line.text[:60]}"
        if name == "lines":
            wanted = int(arg) if arg.isdigit() else 20
            shown = self.lines[-wanted:]
            return "\n".join(
                f"{self.lines.index(l) + 1}. [{_stamp(l.start)}] {l.source}: {l.text}"
                for l in shown) or "nothing said yet"
        if name == "notes":
            return "\n".join(f"[{_stamp(n.at)}] {n.text}" for n in self.notes) \
                or "no notes"
        if name == "marked":
            return self.render(marked_only=True) or "nothing marked"
        if name == "status":
            return self.summary()
        return f"? unknown command: {name}"
