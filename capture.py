"""Per-region screen capture workers.

Each region gets its own daemon thread with its own mss instance
(mss is not thread-safe across threads). The worker continuously grabs
the region's rectangle at the region's FPS and stores the latest frame;
the UI pulls frames on its own schedule.
"""

import threading
import time

import mss
from mss.exception import ScreenShotError
from PIL import Image


class CaptureWorker(threading.Thread):
    def __init__(self, region):
        super().__init__(daemon=True, name=f"capture-{region.name}")
        self.region = region
        self._frame: Image.Image | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.measured_fps = 0.0

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
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    with self._lock:
                        self._frame = img
                except ScreenShotError:
                    time.sleep(0.5)
                    continue
                elapsed = time.perf_counter() - start
                self.measured_fps = 1.0 / max(elapsed, 1e-6)
                time.sleep(max(1.0 / r.fps - elapsed, 0.001))

    def latest_frame(self) -> Image.Image | None:
        with self._lock:
            return self._frame

    def stop(self):
        self._stop.set()
