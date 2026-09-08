"""Read and mark up a running meeting, over the port.

    python meeting_tool.py lines 20
    python meeting_tool.py note "ask who signs off on the installer"
    python meeting_tool.py check
    python meeting_tool.py marked
    python meeting_tool.py save

For the assistant to use while `meeting.py` is running: it can catch up on
what has been said, leave a note in the transcript, or mark the line that just
went past -- without touching the keyboard of somebody who is in a meeting.

Arm's length over a socket, stdlib only, nothing imported from cufflink. Prints
"not running" and exits 1 when there is no meeting, so a caller can tell the
difference between an empty meeting and no meeting.
"""

import socket
import sys

MEETING_PORT = 52720
TIMEOUT = 3.0


def ask(command: str) -> str:
    with socket.create_connection(("127.0.0.1", MEETING_PORT), timeout=TIMEOUT) as conn:
        conn.sendall(command.encode("utf-8"))
        conn.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            block = conn.recv(65536)
            if not block:
                return b"".join(chunks).decode("utf-8", "replace")
            chunks.append(block)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    command = "::" + argv[0] + (" " + " ".join(argv[1:]) if argv[1:] else "")
    try:
        print(ask(command))
    except (ConnectionRefusedError, OSError):
        print("not running -- start it with: python meeting.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
