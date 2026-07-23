"""Per-region capture workers.

Each region gets its own daemon thread. Screen regions use mss (one instance
per thread — mss is not thread-safe across threads); window regions use
wincap.PrintWindow, which keeps capturing a window even when it is covered.
The worker continuously grabs at the region's FPS and stores the latest
frame; the UI pulls frames on its own schedule.
"""

import threading
import time

import mss
from mss.exception import ScreenShotError
from PIL import Image

import browserlaunch
import wincap

RELAUNCH_COOLDOWN_S = 5.0


class BaseWorker(threading.Thread):
    def __init__(self, region):
        super().__init__(daemon=True, name=f"capture-{region.name}")
        self.region = region
        self._frame: Image.Image | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.measured_fps = 0.0
        self.note = ""  # non-empty = warning shown in the UI status

    def latest_frame(self) -> Image.Image | None:
        with self._lock:
            return self._frame

    def _store(self, img: Image.Image):
        with self._lock:
            self._frame = img

    def stop(self):
        self._stop.set()


class ScreenCaptureWorker(BaseWorker):
    """Captures a fixed rectangle of the screen."""

    def run(self):
        with mss.mss() as sct:
            while not self._stop.is_set():
                r = self.region
                if r.paused or r.w < 1 or r.h < 1:
                    time.sleep(0.2)
                    continue
                start = time.perf_counter()
                try:
                    shot = sct.grab({"left": r.x, "top": r.y, "width": r.w, "height": r.h})
                    self._store(Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"))
                except ScreenShotError:
                    time.sleep(0.5)
                    continue
                elapsed = time.perf_counter() - start
                self.measured_fps = 1.0 / max(elapsed, 1e-6)
                time.sleep(max(1.0 / r.fps - elapsed, 0.001))


class WindowCaptureWorker(BaseWorker):
    """Captures an application window's content, even when the window is
    covered by other windows. Minimized windows are not rendered by Windows,
    so the last good frame is kept and a note is set."""

    def __init__(self, region, hwnd: int | None = None):
        super().__init__(region)
        self.hwnd = hwnd
        self._last_relaunch = 0.0
        self.pinned_onscreen = False  # user brought a hidden window forward to use it

    def invalidate(self):
        """Force a re-find of the window (after Reselect)."""
        self.hwnd = None

    def run(self):
        while not self._stop.is_set():
            r = self.region
            if r.paused:
                time.sleep(0.2)
                continue
            if not self.hwnd or not wincap.is_alive(self.hwnd):
                self.hwnd = wincap.find_window(r.window_title)
                if not self.hwnd and r.url:
                    now = time.monotonic()
                    if now - self._last_relaunch >= RELAUNCH_COOLDOWN_S:
                        self._last_relaunch = now
                        self.note = "Reopening hidden window..."
                        found = browserlaunch.launch_offscreen(r.url, offset_index=r.slot)
                        if found:
                            self.hwnd, r.window_title = found
                    else:
                        time.sleep(0.5)
                        continue
                if not self.hwnd:
                    self.note = "Window not found"
                    time.sleep(1.0)
                    continue
            if wincap.is_minimized(self.hwnd):
                # Windows doesn't render minimized windows, so capture would
                # otherwise freeze on the last frame. Tracked windows are
                # never supposed to sit minimized -- whether the user just
                # minimized one they'd brought on-screen ("I'm done with
                # it") or minimized one out of habit -- so un-minimize and
                # push it off-screen instead: capture stays live, and the
                # desktop stays just as clear as if it had stayed minimized.
                self.pinned_onscreen = False
                wincap.restore_window(self.hwnd)
                browserlaunch.push_offscreen(self.hwnd, offset_index=r.slot)
                continue
            if r.url and not self.pinned_onscreen and not browserlaunch.is_offscreen(self.hwnd):
                # The browser re-asserted its own remembered window bounds;
                # keep the hidden window actually hidden. Skipped while
                # pinned_onscreen -- the user brought it forward on purpose.
                browserlaunch.push_offscreen(self.hwnd, offset_index=r.slot)
            start = time.perf_counter()
            img = wincap.grab_window(self.hwnd)
            if img is None:
                self.note = "Capture failed"
                time.sleep(0.5)
                continue
            if r.crop:
                cx, cy, cw, ch = r.crop
                if cx < img.width and cy < img.height:
                    img = img.crop((cx, cy, min(cx + cw, img.width),
                                    min(cy + ch, img.height)))
            self._store(img)
            self.note = ""
            elapsed = time.perf_counter() - start
            self.measured_fps = 1.0 / max(elapsed, 1e-6)
            time.sleep(max(1.0 / r.fps - elapsed, 0.001))


def make_worker(region) -> BaseWorker:
    if region.mode == "window":
        return WindowCaptureWorker(region)
    return ScreenCaptureWorker(region)
