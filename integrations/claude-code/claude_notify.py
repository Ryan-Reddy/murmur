"""Speak Claude Code's notifications through cufflink.

A Claude Code hook for the Notification, Stop and PostToolUse events. It
reads the hook's JSON from stdin, turns it into one spoken line and posts
it to cufflink's text-in port in cufflink's "claude" voice, so an interruption
sounds like one. cufflink says things like:

    my-project: Claude needs your permission to use Bash
    my-project: Claude is done. Tests pass. Want me to open the PR?
    my-project: Still working: editing main.py.

Stop reads Claude's last message from the session transcript the hook is
handed and speaks its opening (markdown stripped, capped) plus its closing
question, so you hear what's going on, not just that it stopped.
PostToolUse fires after every tool call, so it is throttled: nothing until
a turn has run TURN_WARMUP seconds, then at most every NARRATE_EVERY
seconds per session, saying what Claude just did. If cufflink isn't running,
the connection fails and nothing is said. Every spoken call leaves a line
in claude_notify.log next to this file, because a hook fails invisibly.
Stdlib only — wire it up in ~/.claude/settings.json (see README)."""

import datetime
import json
import re
import socket
import sys
import time
from pathlib import Path

CUFFLINK_PORT = 52719
# Claude gets a voice of its own, so an interruption is recognisable before
# you have parsed a word of it. An older cufflink, or one whose settings have no
# profile by this name, ignores the line and reads in the everyday voice.
PROFILE = "claude"
LOG = Path(__file__).with_name("claude_notify.log")
STATE = Path(__file__).with_name("claude_notify.state.json")
SUMMARY_CHARS = 300
TURN_WARMUP = 60
NARRATE_EVERY = 180

_MARKDOWN = [
    (re.compile(r"```.*?```", re.S), " "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"^\s{0,3}(#{1,6}\s*|[-*+]\s+|\d+\.\s+|>\s*|\|.*$)", re.M), ""),
    (re.compile(r"(?<!\w)[*_]+|[*_]+(?!\w)"), ""),
    (re.compile(r"\s+"), " "),
]


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError) as exc:
        _log("?", f"unreadable input: {exc}")
        return
    event = data.get("hook_event_name", "")
    session = data.get("session_id", "")
    if event == "Stop":
        message = "Claude is done."
        summary = _spoken(_last_assistant_text(data.get("transcript_path", "")))
        if summary:
            message += " " + summary
        _end_turn(session)
    elif event == "PostToolUse":
        message = _progress(session, data)
        if not message:
            return
    else:
        message = (data.get("message") or data.get("title") or "").strip()
        if not message:
            _log(event, "nothing to say")
            return
    project = Path(data.get("cwd") or "").name
    text = f"{project}: {message}" if project else message
    # The project folder name is also what editors put in their window title
    # -- "file.py - murmur - Visual Studio Code" -- so it is enough for
    # cufflink to find the window this came from and offer a way back to it.
    headers = f"::as {PROFILE}\n"
    if project:
        headers += f"::from {project}\n"
    try:
        with socket.create_connection(("127.0.0.1", CUFFLINK_PORT), timeout=1) as conn:
            conn.sendall(f"{headers}{text}".encode("utf-8"))
        _log(event, f"sent: {text}")
    except OSError:
        _log(event, f"dropped: {text}")


def _last_assistant_text(transcript: str) -> str:
    try:
        lines = Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "assistant":
            continue
        content = entry.get("message", {}).get("content", [])
        if isinstance(content, str):
            text = content
        else:
            text = " ".join(block.get("text", "") for block in content
                            if isinstance(block, dict) and block.get("type") == "text")
        if text.strip():
            return text
    return ""


def _spoken(text: str) -> str:
    for pattern, replacement in _MARKDOWN:
        text = pattern.sub(replacement, text)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    if not sentences:
        return ""
    summary = ""
    for sentence in sentences:
        if summary and len(summary) + len(sentence) > SUMMARY_CHARS:
            break
        summary = f"{summary} {sentence}".strip()
    last = sentences[-1]
    if last.endswith("?") and last not in summary and len(last) < SUMMARY_CHARS:
        summary += " " + last
    return summary[:SUMMARY_CHARS * 2]


def _progress(session: str, data: dict) -> str:
    now = time.time()
    state = _load_state()
    turn = state.setdefault(session, {})
    turn.setdefault("turn_started", now)
    turn["seen"] = now
    due = (now - turn["turn_started"] >= TURN_WARMUP
           and now - turn.get("last_spoken", 0) >= NARRATE_EVERY)
    if due:
        turn["last_spoken"] = now
    _save_state(state)
    return f"Still working: {_activity(data)}." if due else ""


def _activity(data: dict) -> str:
    tool = data.get("tool_name", "")
    inp = data.get("tool_input") or {}
    path = inp.get("file_path") or inp.get("notebook_path")
    if tool in ("Edit", "Write", "NotebookEdit") and path:
        return f"editing {Path(path).name}"
    if tool == "Read" and path:
        return f"reading {Path(path).name}"
    if tool in ("Bash", "PowerShell"):
        return (inp.get("description") or "running a command").rstrip(".")
    if tool in ("Grep", "Glob"):
        return "searching the code"
    if tool == "Agent":
        return "delegating to an agent"
    if tool in ("WebFetch", "WebSearch"):
        return "looking something up online"
    if tool == "Monitor":
        return "waiting on something to finish"
    return f"using {tool}" if tool else "thinking"


def _end_turn(session: str) -> None:
    state = _load_state()
    state.get(session, {}).pop("turn_started", None)
    _save_state(state)


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    cutoff = time.time() - 86400
    state = {k: v for k, v in state.items() if v.get("seen", 0) >= cutoff or "turn_started" in v}
    STATE.write_text(json.dumps(state), encoding="utf-8")


def _log(event: str, line: str) -> None:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG.open("a", encoding="utf-8") as log:
        log.write(f"{stamp} {event} {line}\n")


if __name__ == "__main__":
    main()
