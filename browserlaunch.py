"""Opens a URL in a hidden, off-screen browser window for RegionOS to track.

Tracked windows run in a dedicated browser profile (--user-data-dir), never
the user's regular one, for two reasons:

  1. It's a genuinely separate process, so startup flags actually take
     effect. A plain `--new-window` against an already-running browser just
     IPCs the request into that existing process, which ignores flags on
     the command line since it's already running. All windows opened later
     for other regions reuse this same profile via that same IPC hand-off,
     so they inherit the flags too.
  2. It has its own Preferences file, so anything RegionOS does to these
     windows (moving them off-screen, closing them) can never leak into the
     window placement the user's own browser remembers.

The window is positioned outside the virtual desktop rather than minimized:
Windows still fully renders off-screen windows (only minimizing or covering
stops rendering), so the same PrintWindow capture used for normal
Application-window regions keeps working, while the window never appears
on screen or clutters the desktop -- except for a SLIVER_PX-wide sliver
deliberately left on-screen at the desktop edge (see push_offscreen):
measured directly, a window with *zero* on-screen pixels gets composited
by DWM at visibly lower fidelity for GPU-accelerated content (e.g. video
looks blurry) even though it keeps updating; a 1-2px sliver is enough to
keep it at full quality while remaining imperceptible. Each tracked
window's sliver is staggered vertically by offset_index (see
Region.slot) so simultaneously-hidden windows don't land on the exact
same coordinates and occlude each other -- which would undo the benefit
for whichever one ends up underneath. LAUNCH_FLAGS additionally disables
Chromium's occlusion-based pause, for the RegionOS-managed windows that
get relaunched with it (real, regular-profile browser windows tracked
directly can't have flags applied after the fact, which is exactly why
the sliver -- not just the flags -- is what keeps their quality up).
"""

import ctypes
from ctypes import wintypes
import os
import subprocess
import time
import winreg

import wincap

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

BROWSER_EXES = ("msedge.exe", "chrome.exe")
WINDOW_SIZE = (1280, 800)
SLIVER_PX = 2  # on-screen pixels left visible at the desktop edge -- confirmed
# by measurement to keep DWM compositing hidden windows at full quality
# (a fully off-screen window measured ~12% lower sharpness on video content).
SLIVER_Y_GAP = 20  # extra vertical gap between staggered windows' slots
FIND_TIMEOUT_S = 8.0
POLL_INTERVAL_S = 0.15
MIN_WINDOW_DIM = 300  # filters out small notification/dialog popups

PROFILE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "RegionOS", "BrowserProfile")
LAUNCH_FLAGS = (
    f"--user-data-dir={PROFILE_DIR}",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
)


def _owning_exe_path(hwnd) -> str | None:
    """Full path of the executable that owns hwnd, or None on failure."""
    pid = wincap.get_window_pid(hwnd)
    if not pid:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(260)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        kernel32.CloseHandle(handle)


def owning_exe_name(hwnd) -> str | None:
    """Lowercase basename of the executable owning hwnd, or None."""
    path = _owning_exe_path(hwnd)
    return os.path.basename(path).lower() if path else None


def is_browser_window(hwnd) -> bool:
    return owning_exe_name(hwnd) in BROWSER_EXES


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
    """True if the window is hidden at (or very near) its off-screen
    position -- i.e. not meaningfully on-screen, allowing for the small
    sliver push_offscreen deliberately leaves visible (see SLIVER_PX)."""
    rect = wincap.get_window_rect(hwnd)
    if rect is None:
        return False
    left, top, right, bottom = rect
    vx, vy, vw, vh = wincap.virtual_screen_bounds()
    overlap_w = max(0, min(right, vx + vw) - max(left, vx))
    overlap_h = max(0, min(bottom, vy + vh) - max(top, vy))
    return overlap_w <= SLIVER_PX or overlap_h <= SLIVER_PX


def push_offscreen(hwnd, offset_index: int = 0):
    """(Re-)position a window mostly outside the virtual desktop, leaving
    a SLIVER_PX-wide sliver on-screen at the right edge -- confirmed by
    measurement to keep DWM compositing GPU-accelerated content (video)
    at full quality, unlike a fully off-screen position. Browsers
    sometimes restore their own remembered window position shortly after
    creation, which can pull a hidden window back on screen; call this
    repeatedly (e.g. once per captured frame) to keep it enforced.

    offset_index staggers the vertical position (use a stable per-region
    index, e.g. Region.slot) so multiple simultaneously-hidden windows
    don't land on the exact same coordinates and occlude each other's
    sliver -- which would undo the quality benefit for whichever window
    ends up underneath."""
    vx, vy, vw, _ = wincap.virtual_screen_bounds()
    w, h = WINDOW_SIZE
    x = vx + vw - SLIVER_PX
    y = vy + offset_index * (h + SLIVER_Y_GAP)
    wincap.move_window(hwnd, x, y, w, h)


ONSCREEN_POSITION = (60, 60)


def bring_onscreen(hwnd):
    """Move a hidden or covered tracked window onto the primary monitor and
    focus it, so the user can use it directly -- any window-mode region,
    not just RegionOS-managed websites. The capture loop checks
    WindowCaptureWorker.pinned_onscreen and skips re-hiding it while this
    is in effect; push_offscreen (via the same flag) puts it back."""
    w, h = WINDOW_SIZE
    wincap.restore_window(hwnd)
    wincap.move_window(hwnd, *ONSCREEN_POSITION, w, h)
    wincap.focus_window(hwnd)


def restore_to_visible(hwnd, offset_index: int = 0):
    """Move a hidden window back on-screen without stealing focus -- a
    safety net so a window RegionOS never spawned (a drag-tracked app, or
    a real, regular-profile browser) is never left stranded off-screen
    once nothing tracks it anymore (region deleted, or RegionOS exiting).
    This matters most for real browser windows: closing one normally
    while it's still off-screen risks that position getting saved into
    the user's own profile, so every code path that stops tracking a
    non-managed window must route through this first. offset_index
    cascades the landing spot so restoring several at once doesn't stack
    them exactly on top of each other."""
    w, h = WINDOW_SIZE
    x = ONSCREEN_POSITION[0] + offset_index * 40
    y = ONSCREEN_POSITION[1] + offset_index * 40
    wincap.restore_window(hwnd)
    wincap.move_window(hwnd, x, y, w, h)


def launch_offscreen(url: str, browser_exe: str | None = None,
                      offset_index: int = 0) -> tuple[int, str] | None:
    """Opens url in a new, hidden browser window. Blocks for up to
    FIND_TIMEOUT_S while the window appears. Returns (hwnd, title), or None
    if no browser is installed or the window couldn't be located.
    offset_index is forwarded to push_offscreen -- see its docstring."""
    browser_exe = browser_exe or find_browser()
    if not browser_exe:
        return None

    os.makedirs(PROFILE_DIR, exist_ok=True)
    before = {hwnd for hwnd, _ in wincap.list_windows()}
    try:
        subprocess.Popen([browser_exe, *LAUNCH_FLAGS, "--new-window", url])
    except OSError:
        return None

    hwnd = None
    deadline = time.monotonic() + FIND_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_S)
        new = [h for h, _ in wincap.list_windows() if h not in before]
        # Require the window to actually belong to the browser process (by
        # exe path, not just PID: an already-running browser handles
        # --new-window via IPC in its own pre-existing process). Without
        # this check, any unrelated window that happens to appear during
        # the poll — even RegionOS's own dashboard — could be mistaken for
        # the new browser window. Also require a real window-sized rect,
        # since the browser can pop up small owned windows of its own around
        # the same time (e.g. a "Restore pages" prompt after an unclean
        # shutdown) that would otherwise pass the exe-path check too.
        matches = [h for h in new
                   if (path := _owning_exe_path(h)) and path.lower() == browser_exe.lower()
                   and (rect := wincap.get_window_rect(h))
                   and rect[2] - rect[0] >= MIN_WINDOW_DIM and rect[3] - rect[1] >= MIN_WINDOW_DIM]
        if matches:
            hwnd = matches[0]
            break
    if not hwnd:
        return None

    time.sleep(0.5)  # let the title settle from "about:blank" to the page title
    title = next((t for h, t in wincap.list_windows() if h == hwnd), "")

    wincap.restore_window(hwnd)
    push_offscreen(hwnd, offset_index)

    return hwnd, title
