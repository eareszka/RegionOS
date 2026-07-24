"""RegionOS dashboard: a bordered grid of live-preview boxes. Empty boxes
are click-to-add; filled boxes show a live capture with a slim control strip."""

import math
import threading
import tkinter as tk
from tkinter import simpledialog, messagebox
from urllib.parse import urlparse

from PIL import Image, ImageTk

import address_bar
import browserlaunch
import wincap
from capture import make_worker, BaseWorker, WindowCaptureWorker
from regions import Region, RegionManager, FPS_CHOICES, GRID_CHOICES
from selector import RegionSelector, WindowPicker, CropSelector, WebsiteEntry

BG = "#1e1e1e"
CARD_BG = "#2a2a2a"
FG = "#e0e0e0"
DIM = "#8a8a8a"
ACCENT = "#4a9eff"
STRIP_BG = "#151515"

STATUS_COLORS = {
    "monitoring": "#5dbb63",
    "paused": "#e0a030",
    "onscreen": ACCENT,
    "note": "#e0a030",
}


def grid_dimensions(n: int) -> tuple[int, int]:
    """(cols, rows) for a roughly square grid holding n boxes."""
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    return cols, rows


def name_from_url(url: str) -> str:
    """A short, recognizable region name derived from a URL, e.g.
    'https://www.google.com/search' -> 'google.com'."""
    host = urlparse(url).netloc
    return host[4:] if host.startswith("www.") else host or url


def fit_cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Scale img to fully cover a w x h box (like CSS object-fit: cover),
    then center-crop the overflow -- fills the box exactly with no
    letterboxing, at the cost of trimming whichever dimension overshoots."""
    src_w, src_h = img.size
    if src_w < 1 or src_h < 1:
        return img
    scale = max(w / src_w, h / src_h)
    new_w, new_h = max(round(src_w * scale), 1), max(round(src_h * scale), 1)
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left, top = (new_w - w) // 2, (new_h - h) // 2
    return resized.crop((left, top, left + w, top + h))


def fit_contain(img: Image.Image, w: int, h: int, bg: str) -> Image.Image:
    """Scale img to fit entirely within a w x h box (like CSS object-fit:
    contain) with no cropping, and pad the remainder with bg so it still
    exactly fills the box -- used in non-uniform mode, where a region's
    crop may not match its box's aspect ratio."""
    src_w, src_h = img.size
    if src_w < 1 or src_h < 1:
        return Image.new("RGB", (w, h), bg)
    scale = min(w / src_w, h / src_h)
    new_w, new_h = max(round(src_w * scale), 1), max(round(src_h * scale), 1)
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (w, h), bg)
    canvas.paste(resized, ((w - new_w) // 2, (h - new_h) // 2))
    return canvas


class Tile(tk.Frame):
    """One grid box. Either empty (click to assign a region) or filled
    (live preview + a slim strip with name, status, and controls)."""

    def __init__(self, master, app, slot: int):
        super().__init__(master, bg=CARD_BG)
        self.app = app
        self.slot = slot
        self.region: Region | None = None
        self.worker = None
        self._photo = None
        self._after_id = None

        # --- filled-state widgets (created once, shown/hidden via pack) ---
        self.preview = tk.Label(self, bg=CARD_BG, bd=0, cursor="hand2")
        self.preview.bind("<Double-Button-1>", self._on_preview_double_click)
        self.preview.bind("<Button-3>", self._context_menu)

        self.strip = tk.Frame(self, bg=STRIP_BG)
        self.name_lbl = tk.Label(self.strip, text="", bg=STRIP_BG, fg=FG,
                                 font=("Segoe UI", 9, "bold"), anchor="w")
        self.name_lbl.pack(side="left", padx=(8, 6), pady=4)
        self.name_lbl.bind("<Double-Button-1>", lambda e: self.rename())
        self.status_lbl = tk.Label(self.strip, text="", bg=STRIP_BG, fg=DIM,
                                   font=("Segoe UI", 8))
        self.status_lbl.pack(side="left")

        self.delete_btn = self._icon_btn(self.strip, "✕", self.delete, fg="#ff6b6b")
        self.reselect_btn = self._icon_btn(self.strip, "⟳", self.reselect)
        self.pause_btn = self._icon_btn(self.strip, "⏸", self.toggle_pause)

        self.strip.bind("<Button-3>", self._context_menu)
        self.bind("<Button-3>", self._context_menu)

        # --- empty-state placeholder ---
        self.placeholder = tk.Frame(self, bg=CARD_BG, cursor="hand2")
        self._plus_lbl = tk.Label(self.placeholder, text="+", bg=CARD_BG, fg=DIM,
                                  font=("Segoe UI", 30))
        self._plus_lbl.pack(expand=True, pady=(0, 2))
        self._hint_lbl = tk.Label(self.placeholder, text="Click to add", bg=CARD_BG, fg=DIM,
                                  font=("Segoe UI", 9))
        self._hint_lbl.pack(pady=(0, 14))
        self._drag_origin = None
        self._dragging_pick = False
        for w in (self.placeholder, self._plus_lbl, self._hint_lbl):
            w.bind("<ButtonPress-1>", self._placeholder_press)
            w.bind("<B1-Motion>", self._placeholder_motion)
            w.bind("<ButtonRelease-1>", self._placeholder_release)
            w.bind("<Enter>", lambda e: self._placeholder_hover(True))
            w.bind("<Leave>", lambda e: self._placeholder_hover(False))

        self._show_empty()

    def _icon_btn(self, parent, text, cmd, fg=FG):
        b = tk.Button(parent, text=text, command=cmd, bg=STRIP_BG, fg=fg, bd=0,
                      relief="flat", font=("Segoe UI", 9), width=2,
                      activebackground="#2a2a2a", activeforeground=ACCENT, cursor="hand2")
        b.pack(side="right", padx=(0, 2), pady=2)
        return b

    def _placeholder_hover(self, entered):
        fg = ACCENT if entered else DIM
        self._plus_lbl.configure(fg=fg)
        self._hint_lbl.configure(fg=fg)

    # --- empty-tile click vs. drag-to-pick disambiguation -----------------------

    DRAG_THRESHOLD = 6

    def _placeholder_press(self, event):
        self._drag_origin = (event.x_root, event.y_root)
        self._dragging_pick = False

    def _placeholder_motion(self, event):
        if self._drag_origin is None or self._dragging_pick:
            return
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        if abs(dx) > self.DRAG_THRESHOLD or abs(dy) > self.DRAG_THRESHOLD:
            self._dragging_pick = True
            for w in (self.placeholder, self._plus_lbl, self._hint_lbl):
                w.configure(cursor="target")
            self._hint_lbl.configure(text="Release over a window to track it")
            # The initial click already raised us (normal OS click-to-focus
            # behavior) -- drop back behind other windows so the app you're
            # about to drag onto, usually on the same screen, is reachable
            # instead of hidden under our own window.
            self.app.root.lower()

    def _placeholder_release(self, event):
        was_dragging = self._dragging_pick
        self._drag_origin = None
        self._dragging_pick = False
        for w in (self.placeholder, self._plus_lbl, self._hint_lbl):
            w.configure(cursor="hand2")
        self._hint_lbl.configure(text="Click to add")
        if was_dragging:
            self._pick_by_drag(event.x_root, event.y_root)
        else:
            self._empty_click(event)

    # --- empty <-> filled state ------------------------------------------------

    def _show_empty(self):
        self.preview.pack_forget()
        self.strip.pack_forget()
        self.placeholder.pack(fill="both", expand=True)

    def _show_filled(self):
        self.placeholder.pack_forget()
        self.strip.pack(fill="x", side="bottom")
        self.preview.pack(fill="both", expand=True, side="top")

    def fill(self, region: Region, hwnd=None):
        """Attach to a region's worker (started fresh, or already running in
        the background if this slot was just hidden by a grid resize -- see
        Dashboard.get_or_start_worker)."""
        self.region = region
        self.name_lbl.configure(text=region.name)
        self.pause_btn.configure(text="▶" if region.paused else "⏸")
        self.worker = self.app.get_or_start_worker(region, hwnd=hwnd)
        self._show_filled()
        self._tick()

    def clear(self):
        """Detach this tile's view. Does NOT stop the worker -- a region
        merely hidden by a grid resize keeps tracking in the background,
        the same way off-screen website tracking already does regardless of
        whether its tile is currently visible. Only delete() and app exit
        actually stop a region's worker."""
        if self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None
        self.worker = None
        self.region = None
        self._photo = None
        self._show_empty()

    # --- live preview loop -------------------------------------------------

    def _tick(self):
        if self.region is None:
            return
        r = self.region
        frame = self.worker.latest_frame()
        if not r.paused and frame is not None:
            w = max(self.preview.winfo_width(), 10)
            h = max(self.preview.winfo_height(), 10)
            thumb = (fit_cover(frame, w, h) if r.uniform
                     else fit_contain(frame, w, h, CARD_BG))
            self._photo = ImageTk.PhotoImage(thumb)
            self.preview.configure(image=self._photo)
        if r.paused:
            self.status_lbl.configure(text="Paused", fg=STATUS_COLORS["paused"])
        elif isinstance(self.worker, WindowCaptureWorker) and self.worker.pinned_onscreen:
            self.status_lbl.configure(text="On screen · dbl-click to hide",
                                      fg=STATUS_COLORS["onscreen"])
        elif self.worker.note:
            self.status_lbl.configure(text=self.worker.note, fg=STATUS_COLORS["note"])
        else:
            self.status_lbl.configure(
                text=f"{min(self.worker.measured_fps, r.fps):.0f} fps",
                fg=STATUS_COLORS["monitoring"])
        interval = 500 if r.paused else max(int(1000 / r.fps), 16)
        self._after_id = self.after(interval, self._tick)

    # --- filled-tile controls ------------------------------------------------

    def toggle_pause(self):
        self.region.paused = not self.region.paused
        self.pause_btn.configure(text="▶" if self.region.paused else "⏸")
        self.app.manager.save()

    def rename(self):
        name = simpledialog.askstring("Rename region", "Region name:",
                                      initialvalue=self.region.name, parent=self.app.root)
        if name:
            self.region.name = name.strip()
            self.name_lbl.configure(text=self.region.name)
            self.app.manager.save()

    def set_fps(self, value):
        self.region.fps = int(value)
        self.app.manager.save()

    def _on_preview_double_click(self, event=None):
        if self.region and self.region.mode == "window":
            self.toggle_onscreen()

    def toggle_onscreen(self):
        """Bring a hidden tracked window forward so the user can use it
        directly, or send it back off-screen."""
        if not (isinstance(self.worker, WindowCaptureWorker) and self.worker.hwnd):
            return
        if self.worker.pinned_onscreen:
            self.worker.pinned_onscreen = False
            browserlaunch.push_offscreen(self.worker.hwnd, offset_index=self.region.slot)
        else:
            self.worker.pinned_onscreen = True
            browserlaunch.bring_onscreen(self.worker.hwnd)

    def _target_aspect(self, default: bool = True):
        """w/h to lock crop-selection dragging to, so the result already
        matches this box's shape -- or None for freeform selection. New
        regions use `default` (screen regions default to locked; window
        regions default to freeform since a window's whole content is
        usually what you want to see); existing ones follow their own
        per-region toggle."""
        uniform = self.region.uniform if self.region else default
        if not uniform:
            return None
        w, h = self.winfo_width(), self.winfo_height()
        return w / h if w > 1 and h > 1 else None

    def reselect(self):
        if self.region.mode == "window":
            hwnd = self.worker.hwnd if isinstance(self.worker, WindowCaptureWorker) else None
            if hwnd and wincap.is_alive(hwnd):
                # Already tracking a specific window -- recrop it directly
                # instead of making the user find it again in the picker.
                self._recrop(hwnd)
            else:
                def apply_window(title, crop):
                    self.region.window_title = title
                    self.region.crop = crop
                    if isinstance(self.worker, WindowCaptureWorker):
                        self.worker.invalidate()
                    self.app.manager.save()
                self.app.pick_window(apply_window, aspect=self._target_aspect())
        else:
            def apply_screen(x, y, w, h):
                self.region.x, self.region.y = x, y
                self.region.w, self.region.h = w, h
                self.app.manager.save()
            self.app.open_selector(apply_screen, aspect=self._target_aspect())

    def _recrop(self, hwnd):
        snapshot = wincap.grab_window(hwnd)
        if snapshot is None:
            messagebox.showerror(
                "RegionOS", f'Could not capture "{self.region.name}".\n'
                "The window may be minimized — restore it and try again.",
                parent=self.app.root)
            return

        def apply(crop):
            self.region.crop = crop
            self.app.manager.save()
        CropSelector(self.app.root, snapshot, apply, aspect=self._target_aspect())

    def delete(self):
        if not messagebox.askyesno("Delete region",
                                   f'Delete region "{self.region.name}"?',
                                   parent=self.app.root):
            return
        self.app.stop_worker(self.region)
        self.app.manager.remove(self.region)
        self.clear()

    def set_uniform(self, value: bool):
        self.region.uniform = value
        self.app.manager.save()

    def _context_menu(self, event):
        if self.region is None:
            return
        menu = tk.Menu(self.app.root, tearoff=0, bg=CARD_BG, fg=FG,
                       activebackground=ACCENT, activeforeground="white", font=("Segoe UI", 10))
        menu.add_command(label="Rename", command=self.rename)
        menu.add_command(label="Reselect", command=self.reselect)
        fps_menu = tk.Menu(menu, tearoff=0, bg=CARD_BG, fg=FG,
                           activebackground=ACCENT, activeforeground="white", font=("Segoe UI", 10))
        for f in FPS_CHOICES:
            fps_menu.add_command(label=f"{f} fps", command=lambda f=f: self.set_fps(f))
        menu.add_cascade(label="FPS", menu=fps_menu)
        # Ephemeral BooleanVar owned by this menu instance -- fine since the
        # menu is rebuilt fresh on every right-click, same as the rest here.
        uniform_var = tk.BooleanVar(value=self.region.uniform)
        menu.add_checkbutton(label="Uniform box (lock crop to box shape)", variable=uniform_var,
                             command=lambda: self.set_uniform(uniform_var.get()),
                             selectcolor=CARD_BG)
        menu.add_separator()
        menu.add_command(label="Delete", command=self.delete)
        menu.post(event.x_root, event.y_root)

    # --- empty-tile assignment -----------------------------------------------

    def _empty_click(self, event):
        menu = tk.Menu(self.app.root, tearoff=0, bg=CARD_BG, fg=FG,
                       activebackground=ACCENT, activeforeground="white", font=("Segoe UI", 10))
        menu.add_command(label="  Screen area — fixed rectangle on screen",
                         command=self.assign_screen)
        menu.add_command(label="  Application window — tracks the app even when covered",
                         command=self.assign_window)
        menu.add_command(label="  Website — opens hidden, tracks automatically",
                         command=self.assign_website)
        menu.post(event.x_root, event.y_root)

    def assign_screen(self):
        def create(x, y, w, h):
            name = simpledialog.askstring(
                "New region", "Region name:",
                initialvalue=f"Region {self.slot + 1}", parent=self.app.root)
            if name is None:
                return
            region = Region(name=name.strip() or f"Region {self.slot + 1}",
                            x=x, y=y, w=w, h=h, slot=self.slot)
            self.app.manager.add(region)
            self.fill(region)
        self.app.open_selector(create, aspect=self._target_aspect())

    def assign_window(self):
        def done(title, crop):
            name = simpledialog.askstring(
                "New region", "Region name:", initialvalue=title[:40], parent=self.app.root)
            if name is None:
                return
            region = Region(name=name.strip() or title[:40], x=0, y=0, w=0, h=0,
                            mode="window", window_title=title, crop=crop, slot=self.slot,
                            uniform=False)
            self.app.manager.add(region)
            self.fill(region)
        self.app.pick_window(done, aspect=self._target_aspect(default=False))

    def assign_website(self):
        WebsiteEntry(self.app.root, self._launch_website)

    def _launch_website(self, url, close_hwnd=None):
        """Launch url in the hidden, isolated-profile window and assign it
        to this box. If close_hwnd is given (the real browser window this
        URL was read from, e.g. a dragged tab), it's closed only once the
        isolated copy is confirmed up and tracking -- never before, so a
        failed launch doesn't lose the user's original window/tab."""
        if not browserlaunch.find_browser():
            messagebox.showerror(
                "RegionOS", "Couldn't find an installed browser (Edge or Chrome).",
                parent=self.app.root)
            return
        name = name_from_url(url)

        status = tk.Toplevel(self.app.root, bg=BG, padx=30, pady=24)
        status.title("RegionOS")
        status.transient(self.app.root)
        status.grab_set()
        status.resizable(False, False)
        tk.Label(status, text=f'Opening "{name}" in a hidden window...',
                 bg=BG, fg=FG, font=("Segoe UI", 10)).pack()

        def work():
            result = browserlaunch.launch_offscreen(url, offset_index=self.slot)
            self.app.root.after(0, lambda: finish(result))

        def finish(result):
            status.destroy()
            if not result:
                messagebox.showerror(
                    "RegionOS", f'Could not open or locate the window for "{url}".\n'
                    "Try again, or check the URL.", parent=self.app.root)
                return
            hwnd, title = result
            region = Region(name=name, x=0, y=0, w=0, h=0, mode="window",
                            window_title=title, url=url, slot=self.slot, uniform=False)
            self.app.manager.add(region)
            self.fill(region, hwnd=hwnd)
            if close_hwnd and wincap.is_alive(close_hwnd):
                wincap.close_window(close_hwnd)

        threading.Thread(target=work, daemon=True).start()

    # --- drag-to-pick: drag from this box's placeholder onto any window --------

    def _pick_by_drag(self, x_root, y_root):
        hwnd = wincap.window_from_point(x_root, y_root)
        if (not hwnd or not wincap.is_alive(hwnd)
                or wincap.get_window_title(hwnd) == "RegionOS"):
            # Nothing usable under the cursor -- we already lowered
            # ourselves for the drag, so raise back up rather than leaving
            # the app stuck behind whatever's on screen.
            self.app.root.lift()
            return
        if browserlaunch.is_browser_window(hwnd):
            self._assign_from_browser_drop(hwnd)
        else:
            self._assign_from_window_drop(hwnd)

    def _assign_from_window_drop(self, hwnd):
        """Dropped onto a non-browser window: track it directly, same as
        the "Application window" picker but without re-picking from a list
        since the drag itself already identified the window. The window
        disappears off-screen the moment you drop it -- RegionOS keeps
        capturing it there, and double-clicking the tile (or minimizing
        the window back) brings it forward or hides it again. This window
        is never closed by RegionOS -- only ever restored back on-screen
        (see Dashboard.stop_worker) -- since it's the user's own, not one
        we spawned."""
        title = wincap.get_window_title(hwnd)
        snapshot = wincap.grab_window(hwnd)
        if snapshot is None:
            messagebox.showerror(
                "RegionOS", f'Could not capture "{title}".\n'
                "The window may be minimized — restore it and try again.",
                parent=self.app.root)
            return
        browserlaunch.push_offscreen(hwnd, offset_index=self.slot)

        def apply(crop):
            name = simpledialog.askstring(
                "New region", "Region name:", initialvalue=title[:40], parent=self.app.root)
            if name is None:
                return
            region = Region(name=name.strip() or title[:40], x=0, y=0, w=0, h=0,
                            mode="window", window_title=title, crop=crop, slot=self.slot,
                            uniform=False)
            self.app.manager.add(region)
            self.fill(region, hwnd=hwnd)
        CropSelector(self.app.root, snapshot, apply, aspect=self._target_aspect(default=False))

    def _assign_from_browser_drop(self, hwnd):
        """Dropped onto a browser window: read its URL and relaunch it
        through the isolated-profile pipeline instead of tracking the
        user's actual regular-profile window directly. Tracking a real
        browser window directly was tried and reverted: pushing it
        off-screen still leaves it as the "last" window of that browser
        process, so any *other*, unrelated new window the user opens
        afterward (Ctrl+N, a link, anything) gets cascade-positioned by
        Chromium relative to it and lands mostly off-screen too --
        confirmed directly, not theoretical. A separate, isolated process
        never has the user's regular windows in view, so this can't
        happen. Once the isolated copy is confirmed up and tracking, the
        original tab/window is closed -- the user dragged it here to
        replace it, not to keep a duplicate live."""
        status = tk.Toplevel(self.app.root, bg=BG, padx=30, pady=24)
        status.title("RegionOS")
        status.transient(self.app.root)
        status.grab_set()
        status.resizable(False, False)
        tk.Label(status, text="Reading the tab's URL...",
                 bg=BG, fg=FG, font=("Segoe UI", 10)).pack()

        def work():
            url = address_bar.get_url(hwnd)
            self.app.root.after(0, lambda: got_url(url))

        def got_url(url):
            status.destroy()
            if not url or not url.startswith(("http://", "https://")):
                messagebox.showerror(
                    "RegionOS", "Couldn't read a URL from that window.\n"
                    'Use "Website" from this box\'s menu instead.', parent=self.app.root)
                return
            self._launch_website(url, close_hwnd=hwnd)

        threading.Thread(target=work, daemon=True).start()


class Dashboard:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.manager = RegionManager()
        self.tiles: list[Tile] = []
        # Keyed by region.id rather than slot: workers outlive their tile
        # across grid resizes, so a hidden region keeps tracking in the
        # background exactly like off-screen website tracking already does.
        self.workers: dict[str, BaseWorker] = {}
        self._restore_cascade = 0  # offsets successive stop_worker restores

        root.title("RegionOS")
        root.configure(bg=BG)
        w, h = 960, 680
        x = (root.winfo_screenwidth() - w) // 2
        y = (root.winfo_screenheight() - h) // 2
        root.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")
        root.minsize(480, 360)

        header = tk.Frame(root, bg=BG, padx=14, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="RegionOS", bg=BG, fg=FG,
                 font=("Segoe UI", 16, "bold")).pack(side="left")

        box_picker = tk.Frame(header, bg=BG)
        box_picker.pack(side="right")
        tk.Label(box_picker, text="Boxes", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(
            side="left", padx=(0, 6))
        self.grid_var = tk.StringVar(value=str(self.manager.grid_size))
        grid_menu = tk.OptionMenu(box_picker, self.grid_var, *(str(n) for n in GRID_CHOICES),
                                  command=self._on_grid_size_change)
        grid_menu.configure(bg=CARD_BG, fg=FG, highlightthickness=0, relief="flat",
                            activebackground=CARD_BG, activeforeground=ACCENT,
                            font=("Segoe UI", 9), width=3)
        grid_menu["menu"].configure(bg=CARD_BG, fg=FG)
        grid_menu.pack(side="left")


        tk.Frame(root, bg=CARD_BG, height=1).pack(fill="x")

        self.grid_container = tk.Frame(root, bg=BG)
        self.grid_container.pack(fill="both", expand=True)

        self.build_grid()

        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # --- grid construction -----------------------------------------------------

    def get_or_start_worker(self, region: Region, hwnd=None):
        """Return the region's already-running worker, or start a new one.
        Used both for brand-new regions and for re-attaching a Tile after a
        grid resize brings a previously-hidden slot back into view."""
        worker = self.workers.get(region.id)
        if worker is not None:
            return worker
        worker = make_worker(region)
        if hwnd is not None and isinstance(worker, WindowCaptureWorker):
            worker.hwnd = hwnd
        worker.start()
        self.workers[region.id] = worker
        return worker

    def stop_worker(self, region: Region):
        worker = self.workers.pop(region.id, None)
        if worker is None:
            return
        if isinstance(worker, WindowCaptureWorker) and worker.hwnd and wincap.is_alive(worker.hwnd):
            if region.url:
                # A RegionOS-managed hidden website -- our window to close.
                wincap.close_window(worker.hwnd)
            elif browserlaunch.is_offscreen(worker.hwnd):
                # The user's own window (e.g. a drag-tracked app or real
                # browser), currently hidden off-screen -- never ours to
                # close, but it must not be left stranded off-screen once
                # nothing is tracking it anymore. Cascade so restoring
                # several at once (e.g. on app exit) doesn't stack them
                # exactly on top of each other.
                browserlaunch.restore_to_visible(worker.hwnd, self._restore_cascade)
                self._restore_cascade += 1
        worker.stop()

    def build_grid(self):
        for tile in self.tiles:
            if tile._after_id:
                tile.after_cancel(tile._after_id)
            tile.destroy()
        for child in self.grid_container.winfo_children():
            child.destroy()
        self.tiles = []

        # Row/column weight configuration persists on the container across
        # rebuilds. Without clearing it first, shrinking the grid leaves
        # leftover weighted-but-now-empty rows/columns from the previous,
        # larger layout claiming space that belongs to the current tiles.
        max_dim = max(max(grid_dimensions(choice)) for choice in GRID_CHOICES)
        for i in range(max_dim):
            self.grid_container.columnconfigure(i, weight=0, uniform="")
            self.grid_container.rowconfigure(i, weight=0, uniform="")

        n = self.manager.grid_size
        cols, rows = grid_dimensions(n)
        for c in range(cols):
            self.grid_container.columnconfigure(c, weight=1, uniform="col")
        for r in range(rows):
            self.grid_container.rowconfigure(r, weight=1, uniform="row")

        for slot in range(n):
            row, col = divmod(slot, cols)
            tile = Tile(self.grid_container, self, slot)
            tile.grid(row=row, column=col, sticky="nsew", padx=1, pady=1)
            self.tiles.append(tile)
            region = self.manager.region_at(slot)
            if region:
                tile.fill(region)

    def _on_grid_size_change(self, value):
        self.manager.set_grid_size(int(value))
        self.build_grid()

    # --- shared selection helpers (used by Tile) --------------------------------

    def pick_window(self, on_done, aspect: float | None = None):
        """WindowPicker -> snapshot -> CropSelector -> on_done(title, crop)."""
        def picked(hwnd, title):
            snapshot = wincap.grab_window(hwnd)
            if snapshot is None:
                messagebox.showerror(
                    "RegionOS", f'Could not capture "{title}".\n'
                    "The window may be minimized — restore it and try again.",
                    parent=self.root)
                return
            CropSelector(self.root, snapshot, lambda crop: on_done(title, crop), aspect=aspect)
        WindowPicker(self.root, picked)

    def open_selector(self, on_select, aspect: float | None = None):
        """Hide the dashboard, run the drag overlay, then restore."""
        self.root.withdraw()

        def wrapped(x, y, w, h):
            self.root.deiconify()
            on_select(x, y, w, h)

        selector = RegionSelector(self.root, wrapped, aspect=aspect)
        selector.bind("<Destroy>", lambda e: self.root.deiconify(), add="+")

    def on_close(self):
        # Stop every tracked region, not just ones with a currently-visible
        # tile -- a region hidden by a smaller grid size is still running.
        for region in list(self.manager.regions):
            self.stop_worker(region)
        self.root.destroy()
