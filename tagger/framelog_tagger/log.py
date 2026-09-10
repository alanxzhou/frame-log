"""Read a Frame Log CSV export into LogEntry records.

The CSV is produced by the phone app's "CSV" / "Export ZIP" buttons and has
these columns (see buildCsv in ../index.html):

    frame,iso_datetime,latitude,longitude,accuracy_m,altitude_m,lens,aperture,shutter,notes

Timestamps are UTC ISO-8601 (Date.toISOString()). Aperture is stored as
"f/2.8"; shutter is free text like "1/250" or "2s".
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REQUIRED_COLUMNS = ("frame", "iso_datetime")


class LogFormatError(ValueError):
    pass


@dataclass
class LogEntry:
    frame: int
    iso: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    acc: Optional[float] = None
    alt: Optional[float] = None
    lens: str = ""
    aperture: str = ""
    shutter: str = ""
    notes: str = ""

    @property
    def utc(self) -> datetime:
        d = datetime.fromisoformat(self.iso.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def f_number(self) -> Optional[float]:
        return parse_aperture(self.aperture)

    @property
    def exposure_time(self) -> Optional[str]:
        return parse_shutter(self.shutter)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["f_number"] = self.f_number
        d["exposure_time"] = self.exposure_time
        return d


def _float(v: str) -> Optional[float]:
    v = (v or "").strip()
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def parse_aperture(raw: str) -> Optional[float]:
    """'f/2.8' -> 2.8, '2.8' -> 2.8, '' -> None."""
    s = (raw or "").strip().lower()
    s = re.sub(r"^f/?", "", s)
    return _float(s)


def parse_shutter(raw: str) -> Optional[str]:
    """Normalise shutter text to something ExifTool accepts for ExposureTime.

    '1/250' -> '1/250'; '1/250s' -> '1/250'; '2s' / '2"' -> '2'; '0.5' -> '0.5'.
    Anything unrecognised returns None (left untagged rather than written wrong).
    """
    s = (raw or "").strip().lower().replace(" ", "")
    s = re.sub(r'(s|sec|")$', "", s)
    if re.fullmatch(r"\d+/\d+", s):
        num, den = s.split("/")
        if int(den) == 0:
            return None
        return s
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return s
    return None


def read_log(path: Path | str) -> list[LogEntry]:
    """Parse the CSV, returning entries sorted by frame number (stable)."""
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = [c.strip() for c in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED_COLUMNS if c not in cols]
        if missing:
            raise LogFormatError(
                f"{path.name}: missing column(s) {', '.join(missing)}; "
                f"is this a Frame Log CSV export? Found: {', '.join(cols) or '(none)'}"
            )
        entries: list[LogEntry] = []
        for lineno, row in enumerate(reader, start=2):
            row = {(k or "").strip(): (v or "") for k, v in row.items()}
            if not any(row.values()):
                continue
            try:
                frame = int(float(row["frame"]))
            except ValueError:
                raise LogFormatError(f"{path.name} line {lineno}: bad frame number {row['frame']!r}")
            iso = row["iso_datetime"].strip()
            try:
                datetime.fromisoformat(iso.replace("Z", "+00:00"))
            except ValueError:
                raise LogFormatError(f"{path.name} line {lineno}: bad timestamp {iso!r}")
            entries.append(LogEntry(
                frame=frame,
                iso=iso,
                lat=_float(row.get("latitude", "")),
                lon=_float(row.get("longitude", "")),
                acc=_float(row.get("accuracy_m", "")),
                alt=_float(row.get("altitude_m", "")),
                lens=row.get("lens", "").strip(),
                aperture=row.get("aperture", "").strip(),
                shutter=row.get("shutter", "").strip(),
                notes=row.get("notes", "").strip(),
            ))
    entries.sort(key=lambda e: e.frame)
    return entries
