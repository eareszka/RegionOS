"""Region model and persistence for RegionOS."""

import json
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path

REGIONS_FILE = Path(__file__).parent / "regions.json"

FPS_CHOICES = (1, 5, 10, 30, 60)


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
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


class RegionManager:
    """Owns the list of regions and saves/loads them from regions.json."""

    def __init__(self, path: Path = REGIONS_FILE):
        self.path = path
        self.regions: list[Region] = []
        self.load()

    def load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.regions = [Region(**r) for r in data]
        except (json.JSONDecodeError, TypeError):
            # Corrupt file: start fresh rather than crash on launch
            self.regions = []

    def save(self):
        data = [asdict(r) for r in self.regions]
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add(self, region: Region):
        self.regions.append(region)
        self.save()

    def remove(self, region: Region):
        self.regions.remove(region)
        self.save()
