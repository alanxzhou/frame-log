"""Enumerate lab scans and build thumbnails."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from PIL import Image, ImageOps

from .keywords import xmp_keywords, xmp_region_names

JPEG_EXTS = {".jpg", ".jpeg"}
EXIF_DATETIME_ORIGINAL = 0x9003
EXIF_ORIENTATION = 0x0112


@dataclass
class ScanInfo:
    name: str
    path: Path
    width: int
    height: int
    tagged: bool  # already has DateTimeOriginal, i.e. probably tagged before
    keywords: list[str] = field(default_factory=list)  # XMP-dc:Subject already in the file
    region_names: list[str] = field(default_factory=list)  # names on the face regions already in the file
    orientation: int = 1  # EXIF Orientation; width/height are the stored pixels, so 5-8 means the picture is turned
    # The file's st_mtime_ns. The UI puts it in image URLs, so a different picture under
    # the same name (another folder, a re-rename, an edit) isn't shown from the browser's cache.
    version: int = 0

    def to_dict(self) -> dict:
        # The page gets the scan's own keywords: a face region's name is in the file's keywords too (Write puts it
        # there), but it belongs to the face, so it's sent apart and shown as one.
        faces = {n.lower() for n in self.region_names}
        return {"name": self.name, "width": self.width, "height": self.height, "tagged": self.tagged,
                "keywords": [k for k in self.keywords if k.lower() not in faces], "region_names": self.region_names,
                "orientation": self.orientation, "v": self.version}


def file_version(path: Path) -> int:
    """The file's st_mtime_ns, or 0 if it can't be read (the image endpoints will say why)."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


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
                keywords = xmp_keywords(im.info.get("xmp"))
                region_names = xmp_region_names(im.info.get("xmp"))
                orientation = int(exif.get(EXIF_ORIENTATION) or 1)
        except Exception:
            w = h = 0
            tagged = False
            keywords = []
            region_names = []
            orientation = 1
        out.append(ScanInfo(name=p.name, path=p, width=w, height=h, tagged=tagged, keywords=keywords, region_names=region_names,
                            orientation=orientation, version=file_version(p)))
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
