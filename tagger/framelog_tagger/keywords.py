"""Keywords: the roll-wide ones derived from the setup screen, the ones already in a scan, and the library.

Keywords are written to XMP-dc:Subject, which is what Lightroom shows as an
image's keywords. They compare case-insensitively ("beach" and "Beach" are one
keyword); the first spelling seen wins.

The library is every keyword you've used, kept next to config.json so it
carries from roll to roll and feeds the suggestions in the UI.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import config


def clean(kw: str) -> str:
    """Trim and collapse inner whitespace. Commas separate keywords in the UI, so they can't be in one."""
    return " ".join(str(kw).replace(",", " ").split())


def merge(*lists: Iterable[str]) -> list[str]:
    """Concatenate, cleaned, dropping blanks and case-insensitive repeats (the first spelling wins)."""
    out, seen = [], set()
    for kws in lists:
        for kw in kws:
            kw = clean(kw)
            if kw and kw.lower() not in seen:
                seen.add(kw.lower())
                out.append(kw)
    return out


def roll_keywords(make: str, camera: str, film: str) -> list[str]:
    """Keywords every scan of the roll gets: make + camera as one ("Nikon FE"), and the film. Never the ISO."""
    make, camera = clean(make), clean(camera)
    if make and camera and not camera.lower().startswith(make.lower()):
        body = f"{make} {camera}"
    else:
        body = camera or make   # a camera named "Nikon FE" already carries its make
    return merge([body, film])


_SUBJECT = re.compile(r"<dc:subject>(.*?)</dc:subject>", re.S)
_LI = re.compile(r"<rdf:li[^>]*>(.*?)</rdf:li>", re.S)


def xmp_keywords(xmp: bytes | str | None) -> list[str]:
    """The XMP-dc:Subject keywords in an XMP packet (Pillow's `im.info["xmp"]`), e.g. from an earlier Write."""
    if isinstance(xmp, bytes):
        xmp = xmp.decode("utf-8", "replace")
    m = _SUBJECT.search(xmp or "")
    return merge(html.unescape(li) for li in _LI.findall(m.group(1))) if m else []


def library_path() -> Path:
    return config.config_path().parent / "keywords.json"


class Library:
    """Every keyword used so far: name, when it was last used, and how many scans it's been written to."""

    def __init__(self, path: Path | None = None):
        self.path = path or library_path()
        self.items: dict[str, dict] = {}   # lower-case name -> {"name", "used", "count"}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for it in data.get("keywords", []):
                name = clean(it.get("name", ""))
                if name:
                    self.items[name.lower()] = {"name": name, "used": str(it.get("used", "")),
                                                "count": int(it.get("count", 0))}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def canonical(self, kw: str) -> str:
        """The library's spelling of a keyword, or the keyword cleaned if it's new."""
        kw = clean(kw)
        hit = self.items.get(kw.lower())
        return hit["name"] if hit else kw

    def add(self, kws: Iterable[str], touch: bool = True, written: int = 0) -> None:
        """Add keywords; `touch` marks them used now, `written` adds to their scan counts."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for kw in merge(kws):
            it = self.items.setdefault(kw.lower(), {"name": kw, "used": "", "count": 0})
            if touch:
                it["used"] = now
            it["count"] += written

    def remove(self, kws: Iterable[str]) -> None:
        for kw in kws:
            self.items.pop(clean(kw).lower(), None)

    def to_list(self) -> list[dict]:
        """Most recently used first, then most written, then by name."""
        by_name = sorted(self.items.values(), key=lambda it: it["name"].lower())
        return sorted(by_name, key=lambda it: (it["used"], it["count"]), reverse=True)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"keywords": self.to_list()}, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
        except OSError:
            pass   # like config.json: remembering is a convenience, never a failure
