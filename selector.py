"""Selection dialogs: fullscreen drag overlay (screen regions), window picker
and in-window crop selector (window regions)."""

import ctypes
import tkinter as tk

from PIL import ImageTk

import wincap

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


BG = "#1e1e1e"
CARD_BG = "#2a2a2a"
FG = "#e0e0e0"
DIM = "#8a8a8a"
ACCENT = "#4a9eff"


class WindowPicker(tk.Toplevel):
    """Modal list of open windows. Calls on_pick(hwnd, title)."""

    def __init__(self, master, on_pick):
        super().__init__(master)
        self.on_pick = on_pick
        self.title("Pick a window")
        self.configure(bg=BG, padx=14, pady=12)
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)

        tk.Label(self, text="Track which window?", bg=BG, fg=FG,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))
        tk.Label(self, text="The region keeps updating even when this window\n"
                            "is covered by other windows (not when minimized).",
                 bg=BG, fg=DIM, font=("Segoe UI", 9), justify="left").pack(
                     anchor="w", pady=(0, 8))

        self.windows = [(h, t) for h, t in wincap.list_windows()
                        if t not in ("RegionOS", "Pick a window")]
        self.listbox = tk.Listbox(self, width=60, height=min(len(self.windows) + 1, 18),
                                  bg=CARD_BG, fg=FG, selectbackground=ACCENT,
                                  relief="flat", font=("Segoe UI", 10),
                                  highlightthickness=0, activestyle="none")
        for _, t in self.windows:
            self.listbox.insert("end", f"  {t}")
        self.listbox.pack()
        if self.windows:
            self.listbox.selection_set(0)
        self.listbox.bind("<Double-Button-1>", lambda e: self._ok())

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", pady=(10, 0))
        tk.Button(btns, text="Select", command=self._ok, bg=ACCENT, fg="white",
                  relief="flat", padx=14, pady=3, cursor="hand2",
                  font=("Segoe UI", 10, "bold")).pack(side="right")
        tk.Button(btns, text="Cancel", command=self.destroy, bg=CARD_BG, fg=FG,
                  relief="flat", padx=14, pady=3, cursor="hand2",
                  font=("Segoe UI", 10)).pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.focus_force()

    def _ok(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        hwnd, title = self.windows[sel[0]]
        self.destroy()
        self.on_pick(hwnd, title)


class CropSelector(tk.Toplevel):
    """Shows a snapshot of the picked window; drag a box to crop to a specific
    area of the app, or take the whole window. Calls on_select(crop_or_None)
    where crop is [x, y, w, h] in window client-area pixels."""

    def __init__(self, master, snapshot, on_select):
        super().__init__(master)
        self.on_select = on_select
        self.title("Select area inside window")
        self.configure(bg=BG)
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)

        sw = self.winfo_screenwidth() * 0.8
        sh = self.winfo_screenheight() * 0.75
        self.scale = min(sw / snapshot.width, sh / snapshot.height, 1.0)
        disp = snapshot if self.scale == 1.0 else snapshot.resize(
            (max(int(snapshot.width * self.scale), 1),
             max(int(snapshot.height * self.scale), 1)))
        self._photo = ImageTk.PhotoImage(disp)

        tk.Label(self, text="Drag a box over the area to track — or use the whole window",
                 bg=BG, fg=FG, font=("Segoe UI", 11)).pack(pady=(10, 6))
        self.canvas = tk.Canvas(self, width=disp.width, height=disp.height,
                                highlightthickness=0, cursor="crosshair")
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        self.canvas.pack(padx=14)

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=14, pady=10)
        tk.Button(btns, text="Whole window", command=self._whole, bg=ACCENT,
                  fg="white", relief="flat", padx=14, pady=3, cursor="hand2",
                  font=("Segoe UI", 10, "bold")).pack(side="right")
        tk.Button(btns, text="Cancel", command=self.destroy, bg=CARD_BG, fg=FG,
                  relief="flat", padx=14, pady=3, cursor="hand2",
                  font=("Segoe UI", 10)).pack(side="right", padx=(0, 8))

        self._start = None
        self._rect = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.bind("<Escape>", lambda e: self.destroy())
        self.focus_force()

    def _whole(self):
        self.destroy()
        self.on_select(None)

    def _press(self, e):
        self._start = (e.x, e.y)
        self._rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y,
                                                  outline="#ff4444", width=2)

    def _drag(self, e):
        if self._rect and self._start:
            self.canvas.coords(self._rect, *self._start, e.x, e.y)

    def _release(self, e):
        if not self._start:
            return
        x0, y0 = self._start
        x, y = min(x0, e.x), min(y0, e.y)
        w, h = abs(e.x - x0), abs(e.y - y0)
        if w < 10 or h < 10:
            self._start = None
            if self._rect:
                self.canvas.delete(self._rect)
                self._rect = None
            return
        self.destroy()
        s = self.scale
        self.on_select([int(x / s), int(y / s), int(w / s), int(h / s)])
