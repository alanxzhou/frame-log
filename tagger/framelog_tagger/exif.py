"""Map a LogEntry onto ExifTool arguments and write them into a JPEG."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .keywords import merge
from .log import LogEntry


class ExifToolError(RuntimeError):
    pass


def find_exiftool(explicit: Optional[str] = None) -> Optional[str]:
    """Return a path to an ExifTool executable, or None."""
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return str(p)
        return shutil.which(explicit)
    env = os.environ.get("EXIFTOOL")
    if env and Path(env).is_file():
        return env
    for name in ("exiftool", "exiftool.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def exiftool_version(exe: str) -> str:
    out = subprocess.run([exe, "-ver"], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise ExifToolError(out.stderr.strip() or f"{exe} -ver failed")
    return out.stdout.strip()


@dataclass
class TagContext:
    """Roll-level values that apply to every frame."""
    tz: ZoneInfo                # fallback zone for entries that don't carry their own
    roll: str = ""
    camera: str = ""            # -> Model
    make: str = ""              # -> Make
    film: str = ""              # free text, recorded in UserComment
    iso: Optional[int] = None   # film speed -> ISO
    keywords: list[str] = field(default_factory=list)    # roll-wide keywords, e.g. ["Nikon FE", "Portra 400"]
    extra_args: list[str] = field(default_factory=list)  # passthrough, e.g. ['-Artist=Alan']


def _fmt_offset(dt) -> str:
    off = dt.utcoffset()
    total = int(off.total_seconds()) if off else 0
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def resolve_zone(entry: LogEntry, ctx: TagContext) -> tuple[ZoneInfo, str]:
    """The zone to write this frame in, and where it came from: 'phone' or 'fallback'.

    Entries logged with schema 2+ carry the phone's zone at the moment of the
    shot, which is what makes a roll that crosses time zones come out right.
    """
    if entry.tz:
        try:
            return ZoneInfo(entry.tz), "phone"
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ctx.tz, "fallback"


def build_args(entry: Optional[LogEntry], ctx: TagContext, keywords: Sequence[str] = (),
               clear_keywords: bool = False) -> list[str]:
    """ExifTool tag assignments for one frame. No file paths, no -o/-overwrite.

    With no entry (a scan with no log behind it) only the roll-wide tags are written.
    `keywords` are this scan's own, written after the roll's; together they replace the
    file's keywords. With none at all the file's are left alone, unless `clear_keywords`.
    """
    args = _frame_args(entry, ctx) if entry else []

    if ctx.camera:
        args.append(f"-Model={ctx.camera}")
    if ctx.make:
        args.append(f"-Make={ctx.make}")
    if ctx.iso is not None:
        args.append(f"-ISO={ctx.iso}")

    comment_bits = [f"Frame Log frame {entry.frame}"] if entry else []
    if ctx.roll:
        comment_bits.append(f"roll: {ctx.roll}")
    if ctx.film:
        comment_bits.append(f"film: {ctx.film}")
    if comment_bits:
        args.append("-UserComment=" + " | ".join(comment_bits))

    kws = merge(ctx.keywords, keywords)
    if kws:
        # One assignment per keyword (so commas never split one); together they replace the existing list.
        args += [f"-XMP-dc:Subject={kw}" for kw in kws]
    elif clear_keywords:
        args.append("-XMP-dc:Subject=")

    args += ctx.extra_args
    return args


def _frame_args(entry: LogEntry, ctx: TagContext) -> list[str]:
    args = []
    utc = entry.utc   # None on a card made in the tagger without a time: no date tags
    if utc is not None:
        zone, _ = resolve_zone(entry, ctx)
        local = utc.astimezone(zone)
        stamp = local.strftime("%Y:%m:%d %H:%M:%S")
        offset = _fmt_offset(local)
        args += [
            f"-DateTimeOriginal={stamp}",
            f"-CreateDate={stamp}",
            f"-OffsetTimeOriginal={offset}",
            f"-OffsetTimeDigitized={offset}",
        ]

    if entry.has_gps:
        # Signed values + *Ref tags: ExifTool derives N/S and E/W from the sign.
        args += [
            f"-GPSLatitude={entry.lat}", f"-GPSLatitudeRef={entry.lat}",
            f"-GPSLongitude={entry.lon}", f"-GPSLongitudeRef={entry.lon}",
        ]
        if utc is not None:
            args += [f"-GPSDateStamp={utc.strftime('%Y:%m:%d')}", f"-GPSTimeStamp={utc.strftime('%H:%M:%S')}"]
        if entry.alt is not None:
            args += [f"-GPSAltitude={entry.alt}", f"-GPSAltitudeRef={entry.alt}"]
        if entry.acc is not None:
            args.append(f"-GPSHPositioningError={entry.acc}")

    fn = entry.f_number
    if fn is not None:
        args.append(f"-FNumber={fn:g}")
    et = entry.exposure_time
    if et is not None:
        args.append(f"-ExposureTime={et}")
    if entry.lens:
        args += [f"-LensModel={entry.lens}", f"-XMP-aux:Lens={entry.lens}"]
    if entry.notes:
        # Lightroom's Caption field reads dc:description / ImageDescription.
        args += [f"-ImageDescription={entry.notes}", f"-XMP-dc:Description={entry.notes}"]
    return args


def write_tags(exe: str, src: Path, dst: Path, tag_args: list[str]) -> None:
    """Copy src to dst (unless they are the same file), then write tags into dst."""
    src, dst = Path(src), Path(dst)
    if src.resolve() != dst.resolve():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    cmd = [exe, "-m", "-overwrite_original", "-charset", "utf8"]
    if sys.platform == "win32":
        cmd += ["-charset", "filename=utf8"]
    cmd += tag_args + [str(dst)]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=120)
    if res.returncode != 0 or "1 image files updated" not in res.stdout:
        raise ExifToolError(res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}")
