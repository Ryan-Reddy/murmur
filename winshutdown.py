"""Answer Windows' session-end broadcast so Murmur never blocks a shutdown.

Murmur has no ordinary window: the pill is a borderless topmost widget that is
withdrawn most of the time, and everything else lives in the tray. When Windows
logs off or shuts down it broadcasts WM_QUERYENDSESSION to top-level windows and
waits for each to answer; with nothing listening, Murmur only gets torn down
after the timeout expires -- and meanwhile the user sees the "this app is
preventing you from shutting down" screen. Worse, the global keyboard and mouse
hooks stay installed the whole time, so input stays laggy while Windows waits.

Registering a hidden window that consents at once, then drops the hooks and
exits when the session really ends, keeps that screen from ever appearing.
"""

import ctypes
import os
import threading
from ctypes import wintypes

WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002

# 64-bit Windows widens these beyond the wintypes definitions; c_ssize_t and
# c_size_t follow the pointer width on both architectures.
LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", WPARAM),
        ("lParam", LPARAM),
        ("time", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
    ]


def install(on_shutdown) -> None:
    """Run a hidden listener window that calls on_shutdown when Windows ends the
    session, then exits the process. Starts its own thread and returns at once.
    """

    def run():
        def wndproc(hwnd, message, wparam, lparam):
            if message == WM_QUERYENDSESSION:
                # Consent, but change nothing yet: another app may still veto
                # the shutdown, and a Murmur left without its hooks would look
                # alive in the tray while every hotkey silently did nothing.
                return 1
            if message == WM_ENDSESSION and not wparam:
                return 0  # the shutdown was called off after all
            if message in (WM_ENDSESSION, WM_CLOSE, WM_DESTROY):
                try:
                    on_shutdown()
                except Exception:
                    pass
                # Nothing here is worth unwinding, and an orderly exit can still
                # block on the audio device or on joining a worker.
                os._exit(0)
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        # Both the callback and the class must outlive the window, or the
        # message loop calls into freed memory.
        proc = WNDPROC(wndproc)
        wndclass = WNDCLASS()
        wndclass.lpfnWndProc = proc
        wndclass.hInstance = kernel32.GetModuleHandleW(None)
        wndclass.lpszClassName = "MurmurShutdownListener"
        if not user32.RegisterClassW(ctypes.byref(wndclass)):
            return
        hwnd = user32.CreateWindowExW(
            0, wndclass.lpszClassName, "Murmur", 0, 0, 0, 0, 0,
            None, None, wndclass.hInstance, None,
        )
        if not hwnd:
            return
        message = MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    threading.Thread(target=run, daemon=True, name="murmur-shutdown").start()
