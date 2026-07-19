"""RegionOS dashboard: main window listing all regions with live previews."""

import tkinter as tk
from tkinter import simpledialog, messagebox

from PIL import ImageTk

import wincap
from capture import make_worker, WindowCaptureWorker
from regions import Region, RegionManager, FPS_CHOICES
from selector import RegionSelector, WindowPicker, CropSelector

BG = "#1e1e1e"
CARD_BG = "#2a2a2a"
FG = "#e0e0e0"
DIM = "#8a8a8a"
ACCENT = "#4a9eff"

PREVIEW_MAX = (360, 200)


class RegionCard(tk.Frame):
    """One region's row in the dashboard: preview + info + controls."""

    def __init__(self, master, app, region: Region):
        super().__init__(master, bg=CARD_BG, padx=10, pady=10)
        self.app = app
        self.region = region
        self.worker = make_worker(region)
        self.worker.start()
        self._photo = None
        self._after_id = None

        top = tk.Frame(self, bg=CARD_BG)
        top.pack(fill="x")
        self.name_lbl = tk.Label(top, text=region.name, bg=CARD_BG, fg=FG,
                                 font=("Segoe UI", 12, "bold"))
        self.name_lbl.pack(side="left")
        self.name_lbl.bind("<Double-Button-1>", lambda e: self.rename())
        self.status_lbl = tk.Label(top, text="", bg=CARD_BG, fg=DIM, font=("Segoe UI", 9))
        self.status_lbl.pack(side="right")

        self.preview = tk.Label(self, bg="black", width=PREVIEW_MAX[0] // 8)
        self.preview.pack(pady=(8, 8))

        info = tk.Frame(self, bg=CARD_BG)
        info.pack(fill="x")
        self.geo_lbl = tk.Label(info, text="", bg=CARD_BG, fg=DIM, font=("Consolas", 9))
        self.geo_lbl.pack(side="left")

        tk.Label(info, text="FPS", bg=CARD_BG, fg=DIM, font=("Segoe UI", 9)).pack(
            side="left", padx=(16, 4))
        self.fps_var = tk.StringVar(value=str(region.fps))
        fps_menu = tk.OptionMenu(info, self.fps_var, *(str(f) for f in FPS_CHOICES),
                                 command=self.set_fps)
        fps_menu.configure(bg=CARD_BG, fg=FG, highlightthickness=0, relief="flat",
                           activebackground=CARD_BG, activeforeground=ACCENT)
        fps_menu["menu"].configure(bg=CARD_BG, fg=FG)
        fps_menu.pack(side="left")

        btns = tk.Frame(self, bg=CARD_BG)
        btns.pack(fill="x", pady=(6, 0))
        self.pause_btn = self._button(btns, "Pause", self.toggle_pause)
        self._button(btns, "Rename", self.rename)
        self._button(btns, "Reselect", self.reselect)
        self._button(btns, "Delete", self.delete, fg="#ff6b6b")
        if region.paused:
            self.pause_btn.configure(text="Resume")

        self._tick()

    def _button(self, parent, text, cmd, fg=FG):
        b = tk.Button(parent, text=text, command=cmd, bg=CARD_BG, fg=fg,
                      relief="flat", font=("Segoe UI", 9),
                      activebackground="#3a3a3a", activeforeground=ACCENT, cursor="hand2")
        b.pack(side="left", padx=(0, 8))
        return b

    # --- live preview loop ---------------------------------------------------

    def _tick(self):
        r = self.region
        frame = self.worker.latest_frame()
        if not r.paused and frame is not None:
            thumb = frame.copy()
            thumb.thumbnail(PREVIEW_MAX)
            self._photo = ImageTk.PhotoImage(thumb)
            self.preview.configure(image=self._photo, width=thumb.width,
                                   height=thumb.height)
        if r.paused:
            self.status_lbl.configure(text="Paused", fg="#e0a030")
        elif self.worker.note:
            self.status_lbl.configure(text=self.worker.note, fg="#e0a030")
        else:
            self.status_lbl.configure(
                text=f"Monitoring · {min(self.worker.measured_fps, r.fps):.0f} fps",
                fg="#5dbb63")
        if r.mode == "window":
            size = f"{frame.width}x{frame.height}" if frame else "—"
            crop = "  (cropped)" if r.crop else ""
            self.geo_lbl.configure(text=f"⊞ {r.window_title[:24]}  {size}{crop}")
        else:
            self.geo_lbl.configure(text=f"({r.x}, {r.y})  {r.w}x{r.h}")
        interval = 500 if r.paused else max(int(1000 / r.fps), 16)
        self._after_id = self.after(interval, self._tick)

    # --- controls ------------------------------------------------------------

    def set_fps(self, value):
        self.region.fps = int(value)
        self.app.manager.save()

    def toggle_pause(self):
        self.region.paused = not self.region.paused
        self.pause_btn.configure(text="Resume" if self.region.paused else "Pause")
        self.app.manager.save()

    def rename(self):
        name = simpledialog.askstring("Rename region", "Region name:",
                                      initialvalue=self.region.name, parent=self.app.root)
        if name:
            self.region.name = name.strip()
            self.name_lbl.configure(text=self.region.name)
            self.app.manager.save()

    def reselect(self):
        if self.region.mode == "window":
            def apply_window(title, crop):
                self.region.window_title = title
                self.region.crop = crop
                if isinstance(self.worker, WindowCaptureWorker):
                    self.worker.invalidate()
                self.app.manager.save()
            self.app.pick_window(apply_window)
        else:
            def apply_screen(x, y, w, h):
                self.region.x, self.region.y = x, y
                self.region.w, self.region.h = w, h
                self.app.manager.save()
            self.app.open_selector(apply_screen)

    def delete(self):
        if not messagebox.askyesno("Delete region",
                                   f'Delete region "{self.region.name}"?',
                                   parent=self.app.root):
            return
        self.destroy_card()
        self.app.manager.remove(self.region)
        self.app.refresh_empty_state()

    def destroy_card(self):
        if self._after_id:
            self.after_cancel(self._after_id)
        self.worker.stop()
        self.destroy()


class Dashboard:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.manager = RegionManager()
        self.cards: list[RegionCard] = []

        root.title("RegionOS")
        root.configure(bg=BG)
        root.geometry("460x640")
        root.minsize(420, 300)

        header = tk.Frame(root, bg=BG, padx=14, pady=12)
        header.pack(fill="x")
        tk.Label(header, text="RegionOS", bg=BG, fg=FG,
                 font=("Segoe UI", 16, "bold")).pack(side="left")
        new_btn = tk.Button(header, text="+ New Region", bg=ACCENT, fg="white",
                            relief="flat", font=("Segoe UI", 10, "bold"),
                            padx=12, pady=4, cursor="hand2",
                            activebackground="#3a8eef", activeforeground="white")
        new_btn.configure(command=lambda: self._new_region_menu(new_btn))
        new_btn.pack(side="right")

        # Scrollable card list
        wrapper = tk.Frame(root, bg=BG)
        wrapper.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrapper, bg=BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(wrapper, orient="vertical", command=self.canvas.yview)
        self.list_frame = tk.Frame(self.canvas, bg=BG)
        self.list_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._window = self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        root.bind_all("<MouseWheel>", self._on_wheel)

        self.empty_lbl = tk.Label(
            self.list_frame,
            text="No regions yet.\n\nClick  + New Region  and drag a box\nover any part of your screen.",
            bg=BG, fg=DIM, font=("Segoe UI", 11), justify="center", pady=40)

        for region in self.manager.regions:
            self.add_card(region)
        self.refresh_empty_state()

        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _on_wheel(self, e):
        self.canvas.yview_scroll(-1 * (e.delta // 120), "units")

    def refresh_empty_state(self):
        self.cards = [c for c in self.cards if c.winfo_exists()]
        if self.cards:
            self.empty_lbl.pack_forget()
        else:
            self.empty_lbl.pack(fill="x")

    def add_card(self, region: Region):
        card = RegionCard(self.list_frame, self, region)
        card.pack(fill="x", padx=14, pady=(0, 12))
        self.cards.append(card)

    # --- region creation -----------------------------------------------------

    def _new_region_menu(self, button):
        menu = tk.Menu(self.root, tearoff=0, bg=CARD_BG, fg=FG,
                       activebackground=ACCENT, activeforeground="white",
                       font=("Segoe UI", 10))
        menu.add_command(label="  Screen area — fixed rectangle on screen",
                         command=self.new_region)
        menu.add_command(label="  Application window — tracks the app even when covered",
                         command=self.new_window_region)
        menu.post(button.winfo_rootx(),
                  button.winfo_rooty() + button.winfo_height() + 4)

    def pick_window(self, on_done):
        """WindowPicker → snapshot → CropSelector → on_done(title, crop)."""
        def picked(hwnd, title):
            snapshot = wincap.grab_window(hwnd)
            if snapshot is None:
                messagebox.showerror(
                    "RegionOS", f'Could not capture "{title}".\n'
                    "The window may be minimized — restore it and try again.",
                    parent=self.root)
                return
            CropSelector(self.root, snapshot, lambda crop: on_done(title, crop))
        WindowPicker(self.root, picked)

    def new_window_region(self):
        def done(title, crop):
            name = simpledialog.askstring(
                "New region", "Region name:", initialvalue=title[:40],
                parent=self.root)
            if name is None:
                return
            region = Region(name=name.strip() or title[:40], x=0, y=0, w=0, h=0,
                            mode="window", window_title=title, crop=crop)
            self.manager.add(region)
            self.add_card(region)
            self.refresh_empty_state()
        self.pick_window(done)

    def open_selector(self, on_select):
        """Hide the dashboard, run the drag overlay, then restore."""
        self.root.withdraw()

        def wrapped(x, y, w, h):
            self.root.deiconify()
            on_select(x, y, w, h)

        selector = RegionSelector(self.root, wrapped)
        selector.bind("<Destroy>", lambda e: self.root.deiconify(), add="+")

    def new_region(self):
        def create(x, y, w, h):
            name = simpledialog.askstring(
                "New region", "Region name:",
                initialvalue=f"Region {len(self.manager.regions) + 1}",
                parent=self.root)
            if name is None:
                return
            region = Region(name=name.strip() or f"Region {len(self.manager.regions) + 1}",
                            x=x, y=y, w=w, h=h)
            self.manager.add(region)
            self.add_card(region)
            self.refresh_empty_state()
        self.open_selector(create)

    def on_close(self):
        for card in self.cards:
            if card.winfo_exists():
                card.worker.stop()
        self.root.destroy()
