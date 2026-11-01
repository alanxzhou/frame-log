"""Face regions in XMP: the MWG Regions structure Lightroom reads and writes.

Pure Python, no face model and no numpy, so everything here is testable anywhere.

Two conventions matter, both measured on a real Lightroom-tagged library (see
docs/face-tagging-experiments.md):

* An area's X, Y are the box's CENTRE and W, H its size, all as fractions of the image.
* In files carrying an EXIF Orientation other than 1 (about a fifth of that library) the
  fractions are relative to the file's STORED pixels, not the upright picture. Reading
  them as upright put the box on the wrong part of the image: detector agreement was 16%
  on rotated scans and 88% once the orientation was applied.

Everything inside this program uses the upright picture, as a top-left corner and a size.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence


@dataclass(frozen=True)
class FaceBox:
    """A face in the upright picture: top-left corner and size, as fractions of its width and height."""
    x: float
    y: float
    w: float
    h: float
    name: str = ""

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    def key(self) -> str:
        """A stable id for this box within its image, used to remember what you called it."""
        return f"{self.x:.3f},{self.y:.3f}"


@dataclass
class TaggedImage:
    """An image whose file already carries face regions (from Lightroom, or an earlier Write)."""
    path: str
    orientation: int
    faces: list[FaceBox] = field(default_factory=list)


def to_upright(x: float, y: float, w: float, h: float, orientation: Optional[int]) -> tuple[float, float, float, float]:
    """Move a region (centre x, y and size w, h) from the file's stored pixels to the upright picture.

    EXIF orientation says how the stored pixels are turned for display. The centre moves with them;
    for the four 90-degree cases (5-8) the normalised width and height trade places.
    """
    o = orientation or 1
    nx, ny = {1: (x, y), 2: (1 - x, y), 3: (1 - x, 1 - y), 4: (x, 1 - y),
              5: (y, x), 6: (1 - y, x), 7: (1 - y, 1 - x), 8: (y, 1 - x)}.get(o, (x, y))
    if o >= 5:
        w, h = h, w
    return nx, ny, w, h


def parse_regions(data: Sequence[dict]) -> list[TaggedImage]:
    """Turn `exiftool -j -struct -RegionInfo -Orientation#` output into upright face boxes.

    Only Type=Face regions are kept; images without any are dropped.
    """
    out = []
    for d in data:
        regions = (d.get("RegionInfo") or {}).get("RegionList") or []
        if isinstance(regions, dict):   # ExifTool collapses a one-item list in some output modes
            regions = [regions]
        orientation = int(d.get("Orientation") or 1)
        faces = []
        for r in regions:
            a = r.get("Area")
            if r.get("Type") != "Face" or not a:
                continue
            cx, cy, w, h = to_upright(float(a["X"]), float(a["Y"]), float(a["W"]), float(a["H"]), orientation)
            faces.append(FaceBox(cx - w / 2, cy - h / 2, w, h, str(r.get("Name") or "")))
        if faces:
            out.append(TaggedImage(path=d["SourceFile"], orientation=orientation, faces=faces))
    return out


def read_regions(exiftool: str, folders: Sequence[Path | str]) -> list[TaggedImage]:
    """Every JPEG under `folders` that has face regions. Slow on a big library (ExifTool reads every file)."""
    cmd = [exiftool, "-r", "-ext", "jpg", "-ext", "jpeg", "-j", "-struct", "-RegionInfo", "-Orientation#",
           *[str(f) for f in folders]]
    res = subprocess.run(cmd, capture_output=True)
    if not res.stdout.strip():
        if res.returncode:
            raise RuntimeError(res.stderr.decode("utf-8", "replace").strip() or f"exiftool exit {res.returncode}")
        return []
    return parse_regions(json.loads(res.stdout.decode("utf-8-sig")))


def iou(a: FaceBox, b: FaceBox) -> float:
    ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def match_boxes(tagged: Sequence[FaceBox], found: Sequence[FaceBox]) -> list[Optional[int]]:
    """For each tagged box, the index of the detected box that is the same face, or None.

    A detection matches when its centre lies inside the tagged box or the overlap is at least 0.3.
    (Lightroom's boxes are looser than a detector's, so overlap alone would miss real matches.)
    Each detection is used at most once, best overlap first.
    """
    pairs = []
    for ti, t in enumerate(tagged):
        for fi, f in enumerate(found):
            cx, cy = f.center
            inside = t.x <= cx <= t.x + t.w and t.y <= cy <= t.y + t.h
            v = iou(t, f)
            if inside or v >= 0.3:
                pairs.append((v, ti, fi))
    pairs.sort(reverse=True)
    result: list[Optional[int]] = [None] * len(tagged)
    used = set()
    for _, ti, fi in pairs:
        if result[ti] is None and fi not in used:
            result[ti] = fi
            used.add(fi)
    return result


def _esc(s: str) -> str:
    """ExifTool's structure syntax: a pipe protects , ] } and | inside a value."""
    return re.sub(r"([|,\]}])", r"|\1", " ".join(s.split()))


def merge_regions(existing: Sequence[FaceBox], named: Sequence[FaceBox]) -> list[FaceBox]:
    """The regions to write: the newly named faces, plus every region already in the file that they don't cover.

    Writing replaces a file's whole region list, so without this, naming one face would erase the boxes
    Lightroom (or an earlier Write) put on everyone else. An existing region is covered, and so replaced,
    when a newly named face is the same face (see `match_boxes`).
    """
    covering = match_boxes(existing, list(named))   # per existing region: the new face that is the same face, or None
    return [f for f, hit in zip(existing, covering) if hit is None] + list(named)


def region_args(faces: Sequence[FaceBox], width: int, height: int, keep_unnamed: bool = False) -> list[str]:
    """ExifTool arguments that replace a file's face regions with these.

    Unnamed faces are dropped unless `keep_unnamed`, which is for regions carried over from the file
    (Lightroom keeps a box for a face nobody has named yet, and it's still the user's).

    `width` and `height` are the file's pixels. Only call this for an upright file (EXIF
    orientation 1): the boxes are written as they are, so a rotated file would need them
    mapped back into its stored frame.
    """
    named = [f for f in faces if keep_unnamed or f.name.strip()]
    if not any(f.name.strip() for f in faces):
        return []
    items = []
    for f in named:
        cx, cy = f.center
        name = f",Name={_esc(f.name)}" if f.name.strip() else ""
        items.append(f"{{Area={{X={cx:.5f},Y={cy:.5f},W={f.w:.5f},H={f.h:.5f},Unit=normalized}}{name},Type=Face}}")
    return [f"-XMP-mwg-rs:RegionInfo={{AppliedToDimensions={{W={width},H={height},Unit=pixel}},"
            f"RegionList=[{','.join(items)}]}}"]
