"""Opens a URL in a hidden, off-screen browser window for RegionOS to track.

The window is positioned outside the virtual desktop rather than minimized:
Windows still fully renders off-screen windows (only minimizing or covering
stops rendering), so the same PrintWindow capture used for normal
Application-window regions keeps working, while the window never appears
on screen or clutters the desktop.
"""

import subprocess
import time
import winreg

import wincap

BROWSER_EXES = ("msedge.exe", "chrome.exe")
WINDOW_SIZE = (1280, 800)
OFFSCREEN_MARGIN = 50
FIND_TIMEOUT_S = 8.0
POLL_INTERVAL_S = 0.15


def find_browser() -> str | None:
    """Locate an installed Chromium browser via the registry App Paths key
    (works regardless of install location)."""
    for exe in BROWSER_EXES:
        path = _app_path(exe)
        if path:
            return path
    return None


def _app_path(exe: str) -> str | None:
    key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "")
                return value
        except OSError:
            continue
    return None


def is_offscreen(hwnd) -> bool:
    """True if the window doesn't overlap the virtual desktop at all."""
    rect = wincap.get_window_rect(hwnd)
    if rect is None:
        return False
    left, top, right, bottom = rect
    vx, vy, vw, vh = wincap.virtual_screen_bounds()
    return right <= vx or left >= vx + vw or bottom <= vy or top >= vy + vh


def push_offscreen(hwnd):
    """(Re-)position a window outside the virtual desktop. Browsers
    sometimes restore their own remembered window position shortly after
    creation, which can pull a hidden window back on screen; call this
    repeatedly (e.g. once per captured frame) to keep it enforced."""
    vx, vy, vw, _ = wincap.virtual_screen_bounds()
    w, h = WINDOW_SIZE
    wincap.move_window(hwnd, vx + vw + OFFSCREEN_MARGIN, vy, w, h)


def launch_offscreen(url: str, browser_exe: str | None = None) -> tuple[int, str] | None:
    """Opens url in a new, hidden browser window. Blocks for up to
    FIND_TIMEOUT_S while the window appears. Returns (hwnd, title), or None
    if no browser is installed or the window couldn't be located."""
    browser_exe = browser_exe or find_browser()
    if not browser_exe:
        return None

    before = {hwnd for hwnd, _ in wincap.list_windows()}
    try:
        subprocess.Popen([browser_exe, "--new-window", url])
    except OSError:
        return None

    hwnd = None
    deadline = time.monotonic() + FIND_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_S)
        new = [h for h, _ in wincap.list_windows() if h not in before]
        if new:
            hwnd = new[0]
            break
    if not hwnd:
        return None

    time.sleep(0.5)  # let the title settle from "about:blank" to the page title
    title = next((t for h, t in wincap.list_windows() if h == hwnd), "")

    wincap.restore_window(hwnd)
    push_offscreen(hwnd)

    return hwnd, title
