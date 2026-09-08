"""Turn the Claude Code integration on and off from inside cufflink.

Speaking Claude's notifications has worked for a while, but only if you found
the instructions in the README and hand-edited ~/.claude/settings.json. That
means approximately nobody has it. This makes it a switch.

    import claudehook
    claudehook.status()    -> "on" | "off" | "no-claude-code" | "unreadable"
    claudehook.enable()    -> (True, "message")
    claudehook.disable()   -> (True, "message")

Everything here writes to another application's configuration file, so:

  * it merges, never replaces -- a real settings.json already has hooks from
    other tools in the same arrays, and clobbering those would be rude and
    invisible;
  * it backs the file up first, with a timestamp;
  * it removes only entries it recognises as its own;
  * it writes to a temporary file and moves it into place, so an interrupted
    write cannot leave a half-written config behind.

Stdlib only, and no import of anything expensive: the settings window asks
for status while the model may still be loading.
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

# The events claude_notify.py knows how to handle.
EVENTS = ("Notification", "Stop", "PostToolUse")

# How long a hook may take before Claude Code gives up on it. Ten seconds is
# generous for "open a socket and write to it", and the point of a timeout is
# that a broken hook never wedges the session.
TIMEOUT = 10


def config_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def notifier_path() -> Path:
    """The hook script, wherever this copy of cufflink keeps it."""
    root = Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else Path(__file__).parent
    return root / "integrations" / "claude-code" / "claude_notify.py"


def _command() -> dict:
    """The hook entry to install.

    Frozen, there is no python.exe to point at, so the exe runs itself in hook
    mode. From source it is the interpreter that is running us -- not "python",
    which may be a different one or absent from PATH entirely.
    """
    if getattr(sys, "frozen", False):
        return {
            "type": "command",
            "command": sys.executable,
            "args": ["--claude-hook"],
            "timeout": TIMEOUT,
            "async": True,
        }
    return {
        "type": "command",
        "command": sys.executable,
        "args": [str(notifier_path())],
        "timeout": TIMEOUT,
        "async": True,
    }


def _is_ours(entry) -> bool:
    """Recognise our own hook however it was installed.

    Matched on the text rather than on equality, because the interpreter path,
    the timeout and the exact spelling all change between installs -- and
    removing someone else's hook would be much worse than leaving one of ours.
    """
    if not isinstance(entry, dict):
        return False
    blob = " ".join(str(entry.get(k, "")) for k in ("command", "args"))
    blob += " ".join(str(a) for a in entry.get("args", []) if isinstance(a, str))
    return "claude_notify" in blob or "--claude-hook" in blob


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return None  # present but unreadable: never overwrite it


def _write(path: Path, data: dict) -> None:
    """Back up, write beside, move into place."""
    if path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_suffix(f".json.{stamp}.bak"))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.cufflink-tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def status() -> str:
    path = config_path()
    if not path.parent.exists():
        return "no-claude-code"
    settings = _read(path)
    if settings is None:
        return "unreadable"
    hooks = settings.get("hooks") or {}
    for event in EVENTS:
        for group in hooks.get(event) or []:
            if any(_is_ours(h) for h in (group or {}).get("hooks") or []):
                return "on"
    return "off"


def enable():
    path = config_path()
    if not path.parent.exists():
        return False, "Claude Code is not installed here (~/.claude is missing)."
    settings = _read(path)
    if settings is None:
        return False, "~/.claude/settings.json could not be read; left alone."
    if not getattr(sys, "frozen", False) and not notifier_path().exists():
        return False, f"{notifier_path().name} is missing from this install."

    hooks = settings.setdefault("hooks", {})
    added = 0
    for event in EVENTS:
        groups = hooks.setdefault(event, [])
        if any(_is_ours(h) for g in groups for h in (g or {}).get("hooks") or []):
            continue
        groups.append({"hooks": [_command()]})
        added += 1
    if not added:
        return True, "Already on."
    try:
        _write(path, settings)
    except Exception as error:
        return False, f"Could not write the Claude Code settings: {error}"
    return True, ("Claude Code will speak through cufflink. "
                  "Start a new session for it to take effect.")


def disable():
    path = config_path()
    settings = _read(path)
    if settings is None:
        return False, "~/.claude/settings.json could not be read; left alone."
    hooks = settings.get("hooks") or {}
    removed = 0
    for event in EVENTS:
        groups = hooks.get(event)
        if not groups:
            continue
        kept = []
        for group in groups:
            others = [h for h in (group or {}).get("hooks") or [] if not _is_ours(h)]
            removed += len(((group or {}).get("hooks") or [])) - len(others)
            # A group that held only our hook goes entirely; one that held
            # someone else's keeps theirs.
            if others:
                group["hooks"] = others
                kept.append(group)
            elif not (group or {}).get("hooks"):
                kept.append(group)
        hooks[event] = kept
    if not removed:
        return True, "Already off."
    try:
        _write(path, settings)
    except Exception as error:
        return False, f"Could not write the Claude Code settings: {error}"
    return True, "Claude Code will stop speaking. Start a new session."


def run_as_hook() -> int:
    """`cufflink.exe --claude-hook`: be the hook, for the packaged build.

    Frozen there is no interpreter to hand Claude Code, so the exe stands in
    for one and runs the notifier itself.
    """
    import runpy

    script = notifier_path()
    if not script.exists():
        return 1
    sys.argv = [str(script)]
    runpy.run_path(str(script), run_name="__main__")
    return 0
