"""Region model and persistence for RegionOS."""

import json
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path

REGIONS_FILE = Path(__file__).parent / "regions.json"

FPS_CHOICES = (1, 5, 10, 30, 60)
# Grid sizes offered in the "Boxes" dropdown; each maps to a roughly square
# rows x cols layout (see dashboard.grid_dimensions).
GRID_CHOICES = (1, 2, 4, 6, 9, 12, 16)
DEFAULT_GRID_SIZE = 4


@dataclass
class Region:
    name: str
    x: int
    y: int
    w: int
    h: int
    fps: int = 10
    paused: bool = False
    # mode "screen": capture the fixed screen rectangle (x, y, w, h).
    # mode "window": capture an application window (found by window_title),
    #   even when it is covered by other windows; x/y/w/h are unused.
    mode: str = "screen"
    window_title: str = ""
    # Optional [x, y, w, h] crop inside the window's client area
    crop: list | None = None
    # If set, mode "window" is a RegionOS-managed hidden browser window:
    # the capture worker relaunches it off-screen if it's ever closed.
    url: str = ""
    # Which grid box this region occupies. Fixed once assigned, so deleting
    # a region leaves that box empty rather than shifting the others.
    slot: int = -1
    # When True, crops for this region are locked to its box's aspect ratio
    # at selection time (so the preview always exactly fills the box, no
    # cropping or letterboxing needed). When False, crops are freeform and
    # the preview letterboxes to show the whole capture.
    uniform: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


class RegionManager:
    """Owns the list of regions and the grid size, and saves/loads both
    from regions.json."""

    def __init__(self, path: Path = REGIONS_FILE):
        self.path = path
        self.regions: list[Region] = []
        self.grid_size = DEFAULT_GRID_SIZE
        self.load()

    def load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return  # Corrupt file: start fresh rather than crash on launch
        if isinstance(data, list):
            self._load_legacy(data)
            return
        try:
            self.grid_size = int(data.get("grid_size", DEFAULT_GRID_SIZE))
            self.regions = [Region(**r) for r in data.get("regions", [])]
        except (TypeError, ValueError):
            self.regions = []
            self.grid_size = DEFAULT_GRID_SIZE

    def _load_legacy(self, data: list):
        """Pre-grid regions.json was a flat list. Give each region its own
        slot in original order, and size the grid to fit them."""
        self.regions = []
        for i, r in enumerate(data):
            r.setdefault("slot", i)
            try:
                self.regions.append(Region(**r))
            except TypeError:
                continue
        needed = len(self.regions)
        self.grid_size = (DEFAULT_GRID_SIZE if needed <= DEFAULT_GRID_SIZE else
                          next((n for n in GRID_CHOICES if n >= needed), GRID_CHOICES[-1]))
        self.save()

    def save(self):
        data = {"grid_size": self.grid_size, "regions": [asdict(r) for r in self.regions]}
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add(self, region: Region):
        self.regions.append(region)
        self.save()

    def remove(self, region: Region):
        self.regions.remove(region)
        self.save()

    def region_at(self, slot: int) -> Region | None:
        return next((r for r in self.regions if r.slot == slot), None)

    def set_grid_size(self, n: int):
        self.grid_size = n
        self.save()
