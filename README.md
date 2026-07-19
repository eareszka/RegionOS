# RegionOS (v0.1 Alpha)

Desktop app: draw boxes ("regions") anywhere on your screen and RegionOS
continuously captures and live-previews each one independently.

No AI. All analysis is (and will be) deterministic computer vision, done locally.

## Run

```
pip install -r requirements.txt
python main.py
```

## Use

**+ New Region** offers two region types:

- **Screen area** — the screen dims; drag a box over any area (any monitor). Captures those fixed screen pixels.
- **Application window** — pick a running app, then optionally drag a box inside it to track just that part. Captures the app's own rendered content, so it **keeps updating even when the window is covered** by other windows. If the app closes and reopens, the region re-finds it by title.

Each region card shows a **live preview**, geometry, and status; per-region **FPS** (1 / 5 / 10 / 30 / 60), each on its own capture thread. **Pause / Resume**, **Rename** (double-click the name), **Reselect**, **Delete**. Regions persist to `regions.json` and reload on launch.

**Window-capture limits (Windows platform, same as OBS):**
- *Minimized* windows aren't rendered by Windows — the region freezes on the last frame and shows "Minimized". Keep the window open-but-covered instead.
- *Background browser tabs* aren't rendered by the browser. Pop the tab out into its own window and track that.

## Files

| File | Role |
|---|---|
| `main.py` | Entry point, DPI awareness |
| `dashboard.py` | Main window + region cards (live previews) |
| `selector.py` | Screen-drag overlay, window picker, in-window crop selector |
| `capture.py` | Per-region capture threads (screen: mss, window: wincap) |
| `wincap.py` | Win32 PrintWindow capture (works while window is covered) |
| `regions.py` | Region model + `regions.json` persistence |

## Roadmap (from Desc.pdf, minus AI)

- Phase 2: OCR (Tesseract), change/color/image detection
- Automation: WHEN/THEN rules (detections → click / notify)
- Region groups
