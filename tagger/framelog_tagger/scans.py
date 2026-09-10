"""Enumerate lab scans and build thumbnails."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from PIL import Image, ImageOps

JPEG_EXTS = {".jpg", ".jpeg"}
EXIF_DATETIME_ORIGINAL = 0x9003


@dataclass
class ScanInfo:
    name: str
    path: Path
    width: int
    height: int
    tagged: bool  # already has DateTimeOriginal, i.e. probably tagged before

    def to_dict(self) -> dict:
        return {"name": self.name, "width": self.width, "height": self.height, "tagged": self.tagged}


def natural_key(name: str):
    """Sort '2.jpg' before '10.jpg' and 'img_9' before 'img_10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def list_scans(directory: Path | str) -> list[ScanInfo]:
    directory = Path(directory)
    if not directory.is_dir():
        raise NotADirectoryError(str(directory))
    files = sorted(
        (p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in JPEG_EXTS),
        key=lambda p: natural_key(p.name),
    )
    out: list[ScanInfo] = []
    for p in files:
        try:
            with Image.open(p) as im:
                w, h = im.size
                exif = im.getexif()
                tagged = bool(exif.get_ifd(0x8769).get(EXIF_DATETIME_ORIGINAL))
        except Exception:
            w = h = 0
            tagged = False
        out.append(ScanInfo(name=p.name, path=p, width=w, height=h, tagged=tagged))
    return out


class ThumbnailCache:
    """JPEG thumbnails, longest edge `size`, honouring EXIF orientation."""

    def __init__(self, size: int = 360):
        self.size = size
        self._cache: dict[tuple[str, int], bytes] = {}
        self._lock = Lock()

    def get(self, path: Path) -> bytes:
        key = (str(path), int(path.stat().st_mtime_ns))
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None:
            return hit
        with Image.open(path) as im:
            im.draft("RGB", (self.size * 2, self.size * 2))  # fast decode at reduced scale
            im = ImageOps.exif_transpose(im)
            im.thumbnail((self.size, self.size))
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=82)
        data = buf.getvalue()
        with self._lock:
            self._cache[key] = data
        return data
