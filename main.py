"""RegionOS — draw boxes on your screen; each one is independently captured
and live-previewed in the dashboard. Run: python main.py"""

import ctypes
import os
import sys
import tkinter as tk


def enable_dpi_awareness():
    """Without this, Tkinter coordinates are scaled and captures misalign
    on displays with Windows scaling > 100%."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def set_window_icon(root: tk.Tk):
    """Tkinter windows show its default feather icon in the title bar and
    taskbar unless told otherwise -- true even when the exe itself has a
    custom icon (that only covers the file/shortcut icon). sys._MEIPASS is
    where PyInstaller's --onefile build extracts bundled data at runtime;
    fall back to the script's own directory when running from source."""
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(base_dir, "icon.ico")
    try:
        root.iconbitmap(icon_path)
    except tk.TclError:
        pass  # missing/unreadable icon shouldn't block the app from starting


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    set_window_icon(root)
    from dashboard import Dashboard
    Dashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
