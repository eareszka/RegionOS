"""Reads the current URL out of a Chromium browser window via UI
Automation, for the drag-a-tab-onto-a-box flow: the box needs the URL, not
the live window itself, so it can relaunch it through the same isolated,
off-screen pipeline as manually typing a URL (see browserlaunch.py)."""

# Chromium sets this exact accessible name on its address bar for screen
# readers, stable across Edge/Chrome versions and independent of locale
# quirks in the automation id.
ADDRESS_BAR_NAME = "Address and search bar"


def get_url(hwnd) -> str | None:
    """The address bar's current text for hwnd, or None if it can't be
    read (unsupported browser build, page still loading, a COM hiccup)."""
    try:
        from pywinauto import Application
        app = Application(backend="uia").connect(handle=hwnd, timeout=2)
        win = app.window(handle=hwnd)
        for edit in win.descendants(control_type="Edit"):
            if edit.element_info.name == ADDRESS_BAR_NAME:
                return edit.get_value() or None
    except Exception:
        return None
    return None
