"""RegionOS — draw boxes on your screen; each one is independently captured
and live-previewed in the dashboard. Run: python main.py"""

import ctypes
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


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    from dashboard import Dashboard
    Dashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
