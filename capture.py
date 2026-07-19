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

import wincap


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
                if not self.hwnd:
                    self.note = "Window not found"
                    time.sleep(1.0)
                    continue
            if wincap.is_minimized(self.hwnd):
                self.note = "Minimized — showing last frame"
                time.sleep(0.5)
                continue
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
