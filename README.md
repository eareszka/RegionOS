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

- **+ New Region** — the screen dims; drag a box over any area (any monitor), then name it.
- Each region card shows a **live preview**, position/size, and status.
- Per-region **FPS** (1 / 5 / 10 / 30 / 60) — each region captures on its own thread.
- **Pause / Resume**, **Rename** (or double-click the name), **Reselect** (redraw the box), **Delete**.
- Regions persist to `regions.json` and reload on launch.

## Files

| File | Role |
|---|---|
| `main.py` | Entry point, DPI awareness |
| `dashboard.py` | Main window + region cards (live previews) |
| `selector.py` | Fullscreen drag-to-select overlay |
| `capture.py` | Per-region capture threads (mss) |
| `regions.py` | Region model + `regions.json` persistence |

## Roadmap (from Desc.pdf, minus AI)

- Phase 2: OCR (Tesseract), change/color/image detection
- Automation: WHEN/THEN rules (detections → click / notify)
- Region groups
