"""Win32 window capture via PrintWindow.

Unlike screen capture, this grabs a specific window's own rendered content,
so it keeps working while the window is covered by other windows.
Limitation: Windows does not render minimized windows, so capture pauses
(callers should keep showing the last good frame).
"""

import ctypes
from ctypes import wintypes

from PIL import Image

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.BOOL]
user32.MoveWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                            wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                            wintypes.UINT]

# PW_CLIENTONLY | PW_RENDERFULLCONTENT: client area only, and force apps with
# GPU-composited content (browsers, editors) to actually render into our DC.
PW_FLAGS = 1 | 2
DWMWA_CLOAKED = 14
SW_RESTORE = 9
WM_CLOSE = 0x0010

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def virtual_screen_bounds():
    """(x, y, w, h) of the full virtual desktop across all monitors."""
    m = user32.GetSystemMetrics
    return (m(SM_XVIRTUALSCREEN), m(SM_YVIRTUALSCREEN),
            m(SM_CXVIRTUALSCREEN), m(SM_CYVIRTUALSCREEN))


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def _is_cloaked(hwnd) -> bool:
    """Hidden UWP/ghost windows report visible but are 'cloaked' by DWM."""
    cloaked = wintypes.DWORD(0)
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED,
                                 ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return bool(cloaked.value)


def list_windows() -> list[tuple[int, str]]:
    """Visible, titled, non-cloaked top-level windows as (hwnd, title)."""
    results: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or _is_cloaked(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        results.append((int(hwnd) if hwnd else 0, buf.value))
        return True

    user32.EnumWindows(callback, 0)
    return results


def find_window(title: str) -> int | None:
    """Best-effort re-find of a window by remembered title: exact match first,
    then case-insensitive containment either way (titles drift, e.g. editors
    append the open file name)."""
    if not title:
        return None
    windows = list_windows()
    for hwnd, t in windows:
        if t == title:
            return hwnd
    low = title.lower()
    for hwnd, t in windows:
        tl = t.lower()
        if low in tl or tl in low:
            return hwnd
    return None


def is_alive(hwnd) -> bool:
    return bool(user32.IsWindow(hwnd))


def get_window_pid(hwnd) -> int | None:
    """PID of the process that owns hwnd, or None on failure."""
    pid = wintypes.DWORD(0)
    if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
        return None
    return pid.value


def is_minimized(hwnd) -> bool:
    return bool(user32.IsIconic(hwnd))


def get_window_rect(hwnd) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) in screen coordinates, or None on failure."""
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def restore_window(hwnd):
    """Un-maximize/un-minimize so a subsequent move_window isn't fought by
    the window manager."""
    user32.ShowWindow(hwnd, SW_RESTORE)


def focus_window(hwnd):
    """Bring hwnd to the front and give it input focus. Windows normally
    blocks a background process from stealing focus, but that restriction
    doesn't apply here: this is only ever called from RegionOS's own window
    procedure in direct response to a real user click, which is exactly the
    condition Windows allows."""
    user32.SetForegroundWindow(hwnd)


def move_window(hwnd, x, y, w, h):
    return bool(user32.MoveWindow(hwnd, x, y, w, h, True))


def close_window(hwnd):
    """Best-effort: ask the window to close (like clicking its X)."""
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def grab_window(hwnd) -> Image.Image | None:
    """Capture a window's client area, even if occluded. None on failure."""
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w < 1 or h < 1:
        return None

    hdc = user32.GetDC(hwnd)
    if not hdc:
        return None
    mem_dc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem_dc, bmp)
    try:
        if not user32.PrintWindow(hwnd, mem_dc, PW_FLAGS):
            return None
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bmi), 0):
            return None
        return Image.frombuffer("RGB", (w, h), bytes(buf), "raw", "BGRX", 0, 1)
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hdc)
