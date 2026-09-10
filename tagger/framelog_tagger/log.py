"""Read a Frame Log JSON export into a Roll of LogEntry records.

The file is produced by the phone app's "JSON" / "Export ZIP" buttons (see
buildJson in ../index.html):

    {
      "app": "framelog",
      "schema": 1,
      "exported": "<ISO-8601 UTC>",
      "roll": "<roll name>",
      "entries": [ { frame, iso, local, tz, lat, lon, acc, alt,
                     lens, aperture, shutterSpeed, notes }, ... ]
    }

Entries are the app's stored objects verbatim: `iso` is UTC, `tz` (schema 2+)
is the IANA zone the phone was in when the frame was logged, lat/lon/acc/alt
are numbers or null, aperture is "f/2.8", shutterSpeed is free text like
"1/250" or "2s". Unknown keys are ignored so the phone side can grow.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SUPPORTED_SCHEMAS = {1, 2}  # 2 adds an optional per-entry "tz" (IANA zone at log time)


class LogFormatError(ValueError):
    pass


@dataclass
class LogEntry:
    frame: int
    iso: str
    tz: str = ""  # IANA zone at log time, "" if the export predates schema 2
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


@dataclass
class Roll:
    name: str
    entries: list[LogEntry] = field(default_factory=list)
    exported: str = ""


def _float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _str(v: Any) -> str:
    return "" if v is None else str(v).strip()


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
        _, den = s.split("/")
        return None if int(den) == 0 else s
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return s
    return None


def entry_from_dict(d: dict, where: str = "") -> LogEntry:
    try:
        frame = int(d["frame"])
    except (KeyError, TypeError, ValueError):
        raise LogFormatError(f"{where}: bad or missing frame number {d.get('frame')!r}")
    iso = _str(d.get("iso"))
    try:
        datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        raise LogFormatError(f"{where}: bad or missing timestamp {iso!r}")
    return LogEntry(
        frame=frame,
        iso=iso,
        tz=_str(d.get("tz")),
        lat=_float(d.get("lat")),
        lon=_float(d.get("lon")),
        acc=_float(d.get("acc")),
        alt=_float(d.get("alt")),
        lens=_str(d.get("lens")),
        aperture=_str(d.get("aperture")),
        shutter=_str(d.get("shutterSpeed", d.get("shutter"))),
        notes=_str(d.get("notes")),
    )


def read_log(path: Path | str) -> Roll:
    """Parse a Frame Log JSON export. Entries come back sorted by frame (stable)."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise LogFormatError(f"{path.name}: not valid JSON ({e.msg} at line {e.lineno})")
    if not isinstance(data, dict) or data.get("app") != "framelog":
        raise LogFormatError(f"{path.name}: not a Frame Log export (missing \"app\": \"framelog\")")
    schema = data.get("schema")
    if schema not in SUPPORTED_SCHEMAS:
        raise LogFormatError(
            f"{path.name}: schema {schema!r} not supported by this tagger "
            f"(supports {sorted(SUPPORTED_SCHEMAS)}); update one side or the other")
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        raise LogFormatError(f"{path.name}: \"entries\" must be a list")
    entries = [entry_from_dict(e, f"{path.name} entry {i}") for i, e in enumerate(raw_entries)
               if isinstance(e, dict)]
    entries.sort(key=lambda e: e.frame)
    return Roll(name=_str(data.get("roll")) or path.stem, entries=entries,
                exported=_str(data.get("exported")))
