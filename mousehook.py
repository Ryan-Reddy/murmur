"""A low-level mouse button hook that can actually be uninstalled.

The `mouse` package installs its WH_MOUSE_LL hook the first time anything is
hooked and never takes it down: unhook() just empties a handler list, while the
hook callback goes on building a MoveEvent for every WM_MOUSEMOVE and pushing it
through two queues to be handed to nobody. Measured against an identical process
with no hook, that costs about 8% of a core whenever the mouse is moving -- all
day, whether or not select mode is on.

Murmur only ever wants clicks. This hook ignores movement inside the callback,
where it is cheapest to ignore, and stops for real when select mode goes off.

    hook = MouseButtons(on_button)   # on_button("down"|"up"|"double", x, y)
    hook.start()
    hook.stop()
"""

import ctypes
import queue
import threading
from ctypes import wintypes

WH_MOUSE_LL = 14
WM_QUIT = 0x0012
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)

user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, WPARAM, LPARAM]
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]


class MouseButtons:
    """Reports left-button down / up / double, and nothing else."""

    def __init__(self, on_button):
        self._on_button = on_button
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ready = threading.Event()
        self._events: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        if self._thread is not None:
            return
        self._ready.clear()
        self._worker = threading.Thread(target=self._drain, daemon=True)
        self._worker.start()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2)

    def stop(self):
        thread, tid = self._thread, self._tid
        self._thread, self._tid = None, 0
        if thread is None:
            return
        # Breaks GetMessageW, after which the pump unhooks and exits.
        user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
        thread.join(timeout=2)
        self._events.put(None)  # retire the worker with it

    @property
    def running(self) -> bool:
        return self._thread is not None

    # -- internals ---------------------------------------------------------

    def _drain(self):
        """Handlers run here, never in the hook: a slow callback inside a
        WH_MOUSE_LL hook stalls mouse input for every app on the desktop."""
        while True:
            item = self._events.get()
            if item is None:
                return
            try:
                self._on_button(*item)
            except Exception:
                pass

    def _pump(self):
        double_click_ms = user32.GetDoubleClickTime()
        last_down = [0]

        def callback(code, wparam, lparam):
            # Movement is the overwhelming majority of what arrives here, so it
            # is rejected before anything is allocated or dereferenced.
            if code < 0 or wparam == WM_MOUSEMOVE:
                return user32.CallNextHookEx(None, code, wparam, lparam)
            if wparam in (WM_LBUTTONDOWN, WM_LBUTTONUP):
                info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                point = (info.pt.x, info.pt.y)
                if wparam == WM_LBUTTONDOWN:
                    now = info.time
                    double = now - last_down[0] <= double_click_ms
                    last_down[0] = now
                    self._events.put(("double" if double else "down",) + point)
                else:
                    self._events.put(("up",) + point)
            return user32.CallNextHookEx(None, code, wparam, lparam)

        proc = HOOKPROC(callback)  # must outlive the hook
        handle = user32.SetWindowsHookExW(WH_MOUSE_LL, proc, None, 0)
        self._tid = kernel32.GetCurrentThreadId()
        self._ready.set()
        if not handle:
            return
        try:
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnhookWindowsHookEx(handle)
