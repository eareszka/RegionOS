"""Fullscreen drag-to-select overlay for creating/reselecting a region."""

import ctypes
import tkinter as tk

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def virtual_screen_bounds():
    """(x, y, w, h) of the full virtual desktop across all monitors."""
    m = ctypes.windll.user32.GetSystemMetrics
    return (m(SM_XVIRTUALSCREEN), m(SM_YVIRTUALSCREEN),
            m(SM_CXVIRTUALSCREEN), m(SM_CYVIRTUALSCREEN))


class RegionSelector(tk.Toplevel):
    """Dimmed overlay covering every monitor. Drag a rectangle; on release,
    calls on_select(x, y, w, h) in absolute screen coordinates. Esc cancels."""

    def __init__(self, master, on_select):
        super().__init__(master)
        self.on_select = on_select
        self.vx, self.vy, vw, vh = virtual_screen_bounds()

        self.overrideredirect(True)
        self.geometry(f"{vw}x{vh}+{self.vx}+{self.vy}")
        self.attributes("-alpha", 0.35)
        self.attributes("-topmost", True)
        self.configure(bg="black")

        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            vw // 2, 60, text="Drag a box over the area to capture  —  Esc to cancel",
            fill="#cccccc", font=("Segoe UI", 16),
        )

        self._start = None
        self._rect = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.bind("<Escape>", lambda e: self.destroy())
        self.focus_force()

    def _press(self, e):
        self._start = (e.x, e.y)
        self._rect = self.canvas.create_rectangle(
            e.x, e.y, e.x, e.y, outline="#ff4444", width=2
        )

    def _drag(self, e):
        if self._rect and self._start:
            self.canvas.coords(self._rect, *self._start, e.x, e.y)

    def _release(self, e):
        if not self._start:
            return
        x0, y0 = self._start
        x1, y1 = e.x, e.y
        x, y = min(x0, x1), min(y0, y1)
        w, h = abs(x1 - x0), abs(y1 - y0)
        self.destroy()
        if w >= 10 and h >= 10:
            self.on_select(x + self.vx, y + self.vy, w, h)
